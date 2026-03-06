# Evidence Fields Reference

**Machine-Readable Catalog**: [evidence_catalog.yaml](./evidence_catalog.yaml)

This index provides quick navigation for StepResult evidence fields and extraction diagnostics. For full field definitions, required/optional keys, and examples, consult the YAML catalog.

---

## StepResult Evidence Structure

Core fields (see `step_result_schema` in the YAML):
- `ok`, `reason`, `evidence`, `selector_used`, `action_budget_used`

---

## Evidence Namespaces

Field namespaces (see `namespaces` in the YAML):
- `calendar.*`, `ui.*`, `verify.*`, `time.*`, `budget.*`, `suggest.*`, `dom.*`, `input.*`
- `combobox.*`, `scope.*`, `vlm.*`, `multimodal.*`, `search_commit.*`

---

## Reason → Evidence Map

Reason codes and required/optional fields (see `reason_evidence_map` in the YAML):
- `calendar_dialog_not_found` (legacy alias: `calendar_not_open`)
- `month_nav_exhausted`
- `calendar_day_not_found`
- `date_picker_unverified`
- `budget_hit`
- `deadline_hit`
- `wall_clock_timeout`
- `suggestion_not_found`
- `iata_mismatch`
- `selector_not_found`
- Scenario-level terminal reason: `skyscanner_blank_shell_persistent_after_post_clear_refill`
  Expect attempt/turn context in `trace/phase_probe_*` artifacts plus latest
  Skyscanner `html` + `screenshot` snapshots showing persistent white-shell.

---

## Success Patterns

Success templates (see `success_evidence_map` in the YAML):
- `date_picker_success`
- `combobox_commit_success`
- `route_ready_fast_path` (post-interstitial route-bound fast-clear acceptance)
- `skyscanner_results_snapshot_after_manual_target_closed` (bounded route-results snapshot salvage when manual target closes)

---

## Extraction Result Schema (Non-StepResult)

Extraction schema fields (see `extraction_result_schema` in the YAML):
- Required: `price`, `currency`, `confidence`, `source`, `reason`
- Optional: `scope`, `route_bound`, `scenario_ready`, `scope_class`, `status`, `strategy`, `normalized`, `raw_output`

---

## Artifacts & Debug Probes

Canonical artifacts (see `artifacts` in the YAML):
- `storage/runs/<run_id>/scenario_last_error.json`
- `storage/runs/<run_id>/route_state_<service>.json`
- `storage/runs/<run_id>/artifacts/google_route_fill_*_selector_probe.json`
- `storage/runs/<run_id>/artifacts/google_date_fill_*_selector_probe.json`
- `storage/runs/<run_id>/artifacts/google_search_commit_*_probe.json`

Human-intervention diagnostics are expected in runtime context and interstitial snapshots:
- `storage/runs/<run_id>/artifacts/context/runtime_context.json` (`human_intervention_mode`, manual timeout, headed/headless)
- `storage/runs/<run_id>/artifacts/*_interstitial_grace_debug.json` (`manual_intervention.*`, grace/fallback probes)
- `storage/runs/<run_id>/artifacts/trace/manual_intervention_events.jsonl`:
  `manual_automation_action_count`, `manual_automation_action_counts` for machine-action detection during manual windows.
- `storage/runs/<run_id>/artifacts/*_interstitial_grace_debug.json` and
  `storage/runs/<run_id>/artifacts/trace/attempt_error_diag_attempt_<n>.json`:
  press/hold variability evidence (`press_hold_probes.*.hold_budget_ms`, `press_hold_probes.*.hold_target_ms`,
  `press_hold_probes.*.long_hold_surface`, `press_hold_probes.*.challenge_url_hint`,
  probe pause and cooldown fields) for anti-bot human-likeness diagnostics.
- `storage/runs/<run_id>/artifacts/trace/manual_intervention_events.jsonl`:
  `capture_restart_count` for probe re-arm diagnostics after page hops during manual windows.
- `storage/runs/<run_id>/artifacts/trace/manual_intervention_events.jsonl` and
  `storage/runs/<run_id>/artifacts/dom_probe/manual_*.json`:
  captcha token/iframe churn fields (`captcha_token_*`, `px_iframe_*`) for challenge reissue diagnostics.
- `storage/runs/<run_id>/artifacts/context/manual_terminal_snapshot.json` and
  `storage/runs/<run_id>/artifacts/context/runtime_browser_initial.json`:
  `aux_page_guard.*` counters/samples for unexpected popup/new-tab closure decisions.
- `storage/runs/<run_id>/artifacts/context/manual_terminal_snapshot.json` and
  `storage/runs/<run_id>/artifacts/context/runtime_browser_initial.json`:
  `popup_guard.*` counters/samples (`blocked`, `allowed`, `samples`) for in-page popup interception
  (`window.open`, `a[target=_blank]`, `form[target=_blank]`).
- `storage/runs/<run_id>/artifacts/context/manual_terminal_snapshot.json` and
  `storage/runs/<run_id>/artifacts/dom_probe/manual_*.json`:
  `browser_lifecycle.*` event stream for browser/context/page lifecycle triage
  (`session_enter_start`, `browser_launched`, `context_created`, `page_mainframe_navigate`,
  `page_close`, `session_exit_snapshot`, `session_exit_done`, `storage_state_skip`).
- `storage/runs/<run_id>/artifacts/trace/attempt_error_diag_attempt_<n>.json`:
  attempt-error page-state diagnostics (`page_url`, `page_title`, `html_len`, `visible_text_len`,
  `shell_incomplete`, shadow-challenge probe, and compact runtime selector/network probe).
- `storage/runs/<run_id>/run.log`:
  machine retry outcome lines (`captcha.grace.result`, `captcha.fallback_reload.attempt`) to confirm
  whether machine press/hold executed before manual escalation.

---

## Evidence Guidelines

See `guidelines` in the YAML for include/exclude rules and naming conventions.

---

## Related Documentation

- [Design Doctrine: Observability](../00_foundation/doctrine.md#observability-doctrine)
- [ActionBudget & Timeouts](budgets_timeouts.md)
- [Runtime Playbook](../20_decision_system/runtime_playbook.md)
- [Date Picker Pattern](../30_patterns/date_picker.md)
- [Combobox Commit Pattern](../30_patterns/combobox_commit.md)
