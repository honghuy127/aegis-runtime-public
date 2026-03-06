"""Tests for LLM extraction fallback behavior."""

import pytest

import core.extractor as extractor
from core.extractor import extract_with_llm, looks_package_bundle_page

pytestmark = [pytest.mark.llm, pytest.mark.vlm, pytest.mark.heavy]


def test_extract_with_llm_uses_heuristic_fallback(monkeypatch):
    """When LLM misses price, fallback should extract a minimum visible fare."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )

    html = """
    <html>
      <body>
        <div aria-label="Find flights from Tokyo (NRT) to Sapporo (CTS) from ¥10,700.">
          from <span>¥10,700</span>
        </div>
        <div aria-label="Find flights from Osaka (KIX) to Tokyo (NRT) from ¥9,740.">
          from <span>¥9,740</span>
        </div>
      </body>
    </html>
    """

    result = extract_with_llm(html=html, site="google_flights", task="price")

    assert result["price"] == 9740.0
    assert result["currency"] == "JPY"
    assert result["source"] == "heuristic_html"
    assert result["reason"] == "heuristic_min_price"


def test_extract_with_llm_prefers_route_matched_price_when_context_provided(monkeypatch):
    """Route context should avoid unrelated lower prices from other routes."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div aria-label="Find flights from Osaka (KIX) to Tokyo (NRT) from ¥9,740.">¥9,740</div>
      <div aria-label="Find flights from Tokyo (HND) to Osaka (ITM) from ¥25,986.">¥25,986</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert result["price"] == 25986.0
    assert result["currency"] == "JPY"


def test_extract_with_llm_returns_none_when_route_not_matched(monkeypatch):
    """With route context, unrelated prices should be ignored."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div aria-label="Find flights from Osaka (KIX) to Tokyo (NRT) from ¥9,740.">¥9,740</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert result["price"] is None


def test_extract_with_llm_does_not_match_metro_code_substring_in_city_name(monkeypatch):
    """Short metro codes like TYO/OSA must not match substrings in TOKYO/OSAKA."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div aria-label="Find flights from Osaka (KIX) to Tokyo (NRT) from ¥9,740.">¥9,740</div>
      <div>Popular routes in TOKYO and OSAKA</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert result["price"] is None
    assert result["reason"] in ("price_not_found", "heuristic_no_route_match")


def test_extract_with_llm_google_embedded_data_prefers_route_and_dates(monkeypatch):
    """Google embedded JSON snippets should be parsed when route+date match."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <script>
        AF_initDataCallback({data:[[["2026-03-01","2026-03-08",null,null,
          [[null,25986],"x","KIX",null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,"HND"]],
          ["2026-04-01","2026-04-08",null,null,
          [[null,9740],"x","KIX",null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,"NRT"]]
        ]]]});
      </script>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] == 25986.0
    assert result["currency"] == "JPY"
    assert result["source"] == "heuristic_embedded"


def test_extract_with_llm_requires_depart_match_when_context_provided(monkeypatch):
    """Date mismatch should block unrelated route snippets from being returned."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div aria-label="Find flights from Tokyo (HND) to Osaka (ITM) from ¥25,986.">¥25,986</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] is None
    assert result["reason"] in ("price_not_found", "heuristic_no_route_match")


def test_extract_with_llm_accepts_provider_metro_codes(monkeypatch):
    """Google metro-coded snippets (TYO/OSA) should match airport-specific input."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div aria-label="Find flights from Tokyo (TYO) to Osaka (OSA) from ¥25,986.">
        from ¥25,986
      </div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert result["price"] == 25986.0


def test_extract_with_llm_rejects_google_alias_route_without_strict_anchor(monkeypatch):
    """Date context alone should not accept airport-alias cards from other route clusters."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div>selected dates: 2026-03-01 - 2026-03-08</div>
      <div aria-label="Find flights from Osaka (KIX) to Tokyo (NRT) from ¥9,740.">¥9,740</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] is None
    assert result["reason"] in ("price_not_found", "heuristic_no_route_match")


