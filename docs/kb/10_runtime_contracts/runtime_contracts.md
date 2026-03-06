# Runtime Contracts

**Scope**: Core runtime types (StepResult, ActionBudget), deadline enforcement, recovery gating
**Defines**: Type signatures, field requirements, contract violations
**Does NOT define**: Extraction details, scenario step implementations, recovery strategies

---

## Quick Reference

| Concept | Module | Lines | Purpose |
|---------|--------|-------|---------|
| StepResult | `scenario/types.py` | [Link](../../../core/scenario/types.py) | Success/failure with reason+evidence |
| ActionBudget | `scenario/types.py` | [Link](../../../core/scenario/types.py) | Per-step action tokens |
| Timeout Strategy | `browser/` | [Link](../../../core/browser/) | Systematic selector waits |
| Deadline | `scenario_runner.py` | [Link](../../../core/scenario_runner.py) | Wall-clock enforcement |
| Recovery Gating | `scenario_runner.py` | [Link](../../../core/scenario_runner.py) | Bounded retry control |

---

## StepResult Type

**Required fields**:
- `ok: bool` — Success/failure flag
- `reason: str` — Machine code (e.g., `calendar_dialog_not_found`, `budget_hit`)
- `evidence: dict` — Diagnostic context
- `selector_used: str | None`
- `action_budget_used: int` — Actions consumed

**Evidence mapping**: [evidence_catalog.yaml](evidence_catalog.yaml) or [evidence.md](evidence.md)

---

## ActionBudget Type

**Constructor**: `ActionBudget(max_actions=N)`

**API**:
```python
if budget.consume(1):  # True if available, False if exhausted
    perform_action()
else:
    return StepResult.failure("budget_hit", evidence=...)
```

**Typical budgets**:
- Date picker: max 20 actions
- Combobox: max 5 actions
- Page scroll/wait: max 3 actions

---

## Timeout Strategy

**API**: `apply_selector_timeout_strategy(selector_or_locator, timeout_ms=800)`

MUST NOT use hardcoded waits. Min threshold: 800ms.

**Scope**: Per-selector, per-site overrides in `configs/service_ui_profiles.json`

Ref: [budgets_timeouts.md](budgets_timeouts.md)

---

## Deadline Contract

**Enforcement**: Wall-clock enforcement in `scenario_runner`

- Trip deadline passed at scenario start
- Each action consumes elapsed time
- When `now >= deadline`, fail fast with `wall_clock_timeout` (legacy alias: `timeout_error`)

NO soft timeouts. Hard enforcement.

---

## Recovery Gating

**Max bounds per scenario**:
- Max 2 attempts per scenario
- Max 2 turns per attempt
- Max 1 collaborative recovery per scenario (VLM + planner)

**Gates**:
1. Budget exhausted → fail fast
2. Deadline exceeded → fail fast
3. Persistent failure (same reason 2+ times) → circuit-open

**Evidence harvest**: Capture evidence for every failure mode.

Ref: [runtime_playbook.md](../20_decision_system/runtime_playbook.md)

---

## Scenario-to-Extraction Guard

When page is non-actionable (bot challenge, blocked, invalid route), extraction skips LLM/VLM paths and returns:

```python
{
    "price": None,
    "confidence": "low",
    "reason": "blocked_interstitial_captcha",  # or explicit mismatch
    "source": "scenario_guard",
    "scenario_ready": False,
}
```

**Guard sources** (priority):
1. `evidence_<service>_state.json` readiness checkpoint
2. `scenario_last_error.json` for blocked interstitial
3. Service route-state artifacts for route mismatch

Ref: [evidence_catalog.yaml](evidence_catalog.yaml)

---

## Related

- [Evidence Catalog](evidence_catalog.yaml)
- [Budgets & Timeouts](budgets_timeouts.md)
- [Scenario Runner](scenario_runner.md)
- [Browser Contract](browser_contract.md)
- [Runtime Playbook](../20_decision_system/runtime_playbook.md)
