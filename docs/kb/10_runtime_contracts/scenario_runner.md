# Scenario Runner Contract

**Scope**: Scenario orchestration, step sequencing, budget tracking, recovery loops  
**Defines**: Main scenario loop, failure modes, recovery gating  
**Does NOT define**: Individual step implementations, site-specific logic

---

## Main Scenario Loop

**Public Entry Module**: `core/scenario_runner.py`
**Implementation Module**: `core/scenario_runner/run_agentic_scenario.py`

**Entry**:
`run_agentic_scenario(url, origin, dest, depart, return_date=None, trip_type="one_way", is_domestic=None, max_transit=None, human_mimic=False, disable_http2=False, knowledge_user=None, mimic_locale=None, mimic_timezone=None, mimic_currency=None, mimic_region=None, mimic_latitude=None, mimic_longitude=None, site_key="google_flights", browser_engine="chromium")`

The public entry in `core/scenario_runner.py` delegates to the extracted
implementation in `core/scenario_runner/run_agentic_scenario.py`. Helper
dependencies are imported explicitly from `core.scenario_runner.*` modules.

**Flow**:
```
1. Load plan, bind route/dates
2. For each step:
   a. Check deadline (fail if elapsed >= deadline)
   b. Check ActionBudget (fail if exhausted)
   c. Execute step action
   d. Collect StepResult
   e. Check step failure (decision gate)
3. Capture HTML/screenshot artifacts
4. Return (ok: bool, results: StepResult[], artifacts: dict)
```

Skyscanner route/date contract:
- Origin and destination must be treated as combobox commit fields (fill + listbox selection), not text-only fills.
- Date fields must use calendar controls; a local date-fill failure (`calendar_dialog_not_found`, legacy alias `calendar_not_open`, `month_nav_exhausted`, related date-picker failures) is actionable.
- After a local Skyscanner date-fill failure in a turn, downstream search submit and result-wait steps are soft-skipped for that turn (fail-closed against wrong default-date searches).
- On `/transport/flights/...`, submit-like search controls are soft-skipped while result-surface waits remain actionable.
- On route-bound results URLs, a bounded results-overlay dismiss probe should run before actionable steps.
- Ready-state return on Skyscanner must include a bounded post-ready confirmation window to avoid immediate success-close on transient shell states.
- When Skyscanner remains shell-incomplete after settle/reload, recovery should escalate in bounded order:
  hard re-open of the same `/transport/flights/...` URL, then one bounded shadow-challenge probe/recovery when PX runtime markers persist, then optional one-shot assisted manual recovery.
- After interstitial clearance on Skyscanner, if the active page is already route-bound (`/transport/flights/...`), run one bounded in-place readiness probe first; only perform home rebind (`/flights`) + full refill when that probe is not ready.
- Interstitial clearance validation on Skyscanner should short-circuit on the first safe route-bound probe (`route_ready_fast_path`) when challenge selectors are not visible and runtime challenge blockers are absent.
- If manual last-resort closes the active page while a route-bound results snapshot is already captured, attempt-gate may return via bounded snapshot salvage (`skyscanner_results_snapshot_after_manual_target_closed`) instead of terminal blocked classification.
- Interstitial fallback reload logic must not persist static page-level client-hint/header overrides across subsequent steps; header mutations should be transport-neutral to avoid post-challenge shell loops.
- Interstitial fallback target decoding must fail-closed to the caller route URL when challenge payload resolves to bare homepage root (`/`) so route context is not lost after clearance.
- If execution context drifts to Skyscanner Hotels (`/hotels/...`) during a flights scenario, bounded recovery back to Flights tab/context must run before flight fill/wait actions.
- During `execute_plan` on Skyscanner, if current URL is a verification/captcha surface, step actions are soft-skipped and control returns to attempt-gate challenge handling.

---

## StepResult Contract

Every step returns:
```python
StepResult(
    ok=True/False,
    reason="success" | "calendar_dialog_not_found" | "budget_hit" | ...,
    evidence={...},
    selector_used=str | None,
    action_budget_used=int
)
```

