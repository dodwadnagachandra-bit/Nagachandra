# Domain Pitfalls -- M2 Control & Alarms

**Domain:** BESS Control Manager + Alarm Manager
**Researched:** 2026-03-14
**Modules:** control_manager, alarm_manager

---

## Critical Pitfalls

Mistakes that cause equipment damage, safety incidents, or major rewrites.

### Pitfall 1: PCS Command Sequencing Violation -- Power to Off PCS

**What goes wrong:** The control_manager sends a non-zero power setpoint (register 0x500E) to the PCS while the PCS is still off (register 0x0291 = 0) or in a startup/fault state. The PCS ignores the setpoint silently, or worse, latches into a fault state requiring manual reset at the site.

**Why it happens:** The state machine has a bug where it transitions from STANDBY directly to CHARGING without waiting for the PCS_STARTING 10-second timer to expire. Or a race condition: the control loop sends the on command and the setpoint in the same cycle, but the PCS needs 10 seconds between on and accepting power commands.

**Consequences:** PCS enters fault state. Site requires manual intervention (physical access to PCS panel or Modbus fault reset sequence). If PCS faults repeatedly, it may enter a lockout state requiring manufacturer support. For a remote BESS site, this means expensive truck rolls.

**Prevention:**
1. **Enforce PCS_STARTING state with a hard 10-second timer.** The state machine must sit in PCS_STARTING for exactly 10 seconds after writing 0x0291=1. No power setpoints during this window. Timer uses monotonic clock, not wall clock.
2. **Read PCS working condition register (0x6057) to confirm PCS is actually on** before sending power. Bit 0+8 set = on-grid running. Do not trust the command alone -- verify the feedback.
3. **Guard every setpoint write with a state check:** `assert self._state in (State.CHARGING, State.DISCHARGING)`. If not, the setpoint must be 0.
4. **Log every PCS command** with timestamp, current state, and PCS feedback. This creates an audit trail for post-incident analysis.

**Detection:** Integration test: send power setpoint while PCS is off, verify setpoint is rejected. Test PCS_STARTING timer accuracy. Verify state machine cannot skip PCS_STARTING.

---

### Pitfall 2: PCS Off Under Load -- Contactor Arc Damage

**What goes wrong:** The control_manager sends 0x0291=0 (PCS off) while the PCS is still delivering significant power. The PCS's internal contactors open under load, causing arcing that degrades the contacts over time. After repeated occurrences, contactors weld shut or fail open.

**Why it happens:** A protection alarm triggers and the control_manager immediately sends PCS off instead of first ramping power to zero. Or the operator sends a manual stop command and the code processes it synchronously without the ramp-down sequence.

**Consequences:** Contactor damage is cumulative and invisible until catastrophic failure. Failed contactors can prevent the PCS from disconnecting during a real emergency -- a safety hazard.

**Prevention:**
1. **Always ramp power to zero before turning PCS off.** The PCS_STOPPING state must: (a) set setpoint to 0 kW, (b) wait for PCS active_power feedback to confirm <1 kW, (c) wait additional 2 seconds, (d) then send off command, (e) wait 10 seconds before allowing re-start.
2. **Exception for safety events:** E-Stop, Fire, and Flood bypass this sequence -- safety_manager asserts DO-5 (hardware PCS emergency stop) which disconnects contactors regardless. This is intentional: hardware safety override takes precedence over graceful shutdown.
3. **Never allow operator "hard stop" to bypass ramp-down** from the software command path. The ZMQ REQ/REP `stop` command always goes through PCS_STOPPING. Only the physical E-Stop button and safety_manager GPIO bypass this.

**Detection:** Integration test: trigger protection alarm during active power delivery, verify ramp-to-zero completes before PCS off command. Monitor Modbus write log for off commands while power > 1 kW.

---

### Pitfall 3: Alarm Chattering Without Hysteresis

