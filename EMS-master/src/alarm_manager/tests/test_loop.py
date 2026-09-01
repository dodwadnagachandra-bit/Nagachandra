"""
Tests for AlarmLoop — 1Hz alarm evaluation loop with RTDB I/O and ZMQ.

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

from pathlib import Path

from ems_common.ipc import (
    SOCK_CONFIG_PUB,
    TOPIC_ALARM,
    TOPIC_CONFIG_RELOAD,
    decode_command_response,
    encode_command_request,
)
from ems_common.rtdb import (
    EmsCluster,
    EmsPcs,
    EmsRtdb,
    MAX_CLUSTERS,
    MAX_RACKS_PER_CLUSTER,
)

# Short sleep to allow ZMQ TCP connections to establish
_CONNECT_SLEEP_S: float = 0.05
# Short sleep to allow a sent message to arrive at the receiving peer
_MSG_DELIVER_S: float = 0.02


# ---------------------------------------------------------------------------
# Minimal test alarm config (3 rules — faster than full 9-rule config)
# ---------------------------------------------------------------------------

def _make_alarm_config() -> dict[str, Any]:
    """Return a minimal alarm config with 3 rules for focused tests."""
    return {
        "defaults": {
            "hysteresis_pct": 2.0,
            "delay_ms": 0,
        },
        "rules": {
            "cell_voltage_high": {
                "signal": "bms.cell_voltage_max",
                "severity": "warning",
                "high_threshold": 3.65,
                "enabled": True,
            },
            "cell_voltage_low": {
                "signal": "bms.cell_voltage_min",
                "severity": "protection",
                "low_threshold": 2.80,
                "enabled": True,
            },
            "pcs_temp_high": {
                "signal": "pcs.internal_temp_c",
                "severity": "action",
                "high_threshold": 75.0,
                "enabled": True,
            },
        },
    }


# ---------------------------------------------------------------------------
# Mock RTDB
# ---------------------------------------------------------------------------

class MockRtdb:
    """In-process mock RTDB with real ctypes structures (no shm required).

    Each section holds a real ctypes struct so seqlock operations work
    exactly as they would against live shared memory.
    """

    def __init__(self) -> None:
        self.clusters: list[EmsCluster] = [EmsCluster() for _ in range(MAX_CLUSTERS)]
        self.pcs: EmsPcs = EmsPcs()

        # Default: one online rack with nominal values
        rack = self.clusters[0].racks[0]
        rack.online = 1
        rack.max_cell_v = 3.50
        rack.min_cell_v = 3.45
        rack.pack_soc = 60.0
        rack.max_cell_t = 25.0
        rack.min_cell_t = 20.0
        self.pcs.dc_voltage = 700.0
        self.pcs.temperature = 35.0


def _build_loop(
    config: dict[str, Any] | None = None,
    mock_rtdb: MockRtdb | None = None,
    rep_endpoint: str = "tcp://127.0.0.1:15600",
    push_endpoint: str = "tcp://127.0.0.1:15601",
    pub_endpoint: str = "tcp://127.0.0.1:15602",
    config_sub_endpoint: str | None = None,
    config_path: Path | None = None,
) -> tuple[Any, MockRtdb]:
    """Create an AlarmLoop with mocked RTDB and custom ZMQ endpoints.

    Returns (loop, mock_rtdb). Caller is responsible for cleanup().
    """
    from ems_alarm_manager.loop import AlarmLoop

    rtdb: MockRtdb = mock_rtdb or MockRtdb()
    cfg: dict[str, Any] = config or _make_alarm_config()

    with (
        patch("ems_alarm_manager.loop.attach_rtdb") as mock_attach,
        patch("ems_alarm_manager.loop.detach_rtdb"),
    ):
        mock_attach.return_value = (MagicMock(), rtdb)
        loop = AlarmLoop(
            config=cfg,
            rep_endpoint=rep_endpoint,
            push_endpoint=push_endpoint,
            pub_endpoint=pub_endpoint,
            config_sub_endpoint=config_sub_endpoint,
            config_path=config_path,
        )

    return loop, rtdb


def _write_alarm_config(path: Path, high_threshold: float = 3.65, enabled: bool = True) -> None:
    """Write a minimal valid alarms_config.yaml with configurable threshold.

    Uses only cell_voltage_high rule with `pcs_temp_high` mapped to pcs_overtemp
    to avoid schema additionalProperties violations.
    Schema allows only named rules: cell_voltage_high, cell_voltage_low,
    cell_temp_high, cell_temp_low, soc_high, soc_low, pcs_overtemp,
    bus_voltage_high, bus_voltage_low.
    """
    text: str = f"""\
