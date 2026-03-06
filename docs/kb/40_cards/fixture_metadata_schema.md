# Fixture Triage Metadata Schema (v1)

**Purpose**: Define the deterministic metadata JSON format stored next to sanitized HTML fixtures under `tests/fixtures/`.
**When to read**: When generating fixture triage metadata, validating fixture annotations, or adding new fixture-driven tooling.
**Key invariants**: Deterministic generation, no network/model calls, no long raw HTML snippets, small stable fields only.

---

## Scope

This schema is for fixture triage metadata files:

- `tests/fixtures/<site>/<name>.triage.json`

This file is separate from fixture capture provenance metadata:

- `tests/fixtures/<site>/<name>.meta.json`

`*.meta.json` stores capture/sanitization provenance.
`*.triage.json` stores deterministic classification and expected outcomes for tests and triage workflows.

---

## Canonical Schema (v1)

```json
{
  "schema_version": "fixture_triage_v1",
  "site": "google_flights",
  "fixture_name": "results_sample",
  "fixture_path": "tests/fixtures/google_flights/results_sample.html",
  "captured_from": {
    "run_id": "20260222_000000_000000",
    "source_path": "storage/runs/.../artifacts/scenario_google_flights_last.html",
    "captured_at": "2026-02-22T00:00:00+00:00"
  },
  "page_kind": "flights_results",
  "locale_hint": "en-US",
  "signals": {
    "has_price_token": true,
    "has_results_list": true,
    "has_calendar_dialog": false,
    "has_origin_dest_inputs": false
  },
  "expected": {
    "extraction": {
      "status": "ok",
      "currency": "USD"
    },
    "ui_driver": {
      "readiness": "ready"
    }
  },
  "kb_refs": [],
  "notes": ""
}
```

---

## Field Rules

### `schema_version`

- MUST equal `fixture_triage_v1`

### `site`

- String site identifier (e.g., `google_flights`, `skyscanner`)
- MUST match fixture directory segment under `tests/fixtures/<site>/`

### `fixture_name`

- Basename of the fixture HTML file without extension

### `fixture_path`

- Repository-relative path to fixture HTML
- SHOULD use forward slashes

### `captured_from`

- Object containing optional provenance fields copied from `*.meta.json` or preserved from prior triage metadata
- Allowed keys:
  - `run_id`
  - `source_path`
  - `captured_at`
- Keys MAY be omitted when unknown

### `page_kind` (enum)

Allowed values:

- `flights_results`
- `search_form`
- `consent`
- `error`
- `unknown`

### `locale_hint`

- Best-effort locale inferred from `<html lang>` or obvious language tokens
- Use `unknown` when not inferable

### `signals`

Boolean-only deterministic heuristics:

- `has_price_token`
- `has_results_list`
- `has_calendar_dialog`
- `has_origin_dest_inputs`

### `expected`

Expected deterministic behavior for fixture-driven tests and triage.

#### `expected.extraction`

- `status` (enum):
  - `ok`
  - `missing_price`
  - `parse_error`
  - `not_applicable`
- `currency`:
  - ISO code like `USD`, `JPY`, etc., or `unknown`
- Optional numeric bounds:
  - `price_min`
  - `price_max`
- Optional `reason_code`

#### `expected.ui_driver`

- `readiness` (enum):
  - `ready`
  - `unready`
  - `unknown`
- Optional `reason_code`

### `kb_refs`

- Array of KB references used during triage or authoring
- Each item:
  - `type`: `card` | `pattern` | `doc`
  - `path`: repository-relative KB path
- If present, referenced path MUST exist

### `notes`

- Free-form short notes for human maintainers
- SHOULD remain concise

---

## Safety Rules

- Metadata MUST NOT include secrets, tokens, cookies, or raw credentials
- Metadata MUST NOT store raw HTML snippets longer than 200 characters
- Prefer booleans, enums, and short strings over copied page content

---

## Generation & Merge Rules

- Classify HTML using deterministic string/regex heuristics only
- Preserve manual triage edits in existing `*.triage.json` for:
  - `expected.*`
  - `notes`
  - `kb_refs`
  - `captured_from`
- Preserve capture provenance from `*.meta.json` when present
- Re-validate after merge before writing
