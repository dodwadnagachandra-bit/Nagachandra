# Feature Landscape -- M2 Control & Alarms

**Domain:** BESS Control Manager + Alarm Manager
**Researched:** 2026-03-14
**Scope:** control_manager (1Hz state machine, source priority, PCS dispatch), alarm_manager (IEC 62682 three-tier)

---

## Table Stakes

Features that operators and integrators expect. Missing any of these means the EMS cannot safely dispatch power.

### control_manager (L4 Application)

| Feature | Why Expected | Complexity | Notes |
|---------|-------------|------------|-------|
| State machine (IDLE/STANDBY/CHARGING/DISCHARGING/FAULT) | Core operational states that every BESS EMS must implement. Without explicit states, the system cannot enforce transitions or safety interlocks | High | 5 primary states + sub-states. Transition guards validate pre-conditions (PCS online, no protection alarms, SOC within limits) |
| 1Hz control loop with precise timing | Control decisions at 1-second interval. Must not drift or skip cycles. At 130us/cycle there is 7700x headroom but the loop scheduler matters | Med | asyncio-based: record monotonic time at start, sleep for (1.0 - elapsed) at end. Log if cycle exceeds 500ms |
| RTDB telemetry reads (BMS, PCS, GPIO, Meter) | Control decisions require current system state. Must read seqlock-protected RTDB sections without blocking | Med | Use existing ems_common.rtdb.attach_rtdb(). Copy entire sections per cycle to avoid torn reads. Check last_update_ms for staleness |
| PCS on/off command sequencing with 10s wait | PCS V1.24 spec: write 0x0291=1 (ON), wait 10s before sending power. Write 0x0291=0 (OFF) only after ramping power to zero. Violating this damages the PCS | High | State machine sub-states: PCS_STARTING (10s timer), PCS_READY, PCS_STOPPING (ramp-to-zero + 10s wait). Timer-based, not polling-based |
| Power setpoint writing to RTDB for comm_manager | control_manager calculates desired power; comm_manager sends via Modbus. Single-writer-per-section rule means control writes to rtdb.system, comm reads it | Med | New fields in EmsSystem: active_setpoint_kw, pcs_command, pcs_command_ts |
| SOC-based charge/discharge cutoff | Stop charging at charge_cutoff_pct (default 95%), stop discharging at discharge_cutoff_pct (default 10%). Configurable in control_config.yaml | Low | Read aggregate SOC from RTDB. Compare against configured limits. Set setpoint to 0 when limit reached |
| Power ramp rate limiting | PCS cannot handle instant step changes in power setpoint. Ramp rate limits protect PCS hardware and comply with grid codes | Med | Configurable ramp rate (kW/s). Each cycle, clamp delta-power to max ramp. Typical: 10-25% of rated power per second |
| BMS current limit enforcement | BMS publishes charge/discharge current limits via CAN. EMS must not exceed these -- BMS will open contactors if violated | High | Read BMS charge_current_limit and discharge_current_limit from RTDB. Convert to power limit (P = V * I). Use as hard ceiling on setpoint |
| Source priority dispatch (DAY/NIGHT mode) | DAY: Solar > Grid > BESS > DG. NIGHT: Grid > BESS > DG. Must follow configured priority order | High | Priority waterfall: check each source in order, use first available with sufficient capacity. Configurable in control_config.yaml |
| Grid-tie awareness (ACDB feedback) | System must know if grid is connected (DI-0 ACDB feedback). Off-grid operation requires different dispatch logic | Med | Read rtdb.gpio.di[0]. If grid lost, switch to off-grid dispatch. If grid returns, switch back after validation delay |
| Safety interlock checking | Before any PCS command, verify no E-Stop, no fire, no flood active. Read safety state from RTDB GPIO section | Low | Pre-check before every setpoint write. If any safety output asserted (DO-0, DO-1, DO-5), refuse to send power commands |
| Fault handling and retry | PCS faults (0x1700-0x1707) require: stop power, read fault code, attempt reset (write 0x5064=1), retry up to fault_retry_count | Med | Configurable retry count (default 3). Exponential backoff between retries. If retries exhausted, transition to FAULT state |
| ZMQ REQ/REP command interface | HMI and cloud need to: change mode, override source priority, request manual charge/discharge, acknowledge faults | Med | Bind SOCK_CONTROL_CMD. Actions: set_mode, set_priority, set_power, fault_reset, safety_reset |
| ZMQ event publishing (state changes) | Logger, HMI, and cloud need to know when control state changes | Low | PUSH to SOCK_LOGGER on every state transition, setpoint change, and dispatch decision |

