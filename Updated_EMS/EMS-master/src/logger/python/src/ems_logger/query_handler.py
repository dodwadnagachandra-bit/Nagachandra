"""DuckDB query handler -- 6 predefined query types over Parquet/JSONL files.

Serves queries via ZMQ REQ/REP using stateless in-memory DuckDB connections.
No persistent database -- Parquet IS the storage, DuckDB reads directly.

Query types:
  - time_series: downsampled time series with configurable interval
  - latest: single most recent row
  - range_stats: min/max/avg/count per signal
  - event_log: reads JSONL event files with severity/source filters
  - energy_totals: charge/discharge kWh and grid import/export
  - cell_snapshot: cell voltages and temperatures for a specific rack

All query functions use in-memory duckdb.connect() -- no persistent database.
Signal names are validated against an allowlist to prevent SQL injection.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import zmq
import zmq.asyncio

from ems_common.ipc import (
    SOCK_LOGGER_QUERY,
    decode_command_request,
    encode_command_response,
)
from ems_logger.config import LoggerConfig
from ems_logger.event_writer import JsonlEventWriter
from ems_logger.parquet_schema import build_cluster_schema, build_system_schema

logger: logging.Logger = logging.getLogger(__name__)


class QueryError(Exception):
    """Raised when a query fails validation or exceeds limits."""


# ---------------------------------------------------------------------------
# Signal allowlist -- built from schema field names
# ---------------------------------------------------------------------------

def _build_signal_allowlist() -> frozenset[str]:
    """Build the set of allowed signal names from Parquet schemas.

    Uses system schema fields plus common cluster field patterns.
    """
    system_schema = build_system_schema()
    allowed: set[str] = set()

    for field in system_schema:
        if field.name != "ts":
            allowed.add(field.name)

    # Cluster schema fields follow rack{N}_field pattern
    # Allow a broad set by generating for up to 16 racks, 16 modules
    for r in range(16):
        prefix: str = f"rack{r}_"
        for suffix in (
            "pack_v", "pack_i", "soc", "soh",
            "min_cell_v", "max_cell_v", "avg_cell_v",
            "min_cell_t", "max_cell_t", "avg_cell_t",
            "fault_code", "online",
        ):
            allowed.add(f"{prefix}{suffix}")
        for m in range(16):
            mod_prefix: str = f"{prefix}mod{m}_"
            allowed.add(f"{mod_prefix}cell_v")
            allowed.add(f"{mod_prefix}cell_t")

    return frozenset(allowed)


_ALLOWED_SIGNALS: frozenset[str] = _build_signal_allowlist()


def _validate_signals(signals: list[str]) -> None:
    """Validate signal names against the allowlist.

    Args:
        signals: List of signal/column names to validate.

    Raises:
        QueryError: If any signal is not in the allowlist.
    """
    for sig in signals:
        if sig not in _ALLOWED_SIGNALS:
            raise QueryError(f"Invalid signal name: {sig!r}")


# ---------------------------------------------------------------------------
# Date-narrowed glob for Parquet files
# ---------------------------------------------------------------------------

def _build_date_globs(
    data_dir: Path,
    start_ts: int,
    end_ts: int,
    prefix: str,
) -> list[str]:
    """Build date-narrowed glob patterns for Parquet file discovery.

    For short ranges (<=30 days), generates per-day globs.
    For longer ranges, falls back to a broad glob.

    Args:
        data_dir: Root data directory.
        start_ts: Start timestamp in milliseconds.
        end_ts: End timestamp in milliseconds.
        prefix: File prefix pattern (e.g. "system_*", "cluster_0_*").

    Returns:
        List of glob pattern strings for DuckDB read_parquet.
    """
    start_dt: datetime = datetime.fromtimestamp(start_ts / 1000.0, tz=timezone.utc)
    end_dt: datetime = datetime.fromtimestamp(end_ts / 1000.0, tz=timezone.utc)

    # Calculate day span
    days: int = (end_dt.date() - start_dt.date()).days + 1

    if days > 30:
        # Broad glob for long ranges
        return [str(data_dir / "**" / f"{prefix}.parquet")]

    patterns: list[str] = []
    current: datetime = start_dt
    seen_dates: set[str] = set()
    for _ in range(days):
        date_key: str = current.strftime("%Y/%m/%d")
        if date_key not in seen_dates:
            seen_dates.add(date_key)
            pattern: str = str(
                data_dir / current.strftime("%Y") / current.strftime("%m")
                / current.strftime("%d") / f"{prefix}.parquet"
            )
            patterns.append(pattern)
        current = current.replace(day=current.day + 1) if current.day < 28 else current

    # If spanning months, also add days in between via simple day iteration
    if not patterns:
        patterns = [str(data_dir / "**" / f"{prefix}.parquet")]

    return patterns


def _find_latest_day_glob(data_dir: Path, prefix: str) -> list[str]:
    """Find the most recent day directory containing matching files.

    Searches backwards from today (up to 90 days) for files matching prefix.

    Args:
        data_dir: Root data directory.
        prefix: File prefix pattern.

    Returns:
        List with single glob pattern for the most recent day, or broad glob.
    """
    # Scan existing directories to find latest
    all_files: list[Path] = sorted(data_dir.rglob(f"{prefix}.parquet"), reverse=True)
    if all_files:
        latest: Path = all_files[0].parent
        return [str(latest / f"{prefix}.parquet")]
    return [str(data_dir / "**" / f"{prefix}.parquet")]


def _glob_to_parquet_list(patterns: list[str]) -> list[str]:
    """Expand glob patterns to actual file paths.

    Args:
        patterns: List of glob patterns.

    Returns:
        List of matching file paths as strings.
    """
    from pathlib import Path as _Path
    import glob as _glob

    files: list[str] = []
    for pattern in patterns:
        files.extend(_glob.glob(pattern, recursive=True))
    return sorted(files)


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def query_time_series(
    data_dir: Path,
    signals: list[str],
    start_ts: int,
    end_ts: int,
    interval_s: int,
    file_prefix: str = "system_*",
    max_rows: int = 10000,
) -> dict[str, Any]:
    """Query downsampled time series data from Parquet files.

    Uses DuckDB time_bucket() for downsampling with avg() aggregation.

    Args:
        data_dir: Root data directory.
        signals: List of signal column names to query.
        start_ts: Start timestamp in milliseconds.
        end_ts: End timestamp in milliseconds.
        interval_s: Downsampling interval in seconds.
        file_prefix: Parquet file prefix pattern.
        max_rows: Maximum rows to return (default 10000).

    Returns:
        Dict with columns, rows, count.

    Raises:
        QueryError: If signals invalid or row limit exceeded.
    """
    _validate_signals(signals)
    data_dir = Path(data_dir)

    globs: list[str] = _build_date_globs(data_dir, start_ts, end_ts, file_prefix)
    files: list[str] = _glob_to_parquet_list(globs)

    if not files:
        return {"columns": ["bucket_ts"] + signals, "rows": [], "count": 0}

    # Build DuckDB query with integer-based bucket downsampling
    # Uses integer division on epoch-ms to avoid DuckDB timestamp/pytz dependency
    signal_aggs: str = ", ".join(f'avg("{s}") AS "{s}"' for s in signals)
    file_list: str = ", ".join(f"'{f}'" for f in files)

    interval_ms: int = interval_s * 1000

    sql: str = f"""
        SELECT
            (ts // {interval_ms}) * {interval_ms} AS bucket_ts,
            {signal_aggs}
        FROM read_parquet([{file_list}])
        WHERE ts >= {start_ts} AND ts <= {end_ts}
        GROUP BY bucket_ts
        ORDER BY bucket_ts
    """

    con: duckdb.DuckDBPyConnection = duckdb.connect()
    try:
        result = con.execute(sql).fetchall()
        col_names: list[str] = [desc[0] for desc in con.description]

        if len(result) > max_rows:
            raise QueryError(
                f"Result exceeds row limit: {len(result)} rows > {max_rows} max"
            )

        rows: list[list[Any]] = [list(r) for r in result]
        return {"columns": col_names, "rows": rows, "count": len(rows)}
    finally:
        con.close()


def query_latest(
    data_dir: Path,
    signals: list[str],
    file_prefix: str = "system_*",
) -> dict[str, Any]:
    """Query the single most recent row.

    Args:
        data_dir: Root data directory.
        signals: List of signal column names.
        file_prefix: Parquet file prefix pattern.

    Returns:
        Dict with columns, rows (1 row), count.

    Raises:
        QueryError: If signals invalid.
    """
    _validate_signals(signals)
    data_dir = Path(data_dir)

    globs: list[str] = _find_latest_day_glob(data_dir, file_prefix)
    files: list[str] = _glob_to_parquet_list(globs)

    if not files:
        return {"columns": ["ts"] + signals, "rows": [], "count": 0}

    signal_cols: str = ", ".join(f'"{s}"' for s in signals)
    file_list: str = ", ".join(f"'{f}'" for f in files)

    sql: str = f"""
        SELECT ts, {signal_cols}
        FROM read_parquet([{file_list}])
        ORDER BY ts DESC
        LIMIT 1
    """

    con: duckdb.DuckDBPyConnection = duckdb.connect()
    try:
        result = con.execute(sql).fetchall()
        col_names: list[str] = [desc[0] for desc in con.description]

        rows: list[list[Any]] = [list(r) for r in result]
        return {"columns": col_names, "rows": rows, "count": len(rows)}
    finally:
        con.close()


def query_range_stats(
    data_dir: Path,
    signals: list[str],
    start_ts: int,
    end_ts: int,
    file_prefix: str = "system_*",
) -> dict[str, Any]:
    """Query min/max/avg/count statistics per signal.

    Args:
        data_dir: Root data directory.
        signals: List of signal column names.
        start_ts: Start timestamp in milliseconds.
        end_ts: End timestamp in milliseconds.
        file_prefix: Parquet file prefix pattern.

    Returns:
        Dict with columns [signal, min_val, max_val, avg_val, row_count], rows, count.

    Raises:
        QueryError: If signals invalid.
    """
    _validate_signals(signals)
    data_dir = Path(data_dir)

    globs: list[str] = _build_date_globs(data_dir, start_ts, end_ts, file_prefix)
    files: list[str] = _glob_to_parquet_list(globs)

    if not files:
        return {
            "columns": ["signal", "min_val", "max_val", "avg_val", "row_count"],
            "rows": [],
            "count": 0,
        }

    file_list: str = ", ".join(f"'{f}'" for f in files)

    # Build UNION ALL for each signal
    unions: list[str] = []
    for sig in signals:
        unions.append(
            f"""SELECT '{sig}' AS signal,
                       min("{sig}") AS min_val,
                       max("{sig}") AS max_val,
                       avg("{sig}") AS avg_val,
                       count(*) AS row_count
                FROM read_parquet([{file_list}])
                WHERE ts >= {start_ts} AND ts <= {end_ts}"""
        )

    sql: str = " UNION ALL ".join(unions)

    con: duckdb.DuckDBPyConnection = duckdb.connect()
    try:
        result = con.execute(sql).fetchall()
        col_names: list[str] = [desc[0] for desc in con.description]
        rows: list[list[Any]] = [list(r) for r in result]
        return {"columns": col_names, "rows": rows, "count": len(rows)}
    finally:
        con.close()


def query_event_log(
    data_dir: Path,
    start_ts: int,
    end_ts: int,
    severity_filter: str | None = None,
    source_filter: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Query structured events from JSONL files with optional filters.

    Reads JSONL files in the date range using JsonlEventWriter.read_events().
    Filtering is done in Python (JSONL files are small).

    Args:
        data_dir: Root data directory.
        start_ts: Start timestamp in milliseconds.
        end_ts: End timestamp in milliseconds.
        severity_filter: Optional severity level to filter by.
        source_filter: Optional source module name to filter by.
        limit: Maximum events to return (default 1000).

    Returns:
        Dict with columns (empty, since events are dicts), rows (list of event dicts), count.
    """
    data_dir = Path(data_dir)

    start_dt: datetime = datetime.fromtimestamp(start_ts / 1000.0, tz=timezone.utc)
    end_dt: datetime = datetime.fromtimestamp(end_ts / 1000.0, tz=timezone.utc)

    # Collect JSONL files in date range
    all_events: list[dict[str, Any]] = []
    days: int = (end_dt.date() - start_dt.date()).days + 1

    for day_offset in range(days):
        day_dt: datetime = start_dt.replace(
            day=start_dt.day + day_offset,
            hour=0, minute=0, second=0, microsecond=0,
        )
        year: str = day_dt.strftime("%Y")
        month: str = day_dt.strftime("%m")
        date_str: str = day_dt.strftime("%Y%m%d")
        jsonl_path: Path = data_dir / "events" / year / month / f"events_{date_str}.jsonl"

        if jsonl_path.exists():
            events: list[dict] = JsonlEventWriter.read_events(jsonl_path)
            all_events.extend(events)

    # Filter by time range
    filtered: list[dict[str, Any]] = [
        e for e in all_events
        if start_ts <= e.get("ts", 0) <= end_ts
    ]

    # Apply filters
    if severity_filter is not None:
        filtered = [e for e in filtered if e.get("severity") == severity_filter]

    if source_filter is not None:
        filtered = [e for e in filtered if e.get("src") == source_filter]

    # Apply limit
    if len(filtered) > limit:
        raise QueryError(f"Result exceeds row limit: {len(filtered)} events > {limit} max")

    return {"columns": [], "rows": filtered, "count": len(filtered)}


def query_energy_totals(
    data_dir: Path,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    """Query energy charge/discharge totals and grid import/export.

    Sums positive pcs_active_power as discharge (Wh), negative as charge (Wh).
    Calculates grid import/export from meter energy deltas.

    Args:
        data_dir: Root data directory.
        start_ts: Start timestamp in milliseconds.
        end_ts: End timestamp in milliseconds.

    Returns:
        Dict with columns [discharge_wh, charge_wh, grid_import_wh, grid_export_wh], rows, count.
    """
    data_dir = Path(data_dir)

    globs: list[str] = _build_date_globs(data_dir, start_ts, end_ts, "system_*")
    files: list[str] = _glob_to_parquet_list(globs)

    if not files:
        return {
            "columns": ["discharge_wh", "charge_wh", "grid_import_wh", "grid_export_wh"],
            "rows": [[0.0, 0.0, 0.0, 0.0]],
            "count": 1,
        }

    file_list: str = ", ".join(f"'{f}'" for f in files)

    sql: str = f"""
        SELECT
            COALESCE(SUM(CASE WHEN pcs_active_power > 0 THEN pcs_active_power ELSE 0 END), 0) AS discharge_wh,
            COALESCE(SUM(CASE WHEN pcs_active_power < 0 THEN ABS(pcs_active_power) ELSE 0 END), 0) AS charge_wh,
            COALESCE(MAX(meter_energy_import) - MIN(meter_energy_import), 0) AS grid_import_wh,
            COALESCE(MAX(meter_energy_export) - MIN(meter_energy_export), 0) AS grid_export_wh
        FROM read_parquet([{file_list}])
        WHERE ts >= {start_ts} AND ts <= {end_ts}
    """

    con: duckdb.DuckDBPyConnection = duckdb.connect()
    try:
        result = con.execute(sql).fetchall()
        col_names: list[str] = [desc[0] for desc in con.description]
        rows: list[list[Any]] = [list(r) for r in result]
        return {"columns": col_names, "rows": rows, "count": len(rows)}
    finally:
        con.close()


def query_cell_snapshot(
    data_dir: Path,
    cluster: int,
    rack: int,
    timestamp: int,
) -> dict[str, Any]:
    """Query cell voltages and temperatures for a specific rack at a timestamp.

    Finds the nearest row by timestamp in the cluster file, extracts cell_v
    and cell_t LIST columns for the specified rack.

    Args:
        data_dir: Root data directory.
        cluster: Cluster index.
        rack: Rack index within the cluster.
        timestamp: Target timestamp in milliseconds.

    Returns:
        Dict with columns (cell_v/cell_t column names), rows, count.
    """
    data_dir = Path(data_dir)

    # Find cluster files near the timestamp
    ts_dt: datetime = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc)
    day_dir: Path = (
        data_dir / ts_dt.strftime("%Y") / ts_dt.strftime("%m") / ts_dt.strftime("%d")
    )

    prefix: str = f"cluster_{cluster}_*"
    import glob as _glob
    files: list[str] = sorted(_glob.glob(str(day_dir / f"{prefix}.parquet")))

    if not files:
        # Try broad search
        files = sorted(_glob.glob(str(data_dir / "**" / f"{prefix}.parquet"), recursive=True))

    if not files:
        return {"columns": [], "rows": [], "count": 0}

    file_list: str = ", ".join(f"'{f}'" for f in files)

    # First get column names to find rack-specific cell columns
    con: duckdb.DuckDBPyConnection = duckdb.connect()
    try:
        # Get schema to identify rack columns
        schema_result = con.execute(
            f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet([{file_list}]))"
        ).fetchall()
        all_cols: list[str] = [r[0] for r in schema_result]

        # Filter to rack-specific cell_v and cell_t columns plus ts
        rack_prefix: str = f"rack{rack}_"
        cell_cols: list[str] = [
            c for c in all_cols
            if c.startswith(rack_prefix) and ("cell_v" in c or "cell_t" in c)
        ]

        if not cell_cols:
            return {"columns": [], "rows": [], "count": 0}

        select_cols: str = ", ".join(f'"{c}"' for c in ["ts"] + cell_cols)

        sql: str = f"""
            SELECT {select_cols}
            FROM read_parquet([{file_list}])
            ORDER BY ABS(ts - {timestamp})
            LIMIT 1
        """

        result = con.execute(sql).fetchall()
        col_names: list[str] = [desc[0] for desc in con.description]

        rows: list[list[Any]] = [list(r) for r in result]
        return {"columns": col_names, "rows": rows, "count": len(rows)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Query type registry
# ---------------------------------------------------------------------------

QUERY_TYPES: dict[str, dict[str, Any]] = {
    "time_series": {"max_rows": 10000, "timeout_s": 5},
    "latest": {"max_rows": 1, "timeout_s": 1},
    "range_stats": {"max_rows": 100, "timeout_s": 5},
    "event_log": {"max_rows": 1000, "timeout_s": 3},
    "energy_totals": {"max_rows": 1, "timeout_s": 5},
    "cell_snapshot": {"max_rows": 1, "timeout_s": 2},
}


# ---------------------------------------------------------------------------
# QueryServer -- ZMQ REP dispatcher (Task 2)
# ---------------------------------------------------------------------------

class QueryServer:
    """ZMQ REP query server dispatching predefined queries.

    Binds REP socket, receives MessagePack requests, dispatches to
    the correct query function, and returns MessagePack responses.

    Args:
        config: LoggerConfig with query settings and data paths.
        zmq_ctx: zmq.asyncio.Context (shared or test-specific).
        endpoint: Override ZMQ endpoint (default: SOCK_LOGGER_QUERY).
    """

    def __init__(
        self,
        config: LoggerConfig,
        zmq_ctx: zmq.asyncio.Context,
        endpoint: str | None = None,
    ) -> None:
        self._config: LoggerConfig = config
        self._data_dir: Path = Path(config.storage.data_dir)
        self._endpoint: str = endpoint or SOCK_LOGGER_QUERY
        self._socket: zmq.asyncio.Socket = zmq_ctx.socket(zmq.REP)
        self._socket.bind(self._endpoint)
        self._closed: bool = False
        logger.info("QueryServer bound to %s", self._endpoint)

    def _get_timeout(self, query_type: str) -> float:
        """Look up per-type timeout from config.

        Args:
            query_type: Query type string.

        Returns:
            Timeout in seconds.
        """
        timeout_map: dict[str, float] = {
            "time_series": float(self._config.query.time_series_timeout_s),
            "latest": float(self._config.query.latest_timeout_s),
            "range_stats": float(self._config.query.range_stats_timeout_s),
            "event_log": float(self._config.query.event_log_timeout_s),
            "energy_totals": float(self._config.query.energy_totals_timeout_s),
            "cell_snapshot": float(self._config.query.cell_snapshot_timeout_s),
        }
        return timeout_map.get(query_type, 5.0)

    async def _dispatch(self, action: str, params: dict) -> dict:
        """Route query request to the correct function.

        Args:
            action: Should be "query".
            params: Dict with "type" and query-specific parameters.

        Returns:
            Result dict with columns/rows/count.

        Raises:
            QueryError: If query type unknown or query fails.
        """
        query_type: str = params.get("type", "")

        if query_type not in QUERY_TYPES:
            raise QueryError(f"Unknown query type: {query_type!r}")

        if query_type == "time_series":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_time_series(
                    data_dir=self._data_dir,
                    signals=params.get("signals", []),
                    start_ts=params.get("start_ts", 0),
                    end_ts=params.get("end_ts", 0),
                    interval_s=params.get("interval_s", 60),
                    file_prefix=params.get("file_prefix", "system_*"),
                    max_rows=self._config.query.time_series_max_rows,
                ),
            )
        elif query_type == "latest":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_latest(
                    data_dir=self._data_dir,
                    signals=params.get("signals", []),
                    file_prefix=params.get("file_prefix", "system_*"),
                ),
            )
        elif query_type == "range_stats":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_range_stats(
                    data_dir=self._data_dir,
                    signals=params.get("signals", []),
                    start_ts=params.get("start_ts", 0),
                    end_ts=params.get("end_ts", 0),
                    file_prefix=params.get("file_prefix", "system_*"),
                ),
            )
        elif query_type == "event_log":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_event_log(
                    data_dir=self._data_dir,
                    start_ts=params.get("start_ts", 0),
                    end_ts=params.get("end_ts", 0),
                    severity_filter=params.get("severity_filter"),
                    source_filter=params.get("source_filter"),
                    limit=self._config.query.event_log_max_rows,
                ),
            )
        elif query_type == "energy_totals":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_energy_totals(
                    data_dir=self._data_dir,
                    start_ts=params.get("start_ts", 0),
                    end_ts=params.get("end_ts", 0),
                ),
            )
        elif query_type == "cell_snapshot":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_cell_snapshot(
                    data_dir=self._data_dir,
                    cluster=params.get("cluster", 0),
                    rack=params.get("rack", 0),
                    timestamp=params.get("timestamp", 0),
                ),
            )

        raise QueryError(f"Unhandled query type: {query_type!r}")  # pragma: no cover

    async def run(self) -> None:
        """Main async loop -- receive requests, dispatch, send responses.

        Runs until close() is called or the task is cancelled.
        """
        try:
            while not self._closed:
                try:
                    data: bytes = await asyncio.wait_for(
                        self._socket.recv(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    action, params = decode_command_request(data)
                    query_type: str = params.get("type", "")
                    timeout: float = self._get_timeout(query_type)

                    result: dict = await asyncio.wait_for(
                        self._dispatch(action, params),
                        timeout=timeout,
                    )

                    response: bytes = encode_command_response("ok", result=result)

                except asyncio.TimeoutError:
                    response = encode_command_response(
                        "error",
                        error_msg=f"Query timed out after {timeout}s",
                    )
                except QueryError as exc:
                    response = encode_command_response(
                        "error", error_msg=str(exc)
                    )
                except Exception as exc:
                    logger.exception("Query dispatch error")
                    response = encode_command_response(
                        "error", error_msg=str(exc)
                    )

                await self._socket.send(response)

        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        """Close ZMQ socket with ZMQ_LINGER=0."""
        self._closed = True
        self._socket.close(linger=0)
        logger.info("QueryServer closed")
