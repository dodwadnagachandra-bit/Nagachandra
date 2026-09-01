# Phase 05-01: CAN Bus Simulator — Summary

**Completed:** 2026-03-05
**Duration:** ~8 min
**Tests:** 15 new (60 total, all passing)

## What Was Built

A complete CAN bus simulator that generates realistic BMU Layer 2 traffic on vcan with DBC-accurate signal encoding.

### Artifacts Created

| File | Purpose |
|------|---------|
| `config/bms_layer2.dbc` | Synthetic DBC with 10 BMU Layer 2 message definitions (extended CAN IDs, 0x18FF0003 base) |
| `tools/simulators/can_sim/__init__.py` | Package exports: CANSimulator, SignalGenerator |
| `tools/simulators/can_sim/__main__.py` | CLI entry point with --interface, --config, --racks, --verbose |
| `tools/simulators/can_sim/simulator.py` | CANSimulator orchestrator: loads config, spawns rack tasks |
| `tools/simulators/can_sim/rack.py` | RackSimulator: dual-rate async cycling (fast/slow), DBC encoding |
| `tools/simulators/can_sim/signals.py` | SignalGenerator: sinusoidal drift + Gaussian noise for all BMS signals |
| `tests/test_can_simulator.py` | 15 tests: DBC correctness, signed signals, CAN ID stride, signal ranges, virtual bus integration |

### Files Modified

| File | Change |
|------|--------|
| `pyproject.toml` | Added `cantools>=39.0` and `python-can>=4.0` to dev deps |
| `Makefile` | Added `sim-can` target for quick vcan simulator launch |
| `tools/__init__.py` | Created (package marker) |
| `tools/simulators/__init__.py` | Created (package marker) |

## Key Design Decisions

- **CAN ID stride 0x10 per rack:** `base_id + cluster * 0x1000 + rack * 0x10 + msg_offset` — prevents ID collisions, supports up to 256 racks per cluster
- **DBC template messages:** 10 generic messages encoded by name; CAN ID overridden at runtime per rack
- **29-bit CAN ID masking:** Config base_id (0x98FF0003) masked with 0x1FFFFFFF to get 29-bit ID (0x18FF0003) for python-can
- **Asyncio tasks (not multiprocessing):** All racks share one CAN bus instance; drift-corrected periodic loops
- **python-can virtual interface for tests:** No kernel modules needed for CI

## Verification

- DBC loads with cantools: 10 messages, correct IDs (0x18FF0003-0x18FF000C)
- Signed pack_i encodes/decodes negative values correctly
- Temperature offset: raw 72 → 32°C verified
- CAN ID: 0 collisions across 4 clusters × 16 racks × 10 messages
- Signal ranges: all values clamped to operating limits
- Virtual bus integration: frames sent, received, and decoded successfully

## Requirements Coverage

| Req ID | Status | Evidence |
|--------|--------|----------|
| SIM-01 | COVERED | Simulator sends at configurable fast (300ms) / slow (2000ms) cycle rates via asyncio |
| SIM-02 | COVERED | DBC-accurate encoding of all 10 L2 message types; CAN ID stride covers full range |
