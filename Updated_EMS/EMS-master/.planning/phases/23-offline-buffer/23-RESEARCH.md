# Phase 23: Offline Buffer - Research

**Researched:** 2026-03-15
**Domain:** Local disk buffering + async replay integrated into cloud_manager publish pipeline
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Buffer Storage Format**
| Aspect | Decision |
|--------|----------|
| Format | JSONL (one JSON object per line) |
| File naming | `buffer/{year}/{month}/{day}/cloud_{hour}.jsonl` |
| Rotation | Hourly (matches logger Parquet rotation pattern) |
| Content | Each line: `{ts, type: "telemetry"|"event", topic, payload}` |
| Write mode | Append-only, no fsync per write (fsync every 100 writes) |
| Crash recovery | Truncated last line skipped on read |
| Buffer root | `data/cloud_buffer/` |
| Size enforcement | Check per-write against max_mb — delete oldest files if exceeded |

**Replay Strategy**
| Aspect | Decision |
|--------|----------|
| Order | FIFO — oldest files first (chronological) |
| Throttle | Max 10 messages/second to broker |
| Telemetry QoS | QoS 0 during replay |
| Event QoS | QoS 1 during replay |
| MQTT topic | Same as live (no separate "replay" topic) |
| Metadata | Replay messages include original `ts` |
| File cleanup | Delete JSONL file after all lines successfully published |
| Interruption | Stop and resume from current position on next reconnect |
| Priority | Live data first — interleave replay (1 replay per 1 live) |
| Progress | Published on ZMQ telemetry: `{buffer_files_remaining, buffer_mb_remaining}` |

**Retention Management**
| Limit | Default (Residential) | Commercial | Container | Enforcement |
|-------|-----------------------|-----------|-----------|-------------|
| max_hours | 24 | 48 | 168 (7 days) | Delete files with path date > max_hours old |
| max_mb | 50 | 200 | 500 | Delete oldest files until total size < max_mb |
- Check both limits before each buffer write
- Drop incoming message (log WARNING) if buffer is full and at minimum limits
- Empty parent directories removed after file deletion

**Activation/Deactivation**
- Buffer activated when `on_disconnect` callback fires (publisher._connected becomes False)
- Buffer deactivated when `on_connect` fires (publisher._connected becomes True)
- Buffer and replay never run simultaneously
- ZMQ consumer continues running during buffering — data flows to buffer instead of MQTT

### Claude's Discretion

- Buffer class design (standalone BufferManager vs integrated into CloudManager)
- File locking strategy (fcntl vs directory-based)
- Replay task integration with asyncio main loop
- Buffer status metrics (files remaining, MB remaining, replay rate)
- Test strategy (mock filesystem vs real temp directory)

### Deferred Ideas (OUT OF SCOPE)

- Buffer compression (gzip JSONL files)
- Buffer encryption (at-rest encryption for sensitive telemetry)
- Selective buffering (only buffer critical events, drop telemetry)
- Buffer priority (events before telemetry in replay queue)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CLOUD-04 | Offline buffer stores telemetry and events to local disk (SSD) when MQTT connection is lost, with configurable retention (max_hours, max_mb per profile) | BufferManager class handles JSONL append; retention enforcement via path-date parsing (logger cleanup.py pattern) |
| CLOUD-05 | Buffer replay drains offline buffer FIFO when MQTT connection is restored, throttled to prevent broker overload, with progress tracking | Async replay task in CloudLoop; 10 msg/s asyncio.sleep throttle; ZMQ status publish for progress |
</phase_requirements>

---

## Summary

Phase 23 adds a `BufferManager` class to `ems_cloud_manager` that hooks into the two existing Phase 23 integration points already in `CloudLoop`: `_do_publish_telemetry()` and `_do_publish_event()`. When `publisher.connected` is False, these methods route data to disk instead of MQTT. When connectivity returns, a background async replay task drains the buffer FIFO at 10 msg/s, interleaved with live traffic.

The implementation reuses two proven patterns directly from the Phase 12 logger: (1) `JsonlEventWriter`'s append-only write with periodic fsync every 100 writes and crash-safe truncated-line skip on read, and (2) `cleanup.py`'s path-date extraction for age-based retention and `_remove_empty_parents()` for directory cleanup. No new design decisions are needed — the planner can go straight to tasking.

