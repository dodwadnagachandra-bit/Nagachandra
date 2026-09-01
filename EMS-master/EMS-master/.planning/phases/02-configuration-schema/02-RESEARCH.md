# Phase 2: Configuration Schema - Research

**Researched:** 2026-02-26
**Domain:** JSON Schema validation for YAML config files (Python tooling)
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Schema Strictness:**
- `additionalProperties: false` on all schemas — reject unknown fields, catch typos immediately
- Full range enforcement on topology fields: clusters 1–8, racks 1–16, modules 3–20, cells 16–108, temps_per_module 4–40
- JSON Schema conditionals (if/then/else or oneOf) for cross-field dependencies (e.g., protocol 'rtu' requires baud/parity, 'tcp' requires host/port)
- Strict enums on all fields with known valid values (protocols, severities, modes, states)

**Validation UX:**
- `make validate` as the primary invocation — consistent with Phase 1 Makefile convention
- Fail-fast per file — stop at first error in each config file, report, move to next file
- Friendly prose error messages: "ERROR: In system_config.yaml, the field 'racks_per_cluster' in cluster 0 is set to 25, but must be between 1 and 16."
- Added to pr-check.yml as a CI gate — invalid config changes block merge

**Config File Defaults:**
- Three profiles: config/profiles/residential/, config/profiles/commercial/, config/profiles/container/ matching the 3 scale tiers
- config/ is the active directory (modules consume from here), profiles are reference templates — developers copy a profile to config/ to activate
- config/ ships pre-populated with residential profile — make build and make test work out of the box with zero setup
- Heavily commented YAML — each field has inline comments explaining purpose, valid range, and unit

**Hot-Reload and Schema Metadata:**
- Schemas only in this phase — runtime file-watch + re-validate + notify logic deferred to config_manager (M1)
- `x-hot-reload: true` metadata on the 3 hot-reloadable schemas (control_config, alarms_config, schedule_config) — config_manager can discover reload capability from schema
- `x-mutable: true/false` on individual fields within hot-reloadable configs — marks which fields can change during runtime reload vs require restart
- `x-unit` metadata on all numeric fields (e.g., "V", "°C", "kW", "ms", "%") — self-documenting schemas, HMI can auto-generate labels from metadata

### Claude's Discretion

- JSON Schema draft version (2020-12 vs draft-07)
- Python validation library choice (jsonschema, pydantic, or custom)
- Schema file naming convention and directory layout
- Which fields in hot-reloadable configs get x-mutable: true vs false (use domain judgment — thresholds/setpoints are mutable, structural fields are not)

### Deferred Ideas (OUT OF SCOPE)

- Runtime hot-reload pipeline (inotify watch + ZMQ notify) — config_manager in M1
- Config versioning / migration between schema versions — future phase
- HMI config editor UI — M3 hmi_server phase
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PLAT-06 | JSON Schema validation for all 14 YAML config files | jsonschema 4.x + PyYAML pattern fully covers this; Draft 2020-12 supported natively; CLI script via tools/validate-config.py invoked from Makefile |
</phase_requirements>

---

## Summary

Phase 2 delivers all 14 JSON Schema files, a validation CLI script, three example config profiles (residential/commercial/container), and a Makefile target that runs as a CI gate. The technical domain is well-understood: Python's `jsonschema` library (v4.x) handles YAML-loaded dicts natively, `Draft202012Validator` is the current recommended validator class, and `iter_errors()` gives the error path information needed to produce the required friendly prose messages.

The locked decision to use `additionalProperties: false` everywhere is correct but has one important pitfall: when schemas use `allOf` composition (e.g., if bms_config extends a base device schema), `additionalProperties: false` in a subschema will reject properties defined in sibling subschemas. The solution is to either use `unevaluatedProperties: false` (Draft 2020-12 feature) at the composed-schema level and omit `additionalProperties: false` in the base, or — simpler for this project — avoid composition and keep each config schema flat and self-contained. Given the 14 schemas are independent files for independent subsystems, flat self-contained schemas are the right approach.

