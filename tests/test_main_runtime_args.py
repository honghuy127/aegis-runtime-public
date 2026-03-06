"""Tests for CLI/config runtime argument merge behavior in main.py."""

import os
from argparse import Namespace

from main import (
    _adjust_salvage_max_attempts_for_scenario_proven_missing_price,
    _adaptive_scenario_candidate_timeout_sec,
    _google_flights_bootstrap_mode,
    _order_google_flights_url_candidates,
    _is_google_host,
    _resolve_runtime_args,
    _should_salvage_extract,
    run_multi_service,
)
from utils.thresholds import get_threshold, get_thresholds_for_profile


def _base_args(input_config: str) -> Namespace:
    """Build a minimal args namespace matching main._parse_args shape."""
    return Namespace(
        origin=None,
        dest=None,
        depart=None,
        return_date=None,
        is_domestic=None,
        max_trip_price=None,
        max_transit=None,
        trip_type=None,
        plan_file=None,
        services_config="configs/services.yaml",
        services=None,
        knowledge_user=None,
        task=None,
        save_html=False,
        llm_mode=None,
        agentic_multimodal_mode=None,
        human_mimic=None,
        mimic_locale=None,
        mimic_timezone=None,
        mimic_currency=None,
        mimic_region=None,
        mimic_latitude=None,
        mimic_longitude=None,
        input_config=input_config,
        alerts_config="configs/alerts.yaml",
        disable_alerts=False,
        debug=False,
        debug_dir="storage/runs",
        debug_keep=0,
        run_id=None,
    )


def test_runtime_args_uses_input_config_when_cli_missing(tmp_path):
    """When no CLI trip params are passed, config values should be used."""
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
                "max_trip_price: 15000",
                "max_transit: 1",
                "llm_mode: light",
                "human_mimic: true",
                "mimic_locale: ja-JP",
                "mimic_timezone: Asia/Tokyo",
                "mimic_currency: JPY",
                "mimic_region: JP",
                "mimic_latitude: 35.6762",
                "mimic_longitude: 139.6503",
                "knowledge_user: user@example.com",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))

    runtime = _resolve_runtime_args(args)
    assert runtime["origin"] == "HND"
    assert runtime["dest"] == "ITM"
    assert runtime["trip_type"] == "round_trip"
    assert runtime["is_domestic"] is True
    assert runtime["max_trip_price"] == 15000.0
    assert runtime["max_transit"] == 1
    assert runtime["llm_mode"] == "light"
    assert runtime["human_mimic"] is True
    assert runtime["mimic_locale"] == "ja-JP"
    assert runtime["mimic_timezone"] == "Asia/Tokyo"
    assert runtime["mimic_currency"] == "JPY"
    assert runtime["mimic_region"] == "JP"
    assert runtime["mimic_latitude"] == 35.6762
    assert runtime["mimic_longitude"] == 139.6503
    assert runtime["knowledge_user"] == "user@example.com"


def test_is_google_host_supports_regional_google_domains():
    """Google host detector should accept google.* regional domains."""
    assert _is_google_host("www.google.com") is True
    assert _is_google_host("www.google.co.jp") is True
    assert _is_google_host("google.co.uk") is True
    assert _is_google_host("travel.google.de") is True
    assert _is_google_host("notgoogle.com") is False
    assert _is_google_host("googleusercontent.com") is False


def test_should_salvage_extract_skips_non_flight_scope_signal():
    """Non-flight scope results should skip expensive salvage retries."""
    result = {
        "price": None,
        "currency": None,
        "confidence": "low",
        "source": "vlm",
        "reason": "vlm_non_flight_scope",
    }
    assert _should_salvage_extract(result) is False
    result["reason"] = "google_route_context_unbound"
    assert _should_salvage_extract(result) is False
    result["reason"] = "scope_non_flight_flight_hotel_package"
    assert _should_salvage_extract(result) is False


def test_adjust_salvage_max_attempts_caps_missing_price_after_scenario_proven_context():
    out = _adjust_salvage_max_attempts_for_scenario_proven_missing_price(
        base_max_attempts=3,
        result={"price": None, "reason": "missing_price"},
        scenario_scope_guard_overrides_active=True,
    )
    assert out == 1


def test_adjust_salvage_max_attempts_keeps_attempts_without_scenario_proven_context():
    out = _adjust_salvage_max_attempts_for_scenario_proven_missing_price(
        base_max_attempts=3,
        result={"price": None, "reason": "missing_price"},
        scenario_scope_guard_overrides_active=False,
    )
    assert out == 3


