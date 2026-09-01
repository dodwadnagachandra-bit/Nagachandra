# Phase 12: Logging - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Parquet 1Hz telemetry, JSONL events, DuckDB queries, and retention management. All telemetry and events are persisted to disk with queryable access and automatic retention management. Covers LOG-01 through LOG-09. Python module subscribing to ZMQ telemetry (SUB) and event (PULL) sockets.

</domain>

<decisions>
## Implementation Decisions

### Parquet Schema and Column Granularity

| Aspect | Decision |
|--------|----------|
| File granularity | Per-cluster + one system file per hour |
| File naming | `telemetry_{cluster}_{hour}.parquet` + `telemetry_system_{hour}.parquet` |
| Cell voltages/temps | LIST (array) columns, not individual columns |
| Pack-level fields | Flat top-level columns (pack_v, pack_i, soc, soh, min/max/avg cell v/t, fault, online) |
| System subsections | Flat columns with section prefix (pcs_, meter_, btms_, gpio_, system_) |
| GPIO | Small array columns (di[8], do[8]) |
| Topology metadata | Stored in Parquet file metadata (cells_per_module, modules_per_rack, etc.) |
| Timestamp | int64 ms since epoch, primary filter key |
| Rows per file | 3,600 rows (1Hz × 1 hour) |

Key rules:
- Per-cluster files match the CAN bus boundary and keep file counts manageable (1-8 per hour vs 4-64 for per-rack)
- PCS, Meter, BTMS, GPIO, System sections go into `telemetry_system_{hour}.parquet` (single-instance, not cluster-scoped)
- Cell voltages/temps as LIST columns compress well with Snappy (adjacent cells are nearly identical) and avoid 2,160+ column schemas
- Pack-level aggregates are flat columns because 90% of queries target these (SOC, voltage, power trends)
- Topology dimensions in Parquet file metadata so readers can reshape flat cell arrays back to [module][cell]
- DuckDB `list_filter()` and `list_min()` work natively on LIST columns for cell-level queries

### DuckDB Query Scope and Access Pattern

| Query Type | Parameters | Returns | Max Time Range | Max Rows | Timeout |
|------------|-----------|---------|---------------|----------|---------|
| `time_series` | signal(s), start_ts, end_ts, interval_s | Array of {ts, values} | 90 days | 10,000 | 5s |
| `latest` | signal(s) | Single row with latest values | N/A | 1 | 1s |
| `range_stats` | signal(s), start_ts, end_ts | min, max, avg, count per signal | 90 days | 100 | 5s |
| `event_log` | start_ts, end_ts, severity_filter, source_filter, limit | Array of event records | 90 days | 1,000 | 3s |
| `energy_totals` | start_ts, end_ts | charge_kwh, discharge_kwh, grid_import, grid_export | 365 days | 1 | 5s |
| `cell_snapshot` | cluster, rack, timestamp | All cell voltages + temps at nearest second | N/A | 1 | 2s |

Key rules:
- Predefined query types only via ZMQ REQ/REP — NO free-form SQL (security and stability risk)
- `time_series` is the workhorse for HMI charts; `interval_s` parameter enables DuckDB `time_bucket()` downsampling
- 10,000 point limit for time_series matches Chart.js practical rendering limit
- `event_log` queries JSONL files, not Parquet — all other types query Parquet
- `cell_snapshot` is the only query touching array columns — targeted single-timestamp lookup, not a scan
- Queries exceeding limits return error response with the specific limit that was hit
- Direct DuckDB CLI available for offline ad-hoc analysis — not exposed via ZMQ
- No caching in Phase 12 — deferred to future (LOG-11: LRU + TTL)

### JSONL Event Retention and Relationship to Parquet

