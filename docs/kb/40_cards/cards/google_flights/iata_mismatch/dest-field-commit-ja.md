---
id: gf-iata-mismatch-dest-ja-001
site: google_flights
scope: scenario
page_kind: [flights_results]
locale: [ja-JP, en-US]
reason_code: iata_mismatch
symptoms: [dest_combobox_filled_but_placeholder_empty, observed_dest_blank_expected_ITM, form_verification_failed_route_mismatch]
evidence_required: [verify.route_mismatch_fields, verify.expected_dest, verify.observed_dest, suggest.committed_value, input.placeholder_present]
actions_allowed: [adjust_selector, adjust_timeout, add_guardrail]
risk: high
confidence: 0.85
last_updated: 2026-02-21
kb_links: [docs/kb/20_decision_system/triage_runbook.md#iata_mismatch, docs/kb/30_patterns/combobox_commit.md#iata-matching, docs/kb/30_patterns/selectors.md#role-based-selection]
code_refs: [core/scenario/google_flights.py:google_fill_and_commit_location, core/browser/combobox.py:fill_google_flights_combobox]
tags: [combobox, iata-commit, route-verification, japan-locale]
---

# CARD: google_flights / iata_mismatch / dest-field-commit-ja

## When to use
This card applies when:
- Site is `google_flights` and locale is `ja-JP` or `en-US`
- Destination combobox was filled and submitted
- Form verification detected destination field is empty or blank
- Expected IATA code (e.g., ITM) was not committed to the field
- Placeholder or display value not updated after fill attempt

## Preconditions
- Destination combobox is visible and clickable
- IATA code suggestion dropdown is available
- Selection mechanism (click option, press Enter) is configured
- Form verification is active to detect empty fields

## Evidence required
Expected evidence keys in StepResult.evidence:
- `verify.route_mismatch_fields` (dict, field name → expected vs observed values)
- `verify.expected_dest` (str, expected IATA code)
- `verify.observed_dest` (str, actual field value after fill)
- `suggest.committed_value` (str, value that was sent to fill)
- `input.placeholder_present` (bool, True if placeholder text visible instead of value)

## Diagnosis
1. Check `verify.observed_dest` vs `verify.expected_dest`: if mismatch, commit failed
2. Profile `input.placeholder_present`: if True, field was not actually filled
3. Verify `suggest.committed_value` was typed before option click
4. Check browser logs for option listbox closure or click interception

## Best patch plan
- PATCH-1: adjust_selector | target: core/scenario/google_flights.py:google_fill_and_commit_location | change: Add post-fill DOM check; inspect input.value for IATA; retry if empty
- PATCH-2: adjust_timeout | target: core/browser/wait.py:wait_for_selector | change: Increase option click timeout if deadline logged; verify listbox stays open during selection
- PATCH-3: add_guardrail | target: core/scenario/google_flights.py:google_fill_and_commit_location | change: Add fallback to use option full text match if IATA mismatch (e.g., "Itami (ITM)")

## Rollback
- Revert to original combobox fill strategy; revert timeout value; remove full-text fallback

## Tests
Command to validate this card:
```bash
python -m utils.kb_cards_lint docs/kb/40_cards/cards/google_flights/iata_mismatch/dest-field-commit-ja.md
```

Command to verify applied patch (manual):
```bash
python -m pytest tests/test_route_fill_ja.py::test_destination_iata_commit -xvs
```

## Notes
- This card addresses destination combobox IATA commitment failures in Japanese locale (ja-JP) Google Flights
- Root cause is often premature listbox closure or click interception before selection registered
- Form verification detects the empty field, but the fill mechanism failed silently
- Confidence score (0.85) reflects moderate reproducibility; some variance in timing

## Anti-patterns
- ❌ Do NOT retry the same fill without verifying previous attempt actually failed
- ❌ Do NOT skip form verification check (silent failures lead to invalid routes)
- ❌ Do NOT ignore placeholder visibility (indicates field was never filled)
- ❌ Do NOT use IATA code as sole verification anchor (use both IATA and full text match)
