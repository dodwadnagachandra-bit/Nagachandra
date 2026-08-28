# Phase 23: Offline Buffer - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Local disk buffering during MQTT connectivity loss, FIFO replay on reconnection, configurable retention limits (max_hours, max_mb). Covers CLOUD-04, CLOUD-05. Extends cloud_manager from Phase 22.

</domain>

<decisions>
## Implementation Decisions

### Buffer Storage Format

How should offline telemetry and events be stored on disk during MQTT outages?

**Decision:** Append-only JSONL files (one per hour), matching the logger's JSONL pattern. Not a database, not binary.

| Aspect | Decision |
|--------|----------|
| Format | JSONL (one JSON object per line) |
| File naming | `buffer/{year}/{month}/{day}/cloud_{hour}.jsonl` |
| Rotation | Hourly (matches logger Parquet rotation pattern) |
| Content | Each line: `{ts, type: "telemetry"|"event", topic, payload}` |
| Write mode | Append-only, no fsync per write (fsync every 100 writes, matching logger pattern) |
| Crash recovery | Truncated last line skipped on read (same as logger JSONL, Phase 12) |

Key rules:
- JSONL reuses the logger's proven crash-safe append pattern (Phase 12, LOG-07).
- Hourly files make age-based cleanup trivial (delete files older than max_hours).
- JSON format means buffer contents are human-readable for debugging — no binary deserialization needed.
- Buffer directory under `data/cloud_buffer/` (same parent as logger's `data/` directory).
- File size is checked per-write against max_mb — if exceeded, oldest files deleted before writing new.

**Rationale:** JSONL is proven in the logger (Phase 12) for crash-safe append writes. A database (SQLite, DuckDB) adds complexity and crash-recovery concerns for a simple FIFO queue. Binary formats (msgpack, protobuf) save space but sacrifice debuggability — for an offline buffer that's only used during outages, human-readability is more valuable than compression.

### Replay Strategy

How should buffered data be replayed when MQTT connectivity returns?

**Decision:** Background replay task reads oldest files first, publishes at throttled rate, deletes files after successful publish.

| Aspect | Decision |
|--------|----------|
| Order | FIFO — oldest files first (chronological) |
| Throttle | Max 10 messages/second to broker (prevents flood after long outage) |
| Telemetry QoS | QoS 0 during replay (same as live — historical telemetry is best-effort) |
| Event QoS | QoS 1 during replay (same as live — events must be delivered) |
| MQTT topic | Same as live: `{prefix}/telemetry` and `{prefix}/events` (no separate "replay" topic) |
| Metadata | Replay messages include original `ts` — cloud can distinguish old vs live by timestamp |
| File cleanup | Delete JSONL file after all lines successfully published |
| Interruption | If MQTT disconnects during replay, stop and resume from current position on next reconnect |
| Priority | Live data takes priority — interleave replay between live publishes (1 replay per 1 live) |

Key rules:
- 10 msg/s throttle prevents broker overload after a 24-hour outage (864,000 buffered messages at 10s interval = 8,640 messages, drains in ~15 minutes).
- Live data always sent first — replay fills gaps between live publishes. Never queue live data behind replay.
- Original timestamps preserved so cloud-side analytics can correctly time-order the data.
- File deleted only after ALL lines published — partial file means resume from that file on next reconnect.
- Replay progress published on ZMQ telemetry (topic: "cloud") — `{buffer_files_remaining, buffer_mb_remaining}`.

**Rationale:** FIFO replay is the only correct strategy — cloud needs data in chronological order for time-series databases. Throttling prevents broker connection reset (most brokers enforce publish rate limits). Interleaving live + replay ensures current data is never delayed by historical replay. File-level deletion (not line-level) keeps the resume logic simple.

### Retention Management

How should the buffer enforce max_hours and max_mb limits?

**Decision:** Dual constraint — delete oldest files when EITHER limit is exceeded. Check before each write.

| Limit | Default (Residential) | Commercial | Container | Enforcement |
|-------|----------------------|-----------|-----------|-------------|
| max_hours | 24 | 48 | 168 (7 days) | Delete files with timestamp > max_hours old |
| max_mb | 50 | 200 | 500 | Delete oldest files until total size < max_mb |

Key rules:
- Check both limits before each buffer write — if either exceeded, delete oldest files first.
- max_hours based on file path date components (same as logger retention, Phase 12 pattern).
- max_mb calculated via `sum(os.path.getsize(f))` across all buffer files.
- Empty parent directories removed after file deletion (same as logger cleanup pattern).
- If buffer is full and both limits are at minimum, drop the incoming message (log WARNING) — don't block the main loop.

**Rationale:** Dual constraint matches the logger's retention model (Phase 12). max_hours prevents unbounded historical data on long outages. max_mb prevents disk exhaustion on high-frequency container deployments. Both are configurable per profile via cloud_config.yaml. The residential 24h/50MB is conservative — at 60s interval with ~2KB per message, 24 hours = ~2.8MB, well within 50MB.

### Claude's Discretion

- Buffer class design (standalone BufferManager vs integrated into CloudManager)
- File locking strategy (fcntl vs directory-based)
- Replay task integration with asyncio main loop
- Buffer status metrics (files remaining, MB remaining, replay rate)
- Test strategy (mock filesystem vs real temp directory)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 12 logger: JSONL append pattern (event_writer.py), crash recovery (truncated line skip), file age from path dates
- Phase 12 logger: retention cleanup pattern (cleanup.py), empty directory removal
- Phase 22 cloud_manager: MQTT publish pipeline (buffer inserts between ZMQ consumer and MQTT publish)
- `config/cloud_config.yaml` — offline_buffer section: enabled, max_hours, max_mb

### Established Patterns
- Append-only JSONL with periodic fsync (logger event_writer)
- File age from directory path date components (logger cleanup)
- Atomic file operations (.tmp → rename) for crash safety
- Configurable retention with dual limits (logger: 90-day + 80% SSD)

### Integration Points
- Integrates into cloud_manager publish pipeline (Phase 22) — buffer sits between ZMQ consumer and MQTT publish
- Uses same data directory parent as logger (`data/cloud_buffer/`)
- Buffer status published on ZMQ telemetry topic "cloud" (same as connection status)

</code_context>

<specifics>
## Specific Ideas

- Buffer is activated when `on_disconnect` callback fires, deactivated when `on_connect` fires
- During buffering, the ZMQ consumer continues running — data flows to buffer instead of MQTT
- Buffer and replay never run simultaneously — either buffering (offline) or replaying (online)
- The buffer path should be configurable for testing (temp directory in tests)

</specifics>

<deferred>
## Deferred Ideas

- Buffer compression (gzip JSONL files) — future optimization for low-storage deployments
- Buffer encryption (at-rest encryption for sensitive telemetry) — future security requirement
- Selective buffering (only buffer critical events, drop telemetry) — future optimization
- Buffer priority (events before telemetry in replay queue) — current FIFO is simpler

</deferred>

---

*Phase: 23-offline-buffer*
*Context gathered: 2026-03-15*
