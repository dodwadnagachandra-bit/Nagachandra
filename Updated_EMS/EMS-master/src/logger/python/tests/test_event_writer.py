"""Tests for JSONL event writer -- daily rotation, crash recovery, compact format.

Covers LOG-04 (event persistence) and LOG-07 (crash recovery).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
import zmq
import zmq.asyncio

from ems_common.ipc import encode_event
from ems_logger.config import LoggerConfig, StorageConfig
from ems_logger.event_writer import EventConsumer, JsonlEventWriter


def _make_event(
    ts_ms: int | None = None,
    src: str = "safety_manager",
    severity: str = "warning",
    event_type: str = "comm_fault",
    message: str = "BMS rack 0 timeout",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sample event dict."""
    return {
        "ts": ts_ms or int(time.time() * 1000),
        "src": src,
        "severity": severity,
        "event_type": event_type,
        "message": message,
        "data": data,
    }


class TestJsonlEventAppend:
    """Test basic JSONL append behaviour."""

    def test_jsonl_event_append(self, tmp_data_dir: Path) -> None:
        """Write 3 events, read back, verify all present with correct fields."""
        writer = JsonlEventWriter(tmp_data_dir)
        events: list[dict[str, Any]] = [
            _make_event(message=f"event {i}") for i in range(3)
        ]
        for ev in events:
            writer.append_event(ev)
        writer.close()

        # Find the written file
        jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
        assert len(jsonl_files) == 1

        read_back = JsonlEventWriter.read_events(jsonl_files[0])
        assert len(read_back) == 3
        for i, ev in enumerate(read_back):
            assert ev["message"] == f"event {i}"
            assert ev["src"] == "safety_manager"

    def test_jsonl_compact_format(self, tmp_data_dir: Path) -> None:
        """Verify no spaces in JSON output (separators=(',', ':'))."""
        writer = JsonlEventWriter(tmp_data_dir)
        writer.append_event(_make_event(data={"key": "value"}))
        writer.close()

        jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
        raw_line = jsonl_files[0].read_text().splitlines()[0]
        # Compact JSON has no spaces after : or ,
        assert ": " not in raw_line
        assert ", " not in raw_line

    def test_jsonl_newline_terminated(self, tmp_data_dir: Path) -> None:
        """Verify each line ends with newline."""
        writer = JsonlEventWriter(tmp_data_dir)
        writer.append_event(_make_event())
        writer.append_event(_make_event())
        writer.close()

        jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
        raw = jsonl_files[0].read_text()
        assert raw.endswith("\n")
        lines = raw.splitlines()
        assert len(lines) == 2


class TestJsonlDailyRotation:
    """Test daily file rotation."""

    def test_jsonl_daily_rotation(self, tmp_data_dir: Path) -> None:
        """Write events with timestamps on different dates, verify 2 files."""
        writer = JsonlEventWriter(tmp_data_dir)
        # Day 1: 2025-06-15 00:00:00 UTC
        ts1 = 1750003200000
        # Day 2: 2025-06-16 00:00:00 UTC
        ts2 = 1750089600000

        writer.append_event(_make_event(ts_ms=ts1))
        writer.append_event(_make_event(ts_ms=ts2))
        writer.close()

        jsonl_files = sorted(tmp_data_dir.rglob("*.jsonl"))
        assert len(jsonl_files) == 2

    def test_jsonl_directory_structure(self, tmp_data_dir: Path) -> None:
        """Verify events/{year}/{month}/ path convention."""
        writer = JsonlEventWriter(tmp_data_dir)
        # 2025-06-15
        ts = 1750003200000
        writer.append_event(_make_event(ts_ms=ts))
        writer.close()

        expected_dir = tmp_data_dir / "events" / "2025" / "06"
        assert expected_dir.is_dir()
        jsonl_files = list(expected_dir.glob("events_20250615.jsonl"))
        assert len(jsonl_files) == 1


