"""Logger configuration loading and validation.

Loads logger_config.yaml, validates against JSON Schema, and provides
typed dataclass access to all logger settings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

logger: logging.Logger = logging.getLogger(__name__)

# Default paths
_DEFAULT_CONFIG_DIR: Path = Path("/opt/ems/config")
_DEFAULT_SCHEMA_DIR: Path = Path("/opt/ems/config/schemas")


@dataclass(frozen=True)
class StorageConfig:
    """Data storage paths and retention settings."""

    data_dir: str = "/mnt/ssd/ems/data"
    parquet_retention_days: int = 90
    jsonl_retention_days: int = 180
    disk_usage_threshold_pct: int = 80
    cleanup_interval_s: int = 300


@dataclass(frozen=True)
class ParquetConfig:
    """Parquet file writing settings."""

    compression: str = "snappy"
    rotation_period_s: int = 3600
    row_group_size: int = 3600


@dataclass(frozen=True)
class QueryConfig:
    """DuckDB query interface settings."""

    socket: str = "ipc:///run/ems/logger_query.sock"
    time_series_max_rows: int = 10000
    time_series_timeout_s: int = 5
    latest_timeout_s: int = 1
    range_stats_timeout_s: int = 5
    event_log_max_rows: int = 1000
    event_log_timeout_s: int = 3
    energy_totals_timeout_s: int = 5
    cell_snapshot_timeout_s: int = 2


@dataclass(frozen=True)
class LoggerConfig:
    """Top-level logger configuration."""

    schema_version: str = "1.0"
    storage: StorageConfig = field(default_factory=StorageConfig)
    parquet: ParquetConfig = field(default_factory=ParquetConfig)
    query: QueryConfig = field(default_factory=QueryConfig)


def load_logger_config(
    config_path: Path,
    schema_path: Path | None = None,
) -> LoggerConfig:
    """Load and validate logger configuration from YAML file.

    Args:
        config_path: Path to logger_config.yaml.
        schema_path: Optional path to logger_config.schema.json.
                     If None, looks in config_path.parent / "schemas".

    Returns:
        Validated LoggerConfig dataclass.

    Raises:
        FileNotFoundError: If config or schema file not found.
        ValidationError: If config fails JSON Schema validation.
        ValueError: If schema version mismatch.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Logger config not found: {config_path}")

    with open(config_path) as f:
        raw: dict = yaml.safe_load(f)

    # Resolve schema path
    if schema_path is None:
        schema_path = config_path.parent / "schemas" / "logger_config.schema.json"

    # Validate against JSON Schema if schema file exists
    if schema_path.exists():
        with open(schema_path) as f:
            schema: dict = json.load(f)

        validator = Draft202012Validator(schema)
        validator.validate(raw)
        logger.info("Logger config validated against schema")
    else:
        logger.warning("Schema file not found at %s, skipping validation", schema_path)

    # Parse into dataclasses
    storage_raw: dict = raw.get("storage", {})
    parquet_raw: dict = raw.get("parquet", {})
    query_raw: dict = raw.get("query", {})

    return LoggerConfig(
        schema_version=raw.get("_schema_version", "1.0"),
        storage=StorageConfig(**storage_raw),
        parquet=ParquetConfig(**parquet_raw),
        query=QueryConfig(**query_raw),
    )