def test_adaptive_candidate_timeout_expands_in_light_mode_escalation():
    """Scenario timeout should expand when adaptive planner timeout is large."""
    out = _adaptive_scenario_candidate_timeout_sec(
        base_timeout_sec=120,
        llm_mode="light",
        adaptive_profile={
            "reason": "enable_extract_escalation",
            "llm_light_planner_timeout_sec": 360,
        },
    )
    assert out >= 540


def test_adaptive_candidate_timeout_keeps_base_for_non_light_mode():
    """Non-light mode should keep configured base scenario timeout."""
    out = _adaptive_scenario_candidate_timeout_sec(
        base_timeout_sec=120,
        llm_mode="full",
        adaptive_profile={
            "reason": "non_light_mode",
            "llm_light_planner_timeout_sec": 360,
        },
    )
    assert out == 120


def test_adaptive_candidate_timeout_respects_env_cap_override(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_CAP_SEC", "21600")
    out = _adaptive_scenario_candidate_timeout_sec(
        base_timeout_sec=120,
        llm_mode="light",
        adaptive_profile={
            "reason": "enable_extract_escalation",
            "llm_light_planner_timeout_sec": 7200,
            "llm_light_extract_timeout_sec": 14400,
        },
    )
    assert out > 3600
    assert out <= 21600


def test_runtime_args_cli_overrides_input_config(tmp_path):
    """CLI values should override run.yaml defaults when provided."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "trip_type: round_trip",
                "max_trip_price: 15000",
                "max_transit: 2",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))
    args.origin = "NRT"
    args.is_domestic = False
    args.max_trip_price = 9999.0
    args.max_transit = 0
    args.human_mimic = False
    args.mimic_locale = "en-US"
    args.mimic_timezone = "UTC"
    args.mimic_currency = "USD"
    args.mimic_region = "US"
    args.mimic_latitude = 37.7749
    args.mimic_longitude = -122.4194
    args.knowledge_user = "gh:alice"
    args.trip_type = "one_way"
    args.llm_mode = "full"

    runtime = _resolve_runtime_args(args)
    assert runtime["origin"] == "NRT"
    assert runtime["trip_type"] == "one_way"
    assert runtime["is_domestic"] is False
    assert runtime["max_trip_price"] == 9999.0
    assert runtime["max_transit"] == 0
    assert runtime["llm_mode"] == "full"
    assert runtime["human_mimic"] is False
    assert runtime["mimic_locale"] == "en-US"
    assert runtime["mimic_timezone"] == "UTC"
    assert runtime["mimic_currency"] == "USD"
    assert runtime["mimic_region"] == "US"
    assert runtime["mimic_latitude"] == 37.7749
    assert runtime["mimic_longitude"] == -122.4194
    assert runtime["knowledge_user"] == "gh:alice"


def test_runtime_args_respects_env_scenario_candidate_timeout_override(tmp_path, monkeypatch):
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLIGHT_WATCHER_SCENARIO_CANDIDATE_TIMEOUT_SEC", "7200")
    args = _base_args(str(cfg_path))

    runtime = _resolve_runtime_args(args)
    assert runtime["scenario_candidate_timeout_sec"] == 7200


def test_runtime_args_accepts_multimodal_judge_mode(tmp_path):
    """Runtime config should preserve multimodal judge mode."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "agentic_multimodal_mode: judge",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))
    runtime = _resolve_runtime_args(args)
    assert runtime["agentic_multimodal_mode"] == "judge"


def test_runtime_args_defaults_google_flights_bootstrap_mode_to_simple_only(tmp_path):
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))
    runtime = _resolve_runtime_args(args)
    assert runtime["google_flights_bootstrap_mode"] == "simple_only"


def test_google_flights_bootstrap_mode_env_override_accepts_deeplink_first(tmp_path, monkeypatch):
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "google_flights_bootstrap_mode: simple_only",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLIGHT_WATCHER_GOOGLE_FLIGHTS_BOOTSTRAP_MODE", "deeplink_first")
    args = _base_args(str(cfg_path))
    runtime = _resolve_runtime_args(args)
    assert runtime["google_flights_bootstrap_mode"] == "deeplink_first"


def test_order_google_flights_url_candidates_defaults_to_simple_only():
    service_url = "https://www.google.com/travel/flights"
    ordered = _order_google_flights_url_candidates(
        url_candidates=[service_url],
        service_url=service_url,
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
        trip_type="round_trip",
        currency="JPY",
        locale="ja-JP",
        region="JP",
        bootstrap_mode="",
    )
    assert ordered[0] == "https://www.google.com/travel/flights?hl=en&gl=JP"
    assert all("#flt=" not in u for u in ordered[:1])


