"""ConfigManager: core config loading, validation, serving, and hot-reload.

Loads all 14 YAML configs at startup, validates against JSON Schema,
checks schema version, and serves from in-memory cache. Fail-fast on
any validation error -- the EMS refuses to start with invalid config.

Hot-reload support: handle_reload() creates backup, validates new config,
computes diff, and publishes config_reload event via ZMQ PUSH.

ZMQ REQ/REP: serve_queries() handles get_config and get_value requests.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import msgpack
import yaml
import zmq
import zmq.asyncio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ems_common.ipc import (
    SOCK_CONFIG,
    SOCK_CONFIG_PUB,
    SOCK_LOGGER,
    STATUS_ERROR,
    STATUS_OK,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    TOPIC_CONFIG_RELOAD,
    decode_command_request,
    encode_command_response,
    encode_event,
)
from ems_config_manager.overlay import load_with_profile

logger: logging.Logger = logging.getLogger(__name__)

# 10 mandatory core configs -- must always be present
CORE_CONFIGS: list[str] = [
    "system_config",
    "gpio_config",
    "control_config",
    "alarms_config",
    "schedule_config",
    "bms_config",
    "pcs_config",
    "network_config",
    "cloud_config",
    "hmi_config",
]

# 4 optional device configs -- loaded only if subsystem is declared
DEVICE_CONFIG_MAP: dict[str, str] = {
    "dg_config": "has_dg",
    "pv_config": "has_pv",
    "btms_config": "has_btms",
    "meter_config": "has_meter",
}

# Maximum number of backup files to keep per config name
MAX_BACKUPS: int = 5


def _format_validation_error(filename: str, error: ValidationError) -> str:
    """Format a ValidationError into a friendly prose message.

    Args:
        filename: The config filename being validated.
        error: The ValidationError from jsonschema.

    Returns:
        A human-readable error string.
    """
    path_parts: list[str] = list(error.absolute_path)
    if path_parts:
        path: str = " -> ".join(str(p) for p in path_parts)
    else:
        path = "(root)"

    value: object = error.instance
    if isinstance(value, dict):
        value_repr: str = "{...}"
    else:
        value_repr = repr(value)

    return (
        f"ERROR: In {filename}, field '{path}' is set to {value_repr}"
        f" but {error.message}."
    )


def _compute_diff(
    old: dict, new: dict, prefix: str = ""
) -> dict[str, dict]:
    """Compute recursive diff between two config dicts.

    Returns dict with 'changed', 'added', 'removed' keys, each mapping
    dotted paths to values.

    Args:
        old: Previous config dict.
        new: New config dict.
        prefix: Dotted path prefix for recursive calls.

    Returns:
        Diff dict with changed/added/removed sections.
    """
    changed: dict[str, dict[str, Any]] = {}
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}

    all_keys: set[str] = set(old.keys()) | set(new.keys())

    for key in sorted(all_keys):
        full_key: str = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"

        if key not in old:
            added[full_key] = new[key]
        elif key not in new:
            removed[full_key] = old[key]
        elif isinstance(old[key], dict) and isinstance(new[key], dict):
            sub_diff: dict[str, dict] = _compute_diff(old[key], new[key], full_key)
            changed.update(sub_diff.get("changed", {}))
            added.update(sub_diff.get("added", {}))
            removed.update(sub_diff.get("removed", {}))
        elif old[key] != new[key]:
            changed[full_key] = {"old": old[key], "new": new[key]}

    return {"changed": changed, "added": added, "removed": removed}


class ConfigManager:
    """Manages EMS configuration: load, validate, and serve from cache.

    All configs are loaded at startup and served from in-memory cache.
    Invalid config causes fail-fast (sys.exit).

    Args:
        config_dir: Path to the config directory containing YAML files.
        schema_dir: Path to the directory containing JSON Schema files.
        profile: Optional deployment profile name for overlay loading.
    """

    def __init__(
        self,
        config_dir: Path,
        schema_dir: Path,
        profile: str | None = None,
    ) -> None:
        self._config_dir: Path = config_dir
        self._schema_dir: Path = schema_dir
        self._profile: str | None = profile
        self._configs: dict[str, dict] = {}
        self._schemas: dict[str, dict] = {}
        self._push_sock: zmq.asyncio.Socket | None = None
        self._pub_sock: zmq.asyncio.Socket | None = None

    # ------------------------------------------------------------------
    # Schema loading and validation
    # ------------------------------------------------------------------

    def _load_schema(self, name: str) -> dict:
        """Load and cache a JSON Schema by config name.

        Args:
            name: Config name (e.g., "system_config").

        Returns:
            Parsed JSON Schema dict.
        """
        if name not in self._schemas:
            schema_path: Path = self._schema_dir / f"{name}.schema.json"
            if not schema_path.exists():
                self._fail(f"Schema file not found: {schema_path}")
            with schema_path.open("r") as fh:
                self._schemas[name] = json.load(fh)
        return self._schemas[name]

    def _check_schema_version(self, name: str, data: dict) -> None:
        """Check _schema_version field matches schema expectation.

        If mismatched, prints migration guidance and fails fast.

        Args:
            name: Config name for error messages.
            data: Parsed YAML config data.
        """
        schema: dict = self._load_schema(name)
        schema_props: dict = schema.get("properties", {})
        sv_def: dict = schema_props.get("_schema_version", {})
        expected_version: str | None = sv_def.get("const")

        if expected_version is None:
            return  # Schema doesn't define version constraint

        actual_version: Any = data.get("_schema_version")

        if actual_version != expected_version:
            self._fail(
                f"Schema version mismatch in {name}.yaml: "
                f"found '{actual_version}', expected '{expected_version}'.\n"
                f"  Migration guidance: Update the _schema_version field in "
                f"{name}.yaml to '{expected_version}'.\n"
                f"  If upgrading from an older schema version, review the "
                f"changelog for breaking changes and update config fields "
                f"accordingly before changing the version."
            )

    def _validate_config(self, name: str, data: dict) -> list[ValidationError]:
        """Validate config data against its JSON Schema.

        Args:
            name: Config name for error messages.
            data: Parsed YAML config data.

        Returns:
            List of validation errors (empty if valid).
        """
        schema: dict = self._load_schema(name)
        validator: Draft202012Validator = Draft202012Validator(schema)
        return list(validator.iter_errors(data))

    def _validate_config_or_fail(self, name: str, data: dict) -> None:
        """Validate config data, fail-fast on error.

        Args:
            name: Config name for error messages.
            data: Parsed YAML config data.
        """
        errors: list[ValidationError] = self._validate_config(name, data)
        if errors:
            error_msgs: list[str] = [
                _format_validation_error(f"{name}.yaml", e) for e in errors
            ]
            self._fail(
                f"Validation failed for {name}.yaml:\n"
                + "\n".join(error_msgs)
            )

    def _load_single(self, name: str) -> dict:
        """Load, version-check, validate, and cache a single config.

        Args:
            name: Config name (e.g., "system_config").

        Returns:
            Parsed and validated config dict.
        """
        try:
            data: dict = load_with_profile(
                name, self._config_dir, self._profile
            )
        except FileNotFoundError as exc:
            self._fail(str(exc))

        self._check_schema_version(name, data)
        self._validate_config_or_fail(name, data)
        self._configs[name] = data
        return data

    # ------------------------------------------------------------------
    # Startup loading
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        """Load all configs at startup with fail-fast on any error.

        Loads system_config first to read subsystem flags, then loads
        remaining core and optional device configs.
        """
        # Load system_config first to read subsystem flags
        sys_data: dict = self._load_single("system_config")

        # Read subsystem presence flags (default to False if missing)
        subsystems: dict = sys_data.get("subsystems", {})

        # Load remaining core configs
        for name in CORE_CONFIGS:
            if name == "system_config":
                continue  # Already loaded
            self._load_single(name)

        # Load optional device configs based on subsystem flags
        for config_name, flag_name in DEVICE_CONFIG_MAP.items():
            if subsystems.get(flag_name, False):
                self._load_single(config_name)

    # ------------------------------------------------------------------
    # Config access (from cache)
    # ------------------------------------------------------------------

    def get_config(self, name: str) -> dict:
        """Get a loaded config by name.

        Args:
            name: Config name (e.g., "pcs_config").

        Returns:
            The full parsed config dict.

        Raises:
            KeyError: If the config was not loaded.
        """
        if name not in self._configs:
            raise KeyError(
                f"Config '{name}' not loaded. "
                f"Loaded configs: {list(self._configs.keys())}"
            )
        return self._configs[name]

    def get_value(self, name: str, path: str) -> Any:
        """Get a value from a loaded config using dotted path notation.

        Args:
            name: Config name (e.g., "pcs_config").
            path: Dotted path (e.g., "connection.protocol").

        Returns:
            The value at the specified path.

        Raises:
            KeyError: If the config is not loaded or path doesn't exist.
        """
        config: dict = self.get_config(name)
        parts: list[str] = path.split(".")
        current: Any = config

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                traversed: str = ".".join(parts[:i])
                raise KeyError(
                    f"Cannot navigate '{path}' in {name}: "
                    f"'{traversed}' is not a dict (type: {type(current).__name__})"
                )
            if part not in current:
                available: list[str] = list(current.keys())
                traversed = ".".join(parts[:i]) if i > 0 else "(root)"
                raise KeyError(
                    f"Key '{part}' not found in {name} at '{traversed}'. "
                    f"Available keys: {available}"
                )
            current = current[part]

        return current

    # ------------------------------------------------------------------
    # ZMQ REQ/REP config query server
    # ------------------------------------------------------------------

    async def serve_queries(
        self,
        ctx: zmq.asyncio.Context,
        bind_addr: str | None = None,
    ) -> None:
        """Bind ZMQ REP and serve config queries from cache.

        Handles 'get_config' and 'get_value' actions. Unknown actions
        return an error response.

        Args:
            ctx: ZMQ asyncio context.
            bind_addr: Override bind address (default: SOCK_CONFIG).
        """
        addr: str = bind_addr or SOCK_CONFIG
        rep_sock: zmq.asyncio.Socket = ctx.socket(zmq.REP)
        rep_sock.bind(addr)
        logger.info("Config query server bound on %s", addr)

        try:
            while True:
                request_data: bytes = await rep_sock.recv()

                try:
                    action, params = decode_command_request(request_data)
                except Exception as exc:
                    reply: bytes = encode_command_response(
                        STATUS_ERROR, error_msg=f"Malformed request: {exc}"
                    )
                    await rep_sock.send(reply)
                    continue

                if action == "get_config":
                    reply = self._handle_get_config(params)
                elif action == "get_value":
                    reply = self._handle_get_value(params)
                else:
                    reply = encode_command_response(
                        STATUS_ERROR,
                        error_msg=f"Unknown action: '{action}'",
                    )

                await rep_sock.send(reply)
        except asyncio.CancelledError:
            logger.info("Config query server stopped")
            raise
        finally:
            rep_sock.close()

    def _handle_get_config(self, params: dict) -> bytes:
        """Handle get_config request.

        Args:
            params: Request params with 'name' key.

        Returns:
            Encoded command response bytes.
        """
        name: str = params.get("name", "")
        try:
            config: dict = self.get_config(name)
            return encode_command_response(STATUS_OK, result=config)
        except KeyError as exc:
            return encode_command_response(
                STATUS_ERROR, error_msg=str(exc)
            )

    def _handle_get_value(self, params: dict) -> bytes:
        """Handle get_value request.

        Args:
            params: Request params with 'name' and 'path' keys.

        Returns:
            Encoded command response bytes.
        """
        name: str = params.get("name", "")
        path: str = params.get("path", "")
        try:
            value: Any = self.get_value(name, path)
            return encode_command_response(STATUS_OK, result=value)
        except KeyError as exc:
            return encode_command_response(
                STATUS_ERROR, error_msg=str(exc)
            )

    # ------------------------------------------------------------------
    # ZMQ PUSH socket for events (logger)
    # ------------------------------------------------------------------

    def _init_push_socket(
        self,
        ctx: zmq.asyncio.Context,
        push_addr: str | None = None,
    ) -> None:
        """Initialize ZMQ PUSH socket for event publishing.

        Args:
            ctx: ZMQ asyncio context.
            push_addr: Override push address (default: SOCK_LOGGER).
        """
        addr: str = push_addr or SOCK_LOGGER
        self._push_sock = ctx.socket(zmq.PUSH)
        self._push_sock.connect(addr)
        logger.info("Event push socket connected to %s", addr)

    def _close_push_socket(self) -> None:
        """Close the PUSH socket if open."""
        if self._push_sock is not None:
            self._push_sock.close()
            self._push_sock = None

    def _init_pub_socket(
        self,
        ctx: zmq.asyncio.Context,
        pub_addr: str | None = None,
    ) -> None:
        """Initialize ZMQ PUB socket for config_reload broadcast.

        Binds a PUB socket on SOCK_CONFIG_PUB so that control_manager and
        alarm_manager can subscribe to config_reload events. Must be called
        by the entry point before hot-reload events are expected.

        Callers that call _init_push_socket should also call _init_pub_socket
        to enable PUB broadcast alongside the existing PUSH-to-logger path.

        Args:
            ctx: ZMQ asyncio context.
            pub_addr: Override bind address (default: SOCK_CONFIG_PUB).
                      Use a TCP address (e.g., "tcp://127.0.0.1:15750") in tests
                      to avoid needing /run/ems on disk.
        """
        addr: str = pub_addr or SOCK_CONFIG_PUB
        self._pub_sock = ctx.socket(zmq.PUB)
        self._pub_sock.bind(addr)
        logger.info("Config PUB socket bound on %s", addr)

    def _close_pub_socket(self) -> None:
        """Close the PUB socket if open."""
        if self._pub_sock is not None:
            self._pub_sock.close()
            self._pub_sock = None

    async def _publish_event(
        self,
        severity: str,
        event_type: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        """Publish an event via ZMQ PUSH.

        Args:
            severity: Event severity (info, warning, error, critical).
            event_type: Event type string.
            message: Human-readable message.
            data: Optional event data dict.
        """
        if self._push_sock is None:
            logger.warning("No push socket -- cannot publish event: %s", message)
            return

        event_bytes: bytes = encode_event(
            timestamp_ms=int(time.time() * 1000),
            source="config_manager",
            severity=severity,
            event_type=event_type,
            message=message,
            data=data,
        )
        await self._push_sock.send(event_bytes)

    # ------------------------------------------------------------------
    # Hot-reload handling
    # ------------------------------------------------------------------

    async def handle_reload(self, name: str) -> None:
        """Handle hot-reload of a config file.

        Creates backup, validates new config, computes diff, swaps cache,
        and publishes config_reload event. On validation failure, rejects
        the change, keeps current config, and publishes error event.

        Args:
            name: Config name (e.g., "control_config").
        """
        logger.info("Hot-reload triggered for %s", name)

        # (a) Create backup before applying
        self._create_backup(name)

        # (b) Load and validate new config
        try:
            new_data: dict = load_with_profile(
                name, self._config_dir, self._profile
            )
        except FileNotFoundError as exc:
            logger.error("Hot-reload failed for %s: %s", name, exc)
            await self._publish_event(
                severity=SEVERITY_ERROR,
                event_type="config_reload_failed",
                message=f"Hot-reload failed for {name}: {exc}",
                data={"name": name, "error": str(exc)},
            )
            return

        # Check schema version
        schema: dict = self._load_schema(name)
        schema_props: dict = schema.get("properties", {})
        sv_def: dict = schema_props.get("_schema_version", {})
        expected_version: str | None = sv_def.get("const")

        if expected_version is not None:
            actual_version: Any = new_data.get("_schema_version")
            if actual_version != expected_version:
                error_msg: str = (
                    f"Schema version mismatch: found '{actual_version}', "
                    f"expected '{expected_version}'"
                )
                logger.error("Hot-reload rejected for %s: %s", name, error_msg)
                await self._publish_event(
                    severity=SEVERITY_ERROR,
                    event_type="config_reload_failed",
                    message=f"Hot-reload rejected for {name}: {error_msg}",
                    data={"name": name, "error": error_msg},
                )
                return

        # Validate against schema
        errors: list[ValidationError] = self._validate_config(name, new_data)
        if errors:
            error_msgs: list[str] = [
                _format_validation_error(f"{name}.yaml", e) for e in errors
            ]
            error_detail: str = "; ".join(error_msgs)
            logger.error("Hot-reload validation failed for %s: %s", name, error_detail)
            await self._publish_event(
                severity=SEVERITY_ERROR,
                event_type="config_reload_failed",
                message=f"Hot-reload validation failed for {name}",
                data={
                    "name": name,
                    "error": error_detail,
                    "validation_errors": [str(e) for e in errors],
                },
            )
            return

        # (c) Compute diff before swapping
        old_config: dict = self._configs.get(name, {})
        diff: dict = _compute_diff(old_config, new_data)

        # (d) Swap cache atomically
        self._configs[name] = new_data

        # (e) Publish config_reload event via PUSH to logger
        logger.info("Hot-reload applied for %s", name)
        await self._publish_event(
            severity=SEVERITY_INFO,
            event_type="config_reload",
            message=f"Config {name} reloaded successfully",
            data={
                "name": name,
                "config": new_data,
                "diff": diff,
            },
        )

        # (f) Broadcast config_reload on PUB socket for subscribing modules
        # Multipart: [topic_frame, msgpack_body] — same pattern as alarm_manager PUB
        if self._pub_sock is not None:
            pub_payload: bytes = msgpack.packb(
                {"name": name, "config": new_data, "diff": diff},
                use_bin_type=True,
            )
            await self._pub_sock.send_multipart([
                TOPIC_CONFIG_RELOAD.encode(),
                pub_payload,
            ])
            logger.debug("Config reload broadcast on PUB for %s", name)

    def _create_backup(self, name: str) -> None:
        """Create backup of current config file before hot-reload.

        Backups stored in config/backups/{name}.{timestamp}.yaml.
        Keeps only the last MAX_BACKUPS per config name.

        Args:
            name: Config name to backup.
        """
        source_path: Path = self._config_dir / f"{name}.yaml"
        if not source_path.exists():
            return

        backup_dir: Path = self._config_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp: str = str(int(time.time() * 1000))
        backup_path: Path = backup_dir / f"{name}.{timestamp}.yaml"
        shutil.copy2(source_path, backup_path)
        logger.debug("Created backup: %s", backup_path)

        # Cleanup: keep only last MAX_BACKUPS
        existing: list[Path] = sorted(backup_dir.glob(f"{name}.*.yaml"))
        if len(existing) > MAX_BACKUPS:
            for old_backup in existing[: len(existing) - MAX_BACKUPS]:
                old_backup.unlink()
                logger.debug("Removed old backup: %s", old_backup)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _fail(message: str) -> None:
        """Print error message and exit with failure code.

        Args:
            message: Error message to print to stderr.
        """
        print(f"FATAL: {message}", file=sys.stderr)
        sys.exit(1)
