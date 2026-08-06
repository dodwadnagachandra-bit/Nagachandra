# Phase 24: OTA Manager - Research

**Researched:** 2026-03-15
**Domain:** OTA firmware update pipeline — HTTP download, Ed25519 signature verification, A/B partition swap, health-check rollback, ZMQ status reporting
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Update Package Format:** Tar archive containing `manifest.json` + `firmware.img` + `manifest.sig`.
- Manifest signed (not firmware directly) — manifest contains SHA-256 hash of firmware
- `min_version` field prevents downgrade below safety-critical version
- `target` field identifies package target (e.g., "ems-rootfs", "ems-application")
- Package downloaded to `/tmp/ems-ota/` staging directory

**A/B Partition Management:**
- Two root filesystem partitions: rootfs_a, rootfs_b, boot_config (flag partition)
- Boot flag: JSON file on boot_config partition `{active: "a"|"b", previous: "a"|"b", boot_count: int}`
- OTA always writes to NON-active (standby) partition
- Apply sequence: download → verify sig → verify SHA-256 → write to standby → update boot flag → reboot
- Rollback trigger: health check fails within timeout (300s default) → revert boot flag → reboot
- `boot_count` > 1 without health confirmation → assume boot loop → rollback
- Health check: all EMS systemd services active + RTDB valid + ZMQ telemetry flowing

**Update Delivery Channel:** HTTP download with URL received via MQTT
- Notification: MQTT topic `{prefix}/ota/notify` with `{version, url, sha256, size_bytes}`
- Download: HTTP GET from URL (supports cloud CDN and local LAN server)
- Progress: published on ZMQ telemetry (topic: "ota") every 5 seconds
- Resume: HTTP Range header for interrupted downloads
- Size limit: reject packages > 500MB
- Download to staging with `.partial` suffix, renamed on completion

**For M4, OTA targets application updates only (Python + C binaries) — full rootfs deferred to Yocto (M5)**

### Claude's Discretion
- Ed25519 library choice (PyNaCl vs cryptography vs ed25519)
- Partition detection implementation (lsblk parsing vs /proc/cmdline vs config file)
- Staging directory management (cleanup on failure, disk space check)
- Boot flag file format and location
- Health check implementation (reuse ModuleProcess from Phase 13 or simpler systemctl checks)
- ZMQ REQ/REP for version query and manual rollback command
- Test strategy (mock partitions, mock HTTP server, mock MQTT)

### Deferred Ideas (OUT OF SCOPE)
- OTA-07: Delta updates — future optimization
- OTA-08: BMS/PCS firmware forwarding — pending vendor protocols
- OTA-09: Scheduled update windows — future requirement
- Full rootfs A/B partition — deferred to M5 Yocto migration
- U-Boot bootloader integration — deferred to M5
- Encrypted firmware packages — future security requirement
- Multi-component updates (application + kernel separately) — M5
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| OTA-01 | Firmware download receives update packages via MQTT topic `{prefix}/ota/package` or HTTP URL, stores to staging partition with integrity check (SHA-256) | httpx AsyncClient streaming + stdlib hashlib SHA-256; staging dir `/tmp/ems-ota/`; `.partial` rename pattern |
| OTA-02 | Ed25519 signature verification validates package signature against embedded public key before applying any update | `cryptography` library already installed; `Ed25519PublicKey.from_public_bytes()` + `.verify()` raises `InvalidSignature` |
| OTA-03 | A/B partition management maintains two system partitions — active and standby. Updates applied to standby, boot flag swapped on success | config-file-based partition detection for M4; boot flag JSON at configurable path; atomic write (.tmp → rename) |
| OTA-04 | Automatic rollback monitors health after boot swap — if new version fails health check within 300s, reverts to previous partition | `systemctl is-active` subprocess check + RTDB magic check + ZMQ telemetry poll; boot_count loop detector |
| OTA-05 | Update status published on ZMQ telemetry (topic: ota) for HMI display — idle/downloading/verifying/applying/rebooting/rolled_back | ZMQ PUB on SOCK_TELEMETRY using existing `encode_telemetry()` + TOPIC_OTA constant; 5s publish cadence during active update |
| OTA-06 | Version tracking maintains current and previous firmware versions in RTDB, queryable via ZMQ REQ/REP | ZMQ REP socket (SOCK_OTA_CMD new constant); JSON file version store in `/var/lib/ems/ota_state.json` as RTDB proxy for M4 |
</phase_requirements>

