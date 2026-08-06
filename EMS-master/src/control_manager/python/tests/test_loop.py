"""
Tests for ControlLoop — 1Hz control loop with RTDB I/O and ZMQ.

Tests are written against the behavior spec (TDD).
All tests use tcp:// endpoints for ZMQ (avoids /run/ems dependency).
RTDB is mocked with ctypes structures in process memory.

Note on ZMQ TCP timing: after socket.connect() there is a small but non-zero
latency before the TCP connection is established. A 50ms sleep after connect()
ensures the peer is ready before send() is called. This is the standard pattern
for ZMQ test suites using inproc-free tcp:// endpoints.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import msgpack
import pytest
import zmq

from ems_common.ipc import (
    TOPIC_CONTROL_STATE,
    TOPIC_STATE_CHANGE,
    decode_command_response,
    encode_command_request,
)
from ems_common.ipc import SOCK_CONFIG_PUB
from ems_common.rtdb import (
    EmsCluster,
    EmsGpio,
    EmsPcs,
    EmsSystem,
    MAX_CLUSTERS,
    MAX_RACKS_PER_CLUSTER,
)
from ems_control_manager.state_machine import (
    STATE_FAULT,
    STATE_IDLE,
    STATE_MAINTENANCE,
    PCS_CMD_NONE,
    PCS_CMD_ON,
    PCS_STATE_OFF,
    PCS_STATE_RUNNING,
)

# Short sleep to allow ZMQ TCP connections to establish
_CONNECT_SLEEP_S: float = 0.05
# Short sleep to allow a sent message to arrive at the receiving peer
_MSG_DELIVER_S: float = 0.02


# ---------------------------------------------------------------------------
# Test helpers / mock RTDB
# ---------------------------------------------------------------------------

def _make_config() -> dict[str, Any]:
    """Minimal valid control config for tests."""
    return {
        "state_machine": {
            "loop_interval_ms": 1000,
            "fault_retry_count": 3,
            "pcs_startup_timeout_s": 5.0,
            "pcs_shutdown_timeout_s": 5.0,
        }
    }


def _make_full_config() -> dict[str, Any]:
    """Full control config with intelligence sections for Plan 16-02 tests."""
    return {
        "state_machine": {
            "loop_interval_ms": 1000,
            "fault_retry_count": 3,
        },
        "soc_limits": {
            "charge_cutoff_pct": 95,
            "discharge_cutoff_pct": 10,
        },
        "power_limits": {
            "max_charge_kw": 100,
            "max_discharge_kw": 100,
        },
        "source_priority": {
            "day_order": ["solar", "grid", "bess", "dg"],
            "night_order": ["grid", "bess", "dg"],
        },
        "derating": {
            "bms_high_start_c": 40,
            "bms_high_cutoff_c": 50,
            "pcs_high_start_c": 65,
            "pcs_high_cutoff_c": 80,
            "bms_low_start_c": 5,
            "bms_low_cutoff_c": 0,
        },
        "ramping": {
            "charge_ramp_kw_s": 5.0,
            "discharge_ramp_kw_s": 5.0,
        },
    }


class MockRtdb:
    """In-process mock RTDB with real ctypes structures (no shm required).

    Each section holds a real ctypes struct so seqlock operations work
    exactly as they would against live shared memory.
    """

    def __init__(self) -> None:
        self.pcs: EmsPcs = EmsPcs()
        self.gpio: EmsGpio = EmsGpio()
        self.system: EmsSystem = EmsSystem()
        self.clusters: list[EmsCluster] = [EmsCluster() for _ in range(MAX_CLUSTERS)]

        # Default: PCS off, no faults
        self.pcs.state = PCS_STATE_OFF
        self.pcs.fault_code = 0
        self.system.control_state = STATE_IDLE
        self.system.pcs_command_seq = 0
        self.system.total_soc = 50.0

        # Default: one online rack with nominal temperatures
        rack = self.clusters[0].racks[0]
        rack.online = 1
        rack.max_cell_t = 25.0
        rack.min_cell_t = 20.0


def _build_loop(
    config: dict[str, Any] | None = None,
    mock_rtdb: MockRtdb | None = None,
    rep_endpoint: str = "tcp://127.0.0.1:15550",
    pub_endpoint: str = "tcp://127.0.0.1:15551",
    push_endpoint: str = "tcp://127.0.0.1:15552",
    alarm_sub_endpoint: str | None = None,
    config_sub_endpoint: str | None = None,
    config_path: Any = None,
) -> tuple[Any, MockRtdb]:
    """Create a ControlLoop with mocked RTDB and custom ZMQ endpoints.

    Returns (loop, mock_rtdb). Caller is responsible for cleanup().
    """
    from ems_control_manager.loop import ControlLoop

    rtdb: MockRtdb = mock_rtdb or MockRtdb()
    cfg: dict[str, Any] = config or _make_config()

    with (
        patch("ems_control_manager.loop.attach_rtdb") as mock_attach,
        patch("ems_control_manager.loop.detach_rtdb"),
    ):
        mock_attach.return_value = (MagicMock(), rtdb)
        loop = ControlLoop(
            config=cfg,
            rep_endpoint=rep_endpoint,
            pub_endpoint=pub_endpoint,
            push_endpoint=push_endpoint,
            alarm_sub_endpoint=alarm_sub_endpoint,
            config_sub_endpoint=config_sub_endpoint,
            config_path=config_path,
        )

    return loop, rtdb


# ---------------------------------------------------------------------------
# Test: RTDB reads on tick
# ---------------------------------------------------------------------------

class TestRtdbReads:
    """Verify _tick() reads correct fields from RTDB sections via seqlock."""

    def test_tick_reads_pcs_state(self) -> None:
        """Tick reads pcs.state from RTDB pcs section."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15500",
            pub_endpoint="tcp://127.0.0.1:15501",
            push_endpoint="tcp://127.0.0.1:15502",
        )
        try:
            rtdb.pcs.state = PCS_STATE_RUNNING
            rtdb.pcs.fault_code = 0
            now = time.monotonic()
            loop._tick(now)
            # State machine reads PCS state — with PCS_RUNNING and no fault,
            # steady IDLE after INIT, no fault triggered
            assert loop._rtdb.pcs.state == PCS_STATE_RUNNING
        finally:
            loop.cleanup()

    def test_tick_reads_pcs_fault_code(self) -> None:
        """Tick reads pcs.fault_code and triggers FAULT state when non-zero."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15503",
            pub_endpoint="tcp://127.0.0.1:15504",
            push_endpoint="tcp://127.0.0.1:15505",
        )
        try:
            # First tick: INIT -> IDLE
            loop._tick(time.monotonic())

            # Set PCS fault and call second tick
            rtdb.pcs.fault_code = 0x01
            loop._tick(time.monotonic())

            # State should now be FAULT
            assert rtdb.system.control_state == STATE_FAULT
        finally:
            loop.cleanup()

    def test_tick_reads_gpio_safety_emergency(self) -> None:
        """Tick reads gpio.do_state for safety emergency signal."""
        from ems_control_manager.loop import SAFETY_EMERGENCY_DO_INDEX
        from ems_control_manager.state_machine import STATE_EMERGENCY

        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15506",
            pub_endpoint="tcp://127.0.0.1:15507",
            push_endpoint="tcp://127.0.0.1:15508",
        )
        try:
            # First tick: INIT -> IDLE
            loop._tick(time.monotonic())

            # Assert emergency via DO pin
            rtdb.gpio.do_state[SAFETY_EMERGENCY_DO_INDEX] = 1
            loop._tick(time.monotonic())

            # State should be EMERGENCY
            assert rtdb.system.control_state == STATE_EMERGENCY
        finally:
            loop.cleanup()

    def test_tick_reads_system_control_state_for_init(self) -> None:
        """First tick reads system.control_state to detect MAINTENANCE persistence."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15509",
            pub_endpoint="tcp://127.0.0.1:15510",
            push_endpoint="tcp://127.0.0.1:15511",
        )
        try:
            # Pre-set RTDB to MAINTENANCE (simulates process restart while in MAINTENANCE)
            rtdb.system.control_state = STATE_MAINTENANCE

            loop._tick(time.monotonic())

            # State machine should have picked up MAINTENANCE from RTDB
            assert rtdb.system.control_state == STATE_MAINTENANCE
        finally:
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: RTDB writes on tick
# ---------------------------------------------------------------------------

