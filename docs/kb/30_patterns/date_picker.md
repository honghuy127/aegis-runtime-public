# Date Picker Pattern

**Scope**: Google Flights date picker with ActionBudget, bounded month navigation, explicit failure modes
**Defines**: `gf_set_date()` API, budget consumption, failure reasons
**Does NOT define**: Calendar rendering internals, locale-specific selectors

---

## Problem

Google Flights date picker is bounded to prevent selector spam. Standard approach fails when:
- Calendar doesn't open (stale selectors, hidden field)
- Month navigation exhausts attempts (distant dates >8 months)
- Day selector patterns don't match grid structure
- Verification fails (locale format mismatch)

---

## Solution: `gf_set_date()` API

**Module**: `core/scenario/google_flights.py`

```python
result = gf_set_date(
    browser,
    role="depart",           # "depart" or "return"
    date=target_date,        # YYYY-MM-DD
    budget=ActionBudget(max_actions=20),
    timeout_ms=4000,
    logger=logger,
    deadline_unix_ms=deadline
)
```

**Contract**:
- Returns `StepResult(ok, reason, evidence)`
- Consumes ActionBudget (`consume(1)` per action)
- Respect deadline enforcement
- Max 8 month navigation steps (hard-gate)

---

## Budget Consumption: Typical Path

| Stage | Actions | Notes |
|-------|---------|-------|
| Open calendar | 3-5 | Try 5 opener selectors max |
| Navigate month | 2-8 | Max 8 navigation steps |
| Click day | 1-2 | Retry once if failed |
| Verify value | 1 | Field value matches expected |
| **Total** | 7-15 | Well under 20-action limit |

---

## Explicit Failure Modes

| Reason | When | Evidence keys | Action |
|--------|------|---------------|----|
| `calendar_not_open` | Opener not visible/enabled after 5 attempts | `ui.opener_visible`, `ui.overlay_detected` | Update opener selectors |
| `month_nav_exhausted` | >8 nav steps for target month | `nav_steps`, `target_month`, `role` | Check date >8 months? Adjust nav selectors |
| `day_not_found` | Day selector doesn't match grid | `day`, `month`, `selectors_tried` | Add day selector variants (role='gridcell') |
| `verify_mismatch` | Field value != expected after calendar close | `verified_value`, `expected_date` | Check date format (YYYY-MM-DD vs locale) |
| `calendar_rereopens` | Return picker reopens unexpectedly | `reopens_count`, `depart_chip_state` | Verify return chip before day click |
| `budget_hit` | Exhausted 20 actions | `stage`, `action_count`, `role` | Reduce unnecessary selector attempts |

---

## Month Navigation Strategy

**Algorithm**:
1. Parse current month from header
2. Compute delta months to target
3. Click nav button repeatedly (max 8 times)
4. After each click, wait + re-parse month

**Importance**: Avoid infinite loops; 8-step hard gate.

---

## Calendar Rendering States

**After depart date selection**:
- Calendar may rerender with price annotations
- Wait for interactive state before day click

**After return date selection** (return flow):
- Google Flights may keep dialog open
- Require `Done/完了/適用` commit action
- Return chip may shift focus; re-activate before day click

---

## Evidence Keys (from evidence_catalog.yaml)

- `calendar.opener_attempts`
- `calendar.nav_steps`
- `calendar.day_selector`
- `calendar.verified_value`
- `calendar.return_chip_activated`
- `calendar.return_chip_ready_after`

---

## Runtime Knobs (configs/run.yaml)

```yaml
calendar_selector_scoring_enabled: true   # Rank month headers
calendar_verify_after_commit: true        # Post-selection verify
calendar_parsing_utility: "new"           # "new" | "legacy"
calendar_snapshot_on_failure: true        # Capture debug HTML
```

---

## Related

- [ActionBudget & Timeouts](../10_runtime_contracts/budgets_timeouts.md)
- [Evidence Catalog](../10_runtime_contracts/evidence_catalog.yaml)
- Code: `core/scenario/calendar_driver.py`, `core/scenario/google_flights.py`
- Tests: `tests/test_calendar_driver_unit.py`, `tests/test_browser_google_flights_combobox.py`
