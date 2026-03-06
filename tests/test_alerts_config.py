"""Tests for alerts configuration loading."""

from core.alerts_config import load_alerts_config


def test_load_alerts_config_defaults(tmp_path):
    """Loader should provide safe defaults when values are omitted."""
    path = tmp_path / "alerts.yaml"
    path.write_text("enabled: true\n", encoding="utf-8")
    cfg = load_alerts_config(str(path))

    assert cfg["enabled"] is True
    assert cfg["alert_direction"] == "drop"
    assert cfg["enabled_channels"] == []


def test_load_alerts_config_parses_channels_and_numbers(tmp_path):
    """Loader should parse channel list and numeric thresholds."""
    path = tmp_path / "alerts.yaml"
    path.write_text(
        "\n".join(
            [
                "enabled: true",
                "enabled_channels: telegram, email",
                "min_absolute_change: 120.5",
                "min_percent_change: 6",
                "cooldown_minutes: 30",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_alerts_config(str(path))

    assert cfg["enabled_channels"] == ["telegram", "email"]
    assert cfg["min_absolute_change"] == 120.5
    assert cfg["min_percent_change"] == 6.0
    assert cfg["cooldown_minutes"] == 30