---

## Summary

The OTA Manager is the most hardware-abstraction-intensive module in M4. On production hardware it writes firmware images to a standby block device partition and manipulates a boot flag file on a dedicated config partition. On developer machines, neither partition exists — both must be simulated via directories and mock files. This simulation gap must be the primary design driver: every partition operation (detect active partition, find standby device, write image, read/write boot flag) must be injectable or configurable so tests run without root or real block devices.

The update pipeline is a linear state machine: `idle → downloading → verifying → applying → rebooting` with `rolled_back` as an exit state from the health-check phase. Each state transition publishes a ZMQ telemetry message on topic "ota". The state machine runs as async Python, consistent with all other L4/L5 modules. Signal handling, config loading, and ZMQ socket lifecycle all follow the `cloud_manager` pattern verbatim.

The two main discretion decisions are already resolved by what is available in the venv: `cryptography` is already installed (confirmed in `.venv`), making it the correct choice over PyNaCl — no new dependency required. For HTTP download, `httpx` is already in `uv.lock` (pulled in by `hmi_server`), so using `httpx.AsyncClient` for async streaming is the correct choice. Both are HIGH confidence findings.

**Primary recommendation:** Build the OTA manager as a single async class `OtaManager` with a state machine, following the `CloudLoop` pattern. Inject all filesystem and partition operations as callable parameters or a `PartitionBackend` abstraction to enable full unit test coverage without hardware.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `cryptography` | >=43 (already in venv) | Ed25519 signature verification | PyCA standard; already installed; OpenSSL-backed; `from_public_bytes()` + `verify()` API |
| `httpx` | >=0.28 (already in uv.lock) | Async HTTP download with streaming | Already in workspace via hmi_server; supports `AsyncClient.stream()` + `aiter_bytes()` + Range headers |
| `tarfile` | stdlib | Extract OTA package tar archive | No dependency; handles `.tar`, `.tar.gz`, `.tar.xz` |
| `hashlib` | stdlib | SHA-256 integrity check | No dependency; streaming-friendly |
| `pyzmq` | >=27.1 (already in dev deps) | ZMQ PUB for status, REP for version query | Established IPC pattern across all modules |
| `pyyaml` | >=6.0 (already in dev deps) | OTA config loading | Standard config pattern across all modules |
| `jsonschema` | >=4.23 (already in dev deps) | OTA config schema validation | Standard config pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `asyncio.subprocess` | stdlib | `systemctl is-active` health check | Avoids blocking event loop during subprocess calls |
| `json` | stdlib | Boot flag file read/write, version state | Boot flag format is JSON per locked decision |
| `pathlib` | stdlib | All file path operations | Project standard; never use `os.path` |
| `shutil` | stdlib | Disk space check (`shutil.disk_usage()`) | Pre-download space validation |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `cryptography` | PyNaCl | PyNaCl bundles libsodium (larger); cryptography is already installed — use what's there |
| `httpx` | `aiohttp` or `requests` | httpx is already in uv.lock; aiohttp adds dependency; requests is sync-only |
| Config-file partition detection | `/proc/cmdline` parsing | /proc/cmdline not available in dev; config file is testable and sufficient for M4 |

**Installation:** No new packages required. All dependencies are already present in the workspace.

```bash
# Add to src/ota_manager/pyproject.toml only:
uv add --project src/ota_manager cryptography httpx pyyaml jsonschema pyzmq
```

