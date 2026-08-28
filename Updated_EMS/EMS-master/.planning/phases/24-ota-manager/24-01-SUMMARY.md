---
phase: 24-ota-manager
plan: 01
subsystem: ota
tags: [ed25519, cryptography, httpx, sha256, ab-partition, ota, firmware]

# Dependency graph
requires:
  - phase: 23-offline-buffer
    provides: SOCK_ALARM_PUB and ems_common.ipc patterns used here

provides:
  - Ed25519 manifest verifier with real key-pair verification (PackageVerifier)
  - Streaming HTTP downloader with Range resume and SHA-256 integrity (HttpDownloader)
  - A/B partition backend with atomic boot flag writes (PartitionBackend, BootFlag)
  - OTA config loader with JSON Schema validation (load_ota_config)
  - IPC constants SOCK_OTA_PUB, SOCK_OTA_CMD, TOPIC_OTA in ems_common.ipc
  - ota_config.yaml + ota_config.schema.json configuration foundation

affects: [24-ota-manager plan 02 state machine, 25-diagnostics]

# Tech tracking
tech-stack:
  added:
    - cryptography==46.0.5 (Ed25519PublicKey, sign/verify)
    - httpx==0.28.1 (async streaming download, MockTransport for tests)
    - cffi==2.0.0, pycparser==3.0 (cryptography deps)
    - anyio==4.12.1, h11==0.16.0, httpcore==1.0.9 (httpx deps)
  patterns:
    - OTA config loader mirrors cloud_manager/config.py pattern (YAML + JSON Schema)
    - Atomic file write via .tmp + rename (same pattern used for boot flags)
    - MockTransport injection via constructor param for zero-dependency async tests
    - Streaming SHA-256: pre-hash existing .partial bytes before resuming Range request
    - TDD RED commit before GREEN for both tasks

key-files:
  created:
    - src/ota_manager/src/ems_ota_manager/config.py
    - src/ota_manager/src/ems_ota_manager/verifier.py
    - src/ota_manager/src/ems_ota_manager/partition.py
    - src/ota_manager/src/ems_ota_manager/downloader.py
    - config/ota_config.yaml
    - config/schemas/ota_config.schema.json
    - tests/test_ota_manager.py
  modified:
    - src/common/python/src/ems_common/ipc.py (added OTA socket/topic constants)
    - src/ota_manager/pyproject.toml (added cryptography, httpx, pyyaml, jsonschema, pyzmq)
    - uv.lock (resolved new deps)

key-decisions:
  - "Ed25519 public key stored as hex string in ota_config.yaml security.public_key_hex (no file on disk — avoids cert path checks like cloud_manager)"
  - "httpx.MockTransport injected via HttpDownloader(transport=...) constructor param — zero new test deps, no real HTTP server needed"
  - "Streaming SHA-256 pre-hashes existing .partial bytes before opening Range request — maintains rolling digest across resume"
  - "extractall uses filter='data' to suppress Python 3.14 deprecation warning and prevent setuid/device node extraction"
  - "PartitionBackend._boot_flag_path stored as str (not Path) to match JSON config dict pattern"

patterns-established:
  - "OTA components are dependency-injection friendly: PackageVerifier(key_hex), HttpDownloader(staging_dir, transport=), PartitionBackend(config_dict)"
  - "All OTA errors are typed exceptions: InvalidSignatureError for signature failures, ValueError for integrity/version/schema failures"
  - "Atomic writes: write to .tmp sibling then os.rename — used for boot_flag.json"

requirements-completed: [OTA-01, OTA-02, OTA-03]

# Metrics
duration: 7min
completed: 2026-03-15
---

# Phase 24 Plan 01: OTA Manager Foundation Summary

**Ed25519-verified OTA package pipeline with streaming HTTP resume, SHA-256 integrity, A/B partition atomic boot flags, and JSON Schema config — 21 unit tests, zero new test dependencies**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-03-15T14:41:02Z
- **Completed:** 2026-03-15T14:48:22Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 10

## Accomplishments

- PackageVerifier: real Ed25519 key-pair sign/verify, streaming SHA-256, tar extraction with path-traversal guard, semver downgrade blocking
- HttpDownloader: async httpx streaming with Range-header resume, SHA-256 rolling hash across resumed bytes, Content-Length size cap, on_progress callback, atomic .partial rename
- PartitionBackend: atomic boot flag JSON via .tmp+rename, get_standby_partition, async dd and systemctl reboot wrappers
- IPC constants SOCK_OTA_PUB, SOCK_OTA_CMD, TOPIC_OTA added to ems_common.ipc without breaking existing modules
- ota_config.yaml + JSON Schema 2020-12 covering all 7 sections

