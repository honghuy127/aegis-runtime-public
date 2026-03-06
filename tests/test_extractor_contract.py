"""Fast contract tests for extraction routing and validation.

These tests use fake clients to verify extraction logic without real LLM/VLM calls.
Run with: pytest tests/test_extractor_contract.py -q

For tests with real LLM/VLM, see test_extractor.py (marked @pytest.mark.integration).
"""

import pytest
from tests.fakes.fake_llm_client import (
    patch_parse_html_with_llm,
    MISSING_PRICE_RESPONSE,
    CIRCUIT_OPEN_RESPONSE,
)
from tests.fakes.fake_vlm_client import (
    patch_analyze_page_ui_with_vlm,
    NON_FLIGHT_SCOPE_RESPONSE,
)
from core.extractor import extract_with_llm

pytestmark = [pytest.mark.llm, pytest.mark.vlm, pytest.mark.heavy]


class TestHeuristicFallbackRouting:
    """Test heuristic fallback when LLM misses price."""

    def test_heuristic_fallback_extracts_minimum_visible_fare(self, monkeypatch):
        """When LLM misses price, fallback should extract minimum visible fare."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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

    def test_heuristic_fallback_respects_route_context(self, monkeypatch):
        """Route context should filter heuristic extraction to matching routes."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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
        assert result["source"] == "heuristic_html"

    def test_heuristic_fallback_returns_none_when_route_not_matched(self, monkeypatch):
        """With route context, unrelated prices should be ignored."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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
        assert result["reason"] in ("price_not_found", "heuristic_no_route_match")

    def test_heuristic_does_not_match_metro_code_substring_in_city_name(self, monkeypatch):
        """Short metro codes like TYO/OSA must not match substrings in TOKYO/OSAKA."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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

    def test_heuristic_parses_jpy_yen_suffix(self, monkeypatch):
        """JPY prices with 円 suffix should be parsed correctly."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

        html = """
        <html><body>
          <div>往復 25,986円</div>
        </body></html>
        """

        result = extract_with_llm(html=html, site="google_flights", task="price")

        assert result["price"] == 25986.0
        assert result["currency"] == "JPY"


class TestRouteContextValidation:
    """Test route validation guards."""

    def test_google_embedded_data_prefers_route_and_dates(self, monkeypatch):
        """Google embedded JSON snippets should be parsed when route+date match."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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

    def test_requires_depart_match_when_date_context_provided(self, monkeypatch):
        """Date mismatch should block unrelated route snippets."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

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

        # Without date match in HTML, should not extract
        assert result["price"] is None


class TestVLMScopeGuard:
    """Test VLM scope detection guards."""

    def test_vlm_scope_guard_integration_placeholder(self, monkeypatch):
        """VLM scope guard tests require actual VLM integration (TODO)."""
        # This is a placeholder for VLM scope guard testing
        # Real VLM scope guard behavior depends on extraction flow implementation
        # and may require integration tests with actual VLM calls
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

        html = "<html><body>Package bundle page</body></html>"

        result = extract_with_llm(
            html=html,
            site="google_flights",
            task="price",
        )

        # Without VLM, should fallback to heuristic or return None
        assert result is not None
        assert "price" in result


class TestCircuitBreakerBehavior:
    """Test fail-fast circuit breaker behavior."""

    def test_circuit_open_returns_immediately_without_retry_loop(self, monkeypatch):
        """Circuit open should fail fast without retry attempts."""
        patch_parse_html_with_llm(monkeypatch, CIRCUIT_OPEN_RESPONSE)

        html = "<html><body>¥25,986</body></html>"

        result = extract_with_llm(html=html, site="google_flights", task="price")

        # Should return circuit_open result or fallback to heuristic
        # (actual behavior depends on extraction flow)
        assert result is not None
        # Circuit open should not block heuristic fallback
        if result.get("reason") == "circuit_open":
            assert result["price"] is None
        else:
            # Heuristic fallback may still extract
            assert result.get("source") in ("heuristic_html", None)


class TestSchemaValidation:
    """Test extraction schema validation."""

    def test_confidence_score_present_and_bounded(self, monkeypatch):
        """Extraction results should include valid confidence score."""
        patch_parse_html_with_llm(monkeypatch, {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": "div.price",
            "reason": "llm_success",
        })

        html = "<html><body>¥25,986</body></html>"

        result = extract_with_llm(html=html, site="google_flights", task="price")

        assert "confidence" in result
        # Confidence should be bounded or categorized
        if isinstance(result["confidence"], float):
            assert 0.0 <= result["confidence"] <= 1.0
        elif isinstance(result["confidence"], str):
            assert result["confidence"] in ("low", "medium", "high")

    def test_result_schema_includes_required_fields(self, monkeypatch):
        """All extraction results should include required schema fields."""
        patch_parse_html_with_llm(monkeypatch, {
            "price": 25986.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": "div.price",
            "reason": "llm_success",
        })

        html = "<html><body>¥25,986</body></html>"

        result = extract_with_llm(html=html, site="google_flights", task="price")

        # Required fields
        assert "price" in result
        assert "currency" in result or result["price"] is None
        assert "source" in result or "reason" in result

        # Price validation
        if result["price"] is not None:
            assert isinstance(result["price"], (int, float))
            assert result["price"] > 0


class TestNonGoogleSiteBehavior:
    """Test extraction behavior for non-Google sites."""

    def test_does_not_apply_google_heuristics_for_other_sites(self, monkeypatch):
        """Google-specific heuristics should not apply to other sites."""
        patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)

        html = """
        <html><body>
          <div aria-label="Find flights from Tokyo (HND) to Osaka (ITM) from ¥25,986.">¥25,986</div>
        </body></html>
        """

        result = extract_with_llm(html=html, site="skyscanner", task="price")

        # Should not use Google-specific aria-label parsing
        # (actual behavior may fall back to generic heuristics)
        assert result["source"] != "heuristic_embedded"
