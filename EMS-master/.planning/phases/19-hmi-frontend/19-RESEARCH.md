# Phase 19: HMI Frontend - Research

**Researched:** 2026-03-15
**Domain:** React 19 + Vite 6 + Tailwind 4 + Chart.js frontend for industrial EMS kiosk
**Confidence:** HIGH

## Summary

Phase 19 builds a 7-screen React frontend for an Energy Management System kiosk display. The backend (Phase 18) is fully implemented: a FastAPI server with WebSocket telemetry at `/ws/telemetry`, REST endpoints for control (`/api/control/*`), alarms (`/api/alarm/*`), queries (`/api/query/*`), and PIN auth (`/api/auth/*`). The frontend scaffold exists with React 19, Vite 6, and Tailwind 4 already configured but no application code beyond a stub `App.tsx`.

The data flow is well-defined: the backend bridges ZMQ PUB telemetry into WebSocket JSON messages with `{topic, data, ts}` envelope. Six telemetry topics feed the screens (system, bms.rack.C.R, pcs, gpio, meter, btms). Historical data and energy totals come via REST POST to `/api/query/{type}`. Control commands go through REST POST endpoints that proxy to ZMQ REQ/REP.

**Primary recommendation:** Build a single-page app with React Router for 7 screens, a global WebSocket context with useReducer for telemetry state, react-chartjs-2 for charts, and Tailwind dark theme. No external state management library needed.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Tab-based navigation with persistent sidebar (collapsed icons on 10", icons+labels on 15")
- No nested routes -- all 7 screens top-level
- Single WebSocket connection managed by custom `useWebSocket` hook
- React Context + useReducer for global state (keyed by topic)
- `useTelemetry(topic)` hook for component subscription
- Exponential backoff: 1s -> 2s -> 4s -> 8s -> 16s -> 30s cap
- Connection indicator: green=connected, red=disconnected, yellow=reconnecting
- Chart.js animation disabled for real-time charts
- Rolling 300-point buffer (5 min at 1Hz) for real-time charts
- Fixed pixel canvas dimensions for charts on embedded display
- Energy/history charts fetched via REST on screen mount
- Two breakpoints: 10" (1280x800) and 15" (1920x1080)
- 44x44px minimum tap targets
- No hover states -- active/pressed states only
- Numeric inputs use custom numeric keypad overlay
- Scroll only on alarm table and energy history
- Settings tab hidden unless admin-authenticated
- No page transitions/animations
- Dashboard SOC gauge is hero widget (largest, center)
- Alarm rows severity-colored (red=protection, orange=action, yellow=warning)
- Control mode selector with confirmation before switching
- Settings schedule editor: simple form (visual editor deferred)

### Claude's Discretion
- React component file organization (per-screen directories vs flat)
- Tailwind color palette for dark theme (specific hex values)
- Chart.js configuration options (tooltips, legends, axis formatting)
- WebSocket message parsing and type safety (TypeScript interfaces)
- Error boundaries and loading states per screen
- Build optimization (code splitting, lazy loading per screen)
- Test strategy (Vitest for unit tests)

### Deferred Ideas (OUT OF SCOPE)
- HMI-14: Multi-language support
- HMI-15: PDF report generation
- HMI-16: Trend viewer with configurable signal selection
- Visual timeline schedule editor (drag-and-drop)
- Dark/light theme toggle (dark only)
- Keyboard shortcuts (touch-only)
- Playwright E2E tests
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| HMI-05 | Dashboard -- SOC, power, PCS state, control state, alarm count, 1Hz | WebSocket `system` topic has all fields; doughnut chart for SOC, bar for power |
| HMI-06 | BMS screen -- per-rack SOC/V/I, cell v/t ranges, rack selector | WebSocket `bms.rack.C.R` topics; rack fields documented below |
| HMI-07 | PCS screen -- AC/DC telemetry, state, faults, temp, setpoint | WebSocket `pcs` topic; all PCS fields documented below |
| HMI-08 | Alarm screen -- active table, history query, ACK button | REST `/api/alarm/active` + `/api/query/event_log` + `/api/alarm/acknowledge` |
| HMI-09 | Control screen -- state machine, mode selector, setpoint, maintenance | REST `/api/control/mode`, `/api/control/setpoint`, `/api/control/maintenance`, `/api/control/fault-reset` |
| HMI-10 | Energy screen -- charge/discharge totals, bar chart | REST `/api/query/energy_totals` with start_ts/end_ts |
| HMI-11 | Settings screen -- admin-only, schedule editing | REST endpoints needed (Phase 20 may add); schedule_config.yaml shape documented |
| HMI-12 | Tailwind dark theme, responsive 10"/15", Chart.js charts | Tailwind 4 + @tailwindcss/vite already configured; react-chartjs-2 to add |
| HMI-13 | WebSocket auto-reconnect, connection indicator | Custom useWebSocket hook with exponential backoff |
</phase_requirements>

