"""AlarmLoop — 1Hz alarm evaluation loop with RTDB I/O and ZMQ.

Owns the asyncio event loop, RTDB seqlock reads, ZMQ REP command server,
ZMQ PUB alarm telemetry, and ZMQ PUSH event sink (logger).

Architecture:
- _poll_commands(): non-blocking REP socket drain with guaranteed reply
- _tick(now_ms): reads RTDB via seqlock, resolves signals, evaluates alarms, publishes events
- run(): 1Hz asyncio loop with timing-corrected sleep
- cleanup(): close sockets and detach RTDB
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import time
from pathlib import Path
from typing import Any

import msgpack
import zmq

from ems_alarm_manager.config import load_alarm_config
from ems_alarm_manager.evaluator import AlarmEvaluator, build_alarm_instances
from ems_alarm_manager.resolver import SignalResolver
from ems_common.ipc import (
    SOCK_ALARM_CMD,
    SOCK_CONFIG_PUB,
    SOCK_LOGGER,
    TOPIC_ALARM,
    TOPIC_CONFIG_RELOAD,
    decode_command_request,
    encode_command_response,
    encode_event,
)
from ems_common.rtdb import (
    MAX_CLUSTERS,
    MAX_RACKS_PER_CLUSTER,
    attach_rtdb,
    detach_rtdb,
)

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Local PUB socket endpoint — alarm_manager binds its own PUB
# (follows Phase 14 precedent: local endpoint defined in loop.py, not ipc.py)
SOCK_ALARM_PUB: str = "ipc:///run/ems/alarm_pub.sock"

# Maximum seqlock retry attempts before falling back with stale data
_SEQLOCK_MAX_RETRIES: int = 100

# Module source name for IPC messages
_SOURCE: str = "alarm_manager"


# ---------------------------------------------------------------------------
# Seqlock helper (mirrors control_manager/loop.py pattern — duplicated per
# Phase 14 deferred refactor note; do NOT import from ems_control_manager)
# ---------------------------------------------------------------------------


def _seqlock_read_section(section: ctypes.Structure) -> ctypes.Structure:
    """Read an RTDB section via seqlock, returning a safe copy.

    Retries until the section's seqlock sequence is even and stable.
    Falls back after _SEQLOCK_MAX_RETRIES with whatever data was read.

    Args:
        section: ctypes Structure with a .lock.sequence field.

    Returns:
        New ctypes instance of the same type, independent of shared memory.
    """
    section_type = type(section)
    copy = section_type()

    for _ in range(_SEQLOCK_MAX_RETRIES):
        seq1: int = section.lock.sequence
        if seq1 & 1:
            # Odd sequence = write in progress, retry immediately
            continue
        ctypes.memmove(
            ctypes.addressof(copy),
            ctypes.addressof(section),
            ctypes.sizeof(section_type),
        )
        seq2: int = section.lock.sequence
        if seq1 == seq2:
            return copy

    logger.warning("Seqlock read exceeded max retries for %s", section_type.__name__)
    return copy


# ---------------------------------------------------------------------------
# AlarmLoop
# ---------------------------------------------------------------------------


class AlarmLoop:
    """1Hz alarm evaluation loop: RTDB reads, signal resolution, ZMQ I/O.

    Reads RTDB each tick via seqlock, resolves alarm signals, drives the
    IEC 62682 state machine via AlarmEvaluator, and publishes alarm events
    to logger (PUSH) and telemetry subscribers (PUB). Serves operator
    commands via a ZMQ REP socket.

    Args:
        config: Validated alarm config dict (from load_alarm_config()).
        rep_endpoint: ZMQ REP socket bind address. Default: SOCK_ALARM_CMD.
        push_endpoint: ZMQ PUSH socket connect address. Default: SOCK_LOGGER.
        pub_endpoint: ZMQ PUB socket bind address. Default: SOCK_ALARM_PUB.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        rep_endpoint: str | None = None,
        push_endpoint: str | None = None,
        pub_endpoint: str | None = None,
        config_sub_endpoint: str | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._config: dict[str, Any] = config
        self._config_path: Path | None = config_path

        # Attach RTDB
        self._shm, self._rtdb = attach_rtdb()

        # Signal resolver — validate paths at startup
        self._resolver: SignalResolver = SignalResolver()
        rules: dict[str, Any] = config.get("rules", {})
        signal_paths: list[str] = [rule["signal"] for rule in rules.values()]
        unknown_paths: list[str] = self._resolver.validate_paths(signal_paths)
        if unknown_paths:
            logger.error(
                "Unknown signal paths in alarm config (fail-open: disabling rules): %s",
                unknown_paths,
            )
            # Disable rules with unknown signal paths
            for alarm_id, rule in rules.items():
                if rule["signal"] in unknown_paths:
                    rule["enabled"] = False

        # Build deduplicated signal paths list (for resolve_all calls)
        self._signal_paths: list[str] = list(dict.fromkeys(signal_paths))

        # Build alarm instances and evaluator
        instances = build_alarm_instances(config)
        self._evaluator: AlarmEvaluator = AlarmEvaluator(instances)

        # Stop signal
        self._stop_event: asyncio.Event = asyncio.Event()

        # ZMQ context (sync — driven from asyncio.to_thread or direct calls)
        self._zmq_ctx: zmq.Context = zmq.Context()

        # REP socket — command server
        _rep_ep: str = rep_endpoint or SOCK_ALARM_CMD
        self._rep: zmq.Socket = self._zmq_ctx.socket(zmq.REP)
        self._rep.setsockopt(zmq.LINGER, 0)
        self._rep.bind(_rep_ep)

        # PUSH socket — event sink (logger)
        _push_ep: str = push_endpoint or SOCK_LOGGER
        self._push: zmq.Socket = self._zmq_ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.LINGER, 0)
        self._push.connect(_push_ep)

        # PUB socket — alarm telemetry broadcast
        _pub_ep: str = pub_endpoint or SOCK_ALARM_PUB
        self._pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(_pub_ep)

        # SUB socket — config_reload events from config_manager
        _config_ep: str = config_sub_endpoint or SOCK_CONFIG_PUB
        self._config_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._config_sub.setsockopt(zmq.LINGER, 0)
        self._config_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONFIG_RELOAD)
        self._config_sub.connect(_config_ep)

        logger.info(
            "AlarmLoop initialised: REP=%s PUSH=%s PUB=%s CONFIG_SUB=%s",
            _rep_ep,
            _push_ep,
            _pub_ep,
            _config_ep,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def stop_event(self) -> asyncio.Event:
        """Asyncio event — set this to request graceful shutdown."""
        return self._stop_event

    async def run(self) -> None:
        """1Hz alarm loop. Runs until stop_event is set.

        Timing-corrected: measures tick duration and subtracts from sleep
        interval to maintain accurate 1Hz cadence.
        """
        logger.info("AlarmLoop running at 1Hz")

        while not self._stop_event.is_set():
            tick_start: float = time.monotonic()

            self._poll_commands()
            now_ms: float = tick_start * 1000.0
            self._tick(now_ms)

            elapsed: float = time.monotonic() - tick_start
            sleep_s: float = max(0.0, 1.0 - elapsed)
            await asyncio.sleep(sleep_s)

        logger.info("AlarmLoop stopped")

    def cleanup(self) -> None:
        """Close ZMQ sockets and detach from RTDB.

        Safe to call multiple times.
        """
        if not self._rep.closed:
            self._rep.close()
        if not self._push.closed:
            self._push.close()
        if not self._pub.closed:
            self._pub.close()
        if not self._config_sub.closed:
            self._config_sub.close()
        self._zmq_ctx.term()
        detach_rtdb(self._shm)
        logger.info("AlarmLoop cleaned up")

    # ------------------------------------------------------------------
    # Command polling (non-blocking REP drain)
    # ------------------------------------------------------------------

    def _poll_commands(self) -> None:
        """Drain all pending REP messages without blocking.

        Decodes each command request, dispatches to the evaluator,
        and sends a response. Always sends a reply — even on errors.
        """
        while True:
            try:
                raw: bytes = self._rep.recv(zmq.NOBLOCK)
            except zmq.Again:
                # No more pending messages
                return

            # Always send a reply — REP socket must not leave requests unanswered
            try:
                action: str
                params: dict[str, Any]
                action, params = decode_command_request(raw)
                reply: bytes = self._dispatch_command(action, params)
            except Exception as exc:
                logger.warning("Command decode/dispatch error: %s", exc)
                reply = encode_command_response(
                    "error", error_msg=f"Internal error: {exc}"
                )

            self._rep.send(reply)

    def _dispatch_command(self, action: str, params: dict[str, Any]) -> bytes:
        """Dispatch a decoded command to the evaluator and encode a response.

        Args:
            action: Command action string.
            params: Command parameters dict.

        Returns:
            Encoded msgpack response bytes.
        """
        if action == "get_active_alarms":
            alarms: list[dict] = self._evaluator.get_active_alarms()
            return encode_command_response("ok", result={"alarms": alarms})

        if action == "acknowledge":
            alarm_id: str = str(params.get("alarm_id", ""))
            result: dict = self._evaluator.acknowledge(alarm_id)
            if result["status"] == "ok":
                return encode_command_response(
                    "ok",
                    result={
                        "alarm_id": alarm_id,
                        "from_state": result["from_state"],
                        "to_state": result["to_state"],
                    },
                )
            return encode_command_response(
                "error", error_msg=result.get("message", "Acknowledge failed")
            )

        if action == "get_alarm_config":
            rules: list[dict] = self._evaluator.get_alarm_config()
            return encode_command_response("ok", result={"rules": rules})

        return encode_command_response(
            "error", error_msg=f"Unknown action: {action!r}"
        )

    # ------------------------------------------------------------------
    # Config reload polling (non-blocking SUB drain)
    # ------------------------------------------------------------------

    def _poll_config_reload(self) -> None:
        """Drain config_reload SUB socket NOBLOCK. Re-reads and applies alarms_config on event.

        Only handles name="alarms_config". Other names are silently ignored.
        Preserves active alarm lifecycle state for still-present alarms.
        On any error reading or validating the config, keeps the current config.
        """
        while True:
            try:
                frames: list[bytes] = self._config_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break

            if len(frames) < 2:
                logger.warning("Config reload SUB received malformed message (< 2 frames)")
                continue

            try:
                data: dict = msgpack.unpackb(frames[1], raw=False)
                config_name: str = str(data.get("name", ""))
            except Exception as exc:
                logger.warning("Config reload SUB decode error: %s", exc)
                continue

            if config_name != "alarms_config":
                logger.debug("Config reload ignored for '%s' (not our config)", config_name)
                continue

            if self._config_path is None:
                logger.warning(
                    "Config reload event received but config_path not set — skipping reload"
                )
                continue

            # Re-read from disk (disk is source of truth; event is just a trigger)
            try:
                new_config: dict[str, Any] = load_alarm_config(self._config_path)
            except Exception as exc:
                logger.error(
                    "Config reload failed for alarms_config: %s — keeping old config", exc
                )
                continue

            # Build new alarm instances from new config
            new_instances: dict = build_alarm_instances(new_config)

            # Preserve lifecycle state for alarms that exist in both old and new configs
            for alarm_id, old_inst in self._evaluator._instances.items():
                if alarm_id in new_instances and old_inst.state != "NORMAL":
                    new_instances[alarm_id].state = old_inst.state
                    new_instances[alarm_id].activated_at = old_inst.activated_at
                    new_instances[alarm_id].acknowledged_at = old_inst.acknowledged_at
                    new_instances[alarm_id].exceeded_since_ms = old_inst.exceeded_since_ms

            # Atomic swap
            self._evaluator._instances = new_instances
            self._config = new_config

            # Rebuild signal paths from new config
            rules: dict[str, Any] = new_config.get("rules", {})
            signal_paths: list[str] = [rule["signal"] for rule in rules.values()]
            self._signal_paths = list(dict.fromkeys(signal_paths))

            logger.info(
                "Config reload applied for alarms_config: %d rules from %s",
                len(new_instances),
                self._config_path,
            )

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self, now_ms: float) -> None:
        """Execute one alarm evaluation cycle.

        1. Read RTDB (clusters, pcs) via seqlock into copies
        2. Build a resolver-compatible object from copies
        3. resolve_all(signal_paths, rtdb_copy) -> values dict
        4. evaluate_tick(values, now_ms) -> events list
        5. For each event: publish on PUSH and PUB

        Args:
            now_ms: Current monotonic time in milliseconds (for delay calculations).
        """
        # ----------------------------------------------------------------
        # Step 0: Poll config_reload events (NOBLOCK — zero cost when idle)
        # ----------------------------------------------------------------
        self._poll_config_reload()

        # ----------------------------------------------------------------
        # Step 1: Read RTDB sections via seqlock (copies, not aliases)
        # ----------------------------------------------------------------
        # PCS section: single read
        pcs_copy = _seqlock_read_section(self._rtdb.pcs)

        # BMS clusters: read each rack individually
        # Build a simple namespace object that the resolver can access via
        # .clusters[c].racks[r] — same shape as EmsRtdb but holding copies
        cluster_copies = _RtdbCopy(pcs_copy, self._rtdb)

        # ----------------------------------------------------------------
        # Step 2+3: Resolve signals from copied RTDB data
        # ----------------------------------------------------------------
        values: dict[str, float | None] = self._resolver.resolve_all(
            self._signal_paths, cluster_copies  # type: ignore[arg-type]
        )

        # ----------------------------------------------------------------
        # Step 4: Evaluate alarms
        # ----------------------------------------------------------------
        events: list[dict] = self._evaluator.evaluate_tick(values, now_ms)

        # ----------------------------------------------------------------
        # Step 5: Publish events
        # ----------------------------------------------------------------
        for event in events:
            ts_ms: int = int(time.time() * 1000)
            event_raw: bytes = encode_event(
                timestamp_ms=ts_ms,
                source=_SOURCE,
                severity=event.get("severity", "info"),
                event_type=TOPIC_ALARM,
                message=(
                    f"{event['alarm_id']}: {event['event_type']} "
                    f"(value={event.get('value')}, threshold={event.get('threshold')})"
                ),
                data=event,
            )
            # PUSH to logger
            try:
                self._push.send(event_raw, zmq.NOBLOCK)
            except zmq.Again:
                logger.warning(
                    "PUSH socket full — alarm event dropped: %s", event.get("alarm_id")
                )

            # PUB to telemetry subscribers (topic prefix + msgpack payload)
            pub_payload: bytes = msgpack.packb(event, use_bin_type=True)
            try:
                self._pub.send_string(TOPIC_ALARM, zmq.SNDMORE | zmq.NOBLOCK)
                self._pub.send(pub_payload, zmq.NOBLOCK)
            except zmq.Again:
                logger.warning(
                    "PUB socket would block — alarm telemetry dropped: %s",
                    event.get("alarm_id"),
                )