The `x-` custom metadata keywords (x-hot-reload, x-mutable, x-unit) are legal in JSON Schema under the "annotation vocabulary" model: unknown keywords are silently ignored by validators, so they do not interfere with validation. The config_manager can read them directly from the loaded schema dict at runtime.

**Primary recommendation:** Use `jsonschema` 4.x with `Draft202012Validator`, flat self-contained schemas per config file, PyYAML for loading, and a single `tools/validate-config.py` script that iterates all 14 files and produces field-level friendly error messages. Wire into `make validate` and add a CI step to pr-check.yml.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `jsonschema` | 4.25.x (latest stable) | JSON Schema validation for Python dicts | Official Python implementation; full Draft 2020-12 + 07 support; `iter_errors()` gives structured error paths; battle-tested |
| `PyYAML` | 6.x | YAML → Python dict loading | Standard YAML library; `yaml.safe_load()` produces plain dicts that jsonschema validates directly |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `ruamel.yaml` | 0.18.x | YAML loading with comment/line-number preservation | Only if error messages need to report source line numbers in YAML; adds complexity, not required for this phase |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `jsonschema` | `pydantic` v2 | Pydantic would require writing Python model classes that duplicate the schema intent. JSON Schema files are the deliverable here (machine-readable, language-agnostic, consumable by future HMI/cloud tools). Pydantic validation is a runtime tool, not a schema artifact. Not appropriate for this phase. |
| `jsonschema` | `jsonschema-rs` (Rust bindings) | Faster validation (~10x), same API shape, actively maintained (0.42.1, Feb 2026). Performance irrelevant for startup/CI config validation at 14 files. Introduces Rust compile dependency. Use only if validation becomes a hot path (not the case here). |
| `jsonschema` | `pykwalify` | YAML-native schema language, not JSON Schema. Non-standard, less tooling support. Does not meet PLAT-06 ("JSON Schema validation"). |
| `Draft202012Validator` | `Draft7Validator` | Both fully supported by jsonschema 4.x. Draft 2020-12 is preferred (see Draft Version section). |

**Installation (via uv, into root dev group):**
```bash
uv add --dev jsonschema pyyaml
```

The validation script lives in `tools/` and does not need to be a workspace member. It will be invoked via `uv run python tools/validate-config.py`.

---

## Architecture Patterns

### Recommended Directory Structure

```
config/
├── schemas/                     # JSON Schema definitions (14 files)
│   ├── system_config.schema.json
│   ├── bms_config.schema.json
│   ├── pcs_config.schema.json
│   ├── btms_config.schema.json
│   ├── meter_config.schema.json
│   ├── dg_config.schema.json
│   ├── pv_config.schema.json
│   ├── control_config.schema.json
│   ├── alarms_config.schema.json
│   ├── schedule_config.schema.json
│   ├── cloud_config.schema.json
│   ├── network_config.schema.json
│   ├── gpio_config.schema.json
│   └── hmi_config.schema.json
├── profiles/
│   ├── residential/             # Reference template — small (1 cluster, 4 racks)
│   │   ├── system_config.yaml
│   │   ├── bms_config.yaml
│   │   └── ... (all 14 files)
│   ├── commercial/              # Medium (2 clusters, 8 racks)
│   │   └── ...
│   └── container/               # Large (4 clusters, 16 racks)
│       └── ...
├── system_config.yaml           # Active config (pre-populated: residential)
├── bms_config.yaml
└── ... (all 14 active config files)
tools/
└── validate-config.py           # Validation CLI script
Makefile                         # add: validate target
.github/workflows/pr-check.yml  # add: validate step
```

**Naming convention:** `{config_name}.schema.json` — matches the YAML filename, `.schema.json` suffix is the established community convention for JSON Schema files, understood by editors (VS Code, JetBrains) for automatic schema association.

### Pattern 1: Draft 2020-12 with flat `additionalProperties: false`

**What:** Each schema is a standalone object schema with all properties declared inline. No `allOf` composition across schemas. `additionalProperties: false` at root level only.

**When to use:** Always, for this project's 14 independent config files. Avoids the `additionalProperties` + `allOf` pitfall (see Pitfalls section).