## Existing Code State

### Frontend Scaffold (Minimal)
The scaffold at `src/hmi_server/frontend/` contains:
- `package.json` -- React 19, Vite 6, Tailwind 4, TypeScript 5 (no router, no chart.js)
- `vite.config.ts` -- tailwindcss + react plugins configured
- `tsconfig.json` -- strict mode, ES2022, bundler resolution
- `index.html` -- minimal shell with `#root` div
- `src/main.tsx` -- React 19 createRoot with StrictMode
- `src/App.tsx` -- empty stub (`<h1>EMS HMI</h1>`)
- `bun.lock` -- dependencies installed
- No CSS file yet (Tailwind v4 uses `@import "tailwindcss"` in CSS)
- No router, no pages, no components, no hooks, no types

### Backend (Phase 18 -- Complete)
All backend endpoints are implemented and tested:

**WebSocket:** `ws://host:8081/ws/telemetry`
- Message format: `{ "topic": string, "data": {...}, "ts": number }`
- Topics: `system`, `bms.rack.C.R`, `pcs`, `gpio`, `meter`, `btms`

**REST Endpoints:**
- `POST /api/auth/login` -- body: `{pin: string}` -> `{token, level, expires_in}`
- `POST /api/auth/logout` -- requires Bearer token
- `POST /api/control/mode` -- body: `{target_state: string}` -> `ZmqResponse`
- `POST /api/control/setpoint` -- body: `{power_kw: number}` -> `ZmqResponse`
- `POST /api/control/priority` -- body: `{mode: string}` -> `ZmqResponse`
- `POST /api/control/fault-reset` -- no body -> `ZmqResponse`
- `POST /api/control/maintenance` -- body: `{action: "enter"|"exit"}` -> `ZmqResponse` (admin only)
- `POST /api/alarm/acknowledge` -- body: `{alarm_id: string}` -> `ZmqResponse`
- `GET /api/alarm/active` -- -> `ZmqResponse` with result containing active alarms
- `GET /api/alarm/config` -- -> `ZmqResponse` with alarm config rules
- `POST /api/query/{query_type}` -- body: query params -> `ZmqResponse`

**Auth:** All endpoints except login require `Authorization: Bearer <token>`. Admin-only endpoints (maintenance, settings) check `level == "admin"`.

**ZmqResponse envelope:** `{ status: string, result: dict|null, error_msg: string|null }`

## Standard Stack

### Core (Already Installed)
| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| react | ^19.0.0 | UI framework | Installed |
| react-dom | ^19.0.0 | DOM renderer | Installed |
| tailwindcss | ^4.0.0 | CSS utility framework (dark theme) | Installed |
| @tailwindcss/vite | ^4.0.0 | Vite plugin for Tailwind 4 | Installed |
| vite | ^6.0.0 | Build tool + dev server | Installed |
| typescript | ^5.0.0 | Type safety | Installed |

### To Add
| Library | Purpose | Why Standard |
|---------|---------|--------------|
| react-router-dom | Client-side routing (7 screens) | Standard React SPA router |
| chart.js | Charting engine | Lightweight, ARM-friendly, widely used |
| react-chartjs-2 | React wrapper for Chart.js | Idiomatic React component API |
| lucide-react | Icons (sidebar nav, status indicators) | Tree-shakeable, consistent icon set |

### Not Needed
| Library | Why Not |
|---------|---------|
| Redux / Zustand / MobX | Context + useReducer sufficient for ~10 topics |
| axios | fetch API sufficient for REST calls |
| socket.io-client | Raw WebSocket API sufficient (server uses raw WS) |
| framer-motion | No animations (locked decision) |
| @tanstack/react-query | Simple fetch wrapper sufficient for on-demand REST |

**Installation:**
```bash
cd src/hmi_server/frontend && bun add react-router-dom chart.js react-chartjs-2 lucide-react
```

## Backend API Contracts (TypeScript Interfaces)

