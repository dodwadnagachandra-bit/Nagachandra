"""Tests for ConfigManager PUB socket — config_reload broadcast via ZMQ PUB.

Verifies that:
- _init_pub_socket() binds a ZMQ PUB socket on the configured address
- handle_reload() broadcasts multipart [topic, msgpack_body] on PUB
- PUB message topic == b"config_reload"
- PUB message body contains {name, config, diff}
- _close_pub_socket() closes the socket cleanly

Tests use tcp://127.0.0.1:15750-15759 to avoid /run/ems dependency.
Strategy: manager._pub_sock is zmq.asyncio, but test SUB uses sync zmq.Context
in a background thread to receive messages — avoids event loop scope issues.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from pathlib import Path

import msgpack
import pytest
import zmq
import zmq.asyncio

from ems_config_manager.manager import ConfigManager

# Short sleep to allow ZMQ TCP connections to establish (TCP loopback)
_CONNECT_SLEEP_S: float = 0.1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(tmp_path: Path) -> ConfigManager:
    """Create a ConfigManager with a temp config directory."""
    return ConfigManager(
        config_dir=tmp_path,
        schema_dir=Path("config/schemas"),
    )


def _write_minimal_control_config(tmp_path: Path) -> None:
    """Write a minimal valid control_config.yaml for reload tests.

    Only includes fields allowed by control_config.schema.json.
    state_machine only allows loop_interval_ms and fault_retry_count.
    """
    config_text: str = """\
_schema_version: "1.0"

soc_limits:
  charge_cutoff_pct: 95
  discharge_cutoff_pct: 10

power_limits:
  max_charge_kw: 25
  max_discharge_kw: 25

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
    (tmp_path / "control_config.yaml").write_text(config_text)


def _recv_multipart_in_thread(
    sub_addr: str,
    result_q: "queue.Queue[list[bytes] | Exception]",
) -> None:
    """Background thread: subscribe, receive one multipart message, enqueue it.

    Uses sync zmq.Context (independent of asyncio event loop) to receive
    a single PUB message and put it in result_q. On error, puts the exception.
    """
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 3000)
    sub.setsockopt(zmq.SUBSCRIBE, b"config_reload")
    sub.connect(sub_addr)
    try:
        frames: list[bytes] = sub.recv_multipart()
        result_q.put(frames)
    except zmq.Again as exc:
        result_q.put(exc)
    finally:
        sub.close()
        ctx.term()


# ---------------------------------------------------------------------------
# Test: PUB socket initialisation (sync — no ZMQ async I/O needed)
# ---------------------------------------------------------------------------


class TestPubSocketInit:
    """Verify _init_pub_socket() initialises correctly."""

    def test_pub_sock_is_none_before_init(self, tmp_path: Path) -> None:
        """ConfigManager._pub_sock is None before _init_pub_socket() is called."""
        manager = _make_manager(tmp_path)
        assert manager._pub_sock is None

    def test_init_pub_socket_sets_pub_sock(self, tmp_path: Path) -> None:
        """_init_pub_socket() creates and binds a ZMQ PUB socket."""
        manager = _make_manager(tmp_path)
        ctx = zmq.asyncio.Context()
        try:
            manager._init_pub_socket(ctx, pub_addr="tcp://127.0.0.1:15751")
            assert manager._pub_sock is not None
        finally:
            manager._close_pub_socket()
            ctx.term()

    def test_close_pub_socket_sets_none(self, tmp_path: Path) -> None:
        """_close_pub_socket() sets _pub_sock back to None."""
        manager = _make_manager(tmp_path)
        ctx = zmq.asyncio.Context()
        try:
            manager._init_pub_socket(ctx, pub_addr="tcp://127.0.0.1:15752")
            manager._close_pub_socket()
            assert manager._pub_sock is None
        finally:
            ctx.term()

    def test_close_pub_socket_idempotent(self, tmp_path: Path) -> None:
        """_close_pub_socket() called twice does not raise."""
        manager = _make_manager(tmp_path)
        ctx = zmq.asyncio.Context()
        try:
            manager._init_pub_socket(ctx, pub_addr="tcp://127.0.0.1:15753")
            manager._close_pub_socket()
            manager._close_pub_socket()  # Second call — should not raise
        finally:
            ctx.term()


# ---------------------------------------------------------------------------
# Helper for broadcast tests: runs handle_reload in asyncio, receives via thread
# ---------------------------------------------------------------------------


