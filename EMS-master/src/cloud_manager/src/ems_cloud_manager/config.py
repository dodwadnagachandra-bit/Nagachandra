"""Cloud manager config loader with JSON Schema validation.

Loads cloud_config.yaml and validates it against the JSON Schema at
config/schemas/cloud_config.schema.json. Raises ValueError with a
descriptive message if validation fails. Raises FileNotFoundError if
cert paths are missing when auth.method=mtls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# Default path to the JSON Schema for cloud_config
_DEFAULT_SCHEMA_PATH: Path = Path("config/schemas/cloud_config.schema.json")


def load_cloud_config(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate a cloud_config YAML file.

    Reads the YAML at ``path``, validates it against the JSON Schema at
    ``schema_path`` (defaults to config/schemas/cloud_config.schema.json),
    and returns the validated configuration as a plain dict.

    When auth.method is "mtls", verifies that ca_cert_path, client_cert_path,
    and client_key_path all exist on disk. Raises FileNotFoundError if any
    are missing. Skips cert path checks when auth.method is not "mtls".

    Args:
        path: Path to the cloud_config.yaml file to load.
        schema_path: Path to the JSON Schema file. If None, derives from
            path.parent.parent / "schemas" / "cloud_config.schema.json".

    Returns:
        Validated configuration dict with broker, auth, telemetry,
        and offline_buffer keys.

    Raises:
        FileNotFoundError: If the config, schema, or required cert files
            do not exist.
        ValueError: If the YAML is invalid or fails schema validation.
    """
    # Derive schema path: if not provided, look relative to config file
    if schema_path is None:
        derived_schema: Path = path.parent.parent / "schemas" / "cloud_config.schema.json"
        if derived_schema.exists():
            resolved_schema_path: Path = derived_schema
        else:
            resolved_schema_path = _DEFAULT_SCHEMA_PATH
    else:
        resolved_schema_path = schema_path

    # Load schema
    if not resolved_schema_path.exists():
        msg: str = f"Cloud config schema not found: {resolved_schema_path}"
        raise FileNotFoundError(msg)

    with resolved_schema_path.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    # Load config YAML
    if not path.exists():
        msg = f"Cloud config file not found: {path}"
        raise FileNotFoundError(msg)

    with path.open("r", encoding="utf-8") as f:
        try:
            config: Any = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            msg = f"Cloud config has invalid YAML: {exc}"
            raise ValueError(msg) from exc

    if not isinstance(config, dict):
        msg = f"Cloud config must be a YAML mapping, got {type(config).__name__}"
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
            f"cloud_config validation failed at '{field_path}': {first.message}"
            f" ({len(errors)} error(s) total)"
        )
        raise ValueError(msg)

    # Verify cert paths exist when using mTLS (fail early with clear error)
    auth: dict[str, Any] = config.get("auth", {})
    if auth.get("method") == "mtls":
        for cert_key in ("ca_cert_path", "client_cert_path", "client_key_path"):
            cert_path_str: str | None = auth.get(cert_key)
            if cert_path_str is None:
                msg = (
                    f"cloud_config: auth.method=mtls but '{cert_key}' is missing. "
                    f"Set the path to the certificate file or switch auth.method to 'token'."
                )
                raise FileNotFoundError(msg)
            cert_path: Path = Path(cert_path_str)
            if not cert_path.exists():
                msg = (
                    f"cloud_config: cert file not found for '{cert_key}': {cert_path}. "
                    f"Ensure the certificate is present or set auth.method to 'token' for testing."
                )
                raise FileNotFoundError(msg)

    return config