**What goes wrong:** A sensor value oscillates around an alarm threshold (e.g., cell voltage fluctuates between 3.64V and 3.66V around a 3.65V warning threshold). Without hysteresis, the alarm activates and clears every cycle, generating hundreds of alarm events per minute. The HMI becomes unusable, the logger fills with noise, and operators develop "alarm fatigue" and start ignoring real alarms.

**Why it happens:** The developer implements threshold comparison as `value > threshold` for activate and `value <= threshold` for clear. This has zero deadband. Any noise in the signal causes oscillation.

**Consequences:** Alarm flooding. IEC 62682 performance target is <6 alarms/hour on average. A chattering alarm generates 3600+ per hour (at 1Hz evaluation). Operators miss real alarms buried in the noise. Logger disk fills faster than expected.

**Prevention:**
1. **Always apply hysteresis to both activate and clear.** For high alarms: activate when `value > threshold`, clear when `value < (threshold - hysteresis)`. For low alarms: activate when `value < threshold`, clear when `value > (threshold + hysteresis)`.
2. **Default hysteresis of 2% of threshold value** as defined in alarms_config.yaml defaults. Allow per-rule override.
3. **Combine hysteresis with delay timer.** A signal must exceed the threshold for `delay_ms` continuously before the alarm activates. If it drops below during the delay, the timer resets. This filters transient spikes.
4. **Test with realistic noise profiles.** Add +/-1% random noise to simulator signals and verify alarm rate stays below 6/hour.

**Detection:** Monitor alarm rate per rule. If any single rule exceeds 10 activations per hour, flag for hysteresis tuning. Log alarm activate/clear timestamps for analysis.

---

### Pitfall 4: SOC Calculation Error Leading to Over-Discharge

**What goes wrong:** The control_manager calculates aggregate SOC as a simple average across all racks: `total_soc = sum(rack.pack_soc) / rack_count`. But one rack has a significantly lower SOC than others (imbalanced battery). The average shows 30% while the weakest rack is at 8%. The control_manager continues discharging because 30% > 10% cutoff. The weakest rack's BMS opens contactors to protect itself, causing a current surge on remaining racks.

**Why it happens:** Simple averaging masks individual rack health. In a BESS with 8+ racks, some racks age faster than others. SOC divergence of 10-20% between best and worst rack is common after 2-3 years.

**Consequences:** BMS contactor opening under load can cause voltage transients that trip the PCS. Repeated deep discharge of the weakest rack accelerates its degradation, creating a vicious cycle. In worst case, cell damage from over-discharge.

**Prevention:**
1. **Use minimum SOC across all online racks as the discharge limit check**, not average SOC. `min_rack_soc = min(rack.pack_soc for rack in online_racks)`. If `min_rack_soc < discharge_cutoff`, stop discharging.
2. **Use maximum SOC across all online racks as the charge limit check.** If any rack is at 95%, stop charging all racks.
3. **Report both aggregate and min/max SOC** in RTDB system section and to HMI for operator awareness.
4. **Consider per-rack dispatch** in future (M3+): only discharge racks above threshold, charge only racks below threshold. This requires multi-PCS or per-rack contactor control which is out of scope for M2.

**Detection:** Integration test with asymmetric rack SOC values. Verify that control_manager respects the weakest rack, not the average.

---

### Pitfall 5: Alarm-Control Feedback Loop Causing Oscillation

**What goes wrong:** A protection alarm activates (e.g., cell_voltage_high at 3.65V). control_manager receives the alarm and stops charging. Cell voltage drops to 3.63V (below hysteresis band). alarm_manager clears the alarm. control_manager sees no alarm and resumes charging. Cell voltage rises back to 3.65V. Alarm activates again. The system oscillates between charging and stopped.

**Why it happens:** The control_manager reacts to alarm clear by immediately resuming the previous operation. Combined with the hysteresis band being smaller than the voltage change caused by charging, the system never reaches a stable state.

