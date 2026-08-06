# Phase 26: Diagnostics - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Diagnostics module: SOH trending, PCS efficiency, thermal analysis, comm health scoring, diagnostic reports, and predictive alerts. Covers DIAG-01 through DIAG-06. Pure Python, subscribes to ZMQ telemetry and events.

</domain>

<decisions>
## Implementation Decisions

### Data Collection Architecture

How should diagnostics collect data from the running system?

**Decision:** ZMQ SUB subscriber on telemetry PUB for real-time data, plus periodic DuckDB queries via logger for historical trends.

| Data Source | Method | Frequency | Purpose |
|------------|--------|-----------|---------|
| BMS rack telemetry | ZMQ SUB topic `bms.rack.*` | 1Hz (live) | SOH inputs: pack_v, pack_i, pack_soc, cycle count |
| PCS telemetry | ZMQ SUB topic `pcs` | 1Hz (live) | Efficiency: ac_power, dc_power |
| BTMS telemetry | ZMQ SUB topic `btms` | 1Hz (live) | Thermal: inlet_temp, outlet_temp, fan_speed |
| Comm fault events | ZMQ SUB on SOCK_ALARM_PUB topic `comm_fault` | Event-driven | Comm health: timeout count, CRC errors |
| Historical data | ZMQ REQ on SOCK_LOGGER_QUERY (`range_stats`) | Hourly | Trend calculation over 24h/7d/30d windows |

Key rules:
- Live data collected at 1Hz for real-time display (e.g., current efficiency).
- Historical trends computed hourly from logger's DuckDB — not from accumulated 1Hz data in memory.
- Diagnostics does NOT write to RTDB — it's a read-only consumer. Publishes results on its own ZMQ PUB.
- No new ZMQ socket type — reuses existing SUB (telemetry) and REQ (logger query) patterns.

**Rationale:** Separating real-time (ZMQ SUB) from historical (DuckDB query) avoids accumulating hours of 1Hz data in memory. The logger already has indexed Parquet data — DuckDB range_stats queries are sub-second. This follows the "don't reinvent the logger" principle.

### Diagnostic Metrics and Algorithms

What specific metrics should each diagnostic category compute?

**Decision:** Simple, proven algorithms — no machine learning, no complex models. All computatable from existing telemetry.

| Metric | Category | Algorithm | Inputs | Output |
|--------|----------|-----------|--------|--------|
| SOH estimate | DIAG-01 | Coulomb counting SOH = (discharge_capacity / rated_capacity) × 100 | pack_i integrated over full cycles | % per rack |
| Cycle count | DIAG-01 | Count full charge→discharge cycles (SOC crosses 90%→10%) | pack_soc | Integer per rack |
| PCS efficiency | DIAG-02 | η = |ac_power| / |dc_power| × 100 (discharge), inverse for charge | ac_power, dc_power | % instantaneous + daily average |
| Cooling effectiveness | DIAG-03 | ΔT = cell_temp_max - ambient_temp; trend over time | cell_temp_max, btms.outlet_temp | °C delta trend |
| Fan duty score | DIAG-03 | Average fan_speed_pct over 24h vs cooling demand | fan_speed_pct, cell_temp_max | 0-100 score |
| Device response rate | DIAG-04 | (successful_polls / total_polls) × 100 per device per hour | comm_fault events | % per device |
| Predictive alert | DIAG-06 | Linear regression on SOH over last 30 days → project to threshold | SOH history | Days until SOH < 80% |

Key rules:
- SOH estimation uses simple coulomb counting — accurate enough for field monitoring, no need for EIS or Kalman filter.
- PCS efficiency ignores standby/idle states (zero power) — only calculated during active charge/discharge.
- Predictive alerts use linear regression (numpy-free — stdlib `statistics.linear_regression` in Python 3.12+) over 30-day SOH trend.
- All metrics are per-rack for BMS, single-value for PCS/BTMS/comm.
- Diagnostic results published on ZMQ PUB (topic: "diagnostics") at configurable interval (default 60s).

**Rationale:** Simple algorithms are more maintainable and interpretable than ML models for a 2-3 person team. Coulomb counting SOH is the standard BESS monitoring approach (BMS vendors use it internally). Linear regression for prediction is sufficient to detect degradation trends — sophisticated models (Kalman, neural) are deferred to DIAG-07/08 future requirements.