**Example (system_config.schema.json):**
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://revx-energy.com/ems/schemas/system_config",
  "title": "system_config",
  "description": "EMS system topology and identity configuration",
  "type": "object",
  "additionalProperties": false,
  "required": ["system", "topology"],
  "properties": {
    "system": {
      "type": "object",
      "additionalProperties": false,
      "required": ["site_id", "site_name"],
      "properties": {
        "site_id": { "type": "string" },
        "site_name": { "type": "string" }
      }
    },
    "topology": {
      "type": "object",
      "additionalProperties": false,
      "required": ["cluster_count", "racks_per_cluster", "modules_per_rack", "cells_per_module"],
      "properties": {
        "cluster_count": {
          "type": "integer", "minimum": 1, "maximum": 8,
          "x-unit": "count",
          "description": "Number of battery clusters (one CAN bus per cluster)"
        },
        "racks_per_cluster": {
          "type": "integer", "minimum": 1, "maximum": 16,
          "x-unit": "count",
          "description": "BMU racks per cluster"
        },
        "modules_per_rack": {
          "type": "integer", "minimum": 3, "maximum": 20,
          "x-unit": "count",
          "description": "LMU modules per rack"
        },
        "cells_per_module": {
          "type": "integer", "minimum": 16, "maximum": 108,
          "x-unit": "count",
          "description": "Series cells per LMU module"
        },
        "temps_per_module": {
          "type": "integer", "minimum": 4, "maximum": 40,
          "x-unit": "count",
          "description": "Temperature sensors per module"
        }
      }
    }
  }
}
```

### Pattern 2: `if/then` for cross-field protocol dependencies

**What:** Use `if/then` (available in Draft 2020-12 and Draft 7) to make certain fields required only when a discriminator field has a specific value. Example: `baud_rate` and `parity` are required when `protocol == "rtu"`, while `host` and `port` are required when `protocol == "tcp"`.

**When to use:** pcs_config, btms_config, meter_config, dg_config, pv_config — any device config that supports both RTU and TCP Modbus.

**Example (within pcs_config.schema.json connection object):**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["protocol"],
  "properties": {
    "protocol": { "type": "string", "enum": ["rtu", "tcp"] },
    "device":   { "type": "string" },
    "baud_rate":{ "type": "integer", "enum": [9600, 19200, 38400, 115200] },
    "parity":   { "type": "string",  "enum": ["N", "E", "O"] },
    "stopbits": { "type": "integer", "enum": [1, 2] },
    "host":     { "type": "string" },
    "port":     { "type": "integer", "minimum": 1, "maximum": 65535 }
  },
  "if":   { "properties": { "protocol": { "const": "rtu" } }, "required": ["protocol"] },
  "then": { "required": ["device", "baud_rate", "parity"] },
  "else": { "required": ["host", "port"] }
}
```
Source: Verified against https://json-schema.org/understanding-json-schema/reference/conditionals

### Pattern 3: x- metadata for hot-reload annotation

**What:** Custom extension keywords prefixed with `x-` are annotation-only: validators ignore them, but code can read them from the loaded schema dict. Use three custom keywords: `x-hot-reload`, `x-mutable`, `x-unit`.

**When to use:**
- `x-hot-reload: true` — at schema root for the 3 hot-reloadable configs (control_config, alarms_config, schedule_config)
- `x-mutable: true` — on individual properties whose values config_manager may apply without restart
- `x-mutable: false` — on structural/connection properties that require restart to take effect
- `x-unit` — on all numeric properties (values: "V", "A", "W", "kW", "°C", "ms", "s", "%", "count", "Hz")

**x-mutable assignment rules (domain judgment):**
- Mutable (thresholds/setpoints that affect ongoing control): SOC limits, power setpoints, temperature thresholds, alarm thresholds, charge/discharge windows, source priority weights
- Immutable (structural/connection fields that require a driver restart): protocol, device path, baud_rate, parity, slave_id, topic prefixes, TLS cert paths, GPIO pin assignments

