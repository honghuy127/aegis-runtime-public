# Foundation Guardrails (Stage 0)

Scope: minimal non-behavioral guardrails to prevent documentation and architecture drift.

## Canonical Documents

The canonical runtime knowledge base lives under `docs/kb/`.

Required canonical entrypoints:
- `docs/kb/INDEX.md`
- `docs/kb/kb_index.yaml`
- `docs/kb/00_foundation/doctrine.md`
- `docs/kb/00_foundation/architecture.md`
- `docs/kb/00_foundation/architecture_invariants.md`
- `docs/kb/20_decision_system/runtime_playbook.md`
- `docs/kb/20_decision_system/triage_runbook.md`

Policy:
- Keep canonical behavior guidance in `docs/kb/` only.
- Keep root markdown limited to high-level project docs.
- Prefer consolidation over duplicated narrative summaries.

## Behavior Change Boundaries

Blocked without explicit scope and test coverage:
- Changing retry ceilings or introducing unbounded loops
- Bypassing `ActionBudget` / deadline enforcement
- Changing failure reason semantics without taxonomy alignment
- Moving runtime artifacts outside `storage/runs/<run_id>/`

Allowed in guardrail maintenance:
- Documentation cleanup and link fixes
- Taxonomy/docs consistency updates
- Test-only hardening for existing behavior

## Invariants To Preserve

- One UI driver per run (`agent` or legacy, never both).
- Extraction modules under `core/plugins/services/` do not import browser modules.
- New UI interaction logic goes to `core/agent/plugins/<site>/`.
- Legacy `core/scenario/<site>.py` remains fallback/shim.
- Failure paths carry explicit `reason` and structured `evidence`.

Reference: `docs/kb/00_foundation/architecture_invariants.md`.

## Required Checks

Run before finishing a change:

```bash
pytest -q
python -m compileall .
python -m utils.kb_taxonomy_check
python -m utils.agent_preflight
```

## Related

- `docs/kb/50_governance/kb_constitution.md`
- `docs/kb/50_governance/kb_guardrails.md`
- `docs/kb/50_governance/tests_hygiene.md`
