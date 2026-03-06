from __future__ import annotations

from pathlib import Path

from core.plugins.services.google_flights import extract_price_from_html


def test_google_flights_extract_price_from_fixture_html():
    html = Path("tests/fixtures/google_flights/results_sample.html").read_text(encoding="utf-8")
    result = extract_price_from_html(
        html,
        page_url="https://www.google.com/travel/flights",
    )

    if result.get("ok") and result.get("price") is not None:
        assert result["price"] == 456
        assert result["currency"] == "USD"
    else:
        assert result["reason_code"] == "missing_price"
        assert isinstance(result.get("evidence"), dict)


def test_google_flights_extract_price_ignores_script_noise_and_weak_consent_tokens():
    html = """
    <html><body>
      <script>
        window.WIZ_global_data = {"foo":"同意", "bar":"$1"};
      </script>
      <div>Flights</div>
      <div>Search results</div>
      <div>31 results returned.</div>
      <div>From ¥10,420</div>
      <div>Privacy</div>
    </body></html>
    """
    result = extract_price_from_html(html, page_url="https://www.google.com/travel/flights?hl=en&gl=JP")

    assert result.get("ok") is True
    assert result.get("page_kind") == "flights_results"
    assert result.get("price") == 10420
    assert result.get("currency") == "JPY"
