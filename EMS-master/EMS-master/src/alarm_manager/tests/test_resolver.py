"""Tests for ems_alarm_manager.resolver — SignalResolver with RTDB path resolution."""

from __future__ import annotations

import pytest

from ems_common.rtdb import EmsRtdb, MAX_CLUSTERS, MAX_RACKS_PER_CLUSTER
from ems_alarm_manager.resolver import SignalResolver


def make_rtdb() -> EmsRtdb:
    """Create a fresh in-process EmsRtdb instance with all racks offline."""
    rtdb: EmsRtdb = EmsRtdb()
    return rtdb


class TestSignalResolver:
    """Test suite for SignalResolver."""

    def test_resolver_cell_voltage_max(self) -> None:
        """Two online racks with max_cell_v 3.4 and 3.5 — resolver returns 3.5."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].max_cell_v = 3.4
        rtdb.clusters[0].racks[1].online = 1
        rtdb.clusters[0].racks[1].max_cell_v = 3.5

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_voltage_max", rtdb)
        assert result == pytest.approx(3.5)

    def test_resolver_offline_rack_excluded(self) -> None:
        """Offline rack at 3.8V is excluded; online rack at 3.4V is returned."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].max_cell_v = 3.4
        rtdb.clusters[0].racks[1].online = 0  # offline
        rtdb.clusters[0].racks[1].max_cell_v = 3.8  # should be excluded

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_voltage_max", rtdb)
        assert result == pytest.approx(3.4)

    def test_resolver_all_offline_returns_none(self) -> None:
        """All racks offline — bms.cell_voltage_max returns None."""
        rtdb: EmsRtdb = make_rtdb()
        # All racks default to online=0 (offline)

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_voltage_max", rtdb)
        assert result is None

    def test_resolver_soc_pct_average(self) -> None:
        """Two online racks at 50% and 70% SOC — resolver returns 60.0."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].pack_soc = 50.0
        rtdb.clusters[0].racks[1].online = 1
        rtdb.clusters[0].racks[1].pack_soc = 70.0

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.soc_pct", rtdb)
        assert result == pytest.approx(60.0)

    def test_resolver_pcs_temp(self) -> None:
        """pcs.internal_temp_c resolves to rtdb.pcs.temperature."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.pcs.temperature = 55.0

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("pcs.internal_temp_c", rtdb)
        assert result == pytest.approx(55.0)

    def test_resolver_bus_voltage(self) -> None:
        """bms.bus_voltage_v resolves to rtdb.pcs.dc_voltage."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.pcs.dc_voltage = 520.0

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.bus_voltage_v", rtdb)
        assert result == pytest.approx(520.0)

    def test_resolver_invalid_path(self) -> None:
        """Unknown signal path returns None without raising."""
        rtdb: EmsRtdb = make_rtdb()
        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("nonexistent.path", rtdb)
        assert result is None

    def test_resolver_cell_voltage_min(self) -> None:
        """bms.cell_voltage_min returns min of online rack min_cell_v values."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].min_cell_v = 3.1
        rtdb.clusters[0].racks[1].online = 1
        rtdb.clusters[0].racks[1].min_cell_v = 2.9

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_voltage_min", rtdb)
        assert result == pytest.approx(2.9)

    def test_resolver_cell_temp_max(self) -> None:
        """bms.cell_temp_max returns max of online rack max_cell_t values."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].max_cell_t = 35.0
        rtdb.clusters[0].racks[1].online = 1
        rtdb.clusters[0].racks[1].max_cell_t = 42.0

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_temp_max", rtdb)
        assert result == pytest.approx(42.0)

    def test_resolver_cell_temp_min(self) -> None:
        """bms.cell_temp_min returns min of online rack min_cell_t values."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.clusters[0].racks[0].online = 1
        rtdb.clusters[0].racks[0].min_cell_t = 5.0
        rtdb.clusters[0].racks[1].online = 1
        rtdb.clusters[0].racks[1].min_cell_t = -2.0

        resolver: SignalResolver = SignalResolver()
        result = resolver.resolve("bms.cell_temp_min", rtdb)
        assert result == pytest.approx(-2.0)

    def test_resolve_all(self) -> None:
        """resolve_all returns a dict with results for all requested paths."""
        rtdb: EmsRtdb = make_rtdb()
        rtdb.pcs.temperature = 45.0
        rtdb.pcs.dc_voltage = 510.0

        resolver: SignalResolver = SignalResolver()
        results = resolver.resolve_all(["pcs.internal_temp_c", "bms.bus_voltage_v", "nonexistent.path"], rtdb)
        assert results["pcs.internal_temp_c"] == pytest.approx(45.0)
        assert results["bms.bus_voltage_v"] == pytest.approx(510.0)
        assert results["nonexistent.path"] is None

    def test_validate_paths_returns_unknown(self) -> None:
        """validate_paths returns list of paths not known to the resolver."""
        resolver: SignalResolver = SignalResolver()
        unknown = resolver.validate_paths(["bms.cell_voltage_max", "pcs.internal_temp_c", "unknown.signal"])
        assert "unknown.signal" in unknown
        assert "bms.cell_voltage_max" not in unknown
        assert "pcs.internal_temp_c" not in unknown
