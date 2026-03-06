# Repo Digest (2026-03-02)

## 1) What this repo does (1 paragraph)
Flight Price Watcher Agent is an experimental multi-service agentic runtime for structured scenario execution, hybrid fare extraction (heuristics/LLM/VLM with guarded fallbacks), and auditable run/evidence persistence. Runtime behavior is config-driven (`configs/*`) with bounded retries, timeout budgets, scope/route binding checks, and fail-closed extraction acceptance. Provider adapters (including Google Flights and Skyscanner) are implementation examples within a provider-agnostic architecture. This digest was prepared KB-first using `docs/kb/INDEX.md`, `docs/kb/00_foundation/doctrine.md`, `docs/kb/00_foundation/architecture.md`, `docs/kb/20_decision_system/runtime_playbook.md`, and `docs/kb/00_foundation/architecture_invariants.md` plus task-mapped runtime contracts.

## 2) Current high-level architecture
- `core/scenario_runner/*`: Scenario orchestration + repair flow. Public entrypoint remains `core/scenario_runner.py::run_agentic_scenario` (wrapper), while core implementation sits in `core/scenario_runner/run_agentic_scenario.py`; site logic is split under `core/scenario_runner/google_flights/*` and `core/scenario_runner/skyscanner/*`.
- `core/service_runners/*`: Legacy/deprecated runner layer (docs mark migration in progress). Still present and sizable (`google_flights.py` is large), with imports into `core.scenario_runner.*` internals.
- `core/plugins/*`: Extraction plugin strategy/router and service adapters; canonical boundary says service extraction modules must stay extraction-only.
- `core/browser/*`: Browser/session/action modules (click/fill/wait/interaction resilience) used by runner paths.
- `llm/*`: Model client/router/prompt/validation helpers for LLM/VLM-assisted plan/extraction paths.
- `storage/*`: Run artifacts and persistent state. Canonical run outputs under `storage/runs/<run_id>/` with `artifacts/` subdir; legacy `storage/debug*` is compatibility-oriented.
- `docs/kb/*`: Canonical architecture/contracts/triage governance; agent planning gate requires KB-first retrieval.
- Ownership boundaries (MUSTs from KB): one UI driver per run (agent OR legacy), new UI logic belongs in `core/agent/plugins/<site>/` / `core/scenario_runner/<site>/` (not monolithic runner), `core/plugins/services/*` must not import browser modules, and `scenario_runner.py` should avoid `core/service_runners/*` dependencies.

## 3) Public entrypoints and how to run
- CLI entrypoint: `main.py::main()` (verified by `if __name__ == "__main__": main()`).
- Main orchestrator called by CLI: `main.py::run_multi_service(args)`.
- Scenario orchestrator callable used by main flow: `core/scenario_runner.py::run_agentic_scenario(...)` (stable wrapper delegating to extracted implementation module).
- Runtime export scan caveat: `scripts/list_runtime_exports.py` currently reports zero runtime names for both requested paths (tool appears tied to internal runtime patch symbol detection, not generic Python exports).
- Minimal run commands:
  - `python main.py --origin HND --dest ITM --depart 2026-03-01 --return-date 2026-03-08 --services google_flights`
  - `python main.py --services-config configs/services.yaml`
  - `python main.py --light-mode` or `python main.py --full-mode`
- Config ownership:
  - `configs/run.yaml`: default trip/runtime values.
  - `configs/services.yaml`: enabled services + URL hints.
  - `configs/models.yaml`: planner/coder/vision model names.
  - `configs/thresholds.yaml`: feature flags, budgets, timeout knobs.
  - `configs/alerts.yaml`: alert policy/channels.
  - `configs/knowledge_rules.yaml`: token/rule mappings.
  - `configs/service_ui_profiles.json`: service UI selectors/labels.

## 4) Developer workflows
- Fast tests (recommended subset):
  - `pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py`
  - `pytest -q tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py`
  - `pytest -q tests/test_scenario_runner_timeouts.py`
- Full test command:
  - `python -m pytest`