def test_extract_with_llm_rejects_google_split_context_with_price_only_snippet(monkeypatch):
    """Bare price snippets are ambiguous and should be ignored with route context."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    html = """
    <html><body>
      <div>trip: 2026-03-01 to 2026-03-08</div>
      <div>route context: Tokyo (NRT) <-> Osaka (KIX)</div>
      <div>¥25,986</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] is None


def test_extract_with_llm_light_mode_escalates_to_llm_on_heuristic_miss(monkeypatch):
    """Light mode should still try one short LLM parse when heuristics miss."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": 26451.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "reason": "llm_route_match",
        },
    )
    result = extract_with_llm(
        html="<html><body>no deterministic match ¥25,986</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] == 26451.0
    assert result["source"] == "llm_light_escalation"


def test_extract_with_llm_light_mode_escalation_supports_legacy_parse_signature(monkeypatch):
    """Escalation path should support monkeypatched parse_html_with_llm without timeout kwarg."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "reason": "llm_route_match",
        },
    )
    result = extract_with_llm(
        html="<html><body>no deterministic match ¥25,986</body></html>",
        site="google_flights",
        task="price",
    )
    assert result["price"] == 25986.0
    assert result["reason"] == "llm_route_match"
    assert result["source"] == "llm_light_escalation"


def test_extract_with_llm_light_mode_skips_chunk_retries_after_request_failure(monkeypatch):
    """Light mode should avoid chunk retry fan-out after hard request failure."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor._semantic_html_chunks",
        lambda *args, **kwargs: [{"html": "<div>chunk</div>"}],
    )
    calls = {"n": 0}

    def _fake_parse(html, site, task, timeout_sec=None):
        calls["n"] += 1
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "llm_request_failed_timeout",
        }

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _fake_parse)
    result = extract_with_llm(
        html="<html><body>¥25,986</body></html>",
        site="google_flights",
        task="price",
    )
    assert result["price"] is None
    assert result["reason"] == "heuristic_no_route_match"
    assert calls["n"] == 1


def test_extract_with_llm_returns_non_flight_scope_from_vlm_without_llm_retry(monkeypatch):
    """Strong VLM non-flight scope signal should short-circuit long LLM retries."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda *args, **kwargs: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "The screenshot shows hotel listings, not flight prices and no flight-related information.",
            "source": "vlm",
        },
    )

    def _should_not_call_llm(*args, **kwargs):
        raise AssertionError("parse_html_with_llm should not be called for non-flight VLM scope")

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _should_not_call_llm)
    result = extract_with_llm(
        html="<html><body>content</body></html>",
        site="google_flights",
        task="price",
        screenshot_path="storage/debug_html/scenario_google_flights_last.png",
    )
    assert result["price"] is None
    assert result["source"] == "vlm"
    assert result["reason"] == "vlm_non_flight_scope"


def test_extract_with_llm_vlm_scope_guard_blocks_llm_price_on_package_screen(monkeypatch):
    """VLM UI scope guard should block LLM price when page is flight+hotel package."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "full")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": 57388.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".x"},
            "reason": "",
            "source": "llm",
        },
    )
    monkeypatch.setattr(
        "core.extractor.analyze_page_ui_with_vlm",
        lambda *args, **kwargs: {"trip_product": "flight_hotel_package"},
    )
    monkeypatch.setattr(
        "core.extractor.assess_trip_product_scope_with_llm",
        lambda *args, **kwargs: {},
    )

    result = extract_with_llm(
        html="<html><body>package-like content</body></html>",
        site="google_flights",
        task="price",
        screenshot_path="dummy.png",
    )
    assert result["price"] is None
    assert result["source"] == "vlm_scope_guard"
    assert result["reason"] == "vlm_non_flight_scope"
    assert result["scope_guard"] == "fail"
    assert result["scope_guard_basis"] in {"vlm", "mixed"}


def test_extract_with_llm_llm_scope_guard_blocks_when_vlm_unavailable(monkeypatch):
    """LLM scope guard should still block package-like prices when VLM scope is unavailable."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "full")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": 57413.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".x"},
            "reason": "",
            "source": "llm",
        },
    )
    monkeypatch.setattr(
        "core.extractor.analyze_page_ui_with_vlm",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "core.extractor.assess_trip_product_scope_with_llm",
        lambda *args, **kwargs: {
            "trip_product": "flight_hotel_package",
            "reason": "mixed hotel+flight package content",
        },
    )

    result = extract_with_llm(
        html="<html><body>package-like content</body></html>",
        site="google_flights",
        task="price",
        screenshot_path="dummy.png",
    )
    assert result["price"] is None
    assert result["source"] == "vlm_scope_guard"
    assert result["reason"] == "vlm_non_flight_scope"


