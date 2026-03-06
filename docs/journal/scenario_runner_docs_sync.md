# Scenario Runner Docs Sync Journal

## Current Status Summary

Codebase status observed on 2026-03-02:
- `core/scenario_runner.py` exposes the stable public `run_agentic_scenario` entrypoint and delegates to `core/scenario_runner/run_agentic_scenario.py`; it still contains compatibility/orchestration surfaces and requires anti-bloat guardrails.
- Scenario runner logic is split across `core/scenario_runner/*`, including site subpackages (`google_flights/`, `skyscanner/`) and `run_agentic/` support modules.
- Browser runtime is modularized under `core/browser/` (`session.py`, `click.py`, `fill.py`, `wait.py`, `verification_challenges.py`, etc.).
- New refactor/audit scripts exist under `scripts/` and `scripts/scenario_runner/`.
- Several docs still reference legacy debug artifact locations as primary (`storage/debug*`) and need canonical artifact policy alignment.

## Scope of Work

- Synchronize documentation with current refactored module layout and boundaries.
- Add concrete anti-bloat rules to prevent orchestrator regression.
- Normalize naming and stale path references where mismatched.
- Record ground-truth script outputs and gate with targeted tests.

## Docs Checklist

- [x] `README.md`
- [x] `docs/README.md`
- [x] `docs/DEVELOPMENT.md`
- [x] `docs/CONFIG.md`
- [x] `docs/kb/10_runtime_contracts/scenario_runner.md`
- [x] `docs/kb/00_foundation/architecture.md`
- [x] `docs/kb/00_foundation/architecture_invariants.md` reviewed (no path correction needed)
- [x] `AGENTS.md`

## Risks and Assumptions

- Assumption: public callable signatures remain unchanged; only docs and non-behavioral consistency updates are in scope.
- Risk: docs may intentionally mention legacy paths for compatibility; updates must preserve compatibility notes while making canonical policy explicit.
- Risk: targeted tests may be environment-sensitive; command outcomes will be recorded exactly.

## Phase Test Subset

Phase A (docs + scripts snapshot):
- No tests required before artifact capture.

Phase B (docs sync + anti-bloat updates):
- `bash scripts/refactor_gate.sh --file core/scenario_runner.py --entrypoints run_agentic_scenario --tests "pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py"`

Phase C (regression gate):
- `pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py`
- `pytest -q tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py`
- `pytest -q tests/test_scenario_runner_timeouts.py`

## Ground Truth Artifacts

Planned artifact directory:
- `storage/debug/scenario_runner_docs_sync_20260302/`

Generated snapshot files:
- `storage/debug/scenario_runner_docs_sync_20260302/import_graph_scenario_runner.txt`
- `storage/debug/scenario_runner_docs_sync_20260302/import_graph_run_agentic_scenario.txt`
- `storage/debug/scenario_runner_docs_sync_20260302/import_graph_browser_session.txt`
- `storage/debug/scenario_runner_docs_sync_20260302/headers.txt`
- `storage/debug/scenario_runner_docs_sync_20260302/runtime_exports_scenario_runner.txt`
- `storage/debug/scenario_runner_docs_sync_20260302/runtime_exports_run_agentic_scenario.txt`

Commands executed:
- `python scripts/extract_import_graph.py core/scenario_runner.py`
- `python scripts/extract_import_graph.py core/scenario_runner/run_agentic_scenario.py`
- `python scripts/extract_import_graph.py core/browser/session.py`
- `python scripts/extract_headers.py core/scenario_runner.py core/scenario_runner/run_agentic_scenario.py`
- `python scripts/list_runtime_exports.py core/scenario_runner/run_agentic_scenario.py`
- `python scripts/list_runtime_exports.py core/scenario_runner.py`

## Change Summary

- Updated docs to reflect modular runtime ownership:
  - scenario runner split (`core/scenario_runner.py` public entry, extracted implementation in `core/scenario_runner/run_agentic_scenario.py`)
  - per-site scenario modules (`core/scenario_runner/google_flights/*`, `core/scenario_runner/skyscanner/*`)
  - modular browser runtime (`core/browser/session.py`, `click.py`, `fill.py`, `wait.py`, `verification_challenges.py`)
- Added refactor tool coverage in docs:
  - `scripts/extract_import_graph.py`
  - `scripts/refactor_gate.sh`
  - `scripts/init_refactor_journal.py`
  - `scripts/list_nested_functions.py`
  - plus `extract_headers.py` / `list_runtime_exports.py` usage examples
- Corrected artifact-path guidance to canonical run directories:
  - `storage/runs/<run_id>/scenario_last_error.json`
  - `storage/runs/<run_id>/artifacts/*`
  - retained `storage/debug*` as legacy pointer-only compatibility paths
- Added concrete anti-bloat rules in:
  - `docs/kb/10_runtime_contracts/scenario_runner.md`
  - `AGENTS.md`

## Gate And Test Results

Commands run:
- `bash scripts/refactor_gate.sh --file core/scenario_runner.py --entrypoints run_agentic_scenario --tests "pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py"`
- `pytest -q tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py`
- `pytest -q tests/test_scenario_runner_timeouts.py`
- `python -m utils.kb_taxonomy_check`

Results:
- Refactor gate: PASS
- `tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py`: `9 passed`
- `tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py`: `22 passed, 6 skipped`
- `tests/test_scenario_runner_timeouts.py`: `73 passed`
- `utils.kb_taxonomy_check`: warnings only (pre-existing temporal-language warnings in `docs/kb/40_cards/*` and `docs/kb/50_governance/*`)

## Follow-ups

1. `core/scenario_runner.py` remains large (~2k LOC). Enforce anti-bloat caps in code (not docs-only) by continuing extraction of `execute_plan` and helper clusters.
2. Decide whether generated `.refactor_gate/*` artifacts should be committed or ignored to reduce review noise.
3. Resolve pre-existing KB temporal-language warnings reported by `utils.kb_taxonomy_check` in card/governance docs.
