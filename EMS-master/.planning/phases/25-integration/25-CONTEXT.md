# Phase 25: Integration and Hardening - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Full stack validation with cloud and OTA modules. Cloud connectivity, offline/online transitions, remote command flows, OTA update cycle, crash recovery. No new requirements — validates all Phase 22-24 requirements in integration.

</domain>

<decisions>
## Implementation Decisions

### Cloud Connectivity Test Methodology

How to test MQTT connectivity without a real cloud broker?

**Decision:** Local Mosquitto broker in test fixture. Start mosquitto as subprocess, connect cloud_manager to localhost:1883 (non-TLS for tests).

| Aspect | Decision |
|--------|----------|
| Broker | Mosquitto started as subprocess with minimal config (no auth, no TLS) |
| Port | Random available port (avoid conflicts with other tests) |
| TLS | Disabled for integration tests (TLS tested in unit tests with mock certs) |
| Cleanup | Kill mosquitto after test, remove temp config |
| Assertions | Subscribe to MQTT topics from test, verify published messages match expected |

#### E2E Remote Command Test

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all modules + cloud_manager + Mosquitto | Cloud_manager connected to broker | 15s |
| 2 | Publish `{prefix}/commands` with `{command: "mode_change", params: {target_state: "standby"}}` | — | — |
| 3 | Subscribe to `{prefix}/responses/{request_id}` | Response: `{status: "ok"}` | 5s |
| 4 | Verify RTDB | control_state == STANDBY | 15s |

Key rules:
- Mosquitto is the default broker (Decision #10.1). Testing against it validates the real deployment scenario.
- Test verifies the full chain: MQTT publish → cloud_manager → ZMQ REQ → control_manager → RTDB.
- Random port prevents conflicts when multiple test runs happen in parallel.
- TLS tested separately in unit tests with self-signed certs — integration tests focus on data flow, not TLS handshake.

**Rationale:** Local Mosquitto is the simplest real broker — it's a single binary with zero config. Mock MQTT clients don't validate the actual protocol interaction. Random ports follow the test isolation pattern from Phase 13 (tcp://127.0.0.1 random ports for ZMQ).

### Offline/Online Transition Test

How to test the offline buffer activate/deactivate/replay cycle?

**Decision:** Control Mosquitto lifecycle during test — stop broker to simulate offline, restart to trigger replay.

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Start all modules + cloud_manager + Mosquitto | Telemetry flowing to MQTT |
| 2 | Kill Mosquitto (simulate network loss) | cloud_manager detects disconnect, buffer activates |
| 3 | Wait 30 seconds (accumulate buffer) | Buffer files created in `data/cloud_buffer/` |
| 4 | Restart Mosquitto | cloud_manager reconnects, replay starts |
| 5 | Wait for replay to complete | Buffer files deleted, live telemetry resumes |
| 6 | Subscribe to MQTT, verify both replay and live messages received | Messages have correct timestamps (old and current) |

Key rules:
- Killing the broker (not just disconnecting the client) simulates real-world connectivity loss.
- 30-second buffer accumulation at 10s interval = ~3 buffered messages — enough to verify FIFO replay without long waits.
- Verify buffer files are created during offline period (filesystem assertion).
- Verify buffer files are deleted after successful replay (cleanup assertion).
- Verify replay messages have historical timestamps, live messages have current timestamps.

**Rationale:** Broker lifecycle control is more realistic than mock disconnects — it tests the actual TCP connection loss and paho-mqtt's reconnect logic. 30 seconds is enough to validate the pattern without making tests slow.

### OTA Update Cycle Test

How to test the full OTA cycle without real partitions?

**Decision:** Mock partition layer. OTA manager uses an abstraction for partition operations — tests inject a mock that simulates A/B partition behavior with temp directories.

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Create test OTA package (tar with manifest + dummy firmware + Ed25519 signature) | Package valid, signature verifies |
| 2 | Start ota_manager with mock partition backend | Status: idle |
| 3 | Serve package via local HTTP server (Python http.server) | — |
| 4 | Send OTA notification via MQTT (or ZMQ command) | Status: downloading → verifying → applying |
| 5 | Mock partition write completes | Boot flag swapped in mock |
| 6 | Simulate health check pass | Status: success, version updated |
| 7 | Test rollback: send another update, mock health check fail | Status: rolled_back, previous version restored |

Key rules:
- Mock partition abstracts `write_firmware()`, `swap_boot_flag()`, `get_active_partition()`, `rollback()`.
- Ed25519 test with a real key pair (generated in test fixture) — don't mock crypto.
- HTTP server serves from temp directory (Python's `http.server` module).
- Rollback test is critical — must verify that health check failure triggers automatic revert.
- Version tracking verified via ZMQ REQ (current_version, previous_version).

**Rationale:** Real partitions can't be tested in CI or on dev machines. The partition abstraction lets OTA logic be fully tested while deferring hardware-specific partition code to M5 (Yocto). Ed25519 verification must use real crypto — mocking it defeats the purpose of signature verification. Local HTTP server follows the test pattern of self-contained fixtures.

### Crash Recovery

| Module | Recovery Behavior |
|--------|-------------------|
| cloud_manager | Restart within 10s, reconnect to MQTT broker, resume telemetry forwarding. Offline buffer persists on disk — replay resumes if buffer had data. |
| ota_manager | Restart within 10s, check boot flag. If mid-update (boot_count > 1 without health confirm), trigger rollback. If idle, return to idle. |

Key rules:
- cloud_manager crash: paho-mqtt reconnects automatically on restart. Buffer files survive (on disk, not in memory). No data loss.
- ota_manager crash during download: `.partial` file remains — can be cleaned up and restarted from scratch (no resume across crashes).
- ota_manager crash after boot flag swap: boot_count detection handles this — next boot checks count and rolls back if needed.

### Claude's Discretion

- Mosquitto test fixture implementation (subprocess, config file, port allocation)
- Mock partition backend design
- Ed25519 test key pair generation (in conftest.py or fixture)
- OTA package builder helper for tests
- Makefile target naming (test-integration-m4)
- Whether to extend existing crash_recovery.py or create new test file

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/integration/conftest.py` — ModuleProcess, MetricsCollector, build_start_order
- `tests/integration/test_crash_recovery.py` — CRASH_MATRIX pattern
- `tests/integration/test_startup.py` — startup ordering
- Phase 22/23 cloud_manager: MQTT client, buffer manager
- Phase 24 ota_manager: update pipeline, partition abstraction

### Integration Points
- Mosquitto broker (local subprocess for tests)
- MQTT topics: `{prefix}/telemetry`, `{prefix}/events`, `{prefix}/commands`, `{prefix}/responses/*`, `{prefix}/status`, `{prefix}/ota/notify`
- HTTP server for OTA package download
- All M1-M3 modules running as background fixtures

</code_context>

<specifics>
## Specific Ideas

- Mosquitto is available on Ubuntu (`apt install mosquitto`) — test fixture can check and skip if not installed
- Ed25519 key pair generation: `cryptography` library or `PyNaCl` — same library used by ota_manager
- OTA package builder: simple Python function that creates tar with manifest + firmware + signature
- Local HTTP server: `http.server` stdlib module on random port

</specifics>

<deferred>
## Deferred Ideas

- Performance testing under high MQTT publish rate — deferred to M5
- TLS certificate rotation testing — deferred
- Multi-broker failover testing — deferred
- Real partition testing on ECU hardware — deferred to PLAT-01 resolution
- Long-duration connectivity soak test — deferred to M5

</deferred>

---

*Phase: 25-integration*
*Context gathered: 2026-03-15*
