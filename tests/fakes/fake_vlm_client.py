"""Fake VLM client for deterministic contract testing.

Provides canned responses for:
- Successful price extraction
- Non-flight scope detection
- Package bundle detection
- Route binding validation
"""

from typing import Dict, Any, Optional


class FakeVLMClient:
    """Deterministic VLM responses for contract testing."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        """
        Args:
            responses: Dict mapping call types to responses.
                      If None, uses default successful extraction.
        """
        self.responses = responses or {}
        self.call_count = 0
        self.last_image_path = None
        self.last_prompt = None

    def parse_image_with_vlm(
        self,
        image_path: str,
        prompt: str = "Extract price",
        **kwargs
    ) -> Dict[str, Any]:
        """Fake parse_image_with_vlm call."""
        self.call_count += 1
        self.last_image_path = image_path
        self.last_prompt = prompt

        # Check for specific response override
        if "price_extraction" in self.responses:
            return self.responses["price_extraction"]

        # Default: successful price extraction
        return {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "source": "vlm",
            "reason": "vlm_success",
        }

    def analyze_page_ui_with_vlm(
        self,
        image_path: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Fake analyze_page_ui_with_vlm call for scope detection."""
        self.call_count += 1
        self.last_image_path = image_path

        # Check for specific response override
        if "scope_analysis" in self.responses:
            return self.responses["scope_analysis"]

        # Default: flight search page
        return {
            "scope": "flight_search",
            "confidence": "high",
            "reason": "vlm_scope_success",
        }

    def analyze_filled_route_with_vlm(
        self,
        image_path: str,
        origin: str,
        dest: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Fake analyze_filled_route_with_vlm for route binding."""
        self.call_count += 1
        self.last_image_path = image_path

        # Check for specific response override
        if "route_binding" in self.responses:
            return self.responses["route_binding"]

        # Default: route match confirmed
        return {
            "route_match": True,
            "visible_origin": origin,
            "visible_dest": dest,
            "confidence": "high",
            "reason": "vlm_route_success",
        }

    def reset(self):
        """Reset call tracking."""
        self.call_count = 0
        self.last_image_path = None
        self.last_prompt = None


# Canned response templates
NON_FLIGHT_SCOPE_RESPONSE = {
    "scope": "package_bundle",
    "confidence": "high",
    "reason": "vlm_non_flight_detected",
}

ROUTE_MISMATCH_RESPONSE = {
    "route_match": False,
    "visible_origin": "TYO",
    "visible_dest": "OSA",
    "confidence": "high",
    "reason": "vlm_route_mismatch",
}

MISSING_PRICE_RESPONSE = {
    "price": None,
    "currency": None,
    "confidence": "low",
    "reason": "price_not_visible",
}


def make_fake_vlm_non_flight_scope():
    """Create fake VLM that detects non-flight scope."""
    return FakeVLMClient(responses={"scope_analysis": NON_FLIGHT_SCOPE_RESPONSE})


def make_fake_vlm_route_mismatch():
    """Create fake VLM that detects route mismatch."""
    return FakeVLMClient(responses={"route_binding": ROUTE_MISMATCH_RESPONSE})


def make_fake_vlm_missing_price():
    """Create fake VLM that cannot find price."""
    return FakeVLMClient(responses={"price_extraction": MISSING_PRICE_RESPONSE})


def make_fake_vlm_successful():
    """Create fake VLM that returns successful extraction."""
    return FakeVLMClient()


# Monkeypatch helpers
def patch_parse_image_with_vlm(monkeypatch, response: Dict[str, Any]):
    """Monkeypatch parse_image_with_vlm with canned response."""
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, prompt, **kwargs: response,
    )


def patch_analyze_page_ui_with_vlm(monkeypatch, response: Dict[str, Any]):
    """Monkeypatch analyze_page_ui_with_vlm with canned response."""
    monkeypatch.setattr(
        "core.extractor.analyze_page_ui_with_vlm",
        lambda image_path, **kwargs: response,
    )


def patch_analyze_filled_route_with_vlm(monkeypatch, response: Dict[str, Any]):
    """Monkeypatch analyze_filled_route_with_vlm with canned response."""
    monkeypatch.setattr(
        "core.extractor.analyze_filled_route_with_vlm",
        lambda image_path, origin, dest, **kwargs: response,
    )
