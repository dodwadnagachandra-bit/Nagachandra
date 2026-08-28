"""Control manager config loader with JSON Schema validation.

Loads control_config.yaml and validates it against the JSON Schema at
config/schemas/control_config.schema.json. Raises ValueError with a
descriptive message if validation fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# Default path to the JSON Schema for control_config
_DEFAULT_SCHEMA_PATH: Path = Path("config/schemas/control_config.schema.json")


def load_control_config(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate a control_config YAML file.

    Reads the YAML at ``path``, validates it against the JSON Schema at
    ``schema_path`` (defaults to config/schemas/control_config.schema.json),
    and returns the validated configuration as a plain dict.

    Args:
        path: Path to the control_config.yaml file to load.
        schema_path: Path to the JSON Schema file. Defaults to
            config/schemas/control_config.schema.json relative to CWD.

    Returns:
        Validated configuration dict.

    Raises:
        FileNotFoundError: If the config or schema file does not exist.
        ValueError: If the YAML is invalid or fails schema validation.
    """
    resolved_schema_path: Path = schema_path or _DEFAULT_SCHEMA_PATH

    # Load schema
    if not resolved_schema_path.exists():
        msg: str = f"Control config schema not found: {resolved_schema_path}"
        raise FileNotFoundError(msg)

    with resolved_schema_path.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    # Load config YAML
    if not path.exists():
        msg = f"Control config file not found: {path}"
        raise FileNotFoundError(msg)

    with path.open("r", encoding="utf-8") as f:
        config: Any = yaml.safe_load(f)

    if not isinstance(config, dict):
        msg = f"Control config must be a YAML mapping, got {type(config).__name__}"
        raise ValueError(msg)

    # Validate against JSON Schema
    validator: Draft202012Validator = Draft202012Validator(schema)
    errors: list[ValidationError] = sorted(
        validator.iter_errors(config), key=lambda e: list(e.path)
    )

    if errors:
        first: ValidationError = errors[0]
        field_path: str = ".".join(str(p) for p in first.absolute_path) or "(root)"
        msg = (
            f"control_config validation failed at '{field_path}': {first.message}"
            f" ({len(errors)} error(s) total)"
        )
        raise ValueError(msg)

    return config