_schema_version: "1.0"

defaults:
  hysteresis_pct: 2.0
  delay_ms: 0

rules:
  cell_voltage_high:
    signal: bms.cell_voltage_max
    severity: warning
    high_threshold: {high_threshold}
    enabled: {str(enabled).lower()}
  pcs_overtemp:
    signal: pcs.internal_temp_c
    severity: action
    high_threshold: 75.0
    enabled: true
"""
    path.write_text(text)


def _make_alarm_config_from_yaml(high_threshold: float = 3.65, enabled: bool = True) -> dict[str, Any]:
    """Return a minimal alarm config dict matching _write_alarm_config."""
    return {
        "_schema_version": "1.0",
        "defaults": {"hysteresis_pct": 2.0, "delay_ms": 0},
        "rules": {
            "cell_voltage_high": {
                "signal": "bms.cell_voltage_max",
                "severity": "warning",
                "high_threshold": high_threshold,
                "enabled": enabled,
            },
            "pcs_overtemp": {
                "signal": "pcs.internal_temp_c",
                "severity": "action",
                "high_threshold": 75.0,
                "enabled": True,
            },
        },
    }


def _connect_req(ctx: zmq.Context, endpoint: str) -> zmq.Socket:
    """Create a connected REQ socket with RCVTIMEO. Includes connection sleep."""
    req: zmq.Socket = ctx.socket(zmq.REQ)
    req.setsockopt(zmq.RCVTIMEO, 2000)
    req.connect(endpoint)
    time.sleep(_CONNECT_SLEEP_S)
    return req


def _send_and_poll(
    loop: Any,
    req: zmq.Socket,
    action: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send command, wait for delivery, poll, receive response."""
    raw: bytes = encode_command_request(action, params or {})
    req.send(raw)
    time.sleep(_MSG_DELIVER_S)
    loop._poll_commands()
    reply: bytes = req.recv()
    return decode_command_response(reply)


# ---------------------------------------------------------------------------
# Test: Event publishing on PUSH
# ---------------------------------------------------------------------------