The config section (`offline_buffer.enabled`, `max_hours`, `max_mb`) is already in `cloud_config.yaml` and `cloud_config.schema.json`. The test fixture in `tests/test_cloud_manager.py` already sets `offline_buffer.enabled: False` — Phase 23 tests will flip this to True and use a `tmp_path` directory.

**Primary recommendation:** Implement `BufferManager` as a standalone class in `src/cloud_manager/src/ems_cloud_manager/buffer.py`, instantiated in `__main__.py` and injected into a `BufferedCloudLoop` subclass that overrides `_do_publish_telemetry` and `_do_publish_event`.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `pathlib` | 3.12+ | File path operations, directory creation | Already used throughout EMS |
| Python stdlib `json` | 3.12+ | JSONL serialization | Chosen for human-readability over msgpack |
| Python stdlib `os` | 3.12+ | `os.fsync()`, `os.path.getsize()`, `os.statvfs()` | POSIX file operations |
| Python stdlib `asyncio` | 3.12+ | Replay task scheduling, throttle sleep | Already the CloudLoop async engine |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `fcntl` (stdlib) | 3.12+ | Advisory file locking | Not needed — single-process write, asyncio is single-threaded |
| `paho-mqtt` | 2.1.x | Publish replayed messages via `publisher.publish_telemetry()` / `publish_event()` | Same publisher instance already used for live traffic |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| JSONL hourly files | SQLite | SQLite adds WAL/crash complexity; JSONL proven in logger |
| JSONL hourly files | msgpack binary | Binary saves ~40% space but sacrifices debuggability during outages |
| asyncio.sleep throttle | Token bucket | Token bucket more precise; asyncio.sleep(0.1) simpler and sufficient at 10 msg/s |
| Subclass CloudLoop | Monkeypatch hook methods | Subclass is more testable and explicit; monkeypatching is fragile |

**Installation:** No new dependencies — uses only stdlib and existing `paho-mqtt`.

---

## Architecture Patterns

### Recommended Project Structure

The buffer implementation adds one new file to the existing cloud_manager package:

```
src/cloud_manager/src/ems_cloud_manager/
├── __init__.py
├── __main__.py        # Add buffer_dir param, wire BufferedCloudLoop
├── buffer.py          # NEW: BufferManager class
├── config.py          # No changes needed
├── dispatcher.py      # No changes needed
├── loop.py            # No changes needed (hooks already in place)
└── publisher.py       # No changes needed
```

Buffer data directory (separate from logger data):

```
data/cloud_buffer/
└── {year}/
    └── {month}/
        └── {day}/
            └── cloud_{hour:02d}.jsonl   # one file per hour of outage
```

### Pattern 1: BufferManager Class

**What:** Standalone class encapsulating all JSONL write, read, retention, and replay logic. Injected into a CloudLoop subclass.

**When to use:** Always — this keeps buffer logic testable without a live CloudLoop or MQTT broker.

```python
# src/cloud_manager/src/ems_cloud_manager/buffer.py
class BufferManager:
    """JSONL offline buffer for cloud_manager telemetry and events."""

    def __init__(
        self,
        buffer_dir: Path,
        max_hours: int,
        max_mb: int,
    ) -> None:
        self._buffer_dir: Path = buffer_dir
        self._max_hours: int = max_hours
        self._max_mb: int = max_mb
        self._writes_since_sync: int = 0
        self._current_file: IO[bytes] | None = None
        self._current_hour_key: str = ""  # "YYYY-MM-DD-HH"

    def write(self, msg_type: str, topic: str, payload: dict[str, Any]) -> None:
        """Append one record to the current hour's JSONL file.

        Enforces retention before writing. Drops with WARNING if limits are
        at minimum and still exceeded.
        """
        ...

    def drain(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield (msg_type, topic, payload) from oldest files first (FIFO).

        Caller deletes the file after all lines are consumed. Skips
        truncated last lines (crash recovery, same as logger LOG-07).
        """
        ...

    def _enforce_retention(self) -> None:
        """Delete oldest files until both max_hours and max_mb are satisfied."""
        ...

    def _total_size_mb(self) -> float:
        """Sum sizes of all buffer JSONL files in MB."""
        ...

    def _all_files_sorted(self) -> list[Path]:
        """Return all buffer JSONL files sorted oldest-first by path date."""
        ...

    @property
    def stats(self) -> dict[str, Any]:
        """Return {files_remaining, mb_remaining} for ZMQ status publish."""
        ...
```

