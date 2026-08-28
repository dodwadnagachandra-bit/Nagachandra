#!/usr/bin/env python3
"""EMS configuration validator.

Validates all YAML config files against their JSON Schema definitions.
Prints friendly prose error messages on failure.

Usage:
    uv run python tools/validate_config.py
    uv run python tools/validate_config.py --config-dir config/profiles/residential
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMAS_DIR: Path = Path("config/schemas")
CONFIG_DIR: Path = Path("config")

# All 14 configuration file names in order
CONFIG_FILES: list[str] = [
    "system_config",
    "bms_config",
    "pcs_config",
    "btms_config",
    "meter_config",
    "dg_config",
    "pv_config",
    "control_config",
    "alarms_config",
    "schedule_config",
    "cloud_config",
    "network_config",
    "gpio_config",
    "hmi_config",
]


def format_error(filename: str, error: ValidationError) -> str:
    """Format a ValidationError into a friendly prose error message.

    Args:
        filename: The config filename being validated (without extension).
        error: The ValidationError from jsonschema.

    Returns:
        A human-readable error string describing the violation.
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


def validate_file(name: str, config_dir: Path) -> list[str]:
    """Validate a single config YAML file against its JSON Schema.

    Args:
        name: The config name (e.g., "system_config"), without extension.
        config_dir: Directory containing the YAML config files.

    Returns:
        A list of error strings. Empty list means the file is valid.
    """
    yaml_path: Path = config_dir / f"{name}.yaml"
    schema_path: Path = SCHEMAS_DIR / f"{name}.schema.json"

    if not yaml_path.exists():
        return [f"NOT FOUND: {yaml_path}"]

    if not schema_path.exists():
        return [f"NOT FOUND: {schema_path}"]

    # Load and parse YAML
    with yaml_path.open("r") as fh:
        data: object = yaml.safe_load(fh)

    if data is None:
        return [f"ERROR: {yaml_path} is empty or contains only comments."]

    # Load JSON Schema
    with schema_path.open("r") as fh:
        schema: dict = json.load(fh)

    validator: Draft202012Validator = Draft202012Validator(schema)
    errors: list[str] = []

    for error in validator.iter_errors(data):
        errors.append(format_error(f"{name}.yaml", error))
        break  # Fail-fast: report only first error per file

    return errors


def main() -> None:
    """Validate all EMS config files against their JSON Schemas.

    Iterates all 14 config files and reports any validation errors.
    Exits with code 0 if all valid, 1 if any errors found.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Validate EMS YAML config files against JSON Schemas."
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=CONFIG_DIR,
        help="Directory containing YAML config files to validate (default: config/)",
    )
    args: argparse.Namespace = parser.parse_args()

    config_dir: Path = args.config_dir
    all_errors: list[str] = []

    for name in CONFIG_FILES:
        file_errors: list[str] = validate_file(name, config_dir)
        all_errors.extend(file_errors)

    if all_errors:
        for err in all_errors:
            print(err)
        print(f"\nFAIL: {len(all_errors)} validation error(s) found.", file=sys.stderr)
        sys.exit(1)
    else:
        n: int = len(CONFIG_FILES)
        print(f"OK: All {n} config files are valid.")
        sys.exit(0)


if __name__ == "__main__":
    main()