## Task Commits

Each task was committed atomically:

1. **TDD RED - Failing tests** - `e414ecc` (test: add failing tests for OTA config, verifier, partition, downloader)
2. **Task 1: IPC constants, config, verifier, partition** - `197bbba` (feat: IPC constants, OTA config schema, verifier, and partition backend)
3. **Task 2: HTTP downloader** - `d2b12e5` (feat: HTTP downloader with streaming, resume, SHA-256, and size limit)

## Files Created/Modified

- `src/ota_manager/src/ems_ota_manager/config.py` - load_ota_config with YAML + JSON Schema validation
- `src/ota_manager/src/ems_ota_manager/verifier.py` - PackageVerifier + InvalidSignatureError
- `src/ota_manager/src/ems_ota_manager/partition.py` - BootFlag dataclass + PartitionBackend
- `src/ota_manager/src/ems_ota_manager/downloader.py` - HttpDownloader with streaming and resume
- `config/ota_config.yaml` - OTA configuration template with all sections
- `config/schemas/ota_config.schema.json` - JSON Schema 2020-12 for ota_config
- `tests/test_ota_manager.py` - 21 unit tests (284+ lines)
- `src/common/python/src/ems_common/ipc.py` - Added SOCK_OTA_PUB, SOCK_OTA_CMD, TOPIC_OTA
- `src/ota_manager/pyproject.toml` - Added cryptography, httpx, pyyaml, jsonschema, pyzmq deps
- `uv.lock` - Resolved cryptography==46.0.5 and httpx transitive deps

## Decisions Made

- Ed25519 public key stored as hex string (64 chars) in YAML rather than a file path — keeps config self-contained without cert infrastructure; `PackageVerifier.__init__` converts hex to `Ed25519PublicKey` directly
- `httpx.MockTransport` injected via `HttpDownloader(transport=...)` — zero extra test dependencies, no asyncio HTTP server spawn needed; handler function supports both full and Range (206) responses
- Streaming SHA-256 pre-hashes the existing `.partial` bytes on resume so the rolling hash covers the full file; only the server's continuation bytes are streamed via Range header
- Used `filter="data"` in `tf.extractall()` to suppress Python 3.14 deprecation warning and prevent extraction of setuid bits or device nodes

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed missing import in test_extract_rejects_path_traversal**
- **Found during:** Task 1 (GREEN phase test run)
- **Issue:** `PackageVerifier` used as type annotation before the local import statement existed in that method, causing NameError
- **Fix:** Added `from ems_ota_manager.verifier import PackageVerifier` at top of method
- **Files modified:** tests/test_ota_manager.py
- **Verification:** Test passes with no error after fix
- **Committed in:** 197bbba (Task 1 commit)

**2. [Rule 1 - Bug] Fixed deprecation warning in PackageVerifier.extract_package**
- **Found during:** Task 1 test run (extractall deprecation warning)
- **Issue:** `tf.extractall()` without filter= generates DeprecationWarning for Python 3.14 behavior change
- **Fix:** Added `filter="data"` argument to suppress warning and prevent unsafe extraction of setuid/device-node members
- **Files modified:** src/ota_manager/src/ems_ota_manager/verifier.py
- **Verification:** Test suite passes with no warnings
- **Committed in:** 197bbba (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 bugs)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered

- Cryptography package was not in the workspace uv.lock — added via `uv add cryptography --package ems-ota-manager`. Pre-existing test failures in test_config_hot_reload.py and test_scaffold.py are unrelated to this plan.

## User Setup Required

None - no external service configuration required. The `public_key_hex` placeholder in `ota_config.yaml` must be replaced with a real Ed25519 public key during commissioning.

## Next Phase Readiness

- All OTA foundation components are independently testable and importable
- Plan 24-02 state machine can import and inject: `PackageVerifier`, `HttpDownloader`, `PartitionBackend`, `load_ota_config`
- IPC sockets SOCK_OTA_PUB and SOCK_OTA_CMD are defined and ready for ZMQ binding in the state machine

## Self-Check: PASSED

All created files verified present on disk. All task commits verified in git log.

---
*Phase: 24-ota-manager*
*Completed: 2026-03-15*
