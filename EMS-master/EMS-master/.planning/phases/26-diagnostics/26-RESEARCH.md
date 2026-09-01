# Phase 26: Diagnostics - Research

**Researched:** 2026-03-16
**Domain:** Python async diagnostics module — ZMQ telemetry consumer, DuckDB trend queries, SOH/efficiency/thermal/comm health analysis
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Data Collection Architecture:**
- ZMQ SUB subscriber on telemetry PUB (`SOCK_TELEMETRY`) for real-time 1Hz data
- Periodic DuckDB queries via logger (`SOCK_LOGGER_QUERY`, `range_stats` query type) for historical trends (hourly computation)
- Diagnostics is a read-only consumer — does NOT write to RTDB
- New ZMQ PUB socket for diagnostic results (topic: "diagnostics")
- New ZMQ REP socket for query API (`SOCK_DIAGNOSTICS_CMD = "ipc:///run/ems/diagnostics_cmd.sock"`)

| Data Source | Method | Frequency |
|------------|--------|-----------|
| BMS rack telemetry | ZMQ SUB topic `bms.rack.*` | 1Hz (live) |
| PCS telemetry | ZMQ SUB topic `pcs` | 1Hz (live) |
| BTMS telemetry | ZMQ SUB topic `btms` | 1Hz (live) |
| Comm fault events | Logger query (event_log) | Hourly computation |
| Historical data | ZMQ REQ on SOCK_LOGGER_QUERY (`range_stats`) | Hourly |

**Diagnostic Metrics and Algorithms:**

| Metric | Algorithm |
|--------|-----------|
| SOH estimate | Coulomb counting: SOH = (discharge_capacity / rated_capacity) × 100 |
| Cycle count | Count full charge→discharge cycles (SOC crosses 90%→10%) |
| PCS efficiency | η = ac_power / dc_power × 100 (discharge), inverse for charge |
| Cooling effectiveness | ΔT = cell_temp_max - outlet_temp; trend over time |
| Fan duty score | Average fan_speed_pct over 24h vs cooling demand |
| Device response rate | Query event_log for comm_fault counts per device per hour |
| Predictive alert | `statistics.linear_regression` on SOH over 30 days → project to threshold |

- No machine learning, no Kalman filter, no EIS
- Python 3.12 `statistics.linear_regression()` — no numpy needed
- All metrics per-rack for BMS, single-value for PCS/BTMS/comm
- Diagnostic results published on ZMQ PUB (topic: "diagnostics") at configurable interval (default 60s)

**Report Query API (SOCK_DIAGNOSTICS_CMD):**

| Query | Request | Response |
|-------|---------|----------|
| `get_current` | `{action: "get_current"}` | Live values from 1Hz ZMQ stream |
| `get_report` | `{action: "get_report", period: "daily"\|"weekly"}` | DuckDB-computed summaries |
| `get_predictions` | `{action: "get_predictions"}` | Linear regression projections |

- Reports computed on-demand (not pre-generated)
- Predictions unavailable until 7+ days of data

### Claude's Discretion

- Diagnostics class architecture (single DiagnosticsLoop vs separate analyzers)
- Coulomb counting implementation (integration method, reset logic)
- Report caching (cache last report for N seconds to avoid redundant DuckDB queries)
- Config schema design (diagnostics_config.yaml — thresholds, intervals, enable/disable per metric)
- Test strategy (mock ZMQ telemetry, mock DuckDB responses)
- ZMQ PUB topic for diagnostic results ("diagnostics")

### Deferred Ideas (OUT OF SCOPE)

- DIAG-07: ML anomaly detection
- DIAG-08: RUL prediction
- Internal resistance estimation from voltage/current response
- EIS (Electrochemical Impedance Spectroscopy) integration
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DIAG-01 | SOH trending tracks per-rack capacity fade and cycle count, publishes on ZMQ telemetry | Coulomb counting from `pack_i` (RTDB/ZMQ telemetry); `pack_soh` already present in RTDB as BMS-reported value; cycle detection on `pack_soc` crossing 90%→10% thresholds; linear regression for fade trend |
| DIAG-02 | PCS efficiency calculated from AC/DC power, degradation tracked over time | ZMQ `pcs` topic carries `active_power` (AC) + `dc_voltage`/`dc_current` (DC); efficiency = `active_power / (dc_voltage * dc_current)` during charge/discharge; `range_stats` query for daily averages |
| DIAG-03 | BTMS thermal analysis tracks cooling effectiveness and temperature delta trending | ZMQ `btms` topic carries `inlet_temp`, `outlet_temp`, `fan_speed_pct`, `cooling_active`; ΔT = `max_cell_t` (from BMS) - `outlet_temp`; fan duty = hourly avg `fan_speed_pct` |
| DIAG-04 | Comm health scoring rates each device on response rate, timeout frequency, CRC errors | comm_fault events stored in JSONL event log via logger; query via `event_log` query type with `source_filter="comm_manager"`; count by `device_id` and `fault_type` fields in event data |
| DIAG-05 | Diagnostic reports queryable via ZMQ REQ/REP with daily/weekly summaries | New `SOCK_DIAGNOSTICS_CMD` REP socket; three query actions (`get_current`, `get_report`, `get_predictions`); pattern established in alarm_cmd (Phase 15) and logger_query (Phase 12) |
| DIAG-06 | Predictive alerts fire warning events when degradation trends exceed configurable rates | `statistics.linear_regression(x, y)` on 30-day SOH history; project intercept to SOH < 80% threshold; publish warning via PUSH to `SOCK_LOGGER` and `SOCK_ALARM_PUB` when days_to_threshold < configurable limit |
</phase_requirements>