class TestJsonlCrashRecovery:
    """Test crash recovery -- truncated line handling (LOG-07)."""

    def test_jsonl_crash_recovery_truncated_line(self, tmp_data_dir: Path) -> None:
        """Manually append truncated JSON to file, verify read_events skips it."""
        writer = JsonlEventWriter(tmp_data_dir)
        writer.append_event(_make_event())
        writer.close()

        # Find the file and append truncated JSON
        jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
        with open(jsonl_files[0], "a") as f:
            f.write('{"ts":123,"src":"test","trunca\n')

        # read_events should skip the bad line, return only the good one
        events = JsonlEventWriter.read_events(jsonl_files[0])
        assert len(events) == 1
        assert events[0]["src"] == "safety_manager"


class TestEventConsumer:
    """Test ZMQ PULL event consumer."""

    @pytest.fixture
    def zmq_endpoint(self, tmp_path: Path) -> str:
        """Generate a unique IPC endpoint for test isolation."""
        return f"ipc://{tmp_path}/test_logger.sock"

    @pytest.fixture
    def logger_config(self, tmp_data_dir: Path) -> LoggerConfig:
        """LoggerConfig pointing at tmp data dir."""
        return LoggerConfig(storage=StorageConfig(data_dir=str(tmp_data_dir)))

    @pytest.mark.asyncio
    async def test_event_consumer_receives_zmq(
        self, tmp_data_dir: Path, logger_config: LoggerConfig, zmq_endpoint: str
    ) -> None:
        """Send events via ZMQ PUSH to EventConsumer, verify JSONL file contains them."""
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            consumer = EventConsumer(logger_config, ctx, endpoint=zmq_endpoint)
            task: asyncio.Task = asyncio.create_task(consumer.run())

            # Give consumer time to bind
            await asyncio.sleep(0.1)

            # Send 3 events via PUSH
            push_sock: zmq.asyncio.Socket = ctx.socket(zmq.PUSH)
            push_sock.connect(zmq_endpoint)

            ts_ms: int = int(time.time() * 1000)
            for i in range(3):
                data: bytes = encode_event(
                    timestamp_ms=ts_ms + i,
                    source="test",
                    severity="info",
                    event_type="test_event",
                    message=f"msg {i}",
                )
                await push_sock.send(data)

            # Give consumer time to process
            await asyncio.sleep(0.3)

            # Shutdown
            consumer.close()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            push_sock.close(linger=0)

            # Verify JSONL file
            jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
            assert len(jsonl_files) >= 1
            events = JsonlEventWriter.read_events(jsonl_files[0])
            assert len(events) == 3
            assert events[0]["message"] == "msg 0"
            assert events[2]["message"] == "msg 2"
        finally:
            ctx.term()

    @pytest.mark.asyncio
    async def test_event_consumer_graceful_shutdown(
        self, tmp_data_dir: Path, logger_config: LoggerConfig, zmq_endpoint: str
    ) -> None:
        """Start consumer, send event, close, verify no data loss."""
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            consumer = EventConsumer(logger_config, ctx, endpoint=zmq_endpoint)
            task: asyncio.Task = asyncio.create_task(consumer.run())

            await asyncio.sleep(0.1)

            push_sock: zmq.asyncio.Socket = ctx.socket(zmq.PUSH)
            push_sock.connect(zmq_endpoint)

            data: bytes = encode_event(
                timestamp_ms=int(time.time() * 1000),
                source="shutdown_test",
                severity="info",
                event_type="test",
                message="before shutdown",
            )
            await push_sock.send(data)
            await asyncio.sleep(0.2)

            # Graceful shutdown
            consumer.close()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            push_sock.close(linger=0)

            # Verify event was persisted
            jsonl_files = list(tmp_data_dir.rglob("*.jsonl"))
            assert len(jsonl_files) >= 1
            events = JsonlEventWriter.read_events(jsonl_files[0])
            assert len(events) == 1
            assert events[0]["message"] == "before shutdown"
        finally:
            ctx.term()