def test_extract_with_llm_google_fast_non_flight_guard_short_circuits(monkeypatch):
    """Google hotel/map HTML should be blocked before expensive LLM/VLM extraction."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])

    def _should_not_call_llm(*args, **kwargs):
        raise AssertionError("parse_html_with_llm should not run after fast non-flight guard")

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _should_not_call_llm)
    html = """
    <html><body>
      <div>地図を表示</div><div>リストを表示</div><div>ホテル</div>
      <div>HND ITM</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] is None
    assert result["source"] == "heuristic_guard"
    assert result["reason"] == "html_non_flight_scope"


def test_extract_with_llm_google_fast_non_flight_guard_ignores_deeplink_url_only_context(
    monkeypatch,
):
    """Deeplink URL alone should not suppress fast non-flight guard on map/hotel page."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])

    def _should_not_call_llm(*args, **kwargs):
        raise AssertionError("parse_html_with_llm should not run after fast non-flight guard")

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _should_not_call_llm)
    html = """
    <html><body>
      <div>地図を表示</div><div>リストを表示</div><div>ホテル</div>
      <div>おすすめの滞在先</div>
    </body></html>
    """
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        page_url="https://www.google.com/travel/flights?hl=ja#flt=HND.ITM.2026-03-01*ITM.HND.2026-03-08;c:JPY;e:1;sd:1;t:f",
    )
    assert result["price"] is None
    assert result["source"] == "heuristic_guard"
    assert result["reason"] == "html_non_flight_scope"


def test_extract_with_llm_google_route_context_guard_blocks_unbound_llm_price(monkeypatch):
    """LLM price should be rejected when requested Google route/date context is not bound."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "full")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_GOOGLE_NON_FLIGHT_FAST_GUARD", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor.parse_html_with_llm", lambda *args, **kwargs: {
        "price": 57468.0,
        "currency": "JPY",
        "confidence": "high",
        "selector_hint": {"css": ".x"},
        "reason": "",
        "source": "llm",
    })
    monkeypatch.setattr("core.extractor.analyze_page_ui_with_vlm", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "core.extractor.assess_trip_product_scope_with_llm",
        lambda *args, **kwargs: {"trip_product": "unknown", "reason": "unknown"},
    )

    result = extract_with_llm(
        html="<html><body>HND ITM fare ¥57,468</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
    )
    assert result["price"] is None
    assert result["source"] == "vlm_scope_guard"
    assert result["reason"] == "google_route_context_unbound"


def test_extract_with_llm_does_not_apply_heuristics_for_other_sites(monkeypatch):
    """Heuristic fallback is site-gated and should not override other sites."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )

    html = "<html><body><div>$123</div></body></html>"
    result = extract_with_llm(html=html, site="other_site", task="price")

    assert result["price"] is None
    assert result["source"] == "llm"
    assert result["reason"] == "price_not_found"


def test_extract_with_llm_parses_jpy_yen_suffix_heuristic(monkeypatch):
    """Heuristic should parse Japanese yen suffix like '9,740円'."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )

    html = """
    <html>
      <body>
        <div aria-label="国内航空券 9,740円">
          最安 9,740円
        </div>
      </body>
    </html>
    """

    result = extract_with_llm(html=html, site="google_flights", task="price")
    assert result["price"] == 9740.0
    assert result["currency"] == "JPY"
    assert result["source"] == "heuristic_html"


