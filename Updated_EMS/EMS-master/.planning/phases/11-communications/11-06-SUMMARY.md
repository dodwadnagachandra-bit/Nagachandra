---
phase: 11-communications
plan: "06"
subsystem: comm_manager
tags: [integration-tests, can, modbus, vcan, tcp, rtdb, zmq, events]
dependency_graph:
  requires: [comm_manager_c_binary, ModbusDevice, PcsDevice, CommOrchestrator, DeviceHealth, CAN_simulator, Modbus_simulator]
  provides: [test_integration_can, test_integration_modbus, end_to_end_validation]
  affects: [CI pipeline, comm_manager reliability confidence]
tech_stack:
  added: [python-can (test only), AsyncModbusTcpClient (test only)]
  patterns: [in-process-modbus-tcp-server, vcan-subprocess-fixture, zero-mode-addressing, zmq-event-capture]
key_files:
  created:
    - src/comm_manager/python/tests/test_integration_can.py
    - src/comm_manager/python/tests/test_integration_modbus.py
  modified:
    - src/comm_manager/python/src/ems_comm_manager/modbus_device.py
decisions:
  - "Modbus integration tests use TCP transport (not pty/serial) for zero OS-level dependencies"
  - "CAN integration tests require root + vcan + built binary -- skip cleanly in CI"
  - "In-process _MiniModbusServer with ZeroModeDevice for test isolation (no subprocess needed)"
  - "ZMQ event capture uses drain loop with sleep to handle async delivery timing"
  - "ModbusDevice.poll() fixed to use device_id= kwarg (pymodbus 3.12+ API)"
metrics:
  duration: "8m 43s"
  completed: "2026-03-14T04:28:02Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 11
  tests_passed: 72
  files_created: 2
  lines_of_code: 988
---

# Phase 11 Plan 06: Integration Tests -- CAN and Modbus End-to-End Summary

Integration tests proving end-to-end data path from simulators through comm_manager to RTDB, with fault/recovery event verification via ZMQ capture.

## Task 1: CAN Integration Tests (test_integration_can.py)

5 test classes covering the full CAN data path:
- **TestCanDecodePackSummary**: Simulator -> comm_manager_c -> RTDB pack_v non-zero
- **TestCanDecodeAllRacks**: Both racks receive CAN data (last_update_ms non-zero)
- **TestCanHeartbeatTimeout**: Stop simulator -> rack goes offline after heartbeat timeout
- **TestCanErrorFrame**: CAN_ERR_BUSOFF error frame -> can_health bus_state = CAN_BUS_OFF
- **TestCanOnlineRecovery**: Online -> offline (timeout) -> online (frames resume)

All tests use `pytest.mark.integration` and skip cleanly without root/vcan/binary.

Fixtures: `vcan_interface` (creates/destroys vcan0), `rtdb_shm` (creates/destroys RTDB shm), `comm_manager_c_process` (starts/kills C binary), `can_simulator` (starts/kills CAN sim).

## Task 2: Modbus Integration Tests (test_integration_modbus.py)

6 test classes covering the Modbus data path:
- **TestPcsPollingWritesRtdb**: Poll mini TCP server -> RTDB PCS fields non-zero
- **TestPcsPollingSignedValues**: Two's complement negative power -> RTDB active_power < 0
- **TestDeviceTimeoutPublishesFault**: 3 consecutive failures -> comm_fault event on ZMQ
- **TestDeviceRecoveryPublishesEvent**: Fault -> recovery -> comm_recovery with offline_duration_ms > 0
- **TestOrchestratorMultiDevice**: PCS + meter poll same server -> both health.online
- **TestStartupDiscovery**: Reachable/unreachable device health state transitions

Uses in-process `_MiniModbusServer` with zero-mode addressing for test isolation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pymodbus `slave` keyword deprecation**
- **Found during:** Task 2 (Modbus integration test against real TCP server)
- **Issue:** `ModbusDevice.poll()` passed `slave=self.slave_id` to `read_holding_registers()`, but pymodbus 3.12+ uses `device_id=` parameter
- **Fix:** Changed `slave=self.slave_id` to `device_id=self.slave_id` in modbus_device.py
- **Files modified:** src/comm_manager/python/src/ems_comm_manager/modbus_device.py
- **Commit:** 5094653
- **Note:** This bug was undetectable by unit tests (mocked clients accept any kwargs). The integration test against a real pymodbus server caught it immediately.

**2. [Rule 1 - Bug] Zero-mode addressing mismatch in test server**
- **Found during:** Task 2 (first integration test failing)
- **Issue:** Default `ModbusDeviceContext` applies +1 address offset, making register 0x0001 accessible at Modbus address 0x0000
- **Fix:** Created `ZeroModeDevice` subclass (matching production simulator pattern) for test server
- **Files modified:** src/comm_manager/python/tests/test_integration_modbus.py (test infrastructure only)

**3. Removed pytest.mark.timeout decorators**
- **Issue:** pytest-timeout not installed
- **Fix:** Removed `@pytest.mark.timeout()` decorators from CAN test classes to eliminate warnings

## Pre-existing Issues (Out of Scope)

- `test_discovery.py` fails with `ModuleNotFoundError: No module named 'ems_comm_manager.discovery'` -- discovery module is planned for Plan 05 (not yet implemented)

## Verification

- Unit tests: 66 passed (existing tests unaffected)
- Modbus integration tests: 6 passed
- CAN integration tests: 5 skipped (requires root + vcan + binary)
- C tests: No comm_manager C tests registered (expected)

## Self-Check: PASSED

- test_integration_can.py: 388 lines (min 80)
- test_integration_modbus.py: 599 lines (min 80)
- Commit cca760a: Found
- Commit 5094653: Found
- 11-06-SUMMARY.md: Found
