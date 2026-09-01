---
phase: 28-production-hardening
plan: "02"
subsystem: hmi_server/frontend
tags: [frontend, react, cloud, ota, websocket, telemetry]
dependency_graph:
  requires: []
  provides:
    - CloudTelemetry and OtaTelemetry types in telemetry.ts
    - CloudStatusIndicator component (sidebar cloud dot)
    - OTA status badge in SettingsScreen
    - Schedule save wired to PUT /api/config/schedule
  affects:
    - src/hmi_server/frontend/src/types/telemetry.ts
    - src/hmi_server/frontend/src/context/TelemetryContext.tsx
    - src/hmi_server/frontend/src/components/Sidebar.tsx
    - src/hmi_server/frontend/src/screens/SettingsScreen.tsx
tech_stack:
  added: []
  patterns:
    - TelemetryContext reducer extended with new topic cases (cloud, ota)
    - CloudStatusIndicator follows ConnectionIndicator dot pattern
    - OTA badge uses inline-flex Tailwind badge pattern with state-dependent colors
key_files:
  created:
    - src/hmi_server/frontend/src/components/CloudStatusIndicator.tsx
    - src/hmi_server/frontend/src/__tests__/CloudStatusIndicator.test.tsx
  modified:
    - src/hmi_server/frontend/src/types/telemetry.ts
    - src/hmi_server/frontend/src/context/TelemetryContext.tsx
    - src/hmi_server/frontend/src/components/Sidebar.tsx
    - src/hmi_server/frontend/src/screens/SettingsScreen.tsx
    - src/hmi_server/frontend/src/__tests__/TelemetryContext.test.tsx
    - src/hmi_server/frontend/src/__tests__/SettingsScreen.test.tsx
    - src/hmi_server/frontend/src/__tests__/Layout.test.tsx
decisions:
  - "CloudStatusIndicator reads cloud state from TelemetryContext directly in Sidebar (not passed as prop) to keep Sidebar props clean"
  - "SettingsScreen OtaStatusSection is a local sub-component (not exported) since it has no other consumers"
  - "handleSave uses void IIFE pattern for async inside sync callback to avoid unhandled promise warnings"
  - "Layout.test.tsx mocks useTelemetryContext to isolate Sidebar from WebSocket requirement in unit tests"
metrics:
  duration: "7m"
  completed_date: "2026-03-16"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 7
  tests_before: 96
  tests_after: 105
---

# Phase 28 Plan 02: HMI Cloud/OTA Status and Schedule Save Summary

**One-liner:** HMI frontend wired to cloud/OTA telemetry topics with green/red dot in sidebar, OTA badge in Settings, and PUT /api/config/schedule save.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Telemetry types, reducer, and CloudStatusIndicator | efa595d | telemetry.ts, TelemetryContext.tsx, CloudStatusIndicator.tsx, Sidebar.tsx, 3 test files |
| 2 | OTA badge in Settings + schedule save wiring | 644face | SettingsScreen.tsx, SettingsScreen.test.tsx |

## What Was Built

### Task 1: Telemetry types, reducer, and CloudStatusIndicator

Extended the telemetry layer to route cloud and OTA data through the existing WebSocket pipeline:

- **`telemetry.ts`**: Added `CloudTelemetry` (`{ state: "connected"|"disconnected", broker, ts }`) and `OtaTelemetry` (`{ state, version_current, version_previous, detail, ts }`) interfaces. Extended `TelemetryState` with `cloud: CloudTelemetry | null` and `ota: OtaTelemetry | null`.

- **`TelemetryContext.tsx`**: Imported new types, added `cloud: null, ota: null` to `initialTelemetryState`, added two reducer cases after the `btms` case routing `"cloud"` and `"ota"` topics to their respective state fields.

- **`CloudStatusIndicator.tsx`** (new): Small dot component mirroring `ConnectionIndicator` pattern. Green (`bg-green-500`) when connected, red (`bg-red-500`) when disconnected, gray (`bg-gray-500`) when null. Shows "Cloud" label on xl breakpoint with tooltip title.

- **`Sidebar.tsx`**: Imported `useTelemetryContext` and `CloudStatusIndicator`. Renders `<CloudStatusIndicator cloud={telemetry.cloud} />` to the left of `<ConnectionIndicator>` in the footer.

### Task 2: OTA badge in Settings + schedule save wiring

- **`SettingsScreen.tsx`**: Added `OtaStatusSection` local component at the top of the settings page. Shows state badge (gray for idle, blue+pulse for downloading/verifying/applying, amber for rebooting, red for failed) plus current and previous firmware versions. Shows "waiting for data..." when `ota === null`. Replaced placeholder `handleSave` with a real fetch call to `PUT /api/config/schedule` with the schedule body and Bearer token, displaying success/error messages.

## Test Results

- **Before:** 96 tests passing (12 test files)
- **After:** 105 tests passing (13 test files)
- **New tests added:** 9 (2 reducer, 3 CloudStatusIndicator, 4 SettingsScreen)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Layout.test.tsx tests broke after Sidebar gained TelemetryContext dependency**

- **Found during:** Task 1 GREEN phase
- **Issue:** Layout tests render `<Sidebar>` directly without a `TelemetryProvider` wrapper. Adding `useTelemetryContext()` to Sidebar caused 4 Layout tests to throw "useTelemetryContext must be used within a TelemetryProvider".
- **Fix:** Added `vi.mock("../context/TelemetryContext", ...)` at the top of `Layout.test.tsx` that re-exports all real exports but overrides `useTelemetryContext` to return a static mock state with `cloud: null, ota: null`.
- **Files modified:** `src/hmi_server/frontend/src/__tests__/Layout.test.tsx`
- **Commit:** efa595d

## Self-Check: PASSED

All created files verified on disk. Both task commits verified in git history.

| Check | Result |
|-------|--------|
| CloudStatusIndicator.tsx created | FOUND |
| CloudStatusIndicator.test.tsx created | FOUND |
| 28-02-SUMMARY.md created | FOUND |
| Commit efa595d (Task 1) | FOUND |
| Commit 644face (Task 2) | FOUND |
