---
id: gf-calendar-not-open-depart-ja-001
site: google_flights
scope: scenario
page_kind: [flights_results]
locale: [ja-JP, en-US]
reason_code: calendar_dialog_not_found
symptoms: [depart_combobox_clicked_but_dialog_unopened, all_selectors_exhausted_with_visibility_false, overlay_detected]
evidence_required: [ui.selector_attempts, ui.last_visible, ui.last_enabled, ui.last_overlay, ui.selectors_tried]
actions_allowed: [adjust_selector, adjust_timeout, add_guardrail]
risk: high
confidence: 0.95
last_updated: 2026-02-21
kb_links: [docs/kb/20_decision_system/triage_runbook.md#calendar_dialog_not_found, docs/kb/30_patterns/date_picker.md#opening-the-calendar, docs/kb/30_patterns/selectors.md#semantic-priority]
code_refs: [core/scenario/google_flights.py:gf_set_date, core/browser/wait.py:wait_for_selector]
tags: [date-picker, japan-locale, ui-timeout]
---

# CARD: google_flights / calendar_dialog_not_found / depart-dialog-unopened-ja

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
1. Check if any selector reports `last_visible=True` → likely a different issue (use date_picker_unverified instead)
2. Profile `ui.last_overlay`: if True, likely a CSS/z-index conflict
3. Check browser console for JavaScript errors: `gf_set_date.open.*` logs
4. Verify dialog root_selector `[role='dialog']:visible` is correct for page locale

## Best patch plan
- PATCH-1: adjust_selector | target: core/scenario/google_flights.py:gf_set_date | change: Add aria-label='出発日' variant for Japanese locale (6th selector)
- PATCH-2: adjust_timeout | target: core/browser/wait.py:wait_for_selector | change: Increase dialog wait from 1200ms to 1500ms if selectors_tried=5
- PATCH-3: add_guardrail | target: core/browser/wait.py:wait_for_selector | change: Add pre-click visual check (opacity >0.8, visibility!='hidden')

## Rollback
- Revert to 5-selector set; keep 1200ms timeout; remove visual pre-check guardrail

## Tests
Command to validate this card:
```bash
python -m utils.kb_cards_lint docs/kb/40_cards/cards/google_flights/calendar_dialog_not_found/depart-dialog-unopened-ja.md
```

Command to verify applied patch (manual):
```bash
python -m pytest tests/test_date_picker_ja.py::test_depart_dialog_opens_with_overlay -xvs
```

## Notes
- This card addresses a recurring issue in Japanese locale (ja-JP) Google Flights scenarios
- Root cause is usually CSS z-index or pointer-events:none on date picker container
- The 6-selector variant (aria-label='出発日') is locale-specific and should only apply in ja-JP context
- Confidence score (0.95) reflects high reproducibility in debug episode 20260221_212819_674011

## Anti-patterns
- ❌ Do NOT increase timeout beyond 1500ms (wastes action budget)
- ❌ Do NOT retry the same 5 selectors without modification (infinite loop)
- ❌ Do NOT skip visual check if overlay is suspected (may lead to false negatives)
- ❌ Do NOT apply this patch without verifying locale is ja-JP or en-US (may break other locales)
