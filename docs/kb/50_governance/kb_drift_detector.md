# KB Drift Detector

## Overview

The KB drift detector identifies mismatches between code-defined runtime artifacts (reason codes, evidence keys, artifact names, invariant IDs) and their YAML registry documentation under `docs/kb/`.

**Purpose**: Ensure documentation stays synchronized with code. Detect when:
- Code emits reason codes not documented in triage YAML
- Code writes evidence keys not defined in evidence YAML
- Tests reference invariants not registered in invariants YAML
- YAML documents unused/orphaned entries

## Architecture

### Detection Types

**ERRORS** (code uses but YAML missing):
1. **Reason Drift**: Code emits `reason="x"` but triage YAML lacks entry
2. **Evidence Drift**: Code writes `evidence["key"]` but evidence YAML lacks definition
3. **Invariant Drift**: Tests reference `INV-*` but invariants YAML lacks entry

**WARNINGS** (YAML documents but code never uses):
1. **Orphaned Reasons**: YAML has reason but code never references it
2. **Orphaned Invariants**: YAML defines invariant but tests never reference it

### Input Sources

**Code Sources**:
- `core/scenario/reasons.py` - canonical reason registry (`REASON_REGISTRY`)
- `core/**/*.py` - grep for `reason="..."` assignments
- `core/**/*.py`, `tests/**/*.py` - grep for `evidence["..."]` writes
- `tests/**/*.py` - grep for `INV-*` references

**YAML Sources**:
- `docs/kb/20_decision_system/triage_decision_table.yaml` - reason codes
- `docs/kb/10_runtime_contracts/evidence_fields.yaml` - evidence key definitions
- `docs/kb/10_runtime_contracts/evidence_artifacts.yaml` - artifact registry
- `docs/kb/00_foundation/invariants_registry.yaml` - invariant IDs

## Usage

### Basic Usage

```bash
# Check for drift (errors only)
python scripts/kb_drift_check.py

# Include warnings (orphaned entries)
python scripts/kb_drift_check.py --warnings

# JSON output
python scripts/kb_drift_check.py --json

# Write report to file
python scripts/kb_drift_check.py --json -o storage/debug/kb_drift_report.json

# Verbose logging
python scripts/kb_drift_check.py -v --warnings
```

### Exit Codes

- `0` - No errors found (warnings may exist)
- `1` - Errors found (drift detected)

### Example Output

**Text Format** (default):
```
============================================================
KB DRIFT ERRORS: 3 found
============================================================
[REASON] calendar_not_verified
  Type: missing_in_yaml
  Location: core/scenario/google_flights.py

[EVIDENCE] calendar.nav_direction
  Type: missing_in_yaml
  Location: core/scenario/calendar_driver.py

[INVARIANT] INV-CALENDAR-042
  Type: missing_in_yaml
  Location: tests/test_calendar_driver.py

============================================================
KB DRIFT WARNINGS: 2 found
============================================================
[REASON] legacy_timeout_reason
  Type: orphaned_in_yaml
  Location: docs/kb/20_decision_system/triage_decision_table.yaml

[INVARIANT] INV-LEGACY-001
  Type: orphaned_in_yaml
  Location: docs/kb/00_foundation/invariants_registry.yaml
```

**JSON Format** (`--json`):
```json
{
  "summary": {
    "errors": 3,
    "warnings": 2
  },
  "errors": [
    {
      "category": "reason",
      "item_key": "calendar_not_verified",
      "drift_type": "missing_in_yaml",
      "severity": "error",
      "source_location": "core/scenario/google_flights.py"
    }
  ],
  "warnings": [
    {
      "category": "reason",
      "item_key": "legacy_timeout_reason",
      "drift_type": "orphaned_in_yaml",
      "severity": "warning",
      "source_location": "docs/kb/20_decision_system/triage_decision_table.yaml"
    }
  ]
}
```

## Integration

### Pre-commit/CI Integration

Add to `.github/workflows/ci.yml` or pre-commit hook:

```yaml
- name: Check KB drift
  run: python scripts/kb_drift_check.py
  # Fails build if errors found
```

### Manual Workflow

1. **Development**: Add new reason code / evidence key to code
2. **Run drift check**: `python scripts/kb_drift_check.py`
3. **Fix drift**: Add missing entries to YAML registries
4. **Verify**: Re-run drift check until clean

### Fixing Drift

**Example: Add missing reason code to YAML**

If drift check reports:
```
[REASON] calendar_nav_overflow
  Type: missing_in_yaml
  Location: core/scenario/calendar_driver.py
```

Fix by adding to `docs/kb/20_decision_system/triage_decision_table.yaml`:
```yaml
reason_tree:
  calendar_nav_overflow:
    severity: error
    next_steps:
      - "Check calendar.nav_steps evidence"
      - "Verify target month is within 12 months"
```

