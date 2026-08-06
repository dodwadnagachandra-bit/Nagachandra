---
phase: 19-hmi-frontend
plan: 02
subsystem: hmi_frontend
tags: [websocket, telemetry, auth, context, reducer, rolling-buffer, numeric-keypad, confirm-dialog]
dependency_graph:
  requires: [frontend/types/telemetry.ts, frontend/types/api.ts, frontend/components/Layout.tsx, frontend/components/Sidebar.tsx, frontend/App.tsx]
  provides: [frontend/hooks/useWebSocket.ts, frontend/context/TelemetryContext.tsx, frontend/hooks/useTelemetry.ts, frontend/lib/rolling-buffer.ts, frontend/lib/api.ts, frontend/context/AuthContext.tsx, frontend/hooks/useApi.ts, frontend/components/NumericKeypad.tsx, frontend/components/ConfirmDialog.tsx, frontend/screens/LoginScreen.tsx]
  affects: [19-03-PLAN, 19-04-PLAN, 19-05-PLAN]
tech_stack:
  added: []
  patterns: [WebSocket auto-reconnect with exponential backoff, React Context + useReducer for global telemetry state, ephemeral auth token in memory, typed topic subscriptions via hook overloads]
key_files:
  created:
    - src/hmi_server/frontend/src/lib/rolling-buffer.ts
    - src/hmi_server/frontend/src/lib/api.ts
    - src/hmi_server/frontend/src/hooks/useWebSocket.ts
    - src/hmi_server/frontend/src/hooks/useTelemetry.ts
    - src/hmi_server/frontend/src/hooks/useAuth.ts
    - src/hmi_server/frontend/src/hooks/useApi.ts
    - src/hmi_server/frontend/src/context/TelemetryContext.tsx
    - src/hmi_server/frontend/src/context/AuthContext.tsx
    - src/hmi_server/frontend/src/components/NumericKeypad.tsx
    - src/hmi_server/frontend/src/components/ConfirmDialog.tsx
    - src/hmi_server/frontend/src/screens/LoginScreen.tsx
    - src/hmi_server/frontend/src/__tests__/RollingBuffer.test.ts
    - src/hmi_server/frontend/src/__tests__/TelemetryContext.test.tsx
    - src/hmi_server/frontend/src/__tests__/useWebSocket.test.ts
    - src/hmi_server/frontend/src/__tests__/AuthContext.test.tsx
  modified:
    - src/hmi_server/frontend/src/App.tsx
    - src/hmi_server/frontend/src/components/Layout.tsx
decisions:
  - "WebSocket URL built from window.location.host for production, Vite proxy for dev -- no hardcoded ports"
  - "Auth token stored in React state only (not localStorage) -- kiosk sessions are ephemeral"
  - "TelemetryProvider wraps inside AuthProvider -- telemetry does not depend on auth"
  - "useTelemetry uses TypeScript overloads for type-safe topic access"
  - "NumericKeypad uses 52x52px buttons (exceeds 44px WCAG minimum) for touch-friendly input"
  - "RollingBuffer uses simple array shift (not circular index) -- sufficient for 300-point buffer at 1Hz"
metrics:
  duration: 249s
  completed: "2026-03-15T08:07:48Z"
  tasks: 2
  tests: 27
---

# Phase 19 Plan 02: WebSocket + State -- real-time data pipeline, auth context, shared UI components

WebSocket hook with auto-reconnect (1s-30s exponential backoff), telemetry context with useReducer routing 6 topic types to typed state fields, auth context with PIN login/logout, apiFetch wrapper with Bearer token injection and error handling, NumericKeypad and ConfirmDialog shared UI components, and RollingBuffer for chart time-series.

## Task Results

### Task 1: WebSocket hook, telemetry context/reducer, useTelemetry hook, rolling buffer
**Commits:** d1b76bc (RED), d1b29e2 (GREEN)

- Created `lib/rolling-buffer.ts`: generic `RollingBuffer<T>` class with push/getAll/clear/length, evicts oldest on overflow
- Created `hooks/useWebSocket.ts`: manages single WebSocket connection with auto-reconnect, exponential backoff (1s, 2s, 4s, 8s, 16s, 30s cap), dispatches UPDATE_TOPIC on each message, uses refs for retry counter and WS instance to avoid re-renders
- Created `context/TelemetryContext.tsx`: `telemetryReducer` routes topics to state fields (system, pcs, gpio, meter, btms, bms.rack.*), `TelemetryProvider` wraps useReducer + useWebSocket, exports `useTelemetryContext` hook
- Created `hooks/useTelemetry.ts`: typed overloads for each topic (returns `SystemTelemetry | null` for "system", etc.), strips "bms.rack." prefix for rack map lookup
- 22 tests: RollingBuffer (6), telemetryReducer (8), TelemetryProvider (1), useWebSocket (7)

### Task 2: Auth context, useApi hook, login screen, numeric keypad, confirm dialog, wire providers
**Commits:** 031e903 (RED), 4546632 (GREEN)

- Created `lib/api.ts`: `apiFetch<T>` wrapper with Bearer token injection, `AuthError` (401/403), `ApiError` (other non-OK)
- Created `context/AuthContext.tsx`: `AuthProvider` with login (POST /api/auth/login), logout (POST /api/auth/logout), token/level/expiresAt in state, clears state on AuthError
- Created `hooks/useAuth.ts`: re-exports `useAuth` from context
- Created `hooks/useApi.ts`: wraps `apiFetch` with token from AuthContext, auto-logout on 401/403
- Created `components/NumericKeypad.tsx`: 3x4 grid (1-9, backspace, 0, confirm), 52x52px buttons, dark overlay, masked PIN display
- Created `components/ConfirmDialog.tsx`: modal with title/message, confirm/cancel buttons (44px+), danger/default variants
- Created `screens/LoginScreen.tsx`: PIN entry via NumericKeypad, error display, loading state
- Updated `App.tsx`: wrapped with AuthProvider (outermost) then TelemetryProvider
- Updated `Layout.tsx`: reads connectionStatus from useTelemetryContext, showSettings from useAuth (admin only)
- 5 auth tests: initial state, login stores token, logout clears, 401 clears, correct POST body

## Deviations from Plan

None - plan executed exactly as written.

## Verification

```
$ bun run build   -> 10 chunks, 0 errors, 1.35s
$ bun run test -- --run  -> 37 tests passed (5 files), 1.63s
```

## Self-Check: PASSED

All 15 created files verified on disk. All 4 commits (d1b76bc, d1b29e2, 031e903, 4546632) verified in git log.
