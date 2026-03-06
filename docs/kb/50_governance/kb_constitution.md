# KB Constitution

## Scope
Defines canonical KB governance: taxonomy, naming, canonicality, and change protocol.

## Core Principles
1. Canonicality: one authoritative doc per concept.
2. Timelessness: no temporal rollout language in canonical KB docs.
3. Agent-readiness: docs must be discoverable via `docs/kb/kb_index.yaml` and `docs/kb/INDEX.md`.
4. Auditability: behavioral claims must be grounded in contracts, evidence, and tests.

## Taxonomy
- `00_foundation/`: doctrine, architecture, invariants
- `10_runtime_contracts/`: runtime contracts, evidence, budget/timeout interfaces
- `20_decision_system/`: playbook, triage runbook, symptom/decision maps
- `30_patterns/`: reusable UI and locale patterns
- `40_cards/`: remediation cards and authoring rules
- `50_governance/`: governance policies and ADRs

## Canonicality Rules
- Keep exactly one canonical document per topic.
- Secondary docs should link to canonical sources instead of duplicating definitions.
- Historical implementation narratives belong in git history, not the canonical KB tree.

## Naming Rules
- Markdown and YAML files in KB use `lowercase_snake_case` names.
- KB directory segments use numeric taxonomy prefixes (`00_`, `10_`, ...).

## Change Protocol
1. Update canonical KB docs first.
2. Update `docs/kb/INDEX.md` and `docs/kb/kb_index.yaml` when topics/paths change.
3. Run `python -m utils.kb_taxonomy_check`.
4. Keep runtime contracts aligned with `ActionBudget`, `StepResult`, reason registry, and evidence schema.

## Root Documentation Policy
Root markdown files must stay minimal and high-level.
Allowed root docs:
- `README.md`
- `AGENTS.md`
- `SECURITY.md`
- `CONTRIBUTING.md` (if present)

## Related
- [KB Index](../INDEX.md)
- [Architecture Invariants](../00_foundation/architecture_invariants.md)
- [Runtime Contracts](../10_runtime_contracts/runtime_contracts.md)
