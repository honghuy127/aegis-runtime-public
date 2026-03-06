# Selector Pattern

**Scope**: Semantic selector ranking, robustness against layout changes, site profile management
**Defines**: Selector quality scoring, preference hierarchy, breakage diagnosis
**Does NOT define**: Service-specific XPath payloads, CSS preprocessor rules

---

## Problem

Selectors break when UI changes. Simple CSS/XPath selectors are brittle because:
- Positional selectors fail on reflow (`.div:nth-child(3)`)
- Class-based selectors fail on style refactors (`.styles_xyz_123`)
- Text selectors fail on copy updates
- No single selector future-proof

---

## Solution: Hierarchical Selector Matching

**Pattern**: Try selectors in ranked order until first match

```python
ACTOR_SELECTOR_PREFERENCE_ORDER = [
    "role",           # aria-label / aria-labelledby
    "aria",           # aria-* attributes
    "data_attr",      # data-test, data-qa, etc.
    "text",           # Text content (locale-fragile)
    "class",          # CSS classes (refactor-fragile)
    "xpath",          # XPath (position-fragile)
]
```

---

## Selector Quality Scoring

**Assign score per dimension**:

| Dimension | High (stable) | Medium | Low (brittle) |
|-----------|--------------|--------|---------------|
| Semantic | role=button | aria-label | none |
| Scoping | Parent ID + child role | Element text | position:nth-child |
| Coverage | action + state attrs | class tokens | partial text match |
| Locality | Recent element path | sibling search | global XPath |

**Score**: Sum across dimensions; prefer highest-scoring selector.

---

## Recommended Selectors (Priority Order)

1. **Role + parent**: `[role='button'][aria-label*='Submit']` within parent
2. **Data attributes**: `[data-testid='date_opener']` (stable if test code-reviewed)
3. **Aria labels**: `[aria-label='Open calendar']` (fragile on translation)
4. **Distinctive text**: `button:has-text('Search')` (rarely changes)
5. **Avoid**: Bare nth-child, class chains, position XPath

---

## Selector Timeout Strategy

**Never hardcode waits.**

Use `apply_selector_timeout_strategy()`:
```python
timeout_ms = apply_selector_timeout_strategy(
    base_timeout_ms=4000,
    action_type="action",  # "action" or "wait"
    site_key=None,         # Per-site override
    is_optional_click=False
)
browser.page.wait_for_selector(selector, timeout=timeout_ms)
```

Ref: [budgets_timeouts.md](../10_runtime_contracts/budgets_timeouts.md)

---

## Robustness Patterns

**Multi-selector fallback**:
```python
selectors = [
    "[role='button'][aria-label*='Date']",  # Semantic
    "[data-testid='date_picker_opener']",   # Data attr
    "button.date-opener",                    # Class
]
for selector in selectors:
    if locator := browser.page.locator(selector):
        click(locator)
        break
```

**Visibility check before click**:
```python
if selector.is_visible():
    selector.click()
else:
    # Scroll into view or fail
    selector.scroll_into_view_if_needed()
    selector.click()
```

**Iframe handling**:
- Sites using iframes require frame-scoped selectors
- Use `page.frame_locator()` to scope within iframe
- Document frame hierarchy in UI profile

---

## Site Profile Management (service_ui_profiles.json)

```json
{
  "google_flights": {
    "selectors": {
      "date_opener": "[role='button'][aria-label*='Depart']",
      "day_cell": "[role='gridcell']",
      "origin_input": "[data-placeholder*='Where from']"
    },
    "timeout_scale": 1.2,
    "locale_specific": {
      "ja": {
        "date_opener": "[aria-label*='出発日']"
      }
    }
  }
}
```

---

## Breakage Diagnosis

**When selector fails**:

1. Capture screenshot → visual inspection
2. Check HTML snapshot → search for similar patterns
3. Check data attributes → is data-testid still present?
4. Check role attributes → has aria-label changed?
5. Update profile → commit to version control

**Evidence logging**:
```python
evidence = {
    "selector": selector,
    "visible": locator.is_visible(),
    "enabled": locator.is_enabled(),
    "element_count": page.locator(selector).count(),
}
```

---

## Locale Considerations

**Selector selection**:
- Prefer role/data attributes (locale-independent)
- Fallback to role (e.g., role="button") when possible
- Use locale-specific profiles for text matching

**Language hint to VLM**:
```python
vlm_analyze_page(screenshot, language_hint="ja", confidence_threshold=0.8)
```

Ref: [i18n_ja.md](i18n_ja.md)

---

## Related

- [Evidence Catalog](../10_runtime_contracts/evidence_catalog.yaml)
- [Budgets & Timeouts](../10_runtime_contracts/budgets_timeouts.md)
- Code: `core/agent/actor/actor.py`, `core/scenario/google_flights.py`
- Tests: `tests/test_calendar_selector_scoring.py`
