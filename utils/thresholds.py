"""Threshold config loader for tunable extraction/scenario behavior.

INTEGRATION GUIDE FOR DEBUG BUDGETS:

When implementing debug mode support for timeouts, retries, and evidence capture,
use get_debug_budget_policy() to fetch reason-aware budget adjustments:

  Timeout Example (browser action, scenario step, LLM call):
  =========================================================
    from utils.thresholds import get_debug_budget_policy, load_thresholds

    thresholds = load_thresholds()
    base_timeout_ms = 20_000

    # When computing timeout for a browser action:
    policy = get_debug_budget_policy(
        thresholds,
        profile=thresholds.get("debug_profile", "lite"),
        reason_code=current_step_reason,  # e.g., "calendar_not_open"
        debug_enabled=runtime.get("debug", False)  # From CLI --debug
    )
    # Apply multiplier but respect safety caps
    timeout_ms = int(base_timeout_ms * policy["timeout_multiplier"])
    timeout_ms = min(timeout_ms, max_safe_timeout_ms)  # Safety cap


  Retry Example (scenario attempts, LLM retries):
  ===============================================
    max_retries = get_threshold("scenario_max_retries", 4)
    last_failure_reason = "transport_timeout"

    # Fetch policy for this failure reason
    policy = get_debug_budget_policy(
        thresholds,
        profile=thresholds.get("debug_profile", "lite"),
        reason_code=last_failure_reason,
        debug_enabled=runtime.get("debug", False)
    )
    # Apply delta but hard-cap at +2 additional attempts
    adjusted_retries = max_retries + policy["retry_delta"]
    final_retries = min(adjusted_retries, max_retries + 2)  # Safety bound


  Evidence Capture Example (step failure handler):
  ================================================
    reason_code = step_result.get("reason")
    policy = get_debug_budget_policy(
        thresholds,
        reason_code=reason_code,
        debug_enabled=runtime.get("debug", False)
    )

    # Attach debug metadata
    evidence = step_result.get("evidence", {})
    if policy["timeout_multiplier"] > 1.0 or policy["retry_delta"] > 0:
        evidence["debug"] = {
            "profile": policy["profile"],
            "timeout_multiplier": policy["timeout_multiplier"],
            "retry_delta": policy["retry_delta"],
            "escalation_level": escalation_counter,
            "evidence_bundle": policy["evidence_bundle"],
        }
        # Trigger best-effort evidence capture per bundle
        for bundle_key in policy["evidence_bundle"]:
            if bundle_key == "screenshot":
                capture_screenshot()
            elif bundle_key == "dom_slice":
                capture_dom_slice()
            # ... etc per DEBUG_BUDGETS.md

SAFETY GUARANTEES:
- When debug=False: All multipliers are 1.0, all deltas are 0 (no production change)
- Timeout multiplier always >= 1.0 (never negative or zero)
- Retry delta always >= 0 (never negative)
- All values are bounded to prevent runaway loops

For full details, see docs/kb/DEBUG_BUDGETS.md
"""
import os
import yaml
from pathlib import Path
from typing import Any, Dict


