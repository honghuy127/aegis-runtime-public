"""Feature-flagged plugin extraction router behavior tests."""

from core import extractor as ex
from core.plugins import runtime_extraction as rx


def test_plugin_extraction_router_flag_off_uses_legacy(monkeypatch):
    """When plugin strategy is off, extract_price should go straight to legacy path."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "false")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    called = {"router": 0}

    def _router(**kwargs):  # noqa: ARG001
        called["router"] += 1
        return {"price": 9999.0}

    monkeypatch.setattr(ex, "run_plugin_extraction_router", _router)
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "legacy",
            "reason": "",
        },
    )

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out["source"] == "legacy"
    assert called["router"] == 0


def test_plugin_extraction_router_flag_on_strategy_empty_falls_back(monkeypatch):
    """Plugin-router empty output must fall back to legacy extraction chain."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setattr(ex, "run_plugin_extraction_router", lambda **kwargs: {})
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: {
            "price": 13579.0,
            "currency": "JPY",
            "confidence": "medium",
            "selector_hint": None,
            "source": "legacy",
            "reason": "",
        },
    )

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out["source"] == "legacy"
    assert out["price"] == 13579.0


def test_plugin_extraction_router_accepts_non_empty_result(monkeypatch):
    """Valid plugin output should be accepted when router returns payload."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    monkeypatch.setattr(
        ex,
        "run_plugin_extraction_router",
        lambda **kwargs: {
            "price": 22222.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": None,
            "source": "plugin_html_llm",
            "reason": "price_found",
            "scope_guard": "pass",
            "scope_guard_basis": "deterministic",
            "confidence_score": 0.8,
        },
    )
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy path should not run")),
    )

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out["source"] == "plugin_html_llm"
    assert out["price"] == 22222.0


def test_router_never_raises_on_strategy_error_and_legacy_runs(monkeypatch):
    """Router strategy errors should soft-fail and extractor should use legacy fallback."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    class _BrokenStrategy:
        key = "html_llm"

        def strategy_key(self):
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _key: _BrokenStrategy())
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: {
            "price": 7777.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "legacy",
            "reason": "",
        },
    )

    out = ex.extract_price("<html></html>", site="google_flights")
    assert out["source"] == "legacy"
    assert out["price"] == 7777.0


def test_router_strategy_env_overrides_threshold(monkeypatch):
    """Explicit env strategy key should win over threshold fallback key."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "vlm_image")

    seen = {"key": ""}

    class _NoopStrategy:
        key = "vlm_image"

        def strategy_key(self):
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 123.0,
                "currency": "JPY",
                "confidence": "low",
                "selector_hint": None,
                "source": "plugin",
                "reason": "",
            }

    def _get_strategy(key):
        seen["key"] = key
        return _NoopStrategy()

    monkeypatch.setattr(rx, "get_strategy_plugin", _get_strategy)

    scope_calls = {"count": 0}

    def _scope_guard(candidate):
        scope_calls["count"] += 1
        return dict(candidate)

    out = rx.run_plugin_extraction_router(
        html="<html></html>",
        site="google_flights",
        task="price",
        origin=None,
        dest=None,
        depart=None,
        return_date=None,
        trip_type=None,
        is_domestic=None,
        screenshot_path=None,
        page_url=None,
        existing_scope_guard_fn=_scope_guard,
        thresholds_getter=lambda _k, d: d,
    )
    assert seen["key"] == "vlm_image"
    assert scope_calls["count"] == 1
    assert out.get("price") == 123.0


def test_router_acceptance_calls_scope_guard_once(monkeypatch):
    """Acceptance path should apply scope guard exactly once per candidate."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY", "html_llm")

    class _Strategy:
        key = "html_llm"

        def strategy_key(self):
            return self.key

        def extract(self, **kwargs):  # noqa: ARG002
            return {
                "price": 9000.0,
                "currency": "JPY",
                "confidence": "medium",
                "selector_hint": None,
                "reason": "price_found",
            }

    monkeypatch.setattr(rx, "get_strategy_plugin", lambda _k: _Strategy())

    calls = {"count": 0}

    def _scope_guard(candidate):
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
        trip_type="round_trip",
        is_domestic=True,
        screenshot_path=None,
        page_url=None,
        existing_scope_guard_fn=_scope_guard,
        thresholds_getter=lambda _k, d: d,
    )
    assert calls["count"] == 1
    assert out.get("price") == 9000.0


def test_disable_plugins_forces_legacy_even_when_enabled(monkeypatch):
    """Emergency disable flag should force legacy extractor path."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_DISABLE_PLUGINS", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")

    called = {"router": 0}

    def _router(**kwargs):  # noqa: ARG001
        called["router"] += 1
        return {
            "price": 9999.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": None,
            "source": "plugin_html_llm",
            "reason": "price_found",
            "scope_guard": "pass",
            "scope_guard_basis": "deterministic",
            "confidence_score": 0.8,
        }

    monkeypatch.setattr(ex, "run_plugin_extraction_router", _router)
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: {
            "price": 54321.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "legacy",
            "reason": "",
        },
    )
    out = ex.extract_price("<html></html>", site="google_flights")
    assert called["router"] == 0
    assert out["source"] == "legacy"
    assert out["price"] == 54321.0


def test_skyscanner_deterministic_prepass_short_circuits_extract_chain(monkeypatch):
    """Skyscanner deterministic parser should return early when it has a price."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setattr(
        ex,
        "extract_skyscanner_price_from_html",
        lambda html, page_url=None: {  # noqa: ARG005
            "ok": True,
            "price": 23746.0,
            "currency": "JPY",
            "page_kind": "flights_results",
            "extraction_strategy": "skyscanner_semantic_price_regex_v1",
            "evidence": {"page_kind": "flights_results"},
        },
    )
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("extract_with_llm should not run")),
    )
    monkeypatch.setattr(
        ex,
        "run_plugin_extraction_router",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("router should not run")),
    )

    out = ex.extract_price("<html></html>", site="skyscanner")
    assert out["price"] == 23746.0
    assert out["source"] == "heuristic_skyscanner_service"
    assert out["scope_guard"] == "pass"


def test_skyscanner_skips_plugin_router_when_deterministic_prepass_misses(monkeypatch):
    """Skyscanner should bypass plugin-router html_llm and continue via legacy chain."""
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", "true")
    monkeypatch.setattr(
        ex,
        "extract_skyscanner_price_from_html",
        lambda html, page_url=None: {  # noqa: ARG005
            "ok": False,
            "price": None,
            "currency": None,
            "reason_code": "missing_price",
        },
    )
    called = {"router": 0}

    def _router(**kwargs):  # noqa: ARG001
        called["router"] += 1
        return {"price": 9999.0}

    monkeypatch.setattr(ex, "run_plugin_extraction_router", _router)
    monkeypatch.setattr(
        ex,
        "extract_with_llm",
        lambda **kwargs: {
            "price": 13579.0,
            "currency": "JPY",
            "confidence": "medium",
            "selector_hint": None,
            "source": "legacy",
            "reason": "",
            "scope_guard": "skip",
            "scope_guard_basis": "deterministic",
            "confidence_score": 0.6,
        },
    )

    out = ex.extract_price("<html></html>", site="skyscanner")
    assert called["router"] == 0
    assert out["source"] == "legacy"
    assert out["price"] == 13579.0