### alarm_manager (L4 Application)

| Feature | Why Expected | Complexity | Notes |
|---------|-------------|------------|-------|
| Per-signal threshold evaluation at 1Hz | Every alarm rule evaluates its RTDB signal against configured high/low thresholds each cycle | Med | 9 alarm rules in default config. Each reads one RTDB signal, compares against threshold. Must handle both high and low threshold directions |
| Three-tier severity (Warning/Action/Protection) | IEC 62682 mandates severity classification. Warning = operator attention. Action = immediate response. Protection = automatic protective action | Low | Enum: WARNING, ACTION, PROTECTION. Severity drives indicator lamps (DO-2 warning, DO-4 fault) and control_manager response |
| Hysteresis to prevent chattering | Without hysteresis, a signal oscillating around a threshold generates alarm/clear/alarm/clear endlessly | Med | Default 2% of threshold. Per-rule override. High alarm clears at (threshold - hysteresis). Low alarm clears at (threshold + hysteresis) |
| Delay timer to filter transient spikes | A 1-second voltage spike should not trigger a protection alarm. Configurable delay (default 5s) filters transients | Med | Per-rule delay_ms (default from alarms_config.yaml). Timer starts when threshold exceeded. Alarm activates only if still exceeded after delay. Timer resets if signal returns to normal |
| Alarm lifecycle (ACTIVE/ACK/CLEARED/RTN) | IEC 62682 defines four states for every alarm instance. Missing lifecycle = operators lose track of which alarms need attention | High | State machine per alarm: NORMAL -> ACTIVE_UNACKED -> ACTIVE_ACKED -> (signal returns) -> RTN. Also: ACTIVE_UNACKED -> (signal returns) -> CLEARED_UNACKED -> ACK -> NORMAL |
| Alarm acknowledgement via ZMQ REQ/REP | Operators must be able to acknowledge alarms from HMI. Unacknowledged alarms persist until ACKed even after condition clears | Med | Bind SOCK_ALARM_CMD. Actions: acknowledge(alarm_id), acknowledge_all, shelve(alarm_id, duration), unshelve(alarm_id) |
| Alarm event publishing to logger | Every alarm state transition must be logged for audit trail. IEC 62682 requires maintaining alarm history | Low | PUSH to SOCK_LOGGER: alarm_activate, alarm_acknowledge, alarm_clear, alarm_rtn. Include alarm_id, signal, value, threshold, severity |
| Active alarm list for HMI | HMI needs current active alarms with severity, timestamp, and acknowledgement status | Low | Maintain in-memory dict of active alarms. Serve via ZMQ REQ/REP (get_active_alarms action) or publish on telemetry PUB |
| Config hot-reload for alarm thresholds | alarms_config.yaml is hot-reloadable. Threshold changes must take effect without restart | Med | Subscribe to config_reload events from config_manager. Re-validate new config, update alarm rule instances, preserve current alarm states |
| Protection alarm -> control_manager notification | When a protection-severity alarm activates, control_manager must reduce or stop power. This is the alarm-to-control feedback loop | Med | Publish protection alarm events on telemetry PUB. control_manager subscribes and adjusts behavior (stop charging on cell_voltage_high_protection, stop discharging on soc_low_protection) |

---

## Differentiators

Features that set this EMS apart. Not expected in every BESS controller but add significant operational value.

