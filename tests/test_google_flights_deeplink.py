"""Google Flights deeplink normalization tests."""

from urllib.parse import parse_qs, urlparse

from core.plugins.services.google_flights import (
    GoogleFlightsServicePlugin,
    build_google_flights_deeplink,
)


def _fragment_tokens(url: str):
    fragment = urlparse(url).fragment or ""
    return [token.strip() for token in fragment.split(";") if token.strip()]


def test_build_deeplink_prefers_english_ui_but_preserves_region_and_currency():
    """Deeplink should prefer hl=en for judgeability while preserving gl/c."""
    url = build_google_flights_deeplink(
        {
            "origin": "HND",
            "dest": "ITM",
            "depart": "2026-03-01",
            "return_date": "2026-03-08",
            "trip_type": "round_trip",
        },
        {
            "mimic_locale": "ja-JP",
            "mimic_region": "JP",
            "mimic_currency": "JPY",
        },
        base_url="https://www.google.com/travel/flights#e:1;sd:1;t:f",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query.get("hl") == ["en"]
    assert query.get("gl") == ["JP"]
    tokens = _fragment_tokens(url)
    assert any(token.startswith("flt=HND.ITM.2026-03-01*ITM.HND.2026-03-08") for token in tokens)
    assert "c:JPY" in tokens


def test_build_deeplink_uses_en_us_usd_mimic_params():
    """Deeplink should preserve region/currency and keep English UI on EN profiles too."""
    url = build_google_flights_deeplink(
        {
            "origin": "NRT",
            "dest": "CTS",
            "depart": "2026-04-10",
            "trip_type": "one_way",
        },
        {
            "mimic_locale": "en-US",
            "mimic_region": "US",
            "mimic_currency": "USD",
        },
        base_url="https://www.google.com/travel/flights#e:1;sd:1;t:f",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query.get("hl") == ["en"]
    assert query.get("gl") == ["US"]
    tokens = _fragment_tokens(url)
    assert any(token.startswith("flt=NRT.CTS.2026-04-10") for token in tokens)
    assert "c:USD" in tokens


def test_build_deeplink_replaces_existing_hl_gl_and_currency_without_duplicates():
    """Existing hl/gl/query and c fragment should be replaced deterministically."""
    url = build_google_flights_deeplink(
        {
            "origin": "HND",
            "dest": "ITM",
            "depart": "2026-03-01",
            "return_date": "2026-03-08",
            "trip_type": "round_trip",
        },
        {
            "mimic_locale": "en-US",
            "mimic_region": "US",
            "mimic_currency": "USD",
        },
        base_url=(
            "https://www.google.com/travel/flights?hl=ja&gl=JP&x=1"
            "#flt=OLD.OLD.2000-01-01;c:JPY;e:1;sd:1;t:f"
        ),
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query.get("hl") == ["en"]
    assert query.get("gl") == ["US"]
    assert query.get("x") == ["1"]
    tokens = _fragment_tokens(url)
    assert len([token for token in tokens if token.startswith("flt=")]) == 1
    assert len([token for token in tokens if token.startswith("c:")]) == 1
    assert "c:USD" in tokens


def test_service_plugin_url_candidates_normalize_google_flights_hl_to_en():
    plugin = GoogleFlightsServicePlugin()
    urls = plugin.url_candidates(
        preferred_url="https://www.google.com/travel/flights?hl=ja&gl=JP",
        knowledge={},
        seed_hints={},
    )
    assert urls
    matched = False
    for candidate in urls:
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query)
        if query.get("gl") == ["JP"]:
            assert query.get("hl") == ["en"]
            matched = True
            break
    assert matched, urls