### Pattern 2: BufferedCloudLoop Subclass

**What:** Subclass of `CloudLoop` that overrides the two Phase 23 hook methods. When offline, routes to `BufferManager.write()`. Adds a sixth async task for replay.

**When to use:** Always — this is the integration point designed in Phase 22 (`loop.py` lines 195-215 document this explicitly).

```python
# src/cloud_manager/src/ems_cloud_manager/loop.py (or a new buffered_loop.py)
class BufferedCloudLoop(CloudLoop):
    """CloudLoop subclass with offline buffer for CLOUD-04/CLOUD-05."""

    def __init__(
        self,
        config: dict[str, Any],
        publisher: MqttPublisher,
        buffer_manager: BufferManager,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, publisher, **kwargs)
        self._buffer: BufferManager = buffer_manager
        self._replay_rate: float = 10.0  # msg/s

    def _do_publish_telemetry(self, payload: dict[str, Any]) -> None:
        if self._publisher.connected:
            self._publisher.publish_telemetry(payload)
        else:
            self._buffer.write("telemetry", "telemetry", payload)

    def _do_publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        if self._publisher.connected:
            self._publisher.publish_event(topic, payload)
        else:
            self._buffer.write("event", topic, payload)

    async def run(self) -> None:
        self._publisher.start()
        await asyncio.gather(
            self._zmq_telemetry_collector(),
            self._periodic_publish(),
            self._zmq_event_forwarder(),
            self._heartbeat_publisher(),
            self._command_dispatcher_loop(),
            self._buffer_replay_task(),   # NEW sixth task
        )

    async def _buffer_replay_task(self) -> None:
        """Drain buffer FIFO when online, throttled to 10 msg/s."""
        ...
```

### Pattern 3: Hourly File Naming from Timestamp

**What:** File path derived from `time.time()` — same pattern as logger's date-from-path convention.

```python
def _current_file_path(self) -> Path:
    """Return path for the current hour's buffer file."""
    now: datetime = datetime.fromtimestamp(time.time(), tz=timezone.utc)
    return (
        self._buffer_dir
        / now.strftime("%Y")
        / now.strftime("%m")
        / now.strftime("%d")
        / f"cloud_{now.strftime('%H'):02s}.jsonl"
    )
```

**Age check from path** (same as `cleanup.py` `_parse_date_from_parquet_path`):

```python
def _file_datetime(self, path: Path) -> datetime | None:
    """Extract datetime from buffer/{year}/{month}/{day}/cloud_{hour}.jsonl."""
    try:
        hour: int = int(path.stem.split("_")[1])
        day: int = int(path.parent.name)
        month: int = int(path.parent.parent.name)
        year: int = int(path.parent.parent.parent.name)
        return datetime(year, month, day, hour, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
```

### Pattern 4: Replay Throttle + Live/Replay Interleave

**What:** Replay task sleeps between each message. Live traffic is never delayed — `_do_publish_telemetry` and `_do_publish_event` run in the main asyncio tasks; replay runs in a separate task that respects `asyncio.sleep()`.

```python
async def _buffer_replay_task(self) -> None:
    while not self._stop_event.is_set():
        if not self._publisher.connected:
            await asyncio.sleep(1.0)  # wait for reconnection
            continue

        replayed: int = 0
        for msg_type, topic, payload in self._buffer.drain():
            if not self._publisher.connected:
                break  # stop mid-replay if connection drops

            if msg_type == "telemetry":
                self._publisher.publish_telemetry(payload)
            else:
                self._publisher.publish_event(topic, payload)

            replayed += 1
            await asyncio.sleep(1.0 / self._replay_rate)  # 10 msg/s

        if replayed == 0:
            await asyncio.sleep(5.0)  # nothing to replay, back off

        # Publish progress on ZMQ status
        self._publish_buffer_status()
```

### Pattern 5: Status Publish (CLOUD-05 progress tracking)

**What:** Buffer stats appended to the `cloud` ZMQ telemetry topic. The HMI already subscribes to `SOCK_CLOUD_PUB` and displays cloud state. Buffer progress adds to the same payload.

