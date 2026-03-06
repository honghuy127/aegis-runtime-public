# Bot Challenge Countermeasure Pattern

## Purpose
Canonical remediation pattern for `blocked_interstitial_captcha` flows.

## Detection Contract
Use structured probes and evidence fields instead of assuming a fixed challenge UI shape.

Required signals:
- `px_shell_present`
- `px_root_visible`
- `px_iframe_total`
- `px_iframe_visible`
- passive behavior counters (`mouse_moves`, `scroll_events`, `elapsed_ms`)

## Bounded Recovery Plan
1. Run bounded passive grace behavior first.
2. Probe challenge state after grace.
3. Attempt bounded JS trigger hook when available.
4. If still blocked, run one bounded fallback reload flow.
5. Exit with explicit reason and evidence when unresolved.

## Practical Threshold Guidance
- Passive behavior must produce non-trivial signal volume (mouse + scroll), not only dwell time.
- Hidden iframe presence must be tracked explicitly; visible-only checks are insufficient.
- Fallback grace windows should be bounded but long enough to observe delayed challenge transitions.

## Evidence Requirements
Capture at least:
- grace behavior counters
- iframe visibility progression by probe attempt
- fallback activation reason
- final clearance verdict

Artifacts:
- `storage/runs/<run_id>/artifacts/*_interstitial_grace_debug.json`
- `storage/runs/<run_id>/artifacts/context/runtime_context.json`
- `storage/runs/<run_id>/artifacts/trace/manual_intervention_events.jsonl`

## Guardrails
- No unbounded retries.
- Respect `ActionBudget` and step deadlines.
- Preserve manual-mode ownership boundaries (`off` vs `assist` vs `demo`).
- Emit explicit reason code on failure.

## Related
- [Runtime Contracts](../10_runtime_contracts/runtime_contracts.md)
- [Evidence Fields](../10_runtime_contracts/evidence.md)
- [Runtime Playbook](../20_decision_system/runtime_playbook.md)
