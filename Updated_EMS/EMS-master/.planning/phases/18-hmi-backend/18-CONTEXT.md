# Phase 18: HMI Backend - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

FastAPI backend serving React build, WebSocket telemetry streaming, REST command proxy to control/alarm managers, and PIN-based authentication. Covers HMI-01, HMI-02, HMI-03, HMI-04. Python only (FastAPI + uvicorn).

</domain>

<decisions>
## Implementation Decisions

### WebSocket Telemetry Architecture

How does the backend bridge ZMQ PUB/SUB telemetry (binary MessagePack) to browser WebSocket clients (JSON)?

**Decision:** A single background asyncio task subscribes to ZMQ telemetry PUB, deserializes MessagePack, and broadcasts JSON to all connected WebSocket clients via an in-memory fan-out.

| Aspect | Decision |
|--------|----------|
| ZMQ subscription | One SUB socket subscribing to all topics (system, bms.rack.*, pcs, gpio, meter, btms) |
| Deserialization | MessagePack → Python dict (already done by decode_telemetry from ems_common/ipc.py) |
| Client broadcast | asyncio.Queue per connected WebSocket client — non-blocking put, drop on full |
| Client message format | JSON with `{topic: str, data: dict, ts: int}` envelope |
| Backpressure | Queue max size = 100 messages per client; oldest dropped if client is slow |
| Reconnection | Client-side responsibility (HMI-13 in Phase 19) |

Key rules:
- One ZMQ SUB → many WebSocket clients. ZMQ is the single source, not one SUB per client.
- JSON (not MessagePack) to browser — native browser parsing, no library needed on frontend.
- Per-client queue prevents a slow client from blocking others.
- Queue overflow drops oldest message (not newest) — client always gets the latest data.
- Backend does NOT filter topics per client — all clients get all telemetry. Topic filtering is a future optimization.

**Rationale:** Single ZMQ SUB is efficient (one subscription regardless of client count). Per-client queues with overflow follow the standard WebSocket broadcast pattern used by FastAPI/Starlette. JSON is the only practical format for browsers without adding a MessagePack client library. Dropping oldest on overflow ensures clients always display current state, not stale data.

### REST Command Proxy Design

How do REST endpoints translate HTTP requests to ZMQ REQ/REP commands for control_manager and alarm_manager?

**Decision:** Thin proxy — each REST endpoint maps 1:1 to a ZMQ command. FastAPI validates the HTTP request body, constructs the ZMQ command envelope, sends REQ, waits for REP, and returns the response as JSON.

| REST Endpoint | Method | ZMQ Target | ZMQ Action | Auth Level |
|--------------|--------|-----------|------------|------------|
| `/api/control/mode` | POST | control_cmd | mode_change | operator |
| `/api/control/setpoint` | POST | control_cmd | manual_setpoint | operator |
| `/api/control/priority` | POST | control_cmd | source_priority | operator |
| `/api/control/fault-reset` | POST | control_cmd | fault_reset | operator |
| `/api/control/maintenance` | POST | control_cmd | maintenance_enter/exit | admin |
| `/api/alarm/acknowledge` | POST | alarm_cmd | acknowledge | operator |
| `/api/alarm/active` | GET | alarm_cmd | get_active_alarms | operator |
| `/api/alarm/config` | GET | alarm_cmd | get_alarm_config | operator |
| `/api/query/{type}` | POST | logger_query | time_series/latest/range_stats/event_log/energy_totals/cell_snapshot | operator |
| `/api/health` | GET | — | Health check (no ZMQ) | none |