### Report Generation and Query API

How should diagnostic reports be structured and queried?

**Decision:** ZMQ REQ/REP on a dedicated `SOCK_DIAGNOSTICS_CMD` socket. Three query types.

| Query | Request | Response | Use Case |
|-------|---------|----------|----------|
| `get_current` | `{action: "get_current"}` | `{soh: [{rack, pct, cycles}], pcs_efficiency: pct, thermal: {delta, fan_score}, comm: [{device, rate}]}` | HMI diagnostics screen — live values |
| `get_report` | `{action: "get_report", period: "daily"\|"weekly"}` | `{period, generated_at, soh_trend: [], efficiency_trend: [], comm_summary: [], alerts: []}` | HMI/cloud — periodic summary |
| `get_predictions` | `{action: "get_predictions"}` | `{predictions: [{metric, current_value, trend_rate, days_to_threshold, severity}]}` | Predictive maintenance display |

Key rules:
- Reports are computed on-demand (not pre-generated) — diagnostics queries DuckDB via logger on each request.
- `get_current` returns latest real-time values from the 1Hz ZMQ stream — instant response.
- `get_report` may take 1-2 seconds (DuckDB queries) — acceptable for on-demand UI requests.
- Predictions only available after 7+ days of data — returns empty before then.
- New IPC constant: `SOCK_DIAGNOSTICS_CMD = "ipc:///run/ems/diagnostics_cmd.sock"` added to ipc.py.

**Rationale:** REQ/REP query pattern matches alarm_cmd (Phase 15) and logger_query (Phase 12). On-demand computation avoids storing redundant computed data — logger already has the raw telemetry. The three query types map to the three HMI diagnostics screen sections: current health, historical report, predictive alerts.

### Claude's Discretion

- Diagnostics class architecture (single DiagnosticsLoop vs separate analyzers)
- Coulomb counting implementation (integration method, reset logic)
- Report caching (cache last report for N seconds to avoid redundant DuckDB queries)
- Config schema design (diagnostics_config.yaml — thresholds, intervals, enable/disable per metric)
- Test strategy (mock ZMQ telemetry, mock DuckDB responses)
- ZMQ PUB topic for diagnostic results ("diagnostics")

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/diagnostics/` — Stub package (v0.1.0)
- `deploy/systemd/diagnostics.service` — After=data_manager, logger
- `src/common/python/src/ems_common/ipc.py` — All socket paths and topic constants
- Logger query API (Phase 12) — `range_stats`, `time_series` queries via SOCK_LOGGER_QUERY
- Alarm event publishing pattern (Phase 15) — for predictive alert events

### Established Patterns
- Async Python with ZMQ SUB for telemetry (logger, alarm, control, cloud patterns)
- ZMQ REQ for queries (logger_query, alarm_cmd patterns)
- ZMQ REQ/REP for command API (control_cmd, alarm_cmd patterns)
- Config loading via yaml.safe_load + JSON Schema validation

### Integration Points
- ZMQ SUB on SOCK_TELEMETRY for BMS/PCS/BTMS/comm telemetry
- ZMQ SUB on SOCK_ALARM_PUB for comm_fault events
- ZMQ REQ on SOCK_LOGGER_QUERY for historical data (DuckDB)
- ZMQ PUB for diagnostic results (new topic: "diagnostics")
- ZMQ REP on SOCK_DIAGNOSTICS_CMD (new) for report queries
- ZMQ PUSH on SOCK_LOGGER for predictive alert events

</code_context>

<specifics>
## Specific Ideas

- Python 3.12 has `statistics.linear_regression()` — no numpy needed for trend prediction
- Diagnostics is the last "feature" module — all infrastructure (IPC, RTDB, logger) is proven
- SOH tracking needs cycle detection logic — SOC must cross both high and low thresholds to count as a full cycle
- diagnostics_config.yaml should include enable/disable per metric (some sites may not have BTMS)

</specifics>

<deferred>
## Deferred Ideas

- **DIAG-07**: ML anomaly detection — future
- **DIAG-08**: RUL prediction — future
- Internal resistance estimation from voltage/current response — complex, deferred
- EIS (Electrochemical Impedance Spectroscopy) integration — requires BMS vendor support

</deferred>

---

*Phase: 26-diagnostics*
*Context gathered: 2026-03-16*