### WebSocket Message Envelope
```typescript
// Received from ws://host:8081/ws/telemetry
interface WsMessage {
  topic: string;   // "system" | "bms.rack.0.0" | "pcs" | "gpio" | "meter" | "btms"
  data: Record<string, unknown>;  // Topic-specific payload (see below)
  ts: number;      // Monotonic timestamp in milliseconds
}
```

### Telemetry Data Shapes (from RTDB -> publisher -> WebSocket)

```typescript
// topic: "system"
interface SystemTelemetry {
  last_update_ms: number;
  control_state: number;      // 1=IDLE, 2=STANDBY, 3=CHARGING, 4=DISCHARGING, 5=FAULT, 6=EMERGENCY, 7=MAINTENANCE
  source_priority: number;    // Integer enum for day/night source priority mode
  active_setpoint_kw: number; // Current active power setpoint
  total_soc: number;          // 0.0-100.0 (%)
  total_power_kw: number;     // Positive=charging, negative=discharging
  total_energy_kwh: number;   // Total energy capacity
  ems_uptime_s: number;       // System uptime in seconds
  pcs_command: number;        // 0=NONE, 1=ON, 2=OFF, 3=FAULT_RESET
  pcs_command_seq: number;    // Monotonic command sequence counter
  active_derating_pct: number; // 0.0-100.0 derating percentage
}

// topic: "bms.rack.{cluster}.{rack}" (e.g., "bms.rack.0.0")
interface BmsRackTelemetry {
  last_update_ms: number;
  pack_v: number;       // Pack voltage (V)
  pack_i: number;       // Pack current (A)
  pack_soc: number;     // Pack SOC (%)
  pack_soh: number;     // Pack SOH (%)
  min_cell_v: number;   // Min cell voltage (V)
  max_cell_v: number;   // Max cell voltage (V)
  avg_cell_v: number;   // Average cell voltage (V)
  min_cell_t: number;   // Min cell temperature (C)
  max_cell_t: number;   // Max cell temperature (C)
  avg_cell_t: number;   // Average cell temperature (C)
  fault_code: number;   // Rack fault bitmask
  online: number;       // 0 or 1
}

// topic: "pcs"
interface PcsTelemetry {
  last_update_ms: number;
  ac_voltage: number;       // AC voltage (V)
  ac_current: number;       // AC current (A)
  active_power: number;     // Active power (kW)
  reactive_power: number;   // Reactive power (kVAR)
  dc_voltage: number;       // DC bus voltage (V)
  dc_current: number;       // DC current (A)
  frequency: number;        // Grid frequency (Hz)
  temperature: number;      // PCS temperature (C)
  state: number;            // 0=OFF, 1=STANDBY, 2=RUNNING, 3=FAULT
  fault_code: number;       // PCS fault bitmask
}

// topic: "gpio"
interface GpioTelemetry {
  last_update_ms: number;
  di: number[];       // 8 digital inputs [0|1] (E-Stop, fire, flood, etc.)
  do_state: number[]; // 8 digital outputs [0|1] (PCS stop, ACDB trip, siren)
}

// topic: "meter"
interface MeterTelemetry {
  last_update_ms: number;
  voltage: number;         // Grid voltage (V)
  current: number;         // Grid current (A)
  active_power: number;    // Grid active power (kW)
  reactive_power: number;  // Grid reactive power (kVAR)
  frequency: number;       // Grid frequency (Hz)
  power_factor: number;    // Power factor
  energy_import: number;   // Total imported energy (kWh)
  energy_export: number;   // Total exported energy (kWh)
}

// topic: "btms"
interface BtmsTelemetry {
  last_update_ms: number;
  inlet_temp: number;      // Inlet temperature (C)
  outlet_temp: number;     // Outlet temperature (C)
  fan_speed_pct: number;   // Fan speed (0-100%)
  cooling_active: number;  // 0 or 1
}
```

### Control State Enum (for display mapping)
```typescript
const CONTROL_STATE_NAMES: Record<number, string> = {
  1: "IDLE",
  2: "STANDBY",
  3: "CHARGING",
  4: "DISCHARGING",
  5: "FAULT",
  6: "EMERGENCY",
  7: "MAINTENANCE",
};

const PCS_STATE_NAMES: Record<number, string> = {
  0: "OFF",
  1: "STANDBY",
  2: "RUNNING",
  3: "FAULT",
};
```

