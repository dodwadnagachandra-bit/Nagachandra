# Phase 12: Logging - Research

**Researched:** 2026-03-14
**Domain:** Parquet telemetry persistence, DuckDB queries, JSONL event logging, disk retention management
**Confidence:** HIGH

## Summary

Phase 12 implements the EMS logger module -- a pure Python service that subscribes to ZMQ telemetry (SUB) and event (PULL) sockets, writes 1Hz Parquet files with PyArrow, appends structured JSONL events, serves predefined DuckDB SQL queries via ZMQ REQ/REP, and manages disk retention with FIFO cleanup. The logger is the "most forgiving" module -- its failure does not affect safety or real-time control.

The stack is straightforward: PyArrow 23.x for Parquet streaming writes with Snappy compression, DuckDB 1.5.x for stateless Parquet queries (in-memory connection, no persistent database), and standard library for JSONL append writes. The existing codebase provides all necessary integration patterns: ZMQ SUB topic filtering, MessagePack envelope decode, atomic file write (.tmp then rename), and the RTDB ctypes struct definitions that define the Parquet schema shape.

**Primary recommendation:** Build a single Python asyncio service with four concurrent tasks: (1) telemetry writer subscribing to ZMQ PUB, (2) event writer on ZMQ PULL, (3) query handler on ZMQ REP, and (4) periodic cleanup timer. Use PyArrow ParquetWriter for incremental row group writes and DuckDB read_parquet() for stateless queries.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- Per-cluster + one system file per hour Parquet granularity
- File naming: `telemetry_{cluster}_{hour}.parquet` + `telemetry_system_{hour}.parquet`
- Cell voltages/temps as LIST (array) columns, pack-level fields as flat columns
- System subsections as flat columns with section prefix (pcs_, meter_, btms_, gpio_, system_)
- GPIO as small array columns (di[8], do[8])
- Topology metadata in Parquet file metadata
- Timestamp as int64 ms since epoch
- 3,600 rows per file (1Hz x 1 hour)
- Predefined query types only via ZMQ REQ/REP (no free-form SQL)
- Six query types: time_series, latest, range_stats, event_log, energy_totals, cell_snapshot
- 10,000 point limit for time_series, timeouts per query type
- JSONL 180-day retention (2x Parquet 90 days)
- JSONL per-day, all modules combined, path: data/events/{year}/{month}/events_{YYYYMMDD}.jsonl
- Three-tier FIFO: expired Parquet -> expired JSONL -> within-retention oldest-first
- Cleanup interval: 5 minutes via statvfs()
- 80% SSD threshold for FIFO cleanup trigger

### Claude's Discretion
- PyArrow streaming writer configuration (row group size, buffering strategy)
- DuckDB query implementation details (SQL generation, connection management)
- JSONL write buffering and fsync strategy
- Logger internal threading model (telemetry writer, event writer, query handler, cleanup)
- Parquet file rotation timing (exact hourly boundary handling, timezone)
- Startup recovery: scanning existing files on restart to resume correct state
- Logger config YAML schema design (retention days, data directory, cleanup interval)
- ZMQ REQ/REP query message format (within existing envelope contract)

