"""Fake LLM client for deterministic contract testing.

Provides canned responses for:
- Successful extraction
- Missing price path
- Invalid schema path
- Circuit-open / timeout simulation
"""

from typing import Dict, Any, Optional


class FakeLLMClient:
    """Deterministic LLM responses for contract testing."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        """
        Args:
            responses: Dict mapping call signatures to responses.
                      If None, uses default successful extraction.
        """
        self.responses = responses or {}
        self.call_count = 0
        self.last_html = None
        self.last_site = None
        self.last_task = None

    def parse_html_with_llm(
        self,
        html: str,
        site: str = "google_flights",
        task: str = "price",
        **kwargs
    ) -> Dict[str, Any]:
        """Fake parse_html_with_llm call."""
        self.call_count += 1
        self.last_html = html
        self.last_site = site
        self.last_task = task

        # Check for specific response override
        key = f"{site}:{task}"
        if key in self.responses:
            return self.responses[key]

        # Default: successful extraction
        return {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": "div.price-display",
            "reason": "llm_success",
        }

    def reset(self):
        """Reset call tracking."""
        self.call_count = 0
        self.last_html = None
        self.last_site = None
        self.last_task = None


# Canned response templates
MISSING_PRICE_RESPONSE = {
    "price": None,
    "currency": None,
    "confidence": "low",
    "selector_hint": None,
    "reason": "price_not_found",
}

INVALID_SCHEMA_RESPONSE = {
    "malformed": True,
    "error": "invalid_json",
}

CIRCUIT_OPEN_RESPONSE = {
    "price": None,
    "currency": None,
    "confidence": "low",
    "selector_hint": None,
    "reason": "circuit_open",
}

TIMEOUT_RESPONSE = {
    "price": None,
    "currency": None,
    "confidence": "low",
    "selector_hint": None,
    "reason": "timeout",
}


def make_fake_llm_missing_price():
    """Create fake client that simulates LLM missing price."""
    return FakeLLMClient(responses={"google_flights:price": MISSING_PRICE_RESPONSE})


def make_fake_llm_circuit_open():
    """Create fake client that simulates circuit breaker open."""
    return FakeLLMClient(responses={"google_flights:price": CIRCUIT_OPEN_RESPONSE})


def make_fake_llm_timeout():
    """Create fake client that simulates timeout."""
    return FakeLLMClient(responses={"google_flights:price": TIMEOUT_RESPONSE})


def make_fake_llm_successful():
    """Create fake client that returns successful extraction."""
    return FakeLLMClient()


# Monkeypatch helper
def patch_parse_html_with_llm(monkeypatch, response: Dict[str, Any]):
    """Monkeypatch parse_html_with_llm with canned response."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, **kwargs: response,
    )
