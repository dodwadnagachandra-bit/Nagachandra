# Phase 22: Cloud Manager - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

MQTT/TLS client connecting to cloud broker, telemetry/event forwarding with downsampling, remote command reception, heartbeat publishing, and connection status reporting. Covers CLOUD-01, CLOUD-02, CLOUD-03, CLOUD-06, CLOUD-07, CLOUD-08. Pure Python (paho-mqtt).

</domain>

<decisions>
## Implementation Decisions

### MQTT Client Architecture

How should the paho-mqtt client be structured for an async Python application that also consumes ZMQ?

**Decision:** paho-mqtt runs its own network loop thread (`loop_start()`). The async main loop handles ZMQ SUB and coordinates with MQTT via thread-safe queues.

| Aspect | Decision |
|--------|----------|
| MQTT loop | paho-mqtt `loop_start()` — dedicated background thread |
| ZMQ consumption | asyncio task subscribes to SOCK_TELEMETRY, batches messages |
| Bridge | `threading.Queue` in paho callbacks → asyncio polling loop (asyncio.Queue is not safe from paho network thread per RESEARCH Pitfall 3) |
| Reconnection | paho-mqtt built-in reconnect with exponential backoff (1s → 60s cap) |
| TLS | `tls_set(ca_certs, certfile, keyfile, tls_version=ssl.PROTOCOL_TLS_CLIENT)` |
| QoS | Telemetry: QoS 0 (at-most-once, high volume). Events: QoS 1 (at-least-once, critical). |
| Clean session | `clean_session=True` — no broker-side message persistence for this device |

Key rules:
- paho-mqtt's network thread handles TCP/TLS and MQTT protocol. Don't fight it with asyncio.
- ZMQ SUB runs in asyncio (same pattern as logger, alarm_manager). Bridge to MQTT via queue.
- QoS 0 for telemetry because data is periodic — a missed sample is replaced in seconds. QoS 1 for events because alarms/faults must be delivered.
- Clean session because the device sends data, doesn't receive persistent queued messages.
- mTLS uses cert paths from cloud_config.yaml — no hardcoded paths.

