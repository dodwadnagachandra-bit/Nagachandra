"""Tests for DiagnosticsLoop — 5 async tasks wiring analyzers to ZMQ.

Uses inproc:// ZMQ sockets to avoid filesystem socket creation.
All ZMQ sockets use LINGER=0 to prevent ctx.term() blocking.
All tests call loop.cleanup() before ctx.term() to close loop sockets.
Telemetry tests use 200ms delay to allow inproc subscription propagation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
import zmq

from ems_common.ipc import (
    SEVERITY_WARNING,
    STATUS_OK,
    STATUS_ERROR,
    TOPIC_DIAGNOSTICS,
    decode_command_response,
    decode_event,
    encode_command_request,
    encode_telemetry,
)
from ems_diagnostics.config import (
    BatteryConfig,
    DiagnosticsConfig,
    IntervalsConfig,
    MetricsConfig,
    PredictionConfig,
    ThresholdsConfig,
)
from ems_diagnostics.loop import DiagnosticsLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_config(publish_s: int = 1, trend_update_s: int = 3600) -> DiagnosticsConfig:
    """Return a short-interval config suitable for loop integration tests."""
    return DiagnosticsConfig(
        intervals=IntervalsConfig(publish_s=publish_s, trend_update_s=trend_update_s),
        thresholds=ThresholdsConfig(
            soh_warning_pct=85.0,
            soh_critical_pct=80.0,
            pcs_efficiency_warning_pct=90.0,
            thermal_delta_warning_c=8.0,
            comm_fault_warning_rate=5,
        ),
        prediction=PredictionConfig(min_history_days=7, history_window_days=30),
        battery=BatteryConfig(rated_capacity_ah=100.0),
        metrics=MetricsConfig(
            soh_enabled=True,
            pcs_efficiency_enabled=True,
            thermal_enabled=True,
            comm_health_enabled=True,
            predictions_enabled=True,
        ),
    )


def _ts() -> int:
    """Return a microsecond timestamp for unique inproc:// endpoint names."""
    return int(time.time() * 1_000_000)


def _mk_sock(ctx: zmq.Context, sock_type: int) -> zmq.Socket:
    """Create a ZMQ socket with LINGER=0 (prevents ctx.term() from blocking)."""
    sock: zmq.Socket = ctx.socket(sock_type)
    sock.setsockopt(zmq.LINGER, 0)
    return sock


def _make_loop(
    zmq_ctx: zmq.Context,
    config: DiagnosticsConfig,
    sub_ep: str,
    logger_req_ep: str,
    logger_push_ep: str,
    pub_ep: str,
    rep_ep: str,
    rack_count: int = 1,
    known_devices: list[str] | None = None,
) -> DiagnosticsLoop:
    """Build a DiagnosticsLoop with inproc:// endpoints."""
    return DiagnosticsLoop(
        config=config,
        zmq_ctx=zmq_ctx,
        telemetry_sub_endpoint=sub_ep,
        logger_req_endpoint=logger_req_ep,
        logger_push_endpoint=logger_push_ep,
        diagnostics_pub_endpoint=pub_ep,
        diagnostics_cmd_endpoint=rep_ep,
        rack_count=rack_count,
        known_devices=known_devices or ["bms_rack_1", "pcs", "btms"],
    )


def _cleanup(loop: DiagnosticsLoop, ctx: zmq.Context, *socks: zmq.Socket) -> None:
    """Close loop sockets, helper sockets, then terminate context."""
    loop.cleanup()  # Closes loop's sockets (LINGER=0, fast)
    for s in socks:
        s.close()   # Close helper sockets (LINGER=0, fast)
    ctx.term()      # Safe: all sockets closed


