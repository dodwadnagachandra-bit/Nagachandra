---
phase: 19-hmi-frontend
plan: 05
subsystem: hmi_frontend
tags: [energy-screen, settings-screen, bar-chart, schedule-form, admin-gate, build-verification]
dependency_graph:
  requires: [frontend/hooks/useApi.ts, frontend/context/AuthContext.tsx, frontend/types/api.ts, frontend/components/charts/PowerBar.tsx]
  provides: [frontend/components/charts/EnergyBar.tsx, frontend/screens/EnergyScreen.tsx, frontend/screens/SettingsScreen.tsx]
  affects: []
tech_stack:
  added: []
  patterns: [REST query with time range selector, admin-only route gating via useNavigate, dynamic form rows for schedule windows, Intl.NumberFormat for kWh display]
key_files:
  created:
    - src/hmi_server/frontend/src/components/charts/EnergyBar.tsx
    - src/hmi_server/frontend/src/__tests__/EnergyScreen.test.tsx
    - src/hmi_server/frontend/src/__tests__/SettingsScreen.test.tsx
  modified:
    - src/hmi_server/frontend/src/screens/EnergyScreen.tsx
    - src/hmi_server/frontend/src/screens/SettingsScreen.tsx
decisions:
  - "EnergyBar uses single dataset with 4 labeled bars (not grouped datasets) for simplicity"
  - "Energy values parsed dynamically from columns/rows response format using column name lookup"
  - "Settings screen redirects non-admin via useNavigate('/') in useEffect"
  - "Time window inputs use simple text/number inputs rather than NumericKeypad for faster form editing"
  - "Save button shows informational warning message (backend endpoint not yet available per RESEARCH.md)"
  - "Build version hardcoded to 0.1.0 from package.json (dynamic import deferred)"
metrics:
  duration: 213s
  completed: "2026-03-15T08:29:58Z"
  tasks: 2
  tests: 15
---

# Phase 19 Plan 05: Energy + Settings screens and full build verification

Energy screen with POST /api/query/energy_totals REST fetch, time range selector (Today/7D/30D), 2x2 kWh total cards, and EnergyBar chart; Settings screen with admin-only gate, schedule mode selector (manual/time_of_day/curve), dynamic time window form editor, save placeholder, and logout button; full 95-test suite green with zero-error production build.

## Task Results

### Task 1: Energy screen with bar chart and time range selector
**Commits:** 41a70a4 (RED), 13b0f01 (GREEN)

- Created `components/charts/EnergyBar.tsx`: Bar chart with 4 bars (Charge=green, Discharge=orange, Import=blue, Export=purple), fixed canvas dimensions, animation: false
- Replaced placeholder `EnergyScreen.tsx`:
  - Fetches POST `/api/query/energy_totals` with `{ start_ts, end_ts }` on mount
  - Time range selector: Today (24h), 7 Days, 30 Days -- re-fetches on change
  - 2x2 card grid: Charge, Discharge, Grid Import, Grid Export in kWh
  - Values converted from Wh to kWh (/ 1000) with Intl.NumberFormat
  - Loading ("Fetching energy data..."), error (with retry button), and empty states
  - EnergyBar chart renders below cards
- 8 tests: time range buttons, fetch on mount, range change re-fetch, kWh display, loading state, error state, bar chart values, empty result

### Task 2: Settings screen (admin-only) with schedule form, full build verification
**Commits:** 19c2b42 (RED), 36e33ae (GREEN)

- Replaced placeholder `SettingsScreen.tsx`:
  - Admin gate: `useEffect` checks `authState.level !== "admin"` and calls `navigate("/")`
  - Schedule mode selector: Manual, Time of Day, Curve (3 buttons, selected highlighted)
  - Time windows editor (visible in time_of_day mode): add/remove rows with start time, end time, action toggle (charge/discharge/idle), power_kw input
  - Day/night settings: day_start and night_start text inputs (defaults 06:00/18:00)
  - Power curve section: placeholder text "deferred to a future release"
  - System info section: build version 0.1.0
  - Save button: shows informational warning "Schedule save endpoint not yet available"
  - Logout button: calls `useAuth().logout()`, danger styling
- 7 tests: mode selector, time windows section visibility, add/remove window, non-admin redirect, save message, logout call
- Full verification: 95 tests pass across 12 files, `bun run build` produces 14 chunks with 0 errors

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed duplicate text match in energy total assertion**
- **Found during:** Task 1 GREEN phase
- **Issue:** `/800/` regex matched multiple elements (card value "800 kWh" and mock bar text "export=800")
- **Fix:** Changed test assertion to `/800 kWh/` for specificity
- **Files modified:** `EnergyScreen.test.tsx`
- **Commit:** 13b0f01

## Verification

```
$ bun run build   -> 14 chunks, 0 errors, 1.75s
$ bun run test -- --run  -> 95 tests passed (12 files), 2.69s
$ ls dist/  -> index.html + assets/ (complete static bundle)
```

All 7 screens in build: Dashboard, BMS, PCS, Alarm, Control, Energy, Settings.

## Self-Check: PASSED
