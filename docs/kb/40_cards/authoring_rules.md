# KB CARDS AUTHORING PROMPT

**Purpose**: Copy-paste this prompt into Copilot (or CLI agent) to generate a new KB diagnostic card with strict compliance.

---

## COPY-PASTE PROMPT FOR CARD GENERATION

```
You are generating a SINGLE KB diagnostic card (Markdown file) for docs/kb/40_cards/cards/.

STRICT REQUIREMENTS (non-negotiable):
1) Output ONLY the card content (no preamble, no explanation, no chat)
2) Card MUST follow docs/kb/template.md schema EXACTLY
3) Every YAML field must be present and valid; do NOT omit optional fields if known
4) reason_code MUST exist in core/scenario/reasons.py registry (canonical, not alias)
5) evidence_required keys MUST be namespaced: ui.*, time.*, budget.*, calendar.*, verify.*, suggest.*, dom.*, net.*, input.*
6) actions_allowed tokens MUST be from this CLOSED SET ONLY:
   - adjust_selector
   - adjust_timeout
   - add_evidence_key
   - add_guardrail
   - add_retry_gate
   - add_debug_snapshot
   - update_reason_mapping
   - update_locale_token
   - update_extraction_rule
   - update_config_default
   - add_test
   - update_docs
7) risk must be: low, medium, or high
8) confidence must be a float 0.0-1.0 (e.g., 0.85)
9) last_updated must be YYYY-MM-DD format
10) Body text MUST include ALL these sections in this order:
    - ## When to use
    - ## Preconditions
    - ## Evidence required
    - ## Diagnosis
    - ## Best patch plan
    - ## Rollback
    - ## Tests
    - ## Notes
    - ## Anti-patterns
11) "Best patch plan" must be structured as:
    - PATCH-N: <action_token> | target: <file/function> | change: <1 sentence, ≤120 chars>
    - TEST-N: add_test | target: <test file> | change: <test description>
12) "Anti-patterns" must list 2-4 bullets with ❌ prefix, each showing what NOT to do
13) No narrative language: no "recently", "we ", "phase", "eventually", "later", etc.
14) No speculation: claims must be backed by observable evidence from logs/artifacts
15) Evidence keys in "Evidence required" section must match evidence_required YAML list

INPUTS YOU MUST USE:
- [DEBUG_EPISODE_PATH]: Path to latest debug run (e.g., storage/runs/20260221_212819_674011)
- [SITE]: Which site this card is for (e.g., google_flights)
- [FAILING_REASON_CODE]: The canonical reason code to diagnose (must exist in reasons.py)
- [OBSERVED_SYMPTOMS]: The actual symptoms observed in the logs
- [TARGET_LOCALE]: Locale if applicable (e.g., ja-JP, en-US, or "any")
- [TARGET_PAGE_KIND]: Page type where issue occurs (e.g., flights_results)

CITATION RULES:
- Every "Best patch plan" action MUST reference a real file path (relative to repo root)
- Every "Evidence required" key MUST map to actual evidence field names from logs or code
- Every kb_links entry MUST point to existing docs/kb/ path with optional #anchor
- Every code_refs entry MUST point to real file:function (line optional)

OUTPUT INSTRUCTIONS:
- Start with YAML frontmatter (---\nfields\n---)
- Follow with title: # CARD: <site> / <reason_code> / <slug>
- Include all 9 body sections listed above
- End with newline
- Do NOT include markdown code fence, file path, or any wrapper text

EXAMPLE (from docs/kb/template.md):
See the full example card at docs/kb/template.md for reference structure.

NOW GENERATE THE CARD:
- Use the inputs provided above
- Ensure YAML is valid (test with: python -m utils.kb_cards_lint)
- Ensure reason_code exists in core/scenario/reasons.py (run: grep -w "{reason_code}" core/scenario/reasons.py)
- Ensure evidence keys are namespaced correctly
- Ensure actions_allowed tokens are from the closed set
- Ensure no forbidden narrative words
- Ready to save as: docs/kb/40_cards/cards/<site>-<reason_code>-<version>.md
```

---

## HOW TO USE THIS PROMPT

1. **Collect inputs** from your debug episode:
   - Identify the DEBUG_EPISODE_PATH (e.g., `storage/runs/20260221_212819_674011`)
   - Extract the SITE (e.g., `google_flights`)
   - Identify the FAILING_REASON_CODE (canonical, from reasons.py)
   - List OBSERVED_SYMPTOMS from run.log
   - Note TARGET_LOCALE and TARGET_PAGE_KIND

2. **Substitute the placeholders** in the prompt above with your actual values

3. **Copy the full prompt** (including the code fence) into Copilot or your CLI agent

4. **Verify output**:
   ```bash
   python -m utils.kb_cards_lint docs/kb/40_cards/cards/
   ```

5. **Check reason_code exists**:
   ```bash
   grep -E "code=\"$(YOUR_REASON_CODE)\"" core/scenario/reasons.py
   ```

6. **Commit** once linter passes:
   ```bash
   git add docs/kb/40_cards/cards/<new_card>.md
   git commit -m "Add KB card: <site>/<reason_code>/<slug>"
   ```

---

## CLOSED SET OF ACTIONS_ALLOWED (do NOT deviate)

| Action Token | Scope | Example |
|---|---|---|
| `adjust_selector` | Selector query refinement | Add aria-label variant; use semantic hierarchy |
| `adjust_timeout` | Timeout/deadline tuning | Increase wait from 1200ms to 1500ms |
| `add_evidence_key` | Debugging/observability | Add selector visibility state to evidence |
| `add_guardrail` | Pre-condition checks | Add visual check before dialog open |
| `add_retry_gate` | Retry logic | Add exponential backoff before retry |
| `add_debug_snapshot` | Logging/tracing | Add HTML dump before/after action |
| `update_reason_mapping` | Reason code aliases | Add fallback alias for new error message |
| `update_locale_token` | Locale/i18n handling | Add Japanese aria-label token to locales |
| `update_extraction_rule` | LLM/VLM parsing | Add regex for new date format variant |
| `update_config_default` | Config schema | Increase LLM timeout threshold in config |
| `add_test` | Test coverage | Add test for Japanese locale scenario |
| `update_docs` | Documentation | Update kb/30_patterns/date_picker.md with new guidance |

---

## VALIDATION CHECKLIST

Before submitting a card, verify:
- [ ] `id` is unique (check `git ls-files docs/kb/40_cards/cards/ | grep <id>`)
- [ ] `reason_code` exists in `core/scenario/reasons.py` (canonical, not alias)
- [ ] All `evidence_required` keys are namespaced (`<ns>.<key>`)
- [ ] All `actions_allowed` tokens are in closed set above
- [ ] `risk` is one of: low, medium, high
- [ ] `confidence` is float 0.0-1.0
- [ ] `last_updated` matches format YYYY-MM-DD
- [ ] All 9 body sections present
- [ ] "When to use" describes exact trigger conditions
- [ ] "Best patch plan" has 2-4 PATCH-N entries (each ≤120 chars)
- [ ] "Anti-patterns" has 2-4 ❌ bullets
- [ ] No forbidden narrative words (recently, we, phase, eventually, later)
- [ ] `python -m utils.kb_cards_lint` passes
- [ ] All file paths in `code_refs` and `kb_links` are real and correct

---

See [docs/kb/template.md](template.md) for full schema and example card.
See [docs/kb/precommit_guide.md](precommit_guide.md) for local validation before commit.