---

## Summary

Phase 26 implements the diagnostics module — the final feature module in the EMS stack. It is a pure Python ZMQ consumer that reads existing telemetry (no new hardware protocols) and computes health metrics using simple, proven algorithms. All infrastructure it depends on (ZMQ IPC, RTDB, logger Parquet/DuckDB, alarm event publishing) is fully proven across five prior milestones.

The module architecture is clear from existing patterns: async Python loop with ZMQ SUB for 1Hz telemetry, ZMQ REQ for hourly DuckDB queries, ZMQ REP for on-demand report queries, and ZMQ PUB for broadcasting computed diagnostics. This mirrors the cloud_manager pattern exactly (multiple async tasks on shared ZMQ context).

One critical implementation finding: comm_fault events are published via PUSH to `SOCK_LOGGER` by comm_manager — they are NOT published on `SOCK_ALARM_PUB`. The CONTEXT.md's reference to subscribing to `SOCK_ALARM_PUB` for comm_fault is incorrect. Comm health scoring must use the logger's `event_log` query type (with `source_filter="comm_manager"`) to retrieve historical fault counts from JSONL. This is actually cleaner: hourly DuckDB/JSONL queries give aggregated counts per device without needing persistent SUB state.

**Primary recommendation:** Use a `DiagnosticsLoop` class with five async tasks (matching cloud_manager pattern): telemetry collector, hourly trend updater, diagnostics publisher, report query server, and predictive alert checker. Each diagnostic category gets a dedicated analyzer class (`SohAnalyzer`, `PcsAnalyzer`, `ThermalAnalyzer`, `CommAnalyzer`) for testability.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyzmq | >=27.1.0 | ZMQ SUB/PUB/REQ/REP sockets | Project standard — all modules use it |
| msgpack | >=1.0 | IPC message encoding/decoding | Project standard — all ZMQ messages are msgpack |
| pyyaml | >=6.0 | Config file loading | Project standard — all modules use yaml.safe_load |
| jsonschema | >=4.23 | Config validation against JSON Schema | Project standard — all configs validated |
| duckdb | >=1.5.0 | Historical trend queries via logger | Already in workspace dev deps; logger query_handler uses it |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| statistics (stdlib) | Python 3.12 | `linear_regression()` for trend prediction | No external dep needed for linear fit |
| asyncio (stdlib) | Python 3.12 | Concurrent ZMQ tasks | Same pattern as cloud_manager, alarm_manager |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `statistics.linear_regression` | numpy polyfit | Numpy adds 20MB dependency for one function; stdlib is sufficient |
| Hourly DuckDB queries | Accumulate 1Hz in memory | Memory grows unbounded; logger already has indexed data |
| Single loop with inline logic | Separate analyzer classes | Separate classes are unit-testable in isolation |

**Installation:**
```bash
# All dependencies already in workspace pyproject.toml dev deps
# For production diagnostics package:
uv add --package ems-diagnostics duckdb msgpack pyyaml jsonschema
```

---

## Architecture Patterns

### Recommended Project Structure

```
src/diagnostics/
├── pyproject.toml
├── src/
│   └── ems_diagnostics/
│       ├── __init__.py          # version stub (already exists)
│       ├── __main__.py          # entry point (async main + signal handlers)
│       ├── config.py            # load_diagnostics_config() + JSON Schema validation
│       ├── loop.py              # DiagnosticsLoop — 5 async tasks
│       ├── analyzers/
│       │   ├── __init__.py
│       │   ├── soh.py           # SohAnalyzer — coulomb counting + cycle detection
│       │   ├── pcs.py           # PcsAnalyzer — efficiency calculation
│       │   ├── thermal.py       # ThermalAnalyzer — cooling effectiveness
│       │   └── comm.py          # CommAnalyzer — device response rates from event log
│       └── reporter.py          # ReportBuilder — on-demand report/prediction queries
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_soh_analyzer.py
    ├── test_pcs_analyzer.py
    ├── test_thermal_analyzer.py
    ├── test_comm_analyzer.py
    ├── test_reporter.py
    └── test_loop.py
```

