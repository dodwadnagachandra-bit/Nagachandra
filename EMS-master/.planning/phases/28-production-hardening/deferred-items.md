# Deferred Items — Phase 28

## Pre-existing Test Failures (out of scope for 28-01)

**File:** src/hmi_server/tests/test_app.py

**Tests:**
- `test_spa_fallback_returns_index_html`
- `test_spa_fallback_returns_404_without_frontend`

**Issue:** These tests expect a 404 when `frontend/dist` doesn't exist, but the app actually returns 200 (possibly because the `/{full_path:path}` route returns 200 with a detail message or the schedule_router's PUT route is catching the request). These were failing before Phase 28-01 changes — confirmed by `git stash` showing nothing to stash when tests still failed.

**Action needed:** Investigate SPA fallback route ordering and fix in a future plan.