**Consequences:** PCS cycles on/off repeatedly (contactor wear from Pitfall 2). Battery experiences unnecessary thermal stress. Operators see alternating alarm/clear events.

**Prevention:**
1. **control_manager must implement a "cooldown" period after a protection alarm clears.** After the alarm clears, wait a configurable duration (e.g., 60 seconds) before resuming the operation that triggered it. This gives the system time to stabilize.
2. **Do not automatically resume the previous power direction after alarm clear.** Instead, transition to STANDBY and require a new dispatch decision. The dispatch algorithm may choose a lower power level on the next cycle.
3. **Apply reduced power derating for a configurable period after alarm clear.** Example: after cell_voltage_high clears, limit charge power to 50% for 5 minutes before allowing full power.
4. **The alarm hysteresis band should be wider than the voltage swing caused by the power change.** If charging at 25 kW causes a 0.03V rise in cell voltage, the hysteresis must be at least 0.03V. This is a commissioning parameter, not a design parameter.

**Detection:** Simulation test: set cell voltage near threshold, apply charge power, verify system reaches stable state within 5 cycles.

---

## Moderate Pitfalls

### Pitfall 6: asyncio Event Loop Blocking from Synchronous RTDB Read

**What goes wrong:** The control loop calls `ctypes` to read RTDB structs. `ctypes` field access is synchronous and can trigger page faults on the shared memory segment if the page has been evicted. On the 4GB ARM A53, memory pressure from other processes can cause frequent page faults, each taking 50-200us. If the control loop reads 128 racks (container topology) with page faults, the total read time could exceed 25ms, monopolizing the asyncio event loop and blocking ZMQ message processing.

**Prevention:**
1. **Use `mlockall()` or `mlock()` on the RTDB pages from the control_manager process** to prevent page eviction. The RTDB is ~1.8MB -- locking this in memory is acceptable.
2. **Read RTDB in a separate thread** using `asyncio.to_thread()` if blocking becomes an issue. The seqlock copy creates a local buffer, so the thread does not need to hold locks.
3. **Monitor control loop cycle time.** If any cycle exceeds 100ms, log a warning. If it exceeds 500ms, log an error.

---

### Pitfall 7: Config Hot-Reload Changing Alarm Thresholds Mid-Evaluation

**What goes wrong:** An operator changes alarms_config.yaml to lower the cell_voltage_high threshold from 3.65V to 3.60V. config_manager publishes a reload event. alarm_manager receives the event mid-cycle and updates the threshold. An alarm that was previously normal (cell voltage at 3.62V) is now in violation. But the delay timer was not running, so the alarm should not activate immediately. The code either: (a) activates the alarm without delay (incorrect -- violates IEC 62682 delay requirement), or (b) ignores the threshold change until next activation (incorrect -- the new threshold should be active immediately).

**Prevention:**
1. **On config reload, update alarm rule thresholds but do not change alarm lifecycle states.** The new threshold takes effect on the next evaluation cycle. If a currently-normal signal now exceeds the new threshold, the delay timer starts fresh.
2. **Never activate alarms retroactively on config change.** The delay timer ensures transient protection.
3. **If an alarm is currently ACTIVE and the threshold changes to make it no longer in violation, allow it to clear normally** (through the hysteresis band of the NEW threshold).
4. **Log the config change with old and new values** for each rule that changed.

---

### Pitfall 8: RTDB System Section Write Conflict

**What goes wrong:** Both control_manager and data_manager write to the RTDB system section. control_manager writes `active_setpoint_kw` and `pcs_command`. data_manager writes `ems_uptime_s` and `total_soc` (aggregated from rack SOCs). With seqlock, the later writer's seqlock-begin overwrites the earlier writer's data within the same section.