_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "configs" / "thresholds.yaml"
_DEFAULTS: Dict[str, Any] = {
    "selector_min_confidence": 0.55,
    "soft_drift_penalty": 0.08,
    "hard_drift_penalty": 0.35,
    # DEPRECATED: hard_drift_disable removed (never used; modern extraction uses continuous confidence decay)
    "selector_success_boost": 0.04,
    "selector_failure_penalty": 0.15,
    "heuristic_min_price": 500,
    "heuristic_max_price": 5_000_000,
    "plausible_max_price": 10_000_000,
    "scenario_max_retries": 4,
    "scenario_max_turns": 2,
    "scenario_candidate_timeout_sec": 120,
    "scenario_candidate_timeout_cap_sec": 3_600,
    "scenario_budget_soft_margin_sec": 12,
    "scenario_wall_clock_cap_sec": 0,
    "scenario_step_wall_clock_cap_ms": 45_000,
    "scenario_step_wall_clock_cap_ms_default": 45_000,
    "scenario_step_wall_clock_cap_ms_click": 20_000,
    "scenario_step_wall_clock_cap_ms_fill": 30_000,
    "scenario_step_wall_clock_cap_ms_wait": 15_000,
    "scenario_evidence_dump_enabled": False,
    "scenario_disable_http2_retry_timeout_sec": 45,
    "browser_goto_timeout_ms": 45_000,
    "browser_action_timeout_ms": 20_000,
    "browser_wait_timeout_ms": 30_000,
    "browser_optional_toggle_selector_timeout_ms": 800,
    "browser_settle_wait_30ms": 30,
    "browser_settle_wait_40ms": 40,
    "browser_search_results_contextual_min_wait_ms": 2_500,
    "google_flights_deeplink_probe_timeout_ms": 35_000,
    "google_flights_deeplink_probe_interval_ms": 800,
    "google_flights_deeplink_lightmode_return_on_unready": False,
    "google_flights_quick_rebind_enabled": True,
    "google_flights_quick_rebind_action_timeout_ms": 2_500,
    "google_flights_quick_rebind_settle_timeout_ms": 12_000,
    "google_flights_quick_rebind_step_pause_ms": 300,
    "google_flights_quick_rebind_search_click_max_selectors": 4,
    "google_flights_quick_rebind_search_visibility_probe_ms": 80,
    # Generic recovery-mode knobs (optional site override keys:
    # `<key>_<site_key>`, e.g. `scenario_recovery_max_retries_google_flights`).
    "scenario_recovery_max_retries": 2,
    "scenario_recovery_max_turns": 2,
    "scenario_recovery_retry_on_unready": True,
    "scenario_recovery_fill_soft_fail": True,
    "scenario_recovery_force_soft_fill": True,
    # Generic selector execution policy with site-specific overrides.
    "scenario_fill_fallback_prepend": False,
    "scenario_fill_fallback_prepend_google_flights": True,
    "scenario_selector_allow_bare_text_fallback": False,
    "scenario_prioritize_fill_selectors": True,
    "scenario_prioritize_fill_selectors_google_flights": False,
    "max_selector_candidates": 12,
    "max_selector_candidates_google_flights": 6,
    "max_selector_candidates_skyscanner": 8,
    "browser_action_selector_timeout_ms_google_flights": 1_500,
    "browser_wait_selector_timeout_ms_google_flights": 1_500,
    "browser_enforce_selector_timeout_single_candidate": False,
    "browser_enforce_selector_timeout_single_candidate_google_flights": True,
    "scenario_step_selector_budget_reserve_ms": 3_000,
    "scenario_fill_type_active_recovery_enabled": True,
    "scenario_fill_type_active_recovery_enabled_google_flights": False,
    "scenario_fill_date_type_active_recovery_enabled": True,
    "google_flights_fill_recovery_max_activation_selectors": 12,
    "google_flights_fill_recovery_min_reserve_ms": 9_000,
    "llm_default_num_ctx": 16_384,
    "llm_default_num_predict": 2_048,
    "llm_planner_num_ctx": 20_480,
    "llm_planner_num_predict": 3_072,
    "llm_coder_num_ctx": 16_384,
    "llm_coder_num_predict": 1_536,
    "llm_temperature": 0.0,
    "llm_light_planner_num_ctx": 12_288,
    "llm_light_planner_num_predict": 2_048,
    "llm_light_coder_num_ctx": 12_288,
    "llm_light_coder_num_predict": 1_024,
    "llm_light_temperature": 0.0,
    "planner_multimodal_assist_enabled": False,
    "planner_multimodal_assist_timeout_sec": 180,
    "planner_multimodal_include_dom_context": True,
    "planner_multimodal_dom_context_max_chars": 12_000,
    "light_mode_try_llm_plan_on_fast_plan_failure": True,
    "llm_light_planner_timeout_sec": 720,
    "light_mode_try_llm_repair_after_fast_failure": True,
    "llm_light_repair_timeout_sec": 900,
    "light_mode_try_llm_extract_on_heuristic_miss": True,
    "llm_light_extract_timeout_sec": 2_700,
    "extract_html_quality_gate_enabled": True,
    "light_mode_try_llm_html_quality_judge": True,
    "llm_light_html_quality_timeout_sec": 300,
    "llm_html_quality_timeout_sec": 480,
    "extract_semantic_chunk_enabled": True,
    "extract_semantic_chunk_min_html_chars": 120_000,
    "extract_semantic_chunk_max_chunks": 8,
    "extract_semantic_chunk_chars": 8_000,
    "extract_semantic_chunk_max_nodes": 280,
    "llm_extract_chunk_attempts": 3,
    "llm_extract_chunk_timeout_sec": 600,
    "llm_light_extract_chunk_timeout_sec": 300,
    "scenario_save_visual_snapshot": True,
    "scenario_vlm_ui_assist_enabled": False,
    "scenario_vlm_ui_assist_timeout_sec": 1_800,
    "scenario_vlm_ui_assist_timeout_cap_sec": 900,
    "scenario_vlm_ui_assist_max_variants": 1,
    "scenario_vlm_ui_assist_include_dom_context": True,
    "scenario_light_smart_repair_use_vlm_ui_assist": True,
    "scenario_light_smart_repair_vlm_timeout_sec": 900,
    "scenario_light_smart_repair_vlm_timeout_cap_sec": 900,
    "scenario_light_smart_repair_vlm_max_variants": 1,
    "scenario_light_smart_repair_vlm_include_dom_context": False,
    "scenario_vlm_fill_verify_enabled": True,
    "scenario_vlm_fill_verify_timeout_sec": 1200,
    "scenario_vlm_fill_verify_require_route_bound_for_ready": True,
    "scenario_vlm_fill_verify_skip_in_recovery_mode": True,
    "scenario_vlm_fill_verify_fail_closed": True,
    "scenario_turn_scope_guard_enabled": True,
    "scenario_turn_scope_guard_on_unready": True,
    "scenario_use_plugin_readiness_probe": False,
    "scenario_turn_scope_guard_vlm_enabled": True,
    "scenario_turn_scope_guard_llm_enabled": True,
    "scenario_turn_scope_guard_vlm_timeout_sec": 1_800,
    "scenario_turn_scope_guard_vlm_timeout_cap_sec": 900,
    "scenario_turn_scope_guard_vlm_max_variants": 1,
    "scenario_turn_scope_guard_vlm_only_when_quick_unknown": True,
    "scenario_turn_scope_guard_vlm_include_dom_context": False,
    "scenario_turn_scope_guard_llm_timeout_sec": 600,
    "scenario_turn_scope_guard_llm_timeout_cap_sec": 420,
    "scenario_scope_repair_rewind_enabled": True,
    "scenario_scope_repair_rewind_max_replay_fills": 4,
    "scenario_scope_repair_rewind_use_service_toggles": True,
    "vlm_fill_verify_roi_crop_enabled": True,
    "vlm_fill_verify_roi_padding_ratio": 0.18,
    "vlm_fill_verify_roi_max_side_px": 1600,
    "vlm_fill_verify_roi_jpeg_quality": 85,
    "vlm_fill_verify_locate_num_predict": 4_096,
    "vlm_fill_verify_read_num_predict": 1_536,
    "vlm_extract_enabled": True,
    "light_mode_try_vlm_extract_on_heuristic_miss": True,
    "vlm_extract_timeout_sec": 2_400,
    "agentic_multimodal_mode": "off",
    "multimodal_extract_timeout_sec": 1200,
    "multimodal_extract_num_ctx": 12_288,
    "multimodal_extract_num_predict": 4_096,
    "multimodal_extract_max_html_chars": 30_000,
    "multimodal_extract_endpoint_policy": "chat_only",
    "vlm_image_preprocess_enabled": True,
    "vlm_image_max_side_px": 960,
    "vlm_image_max_bytes": 300_000,
    "vlm_image_jpeg_quality": 65,
    "vlm_image_max_variants": 2,
    "vlm_image_include_top_crop": True,
    "vlm_image_include_center_crop": True,
    "vlm_image_crop_height_ratio": 0.55,
    "vlm_attempt_timeout_min_sec": 240,
    "vlm_attempt_timeout_max_sec": 2_400,
    "vlm_extract_num_predict": 8_192,
    "vlm_ui_num_predict": 4_096,
    "vlm_extract_skip_remaining_variants_on_token_cap": False,
    "vlm_extract_token_cap_retries": 1,
    "vlm_extract_token_cap_retry_num_predict": 12_288,
    "vlm_extract_token_cap_retry_timeout_backoff": 1.5,
    "vlm_extract_token_cap_retry_timeout_cap_sec": 3_600,
    "vlm_extract_token_cap_retry_endpoint_policy": "generate_only",
    "vlm_endpoint_policy": "auto",
    "vlm_extract_endpoint_policy": "chat_only",
    "vlm_ui_endpoint_policy": "chat_only",
    "vlm_fill_verify_endpoint_policy": "chat_only",
    "vlm_ui_think": False,
    "vlm_extract_think": False,
    "vlm_multimodal_think": False,
    "vlm_fill_verify_think": False,
    "vlm_ui_skip_remaining_variants_on_timeout": True,
    "vlm_extract_skip_remaining_variants_on_timeout": True,
    "vlm_strict_json": False,
    "extract_vlm_scope_guard_enabled": True,
    "extract_vlm_scope_guard_timeout_sec": 720,
    "extract_vlm_scope_guard_timeout_cap_sec": 900,
    "extract_vlm_scope_guard_max_variants": 1,
    "extract_vlm_scope_guard_fail_closed": False,
    "extract_vlm_llm_price_verify_enabled": True,
    "extract_vlm_llm_price_verify_timeout_sec": 180,
    "extract_vlm_llm_price_verify_timeout_cap_sec": 300,
    "extract_vlm_llm_price_verify_fail_closed": False,
    "extract_vlm_llm_price_verify_endpoint_policy": "chat_only",
    "extract_vlm_llm_price_verify_num_predict": 256,
    "extract_vlm_price_grounding_required_on_conflict": True,
    "extract_vlm_price_grounding_tolerance_ratio": 0.03,
    "extract_vlm_price_grounding_tolerance_abs": 2_500,
    "extract_llm_scope_guard_enabled": True,
    "extract_llm_scope_guard_timeout_sec": 480,
    "extract_llm_scope_guard_timeout_cap_sec": 420,
    "extract_llm_scope_guard_fail_closed": False,
    "extract_llm_scope_guard_endpoint_policy": "chat_only",
    "extract_google_non_flight_fast_guard": True,
    "extract_google_require_route_context": True,
    "extract_selector_stability_normalize_enabled": True,
    "extract_confidence_downgrade_on_brittle_selector": True,
    "extract_confidence_downgrade_min": "low",
    "extract_vision_price_assist_enabled": True,
    "extract_dom_probe_max_price_candidates": 1_200,
    "extract_strategy_plugin_key": "html_llm",
    "extract_salvage_retry_enabled": True,
    "extract_salvage_retry_on_low_confidence": True,
    "extract_salvage_min_confidence": "medium",
    "extract_salvage_force_full_mode": True,
    "extract_salvage_clear_circuit_before_retry": True,
    "extract_salvage_max_attempts": 2,
    "extract_salvage_max_attempts_route_miss": 1,
    "extract_salvage_skip_after_elapsed_sec": 1800,
    "extract_salvage_timeout_backoff": 1.6,
    "extract_salvage_stop_confidence": "high",
    "extract_salvage_llm_extract_timeout_sec": 3_000,
    "extract_salvage_vlm_extract_timeout_sec": 3_000,
    "runs_db_max_age_days": 365,
    "runs_db_max_rows": 100_000,
    "runs_db_max_bytes": 200 * 1024 * 1024,
    "runs_db_min_rows_to_keep": 5_000,
    "llm_metrics_db_max_age_days": 90,
    "llm_metrics_db_max_rows": 300_000,
    "llm_metrics_db_min_rows_to_keep": 20_000,
    "log_file_max_bytes": 10 * 1024 * 1024,
    "log_file_keep_bytes": 2 * 1024 * 1024,
    "debug_html_cleanup_enabled": True,
    "debug_html_max_age_days": 7,
    "ollama_connect_timeout_sec": 60,
    "ollama_read_timeout_sec": 1200,
    "ollama_total_timeout_sec": 3600,
    "ollama_circuit_open_sec": 120,
    "llm_call_wall_clock_cap_sec": 0,
    "llm_stall_tokens_per_sec": 0.15,
    "llm_stall_min_elapsed_sec": 180,
    "llm_stall_abort_enabled": False,
    "extract_wall_clock_cap_sec": 0,
    "llm_extract_timeout_sec": 1800,
    "llm_extract_endpoint_policy": "chat_only",
    "llm_extract_fail_fast_on_timeout": True,
    "light_mode_skip_llm_extract": True,
    "llm_planner_timeout_sec": 1800,
    "llm_repair_timeout_sec": 2400,
    "vlm_extract_adaptive_retry_variant_profile_primary": "default",
    "vlm_extract_adaptive_retry_variant_profile_retry": "diverse",
    "scenario_route_bind_gate_enabled": True,
    "scenario_route_bind_gate_requires_strong": True,
    "scenario_route_bind_fail_closed_on_mismatch": True,
    "scenario_route_bind_vlm_verify_enabled": True,
    "scenario_route_bind_vlm_timeout_sec": 180,
    "coordination_enabled": False,
    "scenario_vision_page_kind_enabled": True,
    "scenario_vision_post_fill_verify_enabled": True,
    "google_flights_deeplink_page_state_recovery_action_timeout_ms": 1_800,
    "google_flights_deeplink_page_state_recovery_settle_ms": 250,
    "google_flights_deeplink_use_mimic_params": True,
    "google_flights_rewind_priority_requires_strong_signal": True,
    "google_flights_reset_clear_timeout_ms": 900,
    "google_flights_reset_wait_timeout_ms": 4_500,
    "google_flights_reset_on_route_mismatch_enabled": True,
    "google_flights_reset_on_route_mismatch_max_attempts": 1,
    "google_flights_date_reload_retry_max_attempts": 1,
    "google_flights_rewind_priority_on_route_mismatch_enabled": True,
    "google_flights_rewind_priority_on_route_mismatch_max_per_attempt": 1,
    "google_flights_force_route_bind_repair_enabled": True,
    "google_flights_force_route_bind_repair_max_per_attempt": 1,
    "skyscanner_activation_visibility_timeout_ms": 3_000,
    "skyscanner_post_activation_wait_ms": 5_000,
    "skyscanner_results_readiness_timeout_ms": 8_000,
    "skyscanner_blocked_interstitial_grace_ms": 3_500,
    "skyscanner_blocked_interstitial_grace_fallback_ms": 12_000,
    "skyscanner_captcha_manual_wait_sec": 45,
    "skyscanner_press_hold_ready_wait_ms": 9_000,
    "skyscanner_press_hold_poll_interval_ms": 250,
    "skyscanner_press_hold_min_hold_ms": 10_000,
    "skyscanner_press_hold_degraded_min_ms": 1_800,
    "skyscanner_interstitial_clearance_cooldown_probe_ms": 2_000,
    # Bounded timeout used by Skyscanner hotels->flights context repair.
    "skyscanner_hotels_recovery_timeout_ms": 4_000,
    "adaptive_high_timeout_pressure_light_planner_timeout_sec": 360,
    "adaptive_high_timeout_pressure_light_extract_timeout_sec": 1_200,
}
_CACHE: Dict[str, Any] = {}
_ACTIVE_THRESHOLD_PROFILE: str = "default"