**Evidence keys**: [evidence_catalog.yaml](evidence_catalog.yaml)

---

## ActionBudget Tracking

Each step receives ActionBudget instance:
```python
def gf_set_date(browser, role, date, budget, ...):
    if not budget.consume(1):
        return StepResult.failure("budget_hit", evidence={...})
    # action...
```

Budgets are step-scoped; each step gets fresh budget.

Default: 20 actions per step (configurable)

---

## Deadline Enforcement

**Set at start**: Unix millisecond epoch

**Checked before each action**: `scenario_runner.check_deadline() → True | False`

**On deadline exceed**:
- Fail current step with `wall_clock_timeout` (legacy alias: `timeout_error`)
- Hard stop; no recovery attempt
- Return artifacts + results so far

---

## Recovery Gating: Max Attempts

**Bounds**:
- Max 2 attempts per scenario
- Max 2 turns per attempt
- Max 1 collaborative recovery (VLM + planner)

**Trigger conditions**:
- Failure on date-related step
- Scope conflict detected
- Route verification failed

**Decision gates**:
1. Check attempt count (fail if >= 2)
2. Check budget remaining (fail if exhausted)
3. Check deadline (fail if exceeded)
4. Check persistent failure (circuit-open if same reason 2+ times)
5. THEN dispatch recovery plan

---

## Recovery Dispatch

**Sites with active recovery paths**: Google Flights, Skyscanner

**Recovery types**:
- Date picker retry (re-attempt with alternate selectors)
- Route rebinding (re-navigate, re-fill origin/dest)
- Collaborative recovery (VLM page-kind probe + planner)

Fail-closed guardrails:
- For Skyscanner date controls, generic fill recovery fan-out is blocked after bounded picker flow fails.
- Search-click follow-up is gated by local date-failure signals to prevent false `ready` outcomes from stale/default route dates.
- Skyscanner route-bound script-shell pages (`/transport/flights/...` with low visible content) should trigger bounded settle/reload recovery before terminal unready verdict.
- Blank-shell recovery escalation must remain bounded per run:
  settle/reload -> hard-nav -> bounded hydration poll -> conditional home rebind (only when follow-up turn budget exists) -> max-limited manual assist fallback.
- After a post-interstitial full-refill has already executed in the same attempt, persistent Skyscanner blank-shell must suppress additional home-rebind/full-refill follow-up loops in that turn; escalate via bounded retry/manual path or return explicit unready reason.
- Turn-start gating should short-circuit when the active page is already a verification/challenge URL so attempt-gate handles interstitials before turn-plan execution.
- Turn-start gating should also repair known non-flight drift (for example Skyscanner Hotels tab) before running the turn plan.
- Attempt-gate may emit a post-clear handoff signal for Skyscanner; the next turn must consume it once with bounded behavior: probe current route-bound results in place, then fallback to `/flights` rebind only when needed.

**Dispatch**: `site_recovery_dispatch.py` finds site-specific recovery builder

---

## Challenge Provider Contract

Attempt-start interstitial handling uses a typed provider contract to keep orchestration generic and site adapters isolated.

- Contract module: `core/scenario_runner/run_agentic/challenge_provider.py`
- Provider wiring: `core/scenario_runner/run_agentic/attempt_gate.py::_build_challenge_provider(...)`
- Site adapter location example: `core/scenario_runner/skyscanner/challenge_adapter.py`

Contract fields:

- `name`
- `detect_block(html_text, browser)`
- `attempt_grace(browser, hard_block, ...)`
- `attempt_fallback(browser, url, grace_result, ...)`
- `validate_clearance(browser, html_text, ...)`
- `attempt_last_resort_manual(browser, grace_probe, fallback_result, ...)`
- `supports_last_resort_manual`
- `requires_page_open_for_clearance`

Invariants:

- `attempt_gate` remains orchestration-only and does not embed site tokens/selectors.
- Site-specific challenge behavior is implemented in site adapters under `core/scenario_runner/<site>/`.
- Provider clearance validation must be bounded and evidence-preserving.

---

## Human Intervention Modes

