"""Prompt templates for VLM and LLM coordination.

VLM prompt: Analyze page structure → produce UiSnapshot JSON
LLM prompt: Parse DomSlice HTML → extract price candidates

Both prompts are designed to work with the coordination layer contracts.
"""

from typing import Dict, Any, Optional


# ============================================================================
# VLM PROMPT TEMPLATE
# ============================================================================

VLM_PROMPT_UI_SNAPSHOT = """You are a web UI analyzer. Analyze the page structure and produce a JSON snapshot.

TASK: Extract semantic UI structure without full HTML transcription.

OUTPUT FORMAT (required JSON):
{
  "page_kind": "<one of: search_results, price_details, checkout, confirmation, error, unknown>",
  "confidence": <0.0-1.0 float>,
  "anchors": {
    "<semantic_name>": "<CSS_selector_or_location>",
    ...
  },
  "route_form_state": {
    "origin": "<IATA or location>",
    "dest": "<IATA or location>",
    "depart": "<date YYYY-MM-DD>",
    "return": "<date YYYY-MM-DD or null>",
    ...
  },
  "ui_tokens": ["<extracted_text>", ...]
}

RULES:
1. page_kind: Classify the page type based on visual structure
2. confidence: 0.95 = very confident, 0.5 = ambiguous, 0.1 = uncertain
3. anchors: Map semantic regions to CSS selectors (e.g., "price_container": ".fare-box")
4. route_form_state: Extract visible route parameters if present
5. ui_tokens: List 3-5 important text tokens seen (prices, dates, labels)
6. Skip full HTML transcription; focus on structure and selectors only

ANCHOR EXAMPLES:
- "price_box": "div.price-amount"
- "trip_card": "li[data-trip-id]"
- "header": "header.navbar"
- "search_form": "form.search-panel"

COMMON PAGE KINDS:
- search_results: Multiple trip cards with prices
- price_details: Detailed view of single trip
- checkout: Payment/booking confirmation step
- confirmation: Order successful view
- error: Error or no-results message
- unknown: Cannot classify
"""

# ============================================================================
# LLM PROMPT TEMPLATE FOR DOM SLICE
# ============================================================================

LLM_PROMPT_PRICE_EXTRACTION = """You are a flight price extraction expert. Parse the HTML snippet and extract prices.

TASK: Find all price values and their context from the DOM fragment.

INPUT: Compact HTML fragment (DomSlice) containing price information.

OUTPUT FORMAT (required JSON):
{
  "candidates": [
    {
      "price": <numeric_value>,
      "currency": "<ISO_4217 code>",
      "context": "<surrounding text>",
      "selector_hint": "<CSS selector used to extract>",
      "confidence": <0.0-1.0>
    }
  ],
  "extracted_at": "<ISO timestamp>",
  "note": "<optional parsing notes>"
}

EXTRACTION RULES:
1. Extract NUMBERS that look like prices (e.g., 150, $250.00, 199,99)
2. Infer currency from context ($ = USD, € = EUR, £ = GBP, etc.)
3. Skip numbers that aren't prices (IDs, dates, quantities)
4. For each price, note surrounding text for disambiguation
5. Confidence = 0.9 if clear context, 0.5 if ambiguous, 0.1 if guessing
6. Output all candidates; let caller (router) decide best match

CONTEXT SIGNALS:
- Price in "Total", "Price", "Fare" label nearby = high confidence
- Multiple price candidates = list all with confidence scores
- Currency symbol or code nearby = include in output
- Price in heading or card title = high confidence

EXAMPLE OUTPUT:
{
  "candidates": [
    {
      "price": 250.00,
      "currency": "USD",
      "context": "Price: $250.00 roundtrip",
      "selector_hint": ".fare-amount",
      "confidence": 0.95
    }
  ],
  "extracted_at": "2026-02-21T10:30:00Z",
  "note": "Single clear price found"
}
"""

# ============================================================================
# ROUTING INTEGRATION HELPER
# ============================================================================


def build_vlm_prompt_for_page(html_snippet: str, max_chars: int = 5000) -> str:
    """Build VLM prompt with page content.

    Args:
        html_snippet: Page HTML (will be truncated)
        max_chars: Maximum characters to include

    Returns:
        Complete prompt for VLM model
    """
    truncated = html_snippet[:max_chars] if len(html_snippet) > max_chars else html_snippet
    return f"""{VLM_PROMPT_UI_SNAPSHOT}

PAGE CONTENT:
```html
{truncated}
```

Analyze and output JSON snapshot only (no preamble)."""


def build_llm_prompt_for_dom_slice(dom_slice) -> str:  # type: ignore
    """Build LLM prompt with DomSlice content.

    Args:
        dom_slice: Compact DOM fragment from coordinator

    Returns:
        Complete prompt for LLM model
    """
    anchors_text = ""
    if hasattr(dom_slice, 'anchors') and dom_slice.anchors:
        anchors_text = "\n\nAVAILABLE ANCHORS (from VLM analysis):\n"
        for name, selector in dom_slice.anchors.items():
            anchors_text += f"- {name}: {selector}\n"

    selector_used = getattr(dom_slice, 'selector_used', 'unknown')
    html = getattr(dom_slice, 'html', '')

    return f"""{LLM_PROMPT_PRICE_EXTRACTION}

EXTRACTED DOM FRAGMENT (selector: {selector_used}):
```html
{html}
```
{anchors_text}

Extract prices from this fragment and output JSON only (no preamble)."""


def parse_vlm_response(response_json: str) -> Optional[Dict[str, Any]]:
    """Parse VLM JSON response into UiSnapshot.

    Args:
        response_json: Raw JSON string from VLM

    Returns:
        Dict suitable for UiSnapshot construction or None if invalid
    """
    import json
    try:
        data = json.loads(response_json)
        # Validate required fields
        if not isinstance(data.get("page_kind"), str):
            return None
        if not isinstance(data.get("confidence"), (int, float)):
            return None
        return data
    except (json.JSONDecodeError, ValueError):
        return None


def parse_llm_response(response_json: str) -> Optional[Dict[str, Any]]:
    """Parse LLM JSON response into price candidates.

    Args:
        response_json: Raw JSON string from LLM

    Returns:
        Dict with candidates list or None if invalid
    """
    import json
    try:
        data = json.loads(response_json)
        # Validate required fields
        if not isinstance(data.get("candidates"), list):
            return None
        for candidate in data["candidates"]:
            if not isinstance(candidate.get("price"), (int, float)):
                return None
        return data
    except (json.JSONDecodeError, ValueError):
        return None
