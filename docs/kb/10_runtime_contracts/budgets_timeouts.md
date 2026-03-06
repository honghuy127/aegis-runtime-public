# ActionBudget & Timeouts Contract

**Scope**: ActionBudget tracking, timeout strategy, deadline enforcement
**Defines**: Budget API, timeout formula, per-site overrides
**Does NOT define**: Wait durations, user-facing timeout messages, deadline calculation

---

## ActionBudget: Quick API

**Module**: `core/scenario/types.py`

```python
budget = ActionBudget(max_actions=20)

if budget.consume(1):  # True if available
    action()
else:
    return StepResult.failure("budget_hit", evidence={"action_count": 20})
```

**Methods**: `consume(count) → bool`, `is_exhausted() → bool`, `reset()`

**Evidence req'd**: `stage`, `action_count`, `role` (from [evidence_catalog.yaml](evidence_catalog.yaml))

---

## Typical Budget Usage

| Operation | Budget | Breakdown |
|-----------|--------|-----------|
| Date picker | 20 | 3-5 open + 2-8 nav + 1-2 day + 1 close + 1 verify |
| Combobox | 5-8 | 1 click + 2 clear + 1 type + 1 trigger + 1 select + 1 verify |
| Page scroll | 3 | navigate + wait + verify |

Safe margin: 20-action budget covers retries + selector attempts.

---

## Systematic Timeout Strategy

RULE: **All selector waits MUST call `apply_selector_timeout_strategy()`**

**Module**: `core/browser/` (package)
**Main**: `core/browser/timeouts.py`

```python
timeout_ms = apply_selector_timeout_strategy(
    base_timeout_ms=4000,    # From configs/thresholds.yaml
    action_type="action",    # "action" or "wait"
    site_key=None,           # Optional per-site override
    is_optional_click=False   # Optional click optimization
)
```

**Formula**:
- Base timeout from `thresholds.yaml` (default 800ms min)
- Per-site scale factors available in `service_ui_profiles.json`
- Optional click path skips delay if not visible

**FORBIDDEN**: Hardcoded waits, magic 3000ms delays, no timeout config

---

## Timeout Configuration

**Global defaults** (`configs/thresholds.yaml`):
```yaml
selector_timeout_ms: 4000
selector_optional_timeout_ms: 2000
deadline_soft_margin_ms: 5000
```

Skyscanner interstitial press-hold timing keys:
```yaml
skyscanner_press_hold_ready_wait_ms: 9000
skyscanner_press_hold_poll_interval_ms: 250
skyscanner_press_hold_min_hold_ms: 10000
skyscanner_press_hold_degraded_min_ms: 1800
skyscanner_results_overlay_probe_interval_ms: 1200
skyscanner_results_overlay_dismiss_timeout_ms: 700
skyscanner_results_overlay_dismiss_max_clicks: 2
scenario_skyscanner_blank_shell_settle_ms: 9000
scenario_skyscanner_blank_shell_reload_timeout_ms: 35000
scenario_skyscanner_blank_shell_hard_nav_timeout_ms: 30000
scenario_skyscanner_blank_shell_hard_nav_settle_ms: 2000
scenario_skyscanner_blank_shell_manual_recovery_max_uses: 1
scenario_skyscanner_blank_shell_manual_wait_sec: 45
scenario_skyscanner_post_ready_settle_ms: 6000
```

Contract:
- `skyscanner_press_hold_min_hold_ms` is the preferred long-hold floor on PX-style interstitials.
- `skyscanner_press_hold_degraded_min_ms` is a bounded fallback floor used only when remaining grace budget cannot satisfy the preferred floor.
- Interstitial readiness polling must preserve enough remaining grace budget for one full preferred long-hold attempt; avoid consuming hold budget in pre-hold waits.
- Interstitial grace logic should keep first probe lightweight and avoid unbounded repeat holds within one grace window.
- Long-hold attempts should use bounded randomized hold targets within the available hold budget instead of fixed-duration holds.
- Long-hold attempts should include bounded pre-hold cursor approach movement before `mouse.down`, then bounded in-hold micro-drift within target area.
- Challenge target selection should prefer explicit press-hold controls and support bounded `#px-captcha` shell fallback when nested iframe challenge controls are not yet visible.
- Results overlay dismissal on Skyscanner must be bounded by low click-count and short per-selector timeouts.
- Route-bound Skyscanner blank-shell recovery must stay bounded and stage-ordered:
  settle/reload, then hard-nav, then optional max-limited manual assist.
- Post-interstitial handoff on Skyscanner should use a bounded one-shot home rebind before turn execution when recovery reports a stale `/transport/flights/...` shell.
- Post-ready confirmation on Skyscanner should use bounded settle time and re-check readiness before returning success.

**Per-site overrides** (`configs/service_ui_profiles.json` per service):
- Optional: scale factors for specific action types
- If absent, global defaults apply

---

## Deadline Contract

**Enforcement**: Wall-clock in `scenario_runner.py`

- Deadline set at start of scenario
- Each action consumes elapsed time
- When `now >= deadline`, fail fast with `wall_clock_timeout` (legacy alias: `timeout_error`)

NO grace period. Hard wall-clock.

**Evidence req'd**: `deadline_remaining_ms`, `elapsed_ms`, `operation`

---

## Recovery Bounds (Deadline + Budget)

When either deadline OR budget exhausted:
1. Stop all action attempts
2. Return `StepResult` with `reason="budget_hit"` or `reason="wall_clock_timeout"` (legacy alias: `timeout_error`)
3. Do not attempt recovery

**Recovery gating**: [runtime_playbook.md](../20_decision_system/runtime_playbook.md)

---

## Related

- [Runtime Contracts](runtime_contracts.md)
- [Evidence Catalog](evidence_catalog.yaml)
- [Scenario Runner](scenario_runner.md)
- [Configuration Guide](../../CONFIG.md)
