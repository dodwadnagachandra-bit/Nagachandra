# Research Summary: M2 Control & Alarms

**Domain:** BESS Control Manager (1Hz state machine, power dispatch) + Alarm Manager (IEC 62682)
**Researched:** 2026-03-14
**Overall confidence:** HIGH

## Executive Summary

M2 builds the two L4 Application modules -- control_manager and alarm_manager -- on top of M1's completed infrastructure (RTDB, comm_manager, safety_manager, logger, config_manager with 478 passing tests and 49/49 requirements met). The control_manager implements a 1Hz state machine that reads RTDB telemetry, evaluates system state, selects power sources by priority, calculates power setpoints with SOC/temperature derating, and writes PCS commands via Modbus. The alarm_manager implements IEC 62682 three-tier alarm processing that evaluates RTDB signals against configurable thresholds with hysteresis and delay timers, manages alarm lifecycle (ACTIVE/ACKNOWLEDGED/CLEARED/RTN), and publishes alarm events.

Both modules are Python-only at 1Hz (Decision #23: ~130us/cycle, 7700x headroom). They share the same M1 infrastructure: attach to RTDB via ctypes shared memory (ems_common.rtdb), subscribe to ZMQ telemetry PUB/SUB for change notifications (ems_common.ipc), push events to logger via ZMQ PUSH, and accept commands via ZMQ REQ/REP. The existing config schemas and YAML files already define the data structures needed (control_config.yaml with SOC limits, power limits, source priority, state machine params; alarms_config.yaml with per-signal thresholds, hysteresis, delay).

The control_manager is the most architecturally significant module in the entire EMS -- it is the only module that writes PCS setpoints via comm_manager, making it the sole authority for power dispatch. The alarm_manager is its partner, providing the threshold evaluation that triggers derating or protective shutdowns. Together they form the "brain" of the EMS, sitting at L4 Application and consuming everything L1-L3 produce.

Key insight from research: the control_manager and alarm_manager should be developed as independent modules that communicate through RTDB and ZMQ, not as tightly coupled components. The alarm_manager evaluates thresholds and publishes alarm states; the control_manager reads those alarm states and adjusts behavior. This keeps both testable in isolation and avoids circular dependencies.

## Key Findings

**Stack:** Python asyncio with pyzmq for both modules. No C components needed -- 1Hz loop has massive timing headroom on ARM A53. ctypes RTDB access for shared memory reads. No new dependencies beyond what M1 already has (pyzmq, msgpack, pyyaml already installed).

**Architecture:** State machine pattern with explicit enum states and transition guard functions. Alarm engine uses per-rule evaluator instances with independent hysteresis/delay state tracking. Both modules are independent systemd services. PCS commands flow through RTDB system section (control_manager writes setpoints, comm_manager reads and sends to PCS).

**Critical pitfall:** PCS command sequencing must respect 10-second on/off wait times and power ramp rates. Sending power setpoints to an off PCS, or turning off a PCS under load, can damage equipment. The control_manager must implement strict interlock logic that validates PCS state before every command.

## Implications for Roadmap

Based on research, suggested phase structure:

1. **Control State Machine + Alarm Engine Core** - Build both modules' core logic in parallel: control_manager state machine (IDLE/STANDBY/CHARGING/DISCHARGING/FAULT) with transition guards and RTDB reads; alarm_manager threshold evaluator with hysteresis, delay timers, and alarm lifecycle. No PCS commands yet.
   - Addresses: State transitions, alarm evaluation, RTDB reading
   - Avoids: PCS damage from untested command sequencing

2. **PCS Command Sequencing** - Add PCS on/off sequencing with 10s wait, power setpoint writing via RTDB, ramp rate control, and interlock validation.
   - Addresses: Safe PCS control with state validation
   - Avoids: Equipment damage from unsequenced commands

3. **Source Priority Dispatch + Derating** - Implement DAY/NIGHT priority dispatch, SOC/temperature derating curves, BMS current limit enforcement, and grid-tie/off-grid mode awareness.
   - Addresses: Energy optimization, multi-source coordination, battery protection
   - Avoids: Incorrect dispatch from missing derating curves

4. **Control-Alarm Integration + E2E Testing** - Wire alarm_manager protection actions into control_manager decisions, add alarm shelving/suppression, cross-module testing with simulators.
   - Addresses: Closed-loop protection, IEC 62682 compliance, end-to-end validation
   - Avoids: Siloed testing that misses integration defects

**Phase ordering rationale:**
- State machine and alarm engine can be built in parallel (no dependency between them)
- PCS commands require validated state machine (must know system is in correct state)
- Dispatch requires PCS command layer (needs to actually send setpoints)
- Integration last because it requires both modules fully functional

**Research flags for phases:**
- Phase 2 (PCS commands): Needs careful simulator validation -- PCS has 10s on/off timing, fault reset sequencing, and ramp rate constraints
- Phase 3 (Dispatch): Source priority truth tables from architecture spec need validation against real scenarios
- Phase 4 (Integration): Alarm-to-control feedback loop is the most defect-prone area

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Python asyncio + pyzmq proven in M1, same patterns. No new dependencies. |
| Features | HIGH | Requirements well-defined in architecture spec, config schemas exist from M0. |
| Architecture | HIGH | State machine and alarm engine are standard industrial patterns with clear RTDB interface. |
| Pitfalls | HIGH | PCS sequencing risks well-documented in requirements. Alarm lifecycle defined by IEC 62682. |

## Gaps to Address

- PCS vendor abstraction deferred to when second vendor appears (Decision #21)
- Grid code compliance (CEA India vs IEEE 1547) pending Decision #7.4 -- affects dispatch limits and ramp rates
- Real PCS V1.24 register map pending -- current simulator uses synthetic map
- Multi-PCS coordination (Decision #7.3) explicitly out of scope for M2
- Alarm rationalization and KPI monitoring (IEC 62682 stages H-J) deferred to operational phase
