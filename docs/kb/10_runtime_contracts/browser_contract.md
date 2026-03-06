# Browser Contract

**Scope**: Playwright wrapper, timeout strategy, deadline enforcement, selector stability
**Defines**: Browser API facade, interaction-hardening mode, page wait mechanics
**Does NOT define**: Individual selector implementations, site-specific profiles

---

## Browser Initialization

**Module**: `core/browser/` (package)
**Main Class**: `core/browser/session.py` - BrowserSession

Playwright browser with:
- Interaction hardening for runtime stability
- User agent spoofing
- Geolocation mocking
- Deadline enforcement

```python
from core.browser import BrowserSession
browser = BrowserSession(locale=locale_hint, deadline_unix_ms=deadline)
```

**Interaction hardening**: Chromium-based runtime hardening can reduce brittle automation markers, but challenge handling remains human-verification-gated.

---

## Page Wait Semantics

**API**: `page.wait_for_selector(selector, timeout=timeout_ms)`

MUST use `apply_selector_timeout_strategy()` to compute timeout_ms.

**Behavior**:
- Returns locator if selector matches within timeout
- Raises `TimeoutError` if deadline exceeded
- Respects browser.deadline for hard wall-clock enforcement

**No partial waits**: Timeout applies to full condition, not incremental steps.

---

## Locator & Click Semantics

**API**: `locator.click(timeout=timeout_ms, force=False)`

**Mechanics**:
- Waits for visibility + enabled state
- Scrolls into view if needed
- Fails if covered by overlay (unless force=True)

**Force mode**: Bypasses visibility check (use sparingly; document reason)

---

## Deadline Enforcement

**Type**: Unix milliseconds (wall-clock)

**Enforcement points**:
- Page waits: Each `wait_for_selector()` checks deadline
- Scenario runner: Fails with `wall_clock_timeout` when elapsed >= deadline (legacy alias: `timeout_error`)
- No soft timeouts; hard wall-clock only

Set at scenario start; respected across all browser operations.

---

## Screenshot Capture

**API**: `page.screenshot(path=path_str)`

**Usage**: Capture for evidence, VLM analysis, debug artifacts

**Stored at**: `storage/runs/<run_id>/artifacts/screenshot_<attempt>.png`

---

## HTML Snapshot

**API**: `page.content() → str`

**Usage**: Full DOM snapshot for extraction, selector debugging

**Stored at**: `storage/runs/<run_id>/artifacts/html_<attempt>.html`

---

## Frame Navigation

**API**: `page.goto(url) → Response`

**Contract**:
- Respects deadline
- No error on redirect chains
- Fails if page unreachable (network error)

---

## Selector Timeout Configuration

Per-site timeout multipliers in `configs/service_ui_profiles.json`:
```json
{
  "google_flights": {
    "selector_timeout_scale": 1.5,
    "wait_timeout_scale": 1.2
  }
}
```

Applied via `apply_selector_timeout_strategy()`.

---

## Related

- [Budgets & Timeouts](budgets_timeouts.md)
- [Runtime Contracts](runtime_contracts.md)
- [Scenario Runner](scenario_runner.md)
