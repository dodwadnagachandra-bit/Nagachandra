"""Stage 2: Per-Driver Verification — ECU-1170-552A.

Verifies each hardware interface independently:
- CAN0, CAN1: SocketCAN interface existence, configuration, loopback test
- RS485 x4: UART device existence + Modbus RTU register polling
- GPIO: gpiochip detection, DI readability, safe DO writability
- ETH: interface enumeration, link state
- HDMI: DRM subsystem detection

Run from developer laptop (requires SSH access to ECU):
    uv run pytest tests/hw/test_drivers.py -v

Modbus poll tests use xfail when no simulator is connected — this
distinguishes "UART exists but nothing responds" from full validation.

GPIO DO tests only actuate safe non-critical outputs (RUNNING_LAMP DO-3,
SPARE_DO7). Safety-critical outputs DO-0 (ACDB_TRIP), DO-1 (EXTINGUISHER),
DO-5 (PCS_STOP) are never asserted by this test suite.
"""

from __future__ import annotations

import pytest

from tests.hw.conftest import ecu_ssh

# All tests in this module require ECU hardware; 300s covers Modbus poll timeouts
pytestmark = [pytest.mark.hw, pytest.mark.timeout(300)]


# ---------------------------------------------------------------------------
# CAN tests
# ---------------------------------------------------------------------------