**Example (control_config.schema.json excerpt):**
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "x-hot-reload": true,
  "properties": {
    "soc_limits": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "charge_cutoff_pct": {
          "type": "number", "minimum": 80, "maximum": 100,
          "x-unit": "%",
          "x-mutable": true,
          "description": "SOC level at which charging stops"
        },
        "discharge_cutoff_pct": {
          "type": "number", "minimum": 0, "maximum": 30,
          "x-unit": "%",
          "x-mutable": true
        }
      }
    }
  }
}
```

### Pattern 4: Validation script with friendly error messages

**What:** A standalone Python script (`tools/validate-config.py`) that loads each YAML file, validates against its schema, formats errors as friendly prose, and exits non-zero on any failure.

**Key design:**
- Use `Draft202012Validator.iter_errors()` (not `validate()`) — collects per-file errors without aborting
- Use `error.absolute_path` to build a dotted field path for the error message
- Build the friendly prose message from `error.validator`, `error.validator_value`, and `error.instance`
- Exit code 0 = all valid, 1 = one or more files failed

**Example implementation skeleton:**
```python
#!/usr/bin/env python3
"""Validate all EMS YAML config files against their JSON Schema definitions."""

import json
import sys
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path("config/schemas")
CONFIG_DIR  = Path("config")

CONFIG_FILES: list[str] = [
    "system_config", "bms_config", "pcs_config", "btms_config",
    "meter_config",  "dg_config",  "pv_config",  "control_config",
    "alarms_config", "schedule_config", "cloud_config",
    "network_config","gpio_config", "hmi_config",
]

def format_error(filename: str, error: object) -> str:
    path_parts = list(error.absolute_path)
    field_path  = " -> ".join(str(p) for p in path_parts) if path_parts else "(root)"
    value_repr  = repr(error.instance) if not isinstance(error.instance, dict) else "{...}"
    return (
        f"ERROR: In {filename}, field '{field_path}' "
        f"is set to {value_repr} but {error.message}."
    )

def validate_file(name: str) -> list[str]:
    yaml_path   = CONFIG_DIR  / f"{name}.yaml"
    schema_path = SCHEMAS_DIR / f"{name}.schema.json"
    errors: list[str] = []
    if not yaml_path.exists():
        return [f"ERROR: {yaml_path} not found."]
    if not schema_path.exists():
        return [f"ERROR: {schema_path} not found."]
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    with open(schema_path) as f:
        schema = json.load(f)
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(data):
        errors.append(format_error(yaml_path.name, error))
        break   # fail-fast per file: stop at first error per file, report, move on
    return errors

def main() -> int:
    all_errors: list[str] = []
    for name in CONFIG_FILES:
        file_errors = validate_file(name)
        all_errors.extend(file_errors)
        for msg in file_errors:
            print(msg)
    if not all_errors:
        print(f"OK: All {len(CONFIG_FILES)} config files are valid.")
        return 0
    print(f"\nFAIL: {len(all_errors)} validation error(s) found.", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main())
```

Source: jsonschema 4.x docs — https://python-jsonschema.readthedocs.io/en/stable/errors/

### Pattern 5: Makefile `validate` target

**What:** Add a `validate` target to the existing Makefile, consistent with Phase 1 conventions (`make build`, `make test`, `make lint`).

```makefile
validate: ## Validate all YAML config files against JSON Schema
	uv run python tools/validate-config.py
```

The `.PHONY` list must include `validate`.

### Pattern 6: CI integration in pr-check.yml

**What:** Add a "Validate config" step to the existing `build-and-test` job in `.github/workflows/pr-check.yml`, after Python deps are installed.

```yaml
- name: Validate config files
  run: uv run python tools/validate-config.py