def _coerce_value(raw: str) -> Any:
    """Convert scalar YAML-like text into bool/int/float/str."""
    text = raw.strip()
    if not text:
        return text

    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"

    try:
        if any(ch in text for ch in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_thresholds(force_reload: bool = False) -> Dict[str, Any]:
    """Load thresholds from config file using YAML parser.

    Merges _DEFAULTS with values from configs/thresholds.yaml.
    Handles nested sections like 'profiles:' via proper YAML parsing.
    """
    global _CACHE
    if _CACHE and not force_reload:
        return dict(_CACHE)

    values = dict(_DEFAULTS)

    if _THRESHOLDS_PATH.exists():
        try:
            # Use YAML parser for full structure support (handles nested profiles, lists, etc.)
            yaml_data = yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))
            if yaml_data and isinstance(yaml_data, dict):
                values.update(yaml_data)
        except Exception:
            # Fallback to simple line parser if YAML fails
            for raw_line in _THRESHOLDS_PATH.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                if not key:
                    continue
                values[key] = _coerce_value(value)

    _CACHE = values
    return dict(values)


def get_threshold(key: str, default: Any = None) -> Any:
    """Get one threshold value by key with optional fallback."""
    profile = str(_ACTIVE_THRESHOLD_PROFILE or "default").strip().lower()
    if profile != "default":
        values = get_thresholds_for_profile(profile)
    else:
        values = load_thresholds()
    if key in values:
        return values[key]
    return default


