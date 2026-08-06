"""CLI entry point for ems-config validate.

Provides a command-line tool for validating EMS YAML config files
against their JSON Schema definitions. Used for field service and CI.

Usage:
    ems-config validate config/system_config.yaml
    ems-config validate config/
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# Default schema directory relative to repo root
DEFAULT_SCHEMAS_DIR: Path = Path("config/schemas")

# All 14 configuration file names
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


def _format_error(filename: str, error: ValidationError) -> str:
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
        f"  ERROR: field '{path}' is set to {value_repr}"
        f" but {error.message}."
    )


def _find_schema_for_file(yaml_path: Path, schemas_dir: Path) -> Path | None:
    """Find the matching schema for a YAML config file.

    Args:
        yaml_path: Path to the YAML file.
        schemas_dir: Directory containing JSON Schema files.

    Returns:
        Path to the schema file, or None if not found.
    """
    name: str = yaml_path.stem  # e.g., "system_config"
    schema_path: Path = schemas_dir / f"{name}.schema.json"
    return schema_path if schema_path.exists() else None


def validate_single_file(
    yaml_path: Path, schemas_dir: Path
) -> list[str]:
    """Validate a single YAML config file against its schema.

    Args:
        yaml_path: Path to the YAML file to validate.
        schemas_dir: Directory containing JSON Schema files.

    Returns:
        List of error strings. Empty means valid.
    """
    errors: list[str] = []

    if not yaml_path.exists():
        return [f"File not found: {yaml_path}"]

    schema_path: Path | None = _find_schema_for_file(yaml_path, schemas_dir)
    if schema_path is None:
        return [f"No schema found for: {yaml_path.name}"]

    # Load YAML
    try:
        data: object = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        return [f"YAML parse error in {yaml_path.name}: {exc}"]

    if data is None:
        return [f"File is empty or contains only comments: {yaml_path.name}"]

    # Load schema
    with schema_path.open("r") as fh:
        schema: dict = json.load(fh)

    # Validate
    validator: Draft202012Validator = Draft202012Validator(schema)
    for error in validator.iter_errors(data):
        errors.append(_format_error(yaml_path.name, error))

    return errors


def validate_directory(
    config_dir: Path, schemas_dir: Path
) -> tuple[int, int, list[str]]:
    """Validate all 14 config files in a directory.

    Args:
        config_dir: Directory containing YAML config files.
        schemas_dir: Directory containing JSON Schema files.

    Returns:
        Tuple of (valid_count, error_count, error_messages).
    """
    valid_count: int = 0
    all_errors: list[str] = []

    for name in CONFIG_FILES:
        yaml_path: Path = config_dir / f"{name}.yaml"
        file_errors: list[str] = validate_single_file(yaml_path, schemas_dir)

        if file_errors:
            all_errors.append(f"{name}.yaml:")
            all_errors.extend(file_errors)
        else:
            valid_count += 1

    return valid_count, len(CONFIG_FILES) - valid_count, all_errors


def main() -> None:
    """CLI entry point for ems-config validate."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="ems-config",
        description="EMS configuration management tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate subcommand
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate config file(s) against JSON Schema.",
    )
    validate_parser.add_argument(
        "path",
        type=Path,
        help="Path to a YAML file or directory of config files.",
    )
    validate_parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=DEFAULT_SCHEMAS_DIR,
        help="Directory containing JSON Schema files (default: config/schemas/).",
    )

    args: argparse.Namespace = parser.parse_args()

    if args.command == "validate":
        target: Path = args.path
        schemas_dir: Path = args.schemas_dir

        if target.is_file():
            # Validate single file
            errors: list[str] = validate_single_file(target, schemas_dir)
            if errors:
                print(f"FAIL: {target.name}")
                for err in errors:
                    print(err)
                sys.exit(1)
            else:
                print(f"OK: {target.name} is valid.")
                sys.exit(0)

        elif target.is_dir():
            # Validate all 14 files in directory
            valid_count, error_count, errors = validate_directory(
                target, schemas_dir
            )
            if errors:
                for err in errors:
                    print(err)
                print(
                    f"\nFAIL: {error_count} file(s) invalid, "
                    f"{valid_count} valid out of {len(CONFIG_FILES)}."
                )
                sys.exit(1)
            else:
                print(f"OK: All {valid_count} config files are valid.")
                sys.exit(0)
        else:
            print(f"ERROR: Path not found: {target}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