### REST Request/Response Types
```typescript
// Auth
interface LoginRequest { pin: string; }
interface LoginResponse { token: string; level: "operator" | "admin"; expires_in: number; }

// Control
interface ModeChangeRequest { target_state: string; }  // "standby" | "idle" | "charging" | "discharging"
interface ManualSetpointRequest { power_kw: number; }
interface SourcePriorityRequest { mode: string; }
interface MaintenanceRequest { action: "enter" | "exit"; }

// Alarm
interface AcknowledgeRequest { alarm_id: string; }
// Active alarm from GET /api/alarm/active -> result contains alarm list
interface ActiveAlarm {
  alarm_id: string;
  signal: string;
  severity: "warning" | "action" | "protection";
  state: "ACTIVE_UNACKED" | "ACTIVE_ACKED" | "CLEARED_UNACKED";
  activated_at: number;  // timestamp ms
}

// Query
// POST /api/query/energy_totals body: { start_ts: number, end_ts: number }
// Response result: { columns: ["discharge_wh","charge_wh","grid_import_wh","grid_export_wh"], rows: [[...]], count: 1 }

// POST /api/query/time_series body: { signals: string[], start_ts: number, end_ts: number, interval_s: number }
// Response result: { columns: string[], rows: any[][], count: number }

// POST /api/query/event_log body: { start_ts: number, end_ts: number, severity_filter?: string, source_filter?: string }
// Response result: { columns: string[], rows: any[][], count: number }

// ZMQ Response envelope (all REST command responses)
interface ZmqResponse {
  status: "ok" | "error";
  result: Record<string, unknown> | null;
  error_msg: string | null;
}
```

### Schedule Config Shape (for Settings screen editor)
```typescript
interface ScheduleConfig {
  mode: "manual" | "time_of_day" | "curve";
  time_windows: TimeWindow[];
  day_night: { day_start: string; night_start: string; };  // "HH:MM"
  power_curve: number[];  // 96 entries (15-min intervals)
}

interface TimeWindow {
  start: string;   // "HH:MM"
  end: string;     // "HH:MM"
  action: "charge" | "discharge" | "idle";
  power_kw: number;
}
```

## Architecture Patterns

### Recommended Project Structure
```
src/hmi_server/frontend/src/
├── main.tsx                    # Entry point
├── App.tsx                     # Router + layout shell
├── app.css                     # Tailwind imports + dark theme vars
├── types/
│   ├── telemetry.ts            # WebSocket telemetry interfaces
│   ├── api.ts                  # REST request/response interfaces
│   └── enums.ts                # Control state, PCS state mappings
├── hooks/
│   ├── useWebSocket.ts         # WebSocket connection + auto-reconnect
│   ├── useTelemetry.ts         # Subscribe to specific topic from context
│   ├── useAuth.ts              # Auth state + login/logout
│   └── useApi.ts               # REST fetch helper with Bearer token
├── context/
│   ├── TelemetryContext.tsx     # Global telemetry state (useReducer)
│   ├── AuthContext.tsx          # Auth token + level state
│   └── ConnectionContext.tsx    # WebSocket connection status
├── components/
│   ├── Layout.tsx              # Sidebar + content area shell
│   ├── Sidebar.tsx             # Navigation tabs with icons
│   ├── ConnectionIndicator.tsx # Green/red/yellow dot
│   ├── NumericKeypad.tsx       # Custom numeric input overlay
│   ├── ConfirmDialog.tsx       # Modal confirmation for commands
│   └── charts/
│       ├── SocGauge.tsx        # Doughnut chart for SOC
│       ├── PowerBar.tsx        # Horizontal bar for power
│       ├── TrendLine.tsx       # Rolling 5-min line chart
│       └── EnergyBar.tsx       # Bar chart for energy totals
├── screens/
│   ├── Dashboard.tsx           # HMI-05
│   ├── BmsScreen.tsx           # HMI-06
│   ├── PcsScreen.tsx           # HMI-07
│   ├── AlarmScreen.tsx         # HMI-08
│   ├── ControlScreen.tsx       # HMI-09
│   ├── EnergyScreen.tsx        # HMI-10
│   ├── SettingsScreen.tsx      # HMI-11
│   └── LoginScreen.tsx         # PIN entry
└── lib/
    ├── api.ts                  # fetch wrapper with auth header
    ├── ws.ts                   # WebSocket class with reconnect logic
    └── rolling-buffer.ts       # Circular array for chart data
```