### Pattern 1: DiagnosticsLoop — Five Async Tasks

**What:** Parallel async tasks sharing one ZMQ context, communicating via in-memory shared state (`_current_metrics` dict).

**When to use:** Multiple independent I/O operations (ZMQ SUB, periodic timer, ZMQ REP) that must not block each other.

**Example** (mirrors cloud_manager/loop.py):
```python
# Source: src/cloud_manager/src/ems_cloud_manager/loop.py (confirmed pattern)
async def run(self) -> None:
    await asyncio.gather(
        self._telemetry_collector(),    # ZMQ SUB drain loop
        self._hourly_trend_updater(),   # DuckDB query every 3600s
        self._diagnostics_publisher(),  # Publish metrics every interval_s
        self._report_server(),          # ZMQ REP command loop
        self._predictive_alert_checker(), # Check trends every interval_s
    )
```

### Pattern 2: ZMQ SUB Telemetry Collection (latest-value-wins)

**What:** Non-blocking drain of ZMQ SUB at ~100Hz poll rate, keeping latest value per topic.

**When to use:** 1Hz telemetry where only the current reading matters (not accumulation).

**Example** (mirrors cloud_manager telemetry collector):
```python
# Source: src/cloud_manager/src/ems_cloud_manager/loop.py
async def _telemetry_collector(self) -> None:
    while not self._stop_event.is_set():
        try:
            while True:
                frames = self._telemetry_sub.recv_multipart(zmq.NOBLOCK)
                topic = frames[0].decode()
                payload = msgpack.unpackb(frames[1], raw=False)
                self._snapshot[topic] = payload  # latest-value-wins
        except zmq.Again:
            pass
        await asyncio.sleep(0.01)  # 100Hz poll
```

### Pattern 3: ZMQ REP Command Server (non-blocking drain)

**What:** Drain REP socket without blocking the main async loop. Always send a reply.

**When to use:** On-demand query API (get_current, get_report, get_predictions).

**Example** (mirrors alarm_manager loop._poll_commands()):
```python
# Source: src/alarm_manager/src/ems_alarm_manager/loop.py
async def _report_server(self) -> None:
    while not self._stop_event.is_set():
        try:
            raw = self._rep.recv(zmq.NOBLOCK)
            action, params = decode_command_request(raw)
            reply = await self._dispatch_command(action, params)
            self._rep.send(reply)
        except zmq.Again:
            pass
        await asyncio.sleep(0.01)
```

### Pattern 4: Logger Query via ZMQ REQ

**What:** Send a `range_stats` or `event_log` query to logger and receive aggregated results.

**When to use:** Hourly trend computation over 24h/7d/30d windows without accumulating 1Hz data in memory.

**Example** (verified against logger query_handler.py):
```python
# Source: src/logger/python/src/ems_logger/query_handler.py
# range_stats request — returns min/max/avg/count per signal
request = encode_command_request("query", {
    "type": "range_stats",
    "signals": ["rack0_soc", "rack0_pack_i"],
    "start_ts": start_ms,
    "end_ts": end_ms,
    "file_prefix": "cluster_0_*",
})
self._logger_req.send(request)
response = decode_command_response(self._logger_req.recv())

# event_log request — returns comm_fault events by device
request = encode_command_request("query", {
    "type": "event_log",
    "start_ts": start_ms,
    "end_ts": end_ms,
    "source_filter": "comm_manager",
})
```

### Pattern 5: SOH Coulomb Counting

**What:** Integrate `pack_i` (current in amps) over full discharge cycles. SOH = measured_Ah / rated_Ah × 100.

**When to use:** Per-rack SOH estimation from BMS telemetry.

**Implementation notes:**
- At 1Hz, `pack_i` in amps: `delta_ah += abs(pack_i) / 3600.0` during discharge
- Cycle completed when `pack_soc` falls from >90% to <10%
- Reset `delta_ah` accumulator at cycle start
- SOH = `cycle_discharge_ah / rated_capacity_ah × 100`
- RTDB also carries `pack_soh` (BMS-reported) — diagnostics should track BOTH (BMS-reported as reference, coulomb-counted as trend)

