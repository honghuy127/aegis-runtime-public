"""Tests for run-input config loading."""

import re
from pathlib import Path

from core.run_input_config import load_run_input_config


def test_load_run_input_config_parses_supported_fields(tmp_path):
    """Loader should parse core fields and type-convert supported values."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "return_date: 2099-03-08",
                "trip_type: round_trip",
                "is_domestic: true",
                "max_trip_price: 12345.5",
                "max_transit: 1",
                "task: price",
                "save_html: true",
                "debug_save_service_html: false",
                "llm_mode: light",
                "human_mimic: true",
                "mimic_locale: ja-JP",
                "mimic_timezone: Asia/Tokyo",
                "mimic_currency: JPY",
                "mimic_region: JP",
                "mimic_latitude: 35.6762",
                "mimic_longitude: 139.6503",
                "knowledge_user: user@example.com",
                "disable_alerts: false",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_run_input_config(str(cfg_path))
    assert cfg["origin"] == "HND"
    assert cfg["dest"] == "ITM"
    assert cfg["trip_type"] == "round_trip"
    assert cfg["is_domestic"] is True
    assert cfg["max_trip_price"] == 12345.5
    assert cfg["max_transit"] == 1
    assert cfg["save_html"] is True
    assert cfg["debug_save_service_html"] is False
    assert cfg["llm_mode"] == "light"
    assert cfg["human_mimic"] is True
    assert cfg["mimic_locale"] == "ja-JP"
    assert cfg["mimic_timezone"] == "Asia/Tokyo"
    assert cfg["mimic_currency"] == "JPY"
    assert cfg["mimic_region"] == "JP"
    assert cfg["mimic_latitude"] == 35.6762
    assert cfg["mimic_longitude"] == 139.6503
    assert cfg["knowledge_user"] == "user@example.com"
    assert cfg["disable_alerts"] is False


def test_load_run_input_config_uses_safe_defaults_when_missing_file(tmp_path):
    """Missing file should still provide non-null runtime defaults."""
    cfg = load_run_input_config(str(tmp_path / "does_not_exist.yaml"))
    assert cfg["trip_type"] == "round_trip"
    assert cfg["is_domestic"] is True
    assert cfg["llm_mode"] == "full"
    assert cfg["debug_save_service_html"] is True
    assert cfg["human_mimic"] is True
    assert cfg["mimic_locale"] == "ja-JP"
    assert cfg["mimic_timezone"] == "Asia/Tokyo"


def test_load_run_input_config_knowledge_user_falls_back_to_env(tmp_path, monkeypatch):
    """Knowledge user should fallback to env when not set in config."""
    monkeypatch.setenv("FLIGHT_WATCHER_USER", "gh:bob")
    cfg = load_run_input_config(str(tmp_path / "missing.yaml"))
    assert cfg["knowledge_user"] == "gh:bob"


def test_load_run_input_config_invalid_llm_mode_falls_back_to_full(tmp_path):
    """Unsupported llm_mode values should be normalized to full."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text("llm_mode: turbo\n", encoding="utf-8")

    cfg = load_run_input_config(str(cfg_path))
    assert cfg["llm_mode"] == "full"