def test_extract_with_llm_light_mode_prefers_heuristic_without_llm_call(monkeypatch):
    """Light mode should return heuristic result before calling LLM parser."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")

    def _fail_parse(*args, **kwargs):
        raise AssertionError("parse_html_with_llm should not be called in light mode when heuristic succeeds")

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _fail_parse)

    html = """
    <html><body>
      <div aria-label="Find flights from HND to ITM from ¥9,740.">¥9,740</div>
    </body></html>
    """
    result = extract_with_llm(html=html, site="google_flights", task="price")
    assert result["price"] == 9740.0
    assert result["source"] == "heuristic_html"





def test_extract_with_llm_passes_page_url_to_package_guard(monkeypatch):
    """extract_with_llm should forward `page_url` into package guard checks."""
    seen = {}

    def _fake_guard(html, site="", url=""):
        seen["url"] = url
        return False

    monkeypatch.setattr("core.extractor.looks_package_bundle_page", _fake_guard)
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda *args, **kwargs: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    page_url = "https://www.google.com/travel/flights"
    extract_with_llm(
        html="<html><body>plain content</body></html>",
        site="google_flights",
        task="price",
        page_url=page_url,
    )
    assert seen["url"] == page_url


def test_extract_with_heuristics_passes_page_url_to_package_guard(monkeypatch):
    """Heuristic extractor should also pass `page_url` to package guard."""
    seen = {}

    def _fake_guard(html, site="", url=""):
        seen["url"] = url
        return False

    monkeypatch.setattr("core.extractor.looks_package_bundle_page", _fake_guard)
    extractor._extract_with_heuristics(
        html="<html><body>¥9,740</body></html>",
        site="google_flights",
        page_url="https://www.google.com/travel/flights",
    )
    assert seen["url"] == "https://www.google.com/travel/flights"


def test_package_bundle_guard_does_not_block_google_flights_page():
    """Google Flights page with hotel mentions should not be auto-classified as package."""
    html = "<html><body><div>Flights</div><div>Hotels</div></body></html>"
    assert looks_package_bundle_page(html=html, site="google_flights") is False


def test_package_bundle_guard_site_policy_moves_through_plugin():
    """Site package guard policy should be controlled by service plugin."""
    html = "<html><body>ダイナミックパッケージ 航空券＋ホテル hotel flight</body></html>"
    assert looks_package_bundle_page(html=html, site="google_flights") is False


def test_package_bundle_guard_plugin_defaults_are_stable():
    """Default plugin keeps package guard disabled unless service plugin enables it."""
    assert extractor._plugin_for_site("unknown").package_bundle_page_guard_enabled({}) is False


def test_package_bundle_guard_url_token_requires_page_corroboration(monkeypatch):
    """URL package token alone should not trigger package classification."""
    monkeypatch.setattr("core.extractor._PACKAGE_URL_TOKENS", ["/package/"])
    weak_html = "<html><body><div>Flights</div></body></html>"
    assert (
        looks_package_bundle_page(
            html=weak_html,
            site="google_flights",
            url="https://example.test/package/deal",
        )
        is False
    )
    corroborated_html = "<html><body><div>flight</div><div>hotel</div></body></html>"
    assert (
        looks_package_bundle_page(
            html=corroborated_html,
            site="google_flights",
            url="https://example.test/package/deal",
        )
        is True
    )


def test_extract_with_llm_light_mode_quality_gate_blocks_garbage_before_llm(monkeypatch):
    """Quality gate should skip LLM extraction on obvious auth/interstitial HTML."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: None,
    )

    def _fail_parse(*args, **kwargs):
        raise AssertionError("parse_html_with_llm should be skipped for garbage HTML")

    monkeypatch.setattr("core.extractor.parse_html_with_llm", _fail_parse)
    html = "<html><body>login email password account sign in</body></html>"
    result = extract_with_llm(html=html, site="google_flights", task="price")
    assert result["price"] is None
    assert result["reason"] == "html_quality_garbage"