```

This step runs after `uv sync` so jsonschema and pyyaml are available.

### Anti-Patterns to Avoid

- **Bare `validate()` call:** Raises `ValidationError` on the first error and aborts. Use `iter_errors()` instead so you report the first error per file before moving to the next.
- **`additionalProperties: false` inside `allOf` subschemas:** If you compose schemas using `allOf`, `additionalProperties: false` in a base subschema will reject properties defined by the extending schema. Either keep schemas flat (recommended) or use `unevaluatedProperties: false` at the composed root (Draft 2020-12 only).
- **Storing schemas as YAML:** JSON Schema files should be `.json`, not `.yaml`. Validators load them as JSON; YAML `.schema.yaml` files require extra load step and are non-standard for this domain.
- **One giant schema file:** Each config file gets its own schema file. Easier to read, easier to test, simpler error messages pointing to the right file.
- **Using `error.message` verbatim for user output:** jsonschema's built-in messages are technically accurate but terse (`'25' is greater than the maximum of 16`). Wrap them with file name and field path context as shown in Pattern 4.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Schema validation engine | Custom recursive dict checker | `jsonschema` 4.x | JSON Schema handles `if/then`, `allOf`, `enum`, `minimum/maximum`, `required`, `additionalProperties`, `pattern`, `$ref` — all of which are needed here. A hand-rolled checker misses edge cases. |
| YAML loading | Custom YAML parser | `PyYAML` `yaml.safe_load()` | Handles all YAML quirks (anchors, aliases, multiline strings, booleans as strings). `safe_load` prevents arbitrary Python object construction from untrusted config. |
| Error path formatting | Custom traversal | `error.absolute_path` deque | jsonschema correctly tracks nested paths through `allOf`, `if/then`, arrays, etc. Hand-rolled trackers miss these. |
| Schema version negotiation | Custom `$schema` detection | Pass `Draft202012Validator` explicitly | Avoids the `jsonschema` default behavior of auto-detecting draft from `$schema` field, which can silently use an older validator if `$schema` is wrong. |

**Key insight:** jsonschema's `additionalProperties`, `if/then`, `enum`, and `minimum/maximum` cover 95% of what this project needs. The only custom work needed is the friendly error message formatter in the CLI script.

---

## Draft Version Decision: Draft 2020-12

**Recommendation:** Use `Draft202012Validator` and `"$schema": "https://json-schema.org/draft/2020-12/schema"`.

**Rationale:**
1. jsonschema 4.x fully supports Draft 2020-12 — no compatibility risk (verified: https://python-jsonschema.readthedocs.io/)
2. `unevaluatedProperties` (2020-12 only) is a cleaner solution than `additionalProperties` when composition is needed — available if schemas need to be refactored later
3. Draft 2020-12 is the current stable version (alongside draft-07 which is the last LTS of the old paradigm)
4. `if/then/else` is available in both draft-07 and 2020-12 — no regression risk
5. VS Code and JetBrains resolve `"$schema": "https://json-schema.org/draft/2020-12/schema"` to enable editor autocomplete in YAML files

**The one breaking change from draft-07 → 2020-12 that matters:** Tuple validation uses `prefixItems` + `items` instead of `items` (array) + `additionalItems`. This project's config schemas do not use tuple validation (all arrays are homogeneous lists), so this is not relevant.

---

## Common Pitfalls

### Pitfall 1: `additionalProperties: false` + `allOf` composition breaks schema extension

**What goes wrong:** A base schema has `additionalProperties: false`. A derived schema uses `allOf: [base]` and adds new properties. The base schema's `additionalProperties: false` rejects the new properties because it only sees the properties defined in the base.

**Why it happens:** `additionalProperties` only considers `properties` and `patternProperties` declared in the *same schema object*, not in sibling `allOf` members.

**How to avoid:** Keep each of the 14 config schemas flat and self-contained — no cross-schema `allOf` composition. If a future refactor needs composition, use `unevaluatedProperties: false` at the composed level (Draft 2020-12).

**Warning signs:** Validation rejects valid config with "Additional properties are not allowed" errors pointing to fields that clearly exist in your YAML.

### Pitfall 2: `yaml.safe_load()` returns `None` for empty files

**What goes wrong:** A freshly created config file is empty. `yaml.safe_load()` returns `None`. Passing `None` to `Draft202012Validator.iter_errors()` may produce confusing type errors ("None is not of type 'object'") instead of a clear "file is empty" message.

**How to avoid:** Guard for `None` after loading:
```python
data = yaml.safe_load(f)
if data is None:
    errors.append(f"ERROR: {yaml_path.name} is empty or contains only comments.")
    continue
