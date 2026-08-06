"""ControlLoop — 1Hz control loop with RTDB I/O and ZMQ.

Owns the asyncio event loop, RTDB seqlock reads/writes, ZMQ REP command
server, ZMQ PUB telemetry, ZMQ PUSH event sink, ZMQ SUB for alarm events,
and ZMQ SUB for config_reload events from config_manager.

Architecture:
- _poll_commands(): non-blocking REP socket drain, dispatches to state machine
- _poll_alarm_events(): non-blocking SUB drain, returns highest alarm severity
- _poll_config_reload(): non-blocking SUB drain, re-reads config on event
- _read_bms_thermals(): reads cluster/rack temperatures from RTDB
- _tick(now_s): reads RTDB, polls alarms+reload, calls SM+intelligence, writes RTDB
- run(): 1Hz asyncio loop with timing-corrected sleep
- cleanup(): closes sockets and detaches RTDB
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

from ems_common.ipc import (
    SOCK_CONFIG_PUB,
    SOCK_CONTROL_CMD,
    SOCK_LOGGER,
    TOPIC_ALARM,
    TOPIC_CONFIG_RELOAD,
    TOPIC_CONTROL_STATE,
    TOPIC_STATE_CHANGE,
    decode_command_request,
    encode_command_response,
    encode_event,
    encode_telemetry,
)
from ems_common.rtdb import (
    EmsGpio,
    EmsPcs,
    EmsSystem,
    MAX_CLUSTERS,
    MAX_RACKS_PER_CLUSTER,
    attach_rtdb,
    detach_rtdb,
)
from ems_control_manager.config import load_control_config
from ems_control_manager.intelligence import ControlIntelligence
from ems_control_manager.state_machine import ControlStateMachine, TickResult

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DO index in gpio.do_state[] that safety_manager asserts for PCS emergency stop
# Matches DO_PCS_STOP (bit 5) from safety_manager response_matrix.h
SAFETY_EMERGENCY_DO_INDEX: int = 5

# Local PUB socket endpoint — control_manager binds its own PUB
# (data_manager owns SOCK_TELEMETRY; we use a dedicated endpoint)
SOCK_CONTROL_PUB: str = "ipc:///run/ems/control_pub.sock"

# Alarm SUB endpoint — alarm_manager binds this, control_manager connects
# Defined locally to avoid circular dependency (alarm_manager imports from here)
SOCK_ALARM_PUB: str = "ipc:///run/ems/alarm_pub.sock"

# Maximum seqlock retry attempts before falling back with stale data
_SEQLOCK_MAX_RETRIES: int = 100

# Module source name for IPC messages
_SOURCE: str = "control_manager"

# Alarm severity rank — higher = more severe
_SEVERITY_RANK: dict[str, int] = {"warning": 0, "action": 1, "protection": 2}

# Source-to-int mapping for RTDB system.source_priority (ems_source_priority_t)
_SOURCE_TO_INT: dict[str, int] = {
    "none": 0,
    "solar": 1,
    "grid": 2,
    "bess": 3,
    "dg": 4,
    "manual": 5,
}

# Default BMS temperature when no racks are online (safe operating assumption)
_DEFAULT_BMS_TEMP_C: float = 25.0

# Action-severity cooldown duration (seconds)
_ACTION_COOLDOWN_S: float = 60.0
_PROTECTION_COOLDOWN_S: float = 60.0


# ---------------------------------------------------------------------------
# Seqlock helper (mirrors data_manager/publisher.py pattern)
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


def _now_ms() -> int:
    """Return current monotonic time in milliseconds."""
    return int(time.monotonic() * 1000)


# ---------------------------------------------------------------------------
# ControlLoop
# ---------------------------------------------------------------------------

class ControlLoop:
    """1Hz control loop: RTDB I/O, state machine, intelligence, ZMQ I/O.

    Args:
        config: Validated control config dict (from load_control_config()).
        rep_endpoint: ZMQ REP socket bind address. Default: SOCK_CONTROL_CMD.
        pub_endpoint: ZMQ PUB socket bind address. Default: SOCK_CONTROL_PUB.
        push_endpoint: ZMQ PUSH socket connect address. Default: SOCK_LOGGER.
        alarm_sub_endpoint: ZMQ SUB socket connect address for alarm events.
                            Default: SOCK_ALARM_PUB.
        config_sub_endpoint: ZMQ SUB socket connect address for config_reload events.
                              Default: SOCK_CONFIG_PUB.
        config_path: Path to control_config.yaml for re-reading on hot-reload.
                     If None, config reload events are received but re-read is skipped.
    """

    def __init__(
        self,
        config: dict[str, Any],
        rep_endpoint: str | None = None,
        pub_endpoint: str | None = None,
        push_endpoint: str | None = None,
        alarm_sub_endpoint: str | None = None,
        config_sub_endpoint: str | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._config: dict[str, Any] = config
        self._config_path: Path | None = config_path
        sm_cfg: dict[str, Any] = config.get("state_machine", {})

        # Attach RTDB
        self._shm, self._rtdb = attach_rtdb()

        # State machine
        self._sm: ControlStateMachine = ControlStateMachine(sm_cfg)

        # Intelligence (pure logic — no I/O)
        # Use full config if intelligence sections present, else use empty defaults
        self._intelligence: ControlIntelligence = ControlIntelligence(
            self._config if "soc_limits" in self._config else _default_intelligence_config()
        )

        # Alarm state tracking
        self._last_alarm_severity: str | None = None
        self._alarm_cooldown_until_s: float = 0.0

        # Mode tracking (set via source_priority command or day/night logic)
        self._mode: str = "DAY"

        # Monotonic pcs_command_seq counter (loop-local, written to RTDB on change)
        self._pcs_command_seq: int = 0

        # Stop signal
        self._stop_event: asyncio.Event = asyncio.Event()

        # ZMQ context (sync — we drive it from asyncio.to_thread or direct calls)
        self._zmq_ctx: zmq.Context = zmq.Context()

        # REP socket — command server
        _rep_ep: str = rep_endpoint or SOCK_CONTROL_CMD
        self._rep: zmq.Socket = self._zmq_ctx.socket(zmq.REP)
        self._rep.setsockopt(zmq.LINGER, 0)
        self._rep.bind(_rep_ep)

        # PUB socket — telemetry broadcast
        _pub_ep: str = pub_endpoint or SOCK_CONTROL_PUB
        self._pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(_pub_ep)

        # PUSH socket — event sink (logger)
        _push_ep: str = push_endpoint or SOCK_LOGGER
        self._push: zmq.Socket = self._zmq_ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.LINGER, 0)
        self._push.connect(_push_ep)

        # SUB socket — alarm events from alarm_manager
        _alarm_ep: str = alarm_sub_endpoint or SOCK_ALARM_PUB
        self._alarm_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._alarm_sub.setsockopt(zmq.LINGER, 0)
        self._alarm_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_ALARM)
        self._alarm_sub.connect(_alarm_ep)

        # SUB socket — config_reload events from config_manager
        _config_ep: str = config_sub_endpoint or SOCK_CONFIG_PUB
        self._config_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._config_sub.setsockopt(zmq.LINGER, 0)
        self._config_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONFIG_RELOAD)
        self._config_sub.connect(_config_ep)

        # Telemetry sequence counter
        self._tel_seq: int = 0

        logger.info(
            "ControlLoop initialised: REP=%s PUB=%s PUSH=%s ALARM_SUB=%s CONFIG_SUB=%s",
            _rep_ep,
            _pub_ep,
            _push_ep,
            _alarm_ep,
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
        """1Hz control loop. Runs until stop_event is set.

        Timing-corrected: measures tick duration and subtracts from sleep
        interval to maintain accurate 1Hz cadence.
        """
        sm_cfg: dict[str, Any] = self._config.get("state_machine", {})
        interval_s: float = float(sm_cfg.get("loop_interval_ms", 1000)) / 1000.0

        logger.info("ControlLoop running at %.0fHz", 1.0 / interval_s)

        while not self._stop_event.is_set():
            tick_start: float = time.monotonic()

            self._poll_commands()
            self._tick(tick_start)

            elapsed: float = time.monotonic() - tick_start
            sleep_s: float = max(0.0, interval_s - elapsed)
            await asyncio.sleep(sleep_s)

        logger.info("ControlLoop stopped")

    def cleanup(self) -> None:
        """Close ZMQ sockets and detach from RTDB.

        Safe to call multiple times.
        """
        if not self._rep.closed:
            self._rep.close()
        if not self._pub.closed:
            self._pub.close()
        if not self._push.closed:
            self._push.close()
        if not self._alarm_sub.closed:
            self._alarm_sub.close()
        if not self._config_sub.closed:
            self._config_sub.close()
        self._zmq_ctx.term()
        detach_rtdb(self._shm)
        logger.info("ControlLoop cleaned up")

    # ------------------------------------------------------------------
    # Command polling (non-blocking REP drain)
    # ------------------------------------------------------------------

    def _poll_commands(self) -> None:
        """Drain all pending REP messages without blocking.

        Decodes each command request, dispatches to the state machine,
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
                ok: bool
                err: str
                ok, err = self._dispatch_command(action, params)
                if ok:
                    reply: bytes = encode_command_response(
                        "ok", result={"action": action}
                    )
                else:
                    reply = encode_command_response("error", error_msg=err)
            except Exception as exc:
                logger.warning("Command decode/dispatch error: %s", exc)
                reply = encode_command_response(
                    "error", error_msg=f"Internal error: {exc}"
                )

            self._rep.send(reply)

    def _dispatch_command(
        self, action: str, params: dict[str, Any]
    ) -> tuple[bool, str]:
        """Dispatch a decoded command to the appropriate state machine method.

        Args:
            action: Command action string.
            params: Command parameters dict.

        Returns:
            (ok, error_msg) — error_msg is empty string when ok=True.
        """
        if action == "mode_change":
            target: str = str(params.get("target_state", ""))
            return self._sm.request_mode_change(target)

        elif action == "manual_setpoint":
            power_kw: float = float(params.get("power_kw", 0.0))
            return self._sm.request_manual_setpoint(power_kw)

        elif action == "fault_reset":
            return self._sm.request_fault_reset()

        elif action == "maintenance_enter":
            return self._sm.request_maintenance_enter()

        elif action == "maintenance_exit":
            return self._sm.request_maintenance_exit()

        elif action == "source_priority":
            mode: str = str(params.get("mode", ""))
            if mode == "manual":
                self._mode = "MANUAL"
            elif mode in ("day", "day_first"):
                self._mode = "DAY"
            elif mode in ("night", "night_first"):
                self._mode = "NIGHT"
            logger.info("source_priority command: mode=%s -> %s", mode, self._mode)
            return True, ""

        else:
            return False, f"Unknown command action: {action!r}"

    # ------------------------------------------------------------------
    # Alarm event polling (non-blocking SUB drain)
    # ------------------------------------------------------------------

    def _poll_alarm_events(self) -> str | None:
        """Drain alarm SUB socket NOBLOCK. Return highest severity this tick.

        The alarm_manager PUB format: [topic_frame, body_frame]
        Body is raw msgpack (NOT encode_event wrapper) per RESEARCH.md Pitfall 1.

        Returns:
            Highest severity seen this tick: "warning", "action", "protection", or None.
        """
        tick_highest: str | None = None
        tick_rank: int = -1

        while True:
            try:
                frames: list[bytes] = self._alarm_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break

            if len(frames) < 2:
                logger.warning("Alarm SUB received malformed message (< 2 frames)")
                continue

            try:
                body: dict = msgpack.unpackb(frames[1], raw=False)
                severity: str = str(body.get("severity", ""))
                rank: int = _SEVERITY_RANK.get(severity, -1)
                if rank > tick_rank:
                    tick_rank = rank
                    tick_highest = severity
            except Exception as exc:
                logger.warning("Alarm SUB decode error: %s", exc)

        return tick_highest

    # ------------------------------------------------------------------
    # Config reload polling (non-blocking SUB drain)
    # ------------------------------------------------------------------

    def _poll_config_reload(self) -> None:
        """Drain config_reload SUB socket NOBLOCK. Re-reads config on event.

        Only handles name="control_config". Other names are silently ignored.
        On validation failure, keeps the current config.
        """
        while True:
            try:
                frames: list[bytes] = self._config_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break

            if len(frames) < 2:
                logger.warning("Config reload SUB received malformed message")
                continue

            try:
                data: dict = msgpack.unpackb(frames[1], raw=False)
                config_name: str = str(data.get("name", ""))
            except Exception as exc:
                logger.warning("Config reload SUB decode error: %s", exc)
                continue

            if config_name != "control_config":
                logger.debug("Config reload ignored for '%s' (not our config)", config_name)
                continue

            if self._config_path is None:
                logger.warning(
                    "Config reload event received but config_path not set — skipping reload"
                )
                continue

            # Re-read from disk (disk is source of truth; event is just a trigger)
            try:
                new_config: dict[str, Any] = load_control_config(self._config_path)
            except Exception as exc:
                logger.error(
                    "Config reload failed for control_config: %s — keeping old config", exc
                )
                continue

            # Atomic swap: replace config and update intelligence
            self._config = new_config
            self._intelligence.update_config(new_config)
            logger.info(
                "Config reload applied for control_config from %s", self._config_path
            )

    # ------------------------------------------------------------------
    # BMS thermal reads from RTDB
    # ------------------------------------------------------------------

    def _read_bms_thermals(self) -> tuple[float, float]:
        """Read BMS rack temperatures from RTDB for derating computation.

        Iterates all clusters/racks, reads online racks via seqlock,
        and returns (max_cell_t, min_cell_t) across all online racks.

        Returns:
            (bms_max_cell_t, bms_min_cell_t) — defaults to (25.0, 25.0) if
            no racks are online.
        """
        bms_max: float = -float("inf")
        bms_min: float = float("inf")
        found_online: bool = False

        for c in range(MAX_CLUSTERS):
            for r in range(MAX_RACKS_PER_CLUSTER):
                rack = self._rtdb.clusters[c].racks[r]
                if rack.online == 0:
                    continue
                rack_copy = _seqlock_read_section(rack)
                if rack_copy.online == 0:
                    continue
                found_online = True
                bms_max = max(bms_max, float(rack_copy.max_cell_t))
                bms_min = min(bms_min, float(rack_copy.min_cell_t))

        if not found_online:
            return _DEFAULT_BMS_TEMP_C, _DEFAULT_BMS_TEMP_C

        return bms_max, bms_min

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self, now_s: float) -> None:
        """Execute one control cycle.

        Execution order:
        1. Read RTDB (pcs, gpio, system) via seqlock
        2. Derive safety_emergency from gpio.do_state
        3. Poll alarm SUB events, update alarm severity tracking
        4. Poll config_reload SUB events (NOBLOCK)
        5. Read BMS thermals for derating
        6. If protection alarm active, inject fault into SM before tick
        7. SM tick
        8. Intelligence evaluate (derating, ramping, SOC check)
        9. If soc_cutoff_hit, request idle transition
        10. Write RTDB: use intel desired_setpoint_kw, write derating + source
        11. Publish state_change event on transition
        12. Publish control.state telemetry on PUB

        Args:
            now_s: Current monotonic time in seconds (for SM tick timing).
        """
        # ----------------------------------------------------------------
        # Step 1: Read RTDB sections via seqlock (copies, not aliases)
        # ----------------------------------------------------------------
        pcs_copy: EmsPcs = _seqlock_read_section(self._rtdb.pcs)
        gpio_copy: EmsGpio = _seqlock_read_section(self._rtdb.gpio)
        system_copy: EmsSystem = _seqlock_read_section(self._rtdb.system)

        # ----------------------------------------------------------------
        # Step 2: Derive safety_emergency from gpio DO state
        # DO-5 = PCS emergency stop — asserted by safety_manager on any
        # latching event (E-Stop, Fire, Flood) or ACDB loss
        # ----------------------------------------------------------------
        safety_emergency: bool = bool(
            gpio_copy.do_state[SAFETY_EMERGENCY_DO_INDEX]
        )

        # ----------------------------------------------------------------
        # Step 3: Poll alarm events and update severity tracking
        # ----------------------------------------------------------------
        tick_alarm_severity: str | None = self._poll_alarm_events()

        if tick_alarm_severity is not None:
            tick_rank: int = _SEVERITY_RANK.get(tick_alarm_severity, -1)
            last_rank: int = _SEVERITY_RANK.get(self._last_alarm_severity or "", -1)
            if tick_rank >= last_rank:
                self._last_alarm_severity = tick_alarm_severity
                if tick_alarm_severity in ("protection", "action"):
                    self._alarm_cooldown_until_s = now_s + _PROTECTION_COOLDOWN_S

        # Check if cooldown expired — reset alarm severity
        if self._last_alarm_severity and now_s >= self._alarm_cooldown_until_s:
            self._last_alarm_severity = None

        # ----------------------------------------------------------------
        # Step 4: Poll config_reload events (NOBLOCK — zero cost when idle)
        # ----------------------------------------------------------------
        self._poll_config_reload()

        # ----------------------------------------------------------------
        # Step 5: Read BMS thermals for derating
        # ----------------------------------------------------------------
        bms_max_t: float
        bms_min_t: float
        bms_max_t, bms_min_t = self._read_bms_thermals()

        # ----------------------------------------------------------------
        # Step 6: If protection alarm is active, inject fault into SM
        # This ensures FAULT state even if SM hasn't processed it yet
        # ----------------------------------------------------------------
        if self._last_alarm_severity == "protection":
            self._sm.request_mode_change("fault")

        # ----------------------------------------------------------------
        # Step 7: Call state machine
        # ----------------------------------------------------------------
        result: TickResult = self._sm.tick(
            now_s=now_s,
            pcs_state=pcs_copy.state,
            pcs_fault_code=pcs_copy.fault_code,
            safety_emergency=safety_emergency,
            current_rtdb_state=system_copy.control_state,
        )

        # ----------------------------------------------------------------
        # Step 8: Intelligence evaluate (derating, ramping, SOC, source)
        # ----------------------------------------------------------------
        # Build gpio_di list from RTDB gpio.di
        gpio_di: list[int] = [int(gpio_copy.di[i]) for i in range(8)]

        intel_result = self._intelligence.evaluate(
            now_s=now_s,
            raw_setpoint_kw=result.setpoint_kw,
            pcs_state=pcs_copy.state,
            safety_emergency=safety_emergency,
            gpio_di=gpio_di,
            total_soc=float(system_copy.total_soc),
            bms_max_cell_t=bms_max_t,
            bms_min_cell_t=bms_min_t,
            pcs_temp_c=float(pcs_copy.temperature),
            alarm_severity=self._last_alarm_severity,
            current_state=result.state,
            mode=self._mode,
            solar_available=False,  # M2: no PV meter integration yet
        )

        # ----------------------------------------------------------------
        # Step 9: If SOC cutoff hit, request IDLE transition (takes effect
        # next tick per Pitfall 5 — only when SM is in stable state)
        # ----------------------------------------------------------------
        if intel_result.soc_cutoff_hit:
            self._sm.request_mode_change("idle")
            logger.info("SOC cutoff hit — requesting IDLE transition")

        # ----------------------------------------------------------------
        # Step 10: Write RTDB system section via seqlock
        # single-writer convention: control_manager owns the system section
        # ----------------------------------------------------------------
        sys = self._rtdb.system
        sys.lock.sequence += 1  # begin (odd = write in progress)
        sys.control_state = result.state
        # Use intelligence desired_setpoint_kw (ramped + derated) instead of raw
        sys.active_setpoint_kw = intel_result.desired_setpoint_kw
        sys.active_derating_pct = intel_result.active_derating_pct
        sys.source_priority = _SOURCE_TO_INT.get(intel_result.active_source, 0)
        if result.pcs_command_changed:
            self._pcs_command_seq += 1
            sys.pcs_command = result.pcs_command
            sys.pcs_command_seq = self._pcs_command_seq
        sys.last_update_ms = _now_ms()
        sys.lock.sequence += 1  # end (even = write complete)

        # ----------------------------------------------------------------
        # Step 11: Publish state_change event on transition
        # ----------------------------------------------------------------
        if result.state_changed:
            ts: int = int(time.time() * 1000)
            event_data: dict[str, Any] = {
                "new_state": result.state,
                "state": result.state,
                "prev_state": result.prev_state,
                "setpoint_kw": intel_result.desired_setpoint_kw,
            }
            event_raw: bytes = encode_event(
                timestamp_ms=ts,
                source=_SOURCE,
                severity="info",
                event_type=TOPIC_STATE_CHANGE,
                message=f"State transition: {result.prev_state} -> {result.state}",
                data=event_data,
            )
            try:
                self._push.send(event_raw, zmq.NOBLOCK)
            except zmq.Again:
                logger.warning("PUSH socket full — state_change event dropped")

        # ----------------------------------------------------------------
        # Step 12: Publish control.state telemetry on PUB
        # ----------------------------------------------------------------
        self._tel_seq += 1
        ts_ms: int = int(time.time() * 1000)
        payload: dict[str, Any] = {
            "state": result.state,
            "setpoint_kw": intel_result.desired_setpoint_kw,
            "pcs_command": result.pcs_command,
            "safety_emergency": safety_emergency,
            "active_derating_pct": intel_result.active_derating_pct,
            "active_source": intel_result.active_source,
        }
        telemetry: bytes = encode_telemetry(
            timestamp_ms=ts_ms,
            seq=self._tel_seq,
            source=_SOURCE,
            topic=TOPIC_CONTROL_STATE,
            payload=payload,
        )
        try:
            self._pub.send_string(TOPIC_CONTROL_STATE, zmq.SNDMORE | zmq.NOBLOCK)
            self._pub.send(telemetry, zmq.NOBLOCK)
        except zmq.Again:
            logger.warning("PUB socket would block — telemetry dropped")


# ---------------------------------------------------------------------------
# Helper: default intelligence config when not provided in control config
# ---------------------------------------------------------------------------

def _default_intelligence_config() -> dict[str, Any]:
    """Return minimal intelligence config for loops constructed without full config."""
    return {
        "soc_limits": {"charge_cutoff_pct": 95, "discharge_cutoff_pct": 10},
        "power_limits": {"max_charge_kw": 100, "max_discharge_kw": 100},
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