The existing `_publish_cloud_status()` in `publisher.py` publishes `{state, broker, ts}`. Phase 23 should extend or supplement this with `{buffer_files_remaining, buffer_mb_remaining}` — either by modifying `_publish_cloud_status` to accept optional buffer stats, or by having `BufferedCloudLoop` call it separately.

### Anti-Patterns to Avoid

- **fsync every write:** The logger explicitly chose fsync-every-100 for performance. The buffer should use the same `_SYNC_EVERY = 100` pattern.
- **Line-level deletion tracking:** Deleting individual lines from a JSONL file requires rewriting the file. The decision locks in file-level deletion — publish all lines in a file, then delete the whole file.
- **asyncio.Queue for buffer drain:** The replay task runs in asyncio; `BufferManager.drain()` can be a synchronous generator (no ZMQ or I/O waits) — no asyncio.Queue needed.
- **Modifying loop.py's existing tasks:** `_periodic_publish` already has `if not self._publisher.connected: continue` (line 269 of loop.py). Phase 23 must REMOVE this guard in `BufferedCloudLoop._periodic_publish` override — or handle it in `_do_publish_telemetry`. The current guard short-circuits before calling `_do_publish_telemetry`, so the buffer would never receive offline telemetry unless this guard is removed or the override catches it earlier.

**CRITICAL INTEGRATION ISSUE:** `_periodic_publish` in `loop.py` (lines 268-270) skips the publish entirely when `not self._publisher.connected` — the comment says "Phase 23 handles buffering." This means `_do_publish_telemetry` is never called when offline. `BufferedCloudLoop` MUST override `_periodic_publish` to remove this guard, or `_periodic_publish` must be changed so it calls `_do_publish_telemetry` regardless of connection state (letting the hook decide).

The cleanest approach: override `_periodic_publish` in `BufferedCloudLoop` with the `connected` check removed — always build the snapshot payload and always call `_do_publish_telemetry`, which then routes live vs buffer.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Crash-safe JSONL read | Custom line parser | `json.loads()` per line, skip `json.JSONDecodeError` | Exact pattern from `JsonlEventWriter.read_events()` — proven for LOG-07 |
| Age from file path | Parse mtime | Extract from path components (`path.parent.name`, etc.) | mtime is unreliable across copies/syncs; logger cleanup.py does this correctly |
| Empty directory removal | Custom recursive delete | `_remove_empty_parents(file_path, stop_at)` pattern from cleanup.py | Already handles `OSError` (not-empty) gracefully |
| File size calculation | `du -sh` subprocess | `sum(f.stat().st_size for f in files)` | Pure Python, no subprocess overhead |
| Replay ordering | Database query | Sort `_all_files_sorted()` by path date | Hourly files already encode chronological order in the path |

**Key insight:** The entire buffer implementation can be built with only Python stdlib — no new packages. The logger already solved all the hard problems (crash recovery, retention, FIFO order, empty directory cleanup).

---

## Common Pitfalls

### Pitfall 1: _periodic_publish Connection Guard Blocks Buffering

**What goes wrong:** `loop.py` line 269 (`if not self._publisher.connected: continue`) short-circuits before `_do_publish_telemetry` is ever called when offline. Buffer never receives telemetry.

**Why it happens:** The Phase 22 comment says "Phase 23 handles buffering" but the guard was left in to avoid wasted CPU — it was designed to be removed or bypassed by the subclass.

**How to avoid:** `BufferedCloudLoop` must override `_periodic_publish` with the `if not self._publisher.connected: continue` guard removed. Always build snapshot payload and always call `self._do_publish_telemetry(payload)`.

**Warning signs:** Tests show buffer is empty after simulated disconnect + telemetry publish.

### Pitfall 2: File Still Open During Replay

**What goes wrong:** `BufferManager.write()` holds a file handle open for the current hour. During replay, that same file may be the "oldest" file if the outage just ended in the same hour. Replay tries to read it while it's still being written.

**Why it happens:** Hour boundaries don't align with disconnect/reconnect events.

**How to avoid:** `drain()` must skip or flush-then-read the currently-open file. Simplest: `_current_file` is tracked by hour key — during replay, skip any file matching `_current_hour_key` until write rotates to a new hour. Alternatively, `close()` the current write file before replay starts and let replay consume it in full.

