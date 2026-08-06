---
phase: 25-integration
plan: "01"
subsystem: cloud_manager integration tests
tags: [integration, mqtt, cloud, testing, mosquitto]
dependency_graph:
  requires: [22-cloud-manager, 23-cloud-manager, 24-ota-manager]
  provides: [M4-integration-tests]
  affects: [tests/integration/]
tech_stack:
  added: [mosquitto (broker subprocess fixture), paho-mqtt (MQTT test client)]
  patterns: [subprocess broker fixture, class-scoped system fixtures, MQTT subscribe-before-publish pattern]
key_files:
  created:
    - tests/integration/test_m4_integration.py
  modified:
    - Makefile
key_decisions:
  - "auth.method: token in test cloud config — avoids mTLS cert path checks (Pitfall 1)"
  - "cloud_manager ZMQ PUB uses tcp://127.0.0.1 (not ipc://) — avoids /run/ems binding conflict (Pitfall 5)"
  - "Response subscription before command publish — prevents slow-joiner message loss (Pitfall 4)"
  - "mosquitto_broker fixture skips gracefully via pytest.skip() when mosquitto binary not found"
  - "Heartbeat interval uses cloud_config heartbeat_interval not in schema — uses constructor param default"
metrics:
  duration_minutes: 30
  completed_date: "2026-03-15"
  tasks_completed: 2
  files_created: 1
  files_modified: 1
  tests_added: 6
requirements_satisfied:
  - CLOUD-01
  - CLOUD-02
  - CLOUD-03
  - CLOUD-06
  - CLOUD-07
  - CLOUD-08
---

# Phase 25 Plan 01: M4 Integration Tests (Cloud Manager) Summary

**One-liner:** M4 integration test suite with Mosquitto subprocess broker validating cloud_manager connectivity, telemetry forwarding, QoS-1 event routing, ZMQ status PUB, and full E2E MQTT-to-RTDB command flow.

## What Was Built

### tests/integration/test_m4_integration.py (1,018 lines)

Complete M4 integration test file following the `test_m3_integration.py` pattern:

**Module-level helpers:**
- `_free_port()` — single-port allocator using socket bind
- `_allocate_tcp_ports(count)` — batch port allocator
- `_vcan_available()`, `_binary_exists(name)`, `_delay_ready(seconds)` — prerequisite checks
- `read_control_state()`, `check_control_state(target)` — RTDB state helpers
- `wait_for_mqtt_message(host, port, topic, timeout)` — single-message MQTT subscriber using paho CallbackAPIVersion.VERSION2 + threading.Event
- `collect_mqtt_messages(host, port, topic, count, timeout)` — multi-message MQTT collector

**Fixtures:**
- `mosquitto_broker` (class-scoped) — starts Mosquitto on random TCP port, waits for readiness, yields `(host, port)`, skips if binary not found
- `_build_m4_system(mqtt_host, mqtt_port)` — launches all M1+M2+M3+cloud_manager modules with temp cloud_config.yaml using `auth.method: token`
- `_teardown_m4_system(system)` — reverse-order cleanup of all modules and simulators

**TestM4Startup (5 tests):**
1. `test_all_modules_alive` — checks all launched modules are still running
2. `test_cloud_manager_connects` — verifies heartbeat appears on `ems/TEST-001/status` (CLOUD-07)
3. `test_telemetry_reaches_mqtt` — collects up to 2 messages on `ems/TEST-001/telemetry` within 30s (CLOUD-02)
4. `test_cloud_zmq_status` — connects ZMQ SUB to TCP cloud_pub endpoint, decodes msgpack frame (CLOUD-08)
5. `test_event_forwarded_to_mqtt` — waits for natural state_change event or triggers via command, verifies QoS-1 delivery to `ems/TEST-001/events` (CLOUD-03)

**TestE2ERemoteCommand (1 test):**
- `test_e2e_command_flow` — full chain: unique request_id, subscribe-before-publish to response topic, MQTT command publish, response verification, RTDB STANDBY state check (CLOUD-06)

### Makefile changes

- Added `mosquitto` to `setup` target apt-get install line
- Added `test-integration-m4` target after `test-integration-m3`
- Added `test-integration-m4` to `.PHONY` list

## Deviations from Plan

None — plan executed exactly as written. The schema inspection revealed the cloud_config JSON Schema does not include `heartbeat`, `events`, or `commands` sections (those are handled by constructor params and the `telemetry.topic_prefix` in the loop), so the test cloud config only uses the four required sections matching the schema.

## Self-Check

### Created files exist:

- tests/integration/test_m4_integration.py: FOUND (1,018 lines, >200 line minimum satisfied)

### Commits exist:
- b3ddf65: feat(25-integration-01): add M4 integration test file with Mosquitto fixture
- 537deb8: chore(25-integration-01): add mosquitto to setup deps and test-integration-m4 target

### Verification:
- `grep "mosquitto" Makefile`: present in setup target
- `grep "test-integration-m4" Makefile`: target exists
- `pytest --collect-only -m integration tests/integration/test_m4_integration.py`: 6 tests collected
- Tests skip gracefully when mosquitto not installed (verified with pytest run showing 1 skipped)

## Self-Check: PASSED
