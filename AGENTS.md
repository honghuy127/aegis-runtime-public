# AGENTS.md

Runtime orientation for AI coding agents and contributors.

## Start Here (Canonical Docs Only)

Read these first:

1. [docs/README.md](docs/README.md)
2. [docs/kb/kb_index.yaml](docs/kb/kb_index.yaml)
3. [docs/kb/INDEX.md](docs/kb/INDEX.md)
4. [docs/kb/00_foundation/doctrine.md](docs/kb/00_foundation/doctrine.md)
5. [docs/kb/00_foundation/architecture.md](docs/kb/00_foundation/architecture.md)
6. [docs/kb/20_decision_system/runtime_playbook.md](docs/kb/20_decision_system/runtime_playbook.md)

Canonical runtime guidance lives in `docs/kb/` only.

## KB-First Planning Gate (Mandatory)

Before planning or coding, agents MUST consult the KB first.

Required workflow:
1. Run `python -m utils.agent_preflight` (or include `--reason`, `--topic`, `--path` inputs)
2. Read the mandatory entrypoints plus task-relevant KB docs returned by the preflight
3. In your plan / first coding note, cite the KB docs you used
4. Only then patch code

Examples:
- `python -m utils.agent_preflight --path core/scenario_runner.py --path core/route_binding.py`
- `python -m utils.agent_preflight --reason calendar_dialog_not_found --reason iata_mismatch`
- `python -m utils.agent_preflight --path core/scenario_runner/google_flights/ --path core/scenario/gf_helpers/date_picker_orchestrator.py --strict`

This is a planning gate, not an optional helper.

## KB Taxonomy Overview

Canonical KB root: `docs/kb/`

- `docs/kb/00_foundation/` — doctrine, architecture, system model, invariants
- `docs/kb/10_runtime_contracts/` — runtime contracts, budgets/timeouts, evidence, browser, plugins
- `docs/kb/20_decision_system/` — runtime playbook and triage runbook
- `docs/kb/30_patterns/` — implementation patterns (date picker, combobox, selectors, i18n)
- `docs/kb/40_cards/` — remediation cards and authoring rules
- `docs/kb/50_governance/` — governance rules, constitution, guardrails, ADRs
- `docs/kb/kb_index.yaml` — machine-readable topic/symptom index

Use `docs/kb/kb_index.yaml` to map reason codes, symptoms, and code hotspots to the correct KB documents before patching behavior.

## Runtime Artifact Policy

Canonical runtime artifacts must be written under `storage/runs/<run_id>/`.

- Store logs, manifests, evidence, and HTML/screenshots in the canonical run directory
- Use `storage/runs/<run_id>/artifacts/` for run artifacts
- Use `storage/runs/<run_id>/scenario_last_error.json` and `storage/runs/<run_id>/evidence_*.json` for diagnostics
- `storage/debug/` and `storage/debug_html/` are legacy compatibility locations
- Only pointer files are allowed in legacy debug directories (for example `LAST_RUN.txt`)
- Do not duplicate logs or artifacts into legacy debug directories

## Runtime State Store Policy (Seed vs Local Overlay)

For machine-generated runtime state under `storage/`, preserve committed seeds and
write run-specific updates to local overlays.

- `storage/plan_store.json` is the committed seed plan baseline (safe to review/commit intentionally)
- `storage/plan_store.local.json` is the local runtime plan-cache overlay (machine-generated, do not commit)
- `storage/plan_store.py` reads `seed <- local overlay` and writes runtime updates to the local overlay only
- When clearing local learned state, clear `storage/plan_store.local.json` (not the seed) unless you are intentionally replacing the baseline

## Core Invariants

All changes must preserve bounded, auditable, self-healing behavior.

- Use bounded retries only; never add unbounded retry loops
- Respect `ActionBudget` and existing timeout strategy usage
- Fail with explicit `reason` codes
- Preserve and populate structured `evidence` fields on failure paths
- Prefer semantic selectors: role -> aria -> data attributes -> text/class
- Avoid brittle positional selectors unless semantically anchored
- Preserve existing evidence fields when modifying error handling
- Do not add temporal language to `docs/kb/`
- Keep documentation updates canonical-only in `docs/kb/`

Architecture invariants to preserve:

- One UI driver per run (`agent` or legacy, never both)
- Extraction modules in `core/plugins/services/` must not import browser modules
- New UI interaction logic goes to `core/agent/plugins/<site>/` (agent-first)
- Legacy `core/scenario/<site>.py` remains fallback/shim only

## Where to Patch (Agent-First Rule)

Route fixes to the smallest correct module.

- UI interactions (fill/click/date picker/airport combobox): `core/agent/plugins/<site>/`
- Legacy fallback-only UI behavior: `core/scenario/<site>.py` (do not add new primary logic)
- Extraction/parsing only: `core/plugins/services/<site>.py` (no browser imports)
- Route binding/scope detection: `core/route_binding.py`, `core/scope_reconciliation.py`
- Shared heuristics/utilities: `utils/`
- Behavioral guidance/docs: update canonical docs in `docs/kb/`