**Prevention:**
1. **Single writer per RTDB section is the fundamental rule.** For M2, reassign system section ownership: control_manager becomes the sole writer to `rtdb.system`. It computes `total_soc`, `total_power_kw`, and `ems_uptime_s` itself (it already reads all racks).
2. **data_manager stops writing to system section.** Its health monitoring publishes stale warnings via ZMQ, not RTDB.
3. **If multiple writers to system section are truly needed**, split it into `ems_control_t` (written by control_manager) and `ems_health_t` (written by data_manager) with separate seqlocks. This is the cleaner solution.

---

### Pitfall 9: Stale PCS Telemetry Leading to Incorrect Dispatch

**What goes wrong:** The PCS goes offline (Modbus timeout). comm_manager marks it offline in RTDB (`pcs.online = 0` equivalent via `last_update_ms` going stale). But control_manager does not check `last_update_ms` -- it reads `pcs.active_power` which still shows the last known value (e.g., 25 kW discharging). control_manager thinks the PCS is delivering power and does not start the DG backup.

**Prevention:**
1. **Always check `last_update_ms` staleness for every RTDB section read.** If `now - section.last_update_ms > 2 * poll_interval`, treat the data as invalid.
2. **In the ControlContext snapshot, include a `pcs_stale: bool` field.** All dispatch decisions must check this.
3. **If PCS data is stale, transition to IDLE** (not FAULT -- the PCS may come back). The dispatch algorithm must then fall through to next source (grid or DG).

---

### Pitfall 10: Alarm Manager Missing Startup State Reconciliation

**What goes wrong:** alarm_manager starts (or restarts after crash). It has no alarm state -- all alarms start as NORMAL. But the system actually has several conditions that should be alarming (e.g., cell temperature has been at 48C for 30 minutes). The alarm_manager misses these because the delay timer starts fresh after restart. For 5-second delays, this is barely noticeable. But if future rules have 30-second or 60-second delays, there is a significant gap.

**Prevention:**
1. **On startup, do an immediate evaluation of all rules with delay=0 for the first cycle.** If a signal is already beyond its threshold at startup, activate the alarm immediately (no delay). The delay timer exists to filter transient spikes -- a signal that was already in violation when the alarm_manager started is not transient.
2. **Log a "startup reconciliation" event** listing which alarms were immediately activated on startup.
3. **Optional (defer to M2.5): persist alarm state to a file** so alarm_manager can recover its state after restart.

---

## Minor Pitfalls

### Pitfall 11: Source Priority Config Allows Invalid Combinations

**What goes wrong:** An operator configures `night_order: [solar, grid, bess, dg]`. This includes "solar" in the night order, which makes no sense. Or configures `day_order: [dg, bess]` omitting grid and solar entirely. The system follows the nonsensical priority and dispatches incorrectly.

**Prevention:**
1. **Validate source priority in the JSON schema** (already defined: enum constraints). But also validate at runtime: warn if "solar" appears in night_order.
2. **Do not reject configs with unusual priorities** -- the operator may have valid site-specific reasons (e.g., a 24-hour solar+storage site with no grid). Log a warning, not an error.

---

### Pitfall 12: Monotonic vs Wall Clock Confusion in Timestamps

**What goes wrong:** The control_manager uses `time.monotonic()` for loop timing (correct) but publishes state change events with `time.monotonic()` timestamps instead of `time.time()` (wall clock). The logger stores these monotonic timestamps, which are meaningless across process restarts and cannot be correlated with real-world time.

**Prevention:**
1. **Use `time.monotonic()` for all internal timing** (loop intervals, delay timers, cooldown periods). Never compare monotonic timestamps across processes.
2. **Use `time.time()` (or `int(time.time() * 1000)`) for all event timestamps** published via ZMQ. These are stored by the logger and must be human-readable.
3. **Follow the existing convention** in `ems_common.ipc.encode_event()` which uses `timestamp_ms` parameter -- pass `int(time.time() * 1000)` to this.

---

### Pitfall 13: ZMQ Socket Type Mismatch for Alarm-to-Control Channel