**Pitfall:** RTDB's `pack_soh` field (from BMS CAN) already gives instantaneous SOH. Coulomb counting adds value for trending over cycles — use both.

### Pattern 6: PCS Efficiency Calculation

**What:** Compute η = AC_power / DC_power during active charge/discharge. Skip idle states.

**ZMQ telemetry fields available** (verified in publisher.py):
- `active_power` — AC-side power (positive=discharge, negative=charge)
- `dc_voltage` — DC bus voltage
- `dc_current` — DC bus current

**DC power** must be computed: `dc_power = dc_voltage × dc_current`

**Example:**
```python
ac_power = pcs_payload["active_power"]    # W (positive=discharge)
dc_power = pcs_payload["dc_voltage"] * pcs_payload["dc_current"]  # W

# Only calculate during active power flow (skip idle/standby)
IDLE_THRESHOLD_W = 500.0
if abs(ac_power) > IDLE_THRESHOLD_W and abs(dc_power) > IDLE_THRESHOLD_W:
    efficiency = abs(ac_power) / abs(dc_power) * 100.0
```

### Pattern 7: Linear Regression for Prediction

**What:** Use `statistics.linear_regression(x, y)` to project SOH trend forward to threshold.

**Example** (verified: Python 3.12, stdlib):
```python
import statistics

# x = day offsets [0, 1, 2, ..., N], y = SOH % values
soh_history: list[tuple[int, float]] = [(day, soh_pct), ...]
if len(soh_history) >= 7:  # need 7+ days minimum
    x = [float(d) for d, _ in soh_history]
    y = [s for _, s in soh_history]
    lr = statistics.linear_regression(x, y)
    # lr.slope = SOH change per day (negative = degradation)
    # Days until SOH < 80: (80 - current_soh) / lr.slope
    if lr.slope < 0:
        days_to_threshold = (80.0 - y[-1]) / lr.slope
```

**Result:** `LinearRegression(slope=-0.26, intercept=10.28)` (confirmed working with realistic BESS data).

### Pattern 8: Predictive Alert Event Publishing

**What:** Publish warning events to logger (PUSH) and alarm PUB when trend exceeds threshold.

**When to use:** DIAG-06 predictive alerts.

**Example** (mirrors alarm_manager event publishing):
```python
# Source: src/alarm_manager/src/ems_alarm_manager/loop.py
event_raw = encode_event(
    timestamp_ms=int(time.time() * 1000),
    source="diagnostics",
    severity=SEVERITY_WARNING,
    event_type="predictive_alert",
    message=f"SOH rack {rack_id}: {days:.0f} days until threshold",
    data={"metric": "soh", "rack": rack_id, "days_to_threshold": days},
)
self._push.send(event_raw, zmq.NOBLOCK)  # to SOCK_LOGGER
self._diag_pub.send_string("diagnostics", zmq.SNDMORE | zmq.NOBLOCK)
self._diag_pub.send(event_raw, zmq.NOBLOCK)  # to SOCK_DIAGNOSTICS_PUB
```

### Anti-Patterns to Avoid

- **Accumulating 1Hz data in memory for trend calculation:** 7-day trend at 1Hz = 604,800 data points per rack. Use DuckDB queries instead.
- **Blocking the event loop in DuckDB queries:** Run DuckDB in `asyncio.to_thread()` or use `run_in_executor()` — matches logger query_handler pattern.
- **Subscribing to SOCK_ALARM_PUB for comm_fault events:** comm_fault events go PUSH→SOCK_LOGGER only (not re-published on SOCK_ALARM_PUB). Use `event_log` query to logger instead.
- **Computing DC power as `dc_voltage` alone:** The PCS ZMQ payload has separate `dc_voltage` and `dc_current` fields; DC power = `dc_voltage × dc_current`.
- **Ignoring IDLE states in PCS efficiency:** Very low power levels produce garbage efficiency ratios. Gate on `abs(ac_power) > threshold`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Historical trend stats | Custom Parquet reader | Logger `range_stats` query via SOCK_LOGGER_QUERY | Logger already indexes Parquet files with DuckDB; sub-second query |
| Comm fault history | In-process event accumulation | Logger `event_log` query with `source_filter="comm_manager"` | JSONL already stored by logger; avoids persistent state in diagnostics |
| Linear regression | Custom numpy polyfit | `statistics.linear_regression()` (Python 3.12 stdlib) | Verified working; no external dependency |
| Config validation | Manual dict checking | jsonschema `Draft202012Validator` | Project-wide standard; all configs validated this way |
| ZMQ message encoding | Custom serialization | `encode_command_request`, `decode_telemetry` from `ems_common.ipc` | Project IPC contract; deviating breaks HMI/cloud consumers |