def set_active_threshold_profile(profile: str = "default") -> str:
    """Set process-local threshold profile for get_threshold() lookups.

    Returns the previous profile so callers may restore it if needed.
    """
    global _ACTIVE_THRESHOLD_PROFILE
    previous = _ACTIVE_THRESHOLD_PROFILE
    normalized = str(profile or "default").strip().lower()
    if normalized not in ("default", "debug"):
        normalized = "default"
    _ACTIVE_THRESHOLD_PROFILE = normalized
    return previous


def reset_active_threshold_profile() -> None:
    """Reset get_threshold() profile lookups to base/default profile."""
    global _ACTIVE_THRESHOLD_PROFILE
    _ACTIVE_THRESHOLD_PROFILE = "default"


def get_debug_budget_policy(
    thresholds: Dict[str, Any],
    profile: str = "lite",
    reason_code: str = None,
    debug_enabled: bool = True,
) -> Dict[str, Any]:
    """Fetch debug budget policy with reason-aware overrides.

    Returns a policy dict for applying multipliers, retries, and evidence collection
    when debug mode is enabled. When disabled or not configured, returns safe defaults
    (no behavior change).

    Args:
        thresholds: Loaded thresholds dict (from load_thresholds()).
        profile: Debug profile name: "lite" (default), "deep", or "super_deep".
        reason_code: Optional failure reason code (e.g., "calendar_not_open"). If provided,
                     reason-specific overrides are applied.
        debug_enabled: If False, always returns safe defaults (multiplier=1.0, delta=0).

    Returns:
        {
            "profile": str,                           # "lite" | "deep" | "super_deep"
            "timeout_multiplier": float,              # >= 1.0 (1.0 = no change)
            "retry_delta": int,                       # >= 0 (additional attempts)
            "evidence_bundle": list[str],             # ["screenshot", "dom_slice", ...]
            "escalation_max_steps": int,              # 0..N
            "reason_code": str | None,                # Matched reason or None
            "is_override": bool,                      # True if reason-specific override applied
        }
    """
    # Safe defaults: no behavior change when debug disabled or not configured.
    safe_default = {
        "profile": profile,
        "timeout_multiplier": 1.0,
        "retry_delta": 0,
        "evidence_bundle": [],
        "escalation_max_steps": 0,
        "reason_code": reason_code,
        "is_override": False,
    }

    if not debug_enabled or not thresholds:
        return safe_default

    # Check if debug budgets are enabled globally.
    if not thresholds.get("debug_budgets_enabled", False):
        return safe_default

    # Validate profile.
    profile = str(profile or "lite").strip().lower()
    if profile not in ("lite", "deep", "super_deep"):
        profile = "lite"

    # Base multipliers and deltas by profile.
    base_multiplier_key = f"debug_timeout_multiplier_{profile}"
    base_retry_key = f"debug_retry_delta_{profile}"
    base_multiplier = float(thresholds.get(base_multiplier_key, 1.0))
    base_retry_delta = int(thresholds.get(base_retry_key, 0))
    escalation_max = int(
        thresholds.get(
            f"debug_escalation_max_steps_{profile}",
            thresholds.get("debug_escalation_max_steps", 2),
        )
        or 2
    )
    profile_evidence_key = f"debug_evidence_bundle_{profile}"
    profile_evidence_value = thresholds.get(profile_evidence_key)

    def _parse_evidence_bundle(value: Any) -> list[str]:
        if not value:
            return []
        try:
            return [s.strip() for s in str(value).split(",") if s.strip()]
        except Exception:
            return []

    # Start with base profile values.
    policy = {
        "profile": profile,
        "timeout_multiplier": max(1.0, base_multiplier),
        "retry_delta": max(0, base_retry_delta),
        "evidence_bundle": _parse_evidence_bundle(profile_evidence_value),
        "escalation_max_steps": max(0, escalation_max),
        "reason_code": reason_code,
        "is_override": False,
    }

    # If reason_code provided, check for overrides.
    if reason_code and isinstance(reason_code, str):
        reason_code_clean = reason_code.strip().lower()

        # Try to fetch reason-specific multiplier/retry/evidence.
        reason_override_key = f"debug_reason_overrides_{reason_code_clean}"
        reason_evidence_key = f"debug_evidence_bundle_{reason_code_clean}"

        override_value = thresholds.get(reason_override_key)
        evidence_value = thresholds.get(reason_evidence_key)

        if override_value:
            # Parse legacy and extended formats.
            # Legacy:   mult_lite:mult_deep:retry_lite:retry_deep
            # Extended: mult_lite:mult_deep:mult_super:retry_lite:retry_deep:retry_super
            try:
                parts = str(override_value).split(":")
                if len(parts) >= 6:
                    mult_lite = float(parts[0])
                    mult_deep = float(parts[1])
                    mult_super = float(parts[2])
                    retry_lite = int(parts[3])
                    retry_deep = int(parts[4])
                    retry_super = int(parts[5])
                    if profile == "lite":
                        mult, retry = mult_lite, retry_lite
                    elif profile == "super_deep":
                        mult, retry = mult_super, retry_super
                    else:
                        mult, retry = mult_deep, retry_deep
                    policy["retry_delta"] = max(0, retry)
                    policy["timeout_multiplier"] = max(1.0, mult)
                    policy["is_override"] = True
                elif len(parts) >= 2:
                    mult_lite = float(parts[0])
                    mult_deep = float(parts[1])
                    mult = mult_lite if profile == "lite" else mult_deep
                    if len(parts) >= 4:
                        retry_lite = int(parts[2])
                        retry_deep = int(parts[3])
                        retry = retry_lite if profile == "lite" else retry_deep
                        policy["retry_delta"] = max(0, retry)
                    policy["timeout_multiplier"] = max(1.0, mult)
                    policy["is_override"] = True
            except (ValueError, IndexError):
                pass  # Fall back to base values if parsing fails.

        if evidence_value:
            # Parse comma-separated list: "screenshot,dom_slice,selector_diagnostics"
            try:
                bundles = [s.strip() for s in str(evidence_value).split(",") if s.strip()]
                merged = list(policy.get("evidence_bundle", []) or [])
                for item in bundles:
                    if item not in merged:
                        merged.append(item)
                policy["evidence_bundle"] = merged
            except Exception:
                pass  # Fall back to empty list if parsing fails.

    return policy