---

## Architecture Patterns

### Recommended Project Structure

```
src/ota_manager/
├── pyproject.toml
└── src/ems_ota_manager/
    ├── __init__.py          # version = "0.1.0" (stub exists)
    ├── __main__.py          # entry point; arg parsing; asyncio.run(run())
    ├── config.py            # load_ota_config(); YAML + JSON Schema validation
    ├── downloader.py        # HttpDownloader: async streaming + resume + SHA-256
    ├── verifier.py          # PackageVerifier: tar extract + Ed25519 + hash check
    ├── partition.py         # PartitionBackend: boot flag + write image (injectable)
    ├── health.py            # HealthChecker: systemctl + ZMQ telemetry poll
    ├── state_machine.py     # OtaStateMachine: idle/downloading/verifying/applying/...
    └── loop.py              # OtaManager: async tasks; ZMQ PUB + REP; MQTT sub
tests/
└── test_ota_manager.py      # unit tests for all above (mock partitions, mock HTTP)
```

### Pattern 1: State Machine with ZMQ Status Publish

**What:** OTA pipeline modelled as explicit state enum. Each state transition calls `_publish_status()` on SOCK_TELEMETRY (topic "ota").
**When to use:** Any multi-step async process where HMI needs live progress visibility.

```python
# Source: established project pattern (cloud_manager/loop.py)
from enum import Enum

class OtaState(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    APPLYING = "applying"
    REBOOTING = "rebooting"
    ROLLED_BACK = "rolled_back"

class OtaManager:
    def __init__(self, ...) -> None:
        self._state: OtaState = OtaState.IDLE
        self._zmq_ctx: zmq.Context = zmq.Context()
        self._telemetry_pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._telemetry_pub.connect(telemetry_pub_endpoint or SOCK_TELEMETRY)

    def _set_state(self, new_state: OtaState) -> None:
        self._state = new_state
        self._publish_status()

    def _publish_status(self) -> None:
        payload: dict = {
            "state": self._state.value,
            "version_current": self._version_current,
            "ts": int(time.time() * 1000),
        }
        body: bytes = encode_telemetry(
            timestamp_ms=payload["ts"],
            seq=self._seq,
            source="ota_manager",
            topic=TOPIC_OTA,
            payload=payload,
        )
        try:
            self._telemetry_pub.send_multipart(
                [TOPIC_OTA.encode(), body], flags=zmq.NOBLOCK
            )
        except zmq.ZMQError:
            pass
        self._seq += 1
```

### Pattern 2: Ed25519 Verification

**What:** Load embedded public key bytes at startup; verify manifest signature before any partition write.
**When to use:** OTA-02 — must execute before `_applying` state.

```python
# Source: cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

# Public key embedded in module (32 bytes, base64-encoded in config or hardcoded)
_EMBEDDED_PUBLIC_KEY_BYTES: bytes = bytes.fromhex(
    "..."  # 64 hex chars = 32 bytes
)

class PackageVerifier:
    def __init__(self, public_key_bytes: bytes) -> None:
        self._public_key: Ed25519PublicKey = (
            Ed25519PublicKey.from_public_bytes(public_key_bytes)
        )

    def verify_manifest_signature(
        self, manifest_bytes: bytes, signature_bytes: bytes
    ) -> None:
        """Raises InvalidSignature if signature does not match."""
        self._public_key.verify(signature_bytes, manifest_bytes)
        # Returns None on success; raises InvalidSignature on failure
```

### Pattern 3: Async HTTP Download with Resume

**What:** Streaming download with `httpx.AsyncClient`, SHA-256 computed on the fly, Range header for resume.
**When to use:** OTA-01 — large firmware files (potentially hundreds of MB).

