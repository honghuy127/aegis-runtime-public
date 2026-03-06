# Multilingual (i18n) Pattern: Japanese Focus

**Scope**: Locale-aware selector selection, OCR quality trade-offs, VLM language hints
**Defines**: Locale strategy rules, OCR quality heuristics, platform-specific language guidance
**Does NOT define**: Translation resources, per-service locale lists

---

## Problem

Flight booking sites vary in language support strategy:
- **US platforms** (Google Flights, United): Better OCR in English → use EN locale
- **Japanese platforms** (JAL, ANA): Native Japanese UI → use JA locale
- **Mixed platforms** (Kayak): Multi-locale support → try EN first for robustness

Blind locale switching fails when selectors/tokens are locale-specific.

---

## Solution: Platform-Aware Locale Strategy

**Rule**: Try EN locale first for US-origin platforms; use JA for native Japanese sites

```python
if service.origin == "us":
    locale = "en-US"  # Better OCR, more English selectors available
    language_hint_to_vlm = "en"
else:
    locale = "ja-JP"  # Native site; selectors in Japanese
    language_hint_to_vlm = "ja"
```

**Rationale**:
- EN locale: Better Chromium OCR engine (English training data)
- JA locale: Native JA selectors, tokens, attribute values required

---

## Locale Impact on Selectors

**English locale** (en-US):
- Buttons: `[aria-label='Search']`, `[aria-label='Depart']`
- Date format: "Mar 1, 2026" or "3/1/2026"
- Placeholders: "Where from?"

**Japanese locale** (ja-JP):
- Buttons: `[aria-label='検索']`, `[aria-label='出発日']`
- Date format: "2026年3月1日"
- Placeholders: "出発地"

---

## Selector Token Coverage

**MUST** provide both EN + JA token variants:

```json
{
  "date_opener": {
    "en": "[role='button'][aria-label*='Depart']",
    "ja": "[role='button'][aria-label*='出発']"
  },
  "search_button": {
    "en": "[aria-label='Search']",
    "ja": "[aria-label='検索']"
  }
}
```

**Fallback**:  If JA selector fails on JA platform, do NOT switch to EN blindly.
Fail with explicit reason; alert human for profile update.

---

## OCR Quality by Locale

| Content | EN OCR Quality | JA OCR Quality | Recommendation |
|---------|---|---|---|
| English text | Excellent | Poor | Use EN locale |
| Japanese text | Poor | Excellent | Use JA locale |
| Mixed text | Good | Fair | Use EN if possible |
| Prices (numbers) | Excellent | Excellent | Either OK |

**Decision**: If page content is EN-dominant, use EN locale even on JA platform.

---

## VLM Language Hints

Always provide language hint to Vision LLM:

```python
vlm_analyze_page(
    screenshot,
    language_hint="en",     # or "ja"
    confidence_threshold=0.8,
    focus_regions=["header", "search_results"]
)
```

**Impact**: VLM uses hint to adjust OCR/semantic understanding.

---

## Failure Modes

| Reason | When | Evidence keys | Action |
|--------|------|---------------|----|
| `locale_mismatch` | Selectors expect JA but page EN | `expected_lang`, `detected_lang`, `broken_selector` | Force correct locale or update profile |
| `ocr_low_quality` | VLM confidence < threshold on JA page | `language`, `confidence_score`, `region` | Try different locale or screenshot angle |
| `text_token_missing` | No JA token available for selector | `selector`, `available_tokens`, `platform` | Add JA variant to UI profile |

---

## Locale Rules (MUST / MUST NOT)

**MUST**:
- Provide EN + JA variants in UI profiles
- Pass language hint to VLM
- Test selectors on each locale before deployment

**MUST NOT**:
- Blind locale switching based on service name
- Use EN selectors on JA-only platforms
- Assume "platform language = primary language"

---

## Locale-Specific Date Formats

| Locale | Format | Selector strategy |
|--------|--------|-------------------|
| en-US | "Mar 1, 2026" or "3/1/2026" | Text matching fragile; use role-based |
| en-GB | "1 Mar 2026" or "1/3/2026" | Day-first; different selector |
| ja-JP | "2026年3月1日" or "2026/3/1" | Kanji-aware; use hiragana variants |

**Strategy**:
- Prefer role-based calendar access (role="button" + aria-label)
- Avoid brittle text-based date matching
- Use locale-aware verification patterns

---

## Evidence Keys (from evidence_catalog.yaml)

- `i18n.locale_selected`
- `i18n.locale_ocr_quality`
- `i18n.language_hint_provided`
- `i18n.text_token`
- `i18n.selector_variant_used`

---

## Related

- [Selector Pattern](selectors.md)
- [Evidence Catalog](../10_runtime_contracts/evidence_catalog.yaml)
- Code: `core/browser/` (session, combobox, wait), `core/scenario/google_flights.py`
- Tests: `tests/test_browser_google_flights_combobox.py`