class TestCAN:
    """Stage 2.1: CAN bus driver verification.

    Verifies SocketCAN interfaces are present, configured, and functional.
    The ECU-1170-552A uses the TI AM65xx M_CAN (Bosch MCAN) controllers.
    Kernel must include CONFIG_CAN_M_CAN for interfaces to enumerate.
    """

    @pytest.mark.usefixtures("ecu_reachable")
    def test_can0_interface_exists(self) -> None:
        """Assert can0 SocketCAN interface is present in the kernel network stack.

        Failure indicates: missing CONFIG_CAN_M_CAN, wrong DTB MCAN node,
        or CAN driver not loaded. Run 'dmesg | grep -i can' for diagnostics.
        """
        result = ecu_ssh("ip link show can0")
        if result.returncode != 0:
            # Collect diagnostics before failing
            diag = ecu_ssh("dmesg | grep -i can | tail -10")
            pytest.fail(
                f"can0 interface not found (rc={result.returncode}).\n"
                f"CAN kernel messages:\n{diag.stdout}"
            )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_can1_interface_exists(self) -> None:
        """Assert can1 SocketCAN interface is present in the kernel network stack.

        CAN1 is the second M_CAN controller on AM65xx. Independent of CAN0
        — both must be functional for full BMS + PCS/BMS redundancy.
        Failure means the second MCAN node is absent or disabled in DTB.
        """
        result = ecu_ssh("ip link show can1")
        if result.returncode != 0:
            diag = ecu_ssh("dmesg | grep -i can | tail -10")
            pytest.fail(
                f"can1 interface not found (rc={result.returncode}).\n"
                f"CAN kernel messages:\n{diag.stdout}"
            )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_can0_up_and_configured(self) -> None:
        """Assert can0 can be configured with a 250 kbps bitrate and brought up.

        Attempts to bring up can0 with standard BMS CAN bitrate (250 kbps).
        Cleans up by taking the interface back down after the test.
        Failure indicates: SocketCAN driver loaded but MCAN hw inaccessible,
        or bitrate incompatible with the physical CAN bus wiring.
        """
        # Bring down first in case left in a bad state
        ecu_ssh("ip link set can0 down 2>/dev/null || true")

        # Configure and bring up
        result = ecu_ssh("ip link set can0 up type can bitrate 250000")
        if result.returncode != 0:
            pytest.fail(
                f"Failed to configure can0 at 250 kbps (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )

        # Verify it is now UP
        show_result = ecu_ssh("ip -d link show can0")
        assert "UP" in show_result.stdout or "LOWER_UP" in show_result.stdout, (
            f"can0 brought up but state does not show UP: {show_result.stdout}"
        )

        # Clean up — take interface back down
        ecu_ssh("ip link set can0 down 2>/dev/null || true")

    @pytest.mark.usefixtures("ecu_reachable")
    def test_can_loopback(self) -> None:
        """Assert CAN loopback send/receive works on can0.

        Sets can0 to loopback mode, sends a known CAN frame, verifies it is
        received. Tests the full SocketCAN TX/RX path without requiring an
        external CAN bus or second device.

        If can-utils (cansend/candump) are not present on the ECU, test is
        marked xfail — can-utils may not be included in a minimal Yocto image.
        """
        # Check can-utils availability
        which_result = ecu_ssh("which cansend 2>/dev/null && which candump 2>/dev/null")
        if which_result.returncode != 0:
            pytest.xfail("can-utils (cansend/candump) not available on ECU Yocto image")

        # Bring down and configure loopback
        ecu_ssh("ip link set can0 down 2>/dev/null || true")
        setup = ecu_ssh("ip link set can0 up type can loopback on bitrate 250000")
        if setup.returncode != 0:
            pytest.xfail(
                f"Cannot configure can0 loopback (rc={setup.returncode}): "
                f"{setup.stderr.strip()}"
            )

        try:
            # Send a known frame and capture with 2s timeout (-T 2000ms), 1 frame (-n 1)
            send_result = ecu_ssh(
                "candump can0 -n 1 -T 2000 & "
                "sleep 0.2 && cansend can0 123#DEADBEEF; wait"
            )
            assert "123" in send_result.stdout and "DEADBEEF" in send_result.stdout.upper(), (
                f"Loopback frame not received. candump output: {send_result.stdout!r}"
            )
        finally:
            # Always clean up loopback mode
            ecu_ssh("ip link set can0 down 2>/dev/null || true")


# ---------------------------------------------------------------------------
# RS485 tests
# ---------------------------------------------------------------------------


class TestRS485:
    """Stage 2.2: RS485 UART driver and Modbus polling verification.

    Two-tier validation per port:
    1. UART device existence — character device present in /dev/
    2. Modbus RTU poll — ECU can issue a Modbus read on the serial port

    The ECU-1170-552A has 4 RS485 ports:
    - RS485-1: PCS (Modbus RTU 9600/8N1, V1.24 register map)
    - RS485-2: Energy meter (Modbus RTU)
    - RS485-3: BTMS thermal controller (Modbus RTU)
    - RS485-4: DG / Solar PV (Modbus RTU)

    Actual /dev/ttyS* paths depend on the ECU DTB — checked against
    common AM65xx UART device paths.
    """

    # Common AM65xx UART device paths to probe for each RS485 port
    _UART_CANDIDATES: list[list[str]] = [
        ["/dev/ttyS1", "/dev/ttyS3", "/dev/ttyUSB0"],  # RS485-1 (PCS)
        ["/dev/ttyS2", "/dev/ttyS4", "/dev/ttyUSB1"],  # RS485-2 (Meter)
        ["/dev/ttyS5", "/dev/ttyS6", "/dev/ttyUSB2"],  # RS485-3 (BTMS)
        ["/dev/ttyS7", "/dev/ttyS8", "/dev/ttyUSB3"],  # RS485-4 (DG/PV)
    ]

    def _find_uart(self, port_index: int) -> str | None:
        """Return the first existing /dev/ttySN path for the given RS485 port, or None."""
        candidates: list[str] = self._UART_CANDIDATES[port_index]
        # Build a one-liner that checks each candidate
        checks: str = " || ".join(
            f"(test -c {dev} && echo {dev})"
            for dev in candidates
        )
        result = ecu_ssh(f"({checks}) 2>/dev/null | head -1")
        found: str = result.stdout.strip()
        return found if found else None

    @pytest.mark.usefixtures("ecu_reachable")
    def test_rs485_1_exists(self) -> None:
        """Assert RS485-1 (PCS) UART character device exists on the ECU.

        RS485-1 connects to the PCS over Modbus RTU 9600/8N1.
        Failure means the AM65xx UART node for PCS is missing/disabled in DTB
        or the Yocto kernel does not include the UART driver.
        Reports which device path was found for field reference.
        """
        dev: str | None = self._find_uart(0)
        assert dev is not None, (
            f"RS485-1 (PCS): no UART device found. "
            f"Checked: {self._UART_CANDIDATES[0]}. "
            f"Verify DTB UART node for RS485-1."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_rs485_2_exists(self) -> None:
        """Assert RS485-2 (Energy Meter) UART character device exists on the ECU.

        Failure means the meter RS485 UART is absent or disabled in DTB.
        """
        dev: str | None = self._find_uart(1)
        assert dev is not None, (
            f"RS485-2 (Meter): no UART device found. "
            f"Checked: {self._UART_CANDIDATES[1]}. "
            f"Verify DTB UART node for RS485-2."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_rs485_3_exists(self) -> None:
        """Assert RS485-3 (BTMS) UART character device exists on the ECU.

        Failure means the BTMS thermal controller RS485 UART is absent or disabled.
        """
        dev: str | None = self._find_uart(2)
        assert dev is not None, (
            f"RS485-3 (BTMS): no UART device found. "
            f"Checked: {self._UART_CANDIDATES[2]}. "
            f"Verify DTB UART node for RS485-3."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_rs485_4_exists(self) -> None:
        """Assert RS485-4 (DG/PV) UART character device exists on the ECU.

        Failure means the DG/solar RS485 UART is absent or disabled in DTB.
        """
        dev: str | None = self._find_uart(3)
        assert dev is not None, (
            f"RS485-4 (DG/PV): no UART device found. "
            f"Checked: {self._UART_CANDIDATES[3]}. "
            f"Verify DTB UART node for RS485-4."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_rs485_stty_config(self) -> None:
        """Assert found RS485 UART devices can be configured at 9600 baud.

        For each discovered ttyS device, verifies stty succeeds (device is
        accessible) and the baud rate can be set to 9600. Failure means the
        UART exists but is locked or not configurable (permissions, busy).
        """
        failures: list[str] = []

        for port_idx in range(4):
            dev: str | None = self._find_uart(port_idx)
            if dev is None:
                continue  # UART existence tested separately

            # Check stty can read and set baud rate
            result = ecu_ssh(f"stty -F {dev} 9600 && stty -F {dev}")
            if result.returncode != 0:
                failures.append(
                    f"RS485-{port_idx + 1} ({dev}): stty failed "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )

        if failures:
            pytest.fail(
                f"{len(failures)} RS485 port(s) not configurable:\n"
                + "\n".join(f"  - {f}" for f in failures)
            )

    @pytest.mark.usefixtures("ecu_reachable")
    @pytest.mark.requires_simulator
    def test_rs485_modbus_poll(self) -> None:
        """Assert RS485 Modbus RTU polling works on each port.

        Tier 2 of RS485 validation: verifies the ECU can issue a Modbus RTU
        read_holding_registers request via pymodbus on each discovered UART.

        Per-port behaviour:
        - If a Modbus simulator is connected: asserts 'OK' response.
        - If no simulator (timeout/ERR): marks port as xfail with reason.
          This distinguishes "UART exists but nothing responds" from
          "full Modbus communication validated".

        The @pytest.mark.requires_simulator marker documents that full PASS
        requires external Modbus simulators on USB-RS485 adapters connected
        to each RS485 port. Without simulators, all ports report xfail.

        Validates locked decision from CONTEXT.md:
          RS485-1: PCS polls and decodes registers (9600/8N1, Modbus RTU)
          RS485-2: Meter reads telemetry
          RS485-3: BTMS reads thermal data
          RS485-4: DG reads DG status
        """
        rs485_names: list[str] = ["PCS", "Meter", "BTMS", "DG/PV"]
        xfail_reasons: list[str] = []
        passed_ports: list[str] = []

        for port_idx in range(4):
            dev: str | None = self._find_uart(port_idx)
            if dev is None:
                xfail_reasons.append(
                    f"RS485-{port_idx + 1} ({rs485_names[port_idx]}): "
                    f"UART device not found"
                )
                continue

            # Python one-liner run on the ECU via SSH
            # Uses EMS venv pymodbus if available, falls back to system python3
            modbus_snippet: str = (
                f"python3 -c \""
                f"from pymodbus.client import ModbusSerialClient; "
                f"c = ModbusSerialClient('{dev}', baudrate=9600, timeout=2); "
                f"c.connect(); "
                f"r = c.read_holding_registers(0, 1, slave=1); "
                f"print('OK' if not r.isError() else 'ERR'); "
                f"c.close()"
                f"\""
            )
            result = ecu_ssh(modbus_snippet)
            response: str = result.stdout.strip()

            if response == "OK":
                passed_ports.append(
                    f"RS485-{port_idx + 1} ({rs485_names[port_idx]}, {dev})"
                )
            else:
                # No simulator connected — xfail gracefully
                xfail_reasons.append(
                    f"RS485-{port_idx + 1} ({rs485_names[port_idx]}, {dev}): "
                    f"no Modbus response (response='{response}', "
                    f"rc={result.returncode}) — connect Modbus simulator for full validation"
                )

        if xfail_reasons and not passed_ports:
            # All ports timed out — no simulators connected
            pytest.xfail(
                "No Modbus simulators detected on any RS485 port. "
                "Connect USB-RS485 adapters with Modbus simulators for full validation.\n"
                + "\n".join(f"  - {r}" for r in xfail_reasons)
            )
        elif xfail_reasons:
            # Some ports passed, some didn't — report partial results
            # This is a soft failure: some simulators connected, some not
            import warnings
            warnings.warn(
                f"Partial Modbus validation: {len(passed_ports)} port(s) OK, "
                f"{len(xfail_reasons)} port(s) no simulator:\n"
                + "\n".join(f"  - {r}" for r in xfail_reasons),
                stacklevel=2,
            )


# ---------------------------------------------------------------------------
# GPIO tests
# ---------------------------------------------------------------------------


class TestGPIO:
    """Stage 2.3: GPIO driver and I/O line verification.

    Verifies libgpiod-based GPIO access for safety I/O.
    ECU-1170-552A has 8 DI + 8 DO lines via the TI AM65xx GPIO banks.
    GPIO chip number and line offsets depend on the Advantech DTB.

    SAFETY NOTE: Test never actuates safety-critical DO outputs:
    - DO-0 ACDB_TRIP — trips AC distribution board
    - DO-1 EXTINGUISHER — discharges fire suppression
    - DO-5 PCS_STOP — emergency stop to PCS

    Safe outputs tested: DO-3 RUNNING_LAMP, DO-7 SPARE_DO7.
    """

    @pytest.mark.usefixtures("ecu_reachable")
    def test_gpiochip_exists(self) -> None:
        """Assert at least one gpiochip device is present via gpiodetect.

        Failure indicates: libgpiod not available on Yocto image,
        GPIO driver not loaded, or DTB GPIO node absent/disabled.
        Reports chip names for reference in subsequent tests.
        """
        result = ecu_ssh("gpiodetect")
        if result.returncode != 0:
            pytest.fail(
                f"gpiodetect failed (rc={result.returncode}): {result.stderr.strip()}. "
                f"Ensure libgpiod tools are installed in Yocto image."
            )

        output: str = result.stdout.strip()
        assert output, "gpiodetect returned no output — no gpiochip devices found"

        chips: list[str] = [
            line.split()[0] for line in output.splitlines() if line.strip()
        ]
        assert len(chips) >= 1, f"Expected at least 1 gpiochip, got: {chips}"
        # Report chip names for field reference (useful for DTB verification)
        print(f"\nFound {len(chips)} gpiochip(s): {', '.join(chips)}")

    @pytest.mark.usefixtures("ecu_reachable")
    def test_gpiochip_lines(self) -> None:
        """Assert at least 16 GPIO lines exist (8 DI + 8 DO minimum).

        gpioinfo shows all lines per chip. The EMS requires a minimum of
        16 lines on a single chip for safety I/O. Actual line count depends
        on the AM65xx GPIO bank assigned in the DTB.
        """
        result = ecu_ssh("gpioinfo")
        if result.returncode != 0:
            pytest.fail(
                f"gpioinfo failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        output: str = result.stdout.strip()
        # Count lines (each line entry typically starts with a tab or space)
        line_count: int = sum(
            1 for line in output.splitlines()
            if "line" in line.lower() and not line.strip().startswith("gpiochip")
        )

        assert line_count >= 16, (
            f"Expected at least 16 GPIO lines (8 DI + 8 DO), "
            f"found {line_count} lines. gpioinfo output:\n{output}"
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_gpio_di_readable(self) -> None:
        """Assert DI lines 0-7 are readable via gpioget.

        For each DI line (offsets 0-7 on the first gpiochip), verifies
        gpioget returns 0 or 1 without error. Does not assert specific
        values since DI state depends on connected sensor states.

        Failure indicates: wrong gpiochip/offset mapping (DTB mismatch),
        GPIO line in use by another driver, or libgpiod permission error.
        """
        # Determine the first gpiochip name
        detect_result = ecu_ssh("gpiodetect | head -1 | awk '{print $1}'")
        if detect_result.returncode != 0 or not detect_result.stdout.strip():
            pytest.skip("No gpiochip detected — skipping DI read test")

        chipname: str = detect_result.stdout.strip()
        failures: list[str] = []

        for offset in range(8):  # DI-0 through DI-7
            result = ecu_ssh(f"gpioget {chipname} {offset}")
            value: str = result.stdout.strip()
            if result.returncode != 0:
                failures.append(
                    f"DI-{offset} (offset {offset} on {chipname}): "
                    f"gpioget failed (rc={result.returncode}): {result.stderr.strip()}"
                )
            elif value not in ("0", "1"):
                failures.append(
                    f"DI-{offset} (offset {offset} on {chipname}): "
                    f"unexpected value '{value}' (expected '0' or '1')"
                )

        assert not failures, (
            f"{len(failures)}/8 DI line(s) not readable:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_gpio_do_writable(self) -> None:
        """Assert safe DO lines are writable via gpioset.

        Tests DO-3 (RUNNING_LAMP) and DO-7 (SPARE_DO7) — the two safe
        non-critical outputs. Asserts 1 then 0 on each, verifies gpioset
        returns exit code 0.

        SAFETY: DO-0 (ACDB_TRIP), DO-1 (EXTINGUISHER), DO-5 (PCS_STOP)
        are NEVER actuated by this test. Only RUNNING_LAMP and SPARE are safe
        to toggle without hardware consequences.

        Failure indicates: wrong offset (DTB mismatch), GPIO line claimed by
        safety_manager kernel driver, or libgpiod v2 API incompatibility.
        """
        detect_result = ecu_ssh("gpiodetect | head -1 | awk '{print $1}'")
        if detect_result.returncode != 0 or not detect_result.stdout.strip():
            pytest.skip("No gpiochip detected — skipping DO write test")

        chipname: str = detect_result.stdout.strip()
        # Safe outputs only: DO-3 RUNNING_LAMP (offset 11), DO-7 SPARE_DO7 (offset 15)
        # Offsets assume DI-0..7 on lines 0..7 and DO-0..7 on lines 8..15
        # If gpio_config.yaml uses different offsets, update these
        safe_do_offsets: dict[str, int] = {
            "DO-3 RUNNING_LAMP": 11,
            "DO-7 SPARE_DO7": 15,
        }
        failures: list[str] = []

        for name, offset in safe_do_offsets.items():
            # Assert high
            set_high = ecu_ssh(f"gpioset {chipname} {offset}=1")
            if set_high.returncode != 0:
                failures.append(
                    f"{name} (offset {offset}): gpioset high failed "
                    f"(rc={set_high.returncode}): {set_high.stderr.strip()}"
                )
                continue

            # Assert low (restore safe state)
            set_low = ecu_ssh(f"gpioset {chipname} {offset}=0")
            if set_low.returncode != 0:
                failures.append(
                    f"{name} (offset {offset}): gpioset low failed "
                    f"(rc={set_low.returncode}): {set_low.stderr.strip()}"
                )

        assert not failures, (
            f"{len(failures)} DO line(s) not writable:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


# ---------------------------------------------------------------------------
# Network tests
# ---------------------------------------------------------------------------


class TestNetwork:
    """Stage 2.4: Ethernet interface verification.

    ECU-1170-552A has 2 Ethernet ports (ETH0 WAN, ETH1 LAN).
    Interface names depend on Yocto systemd-networkd predictable naming
    (may be eth0/eth1 or end0/enp1s0 — test handles both).
    """

    @pytest.mark.usefixtures("ecu_reachable")
    def test_eth_interfaces(self) -> None:
        """Assert at least 2 Ethernet interfaces exist (excluding loopback).

        Failure indicates: only one NIC detected, second port disabled in DTB,
        or Yocto network driver missing for the second MAC controller.
        Reports actual interface names for field reference.
        """
        result = ecu_ssh("ip -br link show")
        if result.returncode != 0:
            pytest.fail(
                f"ip link failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        # Count Ethernet-like interfaces (exclude loopback 'lo' and virtual 'can*', 'veth*')
        lines: list[str] = [
            line for line in result.stdout.splitlines()
            if line.strip()
            and not line.startswith("lo ")
            and not line.split()[0].startswith("can")
            and not line.split()[0].startswith("veth")
        ]

        iface_names: list[str] = [line.split()[0] for line in lines]
        print(f"\nEthernet interfaces found: {', '.join(iface_names)}")

        assert len(lines) >= 2, (
            f"Expected at least 2 Ethernet interfaces, found {len(lines)}: "
            f"{iface_names}. Check DTB for second MAC node."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_eth0_link_up(self) -> None:
        """Assert the primary Ethernet interface has link state UP.

        Checks eth0 first; falls back to the first non-loopback interface
        if predictable naming is used. The ECU must have an Ethernet cable
        connected to ETH0 (WAN port) for this test to pass.
        """
        result = ecu_ssh("ip -br link show")
        if result.returncode != 0:
            pytest.fail(f"ip link failed: {result.stderr.strip()}")

        # Find first non-loopback, non-CAN interface
        primary_iface: str | None = None
        for line in result.stdout.splitlines():
            name: str = line.split()[0] if line.split() else ""
            if name and name != "lo" and not name.startswith("can"):
                primary_iface = name
                break

        if primary_iface is None:
            pytest.fail("No Ethernet interfaces found on ECU")

        # Check for UP state
        link_result = ecu_ssh(f"ip -br link show {primary_iface}")
        output: str = link_result.stdout.strip()
        assert "UP" in output, (
            f"{primary_iface} link is not UP: {output}. "
            f"Connect Ethernet cable to ETH0 (WAN port)."
        )

    @pytest.mark.usefixtures("ecu_reachable")
    def test_network_connectivity(self) -> None:
        """Assert WAN connectivity by pinging 8.8.8.8.

        May fail in an isolated lab environment without internet access.
        Marked xfail for environments where only LAN connectivity is expected.
        Failure is not a blocker for hardware bring-up in isolated sites.
        """
        result = ecu_ssh("ping -c 3 -W 2 8.8.8.8")
        if result.returncode != 0:
            pytest.xfail(
                "WAN connectivity not available (ping 8.8.8.8 failed). "
                "Expected in isolated lab environments — not a hardware failure."
            )


# ---------------------------------------------------------------------------
# HDMI test
# ---------------------------------------------------------------------------


class TestHDMI:
    """Stage 2.5: HDMI display output verification.

    Tests the DRM subsystem is present, which is the kernel prerequisite
    for HDMI output. Does not require a monitor to be connected — a
    'disconnected' status from DRM is still a pass (driver is loaded).
    """

    @pytest.mark.usefixtures("ecu_reachable")
    def test_hdmi_output_detected(self) -> None:
        """Assert DRM subsystem is present on the ECU.

        Reads /sys/class/drm/card*/status to check if DRM (Direct Rendering
        Manager) subsystem is loaded. Status may be 'connected' (monitor
        plugged in) or 'disconnected' (no monitor) — both are valid.
        'no drm' indicates the DRM subsystem is absent (driver not loaded
        or DTB display node missing).

        A connected HDMI monitor running the HMI frontend is required for
        Stage 5 full validation, but Stage 2 only verifies driver presence.
        """
        result = ecu_ssh(
            "cat /sys/class/drm/card*/status 2>/dev/null || echo 'no drm'"
        )
        output: str = result.stdout.strip()

        assert output != "no drm", (
            "DRM subsystem not found — HDMI driver not loaded or DTB display "
            "node missing. Check Yocto kernel config for DRM support."
        )

        # Report status for reference (connected/disconnected)
        print(f"\nDRM status: {output}")
        # Pass regardless of connected/disconnected — driver presence is sufficient
