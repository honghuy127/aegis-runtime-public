# Combobox Commit Pattern

**Scope**: Autocomplete field fill + commit with IATA deterministic ranking
**Defines**: `commit_to_suggestion()` API, IATA ranking rules, bounded retry
**Does NOT define**: Individual site-specific selectors, keyboard layouts

---

## Problem

Autocomplete fields (airports, rental locations) are probabilistic. Simply clicking first suggestion fails because:
- Multiple aliases for same IATA code
- Partial matches ("SF" → "San Francisco", "Santa Fe")
- Rank-0 matches have no IATA confidence
- Enter key fallback may be safer than blind Rank-1 click

---

## Solution: Deterministic IATA Ranking

**API**: `commit_to_suggestion(browser, field_role, input_value, budget, timeout_ms, logger)`

```python
result = commit_to_suggestion(
    browser,
    field_role="origin",       # "origin" or "destination"
    input_value="SFO",
    budget=ActionBudget(max_actions=5),
    timeout_ms=2000,
    logger=logger
)
```

**Returns**: `StepResult(ok, reason, evidence)`

---

## IATA Ranking Rules

| Rank | Match Pattern | Action | Confidence |
|------|---------------|--------|------------|
| 3 | Exact IATA code (SFO, JFK, HND) | Click suggestion | High |
| 2 | IATA in parentheses or alias token | Click suggestion | Medium |
| 1 | Partial match (starts with input) | Enter key or fallback | Low |
| 0 | No match | Fallback (skip field or re-enter) | None |

---

## Commit Logic

1. Type input value → wait for suggestions
2. Scan suggestion list for IATA pattern match
3. If Rank >= 2: click suggestion, wait 150ms, verify field
4. If Rank 1: press Enter (let server autocomplete)
5. If Rank 0: fallback (double-enter or skip)
6. Verify field changes post-commit

---

## Failure Modes

| Reason | When | Evidence keys | Action |
|--------|------|---------------|----|
| `combobox_fill_failed` | Suggestions never appear after typing | `input_value`, `suggestions_wait_ms`, `dropdown_visible` | Check input selector, wait time |
| `no_suggestion_match` | Typed value not in suggestions | `input_value`, `suggestions`, `similar_options` | Verify IATA code, try alias |
| `rank_mismatch` | Suggestion rank < 2, commit fails | `rank`, `match_quality`, `input_value` | Confirm expected alias exists |
| `verify_mismatch` | Field value doesn't change after commit | `pre_value`, `post_value`, `expected` | Check server-side acceptance |
| `budget_hit` | Exhausted 5 actions | `stage`, `action_count` | Reduce retry attempts |

---

## Suggestion Detection Heuristic

**Pattern**: Look for:
- Role="option" or role="listbox"
- Text containing IATA code (e.g., "SFO San Francisco")
- Clickable elements in dropdown
- Visible + enabled state

---

## Evidence Keys (from evidence_catalog.yaml)

- `combobox.input_value`
- `combobox.suggestions_count`
- `combobox.match_rank`
- `combobox.commit_strategy`
- `combobox.post_value`

---

## Bounded Retry: Max 2 Attempts

- Attempt 1: Try Rank >= 2 click
- Attempt 2: Fallback (Enter key or skip)
- NO Attempt 3: Hard fail with `combobox_fill_failed`

---

## 150ms Post-Commit Wait

After clicking suggestion or pressing Enter:
- Wait 150ms for field value update
- Scan field value; verify change occurred
- If unchanged, treat as failed commit

This prevents race conditions on slow/mobile networks.

---

## Related

- [IATA Doctrine](../00_foundation/doctrine.md#iata-doctrine-deterministic-commit)
- [Evidence Catalog](../10_runtime_contracts/evidence_catalog.yaml)
- [Selectors Pattern](selectors.md)
- Code: `core/agent/actor/actor.py`, `core/scenario/google_flights.py`
- Tests: `tests/test_browser_google_flights_combobox.py`