def resolve_debug_budgets_from_env(debug_enabled: bool) -> Dict[str, Any]:
    """Resolve debug budget settings from environment variables (only when debug_enabled=True).

    Environment Variables (only read when debug_enabled=True):
    - DEBUG_BUDGETS_PROFILE: "lite", "deep", or "super_deep" (overrides config debug_profile)
    - DEBUG_BUDGETS_ESCALATE: "0" or "1" (allows escalation; feature flag for future use)

    When debug_enabled=False, returns empty dict (no overrides applied).

    Args:
        debug_enabled: Whether debug mode is enabled (from CLI --debug flag)

    Returns:
        {
            "profile": str | None,          # "lite"|"deep"|"super_deep" if env set, else None
            "escalate": bool | None,         # True|False if env set, else None
        }
    """
    if not debug_enabled:
        return {"profile": None, "escalate": None}

    debug_profile_env = (os.environ.get("DEBUG_BUDGETS_PROFILE") or "").strip().lower()
    debug_escalate_env = (os.environ.get("DEBUG_BUDGETS_ESCALATE") or "").strip().lower()

    result = {
        "profile": None,
        "escalate": None,
    }

    # Validate and set profile override
    profile_aliases = {
        "lite": "lite",
        "deep": "deep",
        "super_deep": "super_deep",
        "super-deep": "super_deep",
        "superdeep": "super_deep",
        "ultra": "super_deep",
    }
    if debug_profile_env in profile_aliases:
        result["profile"] = profile_aliases[debug_profile_env]

    # Validate and set escalation flag
    if debug_escalate_env in ("0", "1", "true", "false", "yes", "no"):
        result["escalate"] = debug_escalate_env in ("1", "true", "yes")

    return result