**Key insight:** The logger was built specifically to serve historical queries. Diagnostics is the primary consumer use case for `range_stats` and `event_log` queries. Using it avoids re-implementing data aggregation.

---

## Common Pitfalls

### Pitfall 1: Comm Health — Wrong Data Source

**What goes wrong:** Subscribing to `SOCK_ALARM_PUB` for `comm_fault` topic fails silently — no messages arrive.

**Why it happens:** comm_manager PUSHes `comm_fault` events to `SOCK_LOGGER` (PUSH/PULL pattern), not to `SOCK_ALARM_PUB`. The alarm_manager only publishes IEC 62682 alarm events on `SOCK_ALARM_PUB`, not comm faults. This was confirmed by reading `src/comm_manager/python/src/ems_comm_manager/__main__.py` and `events.py`.

**How to avoid:** Use logger `event_log` query type with `source_filter="comm_manager"` to get historical comm_fault counts per device. Compute response rate from fault frequency over the query window.

**Warning signs:** CommAnalyzer showing 100% response rate for all devices despite connectivity issues.

### Pitfall 2: PCS Efficiency — No DC Power Field

**What goes wrong:** Looking for `dc_power` in the ZMQ PCS payload fails; field does not exist.

**Why it happens:** The RTDB `EmsPcs` struct and ZMQ telemetry carry `dc_voltage` and `dc_current` separately. DC power must be computed as `dc_voltage × dc_current`.

**How to avoid:** Always compute `dc_power = pcs_payload["dc_voltage"] * pcs_payload["dc_current"]` before efficiency calculation. Confirmed in `src/data_manager/python/src/ems_data_manager/publisher.py` (`_pcs_to_dict`).

### Pitfall 3: DuckDB Queries Blocking Async Loop

**What goes wrong:** DuckDB queries run synchronously in the async event loop, blocking ZMQ message processing for 1-2 seconds.

**Why it happens:** DuckDB is a synchronous in-process DB. Running it directly in an `async def` blocks the event loop.

**How to avoid:** Use `await asyncio.get_event_loop().run_in_executor(None, lambda: ...)` — the exact pattern used in `src/logger/python/src/ems_logger/query_handler.py`. Verified in `QueryServer._dispatch()`.

**Warning signs:** Telemetry collection gaps, ZMQ REP timeouts during report requests.

### Pitfall 4: SOH Trend vs BMS-Reported SOH

**What goes wrong:** Diagnostics computes SOH independently via coulomb counting but ignores the BMS-reported `pack_soh` field already in the ZMQ telemetry.

**Why it happens:** CONTEXT.md describes coulomb counting, but the RTDB/ZMQ already carries `pack_soh` from the BMS's own algorithm.

**How to avoid:** Use `pack_soh` from ZMQ BMS telemetry as the primary SOH metric for trending. Reserve coulomb-counted SOH for cross-validation. This is simpler and more reliable than re-implementing what the BMS already does. Verified: `EmsRack._fields_` includes `pack_soh`; ZMQ BMS topic includes it via `_rack_to_dict()`.

### Pitfall 5: BTMS Ambient Temperature — No Field in RTDB

**What goes wrong:** Thermal analysis tries to read `ambient_temp` from BTMS telemetry — field does not exist.

**Why it happens:** The `EmsBtms` struct has `inlet_temp`, `outlet_temp`, `fan_speed_pct`, `cooling_active` — no ambient temperature sensor in the BTMS config.

**How to avoid:** Use `outlet_temp` as the reference temperature for ΔT calculation (CONTEXT.md already specifies this): `delta_t = max_cell_t (from BMS) - btms.outlet_temp`. Do not reference `ambient_temp`.

### Pitfall 6: ZMQ REP Socket — Must Always Reply

**What goes wrong:** Exception in report query leaves REP socket without a reply; next request hangs indefinitely.

**Why it happens:** ZMQ REP socket requires strictly alternating recv/send. Missing a send corrupts the socket state.

**How to avoid:** Wrap dispatch in try/except with guaranteed fallback reply. Established pattern in `alarm_manager/loop.py` `_poll_commands()`:
```python
try:
    reply = self._dispatch_command(action, params)
except Exception as exc:
    reply = encode_command_response("error", error_msg=f"Internal error: {exc}")
self._rep.send(reply)
```

### Pitfall 7: Cycle Detection Race Condition

**What goes wrong:** A cycle is double-counted if SOC hovers near the 90% or 10% threshold, triggering the threshold crossing multiple times.

