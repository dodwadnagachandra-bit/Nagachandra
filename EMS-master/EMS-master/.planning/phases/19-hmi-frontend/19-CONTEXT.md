# Phase 19: HMI Frontend - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

7-screen React frontend with real-time WebSocket data, command forms, alarm management, energy charts, and settings. Covers HMI-05 through HMI-13. React 19 + Vite 6 + Tailwind 4 + Chart.js. Built with bun, served as static files by FastAPI (Phase 18).

</domain>

<decisions>
## Implementation Decisions

### Screen Layout and Navigation

How should the 7 screens be organized for a 10″/15″ touch panel in kiosk mode?

**Decision:** Tab-based navigation with a persistent sidebar (collapsed to icons on 10″). No nested routes — all screens are top-level.

| Screen | Tab Label | Icon | Primary Content | Update Source |
|--------|-----------|------|----------------|---------------|
| Dashboard | Dashboard | grid | SOC gauge, power bar, PCS state, alarm badge | WebSocket 1Hz |
| BMS | Battery | battery | Rack selector, cell v/t ranges, SOC per rack | WebSocket 1Hz |
| PCS | Inverter | zap | AC/DC telemetry, state, faults, temperature | WebSocket 1Hz |
| Alarms | Alarms | bell | Active alarm table, history, ACK button | WebSocket + REST |
| Control | Control | sliders | State machine, mode selector, setpoint, maintenance | WebSocket + REST |
| Energy | Energy | bar-chart | Daily/weekly/monthly totals, bar chart | REST (on-demand) |
| Settings | Settings | settings | Schedule editor, system config (admin only) | REST (on-demand) |

Key rules:
- Sidebar always visible — no hamburger menu (touch panels need persistent navigation).
- Active screen highlighted in sidebar. Screen content fills remaining viewport.
- Alarm badge shows count of ACTIVE_UNACKED alarms — red dot on bell icon.
- Settings tab hidden unless admin-authenticated — prevents operator confusion.
- No page transitions/animations — instant screen swap for touch responsiveness.

**Rationale:** Tab-based navigation is standard for industrial HMI (SCADA panels, PLCs). A sidebar with icons works on both 10″ and 15″ screens — text labels shown on 15″, icons-only on 10″. No nested routes keeps navigation simple for operators who aren't tech-savvy. Persistent sidebar means no hidden menus — every screen is one tap away.

### Data Flow Architecture (WebSocket → React State)

How does real-time telemetry from WebSocket populate React components across all screens?

**Decision:** Single WebSocket connection managed by a custom hook (`useWebSocket`). Incoming messages dispatched to a global state store (React Context + useReducer) keyed by topic. Components select the topics they need.

| Aspect | Decision |
|--------|----------|
| WebSocket management | Custom `useWebSocket` hook with auto-reconnect (HMI-13) |
| State store | React Context + useReducer — topics as keys, latest data as values |
| Component subscription | `useTelemetry(topic)` hook returns latest data for a topic |
| Update frequency | Every WebSocket message updates state — React batches renders naturally |
| Reconnection | Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap |
| Connection indicator | Green dot = connected, red dot = disconnected, yellow = reconnecting |

Key rules:
- One WebSocket connection shared across all screens — not one per screen.
- State store holds only the latest value per topic — no history in frontend (history via REST query to logger).
- `useReducer` dispatch is synchronous and fast — no Redux, no external state library.
- Connection status exposed via Context — any component can show the indicator.
- On reconnect, frontend requests a full state snapshot (first message from backend after connect contains all current values).

**Rationale:** React Context + useReducer is sufficient for a single-page kiosk app with ~10 data topics — no need for Redux/Zustand/MobX overhead. A single WebSocket connection is efficient and matches the backend's broadcast architecture (Phase 18). Custom hook pattern is idiomatic React 19. The 30s reconnect cap matches the comm_manager backoff pattern established in M1.

### Chart.js Integration Strategy

How should Chart.js render time-series and energy charts on an embedded ARM display?

**Decision:** Lightweight chart wrappers using react-chartjs-2. Time-series charts use a rolling 5-minute window from WebSocket data. Historical charts (Energy screen) use on-demand REST queries.

| Chart | Screen | Data Source | Type | Max Points |
|-------|--------|-----------|------|------------|
| SOC gauge | Dashboard | WebSocket `system.total_soc` | Doughnut (single value) | 1 |
| Power bar | Dashboard | WebSocket `system.total_power_kw` | Horizontal bar | 1 |
| SOC trend | BMS | WebSocket rolling buffer (5 min) | Line | 300 (1Hz × 5min) |
| Cell voltage range | BMS | WebSocket `bms.rack.*.min_cell_v / max_cell_v` | Bar (min/max per rack) | 16 racks max |
| PCS power trend | PCS | WebSocket rolling buffer (5 min) | Line | 300 |
| Energy totals | Energy | REST `energy_totals` query | Bar (daily/weekly/monthly) | 31 bars max |
| Power history | Energy | REST `time_series` query | Line | 1440 (24h × 1/min) |