def get_thresholds_for_profile(profile: str = "default") -> Dict[str, Any]:
    """Get thresholds merged with profile overrides (default or debug).

    Loads base thresholds from configs and applies profile-specific boosts.

    Args:
        profile: Profile name ("default" or "debug")

    Returns:
        Dictionary of threshold values with profile overrides applied.
    """
    all_thresholds = load_thresholds()

    # Validate profile
    if profile not in ("default", "debug"):
        profile = "default"

    # Extract profiles section if it exists
    profiles = all_thresholds.get("profiles")
    if not profiles or not isinstance(profiles, dict):
        # Fallback: return base thresholds (no profile mechanism yet)
        return all_thresholds

    # Start with default profile
    result = dict(all_thresholds)

    # Remove profiles section from result (prevents loop-back)
    result.pop("profiles", None)

    default_overrides = profiles.get("default")
    if default_overrides and isinstance(default_overrides, dict):
        # Apply default profile overrides
        for key, value in default_overrides.items():
            if key not in ("default", "debug"):  # Skip profile names
                result[key] = value

    # If debug profile requested, apply debug overrides on top
    if profile == "debug":
        debug_overrides = profiles.get("debug")
        if debug_overrides and isinstance(debug_overrides, dict):
            for key, value in debug_overrides.items():
                if key not in ("default", "debug"):
                    result[key] = value

    return result