```

### Pitfall 3: `iter_errors()` with `if/then` produces confusing sub-errors

**What goes wrong:** When an `if/then` conditional fails, jsonschema may emit errors from both the `then` and `else` branches, producing multiple overlapping error messages for a single root cause.

**How to avoid:** When processing errors, check `error.validator` — if it is `"if"`, inspect `error.context` for the sub-errors from the failing branch and report only the most specific one. Alternatively, rely on fail-fast per file (stop at first error) which avoids accumulating confusing multi-error output from a single root cause.

### Pitfall 4: `uv add jsonschema` adds to wrong package

**What goes wrong:** `uv add jsonschema` inside a module directory (e.g., `src/config_manager/`) adds it to that package's dependencies. The validation script is a dev tool, not a runtime dependency of config_manager.

**How to avoid:** Add to the root workspace dev group: `uv add --dev jsonschema pyyaml` from the repo root. The `tools/validate-config.py` script is invoked via `uv run` in the workspace context.

### Pitfall 5: Schema `$id` URIs cause unexpected `$ref` resolution

**What goes wrong:** If `$id` is set to a real URL and the schema includes `$ref`, jsonschema may attempt HTTP fetches for schema resolution.

**How to avoid:** For this project, schemas are standalone with no cross-schema `$ref`. Set `$id` to a non-resolvable canonical URI (e.g., `"https://revx-energy.com/ems/schemas/system_config"`) for identification purposes only. Do not use `$ref` across schema files.

### Pitfall 6: Profile YAML comments stripped by PyYAML round-trip

**What goes wrong:** If code ever reads and re-writes a YAML profile, PyYAML strips all comments. The heavily-commented profile files lose their documentation.

**How to avoid:** Profile files are read-only templates, never re-written by code. Only the active `config/` files are ever modified at runtime. This phase only reads YAMLs, never writes them.

---

## Code Examples

Verified patterns from official sources:

### Loading YAML and validating with Draft202012Validator
```python
# Source: https://python-jsonschema.readthedocs.io/en/stable/validate/
import json, yaml
from jsonschema import Draft202012Validator
from pathlib import Path

schema = json.loads(Path("config/schemas/system_config.schema.json").read_text())
data   = yaml.safe_load(Path("config/system_config.yaml").read_text())

validator = Draft202012Validator(schema)
for error in validator.iter_errors(data):
    path = " -> ".join(str(p) for p in error.absolute_path) or "(root)"
    print(f"  Field '{path}': {error.message}")
```

### Iterating errors with path context
```python
# Source: https://python-jsonschema.readthedocs.io/en/stable/errors/
for error in validator.iter_errors(data):
    print(error.message)          # terse built-in message
    print(list(error.path))       # ['topology', 'racks_per_cluster']
    print(list(error.absolute_path))  # same, always from root
    print(error.instance)         # the offending value (25)
    print(error.validator)        # 'maximum'
    print(error.validator_value)  # 16 (the schema constraint)
