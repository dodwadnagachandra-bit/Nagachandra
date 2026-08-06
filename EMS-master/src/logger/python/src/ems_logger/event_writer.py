"""JSONL event writer -- ZMQ PULL consumer with daily file rotation.

Receives structured events from all modules via ZMQ PULL, appends to
daily JSONL files at data/events/{year}/{month:02d}/events_{YYYYMMDD}.jsonl.

Crash recovery: read_events() skips lines that fail json.loads() (LOG-07).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from io import BufferedWriter
from pathlib import Path

import zmq
import zmq.asyncio

from ems_common.ipc import SOCK_LOGGER, decode_event
from ems_logger.config import LoggerConfig

logger: logging.Logger = logging.getLogger(__name__)

# Sync threshold -- fsync every N writes (not every write for performance)
_SYNC_EVERY: int = 100

# Periodic fsync interval in seconds
_PERIODIC_SYNC_S: float = 1.0


class JsonlEventWriter:
    """Append structured events to daily JSONL files with rotation.

    File path convention: data_dir/events/{year}/{month:02d}/events_{YYYYMMDD}.jsonl

    Args:
        data_dir: Root data directory (e.g. /mnt/ssd/ems/data).
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir: Path = Path(data_dir)
        self._current_date: str = ""
        self._file: BufferedWriter | None = None
        self._writes_since_sync: int = 0

    def append_event(self, event: dict) -> None:
        """Append a single event dict as compact JSON line.

        Determines date from event["ts"] (milliseconds since epoch).
        Rotates file if date changes.

        Args:
            event: Event dict with at minimum a "ts" field (epoch ms).
        """
        ts_ms: int = event.get("ts", 0)
        dt: datetime = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        date_str: str = dt.strftime("%Y%m%d")

        if date_str != self._current_date:
            self._rotate(date_str, dt)

        line: str = json.dumps(event, separators=(",", ":")) + "\n"
        assert self._file is not None
        self._file.write(line.encode("utf-8"))
        self._writes_since_sync += 1

        if self._writes_since_sync >= _SYNC_EVERY:
            self._sync()

    def _rotate(self, date_str: str, dt: datetime) -> None:
        """Close current file and open new one for the given date.

        Args:
            date_str: Date string in YYYYMMDD format.
            dt: Datetime object for directory structure.
        """
        if self._file is not None:
            self._sync()
            self._file.close()

        year: str = dt.strftime("%Y")
        month: str = dt.strftime("%m")
        directory: Path = self._data_dir / "events" / year / month
        directory.mkdir(parents=True, exist_ok=True)

        file_path: Path = directory / f"events_{date_str}.jsonl"
        self._file = open(file_path, "ab")  # noqa: SIM115
        self._current_date = date_str
        self._writes_since_sync = 0
        logger.info("Opened JSONL event file: %s", file_path)

    def _sync(self) -> None:
        """Flush buffer and fsync to disk."""
        if self._file is not None and not self._file.closed:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._writes_since_sync = 0

    def close(self) -> None:
        """Flush, fsync, and close the current file."""
        if self._file is not None and not self._file.closed:
            self._sync()
            self._file.close()
            self._file = None
        self._current_date = ""

    @staticmethod
    def read_events(path: Path) -> list[dict]:
        """Read a JSONL file, skipping lines that fail to parse.

        Crash recovery (LOG-07): truncated last lines from a crash
        are silently skipped.

        Args:
            path: Path to a .jsonl file.

        Returns:
            List of successfully parsed event dicts.
        """
        events: list[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping unparseable JSONL line: %.80s", line)
        return events


class EventConsumer:
    """ZMQ PULL event consumer -- binds socket, receives events, writes JSONL.

    Uses zmq.asyncio for non-blocking receive and periodic fsync timer.

    Args:
        config: LoggerConfig with storage.data_dir.
        zmq_ctx: zmq.asyncio.Context (shared or test-specific).
        endpoint: Override ZMQ endpoint (default: SOCK_LOGGER).
    """

    def __init__(
        self,
        config: LoggerConfig,
        zmq_ctx: zmq.asyncio.Context,
        endpoint: str | None = None,
    ) -> None:
        self._writer: JsonlEventWriter = JsonlEventWriter(
            Path(config.storage.data_dir)
        )
        self._endpoint: str = endpoint or SOCK_LOGGER
        self._socket: zmq.asyncio.Socket = zmq_ctx.socket(zmq.PULL)
        self._socket.bind(self._endpoint)
        self._closed: bool = False
        self._writes_pending: bool = False
        logger.info("EventConsumer bound to %s", self._endpoint)

    async def run(self) -> None:
        """Main async loop -- receive events and write to JSONL.

        Runs until close() is called or the task is cancelled.
        """
        poller: zmq.asyncio.Poller = zmq.asyncio.Poller()
        poller.register(self._socket, zmq.POLLIN)

        # Start periodic sync task
        sync_task: asyncio.Task = asyncio.create_task(self._periodic_sync())

        try:
            while not self._closed:
                try:
                    events = await asyncio.wait_for(
                        poller.poll(timeout=100), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                for sock, _ in events:
                    data: bytes = await sock.recv(zmq.NOBLOCK)
                    try:
                        event: dict = decode_event(data)
                        self._writer.append_event(event)
                        self._writes_pending = True
                    except Exception:
                        logger.exception("Failed to decode/write event")
        except asyncio.CancelledError:
            pass
        finally:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass

    async def _periodic_sync(self) -> None:
        """Flush writer every _PERIODIC_SYNC_S seconds if writes pending."""
        try:
            while not self._closed:
                await asyncio.sleep(_PERIODIC_SYNC_S)
                if self._writes_pending:
                    self._writer._sync()
                    self._writes_pending = False
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        """Close ZMQ socket and finalize JSONL file."""
        self._closed = True
        self._socket.close(linger=0)
        self._writer.close()
        logger.info("EventConsumer closed")
