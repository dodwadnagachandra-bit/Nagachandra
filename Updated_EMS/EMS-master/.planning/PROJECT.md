# ReVx EMS

## What This Is

Energy Management System for Battery Energy Storage Systems (BESS). Manages BMS, PCS, BTMS, fire suppression, metering, diesel generator, and solar PV through a single Advantech ECU-1170-552A embedded controller. Configuration-driven — one binary serves all topologies from 50 kWh residential to 6+ MWh containerized deployments via 14 YAML config files.

## Core Value

Reliable, safe control of battery energy storage — the safety manager must always protect equipment and people, the control loop must always maintain correct power dispatch, and the system must never lose telemetry data.

## Current State

**v5.0 (M4) shipped** — Cloud & OTA complete. 4 phases, 9 plans, 1110 tests, 58 commits.
See [v5.0 archive](milestones/v5.0-ROADMAP.md) for full details.

**What's built:**
- **cloud_manager**: paho-mqtt with mTLS, telemetry downsampling (1Hz→10-60s), event forwarding QoS 1, remote command dispatch (7 commands incl. OTA), heartbeat, ZMQ connection status
- **Offline buffer**: JSONL disk buffer during MQTT outages, FIFO replay at 10 msg/s, dual retention (max_hours + max_mb)
- **ota_manager**: HTTP firmware download with Range resume, Ed25519 signature verification, A/B partition with boot flag, automatic rollback on health failure, version tracking

<details>
<summary>Previous: v4.0 (M3) — HMI & Scheduling</summary>

4 phases, 12 plans, 969 tests, 64 commits. hmi_server (FastAPI, WebSocket, 7-screen React, PIN auth) + scheduler (time_of_day, curves, day/night).
See [v4.0 archive](milestones/v4.0-ROADMAP.md).
</details>

<details>
<summary>Previous: v3.0 (M2) — Control & Alarms</summary>

4 phases, 11 plans, 731 tests, 66 commits. control_manager + alarm_manager.
See [v3.0 archive](milestones/v3.0-ROADMAP.md).
</details>

<details>
<summary>Previous: v2.0 (M1) — Core Infrastructure</summary>

6 phases, 28 plans, 478 tests, 124 commits. config_manager, data_manager, safety_manager, comm_manager, logger.
See [v2.0 archive](milestones/v2.0-ROADMAP.md).
</details>

<details>
<summary>Previous: v1.0 (M0) — Simulators & Platform</summary>

8 phases, 12 plans, 119 tests, 74 commits. Monorepo scaffold, CI/CD, simulators.
See [v1.0 archive](milestones/v1.0-ROADMAP.md).
</details>

## v1.0 Product Complete

All 6 milestones (M0–M5) shipped. 12 software modules implemented across 30 phases. Production deployment pending ECU-1170-552A hardware validation (Phase 29 test procedures ready).

### Out of Scope

- SIL-2/ASIL-B formal certification — deferred to v2+
- R5 bare-metal safety manager — deferred to v2+
- PREEMPT_RT kernel — stock kernel has 20x headroom
- PCS vendor abstraction — deferred when second vendor appears
- Multi-PCS master/slave — pending site engineering
- Yocto production OS — migration at M5
- Docker/container deployment — native systemd

## Context

- **Hardware**: ECU-1170-552A available. BMS and PCS hardware pending — simulators in use
- **Architecture**: v3.4 complete — 16 ADRs, full decision matrices
- **Decisions**: 81 resolved across M0-M4, 5 pending external dependencies
- **Team**: 2–3 developers, 15-month (64-week) timeline
- **Stack**: C (safety, CAN), Python (application), React/TypeScript (HMI), FastAPI (backend)
- **IPC**: ZeroMQ (PUB/SUB + REQ/REP + PUSH/PULL), MessagePack serialization
- **RTDB**: POSIX shared memory (~1.8 MB), seqlock concurrency
- **Data**: Parquet 1Hz + DuckDB SQL + JSONL events

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Ubuntu 24.04 dev → Yocto production | Fast dev iteration | Validated (M0) |
| Stock kernel (no PREEMPT_RT) | 20x headroom for 100ms | Validated (M0) |
| ZeroMQ + MessagePack IPC | pyzmq maturity, no .proto build step | Validated (M1) |
| POSIX shm RTDB with seqlock | 1000x faster than Redis | Validated (M1) |
| CAN hybrid: C thread + Python | Crash isolation | Validated (M1) |
| PCS commands via RTDB | Single-writer-per-section | Validated (M2) |
| IEC 62682 5-state alarm lifecycle | Industry standard | Validated (M2) |
| Alarm-to-control via PUB/SUB | Non-blocking, no deadlock | Validated (M2) |
| FastAPI + static React build | No Node.js in production | Validated (M3) |
| WebSocket JSON (not MessagePack) | Native browser parsing | Validated (M3) |
| Opaque bearer tokens (not JWT) | Simple for embedded kiosk | Validated (M3) |
| Scheduler uses control_cmd API | No separate command path | Validated (M3) |

---
*Last updated: 2026-03-16 — v6.0 (M5) archived. All milestones complete.*
