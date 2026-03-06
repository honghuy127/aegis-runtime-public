"""Tests for normalized plugin-oriented service config loader."""

from core.plugins.services.config_loader import load_service_plugin_config


def test_load_service_plugin_config_normalizes_legacy_keys(tmp_path):
    """Loader should preserve legacy services.yaml keys while exposing normalized view."""
    cfg_path = tmp_path / "services.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://example.com/google",
                "google_flights_url_hints: https://example.com/google/hint",
            ]
        ),
        encoding="utf-8",
    )

    normalized = load_service_plugin_config(str(cfg_path))
    assert normalized["enabled_service_keys"] == ["google_flights"]
    assert normalized["per_service"]["google_flights"]["preferred_url"] == "https://example.com/google"
    assert normalized["per_service"]["google_flights"]["seed_hints"]["generic"] == [
        "https://example.com/google/hint"
    ]