def adjust_timeout_for_retry(
    base_ms: int,
    retry_index: int,
    floor_ms: int = 500,
) -> int:
    """Adjust timeout downward for retry attempts (diminishing returns).

    Implements a guardrail: as retries increase, per-action timeouts decrease
    slightly to prevent wasted time on failing strategies.

    Formula: adjusted_ms = base_ms * max(0.8, 1.0 - (retry_index * 0.1))

    Args:
        base_ms: Base timeout in milliseconds
        retry_index: Retry attempt number (0-indexed)
        floor_ms: Minimum timeout floor in milliseconds (default 500ms)

    Returns:
        Adjusted timeout in milliseconds, never below floor_ms

    Examples:
        adjust_timeout_for_retry(1500, 0) -> 1500  # First attempt, no reduction
        adjust_timeout_for_retry(1500, 1) -> 1350  # 1500 * 0.9
        adjust_timeout_for_retry(1500, 2) -> 1200  # 1500 * 0.8
        adjust_timeout_for_retry(1500, 3) -> 1050  # Would be < 0.8, clamped to 0.8
    """
    if base_ms < floor_ms:
        return floor_ms

    # Diminishing returns: multiply by min factor that decreases with retries
    factor = max(0.8, 1.0 - (retry_index * 0.1))
    adjusted = int(base_ms * factor)

    return max(adjusted, floor_ms)


def clamp_debug_scenario_timeout(
    timeout_sec: int,
    hard_cap_sec: int = 3600,
) -> int:
    """Clamp debug scenario timeout to prevent runaway execution.

    Debug runs allow modest timeout expansion (up to 1.25x), but enforce
    a hard cap (default 3600s = 1 hour) to prevent infinite runs.

    Args:
        timeout_sec: Current/requested timeout in seconds
        hard_cap_sec: Hard cap in seconds (default 3600s)

    Returns:
        Clamped timeout in seconds: min(timeout * 1.25, hard_cap_sec)

    Examples:
        clamp_debug_scenario_timeout(100) -> 125  # Boost allowed
        clamp_debug_scenario_timeout(3000) -> 3600  # Clamped to hard cap
    """
    boosted = int(timeout_sec * 1.25)
    return min(boosted, hard_cap_sec)
