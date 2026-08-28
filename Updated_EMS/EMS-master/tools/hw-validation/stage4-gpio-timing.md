# Stage 4: Safety GPIO Timing — Oscilloscope Measurement Procedure

**Purpose:** Validate that the `safety_manager` E-Stop response time on the physical
ECU-1170-552A hardware meets the <100ms p99 requirement.

Software timestamps are insufficient for this measurement: they miss kernel scheduling
jitter, interrupt latency, GPIO driver overhead, and debounce filter delays. An
oscilloscope measures the actual end-to-end propagation delay from the DI pin voltage
rising to the DO pin voltage rising, independent of software timestamps.

---

## Equipment Required

| Item | Specification | Notes |
|------|--------------|-------|
| Digital oscilloscope | 2-channel minimum, ≥1 MSa/s sample rate | Rigol DS1054Z or equivalent |
| Jumper wires (×3) | 22 AWG, 15 cm | CH1 probe, CH2 probe, common ground |
| Signal source for DI-6 | 3.3V logic source or a jumper wire from ECU 3.3V pin | For manually toggling E-Stop input |
| Multimeter (optional) | Voltage measurement | To verify 3.3V level before connecting scope |

---

## Pin Connections

Connect the oscilloscope before starting the EMS services.

| Scope Connection | GPIO Signal | Config Name | Physical Location |
|-----------------|-------------|-------------|-------------------|
| CH1 (trigger) | DI-6 | `ESTOP_NO` | ECU GPIO header — see ECU-1170-552A hardware manual for exact pin number |
| CH2 (measure) | DO-5 | `PCS_STOP` | ECU GPIO header — see ECU-1170-552A hardware manual for exact pin number |
| GND (both probes) | GPIO ground | — | ECU GPIO header GND pin |

**Source of truth for signal assignments:**

```yaml
# From config/gpio_config.yaml:
digital_inputs:
  DI-6:
    name: ESTOP_NO
    active_low: false    # High = E-Stop activated
    debounce_ms: 5

digital_outputs:
  DO-5:
    name: PCS_STOP
    active_low: false    # High = stop command asserted to PCS
    initial_state: low
```

**Important:** The GPIO chip-to-header pin mapping depends on the Advantech ECU-1170-552A
BSP Device Tree Blob. Verify pin numbers against the Advantech hardware manual before
connecting. Incorrect pin connections will damage the ECU.

---

## Oscilloscope Setup

Configure the oscilloscope before applying the E-Stop signal.

### Timebase and Trigger

| Setting | Value | Rationale |
|---------|-------|-----------|
| Timebase | 50 ms/div | Shows full 0–500 ms range |
| Memory depth | Maximum available | Capture complete transition |
| Trigger source | CH1 | DI-6 (ESTOP_NO input) |
| Trigger type | Rising edge | E-Stop NO contact closes → voltage rises |
| Trigger level | 1.65 V | 50% of 3.3V logic high |
| Trigger mode | Normal | Captures only on valid E-Stop events |
| Pre-trigger | 10% | Captures baseline before event |

### Channel Setup

| Channel | Signal | Scale | Coupling |
|---------|--------|-------|----------|
| CH1 | DI-6 (ESTOP_NO) | 1 V/div | DC |
| CH2 | DO-5 (PCS_STOP) | 1 V/div | DC |

### Measurement Setup

Enable automatic measurement statistics on the oscilloscope:

1. Add measurement: **CH1-to-CH2 rising edge delay** (also called "phase delay" or
   "time from CH1 rising to CH2 rising" depending on oscilloscope model)
2. Enable statistics: mean, min, max, standard deviation (σ)
3. For p99 estimation: collect ≥100 samples; use the maximum observed value as a
   conservative p99 when statistics mode is not available

---

## Test Procedure

### Pre-Test Checklist

Before applying any E-Stop signal:

- [ ] Oscilloscope connected: CH1 → DI-6, CH2 → DO-5, GND → GPIO GND
- [ ] All 14 EMS services active on the ECU:
  ```bash
  ssh root@ECU "systemctl is-active ems.target"
  ```
  Expected output: `active`

- [ ] Verify `safety_manager` is running with SCHED_FIFO real-time scheduling:
  ```bash
  ssh root@ECU "journalctl -u safety_manager --no-pager | grep SCHED"
  ```
  Expected: line containing `SCHED_FIFO` or `policy=1`

  Also verify directly:
  ```bash
  ssh root@ECU "chrt -p \$(pidof safety_manager)"
  ```
  Expected output: `scheduling policy: SCHED_FIFO`, `scheduling priority: 90` (or similar RT priority)

- [ ] Verify PREEMPT_RT kernel:
  ```bash
  ssh root@ECU "uname -r"
  ```
  Expected: kernel version string ending in `-rt` (e.g., `5.15.90-rt55-ems`)

- [ ] Oscilloscope triggering correctly (arm trigger, no spurious fires before test)
- [ ] Scope memory reset (no old captures present)

---

### Measurement Sequence

Apply E-Stop signals manually, one at a time. Allow 3–5 seconds between each
application to let the EMS transition back to IDLE state before the next trigger.

**For each measurement (repeat 100 times):**

1. Arm the oscilloscope trigger (if not in Auto/Normal continuous mode)
2. Apply 3.3V to DI-6 (ESTOP_NO): connect jumper wire from ECU 3.3V supply pin to
   the DI-6 GPIO header pin
3. Observe CH2 (PCS_STOP) assertion on oscilloscope — the DO-5 signal should rise
   within the expected <100ms window