| Aspect | Decision |
|--------|----------|
| JSONL retention | 180 days (2× Parquet's 90 days) |
| JSONL size cap | None — participates in 80% SSD FIFO, deleted last |
| JSONL file granularity | Per-day, all modules combined |
| JSONL path | `data/events/{year}/{month}/events_{YYYYMMDD}.jsonl` |
| FIFO deletion order | Parquet (oldest first) → JSONL (oldest first) → within-retention Parquet → within-retention JSONL |
| Cleanup interval | 5 minutes via `statvfs()` |
| Survival mode | When above 80% after respecting retention floors, delete within-retention data oldest-first |
| Estimated JSONL size | ~100 KB/day (negligible vs Parquet 2-5 GB/day) |

Key rules:
- JSONL retained 2× longer than Parquet because events are tiny (~100 KB/day) and provide incident narrative for telemetry gaps
- All modules write to one daily file — unified timeline avoids merge/sort at query time
- Path convention: `data/events/{year}/{month}/events_{YYYYMMDD}.jsonl` with monthly subdirs to avoid directory bloat
- Three-tier FIFO cleanup: (1) expired Parquet oldest-first, (2) expired JSONL oldest-first, (3) within-retention data if still above 80%
- "Never delete JSONL before Parquet" (LOG-06) enforced by deletion order
- Survival mode (tier 3) handles small systems (64 GB eMMC) where retention alone can't prevent full disk

### Claude's Discretion

- PyArrow streaming writer configuration (row group size, buffering strategy)
- DuckDB query implementation details (SQL generation, connection management)
- JSONL write buffering and fsync strategy
- Logger internal threading model (telemetry writer, event writer, query handler, cleanup)
- Parquet file rotation timing (exact hourly boundary handling, timezone)
- Startup recovery: scanning existing files on restart to resume correct state
- Logger config YAML schema design (retention days, cleanup interval, data directory)
- ZMQ REQ/REP query message format (within existing envelope contract)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/logger/python/` — Stub package (ems-common dependency only)
- `src/logger/c/` — Stub C executable
- `deploy/systemd/logger.service` — Service file (Python entrypoint, C as subprocess)
- `src/common/python/src/ems_common/ipc.py` — SOCK_LOGGER path, encode_event/decode_event helpers, telemetry encode/decode
- `src/common/c/include/ipc_defs.h` — Logger socket path, event envelope keys, severity strings
- `src/data_manager/python/src/ems_data_manager/publisher.py` — 1Hz telemetry publisher (PUB socket, topic-prefixed MessagePack)
- `src/data_manager/python/src/ems_data_manager/snapshot.py` — Atomic file write pattern (.tmp → rename)
- `src/safety_manager/src/safety_event.h` — C-side event publishing (PUSH to logger socket, length-prefixed mpack)
- `config/system_config.yaml` — Topology dimensions for schema generation

### Established Patterns
- Atomic file I/O: write to .tmp, fsync, rename (snapshot.py)
- MessagePack telemetry envelope: {ts, seq, src, topic, payload}
- MessagePack event envelope: {ts, src, severity, event_type, message, data}
- ZMQ SUB topic filtering: subscribe to "bms.rack", "pcs", "gpio", etc.
- ZMQ PULL for many-to-one event ingestion (non-blocking, buffered)
- Length-prefixed framing (4-byte BE uint32) for C interop on PUSH socket

### Integration Points
- Phase 9 (Foundation): config_manager serves logger config, data_manager publishes telemetry
- Phase 10 (Safety): safety_manager pushes events to logger socket
- Phase 11 (Communications): comm_manager pushes comm_fault/recovery events to logger socket
- ZMQ SUB on `ipc:///run/ems/telemetry.sock` for 1Hz RTDB snapshots (topics: bms.rack.*, pcs, gpio, meter, btms, system)
- ZMQ PULL on `ipc:///run/ems/logger.sock` for events from all modules
- ZMQ REP on new socket (or reuse existing) for query API
- **Dependencies to add:** pyarrow, duckdb in pyproject.toml
- **Config to create:** logger_config.yaml + schema (retention days, data dir, cleanup interval)
- Systemd ordering: After=ems-data-manager.service, After=ems-comm-manager.service (needs data flowing)

</code_context>

<specifics>
## Specific Ideas

- Logger is "most forgiving" module (roadmap note) — failure does not affect real-time control or safety
- PyArrow streaming writer enables row-by-row Parquet writes without buffering entire hour in memory
- DuckDB reads Parquet files directly (no persistent database) — stateless, no WAL, no corruption risk
- Snappy compression is CPU-light and optimized for real-time writes (vs Zstd which is slower to compress)
- Residential system (1 cluster × 4 racks) generates ~50 MB/day Parquet; container (4 clusters × 16 racks) ~2-5 GB/day
- 64 GB eMMC + 256 GB SSD — Parquet goes on SSD, logger binary on eMMC

</specifics>

<deferred>
## Deferred Ideas

- **LOG-10**: 5-year summary retention with daily aggregates (min/max/avg per signal) — future requirement
- **LOG-11**: Query result caching with LRU + TTL for repeated HMI queries — future requirement
- **LOG-12**: Event severity filtering at ingestion (configurable minimum severity for JSONL) — future requirement
- Free-form SQL query API — deferred, use DuckDB CLI for offline ad-hoc analysis
- Blink/pulse patterns for indicator lamps — not logger scope

</deferred>

---

*Phase: 12-logging*
*Context gathered: 2026-03-14*
