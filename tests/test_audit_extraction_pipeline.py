"""Audit tests for plugin extraction router correctness and fallback safety."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from core import extractor as ex
from core.plugins import runtime_extraction as rx
from core.plugins.extraction.normalize import normalize_plugin_candidate

pytestmark = [pytest.mark.llm, pytest.mark.vlm]


def _legacy_payload(price: float = 54321.0) -> Dict[str, Any]:
    return {
        "price": price,
        "currency": "JPY",
        "confidence": "low",
        "selector_hint": None,
        "source": "legacy",
        "reason": "legacy_fallback",
    }


def test_audit_strategy_key_env_precedence(monkeypatch):
    """Env strategy key must override threshold fallback and persist in metadata."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "vlm_image")

    seen = {"key": ""}

    class _Strategy:
        key = "vlm_image"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 321.0,
                "currency": "jpy",
                "confidence": "high",
                "reason": "ok",
            }

    def _get_strategy(key: str):
        seen["key"] = key
        return _Strategy()

    monkeypatch.setattr(rx, "get_strategy_plugin", _get_strategy)
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)

    out = rx.run_plugin_extraction_router(
        html="<html></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
        is_domestic=True,
        screenshot_path=None,
        page_url=None,
        existing_scope_guard_fn=lambda candidate: dict(candidate),
        thresholds_getter=lambda key, default: "html_llm" if key == "extract_strategy_plugin_key" else default,
    )

    assert seen["key"] == "vlm_image"
    assert out.get("price") == 321.0
    assert out.get("strategy_key") == "vlm_image"


def test_audit_plugin_success_short_circuits_legacy(monkeypatch):
    """Accepted plugin output should not execute legacy extraction."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    plugin_payload = {
        "price": 22222.0,
        "currency": "JPY",
        "confidence": "high",
        "selector_hint": None,
        "source": "plugin_html_llm",
        "reason": "price_found",
        "scope_guard": "pass",
        "scope_guard_basis": "deterministic",
        "confidence_score": 0.9,
    }
    monkeypatch.setattr(ex, "run_plugin_extraction_router", lambda **kwargs: dict(plugin_payload))
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy path should not run")),
    )

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out == plugin_payload


def test_audit_scope_guard_runs_once(monkeypatch):
    """Router acceptance should run supplied scope guard exactly once."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "html_llm")

    class _Strategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 9000.0,
                "currency": "JPY",
                "confidence": "medium",
                "reason": "price_found",
            }

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)

    calls = {"count": 0}

    def _scope_guard(candidate: Dict[str, Any]) -> Dict[str, Any]:
        calls["count"] += 1
        return dict(candidate)

    out = rx.run_plugin_extraction_router(
        html="<html></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type=None,
        is_domestic=None,
        screenshot_path=None,
        page_url=None,
        existing_scope_guard_fn=_scope_guard,
        thresholds_getter=lambda _k, d: d,
    )

    assert calls["count"] == 1
    assert out.get("price") == 9000.0


def test_audit_plugin_rejection_calls_legacy_once(monkeypatch):
    """Rejected plugin candidate should trigger exactly one legacy extraction."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "html_llm")

    class _Strategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 12345.0,
                "currency": "JPY",
                "confidence": "high",
                "page_class": "flight_hotel_package",
            }

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)

    calls = {"legacy": 0}
    expected = _legacy_payload(price=11111.0)

    def _legacy(**kwargs):  # noqa: ARG001
        calls["legacy"] += 1
        return dict(expected)

    monkeypatch.setattr(ex, "extract_with_llm", _legacy)
    out = ex.extract_price("<html></html>", site="google_flights")

    assert calls["legacy"] == 1
    assert out == expected


def test_audit_malformed_plugin_payload_falls_closed(monkeypatch):
    """Malformed plugin payload must not bypass fallback path."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    class _Strategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "confidence": "VERY_HIGH",
                "random": "field",
                "payload": {"nested": "shape-drift"},
            }

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)

    calls = {"legacy": 0}
    expected = _legacy_payload(price=22222.0)

    def _legacy(**kwargs):  # noqa: ARG001
        calls["legacy"] += 1
        return dict(expected)

    monkeypatch.setattr(ex, "extract_with_llm", _legacy)
    out = ex.extract_price("<html></html>", site="google_flights")

    assert calls["legacy"] == 1
    assert out == expected


