"""Standalone implementation of the scenario runner extracted from
`core.scenario_runner`.

This module implements the scenario execution logic as a self-contained
implementation. To preserve backwards-compatible test and runtime hooks we
resolve a very small set of well-known monkeypatch targets from the legacy
`core.scenario_runner` facade at invocation time (notably `BrowserSession`
and `execute_plan`) without mutating module globals.
Most helper functions are imported explicitly to avoid circular imports and
keep the runtime surface minimal.
"""

# Bring a few typing names into this module for static tools and annotations.
from typing import Any, Dict, List, Optional
import os
import re
from datetime import datetime, UTC
import time
import copy
from functools import partial
from core.services import is_supported_service
from core.run_input_config import load_run_input_config
from core.flight_plan import validate_flight_plan
from core.scenario_runner import (
    _set_plugin_scenario_hints,
    _default_plan_for_service,
)
from core.scenario_runner.readiness import (
    has_skyscanner_price_signal as _has_skyscanner_price_signal,
    is_skyscanner_results_shell_incomplete as _is_skyscanner_results_shell_incomplete,
)
from core.scenario_runner.readiness import is_results_ready as _is_results_ready
from core.scenario_runner.plan_enrichment import with_knowledge as _with_knowledge
from core.scenario_runner.plan_toggles import _default_turn_followup_plan
from core.scenario_runner.notes import _error_signature
from core.scenario_runner.page_scope import (
    apply_plugin_readiness_probe as _apply_plugin_readiness_probe,
    is_non_flight_page_class as _is_non_flight_page_class,
    normalize_page_class as _normalize_page_class,
    page_class_to_trip_product as _page_class_to_trip_product,
    record_scope_feedback as _record_scope_feedback,
    resolve_page_scope_class as _resolve_page_scope_class,
    should_block_ready_on_scope_conflict as _should_block_ready_on_scope_conflict,
)
from core.scenario_recovery_collab import (
    try_recovery_collab_followup as _try_recovery_collab_followup_impl,
)
from core.scenario_runner.google_recovery_collab import (
    GoogleRecoveryCollabContext,
    google_recovery_collab_followup_impl,
)
from core.scenario_runner.env import (
    _env_int,
    _env_bool,
    _threshold_site_value,
    _debug_exploration_mode,
    _current_mimic_locale,
)
from utils.thresholds import get_threshold
from storage.knowledge_store import get_knowledge, record_failure, record_success
from llm.model_router import ModelRouter
from llm.code_model import (
    analyze_page_ui_with_vlm,
    assess_trip_product_scope_with_llm,
)
from utils.logging import get_logger
from core.browser.manual_intervention_policy import (
    is_skyscanner_px_captcha_url,
    is_verification_url,
    manual_intervention_diagnostic_selectors,
)

from core.scenario_runner.scenario_return import ReturnBuilderContext, scenario_return_impl

log = get_logger(__name__)

# VLM/runtime-hint helpers
from core.scenario_runner.vlm_helpers import (
    _apply_vlm_runtime_hints,
    _clear_vlm_runtime_hints,
    _compose_vlm_knowledge_hint,
)

# Plan utilities
from core.scenario_runner.plan_utils_helpers import (
    _is_valid_plan,
    _is_actionable_plan,
)

# Plan-hygiene helpers
from core.scenario_runner.plan_hygiene_helpers import (
    _retarget_plan_inputs,
    _reconcile_fill_plan_roles_and_values,
)

# Knowledge helpers
from core.scenario_runner.knowledge_helpers import (
    _compose_global_knowledge_hint,
    _compose_local_knowledge_hint,
    _blocked_selectors_from_knowledge,
    _collect_plugin_readiness_hints,
)

# Google Flights helpers
from core.scenario_runner.google_flights_helpers import (
    _write_google_search_commit_probe_artifact,
    _detect_site_interstitial_block,
    _attempt_google_deeplink_page_state_recovery,
    _normalize_google_deeplink_with_mimic,
    _google_deeplink_quick_rebind,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_deeplink_page_state_recovery_policy,
    _google_deeplink_probe_status,
    _google_deeplink_recovery_plan,
    _google_missing_roles_from_reason,
    _google_recovery_collab_limits_from_thresholds,
    _selector_candidates,
    _google_force_bind_flights_tab_selectors,
    _google_step_trace_local_date_open_failure,
    _google_should_suppress_force_bind_after_date_failure,
    _google_quick_page_class,
    _google_display_locale_hint_from_browser,
    _google_display_locale_hint_from_url,
    _google_has_contextual_price_card,
    _google_non_flight_scope_repair_plan,
    _google_route_core_only_recovery_plan,
    _verification_confidence_rank,
    _strip_nonvisible_html,
)

from core.scenario_runner.planner_bridge import (
    _call_generate_action_plan_bundle,
    _call_repair_action_plan_bundle,
)

from core.scenario_runner.notes import (
    _local_programming_exception_reason,
    _should_return_latest_html_on_followup_failure,
    _step_trace_memory_hint as _step_trace_memory_hint_impl,
)

from core.scenario_runner.selectors.fallbacks import (
    _service_search_click_fallbacks,
    _service_wait_fallbacks,
)
from core.scenario_runner.google_flights.core_functions import (
    _assess_google_flights_fill_mismatch,
    _extract_google_flights_form_state,
    _google_form_value_matches_airport,
    _google_form_value_matches_date,
    _is_google_dest_placeholder,
)
from core.scenario_runner.google_flights.smart_escalation import (
    _google_route_fill_smart_escalation_skip_reason,
    _google_search_commit_smart_escalation_skip_reason,
)
# Skyscanner interstitial handlers
from core.scenario_runner.skyscanner.interstitials import (
    attempt_skyscanner_interstitial_grace as _attempt_skyscanner_interstitial_grace,
    attempt_skyscanner_interstitial_fallback_reload as _attempt_skyscanner_interstitial_fallback_reload,
    probe_skyscanner_shadow_challenge_state as _probe_skyscanner_shadow_challenge_state,
)
from core.scenario_runner.skyscanner import (
    detect_skyscanner_interstitial_block as _detect_skyscanner_interstitial_block,
    _ensure_skyscanner_flights_context,
)

# Route-recovery and site-recovery dispatch helpers
from core.scenario_runner.route_recovery_helpers import (
    _site_recovery_collab_focus_plan_dispatch,
    _site_recovery_collab_limits_from_thresholds_dispatch,
    _site_recovery_collab_scope_repair_plan_dispatch,
    _site_recovery_collab_trigger_reason_dispatch,
    _site_recovery_pre_date_gate_dispatch,
    _site_should_attempt_recovery_collab_after_date_failure_dispatch,
)

# UI actions
from core.scenario_runner.ui_actions_helpers import (
    _google_search_and_commit,
)

# Selector utilities
from core.scenario_runner.selectors_helpers import (
    _compact_selector_dom_probe,
)

# Artifact-writing helpers
from core.scenario_runner.artifacts_helpers import (
    _write_debug_snapshot,
    _write_progress_snapshot,
    _write_html_snapshot,
    _write_json_artifact_snapshot,
    _write_image_snapshot,
    _write_route_state_debug,
    _append_jsonl_artifact,
)
from core.scenario_runner.google_flights.route_bind import (
    _build_route_state_return_fallback_payload,
    _google_reconcile_ready_route_bound_consistency,
    _google_turn_fill_success_corroborates_route_bind,
    _build_route_state_scenario_extract_verdict,
    _route_mismatch_suspected_verdict,
    _prioritized_google_route_mismatch_rewind_followup,
    _google_force_route_bound_repair_plan,
    _google_force_bind_repair_policy,
    _should_attempt_google_route_mismatch_reset,
    _run_google_route_mismatch_reset,
    _google_route_core_before_date_gate,
    _scope_rewind_followup_plan,
    _soften_recovery_route_fills,
)
from utils.run_paths import get_artifacts_dir
from utils.dom_diff import dom_changed
from storage.plan_store import get_plan, get_plan_notes, save_plan

# Run-time helper implementations
from core.scenario_runner.run_agentic_helpers import (
    _route_probe_for_html_impl,
    _write_evidence_checkpoint_impl,
)
from core.scenario_runner import _try_agent_v0_optional
from core.scenario_runner.budget import (
    budget_remaining_sec as _budget_remaining_sec_impl,
    budget_almost_exhausted as _budget_almost_exhausted_impl,
    wall_clock_cap_exhausted as _wall_clock_cap_exhausted_impl,
)
from utils.graph_policy_stats import GraphPolicyStats
from core.scenario_runner.timeouts import (
    _optional_click_timeout_ms,
    _wall_clock_cap_reached,
)
from core.scenario_runner.run_agentic_vision_helpers import (
    _run_vision_page_kind_probe_impl,
    _apply_vision_page_kind_hints_impl,
)
from core.scenario_runner.vlm.ui_steps import (
    _maybe_run_initial_vlm_ui_assist,
    _service_mode_toggle_step,
)
from core.scenario_runner.io_paths import (
    snapshot_image_path as _snapshot_image_path,
    planner_snapshot_path as _planner_snapshot_path,
)
from core.scenario_runner.notes import (
    _merge_planner_notes,
    _compose_local_hint_with_notes,
)
from core.ui_tokens import prioritize_tokens
from utils.knowledge_rules import get_tokens
from core.browser import BrowserSession
from core.scenario_runner.selectors import _safe_click_first_match
# (selector helpers from google_flights were consolidated into the earlier import)
from core.scenario_runner.vlm.probes import (
    _vision_cached_stage_call,
    _normalize_vision_fill_verify_result,
    _should_run_vision_page_kind_probe,
    _should_run_vision_post_fill_verify,
    _vision_modal_dismiss_selectors,
)
from core.scenario_runner.run_agentic.bootstrap import (
    RUNTIME_PATCHABLE_SYMBOLS as _RUNTIME_PATCHABLE_SYMBOLS,
    enforce_contract_retry_bounds as _enforce_contract_retry_bounds,
    resolve_retry_turn_defaults as _resolve_retry_turn_defaults,
    resolve_runtime_symbol_overrides as _resolve_runtime_symbol_overrides_impl,
)
from core.scenario_runner.run_agentic.attempt_gate import (
    run_attempt_precheck_and_interstitial_gate,
)
from core.scenario_runner.run_agentic.turn_gate import (
    run_turn_start_gate,
)
from core.scenario_runner.run_agentic.finalize import (
    finalize_retries_exhausted_return,
)
from core.scenario_runner.run_agentic.turn_analysis import (
    analyze_turn_trace,
)
from core.scenario_runner.run_agentic.turn_execute import (
    execute_turn_plan,
)

_STRICT_THREE_LAYER_MODEL = "strict_3layer"


def _resolve_control_model(llm_mode: str) -> str:
    raw = str(os.getenv("FLIGHT_WATCHER_CONTROL_MODEL", "") or "").strip().lower()
    if raw in {"strict_3layer", "legacy", "auto"}:
        return raw
    return _STRICT_THREE_LAYER_MODEL if str(llm_mode or "").strip().lower() == "light" else "auto"


def _protection_surface_detected(*, html_text: str, reason_text: str) -> bool:
    hay = f"{str(reason_text or '')}\n{str(html_text or '')}".lower()
    if not hay:
        return False
    signals = (
        "press & hold",
        "are you a person or a robot",
        "px-captcha",
        "human verification",
        "captcha",
        "verification required",
        "enable javascript",
        "turn cookies on",
        "cloudflare",
    )
    return any(sig in hay for sig in signals)


def _allow_layer3_model_escalation(
    *,
    control_model: str,
    attempt_index: int,
    turn_index: int,
    max_retries: int,
    max_turns: int,
    protection_detected: bool,
    used_count: int,
    max_count: int,
) -> bool:
    mode = str(control_model or "").strip().lower()
    if mode == "legacy":
        return True
    if used_count >= max(0, int(max_count)):
        return False
    if protection_detected:
        return True
    if mode != _STRICT_THREE_LAYER_MODEL:
        return True
    if attempt_index < 0 or turn_index < 0:
        return False
    at_last_attempt = int(attempt_index) + 1 >= max(1, int(max_retries))
    at_last_turn = int(turn_index) + 1 >= max(1, int(max_turns))
    return bool(at_last_attempt and at_last_turn)


def _resolve_runtime_symbol_overrides(legacy_module) -> Dict[str, Any]:
    """Compatibility wrapper delegating to extracted bootstrap resolver."""
    base_symbols = {name: globals().get(name) for name in _RUNTIME_PATCHABLE_SYMBOLS}
    return _resolve_runtime_symbol_overrides_impl(
        legacy_module,
        base_symbols=base_symbols,
        step_trace_memory_hint_fallback=globals().get("_step_trace_memory_hint_impl"),
    )


def _build_scenario_return_context(
    *,
    scenario_started_at: float,
    site_key: str,
    scenario_run_id: str,
    router,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    graph_stats,
    browser,
    write_evidence_checkpoint_fn,
    write_progress_snapshot_fn,
    build_route_state_fallback_fn,
    build_extract_verdict_fn,
    write_route_state_debug_fn,
    get_artifacts_dir_fn,
) -> ReturnBuilderContext:
    """Build ReturnBuilderContext for scenario return handling."""
    return ReturnBuilderContext(
        scenario_started_at=scenario_started_at,
        site_key=site_key,
        scenario_run_id=scenario_run_id,
        router=router,
        url=url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        graph_stats=graph_stats,
        browser=browser,
        write_evidence_checkpoint_fn=write_evidence_checkpoint_fn,
        write_progress_snapshot_fn=write_progress_snapshot_fn,
        build_route_state_fallback_fn=build_route_state_fallback_fn,
        build_extract_verdict_fn=build_extract_verdict_fn,
        write_route_state_debug_fn=write_route_state_debug_fn,
        get_artifacts_dir_fn=get_artifacts_dir_fn,
    )


def _build_scenario_return_callable(
    *,
    scenario_started_at: float,
    site_key: str,
    scenario_run_id: str,
    router,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    graph_stats,
    browser,
    write_evidence_checkpoint_fn,
    write_progress_snapshot_fn,
    build_route_state_fallback_fn,
    build_extract_verdict_fn,
    write_route_state_debug_fn,
    get_artifacts_dir_fn,
):
    """Build the hardened scenario return callable with explicit dependencies."""
    context = _build_scenario_return_context(
        scenario_started_at=scenario_started_at,
        site_key=site_key,
        scenario_run_id=scenario_run_id,
        router=router,
        url=url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        graph_stats=graph_stats,
        browser=browser,
        write_evidence_checkpoint_fn=write_evidence_checkpoint_fn,
        write_progress_snapshot_fn=write_progress_snapshot_fn,
        build_route_state_fallback_fn=build_route_state_fallback_fn,
        build_extract_verdict_fn=build_extract_verdict_fn,
        write_route_state_debug_fn=write_route_state_debug_fn,
        get_artifacts_dir_fn=get_artifacts_dir_fn,
    )
    return partial(scenario_return_impl, context=context)


def _build_google_recovery_collab_context(
    *,
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    trip_type: str,
    is_domestic: bool,
    max_transit: Optional[int],
    mimic_locale: Optional[str],
    mimic_region: Optional[str],
    google_recovery_mode: bool,
    google_recovery_collab_limits: Dict[str, Any],
    google_recovery_collab_usage: Dict[str, int],
    local_knowledge_hint: str,
    planner_notes: List[str],
    trace_memory_hint: str,
    vlm_ui_hint: Optional[Dict[str, Any]],
    global_knowledge_hint: str,
    router,
    browser,
    scenario_run_id: str,
    site_recovery_collab_trigger_reason_dispatch_fn,
    site_recovery_collab_scope_repair_plan_dispatch_fn,
    threshold_site_value_fn,
    soften_recovery_route_fills_fn,
    retarget_plan_inputs_fn,
    site_recovery_collab_focus_plan_dispatch_fn,
    is_valid_plan_fn,
    run_vision_page_kind_probe_fn,
    apply_vision_page_kind_hints_fn,
    site_recovery_pre_date_gate_dispatch_fn,
    compose_local_hint_with_notes_fn,
    call_repair_action_plan_bundle_fn,
    call_generate_action_plan_bundle_fn,
    planner_snapshot_path_fn,
    try_recovery_collab_followup_impl_fn,
    google_non_flight_scope_repair_plan_fn,
    google_route_core_only_recovery_plan_fn,
    google_route_core_before_date_gate_fn,
) -> GoogleRecoveryCollabContext:
    """Build GoogleRecoveryCollabContext used by follow-up recovery orchestration."""
    return GoogleRecoveryCollabContext(
        site_key=site_key,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        is_domestic=is_domestic,
        max_transit=max_transit,
        mimic_locale=mimic_locale,
        mimic_region=mimic_region,
        google_recovery_mode=google_recovery_mode,
        google_recovery_collab_limits=google_recovery_collab_limits,
        google_recovery_collab_usage=google_recovery_collab_usage,
        local_knowledge_hint=local_knowledge_hint,
        planner_notes=planner_notes,
        trace_memory_hint=trace_memory_hint,
        vlm_ui_hint=vlm_ui_hint,
        global_knowledge_hint=global_knowledge_hint,
        router=router,
        browser=browser,
        scenario_run_id=scenario_run_id,
        site_recovery_collab_trigger_reason_dispatch_fn=site_recovery_collab_trigger_reason_dispatch_fn,
        site_recovery_collab_scope_repair_plan_dispatch_fn=site_recovery_collab_scope_repair_plan_dispatch_fn,
        threshold_site_value_fn=threshold_site_value_fn,
        soften_recovery_route_fills_fn=soften_recovery_route_fills_fn,
        retarget_plan_inputs_fn=retarget_plan_inputs_fn,
        site_recovery_collab_focus_plan_dispatch_fn=site_recovery_collab_focus_plan_dispatch_fn,
        is_valid_plan_fn=is_valid_plan_fn,
        run_vision_page_kind_probe_fn=run_vision_page_kind_probe_fn,
        apply_vision_page_kind_hints_fn=apply_vision_page_kind_hints_fn,
        site_recovery_pre_date_gate_dispatch_fn=site_recovery_pre_date_gate_dispatch_fn,
        compose_local_hint_with_notes_fn=compose_local_hint_with_notes_fn,
        call_repair_action_plan_bundle_fn=call_repair_action_plan_bundle_fn,
        call_generate_action_plan_bundle_fn=call_generate_action_plan_bundle_fn,
        planner_snapshot_path_fn=planner_snapshot_path_fn,
        try_recovery_collab_followup_impl_fn=try_recovery_collab_followup_impl_fn,
        google_non_flight_scope_repair_plan_fn=google_non_flight_scope_repair_plan_fn,
        google_route_core_only_recovery_plan_fn=google_route_core_only_recovery_plan_fn,
        google_route_core_before_date_gate_fn=google_route_core_before_date_gate_fn,
    )