def test_extract_with_llm_uses_semantic_chunk_heuristic_after_full_html_miss(monkeypatch):
    """When full HTML heuristics miss, semantic chunk fallback should be used."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")

    def _heuristic(html, site, **kwargs):
        if "chunk-hit" in html:
            return {
                "price": 26451.0,
                "currency": "JPY",
                "confidence": "low",
                "selector_hint": None,
                "source": "heuristic_html",
                "reason": "heuristic_min_price",
            }
        return None

    monkeypatch.setattr("core.extractor._extract_with_heuristics", _heuristic)
    monkeypatch.setattr(
        "core.extractor._semantic_html_chunks",
        lambda *args, **kwargs: [
            {"html": "<div>chunk-hit</div>", "score": 10, "price_hits": 1}
        ],
    )
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    result = extract_with_llm(
        html="<html><body>" + ("x" * 300000) + "</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert result["price"] == 26451.0
    assert result["source"] == "heuristic_chunk"
    assert result["reason"] == "semantic_chunk_route_match"


def test_extract_with_llm_light_mode_uses_vlm_when_enabled(monkeypatch):
    """Light mode should allow optional VLM extraction when screenshot is available."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 26451.0,
            "currency": "JPY",
            "confidence": "medium",
            "reason": "vlm_visible_price",
        },
    )

    result = extract_with_llm(
        html="<html><body>No reliable text match</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        screenshot_path="/tmp/fake.png",
    )
    assert result["price"] == 26451.0
    assert result["currency"] == "JPY"
    assert result["source"] == "vlm"


def test_extract_with_llm_dom_probe_skips_vlm_when_route_bind_strong(monkeypatch):
    """Strong route bind + visible DOM price should short-circuit before VLM."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "off")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": True,
            "support": "strong",
            "source": "dom",
            "reason": "dom_strong_match",
            "observed": {},
        },
    )

    def _fail_vlm(*args, **kwargs):
        raise AssertionError("parse_image_with_vlm should not be called")

    monkeypatch.setattr("core.extractor.parse_image_with_vlm", _fail_vlm)

    html = "<html><body>HND ITM 2026-03-01 Flights 最安 ¥12,345</body></html>"
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        screenshot_path="/tmp/fake.png",
    )
    assert result["price"] == 12345.0
    assert result["source"] == "heuristic_dom_probe"


def test_extract_with_llm_dom_probe_does_not_short_circuit_on_weak_route_bind(monkeypatch):
    """Weak/non-strong route support should keep VLM eligibility path."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "off")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": False,
            "support": "weak",
            "source": "dom",
            "reason": "weak_evidence",
            "observed": {},
        },
    )
    called = {"vlm": 0}

    def _vlm(*args, **kwargs):
        called["vlm"] += 1
        return {
            "price": 12800.0,
            "currency": "JPY",
            "confidence": "low",
            "reason": "vlm_visible_price",
        }

    monkeypatch.setattr("core.extractor.parse_image_with_vlm", _vlm)
    html = "<html><body>HND ITM 2026-03-01 Flights 最安 ¥12,345</body></html>"
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        screenshot_path="/tmp/fake.png",
    )
    assert called["vlm"] == 1
    assert result["source"] != "heuristic_dom_probe"


