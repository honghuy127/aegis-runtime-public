# KB CARDS: Local Validation & Pre-Commit Guide

**Purpose**: Validate KB cards before committing.

---

## Quick Check

```bash
python -m utils.kb_cards_lint docs/kb/40_cards/cards
```

Expected: `✅ KB Cards Linter: All cards valid`

---

## Validation Checklist

### YAML Frontmatter
REQUIRED fields:
- `id`, `site`, `scope`, `page_kind`, `locale`, `reason_code`, `symptoms`
- `evidence_required` (namespaced: `ui.*`, `verify.*`, `time.*`, `budget.*`, `calendar.*`)
- `actions_allowed` (closed set: adjust_selector, adjust_timeout, add_evidence_key, add_guardrail, add_retry_gate, add_debug_snapshot, update_reason_mapping, update_locale_token, update_extraction_rule, update_config_default, add_test, update_docs)
- `risk` (low/medium/high), `confidence` (0.0-1.0), `last_updated` (YYYY-MM-DD)

### Body Sections (required order)
1. When to use
2. Preconditions
3. Evidence required
4. Diagnosis
5. Best patch plan (format: `PATCH-N: <action> | target: <file:func> | change: <...>`)
6. Rollback
7. Tests
8. Notes
9. Anti-patterns (use ❌ prefix)

### Validations
- Reason code exists in `core/scenario/reasons.py`
- Evidence keys namespaced (`namespace.key`)
- No temporal language (recently, phase, eventually)
- Actions from closed set only

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| Unknown reason code | Use canonical from `reasons.py` or alias |
| Invalid evidence format | Use `namespace.key` pattern |
| Body token exceeds limit | Reduce text, combine bullets |
| Forbidden word (recently) | Remove temporal language |

---

## Optional: Git Hook

```bash
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
set -e
[ -d "docs/kb/40_cards/cards" ] && python -m utils.kb_cards_lint docs/kb/40_cards/cards || exit 1
EOF
chmod +x .git/hooks/pre-commit
```

Skip hook: `git commit --no-verify`

---

## Related

- [Template](template.md)
- [Authoring Rules](authoring_rules.md)
- [Cards Index](cards_index.md)
- [KB INDEX](../INDEX.md)