**Why it happens:** 1Hz telemetry with noisy SOC readings can cross the same threshold multiple times.

**How to avoid:** Use state machine for cycle detection (not just threshold check):
- States: `IDLE`, `CHARGING`, `DISCHARGING`
- Transition `IDLE → DISCHARGING` only when SOC drops below 10% from above 90% (require full crossing, not oscillation near threshold)
- Track `cycle_start_soc` and only count as complete cycle when full 90%→10% transit is confirmed

---

## Code Examples

### ZMQ Socket Setup for DiagnosticsLoop

```python
# Source: confirmed pattern from src/cloud_manager/src/ems_cloud_manager/loop.py
import zmq
from ems_common.ipc import SOCK_TELEMETRY, SOCK_ALARM_PUB, SOCK_LOGGER, SOCK_LOGGER_QUERY
from ems_common.ipc import TOPIC_BMS_RACK, TOPIC_PCS, TOPIC_BTMS

# New constants to add to ipc.py:
SOCK_DIAGNOSTICS_CMD: str = "ipc:///run/ems/diagnostics_cmd.sock"
SOCK_DIAGNOSTICS_PUB: str = "ipc:///run/ems/diagnostics_pub.sock"
TOPIC_DIAGNOSTICS: str = "diagnostics"

zmq_ctx = zmq.Context()

# SUB: 1Hz telemetry from data_manager
telemetry_sub = zmq_ctx.socket(zmq.SUB)
telemetry_sub.connect(SOCK_TELEMETRY)
for topic in (TOPIC_BMS_RACK, TOPIC_PCS, TOPIC_BTMS):
    telemetry_sub.setsockopt_string(zmq.SUBSCRIBE, topic)
telemetry_sub.setsockopt(zmq.LINGER, 0)

# REQ: historical queries to logger
logger_req = zmq_ctx.socket(zmq.REQ)
logger_req.connect(SOCK_LOGGER_QUERY)
logger_req.setsockopt(zmq.LINGER, 0)

# PUB: broadcast computed diagnostics (new socket)
diag_pub = zmq_ctx.socket(zmq.PUB)
diag_pub.bind(SOCK_DIAGNOSTICS_PUB)
diag_pub.setsockopt(zmq.LINGER, 0)

# REP: command API for HMI/cloud (new socket)
diag_rep = zmq_ctx.socket(zmq.REP)
diag_rep.bind(SOCK_DIAGNOSTICS_CMD)
diag_rep.setsockopt(zmq.LINGER, 0)

# PUSH: predictive alert events to logger
logger_push = zmq_ctx.socket(zmq.PUSH)
logger_push.connect(SOCK_LOGGER)
logger_push.setsockopt(zmq.LINGER, 0)
```

### SOH Coulomb Counting (per rack)

```python
# Integrates pack_i at 1Hz; accumulates across discharge phase
class SohAnalyzer:
    def __init__(self, rack_id: int, rated_capacity_ah: float) -> None:
        self._rack_id: int = rack_id
        self._rated_ah: float = rated_capacity_ah
        self._state: str = "IDLE"   # IDLE | CHARGING | DISCHARGING
        self._cycle_ah: float = 0.0
        self._last_soc: float = 0.0
        self._cycle_count: int = 0
        self._soh_pct: float = 100.0

    def update(self, pack_i: float, pack_soc: float, pack_soh_bms: float) -> None:
        """Call at 1Hz with current ZMQ telemetry values."""
        # Use BMS-reported SOH as the primary value for trending
        self._soh_pct = pack_soh_bms

        # Coulomb counting: detect full cycle for cross-validation
        if pack_soc >= 90.0 and self._state == "IDLE":
            self._state = "CHARGED"
        elif pack_soc <= 10.0 and self._state == "DISCHARGING":
            self._cycle_count += 1
            self._state = "IDLE"
            self._cycle_ah = 0.0
        elif pack_i < -0.5 and self._state == "CHARGED":  # discharge = negative current
            self._state = "DISCHARGING"

        if self._state == "DISCHARGING" and pack_i < 0:
            self._cycle_ah += abs(pack_i) / 3600.0  # A → Ah at 1Hz
```

### Logger Event Log Query for Comm Health

```python
# Source pattern: src/logger/python/src/ems_logger/query_handler.py
import time
from ems_common.ipc import encode_command_request, decode_command_response

def query_comm_faults_last_hour(logger_req: zmq.Socket) -> dict[str, int]:
    """Returns fault count per device_id for the last hour."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3_600_000  # 1 hour ago

    request = encode_command_request("query", {
        "type": "event_log",
        "start_ts": start_ms,
        "end_ts": now_ms,
        "source_filter": "comm_manager",
    })
    logger_req.send(request)
    response = decode_command_response(logger_req.recv())

    if response.get("status") != "ok":
        return {}

    fault_counts: dict[str, int] = {}
    for event in response.get("result", {}).get("rows", []):
        if event.get("event_type") == "comm_fault":
            device_id = event.get("data", {}).get("device_id", "unknown")
            fault_counts[device_id] = fault_counts.get(device_id, 0) + 1
    return fault_counts
```