# ---------------------------------------------------------------------------
# Internal RTDB copy helper
# ---------------------------------------------------------------------------


class _RackCopy:
    """Lightweight rack data holder for resolver compatibility."""

    __slots__ = (
        "online",
        "max_cell_v",
        "min_cell_v",
        "max_cell_t",
        "min_cell_t",
        "pack_soc",
        "lock",
    )

    def __init__(self, rack: Any) -> None:
        rack_copy = _seqlock_read_section(rack)
        self.online: int = int(rack_copy.online)
        self.max_cell_v: float = float(rack_copy.max_cell_v)
        self.min_cell_v: float = float(rack_copy.min_cell_v)
        self.max_cell_t: float = float(rack_copy.max_cell_t)
        self.min_cell_t: float = float(rack_copy.min_cell_t)
        self.pack_soc: float = float(rack_copy.pack_soc)


class _ClusterCopy:
    """Lightweight cluster data holder for resolver compatibility."""

    def __init__(self, cluster: Any) -> None:
        self.racks: list[_RackCopy] = [
            _RackCopy(cluster.racks[r]) for r in range(MAX_RACKS_PER_CLUSTER)
        ]


class _RtdbCopy:
    """Lightweight RTDB copy with clusters and pcs for resolver compatibility.

    The SignalResolver accesses:
    - rtdb.clusters[c].racks[r].online / max_cell_v / min_cell_v / ...
    - rtdb.pcs.dc_voltage / temperature

    This class mirrors that interface using seqlock-copied data.
    """

    def __init__(self, pcs_copy: Any, rtdb: Any) -> None:
        self.clusters: list[_ClusterCopy] = [
            _ClusterCopy(rtdb.clusters[c]) for c in range(MAX_CLUSTERS)
        ]
        self.pcs: Any = pcs_copy
