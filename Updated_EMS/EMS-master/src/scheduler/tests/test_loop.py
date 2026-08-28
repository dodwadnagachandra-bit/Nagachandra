"""Tests for SchedulerLoop -- 1Hz evaluation, ZMQ command dispatch, hot-reload, telemetry."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

import msgpack
import pytest
import zmq

from ems_common.ipc import (
    TOPIC_SCHEDULE,
    decode_command_request,
    encode_command_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_config(
    mode: str = "manual",
    time_windows: list[dict[str, Any]] | None = None,
    power_curve: list[float] | None = None,
    day_night: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a minimal schedule config dict."""
    return {
        "_schema_version": "1.0",
        "mode": mode,
        "time_windows": time_windows or [],
        "day_night": day_night or {"day_start": "06:00", "night_start": "18:00"},
        "power_curve": power_curve or [0.0] * 96,
    }


class MockRepServer:
    """Background thread that auto-replies OK to all ZMQ REQ messages.

    Collects (action, params) tuples for assertion after the test.
    """

    def __init__(self, ctx: zmq.Context, endpoint: str) -> None:
        self._sock: zmq.Socket = ctx.socket(zmq.REP)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.bind(endpoint)
        self._stop: threading.Event = threading.Event()
        self.received: list[tuple[str, dict[str, Any]]] = []
        self._reply_status: str = "ok"
        self._reply_error: str | None = None
        self._thread: threading.Thread = threading.Thread(
            target=self._run, daemon=True
        )

    def start(self) -> MockRepServer:
        self._thread.start()
        return self

    def set_reject(self, error_msg: str = "Not allowed") -> None:
        """Make next replies return error."""
        self._reply_status = "error"
        self._reply_error = error_msg

    def set_accept(self) -> None:
        """Make next replies return ok."""
        self._reply_status = "ok"
        self._reply_error = None

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        if not self._sock.closed:
            self._sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._sock.poll(50):
                try:
                    raw: bytes = self._sock.recv(zmq.NOBLOCK)
                except zmq.Again:
                    continue
                action, params = decode_command_request(raw)
                self.received.append((action, params))
                self._sock.send(
                    encode_command_response(
                        self._reply_status, error_msg=self._reply_error
                    )
                )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def zmq_ctx() -> zmq.Context:
    ctx: zmq.Context = zmq.Context()
    yield ctx  # type: ignore[misc]
    ctx.term()


@pytest.fixture
def endpoints() -> dict[str, str]:
    return {
        "req": f"tcp://127.0.0.1:{_find_free_port()}",
        "config_pub": f"tcp://127.0.0.1:{_find_free_port()}",
        "pub": f"tcp://127.0.0.1:{_find_free_port()}",
    }


@pytest.fixture
def rep_server(zmq_ctx: zmq.Context, endpoints: dict[str, str]) -> MockRepServer:
    """Auto-replying mock REP server (runs in background thread)."""
    server: MockRepServer = MockRepServer(zmq_ctx, endpoints["req"]).start()
    yield server  # type: ignore[misc]
    server.stop()