def test_load_run_input_config_parses_multi_trip_block(tmp_path):
    """Loader should parse `trips:` YAML-like list blocks from run.yaml."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "trip_type: round_trip",
                "is_domestic: true",
                "trips:",
                "  - origin: HND",
                "    dest: ITM",
                "    depart: 2099-03-01",
                "    return_date: 2099-03-08",
                "    max_trip_price: 15000",
                "  - origin: NRT",
                "    dest: CTS",
                "    depart: 2099-03-10",
                "    return_date: 2099-03-15",
                "    max_transit: 0",
                "task: price",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_run_input_config(str(cfg_path))
    assert len(cfg["trips"]) == 2
    assert cfg["trips"][0]["origin"] == "HND"
    assert cfg["trips"][0]["max_trip_price"] == 15000.0
    assert cfg["trips"][1]["origin"] == "NRT"
    assert cfg["trips"][1]["max_transit"] == 0


def test_load_run_input_config_parses_extended_runtime_flags(tmp_path):
    """Loader should expose typed values for declared advanced run.yaml knobs."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "kb_cards_enabled: true",
                "auto_heal_enabled: true",
                "auto_heal_apply_patch: false",
                "auto_heal_max_files: 3",
                "auto_heal_max_changed_lines: 120",
                "auto_heal_test_cmd: pytest -q tests/test_auto_heal.py",
                "auto_heal_llm_enabled: true",
                "thresholds_profile: debug",
                "adaptive_escalation_enabled: false",
                "escalation_reason_repeat_threshold: 4",
                "escalation_soft_fail_threshold: 5",
                "escalation_max_turns_without_ready: 6",
                "escalation_route_fill_mismatch_threshold: 7",
                "escalation_calendar_loop_detection: false",
                "graph_policy_stats_enabled: true",
                "graph_policy_stats_global_enabled: true",
                "graph_policy_stats_global_path: storage/custom_graph_stats.json",
                "calendar_selector_scoring_enabled: false",
                "calendar_verify_after_commit: false",
                "calendar_parsing_utility: legacy",
                "calendar_snapshot_on_failure: false",
                "calendar_snapshot_write_md: true",
                "calendar_snapshot_max_chars: 54321",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_run_input_config(str(cfg_path))

    assert cfg["kb_cards_enabled"] is True
    assert cfg["auto_heal_enabled"] is True
    assert cfg["auto_heal_apply_patch"] is False
    assert cfg["auto_heal_max_files"] == 3
    assert cfg["auto_heal_max_changed_lines"] == 120
    assert cfg["auto_heal_test_cmd"] == "pytest -q tests/test_auto_heal.py"
    assert cfg["auto_heal_llm_enabled"] is True
    assert cfg["thresholds_profile"] == "debug"
    assert cfg["adaptive_escalation_enabled"] is False
    assert cfg["escalation_reason_repeat_threshold"] == 4
    assert cfg["escalation_soft_fail_threshold"] == 5
    assert cfg["escalation_max_turns_without_ready"] == 6
    assert cfg["escalation_route_fill_mismatch_threshold"] == 7
    assert cfg["escalation_calendar_loop_detection"] is False
    assert cfg["graph_policy_stats_enabled"] is True
    assert cfg["graph_policy_stats_global_enabled"] is True
    assert cfg["graph_policy_stats_global_path"] == "storage/custom_graph_stats.json"
    assert cfg["calendar_selector_scoring_enabled"] is False
    assert cfg["calendar_verify_after_commit"] is False
    assert cfg["calendar_parsing_utility"] == "legacy"
    assert cfg["calendar_snapshot_on_failure"] is False
    assert cfg["calendar_snapshot_write_md"] is True
    assert cfg["calendar_snapshot_max_chars"] == 54321


def test_load_run_input_config_invalid_thresholds_profile_falls_back_to_default(tmp_path):
    """Unsupported thresholds_profile values should normalize to default."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text("thresholds_profile: turbo\n", encoding="utf-8")

    cfg = load_run_input_config(str(cfg_path))
    assert cfg["thresholds_profile"] == "default"


def test_repo_run_yaml_top_level_scalar_keys_round_trip_through_loader():
    """Audit: checked-in `configs/run.yaml` top-level scalar keys should be loader-visible."""
    run_yaml = Path("configs/run.yaml")
    text = run_yaml.read_text(encoding="utf-8")

    # Keep this small and explicit: `trips` is a block list, not a scalar key line.
    # `google_flights_bootstrap_mode` is used at runtime level above loader, not in load_run_input_config
    ignore_keys = {"trips", "google_flights_bootstrap_mode"}
    declared_top_level_keys = set()
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
        if m:
            key = m.group(1)
            if key not in ignore_keys:
                declared_top_level_keys.add(key)

    cfg = load_run_input_config(str(run_yaml))
    missing = sorted(key for key in declared_top_level_keys if key not in cfg)
    assert missing == []