### Deferred Ideas (OUT OF SCOPE)
- LOG-10: 5-year summary retention with daily aggregates
- LOG-11: Query result caching with LRU + TTL
- LOG-12: Event severity filtering at ingestion
- Free-form SQL query API
- Blink/pulse patterns for indicator lamps

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| LOG-01 | 1Hz Parquet telemetry via ZMQ SUB + PyArrow streaming writer | PyArrow ParquetWriter.write_batch() with RecordBatch; ZMQ SUB topic filtering from publisher.py pattern |
| LOG-02 | Parquet file rotation (hourly default) with timestamp in filename | ParquetWriter close/reopen on hour boundary; atomic .tmp rename pattern from snapshot.py |
| LOG-03 | DuckDB SQL query interface via ZMQ REQ/REP (no persistent DB) | DuckDB in-memory connect + read_parquet() glob; predefined query type dispatch |
| LOG-04 | JSONL structured event logging via ZMQ PULL | decode_event() from ipc.py; json.dumps + file.write + newline; daily rotation |
| LOG-05 | 90-day raw Parquet retention | Cleanup timer scans data directory; file age from path date components |
| LOG-06 | FIFO cleanup via statvfs(), oldest Parquet first, never JSONL before Parquet | os.statvfs() for usage %; three-tier deletion order |
| LOG-07 | Crash recovery: atomic rename for Parquet, truncated-line skip for JSONL | .tmp + os.rename() pattern; JSONL reader skips lines failing json.loads() |
| LOG-08 | Snappy compression default | PyArrow ParquetWriter compression='snappy' (default anyway) |
| LOG-09 | Parquet partitioning: data/{year}/{month}/{day}/telemetry_{cluster}_{hour}.parquet | Directory creation from timestamp; DuckDB glob pattern for time-range queries |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyarrow | >=18.0 | Parquet streaming writer with Snappy | Industry standard for Parquet in Python; ParquetWriter supports incremental write_batch(); Snappy is default codec |
| duckdb | >=1.1 | Stateless SQL queries over Parquet files | Reads Parquet natively via read_parquet(); predicate pushdown; time_bucket() for downsampling; in-memory mode = no WAL/corruption risk |
| pyzmq | (already dep) | ZMQ SUB/PULL/REP sockets | Already used by all EMS modules |
| msgpack | (already dep) | Decode telemetry/event envelopes | Already used project-wide |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json (stdlib) | - | JSONL serialization | Event log append (simple, human-readable) |
| os (stdlib) | - | statvfs(), rename(), fsync() | Disk monitoring, atomic writes |
| pathlib (stdlib) | - | Directory tree management | data/{year}/{month}/{day}/ creation |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyArrow ParquetWriter | fastparquet | PyArrow is faster, better LIST column support, official Apache project |
| DuckDB | pandas read_parquet | DuckDB handles glob patterns natively, predicate pushdown, time_bucket(), orders of magnitude faster on multi-file scans |
| JSONL | SQLite events | JSONL is append-only (crash-safe), no WAL corruption risk, human-readable |

**Installation:**
```bash
cd src/logger/python && uv add pyarrow duckdb
```

## Architecture Patterns

### Recommended Project Structure
```
src/logger/python/src/ems_logger/
    __init__.py              # Package metadata
    __main__.py              # Entrypoint, asyncio.run(), signal handling
    telemetry_writer.py      # ZMQ SUB -> Parquet (1Hz write, hourly rotation)
    event_writer.py          # ZMQ PULL -> JSONL (append, daily rotation)
    query_handler.py         # ZMQ REP -> DuckDB queries (6 predefined types)
    cleanup.py               # Disk monitor + FIFO retention manager
    parquet_schema.py        # PyArrow schema definitions (cluster + system)
    config.py                # LoggerConfig dataclass from logger_config.yaml
config/
    logger_config.yaml       # New config file
    schemas/logger_config.schema.json  # JSON Schema
```

### Pattern 1: PyArrow Streaming Writer with Hourly Rotation
**What:** Open a ParquetWriter per cluster (and one for system), write RecordBatch every second, close and rename on hour boundary.
**When to use:** All 1Hz telemetry persistence.
**Example:**
```python
import pyarrow as pa
import pyarrow.parquet as pq
import os
from pathlib import Path

class ParquetRotatingWriter:
    """Manages a single Parquet file for one cluster or system section."""

    def __init__(self, schema: pa.Schema, data_dir: Path,
                 file_prefix: str, metadata: dict[str, str]) -> None:
        self._schema = schema
        self._data_dir = data_dir
        self._prefix = file_prefix
        self._metadata = metadata  # topology dims for file metadata
        self._writer: pq.ParquetWriter | None = None
        self._tmp_path: Path | None = None
        self._final_path: Path | None = None
        self._current_hour: int = -1

    def write_row(self, row_dict: dict) -> None:
        """Write a single telemetry row, rotating file on hour boundary."""
        ts_ms = row_dict["ts"]
        hour = (ts_ms // 3_600_000) % 24  # UTC hour

        if hour != self._current_hour:
            self._close_current()
            self._open_new(ts_ms)
            self._current_hour = hour

        # Convert dict to RecordBatch (single row)
        batch = pa.RecordBatch.from_pydict(
            {k: [v] for k, v in row_dict.items()},
            schema=self._schema
        )
        self._writer.write_batch(batch)

    def _open_new(self, ts_ms: int) -> None:
        """Open new .tmp Parquet file for the hour containing ts_ms."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        day_dir = self._data_dir / f"{dt.year}" / f"{dt.month:02d}" / f"{dt.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{self._prefix}_{dt.hour:02d}.parquet"
        self._final_path = day_dir / filename
        self._tmp_path = day_dir / f"{filename}.tmp"

        # Add topology metadata to schema
        kv_meta = {k.encode(): v.encode() for k, v in self._metadata.items()}
        schema_with_meta = self._schema.with_metadata(kv_meta)

        self._writer = pq.ParquetWriter(
            str(self._tmp_path), schema_with_meta, compression="snappy"
        )

    def _close_current(self) -> None:
        """Close current writer and atomically rename .tmp -> final."""
        if self._writer is not None:
            self._writer.close()
            if self._tmp_path.exists():
                os.rename(str(self._tmp_path), str(self._final_path))
            self._writer = None
```