class TestPushEventPublishing:
    """Verify alarm events are published to PUSH socket (logger)."""

    def test_alarm_event_published_on_push(self) -> None:
        """Trigger alarm activation -> verify PUSH socket receives encoded event."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        pull.bind("tcp://127.0.0.1:15610")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15610",
            rep_endpoint="tcp://127.0.0.1:15611",
            pub_endpoint="tcp://127.0.0.1:15612",
        )
        time.sleep(_CONNECT_SLEEP_S)  # Allow PUSH to connect to PULL
        try:
            # Set cell voltage above threshold
            rtdb.clusters[0].racks[0].max_cell_v = 3.70  # above 3.65

            now_ms: float = time.monotonic() * 1000
            loop._tick(now_ms)

            raw: bytes = pull.recv()
            event: dict = msgpack.unpackb(raw, raw=False)
            assert event["event_type"] == TOPIC_ALARM
            assert event["src"] == "alarm_manager"
            assert "data" in event
            data: dict = event["data"]
            assert data["alarm_id"] == "cell_voltage_high"
            assert data["signal"] == "bms.cell_voltage_max"
            assert data["severity"] == "warning"
            assert "state" in data
            assert "value" in data
            assert "threshold" in data
        finally:
            loop.cleanup()
            pull.close()
            ctx.term()

    def test_alarm_event_published_on_pub(self) -> None:
        """Trigger alarm activation -> verify PUB socket publishes with TOPIC_ALARM prefix."""
        loop, rtdb = _build_loop(
            pub_endpoint="tcp://127.0.0.1:15620",
            rep_endpoint="tcp://127.0.0.1:15621",
            push_endpoint="tcp://127.0.0.1:15622",
        )
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.RCVTIMEO, 2000)
        sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_ALARM)
        sub.connect("tcp://127.0.0.1:15620")
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Set cell voltage above threshold
            rtdb.clusters[0].racks[0].max_cell_v = 3.70  # above 3.65

            now_ms: float = time.monotonic() * 1000
            loop._tick(now_ms)

            topic_raw: bytes = sub.recv()
            body: bytes = sub.recv()
            assert topic_raw.decode() == TOPIC_ALARM
            payload: dict = msgpack.unpackb(body, raw=False)
            assert payload["alarm_id"] == "cell_voltage_high"
        finally:
            sub.close()
            ctx.term()
            loop.cleanup()

    def test_warning_alarm_publishes_event(self) -> None:
        """Warning severity alarm publishes same as protection (Phase 15 = publish only)."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        pull.bind("tcp://127.0.0.1:15630")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15630",
            rep_endpoint="tcp://127.0.0.1:15631",
            pub_endpoint="tcp://127.0.0.1:15632",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Warning alarm: cell_voltage_high (warning severity)
            rtdb.clusters[0].racks[0].max_cell_v = 3.70  # above 3.65

            loop._tick(time.monotonic() * 1000)
            raw: bytes = pull.recv()
            event: dict = msgpack.unpackb(raw, raw=False)
            assert event["data"]["severity"] == "warning"
        finally:
            loop.cleanup()
            pull.close()
            ctx.term()

    def test_all_severity_levels_publish(self) -> None:
        """Warning, action, protection all publish events to PUSH."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        pull.bind("tcp://127.0.0.1:15640")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15640",
            rep_endpoint="tcp://127.0.0.1:15641",
            pub_endpoint="tcp://127.0.0.1:15642",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Trigger all 3 alarms in config simultaneously
            rtdb.clusters[0].racks[0].max_cell_v = 3.70   # warning: cell_voltage_high
            rtdb.clusters[0].racks[0].min_cell_v = 2.70   # protection: cell_voltage_low
            rtdb.pcs.temperature = 80.0                    # action: pcs_temp_high

            loop._tick(time.monotonic() * 1000)

            # Should receive 3 events
            events: list[dict] = []
            for _ in range(3):
                raw = pull.recv()
                events.append(msgpack.unpackb(raw, raw=False))

            severities: set[str] = {e["data"]["severity"] for e in events}
            assert "warning" in severities
            assert "action" in severities
            assert "protection" in severities
        finally:
            loop.cleanup()
            pull.close()
            ctx.term()


# ---------------------------------------------------------------------------
# Test: ZMQ REP command API
# ---------------------------------------------------------------------------

class TestZmqRepCommandApi:
    """Verify _poll_commands() handles all alarm command types via ZMQ REP."""

    def test_get_active_alarms_empty(self) -> None:
        """No alarms active -> get_active_alarms returns {status: ok, alarms: []}."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15650")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15650")
        try:
            resp = _send_and_poll(loop, req, "get_active_alarms")
            assert resp["status"] == "ok"
            assert resp["result"]["alarms"] == []
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_get_active_alarms_returns_active(self) -> None:
        """Trigger alarm -> get_active_alarms returns it with correct fields."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15651")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15651")
        try:
            # Trigger cell_voltage_high
            rtdb.clusters[0].racks[0].max_cell_v = 3.70
            loop._tick(time.monotonic() * 1000)

            resp = _send_and_poll(loop, req, "get_active_alarms")
            assert resp["status"] == "ok"
            alarms: list[dict] = resp["result"]["alarms"]
            assert len(alarms) == 1
            alarm: dict = alarms[0]
            assert alarm["alarm_id"] == "cell_voltage_high"
            assert alarm["signal"] == "bms.cell_voltage_max"
            assert alarm["severity"] == "warning"
            assert "state" in alarm
            assert "current_value" in alarm
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_acknowledge_command(self) -> None:
        """Alarm in ACTIVE_UNACKED -> acknowledge returns {status: ok, from_state, to_state}."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15652")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15652")
        try:
            # Trigger alarm
            rtdb.clusters[0].racks[0].max_cell_v = 3.70
            loop._tick(time.monotonic() * 1000)

            resp = _send_and_poll(
                loop, req, "acknowledge", {"alarm_id": "cell_voltage_high"}
            )
            assert resp["status"] == "ok"
            result: dict = resp["result"]
            assert result["alarm_id"] == "cell_voltage_high"
            assert result["from_state"] == "ACTIVE_UNACKED"
            assert result["to_state"] == "ACTIVE_ACKED"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_acknowledge_normal_rejected(self) -> None:
        """Acknowledge NORMAL alarm -> returns {status: error, error_msg: ...}."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15653")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15653")
        try:
            # Don't trigger alarm — stays NORMAL
            resp = _send_and_poll(
                loop, req, "acknowledge", {"alarm_id": "cell_voltage_high"}
            )
            assert resp["status"] == "error"
            assert resp["error_msg"] is not None
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_get_alarm_config(self) -> None:
        """get_alarm_config returns all 3 rules with alarm_id, signal, severity, thresholds, enabled."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15654")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15654")
        try:
            resp = _send_and_poll(loop, req, "get_alarm_config")
            assert resp["status"] == "ok"
            rules: list[dict] = resp["result"]["rules"]
            assert len(rules) == 3
            rule_ids: set[str] = {r["alarm_id"] for r in rules}
            assert "cell_voltage_high" in rule_ids
            assert "cell_voltage_low" in rule_ids
            assert "pcs_temp_high" in rule_ids
            # Check fields present in each rule
            for rule in rules:
                assert "alarm_id" in rule
                assert "signal" in rule
                assert "severity" in rule
                assert "enabled" in rule
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_unknown_command(self) -> None:
        """Unknown action -> returns {status: error, error_msg: 'Unknown action: ...'}."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15655")
        ctx = zmq.Context()
        req = _connect_req(ctx, "tcp://127.0.0.1:15655")
        try:
            resp = _send_and_poll(loop, req, "bogus_action")
            assert resp["status"] == "error"
            assert resp["error_msg"] is not None
            assert "bogus_action" in resp["error_msg"]
        finally:
            req.close()
            ctx.term()
            loop.cleanup()

    def test_malformed_request(self) -> None:
        """Invalid msgpack -> returns error response (REP never hangs)."""
        loop, rtdb = _build_loop(rep_endpoint="tcp://127.0.0.1:15656")
        ctx = zmq.Context()
        req: zmq.Socket = ctx.socket(zmq.REQ)
        req.setsockopt(zmq.RCVTIMEO, 2000)
        req.connect("tcp://127.0.0.1:15656")
        time.sleep(_CONNECT_SLEEP_S)
        try:
            req.send(b"\x00\x01\x02\x03 not valid msgpack")
            time.sleep(_MSG_DELIVER_S)
            loop._poll_commands()
            reply: bytes = req.recv()
            resp: dict = decode_command_response(reply)
            assert resp["status"] == "error"
        finally:
            req.close()
            ctx.term()
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: RTDB integration
# ---------------------------------------------------------------------------

class TestRtdbIntegration:
    """Verify _tick() reads RTDB signals via seqlock and passes values to evaluator."""

    def test_tick_reads_rtdb_signals(self) -> None:
        """Set mock RTDB values -> verify evaluator receives resolved values."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15660",
            push_endpoint="tcp://127.0.0.1:15661",
            pub_endpoint="tcp://127.0.0.1:15662",
        )
        try:
            # Set a known value — above high threshold (3.65)
            rtdb.clusters[0].racks[0].max_cell_v = 3.70

            # Call tick — if evaluator received the value, alarm should activate
            loop._tick(time.monotonic() * 1000)

            # Check active alarms — cell_voltage_high should be active
            active: list[dict] = loop._evaluator.get_active_alarms()
            alarm_ids: set[str] = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" in alarm_ids
        finally:
            loop.cleanup()

    def test_tick_with_offline_racks(self) -> None:
        """All racks offline -> BMS signals return None -> no BMS alarms activate."""
        loop, rtdb = _build_loop(
            rep_endpoint="tcp://127.0.0.1:15663",
            push_endpoint="tcp://127.0.0.1:15664",
            pub_endpoint="tcp://127.0.0.1:15665",
        )
        try:
            # Set all racks offline
            for c in range(MAX_CLUSTERS):
                for r in range(MAX_RACKS_PER_CLUSTER):
                    rtdb.clusters[c].racks[r].online = 0

            # Also set an extreme value that WOULD trigger if online
            rtdb.clusters[0].racks[0].max_cell_v = 4.5

            loop._tick(time.monotonic() * 1000)

            # BMS alarms should NOT activate (signals are None when offline)
            active: list[dict] = loop._evaluator.get_active_alarms()
            bms_active: list[dict] = [
                a for a in active if a["signal"].startswith("bms.")
            ]
            assert bms_active == []
        finally:
            loop.cleanup()


