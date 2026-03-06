"""Stage-4 parity tests for concrete service plugins."""

from urllib.parse import parse_qs, urlparse

from core import services as legacy_services
from core.plugins.registry import get_service


def test_service_plugins_metadata_and_ui_profile_key():
    """Service plugins should expose stable metadata and ui_profile_key."""
    for key in ("google_flights", "skyscanner"):
        plugin = get_service(key)
        assert plugin.service_key == key
        assert plugin.key == key
        assert plugin.ui_profile_key == key
        assert plugin.display_name == legacy_services.service_name(key)
        if key == "google_flights":
            parsed = urlparse(plugin.default_url)
            query = parse_qs(parsed.query)
            assert plugin.default_url.startswith(legacy_services.default_service_url(key))
            assert query.get("hl") == ["en"]
        else:
            assert plugin.default_url == legacy_services.default_service_url(key)
        assert plugin.base_domains
        assert plugin.domains
        profile = plugin.scenario_profile()
        assert isinstance(profile, dict)
        probe = plugin.readiness_probe("<html></html>", inputs={})
        assert isinstance(probe, dict)
        for required in ("ready", "page_class", "trip_product", "route_bound", "reason"):
            assert required in probe
        hints = plugin.extraction_hints("<html></html>", inputs={})
        assert isinstance(hints, dict)
        readiness_hints = plugin.readiness_hints(inputs={})
        assert isinstance(readiness_hints, dict)
        scope_hints = plugin.scope_hints(inputs={})
        assert isinstance(scope_hints, dict)


def test_service_plugins_url_candidates_match_legacy():
    """Concrete plugins must delegate to legacy URL-candidate ordering behavior."""
    inputs = [
        (
            "google_flights",
            {
                "preferred_url": "https://www.google.com/travel/flights",
                "is_domestic": True,
                "knowledge": {"site_type": "single_flow"},
                "seed_hints": {"generic": [], "domestic": [], "international": [], "package": []},
            },
        ),
        (
            "skyscanner",
            {
                "preferred_url": "https://www.skyscanner.com/flights",
                "is_domestic": False,
                "knowledge": {"site_type": "single_flow"},
                "seed_hints": {"generic": ["https://www.skyscanner.net/flights"], "domestic": [], "international": [], "package": []},
            },
        ),
    ]

    for service_key, kwargs in inputs:
        plugin = get_service(service_key)
        plugin_out = plugin.url_candidates(**kwargs)
        legacy_out = legacy_services.service_url_candidates(service_key, **kwargs)
        if service_key == "google_flights":
            assert len(plugin_out) == len(legacy_out)
            for got, expected in zip(plugin_out, legacy_out):
                got_parsed = urlparse(got)
                exp_parsed = urlparse(expected)
                got_q = parse_qs(got_parsed.query)
                exp_q = parse_qs(exp_parsed.query)
                assert got_parsed.scheme == exp_parsed.scheme
                assert got_parsed.netloc == exp_parsed.netloc
                assert got_parsed.path == exp_parsed.path
                assert got_parsed.fragment == exp_parsed.fragment
                assert got_q.get("hl") == ["en"]
                got_q_no_hl = {k: v for k, v in got_q.items() if k != "hl"}
                exp_q_no_hl = {k: v for k, v in exp_q.items() if k != "hl"}
                assert got_q_no_hl == exp_q_no_hl
        else:
            assert plugin_out == legacy_out
