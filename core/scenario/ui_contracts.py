"""Shared data contracts for VLM/LLM model coordination.

Enforces role separation:
- VLM: Produces UiSnapshot (page_kind, confidence, anchors, UI metadata)
- LLM: Consumes DomSlice (compact HTML fragment with context)
- Router: Gates calls based on route match, produces DomSlice from anchors
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class UiSnapshot:
    """Output from VLM analysis of UI state.

    Captures semantic understanding of page without full HTML.
    Used to guide DOM slicing and anchor selection.
    """

    page_kind: str
    """Page classification (search_results, details, cart, etc)"""

    confidence: float
    """0.0-1.0 confidence in page_kind classification"""

    anchors: Dict[str, Any] = field(default_factory=dict)
    """Semantic anchors: {anchor_name: selector_or_locator}
    Examples:
    - "price_region": "div.price-container"
    - "trip_card": "li[data-trip-id]"
    - "header": "header.main-nav"
    """

    route_form_state: Optional[Dict[str, str]] = None
    """Detected route form values: {origin, dest, depart, return, etc}"""

    ui_tokens: Optional[List[str]] = None
    """Raw text tokens extracted from UI for disambiguation"""

    evidence: Dict[str, Any] = field(default_factory=dict)
    """Diagnostic context:
    - ui.snapshot.received_at (ISO timestamp)
    - ui.snapshot.model_version (VLM model used)
    - ui.snapshot.processing_time_ms
    - ui.snapshot.selector_count
    - ui.snapshot.token_count
    """

    def validate(self) -> Optional[str]:
        """Return error string if invalid; None if valid."""
        if not self.page_kind or not isinstance(self.page_kind, str):
            return "page_kind must be non-empty string"
        if not isinstance(self.confidence, (int, float)):
            return "confidence must be number"
        if self.confidence < 0.0 or self.confidence > 1.0:
            return "confidence must be in [0.0, 1.0]"
        if self.anchors is not None and not isinstance(self.anchors, dict):
            return "anchors must be dict or None"
        if self.route_form_state is not None and not isinstance(self.route_form_state, dict):
            return "route_form_state must be dict or None"
        if self.ui_tokens is not None and not isinstance(self.ui_tokens, list):
            return "ui_tokens must be list or None"
        return None


@dataclass
class DomSlice:
    """Compact DOM fragment for LLM analysis.

    Contains only relevant HTML + context needed for price extraction,
    avoiding full-document overhead.
    """

    html: str
    """Extracted HTML fragment (text/plain or HTML subset)"""

    selector_used: str
    """Selector/strategy used to extract: 'price_container', 'card_body', etc"""

    text_len: int
    """Character count of HTML content"""

    node_count: int
    """Approximate node count (from parsing or estimation)"""

    anchors: Optional[Dict[str, str]] = None
    """Context anchors from UiSnapshot (selector_name -> CSS selector)
    Available to LLM for DOM navigation hints"""

    evidence: Dict[str, Any] = field(default_factory=dict)
    """Diagnostic context:
    - domslice.extracted_at (ISO timestamp)
    - domslice.selector_strategy (how selector was chosen)
    - domslice.text_chars (actual char count)
    - domslice.node_estimate (estimated node count)
    - domslice.anchor_count (how many anchors provided)
    - domslice.skip_reason (if slice empty: why skipped)
    """

    def validate(self) -> Optional[str]:
        """Return error string if invalid; None if valid."""
        if not isinstance(self.html, str):
            return "html must be string"
        if not isinstance(self.selector_used, str):
            return "selector_used must be string"
        if not isinstance(self.text_len, int) or self.text_len < 0:
            return "text_len must be non-negative int"
        if not isinstance(self.node_count, int) or self.node_count < 0:
            return "node_count must be non-negative int"
        if self.anchors is not None and not isinstance(self.anchors, dict):
            return "anchors must be dict or None"
        return None

    @property
    def is_empty(self) -> bool:
        """True if slice is empty or too small."""
        return self.text_len < 10 or self.node_count == 0

    @property
    def is_oversized(self) -> bool:
        """True if slice exceeds default size cap (50000 chars)."""
        return self.text_len > 50000


def validate_ui_snapshot(data: Any) -> Optional[str]:
    """Validate raw UiSnapshot data from VLM JSON.

    Args:
        data: Parsed JSON from VLM output

    Returns:
        Error string if invalid; None if valid and can construct UiSnapshot
    """
    if not isinstance(data, dict):
        return "UiSnapshot must be dict"

    required_fields = {"page_kind", "confidence"}
    if not all(key in data for key in required_fields):
        missing = required_fields - set(data.keys())
        return f"Missing required fields: {missing}"

    snapshot = UiSnapshot(
        page_kind=data.get("page_kind", ""),
        confidence=data.get("confidence", 0.0),
        anchors=data.get("anchors"),
        route_form_state=data.get("route_form_state"),
        ui_tokens=data.get("ui_tokens"),
        evidence=data.get("evidence", {}),
    )
    return snapshot.validate()


def validate_dom_slice(data: Any) -> Optional[str]:
    """Validate data for DomSlice construction.

    Args:
        data: Dict with dom_slice fields

    Returns:
        Error string if invalid; None if valid
    """
    if not isinstance(data, dict):
        return "DomSlice must be dict"

    required_fields = {"html", "selector_used", "text_len", "node_count"}
    if not all(key in data for key in required_fields):
        missing = required_fields - set(data.keys())
        return f"Missing required fields: {missing}"

    slice_obj = DomSlice(
        html=data.get("html", ""),
        selector_used=data.get("selector_used", ""),
        text_len=data.get("text_len", 0),
        node_count=data.get("node_count", 0),
        anchors=data.get("anchors"),
        evidence=data.get("evidence", {}),
    )
    return slice_obj.validate()
