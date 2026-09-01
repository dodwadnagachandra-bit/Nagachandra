---
phase: 19-hmi-frontend
plan: 04
subsystem: hmi_frontend
tags: [alarm-screen, control-screen, ack, mode-selector, setpoint, maintenance, fault-reset, sidebar-badge]
dependency_graph:
  requires: [frontend/hooks/useApi.ts, frontend/hooks/useTelemetry.ts, frontend/context/AuthContext.tsx, frontend/components/ConfirmDialog.tsx, frontend/components/NumericKeypad.tsx, frontend/components/Sidebar.tsx]
  provides: [frontend/screens/AlarmScreen.tsx, frontend/screens/ControlScreen.tsx]
  affects: [19-05-PLAN]
tech_stack:
  added: []
  patterns: [polling REST for alarm data, inline feedback with auto-dismiss, severity color mapping, sortable table columns, conditional button visibility by auth level and state]
key_files:
  created:
    - src/hmi_server/frontend/src/__tests__/AlarmScreen.test.tsx
    - src/hmi_server/frontend/src/__tests__/ControlScreen.test.tsx
  modified:
    - src/hmi_server/frontend/src/screens/AlarmScreen.tsx
    - src/hmi_server/frontend/src/screens/ControlScreen.tsx
    - src/hmi_server/frontend/src/components/Layout.tsx
decisions:
  - "Alarm table uses 5s polling interval for active alarms (not WebSocket -- alarms not on WS topics)"
  - "Sidebar alarm badge uses 10s polling interval (less frequent than alarm screen to reduce load)"
  - "Alarm history fetches last 24h by default with optional severity filter buttons"
  - "Control mode buttons send target_state as lowercase string to match backend expectations"
  - "Maintenance button uses danger variant ConfirmDialog for enter, default variant for exit"
  - "Fault reset button only visible in FAULT state (control_state === 5)"
  - "Inline feedback messages auto-clear after 3 seconds via setTimeout"
metrics:
  duration: 257s
  completed: "2026-03-15T08:23:14Z"
  tasks: 2
  tests: 17
---

# Phase 19 Plan 04: Alarm and Control screens with ACK, mode selector, setpoint, and sidebar badge

Active alarm table with severity-colored rows (red/orange/yellow), sortable by severity and timestamp, ACK button for unacknowledged alarms, alarm history query with severity filter, sidebar badge polling for unacked count, control screen with live state display, 4-mode selector with confirmation dialogs, NumericKeypad setpoint entry, and maintenance/fault-reset buttons gated by auth level and control state.

## Task Results

### Task 1: Alarm screen -- active alarm table, history query, ACK button, sidebar badge
**Commits:** a706787 (RED), 319a216 (GREEN)

- Replaced placeholder AlarmScreen with full implementation:
  - Active/History tab toggle
  - Active tab: GET `/api/alarm/active` on mount + 5s auto-refresh
  - Table with Severity, Signal, State, Time, Action columns
  - Row coloring: protection=red-900/30 + border-red-500, action=orange, warning=yellow
  - Sortable by severity (protection > action > warning) and timestamp (click headers)
  - ACK button on ACTIVE_UNACKED and CLEARED_UNACKED rows, sends POST `/api/alarm/acknowledge`
  - ACTIVE_ACKED shows "Acked" badge, no button
  - Empty state: "No active alarms" centered text
  - Scroll-enabled table with max-h-[480px] (10") and max-h-[720px] (15")
- History tab: POST `/api/query/event_log` with last-24h range, severity filter buttons, dynamic column rendering
- Updated Layout.tsx: polls `/api/alarm/active` every 10s, counts ACTIVE_UNACKED, passes to Sidebar unackedCount
- 7 tests: row count, severity colors, ACK button visibility, ACK fetch call, empty state, sort toggle, CLEARED_UNACKED ACK

### Task 2: Control screen -- state display, mode selector, setpoint input, maintenance/fault buttons
**Commits:** 49b866d (RED), 5dfe4f7 (GREEN)

- Replaced placeholder ControlScreen with full implementation:
  - Current state display with color-coded badge (green for operational, red for FAULT/EMERGENCY, yellow for MAINTENANCE)
  - Active setpoint kW display next to state badge
  - 4 mode buttons: IDLE, STANDBY, CHARGING, DISCHARGING in 2x2 grid (4 cols on 15")
  - Current state button disabled with opacity-60
  - Mode change: opens ConfirmDialog, then POST `/api/control/mode` with `{ target_state: "lowercase" }`
  - Setpoint entry: "Set Power (kW)" button opens NumericKeypad, confirm opens ConfirmDialog, POST `/api/control/setpoint`
  - Fault Reset button: visible only when control_state === 5 (FAULT), POST `/api/control/fault-reset`
  - Maintenance Enter: admin-only, not in maintenance, danger variant confirm, POST `/api/control/maintenance` { action: "enter" }
  - Maintenance Exit: admin-only, in maintenance state, POST `/api/control/maintenance` { action: "exit" }
  - Inline feedback messages (success=green, error=red) auto-clear after 3s
  - All buttons >= 44x44px min-h
- 10 tests: state name, 4 mode buttons, confirm dialog, mode API call, setpoint keypad, fault reset visibility, maintenance admin gate, button sizing, null telemetry

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed duplicate text element in ControlScreen test**
- **Found during:** Task 2 GREEN phase
- **Issue:** "CHARGING" text appears in both state badge and mode button, causing `getByText` to throw on multiple matches
- **Fix:** Changed to `getAllByText` with class-based verification for the state badge element
- **Files modified:** `ControlScreen.test.tsx`
- **Commit:** 5dfe4f7

## Verification

```
$ bun run build   -> 13 chunks, 0 errors, 1.78s
$ bun run test -- --run  -> 80 tests passed (10 files), 2.33s
```

## Self-Check: PASSED