def run_agentic_scenario(
    url,
    origin,
    dest,
    depart,
    return_date=None,
    trip_type="one_way",
    is_domestic=None,
    max_transit=None,
    human_mimic=False,
    disable_http2=False,
    knowledge_user=None,
    mimic_locale=None,
    mimic_timezone=None,
    mimic_currency=None,
    mimic_region=None,
    mimic_latitude=None,
    mimic_longitude=None,
    site_key="google_flights",
    browser_engine="chromium",
):
    """Execute a flight-search scenario, repairing the action plan when needed."""
    scenario_run_id = (
        str(os.getenv("FLIGHT_WATCHER_EVIDENCE_RUN_ID", "") or "").strip()
        or datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    )
    if not is_supported_service(site_key):
        raise ValueError(f"Unsupported site_key: {site_key}")

    # Resolve a minimal, well-known set of monkeypatch targets from
    # the legacy `core.scenario_runner` facade for this invocation only.
    # Keep compatibility for tests/integrations while avoiding global mutation.
    import core.scenario_runner as _sr

    runtime_symbols = _resolve_runtime_symbol_overrides(_sr)
    get_threshold = runtime_symbols["get_threshold"]
    time = runtime_symbols["time"]
    BrowserSession = runtime_symbols["BrowserSession"]
    execute_plan = runtime_symbols["execute_plan"]
    get_plan = runtime_symbols["get_plan"]
    get_plan_notes = runtime_symbols["get_plan_notes"]
    save_plan = runtime_symbols["save_plan"]
    _default_plan_for_service = runtime_symbols["_default_plan_for_service"]
    _is_actionable_plan = runtime_symbols["_is_actionable_plan"]
    _step_trace_memory_hint = runtime_symbols["_step_trace_memory_hint"]
    _call_generate_action_plan_bundle = runtime_symbols["_call_generate_action_plan_bundle"]
    _call_repair_action_plan_bundle = runtime_symbols["_call_repair_action_plan_bundle"]

    # Configuration constants: prefer patched values when available; fall
    # back to threshold-based defaults otherwise.
    default_scenario_max_retries, default_scenario_max_turns = _resolve_retry_turn_defaults(
        _sr, get_threshold
    )

    cfg = load_run_input_config()
    if is_domestic is None:
        is_domestic = bool(cfg.get("is_domestic", False))
    if mimic_locale is None:
        mimic_locale = cfg.get("mimic_locale")
    if mimic_timezone is None:
        mimic_timezone = cfg.get("mimic_timezone")
    if mimic_currency is None:
        mimic_currency = cfg.get("mimic_currency")
    if mimic_region is None:
        mimic_region = cfg.get("mimic_region")
    if mimic_latitude is None:
        mimic_latitude = cfg.get("mimic_latitude")
    if mimic_longitude is None:
        mimic_longitude = cfg.get("mimic_longitude")
    if knowledge_user is None:
        knowledge_user = cfg.get("knowledge_user")

    # Expose active locale so helper functions can choose locale-aware selectors.
    if isinstance(mimic_locale, str) and mimic_locale.strip():
        os.environ["FLIGHT_WATCHER_MIMIC_LOCALE"] = mimic_locale.strip()
    _clear_vlm_runtime_hints()

    flight_plan = validate_flight_plan(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        is_domestic=is_domestic,
        max_transit=max_transit,
        url=url,
    )
    url = flight_plan.url
    origin = flight_plan.origin
    dest = flight_plan.dest
    depart = flight_plan.depart
    return_date = flight_plan.return_date
    trip_type = flight_plan.trip_type
    is_domestic = flight_plan.is_domestic
    max_transit = flight_plan.max_transit
    if site_key == "google_flights":
        url = _normalize_google_deeplink_with_mimic(
            url=url,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date or "",
            trip_type=trip_type,
            mimic_locale=mimic_locale or "",
            mimic_region=mimic_region or "",
            mimic_currency=mimic_currency or "",
        )

    plugin_readiness_hints = _collect_plugin_readiness_hints(
        site_key=site_key,
        inputs={
            "site": site_key,
            "origin": origin,
            "dest": dest,
            "depart": depart,
            "return_date": return_date,
            "trip_type": trip_type,
            "is_domestic": is_domestic,
        },
    )
    _set_plugin_scenario_hints(site_key, plugin_readiness_hints)

    knowledge = get_knowledge(site_key, user_id=knowledge_user)
    global_knowledge_hint = _compose_global_knowledge_hint(knowledge)
    local_knowledge_hint = _compose_local_knowledge_hint(knowledge)
    vlm_ui_hint = {}
    planner_notes = []
    trace_memory_hint = ""
    blocked_selectors = _blocked_selectors_from_knowledge(knowledge)
    llm_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    use_fast_deterministic = llm_mode == "light"
    control_model = _resolve_control_model(llm_mode)
    strict_three_layer_control = control_model == _STRICT_THREE_LAYER_MODEL
    layer3_model_escalation_max = max(
        0,
        _env_int("FLIGHT_WATCHER_LAYER3_MODEL_ESCALATION_MAX", 1),
    )
    browser_headless_env_raw = os.getenv("FLIGHT_WATCHER_BROWSER_HEADLESS")
    browser_headless = _env_bool("FLIGHT_WATCHER_BROWSER_HEADLESS", True)
    allow_human_intervention = _env_bool("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION", False)
    intervention_mode_raw = str(os.getenv("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE", "") or "").strip().lower()
    if intervention_mode_raw in {"off", "assist", "demo"}:
        human_intervention_mode = intervention_mode_raw
    else:
        human_intervention_mode = "assist" if allow_human_intervention else "off"
    allow_human_intervention = human_intervention_mode in {"assist", "demo"}
    last_resort_human_intervention_when_disabled = _env_bool(
        "FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED",
        True,
    )
    browser_storage_state_enabled = _env_bool("FLIGHT_WATCHER_BROWSER_STORAGE_STATE_ENABLED", True)
    browser_storage_state_path = str(
        os.getenv(
            "FLIGHT_WATCHER_BROWSER_STORAGE_STATE_PATH",
            f"storage/browser_state/{(site_key or 'unknown').strip().lower()}_{(mimic_region or 'xx').strip().lower()}.json",
        )
        or ""
    ).strip()
    browser_block_heavy_resources_default = False if (site_key or "").strip().lower() == "skyscanner" else True
    browser_block_heavy_resources = _env_bool(
        "FLIGHT_WATCHER_BROWSER_BLOCK_HEAVY_RESOURCES",
        browser_block_heavy_resources_default,
    )
    manual_intervention_timeout_sec = max(
        10,
        _env_int("FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC", 120),
    )
    browser_headless_auto_override = False
    browser_headless_auto_override_reason = ""
    if (
        (site_key or "").strip().lower() == "skyscanner"
        and bool(browser_headless)
        and not str(browser_headless_env_raw or "").strip()
        and (
            bool(allow_human_intervention)
            or bool(last_resort_human_intervention_when_disabled)
        )
    ):
        # Keep explicit env configuration authoritative, but default to headed
        # for Skyscanner when a manual recovery path may be required.
        browser_headless = False
        browser_headless_auto_override = True
        browser_headless_auto_override_reason = "skyscanner_manual_recovery_requires_headed"
        log.info(
            "scenario.browser.headless_auto_override site=%s from=%s to=%s reason=%s env_headless_set=%s",
            site_key,
            True,
            False,
            browser_headless_auto_override_reason,
            False,
        )

    # Initialize Tier-0 Model Router for per-scenario event tracking
    router = ModelRouter()
    log.debug("scenario.router_initialized site=%s", site_key)

    # Initialize graph policy stats if enabled (gated by config)
    graph_stats = None
    graph_stats_enabled = bool(cfg.get("graph_policy_stats_enabled", False))
    if graph_stats_enabled:
        try:
            graph_stats = GraphPolicyStats()
            log.debug("graph_stats.initialized run_id=%s", scenario_run_id)
        except Exception as stats_exc:
            log.warning("graph_stats.init_failed error=%s", stats_exc)
            graph_stats = None

    log.info(
        "scenario.start site=%s url=%s origin=%s dest=%s depart=%s return=%s trip_type=%s is_domestic=%s max_transit=%s human_mimic=%s headless=%s disable_http2=%s browser_engine=%s allow_human_intervention=%s human_intervention_mode=%s last_resort_human_intervention_when_disabled=%s storage_state_enabled=%s storage_state_path=%s block_heavy_resources=%s manual_intervention_timeout_sec=%s knowledge_user=%s locale=%s timezone=%s currency=%s region=%s",
        site_key,
        url,
        origin,
        dest,
        depart,
        return_date,
        trip_type,
        is_domestic,
        max_transit,
        human_mimic,
        browser_headless,
        disable_http2,
        browser_engine,
        allow_human_intervention,
        human_intervention_mode,
        last_resort_human_intervention_when_disabled,
        browser_storage_state_enabled,
        browser_storage_state_path,
        browser_block_heavy_resources,
        manual_intervention_timeout_sec,
        knowledge_user,
        mimic_locale,
        mimic_timezone,
        mimic_currency,
        mimic_region,
    )
    _write_progress_snapshot(
        stage="scenario_start",
        run_id=scenario_run_id,
        site_key=site_key,
        url=url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        is_domestic=is_domestic,
        max_transit=max_transit,
        knowledge_user=knowledge_user,
    )
    _write_json_artifact_snapshot(
        scenario_run_id,
        "context/runtime_context.json",
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "site_key": site_key,
            "url": url,
            "origin": origin,
            "dest": dest,
            "depart": depart,
            "return_date": return_date,
            "trip_type": trip_type,
            "locale": mimic_locale,
            "timezone": mimic_timezone,
            "currency": mimic_currency,
            "region": mimic_region,
            "browser_engine": browser_engine,
            "headless": browser_headless,
            "headless_auto_override": bool(browser_headless_auto_override),
            "headless_auto_override_reason": browser_headless_auto_override_reason,
            "human_mimic": bool(human_mimic),
            "allow_human_intervention": bool(allow_human_intervention),
            "human_intervention_mode": human_intervention_mode,
            "last_resort_human_intervention_when_disabled": bool(last_resort_human_intervention_when_disabled),
            "browser_storage_state_enabled": bool(browser_storage_state_enabled),
            "browser_storage_state_path": browser_storage_state_path,
            "browser_block_heavy_resources": bool(browser_block_heavy_resources),
            "manual_intervention_timeout_sec": manual_intervention_timeout_sec,
            "control_model": control_model,
            "strict_three_layer_control": bool(strict_three_layer_control),
            "layer3_model_escalation_max": int(layer3_model_escalation_max),
            "thresholds": {
                "scenario_candidate_timeout_sec": int(get_threshold("scenario_candidate_timeout_sec", 120)),
                "scenario_budget_soft_margin_sec": int(get_threshold("scenario_budget_soft_margin_sec", 12)),
                "skyscanner_blocked_interstitial_grace_ms": int(
                    get_threshold("skyscanner_blocked_interstitial_grace_ms", 3500)
                ),
                "skyscanner_blocked_interstitial_grace_fallback_ms": int(
                    get_threshold("skyscanner_blocked_interstitial_grace_fallback_ms", 12000)
                ),
                "skyscanner_captcha_manual_wait_sec": int(get_threshold("skyscanner_captcha_manual_wait_sec", 45)),
            },
        },
    )

    scenario_budget_sec = _env_int(
        "FLIGHT_WATCHER_SCENARIO_BUDGET_SEC",
        int(get_threshold("scenario_candidate_timeout_sec", 120)),
    )
    scenario_budget_soft_margin_sec = max(
        3,
        int(get_threshold("scenario_budget_soft_margin_sec", 12)),
    )
    scenario_wall_clock_cap_sec = int(get_threshold("scenario_wall_clock_cap_sec", 0))
    evidence_dump_enabled = _env_bool(
        "FLIGHT_WATCHER_SCENARIO_EVIDENCE_DUMP_ENABLED",
        bool(get_threshold("scenario_evidence_dump_enabled", False)),
    )
    verify_after_fill_min_confidence = str(
        _threshold_site_value(
            "scenario_google_flights_verify_min_confidence",
            site_key,
            "medium",
        )
        or "medium"
    ).strip().lower()
    scenario_started_at = time.monotonic()
    vision_stage_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
    vision_stage_cooldown: Dict[str, str] = {}
    scope_ctx: Dict[str, Any] = {
        "site": site_key,
        "run_id": scenario_run_id,
        "_scope_override_count": 0,
    }

    _route_probe_for_html = partial(
        _route_probe_for_html_impl,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )

    _write_evidence_checkpoint = partial(
        _write_evidence_checkpoint_impl,
        scenario_run_id=scenario_run_id,
        site_key=site_key,
        url=url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        evidence_dump_enabled=evidence_dump_enabled,
    )

    _budget_remaining_sec = partial(
        _budget_remaining_sec_impl,
        scenario_budget_sec=scenario_budget_sec,
        scenario_started_at=scenario_started_at,
    )

    _budget_almost_exhausted = partial(
        _budget_almost_exhausted_impl,
        scenario_budget_sec=scenario_budget_sec,
        scenario_started_at=scenario_started_at,
        scenario_budget_soft_margin_sec=scenario_budget_soft_margin_sec,
    )

    _wall_clock_cap_exhausted = partial(
        _wall_clock_cap_exhausted_impl,
        scenario_started_at=scenario_started_at,
        scenario_wall_clock_cap_sec=scenario_wall_clock_cap_sec,
        wall_clock_cap_reached_fn=_wall_clock_cap_reached,
    )

    _run_vision_page_kind_probe = partial(
        _run_vision_page_kind_probe_impl,
        site_key=site_key,
        scenario_run_id=scenario_run_id,
        is_domestic=is_domestic,
        mimic_locale=mimic_locale,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        vision_stage_cache=vision_stage_cache,
        vision_stage_cooldown=vision_stage_cooldown,
        get_threshold_fn=get_threshold,
        snapshot_image_path_fn=_snapshot_image_path,
    )

    def _apply_vision_page_kind_hints(page_kind_payload: Dict[str, Any]) -> bool:
        """Apply deterministic optional actions from Stage-A hints."""
        return _apply_vision_page_kind_hints_impl(
            page_kind_payload,
            browser=browser,
            site_key=site_key,
            is_domestic=is_domestic,
            vlm_ui_hint=vlm_ui_hint,
            optional_click_timeout_ms_fn=_optional_click_timeout_ms,
            safe_click_first_match_fn=_safe_click_first_match,
            service_mode_toggle_step_fn=_service_mode_toggle_step,
            selector_candidates_fn=_selector_candidates,
            vision_modal_dismiss_selectors_fn=_vision_modal_dismiss_selectors,
            google_force_bind_flights_tab_selectors_fn=_google_force_bind_flights_tab_selectors,
        )

    manual_event_state: Dict[str, Any] = {
        "seq": 0,
        "last_action_count": -1,
        "last_snapshot_action_count": 0,
        "last_snapshot_ms": 0,
        "last_direct_activity_ms": 0,
        "last_payload": {},
        "last_runtime_diag": {},
    }

    def _manual_intervention_event_hook(payload: Dict[str, Any], session_obj: Any = None) -> None:
        event_payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "site_key": site_key,
            "run_id": scenario_run_id,
        }
        if isinstance(payload, dict):
            event_payload.update(payload)
        manual_event_state["seq"] = int(manual_event_state.get("seq", 0) or 0) + 1
        event_payload["seq"] = int(manual_event_state["seq"])
        manual_event_state["last_payload"] = dict(event_payload)
        _append_jsonl_artifact(
            scenario_run_id,
            "trace/manual_intervention_events.jsonl",
            event_payload,
        )
        stage = str(event_payload.get("stage", "") or "").strip().lower()
        now_ms = int(time.monotonic() * 1000)
        action_count = int(event_payload.get("ui_action_event_count", 0) or 0)
        last_action_count = int(manual_event_state.get("last_action_count", -1) or -1)
        last_snapshot_action_count = int(
            manual_event_state.get("last_snapshot_action_count", 0) or 0
        )
        last_snapshot_ms = int(manual_event_state.get("last_snapshot_ms", 0) or 0)
        direct_event_count = int(event_payload.get("direct_event_count", 0) or 0)
        proxy_event_count = int(event_payload.get("proxy_event_count", 0) or 0)
        event_mode = str(event_payload.get("mode", "") or "").strip().lower()
        if stage == "heartbeat" and action_count > last_action_count and direct_event_count > 0:
            manual_event_state["last_direct_activity_ms"] = now_ms
        last_direct_activity_ms = int(manual_event_state.get("last_direct_activity_ms", 0) or 0)
        snapshot_due = False
        if stage in {"start", "done", "error", "interrupted", "recover", "automation_action", "extend"}:
            snapshot_due = True
        elif stage == "heartbeat":
            action_delta = max(0, action_count - last_snapshot_action_count)
            min_interval_ms = 12_000 if direct_event_count > 0 else (10_000 if proxy_event_count > 0 else 18_000)
            if action_count > 0 and last_snapshot_action_count <= 0 and action_count >= 8:
                snapshot_due = True
            elif action_delta >= 80:
                snapshot_due = True
            elif (now_ms - last_snapshot_ms) >= min_interval_ms:
                snapshot_due = True
            # In demo mode, keep heartbeat telemetry non-intrusive while human is actively driving UI.
            if event_mode == "demo":
                recent_direct_activity = (
                    last_direct_activity_ms > 0 and (now_ms - last_direct_activity_ms) < 7_500
                )
                if recent_direct_activity:
                    snapshot_due = False
        if action_count > last_action_count:
            manual_event_state["last_action_count"] = action_count
        if event_mode == "demo" and stage in {"start", "heartbeat"}:
            # Demo mode is observer-only; avoid intrusive page probes while user is actively driving.
            return
        if not snapshot_due or session_obj is None:
            return
        try:
            stage_tag = f"manual_{stage}_{int(manual_event_state['seq'])}"
            html_probe = ""
            if hasattr(session_obj, "content"):
                try:
                    html_probe = str(session_obj.content() or "")
                except Exception:
                    html_probe = ""
            if html_probe:
                _write_html_snapshot(site_key, html_probe, stage=stage_tag, run_id=scenario_run_id)
            page_open_for_snapshot = False
            page_obj = getattr(session_obj, "page", None)
            if page_obj is not None:
                page_open_for_snapshot = True
                if hasattr(page_obj, "is_closed"):
                    try:
                        page_open_for_snapshot = not bool(page_obj.is_closed())
                    except Exception:
                        page_open_for_snapshot = False
            if page_open_for_snapshot:
                _write_image_snapshot(session_obj, site_key, stage=stage_tag, run_id=scenario_run_id)
            runtime_diag_payload: Dict[str, Any] = {}
            if hasattr(session_obj, "collect_runtime_diagnostics") and not (
                stage == "heartbeat" and event_mode == "demo"
            ):
                runtime_diag_payload = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "site_key": site_key,
                    "event_stage": stage,
                    "event_seq": int(manual_event_state["seq"]),
                    "diag": session_obj.collect_runtime_diagnostics(
                        selectors=manual_intervention_diagnostic_selectors(site_key=site_key)
                    ),
                }
                manual_event_state["last_runtime_diag"] = dict(runtime_diag_payload)
                _write_json_artifact_snapshot(
                    scenario_run_id,
                    f"dom_probe/{stage_tag}.json",
                    runtime_diag_payload,
                )
            if stage in {"error", "interrupted", "done"}:
                _write_json_artifact_snapshot(
                    scenario_run_id,
                    "context/manual_terminal_snapshot.json",
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "site_key": site_key,
                        "run_id": scenario_run_id,
                        "terminal_event": dict(event_payload),
                        "last_payload": dict(manual_event_state.get("last_payload", {}) or {}),
                        "last_runtime_diag": dict(
                            manual_event_state.get("last_runtime_diag", {}) or {}
                        ),
                    },
                )
            manual_event_state["last_snapshot_ms"] = now_ms
            manual_event_state["last_snapshot_action_count"] = action_count
        except Exception:
            pass

    with BrowserSession(
        headless=browser_headless,
        human_mimic=human_mimic,
        disable_http2=disable_http2,
        mimic_locale=mimic_locale,
        mimic_timezone=mimic_timezone,
        mimic_currency=mimic_currency,
        mimic_region=mimic_region,
        mimic_latitude=mimic_latitude,
        mimic_longitude=mimic_longitude,
        browser_engine=browser_engine,
        allow_human_intervention=allow_human_intervention,
        human_intervention_mode=human_intervention_mode,
        last_resort_manual_when_disabled=last_resort_human_intervention_when_disabled,
        manual_intervention_timeout_sec=manual_intervention_timeout_sec,
        manual_intervention_event_hook=_manual_intervention_event_hook,
        block_heavy_resources=browser_block_heavy_resources,
        storage_state_path=(browser_storage_state_path if browser_storage_state_enabled else ""),
        persist_storage_state=browser_storage_state_enabled,
    ) as browser:
        _scenario_return = _build_scenario_return_callable(
            scenario_started_at=scenario_started_at,
            site_key=site_key,
            scenario_run_id=scenario_run_id,
            router=router,
            url=url,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            graph_stats=graph_stats,
            browser=browser,
            write_evidence_checkpoint_fn=_write_evidence_checkpoint,
            write_progress_snapshot_fn=_write_progress_snapshot,
            build_route_state_fallback_fn=_build_route_state_return_fallback_payload,
            build_extract_verdict_fn=_build_route_state_scenario_extract_verdict,
            write_route_state_debug_fn=_write_route_state_debug,
            get_artifacts_dir_fn=get_artifacts_dir,
        )

        try:
            browser.goto(url)
        except Exception as goto_exc:
            try:
                _write_html_snapshot(site_key, browser.content(), stage="goto_error", run_id=scenario_run_id)
                _write_image_snapshot(browser, site_key, stage="goto_error", run_id=scenario_run_id)
            except Exception as snapshot_exc:
                log.warning(
                    "scenario.goto.snapshot_failed site=%s run_id=%s goto_error=%s snapshot_error=%s",
                    site_key,
                    scenario_run_id,
                    goto_exc,
                    snapshot_exc,
                )
            raise

        initial_html = browser.content()
        if hasattr(browser, "collect_runtime_diagnostics"):
            _write_json_artifact_snapshot(
                scenario_run_id,
                "context/runtime_browser_initial.json",
                browser.collect_runtime_diagnostics(
                    selectors=[
                        "#px-captcha",
                        "iframe[title*='Human verification' i]",
                        "input[name*='origin']",
                        "input[name*='from']",
                    ]
                ),
            )
        _write_html_snapshot(site_key, initial_html, stage="initial", run_id=scenario_run_id)
        _write_image_snapshot(browser, site_key, stage="initial", run_id=scenario_run_id)
        _write_evidence_checkpoint(
            "after_initial_page_load",
            payload={
                "route_bind": _route_probe_for_html(initial_html),
                "readiness": {"ready": False, "override_reason": "initial_page_load"},
            },
        )
        # Initialize last_html for fallback guarantee
        last_html = initial_html
        if last_html:
            log.info(
                "scenario.last_html.initialize source=initial_page_load html_len=%d",
                len(last_html),
            )
        phase_probe_seq = 0
        phase_probe_baseline: Dict[str, Any] = {}

        def _step_trace_phase_hints(step_trace_payload: Any) -> Dict[str, Any]:
            hints = {
                "search_action_detected": False,
                "results_wait_detected": False,
                "manual_ui_event_count": 0,
                "manual_ui_signal_quality": "",
                "step_count": 0,
            }
            if not isinstance(step_trace_payload, list):
                return hints
            hints["step_count"] = len(step_trace_payload)
            search_tokens = (
                "search",
                "submit",
                "results",
                "find flights",
                "show flights",
                "update results",
            )
            wait_tokens = ("results_wait", "wait_for_results", "results_transition")
            for item in step_trace_payload[:40]:
                if not isinstance(item, dict):
                    continue
                step_name = str(item.get("step", "") or "").strip().lower()
                status_name = str(item.get("status", "") or "").strip().lower()
                selector_name = str(item.get("selector", "") or "").strip().lower()
                blob = " ".join([step_name, status_name, selector_name])
                if any(token in blob for token in search_tokens):
                    hints["search_action_detected"] = True
                if any(token in blob for token in wait_tokens):
                    hints["results_wait_detected"] = True
                manual_payload = item.get("manual_intervention", {})
                if isinstance(manual_payload, dict):
                    capture = manual_payload.get("ui_action_capture", {})
                    if isinstance(capture, dict):
                        hints["manual_ui_event_count"] = max(
                            int(hints.get("manual_ui_event_count", 0) or 0),
                            int(capture.get("event_count", 0) or 0),
                        )
                        if not str(hints.get("manual_ui_signal_quality", "") or "").strip():
                            hints["manual_ui_signal_quality"] = str(
                                capture.get("signal_quality", "") or ""
                            )
            return hints

        def _capture_phase_probe(
            *,
            stage: str,
            attempt_idx: int,
            turn_idx: int,
            step_trace_payload: Any = None,
            extra: Dict[str, Any] = None,
            snapshot_html: bool = False,
        ) -> None:
            nonlocal phase_probe_seq
            safe_stage = str(stage or "unknown").strip().lower().replace(" ", "_")
            phase_probe_seq += 1
            selectors = [
                "#px-captcha",
                "iframe[title*='Human verification' i]",
                "button[type='submit']",
                "[data-test-id*='search']",
            ]
            runtime_diag: Dict[str, Any] = {}
            if hasattr(browser, "collect_runtime_diagnostics"):
                try:
                    runtime_diag = browser.collect_runtime_diagnostics(selectors=selectors)
                except Exception:
                    runtime_diag = {}
            network_diag: Dict[str, Any] = {}
            if hasattr(browser, "get_network_activity_snapshot"):
                try:
                    network_diag = browser.get_network_activity_snapshot(window_sec=20)
                except Exception:
                    network_diag = {}
            cookies_now = int(
                ((runtime_diag.get("cookies", {}) or {}).get("count_total", -1) or -1)
            )
            dom_probe = runtime_diag.get("dom_probe", {}) if isinstance(runtime_diag, dict) else {}
            local_storage_now = int((dom_probe.get("local_storage_len", -1) or -1)) if isinstance(dom_probe, dict) else -1
            session_storage_now = int((dom_probe.get("session_storage_len", -1) or -1)) if isinstance(dom_probe, dict) else -1
            prev_cookies = int(phase_probe_baseline.get("cookies_total", cookies_now) or cookies_now)
            prev_local_storage = int(
                phase_probe_baseline.get("local_storage_len", local_storage_now) or local_storage_now
            )
            prev_session_storage = int(
                phase_probe_baseline.get("session_storage_len", session_storage_now) or session_storage_now
            )
            phase_probe_baseline["cookies_total"] = cookies_now
            phase_probe_baseline["local_storage_len"] = local_storage_now
            phase_probe_baseline["session_storage_len"] = session_storage_now
            trace_hints = _step_trace_phase_hints(step_trace_payload)
            payload = {
                "timestamp": datetime.now(UTC).isoformat(),
                "site_key": site_key,
                "run_id": scenario_run_id,
                "seq": int(phase_probe_seq),
                "stage": safe_stage,
                "attempt": int(attempt_idx),
                "turn": int(turn_idx),
                "runtime_diag": runtime_diag if isinstance(runtime_diag, dict) else {},
                "network_diag": network_diag if isinstance(network_diag, dict) else {},
                "trace_hints": trace_hints,
                "state_delta": {
                    "cookies_total_delta": int(cookies_now - prev_cookies) if cookies_now >= 0 and prev_cookies >= 0 else 0,
                    "local_storage_len_delta": int(local_storage_now - prev_local_storage)
                    if local_storage_now >= 0 and prev_local_storage >= 0
                    else 0,
                    "session_storage_len_delta": int(session_storage_now - prev_session_storage)
                    if session_storage_now >= 0 and prev_session_storage >= 0
                    else 0,
                },
                "extra": dict(extra or {}),
            }
            _write_json_artifact_snapshot(
                scenario_run_id,
                f"trace/phase_probe_attempt_{attempt_idx}_turn_{turn_idx}_{safe_stage}_{int(phase_probe_seq)}.json",
                payload,
            )
            challenge_status_hits = 0
            if isinstance(network_diag, dict):
                challenge_status_hits = int(
                    (((network_diag.get("window", {}) or {}).get("challenge_status_hits", 0)) or 0)
                )
            _append_jsonl_artifact(
                scenario_run_id,
                "trace/graph_transitions.jsonl",
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "site_key": site_key,
                    "attempt": int(attempt_idx),
                    "turn": int(turn_idx),
                    "stage": "phase_probe",
                    "phase": safe_stage,
                    "seq": int(phase_probe_seq),
                    "search_action_detected": bool(trace_hints.get("search_action_detected", False)),
                    "results_wait_detected": bool(trace_hints.get("results_wait_detected", False)),
                    "challenge_status_hits": int(challenge_status_hits),
                },
            )
            if snapshot_html:
                try:
                    html_probe = str(browser.content() or "")
                except Exception:
                    html_probe = ""
                if html_probe:
                    _write_html_snapshot(
                        site_key,
                        html_probe,
                        stage=f"phase_{safe_stage}_{attempt_idx}_{turn_idx}_{int(phase_probe_seq)}",
                        run_id=scenario_run_id,
                    )
                    _write_image_snapshot(
                        browser,
                        site_key,
                        stage=f"phase_{safe_stage}_{attempt_idx}_{turn_idx}_{int(phase_probe_seq)}",
                        run_id=scenario_run_id,
                    )
        if _wall_clock_cap_exhausted():
            return _scenario_return(
                initial_html,
                ready=False,
                reason="scenario_wall_clock_cap",
                scope_class="unknown",
                route_bound=False,
                route_support="none",
            )

        # Avoid front-loading VLM assist when manual-first flow is expected or
        # when page is already on a known challenge/interstitial surface.
        skip_initial_vlm_ui_assist = False
        skip_initial_vlm_ui_assist_reason = ""
        if human_intervention_mode == "demo":
            skip_initial_vlm_ui_assist = True
            skip_initial_vlm_ui_assist_reason = "demo_mode"
        else:
            page_url_now = str(getattr(getattr(browser, "page", None), "url", "") or "").lower()
            on_verification_url = bool(is_verification_url(page_url_now))
            if on_verification_url:
                skip_initial_vlm_ui_assist = True
                skip_initial_vlm_ui_assist_reason = "verification_surface_detected"
        if not skip_initial_vlm_ui_assist and (site_key or "").strip().lower() == "skyscanner":
            skyscanner_block = {}
            try:
                skyscanner_block = _detect_skyscanner_interstitial_block(initial_html) or {}
            except Exception:
                skyscanner_block = {}
            page_url_now = str(getattr(getattr(browser, "page", None), "url", "") or "").lower()
            on_captcha_url = bool(is_verification_url(page_url_now))
            if bool(skyscanner_block) or on_captcha_url:
                skip_initial_vlm_ui_assist = True
                skip_initial_vlm_ui_assist_reason = "skyscanner_interstitial_detected"

        if skip_initial_vlm_ui_assist:
            log.info(
                "scenario.vlm_ui_assist_skipped site=%s run_id=%s reason=%s",
                site_key,
                scenario_run_id,
                skip_initial_vlm_ui_assist_reason or "policy",
            )
        else:
            # Run VLM UI assist on initial page if enabled
            try:
                vlm_ui_hint, local_knowledge_hint = _maybe_run_initial_vlm_ui_assist(
                    site_key=site_key,
                    url=url,
                    initial_html=initial_html,
                    is_domestic=is_domestic,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date or "",
                    mimic_locale=mimic_locale,
                    local_knowledge_hint=local_knowledge_hint,
                    scenario_run_id=scenario_run_id,
                )
            except Exception as vlm_ui_exc:
                # Best-effort: if VLM assist cannot run due to import/circular issues,
                # continue without it to keep scenario runner robust during extraction.
                log.warning(
                    "scenario.vlm_ui_assist_failed site=%s run_id=%s error=%s",
                    site_key,
                    scenario_run_id,
                    vlm_ui_exc,
                )
                vlm_ui_hint = {}

        skip_cached_plan = False
        force_google_deeplink_recovery_plan = False
        google_recovery_missing_roles = set()
        google_deeplink_page_state_recovery_uses = 0

        # [AGENT V0] Try lightweight agent framework if flag is enabled.
        # This runs independently and does NOT interfere with the existing flow.
        agent_html, agent_succeeded = _try_agent_v0_optional(
            browser=browser,
            url=url,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            site_key=site_key,
        )
        if agent_succeeded and isinstance(agent_html, str) and agent_html:
            log.info("scenario.agent_v0.success returning_ready_html")
            return _scenario_return(
                agent_html,
                ready=True,
                reason="agent_v0_ready",
                scope_class="flight_only",
                route_bound=True,
                route_support="agent",
            )

        # HARDENING: Fallback reload policy
        # If agent didn't succeed, reload URL to ensure clean DOM state
        # before continuing with the rest of the flow. This prevents DOM mutations
        # from agent attempts from affecting subsequent form interactions.
        if not agent_succeeded and site_key == "google_flights":
            try:
                if human_intervention_mode == "demo":
                    log.info(
                        "scenario.agent_v0.fallback_reload_skipped site=%s mode=%s",
                        site_key,
                        human_intervention_mode,
                    )
                else:
                    log.info("scenario.agent_v0.fallback_reload url=%s", url)
                    browser.goto(url)
            except Exception as exc:
                log.warning("scenario.agent_v0.fallback_reload failed: %s", exc)
            _write_html_snapshot(site_key, browser.content(), stage="after_agent_v0_fallback", run_id=scenario_run_id)

        # Google Flights deep-link fast path: when URL already encodes route/date,
        # avoid expensive form interactions and return once results look ready.
        if site_key == "google_flights" and "flt=" in (url or ""):
            deeplink_page_state_recovery_enabled, deeplink_page_state_recovery_max_actions = (
                _google_deeplink_page_state_recovery_policy()
            )
            # Use fresh HTML after fallback reload (if performed) or initial
            probe_html = browser.content() if not agent_succeeded else initial_html
            probe_timeout_ms = int(
                get_threshold("google_flights_deeplink_probe_timeout_ms", 35000)
            )
            probe_interval_ms = int(
                get_threshold("google_flights_deeplink_probe_interval_ms", 800)
            )
            probe_deadline = time.monotonic() + max(2.0, probe_timeout_ms / 1000.0)
            last_reason = "not_checked"
            while time.monotonic() <= probe_deadline:
                if _wall_clock_cap_exhausted():
                    return _scenario_return(
                        probe_html,
                        ready=False,
                        reason="scenario_wall_clock_cap",
                        scope_class="unknown",
                        route_bound=False,
                        route_support="none",
                    )
                ready, reason = _google_deeplink_probe_status(probe_html, url)
                last_reason = reason
                if ready:
                    log.info("scenario.fast_path.google_flights_deeplink ready=True")
                    _write_evidence_checkpoint(
                        "after_deeplink_probe",
                        payload={
                            "route_bind": _route_probe_for_html(probe_html),
                            "readiness": {
                                "ready": True,
                                "override_reason": "deeplink_probe_ready",
                            },
                        },
                    )
                    return _scenario_return(
                        probe_html,
                        ready=True,
                        reason="deeplink_probe_ready",
                        scope_class="flight_only",
                        route_bound=True,
                        route_support="strong",
                    )
                try:
                    browser.page.wait_for_timeout(max(100, probe_interval_ms))
                except Exception as wait_exc:
                    log.debug(
                        "scenario.fast_path.wait_failed stage=deeplink_probe site=%s error=%s",
                        site_key,
                        wait_exc,
                    )
                probe_html = browser.content()
            log.info(
                "scenario.fast_path.google_flights_deeplink ready=False reason=%s",
                last_reason,
            )
            _write_evidence_checkpoint(
                "after_deeplink_probe",
                payload={
                    "route_bind": _route_probe_for_html(probe_html),
                    "readiness": {
                        "ready": False,
                        "override_reason": f"deeplink_probe_{last_reason}",
                    },
                },
            )
            recovery_out = _attempt_google_deeplink_page_state_recovery(
                browser,
                trigger_reason=last_reason,
                url=url,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date or "",
                trip_type=trip_type,
                enabled=deeplink_page_state_recovery_enabled,
                uses=google_deeplink_page_state_recovery_uses,
                max_extra_actions=deeplink_page_state_recovery_max_actions,
            )
            if recovery_out.get("used"):
                google_deeplink_page_state_recovery_uses = int(
                    recovery_out.get("uses", google_deeplink_page_state_recovery_uses)
                )
                log.info(
                    "scenario.fast_path.google_flights_deeplink page_state_recovery used=True trigger=%s ready=%s fail_fast=%s reason=%s use_index=%s/%s",
                    recovery_out.get("trigger_reason", last_reason),
                    bool(recovery_out.get("ready", False)),
                    bool(recovery_out.get("fail_fast", False)),
                    recovery_out.get("reason", ""),
                    google_deeplink_page_state_recovery_uses,
                    max(0, deeplink_page_state_recovery_max_actions),
                )
                if recovery_out.get("ready"):
                    return _scenario_return(
                        str(recovery_out.get("html", "") or probe_html),
                        ready=True,
                        reason=str(recovery_out.get("reason", "") or "deeplink_page_state_recovery_ready"),
                        scope_class="flight_only",
                        route_bound=True,
                        route_support="strong",
                    )
                if recovery_out.get("fail_fast"):
                    return _scenario_return(
                        str(recovery_out.get("html", "") or probe_html),
                        ready=False,
                        reason=str(recovery_out.get("reason", "") or "deeplink_page_state_recovery_failed"),
                        scope_class=str(recovery_out.get("scope_class", "unknown") or "unknown"),
                        route_bound=False,
                        route_support="none",
                    )
                probe_html = str(recovery_out.get("html", "") or probe_html)
            vision_trigger_reason = last_reason
            if use_fast_deterministic and bool(
                get_threshold("google_flights_quick_rebind_enabled", True)
            ):
                rebound_ok, rebound_reason, rebound_html = _google_deeplink_quick_rebind(
                    browser,
                    url=url,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    trip_type=trip_type,
                )
                log.info(
                    "scenario.fast_path.google_flights_deeplink rebind=%s reason=%s",
                    rebound_ok,
                    rebound_reason,
                )
                if rebound_ok:
                    return _scenario_return(
                        rebound_html,
                        ready=True,
                        reason="deeplink_quick_rebind_ready",
                        scope_class="flight_only",
                        route_bound=True,
                        route_support="strong",
                    )
                recovery_out = _attempt_google_deeplink_page_state_recovery(
                    browser,
                    trigger_reason=rebound_reason or last_reason,
                    url=url,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date or "",
                    trip_type=trip_type,
                    enabled=deeplink_page_state_recovery_enabled,
                    uses=google_deeplink_page_state_recovery_uses,
                    max_extra_actions=deeplink_page_state_recovery_max_actions,
                )
                if recovery_out.get("used"):
                    google_deeplink_page_state_recovery_uses = int(
                        recovery_out.get("uses", google_deeplink_page_state_recovery_uses)
                    )
                    log.info(
                        "scenario.fast_path.google_flights_deeplink page_state_recovery used=True trigger=%s ready=%s fail_fast=%s reason=%s use_index=%s/%s",
                        recovery_out.get("trigger_reason", rebound_reason or last_reason),
                        bool(recovery_out.get("ready", False)),
                        bool(recovery_out.get("fail_fast", False)),
                        recovery_out.get("reason", ""),
                        google_deeplink_page_state_recovery_uses,
                        max(0, deeplink_page_state_recovery_max_actions),
                    )
                    if recovery_out.get("ready"):
                        return _scenario_return(
                            str(recovery_out.get("html", "") or rebound_html),
                            ready=True,
                            reason=str(recovery_out.get("reason", "") or "deeplink_page_state_recovery_ready"),
                            scope_class="flight_only",
                            route_bound=True,
                            route_support="strong",
                        )
                    if recovery_out.get("fail_fast"):
                        return _scenario_return(
                            str(recovery_out.get("html", "") or rebound_html),
                            ready=False,
                            reason=str(recovery_out.get("reason", "") or "deeplink_page_state_recovery_failed"),
                            scope_class=str(recovery_out.get("scope_class", "unknown") or "unknown"),
                            route_bound=False,
                            route_support="none",
                        )
                probe_html = rebound_html
                vision_trigger_reason = rebound_reason or last_reason
                skip_cached_plan = True
                force_google_deeplink_recovery_plan = True
                google_recovery_missing_roles = _google_missing_roles_from_reason(
                    rebound_reason,
                    trip_type,
                )
                log.info(
                    "scenario.plan.cache_bypass site=%s reason=%s",
                    site_key,
                    rebound_reason,
                )
            vision_page_kind = _run_vision_page_kind_probe(
                html_text=probe_html,
                screenshot_stage="initial",
                trigger_reason=vision_trigger_reason,
            )
            if vision_page_kind and _apply_vision_page_kind_hints(vision_page_kind):
                try:
                    browser.page.wait_for_timeout(200)
                except Exception as wait_exc:
                    log.debug(
                        "scenario.fast_path.wait_failed stage=vision_hint_recheck site=%s error=%s",
                        site_key,
                        wait_exc,
                    )
                probe_html = browser.content()
                repaired_ready, repaired_reason = _google_deeplink_probe_status(probe_html, url)
                log.info(
                    "scenario.fast_path.google_flights_deeplink vision_hint_recheck ready=%s reason=%s",
                    repaired_ready,
                    repaired_reason,
                )
                if repaired_ready:
                    return _scenario_return(
                        probe_html,
                        ready=True,
                        reason="vision_page_kind_recovered",
                        scope_class="flight_only",
                        route_bound=True,
                        route_support="strong",
                    )
            if use_fast_deterministic and bool(
                get_threshold(
                    "google_flights_deeplink_lightmode_return_on_unready",
                    True,
                )
            ):
                log.warning(
                    "scenario.fast_path.google_flights_deeplink fallback=latest_html mode=light"
                )
                return _scenario_return(
                    probe_html,
                    ready=False,
                    reason="deeplink_probe_unready_light_fallback",
                    scope_class="unknown",
                    route_bound=False,
                    route_support="none",
                )

        plan = None
        if force_google_deeplink_recovery_plan and site_key == "google_flights":
            recovery_soft_fail_fills = bool(
                _threshold_site_value(
                    "scenario_recovery_fill_soft_fail",
                    site_key,
                    True,
                )
            )
            plan = _google_deeplink_recovery_plan(
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                trip_type=trip_type,
                missing_roles=google_recovery_missing_roles,
                soft_fail_fills=recovery_soft_fail_fills,
            )
            log.info(
                "scenario.plan.google_deeplink_recovery valid=%s missing_roles=%s soft_fail_fills=%s",
                _is_valid_plan(plan),
                ",".join(sorted(google_recovery_missing_roles))
                if google_recovery_missing_roles
                else "",
                recovery_soft_fail_fills,
            )
        google_recovery_mode = force_google_deeplink_recovery_plan and site_key == "google_flights"
        light_try_llm_plan = _env_bool(
            "FLIGHT_WATCHER_LIGHT_TRY_LLM_PLAN_ON_FAST_PLAN_FAILURE",
            bool(get_threshold("light_mode_try_llm_plan_on_fast_plan_failure", True)),
        )
        light_planner_timeout_sec = _env_int(
            "FLIGHT_WATCHER_LLM_LIGHT_PLANNER_TIMEOUT_SEC",
            int(get_threshold("llm_light_planner_timeout_sec", 35)),
        )
        google_recovery_collab_limits = _site_recovery_collab_limits_from_thresholds_dispatch(
            site_key,
            google_limits_fn=_google_recovery_collab_limits_from_thresholds,
        )
        google_recovery_collab_usage: Dict[str, int] = {"vlm": 0, "repair": 0, "planner": 0}
        _google_recovery_collab_context_builder = partial(
            _build_google_recovery_collab_context,
            site_key=site_key,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            trip_type=trip_type,
            is_domestic=is_domestic,
            max_transit=max_transit,
            mimic_locale=mimic_locale,
            mimic_region=mimic_region,
            google_recovery_mode=google_recovery_mode,
            google_recovery_collab_limits=google_recovery_collab_limits,
            google_recovery_collab_usage=google_recovery_collab_usage,
            local_knowledge_hint=local_knowledge_hint,
            planner_notes=planner_notes,
            trace_memory_hint=trace_memory_hint,
            vlm_ui_hint=vlm_ui_hint,
            global_knowledge_hint=global_knowledge_hint,
            router=router,
            browser=browser,
            scenario_run_id=scenario_run_id,
            site_recovery_collab_trigger_reason_dispatch_fn=_site_recovery_collab_trigger_reason_dispatch,
            site_recovery_collab_scope_repair_plan_dispatch_fn=_site_recovery_collab_scope_repair_plan_dispatch,
            threshold_site_value_fn=_threshold_site_value,
            soften_recovery_route_fills_fn=_soften_recovery_route_fills,
            retarget_plan_inputs_fn=_retarget_plan_inputs,
            site_recovery_collab_focus_plan_dispatch_fn=_site_recovery_collab_focus_plan_dispatch,
            is_valid_plan_fn=_is_valid_plan,
            run_vision_page_kind_probe_fn=_run_vision_page_kind_probe,
            apply_vision_page_kind_hints_fn=_apply_vision_page_kind_hints,
            site_recovery_pre_date_gate_dispatch_fn=_site_recovery_pre_date_gate_dispatch,
            compose_local_hint_with_notes_fn=_compose_local_hint_with_notes,
            call_repair_action_plan_bundle_fn=_call_repair_action_plan_bundle,
            call_generate_action_plan_bundle_fn=_call_generate_action_plan_bundle,
            planner_snapshot_path_fn=_planner_snapshot_path,
            try_recovery_collab_followup_impl_fn=_try_recovery_collab_followup_impl,
            google_non_flight_scope_repair_plan_fn=_google_non_flight_scope_repair_plan,
            google_route_core_only_recovery_plan_fn=_google_route_core_only_recovery_plan,
            google_route_core_before_date_gate_fn=_google_route_core_before_date_gate,
        )

        def _try_google_recovery_collab_followup(
            *,
            current_html: str,
            failed_plan,
            route_core_failure: Optional[Dict[str, Any]],
            turn_index: int,
        ):
            """Phase B: bounded planner/VLM collaboration for route-core recovery."""
            context = _google_recovery_collab_context_builder()
            return google_recovery_collab_followup_impl(
                current_html=current_html,
                failed_plan=failed_plan,
                route_core_failure=route_core_failure,
                turn_index=turn_index,
                context=context,
            )

        # 1️⃣ Try stored plan first
        if plan is None:
            if skip_cached_plan:
                plan = None
            else:
                plan = get_plan(site_key)
                planner_notes = _merge_planner_notes(planner_notes, get_plan_notes(site_key))
        log.info("scenario.plan.loaded_from_store valid=%s", _is_valid_plan(plan))
        if _is_valid_plan(plan):
            plan = _retarget_plan_inputs(
                plan=plan,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                trip_type=trip_type,
                site_key=site_key,
            )
            if not google_recovery_mode:
                plan = _with_knowledge(
                    plan,
                    site_key,
                    is_domestic,
                    knowledge,
                    vlm_hint=vlm_ui_hint,
                )
            if not _is_actionable_plan(plan, trip_type, site_key=site_key):
                log.warning("scenario.plan.loaded_from_store rejected=missing_required_fields")
                plan = None

        if not _is_actionable_plan(plan, trip_type, site_key=site_key):
            if (skip_cached_plan or force_google_deeplink_recovery_plan) and site_key == "google_flights":
                # After deep-link failure, skip learned plans to avoid replaying stale selector chains.
                plan = None
                log.info("scenario.plan.loaded_from_knowledge valid=False")
            else:
                plan = knowledge.get("last_success_plan")
                log.info("scenario.plan.loaded_from_knowledge valid=%s", _is_valid_plan(plan))
            if _is_valid_plan(plan):
                plan = _retarget_plan_inputs(
                    plan=plan,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    trip_type=trip_type,
                    site_key=site_key,
                )
                if not google_recovery_mode:
                    plan = _with_knowledge(
                        plan,
                        site_key,
                        is_domestic,
                        knowledge,
                        vlm_hint=vlm_ui_hint,
                    )
                if not _is_actionable_plan(plan, trip_type, site_key=site_key):
                    log.warning("scenario.plan.loaded_from_knowledge rejected=missing_required_fields")
                    plan = None

        if not _is_actionable_plan(plan, trip_type, site_key=site_key):
            if use_fast_deterministic:
                if google_recovery_mode and site_key == "google_flights":
                    plan = _google_non_flight_scope_repair_plan(
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        trip_type=trip_type,
                        is_domestic=bool(is_domestic),
                        scope_class="unknown",
                        vlm_hint=vlm_ui_hint,
                    )
                    if bool(
                        _threshold_site_value(
                            "scenario_recovery_force_soft_fill",
                            site_key,
                            True,
                        )
                    ):
                        plan = _soften_recovery_route_fills(plan)
                else:
                    plan = _default_plan_for_service(
                        site_key,
                        origin,
                        dest,
                        depart,
                        return_date=return_date,
                        is_domestic=is_domestic,
                        knowledge={} if google_recovery_mode else knowledge,
                    )
                if _is_valid_plan(plan):
                    plan = _retarget_plan_inputs(
                        plan=plan,
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        trip_type=trip_type,
                        site_key=site_key,
                    )
                    if not google_recovery_mode:
                        plan = _with_knowledge(
                            plan,
                            site_key,
                            is_domestic,
                            knowledge,
                            vlm_hint=vlm_ui_hint,
                        )
                    elif bool(
                        _threshold_site_value(
                            "scenario_recovery_force_soft_fill",
                            site_key,
                            True,
                        )
                    ):
                        plan = _soften_recovery_route_fills(plan)
                log.info(
                    "scenario.plan.fast_default_used valid=%s actionable=%s",
                    _is_valid_plan(plan),
                    _is_actionable_plan(plan, trip_type, site_key=site_key),
                )

        if not _is_actionable_plan(plan, trip_type, site_key=site_key):
            if strict_three_layer_control:
                log.info(
                    "scenario.plan.generate_skipped site=%s reason=strict_three_layer_layer1_layer2_only",
                    site_key,
                )
            elif google_recovery_mode and use_fast_deterministic and not light_try_llm_plan:
                log.info(
                    "scenario.plan.generate_skipped site=%s reason=google_recovery_mode",
                    site_key,
                )
            else:
                try:
                    generated_plan, generated_notes = _call_generate_action_plan_bundle(
                        router=router,
                        html=initial_html,
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        trip_type=trip_type,
                        is_domestic=is_domestic,
                        max_transit=max_transit,
                        turn_index=0,
                        global_knowledge=global_knowledge_hint,
                        local_knowledge=_compose_local_hint_with_notes(
                            local_knowledge_hint, planner_notes, trace_memory_hint
                        ),
                        site_key=site_key,
                        mimic_locale=mimic_locale,
                        mimic_region=mimic_region,
                        screenshot_path=_planner_snapshot_path(site_key, ["initial", "last"], run_id=scenario_run_id),
                        trace_memory_hint=trace_memory_hint,
                        timeout_sec=(
                            light_planner_timeout_sec
                            if use_fast_deterministic
                            else None
                        ),
                    )
                    planner_notes = _merge_planner_notes(planner_notes, generated_notes)
                    plan = generated_plan
                except Exception as plan_exc:
                    if isinstance(plan_exc, (TimeoutError, KeyboardInterrupt)):
                        raise
                    log.warning(
                        "scenario.plan.generate_failed site=%s error=%s",
                        site_key,
                        plan_exc,
                    )
                    plan = None
            log.info("scenario.plan.generated valid=%s", _is_valid_plan(plan))
            if _is_valid_plan(plan):
                plan = _retarget_plan_inputs(
                    plan=plan,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    trip_type=trip_type,
                    site_key=site_key,
                )
                if not google_recovery_mode:
                    plan = _with_knowledge(
                        plan,
                        site_key,
                        is_domestic,
                        knowledge,
                        vlm_hint=vlm_ui_hint,
                    )
                if not _is_actionable_plan(plan, trip_type, site_key=site_key):
                    log.warning("scenario.plan.generated rejected=missing_required_fields")
                    plan = None

        if not _is_actionable_plan(plan, trip_type, site_key=site_key):
            try:
                plan = _default_plan_for_service(
                    site_key,
                    origin,
                    dest,
                    depart,
                    return_date=return_date,
                    is_domestic=is_domestic,
                    knowledge={} if google_recovery_mode else knowledge,
                )
                plan = _retarget_plan_inputs(
                    plan=plan,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    trip_type=trip_type,
                    site_key=site_key,
                )
                if not google_recovery_mode:
                    plan = _with_knowledge(
                        plan,
                        site_key,
                        is_domestic,
                        knowledge,
                        vlm_hint=vlm_ui_hint,
                    )
            except Exception as fallback_plan_exc:
                log.warning(
                    "scenario.plan.fallback_generation_failed site=%s error=%s",
                    site_key,
                    fallback_plan_exc,
                )
                plan = None
            log.warning(
                "scenario.plan.fallback_used source=default_service_plan site=%s valid=%s actionable=%s",
                site_key,
                _is_valid_plan(plan),
                _is_actionable_plan(plan, trip_type, site_key=site_key),
            )

        if human_intervention_mode != "demo" and not _is_actionable_plan(plan, trip_type, site_key=site_key):
            _write_debug_snapshot(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "stage": "initial_plan",
                    "site_key": site_key,
                    "url": url,
                    "origin": origin,
                    "dest": dest,
                    "depart": depart,
                    "return_date": return_date,
                    "trip_type": trip_type,
                    "is_domestic": is_domestic,
                    "max_transit": max_transit,
                    "error": "Initial action plan generation failed",
                },
                run_id=scenario_run_id,
            )
            raise RuntimeError("Initial action plan generation failed")

        max_retries = int(os.getenv("SCENARIO_MAX_RETRIES", str(default_scenario_max_retries)))
        max_turns = max(1, int(os.getenv("SCENARIO_MAX_TURNS", str(default_scenario_max_turns))))
        if force_google_deeplink_recovery_plan and site_key == "google_flights":
            # Keep recovery bounded, but allow more than one smart trial before giving up.
            recovery_retries = max(
                1,
                _env_int(
                    "SCENARIO_RECOVERY_MAX_RETRIES",
                    _threshold_site_value(
                        "scenario_recovery_max_retries",
                        site_key,
                        2,
                    ),
                ),
            )
            recovery_turns = max(
                1,
                _env_int(
                    "SCENARIO_RECOVERY_MAX_TURNS",
                    _threshold_site_value(
                        "scenario_recovery_max_turns",
                        site_key,
                        2,
                    ),
                ),
            )
            max_retries = min(max_retries, recovery_retries)
            max_turns = min(max_turns, recovery_turns)
            log.info(
                "scenario.turns.recovery_bound site=%s max_retries=%s max_turns=%s",
                site_key,
                max_retries,
                max_turns,
            )
        learned_turns = knowledge.get("suggested_turns") if isinstance(knowledge, dict) else None
        if isinstance(learned_turns, int) and learned_turns > max_turns:
            # Keep bounded while allowing site-specific multi-turn behavior to accumulate.
            max_turns = min(max(learned_turns, max_turns), 2)
            log.info(
                "scenario.turns.adjusted_from_knowledge site=%s suggested_turns=%s applied_max_turns=%s",
                site_key,
                learned_turns,
                max_turns,
            )
        bounded_retries, bounded_turns = _enforce_contract_retry_bounds(max_retries, max_turns)
        if bounded_retries != max_retries or bounded_turns != max_turns:
            log.warning(
                "scenario.turns.contract_clamp site=%s retries=%s->%s turns=%s->%s",
                site_key,
                max_retries,
                bounded_retries,
                max_turns,
                bounded_turns,
            )
        max_retries, max_turns = bounded_retries, bounded_turns
        if human_intervention_mode == "demo":
            max_retries, max_turns = 1, 1
            log.info(
                "scenario.human_intervention.demo_mode enabled retries=%s turns=%s",
                max_retries,
                max_turns,
            )
        last_error = None
        static_error_signature = ""
        static_error_repeats = 0
        layer3_model_escalation_used = 0
        skyscanner_blank_shell_manual_recovery_uses = 0
        skyscanner_blank_shell_px_recovery_uses = 0
        skyscanner_force_home_rebind_after_interstitial_clear = False
        skyscanner_force_full_refill_after_interstitial_clear = False

        for attempt in range(max_retries):
            skyscanner_post_clear_refill_executed = False
            if human_intervention_mode == "demo":
                attempt_gate = {"should_return": False, "last_error": last_error}
            else:
                attempt_gate = run_attempt_precheck_and_interstitial_gate(
                    browser=browser,
                    site_key=site_key,
                    url=url,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    trip_type=trip_type,
                    is_domestic=is_domestic,
                    max_transit=max_transit,
                    attempt=attempt,
                    max_retries=max_retries,
                    max_turns=max_turns,
                    human_mimic=bool(human_mimic),
                    plan=plan,
                    last_error=last_error,
                    scenario_run_id=scenario_run_id,
                    wall_clock_cap_exhausted_fn=_wall_clock_cap_exhausted,
                    budget_almost_exhausted_fn=_budget_almost_exhausted,
                    budget_remaining_sec_fn=_budget_remaining_sec,
                    get_threshold_fn=get_threshold,
                    detect_site_interstitial_block_fn=_detect_site_interstitial_block,
                    attempt_skyscanner_interstitial_grace_fn=_attempt_skyscanner_interstitial_grace,
                    attempt_skyscanner_interstitial_fallback_reload_fn=_attempt_skyscanner_interstitial_fallback_reload,
                    write_progress_snapshot_fn=_write_progress_snapshot,
                    write_debug_snapshot_fn=_write_debug_snapshot,
                    write_html_snapshot_fn=_write_html_snapshot,
                    write_image_snapshot_fn=_write_image_snapshot,
                    write_json_artifact_snapshot_fn=_write_json_artifact_snapshot,
                    scenario_return_fn=_scenario_return,
                    logger=log,
                )
            last_error = attempt_gate.get("last_error")
            if site_key == "skyscanner":
                skyscanner_force_home_rebind_after_interstitial_clear = bool(
                    attempt_gate.get("post_interstitial_rebind_home", False)
                )
                skyscanner_force_full_refill_after_interstitial_clear = bool(
                    attempt_gate.get("post_interstitial_rebind_home", False)
                )
            if bool(attempt_gate.get("should_return")):
                return str(attempt_gate.get("result_html", "") or "")

            try:
                route_mismatch_reset_attempts = 0
                date_reload_retry_count = 0  # Track date fill reload retries per attempt
                mismatch_rewind_priority_uses = 0
                google_force_bind_repair_uses = 0
                for turn_idx in range(max_turns):
                    _capture_phase_probe(
                        stage="turn_pre_execute",
                        attempt_idx=attempt + 1,
                        turn_idx=turn_idx + 1,
                        step_trace_payload=[],
                        extra={"mode": human_intervention_mode, "plan_len": len(list(plan or []))},
                        snapshot_html=False,
                    )
                    turn_gate = run_turn_start_gate(
                        browser=browser,
                        site_key=site_key,
                        url=url,
                        scenario_run_id=scenario_run_id,
                        attempt=attempt,
                        turn_idx=turn_idx,
                        max_turns=max_turns,
                        wall_clock_cap_exhausted_fn=_wall_clock_cap_exhausted,
                        budget_almost_exhausted_fn=_budget_almost_exhausted,
                        budget_remaining_sec_fn=_budget_remaining_sec,
                        write_progress_snapshot_fn=_write_progress_snapshot,
                        scenario_return_fn=_scenario_return,
                        vision_stage_cooldown=vision_stage_cooldown,
                        logger=log,
                    )
                    if bool(turn_gate.get("should_return")):
                        return str(turn_gate.get("result_html", "") or "")
                    if (
                        human_intervention_mode != "demo"
                        and site_key == "skyscanner"
                        and skyscanner_force_home_rebind_after_interstitial_clear
                    ):
                        current_page_url = str(
                            getattr(getattr(browser, "page", None), "url", "") or ""
                        ).strip()
                        current_is_results_route = (
                            "/transport/flights/" in current_page_url.lower()
                            and not is_verification_url(current_page_url)
                            and not is_skyscanner_px_captcha_url(current_page_url)
                        )
                        reuse_current_results = False
                        if current_is_results_route:
                            post_clear_settle_ms = max(
                                2000,
                                int(
                                    get_threshold(
                                        "scenario_skyscanner_post_clear_results_settle_ms",
                                        12000,
                                    )
                                    or 12000
                                ),
                            )
                            post_clear_poll_ms = max(
                                400,
                                min(
                                    2500,
                                    int(post_clear_settle_ms // 6),
                                ),
                            )
                            probe_error = ""
                            probe_ready = False
                            probe_shell_incomplete = False
                            probe_html_len = 0
                            settle_elapsed_ms = 0
                            settle_probes = 0
                            try:
                                settle_deadline = time.monotonic() + (post_clear_settle_ms / 1000.0)
                                stable_hits = 0
                                required_stable_hits = 2
                                if bool(last_error):
                                    # Be slightly conservative when recovering from prior turn errors.
                                    required_stable_hits = 3
                                while True:
                                    probe_html = str(browser.content() or "")
                                    probe_html_len = len(probe_html)
                                    probe_shell_incomplete = _is_skyscanner_results_shell_incomplete(
                                        probe_html,
                                        page_url=current_page_url,
                                    )
                                    probe_ready = _is_results_ready(
                                        probe_html,
                                        site_key=site_key,
                                        origin=origin,
                                        dest=dest,
                                        depart=depart,
                                        return_date=return_date,
                                        page_url=current_page_url,
                                    )
                                    settle_probes += 1
                                    if bool(probe_ready) and not bool(probe_shell_incomplete):
                                        stable_hits += 1
                                    else:
                                        stable_hits = 0
                                    if stable_hits >= required_stable_hits:
                                        break
                                    remaining_ms = int((settle_deadline - time.monotonic()) * 1000)
                                    if remaining_ms <= 0:
                                        break
                                    wait_ms = min(post_clear_poll_ms, max(120, remaining_ms))
                                    browser.page.wait_for_timeout(wait_ms)
                                settle_elapsed_ms = max(
                                    0,
                                    int(post_clear_settle_ms - max(0, int((settle_deadline - time.monotonic()) * 1000))),
                                )
                            except Exception as probe_exc:
                                probe_error = str(type(probe_exc).__name__)
                            reuse_current_results = bool(probe_ready) and (not probe_shell_incomplete)
                            log.info(
                                "scenario.skyscanner.post_interstitial_results_probe reuse=%s ready=%s shell_incomplete=%s url=%s settle_ms=%s settle_elapsed_ms=%s probes=%s html_len=%s error=%s",
                                bool(reuse_current_results),
                                bool(probe_ready),
                                bool(probe_shell_incomplete),
                                current_page_url[:180],
                                int(post_clear_settle_ms),
                                int(settle_elapsed_ms),
                                int(settle_probes),
                                int(probe_html_len),
                                probe_error,
                            )

                        if reuse_current_results:
                            # Avoid unnecessary home rebind+refill churn when challenge already
                            # landed on a usable results URL.
                            # Keep a valid no-op plan shape so execute_plan contract remains
                            # satisfied while we transition into readiness/extraction checks.
                            plan = [
                                {
                                    "action": "wait_msec",
                                    "duration_ms": 250,
                                    "metadata": {
                                        "purpose": "results_route_ready_in_place_noop",
                                    },
                                }
                            ]
                            skyscanner_force_home_rebind_after_interstitial_clear = False
                            skyscanner_force_full_refill_after_interstitial_clear = False
                            log.info(
                                "scenario.skyscanner.post_interstitial_rebind_skipped reason=results_route_ready_in_place url=%s",
                                current_page_url[:180],
                            )
                        else:
                            rebind_home_url = str(url or "").strip() or "https://www.skyscanner.com/flights"
                            rebind_ok = False
                            rebind_interstitial = False
                            rebind_form_visible = False
                            rebind_error = ""
                            try:
                                browser.page.goto(
                                    rebind_home_url,
                                    wait_until="domcontentloaded",
                                    timeout=30000,
                                )
                                browser.page.wait_for_timeout(1600)
                                rebind_html = str(browser.content() or "")
                                rebind_block = _detect_skyscanner_interstitial_block(rebind_html) or {}
                                rebind_interstitial = bool(rebind_block)
                                if not rebind_interstitial:
                                    try:
                                        rebind_form_visible = bool(
                                            browser.page.evaluate(
                                                """
                                                () => {
                                                  const isVisible = (el) => {
                                                    if (!el) return false;
                                                    const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                                                    if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                                                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                                                    return !!r && r.width > 0 && r.height > 0;
                                                  };
                                                  const origin = document.querySelector("#originInput-input, input[name='originInput-search']");
                                                  const dest = document.querySelector("#destinationInput-input, input[name='destinationInput-search']");
                                                  return isVisible(origin) && isVisible(dest);
                                                }
                                                """
                                            )
                                        )
                                    except Exception:
                                        rebind_form_visible = False
                                rebind_ok = bool(rebind_html) and (not rebind_interstitial) and rebind_form_visible
                            except Exception as rebind_exc:
                                rebind_error = str(type(rebind_exc).__name__)
                            log.info(
                                "scenario.skyscanner.post_interstitial_home_rebind ok=%s interstitial=%s form_visible=%s url=%s error=%s",
                                bool(rebind_ok),
                                bool(rebind_interstitial),
                                bool(rebind_form_visible),
                                rebind_home_url[:180],
                                rebind_error,
                            )
                            if rebind_interstitial:
                                log.warning(
                                    "scenario.turn.interstitial_surface_detected site=%s attempt=%s turn=%s/%s url=%s action=break_attempt_for_attempt_gate_post_clear_rebind",
                                    site_key,
                                    attempt + 1,
                                    turn_idx + 1,
                                    max_turns,
                                    str(getattr(getattr(browser, "page", None), "url", "") or "")[:220],
                                )
                                break
                            if skyscanner_force_full_refill_after_interstitial_clear:
                                refill_plan = _default_plan_for_service(
                                    site_key,
                                    origin,
                                    dest,
                                    depart,
                                    return_date=return_date,
                                    is_domestic=is_domestic,
                                    knowledge=knowledge if isinstance(knowledge, dict) else {},
                                )
                                refill_plan = _retarget_plan_inputs(
                                    plan=refill_plan,
                                    origin=origin,
                                    dest=dest,
                                    depart=depart,
                                    return_date=return_date,
                                    trip_type=trip_type,
                                    site_key=site_key,
                                )
                                refill_plan = _with_knowledge(
                                    refill_plan,
                                    site_key,
                                    is_domestic,
                                    knowledge,
                                    vlm_hint=vlm_ui_hint,
                                )
                                if _is_actionable_plan(refill_plan, trip_type, site_key=site_key):
                                    plan = refill_plan
                                    skyscanner_post_clear_refill_executed = True
                                    log.info(
                                        "scenario.skyscanner.post_interstitial_plan_reset reason=full_refill_after_challenge plan_len=%s",
                                        len(list(plan or [])),
                                    )
                                else:
                                    log.warning(
                                        "scenario.skyscanner.post_interstitial_plan_reset_skipped reason=non_actionable_refill plan_len=%s",
                                        len(list(refill_plan or [])),
                                    )
                            skyscanner_force_home_rebind_after_interstitial_clear = False
                            skyscanner_force_full_refill_after_interstitial_clear = False
                    if human_intervention_mode != "demo" and site_key == "skyscanner":
                        turn_page_url = str(
                            getattr(getattr(browser, "page", None), "url", "") or ""
                        )
                        if "/hotels" in turn_page_url.strip().lower():
                            recovery = _ensure_skyscanner_flights_context(
                                browser,
                                timeout_ms=6000,
                            )
                            recovered_url = str(
                                getattr(getattr(browser, "page", None), "url", "") or ""
                            )
                            log.warning(
                                "scenario.turn.hotels_context_recovery site=%s attempt=%s turn=%s/%s ok=%s reason=%s before=%s after=%s",
                                site_key,
                                attempt + 1,
                                turn_idx + 1,
                                max_turns,
                                bool((recovery or {}).get("ok", False)),
                                str((recovery or {}).get("reason", "") or ""),
                                turn_page_url[:220],
                                recovered_url[:220],
                            )
                            turn_page_url = recovered_url
                        if bool(is_verification_url(turn_page_url)) or bool(
                            is_skyscanner_px_captcha_url(turn_page_url)
                        ):
                            log.warning(
                                "scenario.turn.interstitial_surface_detected site=%s attempt=%s turn=%s/%s url=%s action=break_attempt_for_attempt_gate",
                                site_key,
                                attempt + 1,
                                turn_idx + 1,
                                max_turns,
                                turn_page_url[:220],
                            )
                            break
                    if human_intervention_mode != "demo" and site_key == "skyscanner":
                        turn_page_url_lower = str(
                            getattr(getattr(browser, "page", None), "url", "") or ""
                        ).strip().lower()
                        on_home_search_surface = bool(turn_page_url_lower.rstrip("/").endswith("/flights"))
                        if on_home_search_surface and not _is_actionable_plan(
                            plan, trip_type, site_key=site_key
                        ):
                            refill_plan = _default_plan_for_service(
                                site_key,
                                origin,
                                dest,
                                depart,
                                return_date=return_date,
                                is_domestic=is_domestic,
                                knowledge=knowledge if isinstance(knowledge, dict) else {},
                            )
                            refill_plan = _retarget_plan_inputs(
                                plan=refill_plan,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                trip_type=trip_type,
                                site_key=site_key,
                            )
                            refill_plan = _with_knowledge(
                                refill_plan,
                                site_key,
                                is_domestic,
                                knowledge,
                                vlm_hint=vlm_ui_hint,
                            )
                            if _is_actionable_plan(refill_plan, trip_type, site_key=site_key):
                                plan = refill_plan
                                log.info(
                                    "scenario.skyscanner.plan_auto_recover_full_refill reason=home_surface_non_actionable_plan plan_len=%s",
                                    len(list(plan or [])),
                                )
                    plan = _reconcile_fill_plan_roles_and_values(
                        plan,
                        site_key=site_key or "",
                        origin=origin or "",
                        dest=dest or "",
                        depart=depart or "",
                        return_date=return_date or "",
                        trip_type=trip_type,
                    )
                    if human_intervention_mode == "demo":
                        manual_result = browser.allow_manual_verification_intervention(
                            reason=f"scenario_demo_mode_attempt_{attempt + 1}_turn_{turn_idx + 1}",
                            wait_sec=manual_intervention_timeout_sec,
                        )
                        manual_reason = str((manual_result or {}).get("reason", "") or "")
                        step_trace = [
                            {
                                "step": "manual_demo_control",
                                "status": manual_reason or "manual_window_elapsed",
                                "manual_intervention": dict(manual_result) if isinstance(manual_result, dict) else {},
                            }
                        ]
                        if manual_reason == "manual_intervention_interrupted":
                            return _scenario_return(
                                browser.content(),
                                ready=False,
                                reason="demo_mode_manual_interrupted",
                                scope_class="unknown",
                                route_bound=False,
                                route_support="none",
                            )
                        if not bool((manual_result or {}).get("used", False)):
                            return _scenario_return(
                                browser.content(),
                                ready=False,
                                reason=manual_reason or "manual_intervention_unavailable",
                                scope_class="unknown",
                                route_bound=False,
                                route_support="none",
                            )
                    else:
                        step_trace = execute_turn_plan(
                            execute_plan_fn=execute_plan,
                            browser=browser,
                            plan=plan,
                            site_key=site_key,
                            blocked_selectors=blocked_selectors,
                            router=router,
                            evidence_dump_enabled=evidence_dump_enabled,
                            scenario_run_id=scenario_run_id,
                            url=url,
                            google_recovery_mode=bool(google_recovery_mode),
                            get_threshold_fn=get_threshold,
                            graph_stats=graph_stats,
                            attempt=attempt,
                            turn_idx=turn_idx,
                            mimic_locale=mimic_locale or "",
                            origin=origin or "",
                            dest=dest or "",
                            depart=depart or "",
                            return_date=return_date or "",
                            is_results_ready_fn=_is_results_ready,
                            google_quick_page_class_fn=_google_quick_page_class,
                        )
                    _write_json_artifact_snapshot(
                        scenario_run_id,
                        f"trace/step_trace_attempt_{attempt + 1}_turn_{turn_idx + 1}.json",
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "site_key": site_key,
                            "attempt": attempt + 1,
                            "turn": turn_idx + 1,
                            "step_trace": list(step_trace) if isinstance(step_trace, list) else [],
                        },
                    )
                    _capture_phase_probe(
                        stage="turn_post_execute",
                        attempt_idx=attempt + 1,
                        turn_idx=turn_idx + 1,
                        step_trace_payload=step_trace if isinstance(step_trace, list) else [],
                        extra={"mode": human_intervention_mode},
                        snapshot_html=True,
                    )
                    trace_hints_probe = _step_trace_phase_hints(step_trace if isinstance(step_trace, list) else [])
                    if bool(trace_hints_probe.get("search_action_detected", False)):
                        _capture_phase_probe(
                            stage="post_search_action",
                            attempt_idx=attempt + 1,
                            turn_idx=turn_idx + 1,
                            step_trace_payload=step_trace if isinstance(step_trace, list) else [],
                            extra={
                                "search_action_detected": True,
                                "results_wait_detected": bool(
                                    trace_hints_probe.get("results_wait_detected", False)
                                ),
                            },
                            snapshot_html=True,
                        )
                    if hasattr(browser, "collect_runtime_diagnostics"):
                        try:
                            trace_selectors = []
                            if isinstance(step_trace, list):
                                for item in step_trace[:20]:
                                    if not isinstance(item, dict):
                                        continue
                                    sel = str(item.get("selector", "") or "").strip()
                                    if sel:
                                        trace_selectors.append(sel)
                            _write_json_artifact_snapshot(
                                scenario_run_id,
                                f"dom_probe/turn_attempt_{attempt + 1}_turn_{turn_idx + 1}.json",
                                {
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "site_key": site_key,
                                    "attempt": attempt + 1,
                                    "turn": turn_idx + 1,
                                    "diag": browser.collect_runtime_diagnostics(selectors=trace_selectors[:12]),
                                },
                            )
                        except Exception:
                            pass
                    trace_analysis = analyze_turn_trace(
                        step_trace=step_trace,
                        browser=browser,
                        last_html=last_html,
                        site_key=site_key,
                        attempt=attempt,
                        turn_idx=turn_idx,
                        planner_notes=planner_notes,
                        merge_planner_notes_fn=_merge_planner_notes,
                        step_trace_memory_hint_fn=_step_trace_memory_hint,
                        google_step_trace_local_date_open_failure_fn=_google_step_trace_local_date_open_failure,
                        google_should_suppress_force_bind_after_date_failure_fn=_google_should_suppress_force_bind_after_date_failure,
                        debug_exploration_mode_fn=_debug_exploration_mode,
                        selector_candidates_fn=_selector_candidates,
                        prioritize_tokens_fn=prioritize_tokens,
                        get_tokens_fn=get_tokens,
                        current_mimic_locale_fn=_current_mimic_locale,
                        logger=log,
                    )
                    last_html = str(trace_analysis.get("last_html", "") or last_html)
                    planner_notes = trace_analysis.get("planner_notes", planner_notes)
                    trace_memory_hint = str(trace_analysis.get("trace_memory_hint", "") or "")
                    route_fill_mismatch_events = trace_analysis.get("route_fill_mismatch_events", [])
                    google_recovery_collab_followup = None
                    google_recovery_collab_followup_reason = ""
                    gf_date_failure_events = trace_analysis.get("gf_date_failure_events", [])
                    google_local_date_open_failure = trace_analysis.get(
                        "google_local_date_open_failure", {"matched": False, "reason": ""}
                    )
                    google_force_bind_suppression = trace_analysis.get(
                        "google_force_bind_suppression", {"use": False, "reason": ""}
                    )
                    debug_exploration_mode = str(
                        trace_analysis.get("debug_exploration_mode", "") or ""
                    )
                    super_deep_exploration = bool(
                        trace_analysis.get("super_deep_exploration", False)
                    )
                    google_trace_dest_selector = str(
                        trace_analysis.get("google_trace_dest_selector", "") or ""
                    )
                    google_trace_dest_committed = bool(
                        trace_analysis.get("google_trace_dest_committed", False)
                    )
                    google_trace_dest_commit_reason = str(
                        trace_analysis.get("google_trace_dest_commit_reason", "") or ""
                    )
                    google_trace_suggestion_used = bool(
                        trace_analysis.get("google_trace_suggestion_used", False)
                    )
                    google_trace_date_done_clicked = bool(
                        trace_analysis.get("google_trace_date_done_clicked", False)
                    )
                    google_trace_date_picker_seen = bool(
                        trace_analysis.get("google_trace_date_picker_seen", False)
                    )
                    _append_jsonl_artifact(
                        scenario_run_id,
                        "trace/graph_transitions.jsonl",
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "site_key": site_key,
                            "attempt": attempt + 1,
                            "turn": turn_idx + 1,
                            "stage": "turn_trace_analysis",
                            "trace_notes_count": len(list(planner_notes or [])),
                            "route_fill_mismatch_events": len(list(route_fill_mismatch_events or [])),
                            "gf_date_failure_events": len(list(gf_date_failure_events or [])),
                            "debug_exploration_mode": debug_exploration_mode,
                            "super_deep_exploration": bool(super_deep_exploration),
                            "dest_commit_reason": google_trace_dest_commit_reason,
                        },
                    )
                    mismatch_reset_enabled = bool(
                        get_threshold("google_flights_reset_on_route_mismatch_enabled", True)
                    )
                    mismatch_reset_max_attempts = int(
                        get_threshold("google_flights_reset_on_route_mismatch_max_attempts", 1)
                    )

                    # Skip mismatch_reset if date-specific failure detected
                    # Instead: try ONE bounded reload+retry or fail the scenario
                    skip_mismatch_reset_due_to_date_failure = (
                        bool(gf_date_failure_events)
                        and site_key == "google_flights"
                    )

                    if skip_mismatch_reset_due_to_date_failure:
                        date_failure = gf_date_failure_events[0] if gf_date_failure_events else {}
                        date_failure_reason = str(date_failure.get("status", "") or "unknown_date_failure")
                        date_failure_role = str(date_failure.get("role", "") or "unknown")

                        log.info(
                            "scenario.date_fill_failure.skip_mismatch_reset role=%s reason=%s evidence=%s",
                            date_failure_role,
                            date_failure_reason,
                            str(date_failure.get("evidence", {}))[:100],
                        )

                        if bool(google_force_bind_suppression.get("use")) and not super_deep_exploration:
                            log.info(
                                "scenario.date_fill_failure.deterministic_local_open_exit role=%s reason=%s suppression=%s",
                                date_failure_role,
                                date_failure_reason,
                                str(google_force_bind_suppression.get("reason", "") or ""),
                            )
                            return _scenario_return(
                                browser.content(),
                                ready=False,
                                reason=f"date_fill_failure_{date_failure_reason}",
                                scope_class="unknown",
                                route_bound=False,
                                route_support="none",
                            )
                        elif bool(google_force_bind_suppression.get("use")) and super_deep_exploration:
                            log.info(
                                "scenario.date_fill_failure.deterministic_local_open_exit_bypassed mode=%s role=%s reason=%s suppression=%s",
                                debug_exploration_mode,
                                date_failure_role,
                                date_failure_reason,
                                str(google_force_bind_suppression.get("reason", "") or ""),
                            )

                        if _site_should_attempt_recovery_collab_after_date_failure_dispatch(
                            site_key=site_key,
                            recovery_mode=bool(google_recovery_mode),
                            date_failure_reason=date_failure_reason,
                        ):
                            collab_plan, collab_notes = _try_google_recovery_collab_followup(
                                current_html=str(browser.content() or ""),
                                failed_plan=plan,
                                route_core_failure=date_failure if isinstance(date_failure, dict) else {},
                                turn_index=turn_idx + 1,
                            )
                            if _is_valid_plan(collab_plan):
                                google_recovery_collab_followup = collab_plan
                                google_recovery_collab_followup_reason = date_failure_reason
                                planner_notes = _merge_planner_notes(planner_notes, collab_notes)
                                log.info(
                                    "scenario.date_fill_failure.collab_followup_prepared role=%s reason=%s valid=True",
                                    date_failure_role,
                                    date_failure_reason,
                                )
                            else:
                                log.info(
                                    "scenario.date_fill_failure.collab_followup_prepared role=%s reason=%s valid=False",
                                    date_failure_role,
                                    date_failure_reason,
                                )

                        # Bounded reload+retry policy: try once if not already retried
                        date_reload_retry_max_attempts = int(
                            _env_int(
                                "FLIGHT_WATCHER_GOOGLE_FLIGHTS_DATE_RELOAD_RETRY_MAX_ATTEMPTS",
                                int(get_threshold("google_flights_date_reload_retry_max_attempts", 1)),
                            )
                        )

                        if _is_valid_plan(google_recovery_collab_followup):
                            log.info(
                                "scenario.date_fill_failure.reload_retry_skipped reason=collab_followup_available"
                            )
                        elif date_reload_retry_count < date_reload_retry_max_attempts:
                            date_reload_retry_count += 1
                            log.info(
                                "scenario.date_fill_failure.reload_retry attempt=%d/%d",
                                date_reload_retry_count,
                                date_reload_retry_max_attempts,
                            )
                            # Reload the deeplink and continue (will retry on next turn)
                            try:
                                reload_url = _normalize_google_deeplink_with_mimic(
                                    url=url,
                                    origin=origin,
                                    dest=dest,
                                    depart=depart,
                                    return_date=return_date or "",
                                    trip_type=trip_type,
                                    mimic_locale=mimic_locale or "",
                                    mimic_region=mimic_region or "",
                                    mimic_currency=mimic_currency or "",
                                )
                                browser.goto(reload_url)
                                url = reload_url
                                time.sleep(0.5)
                                log.info(
                                    "scenario.date_fill_failure.reload_success url=%s",
                                    reload_url,
                                )
                            except Exception as exc:
                                log.warning("scenario.date_fill_failure.reload_failed error=%s", str(exc)[:100])
                        else:
                            log.warning(
                                "scenario.date_fill_failure.exit reason=%s role=%s max_retries_exhausted=%d",
                                date_failure_reason,
                                date_failure_role,
                                date_reload_retry_max_attempts,
                            )
                            return _scenario_return(
                                browser.content(),
                                ready=False,
                                reason=f"date_fill_failure_{date_failure_reason}",
                                scope_class="unknown",
                                route_bound=False,
                                route_support="none",
                            )
                    elif _should_attempt_google_route_mismatch_reset(
                        mismatch_detected=bool(route_fill_mismatch_events)
                        and site_key == "google_flights"
                        and "flt=" in (url or ""),
                        enabled=mismatch_reset_enabled,
                        attempts=route_mismatch_reset_attempts,
                        max_attempts=mismatch_reset_max_attempts,
                    ):
                        route_mismatch_reset_attempts += 1
                        reset_url = _normalize_google_deeplink_with_mimic(
                            url=url,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date or "",
                            trip_type=trip_type,
                            mimic_locale=mimic_locale or "",
                            mimic_region=mimic_region or "",
                            mimic_currency=mimic_currency or "",
                        )
                        reset_ok = _run_google_route_mismatch_reset(
                            browser,
                            deeplink_url=reset_url,
                            wait_selectors=_service_wait_fallbacks("google_flights"),
                        )
                        url = reset_url
                        log.info(
                            "scenario.route_mismatch_reset attempted=%s ok=%s attempt_index=%s/%s",
                            True,
                            reset_ok,
                            route_mismatch_reset_attempts,
                            max(0, mismatch_reset_max_attempts),
                        )
                        if reset_ok:
                            route_fill_mismatch_events = []
                    final_html = ""
                    scope_sources: List[str] = []
                    try:
                        final_html = browser.content()
                    except Exception as final_html_exc:
                        manual_status = ""
                        if human_intervention_mode == "demo" and isinstance(step_trace, list) and step_trace:
                            first_step = step_trace[0] if isinstance(step_trace[0], dict) else {}
                            manual_status = str(first_step.get("status", "") or "")
                            manual_payload = (
                                first_step.get("manual_intervention", {})
                                if isinstance(first_step.get("manual_intervention", {}), dict)
                                else {}
                            )
                            manual_automation_count = int(
                                (
                                    (manual_payload.get("automation_activity_during_manual", {}) or {})
                                    .get("count", 0)
                                )
                                or 0
                            )
                        else:
                            manual_payload = {}
                            manual_automation_count = 0
                        if human_intervention_mode == "demo":
                            ui_capture_payload = (
                                manual_payload.get("ui_action_capture", {})
                                if isinstance(manual_payload.get("ui_action_capture", {}), dict)
                                else {}
                            )
                            ui_event_count = int(ui_capture_payload.get("event_count", 0) or 0)
                            ui_direct_count = int(ui_capture_payload.get("direct_event_count", 0) or 0)
                            ui_signal_quality = str(ui_capture_payload.get("signal_quality", "") or "")
                            log.warning(
                                "scenario.demo.final_html_unavailable reason=%s error=%s ui_event_count=%s ui_direct_count=%s ui_signal_quality=%s",
                                manual_status,
                                str(type(final_html_exc).__name__),
                                ui_event_count,
                                ui_direct_count,
                                ui_signal_quality,
                            )
                            if manual_status == "manual_observation_complete_target_closed":
                                demo_reason = "demo_mode_manual_observation_complete_target_closed"
                            elif manual_status == "manual_intervention_reissue_suspected_target_closed":
                                demo_reason = "demo_mode_manual_reissue_suspected_target_closed"
                            elif manual_status == "manual_intervention_target_closed":
                                demo_reason = "demo_mode_manual_target_closed"
                            else:
                                demo_reason = "demo_mode_final_html_unavailable"
                            return _scenario_return(
                                str(last_html or ""),
                                ready=False,
                                reason=demo_reason,
                                scope_class="unknown",
                                route_bound=False,
                                route_support="none",
                            )
                        raise
                    _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                    _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)
                    _capture_phase_probe(
                        stage="results_wait_start",
                        attempt_idx=attempt + 1,
                        turn_idx=turn_idx + 1,
                        step_trace_payload=step_trace if isinstance(step_trace, list) else [],
                        extra={"final_html_len": len(str(final_html or ""))},
                        snapshot_html=False,
                    )
                    ready = _is_results_ready(
                        final_html,
                        site_key=site_key,
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        page_url=str(getattr(getattr(browser, "page", None), "url", "") or ""),
                    )
                    skyscanner_suppress_turn_followup = False
                    skyscanner_home_rebind_followup_required = False
                    skyscanner_followup_turn_available = (turn_idx + 1) < max_turns
                    if site_key == "skyscanner" and not bool(ready):
                        current_page_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                        if _is_skyscanner_results_shell_incomplete(final_html, page_url=current_page_url):
                            settle_ms = max(
                                0,
                                int(
                                    _threshold_site_value(
                                        "scenario_skyscanner_blank_shell_settle_ms",
                                        site_key,
                                        9000,
                                    )
                                ),
                            )
                            reload_timeout_ms = max(
                                2000,
                                int(
                                    _threshold_site_value(
                                        "scenario_skyscanner_blank_shell_reload_timeout_ms",
                                        site_key,
                                        35000,
                                    )
                                ),
                            )
                            log.warning(
                                "scenario.skyscanner.blank_shell_detected url=%s html_len=%s settle_ms=%s",
                                current_page_url,
                                len(str(final_html or "")),
                                settle_ms,
                            )
                            if settle_ms > 0:
                                try:
                                    browser.page.wait_for_timeout(settle_ms)
                                except Exception:
                                    pass
                            try:
                                refreshed_html = str(browser.content() or "")
                            except Exception:
                                refreshed_html = ""
                            if _is_skyscanner_results_shell_incomplete(
                                refreshed_html or final_html,
                                page_url=str(getattr(getattr(browser, "page", None), "url", "") or ""),
                            ):
                                try:
                                    browser.page.reload(wait_until="domcontentloaded", timeout=reload_timeout_ms)
                                    browser.page.wait_for_timeout(1200)
                                except Exception as reload_exc:
                                    log.warning(
                                        "scenario.skyscanner.blank_shell_reload_failed error=%s",
                                        reload_exc,
                                    )
                            try:
                                recovered_html = str(browser.content() or "")
                            except Exception:
                                recovered_html = ""
                            post_reload_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                            if _is_skyscanner_results_shell_incomplete(
                                recovered_html or final_html,
                                page_url=post_reload_url,
                            ):
                                hard_nav_timeout_ms = max(
                                    3000,
                                    int(
                                        _threshold_site_value(
                                            "scenario_skyscanner_blank_shell_hard_nav_timeout_ms",
                                            site_key,
                                            30000,
                                        )
                                    ),
                                )
                                hard_nav_settle_ms = max(
                                    0,
                                    int(
                                        _threshold_site_value(
                                            "scenario_skyscanner_blank_shell_hard_nav_settle_ms",
                                            site_key,
                                            2000,
                                        )
                                    ),
                                )
                                if "/transport/flights/" in post_reload_url:
                                    log.warning(
                                        "scenario.skyscanner.blank_shell_hard_nav attempt=%s turn=%s timeout_ms=%s url=%s",
                                        attempt + 1,
                                        turn_idx + 1,
                                        hard_nav_timeout_ms,
                                        post_reload_url[:240],
                                    )
                                    try:
                                        browser.page.goto(
                                            post_reload_url,
                                            wait_until="domcontentloaded",
                                            timeout=hard_nav_timeout_ms,
                                        )
                                        if hard_nav_settle_ms > 0:
                                            browser.page.wait_for_timeout(hard_nav_settle_ms)
                                    except Exception as hard_nav_exc:
                                        log.warning(
                                            "scenario.skyscanner.blank_shell_hard_nav_failed error=%s",
                                            hard_nav_exc,
                                        )
                            try:
                                recovered_html = str(browser.content() or "")
                            except Exception:
                                recovered_html = ""
                            post_hard_nav_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                            blank_shell_still = _is_skyscanner_results_shell_incomplete(
                                recovered_html or final_html,
                                page_url=post_hard_nav_url,
                            )
                            if blank_shell_still and "/transport/flights/" in post_hard_nav_url:
                                # Give results shell a bounded chance to hydrate after hard-nav
                                # before switching back to home form rebind.
                                hydration_wait_ms = max(
                                    0,
                                    min(
                                        15_000,
                                        max(int(hard_nav_settle_ms or 0), int(settle_ms or 0)),
                                    ),
                                )
                                hydration_interval_ms = 1_000
                                hydration_checks = max(
                                    0,
                                    min(15, hydration_wait_ms // hydration_interval_ms),
                                )
                                for _ in range(hydration_checks):
                                    try:
                                        browser.page.wait_for_timeout(hydration_interval_ms)
                                    except Exception:
                                        break
                                    try:
                                        hydration_html = str(browser.content() or "")
                                    except Exception:
                                        hydration_html = ""
                                    hydration_url = str(
                                        getattr(getattr(browser, "page", None), "url", "") or ""
                                    )
                                    if not _is_skyscanner_results_shell_incomplete(
                                        hydration_html or recovered_html or final_html,
                                        page_url=hydration_url,
                                    ):
                                        recovered_html = hydration_html or recovered_html or final_html
                                        post_hard_nav_url = hydration_url
                                        blank_shell_still = False
                                        log.info(
                                            "scenario.skyscanner.blank_shell_hydration_recovered attempt=%s turn=%s wait_ms=%s",
                                            attempt + 1,
                                            turn_idx + 1,
                                            hydration_wait_ms,
                                        )
                                        break
                            if blank_shell_still and "/transport/flights/" in post_hard_nav_url:
                                # Allow one additional bounded PX-shadow recovery within the same
                                # run so repeated white-shell loops can still escalate before exit.
                                max_px_recovery_uses = 2
                                if skyscanner_blank_shell_px_recovery_uses < max_px_recovery_uses:
                                    shadow_probe = _probe_skyscanner_shadow_challenge_state(browser)
                                    if bool((shadow_probe or {}).get("suspected", False)):
                                        skyscanner_blank_shell_px_recovery_uses += 1
                                        recovery_grace_ms = max(
                                            18_000,
                                            int(
                                                _threshold_site_value(
                                                    "skyscanner_blocked_interstitial_grace_fallback_ms",
                                                    site_key,
                                                    22_000,
                                                )
                                            ),
                                        )
                                        px_manual_escalation = bool(not skyscanner_followup_turn_available)
                                        px_reload_attempts = 2 if px_manual_escalation else 1
                                        def _blank_shell_cleared(_html_text: str, _url_text: str) -> bool:
                                            return not _is_skyscanner_results_shell_incomplete(
                                                str(_html_text or ""),
                                                page_url=str(_url_text or ""),
                                            )
                                        px_recovery = _attempt_skyscanner_interstitial_fallback_reload(
                                            browser,
                                            post_hard_nav_url,
                                            grace_result={"used": True, "cleared": False, "reason": "blank_shell_shadow_challenge"},
                                            human_mimic=bool(human_mimic),
                                            grace_ms_extended=recovery_grace_ms,
                                            max_reload_attempts=px_reload_attempts,
                                            allow_manual_escalation=px_manual_escalation,
                                            success_html_predicate=_blank_shell_cleared,
                                        )
                                        log.warning(
                                            "scenario.skyscanner.blank_shell_px_recovery used=%s suspected=%s reason=%s cleared=%s px_sig=%s failed_ch_hosts=%s count=%s/%s manual_escalation=%s reload_attempts=%s",
                                            bool((px_recovery or {}).get("used", False)),
                                            bool((shadow_probe or {}).get("suspected", False)),
                                            str((shadow_probe or {}).get("reason", "") or ""),
                                            bool((px_recovery or {}).get("cleared", False)),
                                            str((shadow_probe or {}).get("px_signature_prefix", "") or ""),
                                            int((shadow_probe or {}).get("failed_challenge_hosts", 0) or 0),
                                            skyscanner_blank_shell_px_recovery_uses,
                                            max_px_recovery_uses,
                                            bool(px_manual_escalation),
                                            int(px_reload_attempts),
                                        )
                                        recovered_html = str((px_recovery or {}).get("html", "") or recovered_html or "")
                                        post_hard_nav_url = str(
                                            getattr(getattr(browser, "page", None), "url", "") or post_hard_nav_url
                                        )
                                        blank_shell_still = _is_skyscanner_results_shell_incomplete(
                                            recovered_html or final_html,
                                            page_url=post_hard_nav_url,
                                        )
                            if (
                                blank_shell_still
                                and "/transport/flights/" in post_hard_nav_url
                                and skyscanner_followup_turn_available
                            ):
                                if skyscanner_post_clear_refill_executed:
                                    skyscanner_suppress_turn_followup = True
                                    log.warning(
                                        "scenario.skyscanner.blank_shell_rebind_home_suppressed reason=post_clear_refill_already_executed attempt=%s turn=%s/%s",
                                        attempt + 1,
                                        turn_idx + 1,
                                        max_turns,
                                    )
                                else:
                                    rebind_home_url = str(url or "").strip() or "https://www.skyscanner.com/flights"
                                    home_rebind_ok = False
                                    home_rebind_reason = "rebind_not_attempted"
                                    home_rebind_error = ""
                                    home_rebind_interstitial = False
                                    home_rebind_form_visible = False
                                    home_html = ""
                                    try:
                                        browser.page.goto(
                                            rebind_home_url,
                                            wait_until="domcontentloaded",
                                            timeout=hard_nav_timeout_ms,
                                        )
                                        if hard_nav_settle_ms > 0:
                                            browser.page.wait_for_timeout(min(2500, max(800, hard_nav_settle_ms)))
                                        home_html = str(browser.content() or "")
                                        home_block = _detect_skyscanner_interstitial_block(home_html) or {}
                                        home_rebind_interstitial = bool(home_block)
                                        if not home_rebind_interstitial:
                                            try:
                                                home_rebind_form_visible = bool(
                                                    browser.page.evaluate(
                                                        """
                                                        () => {
                                                          const isVisible = (el) => {
                                                            if (!el) return false;
                                                            const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                                                            if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                                                            const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                                                            return !!r && r.width > 0 && r.height > 0;
                                                          };
                                                          const origin = document.querySelector("#originInput-input, input[name='originInput-search']");
                                                          const dest = document.querySelector("#destinationInput-input, input[name='destinationInput-search']");
                                                          return isVisible(origin) && isVisible(dest);
                                                        }
                                                        """
                                                    )
                                                )
                                            except Exception:
                                                home_rebind_form_visible = False
                                        home_rebind_ok = bool(home_html) and (not home_rebind_interstitial) and home_rebind_form_visible
                                        if home_rebind_ok:
                                            blank_shell_still = False
                                            recovered_html = home_html
                                            home_rebind_reason = "rebind_home_form_visible"
                                            skyscanner_home_rebind_followup_required = True
                                            scope_sources.append("ready_guard:blank_shell_rebind_home")
                                        else:
                                            home_rebind_reason = (
                                                "rebind_home_interstitial"
                                                if home_rebind_interstitial
                                                else "rebind_home_form_missing"
                                            )
                                    except Exception as home_rebind_exc:
                                        home_rebind_reason = "rebind_home_exception"
                                        home_rebind_error = str(type(home_rebind_exc).__name__)
                                    log.warning(
                                        "scenario.skyscanner.blank_shell_rebind_home ok=%s reason=%s interstitial=%s form_visible=%s target=%s error=%s",
                                        bool(home_rebind_ok),
                                        home_rebind_reason,
                                        bool(home_rebind_interstitial),
                                        bool(home_rebind_form_visible),
                                        rebind_home_url[:180],
                                        home_rebind_error,
                                    )
                            elif blank_shell_still and "/transport/flights/" in post_hard_nav_url:
                                log.info(
                                    "scenario.skyscanner.blank_shell_rebind_home_skipped_no_turn_budget attempt=%s turn=%s/%s",
                                    attempt + 1,
                                    turn_idx + 1,
                                    max_turns,
                                )
                            max_manual_uses = max(
                                0,
                                int(
                                    _threshold_site_value(
                                        "scenario_skyscanner_blank_shell_manual_recovery_max_uses",
                                        site_key,
                                        1,
                                    )
                                ),
                            )
                            manual_wait_sec = max(
                                20,
                                int(
                                    _threshold_site_value(
                                        "scenario_skyscanner_blank_shell_manual_wait_sec",
                                        site_key,
                                        45,
                                    )
                                ),
                            )
                            if (
                                blank_shell_still
                                and skyscanner_blank_shell_manual_recovery_uses < max_manual_uses
                                and hasattr(browser, "allow_manual_verification_intervention")
                            ):
                                skyscanner_blank_shell_manual_recovery_uses += 1
                                manual_result = browser.allow_manual_verification_intervention(
                                    reason="skyscanner_blank_shell_recovery",
                                    wait_sec=manual_wait_sec,
                                    force=True,
                                    mode_override="assist",
                                )
                                log.warning(
                                    "scenario.skyscanner.blank_shell_manual_recovery used=%s reason=%s elapsed_ms=%s count=%s/%s",
                                    bool((manual_result or {}).get("used", False)),
                                    str((manual_result or {}).get("reason", "") or ""),
                                    int((manual_result or {}).get("elapsed_ms", 0) or 0),
                                    skyscanner_blank_shell_manual_recovery_uses,
                                    max_manual_uses,
                                )
                                try:
                                    browser.page.wait_for_timeout(1200)
                                except Exception:
                                    pass
                                try:
                                    recovered_html = str(browser.content() or "")
                                except Exception:
                                    recovered_html = ""
                            if recovered_html:
                                final_html = recovered_html
                                _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                                _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)
                            ready = _is_results_ready(
                                final_html,
                                site_key=site_key,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                page_url=str(getattr(getattr(browser, "page", None), "url", "") or ""),
                            )
                    _capture_phase_probe(
                        stage="results_wait_end",
                        attempt_idx=attempt + 1,
                        turn_idx=turn_idx + 1,
                        step_trace_payload=step_trace if isinstance(step_trace, list) else [],
                        extra={"ready": bool(ready)},
                        snapshot_html=False,
                    )
                    if (
                        site_key == "skyscanner"
                        and bool(skyscanner_suppress_turn_followup)
                        and not bool(ready)
                    ):
                        loop_guard_reason = "skyscanner_blank_shell_persistent_after_post_clear_refill"
                        log.warning(
                            "scenario.skyscanner.turn_followup_suppressed reason=%s attempt=%s turn=%s/%s",
                            loop_guard_reason,
                            attempt + 1,
                            turn_idx + 1,
                            max_turns,
                        )
                        if (attempt + 1) < max_retries:
                            raise RuntimeError(
                                f"results_not_ready_after_turn_limit attempt={attempt + 1} turns={max_turns}"
                            )
                        return _scenario_return(
                            final_html,
                            ready=False,
                            reason=loop_guard_reason,
                            scope_class="unknown",
                            route_bound=False,
                            route_support="none",
                        )
                    if (
                        site_key == "skyscanner"
                        and skyscanner_home_rebind_followup_required
                        and human_intervention_mode != "demo"
                    ):
                        if skyscanner_followup_turn_available:
                            followup = _default_plan_for_service(
                                site_key,
                                origin,
                                dest,
                                depart,
                                return_date=return_date,
                                is_domestic=is_domestic,
                                knowledge=knowledge if isinstance(knowledge, dict) else {},
                            )
                            plan = _retarget_plan_inputs(
                                plan=followup,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                trip_type=trip_type,
                                site_key=site_key,
                            )
                            plan = _with_knowledge(
                                plan,
                                site_key,
                                is_domestic,
                                knowledge,
                                vlm_hint=vlm_ui_hint,
                            )
                            initial_html = final_html
                            log.info(
                                "scenario.skyscanner.blank_shell_rebind_followup_reset attempt=%s turn=%s/%s",
                                attempt + 1,
                                turn_idx + 1,
                                max_turns,
                            )
                            continue
                    if human_intervention_mode == "demo":
                        return _scenario_return(
                            final_html,
                            ready=bool(ready),
                            reason="demo_mode_observation_complete",
                            scope_class="unknown",
                            route_bound=False,
                            route_support="none",
                        )
                    route_bound = None
                    verify_available = False
                    verify_status = "not_attempted"
                    verify_override_reason = ""
                    scope_page_class = "unknown"
                    scope_trip_product = "unknown"
                    if not isinstance(scope_sources, list):
                        scope_sources = []
                    scope_repair_requested = False
                    scope_vlm_hint = {}
                    quick_scope = "unknown"
                    vlm_scope_class = "unknown"
                    llm_scope_class = "unknown"
                    dom_probe_observed = {}
                    route_fill_mismatch_meta = {}
                    observed_origin_raw = ""
                    observed_dest_raw = ""
                    dest_is_placeholder = False
                    vlm_verify_fields = {}
                    plugin_probe_page_class = "unknown"
                    if route_fill_mismatch_events and site_key == "google_flights":
                        mismatch_event = route_fill_mismatch_events[0]
                        mismatch_meta = mismatch_event.get("route_verify", {})
                        route_fill_mismatch_meta = (
                            dict(mismatch_meta) if isinstance(mismatch_meta, dict) else {}
                        )
                        if isinstance(mismatch_meta, dict):
                            dom_probe_observed = dict(
                                mismatch_meta.get("observed", {}) or {}
                            )
                            observed_origin_raw = str(
                                mismatch_meta.get("observed_raw", {}).get(
                                    "origin",
                                    dom_probe_observed.get("origin", ""),
                                )
                                or ""
                            )
                            observed_dest_raw = str(
                                mismatch_meta.get("observed_raw", {}).get(
                                    "dest",
                                    dom_probe_observed.get("dest", ""),
                                )
                                or ""
                            )
                            dest_is_placeholder = bool(
                                mismatch_meta.get("dest_is_placeholder")
                            ) or _is_google_dest_placeholder(observed_dest_raw)
                            google_trace_dest_committed = bool(
                                mismatch_meta.get(
                                    "dest_committed",
                                    google_trace_dest_committed,
                                )
                            )
                            google_trace_dest_commit_reason = str(
                                mismatch_meta.get(
                                    "dest_commit_reason",
                                    google_trace_dest_commit_reason,
                                )
                                or google_trace_dest_commit_reason
                            )
                            google_trace_suggestion_used = bool(
                                mismatch_meta.get(
                                    "suggestion_used",
                                    google_trace_suggestion_used,
                                )
                            )
                        ready = False
                        route_bound = False
                        verify_status = "route_fill_mismatch"
                        verify_override_reason = "route_fill_mismatch"
                        scope_repair_requested = True
                        scope_sources.append("verify:route_fill_mismatch")
                        log.warning(
                            "scenario.turn.ready_overridden site=%s reason=route_fill_mismatch expected_origin=%s expected_dest=%s expected_depart=%s observed_origin=%s observed_dest=%s observed_depart=%s observed_return=%s confidence=%s mismatch_fields=%s",
                            site_key,
                            mismatch_meta.get("expected", {}).get("origin", ""),
                            mismatch_meta.get("expected", {}).get("dest", ""),
                            mismatch_meta.get("expected", {}).get("depart", ""),
                            mismatch_meta.get("observed", {}).get("origin", ""),
                            mismatch_meta.get("observed", {}).get("dest", ""),
                            mismatch_meta.get("observed", {}).get("depart", ""),
                            mismatch_meta.get("observed", {}).get("return", ""),
                            mismatch_meta.get("confidence", ""),
                            ",".join(mismatch_meta.get("mismatches", []) or []),
                        )
                    if bool(get_threshold("scenario_use_plugin_readiness_probe", False)):
                        plugin_probe = run_service_readiness_probe(
                            site_key,
                            html=final_html,
                            screenshot_path=str(_snapshot_image_path(site_key, "last", run_id=scenario_run_id)),
                            inputs={
                                "site": site_key,
                                "origin": origin or "",
                                "dest": dest or "",
                                "depart": depart or "",
                                "return_date": return_date or "",
                                "trip_type": trip_type,
                                "is_domestic": is_domestic,
                            },
                        )
                        probe_out = _apply_plugin_readiness_probe(
                            ready=ready,
                            route_bound=route_bound,
                            verify_status=verify_status,
                            verify_override_reason=verify_override_reason,
                            scope_page_class=scope_page_class,
                            scope_trip_product=scope_trip_product,
                            scope_sources=scope_sources,
                            plugin_probe=plugin_probe,
                        )
                        if probe_out.get("used"):
                            ready = bool(probe_out.get("ready", ready))
                            route_bound = probe_out.get("route_bound")
                            verify_status = str(
                                probe_out.get("verify_status", verify_status) or verify_status
                            )
                            verify_override_reason = str(
                                probe_out.get(
                                    "verify_override_reason",
                                    verify_override_reason,
                                )
                                or verify_override_reason
                            )
                            scope_page_class = str(
                                probe_out.get("scope_page_class", scope_page_class)
                                or scope_page_class
                            )
                            scope_trip_product = str(
                                probe_out.get("scope_trip_product", scope_trip_product)
                                or scope_trip_product
                            )
                            scope_sources = list(probe_out.get("scope_sources", scope_sources))
                            plugin_probe_page_class = str(
                                probe_out.get("probe_page_class", "unknown") or "unknown"
                            )
                            log.info(
                                "scenario.plugin_readiness_probe site=%s ready=%s class=%s product=%s reason=%s",
                                site_key,
                                ready,
                                scope_page_class,
                                scope_trip_product,
                                verify_override_reason,
                            )
                    if (
                        ready
                        and site_key == "google_flights"
                        and not (
                            google_recovery_mode
                            and bool(
                                get_threshold(
                                    "scenario_vlm_fill_verify_skip_in_recovery_mode",
                                    True,
                                )
                            )
                        )
                    ):
                        deterministic_state = _extract_google_flights_form_state(browser.page)
                        deterministic_confidence = str(
                            deterministic_state.get("confidence", "low") or "low"
                        ).strip().lower()
                        deterministic_reason = str(
                            deterministic_state.get("reason", "dom_probe_unavailable") or "dom_probe_unavailable"
                        )
                        deterministic_assess = _assess_google_flights_fill_mismatch(
                            form_state=deterministic_state,
                            expected_origin=origin or "",
                            expected_dest=dest or "",
                            expected_depart=depart or "",
                            expected_return=return_date or "",
                            min_confidence=verify_after_fill_min_confidence,
                            fail_closed=False,
                        )
                        deterministic_available = bool(
                            deterministic_state
                            and deterministic_reason not in {"dom_probe_unavailable", "no_candidates"}
                        )
                        legacy_verify_enabled = bool(get_threshold("scenario_vlm_fill_verify_enabled", True))
                        should_run_stage_b = _should_run_vision_post_fill_verify(
                            enabled=bool(get_threshold("scenario_vision_post_fill_verify_enabled", True)),
                            deterministic_reason=str(
                                deterministic_assess.get("reason", deterministic_reason) or deterministic_reason
                            ),
                            deterministic_confidence=deterministic_confidence,
                            min_confidence=verify_after_fill_min_confidence,
                            commit_reason=google_trace_dest_commit_reason,
                            deterministic_available=deterministic_available,
                            legacy_verify_enabled=legacy_verify_enabled,
                        )
                        if should_run_stage_b:
                            verify_status = "requested"
                            verify_timeout_sec = int(
                                get_threshold(
                                    "scenario_vlm_fill_verify_timeout_sec",
                                    240,
                                )
                            )
                            remaining = _budget_remaining_sec()
                            if remaining is not None:
                                available = int(remaining - float(scenario_budget_soft_margin_sec))
                                if available <= 3:
                                    verify_status = "skipped_budget"
                                    verify_timeout_sec = 0
                                else:
                                    verify_timeout_sec = min(verify_timeout_sec, available)
                            screenshot_last = _snapshot_image_path(site_key, "last", run_id=scenario_run_id)
                            if verify_timeout_sec <= 0 or not screenshot_last.exists():
                                verify_raw = {}
                                meta = {
                                    "cached": False,
                                    "cooldown_skip": False,
                                }
                            else:
                                verify_raw, meta = _vision_cached_stage_call(
                                    cache=vision_stage_cache,
                                    cooldown=vision_stage_cooldown,
                                    stage="fill_verify",
                                    screenshot_path=str(screenshot_last),
                                    runner=lambda: analyze_filled_route_with_vlm(
                                        str(screenshot_last),
                                        site=site_key,
                                        origin=origin or "",
                                        dest=dest or "",
                                        depart=depart or "",
                                        return_date=return_date or "",
                                        trip_type=trip_type,
                                        html_context=final_html,
                                        locale=mimic_locale or "",
                                        timeout_sec=verify_timeout_sec,
                                    ),
                                )
                            vision_verify = _normalize_vision_fill_verify_result(verify_raw)
                            log.info(
                                "vision.fill_verify %s",
                                {
                                    "site": site_key,
                                    "cached": bool(meta.get("cached", False)),
                                    "cooldown_skip": bool(meta.get("cooldown_skip", False)),
                                    "confidence": vision_verify.get("confidence", "low"),
                                    "reason": vision_verify.get("reason", ""),
                                    "mismatch_fields": list(vision_verify.get("mismatch_fields", []) or []),
                                    "suggested_fix": dict(vision_verify.get("suggested_fix", {}) or {}),
                                },
                            )
                            if isinstance(verify_raw, dict) and isinstance(verify_raw.get("fields"), dict):
                                vlm_verify_fields = dict(verify_raw.get("fields") or {})
                            mismatch_fields = list(vision_verify.get("mismatch_fields", []) or [])
                            if origin and not _google_form_value_matches_airport(
                                vision_verify.get("origin_text", ""),
                                origin,
                            ):
                                if "origin" not in mismatch_fields:
                                    mismatch_fields.append("origin")
                            if dest and not _google_form_value_matches_airport(
                                vision_verify.get("dest_text", ""),
                                dest,
                            ):
                                if "dest" not in mismatch_fields:
                                    mismatch_fields.append("dest")
                            if depart and not _google_form_value_matches_date(
                                vision_verify.get("depart_text", ""),
                                depart,
                            ):
                                if "depart" not in mismatch_fields:
                                    mismatch_fields.append("depart")
                            if return_date and not _google_form_value_matches_date(
                                vision_verify.get("return_text", ""),
                                return_date,
                            ):
                                if "return" not in mismatch_fields:
                                    mismatch_fields.append("return")
                            confidence_ok = _verification_confidence_rank(
                                str(vision_verify.get("confidence", "low") or "low")
                            ) >= _verification_confidence_rank("medium")
                            if mismatch_fields and confidence_ok:
                                ready = False
                                route_bound = False
                                verify_status = "route_fill_mismatch"
                                verify_override_reason = "vision_fill_mismatch"
                                scope_repair_requested = True
                                suggested_fix = dict(vision_verify.get("suggested_fix", {}) or {})
                                fix_field = str(suggested_fix.get("field", "none") or "none").strip().lower()
                                if fix_field in {"origin", "dest", "depart", "return"}:
                                    scope_sources.append(f"vision_fix:{fix_field}")
                                route_fill_mismatch_meta = {
                                    "expected": {
                                        "origin": origin or "",
                                        "dest": dest or "",
                                        "depart": depart or "",
                                        "return": return_date or "",
                                    },
                                    "observed": {
                                        "origin": vision_verify.get("origin_text", ""),
                                        "dest": vision_verify.get("dest_text", ""),
                                        "depart": vision_verify.get("depart_text", ""),
                                        "return": vision_verify.get("return_text", ""),
                                    },
                                    "observed_raw": {
                                        "origin": vision_verify.get("origin_text", ""),
                                        "dest": vision_verify.get("dest_text", ""),
                                        "depart": vision_verify.get("depart_text", ""),
                                        "return": vision_verify.get("return_text", ""),
                                    },
                                    "confidence": vision_verify.get("confidence", "low"),
                                    "reason": vision_verify.get("reason", ""),
                                    "mismatches": mismatch_fields,
                                }
                                dom_probe_observed = dict(route_fill_mismatch_meta.get("observed", {}) or {})
                                observed_origin_raw = str(vision_verify.get("origin_text", "") or "")
                                observed_dest_raw = str(vision_verify.get("dest_text", "") or "")
                            else:
                                verify_available = bool(verify_raw)
                                if verify_available:
                                    route_bound = bool(not mismatch_fields)
                                    verify_status = "bound" if route_bound else "unbound"
                                elif verify_status == "requested":
                                    verify_status = "unavailable_empty"
                            if (
                                not verify_available
                                and verify_status != "skipped_budget"
                                and bool(get_threshold("scenario_vlm_fill_verify_fail_closed", False))
                            ):
                                ready = False
                                verify_override_reason = "vlm_route_verify_unavailable"
                                verify_status = f"{verify_status}_fail_closed"
                                log.warning(
                                    "scenario.turn.ready_overridden site=%s reason=vlm_route_verify_unavailable",
                                    site_key,
                                )

                    if (
                        site_key == "google_flights"
                        and (not dom_probe_observed)
                        and isinstance(route_fill_mismatch_meta, dict)
                        and route_fill_mismatch_meta.get("observed")
                    ):
                        dom_probe_observed = dict(
                            route_fill_mismatch_meta.get("observed") or {}
                        )

                    if (
                        site_key == "google_flights"
                        and bool(ready)
                        and (route_bound is None or route_bound is False)
                        and str(verify_status or "").strip().lower() in {"not_attempted", "not_verified", "unavailable_empty"}
                    ):
                        fill_corroboration = _google_turn_fill_success_corroborates_route_bind(step_trace)
                        if bool(fill_corroboration.get("ok")):
                            route_bound = True
                            if str(verify_status or "").strip().lower() in {"not_attempted", "not_verified"}:
                                verify_status = "bound_local_fill_corroborated"
                            if not str(verify_override_reason or "").strip():
                                verify_override_reason = "route_bind_corroborated_local_fill"
                            scope_sources.append("verify:local_fill_corroborated")
                            log.info(
                                "scenario.turn.route_bind_corroborated site=%s reason=local_fill_success roles=%s",
                                site_key,
                                ",".join(
                                    role
                                    for role, ok in (fill_corroboration.get("roles") or {}).items()
                                    if ok
                                ),
                            )

                    if (
                        site_key == "skyscanner"
                        and bool(ready)
                        and route_bound is None
                        and str(verify_status or "").strip().lower() in {"not_attempted", "not_verified", "unavailable_empty"}
                    ):
                        current_page_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                        path_lower = ""
                        try:
                            path_lower = str(urlparse(current_page_url).path or "").strip().lower()
                        except Exception:
                            path_lower = str(current_page_url or "").strip().lower()
                        if "/transport/flights/" in path_lower:
                            tail = path_lower.split("/transport/flights/", 1)[-1].strip("/")
                            parts = [p for p in tail.split("/") if p]
                            expected_origin = str(origin or "").strip().lower()
                            expected_dest = str(dest or "").strip().lower()
                            expected_depart = ""
                            expected_return = ""
                            for raw_value, role in ((depart, "depart"), (return_date, "return")):
                                text = str(raw_value or "").strip()
                                if not text:
                                    if role == "depart":
                                        expected_depart = ""
                                    else:
                                        expected_return = ""
                                    continue
                                parsed_date = None
                                for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                                    try:
                                        parsed_date = datetime.strptime(text, fmt)
                                        break
                                    except Exception:
                                        continue
                                compact = parsed_date.strftime("%y%m%d") if parsed_date else ""
                                if role == "depart":
                                    expected_depart = compact
                                else:
                                    expected_return = compact
                            observed_origin = parts[0] if len(parts) >= 1 else ""
                            observed_dest = parts[1] if len(parts) >= 2 else ""
                            observed_depart = parts[2] if len(parts) >= 3 else ""
                            observed_return = parts[3] if len(parts) >= 4 else ""
                            route_match = bool(
                                expected_origin
                                and expected_dest
                                and observed_origin == expected_origin
                                and observed_dest == expected_dest
                                and (not expected_depart or observed_depart == expected_depart)
                                and (
                                    not expected_return
                                    or not observed_return
                                    or observed_return == expected_return
                                )
                            )
                            if route_match:
                                route_bound = True
                                verify_status = "bound_url_route_corroborated"
                                if not str(verify_override_reason or "").strip():
                                    verify_override_reason = "route_bind_corroborated_url"
                                scope_sources.append("verify:url_route_corroborated")
                                log.info(
                                    "scenario.turn.route_bind_corroborated site=%s reason=url_route_match",
                                    site_key,
                                )

                    if (
                        site_key == "google_flights"
                        and bool(route_bound is True)
                        and not bool(ready)
                        and (
                            str(verify_status or "").strip().lower() == "bound_local_fill_corroborated"
                            or str(verify_override_reason or "").strip().lower()
                            == "route_bind_corroborated_local_fill"
                        )
                    ):
                        visible_final_html = _strip_nonvisible_html(final_html)
                        missing_contextual_results = not _google_has_contextual_price_card(
                            visible_final_html,
                            {
                                "origin": origin or "",
                                "dest": dest or "",
                                "depart": depart or "",
                                "return_date": return_date or "",
                            },
                        )
                        if missing_contextual_results:
                            search_timeout_ms = 6000
                            remaining = _budget_remaining_sec()
                            if remaining is not None:
                                search_timeout_ms = min(
                                    search_timeout_ms,
                                    max(0, int((float(remaining) - float(scenario_budget_soft_margin_sec)) * 1000)),
                                )
                            if search_timeout_ms > 0:
                                search_locale_hint = (
                                    _google_display_locale_hint_from_browser(browser)
                                    or _google_display_locale_hint_from_url(url)
                                    or _current_mimic_locale()
                                )
                                search_selectors = _service_search_click_fallbacks(
                                    "google_flights",
                                    locale_hint_override=str(search_locale_hint or ""),
                                )
                                search_commit_result = _google_search_and_commit(
                                    browser,
                                    selectors=search_selectors,
                                    timeout_ms=search_timeout_ms,
                                    page_url=url,
                                    origin=origin or "",
                                    dest=dest or "",
                                    depart=depart or "",
                                    return_date=return_date or "",
                                )
                                if scenario_run_id:
                                    _write_google_search_commit_probe_artifact(
                                        run_id=scenario_run_id,
                                        browser=browser,
                                        artifact_label="post_fill_turn_commit",
                                        selectors=list(search_selectors or []),
                                        search_result=search_commit_result,
                                        site_key=site_key or "google_flights",
                                        attempt=attempt,
                                        turn=turn_idx,
                                        page_url=url,
                                        origin=origin or "",
                                        dest=dest or "",
                                        depart=depart or "",
                                        return_date=return_date or "",
                                        compact_selector_dom_probe_fn=_compact_selector_dom_probe,
                                        write_json_artifact_fn=_write_json_artifact_snapshot,
                                    )
                                log.info(
                                    "scenario.turn.post_fill_search_commit site=%s ok=%s strategy=%s selector=%s results_signal=%s",
                                    site_key,
                                    bool(search_commit_result.get("ok")),
                                    str(search_commit_result.get("strategy", "") or ""),
                                    str(search_commit_result.get("selector_used", "") or ""),
                                    bool(search_commit_result.get("results_signal_found")),
                                )
                                if bool(search_commit_result.get("ok")):
                                    try:
                                        final_html = browser.content()
                                    except Exception as final_html_exc:
                                        log.debug(
                                            "scenario.post_fill_search_commit.final_html_failed site=%s error=%s",
                                            site_key,
                                            final_html_exc,
                                        )
                                        final_html = final_html
                                    _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                                    _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)
                                    ready = _is_results_ready(
                                        final_html,
                                        site_key=site_key,
                                        origin=origin,
                                        dest=dest,
                                        depart=depart,
                                        return_date=return_date,
                                        page_url=str(getattr(getattr(browser, "page", None), "url", "") or ""),
                                    )
                                    if ready:
                                        scope_sources.append("search_commit:post_fill")
                                else:
                                    scope_sources.append("search_commit:post_fill_failed")

                    if ready and site_key == "skyscanner" and not _has_skyscanner_price_signal(final_html):
                        settle_ms = max(
                            0,
                            int(
                                _threshold_site_value(
                                    "scenario_skyscanner_post_ready_settle_ms",
                                    site_key,
                                    6000,
                                )
                            ),
                        )
                        if settle_ms > 0:
                            log.info(
                                "scenario.skyscanner.ready_settle_wait start_ms=%s reason=results_hydration_pending",
                                settle_ms,
                            )
                            try:
                                browser.page.wait_for_timeout(settle_ms)
                            except Exception as settle_exc:
                                log.debug(
                                    "scenario.skyscanner.ready_settle_wait.failed error=%s",
                                    settle_exc,
                                )
                            try:
                                refreshed_html = str(browser.content() or "")
                            except Exception:
                                refreshed_html = ""
                            if refreshed_html:
                                final_html = refreshed_html
                                _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                                _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)
                        if not _has_skyscanner_price_signal(final_html):
                            ready = False
                            verify_status = "results_hydration_incomplete"
                            verify_override_reason = "skyscanner_results_hydration_incomplete"
                            scope_sources.append("ready_guard:missing_price_signal")
                            log.warning(
                                "scenario.turn.ready_overridden site=%s reason=%s html_len=%s",
                                site_key,
                                verify_override_reason,
                                len(str(final_html or "")),
                            )

                    if ready and site_key == "skyscanner":
                        current_page_url = str(
                            getattr(getattr(browser, "page", None), "url", "") or ""
                        )
                        if "/hotels" in current_page_url.lower():
                            recovery = _ensure_skyscanner_flights_context(
                                browser,
                                timeout_ms=6000,
                            )
                            scope_sources.append("ready_guard:hotels_context_recovery")
                            log.warning(
                                "scenario.skyscanner.hotels_context_detected url=%s recovery_ok=%s reason=%s selector=%s",
                                current_page_url[:240],
                                bool(recovery.get("ok")),
                                str(recovery.get("reason", "") or ""),
                                str(recovery.get("selector_used", "") or ""),
                            )
                            ready = False
                            verify_status = "non_flight_hotels_context"
                            verify_override_reason = "skyscanner_hotels_context_detected"
                            try:
                                final_html = str(browser.content() or "")
                            except Exception:
                                final_html = str(final_html or "")
                            _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                            _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)

                    scope_guard_enabled = bool(
                        _threshold_site_value(
                            "scenario_turn_scope_guard_enabled",
                            site_key,
                            True,
                        )
                    )
                    scope_guard_on_unready = bool(
                        _threshold_site_value(
                            "scenario_turn_scope_guard_on_unready",
                            site_key,
                            True,
                        )
                    )
                    if scope_guard_enabled and site_key == "google_flights":
                        quick_scope = _google_quick_page_class(
                            final_html,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date,
                        )
                        scope_sources.append(f"heuristic:{quick_scope}")

                    unready_scope_class = quick_scope
                    if (
                        unready_scope_class == "unknown"
                        and _is_non_flight_page_class(plugin_probe_page_class)
                    ):
                        unready_scope_class = plugin_probe_page_class
                    if (
                        not ready
                        and site_key == "google_flights"
                        and not bool(google_force_bind_suppression.get("use"))
                        and _should_run_vision_page_kind_probe(
                            enabled=bool(get_threshold("scenario_vision_page_kind_enabled", True)),
                            trigger_reason=verify_override_reason or verify_status,
                            scope_class=unready_scope_class,
                        )
                    ):
                        vision_page_kind = _run_vision_page_kind_probe(
                            html_text=final_html,
                            screenshot_stage="last",
                            trigger_reason=verify_override_reason or verify_status,
                        )
                        if isinstance(vision_page_kind, dict) and vision_page_kind:
                            scope_vlm_hint = dict(vision_page_kind)
                            kind = str(vision_page_kind.get("page_kind", "unknown") or "unknown").strip().lower()
                            if kind == "package":
                                unready_scope_class = "flight_hotel_package"
                            elif kind in {"irrelevant", "consent", "interstitial"}:
                                unready_scope_class = "irrelevant_page"
                            if _apply_vision_page_kind_hints(vision_page_kind):
                                try:
                                    browser.page.wait_for_timeout(200)
                                except Exception as wait_exc:
                                    log.debug(
                                        "scenario.scope_guard.wait_failed stage=vision_hint site=%s error=%s",
                                        site_key,
                                        wait_exc,
                                    )
                                final_html = browser.content()
                                _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                                quick_scope = _google_quick_page_class(
                                    final_html,
                                    origin=origin,
                                    dest=dest,
                                    depart=depart,
                                    return_date=return_date,
                                )
                                if not any(str(item).startswith("heuristic:") for item in scope_sources):
                                    scope_sources.append(f"heuristic:{quick_scope}")
                                if quick_scope != "unknown":
                                    unready_scope_class = quick_scope
                    elif (
                        not ready
                        and site_key == "google_flights"
                        and bool(google_force_bind_suppression.get("use"))
                        and not super_deep_exploration
                    ):
                        log.info(
                            "scenario.turn.scope_vlm_page_kind_skipped site=%s reason=%s",
                            site_key,
                            str(google_force_bind_suppression.get("reason", "") or "recent_local_date_open_failure"),
                        )

                    if (
                        scope_guard_enabled
                        and scope_guard_on_unready
                        and not ready
                        and site_key == "google_flights"
                        and _is_non_flight_page_class(unready_scope_class)
                    ):
                        # Check for irrelevant_page downgrade (Phase 3.1 VLM downgrade)
                        # If heuristic blocks as irrelevant_page but VLM affirms flights_results with medium+ confidence,
                        # downgrade the block to allow processing to continue, up to a limit of 2 overrides per scenario.
                        downgrade_result = {}
                        if unready_scope_class == "irrelevant_page":
                            vlm_probe = scope_vlm_hint if isinstance(scope_vlm_hint, dict) else {}
                            if vlm_probe:
                                downgrade_result = evaluate_irrelevant_page_downgrade(
                                    vlm_probe=vlm_probe,
                                    heuristic_reason="scope_guard_non_flight_irrelevant_page",
                                    context=scope_ctx,
                                    max_overrides=2,
                                )

                        if downgrade_result.get("should_downgrade"):
                            # Downgrade applied: continue without scope repair
                            log.info(
                                "scenario.turn.scope_override_irrelevant_page site=%s vlm_result=%s override_count=%d",
                                site_key,
                                downgrade_result.get("reason", ""),
                                downgrade_result.get("override_count", 0),
                            )
                            scope_conflict_log = {
                                "scope_conflict_detected": True,
                                "resolved_via_vlm": True,
                                "scope_override_count": downgrade_result.get("override_count", 0),
                                "reason": downgrade_result.get("reason", ""),
                            }
                            log.debug("scenario.turn.scope_conflict scope=%s", json.dumps(scope_conflict_log))
                        else:
                            if scope_ctx.get("_scope_override_count", 0) >= 2:
                                log.info(
                                    "scenario.turn.scope_override_limit_reached site=%s count=%s",
                                    site_key,
                                    scope_ctx.get("_scope_override_count", 0),
                                )
                                scope_repair_requested = False
                                verify_override_reason = "scope_override_limit_reached"
                                verify_status = "scope_override_limit_reached"
                            else:
                                scope_page_class = unready_scope_class
                                scope_trip_product = _page_class_to_trip_product(scope_page_class)
                                scope_repair_requested = True
                                verify_override_reason = f"scope_non_flight_{scope_page_class}"
                                verify_status = "scope_non_flight_unready"
                                feedback_step, feedback_selectors, feedback_reason = _record_scope_feedback(
                                    site_key=site_key,
                                    page_class=scope_page_class,
                                    step_trace=step_trace,
                                    fallback_plan=plan,
                                    user_id=knowledge_user,
                                )
                                if feedback_selectors:
                                    for selector in feedback_selectors:
                                        if selector not in blocked_selectors:
                                            blocked_selectors.append(selector)
                                if isinstance(feedback_step, dict) and feedback_step:
                                    log.warning(
                                        "scenario.scope_guard.feedback site=%s reason=%s step_index=%s action=%s role=%s selector=%s",
                                        site_key,
                                        feedback_reason,
                                        feedback_step.get("index"),
                                        feedback_step.get("action"),
                                        feedback_step.get("role"),
                                        feedback_step.get("used_selector") or (
                                            (feedback_step.get("selectors") or [None])[0]
                                            if isinstance(feedback_step.get("selectors"), list)
                                            else None
                                        ),
                                    )
                                knowledge = get_knowledge(site_key, user_id=knowledge_user)
                                global_knowledge_hint = _compose_global_knowledge_hint(knowledge)
                                local_knowledge_hint = _compose_local_knowledge_hint(knowledge)
                                if isinstance(vlm_ui_hint, dict) and vlm_ui_hint:
                                    vlm_hint_text = _compose_vlm_knowledge_hint(
                                        vlm_ui_hint,
                                        is_domestic=bool(is_domestic),
                                    )
                                    if vlm_hint_text:
                                        local_knowledge_hint = (
                                            local_knowledge_hint + "\n" + vlm_hint_text
                                            if local_knowledge_hint
                                            else vlm_hint_text
                                        )
                                blocked_selectors = _blocked_selectors_from_knowledge(knowledge)
                                for selector in feedback_selectors:
                                    if selector not in blocked_selectors:
                                        blocked_selectors.append(selector)
                                log.warning(
                                    "scenario.turn.scope_repair_requested site=%s reason=%s sources=%s",
                                    site_key,
                                    verify_override_reason,
                                    ",".join(scope_sources) if scope_sources else "",
                                )

                    route_support_for_scope_conflict = "none"
                    if route_bound is True and verify_available and verify_status == "bound":
                        route_support_for_scope_conflict = "strong"
                    if ready and site_key == "google_flights":
                        dom_scope_probe = _route_probe_for_html(final_html)
                        if isinstance(dom_scope_probe, dict) and dom_scope_probe:
                            dom_support = str(
                                dom_scope_probe.get("support", "none") or "none"
                            ).strip().lower()
                            if dom_support in {"strong", "weak"}:
                                route_support_for_scope_conflict = dom_support
                            if (
                                route_bound is None
                                and isinstance(dom_scope_probe.get("route_bound"), bool)
                            ):
                                route_bound = bool(dom_scope_probe.get("route_bound"))
                            if (
                                not dom_probe_observed
                                and isinstance(dom_scope_probe.get("observed"), dict)
                            ):
                                dom_probe_observed = dict(dom_scope_probe.get("observed") or {})

                    if ready and scope_guard_enabled:
                        skip_expensive_ready_scope = bool(
                            site_key == "google_flights"
                            and bool(route_bound is True)
                            and str(quick_scope or "").strip().lower() == "flight_only"
                            and (
                                str(verify_status or "").strip().lower() == "bound_local_fill_corroborated"
                                or str(verify_override_reason or "").strip().lower()
                                == "route_bind_corroborated_local_fill"
                            )
                        )
                        if skip_expensive_ready_scope:
                            scope_sources.append("scope:ready_local_fill_corroborated_skip_expensive")
                            log.info(
                                "scenario.turn.scope_guard_multimodal_skipped site=%s reason=ready_local_fill_corroborated scope=%s",
                                site_key,
                                quick_scope,
                            )

                        vlm_scope = {}
                        vlm_enabled = bool(
                            _threshold_site_value(
                                "scenario_turn_scope_guard_vlm_enabled",
                                site_key,
                                True,
                            )
                        )
                        if skip_expensive_ready_scope:
                            vlm_enabled = False
                        if vlm_enabled and bool(
                            _threshold_site_value(
                                "scenario_turn_scope_guard_vlm_only_when_quick_unknown",
                                site_key,
                                True,
                            )
                        ):
                            # Quick deterministic class already has a signal; skip
                            # expensive VLM scope pass unless class is unknown.
                            vlm_enabled = quick_scope == "unknown"
                        if vlm_enabled:
                            vlm_timeout_sec = int(
                                _threshold_site_value(
                                    "scenario_turn_scope_guard_vlm_timeout_sec",
                                    site_key,
                                    120,
                                )
                            )
                            vlm_timeout_cap_sec = int(
                                _threshold_site_value(
                                    "scenario_turn_scope_guard_vlm_timeout_cap_sec",
                                    site_key,
                                    300,
                                )
                            )
                            if vlm_timeout_cap_sec > 0:
                                vlm_timeout_sec = min(
                                    vlm_timeout_sec,
                                    max(1, vlm_timeout_cap_sec),
                                )
                            vlm_scope_max_variants = max(
                                1,
                                int(
                                    _threshold_site_value(
                                        "scenario_turn_scope_guard_vlm_max_variants",
                                        site_key,
                                        1,
                                    )
                                ),
                            )
                            vlm_scope_include_dom_context = bool(
                                _threshold_site_value(
                                    "scenario_turn_scope_guard_vlm_include_dom_context",
                                    site_key,
                                    False,
                                )
                            )
                            remaining = _budget_remaining_sec()
                            if remaining is not None:
                                available = int(remaining - float(scenario_budget_soft_margin_sec))
                                vlm_timeout_sec = min(vlm_timeout_sec, max(0, available))
                            if vlm_timeout_sec > 0:
                                image_path = _snapshot_image_path(site_key, "last", run_id=scenario_run_id)
                                if image_path.exists():
                                    try:
                                        vlm_scope = analyze_page_ui_with_vlm(
                                            str(image_path),
                                            site=site_key,
                                            is_domestic=is_domestic,
                                            origin=origin or "",
                                            dest=dest or "",
                                            depart=depart or "",
                                            return_date=return_date or "",
                                            locale=mimic_locale or "",
                                            html_context=final_html,
                                            include_dom_context=vlm_scope_include_dom_context,
                                            timeout_sec=vlm_timeout_sec,
                                            max_variants=vlm_scope_max_variants,
                                        )
                                    except Exception as scope_vlm_exc:
                                        log.warning(
                                            "scenario.scope_guard.vlm_failed site=%s error=%s",
                                            site_key,
                                            scope_vlm_exc,
                                        )
                                        vlm_scope = {}
                        if isinstance(vlm_scope, dict) and vlm_scope:
                            if isinstance(scope_vlm_hint, dict) and scope_vlm_hint:
                                merged_hint = dict(scope_vlm_hint)
                                merged_hint.update(vlm_scope)
                                scope_vlm_hint = merged_hint
                            else:
                                scope_vlm_hint = vlm_scope
                        vlm_scope_class = _normalize_page_class(
                            vlm_scope.get("page_class") if isinstance(vlm_scope, dict) else ""
                        )
                        if vlm_scope_class != "unknown":
                            scope_sources.append(f"vlm:{vlm_scope_class}")

                        llm_scope = {}
                        llm_enabled = bool(
                            _threshold_site_value(
                                "scenario_turn_scope_guard_llm_enabled",
                                site_key,
                                True,
                            )
                        )
                        if skip_expensive_ready_scope:
                            llm_enabled = False
                        if llm_enabled:
                            llm_timeout_sec = int(
                                _threshold_site_value(
                                    "scenario_turn_scope_guard_llm_timeout_sec",
                                    site_key,
                                    120,
                                )
                            )
                            llm_timeout_cap_sec = int(
                                _threshold_site_value(
                                    "scenario_turn_scope_guard_llm_timeout_cap_sec",
                                    site_key,
                                    240,
                                )
                            )
                            if llm_timeout_cap_sec > 0:
                                llm_timeout_sec = min(
                                    llm_timeout_sec,
                                    max(1, llm_timeout_cap_sec),
                                )
                            remaining = _budget_remaining_sec()
                            if remaining is not None:
                                available = int(remaining - float(scenario_budget_soft_margin_sec))
                                llm_timeout_sec = min(llm_timeout_sec, max(0, available))
                            if llm_timeout_sec > 0:
                                try:
                                    llm_scope = assess_trip_product_scope_with_llm(
                                        final_html,
                                        site=site_key,
                                        origin=origin or "",
                                        dest=dest or "",
                                        depart=depart or "",
                                        return_date=return_date or "",
                                        timeout_sec=llm_timeout_sec,
                                    )
                                except Exception as scope_llm_exc:
                                    log.warning(
                                        "scenario.scope_guard.llm_failed site=%s error=%s",
                                        site_key,
                                        scope_llm_exc,
                                    )
                                    llm_scope = {}
                        llm_scope_class = _normalize_page_class(
                            llm_scope.get("page_class") if isinstance(llm_scope, dict) else ""
                        )
                        if llm_scope_class != "unknown":
                            scope_sources.append(f"llm:{llm_scope_class}")

                        scope_page_class = _resolve_page_scope_class(
                            heuristic_class=quick_scope,
                            vlm_class=vlm_scope_class,
                            llm_class=llm_scope_class,
                        )
                        if _should_block_ready_on_scope_conflict(
                            heuristic_class=quick_scope,
                            llm_class=llm_scope_class,
                            resolved_class=scope_page_class,
                            route_bound=route_bound if isinstance(route_bound, bool) else None,
                            route_support=route_support_for_scope_conflict,
                            require_scope_not_irrelevant=bool(
                                _threshold_site_value(
                                    "scenario_ready_requires_scope_not_irrelevant",
                                    site_key,
                                    True,
                                )
                            ),
                        ):
                            scope_page_class = llm_scope_class
                            scope_sources.append("scope:llm_irrelevant_conflict")
                        scope_trip_product = _page_class_to_trip_product(scope_page_class)
                        if scope_page_class == "unknown":
                            if isinstance(vlm_scope, dict):
                                scope_trip_product = str(
                                    vlm_scope.get("trip_product", "") or scope_trip_product
                                ).strip().lower() or scope_trip_product
                            if scope_trip_product == "unknown" and isinstance(llm_scope, dict):
                                scope_trip_product = str(
                                    llm_scope.get("trip_product", "") or scope_trip_product
                                ).strip().lower() or scope_trip_product

                        if _is_non_flight_page_class(scope_page_class):
                            downgrade_ready = False
                            if scope_page_class == "irrelevant_page" and isinstance(scope_vlm_hint, dict):
                                downgrade_result = evaluate_irrelevant_page_downgrade(
                                    vlm_probe=scope_vlm_hint,
                                    heuristic_reason="scope_guard_non_flight_irrelevant_page",
                                    context=scope_ctx,
                                    max_overrides=2,
                                )
                                downgrade_ready = bool(downgrade_result.get("should_downgrade"))
                                if downgrade_ready:
                                    log.info(
                                        "scenario.turn.scope_override_irrelevant_page site=%s vlm_result=%s override_count=%d",
                                        site_key,
                                        downgrade_result.get("reason", ""),
                                        downgrade_result.get("override_count", 0),
                                    )
                                    scope_sources.append("scope:vlm_page_kind_override")

                            if not downgrade_ready:
                                ready = False
                                scope_repair_requested = True
                                verify_override_reason = f"scope_non_flight_{scope_page_class}"
                                verify_status = "scope_non_flight"
                                feedback_step, feedback_selectors, feedback_reason = _record_scope_feedback(
                                    site_key=site_key,
                                    page_class=scope_page_class,
                                    step_trace=step_trace,
                                    fallback_plan=plan,
                                    user_id=knowledge_user,
                                )
                                if feedback_selectors:
                                    for selector in feedback_selectors:
                                        if selector not in blocked_selectors:
                                            blocked_selectors.append(selector)
                                if isinstance(feedback_step, dict) and feedback_step:
                                    log.warning(
                                        "scenario.scope_guard.feedback site=%s reason=%s step_index=%s action=%s role=%s selector=%s",
                                        site_key,
                                        feedback_reason,
                                        feedback_step.get("index"),
                                        feedback_step.get("action"),
                                        feedback_step.get("role"),
                                        feedback_step.get("used_selector") or (
                                            (feedback_step.get("selectors") or [None])[0]
                                            if isinstance(feedback_step.get("selectors"), list)
                                            else None
                                        ),
                                    )
                                knowledge = get_knowledge(site_key, user_id=knowledge_user)
                                global_knowledge_hint = _compose_global_knowledge_hint(knowledge)
                                local_knowledge_hint = _compose_local_knowledge_hint(knowledge)
                                if isinstance(vlm_ui_hint, dict) and vlm_ui_hint:
                                    vlm_hint_text = _compose_vlm_knowledge_hint(
                                        vlm_ui_hint,
                                        is_domestic=bool(is_domestic),
                                    )
                                    if vlm_hint_text:
                                        local_knowledge_hint = (
                                            local_knowledge_hint + "\n" + vlm_hint_text
                                            if local_knowledge_hint
                                            else vlm_hint_text
                                        )
                                blocked_selectors = _blocked_selectors_from_knowledge(knowledge)
                                for selector in feedback_selectors:
                                    if selector not in blocked_selectors:
                                        blocked_selectors.append(selector)
                                log.warning(
                                    "scenario.turn.ready_overridden site=%s reason=%s sources=%s",
                                    site_key,
                                    verify_override_reason,
                                    ",".join(scope_sources) if scope_sources else "",
                                )
                    if site_key == "google_flights":
                        consistency = _google_reconcile_ready_route_bound_consistency(
                            ready=bool(ready),
                            route_bound=route_bound if isinstance(route_bound, bool) else None,
                            verify_status=verify_status,
                            verify_override_reason=verify_override_reason,
                            scope_page_class=scope_page_class,
                        )
                        if bool(consistency.get("changed")):
                            ready = bool(consistency.get("ready"))
                            verify_status = str(consistency.get("verify_status", verify_status) or verify_status)
                            verify_override_reason = str(
                                consistency.get("verify_override_reason", verify_override_reason)
                                or verify_override_reason
                            )
                            scope_repair_requested = True
                            scope_sources.append("verify:route_bind_not_verified")
                            log.warning(
                                "scenario.turn.ready_overridden site=%s reason=%s route_bound=%s verify_status=%s scope_class=%s",
                                site_key,
                                str(consistency.get("reason", "") or "route_bind_not_verified"),
                                route_bound,
                                verify_status,
                                scope_page_class,
                            )

                    route_support = "none"
                    if not observed_dest_raw:
                        observed_dest_raw = str(dom_probe_observed.get("dest", "") or "")
                    if not dest_is_placeholder:
                        dest_is_placeholder = _is_google_dest_placeholder(observed_dest_raw)
                    if route_bound is True:
                        route_support = "strong"
                    elif dom_probe_observed or vlm_verify_fields:
                        route_support = "weak"
                    if verify_status in {"route_fill_mismatch", "unbound"}:
                        route_support = "none"
                    route_source = "unknown"
                    if dom_probe_observed and vlm_verify_fields:
                        route_source = "mixed"
                    elif dom_probe_observed:
                        route_source = "dom"
                    elif vlm_verify_fields:
                        route_source = "vlm"
                    route_reason = (
                        "explicit_mismatch"
                        if verify_status == "route_fill_mismatch"
                        else (verify_override_reason or verify_status or "unknown")
                    )
                    if str(route_reason or "").strip().lower() == "route_bind_corroborated_local_fill":
                        route_source = "local"
                    route_verdict = {
                        "route_bound": bool(route_bound),
                        "support": route_support,
                        "source": route_source,
                        "reason": route_reason,
                        "dest_is_placeholder": bool(dest_is_placeholder),
                        "observed_dest_raw": observed_dest_raw,
                        "observed": {
                            "origin": dom_probe_observed.get("origin"),
                            "dest": dom_probe_observed.get("dest"),
                            "depart": dom_probe_observed.get("depart"),
                            "return": dom_probe_observed.get("return"),
                        },
                    }
                    _write_evidence_checkpoint(
                        "after_results_ready_check",
                        payload={
                            "attempt": attempt + 1,
                            "turn": turn_idx + 1,
                            "form_state": {
                                "origin_text": dom_probe_observed.get("origin", ""),
                                "dest_text": dom_probe_observed.get("dest", ""),
                                "depart_text": dom_probe_observed.get("depart", ""),
                                "return_text": dom_probe_observed.get("return", ""),
                                "confidence": "high" if dom_probe_observed else "low",
                                "reason": verify_status,
                                "observed_dest_raw": observed_dest_raw,
                                "dest_is_placeholder": bool(dest_is_placeholder),
                                "dest_selector_used": google_trace_dest_selector,
                                "dest_committed": bool(google_trace_dest_committed),
                                "dest_commit_reason": google_trace_dest_commit_reason,
                                "suggestion_used": bool(google_trace_suggestion_used),
                                "date_picker_seen": bool(google_trace_date_picker_seen),
                                "date_picker_done_clicked": bool(google_trace_date_done_clicked),
                            },
                            "route_bind": route_verdict,
                            "scope_guard": {
                                "page_class": scope_page_class,
                                "trip_product": scope_trip_product,
                                "sources": list(scope_sources or []),
                                "heuristic": quick_scope,
                                "vlm": vlm_scope_class,
                                "llm": llm_scope_class,
                            },
                            "readiness": {
                                "ready": bool(ready),
                                "override_reason": verify_override_reason or verify_status,
                            },
                        },
                    )
                    _write_route_state_debug(
                        run_id=scenario_run_id,
                        site_key=site_key,
                        payload={
                            "run_id": scenario_run_id,
                            "service": site_key,
                            "attempt": attempt + 1,
                            "turn": turn_idx + 1,
                            "expected": {
                                "origin": origin or "",
                                "dest": dest or "",
                                "depart": depart or "",
                                "return": return_date or "",
                            },
                            "observed_dom_probe": dom_probe_observed or {},
                            "observed_dest_raw": observed_dest_raw,
                            "dest_is_placeholder": bool(dest_is_placeholder),
                            "dest_selector_used": google_trace_dest_selector,
                            "dest_committed": bool(google_trace_dest_committed),
                            "dest_commit_reason": google_trace_dest_commit_reason,
                            "suggestion_used": bool(google_trace_suggestion_used),
                            "date_picker_seen": bool(google_trace_date_picker_seen),
                            "date_picker_done_clicked": bool(google_trace_date_done_clicked),
                            "vlm_route_verify_fields": vlm_verify_fields or {},
                            "route_bind_verdict": route_verdict,
                            "scope_verdicts": {
                                "heuristic": quick_scope,
                                "vlm": vlm_scope_class,
                                "llm": llm_scope_class,
                                "final": scope_page_class,
                                "sources": list(scope_sources or []),
                            },
                            "scenario_extract_verdict": _build_route_state_scenario_extract_verdict(
                                site_key=site_key,
                                route_bind_verdict=route_verdict if isinstance(route_verdict, dict) else {},
                                scope_final=scope_page_class,
                                ready=ready if isinstance(ready, bool) else None,
                            ),
                        },
                    )
                    log.info(
                        "scenario.turn.done attempt=%s turn=%s html_len=%s ready=%s route_bound=%s verify_available=%s verify_status=%s scope_class=%s scope_product=%s scope_sources=%s override_reason=%s",
                        attempt + 1,
                        turn_idx + 1,
                        len(final_html),
                        ready,
                        route_bound,
                        verify_available,
                        verify_status,
                        scope_page_class,
                        scope_trip_product,
                        ",".join(scope_sources) if scope_sources else "",
                        verify_override_reason,
                    )
                    _write_progress_snapshot(
                        stage="turn_done",
                        run_id=scenario_run_id,
                        site_key=site_key,
                        url=url,
                        attempt=attempt + 1,
                        turn=turn_idx + 1,
                        html_len=len(final_html),
                        ready=ready,
                        route_bound=route_bound,
                        verify_available=verify_available,
                        verify_status=verify_status,
                        scope_page_class=scope_page_class,
                        scope_trip_product=scope_trip_product,
                        scope_sources=scope_sources,
                        step_trace=step_trace,
                        verify_override_reason=verify_override_reason,
                    )

                    if ready:
                        if site_key == "skyscanner":
                            settle_ms = max(
                                0,
                                int(
                                    _threshold_site_value(
                                        "scenario_skyscanner_post_ready_settle_ms",
                                        site_key,
                                        6000,
                                    )
                                ),
                            )
                            if settle_ms > 0:
                                log.info(
                                    "scenario.skyscanner.ready_settle_wait start_ms=%s reason=post_ready_confirmation",
                                    settle_ms,
                                )
                                try:
                                    browser.page.wait_for_timeout(settle_ms)
                                except Exception as settle_exc:
                                    log.debug(
                                        "scenario.skyscanner.ready_settle_wait.failed error=%s",
                                        settle_exc,
                                    )
                                try:
                                    refreshed_html = str(browser.content() or "")
                                except Exception:
                                    refreshed_html = ""
                                if refreshed_html:
                                    final_html = refreshed_html
                                    _write_html_snapshot(site_key, final_html, stage="last", run_id=scenario_run_id)
                                    _write_image_snapshot(browser, site_key, stage="last", run_id=scenario_run_id)
                            current_page_url = str(
                                getattr(getattr(browser, "page", None), "url", "") or ""
                            )
                            ready_after_settle = _is_results_ready(
                                final_html,
                                site_key=site_key,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                page_url=current_page_url,
                            )
                            shell_incomplete_after_settle = _is_skyscanner_results_shell_incomplete(
                                final_html,
                                page_url=current_page_url,
                            )
                            has_price_signal_after_settle = _has_skyscanner_price_signal(final_html)
                            log.info(
                                "scenario.skyscanner.ready_settle_recheck url=%s ready=%s shell_incomplete=%s has_price_signal=%s html_len=%s",
                                current_page_url[:240],
                                bool(ready_after_settle),
                                bool(shell_incomplete_after_settle),
                                bool(has_price_signal_after_settle),
                                len(str(final_html or "")),
                            )
                            if (not bool(ready_after_settle)) or bool(shell_incomplete_after_settle):
                                ready = False
                                verify_status = "ready_recheck_failed"
                                verify_override_reason = (
                                    "skyscanner_results_shell_incomplete_after_ready"
                                    if bool(shell_incomplete_after_settle)
                                    else "skyscanner_ready_recheck_failed"
                                )
                                scope_sources.append("ready_guard:post_ready_recheck_failed")
                                log.warning(
                                    "scenario.skyscanner.ready_settle_recheck_failed reason=%s",
                                    verify_override_reason,
                                )

                        persist_plan = _reconcile_fill_plan_roles_and_values(
                            _retarget_plan_inputs(
                                copy.deepcopy(plan),
                                origin,
                                dest,
                                depart,
                                return_date or "",
                                trip_type,
                                site_key=site_key or "",
                            ),
                            site_key=site_key or "",
                            origin=origin or "",
                            dest=dest or "",
                            depart=depart or "",
                            return_date=return_date or "",
                            trip_type=trip_type,
                        )
                        if ready:
                            save_plan(site_key, persist_plan, notes=planner_notes)
                            record_success(
                                site_key,
                                persist_plan,
                                is_domestic=is_domestic,
                                source_url=url,
                                turns_used=turn_idx + 1,
                                user_id=knowledge_user,
                            )
                            return _scenario_return(
                                final_html,
                                ready=True,
                                reason=verify_override_reason or "ready",
                                scope_class=scope_page_class,
                                route_bound=route_bound,
                                route_support=route_support,
                            )

                    if turn_idx + 1 >= max_turns:
                        retry_on_unready = bool(
                            _threshold_site_value(
                                "scenario_recovery_retry_on_unready",
                                site_key,
                                True,
                            )
                        )
                        if retry_on_unready and (attempt + 1) < max_retries:
                            raise RuntimeError(
                                f"results_not_ready_after_turn_limit attempt={attempt + 1} turns={max_turns}"
                            )
                        # Best effort fallback when retry-on-unready is disabled or retries exhausted.
                        log.warning(
                            "scenario.turn.max_reached attempt=%s turns=%s returning_latest_html retry_on_unready=%s",
                            attempt + 1,
                            max_turns,
                            retry_on_unready,
                        )
                        # Do not persist this plan/url as "success" when readiness check failed.
                        # Otherwise knowledge store may drift toward wrong page types.
                        return _scenario_return(
                            final_html,
                            ready=False,
                            reason=verify_override_reason or "turn_limit_unready",
                            scope_class=scope_page_class,
                            route_bound=route_bound,
                            route_support=route_support,
                        )

                    mismatch_rewind_followup = None
                    mismatch_rewind_reason = ""
                    force_bind_followup = None
                    force_bind_reason = ""
                    mismatch_rewind_enabled = bool(
                        get_threshold(
                            "google_flights_rewind_priority_on_route_mismatch_enabled",
                            True,
                        )
                    )
                    mismatch_rewind_max = int(
                        get_threshold(
                            "google_flights_rewind_priority_on_route_mismatch_max_per_attempt",
                            1,
                        )
                    )
                    if site_key == "google_flights":
                        last_known_form_state = {}
                        if isinstance(dom_probe_observed, dict) and dom_probe_observed:
                            last_known_form_state = {
                                "origin_text": str(dom_probe_observed.get("origin", "") or ""),
                                "dest_text": str(dom_probe_observed.get("dest", "") or ""),
                                "dest_text_raw": observed_dest_raw
                                or str(dom_probe_observed.get("dest", "") or ""),
                                "depart_text": str(dom_probe_observed.get("depart", "") or ""),
                                "return_text": str(dom_probe_observed.get("return", "") or ""),
                                "confidence": "high",
                                "current_url": url or "",
                            }
                        mismatch_verdict = _route_mismatch_suspected_verdict(
                            service_key=site_key,
                            origin=origin or "",
                            dest=dest or "",
                            depart=depart or "",
                            return_date=return_date or "",
                            trip_type=trip_type,
                            html=final_html,
                            last_known_form_state=(
                                last_known_form_state if last_known_form_state else None
                            ),
                        )
                        mismatch_rewind_reason = str(
                            mismatch_verdict.get("reason", "") or ""
                        )
                        prioritized = _prioritized_google_route_mismatch_rewind_followup(
                            service_key=site_key,
                            mismatch_suspected=bool(mismatch_verdict.get("mismatch")),
                            enabled=mismatch_rewind_enabled,
                            uses=mismatch_rewind_priority_uses,
                            max_per_attempt=mismatch_rewind_max,
                            plan=plan,
                            step_trace=step_trace,
                            scope_class=scope_page_class,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date or "",
                            trip_type=trip_type,
                            is_domestic=bool(is_domestic),
                            vlm_hint=scope_vlm_hint or vlm_ui_hint,
                        )
                        mismatch_rewind_followup = prioritized.get("followup")
                        mismatch_rewind_priority_uses = int(
                            prioritized.get("uses", mismatch_rewind_priority_uses)
                        )
                        if _is_valid_plan(mismatch_rewind_followup):
                            log.info(
                                "scenario.plan.turn_followup_mismatch_rewind site=%s attempt=%s turn=%s/%s reason=%s use_index=%s/%s",
                                site_key,
                                attempt + 1,
                                turn_idx + 1,
                                max_turns,
                                mismatch_rewind_reason or "mismatch",
                                mismatch_rewind_priority_uses,
                                max(0, mismatch_rewind_max),
                            )

                        force_bind_enabled = bool(
                            get_threshold(
                                "google_flights_force_route_bind_repair_enabled",
                                True,
                            )
                        )
                        force_bind_max = int(
                            get_threshold(
                                "google_flights_force_route_bind_repair_max_per_attempt",
                                1,
                            )
                        )
                        force_bind_policy = _google_force_bind_repair_policy(
                            service_key=site_key,
                            enabled=force_bind_enabled,
                            uses=google_force_bind_repair_uses,
                            max_per_attempt=force_bind_max,
                            verify_status=verify_status,
                            scope_class=scope_page_class,
                            observed_dest_raw=observed_dest_raw
                            or str(dom_probe_observed.get("dest", "") or ""),
                            observed_origin_raw=observed_origin_raw
                            or str(dom_probe_observed.get("origin", "") or ""),
                            expected_origin=origin or "",
                        )
                        if bool(google_force_bind_suppression.get("use")) and not super_deep_exploration:
                            force_bind_policy = {"use": False, "reason": "suppressed_recent_local_date_open_failure"}
                            log.info(
                                "scenario.plan.turn_followup_force_bind_skipped site=%s reason=%s",
                                site_key,
                                str(google_force_bind_suppression.get("reason", "") or "recent_local_date_open_failure"),
                            )
                        if bool(force_bind_policy.get("use")):
                            force_bind_reason = str(
                                force_bind_policy.get("reason", "") or "force_bind"
                            )
                            force_bind_followup = _google_force_route_bound_repair_plan(
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date or "",
                                trip_type=trip_type,
                                is_domestic=bool(is_domestic),
                                scope_class=scope_page_class,
                                vlm_hint=scope_vlm_hint or vlm_ui_hint,
                                force_flights_tab=bool(
                                    force_bind_policy.get("force_flights_tab")
                                ),
                            )
                            if _is_valid_plan(force_bind_followup):
                                google_force_bind_repair_uses += 1
                                log.info(
                                    "scenario.plan.turn_followup_force_bind site=%s attempt=%s turn=%s/%s reason=%s use_index=%s/%s",
                                    site_key,
                                    attempt + 1,
                                    turn_idx + 1,
                                    max_turns,
                                    force_bind_reason,
                                    google_force_bind_repair_uses,
                                    max(0, force_bind_max),
                                )

                    if _is_valid_plan(google_recovery_collab_followup):
                        followup = google_recovery_collab_followup
                        log.info(
                            "scenario.plan.turn_followup_google_recovery_collab valid=True site=%s reason=%s",
                            site_key,
                            google_recovery_collab_followup_reason or "",
                        )
                    elif _is_valid_plan(force_bind_followup):
                        followup = force_bind_followup
                    elif _is_valid_plan(mismatch_rewind_followup):
                        followup = mismatch_rewind_followup
                    elif scope_repair_requested:
                        followup = _scope_rewind_followup_plan(
                            site_key=site_key,
                            plan=plan,
                            step_trace=step_trace,
                            scope_class=scope_page_class,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date or "",
                            trip_type=trip_type,
                            is_domestic=bool(is_domestic),
                            vlm_hint=scope_vlm_hint or vlm_ui_hint,
                        )
                        if _is_valid_plan(followup):
                            log.info(
                                "scenario.plan.turn_followup_scope_repair_rewind valid=True site=%s scope_class=%s",
                                site_key,
                                scope_page_class,
                            )
                        elif site_key == "google_flights":
                            followup = _google_non_flight_scope_repair_plan(
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                trip_type=trip_type,
                                is_domestic=bool(is_domestic),
                                scope_class=scope_page_class,
                                vlm_hint=scope_vlm_hint or vlm_ui_hint,
                            )
                        else:
                            followup = _default_plan_for_service(
                                site_key,
                                origin,
                                dest,
                                depart,
                                return_date=return_date,
                                is_domestic=is_domestic,
                                knowledge=knowledge if isinstance(knowledge, dict) else {},
                            )
                        log.info(
                            "scenario.plan.turn_followup_scope_repair valid=%s site=%s scope_class=%s",
                            _is_valid_plan(followup),
                            site_key,
                            scope_page_class,
                        )
                    elif use_fast_deterministic or strict_three_layer_control:
                        if site_key == "skyscanner":
                            followup = _default_plan_for_service(
                                site_key,
                                origin,
                                dest,
                                depart,
                                return_date=return_date,
                                is_domestic=is_domestic,
                                knowledge=knowledge if isinstance(knowledge, dict) else {},
                            )
                            log.info(
                                "scenario.plan.turn_followup_fast_default valid=%s mode=%s promoted=skyscanner_full_refill",
                                _is_valid_plan(followup),
                                "strict_three_layer" if strict_three_layer_control else "fast_deterministic",
                            )
                        else:
                            followup = _default_turn_followup_plan(site_key)
                            log.info(
                                "scenario.plan.turn_followup_fast_default valid=%s mode=%s",
                                _is_valid_plan(followup),
                                "strict_three_layer" if strict_three_layer_control else "fast_deterministic",
                            )
                    else:
                        try:
                            followup, followup_notes = _call_generate_action_plan_bundle(
                                router=router,
                                html=final_html,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                trip_type=trip_type,
                                is_domestic=is_domestic,
                                max_transit=max_transit,
                                turn_index=turn_idx + 1,
                                global_knowledge=global_knowledge_hint,
                                local_knowledge=_compose_local_hint_with_notes(
                                    local_knowledge_hint, planner_notes, trace_memory_hint
                                ),
                                site_key=site_key,
                                mimic_locale=mimic_locale,
                                mimic_region=mimic_region,
                                screenshot_path=_planner_snapshot_path(site_key, ["last", "initial"], run_id=scenario_run_id),
                                trace_memory_hint=trace_memory_hint,
                            )
                            planner_notes = _merge_planner_notes(planner_notes, followup_notes)
                        except Exception as followup_exc:
                            if isinstance(followup_exc, (TimeoutError, KeyboardInterrupt)):
                                raise
                            log.warning(
                                "scenario.plan.turn_generate_failed site=%s attempt=%s turn=%s error=%s",
                                site_key,
                                attempt + 1,
                                turn_idx + 1,
                                followup_exc,
                            )
                            followup = None
                        log.info("scenario.plan.turn_followup valid=%s", _is_valid_plan(followup))
                        if not _is_valid_plan(followup):
                            try:
                                followup, repair_notes = _call_repair_action_plan_bundle(
                                    plan,
                                    final_html,
                                    router=router,
                                    site_key=site_key,
                                    turn_index=turn_idx + 1,
                                    origin=origin,
                                    dest=dest,
                                    depart=depart,
                                    return_date=return_date or "",
                                    is_domestic=is_domestic,
                                    mimic_locale=mimic_locale,
                                    mimic_region=mimic_region,
                                    screenshot_path=_planner_snapshot_path(site_key, ["last", "initial"], run_id=scenario_run_id),
                                    trace_memory_hint=trace_memory_hint,
                                )
                                planner_notes = _merge_planner_notes(planner_notes, repair_notes)
                            except Exception as repair_exc:
                                if isinstance(repair_exc, (TimeoutError, KeyboardInterrupt)):
                                    raise
                                log.warning(
                                    "scenario.plan.turn_repair_failed site=%s attempt=%s turn=%s error=%s",
                                    site_key,
                                    attempt + 1,
                                    turn_idx + 1,
                                    repair_exc,
                                )
                                followup = None
                            log.info("scenario.plan.turn_repair valid=%s", _is_valid_plan(followup))

                    if not _is_valid_plan(followup):
                        deterministic_followup = _default_turn_followup_plan(site_key)
                        if _is_valid_plan(deterministic_followup):
                            log.warning(
                                "scenario.plan.turn_followup_fallback source=deterministic site=%s attempt=%s turn=%s",
                                site_key,
                                attempt + 1,
                                turn_idx + 1,
                            )
                            followup = deterministic_followup

                    if not _is_valid_plan(followup):
                        if _should_return_latest_html_on_followup_failure():
                            log.warning(
                                "scenario.turn.followup_unavailable returning_latest_html attempt=%s turn=%s/%s mode=%s",
                                attempt + 1,
                                turn_idx + 1,
                                max_turns,
                                llm_mode,
                            )
                            _write_progress_snapshot(
                                stage="turn_followup_unavailable_returning_html",
                                run_id=scenario_run_id,
                                site_key=site_key,
                                url=url,
                                attempt=attempt + 1,
                                turn=turn_idx + 1,
                                max_turns=max_turns,
                                llm_mode=llm_mode,
                            )
                            return _scenario_return(
                                final_html,
                                ready=False,
                                reason="turn_followup_unavailable",
                                scope_class=scope_page_class,
                                route_bound=route_bound,
                                route_support=route_support,
                            )
                        raise RuntimeError(
                            f"Unable to produce follow-up plan at turn {turn_idx + 1}"
                        )

                    plan = _retarget_plan_inputs(
                        plan=followup,
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        trip_type=trip_type,
                        site_key=site_key,
                    )
                    plan = _with_knowledge(
                        plan,
                        site_key,
                        is_domestic,
                        knowledge,
                        vlm_hint=vlm_ui_hint,
                    )
                    initial_html = final_html

            except Exception as exc:
                if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
                    raise
                last_error = exc
                log.exception("scenario.attempt.error attempt=%s error=%s", attempt + 1, exc)
                record_failure(
                    site_key,
                    error_message=str(exc),
                    plan=plan,
                    user_id=knowledge_user,
                )
                knowledge = get_knowledge(site_key, user_id=knowledge_user)
                global_knowledge_hint = _compose_global_knowledge_hint(knowledge)
                local_knowledge_hint = _compose_local_knowledge_hint(knowledge)
                if isinstance(vlm_ui_hint, dict) and vlm_ui_hint:
                    vlm_hint_text = _compose_vlm_knowledge_hint(
                        vlm_ui_hint,
                        is_domestic=bool(is_domestic),
                    )
                    if vlm_hint_text:
                        local_knowledge_hint = (
                            local_knowledge_hint + "\n" + vlm_hint_text
                            if local_knowledge_hint
                            else vlm_hint_text
                        )
                blocked_selectors = _blocked_selectors_from_knowledge(knowledge)

                # snapshot new DOM (best-effort; page may be mid-navigation after a failing step)
                try:
                    new_html = str(browser.content() or "")
                except Exception as content_exc:
                    log.warning(
                        "scenario.attempt.error_content_unavailable attempt=%s error=%s",
                        attempt + 1,
                        str(type(content_exc).__name__),
                    )
                    new_html = str(initial_html or "")
                error_url = ""
                error_title = ""
                try:
                    error_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                except Exception:
                    error_url = ""
                try:
                    error_title = str(browser.title() or "")
                except Exception:
                    error_title = ""
                visible_html = _strip_nonvisible_html(new_html)
                visible_text = ""
                if isinstance(visible_html, str) and visible_html:
                    try:
                        visible_text = re.sub(r"(?is)<[^>]+>", " ", visible_html)
                        visible_text = re.sub(r"\s+", " ", visible_text).strip()
                    except Exception:
                        visible_text = ""
                shell_incomplete = False
                shadow_probe: Dict[str, Any] = {}
                if site_key == "skyscanner":
                    try:
                        shell_incomplete = _is_skyscanner_results_shell_incomplete(
                            new_html,
                            page_url=error_url,
                        )
                    except Exception:
                        shell_incomplete = False
                    try:
                        shadow_probe = _probe_skyscanner_shadow_challenge_state(browser)
                    except Exception:
                        shadow_probe = {}
                runtime_diag_error: Dict[str, Any] = {}
                if hasattr(browser, "collect_runtime_diagnostics"):
                    try:
                        runtime_diag_error = browser.collect_runtime_diagnostics(
                            selectors=[
                                "#px-captcha",
                                "iframe[src*='px-cloud.net']",
                                "iframe[title*='Human verification' i]",
                                "button[type='submit']",
                                "button[data-testid*='search']",
                                "button[aria-label*='Search']",
                                "button[aria-label*='検索']",
                                "main [role='main']",
                            ]
                        )
                    except Exception:
                        runtime_diag_error = {}
                _write_html_snapshot(site_key, new_html, stage="attempt_error", run_id=scenario_run_id)
                _write_image_snapshot(browser, site_key, stage="attempt_error", run_id=scenario_run_id)
                changed = dom_changed(initial_html, new_html)
                log.info(
                    "scenario.attempt.dom_snapshot attempt=%s changed=%s old_len=%s new_len=%s",
                    attempt + 1,
                    changed,
                    len(initial_html),
                    len(new_html),
                )
                log.warning(
                    "scenario.attempt.error_page_state site=%s attempt=%s url=%s title=%s html_len=%s visible_text_len=%s shell_incomplete=%s shadow_suspected=%s shadow_reason=%s",
                    site_key,
                    attempt + 1,
                    error_url[:220],
                    error_title[:120],
                    len(str(new_html or "")),
                    len(str(visible_text or "")),
                    bool(shell_incomplete),
                    bool((shadow_probe or {}).get("suspected", False)),
                    str((shadow_probe or {}).get("reason", "") or ""),
                )
                _write_json_artifact_snapshot(
                    scenario_run_id,
                    f"trace/attempt_error_diag_attempt_{attempt + 1}.json",
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "site_key": site_key,
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "page_url": error_url,
                        "page_title": error_title,
                        "html_len": len(str(new_html or "")),
                        "visible_text_len": len(str(visible_text or "")),
                        "shell_incomplete": bool(shell_incomplete),
                        "shadow_probe": dict(shadow_probe or {}),
                        "runtime_diag": dict(runtime_diag_error or {}),
                    },
                )
                _write_progress_snapshot(
                    stage="attempt_error",
                    run_id=scenario_run_id,
                    site_key=site_key,
                    url=url,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    changed=changed,
                    error=str(exc),
                )
                if _budget_almost_exhausted():
                    remaining = _budget_remaining_sec()
                    log.warning(
                        "scenario.budget.soft_stop stage=attempt_error site=%s attempt=%s/%s remaining_s=%.2f",
                        site_key,
                        attempt + 1,
                        max_retries,
                        remaining if remaining is not None else -1.0,
                    )
                    return _scenario_return(
                        new_html,
                        ready=False,
                        reason="scenario_budget_soft_stop",
                        scope_class="unknown",
                        route_bound=False,
                        route_support="none",
                    )

                local_programming_reason = _local_programming_exception_reason(exc)
                if local_programming_reason:
                    log.warning(
                        "scenario.attempt.local_runtime_exception_no_burn site=%s attempt=%s reason=%s error=%s",
                        site_key,
                        attempt + 1,
                        local_programming_reason,
                        exc,
                    )
                    _write_progress_snapshot(
                        stage="attempt_error_local_runtime_exception_return",
                        run_id=scenario_run_id,
                        site_key=site_key,
                        url=url,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        changed=changed,
                        error=str(exc),
                        local_runtime_reason=local_programming_reason,
                    )
                    return _scenario_return(
                        new_html,
                        ready=False,
                        reason="local_runtime_exception",
                        scope_class="unknown",
                        route_bound=False,
                        route_support="none",
                    )

                signature = _error_signature(str(exc))
                if not changed and signature:
                    if signature == static_error_signature:
                        static_error_repeats += 1
                    else:
                        static_error_signature = signature
                        static_error_repeats = 1
                else:
                    static_error_signature = ""
                    static_error_repeats = 0

                if not changed and static_error_repeats >= 2:
                    _write_debug_snapshot(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "stage": "retry_repeated_static_error",
                            "site_key": site_key,
                            "url": url,
                            "origin": origin,
                            "dest": dest,
                            "depart": depart,
                            "return_date": return_date,
                            "trip_type": trip_type,
                            "is_domestic": is_domestic,
                            "max_transit": max_transit,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "error": str(last_error),
                            "error_signature": signature,
                            "static_error_repeats": static_error_repeats,
                            "plan": plan,
                            "new_html_head": new_html[:2000],
                        },
                        run_id=scenario_run_id,
                    )
                    raise RuntimeError(
                        "Repeated failure without DOM change; aborting early: "
                        f"{last_error}"
                    )

                smart_escalation_skip_reason = ""
                if site_key == "google_flights":
                    smart_escalation_skip_reason = _google_route_fill_smart_escalation_skip_reason(
                        locals().get("step_trace"),
                        error_message=str(exc),
                        browser=browser,
                    )
                    if not smart_escalation_skip_reason:
                        smart_escalation_skip_reason = (
                            _google_search_commit_smart_escalation_skip_reason(
                                locals().get("step_trace"),
                                error_message=str(exc),
                            )
                        )
                    if smart_escalation_skip_reason:
                        log.info(
                            "scenario.plan.smart_escalation_skipped site=%s attempt=%s reason=%s",
                            site_key,
                            attempt + 1,
                            smart_escalation_skip_reason,
                        )

                turn_idx_for_gate = int(locals().get("turn_idx", -1))
                protection_detected = _protection_surface_detected(
                    html_text=str(new_html or ""),
                    reason_text=str(exc or ""),
                )
                layer3_model_allowed = _allow_layer3_model_escalation(
                    control_model=control_model,
                    attempt_index=int(attempt),
                    turn_index=turn_idx_for_gate,
                    max_retries=int(max_retries),
                    max_turns=int(max_turns),
                    protection_detected=protection_detected,
                    used_count=int(layer3_model_escalation_used),
                    max_count=int(layer3_model_escalation_max),
                )
                if strict_three_layer_control and not layer3_model_allowed:
                    log.info(
                        "scenario.control.layer3_blocked site=%s attempt=%s turn=%s used=%s/%s protection_detected=%s",
                        site_key,
                        int(attempt) + 1,
                        turn_idx_for_gate + 1 if turn_idx_for_gate >= 0 else 0,
                        int(layer3_model_escalation_used),
                        int(layer3_model_escalation_max),
                        bool(protection_detected),
                    )

                if use_fast_deterministic:
                    should_keep_google_recovery = (
                        site_key == "google_flights"
                        and (
                            google_recovery_mode
                            or "results_not_ready_after_turn_limit" in str(exc)
                        )
                    )
                    force_soft_recovery_fills = bool(
                        _threshold_site_value(
                            "scenario_recovery_force_soft_fill",
                            site_key,
                            True,
                        )
                    )
                    if should_keep_google_recovery:
                        repaired = _google_non_flight_scope_repair_plan(
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date,
                            trip_type=trip_type,
                            is_domestic=bool(is_domestic),
                            scope_class="unknown",
                            vlm_hint=vlm_ui_hint,
                        )
                    else:
                        repaired = _default_plan_for_service(
                            site_key,
                            origin,
                            dest,
                            depart,
                            return_date=return_date,
                            is_domestic=is_domestic,
                            knowledge=knowledge,
                        )
                    if _is_valid_plan(repaired):
                        repaired = _retarget_plan_inputs(
                            plan=repaired,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date,
                            trip_type=trip_type,
                            site_key=site_key,
                        )
                        repaired = _with_knowledge(
                            repaired,
                            site_key,
                            is_domestic,
                            knowledge,
                            vlm_hint=vlm_ui_hint,
                        )
                        if should_keep_google_recovery and force_soft_recovery_fills:
                            repaired = _soften_recovery_route_fills(repaired)
                    log.info(
                        "scenario.plan.fast_default_repair valid=%s actionable=%s",
                        _is_valid_plan(repaired),
                        _is_actionable_plan(repaired, trip_type, site_key=site_key),
                    )
                    if (
                        not _is_actionable_plan(repaired, trip_type, site_key=site_key)
                        and not smart_escalation_skip_reason
                        and layer3_model_allowed
                        and bool(
                            get_threshold(
                                "light_mode_try_llm_repair_after_fast_failure",
                                True,
                            )
                        )
                    ):
                        smart_timeout_sec = _env_int(
                            "FLIGHT_WATCHER_LLM_LIGHT_REPAIR_TIMEOUT_SEC",
                            int(
                                get_threshold(
                                    "llm_light_repair_timeout_sec",
                                    max(60, light_planner_timeout_sec),
                                )
                            ),
                        )
                        if (
                            site_key == "google_flights"
                            and bool(
                                get_threshold(
                                    "scenario_light_smart_repair_use_vlm_ui_assist",
                                    True,
                                )
                            )
                        ):
                            retry_image_path = _snapshot_image_path(site_key, "attempt_error", run_id=scenario_run_id)
                            if retry_image_path.exists():
                                try:
                                    retry_ui_assist_max_variants = max(
                                        1,
                                        int(
                                            get_threshold(
                                                "scenario_light_smart_repair_vlm_max_variants",
                                                int(get_threshold("scenario_vlm_ui_assist_max_variants", 1)),
                                            )
                                        ),
                                    )
                                    retry_hint = analyze_page_ui_with_vlm(
                                        str(retry_image_path),
                                        site=site_key,
                                        is_domestic=is_domestic,
                                        origin=origin,
                                        dest=dest,
                                        depart=depart,
                                        return_date=return_date or "",
                                        locale=mimic_locale or "",
                                        html_context=new_html,
                                        include_dom_context=bool(
                                            get_threshold(
                                                "scenario_light_smart_repair_vlm_include_dom_context",
                                                False,
                                            )
                                        ),
                                        timeout_sec=min(
                                            int(
                                                get_threshold(
                                                    "scenario_light_smart_repair_vlm_timeout_sec",
                                                    120,
                                                )
                                            ),
                                            max(
                                                1,
                                                int(
                                                    get_threshold(
                                                        "scenario_light_smart_repair_vlm_timeout_cap_sec",
                                                        300,
                                                    )
                                                ),
                                            ),
                                        ),
                                        max_variants=retry_ui_assist_max_variants,
                                    )
                                except Exception as retry_vlm_exc:
                                    log.warning(
                                        "scenario.vlm_ui.retry_failed site=%s attempt=%s error=%s",
                                        site_key,
                                        attempt + 1,
                                        retry_vlm_exc,
                                    )
                                    retry_hint = {}
                                if isinstance(retry_hint, dict) and retry_hint:
                                    vlm_ui_hint = retry_hint
                                    _apply_vlm_runtime_hints(vlm_ui_hint)
                                    retry_hint_text = _compose_vlm_knowledge_hint(
                                        vlm_ui_hint,
                                        is_domestic=bool(is_domestic),
                                    )
                                    if retry_hint_text:
                                        local_knowledge_hint = (
                                            local_knowledge_hint + "\n" + retry_hint_text
                                            if local_knowledge_hint
                                            else retry_hint_text
                                        )
                                    log.info(
                                        "scenario.vlm_ui.retry_applied site=%s attempt=%s page_scope=%s trip_product=%s",
                                        site_key,
                                        attempt + 1,
                                        vlm_ui_hint.get("page_scope"),
                                        vlm_ui_hint.get("trip_product"),
                                    )

                        layer3_model_escalation_used += 1
                        llm_repaired = None
                        try:
                            llm_repaired, llm_repair_notes = _call_repair_action_plan_bundle(
                                plan,
                                new_html,
                                router=router,
                                site_key=site_key,
                                turn_index=attempt + 1,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date or "",
                                is_domestic=is_domestic,
                                mimic_locale=mimic_locale,
                                mimic_region=mimic_region,
                                screenshot_path=_planner_snapshot_path(site_key, ["attempt_error", "last", "initial"], run_id=scenario_run_id),
                                trace_memory_hint=trace_memory_hint,
                                timeout_sec=smart_timeout_sec,
                            )
                            planner_notes = _merge_planner_notes(
                                planner_notes,
                                llm_repair_notes,
                            )
                        except Exception as smart_repair_exc:
                            if isinstance(smart_repair_exc, (TimeoutError, KeyboardInterrupt)):
                                raise
                            log.warning(
                                "scenario.plan.smart_repair_failed site=%s attempt=%s error=%s",
                                site_key,
                                attempt + 1,
                                smart_repair_exc,
                            )
                            llm_repaired = None

                        if not _is_actionable_plan(
                            llm_repaired, trip_type, site_key=site_key
                        ):
                            try:
                                llm_repaired, llm_generate_notes = _call_generate_action_plan_bundle(
                                    router=router,
                                    html=new_html,
                                    origin=origin,
                                    dest=dest,
                                    depart=depart,
                                    return_date=return_date,
                                    trip_type=trip_type,
                                    is_domestic=is_domestic,
                                    max_transit=max_transit,
                                    turn_index=attempt + 1,
                                    global_knowledge=global_knowledge_hint,
                                    local_knowledge=_compose_local_hint_with_notes(
                                        local_knowledge_hint, planner_notes, trace_memory_hint
                                    ),
                                    site_key=site_key,
                                    mimic_locale=mimic_locale,
                                    mimic_region=mimic_region,
                                    screenshot_path=_planner_snapshot_path(site_key, ["attempt_error", "last", "initial"], run_id=scenario_run_id),
                                    trace_memory_hint=trace_memory_hint,
                                    timeout_sec=smart_timeout_sec,
                                )
                                planner_notes = _merge_planner_notes(
                                    planner_notes,
                                    llm_generate_notes,
                                )
                            except Exception as smart_generate_exc:
                                if isinstance(smart_generate_exc, (TimeoutError, KeyboardInterrupt)):
                                    raise
                                log.warning(
                                    "scenario.plan.smart_generate_failed site=%s attempt=%s error=%s",
                                    site_key,
                                    attempt + 1,
                                    smart_generate_exc,
                                )
                                llm_repaired = None

                        if _is_actionable_plan(llm_repaired, trip_type, site_key=site_key):
                            repaired = llm_repaired
                            log.info(
                                "scenario.plan.smart_escalation_applied site=%s attempt=%s",
                                site_key,
                                attempt + 1,
                            )
                        elif not smart_escalation_skip_reason:
                                log.warning(
                                    "scenario.plan.smart_escalation_unavailable site=%s attempt=%s",
                                    site_key,
                                    attempt + 1,
                                )
                else:
                    # Ask LLM to repair first; if that fails, do full regeneration.
                    repaired = None
                    if strict_three_layer_control and not layer3_model_allowed:
                        log.info(
                            "scenario.plan.repair_skipped site=%s attempt=%s reason=strict_three_layer_layer3_gate",
                            site_key,
                            attempt + 1,
                        )
                    elif not smart_escalation_skip_reason:
                        layer3_model_escalation_used += 1
                        try:
                            repaired, repair_notes = _call_repair_action_plan_bundle(
                                plan,
                                new_html,
                                router=router,
                                site_key=site_key,
                                turn_index=attempt + 1,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date or "",
                                is_domestic=is_domestic,
                                mimic_locale=mimic_locale,
                                mimic_region=mimic_region,
                                screenshot_path=_planner_snapshot_path(site_key, ["attempt_error", "last", "initial"], run_id=scenario_run_id),
                                trace_memory_hint=trace_memory_hint,
                            )
                            planner_notes = _merge_planner_notes(planner_notes, repair_notes)
                        except Exception as repair_exc:
                            if isinstance(repair_exc, (TimeoutError, KeyboardInterrupt)):
                                raise
                            log.warning(
                                "scenario.plan.repair_failed site=%s attempt=%s error=%s",
                                site_key,
                                attempt + 1,
                                repair_exc,
                            )
                            repaired = None
                    else:
                        log.info(
                            "scenario.plan.repair_skipped site=%s attempt=%s reason=%s",
                            site_key,
                            attempt + 1,
                            smart_escalation_skip_reason,
                        )
                    log.info("scenario.plan.repaired valid=%s", _is_valid_plan(repaired))
                    if (
                        not _is_actionable_plan(repaired, trip_type, site_key=site_key)
                        and not smart_escalation_skip_reason
                        and layer3_model_allowed
                    ):
                        try:
                            repaired, regenerated_notes = _call_generate_action_plan_bundle(
                                router=router,
                                html=new_html,
                                origin=origin,
                                dest=dest,
                                depart=depart,
                                return_date=return_date,
                                trip_type=trip_type,
                                is_domestic=is_domestic,
                                max_transit=max_transit,
                                turn_index=attempt + 1,
                                global_knowledge=global_knowledge_hint,
                                local_knowledge=_compose_local_hint_with_notes(
                                    local_knowledge_hint, planner_notes, trace_memory_hint
                                ),
                                site_key=site_key,
                                mimic_locale=mimic_locale,
                                mimic_region=mimic_region,
                                screenshot_path=_planner_snapshot_path(site_key, ["attempt_error", "last", "initial"], run_id=scenario_run_id),
                                trace_memory_hint=trace_memory_hint,
                            )
                            planner_notes = _merge_planner_notes(
                                planner_notes,
                                regenerated_notes,
                            )
                        except Exception as regen_exc:
                            if isinstance(regen_exc, (TimeoutError, KeyboardInterrupt)):
                                raise
                            log.warning(
                                "scenario.plan.regenerate_failed site=%s attempt=%s error=%s",
                                site_key,
                                attempt + 1,
                                regen_exc,
                            )
                            repaired = None
                        log.info(
                            "scenario.plan.regenerated valid=%s actionable=%s",
                            _is_valid_plan(repaired),
                            _is_actionable_plan(repaired, trip_type, site_key=site_key),
                        )
                if _is_actionable_plan(repaired, trip_type, site_key=site_key):
                    plan = _retarget_plan_inputs(
                        plan=repaired,
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                        trip_type=trip_type,
                        site_key=site_key,
                    )
                    plan = _with_knowledge(
                        plan,
                        site_key,
                        is_domestic,
                        knowledge,
                        vlm_hint=vlm_ui_hint,
                    )
                    if (
                        use_fast_deterministic
                        and site_key == "google_flights"
                        and (
                            google_recovery_mode
                            or "results_not_ready_after_turn_limit" in str(exc)
                        )
                        and bool(
                            _threshold_site_value(
                                "scenario_recovery_force_soft_fill",
                                site_key,
                                True,
                            )
                        )
                    ):
                        plan = _soften_recovery_route_fills(plan)
                    initial_html = new_html
                    continue

                # If DOM did not change and we still cannot repair, fail fast.
                if not changed:
                    _write_debug_snapshot(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "stage": "retry_no_dom_change",
                            "site_key": site_key,
                            "url": url,
                            "origin": origin,
                            "dest": dest,
                            "depart": depart,
                            "return_date": return_date,
                            "trip_type": trip_type,
                            "is_domestic": is_domestic,
                            "max_transit": max_transit,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "error": str(last_error),
                            "exception_type": type(last_error).__name__,
                            "traceback": traceback.format_exc(),
                            "plan": plan,
                            "new_html_head": new_html[:2000],
                        },
                        run_id=scenario_run_id,
                    )
                    raise RuntimeError(
                        f"No DOM change and plan repair failed: {last_error}"
                    )

        return finalize_retries_exhausted_return(
            browser=browser,
            site_key=site_key,
            scenario_run_id=scenario_run_id,
            url=url,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            trip_type=trip_type,
            is_domestic=is_domestic,
            max_transit=max_transit,
            max_retries=max_retries,
            max_turns=max_turns,
            last_error=last_error,
            plan=plan,
            write_debug_snapshot_fn=_write_debug_snapshot,
            write_html_snapshot_fn=_write_html_snapshot,
            write_image_snapshot_fn=_write_image_snapshot,
            scenario_return_fn=_scenario_return,
            logger=log,
        )
