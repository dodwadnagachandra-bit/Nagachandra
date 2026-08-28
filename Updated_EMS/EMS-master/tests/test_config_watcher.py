"""Tests for ConfigWatcher (inotify hot-reload) and SOCK_CONFIG IPC contract.

Covers:
  - CONF-02: Hot-reload detects file change via inotify IN_CLOSE_WRITE with 500ms debounce
  - CONF-05: system_config hot-reload is rejected (not in HOT_RELOAD_CONFIGS)

Run from repo root: uv run pytest tests/test_config_watcher.py -v
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


class TestSOCKConfigContract:
    """Verify SOCK_CONFIG defined correctly in Python IPC contract."""

    def test_sock_config_defined(self) -> None:
        """SOCK_CONFIG is defined in ipc.py."""
        from ems_common.ipc import SOCK_CONFIG

        assert SOCK_CONFIG == "ipc:///run/ems/config.sock"

    def test_sock_config_in_all_sockets(self) -> None:
        """SOCK_CONFIG follows the ipc:///run/ems/ convention."""
        from ems_common.ipc import SOCK_CONFIG

        assert SOCK_CONFIG.startswith("ipc:///run/ems/")


class TestConfigWatcherDebounce:
    """Test inotify watcher with debounced callbacks."""

    @pytest.fixture
    def config_dir(self, tmp_path: Path) -> Path:
        """Create a temporary config directory with hot-reloadable files."""
        d: Path = tmp_path / "config"
        d.mkdir()
        (d / "control_config.yaml").write_text("key: value1\n")
        (d / "alarms_config.yaml").write_text("key: value1\n")
        (d / "schedule_config.yaml").write_text("key: value1\n")
        (d / "system_config.yaml").write_text("key: value1\n")
        return d

    @pytest.mark.asyncio
    async def test_detects_close_write_on_yaml(self, config_dir: Path) -> None:
        """Watcher detects IN_CLOSE_WRITE on a hot-reloadable .yaml file."""
        from ems_config_manager.watcher import ConfigWatcher

        changed_names: list[str] = []

        async def on_change(name: str) -> None:
            changed_names.append(name)

        watcher = ConfigWatcher(config_dir=config_dir, on_change=on_change)
        task: asyncio.Task = asyncio.create_task(watcher.watch_loop())

        try:
            # Allow watcher to start
            await asyncio.sleep(0.1)

            # Write to a hot-reloadable config
            (config_dir / "control_config.yaml").write_text("key: value2\n")

            # Wait for debounce (500ms) + buffer
            await asyncio.sleep(1.0)

            assert "control_config" in changed_names
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_ignores_non_yaml_files(self, config_dir: Path) -> None:
        """Watcher ignores non-.yaml files."""
        from ems_config_manager.watcher import ConfigWatcher

        changed_names: list[str] = []

        async def on_change(name: str) -> None:
            changed_names.append(name)

        watcher = ConfigWatcher(config_dir=config_dir, on_change=on_change)
        task: asyncio.Task = asyncio.create_task(watcher.watch_loop())

        try:
            await asyncio.sleep(0.1)
            (config_dir / "readme.txt").write_text("hello\n")
            await asyncio.sleep(1.0)
            assert len(changed_names) == 0
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_ignores_dot_prefixed_temp_files(self, config_dir: Path) -> None:
        """Watcher ignores dot-prefixed temp files (editor swap files)."""
        from ems_config_manager.watcher import ConfigWatcher

        changed_names: list[str] = []

        async def on_change(name: str) -> None:
            changed_names.append(name)

        watcher = ConfigWatcher(config_dir=config_dir, on_change=on_change)
        task: asyncio.Task = asyncio.create_task(watcher.watch_loop())

        try:
            await asyncio.sleep(0.1)
            (config_dir / ".control_config.yaml.swp").write_text("temp\n")
            await asyncio.sleep(1.0)
            assert len(changed_names) == 0
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_ignores_system_config(self, config_dir: Path) -> None:
        """Watcher ignores system_config.yaml changes (topology requires restart)."""
        from ems_config_manager.watcher import ConfigWatcher

        changed_names: list[str] = []

        async def on_change(name: str) -> None:
            changed_names.append(name)

        watcher = ConfigWatcher(config_dir=config_dir, on_change=on_change)
        task: asyncio.Task = asyncio.create_task(watcher.watch_loop())

        try:
            await asyncio.sleep(0.1)
            (config_dir / "system_config.yaml").write_text("key: changed\n")
            await asyncio.sleep(1.0)
            assert "system_config" not in changed_names
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_debounce_multiple_rapid_writes(self, config_dir: Path) -> None:
        """Multiple rapid writes to same file result in only one callback."""
        from ems_config_manager.watcher import ConfigWatcher

        changed_names: list[str] = []

        async def on_change(name: str) -> None:
            changed_names.append(name)

        watcher = ConfigWatcher(config_dir=config_dir, on_change=on_change)
        task: asyncio.Task = asyncio.create_task(watcher.watch_loop())

        try:
            await asyncio.sleep(0.1)

            # Rapid writes within debounce window
            for i in range(5):
                (config_dir / "alarms_config.yaml").write_text(f"key: value{i}\n")
                await asyncio.sleep(0.05)

            # Wait for debounce to fire
            await asyncio.sleep(1.0)

            # Should only have one callback despite 5 writes
            assert changed_names.count("alarms_config") == 1
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestConfigWatcherHotReloadSet:
    """Verify HOT_RELOAD_CONFIGS set contains correct configs."""

    def test_hot_reload_configs_set(self) -> None:
        """HOT_RELOAD_CONFIGS contains exactly control, alarms, schedule."""
        from ems_config_manager.watcher import ConfigWatcher

        expected: set[str] = {"control_config", "alarms_config", "schedule_config"}
        assert ConfigWatcher.HOT_RELOAD_CONFIGS == expected

    def test_debounce_value(self) -> None:
        """DEBOUNCE_S is 0.5 seconds."""
        from ems_config_manager.watcher import ConfigWatcher

        assert ConfigWatcher.DEBOUNCE_S == 0.5