**Warning signs:** Partial JSONL lines appearing in replay output; `json.JSONDecodeError` on lines that were mid-write.

### Pitfall 3: Replay Blocks _zmq_event_forwarder Event Delivery

**What goes wrong:** If replay takes longer than 1 loop iteration, events arriving during replay accumulate in the ZMQ socket buffer but `_zmq_event_forwarder` is still running (asyncio gather). This is actually fine — they're interleaved correctly. The real risk is calling `publisher.publish_event()` from replay while `_zmq_event_forwarder` also calls it.

**Why it happens:** `publish_event()` calls `self._client.publish()` which is paho-thread-safe (documented in `publisher.py` header). No actual race condition exists. Flag this as a non-issue.

**How to avoid:** No action needed — paho publish is thread-safe. Document in code.

### Pitfall 4: max_mb Calculated on Every Write (Performance)

**What goes wrong:** Summing `os.path.getsize()` across all buffer files on every write is O(N) in file count. During a 7-day outage at 60s intervals, this is ~10,000 files. Repeated `stat()` calls at 10-60s interval won't be a bottleneck, but calling it every write in the replay throttle path could create latency.

**Why it happens:** The decision says "check per-write" — at 60s telemetry intervals this is fine (1 call per minute). During replay at 10 msg/s this would be 10 calls/s.

**How to avoid:** Only enforce retention on writes to the buffer (offline path), not during replay. Replay reads and deletes — size can only decrease. Cache `_total_size_mb` after enforcement and invalidate on next write.

### Pitfall 5: Buffer Directory Not Created at Startup

**What goes wrong:** First write fails with `FileNotFoundError` because `data/cloud_buffer/YYYY/MM/DD/` doesn't exist.

**Why it happens:** `data/cloud_buffer/` is not created by the installer or any other module.

**How to avoid:** `BufferManager.__init__` calls `self._buffer_dir.mkdir(parents=True, exist_ok=True)`. Each new hourly file also does `directory.mkdir(parents=True, exist_ok=True)` before `open()` — same as `JsonlEventWriter._rotate()`.

### Pitfall 6: Replay Starts Before Buffer Write File is Closed (Flush Loss)

**What goes wrong:** Reconnect fires, replay starts immediately. The current write file has unflushed data (last partial sync period). Replay reads the file before `_sync()` is called. Unflushed lines are skipped because they're not in the kernel buffer yet.

**Why it happens:** `BufferManager` writes to `BufferedWriter`; fsync happens every 100 writes. If disconnect drops mid-buffer (say, 50 writes in), the file has 50 unsynced lines when replay starts.

**How to avoid:** When `BufferedCloudLoop` detects reconnect (via `status_queue` or by overriding `_on_connect` notification), call `self._buffer.flush()` (or `close()`) before starting replay. Replay then sees all buffered lines including the unsynced ones.

---

## Code Examples

### JSONL Crash-Safe Read (from logger pattern)

```python
# Source: src/logger/python/src/ems_logger/event_writer.py — JsonlEventWriter.read_events()
def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    """Read buffer JSONL file, skipping truncated last lines (crash recovery)."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping unparseable buffer line: %.80s", line)
    return records
```

### File Age from Path (from logger cleanup.py pattern)

```python
# Source: src/logger/python/src/ems_logger/cleanup.py — _parse_date_from_parquet_path()
def _file_datetime_from_path(path: Path) -> datetime | None:
    """Extract datetime from buffer/{year}/{month}/{day}/cloud_{HH}.jsonl."""
    try:
        hour: int = int(path.stem.split("_")[1])  # "cloud_14" -> 14
        day: int = int(path.parent.name)
        month: int = int(path.parent.parent.name)
        year: int = int(path.parent.parent.parent.name)
        return datetime(year, month, day, hour, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
```

### Retention: Delete Oldest Until Under max_mb

```python
# Pattern from cleanup.py _delete_files() — adapted for MB limit instead of disk %
def _enforce_size_limit(
    self,
    files_sorted_oldest_first: list[Path],
    max_mb: float,
) -> None:
    for path in files_sorted_oldest_first:
        if self._total_size_mb() <= max_mb:
            break
        try:
            path.unlink()
            _remove_empty_parents(path, self._buffer_dir)
            logger.info("Buffer retention: deleted %s", path)
        except OSError as exc:
            logger.warning("Failed to delete buffer file %s: %s", path, exc)
```