@pytest.fixture
def config_pub_sock(zmq_ctx: zmq.Context, endpoints: dict[str, str]) -> zmq.Socket:
    sock: zmq.Socket = zmq_ctx.socket(zmq.PUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(endpoints["config_pub"])
    yield sock  # type: ignore[misc]
    sock.close()


@pytest.fixture
def tel_sub(zmq_ctx: zmq.Context, endpoints: dict[str, str]) -> zmq.Socket:
    sock: zmq.Socket = zmq_ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    sock.connect(endpoints["pub"])
    yield sock  # type: ignore[misc]
    sock.close()


def _make_loop(
    config: dict[str, Any],
    endpoints: dict[str, str],
    now_func: Any = None,
) -> Any:
    """Create a SchedulerLoop wired to test endpoints."""
    from ems_scheduler.loop import SchedulerLoop

    return SchedulerLoop(
        config,
        req_endpoint=endpoints["req"],
        config_sub_endpoint=endpoints["config_pub"],
        pub_endpoint=endpoints["pub"],
        now_func=now_func,
    )


# ---------------------------------------------------------------------------
# Core loop tests
# ---------------------------------------------------------------------------


class TestManualMode:
    """In manual mode, scheduler sends no schedule commands."""

    def test_manual_mode_no_commands(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        config: dict[str, Any] = _make_config(mode="manual")
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)  # Let background thread process

            # Day/night may send source_priority, but no manual_setpoint
            setpoint_cmds: list = [
                c for c in rep_server.received if c[0] == "manual_setpoint"
            ]
            assert len(setpoint_cmds) == 0, "Manual mode should not send manual_setpoint"
        finally:
            loop_obj.cleanup()


class TestTimeOfDay:
    """time_of_day mode sends setpoints on window change."""

    def test_time_of_day_sends_setpoint_on_change(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
            {"start": "22:00", "end": "06:00", "action": "charge", "power_kw": 25},
        ]
        config: dict[str, Any] = _make_config(mode="time_of_day", time_windows=windows)
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)

            actions: list[str] = [c[0] for c in rep_server.received]
            assert "source_priority" in actions, "Should send source_priority MANUAL first"
            assert "manual_setpoint" in actions, "Should send manual_setpoint"

            # source_priority should come before manual_setpoint
            sp_idx: int = actions.index("source_priority")
            ms_idx: int = actions.index("manual_setpoint")
            assert sp_idx < ms_idx, "source_priority must precede manual_setpoint"

            # Check values
            sp_cmd = rep_server.received[sp_idx]
            assert sp_cmd[1]["mode"] == "manual"

            ms_cmd = rep_server.received[ms_idx]
            assert ms_cmd[1]["power_kw"] == 50.0
        finally:
            loop_obj.cleanup()

    def test_time_of_day_no_resend_same_window(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
        ]
        config: dict[str, Any] = _make_config(mode="time_of_day", time_windows=windows)
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            # First tick sends commands
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            count_after_first: int = len(rep_server.received)
            assert count_after_first > 0

            # Second tick -- no change, should not send setpoint again
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            new_cmds: list = rep_server.received[count_after_first:]
            setpoint_cmds: list = [c for c in new_cmds if c[0] == "manual_setpoint"]
            assert len(setpoint_cmds) == 0, "Should not resend for same window"
        finally:
            loop_obj.cleanup()