### Pattern 2: DuckDB Stateless Query Dispatch
**What:** In-memory DuckDB connection with predefined query functions. Each query type maps to a SQL template with parameterized WHERE clauses. No persistent database.
**When to use:** All ZMQ REQ/REP query handling.
**Example:**
```python
import duckdb

def query_time_series(
    data_dir: str, signals: list[str], start_ts: int,
    end_ts: int, interval_s: int
) -> list[dict]:
    """Downsampled time series query using time_bucket."""
    con = duckdb.connect()  # In-memory, stateless

    # Build glob pattern from time range
    glob_pattern = f"{data_dir}/*/*/*/telemetry_*.parquet"

    cols = ", ".join(f"avg({s}) as {s}" for s in signals)
    sql = f"""
        SELECT
            time_bucket(INTERVAL '{interval_s} seconds',
                epoch_ms(ts)) as bucket,
            {cols}
        FROM read_parquet('{glob_pattern}')
        WHERE ts BETWEEN {start_ts} AND {end_ts}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT 10000
    """
    result = con.execute(sql).fetchall()
    con.close()
    return result
```

### Pattern 3: JSONL Crash-Safe Append
**What:** Append JSON lines with newline terminator. On read, skip lines that fail json.loads() (truncated on crash). fsync periodically, not per write.
**When to use:** Event logging from all modules.
**Example:**
```python
import json
import os

class JsonlEventWriter:
    """Append-only JSONL writer with periodic fsync."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._file = None
        self._current_date: str = ""
        self._writes_since_sync: int = 0

    def append_event(self, event: dict) -> None:
        date_str = self._date_from_ts(event["ts"])
        if date_str != self._current_date:
            self._rotate(date_str)

        line = json.dumps(event, separators=(",", ":")) + "\n"
        self._file.write(line)
        self._writes_since_sync += 1

        # fsync every 100 events or 1 second (whichever first)
        if self._writes_since_sync >= 100:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._writes_since_sync = 0
```

### Pattern 4: Three-Tier FIFO Cleanup
**What:** Monitor disk with os.statvfs() every 5 minutes. When usage > 80%, delete in order: (1) expired Parquet, (2) expired JSONL, (3) within-retention oldest-first.
**When to use:** Automatic retention management.
**Example:**
```python
import os

def get_disk_usage_pct(path: str) -> float:
    """Return disk usage percentage for the filesystem containing path."""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bfree * st.f_frsize
    used = total - free
    return (used / total) * 100.0
```

### Anti-Patterns to Avoid
- **Buffering entire hour in memory:** Use ParquetWriter streaming (write_batch per second), not collect-then-write_table. Memory stays constant at ~1 row worth.
- **Creating DuckDB persistent database:** Use in-memory connect() only. Persistent DB adds WAL corruption risk and is unnecessary since Parquet IS the storage.
- **fsync on every JSONL write:** Kills throughput. Events are ~100/day, but batch fsync every N writes or on timer.
- **Parsing Parquet filenames for time-range queries:** Use the ts column in WHERE clause with DuckDB predicate pushdown. The directory structure is for human navigation and gross file pruning.
- **Deleting JSONL before Parquet in cleanup:** Explicit requirement violation. JSONL is tiny (~100 KB/day) and provides incident narrative.
- **Opening new DuckDB connection per query:** Acceptable for isolation but consider connection reuse within the query handler for the REP socket (one query at a time anyway). Either approach works -- the key is no persistent database file.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Parquet columnar writes | Custom binary format | PyArrow ParquetWriter | Compression, metadata, columnar layout, ecosystem compatibility |
| SQL over files | Custom index/scan | DuckDB read_parquet() | Predicate pushdown, time_bucket, parallel scan, zero config |
| Snappy compression | Manual compress blocks | PyArrow compression='snappy' | Built-in, CPU-light, well-tested |
| Disk usage monitoring | df subprocess | os.statvfs() | Native syscall, no subprocess overhead |
| Atomic file writes | Direct file close | .tmp + os.rename() | Already established pattern in snapshot.py |
| Time-bucketed aggregation | Manual bin/group | DuckDB time_bucket() | SQL-native, handles edge cases, DST-safe with UTC |

