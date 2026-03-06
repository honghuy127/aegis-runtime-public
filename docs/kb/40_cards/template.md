---
id: example-gf-calendar-not-open-depart-002
site: google_flights
scope: scenario
page_kind: [flights_results]
locale: [ja-JP, en-US]
reason_code: calendar_not_open
symptoms: [depart_combobox_clicked_but_dialog_unopened, all_selectors_exhausted_with_visibility_false, overlay_detected]
evidence_required: [ui.selector_attempts, ui.last_visible, ui.last_enabled, ui.last_overlay, ui.selectors_tried]
actions_allowed: [adjust_selector, adjust_timeout, add_guardrail]
risk: high
confidence: 0.95
last_updated: 2026-02-21
kb_links: [docs/kb/20_decision_system/triage_runbook.md#calendar_not_open, docs/kb/30_patterns/date_picker.md#opening-the-calendar]
code_refs: [core/scenario/google_flights.py:gf_set_date, core/browser/wait.py:wait_for_selector]
tags: [date-picker, japan-locale, ui-timeout]
---

# CARD: google_flights / calendar_not_open / depart-dialog-unopened-002

## When to use
This card applies when:
- Site is `google_flights` and locale is `ja-JP` or `en-US`
- Page is `flights_results` (search results page with form)
- User attempts to fill depart date field
- Combobox role element is successfully clicked (logged as `gf_set_date.open.*`)
- But calendar dialog never appears (root_selector `[role='dialog']:visible` fails)
- All 5 selector variants are tried with `last_visible=False` on each

## Preconditions
- Browser is on Google Flights search page
- Depart date combobox is visible and clickable
- Date picker JavaScript has been injected and initialized
- No hard interstitials or consent dialogs blocking interaction

## Evidence required
Expected evidence keys in StepResult.evidence:
- `ui.selector_attempts` (int, ≥1)
- `ui.last_visible` (bool, False indicates non-visibility)
- `ui.last_enabled` (bool, False indicates disabled state)
- `ui.last_overlay` (bool, True if z-index/pointer-events issues detected)
- `ui.selectors_tried` (list, all 5 variants should be present)

## Diagnosis
1. Check if any selector reports `last_visible=True` → likely a different issue (use calendar_dialog_not_awaited instead)
2. Profile `ui.last_overlay`: if True, likely a CSS/z-index conflict
3. Check browser console for JavaScript errors: `gf_set_date.open.*` logs
4. Verify dialog root_selector `[role='dialog']:visible` is correct for page locale

## Best patch plan
- PATCH-1: adjust_selector | target: core/scenario/google_flights.py:gf_set_date | change: Add aria-label='出発日' variant for Japanese locale (current: 5 selectors, add 6th)
- PATCH-2: adjust_timeout | target: core/browser/wait.py:wait_for_selector | change: Increase dialog wait from 1200ms to 1500ms if selectors_tried=5
- PATCH-3: add_guardrail | target: core/browser/wait.py:wait_for_selector | change: Add pre-click visual check (opacity >0.8, visibility!='hidden') before attempting dialog open
- TEST-1: add_test | target: tests/test_date_picker_ja.py | change: Add test for Japanese locale depart calendar opening with overlay simulation
- TEST-2: add_test | target: tests/test_date_picker_ja.py | change: Add test for 6-selector variant fallback

## Rollback
- Revert to 5-selector set; keep 1200ms timeout; remove visual pre-check guardrail

## Tests
Command to validate this card:
```bash
python -m utils.kb_cards_lint docs/kb/40_cards/cards/gf_ja_calendar_not_open_depart_002.md
```

Command to verify applied patch (manual):
```bash
python -m pytest tests/test_date_picker_ja.py::test_depart_dialog_opens_with_overlay -xvs
```

## Notes
- This card addresses a recurring issue in Japanese locale (ja-JP) Google Flights scenarios
- Root cause is usually CSS z-index or pointer-events:none on date picker container
- The 6-selector variant (aria-label='出発日') is locale-specific and should only be used in ja-JP context
- Confidence score (0.95) reflects high reproducibility in latest debug episode (20260221_212819_674011)

## Anti-patterns
- ❌ Do NOT increase timeout beyond 1500ms (wastes action budget)
- ❌ Do NOT retry the same 5 selectors without modification (infinite loop)
- ❌ Do NOT skip visual check if overlay is suspected (may lead to false negatives)
- ❌ Do NOT apply this patch without verifying locale is ja-JP or en-US (may break other locales)

---

# TEMPLATE CARD (COPY BELOW FOR NEW CARDS)

---
id: <site>-<reason-slug>-<version>
site: <enum: google_flights, skyscanner, kayak, ...>
scope: <enum: scenario, extraction, routing, llm, vlm, storage, config>
page_kind: [<page_type>, "any"]
locale: [<locale_tag>, "any"]
reason_code: <canonical reason code from core/scenario/reasons.py>
symptoms: [<short symptom string>, <another>]
evidence_required: [<namespaced.key>, <another>]
actions_allowed: [<token from closed set>]
risk: <enum: low, medium, high>
confidence: <float 0.0-1.0>
last_updated: YYYY-MM-DD
kb_links: [docs/kb/path.md#anchor, ...]
code_refs: [path/to/file.py:function, ...]
tags: [<descriptive tag>]
---

# CARD: <site> / <reason_code> / <short-slug>

## When to use
- Describe the exact condition when this card applies
- List required preconditions (locale, page_kind, browser state)
- Reference reason_code that must be triggered

## Preconditions
- State prerequisites that must be true for the card to apply
- Include browser state, page readiness, JavaScript injection status

## Evidence required
List each evidence key in `<namespace>.<key>` format:
- `namespace.key` (type, description)
- Example: `ui.selector_attempts` (int, count of selector attempts)

## Diagnosis
- Step 1: Check X condition
- Step 2: If X, then likely cause Y
- Step 3: Verify via console log Z

## Best patch plan
Structure as:
- PATCH-N: <action_token> | target: <file:function> | change: <1 sentence, ≤120 chars>
- TEST-N: add_test | target: <test file> | change: <test description>

## Rollback
- Single bullet with rollback action

## Tests
Include bash/Python commands to validate patch and run tests.

## Notes
- Any supporting context, reproducibility rate, environment notes

## Anti-patterns
- ❌ Anti-pattern 1
- ❌ Anti-pattern 2
