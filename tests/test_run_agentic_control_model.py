import importlib.util
from pathlib import Path


def _load_run_agentic_impl_module():
    repo_root = Path(__file__).resolve().parents[1]
    impl_path = repo_root / "core" / "scenario_runner" / "run_agentic_scenario.py"
    spec = importlib.util.spec_from_file_location(
        "tests._run_agentic_impl_control_model",
        str(impl_path),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_control_model_defaults_to_strict_for_light_mode(monkeypatch):
    ras = _load_run_agentic_impl_module()
    monkeypatch.delenv("FLIGHT_WATCHER_CONTROL_MODEL", raising=False)
    assert ras._resolve_control_model("light") == "strict_3layer"


def test_resolve_control_model_honors_explicit_legacy(monkeypatch):
    ras = _load_run_agentic_impl_module()
    monkeypatch.setenv("FLIGHT_WATCHER_CONTROL_MODEL", "legacy")
    assert ras._resolve_control_model("light") == "legacy"


def test_protection_surface_detected_matches_skyscanner_verification_tokens():
    ras = _load_run_agentic_impl_module()
    html = "<html><body>Are you a person or a robot? PRESS & HOLD</body></html>"
    assert ras._protection_surface_detected(html_text=html, reason_text="") is True


def test_allow_layer3_model_escalation_strict_blocks_midflow_without_protection():
    ras = _load_run_agentic_impl_module()
    allowed = ras._allow_layer3_model_escalation(
        control_model="strict_3layer",
        attempt_index=0,
        turn_index=0,
        max_retries=2,
        max_turns=2,
        protection_detected=False,
        used_count=0,
        max_count=1,
    )
    assert allowed is False


def test_allow_layer3_model_escalation_strict_allows_on_final_turn():
    ras = _load_run_agentic_impl_module()
    allowed = ras._allow_layer3_model_escalation(
        control_model="strict_3layer",
        attempt_index=1,
        turn_index=1,
        max_retries=2,
        max_turns=2,
        protection_detected=False,
        used_count=0,
        max_count=1,
    )
    assert allowed is True


def test_allow_layer3_model_escalation_strict_allows_on_protection_surface():
    ras = _load_run_agentic_impl_module()
    allowed = ras._allow_layer3_model_escalation(
        control_model="strict_3layer",
        attempt_index=0,
        turn_index=0,
        max_retries=3,
        max_turns=2,
        protection_detected=True,
        used_count=0,
        max_count=1,
    )
    assert allowed is True