**Key insight:** The entire persistence layer is a thin glue between ZMQ data sources and PyArrow/DuckDB. The libraries handle all the hard parts (compression, columnar encoding, query optimization). The logger's complexity is in orchestration, not data processing.

## Common Pitfalls

### Pitfall 1: ParquetWriter Not Closed on Crash
**What goes wrong:** If the process crashes, the .tmp file is incomplete and unreadable.
**Why it happens:** ParquetWriter needs close() to write footer metadata.
**How to avoid:** Always write to .tmp file. On startup, delete any stale .tmp files (they are incomplete). On clean rotation, close() then rename().
**Warning signs:** .tmp files in data directory after restart.

### Pitfall 2: DuckDB Glob Pattern Too Broad
**What goes wrong:** Query scans all 90 days of Parquet files for a 1-hour query.
**Why it happens:** Using `data/*/*/*/*.parquet` without narrowing.
**How to avoid:** Build glob patterns from the time range: `data/2026/03/14/*.parquet` for single-day queries. For multi-day, use DuckDB's list of files or narrowed globs.
**Warning signs:** Query latency exceeds 5s timeout on small time ranges.

### Pitfall 3: ZMQ SUB Message Loss During Parquet Write
**What goes wrong:** If write_batch takes too long, ZMQ HWM causes message drops.
**Why it happens:** Single-threaded processing of SUB + write.
**How to avoid:** Parquet write_batch for a single row is sub-millisecond. No real risk at 1Hz. But set ZMQ_RCVHWM to reasonable value (e.g., 1000) and monitor for gaps.
**Warning signs:** Sequence number gaps in telemetry.

### Pitfall 4: Hour Boundary Race Condition
**What goes wrong:** Telemetry at exactly HH:59:59.999 and HH+1:00:00.000 might split incorrectly.
**How to avoid:** Use the message timestamp (ts field in envelope), not wall clock, to determine which hour-file a row belongs to. This ensures deterministic assignment.
**Warning signs:** Files with 3,599 or 3,601 rows.

### Pitfall 5: DuckDB LIST Column Type Mismatch
**What goes wrong:** PyArrow writes list<float32> but DuckDB reads as FLOAT[] -- functions work but type casting may surprise.
**Why it happens:** Parquet LIST type maps cleanly to DuckDB LIST type. The risk is if cell arrays have variable lengths across rows.
**How to avoid:** Always pad cell voltage/temp arrays to fixed length (cells_per_module) with NaN for unused slots. This keeps schema consistent.
**Warning signs:** DuckDB query errors on list_extract() or array indexing.

### Pitfall 6: Startup Recovery Missing Files
**What goes wrong:** Logger starts mid-hour, doesn't know if a Parquet file for this hour already exists (from previous run).
**How to avoid:** On startup, scan data directory for existing files. If a .parquet file for the current hour exists, open new writer and append (ParquetWriter creates a fresh file -- previous data for this hour is already committed). If a .tmp exists, delete it (incomplete from crash).
**Warning signs:** Duplicate or missing data for the hour spanning a restart.

### Pitfall 7: JSONL Corruption on Power Loss
**What goes wrong:** Last line in JSONL file is truncated.
**Why it happens:** Power loss during write before fsync completes.
**How to avoid:** JSONL reader must skip lines that fail json.loads(). LOG-07 requires this. Maximum data loss is events since last fsync.
**Warning signs:** json.JSONDecodeError on last line of daily file.

## Code Examples

