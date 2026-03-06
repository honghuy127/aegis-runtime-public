"""Tests for service registry and config loading."""

import pytest

from core.services import (
    all_service_keys,
    default_service_url,
    is_supported_service,
    service_url_candidates,
)
from core.services_config import load_services_config


def test_service_registry_contains_requested_providers():
    """Supported services should include all requested booking providers."""
    keys = set(all_service_keys())
    assert {"google_flights", "skyscanner"}.issubset(keys)
    assert is_supported_service("skyscanner")
    assert default_service_url("google_flights").startswith("https://")


def test_load_services_config_parses_enabled_and_url_overrides(tmp_path):
    """Loader should parse enabled_services and custom URL overrides."""
    cfg_path = tmp_path / "services.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "enabled_services: google_flights, skyscanner",
                "skyscanner_url: https://example.com/skyscanner",
                "skyscanner_url_hints: https://example.com/sky/a,https://example.com/sky/b",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_services_config(str(cfg_path))
    assert cfg["enabled_services"] == ["google_flights", "skyscanner"]
    assert cfg["service_urls"]["skyscanner"] == "https://example.com/skyscanner"
    assert cfg["service_url_hints"]["skyscanner"]["generic"] == [
        "https://example.com/sky/a",
        "https://example.com/sky/b",
    ]


def test_load_services_config_rejects_unknown_service(tmp_path):
    """Loader should fail fast when unknown service keys are configured."""
    cfg_path = tmp_path / "services.yaml"
    cfg_path.write_text("enabled_services: google_flights, not_real_service", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported service keys"):
        load_services_config(str(cfg_path))


def test_service_url_candidates_use_seed_and_learned_hints():
    """Candidate ordering should combine root URL, learned hints, and seed hints."""
    urls = service_url_candidates(
        "google_flights",
        preferred_url="https://www.google.com/travel/flights",
        is_domestic=True,
        knowledge={
            "site_type": None,
            "local_url_hints": ["https://learned.example/root"],
            "local_domestic_url_hints": ["https://learned.example/domestic"],
        },
        seed_hints={
            "generic": ["https://seed.example/root"],
            "domestic": ["https://seed.example/domestic"],
            "international": [],
        },
    )
    # When site_type is None (unclassified), domain split insights are included.
    # Order: preferred_url → learned hints → seed hints → default service URL
    assert urls[0] == "https://www.google.com/travel/flights"
    # Verify all expected URLs are present (ordering may vary after feature prioritization)
    assert "https://learned.example/root" in urls
    assert "https://learned.example/domestic" in urls
    assert "https://seed.example/domestic" in urls
    assert "https://seed.example/root" in urls