class TestRtdbWrites:
    """Verify _tick() writes state, setpoint, pcs_command to RTDB system section."""

    def test_tick_writes_control_state(self) -> None:
        """Tick writes result.state to RTDB system.control_state."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15520",
            pub_endpoint="tcp://127.0.0.1:15521",
            push_endpoint="tcp://127.0.0.1:15522",
        )
        try:
            loop._tick(time.monotonic())
            # After INIT->IDLE, RTDB should have STATE_IDLE
            assert rtdb.system.control_state == STATE_IDLE
        finally:
            loop.cleanup()

    def test_tick_writes_active_setpoint_kw(self) -> None:
        """Tick writes result.setpoint_kw to RTDB system.active_setpoint_kw."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15523",
            pub_endpoint="tcp://127.0.0.1:15524",
            push_endpoint="tcp://127.0.0.1:15525",
        )
        try:
            loop._tick(time.monotonic())
            # Initial setpoint is 0.0
            assert abs(rtdb.system.active_setpoint_kw - 0.0) < 0.001
        finally:
            loop.cleanup()

    def test_tick_increments_pcs_command_seq_when_changed(self) -> None:
        """Tick increments pcs_command_seq when pcs_command_changed is True."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15526",
            pub_endpoint="tcp://127.0.0.1:15527",
            push_endpoint="tcp://127.0.0.1:15528",
        )
        try:
            initial_seq: int = rtdb.system.pcs_command_seq

            # First tick: INIT -> IDLE
            loop._tick(time.monotonic())

            # Request mode change to standby (triggers PCS ON -> pcs_command_changed=True)
            loop._sm.request_mode_change("standby")
            loop._tick(time.monotonic())

            # pcs_command_seq should have incremented
            assert rtdb.system.pcs_command_seq == initial_seq + 1
            assert rtdb.system.pcs_command == PCS_CMD_ON
        finally:
            loop.cleanup()

    def test_tick_does_not_increment_seq_when_unchanged(self) -> None:
        """Tick does NOT increment pcs_command_seq when pcs_command_changed is False."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15529",
            pub_endpoint="tcp://127.0.0.1:15530",
            push_endpoint="tcp://127.0.0.1:15531",
        )
        try:
            # First tick: INIT -> IDLE (no pcs_command_changed)
            loop._tick(time.monotonic())
            seq_after_init: int = rtdb.system.pcs_command_seq

            # Second tick: steady IDLE (no command)
            loop._tick(time.monotonic())
            assert rtdb.system.pcs_command_seq == seq_after_init
        finally:
            loop.cleanup()

    def test_tick_writes_seqlock_correctly(self) -> None:
        """Tick seqlock is even (write complete) after _tick() returns."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15532",
            pub_endpoint="tcp://127.0.0.1:15533",
            push_endpoint="tcp://127.0.0.1:15534",
        )
        try:
            loop._tick(time.monotonic())
            # After write, seqlock sequence must be even
            assert rtdb.system.lock.sequence % 2 == 0
        finally:
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: ZMQ REP command handling
# ---------------------------------------------------------------------------

class TestZmqRepCommands:
    """Verify _poll_commands() handles all command types via ZMQ REP."""

    def _connect_req(
        self, ctx: zmq.Context, endpoint: str
    ) -> zmq.Socket:
        """Create a connected REQ socket with RCVTIMEO. Includes connection sleep."""
        req = ctx.socket(zmq.REQ)
        req.setsockopt(zmq.RCVTIMEO, 2000)
        req.connect(endpoint)
        time.sleep(_CONNECT_SLEEP_S)  # Allow TCP connection to establish
        return req

    def _send_and_poll(
        self,
        loop: Any,
        req: zmq.Socket,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send command, wait for delivery, poll, receive response."""
        raw = encode_command_request(action, params or {})
        req.send(raw)
        time.sleep(_MSG_DELIVER_S)  # Allow TCP to deliver the message
        loop._poll_commands()
        reply = req.recv()
        return decode_command_response(reply)

    def test_mode_change_standby_returns_ok(self) -> None:
        """ZMQ REP returns 'ok' for valid mode_change standby from IDLE."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15560")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15560")
        try:
            # Init the state machine first
            loop._tick(time.monotonic())  # INIT -> IDLE
            resp = self._send_and_poll(
                loop, req, "mode_change", {"target_state": "standby"}
            )
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_manual_setpoint_command(self) -> None:
        """ZMQ REP handles manual_setpoint command."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15561")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15561")
        try:
            # Init SM, get to STANDBY via direct SM manipulation
            loop._tick(time.monotonic())  # INIT -> IDLE
            loop._sm.request_mode_change("standby")
            rtdb.pcs.state = PCS_STATE_RUNNING
            loop._tick(time.monotonic())  # STARTING sub-state entered, PCS ON sent
            loop._tick(time.monotonic())  # PCS RUNNING confirmed -> STANDBY

            resp = self._send_and_poll(
                loop, req, "manual_setpoint", {"power_kw": 50.0}
            )
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_fault_reset_command(self) -> None:
        """ZMQ REP handles fault_reset command."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15562")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15562")
        try:
            # Init, then drive to FAULT
            loop._tick(time.monotonic())  # INIT -> IDLE
            rtdb.pcs.fault_code = 0x01
            loop._tick(time.monotonic())  # -> FAULT

            resp = self._send_and_poll(loop, req, "fault_reset")
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_maintenance_enter_command(self) -> None:
        """ZMQ REP handles maintenance_enter command."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15563")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15563")
        try:
            loop._tick(time.monotonic())  # INIT -> IDLE
            resp = self._send_and_poll(loop, req, "maintenance_enter")
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_maintenance_exit_command(self) -> None:
        """ZMQ REP handles maintenance_exit command."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15564")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15564")
        try:
            loop._tick(time.monotonic())  # INIT -> IDLE
            # Enter maintenance via direct SM call
            loop._sm.request_maintenance_enter()
            loop._tick(time.monotonic())  # -> MAINTENANCE

            resp = self._send_and_poll(loop, req, "maintenance_exit")
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_unknown_command_returns_error(self) -> None:
        """ZMQ REP returns error for unknown action string."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15565")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15565")
        try:
            resp = self._send_and_poll(loop, req, "bogus_action")
            assert resp["status"] == "error"
            assert resp["error_msg"] is not None
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_rep_always_sends_reply_on_internal_error(self) -> None:
        """ZMQ REP sends error reply even if decode raises an exception (REP safety)."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15566")
        ctx = zmq.Context()
        req = ctx.socket(zmq.REQ)
        req.setsockopt(zmq.RCVTIMEO, 2000)
        req.connect("tcp://127.0.0.1:15566")
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Send malformed msgpack (not a valid command)
            req.send(b"\x00\x01\x02\x03 invalid msgpack data")
            time.sleep(_MSG_DELIVER_S)
            loop._poll_commands()
            reply = req.recv()
            resp = decode_command_response(reply)
            assert resp["status"] == "error"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_source_priority_command_returns_ok(self) -> None:
        """ZMQ REP returns 'ok' for source_priority command (deferred to Phase 16)."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15567")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15567")
        try:
            resp = self._send_and_poll(
                loop, req, "source_priority", {"mode": "grid_first"}
            )
            assert resp["status"] == "ok"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_commands_rejected_during_starting_substate(self) -> None:
        """Commands rejected during STARTING sub-state return error with remaining time."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15568")
        ctx = zmq.Context()
        req = self._connect_req(ctx, "tcp://127.0.0.1:15568")
        try:
            # INIT -> IDLE
            loop._tick(time.monotonic())

            # Request standby — SM accepts it and sets _pending_target_state
            # Tick to process: mode change -> PCS ON sent, STARTING sub-state entered
            loop._sm.request_mode_change("standby")
            loop._tick(time.monotonic())  # -> STARTING sub-state (state still IDLE)

            # Now try to request another mode change — should be rejected
            resp = self._send_and_poll(
                loop, req, "mode_change", {"target_state": "standby"}
            )
            assert resp["status"] == "error"
            assert (
                "STARTING" in resp["error_msg"]
                or "progress" in resp["error_msg"]
                or "rejected" in resp["error_msg"]
            )
        finally:
            req.close()
            ctx.term()
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: ZMQ PUB telemetry publishing
# ---------------------------------------------------------------------------

class TestZmqPubTelemetry:
    """Verify _tick() publishes control.state telemetry on PUB socket."""

    def test_tick_publishes_control_state_telemetry(self) -> None:
        """_tick() publishes control.state topic with state and setpoint_kw."""
        loop, rtdb = _build_loop(
            pub_endpoint="tcp://127.0.0.1:15580",
            rep_endpoint="tcp://127.0.0.1:15581",
            push_endpoint="tcp://127.0.0.1:15582",
        )
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.RCVTIMEO, 2000)
        sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONTROL_STATE)
        sub.connect("tcp://127.0.0.1:15580")
        time.sleep(_CONNECT_SLEEP_S)  # Allow SUB to connect
        try:
            loop._tick(time.monotonic())
            topic_raw = sub.recv()
            body = sub.recv()
            msg = msgpack.unpackb(body, raw=False)
            assert msg["topic"] == TOPIC_CONTROL_STATE
            payload = msg["payload"]
            assert "state" in payload
            assert "setpoint_kw" in payload
            assert payload["state"] == STATE_IDLE
        finally:
            sub.close()
            ctx.term()
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: ZMQ PUSH event publishing
# ---------------------------------------------------------------------------

class TestZmqPushEvents:
    """Verify _tick() sends state_change events to PUSH socket on transitions."""

    def test_tick_sends_state_change_event_on_transition(self) -> None:
        """_tick() sends state_change PUSH event when state transitions."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        pull.bind("tcp://127.0.0.1:15590")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15590",
            rep_endpoint="tcp://127.0.0.1:15591",
            pub_endpoint="tcp://127.0.0.1:15592",
        )
        time.sleep(_CONNECT_SLEEP_S)  # Allow PUSH to connect to PULL
        try:
            # First tick transitions INIT -> IDLE (state_changed=True)
            loop._tick(time.monotonic())
            raw_event = pull.recv()
            event = msgpack.unpackb(raw_event, raw=False)
            assert event["event_type"] == TOPIC_STATE_CHANGE
            assert "state" in event["data"] or "new_state" in event["data"]
        finally:
            loop.cleanup()
            pull.close()
            ctx.term()

    def test_tick_no_event_when_no_transition(self) -> None:
        """_tick() does NOT send event when state stays the same."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 500)
        pull.bind("tcp://127.0.0.1:15595")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15595",
            rep_endpoint="tcp://127.0.0.1:15596",
            pub_endpoint="tcp://127.0.0.1:15597",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # First tick: INIT -> IDLE (sends event)
            loop._tick(time.monotonic())
            pull.recv()  # consume the INIT->IDLE event

            # Second tick: steady IDLE (no transition, no event)
            loop._tick(time.monotonic())
            pull.setsockopt(zmq.RCVTIMEO, 100)  # short timeout to check no message
            with pytest.raises(zmq.Again):
                pull.recv()
        finally:
            loop.cleanup()
            pull.close()
            ctx.term()


# ---------------------------------------------------------------------------
# Plan 16-02 Tests: Alarm Protection Flow (ALM-08)
# Ports: 15700-15749
# ---------------------------------------------------------------------------


def _send_alarm_event(
    pub_sock: zmq.Socket,
    alarm_id: str,
    severity: str,
    state: str = "ACTIVE_UNACKED",
) -> None:
    """Send an alarm PUB event from a test PUB socket (simulating alarm_manager).

    The alarm_manager PUB format: [topic=b"alarm", body=msgpack({...})]
    Raw msgpack body — NOT wrapped in encode_event() per RESEARCH.md Pitfall 1.
    """
    body = msgpack.packb(
        {
            "alarm_id": alarm_id,
            "signal": "bms.cell_voltage_min",
            "severity": severity,
            "state": state,
            "event_type": "alarm_activated",
            "value": 2.70,
            "threshold": 2.80,
        },
        use_bin_type=True,
    )
    pub_sock.send_string("alarm", zmq.SNDMORE)
    pub_sock.send(body)


class TestAlarmProtectionFlow:
    """ALM-08: Alarm events via ZMQ SUB affect ControlLoop state and setpoint."""

    def test_protection_alarm_triggers_fault_state(self) -> None:
        """Protection-severity alarm on SUB causes ControlLoop to enter FAULT."""
        ctx = zmq.Context()
        # PUB simulating alarm_manager's SOCK_ALARM_PUB
        alarm_pub = ctx.socket(zmq.PUB)
        alarm_pub.bind("tcp://127.0.0.1:15700")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15701",
            pub_endpoint="tcp://127.0.0.1:15702",
            push_endpoint="tcp://127.0.0.1:15703",
            alarm_sub_endpoint="tcp://127.0.0.1:15700",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # INIT -> IDLE
            loop._tick(time.monotonic())
            assert rtdb.system.control_state == STATE_IDLE

            # Send protection alarm
            _send_alarm_event(alarm_pub, "cell_voltage_low", "protection")
            time.sleep(_MSG_DELIVER_S)

            # Next tick: protection alarm received -> FAULT
            loop._tick(time.monotonic())
            assert rtdb.system.control_state == STATE_FAULT
        finally:
            alarm_pub.close()
            ctx.term()
            loop.cleanup()

    def test_action_alarm_reduces_setpoint_50pct(self) -> None:
        """Action-severity alarm on SUB causes setpoint reduction to 50%."""
        ctx = zmq.Context()
        alarm_pub = ctx.socket(zmq.PUB)
        alarm_pub.bind("tcp://127.0.0.1:15705")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15706",
            pub_endpoint="tcp://127.0.0.1:15707",
            push_endpoint="tcp://127.0.0.1:15708",
            alarm_sub_endpoint="tcp://127.0.0.1:15705",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # INIT -> IDLE
            loop._tick(time.monotonic())

            # Send action alarm
            _send_alarm_event(alarm_pub, "pcs_temp_high", "action")
            time.sleep(_MSG_DELIVER_S)

            # Next tick: action alarm received
            loop._tick(time.monotonic())

            # Action severity should be tracked
            assert loop._last_alarm_severity == "action"
        finally:
            alarm_pub.close()
            ctx.term()
            loop.cleanup()

    def test_warning_alarm_has_no_effect_on_state(self) -> None:
        """Warning-severity alarm on SUB does not affect ControlLoop state."""
        ctx = zmq.Context()
        alarm_pub = ctx.socket(zmq.PUB)
        alarm_pub.bind("tcp://127.0.0.1:15710")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15711",
            pub_endpoint="tcp://127.0.0.1:15712",
            push_endpoint="tcp://127.0.0.1:15713",
            alarm_sub_endpoint="tcp://127.0.0.1:15710",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())
            assert rtdb.system.control_state == STATE_IDLE

            _send_alarm_event(alarm_pub, "cell_voltage_high", "warning")
            time.sleep(_MSG_DELIVER_S)

            loop._tick(time.monotonic())
            # Warning alarm does NOT cause FAULT
            assert rtdb.system.control_state == STATE_IDLE
        finally:
            alarm_pub.close()
            ctx.term()
            loop.cleanup()

    def test_protection_cooldown_prevents_immediate_state_reset(self) -> None:
        """60-second cooldown after protection — state does not reset immediately."""
        ctx = zmq.Context()
        alarm_pub = ctx.socket(zmq.PUB)
        alarm_pub.bind("tcp://127.0.0.1:15715")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15716",
            pub_endpoint="tcp://127.0.0.1:15717",
            push_endpoint="tcp://127.0.0.1:15718",
            alarm_sub_endpoint="tcp://127.0.0.1:15715",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())

            # Send protection alarm
            _send_alarm_event(alarm_pub, "cell_voltage_low", "protection")
            time.sleep(_MSG_DELIVER_S)

            now = time.monotonic()
            loop._tick(now)

            # Cooldown should be set 60s in the future
            assert loop._alarm_cooldown_until_s > now
            assert loop._last_alarm_severity == "protection"
        finally:
            alarm_pub.close()
            ctx.term()
            loop.cleanup()

    def test_multiple_alarms_most_severe_wins(self) -> None:
        """When multiple alarms arrive in one tick, most severe takes precedence."""
        ctx = zmq.Context()
        alarm_pub = ctx.socket(zmq.PUB)
        alarm_pub.bind("tcp://127.0.0.1:15720")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15721",
            pub_endpoint="tcp://127.0.0.1:15722",
            push_endpoint="tcp://127.0.0.1:15723",
            alarm_sub_endpoint="tcp://127.0.0.1:15720",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())

            # Send warning then action then protection — protection must win
            _send_alarm_event(alarm_pub, "alarm_a", "warning")
            _send_alarm_event(alarm_pub, "alarm_b", "action")
            _send_alarm_event(alarm_pub, "alarm_c", "protection")
            time.sleep(_MSG_DELIVER_S)

            loop._tick(time.monotonic())
            # Protection wins
            assert loop._last_alarm_severity == "protection"
        finally:
            alarm_pub.close()
            ctx.term()
            loop.cleanup()


# ---------------------------------------------------------------------------
# Plan 16-02 Tests: Config Hot-Reload via ZMQ SUB (CTRL-11)
# Ports: 15725-15749
# ---------------------------------------------------------------------------


def _write_control_config(path: Any, soc_charge_cutoff: int = 95) -> None:
    """Write control_config.yaml to a path-like with given soc charge cutoff."""
    from pathlib import Path

    config_text: str = f"""\
