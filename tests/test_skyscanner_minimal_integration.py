from __future__ import annotations

import ast
from pathlib import Path

from core.adapters.skyscanner_adapter import SkyscannerAgentAdapter
from core.plugins.services.skyscanner import extract_price_from_html
from core.site_adapter import SiteAdapterBindResult
from core.site_adapter_registry import SiteAdapterRegistry


class _FakeBrowser:
    def __init__(self, html: str):
        self._html = html
        self.gotos = []
        self.wait_calls = []

    def goto(self, url: str):
        self.gotos.append(url)

    def content(self) -> str:
        return self._html

    def wait_for(self, selector: str, timeout_ms: int = 0):
        self.wait_calls.append((selector, timeout_ms))
        return True


def _fixture_html(name: str) -> str:
    return (Path("tests/fixtures/skyscanner") / name).read_text(encoding="utf-8")


def test_services_skyscanner_no_browser_imports():
    source = Path("core/plugins/services/skyscanner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert "playwright" not in imported
    assert "core.browser" not in imported


def test_site_adapter_registry_selects_skyscanner_agent_driver():
    registry = SiteAdapterRegistry()
    registry.register_agent_adapter("skyscanner", SkyscannerAgentAdapter)
    adapter = registry.get_adapter(
        "skyscanner",
        {"ui_driver_mode": "agent", "ui_driver_fallback_to_legacy": False},
    )
    assert isinstance(adapter, SkyscannerAgentAdapter)


def test_skyscanner_extraction_from_fixture_html():
    html = _fixture_html("results_sample.html")
    result = extract_price_from_html(
        html,
        page_url="https://www.skyscanner.com/transport/flights/lax/jfk/",
    )
    assert result["ok"] is True
    assert result["price"] == 123
    assert result["currency"] == "USD"
    assert result["page_kind"] == "flights_results"


def test_skyscanner_agent_failure_reason_has_evidence(monkeypatch):
    import core.agent.plugins.skyscanner.plugin as sk_plugin_mod

    monkeypatch.setattr(
        sk_plugin_mod,
        "_extract_price_with_runtime_pipeline",
        lambda **kwargs: {},
    )

    html = """
    <html>
      <head>
        <title>Skyscanner Flights Results</title>
        <link rel="canonical" href="https://www.skyscanner.com/transport/flights/lax/jfk/" />
      </head>
      <body>
        <main role="main">
          <div data-testid="search-results">
            <article data-testid="itinerary-card">Flights LAX JFK itinerary</article>
          </div>
        </main>
      </body>
    </html>
    """
    browser = _FakeBrowser(html)
    adapter = SkyscannerAgentAdapter()
    bind = adapter.bind_route(
        browser=browser,
        url="https://www.skyscanner.com/transport/flights/lax/jfk/",
        origin="LAX",
        dest="JFK",
        depart="2026-03-01",
        return_date=None,
    )
    assert bind.success is True

    ready = adapter.ensure_results_ready(browser)
    assert ready.ready is False
    assert ready.reason == "missing_price"
    assert isinstance(ready.evidence, dict)
    for key in ("url", "html_len", "page_kind", "extraction_strategy_attempted", "gating_decisions"):
        assert key in ready.evidence


def test_skyscanner_bind_fallback_without_legacy_emits_no_legacy_driver():
    class _FailingAgent(SkyscannerAgentAdapter):
        def bind_route(self, *args, **kwargs):  # type: ignore[override]
            return SiteAdapterBindResult(success=False, reason="agent_bind_failed", evidence={"x": 1})

    registry = SiteAdapterRegistry()
    registry.register_agent_adapter("skyscanner", _FailingAgent)
    adapter = registry.get_adapter(
        "skyscanner",
        {"ui_driver_mode": "agent", "ui_driver_fallback_to_legacy": True},
    )
    _, result = registry.bind_with_fallback(
        adapter=adapter,
        browser=_FakeBrowser("<html></html>"),
        url="https://www.skyscanner.com/flights",
        origin="LAX",
        dest="JFK",
        depart="2026-03-01",
        return_date=None,
        config={"ui_driver_fallback_to_legacy": True},
    )
    assert result.success is False
    assert result.reason == "no_legacy_driver"
    assert isinstance(result.evidence, dict)
    assert result.evidence.get("fallback_requested") is True
