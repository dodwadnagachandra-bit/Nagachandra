# Phase 24: OTA Manager - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Firmware download (MQTT/HTTP), Ed25519 signature verification, A/B partition management with boot flag swap, automatic rollback on health check failure, status reporting. Covers OTA-01 through OTA-06. Pure Python.

</domain>

<decisions>
## Implementation Decisions

### Update Package Format

What format should OTA update packages use?

**Decision:** Tar archive containing manifest.json + firmware image + signature file. Not a custom binary format.

| Component | Filename | Content |
|-----------|----------|---------|
| Manifest | `manifest.json` | `{version, target, sha256, size_bytes, created_at, min_version}` |
| Firmware | `firmware.img` | Raw filesystem image for target partition |
| Signature | `manifest.sig` | Ed25519 signature of manifest.json |

Key rules:
- Tar (not zip) because tar preserves unix permissions and is streamable.
- Manifest signed (not firmware directly) — manifest contains the SHA-256 hash of the firmware, so verifying manifest signature + firmware hash = full chain of trust.
- `min_version` field in manifest prevents downgrade below a safety-critical version.
- `target` field identifies what the package updates (e.g., "ems-rootfs", "ems-application") — future-proofs for multi-component updates.
- Package downloaded to `/tmp/ems-ota/` staging directory, verified, then applied.

**Rationale:** Tar + manifest + signature is the standard Linux OTA pattern (used by swupdate, RAUC, Mender). Signing the manifest rather than the raw image allows metadata inspection before downloading the full firmware (manifest is ~200 bytes, firmware could be 100MB+). SHA-256 integrity check catches download corruption. Ed25519 signatures are small (64 bytes) and fast to verify.

### A/B Partition Management

How does the OTA manager handle dual partitions for safe updates?

**Decision:** Two root filesystem partitions (A and B). Boot flag in a small config partition determines which is active. OTA writes to standby, swaps flag, reboots.

| Aspect | Decision |
|--------|----------|
| Partition layout | rootfs_a (active or standby), rootfs_b (the other), boot_config (flag partition) |
| Boot flag | JSON file on boot_config partition: `{active: "a"|"b", previous: "a"|"b", boot_count: int}` |
| Update target | Always the NON-active partition (standby) |
| Apply sequence | Download → verify sig → verify SHA-256 → write to standby → update boot flag → reboot |
| Rollback trigger | Health check fails within timeout (300s default) → revert boot flag → reboot |
| Health check | All systemd EMS services active + RTDB valid + ZMQ telemetry flowing |

Key rules:
- OTA manager NEVER writes to the active partition — update always goes to standby.
- Boot flag swap is the commit point — once flag is swapped and reboot initiated, the new version runs.
- `boot_count` increments on each boot — if > 1 without health confirmation, assume boot loop → rollback.
- Health check reuses Phase 13/17 patterns: check systemd service status + RTDB magic/version + ZMQ telemetry.
- Rollback is automatic — no operator intervention needed. Manual rollback also available via ZMQ command.

**Rationale:** A/B partitioning is the industry standard for embedded OTA (Android, ChromeOS, Yocto swupdate). Writing to standby ensures the active system is never corrupted during update. The boot flag pattern is simpler than bootloader modification (U-Boot env). boot_count as a loop detector catches cases where the new firmware boots but crashes immediately.

### Update Delivery Channel

How do firmware packages arrive — MQTT, HTTP, or both?

**Decision:** HTTP download with URL received via MQTT. Not direct MQTT binary transfer.

| Aspect | Decision |
|--------|----------|
| Notification | MQTT topic `{prefix}/ota/notify` with `{version, url, sha256, size_bytes}` |
| Download | HTTP GET from URL (supports both cloud CDN and local LAN server) |
| Progress | Download progress published on ZMQ telemetry (topic: "ota") every 5 seconds |
| Resume | HTTP Range header for interrupted downloads (resume from last byte) |
| Size limit | Reject packages > 500MB (sanity check) |
| Auth | HTTP download URL may include time-limited token (signed URL pattern) |

Key rules:
- MQTT carries the notification (small JSON), HTTP carries the payload (large binary). MQTT is not designed for large file transfer.
- HTTP download supports resume via Range headers — essential for unreliable cellular connections.
- URL can point to cloud CDN (internet) or local server (LAN for fleet deployments) — cloud_config doesn't constrain the URL source.
- Download to staging directory (`/tmp/ems-ota/`) with `.partial` suffix during download, renamed on completion.
- MQTT notification includes SHA-256 for pre-download integrity planning (reject if staging doesn't have enough space).

**Rationale:** MQTT's QoS and message size limits make it unsuitable for 100MB+ binary transfers. HTTP with Range resume is the proven pattern for firmware downloads (Tesla, Mender, swupdate all use it). MQTT notification + HTTP download decouples the control plane (MQTT) from the data plane (HTTP). Signed URLs provide temporary access without storing credentials on the device.

### Claude's Discretion

- Ed25519 library choice (PyNaCl vs cryptography vs ed25519)
- Partition detection implementation (lsblk parsing vs /proc/cmdline vs config file)
- Staging directory management (cleanup on failure, disk space check)
- Boot flag file format and location
- Health check implementation (reuse ModuleProcess from Phase 13 or simpler systemctl checks)
- ZMQ REQ/REP for version query and manual rollback command
- Test strategy (mock partitions, mock HTTP server, mock MQTT)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/ota_manager/` — Stub package (v0.1.0, depends on ems-common)
- `deploy/systemd/ota_manager.service` — After=cloud_manager
- Phase 13/17 health check patterns (ModuleProcess, check_rtdb_exists, check_zmq_receiving)
- Phase 22 cloud_manager: MQTT subscription for OTA notifications

### Established Patterns
- Async Python modules with signal handlers
- ZMQ PUB for status telemetry (topic: "ota")
- ZMQ REQ/REP for version queries
- Atomic file operations (.tmp → rename) for crash safety
- Config loading via yaml.safe_load + JSON Schema validation

### Integration Points
- MQTT subscription on `{prefix}/ota/notify` via cloud_manager (or direct MQTT client)
- HTTP download (requests or httpx library)
- ZMQ PUB on SOCK_TELEMETRY for update status (topic: "ota")
- ZMQ REQ/REP for version query and rollback commands
- systemd for health check (systemctl is-active)
- Reboot via `os.system("reboot")` or systemd `systemctl reboot`

</code_context>

<specifics>
## Specific Ideas

- OTA is the most hardware-dependent M4 module — partition layout won't exist on dev machines
- Testing requires extensive mocking (partitions, reboot, boot flags)
- Ed25519 key pair: public key embedded in firmware, private key held by build system (never on device)
- For M4, OTA targets application updates only (Python + C binaries) — full rootfs update waits for Yocto (M5)
- boot_config partition could be simulated as a directory on dev machines

</specifics>

<deferred>
## Deferred Ideas

- **OTA-07**: Delta updates — future optimization
- **OTA-08**: BMS/PCS firmware forwarding — pending vendor protocols
- **OTA-09**: Scheduled update windows — future requirement
- Full rootfs A/B partition — deferred to M5 Yocto migration
- U-Boot bootloader integration — deferred to M5
- Encrypted firmware packages — future security requirement
- Multi-component updates (application + kernel separately) — M5

</deferred>

---

*Phase: 24-ota-manager*
*Context gathered: 2026-03-15*