### Pattern 1: WebSocket with Auto-Reconnect (HMI-13)
**What:** Custom hook manages a single WebSocket connection, dispatches messages to context reducer, handles reconnection with exponential backoff.
**When to use:** App-level -- created once in root provider.

```typescript
// useWebSocket hook sketch
type ConnectionStatus = "connected" | "disconnected" | "reconnecting";

function useWebSocket(url: string, dispatch: React.Dispatch<TelemetryAction>) {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const retriesRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket;
    let timeoutId: number;

    function connect() {
      setStatus("reconnecting");
      ws = new WebSocket(url);

      ws.onopen = () => {
        setStatus("connected");
        retriesRef.current = 0;
      };

      ws.onmessage = (event) => {
        const msg: WsMessage = JSON.parse(event.data);
        dispatch({ type: "UPDATE_TOPIC", topic: msg.topic, data: msg.data, ts: msg.ts });
      };

      ws.onclose = () => {
        setStatus("disconnected");
        const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
        retriesRef.current++;
        timeoutId = window.setTimeout(connect, delay);
      };
    }

    connect();
    return () => { ws?.close(); clearTimeout(timeoutId); };
  }, [url, dispatch]);

  return status;
}
```

### Pattern 2: Telemetry Context + useReducer
**What:** Global state store keyed by topic. Each WebSocket message replaces the latest value for its topic.

```typescript
interface TelemetryState {
  system: SystemTelemetry | null;
  pcs: PcsTelemetry | null;
  gpio: GpioTelemetry | null;
  meter: MeterTelemetry | null;
  btms: BtmsTelemetry | null;
  bmsRacks: Record<string, BmsRackTelemetry>;  // key: "0.0", "0.1", etc.
  lastUpdate: number;
}

type TelemetryAction =
  | { type: "UPDATE_TOPIC"; topic: string; data: Record<string, unknown>; ts: number };

function telemetryReducer(state: TelemetryState, action: TelemetryAction): TelemetryState {
  if (action.type === "UPDATE_TOPIC") {
    const { topic, data, ts } = action;
    if (topic === "system") return { ...state, system: data as SystemTelemetry, lastUpdate: ts };
    if (topic === "pcs") return { ...state, pcs: data as PcsTelemetry, lastUpdate: ts };
    if (topic === "gpio") return { ...state, gpio: data as GpioTelemetry, lastUpdate: ts };
    if (topic === "meter") return { ...state, meter: data as MeterTelemetry, lastUpdate: ts };
    if (topic === "btms") return { ...state, btms: data as BtmsTelemetry, lastUpdate: ts };
    if (topic.startsWith("bms.rack.")) {
      const rackKey = topic.replace("bms.rack.", "");  // "0.0", "0.1", etc.
      return { ...state, bmsRacks: { ...state.bmsRacks, [rackKey]: data as BmsRackTelemetry }, lastUpdate: ts };
    }
  }
  return state;
}
```

### Pattern 3: Auth Context with Token Management
**What:** Context holds token, level, and provides login/logout. Token sent as Bearer header on all REST calls.

```typescript
interface AuthState {
  token: string | null;
  level: "operator" | "admin" | null;
  expiresAt: number | null;
}

// Login: POST /api/auth/login {pin} -> store token
// Logout: POST /api/auth/logout -> clear token
// All REST calls: Authorization: Bearer <token>
```

### Pattern 4: Rolling Buffer for Real-Time Charts
**What:** Fixed-size circular array that shifts on each new data point. Used for 5-minute trend lines.

```typescript
class RollingBuffer<T> {
  private buffer: T[];
  private maxSize: number;

  constructor(maxSize: number) {
    this.buffer = [];
    this.maxSize = maxSize;
  }

  push(value: T): void {
    this.buffer.push(value);
    if (this.buffer.length > this.maxSize) {
      this.buffer.shift();
    }
  }

  getAll(): T[] {
    return this.buffer;
  }
}
// Usage: const socBuffer = new RollingBuffer<{ts: number, value: number}>(300);
```

### Pattern 5: Lazy Loading Screens
**What:** React.lazy + Suspense for code-splitting per screen. Keeps initial bundle small.

```typescript
const Dashboard = React.lazy(() => import("./screens/Dashboard"));
const BmsScreen = React.lazy(() => import("./screens/BmsScreen"));
// etc.

// In router:
<Suspense fallback={<LoadingSpinner />}>
  <Routes>
    <Route path="/" element={<Dashboard />} />
    <Route path="/bms" element={<BmsScreen />} />
    ...
  </Routes>
</Suspense>
```

