# KB CARDS: Master Index

**Last updated**: 2026-02-21

Master index of all KB diagnostic cards organized by site and reason code.

---

## Index by Site

### google_flights

#### calendar_dialog_not_found
- [depart-dialog-unopened-ja](cards/google_flights/calendar_dialog_not_found/depart-dialog-unopened-ja.md) — Depart date combobox clicked but calendar dialog never appeared; all 5 selector variants exhausted with visibility issues (ja-JP, en-US)

#### month_nav_exhausted
- [return-nav-ja](cards/google_flights/month_nav_exhausted/return-nav-ja.md) — Return date calendar opened but month header parsing failed; navigation exhausted after 8 steps (ja-JP, en-US)

#### iata_mismatch
- [dest-field-commit-ja](cards/google_flights/iata_mismatch/dest-field-commit-ja.md) — Destination combobox filled but field remains empty; IATA code not committed to form (ja-JP, en-US)

---

## Index by Reason Code

### calendar_dialog_not_found
- [google_flights / depart-dialog-unopened-ja](cards/google_flights/calendar_dialog_not_found/depart-dialog-unopened-ja.md)
  - Locales: ja-JP, en-US
  - Risk: high | Confidence: 0.95

### month_nav_exhausted
- [google_flights / return-nav-ja](cards/google_flights/month_nav_exhausted/return-nav-ja.md)
  - Locales: ja-JP, en-US
  - Risk: high | Confidence: 0.90

### iata_mismatch
- [google_flights / dest-field-commit-ja](cards/google_flights/iata_mismatch/dest-field-commit-ja.md)
  - Locales: ja-JP, en-US
  - Risk: high | Confidence: 0.85

---

## Statistics

- **Total cards**: 3
- **Sites covered**: 1 (google_flights)
- **Reason codes covered**: 3 (calendar_dialog_not_found, month_nav_exhausted, iata_mismatch)
- **Locales represented**: 2 (ja-JP, en-US)
- **Action tokens used**: adjust_selector, adjust_timeout, add_guardrail
- **Average confidence**: 0.90

---

## Quick Links

- [template.md](template.md) — Canonical template for new cards
- [authoring_rules.md](authoring_rules.md) — Copy-paste prompt for card generation
- [precommit_guide.md](precommit_guide.md) — Local validation checklist
- [utils/kb_cards_lint.py](../../../utils/kb_cards_lint.py) — YAML linter for cards
- [utils/kb_cards_sanity_check.py](../../../utils/kb_cards_sanity_check.py) — Sanity checker for card registry

---

## Contributing

When adding new cards:
1. Use the [template.md](template.md) as a starting template
2. Follow the [authoring_rules.md](authoring_rules.md) for strict requirements
3. Validate locally with [precommit_guide.md](precommit_guide.md)
4. Place card in: `docs/kb/40_cards/cards/<site>/<reason_code>/<slug>.md`
5. Update this index (cards_index.md) with new card entries
6. Run sanity check before committing: `python -m utils.kb_cards_sanity_check`