# ---------------------------------------------------------------------------
# Test: Full alarm lifecycle
# ---------------------------------------------------------------------------

class TestAlarmLifecycle:
    """End-to-end alarm lifecycle tests through AlarmLoop."""

    def test_full_alarm_lifecycle(self) -> None:
        """Set high value -> verify ACTIVE -> acknowledge -> clear value -> verify RTN -> NORMAL."""
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        pull.bind("tcp://127.0.0.1:15670")

        loop, rtdb = _build_loop(
            push_endpoint="tcp://127.0.0.1:15670",
            rep_endpoint="tcp://127.0.0.1:15671",
            pub_endpoint="tcp://127.0.0.1:15672",
        )
        req = _connect_req(ctx, "tcp://127.0.0.1:15671")
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Step 1: Trigger pcs_temp_high (action severity, no delay)
            rtdb.pcs.temperature = 80.0  # above 75.0
            loop._tick(time.monotonic() * 1000)

            # Verify PUSH event received
            raw: bytes = pull.recv()
            event: dict = msgpack.unpackb(raw, raw=False)
            assert event["data"]["alarm_id"] == "pcs_temp_high"
            assert event["data"]["state"] == "ACTIVE_UNACKED"

            # Step 2: Verify active alarm via REQ/REP
            resp = _send_and_poll(loop, req, "get_active_alarms")
            alarms: list[dict] = resp["result"]["alarms"]
            assert any(a["alarm_id"] == "pcs_temp_high" for a in alarms)

            # Step 3: Acknowledge
            resp = _send_and_poll(
                loop, req, "acknowledge", {"alarm_id": "pcs_temp_high"}
            )
            assert resp["status"] == "ok"
            assert resp["result"]["to_state"] == "ACTIVE_ACKED"

            # Step 4: Clear the condition (below high_clear = 75.0 * (1 - 0.02) = 73.5)
            rtdb.pcs.temperature = 70.0
            loop._tick(time.monotonic() * 1000)

            # Alarm should now be in RTN
            active: list[dict] = loop._evaluator.get_active_alarms()
            rtn_alarms: list[dict] = [
                a for a in active if a["alarm_id"] == "pcs_temp_high"
            ]
            assert len(rtn_alarms) == 1
            assert rtn_alarms[0]["state"] == "RTN"

            # Step 5: Next tick — RTN auto-transitions to NORMAL
            loop._tick(time.monotonic() * 1000)
            active = loop._evaluator.get_active_alarms()
            final_alarms: list[dict] = [
                a for a in active if a["alarm_id"] == "pcs_temp_high"
            ]
            assert final_alarms == []
        finally:
            req.close()
            loop.cleanup()
            pull.close()
            ctx.term()