# ---------------------------------------------------------------------------
# Test: BMS telemetry routing to SohAnalyzer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_routing_bms() -> None:
    """BMS telemetry on SUB updates SohAnalyzer pack_soh_bms."""
    config: DiagnosticsConfig = _make_test_config()
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    async def run_and_inject() -> None:
        # 200ms delay: inproc subscription propagation takes longer than tcp
        await asyncio.sleep(0.2)
        # Multipart: frame 0 = topic bytes, frame 1 = msgpack envelope
        topic: str = "bms.rack.1"
        envelope: bytes = encode_telemetry(
            timestamp_ms=int(time.time() * 1000), seq=1,
            source="data_manager", topic=topic,
            payload={"pack_i": -10.0, "pack_soc": 70.0, "pack_soh": 93.5},
        )
        pub.send_multipart([topic.encode(), envelope])
        await asyncio.sleep(0.1)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), run_and_inject()),
        timeout=4.0,
    )

    snap: dict = loop._soh_analyzers[0].get_current()
    assert snap["soh_pct"] == 93.5

    _cleanup(loop, ctx, pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: PCS telemetry routing to PcsAnalyzer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pcs_telemetry_routing() -> None:
    """PCS telemetry on SUB updates PcsAnalyzer."""
    config: DiagnosticsConfig = _make_test_config()
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    async def run_and_inject() -> None:
        await asyncio.sleep(0.2)
        topic: str = "pcs"
        envelope: bytes = encode_telemetry(
            timestamp_ms=int(time.time() * 1000), seq=1,
            source="data_manager", topic=topic,
            payload={"active_power": 5000.0, "dc_voltage": 600.0, "dc_current": 9.0},
        )
        pub.send_multipart([topic.encode(), envelope])
        await asyncio.sleep(0.1)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), run_and_inject()),
        timeout=4.0,
    )

    snap: dict = loop._pcs_analyzer.get_current()
    assert snap["sample_count"] == 1
    assert snap["efficiency_pct"] is not None

    _cleanup(loop, ctx, pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: BTMS telemetry routing to ThermalAnalyzer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_btms_telemetry_routing() -> None:
    """BTMS telemetry on SUB updates ThermalAnalyzer."""
    config: DiagnosticsConfig = _make_test_config()
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    async def run_and_inject() -> None:
        await asyncio.sleep(0.2)
        topic: str = "btms"
        envelope: bytes = encode_telemetry(
            timestamp_ms=int(time.time() * 1000), seq=1,
            source="data_manager", topic=topic,
            payload={"max_cell_t": 38.0, "outlet_temp": 30.0, "fan_speed_pct": 70.0},
        )
        pub.send_multipart([topic.encode(), envelope])
        await asyncio.sleep(0.1)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), run_and_inject()),
        timeout=4.0,
    )

    snap: dict = loop._thermal_analyzer.get_current()
    assert snap["delta_t"] == pytest.approx(8.0)
    assert snap["outlet_temp"] == 30.0

    _cleanup(loop, ctx, pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: Report server — get_current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_server_get_current() -> None:
    """REP server returns current snapshot with all 4 sections for get_current."""
    config: DiagnosticsConfig = _make_test_config()
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    tel_pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    tel_pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    # REQ client to query the loop's REP server (non-blocking recv via poll)
    client: zmq.Socket = _mk_sock(ctx, zmq.REQ)
    client.connect(rep_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    result_holder: list[dict] = []

    async def query_and_stop() -> None:
        await asyncio.sleep(0.1)
        # Non-blocking: send is fast, poll for reply to avoid blocking event loop
        client.send(encode_command_request("get_current", {}))
        deadline: float = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                raw: bytes = client.recv(zmq.NOBLOCK)
                result_holder.append(decode_command_response(raw))
                break
            except zmq.Again:
                await asyncio.sleep(0.01)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), query_and_stop()),
        timeout=4.0,
    )

    assert len(result_holder) == 1
    resp: dict = result_holder[0]
    assert resp["status"] == STATUS_OK
    result: dict = resp["result"]
    assert "soh" in result
    assert "pcs_efficiency" in result
    assert "thermal" in result
    assert "comm" in result

    _cleanup(loop, ctx, client, tel_pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: Report server — error handling (malformed request)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_server_error_handling() -> None:
    """Malformed REP request returns error response (no hang)."""
    config: DiagnosticsConfig = _make_test_config()
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    tel_pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    tel_pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    client: zmq.Socket = _mk_sock(ctx, zmq.REQ)
    client.connect(rep_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    result_holder: list[dict] = []

    async def send_malformed_and_stop() -> None:
        await asyncio.sleep(0.1)
        # Send garbage bytes (not valid msgpack command request)
        client.send(b"\xff\xfe\xfd not valid msgpack")
        # Non-blocking poll for error reply
        deadline: float = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                raw: bytes = client.recv(zmq.NOBLOCK)
                result_holder.append(decode_command_response(raw))
                break
            except zmq.Again:
                await asyncio.sleep(0.01)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), send_malformed_and_stop()),
        timeout=4.0,
    )

    assert len(result_holder) == 1
    assert result_holder[0]["status"] == STATUS_ERROR

    _cleanup(loop, ctx, client, tel_pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: Diagnostics PUB publishes at interval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_published() -> None:
    """Diagnostics PUB broadcasts at publish_s interval."""
    config: DiagnosticsConfig = _make_test_config(publish_s=1)
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    tel_pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    tel_pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    # Subscribe to diagnostics PUB — must be done BEFORE loop binds the PUB socket
    # Since the loop binds in __init__, we connect after loop creation
    diag_sub: zmq.Socket = _mk_sock(ctx, zmq.SUB)
    diag_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_DIAGNOSTICS)
    diag_sub.connect(pub_ep)

    received: list[bytes] = []

    async def collect_and_stop() -> None:
        # Wait longer for inproc sub to propagate + first publish_s=1 to fire
        await asyncio.sleep(0.2)
        deadline: float = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            try:
                raw: bytes = diag_sub.recv(zmq.NOBLOCK)
                received.append(raw)
                break
            except zmq.Again:
                await asyncio.sleep(0.05)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), collect_and_stop()),
        timeout=5.0,
    )

    assert len(received) >= 1
    assert received[0].startswith(TOPIC_DIAGNOSTICS.encode())

    _cleanup(loop, ctx, diag_sub, tel_pub, logger_rep, push_sink)