def test_extract_with_llm_vision_extract_assist_runs_on_strong_bound_llm_miss(monkeypatch):
    """Stage-C vision assist should run when LLM misses and route binding is accepted."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "full")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": True,
            "support": "strong",
            "source": "dom",
            "reason": "dom_strong_match",
            "observed": {},
        },
    )
    calls = {"count": 0}

    def _vision_parse(*args, **kwargs):
        calls["count"] += 1
        return {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "medium",
            "reason": "price_on_top_card",
            "visible_price_text": "¥25,986",
        }

    monkeypatch.setattr("core.extractor.parse_image_with_vlm", _vision_parse)
    result = extract_with_llm(
        html="<html><body>No deterministic match</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
    )
    assert calls["count"] == 1
    assert result["price"] == 25986.0
    assert result["source"] == "vision_price_assist"


def test_extract_with_llm_vision_extract_assist_skips_when_route_not_accepted(monkeypatch):
    """Stage-C should not call VLM when route binding is not accepted."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "full")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": False,
            "support": "weak",
            "source": "dom",
            "reason": "weak_context",
            "observed": {},
        },
    )
    calls = {"count": 0}

    def _vision_parse(*args, **kwargs):
        calls["count"] += 1
        return {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "medium",
            "reason": "price_on_top_card",
        }

    monkeypatch.setattr("core.extractor.parse_image_with_vlm", _vision_parse)
    result = extract_with_llm(
        html="<html><body>No deterministic match</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
    )
    assert calls["count"] == 0
    assert result["price"] is None


def test_vision_cached_stage_call_reuses_cached_result(monkeypatch):
    """Extractor vision cache helper should avoid duplicate runner calls per fingerprint."""
    cache = {}
    cooldown = {}
    calls = {"count": 0}
    monkeypatch.setattr(
        "core.extractor._vision_screenshot_fingerprint",
        lambda path, max_prefix_bytes=65536: "fp-1",  # noqa: ARG005
    )

    def _runner():
        calls["count"] += 1
        return {"price": 123.0}

    first, first_meta = extractor._vision_cached_stage_call(
        cache=cache,
        cooldown=cooldown,
        stage="extract_assist",
        screenshot_path="/tmp/a.png",
        runner=_runner,
    )
    second, second_meta = extractor._vision_cached_stage_call(
        cache=cache,
        cooldown=cooldown,
        stage="extract_assist",
        screenshot_path="/tmp/a.png",
        runner=_runner,
    )
    assert first == {"price": 123.0}
    assert first_meta["cached"] is False
    assert second == {"price": 123.0}
    assert second_meta["cached"] is True
    assert calls["count"] == 1


def test_normalize_vision_extract_assist_result_handles_invalid_payload():
    """Stage-C schema normalization should fail open on malformed model output."""
    out = extractor._normalize_vision_extract_assist_result({"price": "bad", "confidence": "??"})
    assert out["price"] is None
    assert out["confidence"] == "low"
    assert out["currency"] is None


def test_extract_with_llm_confidence_score_is_present_and_bounded(monkeypatch):
    """All extract_with_llm outputs should include confidence_score in [0, 1]."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    failure = extract_with_llm(
        html="<html><body>No reliable text match</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
    )
    assert "confidence_score" in failure
    assert 0.0 <= float(failure["confidence_score"]) <= 1.0

    monkeypatch.setattr(
        "core.extractor._extract_with_heuristics",
        lambda **kwargs: {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "heuristic_html",
            "reason": "heuristic_min_price",
        },
    )
    success = extract_with_llm(
        html="<html><body>HND ITM 2026-03-01 ¥25,986</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
    )
    assert "confidence_score" in success
    assert 0.0 <= float(success["confidence_score"]) <= 1.0


def test_extract_with_llm_rejects_vlm_price_when_llm_verify_fails(monkeypatch):
    """VLM price candidate should be rejected when LLM verifier returns accept=false."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "high",
            "reason": "price_found",
            "visible_price_text": "¥12,345",
        },
    )
    monkeypatch.setattr(
        "core.extractor.assess_vlm_price_candidate_with_llm",
        lambda *args, **kwargs: {
            "accept": False,
            "support": "none",
            "reason": "candidate_not_grounded",
        },
    )

    result = extract_with_llm(
        html="<html><body>HND ITM 2026-03-01 2026-03-08 flights ¥57,468</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
        page_url="https://www.google.com/travel/flights?hl=ja#flt=HND.ITM.2026-03-01*ITM.HND.2026-03-08;c:JPY;e:1;sd:1;t:f",
    )
    assert result["price"] is None
    assert result["source"] == "vlm"
    assert result["reason"] == "vlm_price_rejected_by_llm_verify"
    assert result["scope_guard_trigger"] == "llm"