```python
# Source: httpx.org/async — aiter_bytes() streaming pattern
import hashlib
import httpx
from pathlib import Path

class HttpDownloader:
    async def download(
        self,
        url: str,
        dest: Path,
        expected_sha256: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        partial: Path = dest.with_suffix(".partial")
        resume_from: int = partial.stat().st_size if partial.exists() else 0
        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        sha256: hashlib._Hash = hashlib.sha256()
        if resume_from > 0:
            # Pre-hash existing partial content for resume integrity
            sha256.update(partial.read_bytes())

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                total: int = int(response.headers.get("content-length", 0)) + resume_from
                downloaded: int = resume_from
                with partial.open("ab") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        sha256.update(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)

        actual: str = sha256.hexdigest()
        if actual != expected_sha256:
            partial.unlink(missing_ok=True)
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")

        partial.rename(dest)
```

### Pattern 4: Injected PartitionBackend for Testability

**What:** All partition I/O behind a `PartitionBackend` class. Tests inject a `MockPartitionBackend` that uses temp directories.
**When to use:** OTA is the most hardware-dependent module. Without this, tests need root and real block devices.

```python
# Source: project requirement (CONTEXT.md specifics)
from dataclasses import dataclass
from pathlib import Path
import json, time

@dataclass
class BootFlag:
    active: str        # "a" or "b"
    previous: str      # "a" or "b"
    boot_count: int

class PartitionBackend:
    """Production implementation — uses real paths from ota_config."""

    def __init__(self, config: dict) -> None:
        self._boot_flag_path: Path = Path(config["partition"]["boot_flag_path"])
        self._standby_device: str = config["partition"]["standby_device"]
        self._active_device: str = config["partition"]["active_device"]

    def read_boot_flag(self) -> BootFlag:
        data: dict = json.loads(self._boot_flag_path.read_text())
        return BootFlag(**data)

    def write_boot_flag(self, flag: BootFlag) -> None:
        """Atomic write: .tmp → rename (crash-safe)."""
        tmp: Path = self._boot_flag_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(flag.__dict__))
        tmp.rename(self._boot_flag_path)

    def write_image_to_standby(self, image_path: Path) -> None:
        """Write firmware image to standby block device (requires root)."""
        import subprocess
        subprocess.run(
            ["dd", f"if={image_path}", f"of={self._standby_device}", "bs=4M"],
            check=True,
        )

    def get_active_partition(self) -> str:
        return self.read_boot_flag().active

    def reboot(self) -> None:
        import subprocess
        subprocess.run(["systemctl", "reboot"], check=True)
```

### Pattern 5: Health Check After Boot Swap

**What:** After reboot to new partition, a health monitor task checks all EMS services and ZMQ telemetry within a timeout window.
**When to use:** OTA-04 — runs on first boot after partition swap.

```python
# Source: established project pattern (Phase 13/17 diagnostics)
import asyncio
import subprocess

class HealthChecker:
    _EMS_SERVICES: list[str] = [
        "safety_manager", "comm_manager", "data_manager",
        "control_manager", "alarm_manager", "cloud_manager",
    ]

    async def check_services_active(self) -> bool:
        for svc in self._EMS_SERVICES:
            result = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", f"{svc}.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await result.communicate()
            if stdout.strip() != b"active":
                return False
        return True

    async def run_health_check(self, timeout_s: float = 300.0) -> bool:
        """Poll health until OK or timeout. Returns True if healthy."""
        deadline: float = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if await self.check_services_active():
                return True
            await asyncio.sleep(10.0)
        return False
```

### Pattern 6: ZMQ REP for Version Query

**What:** REP socket (SOCK_OTA_CMD) responds to version query and manual rollback commands.
**When to use:** OTA-06 — HMI and cloud can query current/previous version.