def _run_broadcast_test(
    tmp_path: Path,
    pub_addr: str,
    sub_addr: str,
    seed_config: dict | None = None,
) -> list[bytes]:
    """Run a full config_reload PUB broadcast test.

    Strategy to avoid "slow subscriber" ZMQ problem:
    1. Bind PUB socket with sync zmq context FIRST
    2. Start SUB thread (connects to already-bound PUB)
    3. Sleep 100ms to ensure SUB's TCP connection is fully established
    4. Run handle_reload in asyncio (uses the pre-bound PUB via zmq.asyncio)
    5. Return received multipart frames

    NOTE: We bind PUB with sync ctx, then wrap in zmq.asyncio via manager.
    Actually simpler: use asyncio.run() but sleep INSIDE the coroutine AFTER
    starting the SUB thread and BEFORE sending.

    Args:
        tmp_path: Temp directory with control_config.yaml written by caller.
        pub_addr: TCP address for the PUB socket (PUB binds here).
        sub_addr: TCP address for the SUB socket (must match pub_addr).
        seed_config: Optional dict to pre-seed manager._configs for diff computation.

    Returns:
        [topic_bytes, body_bytes] received by the SUB.
    """
    result_q: queue.Queue[list[bytes] | Exception] = queue.Queue()

    async def _do_reload() -> None:
        ctx = zmq.asyncio.Context()
        manager = _make_manager(tmp_path)
        if seed_config is not None:
            manager._configs["control_config"] = seed_config
        else:
            manager._configs["control_config"] = {"_schema_version": "1.0"}

        # Bind PUB FIRST (before starting SUB thread)
        manager._init_pub_socket(ctx, pub_addr=pub_addr)

        # Start SUB thread after PUB is bound so it subscribes to a ready socket
        t = threading.Thread(
            target=_recv_multipart_in_thread,
            args=(sub_addr, result_q),
            daemon=True,
        )
        t.start()

        # Sleep to ensure SUB thread's TCP connection is established
        await asyncio.sleep(_CONNECT_SLEEP_S)

        try:
            await manager.handle_reload("control_config")
        finally:
            manager._close_pub_socket()
            ctx.term()
            t.join(timeout=5.0)

    asyncio.run(_do_reload())

    result: list[bytes] | Exception = result_q.get(timeout=5.0)

    if isinstance(result, Exception):
        raise result

    return result


# ---------------------------------------------------------------------------
# Test: handle_reload() broadcasts on PUB socket
# ---------------------------------------------------------------------------


class TestHandleReloadPubBroadcast:
    """Verify handle_reload() sends multipart [topic, body] on PUB socket."""

    def test_handle_reload_broadcasts_config_reload_topic(
        self, tmp_path: Path
    ) -> None:
        """handle_reload() sends topic frame b"config_reload" on PUB."""
        _write_minimal_control_config(tmp_path)
        frames = _run_broadcast_test(tmp_path, "tcp://127.0.0.1:15750", "tcp://127.0.0.1:15750")
        assert frames[0] == b"config_reload"

    def test_handle_reload_broadcasts_msgpack_body(
        self, tmp_path: Path
    ) -> None:
        """handle_reload() body frame is msgpack with name, config, diff keys."""
        _write_minimal_control_config(tmp_path)
        frames = _run_broadcast_test(tmp_path, "tcp://127.0.0.1:15754", "tcp://127.0.0.1:15754")
        data: dict = msgpack.unpackb(frames[1], raw=False)
        assert "name" in data
        assert data["name"] == "control_config"
        assert "config" in data
        assert "diff" in data

    def test_handle_reload_no_pub_when_sock_is_none(
        self, tmp_path: Path
    ) -> None:
        """handle_reload() with no PUB socket initialised does not raise."""
        _write_minimal_control_config(tmp_path)

        async def _reload_without_pub() -> None:
            manager = _make_manager(tmp_path)
            manager._configs["control_config"] = {"_schema_version": "1.0"}
            # _push_sock and _pub_sock are both None — no error expected
            await manager.handle_reload("control_config")

        asyncio.run(_reload_without_pub())
        # If we reach here, no exception was raised

    def test_handle_reload_pub_contains_new_config(
        self, tmp_path: Path
    ) -> None:
        """PUB body 'config' field contains the newly loaded config dict."""
        _write_minimal_control_config(tmp_path)
        frames = _run_broadcast_test(
            tmp_path, "tcp://127.0.0.1:15757", "tcp://127.0.0.1:15757",
            seed_config={}
        )
        data: dict = msgpack.unpackb(frames[1], raw=False)
        config: dict = data["config"]
        # The loaded control_config should have these top-level keys
        assert "soc_limits" in config
        assert "power_limits" in config