def test_extract_with_llm_allows_vlm_candidate_without_visible_price_text(monkeypatch):
    """Missing visible_price_text should mark grounding false instead of hard reject."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "high",
            "reason": "price_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": True,
            "support": "strong",
            "source": "dom",
            "reason": "dom_strong_match",
            "observed": {},
        },
    )

    result = extract_with_llm(
        html="<html><body>HND ITM 2026-03-01 flights</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        screenshot_path="/tmp/fake.png",
    )
    assert result["price"] == 12345.0
    assert result.get("price_grounded_in_html") is False


def test_extract_with_llm_vlm_visible_price_tolerance_for_jpy(monkeypatch):
    """JPY visible text mismatch should allow small tolerant differences."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "high",
            "reason": "price_found",
            "visible_price_text": "¥12,500",
        },
    )
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": True,
            "support": "strong",
            "source": "dom",
            "reason": "dom_strong_match",
            "observed": {},
        },
    )

    result = extract_with_llm(
        html="<html><body>HND ITM 2026-03-01 flights</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        screenshot_path="/tmp/fake.png",
    )
    assert result["price"] == 12345.0


def test_extract_with_llm_cached_selector_enriches_route_bind_fields(monkeypatch):
    """Cached selector candidates should carry route-bind fields for selector-stability logic."""
    monkeypatch.setenv("FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_GATE_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".hXU5Ud.aA5Mwe"},
            "source": "cached_selector",
            "reason": "ok",
        },
    )
    monkeypatch.setattr(
        "core.extractor._compute_google_route_bind_verdict",
        lambda **kwargs: {
            "route_bound": True,
            "support": "strong",
            "source": "dom",
            "reason": "dom_strong_match",
            "observed": {},
        },
    )
    result = extract_with_llm(
        html="<html><body>HND ITM 2026-03-01 flights</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
    )
    assert result["source"] == "cached_selector"
    assert result.get("route_bound") is True
    assert result.get("route_bind_support") == "strong"


def test_extract_with_llm_blocks_vlm_price_when_scope_conflict_unresolved(monkeypatch):
    """VLM price should be blocked when LLM scope is non-flight and VLM cannot affirm flight scope."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_GOOGLE_REQUIRE_ROUTE_CONTEXT", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "high",
            "reason": "price_found",
            "visible_price_text": "¥12,345",
            "page_class": "unknown",
            "trip_product": "unknown",
        },
    )
    monkeypatch.setattr("core.extractor.analyze_page_ui_with_vlm", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "core.extractor.assess_trip_product_scope_with_llm",
        lambda *args, **kwargs: {
            "page_class": "irrelevant_page",
            "trip_product": "unknown",
            "reason": "non_flight_scope",
        },
    )

    result = extract_with_llm(
        html="<html><body>HND ITM flights 2026-03-01 2026-03-08 ¥57,468</body></html>",
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
        page_url="https://www.google.com/travel/flights?hl=ja#flt=HND.ITM.2026-03-01*ITM.HND.2026-03-08;c:JPY;e:1;sd:1;t:f",
    )
    assert result["price"] is None
    assert result["source"] == "vlm_scope_guard"
    assert result["reason"] == "scope_conflict_unresolved_vlm_price"


def test_extract_with_llm_vlm_route_guard_blocks_unbound_google_context(monkeypatch):
    """VLM price should be rejected when Google page context lacks requested route/date."""
    monkeypatch.setenv("FLIGHT_WATCHER_LLM_MODE", "light")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "1")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_image_with_vlm",
        lambda image_path, **kwargs: {
            "price": 15200.0,
            "currency": "JPY",
            "confidence": "high",
            "reason": "vlm_visible_price",
        },
    )

    # No requested depart/return in HTML -> should not accept VLM price for Google Flights.
    html = "<html><body>Explore deals from Osaka (KIX) to Tokyo (NRT) ¥9,740</body></html>"
    result = extract_with_llm(
        html=html,
        site="google_flights",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
    )
    assert result["price"] is None


def test_schema_complete_for_non_google_no_price(monkeypatch):
    """Non-google sites should still return the complete normalized schema on misses."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
            "site": site,
            "task": task,
        },
    )
    result = extractor.extract_price(
        html="<html><body>No useful price context</body></html>",
        site="skyscanner",
        task="price",
        origin="NRT",
        dest="KIX",
        depart="2026-03-01",
    )
    required = {
        "price",
        "currency",
        "confidence",
        "confidence_score",
        "selector_hint",
        "source",
        "reason",
        "scope_guard",
        "scope_guard_basis",
    }
    assert required.issubset(result.keys())
    assert result["price"] is None
    assert result["scope_guard"] in {"pass", "fail", "skip", "conflict_resolved"}
    assert result["scope_guard_basis"] in {"deterministic", "vlm", "llm", "mixed"}
    assert 0.0 <= float(result["confidence_score"]) <= 1.0


