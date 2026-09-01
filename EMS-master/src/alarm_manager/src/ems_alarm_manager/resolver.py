"""RTDB signal resolver for alarm_manager.

Maps dotted signal paths (e.g. "bms.cell_voltage_max") to RTDB field reads
using a pre-built callable dictionary. BMS aggregate signals iterate all
racks across all clusters, filtering to online racks only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ems_common.rtdb import EmsRtdb, MAX_CLUSTERS, MAX_RACKS_PER_CLUSTER

_log: logging.Logger = logging.getLogger(__name__)


def _collect_online_rack_values(
    rtdb: EmsRtdb,
    field: str,
) -> list[float]:
    """Collect a field value from all online racks across all clusters.

    Args:
        rtdb: The RTDB struct.
        field: Attribute name on EmsRack (e.g. "max_cell_v").

    Returns:
        List of float values from online racks (may be empty if none online).
    """
    values: list[float] = []
    for c in range(MAX_CLUSTERS):
        for r in range(MAX_RACKS_PER_CLUSTER):
            rack = rtdb.clusters[c].racks[r]
            if rack.online != 0:
                values.append(float(getattr(rack, field)))
    return values


def _bms_cell_voltage_max(rtdb: EmsRtdb) -> float | None:
    """Return max cell voltage across all online racks, or None if all offline."""
    values: list[float] = _collect_online_rack_values(rtdb, "max_cell_v")
    return max(values) if values else None


def _bms_cell_voltage_min(rtdb: EmsRtdb) -> float | None:
    """Return min cell voltage across all online racks, or None if all offline."""
    values: list[float] = _collect_online_rack_values(rtdb, "min_cell_v")
    return min(values) if values else None


def _bms_cell_temp_max(rtdb: EmsRtdb) -> float | None:
    """Return max cell temperature across all online racks, or None if all offline."""
    values: list[float] = _collect_online_rack_values(rtdb, "max_cell_t")
    return max(values) if values else None


def _bms_cell_temp_min(rtdb: EmsRtdb) -> float | None:
    """Return min cell temperature across all online racks, or None if all offline."""
    values: list[float] = _collect_online_rack_values(rtdb, "min_cell_t")
    return min(values) if values else None


def _bms_soc_pct(rtdb: EmsRtdb) -> float | None:
    """Return mean SOC across all online racks, or None if all offline."""
    values: list[float] = _collect_online_rack_values(rtdb, "pack_soc")
    return sum(values) / len(values) if values else None


def _bms_bus_voltage_v(rtdb: EmsRtdb) -> float | None:
    """Return DC bus voltage from PCS."""
    return float(rtdb.pcs.dc_voltage)


def _pcs_internal_temp_c(rtdb: EmsRtdb) -> float | None:
    """Return PCS internal temperature."""
    return float(rtdb.pcs.temperature)


# Type alias for resolver callables
_ResolverFn = Callable[[EmsRtdb], "float | None"]

# All supported signal paths
_SIGNAL_RESOLVERS: dict[str, _ResolverFn] = {
    "bms.cell_voltage_max": _bms_cell_voltage_max,
    "bms.cell_voltage_min": _bms_cell_voltage_min,
    "bms.cell_temp_max": _bms_cell_temp_max,
    "bms.cell_temp_min": _bms_cell_temp_min,
    "bms.soc_pct": _bms_soc_pct,
    "bms.bus_voltage_v": _bms_bus_voltage_v,
    "pcs.internal_temp_c": _pcs_internal_temp_c,
}


class SignalResolver:
    """Resolves dotted signal paths to RTDB field values.

    Maps signal paths used in alarms_config.yaml to callables that read
    the corresponding RTDB fields. BMS aggregate signals (cell_voltage_max,
    cell_voltage_min, cell_temp_max, cell_temp_min, soc_pct) exclude offline
    racks and return None when all racks are offline.

    The resolver dict is built once at construction time for O(1) lookup.
    """

    def __init__(self) -> None:
        """Build the signal resolver dictionary."""
        self._resolvers: dict[str, _ResolverFn] = dict(_SIGNAL_RESOLVERS)

    def resolve(self, signal_path: str, rtdb: EmsRtdb) -> float | None:
        """Resolve a signal path to its current RTDB value.

        Args:
            signal_path: Dotted signal path (e.g. "bms.cell_voltage_max").
            rtdb: The EmsRtdb struct to read from.

        Returns:
            Float value if the signal is known and available, None otherwise.
        """
        fn: _ResolverFn | None = self._resolvers.get(signal_path)
        if fn is None:
            return None
        try:
            return fn(rtdb)
        except Exception as exc:
            _log.error("Signal resolution failed for '%s': %s", signal_path, exc)
            return None

    def resolve_all(
        self,
        signal_paths: list[str],
        rtdb: EmsRtdb,
    ) -> dict[str, float | None]:
        """Resolve multiple signal paths in a single call.

        Args:
            signal_paths: List of dotted signal paths to resolve.
            rtdb: The EmsRtdb struct to read from.

        Returns:
            Dict mapping each path to its resolved value (None for unknown or unavailable).
        """
        return {path: self.resolve(path, rtdb) for path in signal_paths}

    def validate_paths(self, paths: list[str]) -> list[str]:
        """Return a list of signal paths not known to this resolver.

        Intended for startup validation to detect misconfigured alarm rules
        before the alarm loop begins.

        Args:
            paths: Signal paths to validate.

        Returns:
            List of unknown paths (empty if all are known).
        """
        return [p for p in paths if p not in self._resolvers]