4. Record the CH1-to-CH2 delay displayed by the oscilloscope measurement
5. Remove the 3.3V signal from DI-6 (disconnect jumper wire)
6. Wait 3–5 seconds for the EMS to return to IDLE state:
   ```bash
   ssh root@ECU "journalctl -u safety_manager --no-pager -n 5"
   ```
7. Confirm `safety_manager` logged "E-Stop cleared, returning to IDLE" (or similar)

After 100 measurements, record the statistics displayed by the oscilloscope:

| Statistic | Value (ms) |
|-----------|-----------|
| p50 (median) | |
| p95 | |
| p99 (or max of 100 samples) | |
| Standard deviation (σ) | |
| Min observed | |
| Max observed | |

---

## Pass Criteria

| Metric | Target | Interpretation |
|--------|--------|----------------|
| p99 < 100 ms | **REQUIRED** | Hard safety requirement |
| p95 < 80 ms | Recommended | 20 ms margin for p99 |
| p50 < 50 ms | Informational | Typical case latency |
| 0 misses (DO-5 never asserted) | **REQUIRED** | System responded to all 100 events |

**Result: PASS** if p99 < 100ms AND all 100 events triggered DO-5.

**Result: FAIL** if p99 ≥ 100ms OR any event did not trigger DO-5.

---

## Dual-Channel Cross-Check

The E-Stop is a safety-critical dual-channel input. `safety_manager` must only assert
DO-5 (PCS_STOP) when **both** channels agree:

- DI-6 (ESTOP_NO) HIGH — normally-open contact closed
- DI-7 (ESTOP_NC) LOW — normally-closed contact opened

The cross-monitoring logic must also **reject** a single-channel failure:
- DI-6 HIGH but DI-7 HIGH (NC channel stuck high) → wiring fault, should NOT assert DO-5
- DI-6 LOW but DI-7 LOW (NO channel stuck low) → wiring fault, should NOT assert DO-5

### Dual-Channel Test Procedure

**Test A — Valid E-Stop (both channels agree):**
1. Simultaneously apply DI-6 HIGH and DI-7 LOW (pull DI-7 GPIO to GND)
2. Verify DO-5 asserts (PCS_STOP high)
3. Verify `safety_manager` logs "Valid E-Stop — both channels agree"

**Test B — Single-channel fault (DI-6 only):**
1. Apply DI-6 HIGH only; leave DI-7 at default HIGH (floating/pull-up)
2. Verify DO-5 does NOT assert within 500ms
3. Verify `safety_manager` logs "E-Stop channel discrepancy — wiring fault"

**Test C — Single-channel fault (DI-7 only):**
1. Pull DI-7 LOW only; leave DI-6 at default LOW (no signal)
2. Verify DO-5 does NOT assert within 500ms
3. Verify `safety_manager` logs "E-Stop channel discrepancy — wiring fault"

Record results:

| Test | Expected DO-5 | Actual DO-5 | Result |
|------|--------------|-------------|--------|
| A: Both channels agree | Asserts | | |
| B: DI-6 only | Does NOT assert | | |
| C: DI-7 only | Does NOT assert | | |

**Dual-channel cross-check: PASS** if all three tests match expected behavior.

---

## Failure Investigation

If p99 > 100ms, investigate in this order:

### 1. Verify PREEMPT_RT Kernel

```bash
ssh root@ECU "uname -r"
# Expected: ends with -rt
# If missing: the Yocto image was not built with RT_PREEMPT patches
```

### 2. Verify Real-Time Scheduling

```bash
ssh root@ECU "chrt -p \$(pidof safety_manager)"
# Expected: scheduling policy: SCHED_FIFO, priority >= 80
# If wrong: check safety_manager.service CPUSchedulingPolicy=fifo
```

### 3. Check CPU Load During Test

```bash
ssh root@ECU "top -b -n 3 -d 1 | head -20"
# Look for: high %st (steal time) or another process consuming >50% CPU
```

### 4. Check IRQ Storm

```bash
ssh root@ECU "watch -n 1 cat /proc/interrupts"
# Look for: GPIO IRQ line counter incrementing rapidly
# If IRQ storm: check DI pin for electrical noise — add hardware debounce filter
```

### 5. Check Kernel GPIO Driver Latency

```bash
ssh root@ECU "cat /proc/sys/kernel/sched_rt_runtime_us"
# Expected: 980000 (or -1 to disable RT throttling)
# If 950000 and system is busy: RT throttling may delay safety_manager
# Fix: set to -1 (disable RT throttling) for safety-critical systems
```

### 6. Escalation

If the above checks do not resolve p99 > 100ms, open a tech debt item:
- Add dedicated GPIO IRQ affinity for safety_manager's CPU core
- Evaluate isolcpus boot parameter to isolate one A53 core for safety_manager
- Evaluate FIFO policy priority relative to other RT processes

---

## Data Recording Template

```
Hardware Validation — Stage 4: Safety GPIO Timing
================================================
Date:           _______________
Tester:         _______________
ECU Serial:     _______________
Kernel version: _______________
SCHED_FIFO:     YES / NO
Scope model:    _______________

Measurement Results (100 samples):
  p50:          _______ ms
  p95:          _______ ms
  p99:          _______ ms
  Max observed: _______ ms
  Min observed: _______ ms
  Std dev (σ):  _______ ms

Dual-Channel Cross-Check:
  Test A (both agree → DO-5 asserts):    PASS / FAIL
  Test B (DI-6 only → DO-5 no-assert):  PASS / FAIL
  Test C (DI-7 only → DO-5 no-assert):  PASS / FAIL

Stage 4 Result: PASS / FAIL

Notes:
  _______________
```
