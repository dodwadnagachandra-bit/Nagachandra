"""Tests for ConfigManager hot-reload, ZMQ config API, and service entry point.

Covers:
  - CONF-02: Hot-reload backup, diff, and event publishing
  - CONF-04: Backup created before hot-reload (keep last 5)
  - CONF-05: Failed hot-reload rejects change, keeps current config
  - CONF-08: Config reload event includes full config + diff
  - ZMQ REQ/REP serves get_config and get_value from cache

Run from repo root: uv run pytest tests/test_config_hot_reload.py -v
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import msgpack
import pytest
import yaml
import zmq
import zmq.asyncio

from ems_common.ipc import (
    SOCK_CONFIG,
    decode_command_response,
    encode_command_request,
)


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = REPO_ROOT / "config"
SCHEMAS_DIR: Path = CONFIG_DIR / "schemas"


def _copy_config_tree(src_dir: Path, dst_dir: Path) -> None:
    """Copy all YAML configs from src to dst."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for yaml_file in src_dir.glob("*.yaml"):
        (dst_dir / yaml_file.name).write_text(yaml_file.read_text())


def _copy_schemas(dst_dir: Path) -> None:
    """Copy all JSON schemas to dst_dir."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for schema_file in SCHEMAS_DIR.glob("*.schema.json"):
        (dst_dir / schema_file.name).write_text(schema_file.read_text())


def _create_test_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create test environment with configs and schemas."""
    config_dir: Path = tmp_path / "config"
    schema_dir: Path = tmp_path / "schemas"
    _copy_config_tree(CONFIG_DIR, config_dir)
    _copy_schemas(schema_dir)
    return config_dir, schema_dir


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create test environment with all configs and schemas."""
    return _create_test_env(tmp_path)


@pytest.fixture
def loaded_manager(test_env: tuple[Path, Path]) -> Any:
    """Create a ConfigManager with all configs loaded."""
    from ems_config_manager.manager import ConfigManager

    config_dir, schema_dir = test_env
    cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
    cm.load_all()
    return cm


class TestZmqConfigAPI:
    """Test ZMQ REQ/REP config query API."""

    @pytest.mark.asyncio
    async def test_get_config_via_zmq(self, loaded_manager: Any, tmp_path: Path) -> None:
        """ZMQ REQ get_config returns full config dict."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager

        # Use unique IPC path per test
        sock_path: str = f"ipc://{tmp_path}/config_test.sock"

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        server_task: asyncio.Task = asyncio.create_task(
            cm.serve_queries(ctx, bind_addr=sock_path)
        )

        try:
            await asyncio.sleep(0.1)

            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(sock_path)

            # Send get_config request
            request: bytes = encode_command_request("get_config", {"name": "pcs_config"})
            await req_sock.send(request)
            reply: bytes = await asyncio.wait_for(req_sock.recv(), timeout=2.0)

            response: dict = decode_command_response(reply)
            assert response["status"] == "ok"
            assert response["result"] is not None
            assert "connection" in response["result"]

            req_sock.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_get_value_via_zmq(self, loaded_manager: Any, tmp_path: Path) -> None:
        """ZMQ REQ get_value returns value at dotted path."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        sock_path: str = f"ipc://{tmp_path}/config_test.sock"

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        server_task: asyncio.Task = asyncio.create_task(
            cm.serve_queries(ctx, bind_addr=sock_path)
        )

        try:
            await asyncio.sleep(0.1)

            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(sock_path)

            request: bytes = encode_command_request(
                "get_value", {"name": "pcs_config", "path": "connection.protocol"}
            )
            await req_sock.send(request)
            reply: bytes = await asyncio.wait_for(req_sock.recv(), timeout=2.0)

            response: dict = decode_command_response(reply)
            assert response["status"] == "ok"
            assert response["result"] == "rtu"

            req_sock.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_get_value_missing_path_returns_error(
        self, loaded_manager: Any, tmp_path: Path
    ) -> None:
        """ZMQ REQ get_value with missing path returns error response."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        sock_path: str = f"ipc://{tmp_path}/config_test.sock"

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        server_task: asyncio.Task = asyncio.create_task(
            cm.serve_queries(ctx, bind_addr=sock_path)
        )

        try:
            await asyncio.sleep(0.1)

            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(sock_path)

            request: bytes = encode_command_request(
                "get_value", {"name": "pcs_config", "path": "nonexistent.field"}
            )
            await req_sock.send(request)
            reply: bytes = await asyncio.wait_for(req_sock.recv(), timeout=2.0)

            response: dict = decode_command_response(reply)
            assert response["status"] == "error"
            assert response["error_msg"] is not None
            assert "nonexistent" in response["error_msg"]

            req_sock.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(
        self, loaded_manager: Any, tmp_path: Path
    ) -> None:
        """ZMQ REQ with unknown action returns error response."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        sock_path: str = f"ipc://{tmp_path}/config_test.sock"

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        server_task: asyncio.Task = asyncio.create_task(
            cm.serve_queries(ctx, bind_addr=sock_path)
        )

        try:
            await asyncio.sleep(0.1)

            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(sock_path)

            request: bytes = encode_command_request("bogus_action", {})
            await req_sock.send(request)
            reply: bytes = await asyncio.wait_for(req_sock.recv(), timeout=2.0)

            response: dict = decode_command_response(reply)
            assert response["status"] == "error"
            assert "unknown" in response["error_msg"].lower() or "bogus" in response["error_msg"].lower()

            req_sock.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()


class TestHotReloadBackup:
    """Test hot-reload backup, diff, and event publishing."""

    @pytest.mark.asyncio
    async def test_backup_created_before_apply(
        self, loaded_manager: Any, test_env: tuple[Path, Path]
    ) -> None:
        """Hot-reload creates backup in config/backups/ before applying."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        config_dir: Path = test_env[0]

        # Modify a hot-reloadable config
        control_path: Path = config_dir / "control_config.yaml"
        data: dict = yaml.safe_load(control_path.read_text())
        data["soc_limits"]["charge_cutoff_pct"] = 90
        control_path.write_text(yaml.dump(data))

        # Trigger hot-reload
        await cm.handle_reload("control_config")

        # Check backup was created
        backup_dir: Path = config_dir / "backups"
        assert backup_dir.exists()
        backups: list[Path] = list(backup_dir.glob("control_config.*.yaml"))
        assert len(backups) >= 1

    @pytest.mark.asyncio
    async def test_backup_cleanup_keeps_5(
        self, loaded_manager: Any, test_env: tuple[Path, Path]
    ) -> None:
        """Backup cleanup keeps only last 5 per config name."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        config_dir: Path = test_env[0]

        # Trigger 7 reloads
        for i in range(7):
            control_path: Path = config_dir / "control_config.yaml"
            data: dict = yaml.safe_load(control_path.read_text())
            data["soc_limits"]["charge_cutoff_pct"] = 80 + i
            control_path.write_text(yaml.dump(data))
            await cm.handle_reload("control_config")
            await asyncio.sleep(0.01)  # Ensure different timestamps

        backup_dir: Path = config_dir / "backups"
        backups: list[Path] = sorted(backup_dir.glob("control_config.*.yaml"))
        assert len(backups) == 5

    @pytest.mark.asyncio
    async def test_reload_publishes_event_with_diff(
        self, loaded_manager: Any, test_env: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Hot-reload publishes config_reload event with full config and diff."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        config_dir: Path = test_env[0]

        # Set up a ZMQ PULL to capture the event
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        pull_path: str = f"ipc://{tmp_path}/logger_test.sock"
        pull_sock: zmq.asyncio.Socket = ctx.socket(zmq.PULL)
        pull_sock.bind(pull_path)

        # Connect ConfigManager's push socket
        cm._init_push_socket(ctx, push_addr=pull_path)

        try:
            # Get original value
            original_cutoff: Any = cm.get_value(
                "control_config", "soc_limits.charge_cutoff_pct"
            )

            # Modify control_config
            control_path: Path = config_dir / "control_config.yaml"
            data: dict = yaml.safe_load(control_path.read_text())
            new_cutoff: int = 85  # Valid value in range 80-100
            data["soc_limits"]["charge_cutoff_pct"] = new_cutoff
            control_path.write_text(yaml.dump(data))

            # Trigger reload
            await cm.handle_reload("control_config")

            # Read the published event
            event_data: bytes = await asyncio.wait_for(pull_sock.recv(), timeout=2.0)
            event: dict = msgpack.unpackb(event_data, raw=False)

            assert event["event_type"] == "config_reload"
            assert event["src"] == "config_manager"
            assert event["data"]["name"] == "control_config"
            assert "config" in event["data"]
            assert "diff" in event["data"]

            # Verify cache was updated
            assert cm.get_value("control_config", "soc_limits.charge_cutoff_pct") == new_cutoff
        finally:
            pull_sock.close()
            cm._close_push_socket()
            ctx.term()

    @pytest.mark.asyncio
    async def test_failed_reload_keeps_current_config(
        self, loaded_manager: Any, test_env: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Failed hot-reload rejects change, keeps current config, publishes error."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        config_dir, schema_dir = test_env

        # Set up PULL to capture error event
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        pull_path: str = f"ipc://{tmp_path}/logger_err.sock"
        pull_sock: zmq.asyncio.Socket = ctx.socket(zmq.PULL)
        pull_sock.bind(pull_path)
        cm._init_push_socket(ctx, push_addr=pull_path)

        try:
            # Remember current config
            original_config: dict = cm.get_config("control_config").copy()

            # Write invalid YAML that will fail schema validation
            control_path: Path = config_dir / "control_config.yaml"
            data: dict = yaml.safe_load(control_path.read_text())
            data["soc_limits"]["charge_cutoff_pct"] = -999  # Invalid: out of valid range
            control_path.write_text(yaml.dump(data))

            # Trigger reload -- should fail validation
            await cm.handle_reload("control_config")

            # Cache should be unchanged
            current_config: dict = cm.get_config("control_config")
            assert current_config == original_config

            # Error event should be published
            event_data: bytes = await asyncio.wait_for(pull_sock.recv(), timeout=2.0)
            event: dict = msgpack.unpackb(event_data, raw=False)
            assert event["severity"] == "error"
            assert event["event_type"] == "config_reload_failed"
        finally:
            pull_sock.close()
            cm._close_push_socket()
            ctx.term()

    @pytest.mark.asyncio
    async def test_diff_contains_changed_values(
        self, loaded_manager: Any, test_env: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Diff in reload event contains correct old/new values."""
        from ems_config_manager.manager import ConfigManager

        cm: ConfigManager = loaded_manager
        config_dir: Path = test_env[0]

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        pull_path: str = f"ipc://{tmp_path}/logger_diff.sock"
        pull_sock: zmq.asyncio.Socket = ctx.socket(zmq.PULL)
        pull_sock.bind(pull_path)
        cm._init_push_socket(ctx, push_addr=pull_path)

        try:
            original_cutoff: Any = cm.get_value(
                "control_config", "soc_limits.charge_cutoff_pct"
            )

            control_path: Path = config_dir / "control_config.yaml"
            data: dict = yaml.safe_load(control_path.read_text())
            new_cutoff: int = 88  # Valid value in range 80-100
            data["soc_limits"]["charge_cutoff_pct"] = new_cutoff
            control_path.write_text(yaml.dump(data))

            await cm.handle_reload("control_config")

            event_data: bytes = await asyncio.wait_for(pull_sock.recv(), timeout=2.0)
            event: dict = msgpack.unpackb(event_data, raw=False)

            diff: dict = event["data"]["diff"]
            assert "changed" in diff
            # The diff should contain the changed field
            changed: dict = diff["changed"]
            assert "soc_limits.charge_cutoff_pct" in changed
            assert changed["soc_limits.charge_cutoff_pct"]["old"] == original_cutoff
            assert changed["soc_limits.charge_cutoff_pct"]["new"] == new_cutoff
        finally:
            pull_sock.close()
            cm._close_push_socket()
            ctx.term()


class TestServiceEntryPoint:
    """Test __main__.py service entry point."""

    def test_main_module_importable(self) -> None:
        """__main__.py module is importable."""
        import ems_config_manager.__main__  # noqa: F401

    def test_main_has_run_function(self) -> None:
        """__main__.py has a main() or run() entry point."""
        import ems_config_manager.__main__ as main_mod

        assert hasattr(main_mod, "main") or hasattr(main_mod, "run")
