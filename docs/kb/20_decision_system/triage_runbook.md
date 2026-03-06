# Triage Runbook

**Machine-Readable Decision Table**: [triage_decision_table.yaml](./triage_decision_table.yaml)

This index provides quick navigation for runtime triage. For detailed decision logic, evidence keys, and fixes, consult the YAML table.

---

## Quick Start

- `python -m utils.triage`
- `python -m utils.triage --reason <reason> --show-evidence`
- `python -m utils.triage --json --lookback <hours>`
- `python -m utils.triage --top-n <n> --lookback <hours>`

---

## Inputs & Artifacts

Canonical inputs (see `inputs` in YAML):
- `storage/latest_run_id.txt`
- `storage/runs/<run_id>/scenario_last_error.json`
- `storage/runs/<run_id>/artifacts/`

---

## Output Modes

Modes and structure are documented in `output_modes` and `kb_cards` in YAML.

---

## Auto-Heal Sandbox

Debug-only bounded workflow (see `auto_heal` in YAML).

---

## Decision Tree (Reason Codes)

Reason codes grouped by failure class (see `reason_tree` in YAML):

### Date Picker Failures
- `calendar_dialog_not_found`
- `month_nav_exhausted`
- `calendar_day_not_found`
- `date_picker_unverified`

### Location (Combobox) Failures
- `iata_mismatch`
- `suggestion_not_found`

### Budget & Timeout Failures
- `budget_hit`
- `deadline_hit`
- `wall_clock_timeout`
- `selector_not_found`
- `deeplink_recovery_activation_unverified`
- `deeplink_recovery_rebind_unverified`
- `route_core_before_date_fill_unverified`

For `blocked_interstitial_captcha`, confirm run mode before remediation:
- `off`: machine-only bounded recovery
- `assist`: challenge-time manual control, machine resumes after clear
- `demo`: human-driven run, machine observer/logger

---

## Workflows & Escalation

- Agent and human workflows: see `workflows` in YAML.
- Escalation criteria: see `escalation` in YAML.

---

## KB Cards Smoke Check

Commands and validations live in `kb_cards_smoke_check` in YAML.

---

## Calendar Snapshots

Snapshot structure and config live in `calendar_snapshots` in YAML.

---

## Related Documentation

- [Evidence Fields](../10_runtime_contracts/evidence.md)
- [ActionBudget & Timeouts](../10_runtime_contracts/budgets_timeouts.md)
- [Date Picker Pattern](../30_patterns/date_picker.md)
- [Combobox Commit Pattern](../30_patterns/combobox_commit.md)
- [Selectors Pattern](../30_patterns/selectors.md)
- [Runtime Playbook](runtime_playbook.md)
- [Reason Code Registry](../../../core/scenario/reasons.py)