```python
# Source: established project pattern (ipc.py encode_command_request/response)
async def _version_query_loop(self) -> None:
    while not self._stop_event.is_set():
        try:
            req_bytes: bytes = self._ota_rep.recv(flags=zmq.NOBLOCK)
            action, params = decode_command_request(req_bytes)
            if action == "get_version":
                result: dict = {
                    "current": self._version_current,
                    "previous": self._version_previous,
                }
                self._ota_rep.send(encode_command_response("ok", result))
            elif action == "rollback":
                # Trigger manual rollback
                asyncio.create_task(self._do_rollback())
                self._ota_rep.send(encode_command_response("ok", {}))
            else:
                self._ota_rep.send(
                    encode_command_response("error", error_msg=f"Unknown action: {action}")
                )
        except zmq.Again:
            pass
        await asyncio.sleep(0.05)
```

### Anti-Patterns to Avoid

- **Writing to active partition:** OTA must NEVER write to the currently running partition. Always derive standby from `active` flag, never hard-code.
- **Blocking the event loop during image write:** `dd` / block device write is slow. Use `asyncio.create_subprocess_exec` or run in a thread executor.
- **Swapping boot flag before verifying the image:** Boot flag swap is the commit point — only swap AFTER SHA-256 passes.
- **Hardcoding `/tmp/ems-ota/`:** Use configurable staging directory. Default to `/tmp/ems-ota/` but allow override for tests.
- **Not cleaning staging on failure:** Failed downloads must delete `.partial` files or they consume disk across reboots.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Ed25519 verification | Custom signature code | `cryptography.hazmat.primitives.asymmetric.ed25519` | Side-channel attacks, timing vulnerabilities; already installed |
| SHA-256 streaming | Manual digest loops | `hashlib.sha256()` + `update()` per chunk | stdlib; streaming-friendly; correct |
| Tar extraction | Manual archive parsing | `tarfile.open()` + `extractall(members=...)` | stdlib; handles compression variants |
| HTTP range resume | Manual byte tracking | `httpx` Range header + `ab` file mode | Correct edge-case handling; already in uv.lock |
| Disk space check | Manual df parsing | `shutil.disk_usage(path).free` | stdlib; cross-platform; no subprocess |
| Atomic file writes | Direct writes | `.tmp` write + `Path.rename()` | Kernel guarantees atomic rename on same filesystem |

**Key insight:** The hard parts of OTA (signature schemes, partial-content HTTP, tar edge cases) are solved by well-audited libraries. The value is in the state machine logic and the testability layer around partition I/O.

---

## Common Pitfalls

### Pitfall 1: Health Check on Wrong Boot
**What goes wrong:** The health check runs every boot, not just after an OTA swap. Checking `boot_count > 1` without first verifying that a swap actually occurred triggers spurious rollbacks on normal restarts.
**Why it happens:** boot_count is incremented by bootloader / init script on every boot.
**How to avoid:** Only enter `HEALTH_CHECK` mode if boot flag shows `pending_health_check: true` (set by OTA manager at swap time). Clear flag after health confirms OK.
**Warning signs:** Unexpected rollbacks in logs after normal service restarts.

### Pitfall 2: MQTT Notify vs. RTDB Write for Version State
**What goes wrong:** Storing `version_current` only in memory — lost after rollback reboot.
**Why it happens:** Version state must survive across reboots; RTDB shared memory does not.
**How to avoid:** Persist version state to a JSON file (`/var/lib/ems/ota_state.json`). Read on startup. RTDB query (OTA-06) reads from this file.
**Warning signs:** Version query returns empty string after reboot.

### Pitfall 3: Blocking asyncio During Image Write
**What goes wrong:** Writing a 100MB firmware image with synchronous `dd` blocks the asyncio event loop, preventing ZMQ status updates and health check polling.
**Why it happens:** `subprocess.run()` is synchronous.
**How to avoid:** Use `asyncio.create_subprocess_exec()` for all subprocess calls (dd, systemctl). Or run in `loop.run_in_executor(None, ...)` for synchronous file I/O.
**Warning signs:** ZMQ telemetry stops updating during the `applying` phase.