def test_google_plugin_fast_scope_guard_blocks_non_flight_scope(monkeypatch):
    """Google plugin fast scope guard should block map+hotel non-flight pages."""
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda *args, **kwargs: {
            "price": 99999.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": {"css": ".x"},
            "reason": "",
            "source": "llm",
        },
    )
    html = """
    <html><body>
      <div>地図を表示</div>
      <div>リストを表示</div>
      <div>ホテル</div>
      <div>おすすめの宿泊施設</div>
    </body></html>
    """
    result = extractor.extract_price(
        html=html,
        site="google_flights",
        task="price",
        origin="NRT",
        dest="KIX",
        depart="2026-03-01",
        return_date="2026-03-05",
    )
    assert result["price"] is None
    assert result["reason"] == "html_non_flight_scope"
    assert result["scope_guard"] == "fail"
    assert result["scope_guard_trigger"] == "deterministic"


def test_plugin_registry_routes_google_to_google_plugin():
    """Registry should route google_flights to the Google plugin implementation."""
    plugin = extractor._plugin_for_site("google_flights")
    assert plugin.name == "google_flights"


def test_price_grounding_tolerance_reuses_thresholds(monkeypatch):
    """Grounding tolerance should be computed from ratio/abs thresholds consistently."""
    monkeypatch.setattr(
        "core.extractor.get_threshold",
        lambda key, default=None: {
            "extract_vlm_price_grounding_tolerance_ratio": 0.01,
            "extract_vlm_price_grounding_tolerance_abs": 100.0,
        }.get(key, default),
    )
    assert extractor._price_grounding_tolerance(5_000.0) == 100.0
    assert extractor._price_grounding_tolerance(20_000.0) == 200.0


def test_google_site_helper_normalizes_case_and_spacing():
    """Google-site helper should normalize common casing/spacing variations."""
    assert extractor._is_google_flights_site("google_flights") is True
    assert extractor._is_google_flights_site("  Google_Flights  ") is True
    assert extractor._is_google_flights_site("skyscanner") is False


def test_confidence_score_deterministic_repeated_calls(monkeypatch):
    """Same input should produce stable confidence score and schema fields."""
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    kwargs = dict(
        html="<html><body>No reliable text match</body></html>",
        site="skyscanner",
        task="price",
        origin="NRT",
        dest="KIX",
        depart="2026-03-01",
    )
    first = extractor.extract_price(**kwargs)
    second = extractor.extract_price(**kwargs)
    assert first["confidence_score"] == second["confidence_score"]
    for key in (
        "price",
        "currency",
        "confidence",
        "confidence_score",
        "selector_hint",
        "source",
        "reason",
        "scope_guard",
        "scope_guard_basis",
    ):
        assert key in first
        assert key in second