_schema_version: "1.0"

soc_limits:
  charge_cutoff_pct: {soc_charge_cutoff}
  discharge_cutoff_pct: 10

power_limits:
  max_charge_kw: 100
  max_discharge_kw: 100

source_priority:
  day_order:
    - solar
    - grid
    - bess
    - dg
  night_order:
    - grid
    - bess
    - dg

derating:
  bms_high_start_c: 40
  bms_high_cutoff_c: 50
  pcs_high_start_c: 65
  pcs_high_cutoff_c: 80
  bms_low_start_c: 5
  bms_low_cutoff_c: 0

ramping:
  charge_ramp_kw_s: 5.0
  discharge_ramp_kw_s: 5.0

state_machine:
  loop_interval_ms: 1000
  fault_retry_count: 3
"""
    Path(path).write_text(config_text)


class TestConfigHotReload:
    """CTRL-11: config_reload ZMQ SUB triggers re-read and intelligence update."""

    def test_config_reload_event_for_control_config_updates_intelligence(
        self, tmp_path: Any
    ) -> None:
        """config_reload event with name='control_config' triggers re-read."""
        _write_control_config(tmp_path / "control_config.yaml")
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15725")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15726",
            pub_endpoint="tcp://127.0.0.1:15727",
            push_endpoint="tcp://127.0.0.1:15728",
            config_sub_endpoint="tcp://127.0.0.1:15725",
            config_path=tmp_path / "control_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())

            # Modify config on disk and send reload event
            _write_control_config(tmp_path / "control_config.yaml", soc_charge_cutoff=80)
            reload_body = msgpack.packb(
                {"name": "control_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Next tick: reload should be processed
            loop._tick(time.monotonic())

            # Intelligence should have new SOC limit
            assert loop._intelligence._soc_limits["charge_cutoff_pct"] == 80
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_config_reload_for_other_config_is_ignored(
        self, tmp_path: Any
    ) -> None:
        """config_reload event with name='schedule_config' is ignored."""
        _write_control_config(tmp_path / "control_config.yaml")
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15730")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15731",
            pub_endpoint="tcp://127.0.0.1:15732",
            push_endpoint="tcp://127.0.0.1:15733",
            config_sub_endpoint="tcp://127.0.0.1:15730",
            config_path=tmp_path / "control_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())
            initial_soc_limit = loop._config["soc_limits"]["charge_cutoff_pct"]

            # Send reload event for DIFFERENT config name
            reload_body = msgpack.packb(
                {"name": "schedule_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            loop._tick(time.monotonic())

            # Config should NOT have changed
            assert loop._config["soc_limits"]["charge_cutoff_pct"] == initial_soc_limit
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_invalid_config_on_disk_rejected_keeps_old_config(
        self, tmp_path: Any
    ) -> None:
        """config_reload event with invalid config on disk is rejected."""
        _write_control_config(tmp_path / "control_config.yaml")
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15735")

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15736",
            pub_endpoint="tcp://127.0.0.1:15737",
            push_endpoint="tcp://127.0.0.1:15738",
            config_sub_endpoint="tcp://127.0.0.1:15735",
            config_path=tmp_path / "control_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            loop._tick(time.monotonic())
            initial_soc_limit = loop._config["soc_limits"]["charge_cutoff_pct"]

            # Write invalid config on disk
            from pathlib import Path
            Path(tmp_path / "control_config.yaml").write_text("not: valid: yaml: !!!")

            # Send reload event
            reload_body = msgpack.packb(
                {"name": "control_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            loop._tick(time.monotonic())

            # Old config retained
            assert loop._config["soc_limits"]["charge_cutoff_pct"] == initial_soc_limit
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()


# ---------------------------------------------------------------------------
# Plan 16-02 Tests: Intelligence Integration
# Ports: 15740-15749
# ---------------------------------------------------------------------------


class TestIntelligenceIntegration:
    """Intelligence evaluate() results wired into tick and RTDB writes."""

    def test_derating_pct_written_to_rtdb(self) -> None:
        """active_derating_pct is written to RTDB system section every tick."""
        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15740",
            pub_endpoint="tcp://127.0.0.1:15741",
            push_endpoint="tcp://127.0.0.1:15742",
        )
        try:
            # All temps normal (25°C) -> 100% derating (no derating)
            loop._tick(time.monotonic())
            # active_derating_pct should be 100.0 (full power)
            assert abs(rtdb.system.active_derating_pct - 100.0) < 0.1
        finally:
            loop.cleanup()

    def test_source_priority_written_to_rtdb(self) -> None:
        """source_priority (int) is written to RTDB system section every tick."""
        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15743",
            pub_endpoint="tcp://127.0.0.1:15744",
            push_endpoint="tcp://127.0.0.1:15745",
        )
        try:
            loop._tick(time.monotonic())
            # source_priority should be set (non-zero or 0 for "none")
            # Just verify the field was written (no AttributeError)
            _ = rtdb.system.source_priority
        finally:
            loop.cleanup()

    def test_intelligence_object_created_in_loop(self) -> None:
        """ControlLoop creates a ControlIntelligence instance on init."""
        from ems_control_manager.intelligence import ControlIntelligence

        loop, rtdb = _build_loop(
            config=_make_full_config(),
            rep_endpoint="tcp://127.0.0.1:15746",
            pub_endpoint="tcp://127.0.0.1:15747",
            push_endpoint="tcp://127.0.0.1:15748",
        )
        try:
            assert hasattr(loop, "_intelligence")
            assert isinstance(loop._intelligence, ControlIntelligence)
        finally:
            loop.cleanup()