### Pitfall 4: SHA-256 Mismatch Source Confusion
**What goes wrong:** Developer assumes `sha256` in MQTT notification matches the `.tar` package. But per locked decision, `sha256` in `manifest.json` is the SHA-256 of `firmware.img`, not the outer tar.
**Why it happens:** The MQTT notification `sha256` is for the overall package (tar), while `manifest.json.sha256` is for `firmware.img`. Two different hash checks.
**How to avoid:** Document and implement as two separate checks: (1) verify downloaded tar SHA-256 matches MQTT notification sha256, (2) after extraction, verify firmware.img SHA-256 matches manifest.json sha256.
**Warning signs:** "SHA-256 mismatch" errors when the download is actually valid.

### Pitfall 5: Test Coverage Without Hardware
**What goes wrong:** Tests that attempt to open `/dev/mmcblk*` or read `/boot/config` fail on dev machines. The whole test suite is then marked `skip` or relies on mocks so shallow they don't catch real bugs.
**Why it happens:** Partition backend mixed with business logic.
**How to avoid:** `PartitionBackend` is the ONLY class that touches real block devices. Everything else receives a `PartitionBackend`-compatible object. Tests inject `FakePartitionBackend(tmp_path)`.
**Warning signs:** Tests only cover config loading; zero tests for the update state machine.

### Pitfall 6: ZMQ PUB Socket Direction
**What goes wrong:** OTA manager tries to `bind()` to SOCK_TELEMETRY (already bound by data_manager). ZMQ error or silent failure.
**Why it happens:** SOCK_TELEMETRY is a PUB socket bound by `data_manager`. OTA should `connect()` (as a secondary publisher to the same endpoint is invalid in standard PUB/SUB).
**How to avoid:** OTA publishes status on its own dedicated PUB socket (`SOCK_OTA_PUB`), similar to how cloud_manager uses `SOCK_CLOUD_PUB`. OR: OTA connects to SOCK_TELEMETRY as a SUB to read data and only publishes via PUSH to logger. Verify against existing IPC contract.
**Warning signs:** `zmq.error.ZMQError: Address already in use`.

> **Research note:** This is a critical design question. The ZMQ IPC contract shows `SOCK_TELEMETRY` bound by `data_manager` (PUB). Multiple processes cannot `bind()` the same socket. The CONTEXT.md says "ZMQ PUB on SOCK_TELEMETRY for update status (topic: ota)" — this likely means OTA needs its own dedicated PUB socket (`SOCK_OTA_PUB`) that HMI subscribers connect to, OR OTA sends telemetry to logger PUSH and data_manager PUBs it. The safest and most consistent approach with `cloud_manager` pattern is a dedicated `SOCK_OTA_PUB` socket. **Planner must resolve: add `SOCK_OTA_PUB` to `ipc.py` or clarify SOCK_TELEMETRY multi-publisher strategy.**

---

## Code Examples

### Ed25519 Verification (verified against official docs)
```python
# Source: https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

public_key: Ed25519PublicKey = Ed25519PublicKey.from_public_bytes(
    bytes.fromhex("abcd...64hexchars")  # 32 bytes
)
try:
    public_key.verify(signature_bytes, manifest_bytes)  # raises on failure
except InvalidSignature:
    raise ValueError("Package signature invalid — rejecting update")
```

### Async Streaming Download with Progress
```python
# Source: https://www.python-httpx.org/async/
import httpx, hashlib
from pathlib import Path

async def download_file(url: str, dest: Path, expected_sha256: str) -> None:
    partial: Path = dest.with_suffix(".partial")
    resume_from: int = partial.stat().st_size if partial.exists() else 0
    headers: dict[str, str] = {}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"

    digest: hashlib._Hash = hashlib.sha256()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            with partial.open("ab") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    digest.update(chunk)

    if digest.hexdigest() != expected_sha256:
        partial.unlink(missing_ok=True)
        raise ValueError("SHA-256 mismatch")
    partial.rename(dest)
```