### Cluster Parquet Schema (per-cluster file)
```python
import pyarrow as pa

def build_cluster_schema(
    racks_per_cluster: int,
    modules_per_rack: int,
    cells_per_module: int,
    temps_per_module: int,
) -> pa.Schema:
    """Build PyArrow schema for a per-cluster Parquet file."""
    fields = [
        pa.field("ts", pa.int64()),  # ms since epoch
    ]

    for r in range(racks_per_cluster):
        prefix = f"rack{r}_"
        # Flat pack-level fields (90% of queries)
        fields.extend([
            pa.field(f"{prefix}pack_v", pa.float32()),
            pa.field(f"{prefix}pack_i", pa.float32()),
            pa.field(f"{prefix}soc", pa.float32()),
            pa.field(f"{prefix}soh", pa.float32()),
            pa.field(f"{prefix}min_cell_v", pa.float32()),
            pa.field(f"{prefix}max_cell_v", pa.float32()),
            pa.field(f"{prefix}avg_cell_v", pa.float32()),
            pa.field(f"{prefix}min_cell_t", pa.float32()),
            pa.field(f"{prefix}max_cell_t", pa.float32()),
            pa.field(f"{prefix}avg_cell_t", pa.float32()),
            pa.field(f"{prefix}fault_code", pa.uint32()),
            pa.field(f"{prefix}online", pa.uint8()),
        ])

        # LIST columns for cell-level data (compressed well with Snappy)
        for m in range(modules_per_rack):
            mod_prefix = f"{prefix}mod{m}_"
            fields.append(
                pa.field(f"{mod_prefix}cell_v", pa.list_(pa.float32()))
            )
            fields.append(
                pa.field(f"{mod_prefix}cell_t", pa.list_(pa.float32()))
            )

    return pa.schema(fields)
```

### System Parquet Schema
```python
def build_system_schema() -> pa.Schema:
    """Build PyArrow schema for system-level Parquet file."""
    return pa.schema([
        pa.field("ts", pa.int64()),
        # PCS section
        pa.field("pcs_ac_voltage", pa.float32()),
        pa.field("pcs_ac_current", pa.float32()),
        pa.field("pcs_active_power", pa.float32()),
        pa.field("pcs_reactive_power", pa.float32()),
        pa.field("pcs_dc_voltage", pa.float32()),
        pa.field("pcs_dc_current", pa.float32()),
        pa.field("pcs_frequency", pa.float32()),
        pa.field("pcs_temperature", pa.float32()),
        pa.field("pcs_state", pa.int32()),
        pa.field("pcs_fault_code", pa.uint32()),
        # Meter section
        pa.field("meter_voltage", pa.float32()),
        pa.field("meter_current", pa.float32()),
        pa.field("meter_active_power", pa.float32()),
        pa.field("meter_reactive_power", pa.float32()),
        pa.field("meter_frequency", pa.float32()),
        pa.field("meter_power_factor", pa.float32()),
        pa.field("meter_energy_import", pa.float32()),
        pa.field("meter_energy_export", pa.float32()),
        # BTMS section
        pa.field("btms_inlet_temp", pa.float32()),
        pa.field("btms_outlet_temp", pa.float32()),
        pa.field("btms_fan_speed_pct", pa.float32()),
        pa.field("btms_cooling_active", pa.uint8()),
        # GPIO section
        pa.field("gpio_di", pa.list_(pa.uint8())),
        pa.field("gpio_do", pa.list_(pa.uint8())),
        # System aggregates
        pa.field("system_control_state", pa.int32()),
        pa.field("system_source_priority", pa.int32()),
        pa.field("system_active_setpoint_kw", pa.float32()),
        pa.field("system_total_soc", pa.float32()),
        pa.field("system_total_power_kw", pa.float32()),
        pa.field("system_total_energy_kwh", pa.float32()),
        pa.field("system_ems_uptime_s", pa.uint32()),
    ])
```