class TestCurve:
    """Curve mode sends setpoints on index change."""

    def test_curve_sends_setpoint_on_index_change(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        curve: list[float] = [0.0] * 96
        curve[40] = 50.0   # 10:00 index
        curve[41] = -25.0  # 10:15 index
        config: dict[str, Any] = _make_config(mode="curve", power_curve=curve)

        time_10_00: datetime = datetime(2026, 3, 15, 10, 0, 0)
        time_10_15: datetime = datetime(2026, 3, 15, 10, 15, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: time_10_00)
        try:
            # First tick at 10:00
            loop_obj._evaluate_tick(time_10_00)
            time.sleep(0.1)

            ms_cmds_1: list = [
                c for c in rep_server.received if c[0] == "manual_setpoint"
            ]
            assert len(ms_cmds_1) == 1
            assert ms_cmds_1[0][1]["power_kw"] == 50.0

            # Second tick at 10:15 (different index)
            count_before: int = len(rep_server.received)
            loop_obj._evaluate_tick(time_10_15)
            time.sleep(0.1)

            new_ms: list = [
                c for c in rep_server.received[count_before:] if c[0] == "manual_setpoint"
            ]
            assert len(new_ms) == 1
            assert new_ms[0][1]["power_kw"] == -25.0
        finally:
            loop_obj.cleanup()

    def test_curve_no_resend_same_index(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        curve: list[float] = [0.0] * 96
        curve[40] = 50.0
        config: dict[str, Any] = _make_config(mode="curve", power_curve=curve)
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            count_after_first: int = len(rep_server.received)

            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            new_ms: list = [
                c for c in rep_server.received[count_after_first:]
                if c[0] == "manual_setpoint"
            ]
            assert len(new_ms) == 0, "Same curve index should not resend"
        finally:
            loop_obj.cleanup()


class TestStartup:
    """First tick always evaluates and sends commands."""

    def test_startup_immediate_evaluation(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 30},
        ]
        config: dict[str, Any] = _make_config(mode="time_of_day", time_windows=windows)
        fixed_time: datetime = datetime(2026, 3, 15, 12, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            assert any(
                c[0] == "manual_setpoint" for c in rep_server.received
            ), "First tick must send commands immediately"
        finally:
            loop_obj.cleanup()


# ---------------------------------------------------------------------------
# Day/night tests
# ---------------------------------------------------------------------------


class TestDayNight:
    """Day/night switching independent of schedule mode."""

    def test_day_night_transition_sends_source_priority(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        config: dict[str, Any] = _make_config(
            mode="manual",
            day_night={"day_start": "06:00", "night_start": "18:00"},
        )
        daytime: datetime = datetime(2026, 3, 15, 10, 0, 0)
        nighttime: datetime = datetime(2026, 3, 15, 20, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: daytime)
        try:
            # First tick at 10:00 (day)
            loop_obj._evaluate_tick(daytime)
            time.sleep(0.1)

            sp_cmds_1: list = [
                c for c in rep_server.received if c[0] == "source_priority"
            ]
            assert len(sp_cmds_1) >= 1
            assert sp_cmds_1[0][1]["mode"] == "day"

            # Transition to night
            count_before: int = len(rep_server.received)
            loop_obj._evaluate_tick(nighttime)
            time.sleep(0.1)

            sp_cmds_2: list = [
                c for c in rep_server.received[count_before:]
                if c[0] == "source_priority"
            ]
            assert len(sp_cmds_2) >= 1
            assert sp_cmds_2[-1][1]["mode"] == "night"
        finally:
            loop_obj.cleanup()

    def test_day_night_runs_in_manual_mode(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        config: dict[str, Any] = _make_config(
            mode="manual",
            day_night={"day_start": "06:00", "night_start": "18:00"},
        )
        nighttime: datetime = datetime(2026, 3, 15, 22, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: nighttime)
        try:
            loop_obj._evaluate_tick(nighttime)
            time.sleep(0.1)

            sp_cmds: list = [
                c for c in rep_server.received if c[0] == "source_priority"
            ]
            assert len(sp_cmds) >= 1, "Day/night should work in manual mode"
            assert sp_cmds[0][1]["mode"] == "night"
        finally:
            loop_obj.cleanup()

    def test_schedule_mode_overrides_day_night(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        """In time_of_day mode, source_priority is MANUAL (overrides day/night)."""
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
        ]
        config: dict[str, Any] = _make_config(
            mode="time_of_day",
            time_windows=windows,
            day_night={"day_start": "06:00", "night_start": "18:00"},
        )
        daytime: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: daytime)
        try:
            loop_obj._evaluate_tick(daytime)
            time.sleep(0.1)

            sp_cmds: list = [
                c for c in rep_server.received if c[0] == "source_priority"
            ]
            for cmd in sp_cmds:
                assert cmd[1]["mode"] == "manual", (
                    f"In time_of_day mode, source_priority should be manual, "
                    f"got {cmd[1]['mode']}"
                )
        finally:
            loop_obj.cleanup()

    def test_mode_change_to_manual_restores_day_night(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        """Switching from time_of_day to manual restores day/night priority."""
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
        ]
        config: dict[str, Any] = _make_config(
            mode="time_of_day",
            time_windows=windows,
            day_night={"day_start": "06:00", "night_start": "18:00"},
        )
        daytime: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: daytime)
        try:
            # First tick in time_of_day -- sets MANUAL priority
            loop_obj._evaluate_tick(daytime)
            time.sleep(0.1)
            assert loop_obj._schedule_owns_priority is True

            # Simulate mode change to manual
            count_before: int = len(rep_server.received)
            loop_obj._mode = "manual"
            loop_obj._evaluate_tick(daytime)
            time.sleep(0.1)

            # Should have restored day source_priority
            sp_cmds: list = [
                c for c in rep_server.received[count_before:]
                if c[0] == "source_priority"
            ]
            assert len(sp_cmds) >= 1
            assert sp_cmds[-1][1]["mode"] == "day"
            assert loop_obj._schedule_owns_priority is False
        finally:
            loop_obj.cleanup()


# ---------------------------------------------------------------------------
# Hot-reload tests
# ---------------------------------------------------------------------------


class TestHotReload:
    """Config reload resets tracking state."""

    def test_config_reload_resets_tracking(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
        ]
        config: dict[str, Any] = _make_config(mode="time_of_day", time_windows=windows)
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            # First tick
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            assert loop_obj._last_window_result is not None

            # Simulate config reload
            loop_obj._on_config_reloaded(config)
            assert loop_obj._last_window_result is None
            assert loop_obj._last_curve_index is None
            assert loop_obj._last_day_night is None

            # Next tick should send commands again
            count_before: int = len(rep_server.received)
            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)

            new_ms: list = [
                c for c in rep_server.received[count_before:]
                if c[0] == "manual_setpoint"
            ]
            assert len(new_ms) >= 1, "After reload, should re-send commands"
        finally:
            loop_obj.cleanup()

    def test_config_reload_applies_new_mode(
        self, endpoints: dict[str, str], rep_server: MockRepServer
    ) -> None:
        config: dict[str, Any] = _make_config(mode="manual")
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            assert loop_obj._mode == "manual"

            new_config: dict[str, Any] = _make_config(
                mode="time_of_day",
                time_windows=[
                    {"start": "06:00", "end": "18:00", "action": "charge", "power_kw": 20}
                ],
            )
            loop_obj._on_config_reloaded(new_config)
            assert loop_obj._mode == "time_of_day"
        finally:
            loop_obj.cleanup()


# ---------------------------------------------------------------------------
# Telemetry tests
# ---------------------------------------------------------------------------


class TestTelemetry:
    """Telemetry PUB publishes schedule state each tick."""

    def test_telemetry_publishes_state(
        self,
        endpoints: dict[str, str],
        rep_server: MockRepServer,
        tel_sub: zmq.Socket,
    ) -> None:
        windows: list[dict[str, Any]] = [
            {"start": "06:00", "end": "18:00", "action": "discharge", "power_kw": 50},
        ]
        config: dict[str, Any] = _make_config(mode="time_of_day", time_windows=windows)
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            time.sleep(0.1)  # Give SUB time to connect

            loop_obj._evaluate_tick(fixed_time)
            time.sleep(0.1)
            loop_obj._publish_telemetry(fixed_time)

            if tel_sub.poll(1000):
                topic_frame: bytes = tel_sub.recv()
                body_frame: bytes = tel_sub.recv()

                topic: str = topic_frame.decode("utf-8")
                assert topic == TOPIC_SCHEDULE

                body: dict = msgpack.unpackb(body_frame, raw=False)
                payload: dict = body["payload"]
                assert payload["mode"] == "time_of_day"
                assert payload["day_night"] is not None
                assert "active_window" in payload
            else:
                pytest.fail("No telemetry received within timeout")
        finally:
            loop_obj.cleanup()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """REQ timeout recovery and command rejection logging."""

    def test_req_timeout_recreates_socket(
        self, endpoints: dict[str, str]
    ) -> None:
        """When REQ poll times out (no reply), socket is closed and recreated."""
        # No rep_server -- nobody answers
        config: dict[str, Any] = _make_config(mode="manual")
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            old_req = loop_obj._req

            result: bool = loop_obj._send_command(
                "manual_setpoint", {"power_kw": 10.0}, timeout_ms=100
            )

            assert result is False, "Should return False on timeout"
            assert loop_obj._req is not old_req, "Socket should have been recreated"
            assert old_req.closed, "Old socket should be closed"
        finally:
            loop_obj.cleanup()

    def test_command_rejection_logged(
        self,
        endpoints: dict[str, str],
        rep_server: MockRepServer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When control_manager rejects command, WARNING is logged."""
        config: dict[str, Any] = _make_config(mode="manual")
        fixed_time: datetime = datetime(2026, 3, 15, 10, 0, 0)

        rep_server.set_reject("Not allowed")
        loop_obj = _make_loop(config, endpoints, now_func=lambda: fixed_time)
        try:
            with caplog.at_level(logging.WARNING, logger="ems_scheduler.loop"):
                result: bool = loop_obj._send_command(
                    "manual_setpoint", {"power_kw": 10.0}
                )

            assert result is False
            assert any(
                "rejected" in r.message.lower() or "not allowed" in r.message.lower()
                for r in caplog.records
            ), (
                f"Should log warning about rejection. "
                f"Records: {[r.message for r in caplog.records]}"
            )
        finally:
            loop_obj.cleanup()