### Tar Package Extraction (stdlib)
```python
# Source: Python stdlib tarfile documentation
import tarfile
from pathlib import Path

def extract_ota_package(tar_path: Path, extract_dir: Path) -> dict:
    """Extract OTA tar and return parsed manifest."""
    import json
    with tarfile.open(tar_path, "r:*") as tf:
        # Validate members before extraction (security: no path traversal)
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                raise ValueError(f"Dangerous tar member: {member.name}")
        tf.extractall(extract_dir)

    manifest: dict = json.loads((extract_dir / "manifest.json").read_text())
    return manifest
```

### Boot Flag Atomic Write
```python
# Source: established project pattern (atomic .tmp → rename)
import json
from pathlib import Path
from dataclasses import asdict

def write_boot_flag(flag_path: Path, flag: BootFlag) -> None:
    tmp: Path = flag_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(flag)))
    tmp.rename(flag_path)  # atomic on POSIX same-filesystem
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Mender/swupdate (separate daemon) | Pure Python state machine | M4 decision | No C daemon; testable; same async pattern as other modules |
| U-Boot env for boot flag | JSON file on boot_config partition | M4 decision | No U-Boot dependency; bootloader-agnostic; works on dev machines |
| Full rootfs A/B | Application-layer A/B (Python + binaries only) | M4 decision, M5 for full rootfs | Simpler for now; sufficient for application updates |
| Direct MQTT binary transfer | MQTT notify + HTTP download | M4 decision | MQTT unsuitable for 100MB+; HTTP resume is robust |

**Deprecated/outdated for this project:**
- `requests` library (sync-only): use `httpx.AsyncClient` instead — already in uv.lock
- PyNaCl for Ed25519: `cryptography` is already installed and provides the same API

---

## Open Questions

1. **ZMQ telemetry publish mechanism for OTA status**
   - What we know: SOCK_TELEMETRY is `bind()`-ed by data_manager (PUB). `cloud_manager` uses its own `SOCK_CLOUD_PUB`. CONTEXT.md says OTA publishes on "SOCK_TELEMETRY (topic: ota)".
   - What's unclear: Can two processes `connect()` to the same ZMQ PUB socket as additional publishers? Answer: No — PUB/SUB in ZMQ allows multiple publishers to `bind()` different sockets and subscribers connect to each. Each publisher needs its own socket.
   - **Recommendation:** Add `SOCK_OTA_PUB: str = "ipc:///run/ems/ota_pub.sock"` to `ems_common/ipc.py` and `TOPIC_OTA: str = "ota"`. HMI/cloud subscribe to this socket for OTA status. This matches cloud_manager's pattern exactly.

2. **Active partition detection on dev machines**
   - What we know: `/proc/cmdline` won't have partition info on Ubuntu dev. Config file detection (from `ota_config.yaml`) is the safest approach.
   - What's unclear: Should `active` partition be auto-detected or always read from boot flag file?
   - **Recommendation:** Always read `active` from boot flag file. On dev, boot flag file lives at a configurable path (e.g., `/tmp/ems-ota/boot_flag.json`). Production path is on boot_config partition mount point.

3. **Public key embedding strategy**
   - What we know: Private key on build system, public key on device. CONTEXT.md says "embedded public key".
   - What's unclear: Hardcoded bytes in source? Config file? Schema-validated config field?
   - **Recommendation:** Public key as hex string in `ota_config.yaml` under `security.public_key_hex`. This makes it overridable for test keys without recompiling, but still validated by schema. The planner should include a test key pair in the test fixtures.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (exists) |
| Quick run command | `uv run pytest tests/test_ota_manager.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OTA-01 | Download firmware package via HTTP URL, verify SHA-256, write to staging | unit | `uv run pytest tests/test_ota_manager.py::test_http_download_sha256_ok -x` | ❌ Wave 0 |
| OTA-01 | Resume partial download via Range header | unit | `uv run pytest tests/test_ota_manager.py::test_download_resume_range_header -x` | ❌ Wave 0 |
| OTA-01 | Reject package > 500MB | unit | `uv run pytest tests/test_ota_manager.py::test_download_size_limit_rejected -x` | ❌ Wave 0 |
| OTA-02 | Ed25519 signature verification passes with valid key | unit | `uv run pytest tests/test_ota_manager.py::test_ed25519_verify_valid -x` | ❌ Wave 0 |
| OTA-02 | Ed25519 signature verification raises on tampered manifest | unit | `uv run pytest tests/test_ota_manager.py::test_ed25519_verify_invalid_raises -x` | ❌ Wave 0 |
| OTA-03 | Boot flag read/write round-trip | unit | `uv run pytest tests/test_ota_manager.py::test_boot_flag_rw -x` | ❌ Wave 0 |
| OTA-03 | State machine transitions idle → downloading → verifying → applying | unit | `uv run pytest tests/test_ota_manager.py::test_state_machine_happy_path -x` | ❌ Wave 0 |
| OTA-04 | Health check passes when all services active | unit | `uv run pytest tests/test_ota_manager.py::test_health_check_passes -x` | ❌ Wave 0 |
| OTA-04 | Health check timeout triggers rollback | unit | `uv run pytest tests/test_ota_manager.py::test_health_check_timeout_rollback -x` | ❌ Wave 0 |
| OTA-05 | Status published on ZMQ telemetry at each state transition | unit | `uv run pytest tests/test_ota_manager.py::test_status_published_on_state_change -x` | ❌ Wave 0 |
| OTA-06 | ZMQ REP returns current and previous version | unit | `uv run pytest tests/test_ota_manager.py::test_version_query_zmq_rep -x` | ❌ Wave 0 |
| OTA-06 | Version state persisted to JSON file, survives restart | unit | `uv run pytest tests/test_ota_manager.py::test_version_state_persistence -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_ota_manager.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_ota_manager.py` — all OTA-01 through OTA-06 test cases
- [ ] `config/ota_config.yaml` — OTA configuration file (does not exist)
- [ ] `config/schemas/ota_config.schema.json` — OTA config JSON Schema (does not exist)
- [ ] `SOCK_OTA_PUB` and `TOPIC_OTA` constants in `src/common/python/src/ems_common/ipc.py`
- [ ] `SOCK_OTA_CMD` constant in `src/common/python/src/ems_common/ipc.py` (for REP socket)
- [ ] Test key pair (Ed25519): generate in Wave 0 fixture and embed in test config
- [ ] `src/ota_manager/pyproject.toml` updated with actual dependencies (cryptography, httpx, pyyaml, jsonschema, pyzmq)

