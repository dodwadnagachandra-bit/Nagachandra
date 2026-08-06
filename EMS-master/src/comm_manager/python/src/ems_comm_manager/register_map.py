"""Register map loader and value scaling for Modbus devices.

Loads YAML register map files (same format as pcs_register_map.yaml) and
provides value scaling with signed/unsigned two's complement handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RegisterDef:
    """Definition of a single Modbus holding register.

    Attributes:
        address: Modbus register address.
        name: Human-readable register name (maps to RTDB field).
        scale: Scaling factor. Engineering value = raw / scale.
        unit: Engineering unit string (e.g., "V", "A", "kW").
        access: Access mode ("r" for read-only, "rw" for read-write).
        signed: Whether the register uses two's complement signed encoding.
        default: Default raw value for simulation.
        rtdb_field: Optional explicit RTDB field name override.
        description: Optional human-readable description.
    """

    address: int
    name: str
    scale: int
    unit: str
    access: str
    signed: bool
    default: int = 0
    rtdb_field: str | None = None
    description: str = ""


def load_register_map(path: str | Path) -> list[RegisterDef]:
    """Load a register map YAML file and return a list of RegisterDef.

    Args:
        path: Path to the YAML register map file.

    Returns:
        List of RegisterDef parsed from the ``registers`` section.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        KeyError: If required fields are missing from a register entry.
    """
    path = Path(path)
    with path.open("r") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    registers: list[dict[str, Any]] = data.get("registers", [])
    result: list[RegisterDef] = []

    for entry in registers:
        reg = RegisterDef(
            address=int(entry["address"]),
            name=str(entry["name"]),
            scale=int(entry["scale"]),
            unit=str(entry.get("unit", "")),
            access=str(entry.get("access", "r")),
            signed=bool(entry.get("signed", False)),
            default=int(entry.get("default", 0)),
            rtdb_field=entry.get("rtdb_field"),
            description=str(entry.get("description", "")),
        )
        result.append(reg)

    return result


def scale_value(raw: int, reg: RegisterDef) -> float:
    """Scale a raw Modbus register value to engineering units.

    For signed registers, values > 32767 are treated as two's complement
    negative numbers (16-bit).

    Args:
        raw: Raw 16-bit register value.
        reg: Register definition with scale and signed flag.

    Returns:
        Scaled engineering value as float.
    """
    value: int = raw
    if reg.signed and value > 32767:
        value -= 65536
    return value / reg.scale


def build_read_ranges(registers: list[RegisterDef]) -> list[tuple[int, int]]:
    """Group contiguous registers into (start_address, count) tuples.

    Enables efficient Modbus reads by combining adjacent registers into
    single read operations.

    Args:
        registers: List of RegisterDef, need not be sorted.

    Returns:
        List of (start_address, register_count) tuples, sorted by address.
    """
    if not registers:
        return []

    sorted_regs: list[RegisterDef] = sorted(registers, key=lambda r: r.address)
    ranges: list[tuple[int, int]] = []

    start: int = sorted_regs[0].address
    end: int = start  # inclusive

    for reg in sorted_regs[1:]:
        if reg.address == end + 1:
            end = reg.address
        else:
            ranges.append((start, end - start + 1))
            start = reg.address
            end = reg.address

    ranges.append((start, end - start + 1))
    return ranges