### Anti-Patterns to Avoid
- **Multiple WebSocket connections:** Never create a WebSocket per screen. One connection, one context.
- **Storing history in React state:** Only store latest value per topic. History comes from REST queries to logger.
- **Using setInterval for polling:** WebSocket pushes at 1Hz. No polling needed for live data.
- **Chart.js animations on ARM:** Always set `animation: false` for real-time charts. Animation at 1Hz causes jank on Cortex-A53.
- **CSS percentage dimensions for charts:** Use fixed pixel dimensions. Percentage causes layout recalculation on every update.
- **Hover-dependent interactions:** No `:hover` styles. Touch panels have no cursor. Use `:active` or tap handlers.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Icons | Custom SVG management | lucide-react | Tree-shakeable, consistent, 1000+ icons |
| Charts | Canvas drawing code | chart.js + react-chartjs-2 | Handles axes, tooltips, responsive sizing |
| Routing | Conditional rendering | react-router-dom | URL state, browser history, lazy loading |
| CSS reset/utilities | Custom stylesheets | Tailwind CSS 4 | Consistent design tokens, dark mode built-in |
| WebSocket reconnect | Manual retry logic | Custom hook (simple enough) | Only ~30 lines with exponential backoff |
| Date formatting | Manual string ops | Intl.DateTimeFormat | Built into browser, locale-aware |
| Number formatting | Manual rounding | Intl.NumberFormat | Handles precision, units consistently |

## Common Pitfalls

### Pitfall 1: Tailwind v4 CSS Import
**What goes wrong:** No styles appear because Tailwind v4 has no `tailwind.config.js` and requires `@import "tailwindcss"` in CSS.
**Why it happens:** Tailwind v4 is CSS-first config, completely different from v3.
**How to avoid:** Create `src/app.css` with `@import "tailwindcss";` and import it in `main.tsx`. Use `@theme` directive for custom colors.
**Warning signs:** All Tailwind classes render as plain text with no styling.

### Pitfall 2: WebSocket URL in Development vs Production
**What goes wrong:** WebSocket connects to wrong host/port.
**Why it happens:** Dev server runs on different port than backend.
**How to avoid:** Use Vite proxy in `vite.config.ts` for dev, relative URL in production. WebSocket URL: `ws://${window.location.host}/ws/telemetry` in prod, proxied in dev.
**Config:**
```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      "/ws": { target: "ws://localhost:8081", ws: true },
      "/api": { target: "http://localhost:8080" },
    },
  },
});
```

### Pitfall 3: React Re-Renders on Every WebSocket Message
**What goes wrong:** Entire app re-renders at 1Hz because context value changes.
**Why it happens:** Naive context setup causes all consumers to re-render.
**How to avoid:** Use separate contexts for connection status vs telemetry data. Consider `useSyncExternalStore` or split context per topic group. At minimum, memoize screen components that don't use frequently-changing data.
**Warning signs:** UI feels sluggish, React DevTools shows unnecessary renders.

### Pitfall 4: Chart.js Registration
**What goes wrong:** "X is not a registered controller/element" error.
**Why it happens:** Chart.js v4 requires explicit registration of components.
**How to avoid:** Register all needed components at app entry:
```typescript
import { Chart, ArcElement, BarElement, LineElement, PointElement,
  CategoryScale, LinearScale, TimeScale, Tooltip, Legend, Filler } from "chart.js";
Chart.register(ArcElement, BarElement, LineElement, PointElement,
  CategoryScale, LinearScale, TimeScale, Tooltip, Legend, Filler);
```

### Pitfall 5: BMS Rack Topic Key Parsing
**What goes wrong:** BMS rack data not updating or keyed incorrectly.
**Why it happens:** Topic format is `bms.rack.{cluster}.{rack}` (e.g., `bms.rack.0.0`). Need to parse cluster and rack indices.
**How to avoid:** Strip `bms.rack.` prefix, use remaining `"C.R"` as key in state map. Display rack selector based on available keys.

### Pitfall 6: Token Expiry Mid-Session
**What goes wrong:** REST calls fail with 403 after session timeout.
**Why it happens:** Token expires after `session_timeout_s` (default 1800s / 30 min).
**How to avoid:** Track expiry time in auth context. Show re-login prompt before/on expiry. Handle 401/403 responses globally in fetch wrapper.

## Code Examples

