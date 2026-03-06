---
id: gf-month-nav-exhausted-return-ja-001
site: google_flights
scope: scenario
page_kind: [flights_results]
locale: [ja-JP, en-US]
reason_code: month_nav_exhausted
symptoms: [calendar_dialog_opened, month_header_parse_failed, candidates_found_but_rejected, target_date_unreachable]
evidence_required: [calendar.failure_stage, calendar.header_selectors_tried, calendar.candidates, calendar.rejected, calendar.target_month]
actions_allowed: [adjust_selector, adjust_timeout, add_guardrail]
risk: high
confidence: 0.90
last_updated: 2026-02-21
kb_links: [docs/kb/20_decision_system/triage_runbook.md#month_nav_exhausted, docs/kb/30_patterns/date_picker.md#month-navigation, docs/kb/30_patterns/selectors.md#robust-locators]
code_refs: [core/scenario/google_flights.py:gf_set_date, core/browser/wait.py:wait_for_selector]
tags: [date-picker, month-navigation, japan-locale, text-parsing]
---

# CARD: google_flights / month_nav_exhausted / return-nav-ja

## When to use
This card applies when:
- Site is `google_flights` and locale is `ja-JP` or `en-US`
- Return date calendar dialog has opened successfully
- Month header text parsing failed (selector matched 1 candidate but was rejected)
- Navigation exhausted after 8 steps without reaching target month
- Reason code: month_nav_exhausted

## Preconditions
- Calendar dialog is visible and open
- Month header element is present but text parsing failed
- Navigation attempts have been exhausted
- Target month/year has been calculated

## Evidence required
Expected evidence keys in StepResult.evidence:
- `calendar.failure_stage` (str, stage where parsing failed)
- `calendar.header_selectors_tried` (list, selectors attempted)
- `calendar.candidates` (int, candidates found)
- `calendar.rejected` (int, candidates rejected due to format)
- `calendar.target_month` (str, target month in local format)

## Diagnosis
1. Check `calendar.target_month` format: e.g., "2026年3月" (Japanese) vs "March 2026" (English)
2. Verify `calendar.header_selectors_tried` is not empty; if empty, add header selector variant
3. Confirm rejected candidate text does not contain target month string
4. Profile locale-specific date parsing (kanji vs ASCII month names)

## Best patch plan
- PATCH-1: adjust_selector | target: core/scenario/google_flights.py:gf_set_date | change: Add [role='heading']:has-text(year-month pattern) for robust month header
- PATCH-2: adjust_selector | target: core/scenario/google_flights.py:gf_set_date | change: Improve parser regex for Japanese "2026年3月" (year-kanji-month-kanji)
- PATCH-3: add_guardrail | target: core/scenario/google_flights.py:gf_set_date | change: Extend nav attempts from 8 to 10 if candidates found but rejected

## Rollback
- Revert to original 8 navigation steps; revert to previous header selector; revert parser regex

## Tests
Command to validate this card:
```bash
python -m utils.kb_cards_lint docs/kb/40_cards/cards/google_flights/month_nav_exhausted/return-nav-ja.md
```

Command to verify applied parser change (manual):
```bash
python -m pytest tests/test_date_picker_ja.py::test_month_header_parsing_japanese -xvs
```

## Notes
- This card addresses month navigation issues in Japanese locale (ja-JP) Google Flights
- Root cause is often Japanese date format parsing (year+kanji+month+kanji vs English)
- Navigation exhaustion suggests parser is rejecting valid candidates due to format mismatch
- Confidence score (0.90) reflects good reproducibility with minor locale variations

## Anti-patterns
- ❌ Do NOT hardcode month names (use locale-aware parsing)
- ❌ Do NOT increase nav attempts beyond 10 (diminishing returns on actionbudget)
- ❌ Do NOT skip format validation between candidates (may lead to wrong month selection)
- ❌ Do NOT apply English-locale selectors to Japanese content (will cause immediate failure)