Key rules:
- Rolling buffer for real-time charts: circular array of 300 points (5 minutes at 1Hz). Shift on each new data point.
- Chart.js animation disabled for real-time charts — animation causes jank at 1Hz updates on ARM.
- Energy/history charts fetched via REST on screen mount — not from WebSocket (too much data for live streaming).
- react-chartjs-2 provides React component wrappers — no manual Chart.js lifecycle management.
- Canvas element should have fixed pixel dimensions (not percentage) on embedded display — prevents layout thrashing.

**Rationale:** 300-point rolling buffer is the sweet spot: enough for operators to see 5 minutes of trends, low enough for Chart.js to render without lag on ARM Cortex-A53. Disabling animation is standard for industrial HMI — operators need instant visual feedback, not smooth transitions. On-demand REST for historical data keeps the WebSocket lean and matches the logger query API (Phase 12). react-chartjs-2 is the standard React wrapper — 4M+ npm downloads/week.

### Touch Interaction and Responsive Design

How should the UI handle touch input on 10″ and 15″ panels?

**Decision:** Touch-first design with minimum 44×44px tap targets. Two breakpoints: 10″ (1280×800) and 15″ (1920×1080). Tailwind responsive classes handle the differences.

| Aspect | 10″ (1280×800) | 15″ (1920×1080) |
|--------|----------------|------------------|
| Sidebar | Icons only, 60px wide | Icons + labels, 200px wide |
| Grid columns | 2 columns for dashboard cards | 3-4 columns |
| Font size | 14px base | 16px base |
| Chart height | 200px | 300px |
| Table rows visible | 6 per page | 12 per page |
| Tap targets | 44×44px minimum | 44×44px minimum (unchanged) |

Key rules:
- All interactive elements ≥44×44px (Apple HIG touch target minimum — also suits gloved operators).
- No hover states — touch panels don't have hover. Use active/pressed states instead.
- No text input fields except manual setpoint (numeric) and PIN login — minimize on-screen keyboard usage.
- Numeric inputs use a custom numeric keypad overlay, not the system keyboard.
- Scroll only on alarm table and energy history — dashboards and detail screens fit without scrolling.

**Rationale:** 44×44px touch targets are the established minimum for industrial touch panels (IEC 61131-3 recommends 50×50px for safety-critical, but operator screens are non-safety). Two breakpoints match the three deployment profiles (residential=10″, commercial/container=15″). Tailwind responsive classes (sm:/md:/lg:) handle this without media query boilerplate. No hover states because hover is impossible on touch — a common mistake in web-to-kiosk conversions.

### Claude's Discretion

- React component file organization (per-screen directories vs flat)
- Tailwind color palette for dark theme (specific hex values)
- Chart.js configuration options (tooltips, legends, axis formatting)
- WebSocket message parsing and type safety (TypeScript interfaces)
- Error boundaries and loading states per screen
- Build optimization (code splitting, lazy loading per screen)
- Test strategy (Vitest for unit tests, Playwright for E2E if needed)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/hmi_server/frontend/` — React 19 + Vite 6 + Tailwind 4 scaffold (package.json, vite.config.ts, App.tsx stub)
- `src/hmi_server/frontend/src/main.tsx` — React entry point (exists)
- Phase 18 backend — WebSocket on `/ws`, REST on `/api/*`

### Established Patterns
- bun as package manager (global CLAUDE.md)
- TypeScript strict mode (tsconfig.json already configured)
- Vite build outputs to `dist/` (served by FastAPI in production)

### Integration Points
- WebSocket at `ws://localhost:8081/ws` (or configurable) — receives JSON telemetry
- REST API at `http://localhost:8080/api/*` — sends commands, queries data
- Auth via `Authorization: Bearer <token>` header from Phase 18 login endpoint
- Logger query API (Phase 12) via `/api/query/{type}` proxy — energy totals, time series, event log

</code_context>

<specifics>
## Specific Ideas

- Dashboard SOC gauge is the "hero" widget — largest element, center of screen, immediately tells operator system health
- Alarm screen should show severity-colored rows (red=protection, orange=action, yellow=warning)
- Control screen mode selector should clearly show current mode and confirm before switching
- Settings screen schedule editor: visual timeline with draggable windows, or simple form? Form is simpler for M3 — visual editor deferred
- react-chartjs-2 + chart.js dependencies need to be added via `bun add`

</specifics>

<deferred>
## Deferred Ideas

- **HMI-14**: Multi-language support — future requirement
- **HMI-15**: PDF report generation — future requirement
- **HMI-16**: Trend viewer with configurable signal selection — future requirement
- Visual timeline schedule editor (drag-and-drop) — M3 uses simple form, visual deferred
- Dark/light theme toggle — dark only for M3 (industrial standard)
- Keyboard shortcuts — touch-only for M3
- Playwright E2E tests — unit tests sufficient for M3

</deferred>

---

*Phase: 19-hmi-frontend*
*Context gathered: 2026-03-15*