### Tailwind v4 Dark Theme Setup
```css
/* src/app.css */
@import "tailwindcss";

@theme {
  --color-bg-primary: #0f172a;      /* slate-900 */
  --color-bg-secondary: #1e293b;    /* slate-800 */
  --color-bg-card: #334155;         /* slate-700 */
  --color-text-primary: #f1f5f9;    /* slate-100 */
  --color-text-secondary: #94a3b8;  /* slate-400 */
  --color-accent: #3b82f6;          /* blue-500 */
  --color-success: #22c55e;         /* green-500 */
  --color-warning: #eab308;         /* yellow-500 */
  --color-danger: #ef4444;          /* red-500 */
  --color-orange: #f97316;          /* orange-500 */
}

html {
  background-color: var(--color-bg-primary);
  color: var(--color-text-primary);
}
```

### Vite Proxy Configuration for Development
```typescript
// vite.config.ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), react()],
  server: {
    proxy: {
      "/ws/telemetry": {
        target: "ws://localhost:8081",
        ws: true,
      },
      "/api": {
        target: "http://localhost:8080",
      },
    },
  },
});
```

### Authenticated Fetch Wrapper
```typescript
// lib/api.ts
async function apiFetch<T>(
  path: string,
  token: string | null,
  options?: RequestInit,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const res = await fetch(path, { ...options, headers: { ...headers, ...options?.headers } });

  if (res.status === 401 || res.status === 403) {
    // Token expired or invalid -- trigger re-login
    throw new AuthError(res.status, await res.text());
  }

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail || "Unknown error");
  }

  return res.json();
}
```

### SOC Doughnut Chart (Dashboard Hero Widget)
```typescript
// components/charts/SocGauge.tsx
import { Doughnut } from "react-chartjs-2";

interface SocGaugeProps {
  soc: number;  // 0-100
}

function SocGauge({ soc }: SocGaugeProps) {
  const data = {
    datasets: [{
      data: [soc, 100 - soc],
      backgroundColor: [
        soc > 20 ? "#22c55e" : soc > 10 ? "#eab308" : "#ef4444",
        "#334155",
      ],
      borderWidth: 0,
      cutout: "75%",
    }],
  };

  const options = {
    responsive: false,
    animation: false as const,
    plugins: { tooltip: { enabled: false }, legend: { display: false } },
  };

  return (
    <div className="relative">
      <Doughnut data={data} options={options} width={200} height={200} />
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-3xl font-bold">{soc.toFixed(1)}%</span>
      </div>
    </div>
  );
}
```

### Alarm Severity Color Map
```typescript
const SEVERITY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  protection: { bg: "bg-red-900/50", text: "text-red-400", border: "border-red-500" },
  action:     { bg: "bg-orange-900/50", text: "text-orange-400", border: "border-orange-500" },
  warning:    { bg: "bg-yellow-900/50", text: "text-yellow-400", border: "border-yellow-500" },
};
```

## State of the Art

| Aspect | Implementation | Notes |
|--------|---------------|-------|
| Tailwind v4 | CSS-first config via `@import "tailwindcss"` + `@theme` | No tailwind.config.js needed |
| React 19 | Stable release, no major API changes from 18 for this use case | use() hook available but not needed |
| Chart.js 4 | Tree-shakeable, explicit component registration | Must register elements manually |
| Vite 6 | Stable, fast HMR, built-in proxy | No config changes from v5 for this use case |
| react-router-dom v7 | Latest stable | Use `<Routes>` / `<Route>` pattern |

## Open Questions

1. **Settings screen schedule save endpoint**
   - What we know: Settings screen needs to save schedule config. Phase 20 (scheduler) will consume `schedule_config.yaml`.
   - What's unclear: No REST endpoint exists yet for writing schedule config. Phase 18 backend has no PATCH/PUT for config.
   - Recommendation: Build the Settings UI form. For the save action, either (a) add a `/api/config/schedule` PUT endpoint to Phase 18 backend as a small extension, or (b) show the form as read-only for M3 with a "save not yet implemented" message. The form shape matches `ScheduleConfig` interface above.

2. **Number of BMS racks to display**
   - What we know: RTDB supports up to 8 clusters x 16 racks. Residential profile likely has 1 cluster x 1-4 racks.
   - What's unclear: How many racks will appear in WebSocket data at runtime.
   - Recommendation: Dynamically build rack selector from keys present in `bmsRacks` state map. No hardcoded rack count.

