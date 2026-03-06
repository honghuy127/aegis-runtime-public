"""Stage-2 adapter parity tests against existing implementations."""

import pytest

from core import services as services_mod
from core.plugins.adapters.extraction_adapter import (
    HtmlLLMExtractionStrategy,
    VLMImageExtractionStrategy,
    VLMMultimodalExtractionStrategy,
)
from core.plugins.adapters.llm_provider_adapter import OllamaLLMProvider
from core.plugins.adapters import services_adapter
from core.plugins.adapters.services_adapter import ExistingServicePlugin
from llm import code_model

pytestmark = [pytest.mark.llm, pytest.mark.vlm, pytest.mark.heavy]


def test_service_adapter_delegates_url_candidate_ordering():
    """Service adapter should preserve core.services URL ordering behavior."""
    adapter = ExistingServicePlugin(key="google_flights")
    direct = services_mod.service_url_candidates(
        "google_flights",
        preferred_url="https://www.google.com/travel/flights",
        is_domestic=True,
        knowledge={"site_type": "single_flow"},
        seed_hints={"generic": [], "domestic": [], "international": []},
    )
    via_adapter = adapter.url_candidates(
        preferred_url="https://www.google.com/travel/flights",
        is_domestic=True,
        knowledge={"site_type": "single_flow"},
        seed_hints={"generic": [], "domestic": [], "international": []},
    )
    assert via_adapter == direct


def test_html_extraction_adapter_matches_direct(monkeypatch):
    """HTML strategy should return exactly what direct parser returns."""
    expected = {
        "price": 12345.0,
        "currency": "JPY",
        "confidence": "high",
        "selector_hint": None,
        "site": "google_flights",
        "task": "price",
        "reason": "",
    }
    monkeypatch.setattr(code_model, "parse_html_with_llm", lambda **kwargs: expected)
    strategy = HtmlLLMExtractionStrategy()
    result = strategy.extract(
        html="<html></html>",
        screenshot_path=None,
        context={"site": "google_flights", "task": "price", "timeout_sec": 60},
    )
    direct = code_model.parse_html_with_llm(
        html="<html></html>",
        site="google_flights",
        task="price",
        timeout_sec=60,
    )
    assert result == direct


def test_vlm_image_extraction_adapter_matches_direct(monkeypatch):
    """VLM image strategy should delegate to direct image parser."""
    expected = {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "site": "google_flights",
        "task": "price",
        "reason": "price_not_found",
        "source": "vlm",
    }
    monkeypatch.setattr(code_model, "parse_image_with_vlm", lambda *args, **kwargs: expected)
    strategy = VLMImageExtractionStrategy()
    result = strategy.extract(
        html="<html></html>",
        screenshot_path="/tmp/a.png",
        context={"site": "google_flights", "task": "price"},
    )
    direct = code_model.parse_image_with_vlm(
        "/tmp/a.png",
        site="google_flights",
        task="price",
    )
    assert result == direct


def test_vlm_multimodal_adapter_matches_direct(monkeypatch):
    """Multimodal strategy should delegate to direct multimodal parser."""
    expected = {
        "price": 22222.0,
        "currency": "JPY",
        "confidence": "medium",
        "selector_hint": None,
        "site": "google_flights",
        "task": "price",
        "source": "vlm_multimodal",
        "reason": "price_found",
    }
    monkeypatch.setattr(code_model, "parse_page_multimodal_with_vlm", lambda **kwargs: expected)
    strategy = VLMMultimodalExtractionStrategy()
    result = strategy.extract(
        html="<html></html>",
        screenshot_path="/tmp/a.png",
        context={"site": "google_flights", "task": "price"},
    )
    direct = code_model.parse_page_multimodal_with_vlm(
        image_path="/tmp/a.png",
        html="<html></html>",
        site="google_flights",
        task="price",
    )
    assert result == direct


def test_llm_provider_adapter_delegates_to_call_llm(monkeypatch):
    """Provider adapter should transparently forward args to call_llm."""
    from core.plugins.adapters import llm_provider_adapter as provider_mod

    monkeypatch.setattr(
        provider_mod,
        "call_llm",
        lambda prompt, model, **kwargs: f"{model}:{prompt}:{kwargs.get('timeout_sec', 0)}",
    )
    provider = OllamaLLMProvider()
    out = provider.call("ping", "qwen3:8b", timeout_sec=12)
    assert out == "qwen3:8b:ping:12"


def test_readiness_probe_router_falls_back_when_flag_off(monkeypatch):
    """Router should no-op when global plugin strategy switch is disabled."""
    monkeypatch.setenv("FLIGHT_WATCHER_DISABLE_PLUGINS", "true")
    out = services_adapter.run_service_readiness_probe(
        "google_flights",
        html="<html></html>",
        screenshot_path=None,
        inputs={},
    )
    assert out == {}


def test_readiness_probe_router_falls_back_when_plugin_missing(monkeypatch):
    """Router should no-op when plugin lookup fails under plugin mode."""
    import core.plugins.registry as registry_mod

    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")

    def _raise_missing(_key):
        raise KeyError("missing")

    monkeypatch.setattr(registry_mod, "get_service", _raise_missing)
    out = services_adapter.run_service_readiness_probe(
        "unknown_service",
        html="<html></html>",
        screenshot_path=None,
        inputs={},
    )
    assert out == {}