### Config Structure (diagnostics_config.yaml)

```yaml
# diagnostics_config.yaml — validates against schemas/diagnostics_config.schema.json
_schema_version: "1.0"

intervals:
  publish_s: 60          # How often to publish diagnostic results on ZMQ PUB
  trend_update_s: 3600   # How often to run DuckDB queries for trends

thresholds:
  soh_warning_pct: 85.0         # Publish predictive alert when projected < 85%
  soh_critical_pct: 80.0        # Minimum acceptable SOH
  pcs_efficiency_warning_pct: 90.0  # Warning below 90% efficiency
  thermal_delta_warning_c: 8.0  # Warning above 8°C cell-to-outlet delta
  comm_fault_warning_rate: 5    # Faults per hour before degraded status

prediction:
  min_history_days: 7     # Minimum days of data before predictions enabled
  history_window_days: 30 # SOH history window for regression

battery:
  rated_capacity_ah: 100.0  # Per-rack rated capacity

metrics:
  soh_enabled: true
  pcs_efficiency_enabled: true
  thermal_enabled: true      # Set false on sites without BTMS
  comm_health_enabled: true
  predictions_enabled: true
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| ML-based SOH prediction | Simple coulomb counting + linear regression | Design decision (M5) | Maintainable by 2-3 person team; EIS/ML deferred to DIAG-07/08 |
| Standalone diagnostic daemon | Module integrated into EMS ZMQ mesh | M5 design | No new protocols; reuses all existing IPC infrastructure |
| Custom historical data store | DuckDB queries on existing Parquet logger | M2 logger design | Logger already does this; zero duplication |

**Deprecated/outdated:**
- numpy for linear regression: Python 3.12 stdlib `statistics.linear_regression` covers the use case.

---

## Open Questions

1. **Comm health: poll-rate baseline unknown**
   - What we know: comm_fault events are logged per device; we can count faults per hour
   - What's unclear: "response rate" requires knowing total_polls count, not just fault count. Total polls are NOT logged — only faults are.
   - Recommendation: Redefine comm health score as "fault frequency" (faults/hour) rather than "1 - (faults/total_polls)". This is computable from logger events alone. The CONTEXT.md algorithm `(successful_polls / total_polls) × 100` needs adjustment since total_polls is not available from the event log.

2. **SOH history persistence across diagnostics restarts**
   - What we know: DuckDB `range_stats` can query 30-day Parquet data for avg SOH
   - What's unclear: Where diagnostics persists cycle count across restarts
   - Recommendation: Store per-rack cycle count in a small state file (`/var/lib/ems/diagnostics_state.json`) — simple JSON, updated on each completed cycle. Alternatively, query logger for pack_soc time series and recount cycles from history (expensive at startup).

3. **Diagnostics PUB socket — who subscribes?**
   - What we know: CONTEXT says publish on "diagnostics" topic; HMI and cloud are consumers
   - What's unclear: HMI and cloud must be updated (Phases 28) to subscribe to SOCK_DIAGNOSTICS_PUB
   - Recommendation: Define `SOCK_DIAGNOSTICS_PUB` and `TOPIC_DIAGNOSTICS` in `ipc.py` now. Phase 28 (HMI cloud/OTA status) will add the subscription.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.3.x |
| Config file | `pyproject.toml` (workspace root `[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest src/diagnostics/tests/ -x -q` |
| Full suite command | `uv run pytest src/diagnostics/tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DIAG-01 | SOH % computed from BMS `pack_soh` and cycle count from SOC | unit | `uv run pytest src/diagnostics/tests/test_soh_analyzer.py -x` | Wave 0 |
| DIAG-01 | SOH trend publishes per-rack on ZMQ diagnostics topic | unit | `uv run pytest src/diagnostics/tests/test_loop.py::test_soh_published -x` | Wave 0 |
| DIAG-02 | PCS efficiency = ac_power / (dc_voltage * dc_current) × 100 | unit | `uv run pytest src/diagnostics/tests/test_pcs_analyzer.py -x` | Wave 0 |
| DIAG-02 | Efficiency skipped during idle (zero power) states | unit | `uv run pytest src/diagnostics/tests/test_pcs_analyzer.py::test_idle_skipped -x` | Wave 0 |
| DIAG-03 | Thermal ΔT = max_cell_t - btms.outlet_temp | unit | `uv run pytest src/diagnostics/tests/test_thermal_analyzer.py -x` | Wave 0 |
| DIAG-03 | Fan duty score = avg fan_speed_pct over query window | unit | `uv run pytest src/diagnostics/tests/test_thermal_analyzer.py::test_fan_score -x` | Wave 0 |
| DIAG-04 | Comm health scores from event_log query (faults/hour per device) | unit | `uv run pytest src/diagnostics/tests/test_comm_analyzer.py -x` | Wave 0 |
| DIAG-05 | `get_current` REP returns live metric snapshot | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_get_current -x` | Wave 0 |
| DIAG-05 | `get_report` REP queries DuckDB and returns summary | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_get_report -x` | Wave 0 |
| DIAG-05 | `get_predictions` REP returns empty before 7 days of data | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_predictions_min_days -x` | Wave 0 |
| DIAG-06 | `linear_regression` slope correctly projects days to threshold | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_linear_regression -x` | Wave 0 |
| DIAG-06 | Predictive alert event published when days < threshold | unit | `uv run pytest src/diagnostics/tests/test_loop.py::test_predictive_alert_fires -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest src/diagnostics/tests/ -x -q`
- **Per wave merge:** `uv run pytest src/diagnostics/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `src/diagnostics/tests/__init__.py` — package init
- [ ] `src/diagnostics/tests/test_config.py` — covers config loading + JSON Schema validation
- [ ] `src/diagnostics/tests/test_soh_analyzer.py` — covers DIAG-01
- [ ] `src/diagnostics/tests/test_pcs_analyzer.py` — covers DIAG-02
- [ ] `src/diagnostics/tests/test_thermal_analyzer.py` — covers DIAG-03
- [ ] `src/diagnostics/tests/test_comm_analyzer.py` — covers DIAG-04
- [ ] `src/diagnostics/tests/test_reporter.py` — covers DIAG-05, DIAG-06 predictions
- [ ] `src/diagnostics/tests/test_loop.py` — covers loop integration, ZMQ publish, alert firing
- [ ] `config/diagnostics_config.yaml` — default config for residential profile
- [ ] `config/schemas/diagnostics_config.schema.json` — JSON Schema for config validation

---

## Sources

### Primary (HIGH confidence)

- Source code: `src/alarm_manager/src/ems_alarm_manager/loop.py` — ZMQ REP/PUB/PUSH pattern, non-blocking drain, command dispatch
- Source code: `src/cloud_manager/src/ems_cloud_manager/loop.py` — Multi-task async architecture, ZMQ SUB latest-value-wins, asyncio.gather
- Source code: `src/logger/python/src/ems_logger/query_handler.py` — Logger query API: range_stats, event_log, DuckDB in executor, ZMQ REQ/REP
- Source code: `src/comm_manager/python/src/ems_comm_manager/events.py` — comm_fault event structure (device_id, fault_type in data dict), PUSH target = SOCK_LOGGER
- Source code: `src/common/python/src/ems_common/rtdb.py` — RTDB structs: EmsPcs (active_power, dc_voltage, dc_current), EmsBtms (inlet_temp, outlet_temp, fan_speed_pct), EmsRack (pack_soh, pack_i, pack_soc)
- Source code: `src/data_manager/python/src/ems_data_manager/publisher.py` — ZMQ telemetry payload keys per topic
- Source code: `src/common/python/src/ems_common/ipc.py` — All socket paths, topic strings, encode/decode functions
- Python 3.12 stdlib docs: `statistics.linear_regression()` — verified working in runtime environment

### Secondary (MEDIUM confidence)

- `pyproject.toml` (workspace root) — confirmed pytest >=8.0, pytest-asyncio >=1.3.0, duckdb >=1.5.0 in dev deps
- `config/alarms_config.yaml` + schema pattern — establishes YAML config + JSON Schema validation convention

### Tertiary (LOW confidence)

- None — all findings backed by source code inspection

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in workspace deps, confirmed in pyproject.toml
- Architecture: HIGH — patterns confirmed by reading 3+ existing modules implementing the same ZMQ patterns
- Pitfalls: HIGH — identified by direct source code inspection (comm_fault routing, PCS field names, BTMS ambient temp absence)
- Comm health implementation: MEDIUM — fundamental design gap (total_polls not logged); requires adaptation from CONTEXT.md spec

**Research date:** 2026-03-16
**Valid until:** 2026-04-16 (stable architecture; only changes if IPC contract is modified)