**Rationale:** paho-mqtt is the Python MQTT standard (specified in requirements). Its threaded `loop_start()` is simpler and more reliable than trying to integrate MQTT into asyncio (paho's asyncio support is experimental). The queue bridge pattern is proven in production SCADA systems.

### Telemetry Downsampling Strategy

How does cloud_manager reduce 1Hz ZMQ telemetry to 10-60 second MQTT publishing?

**Decision:** Timer-based publish. Collect latest value per topic from ZMQ, publish accumulated snapshot at configured interval.

| Aspect | Decision |
|--------|----------|
| Collection | ZMQ SUB receives all 1Hz messages, stores latest per topic in dict |
| Publish trigger | asyncio timer at `telemetry.interval_s` from cloud_config.yaml |
| Payload format | JSON object with all topic snapshots: `{system: {...}, pcs: {...}, bms: [{rack0}, {rack1}...]}` |
| MQTT topic | `{topic_prefix}/telemetry` (single consolidated message) |
| Timestamp | Publish timestamp (not individual message timestamps) |
| Missing topics | Omit topics with no data since last publish (don't send stale data) |

Key rules:
- Latest-value-wins: if 60 values arrive for `pcs` topic in 60 seconds, only the last one is published.
- Single MQTT message per interval containing all topics — reduces MQTT overhead vs per-topic messages.
- JSON payload (not MessagePack) — cloud-side consumers expect JSON (standard for MQTT IoT).
- BMS racks aggregated into array: `bms: [{rack_index: 0, soc: 50, ...}, ...]` — not one MQTT topic per rack.
- Missing topics (e.g., meter offline) are omitted, not sent as null — reduces payload size.

**Rationale:** Latest-value downsampling is the standard IoT telemetry pattern — the cloud needs current state, not historical samples (logger handles history locally). Single consolidated message reduces MQTT publish count from 6+ per interval to 1, saving bandwidth on cellular connections. JSON is universal for cloud MQTT consumers (AWS IoT, Azure IoT Hub, Grafana, etc.).

### Remote Command Handling

How should cloud_manager receive and process remote commands from the MQTT broker?

**Decision:** Subscribe to `{prefix}/commands`, validate JSON payload, forward to control_manager or alarm_manager via ZMQ REQ (same API as HMI and scheduler).

| MQTT Command | ZMQ Target | ZMQ Action | Validation |
|-------------|-----------|------------|-----------|
| `mode_change` | control_cmd | mode_change | target_state in [idle, standby] |
| `setpoint` | control_cmd | manual_setpoint | power_kw is float |
| `priority` | control_cmd | source_priority | mode in [day, night, manual] |
| `fault_reset` | control_cmd | fault_reset | No params |
| `maintenance` | control_cmd | maintenance_enter/exit | action in [enter, exit] |
| `alarm_ack` | alarm_cmd | acknowledge | alarm_id is string |

Key rules:
- Command payload must be JSON with `{command: str, params: dict, request_id: str}`.
- `request_id` is echoed in the response published to `{prefix}/responses/{request_id}`.
- Invalid commands logged WARNING and rejected — never forwarded to ZMQ.
- Command response published to MQTT with `{request_id, status, result/error_msg}`.
- Same command set as HMI REST API (Phase 18) — no new commands invented for cloud.
- Rate limiting: max 10 commands/minute from cloud — prevents runaway automation.

**Rationale:** Reusing the existing control_cmd/alarm_cmd ZMQ API (Phase 14/15) means zero new business logic in cloud_manager — it's a thin MQTT-to-ZMQ proxy, just like the HMI is a thin HTTP-to-ZMQ proxy. The command set matches HMI exactly, so cloud and local operators have identical capabilities. Rate limiting prevents a misconfigured cloud automation from flooding the control loop.

### Claude's Discretion

- paho-mqtt client configuration (keepalive, max_inflight, reconnect_delay)
- MQTT topic naming conventions beyond prefix
- ZMQ socket lifecycle (create on startup vs per-command)
- Connection status ZMQ telemetry message format
- Heartbeat payload fields
- Test strategy (mock MQTT broker, or use paho-mqtt test fixtures)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/cloud_manager/` — Stub package (v0.1.0, depends on ems-common)
- `config/cloud_config.yaml` — Broker URL, mTLS cert paths, telemetry interval, offline buffer settings
- `config/schemas/cloud_config.schema.json` — Full schema with broker, auth, telemetry, offline sections
- `config/profiles/*/cloud_config.yaml` — Per-deployment overrides (residential 60s, commercial 30s, container 10s)
- `deploy/systemd/cloud_manager.service` — After=data_manager
- `src/common/python/src/ems_common/ipc.py` — All ZMQ socket paths and topic constants, encode/decode helpers

### Established Patterns
- Async Python modules with SIGTERM/SIGINT handlers
- ZMQ SUB for telemetry (logger, alarm_manager, control_manager pattern)
- ZMQ REQ for commands (HMI, scheduler pattern)
- Config loading via yaml.safe_load + JSON Schema validation
- Event publishing via ZMQ PUSH to logger

### Integration Points
- ZMQ SUB on SOCK_TELEMETRY for 1Hz telemetry (all topics)
- ZMQ SUB on SOCK_ALARM_PUB for alarm events (Phase 15)
- ZMQ REQ on SOCK_CONTROL_CMD for remote command forwarding
- ZMQ REQ on SOCK_ALARM_CMD for alarm acknowledgement forwarding
- ZMQ PUB on SOCK_TELEMETRY for connection status (topic: "cloud")
- ZMQ PUSH on SOCK_LOGGER for cloud events (connect/disconnect/error)

</code_context>

<specifics>
## Specific Ideas

- paho-mqtt must be added as dependency to cloud_manager pyproject.toml
- mTLS cert paths (/etc/ems/certs/) won't exist in dev — need mock/self-signed for testing
- Cloud telemetry interval varies by profile: 60s residential, 30s commercial, 10s container
- Offline buffer (Phase 23) integrates into this module — design the publish path with buffer insertion point

</specifics>

<deferred>
## Deferred Ideas

- **CLOUD-09**: Per-cell telemetry — pending Decision #10.2 from cloud architect
- **CLOUD-10**: Cloud-initiated schedule push — future requirement
- **CLOUD-11**: Fleet management — future requirement
- AWS IoT Core / Azure IoT Hub specific integrations — pending Decision #10.1
- Message compression for low-bandwidth sites — future optimization
- MQTT 5.0 features (shared subscriptions, topic aliases) — paho-mqtt v2.x

</deferred>

---

*Phase: 22-cloud-manager*
*Context gathered: 2026-03-15*
