from __future__ import annotations

from pathlib import Path

from core.plugins.services.skyscanner import extract_price_from_html


def test_skyscanner_extract_price_from_fixture_html():
    html = Path("tests/fixtures/skyscanner/results_sample.html").read_text(encoding="utf-8")
    result = extract_price_from_html(
        html,
        page_url="https://www.skyscanner.com/transport/flights/lax/jfk/",
    )

    if result.get("ok") and result.get("price") is not None:
        assert result["price"] == 123
        assert result["currency"] == "USD"
    else:
        assert result["reason_code"] == "missing_price"
        assert isinstance(result.get("evidence"), dict)


def test_skyscanner_homepage_search_form_is_not_treated_as_results():
    html = """
    <html>
      <head>
        <title>最もお得な航空券・航空券予約の情報を検索 | スカイスキャナー</title>
        <link rel="canonical" href="https://www.skyscanner.com/flights" />
      </head>
      <body>
        <main>
          <input id="originInput-input" name="originInput-search" value="FUK" />
          <input id="destinationInput-input" name="destinationInput-search" value="HND" />
          <button data-testid="depart-btn">2026/05/02</button>
          <button data-testid="return-btn">2026/06/08</button>
          <div>from ¥6,128</div>
        </main>
      </body>
    </html>
    """
    result = extract_price_from_html(
        html,
        page_url="https://www.skyscanner.com/flights",
    )
    assert result["ok"] is False
    assert result["reason_code"] == "missing_price"
    assert result["page_kind"] == "search_form"
    gating = dict((result.get("evidence") or {}).get("gating_decisions", {}) or {})
    assert gating.get("on_results_page") is False