### control_manager

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| SOC-based power derating curves | Smoothly reduce charge power as SOC approaches cutoff instead of hard stop. Reduces thermal stress and extends battery cycle life | Med | Piecewise-linear curve: 100% power below 80% SOC, linear ramp to 0% at 95% SOC. Similar for discharge. Configurable breakpoints in control_config.yaml |
| Temperature-based power derating | Reduce charge/discharge power at extreme temperatures. Charging below 0C causes lithium plating (catastrophic). Discharging above 55C accelerates degradation | Med | Read cell_temp_min and cell_temp_max from RTDB. Apply derating: no charge below 0C, 50% charge below 5C, no discharge above 55C. Curves configurable per chemistry (LFP vs NMC) |
| PCS fault word decode and categorization | PCS registers 0x1700-0x1707 contain 8 fault word registers with bitfield faults. Decode these into human-readable fault descriptions | Low | Lookup table mapping each bit in each fault register to a fault name and severity. Already defined in requirements (Section 6.2) |
| Mode persistence across restarts | Source priority and operating mode survive reboot by persisting to control_config.yaml | Low | Write updated source_priority to YAML on operator change (Decision #24). Validate before writing |
| DG start/stop sequencing | When BESS and grid both unavailable, start diesel generator via DO-7 (or Modbus). Requires warm-up delay before loading | Med | Only if DG is configured. Start delay (configurable, typically 30-60s). Load transfer delay. Stop delay after grid returns |
| Energy accounting (charge/discharge kWh) | Track cumulative energy charged and discharged for billing, warranty, and performance analysis | Low | Integrate power * time each cycle. Store in RTDB system section. Reset via command or daily |

### alarm_manager

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Alarm shelving (IEC 62682) | Temporarily suppress a known nuisance alarm for a configurable duration. Required by IEC 62682 and ISA 18.2 for maintenance scenarios | Med | Shelved alarm stops generating events but state is tracked. Auto-unshelve after timeout. Max shelve duration configurable (typically 8-24 hours) |
| Alarm suppression by state | Suppress alarms that are expected in certain system states. Example: low SOC warning during intentional deep discharge test | Med | Suppression rules tied to control_manager state. When in MAINTENANCE mode, suppress warning-level alarms. Protection alarms never suppressed |
| Alarm rate monitoring (IEC 62682 KPI) | Track alarms per hour. IEC 62682 says >6/hour is concerning, >12/hour is excessive. Indicates system design issues | Low | Rolling 1-hour alarm count. Publish as metric. Flag when exceeding thresholds |
| Alarm flood detection | Detect when alarm rate exceeds manageable level (>10 alarms in 10 seconds) and aggregate into a single "alarm flood" event | Med | Prevents HMI from becoming unusable during cascade failures. Show "ALARM FLOOD: N active alarms" instead of N individual popups |
| Multi-level thresholds per signal | Same signal can have warning, action, and protection thresholds. Example: cell_temp 40C=warning, 45C=action, 55C=protection | Low | Multiple alarm rules referencing same RTDB signal with different thresholds and severities. Already supported by alarms_config.yaml structure |
| Alarm response procedures | Display recommended operator action for each alarm. "Cell voltage high: reduce charge power or check BMS cooling" | Low | Text field in alarms_config.yaml per rule. Served to HMI via alarm query response |

---

## Anti-Features

Features to explicitly NOT build in M2.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| PCS vendor abstraction layer | Decision #21: defer until second PCS vendor appears. Premature abstraction adds complexity for zero benefit | Build concrete V1.24 command interface. Keep register addresses configurable in pcs_config.yaml so a second vendor can be added later |
| Multi-PCS master/slave coordination | Decision #7.3 pending site engineering. No second PCS to test against | Single PCS dispatch only. Setpoint goes to one PCS |
| Predictive/ML-based dispatch optimization | Requires historical data patterns, training infrastructure, and data science expertise. Overkill for v1 | Rule-based priority dispatch is deterministic, debuggable, and sufficient for v1 sites |
| Grid code frequency/voltage ride-through | Pending Decision #7.4 (CEA India vs IEEE 1547). Implementing wrong grid code wastes effort | Read grid frequency/voltage from meter. Log but do not act. Add grid code compliance in M3 or M5 |
| Alarm rationalization automation | IEC 62682 stages H-J (monitoring, assessment, audit) are operational processes, not software features | Track alarm KPIs. Human-driven rationalization during commissioning |
| Alarm sound/annunciation control | HMI handles audio. alarm_manager provides data only | Publish alarm events. HMI decides how to present (sound, color, position) |
| Complex alarm suppression trees | State-based suppression with parent/child alarm relationships is over-engineering for 9-15 alarm rules | Simple enable/disable per rule and manual shelving covers all practical scenarios |

---

## Feature Dependencies

```
                   alarms_config.yaml          control_config.yaml
                         |                            |
                   +-----v------+              +------v-------+
                   |  alarm_    |              |  control_    |
                   |  manager   |              |  manager     |
                   |            |              |              |
                   |  Threshold |  alarm PUB   |  State       |
                   |  Evaluate  |------------->|  Machine     |
                   |  Lifecycle |              |  Dispatch    |
                   |  ACK/Shelve|              |  PCS Cmds    |
                   +-----+------+              +------+-------+
                         |                            |
                    ZMQ PUSH                   RTDB system write
                    (alarm events)             (setpoint, command)
                         |                            |
                    +----v----+                +------v-------+
                    |  logger |                | comm_manager |
                    | (JSONL) |                | (Modbus PCS) |
                    +---------+                +--------------+

Dependencies:
  alarm_manager -> RTDB (read BMS/PCS/Meter signals)
  alarm_manager -> config_manager (alarms_config hot-reload)
  alarm_manager -> logger (event persistence)

  control_manager -> RTDB (read BMS/PCS/GPIO/Meter)
  control_manager -> alarm_manager events (protection alarms trigger actions)
  control_manager -> config_manager (control_config hot-reload)
  control_manager -> logger (event persistence)
  control_manager -> comm_manager (PCS commands via RTDB system section)

Boot order (systemd):
  1. config_manager (already running from M1)
  2. data_manager (already running from M1)
  3. safety_manager (already running from M1)
  4. comm_manager (already running from M1)
  5. alarm_manager (After=data_manager, reads RTDB)
  6. control_manager (After=data_manager alarm_manager comm_manager)
  7. logger (already running from M1)

Note: control_manager starts AFTER alarm_manager so it can subscribe
      to alarm events before processing its first cycle.
      alarm_manager has no dependency on control_manager.
```

---

## MVP Recommendation

### Must-have for M2:

1. **control_manager state machine** -- IDLE/STANDBY/CHARGING/DISCHARGING/FAULT with transition guards. This is the skeleton that everything hangs on.
2. **alarm_manager threshold evaluation** -- Per-signal evaluation with hysteresis and delay. Without this, the system has no protection layer above safety_manager GPIO.
3. **PCS on/off sequencing** -- 10s wait timer, power ramp, interlock validation. Critical for equipment safety.
4. **SOC-based charge/discharge cutoff** -- Hard stops at configured SOC limits. Most basic battery protection.
5. **Source priority dispatch** -- DAY/NIGHT priority waterfall. Core EMS value proposition.
6. **Alarm lifecycle** -- ACTIVE/ACK/CLEARED/RTN state machine per alarm. IEC 62682 compliance.
7. **Safety interlock checking** -- Read GPIO safety outputs before every PCS command.
8. **ZMQ command interfaces** -- REQ/REP for both modules (mode changes, alarm ACK).

### Defer to later in M2 or M2.5:

- **SOC/temperature derating curves** -- Build hard cutoffs first, add smooth derating later
- **Alarm shelving** -- Useful but not blocking. Hard enable/disable covers basic scenarios
- **DG start/stop sequencing** -- Only needed for sites with DG (optional device)
- **Alarm flood detection** -- Edge case, build after alarm engine is stable
- **Energy accounting** -- Nice to have, not blocking any module

### Defer to M3+:

- PCS vendor abstraction (when second vendor appears)
- Grid code ride-through (pending Decision #7.4)
- Alarm rationalization KPI dashboards (HMI feature, M3)

---

## Sources

- Requirements v0.2 Section 2 (control_manager, alarm_manager module descriptions)
- Requirements v0.2 Section 6 (PCS protocol: registers, sequencing, fault words)
- Requirements v0.2 Section 8 (source priority: DAY/NIGHT truth tables)
- Architecture spec v3.4 (5-layer stack, L4 Application modules)
- Codebase: `config/control_config.yaml` (SOC limits, power limits, source priority, state machine)
- Codebase: `config/alarms_config.yaml` (9 alarm rules with thresholds)
- [IEC 62682 Wikipedia](https://en.wikipedia.org/wiki/IEC_62682) -- Alarm lifecycle states
- [Yokogawa IEC 62682 Implementation](https://blog.yokogawa.com/blog/implementing-alarm-management-per-iec-62682-standard) -- Lifecycle stages, KPIs
- [Li-ion Derating Guidelines](https://www.mdpi.com/1996-1073/11/12/3295) -- SOC and temperature derating for LFP
- [EVReporter Derating](https://evreporter.com/derating-of-lithium-ion-cells-relationship-between-soc-c-rate-and-temperature/) -- C-rate vs temperature derating tables