Runtime intervention control is mode-driven:

- `off`: machine-only execution; manual windows are disabled except explicit last-resort force path.
- `assist`: machine handles normal flow, but hands full control to human during verification challenges until challenge clears.
- `demo`: human controls the run from start to end while machine records artifacts and diagnostics only.

Runtime env contract:

- `FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE=off|assist|demo`
- `FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION` remains supported for compatibility and maps to `assist` when enabled and mode is unset.

Behavioral contract:

- In `assist` mode on interstitial/captcha pages, control is manual-first and machine follow-up must not interfere during manual windows.
- In `demo` mode, scenario execution is observer-only: no automated turn plan execution, bounded manual window, artifact capture retained.
- In `demo` mode, if the browser target closes after high-signal human activity on a non-verification URL, the terminal manual reason is `manual_observation_complete_target_closed` and scenario reason is `demo_mode_manual_observation_complete_target_closed`.
- Manual windows should re-arm UI capture probes after page/context navigation so `ui_action_capture` stays cumulative across challenge hops and redirects.
- Browser sessions should enforce a strict single-page policy during runs: unexpected popup/new-tab pages are closed and logged by default, while only explicitly expected pages (for bounded recovery flows) remain allowed.
- Single-page enforcement should include both event-based page-open guards and bounded periodic page sweeps so closure still happens when popup events are missed.
- Browser teardown should emit bounded lifecycle diagnostics and avoid state-persist side effects when the primary page is already closed (for example, skip storage-state write and record `storage_state_skip`).
- Verification-surface gating in manual workflows should use shared policy helpers (for example `core/browser/manual_intervention_policy.py`) instead of embedding provider-specific URL checks in orchestration paths.

---

## Artifact Capture

**On scenario complete** (success or failure):
- `storage/runs/<run_id>/scenario_last_error.json` — Scenario-level failure snapshot (when failed)
- `storage/runs/<run_id>/artifacts/html_<attempt>.html` — Full DOM snapshot
- `storage/runs/<run_id>/artifacts/screenshot_<attempt>.png` — Browser viewport screenshot
- `storage/runs/<run_id>/artifacts/evidence_<service>_state.json` — Per-step evidence summary

**Storage**: `storage/runs/<run_id>/`

Legacy compatibility locations (`storage/debug/`, `storage/debug_html/`) are pointer-only and must not be used as primary artifact sinks.

---

## Anti-bloat rules

These rules are enforceable guardrails for scenario-runner maintainability:

1. `core/scenario_runner.py` is orchestration-only; site-specific logic MUST live in `core/scenario_runner/<site>/`.
2. Soft cap: keep orchestration modules under ~800 LOC; if growth exceeds this, split by concern (`run_agentic/*`, `selectors/*`, `vlm/*`, `<site>/*`).
3. Do not add nested helper functions > ~30 lines inside runner/execute functions; extract to dedicated modules.
4. New site integration MUST include all of:
   - site module under `core/scenario_runner/<site>/`
   - interstitial handling path
   - evidence artifacts under `storage/runs/<run_id>/artifacts/`
   - explicit failure reason taxonomy
   - fixtures + focused tests for route/date fill and recovery
5. Public entrypoint signatures (`core/scenario_runner.py::run_agentic_scenario` and `core/scenario_runner/run_agentic_scenario.py::run_agentic_scenario`) MUST remain stable unless tests and release notes explicitly cover the change.

---

## Error Propagation

Failures return structured StepResult, never exceptions:
- Browser errors → StepResult.failure("selector_not_found", ...)
- Timeout → StepResult.failure("wall_clock_timeout", ...) (legacy alias: "timeout_error")
- Budget exhausted → StepResult.failure("budget_hit", ...)

All StepResults include evidence + selector_used + action_budget_used for observability.

---

## Related

- [Runtime Contracts](runtime_contracts.md)
- [Budgets & Timeouts](budgets_timeouts.md)
- [Evidence Catalog](evidence_catalog.yaml)
- [Runtime Playbook](../20_decision_system/runtime_playbook.md)