```

### Draft-specific validator with `if/then` (verified working in Draft 2020-12)
```python
# Source: https://context7.com/python-jsonschema/jsonschema
schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"protocol": {"enum": ["rtu", "tcp"]}},
    "if":   {"properties": {"protocol": {"const": "rtu"}}, "required": ["protocol"]},
    "then": {"required": ["device", "baud_rate"]},
    "else": {"required": ["host", "port"]}
}
validator = Draft202012Validator(schema)
print(validator.is_valid({"protocol": "rtu", "device": "/dev/ttyUSB0", "baud_rate": 9600}))  # True
print(validator.is_valid({"protocol": "rtu"}))  # False — missing device, baud_rate
```

### Topology range constraints
```json
{
  "cluster_count":    { "type": "integer", "minimum": 1, "maximum": 8  },
  "racks_per_cluster":{ "type": "integer", "minimum": 1, "maximum": 16 },
  "modules_per_rack": { "type": "integer", "minimum": 3, "maximum": 20 },
  "cells_per_module": { "type": "integer", "minimum": 16,"maximum": 108},
  "temps_per_module": { "type": "integer", "minimum": 4, "maximum": 40 }
}
```

---

## The 14 Config Files: Schema Coverage Plan

Based on architecture.md section 3.3:

| Config File | Hot-Reload | Key Schema Features |
|-------------|------------|---------------------|
| `system_config` | No | Topology ranges (cluster/rack/module/cell), site identity |
| `bms_config` | No | CAN interface config, DBC path, BMU addressing, cycle rates |
| `pcs_config` | No | if/then RTU/TCP, slave ID, register offsets |
| `btms_config` | No | if/then RTU/TCP, BTMS mode enum (liquid/air) |
| `meter_config` | No | if/then RTU/TCP, meter model enum |
| `dg_config` | No | if/then RTU/TCP, generator capacity kW |
| `pv_config` | No | if/then RTU/TCP, inverter capacity kW |
| `control_config` | **Yes** | SOC limits, power setpoints, source priority enums — x-mutable: true on all setpoints |
| `alarms_config` | **Yes** | Threshold arrays per signal, hysteresis, delay_ms, severity enum — x-mutable: true on thresholds/delays |
| `schedule_config` | **Yes** | Time windows (HH:MM format), 96-point curve arrays, mode enum — x-mutable: true on all |
| `cloud_config` | No | MQTT broker host/port, topic prefix, TLS cert paths, interval_s |
| `network_config` | No | ETH0/ETH1 config, static/dhcp enum |
| `gpio_config` | No | DI/DO pin assignments — strict enum: DO-0 through DO-7, DI-0 through DI-7 |
| `hmi_config` | No | HTTP port, websocket interval, PIN hash (string pattern) |

**Note on 4 "TBD" files from CONTEXT.md:** The CONTEXT.md notes "14 config files listed in requirements Section 3" and identifies 10 by name with 4 TBD from architecture. The architecture.md section 3.3 lists all 14 explicitly (shown above). All 14 are now identified.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x (already in root dev group) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — testpaths = ["tests"] |
| Quick run command | `uv run pytest tests/test_config_validation.py -x` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PLAT-06 (SC1) | All 14 schemas exist and parse as valid JSON | unit | `uv run pytest tests/test_config_validation.py::test_all_schemas_exist -x` | Wave 0 |
| PLAT-06 (SC2) | Valid YAML passes validation (residential profile) | unit | `uv run pytest tests/test_config_validation.py::test_residential_profile_valid -x` | Wave 0 |
| PLAT-06 (SC2) | Invalid YAML with known bad field fails with exit code 1 | unit | `uv run pytest tests/test_config_validation.py::test_invalid_config_rejected -x` | Wave 0 |
| PLAT-06 (SC2) | Error message contains the offending field name | unit | `uv run pytest tests/test_config_validation.py::test_error_message_contains_field -x` | Wave 0 |
| PLAT-06 (SC3) | system_config topology out-of-range values are rejected | unit | `uv run pytest tests/test_config_validation.py::test_topology_constraints -x` | Wave 0 |
| PLAT-06 (SC3) | system_config topology within-range values are accepted | unit | `uv run pytest tests/test_config_validation.py::test_topology_valid_range -x` | Wave 0 |
| PLAT-06 (SC4) | All 3 profiles (residential/commercial/container) pass validation | unit | `uv run pytest tests/test_config_validation.py::test_all_profiles_valid -x` | Wave 0 |
| PLAT-06 (SC4) | Active config/ directory validates clean (make validate) | smoke | `make validate` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_config_validation.py -x`
- **Per wave merge:** `uv run pytest tests/ -v`
- **Phase gate:** `make validate` returns 0 + full pytest green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_config_validation.py` — covers all PLAT-06 behaviors above
- [ ] `tests/conftest.py` — fixtures for loading schema+data pairs, tmp_path-based invalid YAML injection
- Framework install: already present (`pytest>=8.0` in root `pyproject.toml` dev group)

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Draft-07 as default | Draft 2020-12 as current | 2020 | `unevaluatedProperties` available; `prefixItems` for tuples; same `if/then` support |
| `items` + `additionalItems` for tuples | `prefixItems` + `items` | Draft 2020-12 | Not relevant — this project uses no tuple schemas |
| `validate()` raises on first error | `iter_errors()` for all errors | Stable since v3 | Enables the fail-fast-per-file pattern |
| `jsonschema.validate(schema=..., instance=...)` | `Draft202012Validator(schema).iter_errors(data)` | v4 recommendation | Explicit validator class avoids draft auto-detection |

**Deprecated/outdated:**
- `Draft3Validator`, `Draft4Validator`: Do not use. jsonschema still ships them but they are obsolete.
- `jsonschema.validate()` without explicit validator class: Auto-detects draft from `$schema` field, which can silently fall back to an old validator if `$schema` is mistyped. Prefer explicit `Draft202012Validator(schema)`.

---

## Open Questions

1. **Do pcs_config and the other Modbus device configs need per-device slave ID arrays, or a single slave ID?**
   - What we know: Architecture shows one device per RS485 port (PCS on RS485-1, Meter on RS485-2, etc.), so likely one slave ID per config file
   - What's unclear: Whether future multi-device support on a single RS485 bus needs an array; depends on M1 comm_manager design
   - Recommendation: Start with single slave ID integer for M0. Schema can be extended to array in M1 if comm_manager requires it.

2. **What is the exact field structure for alarms_config threshold arrays?**
   - What we know: ALM-02 requires "per-signal configurable thresholds with hysteresis and delay" — implies a list of alarm rule objects
   - What's unclear: Whether alarms_config is an array of rule objects or a keyed dict of signal names — this determines the JSON Schema structure
   - Recommendation: Design as keyed dict `{ "signal_name": { "threshold": ..., "hysteresis": ..., "delay_ms": ... } }` for readability. Schema uses `patternProperties` or a fixed dict of known signal names.

3. **Does `hmi_config` need the PIN stored as a hash or plaintext?**
   - What we know: HMI-08 requires PIN-based auth; storing plaintext PINs in YAML is a security concern
   - What's unclear: Whether hashing is done in M3 or if this phase should define the field type now
   - Recommendation: Schema this phase should define the field as `"type": "string"` with a comment noting it will be a bcrypt hash in production. The actual auth logic is M3 scope.

---

## Sources

### Primary (HIGH confidence)
- `/python-jsonschema/jsonschema` (Context7) — `iter_errors()`, `Draft202012Validator`, `ValidationError.absolute_path`, custom validators, if/then examples
- `/websites/json-schema_understanding-json-schema` (Context7) — `additionalProperties: false`, `unevaluatedProperties`, `if/then/else` conditionals, custom metadata keywords
- https://python-jsonschema.readthedocs.io/en/stable/validate/ — official API docs for validate/iter_errors patterns
- https://python-jsonschema.readthedocs.io/en/stable/errors/ — ValidationError attributes (message, path, absolute_path, instance, validator, validator_value)
- https://json-schema.org/understanding-json-schema/reference/conditionals — if/then/else syntax and truth table

### Secondary (MEDIUM confidence)
- https://json-schema.org/draft/2020-12/release-notes — breaking changes from 2019-09 to 2020-12 (prefixItems, dynamic refs); verified no impact on this project
- https://pypi.org/project/jsonschema-rs/ — jsonschema-rs 0.42.1 (Feb 17, 2026), active Rust-backed alternative; not needed here
- WebSearch results for jsonschema 4.x best practices 2024 — confirmed PyYAML + jsonschema is the standard Python pattern

### Tertiary (LOW confidence)
- None — all key claims verified through primary/secondary sources

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — jsonschema 4.x + PyYAML is the unambiguous standard; verified through Context7 and official docs
- Architecture: HIGH — patterns derived from official docs and project constraints; flat schemas avoid the composition pitfall
- Pitfalls: HIGH — additionalProperties+allOf pitfall is documented in official JSON Schema spec; others verified through jsonschema docs
- x- metadata keywords: HIGH — annotation-only keywords are a standard JSON Schema feature, verified against spec

**Research date:** 2026-02-26
**Valid until:** 2026-08-26 (jsonschema 4.x is stable; no breaking changes expected in this domain)
