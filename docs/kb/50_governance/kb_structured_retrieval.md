# KB Structured Retrieval

## Purpose
Prevent unbounded KB catalog loads in agent prompts. Retrieval must stay selective, bounded, and auditable.

## Invariants
- Use key-scoped retrieval APIs; do not load full catalogs in normal runtime.
- Respect prompt budget configuration from `configs/thresholds.yaml`.
- Return bounded payloads or explicit truncation markers.
- Keep behavior deterministic with graceful fallbacks on malformed/missing YAML.

## Canonical APIs
Module: `utils/kb_yaml_retriever.py`

- `get_evidence_field(field_key)`
- `get_reason_evidence_mapping(reason_code)`
- `get_triage_decision(reason_code)`
- `list_triage_reasons()`
- `get_symptom_diagnosis(symptom_name)`
- `list_symptoms()`
- `get_invariant(invariant_id)`
- `list_invariants(category=None)`

Budget helpers: `utils/kb.py`
- `KBPromptBudget`
- `render_entry_for_prompt(...)`
- `load_kb_budget_from_config(debug_mode)`

## Guardrails
- Full catalog loads are blocked for large registries unless debug override is explicitly enabled.
- Selective calls are always preferred and expected in runtime paths.
- Cache scope is field-level or key-level; do not cache full catalogs in agent-facing retrieval flows.
- On retrieval failure, return safe empty/none values and preserve runtime continuity.

## Configuration Contract
Budget knobs live under:
- `kb_prompt_budget`
- `kb_prompt_budget_debug`

Budget truncation must preserve high-signal keys first (IDs, statements, summaries, required fields).

## Validation
- `python -m utils.kb_taxonomy_check`
- `pytest -q tests/test_kb_yaml_retriever.py`

## Related
- [KB Constitution](kb_constitution.md)
- [Evidence Fields](../10_runtime_contracts/evidence.md)
- [Triage Runbook](../20_decision_system/triage_runbook.md)
- [Runtime Playbook](../20_decision_system/runtime_playbook.md)