Reference invariants:

- [docs/kb/00_foundation/architecture_invariants.md](docs/kb/00_foundation/architecture_invariants.md)
- [docs/kb/00_foundation/architecture_invariants.md#m-site-adapter--ui-driver-ownership](docs/kb/00_foundation/architecture_invariants.md#m-site-adapter--ui-driver-ownership)

## Orchestrator Discipline

When adding a new site or feature:

- DO NOT put logic into `scenario_runner.py`.
- Always create a submodule under `core/scenario_runner/<site>/`.
- If logic exceeds ~30 lines or includes site-specific tokens, move it.
- If touching `scenario_runner.py`, explain why it cannot live elsewhere.

### Adding a new site (e.g., Expedia)

1. Create: `core/scenario_runner/<site>/`
2. Add:
	- `ui_actions.py`
	- `selectors.py`
	- `form_state.py`
	- plan preset
3. Wire orchestrator minimally.
4. Add tests.
5. Verify no growth in `scenario_runner.py` beyond wiring.

### Anti-bloat rules

Use these rules to keep orchestration maintainable:

- Keep `core/scenario_runner.py` orchestration-focused; no site-specific selector/token logic.
- Soft cap orchestration modules at ~800 LOC; extract by concern when approaching cap.
- Do not add nested helper functions longer than ~30 lines inside runner/execute paths.
- New site work MUST live in `core/scenario_runner/<site>/` and include interstitial handling + evidence artifacts + reason taxonomy + tests.
- Keep public `run_agentic_scenario(...)` signatures stable unless test and release surfaces explicitly require a change.

## Definition of Done

Before completing a change:

- [ ] Targeted tests pass (`pytest` or narrower relevant suite)
- [ ] No unbounded loops or retry regressions introduced
- [ ] `ActionBudget` and timeout strategy behavior remains enforced
- [ ] Failure paths return explicit `reason` codes
- [ ] `StepResult.evidence` fields are preserved or improved
- [ ] Any behavior change is documented in canonical `docs/kb/` only
- [ ] KB docs use no temporal language
- [ ] Never introduce fixed calendar dates in tests except parsing/format subject tests with `# allow-fixed-date: parsing-test`; otherwise use `tests/utils/dates.py`

If you introduce a new failure mode:

- [ ] Document the `reason`/evidence expectations in [docs/kb/10_runtime_contracts/evidence.md](docs/kb/10_runtime_contracts/evidence.md)
- [ ] Update [docs/kb/20_decision_system/runtime_playbook.md](docs/kb/20_decision_system/runtime_playbook.md) and/or [docs/kb/20_decision_system/triage_runbook.md](docs/kb/20_decision_system/triage_runbook.md) when triage guidance changes

## Governance Checks (`kb_taxonomy_check` + `pytest`)

Run before finishing structural or behavioral changes:

## Refactor notes: extracted run_agentic_scenario

Recent change: the core `run_agentic_scenario` implementation was moved from `core/scenario_runner.py` into
`core/scenario_runner/run_agentic_scenario.py` to reduce module size and improve maintainability.

Key points:
- `core/scenario_runner.py` remains the stable public entrypoint and delegates to the extracted implementation module.
- Helper symbols should be consumed via direct imports from `core.scenario_runner.*` modules.
- Compatibility wrappers in `core/scenario_runner.py` should be avoided when a direct import is sufficient.
- A smoke harness and scanner tools were added under `scripts/scenario_runner/` to exercise the extracted implementation without
	a real browser or networked LLM during development: `scripts/scenario_runner/smoke_run_agentic.py`,
	`scripts/scenario_runner/scan_run_agentic_underscored.py`, and
	`scripts/scenario_runner/classify_missing_underscored.py`.

If you need to monkeypatch symbols for tests, patch the specific symbol where it is imported/used by the target module
(for example the extracted implementation module or its dependency module), rather than relying on runtime symbol copying.

```
If KB paths or links are changed, ensure taxonomy checks pass and links point to the current lowercase `docs/kb/` layout.

## Quick Commands

- `python -m utils.agent_preflight`
- `python -m utils.agent_preflight --path <file> [--path <file> ...]`
- `python -m utils.agent_preflight --reason <reason_code> [--reason <reason_code> ...]`
- `python -c "import yaml; print(yaml.safe_load(open('docs/kb/kb_index.yaml')))"`
- `find docs/kb -name '*.md'`
- `rg 'ActionBudget|reason|evidence' docs/kb -g '*.md'`
- `python -m utils.triage`
- `python -m utils.triage --reason <reason_code>`
- `python -m utils.kb_taxonomy_check`
- `pytest -q`
- `grep -rE "we (added|implemented|optimized|recently)|Phase [0-9]|Tier[- ]?[0-9]" docs/kb/ --include='*.md'`