# ---------------------------------------------------------------------------
# Test: Config hot-reload via ZMQ SUB (ALM-09)
# ---------------------------------------------------------------------------


class TestConfigHotReload:
    """Verify AlarmLoop subscribes to SOCK_CONFIG_PUB and updates thresholds on reload."""

    def test_config_reload_alarms_config_updates_threshold(
        self, tmp_path: Path
    ) -> None:
        """config_reload event with config_name="alarms_config" triggers re-read and threshold update."""
        # Write initial config: cell_voltage_high threshold = 3.65
        _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.65)
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15680")

        loop, rtdb = _build_loop(
            config=_make_alarm_config_from_yaml(high_threshold=3.65),
            rep_endpoint="tcp://127.0.0.1:15681",
            push_endpoint="tcp://127.0.0.1:15682",
            pub_endpoint="tcp://127.0.0.1:15683",
            config_sub_endpoint="tcp://127.0.0.1:15680",
            config_path=tmp_path / "alarms_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Update config on disk: raise threshold to 3.80 (above current max_cell_v=3.50)
            _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.80)

            # Send config_reload event
            reload_body: bytes = msgpack.packb(
                {"name": "alarms_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Tick — reload should have been processed
            loop._tick(time.monotonic() * 1000)

            # After reload, cell_voltage_high threshold is 3.80
            # Default rack has max_cell_v = 3.50 — should NOT trigger (below 3.80)
            active: list[dict] = loop._evaluator.get_active_alarms()
            ids: set[str] = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" not in ids
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_active_alarm_lifecycle_preserved_across_reload(
        self, tmp_path: Path
    ) -> None:
        """Active alarm stays ACTIVE after config reload — lifecycle state is preserved."""
        _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.65)
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15685")

        loop, rtdb = _build_loop(
            config=_make_alarm_config_from_yaml(high_threshold=3.65),
            rep_endpoint="tcp://127.0.0.1:15686",
            push_endpoint="tcp://127.0.0.1:15687",
            pub_endpoint="tcp://127.0.0.1:15688",
            config_sub_endpoint="tcp://127.0.0.1:15685",
            config_path=tmp_path / "alarms_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Trigger alarm: max_cell_v above 3.65
            rtdb.clusters[0].racks[0].max_cell_v = 3.70
            loop._tick(time.monotonic() * 1000)

            # Verify alarm is active
            active: list[dict] = loop._evaluator.get_active_alarms()
            ids: set[str] = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" in ids

            # Write same config on disk and send reload
            _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.65)
            reload_body: bytes = msgpack.packb(
                {"name": "alarms_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Tick — reload processed, alarm still active
            loop._tick(time.monotonic() * 1000)

            active = loop._evaluator.get_active_alarms()
            ids = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" in ids  # Still ACTIVE after reload
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_config_reload_enable_disable_flag_change(
        self, tmp_path: Path
    ) -> None:
        """Disabling an alarm via config_reload stops it from activating."""
        _write_alarm_config(tmp_path / "alarms_config.yaml", enabled=True)
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15690")

        loop, rtdb = _build_loop(
            config=_make_alarm_config_from_yaml(enabled=True),
            rep_endpoint="tcp://127.0.0.1:15691",
            push_endpoint="tcp://127.0.0.1:15692",
            pub_endpoint="tcp://127.0.0.1:15693",
            config_sub_endpoint="tcp://127.0.0.1:15690",
            config_path=tmp_path / "alarms_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Send reload with alarm disabled
            _write_alarm_config(tmp_path / "alarms_config.yaml", enabled=False)
            reload_body: bytes = msgpack.packb(
                {"name": "alarms_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Set cell_voltage above old threshold — should NOT trigger (disabled)
            rtdb.clusters[0].racks[0].max_cell_v = 3.70
            loop._tick(time.monotonic() * 1000)

            active: list[dict] = loop._evaluator.get_active_alarms()
            ids: set[str] = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" not in ids
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_config_reload_wrong_config_name_ignored(
        self, tmp_path: Path
    ) -> None:
        """config_reload event with config_name != "alarms_config" is ignored."""
        _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.65)
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15694")

        loop, rtdb = _build_loop(
            config=_make_alarm_config_from_yaml(high_threshold=3.65),
            rep_endpoint="tcp://127.0.0.1:15695",
            push_endpoint="tcp://127.0.0.1:15696",
            pub_endpoint="tcp://127.0.0.1:15697",
            config_sub_endpoint="tcp://127.0.0.1:15694",
            config_path=tmp_path / "alarms_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Write new config on disk with higher threshold (3.80)
            _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.80)

            # Send reload for "control_config" — should be ignored
            reload_body: bytes = msgpack.packb(
                {"name": "control_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Tick — disk has threshold 3.80 but reload was ignored; original threshold 3.65 still active
            rtdb.clusters[0].racks[0].max_cell_v = 3.70  # above 3.65, below 3.80
            loop._tick(time.monotonic() * 1000)

            active: list[dict] = loop._evaluator.get_active_alarms()
            ids: set[str] = {a["alarm_id"] for a in active}
            # control_config reload was ignored, so threshold still 3.65 -> alarm fires
            assert "cell_voltage_high" in ids
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()

    def test_invalid_config_on_disk_rejected_keeps_old(
        self, tmp_path: Path
    ) -> None:
        """config_reload event with invalid YAML on disk is rejected — old config retained."""
        _write_alarm_config(tmp_path / "alarms_config.yaml", high_threshold=3.65)
        ctx = zmq.Context()
        config_pub = ctx.socket(zmq.PUB)
        config_pub.bind("tcp://127.0.0.1:15698")

        loop, rtdb = _build_loop(
            config=_make_alarm_config_from_yaml(high_threshold=3.65),
            rep_endpoint="tcp://127.0.0.1:15699",
            push_endpoint="tcp://127.0.0.1:15700",
            pub_endpoint="tcp://127.0.0.1:15701",
            config_sub_endpoint="tcp://127.0.0.1:15698",
            config_path=tmp_path / "alarms_config.yaml",
        )
        time.sleep(_CONNECT_SLEEP_S)
        try:
            # Write invalid YAML on disk
            (tmp_path / "alarms_config.yaml").write_text("not: valid: yaml: !!!")

            # Send reload event
            reload_body: bytes = msgpack.packb(
                {"name": "alarms_config", "config": {}, "diff": {}},
                use_bin_type=True,
            )
            config_pub.send_multipart([b"config_reload", reload_body])
            time.sleep(_MSG_DELIVER_S)

            # Tick — invalid config rejected, old config (threshold=3.65) retained
            rtdb.clusters[0].racks[0].max_cell_v = 3.70  # above 3.65
            loop._tick(time.monotonic() * 1000)

            active: list[dict] = loop._evaluator.get_active_alarms()
            ids: set[str] = {a["alarm_id"] for a in active}
            assert "cell_voltage_high" in ids  # Old threshold still active
        finally:
            config_pub.close()
            ctx.term()
            loop.cleanup()