- Refactor workflow:
  - `python scripts/extract_headers.py core/scenario_runner.py core/scenario_runner/run_agentic_scenario.py`
  - `python scripts/extract_import_graph.py core/scenario_runner.py`
  - `python scripts/list_nested_functions.py core/scenario_runner/run_agentic_scenario.py --max-lines 30`
  - `bash scripts/refactor_gate.sh --file <path.py> --entrypoints <fn...> --tests "pytest -q ..."`

## 5) Hotspots and risks (based on command outputs)
- Largest / most complex modules (by LOC from `wc -l`):
  - `core/scenario_runner/run_agentic_scenario.py` (3940)
  - `core/service_runners/google_flights.py` (3394)
  - `main.py` (2291)
  - `core/scenario_runner.py` (2143)
- Circular import risk areas:
  - `core/scenario_runner/run_agentic_scenario.py` imports `core.scenario_runner` while `core/scenario_runner.py` lazy-loads extracted implementation by file-path to avoid wrapper overwrite; this is an explicit anti-circular workaround and remains a fragile coupling point.
  - Legacy `core/service_runners/google_flights.py` imports internals from `core.scenario_runner.*`, increasing cross-layer coupling risk during refactors.
- Nested-function hotspots:
  - `run_agentic_scenario._scenario_return` (38 lines, 19 free vars) is the top nested complexity cluster.
  - `run_agentic_scenario._apply_vision_page_kind_hints` and `_try_google_recovery_collab_followup` are smaller but still closure-heavy.
  - `core/scenario_runner.py` currently has only 3 small nested functions under `execute_plan` (lower immediate risk).
- Underscored import boundary risk:
  - Heavy underscore-prefixed symbol imports across modules are visible (`service_runner_bridge`, `route_bind`, helpers), including from legacy `core/service_runners/*`; this increases breakage risk when private helper signatures shift.

## 6) Recent refactor signals (inferred)
- Newly modularized signals:
  - Extracted implementation path `core/scenario_runner/run_agentic_scenario.py` with wrapper retained in `core/scenario_runner.py`.
  - Growth of helper submodules under `core/scenario_runner/*` (`run_agentic/*`, `google_flights/*`, selector/timeouts/artifact helpers).
  - Presence of dedicated refactor tooling scripts (`extract_import_graph`, `list_nested_functions`, `refactor_gate`).
- What remains monolithic:
  - `core/scenario_runner/run_agentic_scenario.py` is still very large and operationally central.
  - `core/service_runners/google_flights.py` remains large despite migration direction toward site modules/adapters.
  - `main.py` remains a large all-in-one CLI/runtime coordinator.
- Bloat pressure likely to return:
  - Additional Google Flights or Skyscanner recovery variants may continue to accumulate in runner helper graphs unless private helper APIs are stabilized and legacy service_runner dependencies are reduced.

## 7) Latest updates
- `run_agentic_scenario._scenario_return` closure assembly was moved behind a pure callable builder in `core/scenario_runner/run_agentic_scenario.py`.
- Legacy Google Flights runner coupling was reduced through bridge-first rewiring; direct private underscore imports from `core.scenario_runner.*` into `core/service_runners/google_flights.py` are now guarded by tests.
- Public bridge helpers were standardized in `core/scenario_runner/google_flights/service_runner_bridge.py` and consumed by legacy runner paths.
- Orchestrator-boundary tests now enforce that `core/scenario_runner.py` stays wrapper/orchestration-focused and does not import `core.service_runners.*` or accumulate site-prefixed top-level helpers.
- `scripts/refactor_gate.sh` now rejects no-op test commands and requires at least one pytest command.
- `scripts/scenario_runner/list_runtime_exports.py` now exposes explicit runtime-audit semantics in CLI help, including that `runtime_names_count=0` can be expected.
- Legacy retirement milestones and exit criteria were documented in:
  - `docs/kb/50_governance/adr/0002-incremental-plugin-migration.md`
  - `docs/kb/00_foundation/architecture.md`

Remaining follow-up focus:
- Continue reducing remaining `core/service_runners/* -> core/scenario_runner/*` edges.
- Continue KB drift backlog reduction tracked in `docs/journal/deep_audit_20260302.md`.