### Replay Throttle Loop (10 msg/s)

```python
# Pattern: asyncio.sleep for throttle, publisher method for delivery
async def _buffer_replay_task(self) -> None:
    _REPLAY_INTERVAL: float = 1.0 / 10.0  # 0.1s per message = 10 msg/s
    while not self._stop_event.is_set():
        if not self._publisher.connected:
            await asyncio.sleep(1.0)
            continue

        replayed_this_cycle: int = 0
        for file_path in self._buffer._all_files_sorted():
            if not self._publisher.connected:
                break
            records: list[dict] = _read_jsonl_file(file_path)
            for record in records:
                if not self._publisher.connected:
                    break
                msg_type: str = record.get("type", "telemetry")
                topic: str = record.get("topic", "telemetry")
                payload: dict = record.get("payload", {})
                if msg_type == "telemetry":
                    self._publisher.publish_telemetry(payload)
                else:
                    self._publisher.publish_event(topic, payload)
                replayed_this_cycle += 1
                await asyncio.sleep(_REPLAY_INTERVAL)
            else:
                # All records published — safe to delete
                file_path.unlink()
                _remove_empty_parents(file_path, self._buffer._buffer_dir)
                continue
            break  # inner loop broke — connection dropped

        if replayed_this_cycle == 0:
            await asyncio.sleep(5.0)
        self._publish_buffer_status()
```

### __main__.py Wiring (with BufferManager)

```python
# Extend existing __main__.py run() to construct BufferManager + BufferedCloudLoop
buffer_cfg: dict = config.get("offline_buffer", {})
if buffer_cfg.get("enabled", False):
    buffer_dir: Path = Path("data/cloud_buffer")  # or env override
    buffer_mgr: BufferManager = BufferManager(
        buffer_dir=buffer_dir,
        max_hours=buffer_cfg["max_hours"],
        max_mb=buffer_cfg["max_mb"],
    )
    loop_obj = BufferedCloudLoop(config, publisher, buffer_mgr, ...)
else:
    loop_obj = CloudLoop(config, publisher, ...)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Queue in memory during outage | Disk-backed JSONL buffer | Phase 23 decision | Survives process restart during outage |
| Publish all buffered data immediately | 10 msg/s throttle | Phase 23 decision | Prevents broker overload / connection reset |

**Deprecated/outdated:**
- `_periodic_publish` connection guard (`if not self._publisher.connected: continue`): This guard was a Phase 22 placeholder stub; Phase 23 removes it in `BufferedCloudLoop` override.

---

## Open Questions

1. **flush() on reconnect — where does the trigger come from?**
   - What we know: `publisher._on_connect` puts `"connected"` on `status_queue`. `CloudLoop` polls `status_queue` in `_command_dispatcher_loop` (no — it only polls `command_queue`). Status queue is not currently consumed by CloudLoop.
   - What's unclear: How does `BufferedCloudLoop` learn that connection was restored to trigger `buffer.flush_current_file()` before replay starts?
   - Recommendation: Override `_publish_cloud_status()` in `BufferedCloudLoop` to detect `state == "connected"` and set an asyncio Event (`self._reconnect_event`) that `_buffer_replay_task` waits on. Or poll `publisher.connected` directly — since asyncio is single-threaded and `publisher._connected` is set by the paho thread (boolean assignment is atomic in CPython), polling is safe.

2. **Buffer directory as env var override (for tests)**
   - What we know: The CONTEXT.md says "The buffer path should be configurable for testing (temp directory in tests)." `__main__.py` already uses env vars for all ZMQ endpoints.
   - What's unclear: Whether to add `EMS_CLOUD_BUFFER_DIR` env var or pass via constructor.
   - Recommendation: Add `EMS_CLOUD_BUFFER_DIR` env var in `__main__.py` (consistent with existing pattern), pass as `buffer_dir` to `BufferManager`.

3. **Event loop for _zmq_event_forwarder during offline: missed events**
   - What we know: `_zmq_event_forwarder` only calls `_do_publish_event` when `publisher.connected`. The guard is at lines 322-326 of loop.py. But `_do_publish_event` is the Phase 23 hook — events will only reach the buffer if this guard is removed in the subclass override.
   - Recommendation: `BufferedCloudLoop` must also override `_zmq_event_forwarder` to remove the `if self._publisher.connected:` guard, replacing it with always calling `self._do_publish_event(topic, event_payload)` (which routes to buffer when offline).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (used for all EMS Python modules) |
| Config file | `tests/conftest.py` (project root) |
| Quick run command | `cd /home/overlord/EMS && uv run pytest tests/test_cloud_manager.py -x -q` |
| Full suite command | `cd /home/overlord/EMS && uv run pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLOUD-04 | Buffer writes JSONL when offline | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer" -x` | ❌ Wave 0 |
| CLOUD-04 | Hourly file rotation | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer_rotation" -x` | ❌ Wave 0 |
| CLOUD-04 | max_hours retention enforced (delete old files) | unit | `uv run pytest tests/test_cloud_manager.py -k "retention_hours" -x` | ❌ Wave 0 |
| CLOUD-04 | max_mb retention enforced (delete oldest on overflow) | unit | `uv run pytest tests/test_cloud_manager.py -k "retention_mb" -x` | ❌ Wave 0 |
| CLOUD-04 | Crash recovery: truncated last line skipped | unit | `uv run pytest tests/test_cloud_manager.py -k "crash_recovery" -x` | ❌ Wave 0 |
| CLOUD-05 | Replay drains FIFO (oldest file first) | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_fifo" -x` | ❌ Wave 0 |
| CLOUD-05 | Replay throttled at 10 msg/s | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_throttle" -x` | ❌ Wave 0 |
| CLOUD-05 | Replay stops and resumes on disconnect | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_interrupt" -x` | ❌ Wave 0 |
| CLOUD-05 | Buffer progress published on ZMQ status | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer_status" -x` | ❌ Wave 0 |
| CLOUD-05 | File deleted after full replay | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_cleanup" -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_cloud_manager.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_cloud_manager.py` — Add buffer test class (CLOUD-04, CLOUD-05); file exists, add new class `TestBufferManager` and `TestBufferedCloudLoop`
- [ ] `src/cloud_manager/src/ems_cloud_manager/buffer.py` — Does not exist; Wave 0 creates it

