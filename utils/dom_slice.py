"""DOM fragment slicing for LLM consumption.

Extracts minimal HTML needed for price parsing, with size caps
and priority-based selector logic.
"""

import re
from typing import Optional, Dict, Any, List
from core.scenario.ui_contracts import DomSlice


# Selector priority: earlier = preferred
SELECTOR_PRIORITY = [
    "price_container",
    "price_card",
    ".price",
    "[data-price]",
    "[class*='price']",
    ".fare",
    ".cost",
    ".total",
    "[class*='total']",
    ".itinerary",
    "[data-result]",
    ".trip-item",
    "article",
    ".search-result",
]


def build_dom_slice(
    html: str,
    selectors_priority: Optional[List[str]] = None,
    max_chars: int = 20000,
    max_nodes: int = 500,
) -> DomSlice:
    """Build compact DOM slice for LLM price extraction.

    Applies selectors in priority order, returns first match.
    Falls back to full HTML (capped) if no selector matches.

    Args:
        html: Full page HTML
        selectors_priority: List of CSS selectors to try (in order)
        max_chars: Character cap for output HTML
        max_nodes: Estimated node cap (based on tag count)

    Returns:
        DomSlice with extracted HTML + evidence
    """
    if not html or not isinstance(html, str):
        return DomSlice(
            html="",
            selector_used="none",
            text_len=0,
            node_count=0,
            evidence={"domslice.skip_reason": "empty_or_invalid_input"},
        )

    selectors = selectors_priority or SELECTOR_PRIORITY
    evidence: Dict[str, Any] = {
        "domslice.selector_count": len(selectors),
        "domslice.max_chars": max_chars,
        "domslice.max_nodes": max_nodes,
    }

    # Try each selector in priority order
    for selector in selectors:
        extracted = _extract_by_selector(html, selector)
        if extracted and len(extracted) > 50:  # Minimum viable slice
            capped = extracted[:max_chars] if len(extracted) > max_chars else extracted
            node_count = _estimate_node_count(capped)

            evidence.update({
                "domslice.selector_used": selector,
                "domslice.selector_index": selectors.index(selector),
                "domslice.extraction_strategy": "css_selector",
                "domslice.text_chars": len(capped),
                "domslice.node_estimate": node_count,
            })

            if len(extracted) > max_chars:
                evidence["domslice.truncated"] = True
                evidence["domslice.original_chars"] = len(extracted)

            return DomSlice(
                html=capped,
                selector_used=selector,
                text_len=len(capped),
                node_count=node_count,
                evidence=evidence,
            )

    # Fallback: use full HTML (capped)
    capped_html = html[:max_chars] if len(html) > max_chars else html
    node_count = _estimate_node_count(capped_html)

    evidence.update({
        "domslice.selector_used": "none",
        "domslice.extraction_strategy": "fallback_full_html",
        "domslice.text_chars": len(capped_html),
        "domslice.node_estimate": node_count,
    })

    if len(html) > max_chars:
        evidence["domslice.truncated"] = True
        evidence["domslice.original_chars"] = len(html)

    return DomSlice(
        html=capped_html,
        selector_used="none",
        text_len=len(capped_html),
        node_count=node_count,
        evidence=evidence,
    )


def _extract_by_selector(html: str, selector: str) -> str:
    """Naive DOM extraction by selector pattern.

    Uses regex to find elements matching CSS-like selector.
    Handles class, id, tag, attribute patterns.

    Args:
        html: HTML string
        selector: CSS selector pattern

    Returns:
        Extracted HTML fragment or empty string
    """
    if not selector or not html:
        return ""

    # Class selector: .class-name
    if selector.startswith("."):
        class_name = selector[1:]
        pattern = rf'<[^>]*class="[^"]*{re.escape(class_name)}[^"]*"[^>]*>.*?</[^>]+>'
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0)

    # ID selector: #id-name
    if selector.startswith("#"):
        id_name = selector[1:]
        pattern = rf'<[^>]*id="[^"]*{re.escape(id_name)}[^"]*"[^>]*>.*?</[^>]+>'
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0)

    # Attribute selector: [attr='value'] or [attr]
    if selector.startswith("[") and selector.endswith("]"):
        attr_pattern = selector[1:-1]
        # Match elements with this attribute
        pattern = rf'<[^>]*{re.escape(attr_pattern)}[^>]*>.*?</[^>]+>'
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0)

    # Attribute substring: [class*='substring']
    if "[class*=" in selector or "[data-" in selector:
        # Extract the value inside quotes
        attr_match = re.search(r"\[([^=]+)=\s*['\"]?([^'\"]+)['\"]?\]", selector)
        if attr_match:
            attr_name, attr_value = attr_match.groups()
            pattern = rf'<[^>]*{re.escape(attr_name)}=["\']?[^"\']*{re.escape(attr_value)}[^"\']*["\']?[^>]*>.*?</[^>]+>'
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(0)

    # Tag selector: div, article, li, etc
    if selector.isalpha() or selector == "article":
        pattern = rf'<{selector}[^>]*>.*?</{selector}>'
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0)

    return ""


def _estimate_node_count(html: str) -> int:
    """Estimate node count by counting tags.

    Args:
        html: HTML fragment

    Returns:
        Estimated count of HTML nodes
    """
    if not html:
        return 0
    # Count opening tags as proxy for nodes
    tag_count = len(re.findall(r"<[^/!][^>]*>", html))
    return max(1, tag_count)