# ---------------------------------------------------------------------------
# Test: Predictive alert fires for declining SOH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predictive_alert_fires() -> None:
    """Predictive alert PUSH event sent to logger when SOH near threshold."""
    config: DiagnosticsConfig = _make_test_config(publish_s=1)
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    tel_pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    tel_pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)

    # PULL socket to receive alert events
    alert_pull: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    alert_pull.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    # Pre-populate SohAnalyzer with 30 declining history points:
    # SOH goes from 95 → 65 (slope ~-1/day) → days_to_threshold ≈ (80-65)/1 = 15 → critical
    rack: Any = loop._soh_analyzers[0]
    rack._soh_pct = 65.0
    rack._history = [(i * 86400000, 95.0 - i) for i in range(30)]

    received_events: list[bytes] = []

    async def collect_alert_and_stop() -> None:
        await asyncio.sleep(0.05)
        deadline: float = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            try:
                raw: bytes = alert_pull.recv(zmq.NOBLOCK)
                received_events.append(raw)
                break
            except zmq.Again:
                await asyncio.sleep(0.05)
        loop.stop()

    await asyncio.wait_for(
        asyncio.gather(loop.run(), collect_alert_and_stop()),
        timeout=5.0,
    )

    assert len(received_events) >= 1
    event: dict = decode_event(received_events[0])
    # decode_event returns raw dict with short keys from ipc.py (MSG_KEY_*)
    assert event["event_type"] == "predictive_alert"
    assert event["severity"] == SEVERITY_WARNING
    assert event["src"] == "diagnostics"
    assert event["data"]["rack_id"] == 1

    _cleanup(loop, ctx, alert_pull, logger_rep, tel_pub)


# ---------------------------------------------------------------------------
# Test: Stop event terminates the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_terminates_loop() -> None:
    """stop() causes run() to complete within reasonable time."""
    config: DiagnosticsConfig = _make_test_config(publish_s=60, trend_update_s=3600)
    ctx: zmq.Context = zmq.Context()
    ts: int = _ts()

    sub_ep: str = f"inproc://sub-{ts}"
    req_ep: str = f"inproc://req-{ts}"
    push_ep: str = f"inproc://push-{ts}"
    pub_ep: str = f"inproc://pub-{ts}"
    rep_ep: str = f"inproc://rep-{ts}"

    tel_pub: zmq.Socket = _mk_sock(ctx, zmq.PUB)
    tel_pub.bind(sub_ep)
    logger_rep: zmq.Socket = _mk_sock(ctx, zmq.REP)
    logger_rep.bind(req_ep)
    push_sink: zmq.Socket = _mk_sock(ctx, zmq.PULL)
    push_sink.bind(push_ep)

    loop: DiagnosticsLoop = _make_loop(
        zmq_ctx=ctx, config=config,
        sub_ep=sub_ep, logger_req_ep=req_ep, logger_push_ep=push_ep,
        pub_ep=pub_ep, rep_ep=rep_ep,
    )

    async def stop_after_short_delay() -> None:
        await asyncio.sleep(0.1)
        loop.stop()

    # Should complete within 3s (well under 60s publish interval)
    await asyncio.wait_for(
        asyncio.gather(loop.run(), stop_after_short_delay()),
        timeout=3.0,
    )

    _cleanup(loop, ctx, tel_pub, logger_rep, push_sink)
