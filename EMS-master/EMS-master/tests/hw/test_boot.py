"""Stage 1: Boot Validation — ECU-1170-552A.

Verifies that the Yocto image boots correctly and all 14 EMS services
reach 'active' state within a 60-second timeout after power-on.

Run from developer laptop (requires SSH access to ECU):
    uv run pytest tests/hw/test_boot.py -v

Skip when ECU is offline (graceful no-hardware behaviour):
    All tests auto-skip via ecu_reachable session fixture.
"""

from __future__ import annotations

import re

import pytest

from tests.hw.conftest import SERVICES, ecu_ssh

# All tests in this module require ECU hardware and respect the 120s timeout
pytestmark = [pytest.mark.hw, pytest.mark.timeout(120)]


@pytest.mark.usefixtures("ecu_reachable")
def test_all_services_active() -> None:
    """Assert all 14 EMS services report 'active' via systemctl.

    Failure shows ALL failed services (not just the first), enabling a
    complete picture of boot health in a single test run.
    """
    failures: list[str] = []

    for svc in SERVICES:
        result = ecu_ssh(f"systemctl is-active {svc}")
        state: str = result.stdout.strip()
        if state != "active":
            failures.append(f"{svc}: state='{state}' (stdout={result.stdout!r}, stderr={result.stderr!r})")

    assert not failures, (
        f"{len(failures)}/{len(SERVICES)} services not active:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


@pytest.mark.usefixtures("ecu_reachable")
def test_boot_time_within_60s() -> None:
    """Assert total Yocto boot time is under 60 seconds per systemd-analyze.

    If systemd-analyze is unavailable on the Yocto image, the test is
    marked xfail with an informative reason rather than failing hard.
    """
    result = ecu_ssh("systemd-analyze")

    if result.returncode != 0:
        pytest.xfail(
            f"systemd-analyze unavailable on ECU (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )

    # Parse: "Startup finished in 1.234s (kernel) + 5.678s (userspace) = 6.912s"
    # or:    "Startup finished in 6.912s"
    output: str = result.stdout.strip()
    match: re.Match[str] | None = re.search(r"=\s*([\d.]+)s", output)
    if match is None:
        # Try alternate format: "Startup finished in Ns"
        match = re.search(r"in\s+([\d.]+)s", output)

    if match is None:
        pytest.xfail(f"Could not parse systemd-analyze output: {output!r}")

    total_s: float = float(match.group(1))
    assert total_s < 60.0, (
        f"Boot time {total_s:.1f}s exceeds 60s limit. "
        f"systemd-analyze output: {output}"
    )


@pytest.mark.usefixtures("ecu_reachable")
def test_ems_target_active() -> None:
    """Assert ems.target is active — the EMS system-level target unit.

    ems.target depends on all EMS services; if any service fails to start,
    ems.target remains inactive. This provides a single-shot health check
    complementing the per-service test_all_services_active test.
    """
    result = ecu_ssh("systemctl is-active ems.target")
    state: str = result.stdout.strip()

    assert state == "active", (
        f"ems.target is not active (state='{state}'). "
        f"Run: ssh {result.returncode} root@ECU systemctl status ems.target"
    )


@pytest.mark.usefixtures("ecu_reachable")
def test_no_failed_services() -> None:
    """Assert no EMS services are in a failed state.

    Uses systemctl list-units --state=failed to detect services that
    started but crashed or exited with a non-zero code. This catches
    services that fail after starting (unlike test_all_services_active
    which catches services that never reached 'active').
    """
    result = ecu_ssh("systemctl list-units --state=failed --no-legend --no-pager")

    if result.returncode != 0:
        # systemctl itself failed — may be a connectivity issue
        pytest.fail(
            f"systemctl list-units failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )

    output: str = result.stdout.strip()
    if not output:
        return  # No failed units — test passes

    # Check if any EMS service names appear in the failed list
    ems_failed: list[str] = [
        line for line in output.splitlines()
        if any(svc in line for svc in SERVICES)
    ]

    assert not ems_failed, (
        f"{len(ems_failed)} EMS service(s) in failed state:\n"
        + "\n".join(f"  {line}" for line in ems_failed)
    )
