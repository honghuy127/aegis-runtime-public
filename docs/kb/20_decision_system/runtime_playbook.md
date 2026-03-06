# Runtime Playbook

**Machine-Readable Symptom Map**: [runtime_symptom_map.yaml](./runtime_symptom_map.yaml)

This index provides quick navigation for runtime troubleshooting by symptom, log reason, and evidence fields. For detailed diagnostics and actions, consult the YAML map.

---

## How to Use

1. Find the symptom
2. Check log patterns and reason codes
3. Inspect evidence fields
4. Correlate with budgets/timeouts
5. Apply bounded fix

(See `usage_steps` in the YAML.)

---

## Symptom Index

Symptoms and log patterns (see `symptoms` in the YAML):
- `no_html_returned`
- `blocked_interstitial_captcha`
- `date_picker_failed`
- `combobox_commit_failed`
- `wall_clock_timeout` (legacy alias: `timeout_error`)
- `extraction_failed`
- `extraction_skipped_scenario_guard`
- `scope_conflict`
- `vlm_not_called`
- `vlm_invalid_response`
- `recovery_loop_detected`
- `selector_spam`
- `knowledge_store_drift`
- `debug_run_folders`

### `blocked_interstitial_captcha` mode handling

- `assist` mode:
  - Human controls challenge solve windows.
  - Keep machine from issuing follow-up reload/press actions while manual control is active.
  - If a turn starts on a verification URL, short-circuit turn execution and return control to bounded attempt-gate challenge handling.
  - Keep strict single-page control active during manual windows; close unexpected popup/new-tab pages and record guard evidence.
  - Verify clear state with artifacts (`html`, `screenshot`, selector probe) before resuming scenario steps.
- `demo` mode:
  - Human controls the full scenario end-to-end.
  - Machine stays observer/logger and records diagnostics for post-run remediation work.
  - Keep strict single-page control active with periodic guard sweeps so popup windows are closed even if a page-open callback is missed.
- `off` mode:
  - Use bounded automated recovery only; no normal manual intervention windows.
  - For Skyscanner blank-shell on `/transport/flights/...`, run bounded settle/reload and hard-nav recovery first, then bounded hydration polling before any home rebind or manual fallback.
  - If blank-shell persists with hidden PX runtime markers, run one bounded challenge-aware recovery window before home rebind/manual fallback.
  - If blank-shell persists after a post-clear full-refill in the same attempt, suppress additional refill follow-up loops and return an explicit unready reason (`skyscanner_blank_shell_persistent_after_post_clear_refill`) or bounded retry.
  - If challenge clearance lands on a route-bound page, consume one bounded post-clear handoff by probing in-place readiness first; rebind to `/flights` only when the in-place probe is not ready.
  - If captcha payload decoding resolves to bare root (`/`), keep fallback reload target bound to the original scenario URL instead of homepage root.
  - If flow drifts to Skyscanner Hotels during a flights scenario, perform bounded Flights-tab/context recovery before continuing any fill or wait step.
  - If a turn/step starts on a verification URL, soft-skip step execution and hand control back to bounded interstitial handling.
  - If the first clearance probe is route-bound and challenge-visible selectors are absent, accept `route_ready_fast_path` and skip additional cooldown re-probes.
  - Keep interstitial fallback reload header handling transport-neutral (no persistent static client-hint overrides) to prevent repeated post-challenge shadow-shell states.
  - For machine press-and-hold retries, keep hold duration and probe pauses bounded but randomized to reduce deterministic interaction fingerprints.
  - For PX-style challenge pages, allow bounded hold targeting on visible `#px-captcha` shell when the inner challenge iframe is temporarily hidden/unavailable.
  - For long-hold machine retries, include a short bounded cursor-approach path before `mouse.down` so pointer behavior is not a single direct center jump.

---

## Debug Run Artifacts

Canonical debug run layout and artifact paths live in the YAML (`debug_run_folders`).

---

## Related Documentation

- [Evidence Fields Reference](../10_runtime_contracts/evidence.md)
- [ActionBudget & Timeouts](../10_runtime_contracts/budgets_timeouts.md)
- [Date Picker Pattern](../30_patterns/date_picker.md)
- [Combobox Commit Pattern](../30_patterns/combobox_commit.md)
- [Selector Strategies](../30_patterns/selectors.md)
- [Triage Runbook](triage_runbook.md)