**Example: Add missing evidence key to YAML**

If drift check reports:
```
[EVIDENCE] calendar.nav_direction
  Type: missing_in_yaml
  Location: core/scenario/calendar_driver.py
```

Fix by adding to `docs/kb/10_runtime_contracts/evidence_fields.yaml`:
```yaml
namespaces:
  calendar:
    fields:
    - key: calendar.nav_direction
      type: string
      values: ["forward", "backward"]
      required: false
      producers:
      - core/scenario/calendar_driver.py
      description: Direction of month navigation
```

## Implementation Details

### Detection Algorithm

**Step 1 - Load YAML Registries**:
1. Parse `triage_decision_table.yaml` → extract reason codes from `reason_tree`
2. Parse `evidence_fields.yaml` → extract namespaced evidence keys
3. Parse `invariants_registry.yaml` → extract invariant IDs from `invariants` list

**Step 2 - Extract Code Usage**:
1. Parse `core/scenario/reasons.py` → extract `REASON_REGISTRY` keys (authoritative)
2. Grep `core/**/*.py` for `reason="..."` → find ad-hoc reason strings
3. Grep `core/**/*.py`, `tests/**/*.py` for `evidence["..."]` → find evidence key writes
4. Grep `tests/**/*.py` for `INV-[A-Z]+-\\d{3}` → find invariant references

**Step 3 - Compare & Report**:
1. `code_reasons - yaml_reasons` → ERROR: missing in YAML
2. `yaml_reasons - code_reasons` → WARNING: orphaned in YAML
3. `code_evidence - yaml_evidence` → ERROR: missing in YAML
4. `code_invariants - yaml_invariants` → ERROR: missing in YAML
5. `yaml_invariants - code_invariants` → WARNING: orphaned in YAML

### Filtering Rules

**Evidence Keys**:
- Skip keys starting with `diag.` (diagnostic signals, not documented)
- Skip common test fixtures: `"x"`, `"test"`, `"dummy"`

**Reason Codes**:
- Skip reserved codes: `"success"`, `"unknown"`, `""`

## Testing

Comprehensive test suite in `tests/test_kb_drift_check.py`:

**Test Coverage**:
- `TestDriftReport` - Report data structure and formatting
- `TestYAMLLoaders` - YAML parsing for reasons/evidence/invariants
- `TestCodeExtractors` - Code parsing for reason/evidence/invariant usage
- `TestDriftDetection` - End-to-end drift detection with fixtures
- `TestRealRepo` - Sanity check on real repository

**Run Tests**:
```bash
pytest tests/test_kb_drift_check.py -xvs
```

**Test Results**:
```
18 passed in 0.20s
```

## Current Status

**Latest Run** (Feb 26, 2026):

```bash
$ python scripts/kb_drift_check.py --warnings | head -30
```

**Summary**:
- **Errors**: 162 evidence keys in code not documented in YAML
- **Warnings**: TBD orphaned entries in YAML

**Top Missing Evidence Keys**:
- `calendar.nav_direction`
- `calendar.strategies_tried`
- `combobox.activation_attempts`
- `coordination.gate.*` (multiple)
- `verify.method_used`
- `route.scope_detected`

## Maintenance

**When to Run**:
- After adding new reason codes to `core/scenario/reasons.py`
- After adding new evidence keys in scenario/plugin code
- After adding new INV-* references in tests
- In CI pipeline (fails build on errors)
- Weekly scheduled check for orphaned entries

**When to Update**:
- **Add to YAML**: When drift check reports missing entries (errors)
- **Remove from YAML**: When orphaned entries accumulate (warnings)
- **Update Code**: When YAML documents required evidence not produced

## Files

**Implementation**:
- `utils/kb_drift.py` - Main drift detection logic (480 lines)
- `scripts/kb_drift_check.py` - CLI wrapper script (80 lines)

**Tests**:
- `tests/test_kb_drift_check.py` - Comprehensive test suite (350 lines, 18 tests)

**Documentation**:
- `docs/kb/50_governance/kb_drift_detector.md` - This file

## Future Enhancements

**Potential Improvements**:
1. **Auto-fix Mode**: Generate placeholder YAML entries for missing items
2. **Artifact Drift**: Track artifact filenames vs registry (not yet implemented)
3. **Evidence Producer/Consumer Match**: Verify producers actually produce required keys
4. **CI Integration**: Auto-comment PRs with drift reports
5. **Historical Tracking**: Track drift trends over time
6. **Smart Orphan Detection**: Detect truly unused vs. legitimately documented entries

## References

- [Evidence Field Reference](../10_runtime_contracts/evidence.md)
- [Triage Runbook](../20_decision_system/triage_runbook.md)
- [Reason Registry](../../../core/scenario/reasons.py)
- [Invariants Registry](../00_foundation/invariants_registry.yaml)