### DuckDB Query: time_series with Downsampling
```python
import duckdb

def query_time_series(
    data_dir: str, signals: list[str], start_ts: int,
    end_ts: int, interval_s: int, file_prefix: str = "telemetry_*"
) -> list[dict]:
    """Time-series query with time_bucket downsampling."""
    con = duckdb.connect()

    # Narrow glob to relevant date directories
    glob = _build_date_glob(data_dir, start_ts, end_ts, file_prefix)

    cols_sql = ", ".join(f"avg({s}) as {s}" for s in signals)
    sql = f"""
        SELECT
            time_bucket(
                INTERVAL '{interval_s} seconds',
                make_timestamp(ts * 1000)
            ) as bucket_ts,
            {cols_sql}
        FROM read_parquet({glob})
        WHERE ts >= ? AND ts <= ?
        GROUP BY bucket_ts
        ORDER BY bucket_ts
        LIMIT 10000
    """
    result = con.execute(sql, [start_ts, end_ts]).fetchdf()
    con.close()
    return result.to_dict("records")
```

### ZMQ REQ/REP Query Message Format
```python
# Request (MessagePack encoded):
{
    "action": "query",
    "params": {
        "type": "time_series",       # One of 6 predefined types
        "signals": ["pcs_active_power", "system_total_soc"],
        "start_ts": 1710374400000,   # ms since epoch
        "end_ts": 1710378000000,
        "interval_s": 60             # Downsampling interval
    }
}

# Response (MessagePack encoded):
{
    "status": "ok",
    "result": {
        "columns": ["bucket_ts", "pcs_active_power", "system_total_soc"],
        "rows": [[1710374400000, 45.2, 78.1], ...],
        "count": 60
    },
    "error_msg": None
}

# Error Response:
{
    "status": "error",
    "result": None,
    "error_msg": "time_series query exceeded 10000 row limit"
}
```

### Logger Config YAML
```yaml
# logger_config.yaml
_schema_version: "1.0"

storage:
  data_dir: "/mnt/ssd/ems/data"         # SSD mount point for Parquet + JSONL
  parquet_retention_days: 90             # LOG-05: raw Parquet retention
  jsonl_retention_days: 180              # 2x Parquet per CONTEXT.md decision
  disk_usage_threshold_pct: 80          # LOG-06: FIFO trigger threshold
  cleanup_interval_s: 300                # 5 minutes per CONTEXT.md decision

parquet:
  compression: "snappy"                  # LOG-08
  rotation_period_s: 3600                # Hourly rotation (LOG-02)
  row_group_size: 3600                   # One row group per file (1 hour)

query:
  socket: "ipc:///run/ems/logger_query.sock"
  time_series_max_rows: 10000
  time_series_timeout_s: 5
  latest_timeout_s: 1
  range_stats_timeout_s: 5
  event_log_max_rows: 1000
  event_log_timeout_s: 3
  energy_totals_timeout_s: 5
  cell_snapshot_timeout_s: 2
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| CSV telemetry files | Parquet columnar with Snappy | Standard since ~2020 | 5-10x compression, columnar predicate pushdown |
| SQLite for time-series | DuckDB over Parquet | DuckDB stable ~2023 | No persistent DB, parallel scans, time_bucket() |
| Custom binary logs | JSONL structured events | Industry standard | Human-readable, grep-able, crash-safe append |
| pandas for Parquet queries | DuckDB SQL | DuckDB 0.8+ ~2023 | No pandas dependency, faster, lower memory |

**Deprecated/outdated:**
- fastparquet: Functionally equivalent but less maintained; PyArrow is the standard
- pandas.DataFrame.to_parquet: Still works but adds unnecessary pandas dependency when PyArrow suffices

## Open Questions

1. **DuckDB connection lifecycle**
   - What we know: In-memory connect() is stateless and safe. No WAL, no corruption risk.
   - What's unclear: Whether to create one connection per query or reuse across queries. Both work since REP socket is sequential.
   - Recommendation: Create per-query for simplicity and guaranteed cleanup. Profile if latency is a concern.

2. **Telemetry message batching**
   - What we know: data_manager publishes topics sequentially in publish_once() -- BMS racks first, then PCS, GPIO, etc. All within ~1ms.
   - What's unclear: Whether to buffer all topics for 1 second then write one row, or write per-topic as received.
   - Recommendation: Buffer for 1 second. A "row" in the Parquet file represents a 1-second snapshot. Collect all topics within a 1-second window, then write a single row to each Parquet file (cluster file gets BMS data, system file gets PCS/GPIO/etc).

3. **Query socket path**
   - What we know: SOCK_LOGGER (ipc:///run/ems/logger.sock) is defined for PULL events. Query needs a separate socket.
   - What's unclear: Whether to add a new SOCK_LOGGER_QUERY constant or reuse SOCK_LOGGER with a different socket type.
   - Recommendation: Add SOCK_LOGGER_QUERY to ipc.py (and ipc_defs.h). PULL and REP cannot share a socket path. Use `ipc:///run/ems/logger_query.sock`.