**What goes wrong:** The developer uses REQ/REP for alarm_manager to notify control_manager of protection alarms. This creates a synchronous dependency: alarm_manager blocks waiting for control_manager's reply. If control_manager is slow or crashed, alarm_manager's evaluation loop stalls.

**Prevention:**
1. **Use PUB/SUB for alarm-to-control notification.** alarm_manager publishes on the telemetry PUB socket with topic `alarm.protection`. control_manager subscribes. No blocking, no dependency.
2. **REQ/REP is only for command-response patterns** (HMI -> control_manager, HMI -> alarm_manager). Never use REQ/REP for inter-module event notification.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| State machine implementation | PCS command without proper state validation | Transition guards with pre-condition checks (Pitfall 1) |
| PCS on/off sequencing | Off under load, contactor damage | Always ramp to zero first (Pitfall 2) |
| Alarm threshold evaluation | Chattering without hysteresis | Hysteresis + delay timer combination (Pitfall 3) |
| SOC-based dispatch | Average SOC masking weak rack | Use min/max SOC, not average (Pitfall 4) |
| Alarm-control integration | Oscillation between alarm and resume | Cooldown period + derating after alarm clear (Pitfall 5) |
| RTDB reads in asyncio | Event loop blocking from page faults | mlock RTDB pages, monitor cycle time (Pitfall 6) |
| Config hot-reload in alarm_manager | Threshold change mid-evaluation | Update thresholds, preserve alarm states (Pitfall 7) |
| RTDB system section | Multi-writer conflict | Single writer (control_manager) or split section (Pitfall 8) |
| PCS telemetry freshness | Stale data driving dispatch | Check last_update_ms every cycle (Pitfall 9) |
| Alarm manager startup | Missing pre-existing alarm conditions | Startup reconciliation with delay=0 (Pitfall 10) |
| Source priority validation | Invalid config combinations | Schema + runtime warnings (Pitfall 11) |
| Event timestamps | Monotonic vs wall clock confusion | monotonic for timing, time.time() for events (Pitfall 12) |
| Inter-module communication | REQ/REP blocking dependency | PUB/SUB for events, REQ/REP only for commands (Pitfall 13) |

---

## Sources

- Requirements v0.2 Section 6.2 (PCS V1.24 register map, on/off with 10s wait, fault registers)
- Requirements v0.2 Section 7 (Safety: E-Stop, Fire, Flood responses)
- Requirements v0.2 Section 8 (Source priority DAY/NIGHT)
- Architecture spec v3.4 ADR-001 (RTDB single-writer-per-section)
- [IEC 62682 Alarm Management](https://webstore.iec.ch/en/publication/65543) -- Alarm lifecycle, KPIs, hysteresis
- [Yokogawa IEC 62682 Blog](https://blog.yokogawa.com/blog/implementing-alarm-management-per-iec-62682-standard) -- Performance targets (6/hr acceptable, 12/hr max)
- [ISA 18.2 Alarm Shelving](https://www.isa.org/intech-home/2020/march-april/features/alarm-management-questions-that-everyone-asks)
- [Siemens Alarm Management White Paper](https://assets.new.siemens.com/siemens/assets/api/uuid:234f1026-298f-49dc-8961-0c5223c38588/white-paper-alarm-management-2021-final.pdf)
- [Li-ion Derating (MDPI)](https://www.mdpi.com/1996-1073/11/12/3295) -- SOC and temperature derating
- [BESS PCS Architecture (TLS)](https://www.tls-containers.com/tls-blog/understanding-power-conversion-systems-pcs-in-battery-energy-storage-systems-bess) -- PCS command execution
- [pyzmq asyncio](https://pyzmq.readthedocs.io/en/latest/api/zmq.asyncio.html) -- Async socket patterns
- M1 integration test results (478 tests, seqlock validation, ZMQ framing)