def test_google_flights_bootstrap_mode_normalizes_invalid_to_simple_only():
    assert _google_flights_bootstrap_mode(None) == "simple_only"
    assert _google_flights_bootstrap_mode("SIMPLE-FIRST") == "simple_first"
    assert _google_flights_bootstrap_mode("weird") == "simple_only"


def test_order_google_flights_url_candidates_allows_explicit_deeplink_first():
    service_url = "https://www.google.com/travel/flights"
    ordered = _order_google_flights_url_candidates(
        url_candidates=[service_url],
        service_url=service_url,
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
        trip_type="round_trip",
        currency="JPY",
        locale="en-US",
        region="JP",
        bootstrap_mode="deeplink_first",
    )
    assert "#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08" in ordered[0]
    assert any(u == "https://www.google.com/travel/flights?hl=en&gl=JP" for u in ordered)


def test_runtime_args_accepts_multimodal_judge_primary_mode(tmp_path):
    """Runtime config should preserve multimodal judge_primary mode."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "agentic_multimodal_mode: judge_primary",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))
    runtime = _resolve_runtime_args(args)
    assert runtime["agentic_multimodal_mode"] == "judge_primary"


def test_runtime_args_invalid_llm_mode_falls_back_to_full(tmp_path):
    """Invalid llm_mode from config should normalize to full."""
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "llm_mode: random_mode",
            ]
        ),
        encoding="utf-8",
    )
    args = _base_args(str(cfg_path))
    runtime = _resolve_runtime_args(args)
    assert runtime["llm_mode"] == "full"


def test_run_multi_service_prefers_services_yaml_over_run_yaml_services(
    tmp_path,
    monkeypatch,
):
    """Service set should come from services.yaml unless CLI --services is used."""
    run_cfg = tmp_path / "run.yaml"
    run_cfg.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "return_date: 2099-03-08",
                "trip_type: round_trip",
                # This must be ignored now.
                "services: google_flights",
            ]
        ),
        encoding="utf-8",
    )
    services_cfg = tmp_path / "services.yaml"
    services_cfg.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://example.com/google",
            ]
        ),
        encoding="utf-8",
    )
    alerts_cfg = tmp_path / "alerts.yaml"
    alerts_cfg.write_text("enabled: false\n", encoding="utf-8")

    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.run_agentic_scenario", lambda **kwargs: "<html></html>")
    monkeypatch.setattr(
        "main.extract_price",
        lambda html, site, task: {
            "price": 10000.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "test",
            "reason": "",
        },
    )
    monkeypatch.setattr("main.get_last_price_record", lambda site, task: None)
    monkeypatch.setattr("main.save_run", lambda **kwargs: None)
    monkeypatch.setattr(
        "main.evaluate_alert",
        lambda **kwargs: {"should_alert": False, "reason": "alerts_disabled"},
    )

    args = Namespace(
        origin=None,
        dest=None,
        depart=None,
        return_date=None,
        is_domestic=None,
        max_trip_price=None,
        max_transit=None,
        trip_type=None,
        plan_file=None,
        services_config=str(services_cfg),
        services=None,
        knowledge_user=None,
        task=None,
        save_html=False,
        llm_mode=None,
        agentic_multimodal_mode=None,
        human_mimic=None,
        mimic_locale=None,
        mimic_timezone=None,
        mimic_currency=None,
        mimic_region=None,
        mimic_latitude=None,
        mimic_longitude=None,
        input_config=str(run_cfg),
        alerts_config=str(alerts_cfg),
        disable_alerts=False,
        debug=False,
        debug_dir="storage/runs",
        debug_keep=0,
        run_id=None,
    )

    outputs = run_multi_service(args)
    assert [item["service"] for item in outputs] == ["google_flights"]


def test_run_multi_service_allows_service_override(
    tmp_path,
    monkeypatch,
):
    """Multi-service run should use configured services."""
    run_cfg = tmp_path / "run.yaml"
    run_cfg.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "return_date: 2099-03-08",
                "trip_type: round_trip",
                "is_domestic: true",
            ]
        ),
        encoding="utf-8",
    )
    services_cfg = tmp_path / "services.yaml"
    services_cfg.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://www.google.com/travel/flights",
            ]
        ),
        encoding="utf-8",
    )
    alerts_cfg = tmp_path / "alerts.yaml"
    alerts_cfg.write_text("enabled: false\n", encoding="utf-8")

    monkeypatch.setattr("main.init_db", lambda: None)
    visited = []

    def _fake_scenario(**kwargs):
        visited.append(kwargs.get("url"))
        url = kwargs.get("url") or ""
        if "google.com" in url:
            return "<div>Flight results $1200</div>"
        raise RuntimeError("Wrong domain")

    monkeypatch.setattr("main.run_agentic_scenario", _fake_scenario)
    monkeypatch.setattr(
        "main.extract_price",
        lambda html, site, task: {
            "price": 1200.0,
            "currency": "USD",
            "confidence": "low",
            "selector_hint": None,
            "source": "test",
            "reason": "",
        },
    )
    monkeypatch.setattr("main.get_last_price_record", lambda site, task: None)
    monkeypatch.setattr("main.save_run", lambda **kwargs: None)
    monkeypatch.setattr(
        "main.evaluate_alert",
        lambda **kwargs: {"should_alert": False, "reason": "alerts_disabled"},
    )

    args = Namespace(
        origin=None,
        dest=None,
        depart=None,
        return_date=None,
        is_domestic=None,
        max_trip_price=None,
        max_transit=None,
        trip_type=None,
        plan_file=None,
        services_config=str(services_cfg),
        services=None,
        knowledge_user=None,
        task=None,
        save_html=False,
        llm_mode=None,
        agentic_multimodal_mode=None,
        human_mimic=None,
        mimic_locale=None,
        mimic_timezone=None,
        mimic_currency=None,
        mimic_region=None,
        mimic_latitude=None,
        mimic_longitude=None,
        input_config=str(run_cfg),
        alerts_config=str(alerts_cfg),
        disable_alerts=False,
        debug=False,
        debug_dir="storage/runs",
        debug_keep=0,
        run_id=None,
    )

    outputs = run_multi_service(args)
    assert outputs[0]["status"] == "ok"
    assert "google.com" in outputs[0]["url"]
    assert visited[0].startswith("https://www.google.com")




def test_run_multi_service_sets_llm_mode_env_from_runtime(tmp_path, monkeypatch):
    """run_multi_service should export FLIGHT_WATCHER_LLM_MODE for llm.code_model."""
    run_cfg = tmp_path / "run.yaml"
    run_cfg.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "return_date: 2099-03-08",
                "trip_type: round_trip",
                "llm_mode: light",
            ]
        ),
        encoding="utf-8",
    )
    services_cfg = tmp_path / "services.yaml"
    services_cfg.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://example.com/google",
            ]
        ),
        encoding="utf-8",
    )
    alerts_cfg = tmp_path / "alerts.yaml"
    alerts_cfg.write_text("enabled: false\n", encoding="utf-8")

    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.run_agentic_scenario", lambda **kwargs: "<html></html>")
    monkeypatch.setattr(
        "main.extract_price",
        lambda html, site, task: {
            "price": 10000.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "test",
            "reason": "",
        },
    )
    monkeypatch.setattr("main.get_last_price_record", lambda site, task: None)
    monkeypatch.setattr("main.save_run", lambda **kwargs: None)
    monkeypatch.setattr(
        "main.evaluate_alert",
        lambda **kwargs: {"should_alert": False, "reason": "alerts_disabled"},
    )

    args = Namespace(
        origin=None,
        dest=None,
        depart=None,
        return_date=None,
        is_domestic=None,
        max_trip_price=None,
        max_transit=None,
        trip_type=None,
        plan_file=None,
        services_config=str(services_cfg),
        services=None,
        knowledge_user=None,
        task=None,
        save_html=False,
        llm_mode=None,
        agentic_multimodal_mode=None,
        human_mimic=None,
        mimic_locale=None,
        mimic_timezone=None,
        mimic_currency=None,
        mimic_region=None,
        mimic_latitude=None,
        mimic_longitude=None,
        input_config=str(run_cfg),
        alerts_config=str(alerts_cfg),
        disable_alerts=False,
        debug=False,
        debug_dir="storage/runs",
        debug_keep=0,
        run_id=None,
    )

    run_multi_service(args)
    assert os.getenv("FLIGHT_WATCHER_LLM_MODE") == "light"


def test_run_multi_service_applies_threshold_profile_to_downstream_runtime_path(
    tmp_path,
    monkeypatch,
):
    """A debug thresholds_profile should affect get_threshold() inside runtime callbacks."""
    run_cfg = tmp_path / "run.yaml"
    run_cfg.write_text(
        "\n".join(
            [
                "origin: HND",
                "dest: ITM",
                "depart: 2099-03-01",
                "return_date: 2099-03-08",
                "trip_type: round_trip",
                "thresholds_profile: debug",
            ]
        ),
        encoding="utf-8",
    )
    services_cfg = tmp_path / "services.yaml"
    services_cfg.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://example.com/google",
            ]
        ),
        encoding="utf-8",
    )
    alerts_cfg = tmp_path / "alerts.yaml"
    alerts_cfg.write_text("enabled: false\n", encoding="utf-8")

    monkeypatch.setattr("main.init_db", lambda: None)
    observed = {}
    key = "browser_action_selector_timeout_ms"
    base_value = get_threshold(key, 0)
    debug_value = get_thresholds_for_profile("debug").get(key, base_value)

    def _fake_scenario(**kwargs):
        del kwargs
        observed["threshold"] = get_threshold(key, 0)
        return "<html></html>"

    monkeypatch.setattr("main.run_agentic_scenario", _fake_scenario)
    monkeypatch.setattr(
        "main.extract_price",
        lambda html, site, task: {
            "price": 10000.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "test",
            "reason": "",
        },
    )
    monkeypatch.setattr("main.get_last_price_record", lambda site, task: None)
    monkeypatch.setattr("main.save_run", lambda **kwargs: None)
    monkeypatch.setattr(
        "main.evaluate_alert",
        lambda **kwargs: {"should_alert": False, "reason": "alerts_disabled"},
    )

    args = _base_args(str(run_cfg))
    args.services_config = str(services_cfg)
    args.alerts_config = str(alerts_cfg)

    outputs = run_multi_service(args)

    assert outputs[0]["status"] == "ok"
    assert observed["threshold"] == debug_value
    assert observed["threshold"] >= base_value
    # Profile activation is scoped to the run and should be reset afterward.
    assert get_threshold(key, 0) == base_value


def test_run_multi_service_executes_all_trips_from_input_config(tmp_path, monkeypatch):
    """When `trips:` is configured and no trip CLI overrides are set, run all trips."""
    run_cfg = tmp_path / "run.yaml"
    run_cfg.write_text(
        "\n".join(
            [
                "trip_type: round_trip",
                "is_domestic: true",
                "trips:",
                "  - origin: HND",
                "    dest: ITM",
                "    depart: 2099-03-01",
                "    return_date: 2099-03-08",
                "  - origin: NRT",
                "    dest: CTS",
                "    depart: 2099-03-10",
                "    return_date: 2099-03-15",
            ]
        ),
        encoding="utf-8",
    )
    services_cfg = tmp_path / "services.yaml"
    services_cfg.write_text(
        "\n".join(
            [
                "enabled_services: google_flights",
                "google_flights_url: https://example.com/google",
            ]
        ),
        encoding="utf-8",
    )
    alerts_cfg = tmp_path / "alerts.yaml"
    alerts_cfg.write_text("enabled: false\n", encoding="utf-8")

    monkeypatch.setattr("main.init_db", lambda: None)
    visited = []

    def _fake_scenario(**kwargs):
        visited.append((kwargs["origin"], kwargs["dest"], kwargs["depart"]))
        return "<html></html>"

    monkeypatch.setattr("main.run_agentic_scenario", _fake_scenario)
    monkeypatch.setattr(
        "main.extract_price",
        lambda html, site, task: {
            "price": 10000.0,
            "currency": "JPY",
            "confidence": "low",
            "selector_hint": None,
            "source": "test",
            "reason": "",
        },
    )
    monkeypatch.setattr("main.get_last_price_record", lambda site, task: None)
    monkeypatch.setattr("main.save_run", lambda **kwargs: None)
    monkeypatch.setattr(
        "main.evaluate_alert",
        lambda **kwargs: {"should_alert": False, "reason": "alerts_disabled"},
    )

    args = _base_args(str(run_cfg))
    args.services_config = str(services_cfg)
    args.alerts_config = str(alerts_cfg)
    outputs = run_multi_service(args)

    assert len(outputs) == 2
    assert visited == [
        ("HND", "ITM", "2099-03-01"),
        ("NRT", "CTS", "2099-03-10"),
    ]
    assert outputs[0]["trip_index"] == 1
    assert outputs[1]["trip_index"] == 2
    assert outputs[0]["task"] != outputs[1]["task"]