4. **C logger stub**
   - What we know: The C stub exists but does nothing. The systemd service says "C++ component launched as subprocess by Python orchestrator."
   - What's unclear: Whether the C component is needed for Phase 12.
   - Recommendation: The C logger is not needed in Phase 12. All logging is Python (PyArrow, DuckDB, JSONL). The C stub can remain as-is for future use (e.g., high-frequency ring buffer). Update systemd service description to remove C subprocess mention.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_logger.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LOG-01 | 1Hz Parquet write from ZMQ SUB messages | unit | `uv run pytest tests/test_logger.py::test_parquet_write_from_telemetry -x` | Wave 0 |
| LOG-02 | Hourly Parquet rotation with correct naming | unit | `uv run pytest tests/test_logger.py::test_parquet_hourly_rotation -x` | Wave 0 |
| LOG-03 | DuckDB query returns correct results from Parquet | unit | `uv run pytest tests/test_logger.py::test_duckdb_time_series_query -x` | Wave 0 |
| LOG-04 | JSONL event append from ZMQ PULL messages | unit | `uv run pytest tests/test_logger.py::test_jsonl_event_append -x` | Wave 0 |
| LOG-05 | Files older than 90 days identified for deletion | unit | `uv run pytest tests/test_logger.py::test_retention_expiry -x` | Wave 0 |
| LOG-06 | FIFO cleanup deletes Parquet before JSONL | unit | `uv run pytest tests/test_logger.py::test_fifo_deletion_order -x` | Wave 0 |
| LOG-07 | Crash recovery: .tmp cleanup + truncated JSONL skip | unit | `uv run pytest tests/test_logger.py::test_crash_recovery -x` | Wave 0 |
| LOG-08 | Parquet files use Snappy compression | unit | `uv run pytest tests/test_logger.py::test_snappy_compression -x` | Wave 0 |
| LOG-09 | Files written to data/{year}/{month}/{day}/ structure | unit | `uv run pytest tests/test_logger.py::test_parquet_directory_structure -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_logger.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/test_logger.py` -- covers LOG-01 through LOG-09
- [ ] `tests/conftest.py` -- add tmp_path fixtures for data directory, sample telemetry/event dicts
- [ ] pyarrow + duckdb install: `cd src/logger/python && uv add pyarrow duckdb`
- [ ] pytest marker: add `logging` marker to pyproject.toml [tool.pytest.ini_options]

## Sources

### Primary (HIGH confidence)
- [PyArrow ParquetWriter docs v23.0.1](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html) - streaming write, write_batch, compression options
- [PyArrow Parquet guide](https://arrow.apache.org/docs/python/parquet.html) - schema, metadata, LIST types
- [DuckDB Parquet query docs](https://duckdb.org/docs/stable/guides/file_formats/query_parquet) - read_parquet(), glob patterns, predicate pushdown
- [DuckDB timestamp functions](https://duckdb.org/docs/stable/sql/functions/timestamp) - time_bucket() for downsampling
- [DuckDB list type docs](https://duckdb.org/docs/stable/sql/data_types/list) - list_extract, list_filter for array columns

### Secondary (MEDIUM confidence)
- [PyArrow PyPI](https://pypi.org/project/pyarrow/) - version 23.0.1 (Feb 2026)
- [DuckDB PyPI](https://pypi.org/project/duckdb/) - version 1.5.0 (Mar 2026)
- [os.statvfs Python docs](https://docs.python.org/3/library/statvfs.html) - disk usage monitoring

### Tertiary (LOW confidence)
- None - all findings verified with official documentation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - PyArrow and DuckDB are the definitive tools for this pattern, versions verified on PyPI
- Architecture: HIGH - Patterns derived from existing codebase (publisher.py, snapshot.py, ipc.py) + official library docs
- Pitfalls: HIGH - Based on known Parquet/DuckDB behavior and established atomic write patterns already used in project

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable libraries, unlikely to change)