---

## Sources

### Primary (HIGH confidence)
- `cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/` — Ed25519PublicKey API, `from_public_bytes()`, `verify()`, `InvalidSignature` exception
- `python-httpx.org/async/` — `AsyncClient.stream()`, `aiter_bytes()`, Range header pattern
- `/home/overlord/EMS/src/common/python/src/ems_common/ipc.py` — ZMQ socket constants, encode/decode helpers
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/loop.py` — async task pattern, ZMQ NOBLOCK polling
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/config.py` — YAML + jsonschema config loader pattern
- `/home/overlord/EMS/uv.lock` — confirmed `httpx 0.28.1` present; `cryptography` confirmed installed in venv

### Secondary (MEDIUM confidence)
- WebSearch: `cryptography` vs `PyNaCl` — cross-verified with official pyca.io; both PyCA projects; cryptography preferred when already installed
- WebSearch: httpx streaming + Range headers — cross-verified with httpx.org docs

### Tertiary (LOW confidence)
- None — all critical claims verified against official sources or codebase inspection

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified as installed or in uv.lock
- Architecture: HIGH — patterns lifted directly from cloud_manager and ipc.py in this repo
- Pitfalls: HIGH for ZMQ PUB conflict (verified against ipc.py); MEDIUM for SHA-256 dual-check confusion (logical analysis)

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable libraries; OTA pattern is not fast-moving)