*(No framework gaps — pytest + conftest already present in existing test infrastructure)*

---

## Sources

### Primary (HIGH confidence)

- Source code: `src/cloud_manager/src/ems_cloud_manager/loop.py` — Phase 23 hook points `_do_publish_telemetry` / `_do_publish_event` confirmed at lines 195-215; connection guard at line 269
- Source code: `src/cloud_manager/src/ems_cloud_manager/publisher.py` — `status_queue`, `command_queue`, `_connected` flag, paho thread model documented in module docstring
- Source code: `src/logger/python/src/ems_logger/event_writer.py` — JSONL append pattern, `_SYNC_EVERY = 100`, crash recovery `read_events()` static method
- Source code: `src/logger/python/src/ems_logger/cleanup.py` — Path-date extraction functions, `_remove_empty_parents()`, `_delete_files()` patterns
- Source code: `config/cloud_config.yaml` + `config/schemas/cloud_config.schema.json` — `offline_buffer` section already defined with `enabled`, `max_hours`, `max_mb`
- Source code: `src/cloud_manager/src/ems_cloud_manager/__main__.py` — Env var override pattern for ZMQ endpoints; `BufferManager` wiring location
- Source code: `tests/test_cloud_manager.py` — Existing fixture `cloud_config_dict` sets `offline_buffer.enabled: False`; test infrastructure confirmed present

### Secondary (MEDIUM confidence)

- `.planning/phases/23-offline-buffer/23-CONTEXT.md` — All implementation decisions locked; replicated verbatim in User Constraints section above
- `.planning/REQUIREMENTS.md` — CLOUD-04 and CLOUD-05 requirement text confirmed

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all code read directly from source; no external dependencies needed
- Architecture: HIGH — integration points confirmed in loop.py comments + source; subclass pattern matches prior Phase 22 design intent
- Pitfalls: HIGH — identified from direct code reading (connection guard pitfall is in loop.py line 269; flush-before-replay from BufferedWriter semantics)
- Open questions: MEDIUM — two integration questions (reconnect signal, event forwarder guard) require design decision; both have clear recommended resolutions

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable stdlib patterns; no fast-moving dependencies)