def test_audit_strategy_exception_never_escapes(monkeypatch):
    """Plugin strategy exceptions should soft-fail to legacy path."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    class _BrokenStrategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _BrokenStrategy())
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)
    expected = _legacy_payload(price=33333.0)
    monkeypatch.setattr(ex, "extract_with_llm", lambda **kwargs: dict(expected))

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out == expected


def test_audit_enum_drift_normalized_and_rejected(monkeypatch):
    """Invalid enum labels should normalize and be rejected to legacy fallback."""
    normalized = normalize_plugin_candidate(
        {
            "price": 25986.0,
            "currency": "jpy",
            "confidence": "VERY_HIGH",
            "page_class": "flights",
            "trip_product": "package",
        },
        strategy_key="html_llm",
    )
    assert normalized["confidence"] == "low"
    assert normalized["page_class"] == "unknown"
    assert normalized["trip_product"] == "unknown"

    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    class _Strategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 25986.0,
                "currency": "jpy",
                "confidence": "VERY_HIGH",
                "page_class": "flights",
                "trip_product": "package",
                "reason": "enum_drift",
            }

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())
    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: None)

    calls = {"legacy": 0}
    expected = _legacy_payload(price=44444.0)

    def _legacy(**kwargs):  # noqa: ARG001
        calls["legacy"] += 1
        return dict(expected)

    monkeypatch.setattr(ex, "extract_with_llm", _legacy)
    out = ex.extract_price("<html></html>", site="google_flights")

    assert calls["legacy"] == 1
    assert out == expected


def test_audit_cross_service_hints_are_ignored(monkeypatch):
    """Extraction hints from a different service key must not influence acceptance."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "html_llm")

    class _ServicePlugin:
        def extraction_hints(self, html, screenshot_path=None, inputs=None):  # noqa: ANN001, ARG002
            return {
                "service_key": "skyscanner",
                "trusted_container": "skyscanner-only",
            }

    seen = {"hints": None}

    class _Strategy:
        key = "html_llm"

        def strategy_key(self) -> str:
            return self.key

        def extract(self, **kwargs):
            hints = dict(kwargs.get("context", {}).get("extraction_hints", {}) or {})
            seen["hints"] = hints
            if hints.get("trusted_container"):
                return {
                    "price": 7777.0,
                    "currency": "JPY",
                    "confidence": "high",
                    "reason": "hint_leak",
                }
            return {}

    monkeypatch.setattr(rx, "get_runtime_service_plugin", lambda _site: _ServicePlugin())
    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())

    out = rx.run_plugin_extraction_router(
        html="<html></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type=None,
        is_domestic=True,
        screenshot_path=None,
        page_url=None,
        existing_scope_guard_fn=lambda candidate: dict(candidate),
        thresholds_getter=lambda _k, d: d,
    )

    assert seen["hints"] == {}
    assert out == {}


def test_audit_semantic_chunk_builder_runs_once_per_attempt(monkeypatch):
    """Semantic chunk build should be memoized during one extraction attempt."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "false")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "false")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_SEMANTIC_CHUNK_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "false")
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "off")

    calls = {"chunks": 0}

    def _chunks(*args, **kwargs):  # noqa: ARG001
        calls["chunks"] += 1
        return [{"html": "<div>chunk-1</div>"}, {"html": "<div>chunk-2</div>"}]

    def _heuristics(**kwargs):  # noqa: ARG001
        return None

    def _parse(html, site, task, timeout_sec=None):  # noqa: ARG001
        if "chunk-1" in html:
            return {
                "price": 13579.0,
                "currency": "JPY",
                "confidence": "low",
                "selector_hint": None,
                "reason": "chunk_price_found",
            }
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        }

    monkeypatch.setattr(ex, "_semantic_html_chunks", _chunks)
    monkeypatch.setattr(ex, "_extract_with_heuristics", _heuristics)
    monkeypatch.setattr(ex, "parse_html_with_llm", _parse)

    out = ex.extract_price(
        "<html><body>x</body></html>",
        site="unknown",
        task="price",
    )

    assert calls["chunks"] == 1
    assert out.get("price") == 13579.0