Key rules:
- Each endpoint uses a dedicated ZMQ REQ socket with a timeout (5s). If ZMQ times out, return HTTP 504 (Gateway Timeout).
- ZMQ sockets are created per-request (REQ is synchronous — can't share across concurrent requests). FastAPI runs in asyncio, so use `run_in_executor` for blocking ZMQ send/recv.
- Request body validated via Pydantic models (FastAPI native). Invalid requests return 422 before touching ZMQ.
- Maintenance enter/exit requires admin auth level — all other commands require operator.
- `/api/health` returns 200 with `{status: "ok", uptime_s: int}` — no auth needed, used for load balancer/monitoring.

**Rationale:** 1:1 mapping keeps the proxy thin and maintainable — no business logic in the HMI backend. ZMQ REQ per-request avoids socket sharing complexity in async context. 5-second timeout matches the logger query timeout (Phase 12). Pydantic validation reuses FastAPI's native capabilities. The REST paths follow REST conventions (/api/{resource}/{action}).

### PIN Authentication Mechanism

How does PIN auth work — session tokens, cookies, or per-request headers?

**Decision:** Bearer token in Authorization header. Login endpoint issues a JWT-like opaque token stored in memory (not a database). Token has an expiry matching session_timeout_s from config.

| Aspect | Decision |
|--------|----------|
| Login endpoint | POST `/api/auth/login` with `{pin: "1234"}` |
| Token format | Random 32-byte hex string (not JWT — no need for claims verification across services) |
| Token storage | In-memory dict `{token: {level: "operator"\|"admin", expires_at: float}}` |
| Token delivery | Response: `{token: str, level: str, expires_in: int}` |
| Token usage | `Authorization: Bearer <token>` header on all protected requests |
| Expiry | Configurable session_timeout_s (default 1800s = 30 min) |
| Logout | POST `/api/auth/logout` — removes token from memory |
| PIN validation | bcrypt.checkpw against hmi_config.yaml operator_pin_hash / admin_pin_hash |
| Max sessions | 10 concurrent (prevent memory bloat from leaked tokens) |

Key rules:
- Operator PIN checked first, then admin PIN. If PIN matches admin, token gets admin level.
- Tokens are memory-only — lost on restart. Users re-login after restart (acceptable for embedded kiosk).
- Expired tokens cleaned up lazily (checked on each auth middleware call) + periodic sweep every 60s.
- No HTTPS in M3 (local network, embedded kiosk). HTTPS deferred to M4 when cloud connectivity adds TLS.
- CORS configured to allow the frontend origin (same-origin when served by FastAPI, or localhost:5173 in dev).

**Rationale:** Opaque tokens are simpler than JWT for a single-server embedded system — no secret key management, no expiry clock skew issues, no token size overhead. bcrypt PIN hashing is already specified in hmi_config.yaml schema. In-memory storage is sufficient because the HMI server is a single-process, single-user kiosk — not a distributed system. Max 10 sessions prevents a misbehaving client from accumulating unbounded tokens.

### FastAPI Application Structure

How should the FastAPI app be organized — single file or modular?

**Decision:** Modular with router-based organization. One router per functional area.

| Module | Router Prefix | Responsibility |
|--------|--------------|----------------|
| `app.py` | — | FastAPI app factory, lifespan (ZMQ init/cleanup), static file mount |
| `auth.py` | `/api/auth` | Login, logout, middleware, token store |
| `control.py` | `/api/control` | Proxy to control_cmd ZMQ socket |
| `alarm.py` | `/api/alarm` | Proxy to alarm_cmd ZMQ socket |
| `query.py` | `/api/query` | Proxy to logger_query ZMQ socket |
| `ws.py` | `/ws` | WebSocket endpoint, ZMQ SUB bridge, client management |
| `health.py` | `/api/health` | Health check endpoint |

Key rules:
- `app.py` uses FastAPI's lifespan context manager to initialize ZMQ context and start the telemetry bridge task on startup, and clean up on shutdown.
- Static files mounted at `/` with `StaticFiles(directory="frontend/dist")` — React build served directly.
- `index.html` served as fallback for client-side routing (React Router).
- All routers import from a shared `deps.py` module that provides ZMQ socket factories and auth dependency injection.

**Rationale:** Router-based organization follows FastAPI best practices and keeps each file under 200 lines. The lifespan context manager is the modern FastAPI pattern (replacing deprecated `@app.on_event`). Static file serving avoids needing nginx/caddy in production — single process serves both API and frontend.

### Claude's Discretion

- Uvicorn configuration (workers, host binding, log level)
- ZMQ socket creation pattern in async context (run_in_executor vs asyncio poller)
- WebSocket message batching (send every message vs aggregate per-topic per second)
- Pydantic model definitions for request/response validation
- Error response format (FastAPI HTTPException vs custom error handler)
- Test strategy (httpx.AsyncClient for FastAPI testing, WebSocket test patterns)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/hmi_server/` — Stub package (v0.1.0, depends on ems-common)
- `src/hmi_server/frontend/` — React 19 + Vite 6 + Tailwind 4 scaffold (package.json, vite.config.ts, App.tsx stub)
- `config/hmi_config.yaml` — Server ports, PIN hashes, display settings, kiosk mode
- `config/schemas/hmi_config.schema.json` — Full schema with auth, display, server sections
- `deploy/systemd/hmi_server.service` — Service file (After=data_manager)
- `src/common/python/src/ems_common/ipc.py` — All ZMQ socket paths, encode/decode helpers
- `src/common/python/src/ems_common/rtdb.py` — RTDB struct definitions for telemetry data shape

### Established Patterns
- Async Python modules with SIGTERM/SIGINT handlers (all M1+M2 modules)
- ZMQ SUB for telemetry consumption (logger pattern from Phase 12)
- ZMQ REQ for command dispatch (scheduler will use same pattern in Phase 20)
- Config loading via yaml.safe_load with JSON Schema validation
- MessagePack encode/decode via ems_common/ipc.py helpers

### Integration Points
- ZMQ SUB on SOCK_TELEMETRY for 1Hz telemetry (data_manager publishes)
- ZMQ REQ on SOCK_CONTROL_CMD for control commands (Phase 14 API)
- ZMQ REQ on SOCK_ALARM_CMD for alarm queries/acknowledge (Phase 15 API)
- ZMQ REQ on SOCK_LOGGER_QUERY for historical data queries (Phase 12 API)
- Static files: `frontend/dist/` built by `bun run build`

</code_context>

<specifics>
## Specific Ideas

- FastAPI + uvicorn is the standard async Python web framework — no alternatives needed
- bcrypt dependency must be added to hmi_server pyproject.toml
- WebSocket endpoint should handle connection lifecycle (open, message, close, error) gracefully
- Frontend dev server (vite dev on :5173) needs CORS to talk to backend on :8080 during development
- Production: React build is a static bundle under frontend/dist/ — no Node.js runtime needed

</specifics>

<deferred>
## Deferred Ideas

- HTTPS/TLS for HMI server — deferred to M4 (cloud connectivity adds TLS infrastructure)
- WebSocket topic filtering per client — all clients get all topics for now
- Rate limiting on REST endpoints — embedded kiosk, single user, not needed
- Audit logging of HMI commands — logger already captures state_change events from control_manager
- Multi-language support — HMI-14 future requirement
- PDF report generation — HMI-15 future requirement

</deferred>

---

*Phase: 18-hmi-backend*
*Context gathered: 2026-03-15*