3. **Alarm history data shape**
   - What we know: `event_log` query returns columns/rows format from JSONL files.
   - What's unclear: Exact column names in event_log response.
   - Recommendation: Parse columns dynamically from response. Display a generic table with sortable columns.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Vitest (latest, bundled with Vite ecosystem) |
| Config file | none -- Wave 0 must create `vitest.config.ts` |
| Quick run command | `cd src/hmi_server/frontend && bun run test` |
| Full suite command | `cd src/hmi_server/frontend && bun run test -- --run` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HMI-05 | Dashboard renders SOC, power, state from system telemetry | unit | `bun run test -- --run src/__tests__/Dashboard.test.tsx` | No -- Wave 0 |
| HMI-06 | BMS screen renders rack data, rack selector works | unit | `bun run test -- --run src/__tests__/BmsScreen.test.tsx` | No -- Wave 0 |
| HMI-07 | PCS screen renders AC/DC telemetry fields | unit | `bun run test -- --run src/__tests__/PcsScreen.test.tsx` | No -- Wave 0 |
| HMI-08 | Alarm screen shows active alarms, ACK button calls API | unit | `bun run test -- --run src/__tests__/AlarmScreen.test.tsx` | No -- Wave 0 |
| HMI-09 | Control screen mode selector, setpoint, maintenance | unit | `bun run test -- --run src/__tests__/ControlScreen.test.tsx` | No -- Wave 0 |
| HMI-10 | Energy screen fetches totals, renders bar chart | unit | `bun run test -- --run src/__tests__/EnergyScreen.test.tsx` | No -- Wave 0 |
| HMI-11 | Settings screen form renders, admin-only access | unit | `bun run test -- --run src/__tests__/SettingsScreen.test.tsx` | No -- Wave 0 |
| HMI-12 | Dark theme classes applied, responsive breakpoints | unit | `bun run test -- --run src/__tests__/Layout.test.tsx` | No -- Wave 0 |
| HMI-13 | WebSocket hook reconnects with backoff | unit | `bun run test -- --run src/__tests__/useWebSocket.test.ts` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `cd src/hmi_server/frontend && bun run test -- --run`
- **Per wave merge:** Full suite
- **Phase gate:** Full suite green

### Wave 0 Gaps
- [ ] `vitest.config.ts` -- Vitest configuration
- [ ] `package.json` test script update: `"test": "vitest"`
- [ ] `bun add -D vitest @testing-library/react @testing-library/jest-dom jsdom` -- test deps
- [ ] `src/__tests__/` directory -- all test files listed above

## Sources

### Primary (HIGH confidence)
- Codebase: `src/hmi_server/src/ems_hmi_server/ws.py` -- WebSocket message format verified
- Codebase: `src/hmi_server/src/ems_hmi_server/control.py` -- Control REST endpoints verified
- Codebase: `src/hmi_server/src/ems_hmi_server/alarm.py` -- Alarm REST endpoints verified
- Codebase: `src/hmi_server/src/ems_hmi_server/query.py` -- Query REST endpoints verified
- Codebase: `src/hmi_server/src/ems_hmi_server/auth.py` -- Auth flow verified
- Codebase: `src/hmi_server/src/ems_hmi_server/models.py` -- Pydantic models verified
- Codebase: `src/data_manager/python/src/ems_data_manager/publisher.py` -- Telemetry payload shapes verified
- Codebase: `src/common/python/src/ems_common/rtdb.py` -- RTDB struct fields verified
- Codebase: `src/control_manager/python/src/ems_control_manager/state_machine.py` -- Control state enums verified
- Codebase: `src/alarm_manager/src/ems_alarm_manager/evaluator.py` -- Alarm state/severity enums verified
- Codebase: `config/hmi_config.yaml` -- Display config verified
- Codebase: `config/schedule_config.yaml` -- Schedule config shape verified

### Secondary (MEDIUM confidence)
- React 19, Vite 6, Tailwind 4, Chart.js 4 -- library APIs from training data, stable releases

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all dependencies verified against existing package.json and codebase patterns
- Architecture: HIGH -- data flow fully traceable from RTDB -> publisher -> ZMQ -> WS bridge -> WebSocket -> React
- API contracts: HIGH -- all endpoints, models, and payloads read directly from source code
- Pitfalls: HIGH -- based on known React/Chart.js/Tailwind patterns and codebase specifics
- Tailwind v4 specifics: MEDIUM -- CSS-first config is well documented but relatively new

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable libraries, locked backend)
