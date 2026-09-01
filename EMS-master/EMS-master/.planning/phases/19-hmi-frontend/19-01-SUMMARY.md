---
phase: 19-hmi-frontend
plan: 01
subsystem: hmi_frontend
tags: [react, tailwind, router, vitest, typescript, dark-theme]
dependency_graph:
  requires: []
  provides: [frontend/types/telemetry.ts, frontend/types/api.ts, frontend/types/enums.ts, frontend/components/Layout.tsx, frontend/components/Sidebar.tsx, frontend/components/ConnectionIndicator.tsx, frontend/App.tsx]
  affects: [19-02-PLAN, 19-03-PLAN, 19-04-PLAN, 19-05-PLAN]
tech_stack:
  added: [react-router-dom, chart.js, react-chartjs-2, lucide-react, vitest, @testing-library/react, @testing-library/jest-dom, jsdom]
  patterns: [React Router layout routes, lazy-loaded screens, Tailwind v4 CSS-first config, TDD]
key_files:
  created:
    - src/hmi_server/frontend/src/types/telemetry.ts
    - src/hmi_server/frontend/src/types/api.ts
    - src/hmi_server/frontend/src/types/enums.ts
    - src/hmi_server/frontend/src/components/Layout.tsx
    - src/hmi_server/frontend/src/components/Sidebar.tsx
    - src/hmi_server/frontend/src/components/ConnectionIndicator.tsx
    - src/hmi_server/frontend/src/screens/Dashboard.tsx
    - src/hmi_server/frontend/src/screens/BmsScreen.tsx
    - src/hmi_server/frontend/src/screens/PcsScreen.tsx
    - src/hmi_server/frontend/src/screens/AlarmScreen.tsx
    - src/hmi_server/frontend/src/screens/ControlScreen.tsx
    - src/hmi_server/frontend/src/screens/EnergyScreen.tsx
    - src/hmi_server/frontend/src/screens/SettingsScreen.tsx
    - src/hmi_server/frontend/src/app.css
    - src/hmi_server/frontend/vitest.config.ts
    - src/hmi_server/frontend/src/__tests__/Layout.test.tsx
    - src/hmi_server/frontend/src/__tests__/setup.ts
  modified:
    - src/hmi_server/frontend/package.json
    - src/hmi_server/frontend/vite.config.ts
    - src/hmi_server/frontend/src/App.tsx
    - src/hmi_server/frontend/src/main.tsx
    - src/hmi_server/frontend/index.html
decisions:
  - "App.tsx exports routes without BrowserRouter -- BrowserRouter wraps in main.tsx so tests can use MemoryRouter"
  - "Tailwind v4 @theme block defines custom color tokens (bg-primary, bg-secondary, accent, success, warning, danger)"
  - "Sidebar uses xl: breakpoint (1280px) for icons-only vs icons+labels responsive behavior"
  - "jest-dom setup file at src/__tests__/setup.ts for vitest custom matchers"
metrics:
  duration: 240s
  completed: "2026-03-15T07:59:56Z"
  tasks: 2
  tests: 10
---

# Phase 19 Plan 01: Foundation -- deps, types, router, layout shell, dark theme, test infra

TypeScript interfaces for all 6 WebSocket telemetry topics and all REST API contracts, React Router with 7 lazy-loaded screen routes, persistent sidebar with lucide-react icons, Tailwind v4 dark theme, ConnectionIndicator component, and Vitest test infrastructure with 10 passing tests.

## Task Results

### Task 1: Install deps, create types, dark theme CSS, Vite proxy, test infrastructure
**Commit:** ffc37f3

- Installed runtime deps: react-router-dom, chart.js, react-chartjs-2, lucide-react
- Installed dev/test deps: vitest, @testing-library/react, @testing-library/jest-dom, jsdom, @testing-library/user-event
- Created `src/types/telemetry.ts` with all 6 WebSocket telemetry interfaces (SystemTelemetry, BmsRackTelemetry, PcsTelemetry, GpioTelemetry, MeterTelemetry, BtmsTelemetry) plus TelemetryState, TelemetryAction, ConnectionStatus, and WsMessage
- Created `src/types/api.ts` with all REST API interfaces (LoginRequest/Response, ZmqResponse, ModeChangeRequest, ManualSetpointRequest, SourcePriorityRequest, MaintenanceRequest, AcknowledgeRequest, ActiveAlarm, ScheduleConfig, TimeWindow)
- Created `src/types/enums.ts` with CONTROL_STATE_NAMES and PCS_STATE_NAMES display maps
- Created `src/app.css` with Tailwind v4 `@import "tailwindcss"` and `@theme` block defining dark color palette
- Updated `vite.config.ts` with dev proxy for `/ws/telemetry` (WebSocket) and `/api` (REST)
- Created `vitest.config.ts` with jsdom environment and globals enabled
- Updated `package.json` test script from echo placeholder to `vitest`

### Task 2: Layout shell, sidebar navigation, router, connection indicator, placeholder screens (TDD)
**Commits:** e532f90 (RED), 63e5915 (GREEN)

- Created 7 placeholder screen stubs (Dashboard, BMS, PCS, Alarms, Control, Energy, Settings) with data-testid attributes
- Created `ConnectionIndicator.tsx` with green/red/yellow status dot, 44x44px tap target, screen-reader text
- Created `Sidebar.tsx` with 6+1 NavLink tabs using lucide-react icons (LayoutGrid, Battery, Zap, Bell, SlidersHorizontal, BarChart3, Settings), alarm badge, responsive icons-only/icons+labels at xl breakpoint
- Created `Layout.tsx` with flex row sidebar + Outlet content area, full viewport height
- Rewrote `App.tsx` with React Router layout routes and React.lazy code-split screen imports
- Updated `main.tsx` with BrowserRouter wrapper and app.css import
- Added `class="dark"` to index.html
- 10 tests: layout rendering, 6-tab sidebar, 7-tab with settings, tab navigation, active highlighting, connection indicator colors, screen-reader labels, lucide-react icons

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added jest-dom vitest setup file**
- **Found during:** Task 2 GREEN phase
- **Issue:** `toBeInTheDocument()` matcher not recognized by vitest without jest-dom setup
- **Fix:** Created `src/__tests__/setup.ts` importing `@testing-library/jest-dom/vitest` and added to vitest.config.ts setupFiles
- **Files modified:** `vitest.config.ts`, `src/__tests__/setup.ts`
- **Commit:** 63e5915

**2. [Rule 1 - Bug] Fixed lazy-loaded component test assertions**
- **Found during:** Task 2 GREEN phase
- **Issue:** Tests using `getByTestId` failed because React.lazy components render asynchronously, showing Suspense fallback
- **Fix:** Changed to `findByTestId` (async) queries for lazy-loaded screens
- **Files modified:** `src/__tests__/Layout.test.tsx`
- **Commit:** 63e5915

## Verification

```
$ bun run build   -> 8 chunks, 0 errors, 1.40s
$ bun run test -- --run  -> 10 tests passed, 1.26s
```

## Self-Check: PASSED

All 17 created files verified on disk. All 3 commits (ffc37f3, e532f90, 63e5915) verified in git log.
