"""Agentic browser scenario execution with plan generation and repair retries."""

import copy
import calendar
import hashlib
import html as html_lib
import json
import os
import re
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

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
    """Lazy wrapper that delegates to the extracted implementation.

    This avoids import-time circularity: the implementation lives in
    `core.scenario_runner.run_agentic_scenario`. At first call we import that
    module and inject this module's helper symbols into its globals so the
    implementation can reference them as before.
    """
    # Load the implementation module from its file path into an isolated
    # module object to avoid Python automatically binding the submodule onto
    # the parent package (which would overwrite this wrapper in
    # `core.scenario_runner`). This prevents the 'module object is not
    # callable' error observed when the attribute gets replaced.
    import importlib.util
    import sys
    from pathlib import Path

    impl_path = Path(__file__).parent / "scenario_runner" / "run_agentic_scenario.py"
    spec = importlib.util.spec_from_file_location(
        "core.scenario_runner._run_agentic_impl",
        str(impl_path),
    )
    impl_mod = importlib.util.module_from_spec(spec)
    # Execute the module in its own namespace
    spec.loader.exec_module(impl_mod)

    # No runtime injection is required: the extracted implementation imports
    # required helper symbols explicitly (via `core.scenario_runner.shared_helpers`).
    # Keeping this wrapper minimal avoids accidental attribute overwrite on
    # the parent package when the implementation is loaded.

    impl = getattr(impl_mod, "run_agentic_scenario")
    return impl(
        url,
        origin,
        dest,
        depart,
        return_date=return_date,
        trip_type=trip_type,
        is_domestic=is_domestic,
        max_transit=max_transit,
        human_mimic=human_mimic,
        disable_http2=disable_http2,
        knowledge_user=knowledge_user,
        mimic_locale=mimic_locale,
        mimic_timezone=mimic_timezone,
        mimic_currency=mimic_currency,
        mimic_region=mimic_region,
        mimic_latitude=mimic_latitude,
        mimic_longitude=mimic_longitude,
        site_key=site_key,
        browser_engine=browser_engine,
    )

from core.adapters.google_flights_adapter import (
    GoogleFlightsAgentAdapter,
    GoogleFlightsLegacyAdapter,
)
from core.adapters.skyscanner_adapter import SkyscannerAgentAdapter
from core.agent.engine import AgentEngine
from core.agent.plugins.base import RunContext
from core.agent.plugins.google_flights.plugin import GoogleFlightsPlugin
from core.browser import BrowserSession, apply_selector_timeout_strategy, safe_min_timeout_ms
from core.flight_plan import validate_flight_plan
from core.scenario_runner.vlm_helpers import (
    _apply_vlm_runtime_hints,
    _clear_vlm_runtime_hints,
    _compose_vlm_knowledge_hint,
    _sanitize_vlm_label,
    _sanitize_vlm_labels,
)
from core.scenario_runner.vlm.probes import (
    _normalize_vision_fill_verify_result,
    _normalize_vision_page_kind_result,
    _should_run_vision_page_kind_probe,
    _should_run_vision_post_fill_verify,
    _vision_cached_stage_call,
    _vision_modal_dismiss_selectors,
    _vision_screenshot_fingerprint,
)
from core.scenario_runner.vlm.ui_steps import (
    _maybe_prepend_vlm_ui_steps,
    _maybe_run_initial_vlm_ui_assist,
    _service_mode_toggle_step,
    _service_product_toggle_step,
    _vlm_mode_toggle_step,
    _vlm_product_toggle_step,
)
from core.scenario_runner.google_flights_helpers import (
    build_google_fill_fallback_selectors,
    write_google_date_selector_probe,
    create_google_date_debug_probe_callback,
    _write_google_search_commit_probe_artifact,
    has_recent_google_date_failure_in_trace,
    has_google_date_done_clicked_in_trace,
)
from core.site_adapter_registry import get_global_registry
from core.plugins.adapters.services_adapter import (
    is_actionable_readiness_probe,
    plugin_strategy_enabled,
    run_service_readiness_hints,
    run_service_readiness_probe,
)
from core.plugins.services.google_flights import build_google_flights_deeplink
from core.route_binding import (
    classify_google_deeplink_page_state_recovery_reason,
    dom_route_bind_probe,
)
from core.run_input_config import load_run_input_config
from core.scenario_runner.route_recovery_helpers import (
    _google_activate_route_form_recovery_impl,
    _google_force_bind_repair_policy_impl,
    _google_force_route_bound_repair_plan_impl,
    _google_refill_dest_on_mismatch_impl,
    _should_attempt_google_route_mismatch_reset_impl,
)
from core.scenario_runner.execute_plan_context import ExecutePlanContext
from core.scenario_runner.execute_plan_handlers import (
    has_recent_skyscanner_date_failure_in_turn,
    optional_click_visibility_soft_skip,
    run_generic_click_action,
    run_generic_fill_or_wait_action,
    should_skip_return_fill_after_depart_failure,
    soft_skip_after_recent_date_failure,
)
from core.scenario_runner.notes import (
    _compose_local_hint_with_notes,
    _error_signature,
    _local_programming_exception_reason,
    _merge_planner_notes,
    _planner_notes_hint,
    _sanitize_runtime_note,
    _should_return_latest_html_on_followup_failure,
    _step_trace_memory_hint,
)
from core.scenario_runner.page_scope import (
    apply_plugin_readiness_probe as _apply_plugin_readiness_probe,
    page_class_to_trip_product as _page_class_to_trip_product,
    record_scope_feedback as _record_scope_feedback,
    resolve_page_scope_class as _resolve_page_scope_class,
    scope_feedback_step as _scope_feedback_step,
    should_block_ready_on_scope_conflict as _should_block_ready_on_scope_conflict,
    is_non_flight_page_class as _is_non_flight_page_class,
    normalize_page_class as _normalize_page_class,
)
from core.scenario_runner.plan_hygiene import (
    _plan_has_click_token,
    _return_date_step,
    _selector_expects_airport_code,
    _selector_expects_date,
)
from core.scenario_runner.plan_enrichment import (
    maybe_enrich_wait_step as _maybe_enrich_wait_step,
    maybe_harden_fill_steps as _maybe_harden_fill_steps,
    maybe_harden_search_clicks as _maybe_harden_search_clicks,
    maybe_harden_wait_steps as _maybe_harden_wait_steps,
    maybe_prepend_modal_step as _maybe_prepend_modal_step,
    with_knowledge as _with_knowledge,
)
from core.scenario_runner.plan_toggles import (
    _default_domain_toggle_step,
    _default_turn_followup_plan,
    _domain_toggle_step_from_knowledge,
    _maybe_prepend_domain_toggle,
)
from core.scenario_runner.planner_bridge import (
    _call_generate_action_plan_bundle,
    _call_repair_action_plan_bundle,
)
from core.scenario_runner.readiness import (
    is_results_ready as _is_results_ready,
)
from core.scenario_runner.selectors.fallbacks import (
    _selector_hints_overlay,
    _service_fill_fallbacks,
    _service_search_click_fallbacks,
    _service_wait_fallbacks,
)
from core.scenario_runner.selectors.probes import (
    _safe_click_first_match,
)
from core.scenario.gf_helpers.date_picker_orchestrator import (
    gf_set_date as _gf_set_date_impl,
)
from core.scenario.gf_helpers.selectors import (
    _expected_field_tokens,
)
from core.scenario.gf_helpers.google_date_picker import (
    google_fill_date_via_picker as _google_fill_date_via_picker_impl,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _contains_any_token,
    _dedupe_selectors,
    _default_google_flights_plan,
    _env_list,
    _google_date_done_selectors,
    _google_date_open_selector_hint_is_plausible,
    _google_date_tokens,
    _google_default_date_reference_year,
    _google_deeplink_page_state_recovery_policy,
    _google_deeplink_probe_status,
    _google_deeplink_recovery_plan,
    _google_display_locale_hint_from_browser,
    _google_display_locale_hint_from_url,
    _google_flights_after_search_ready,
    _google_force_bind_dest_selectors,
    _google_force_bind_flights_tab_selectors,
    _google_force_bind_location_input_selectors,
    _google_form_text_looks_date_like,
    _google_form_text_looks_instructional_noise,
    _google_has_contextual_price_card,
    _google_has_iata_token,
    _google_has_results_shell_for_context,
    _google_missing_roles_from_reason,
    _google_non_flight_scope_repair_plan,
    _google_quick_page_class,
    _google_recovery_collab_limits_from_thresholds,
    _google_results_itinerary_matches_expected,
    _google_role_i18n_token_bank,
    _google_role_tokens,
    _google_route_alias_tokens,
    _google_route_context_matches,
    _google_route_core_only_recovery_plan,
    _google_route_fill_input_selector_hint_is_plausible,
    _google_route_reset_selectors,
    _google_search_selector_hint_is_plausible,
    _google_selector_locale_markers,
    _google_should_suppress_force_bind_after_date_failure,
    _google_step_trace_local_date_open_failure,
    _google_step_trace_route_fill_roles_ok,
    _label_click_selectors,
    _normalize_google_form_date_text,
    _selector_candidates,
    _service_fill_activation_clicks,
    _service_fill_activation_keywords,
    _strip_nonvisible_html,
    _verification_confidence_rank,
    _google_route_activation_selectors,
)
from core.scenario_runner.google_flights.core_functions import (
    _assess_google_flights_fill_mismatch,
    _extract_google_flights_form_state,
    _google_form_candidates_from_html,
    _google_form_role_tokens,
    _google_form_value_matches_airport,
    _google_form_value_matches_date,
    _google_origin_looks_unbound,
    _google_origin_needs_iata_support,
    _is_google_dest_placeholder,
    _profile_localized_list,
    _profile_role_list,
)
from core.scenario.types import ActionBudget, StepResult
from core.scope_reconciliation import evaluate_irrelevant_page_downgrade
from core.service_ui_profiles import get_service_ui_profile, profile_role_token_list
from core.services import is_supported_service
from core.scenario_recovery_collab import (
    try_recovery_collab_followup as _try_recovery_collab_followup_impl,
)
from core.scenario_runner.route_recovery_helpers import (
    _site_recovery_collab_focus_plan_dispatch,
    _site_recovery_collab_limits_from_thresholds_dispatch,
    _site_recovery_collab_scope_repair_plan_dispatch,
    _site_recovery_collab_trigger_reason_dispatch,
    _site_recovery_pre_date_gate_dispatch,
    _site_recovery_pre_date_gate_canonical_reason_dispatch,
    _site_should_attempt_recovery_collab_after_date_failure_dispatch,
)
from core.ui_tokens import (
    build_button_text_selectors,
    is_placeholder,
    normalize_visible_text,
    prioritize_tokens,
)
from llm.code_model import (
    analyze_page_ui_with_vlm,
    analyze_filled_route_with_vlm,
    assess_trip_product_scope_with_llm,
)
from storage.knowledge_store import get_knowledge, record_failure, record_success
from storage.shared_knowledge_store import get_airport_aliases_for_provider
from storage.plan_store import get_plan, get_plan_notes, save_plan
from utils.dom_diff import dom_changed
from utils.date_text import parse_english_month_day_text
from utils.evidence import write_service_evidence_checkpoint
from utils.graph_policy_stats import GraphPolicyStats
from utils.knowledge_rules import (
    get_tokens,
    get_knowledge_rule_tokens,
)
from utils.logging import get_logger
from utils.run_paths import get_artifacts_dir, get_run_dir, normalize_run_id, write_latest_run_id
from utils.selector_hints import (
    get_selector_hints,
    promote_selector_hint,
    quarantine_selector_hint,
    record_selector_hint_failure,
)
from utils.thresholds import get_threshold

# Extracted Google Flights functions
from core.scenario_runner.google_flights_helpers import (
    _detect_site_interstitial_block,
    _should_attempt_google_deeplink_page_state_recovery,
    _attempt_google_deeplink_page_state_recovery,
    _normalize_google_deeplink_with_mimic,
    _google_deeplink_quick_rebind,
)
from core.scenario_runner.google_flights.route_bind import (
    _build_route_state_scenario_extract_verdict,
    _google_reconcile_ready_route_bound_consistency,
    _google_turn_fill_success_corroborates_route_bind,
    _build_route_state_return_fallback_payload,
    _bounded_google_mismatch_scan_html,
    _route_mismatch_suspected_verdict,
    _is_route_mismatch_suspected,
    _should_prioritize_google_route_mismatch_rewind,
    _prioritized_google_route_mismatch_rewind_followup,
    _google_force_route_bound_repair_plan,
    _google_force_bind_repair_policy,
    _should_attempt_google_route_mismatch_reset,
    _run_google_route_mismatch_reset,
    _expected_route_values_from_plan,
    _google_route_core_before_date_gate,
    _scope_rewind_followup_plan,
    _soften_recovery_route_fills,
)
from core.scenario_runner.ui_actions_helpers import (
    _google_fill_and_commit_location,
    _google_fill_date_via_picker,
    _google_search_and_commit,
)
from core.scenario_runner.google_flights.smart_escalation import (
    _google_route_fill_smart_escalation_skip_reason,
    _google_search_commit_smart_escalation_skip_reason,
)
from core.scenario_runner.skyscanner import (
    default_skyscanner_plan as _default_skyscanner_plan,
    attempt_skyscanner_interstitial_grace as _attempt_skyscanner_interstitial_grace,
    attempt_skyscanner_interstitial_fallback_reload as _attempt_skyscanner_interstitial_fallback_reload,
    _skyscanner_fill_date_via_picker,
    _skyscanner_fill_and_commit_location,
    _skyscanner_search_click_selectors,
    _skyscanner_dismiss_results_overlay,
    _ensure_skyscanner_flights_context,
    _is_skyscanner_route_value_already_bound_from_url,
    _is_skyscanner_date_value_already_bound_from_url,
)
from core.scenario_runner.budget import (
    budget_remaining_sec as _budget_remaining_sec_impl,
    budget_almost_exhausted as _budget_almost_exhausted_impl,
    wall_clock_cap_exhausted as _wall_clock_cap_exhausted_impl,
)
from core.scenario_runner.execute_helpers import (
    _trace_latest_fill_selector_impl,
    _run_step_action_impl,
    _get_current_page_url_impl,
    _get_step_wall_clock_cap_ms_impl,
    _calculate_remaining_step_timeout_ms_impl,
)
from core.scenario_runner.artifacts_helpers import (
    _write_debug_snapshot,
    _write_progress_snapshot,
    _write_html_snapshot,
    _write_json_artifact_snapshot,
    _write_image_snapshot,
    _write_route_state_debug,
)
from core.scenario_runner.env import (
    _env_bool,
    _env_int,
    _threshold_site_value,
    _debug_exploration_mode,
    _current_mimic_locale,
)
from core.scenario_runner.timeouts import (
    _normalize_selector_timeout_ms,
    _optional_click_timeout_ms,
    _optional_toggle_timeout_ms,
    _wall_clock_cap_reached,
)
# Direct aliases to extracted helper implementations to avoid per-call imports
# Replacing move-only thin wrappers with module-level aliases reduces indirection
# and file length while preserving public/private API symbols used across the file.
from core.scenario_runner.token_utils import (
    load_rule_tokens as _load_rule_tokens,
    compile_token_regex as _compile_token_regex,
)
from core.scenario_runner.io_paths import (
    snapshot_image_path as _snapshot_image_path,
    planner_snapshot_path as _planner_snapshot_path,
)
from core.scenario_runner.selector_utils import (
    check_selector_visibility as _check_selector_visibility,
    visible_selector_subset as _visible_selector_subset,
    selector_blob as _selector_blob,
    contains_selector_word as _contains_selector_word,
    selectors_look_search_submit as _selectors_look_search_submit,
    selectors_look_post_search_wait as _selectors_look_post_search_wait,
    is_clickable_selector_candidate as _is_clickable_selector_candidate,
    selectors_look_domain_toggle as _selectors_look_domain_toggle,
)
from core.scenario_runner.plan_utils_helpers import (
    _is_valid_plan,
    _plan_has_required_fill_roles,
    _is_actionable_plan,
    _is_irrelevant_contact_fill_step,
    _plan_auth_profile_fill_selectors,
    _prepend_ranked_selectors,
    _maybe_prioritize_fill_steps_from_knowledge,
    _maybe_filter_failed_selectors,
    _reorder_search_selectors_for_locale,
)
from core.scenario_runner.knowledge_helpers import (
    _format_knowledge_hints,
    _compose_global_knowledge_hint,
    _compose_local_knowledge_hint,
    _blocked_selectors_from_knowledge,
    _fill_role_knowledge_key,
    _collect_plugin_readiness_hints,
)
from core.scenario_runner.selectors_helpers import (
    _selector_probe_css_compatible,
    _compact_selector_dom_probe,
    _looks_non_fillable_selector_blob,
    _fill_selector_priority,
    _prioritize_fill_selectors,
    _filter_blocked_selectors,
)
from core.scenario_runner.plan_hygiene_helpers import (
    _infer_fill_role,
    _annotate_fill_roles,
    _retarget_plan_inputs,
    _reconcile_fill_plan_roles_and_values,
    _compatible_for_role_impl,
    _plan_semantic_fill_mismatches,
)
from core.scenario_runner.run_agentic_helpers import (
    _route_probe_for_html_impl,
    _write_evidence_checkpoint_impl,
)
from core.scenario_runner.run_agentic_vision_helpers import (
    _run_vision_page_kind_probe_impl,
    _apply_vision_page_kind_hints_impl,
)
from core.scenario_runner.graph_trace import (
    GraphTransitionContext,
    record_graph_transition_impl,
)
from core.scenario_runner.evidence import (
    EvidenceContext,
    write_before_search_evidence_impl,
)
from core.scenario_runner.scenario_return import (
    ReturnBuilderContext,
    scenario_return_impl,
)
from core.scenario_runner.google_recovery_collab import (
    GoogleRecoveryCollabContext,
    google_recovery_collab_followup_impl,
)


log = get_logger(__name__)

# Register site adapters for config-driven UI driver selection
# INV-ADAPTER-001: Enforce agent-first with legacy fallback
_registry = get_global_registry()
_registry.register_agent_adapter("google_flights", GoogleFlightsAgentAdapter)
_registry.register_legacy_adapter("google_flights", GoogleFlightsLegacyAdapter)
_registry.register_agent_adapter("skyscanner", SkyscannerAgentAdapter)

DEFAULT_DEBUG_PATH = Path("storage/scenario_last_error.json")
DEFAULT_HTML_DEBUG_DIR = Path("storage/debug_html")
DEFAULT_ROUTE_DEBUG_DIR = Path("storage/debug")
DEFAULT_SCENARIO_MAX_RETRIES = int(get_threshold("scenario_max_retries", 4))
DEFAULT_SCENARIO_MAX_TURNS = int(get_threshold("scenario_max_turns", 2))
_PRICE_TOKEN_RE = re.compile(
    r"(?:¥\s*\d[\d,]*|\$\s*\d[\d,]*|€\s*\d[\d,]*|£\s*\d[\d,]*|"
    r"JPY\s*\d[\d,]*|USD\s*\d[\d,]*|EUR\s*\d[\d,]*|GBP\s*\d[\d,]*)",
    re.IGNORECASE,
)
_DATE_VALUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IATA_VALUE_RE = re.compile(r"^[A-Za-z]{3}$")
_IATA_TOKEN_RE = re.compile(r"\b[A-Z]{3}\b")
_DATE_LITERAL_RE = re.compile(
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b|\b\d{1,2}/\d{1,2}\b|\d{1,2}月\d{1,2}日"
)
_IATA_TOKEN_IGNORE = {
    "THE",
    "AND",
    "FOR",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
}






_PLUGIN_SCENARIO_HINTS: Dict[str, Dict[str, Any]] = {}



# Pattern matching tokens for result page detection.
# Loaded from configs/knowledge_rules.yaml (tokens.hints.results) with deterministic fallback.
# Fallback ensures bounded behavior when config is unavailable.
_RESULT_HINT_RE = _compile_token_regex(
    _load_rule_tokens(
        group="hints",
        key="results",
        fallback=(
            "flight",
            "flights",
            "itinerary",
            "result",
            "results",
            "search result",
            "運賃",
            "料金",
            "便",
            "検索結果",
            "最安",
        ),
    )
)
# Pattern matching tokens for route field detection.
# Loaded from configs/knowledge_rules.yaml (tokens.hints.route_fields) with deterministic fallback.
# Fallback ensures bounded behavior when config is unavailable.
_ROUTE_FIELD_HINT_RE = _compile_token_regex(
    _load_rule_tokens(
        group="hints",
        key="route_fields",
        fallback=(
            "where from",
            "where to",
            "origin",
            "destination",
            "from",
            "to",
            "depart",
            "departure",
            "return",
            "出発地",
            "出発空港",
            "出発日",
            "目的地",
            "到着地",
            "到着空港",
            "復路",
            "帰り",
            "帰路",
        ),
    )
)
# Pattern matching tokens for auth/contact field detection.
# Loaded from configs/knowledge_rules.yaml (tokens.hints.auth) with deterministic fallback.
# Fallback ensures bounded behavior when config is unavailable.
_CONTACT_AUTH_HINT_RE = _compile_token_regex(
    _load_rule_tokens(
        group="hints",
        key="auth",
        fallback=(
            "email",
            "e-mail",
            "password",
            "passcode",
            "phone",
            "mobile",
            "tel",
            r"full[\s_-]*name",
            r"first[\s_-]*name",
            r"last[\s_-]*name",
            "surname",
            r"given[\s_-]*name",
            "login",
            r"log[\s_-]*in",
            r"sign[\s_-]*in",
            "signin",
            r"sign[\s_-]*up",
            "signup",
            "register",
            "account",
            "newsletter",
            "subscribe",
            "member",
            "メール",
            "氏名",
            "お名前",
            "電話",
            "パスワード",
            "ログイン",
            "会員",
        ),
    ),
    escape_literals=False,
)
# Pattern matching tokens for Google Maps scope detection.
# Loaded from configs/knowledge_rules.yaml (tokens.google.non_flight_map) with deterministic fallback.
_GOOGLE_SCOPE_MAP_TOKENS = tuple(
    token.lower()
    for token in _load_rule_tokens(
        group="google",
        key="non_flight_map",
        fallback=(
            "地図を表示",
            "リストを表示",
            "地図データ",
            "gmp-internal-camera-control",
        ),
    )
)
# Pattern matching tokens for Google hotel/package scope detection.
# Loaded from configs/knowledge_rules.yaml (tokens.google.non_flight_hotel) with deterministic fallback.
_GOOGLE_SCOPE_HOTEL_TOKENS = tuple(
    token.lower()
    for token in _load_rule_tokens(
        group="google",
        key="non_flight_hotel",
        fallback=(
            "hotel",
            "hotels",
            "ホテル",
            "宿泊",
            "check-in",
            "check out",
            "チェックイン",
        ),
    )
)


def _set_plugin_scenario_hints(site_key: str, hints: Optional[Dict[str, Any]]) -> None:
    """Store per-site optional plugin scenario hints for lightweight consumers."""
    site = (site_key or "").strip().lower()
    if not site:
        return
    _PLUGIN_SCENARIO_HINTS[site] = dict(hints) if isinstance(hints, dict) else {}


def _is_fill_value_already_bound(html: str, *, role: str, value: str) -> bool:
    """Best-effort check whether a failed fill target already reflects the intended value."""
    if not isinstance(html, str) or not html:
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    cleaned = _strip_nonvisible_html(html)
    if role in {"depart", "return"}:
        # Date chips can be localized; match common variants for the requested ISO date.
        tokens = _google_date_tokens(value.strip())
        return any(token in cleaned for token in tokens)
    if role in {"origin", "dest"}:
        upper = cleaned.upper()
        aliases = get_airport_aliases_for_provider(value.strip(), "google_flights")
        if not aliases:
            aliases = {value.strip().upper()}
        return _contains_any_token(cleaned, upper, aliases)
    return False


def _default_plan_for_service(
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
    is_domestic: bool = False,
    knowledge=None,
):
    """Return a service-specific fallback interaction plan."""
    knowledge = knowledge or {}
    builders = {
        "google_flights": _default_google_flights_plan,
        "skyscanner": _default_skyscanner_plan,
    }
    builder = builders.get((site_key or "").strip().lower())
    if builder:
        if (site_key or "").strip().lower() == "skyscanner":
            built_plan = builder(origin, dest, depart, return_date)
        else:
            built_plan = builder(origin, dest, depart)
        return _with_knowledge(
            built_plan,
            site_key,
            is_domestic,
            knowledge,
        )
    return []


def execute_plan(
    browser,
    plan,
    site_key: str = None,
    blocked_selectors=None,
    *,
    router=None,
    evidence_ctx: Optional[Dict[str, Any]] = None,
    graph_stats: Optional["GraphPolicyStats"] = None,
    attempt: int = 0,
    turn: int = 0,
    page_kind: str = "unknown",
    locale: str = "",
):
    """Run a sequence of browser actions generated by the scenario planner.

    Args:
        graph_stats: Optional GraphPolicyStats instance for recording transitions (gated by config).
        attempt: Scenario retry attempt number (0-indexed).
        turn: Plan generation turn within attempt (0-indexed).
        page_kind: Page classification (e.g., "search_form", "search_results").
        locale: Locale string (e.g., "ja-JP").
    """
    if not _is_valid_plan(plan):
        raise ValueError("Invalid action plan format")
    suspicious_fill_selectors = _plan_auth_profile_fill_selectors(plan)
    if suspicious_fill_selectors:
        raise ValueError(
            "Rejected plan with auth/profile fill selectors: "
            f"{suspicious_fill_selectors[:5]}"
        )
    semantic_mismatches = _plan_semantic_fill_mismatches(plan)
    if semantic_mismatches:
        raise ValueError(
            "Rejected plan with semantic fill mismatches: "
            f"{semantic_mismatches[:3]}"
        )

    fill_fallback_prepend = bool(
        _threshold_site_value("scenario_fill_fallback_prepend", site_key, False)
    )
    prioritize_fill_selectors = bool(
        _threshold_site_value("scenario_prioritize_fill_selectors", site_key, True)
    )
    max_selector_candidates = int(
        _threshold_site_value(
            "max_selector_candidates",
            site_key,
            int(get_threshold("max_selector_candidates", 12)),
        )
    )
    action_selector_timeout_ms = int(
        _threshold_site_value(
            "browser_action_selector_timeout_ms",
            site_key,
            int(get_threshold("browser_action_selector_timeout_ms", 4000)),
        )
    )
    wait_selector_timeout_ms = int(
        _threshold_site_value(
            "browser_wait_selector_timeout_ms",
            site_key,
            int(get_threshold("browser_wait_selector_timeout_ms", 4000)),
        )
    )
    enforce_single_selector_timeout = bool(
        _threshold_site_value(
            "browser_enforce_selector_timeout_single_candidate",
            site_key,
            False,
        )
    )
    allow_type_active_recovery = bool(
        _threshold_site_value(
            "scenario_fill_type_active_recovery_enabled",
            site_key,
            (site_key or "").strip().lower() != "google_flights",
        )
    )
    allow_date_type_active_recovery = bool(
        _threshold_site_value(
            "scenario_fill_date_type_active_recovery_enabled",
            site_key,
            True,
        )
    )
    step_selector_budget_reserve_ms = int(
        _threshold_site_value(
            "scenario_step_selector_budget_reserve_ms",
            site_key,
            int(get_threshold("scenario_step_selector_budget_reserve_ms", 3000)),
        )
    )
    verify_after_fill_enabled = bool(
        _threshold_site_value(
            "scenario_google_flights_verify_after_fill_enabled",
            site_key,
            (site_key or "").strip().lower() == "google_flights",
        )
    )
    verify_after_fill_fail_closed = bool(
        _threshold_site_value(
            "scenario_google_flights_verify_after_fill_fail_closed",
            site_key,
            True,
        )
    )
    verify_after_fill_min_confidence = str(
        _threshold_site_value(
            "scenario_google_flights_verify_min_confidence",
            site_key,
            "medium",
        )
        or "medium"
    ).strip().lower()
    expected_route_values = _expected_route_values_from_plan(plan)
    evidence_enabled = bool((evidence_ctx or {}).get("enabled", False))
    evidence_run_id = str((evidence_ctx or {}).get("run_id", "") or "")
    evidence_service = str((evidence_ctx or {}).get("service", site_key or "") or "")
    evidence_checkpoint = str(
        (evidence_ctx or {}).get("checkpoint_before_search", "after_fills_before_search")
        or "after_fills_before_search"
    )
    evidence_url = str((evidence_ctx or {}).get("url", "") or "")
    google_recovery_route_core_gate_enabled = bool(
        (evidence_ctx or {}).get("google_recovery_route_core_gate_enabled", False)
    )

    step_trace = []
    exec_ctx = ExecutePlanContext(
        browser=browser,
        site_key=site_key,
        graph_stats=graph_stats,
        attempt=attempt,
        turn=turn,
        page_kind=page_kind,
        locale=locale,
        step_trace=step_trace,
        evidence_enabled=evidence_enabled,
        evidence_run_id=evidence_run_id,
        evidence_service=evidence_service,
        evidence_checkpoint=evidence_checkpoint,
        evidence_url=evidence_url,
        expected_route_values=expected_route_values,
        get_threshold_fn=get_threshold,
        threshold_site_value_fn=_threshold_site_value,
        selector_candidates_fn=_selector_candidates,
        current_mimic_locale_fn=_current_mimic_locale,
        prioritize_tokens_fn=prioritize_tokens,
        get_tokens_fn=get_tokens,
        compact_selector_dom_probe_fn=_compact_selector_dom_probe,
        write_json_artifact_snapshot_fn=_write_json_artifact_snapshot,
        write_google_date_selector_probe_fn=write_google_date_selector_probe,
        get_current_page_url_impl_fn=_get_current_page_url_impl,
        dom_route_bind_probe_fn=dom_route_bind_probe,
    )
    skyscanner_last_overlay_probe_at = 0.0

    for idx, step in enumerate(plan):
        # Wall clock guard: track step start time and enforce hard cap
        step_start = time.monotonic()
        action = step.get("action")
        step_wall_clock_cap_ms = exec_ctx.step_wall_clock_cap_ms(action)
        step_deadline = None
        if step_wall_clock_cap_ms and step_wall_clock_cap_ms > 0:
            step_deadline = step_start + (max(1, int(step_wall_clock_cap_ms)) / 1000.0)
        selectors = _selector_candidates(step.get("selector"))
        role = _infer_fill_role(step) if action == "fill" else None

        # Log step start
        log.info(
            "scenario.step.start step_index=%d action=%s role=%s selectors=%s",
            idx,
            action or "unknown",
            role or "none",
            ",".join(selectors[:3]) if selectors else "none",
        )

        fill_commit_meta: Dict[str, Any] = {}
        if action == "fill":
            role = _infer_fill_role(step)
            if role and site_key:
                merged = []
                fill_fallbacks = _service_fill_fallbacks(site_key, role)
                candidate_chain = (
                    fill_fallbacks + selectors
                    if fill_fallback_prepend
                    else selectors + fill_fallbacks
                )
                for selector in candidate_chain:
                    if selector not in merged:
                        merged.append(selector)
                selectors = merged
            if prioritize_fill_selectors:
                selectors = _prioritize_fill_selectors(selectors)

        selectors = _filter_blocked_selectors(selectors, blocked_selectors)

        if action == "click" and step.get("optional") and selectors:
            visible_selectors = _visible_selector_subset(
                browser,
                selectors,
                per_selector_timeout_ms=100,
                max_candidates=3,
            )
            if visible_selectors:
                selectors = visible_selectors
            else:
                log.info(
                    "scenario.step.click_optional_visibility_skip selectors=%s",
                    selectors[:3] if selectors else [],
                )
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": None,
                        "status": "soft_fail",
                        "error": "selector_not_visible",
                    }
                )
                exec_ctx.record_graph_transition(
                    idx, action, None, "", "soft_fail", "selector_not_visible", elapsed_ms
                )
                continue

        # Hardening: if Google date fill already soft-failed locally in this turn,
        # skip downstream Search/Wait plan steps to avoid pointless search retries,
        # reloads, and expensive VLM/planner churn triggered by a known local date
        # verification ambiguity/failure.
        google_recent_local_date_failure = bool(exec_ctx.google_recent_local_date_failure_in_turn())
        skyscanner_recent_local_date_failure = has_recent_skyscanner_date_failure_in_turn(
            site_key=site_key,
            step_trace=step_trace,
        )
        has_recent_local_date_failure = bool(
            google_recent_local_date_failure or skyscanner_recent_local_date_failure
        )
        if has_recent_local_date_failure:
            if skyscanner_recent_local_date_failure and action == "wait":
                # Fail closed on Skyscanner after local date-fill failure: avoid
                # waiting on result surfaces for a search that should not proceed.
                skip_reason = "skip_wait_after_local_date_fail"
            else:
                skip_reason = soft_skip_after_recent_date_failure(
                    action=action,
                    selectors=selectors,
                    has_recent_date_failure=True,
                    selectors_look_search_submit_fn=_selectors_look_search_submit,
                    selectors_look_post_search_wait_fn=_selectors_look_post_search_wait,
                )
            if skip_reason == "skip_search_after_local_date_fail":
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.%s_date.skip_search_after_local_date_fail selectors=%s",
                    (site_key or "").strip().lower() or "local",
                    selectors[:3] if selectors else [],
                )
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": None,
                        "status": "soft_skip",
                        "error": "skip_search_after_local_date_fail",
                    }
                )
                exec_ctx.record_graph_transition(
                    idx, action, None, "", "soft_skip", "skip_search_after_local_date_fail", elapsed_ms
                )
                continue
            if skip_reason == "skip_wait_after_local_date_fail":
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.%s_date.skip_wait_after_local_date_fail selectors=%s",
                    (site_key or "").strip().lower() or "local",
                    selectors[:3] if selectors else [],
                )
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": None,
                        "status": "soft_skip",
                        "error": "skip_wait_after_local_date_fail",
                    }
                )
                exec_ctx.record_graph_transition(
                    idx, action, None, "", "soft_skip", "skip_wait_after_local_date_fail", elapsed_ms
                )
                continue

        if max_selector_candidates > 0:
            selectors = selectors[:max_selector_candidates]
        last_step_error = None
        per_selector_timeout_ms = None
        if len(selectors) > 1:
            if action == "wait":
                per_selector_timeout_ms = wait_selector_timeout_ms
            elif action in ("fill", "click"):
                per_selector_timeout_ms = action_selector_timeout_ms
                if action == "click" and step.get("optional"):
                    per_selector_timeout_ms = _optional_click_timeout_ms(site_key or "")
        elif enforce_single_selector_timeout:
            # Some dynamic sites need bounded single-selector probes too.
            if action in ("fill", "click"):
                per_selector_timeout_ms = action_selector_timeout_ms
                if action == "click" and step.get("optional"):
                    per_selector_timeout_ms = _optional_click_timeout_ms(site_key or "")
            elif action == "wait":
                per_selector_timeout_ms = wait_selector_timeout_ms

        effective_step_timeout_ms = _normalize_selector_timeout_ms(
            per_selector_timeout_ms,
            site_key=site_key or "",
            action=action or "",
        )
        # Prevent selector fan-out from consuming the whole step wall-clock cap.
        if (
            selectors
            and isinstance(step_wall_clock_cap_ms, int)
            and step_wall_clock_cap_ms > 0
            and isinstance(effective_step_timeout_ms, int)
            and effective_step_timeout_ms > 0
        ):
            selector_budget_ms = max(0, int(step_wall_clock_cap_ms) - max(0, int(step_selector_budget_reserve_ms)))
            if selector_budget_ms > 0:
                max_by_budget = max(1, int(selector_budget_ms // max(1, int(effective_step_timeout_ms))))
                if len(selectors) > max_by_budget:
                    selectors = selectors[:max_by_budget]
        route_verify_meta = None
        used_selector = None

        def _raise_if_step_timed_out(stage: str) -> None:
            if step_deadline is None:
                return
            elapsed_ms = int((time.monotonic() - step_start) * 1000)
            if elapsed_ms >= step_wall_clock_cap_ms:
                raise RuntimeError(
                    f"Step exceeded wall clock cap: action={action} role={role} "
                    f"selectors={selectors[:3] if selectors else []} "
                    f"used_selector={used_selector or 'none'} "
                    f"timeout_ms={effective_step_timeout_ms} elapsed_ms={elapsed_ms} "
                    f"cap_ms={step_wall_clock_cap_ms} stage={stage}"
                )

        def _remaining_step_timeout_ms() -> Optional[int]:
            return _calculate_remaining_step_timeout_ms_impl(
                step_deadline, effective_step_timeout_ms, _raise_if_step_timed_out
            )

        # Wall clock guard: check if step has exceeded cap before executing action
        _raise_if_step_timed_out("pre_action")

        if (site_key or "").strip().lower() == "skyscanner":
            page_obj = getattr(browser, "page", None)
            current_url = str(getattr(page_obj, "url", "") or "").strip().lower()
            if "/hotels" in current_url:
                recovery = _ensure_skyscanner_flights_context(
                    browser,
                    timeout_ms=int(get_threshold("skyscanner_hotels_recovery_timeout_ms", 4000) or 4000),
                )
                recovered_url = str(getattr(page_obj, "url", "") or "").strip().lower()
                log.warning(
                    "scenario.skyscanner.hotels_context_recovery step_index=%s action=%s ok=%s reason=%s before=%s after=%s",
                    idx,
                    action or "unknown",
                    bool((recovery or {}).get("ok", False)),
                    str((recovery or {}).get("reason", "") or ""),
                    current_url[:220],
                    recovered_url[:220],
                )
                current_url = recovered_url
                if "/hotels" in current_url and not bool((recovery or {}).get("ok", False)):
                    elapsed_ms = int((time.monotonic() - step_start) * 1000)
                    step_trace.append(
                        {
                            "index": idx,
                            "action": action,
                            "role": role,
                            "selectors": list(selectors),
                            "used_selector": None,
                            "status": "soft_fail",
                            "error": "skyscanner_hotels_context_recovery_failed",
                        }
                    )
                    exec_ctx.record_graph_transition(
                        idx,
                        action,
                        role,
                        "",
                        "soft_fail",
                        "skyscanner_hotels_context_recovery_failed",
                        elapsed_ms,
                    )
                    log.info(
                        "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                        idx,
                        action or "unknown",
                        elapsed_ms,
                    )
                    continue
            if "/sttc/px/captcha-v2/" in current_url or "captcha-v2/index.html" in current_url:
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": None,
                        "status": "soft_skip",
                        "error": "skip_step_on_interstitial_surface",
                    }
                )
                exec_ctx.record_graph_transition(
                    idx,
                    action,
                    role,
                    "",
                    "soft_skip",
                    "skip_step_on_interstitial_surface",
                    elapsed_ms,
                )
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                continue
            if "/transport/flights/" in current_url:
                now_mono = time.monotonic()
                min_probe_interval_ms = max(
                    400,
                    min(
                        2500,
                        int(get_threshold("skyscanner_results_overlay_probe_interval_ms", 1200) or 1200),
                    ),
                )
                if (
                    skyscanner_last_overlay_probe_at <= 0.0
                    or (now_mono - skyscanner_last_overlay_probe_at) * 1000 >= min_probe_interval_ms
                ):
                    skyscanner_last_overlay_probe_at = now_mono
                    overlay_meta = _skyscanner_dismiss_results_overlay(
                        browser=browser,
                        timeout_ms=int(get_threshold("skyscanner_results_overlay_dismiss_timeout_ms", 700) or 700),
                        max_clicks=int(get_threshold("skyscanner_results_overlay_dismiss_max_clicks", 2) or 2),
                    )
                    log.info(
                        "scenario.skyscanner.results_overlay_dismiss ok=%s reason=%s selector=%s",
                        bool((overlay_meta or {}).get("ok")),
                        str((overlay_meta or {}).get("reason", "") or ""),
                        str((overlay_meta or {}).get("selector_used", "") or ""),
                    )

        if (
            (site_key or "").strip().lower() == "skyscanner"
            and action in {"wait", "click"}
            and _selectors_look_search_submit(selectors)
            and not _selectors_look_post_search_wait(selectors)
        ):
            page_obj = getattr(browser, "page", None)
            current_url = str(getattr(page_obj, "url", "") or "").strip().lower()
            if "/transport/flights/" in current_url:
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.skyscanner.search_controls.skip_on_results_url action=%s url=%s",
                    action,
                    current_url[:240],
                )
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": None,
                        "status": "soft_skip",
                        "error": "skip_search_controls_on_results_url",
                    }
                )
                exec_ctx.record_graph_transition(
                    idx,
                    action,
                    role,
                    "",
                    "soft_skip",
                    "skip_search_controls_on_results_url",
                    elapsed_ms,
                )
                continue

        if (
            action == "fill"
            and (site_key or "").strip().lower() == "skyscanner"
            and role in {"origin", "dest"}
        ):
            if _is_skyscanner_route_value_already_bound_from_url(
                browser,
                role=role,
                value=str(step.get("value", "") or ""),
            ):
                fill_commit_meta = {
                    "ok": True,
                    "reason": "route_url_already_bound",
                    "selector_used": "url_route_bind",
                    "evidence": {"role": role},
                }
                used_selector = "url_route_bind"
                last_step_error = None
            else:
                fill_commit_meta = _skyscanner_fill_and_commit_location(
                    browser=browser,
                    role=role,
                    value=str(step.get("value", "") or ""),
                    selectors=[str(s) for s in list(selectors or []) if isinstance(s, str)],
                    timeout_ms=_remaining_step_timeout_ms(),
                )
                used_selector = str(fill_commit_meta.get("selector_used", "") or "")
                if bool(fill_commit_meta.get("ok")):
                    last_step_error = None
                else:
                    reason = str(fill_commit_meta.get("reason", "") or "skyscanner_route_fill_failed")
                    last_step_error = RuntimeError(reason)
                    if router is not None:
                        router.record_event(
                            "ui_commit_failed",
                            role=role,
                            selector=used_selector,
                            reason=reason,
                        )
        elif (
            action == "fill"
            and (site_key or "").strip().lower() == "google_flights"
            and role in {"origin", "dest"}
        ):
            # Timeout/unit-test stubs may not implement the bounded combobox helper.
            # For non-optional route fills, fall back to generic fill execution in that case.
            if not hasattr(browser, "fill_google_flights_combobox") and not bool(step.get("optional")):
                last_step_error, used_selector = exec_ctx.run_step_action(
                    "fill",
                    selectors,
                    value=step.get("value"),
                    timeout_ms=_remaining_step_timeout_ms(),
                )
                fill_commit_meta = {}
            else:
                if bool(step.get("force_bind_commit")):
                    # Skip fill/commit if already bound correctly
                    expected_value = str(step.get("value", "") or "").strip().upper()
                    probe_target = getattr(browser, "page", None) or browser
                    try:
                        form_state = _extract_google_flights_form_state(probe_target)
                        observed_value = str(form_state.get(role, "") or "").strip().upper()
                        if observed_value and expected_value and observed_value == expected_value:
                            log.debug(
                                "google_flights.route_skip role=%s already_bound=%s",
                                role,
                                expected_value,
                            )
                            used_selector = "skip_already_bound"
                            last_step_error = None
                            # Skip to next step
                            step_trace.append(
                                {
                                    "index": idx,
                                    "action": action,
                                    "selectors": list(selectors),
                                    "used_selector": used_selector,
                                    "status": "skip_already_bound",
                                }
                            )
                            continue
                    except Exception:
                        # If can't read form state, proceed with fill/commit
                        pass

                # Route all Google route-field fills through the bounded combobox helper.
                # Raw browser.fill() on [role='combobox'] containers can report success while
                # leaving placeholder/unbound route state (observed in deeplink recovery runs).
                fill_commit_meta = _google_fill_and_commit_location(
                    browser,
                    role=role,
                    value=str(step.get("value", "") or ""),
                    selectors=selectors,
                    locale_hint=_current_mimic_locale(),
                    timeout_ms=_remaining_step_timeout_ms(),
                    deadline=step_deadline,
                    debug_run_id=evidence_run_id,
                    debug_attempt=attempt,
                    debug_turn=turn,
                    debug_step_index=idx,
                    expected_origin=str(expected_route_values.get("origin", "") or ""),
                    expected_depart=str(expected_route_values.get("depart", "") or ""),
                    expected_return=str(expected_route_values.get("return", "") or ""),
                )
                used_selector = str(fill_commit_meta.get("selector_used", "") or "")
                if bool(fill_commit_meta.get("ok")):
                    last_step_error = None
                else:
                    reason = str(fill_commit_meta.get("reason", "") or "google_fill_commit_failed")
                    last_step_error = RuntimeError(reason)
                    # Record ui_commit_failed event if commit specifically failed
                    if router is not None and "commit" in reason.lower():
                        router.record_event(
                            "ui_commit_failed",
                            role=role,
                            selector=used_selector,
                            reason=reason,
                        )
        elif (
            action == "fill"
            and (site_key or "").strip().lower() == "google_flights"
            and role in {"depart", "return"}
        ):
            if should_skip_return_fill_after_depart_failure(
                role=role,
                step_optional=bool(step.get("optional")),
                step_trace=step_trace,
            ):
                log.info("scenario.google_date.skip_return_after_depart_fail role=return")
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "soft_skip",
                        "error": "skip_return_after_depart_fail",
                    }
                )
                continue

            if google_recovery_route_core_gate_enabled:
                current_html = ""
                try:
                    current_html = str(browser.content() or "")
                except Exception:
                    current_html = ""
                route_core_gate = _site_recovery_pre_date_gate_dispatch(
                    site_key=site_key or "",
                    html=current_html,
                    page=getattr(browser, "page", None),
                    expected_origin=expected_route_values.get("origin", ""),
                    expected_dest=expected_route_values.get("dest", ""),
                    expected_depart=expected_route_values.get("depart", ""),
                    expected_return=expected_route_values.get("return", ""),
                    google_gate_fn=_google_route_core_before_date_gate,
                )
                if not bool(route_core_gate.get("ok")):
                    gate_reason = str(route_core_gate.get("reason", "") or "route_core_unverified")
                    canonical_reason = _site_recovery_pre_date_gate_canonical_reason_dispatch(site_key or "")
                    log.warning(
                        "scenario.google_recovery.route_core_gate.block role=%s reason=%s",
                        role,
                        gate_reason,
                    )
                    fill_commit_meta = {
                        "ok": False,
                        "reason": canonical_reason,
                        "selector_used": "",
                        "evidence": dict(route_core_gate.get("evidence", {}) or {}),
                    }
                    used_selector = ""
                    last_step_error = RuntimeError(canonical_reason)
                    step_trace.append(
                        {
                            "index": idx,
                            "action": action,
                            "role": role,
                            "selectors": list(selectors),
                            "used_selector": used_selector,
                            "status": canonical_reason,
                            "error": str(last_step_error),
                            "evidence": fill_commit_meta.get("evidence", {}),
                            "fill_commit": dict(fill_commit_meta),
                        }
                    )
                    if router is not None:
                        router.record_event(
                            "ui_date_fill_failed",
                            role=role,
                            selector="",
                            reason=canonical_reason,
                        )
                    continue

            # Use new gf_set_date for Google Flights date fields
            # Hard-bounded date picker with ActionBudget guards
            step_budget = ActionBudget(max_actions=20)  # Conservative budget for date operations
            date_fill_value = str(step.get("value", "") or "")
            date_display_lang_hint = _google_display_locale_hint_from_browser(browser)
            date_locale_hint = str(locale or _current_mimic_locale() or "").strip().lower()
            date_fill_selectors = [str(s) for s in list(selectors or []) if isinstance(s, str)]
            date_fill_selectors = _selector_hints_overlay(
                date_fill_selectors,
                site="google_flights",
                action="date_open",
                role=str(role or ""),
                display_lang=date_display_lang_hint,
                locale_hint=date_locale_hint,
                max_hints=2,
                hint_allow=lambda s, rk=str(role or ""): _google_date_open_selector_hint_is_plausible(rk, s),
            )

            def _google_date_debug_probe_callback(stage_label: str, payload: Dict[str, Any]) -> None:
                return create_google_date_debug_probe_callback(
                    evidence_run_id,
                    role,
                    date_fill_value,
                    date_fill_selectors,
                    exec_ctx.debug_google_date_selector_probe,
                )(stage_label, payload)

            exec_ctx.debug_google_date_selector_probe(
                stage_label="pre_open",
                role_key=str(role or ""),
                target_value=date_fill_value,
                selectors_for_probe=date_fill_selectors[:10],
                extra={"step_index": int(idx)},
            )

            fill_commit_meta = _gf_set_date_impl(
                browser,
                role=role,
                date=date_fill_value,
                timeout_ms=_remaining_step_timeout_ms(),
                role_selectors=date_fill_selectors,
                locale_hint=(date_display_lang_hint or _current_mimic_locale()),
                budget=step_budget,
                logger=log,
                deadline=step_deadline,
                expected_peer_date=(
                    str(expected_route_values.get("depart", "") or "")
                    if role == "return"
                    else str(expected_route_values.get("return", "") or "")
                ),
                debug_probe_callback=_google_date_debug_probe_callback,
            )
            used_selector = str(fill_commit_meta.get("selector_used", "") or "")
            if bool(fill_commit_meta.get("ok")):
                exec_ctx.debug_google_date_selector_probe(
                    stage_label="post_open",
                    role_key=str(role or ""),
                    target_value=date_fill_value,
                    selectors_for_probe=([used_selector] if used_selector else []) + date_fill_selectors[:10],
                    extra={
                        "ok": True,
                        "selector_used": used_selector,
                        "result_reason": str(fill_commit_meta.get("reason", "") or ""),
                    },
                )
                if used_selector and _google_date_open_selector_hint_is_plausible(str(role or ""), used_selector):
                    try:
                        promote_selector_hint(
                            site="google_flights",
                            action="date_open",
                            role=str(role or ""),
                            selector=used_selector,
                            display_lang=date_display_lang_hint,
                            locale=date_locale_hint,
                            source="runtime_verified",
                        )
                        log.info(
                            "selector_hints.promote site=google_flights action=date_open role=%s selector=%s lang=%s",
                            str(role or ""),
                            used_selector[:120],
                            date_display_lang_hint or "",
                        )
                    except Exception:
                        pass
                elif used_selector:
                    log.info(
                        "selector_hints.promote_skipped site=google_flights action=date_open role=%s selector=%s reason=nonsemantic_selector",
                        str(role or ""),
                        used_selector[:120],
                    )
                last_step_error = None
            else:
                reason = str(fill_commit_meta.get("reason", "") or "google_date_fill_failed")
                last_step_error = RuntimeError(reason)
                fail_probe_selectors: List[str] = []
                evidence = fill_commit_meta.get("evidence", {}) if isinstance(fill_commit_meta, dict) else {}
                if isinstance(evidence, dict):
                    for key in ("calendar.opener_candidate_order", "calendar.opener_candidates", "selectors_tried"):
                        vals = evidence.get(key)
                        if isinstance(vals, list):
                            for item in vals:
                                s = str(item or "").strip()
                                if s and s not in fail_probe_selectors:
                                    fail_probe_selectors.append(s)
                exec_ctx.debug_google_date_selector_probe(
                    stage_label="failed",
                    role_key=str(role or ""),
                    target_value=date_fill_value,
                    selectors_for_probe=(fail_probe_selectors or date_fill_selectors)[:10],
                    extra={
                        "ok": False,
                        "result_reason": reason,
                        "result": dict(fill_commit_meta) if isinstance(fill_commit_meta, dict) else {},
                    },
                )

                # Record the failure with specific reason in step_trace for scenario detection
                # This allows the scenario_runner to distinguish date failures and skip mismatch_reset
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": reason,  # e.g., "calendar_not_open", "month_nav_exhausted", etc.
                        "error": str(last_step_error),
                        "evidence": fill_commit_meta.get("evidence", {}),
                        "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                    }
                )

                # Record ui_date_fill_failed event if commit specifically failed
                if router is not None and "failed" in reason.lower():
                    router.record_event(
                        "ui_date_fill_failed",
                        role=role,
                        selector=used_selector,
                        reason=reason,
                    )
        elif (
            action == "fill"
            and (site_key or "").strip().lower() == "skyscanner"
            and role in {"depart", "return"}
        ):
            if _is_skyscanner_date_value_already_bound_from_url(
                browser,
                role=role,
                value=str(step.get("value", "") or ""),
            ):
                fill_commit_meta = {
                    "ok": True,
                    "reason": "date_url_already_bound",
                    "selector_used": "url_date_bind",
                    "evidence": {"role": role},
                }
                used_selector = "url_date_bind"
                last_step_error = None
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=url_date_bound elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "url_date_bound",
                        "error": "",
                        "fill_commit": dict(fill_commit_meta),
                    }
                )
                continue
            if should_skip_return_fill_after_depart_failure(
                role=role,
                step_optional=bool(step.get("optional")),
                step_trace=step_trace,
            ):
                log.info("scenario.skyscanner_date.skip_return_after_depart_fail role=return")
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_skip elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "soft_skip",
                        "error": "skip_return_after_depart_fail",
                    }
                )
                continue

            fill_commit_meta = _skyscanner_fill_date_via_picker(
                browser=browser,
                role=role,
                date=str(step.get("value", "") or ""),
                timeout_ms=_remaining_step_timeout_ms(),
                role_selectors=[str(s) for s in list(selectors or []) if isinstance(s, str)],
            )
            used_selector = str(fill_commit_meta.get("selector_used", "") or "")
            if bool(fill_commit_meta.get("ok")):
                last_step_error = None
            else:
                reason = str(fill_commit_meta.get("reason", "") or "skyscanner_date_fill_failed")
                last_step_error = RuntimeError(reason)
                if router is not None:
                    router.record_event(
                        "ui_date_fill_failed",
                        role=role,
                        selector=used_selector,
                        reason=reason,
                    )
        elif action == "click" and _selectors_look_search_submit(selectors):
            if (site_key or "").strip().lower() == "skyscanner":
                selectors = _skyscanner_search_click_selectors(selectors)
            form_state = {}
            google_recent_date_failure = exec_ctx.google_recent_local_date_failure_in_turn()
            # Check if date fields have been targeted in this turn to avoid premature warnings
            date_fields_attempted = any(
                step.get("role") in {"depart", "return"}
                for step in step_trace or []
            )
            should_verify_route = bool(
                (site_key or "").strip().lower() == "google_flights"
                and verify_after_fill_enabled
                and date_fields_attempted  # Only verify if date filling was attempted
                and expected_route_values.get("origin")
                and expected_route_values.get("dest")
                and expected_route_values.get("depart")
            )
            if should_verify_route and google_recent_date_failure:
                should_verify_route = False
                log.info(
                    "scenario.route_fill_verify.skipped reason=recent_date_failure role_candidates=%s",
                    "depart,return",
                )
            if should_verify_route:
                probe_target = getattr(browser, "page", None) or browser
                verify_html = ""
                try:
                    verify_html = str(browser.content() or "")
                except Exception:
                    verify_html = ""
                form_state = _extract_google_flights_form_state(probe_target)
                route_verify_meta = _assess_google_flights_fill_mismatch(
                    form_state=form_state,
                    html=verify_html,
                    expected_origin=expected_route_values.get("origin", ""),
                    expected_dest=expected_route_values.get("dest", ""),
                    expected_depart=expected_route_values.get("depart", ""),
                    expected_return=expected_route_values.get("return", ""),
                    min_confidence=verify_after_fill_min_confidence,
                    fail_closed=verify_after_fill_fail_closed,
                )
                if isinstance(route_verify_meta, dict):
                    route_verify_meta["dest_selector_used"] = exec_ctx.trace_latest_fill_selector("dest")
                    route_verify_meta["date_picker_done_clicked"] = exec_ctx.trace_date_done_clicked()
                if bool(route_verify_meta.get("block")):
                    route_verify_meta["dest_refill_attempted"] = 0
                    exec_ctx.write_before_search_evidence(
                        form_state=form_state,
                        route_verify=route_verify_meta,
                    )
                    log.warning(
                        "scenario.route_fill_mismatch expected_origin=%s expected_dest=%s expected_depart=%s expected_return=%s observed_origin=%s observed_dest=%s observed_depart=%s observed_return=%s confidence=%s reason=%s mismatches=%s",
                        expected_route_values.get("origin", ""),
                        expected_route_values.get("dest", ""),
                        expected_route_values.get("depart", ""),
                        expected_route_values.get("return", ""),
                        route_verify_meta.get("observed", {}).get("origin", ""),
                        route_verify_meta.get("observed", {}).get("dest", ""),
                        route_verify_meta.get("observed", {}).get("depart", ""),
                        route_verify_meta.get("observed", {}).get("return", ""),
                        route_verify_meta.get("confidence", ""),
                        route_verify_meta.get("reason", ""),
                        ",".join(route_verify_meta.get("mismatches", []) or []),
                    )
                    # Record route_fill_mismatch event for router
                    if router is not None:
                        router.record_event(
                            "route_fill_mismatch",
                            expected_origin=expected_route_values.get("origin", ""),
                            expected_dest=expected_route_values.get("dest", ""),
                            observed_origin=route_verify_meta.get("observed", {}).get("origin", ""),
                            observed_dest=route_verify_meta.get("observed", {}).get("dest", ""),
                            role="dest",  # Typically dest field mismatches
                        )
                    # Set error with exact message for status matching in trace recording
                    last_step_error = RuntimeError("route_fill_mismatch")
                    used_selector = "route_fill_verify_blocked"
                    # Immediately record trace since this error won't be overwritten
                    elapsed_ms = int((time.monotonic() - step_start) * 1000)
                    step_trace.append(
                        {
                            "index": idx,
                            "action": action,
                            "selectors": list(selectors),
                            "used_selector": used_selector,
                            "status": "route_fill_mismatch",
                            "error": str(last_step_error),
                            "route_verify": route_verify_meta if isinstance(route_verify_meta, dict) else {},
                        }
                    )
                    continue
                else:
                    exec_ctx.write_before_search_evidence(
                        form_state=form_state,
                        route_verify=route_verify_meta,
                    )
                    if (site_key or "").strip().lower() == "google_flights":
                        search_result = _google_search_and_commit(
                            browser,
                            selectors=selectors,
                            timeout_ms=_remaining_step_timeout_ms(),
                            deadline=step_deadline,
                            page_url=exec_ctx.current_page_url_for_search_commit(),
                            origin=str(expected_route_values.get("origin", "") or ""),
                            dest=str(expected_route_values.get("dest", "") or ""),
                            depart=str(expected_route_values.get("depart", "") or ""),
                            return_date=str(expected_route_values.get("return", "") or ""),
                        )
                        if evidence_run_id:
                            _write_google_search_commit_probe_artifact(
                                run_id=evidence_run_id,
                                browser=browser,
                                artifact_label=f"execute_plan_step_{idx}",
                                selectors=list(selectors),
                                search_result=search_result,
                                site_key=site_key or "google_flights",
                                attempt=attempt,
                                turn=turn,
                                step_index=idx,
                                page_url=exec_ctx.current_page_url_for_search_commit(),
                                origin=str(expected_route_values.get("origin", "") or ""),
                                dest=str(expected_route_values.get("dest", "") or ""),
                                depart=str(expected_route_values.get("depart", "") or ""),
                                return_date=str(expected_route_values.get("return", "") or ""),
                                compact_selector_dom_probe_fn=_compact_selector_dom_probe,
                                write_json_artifact_fn=_write_json_artifact_snapshot,
                            )
                        if bool(search_result.get("ok")):
                            last_step_error = None
                            used_selector = str(search_result.get("selector_used", "") or "")
                        else:
                            last_step_error = RuntimeError(
                                search_result.get("error", "search_commit_failed")
                            )
                            used_selector = None
                    else:
                        last_step_error, used_selector = _safe_click_first_match(
                            browser,
                            selectors,
                            timeout_ms=_remaining_step_timeout_ms(),
                            require_clickable=True,
                        )
            else:
                if (site_key or "").strip().lower() == "google_flights":
                    probe_target = getattr(browser, "page", None) or browser
                    try:
                        form_state = _extract_google_flights_form_state(probe_target)
                    except Exception:
                        form_state = {}
                    exec_ctx.write_before_search_evidence(
                        form_state=form_state,
                        route_verify=route_verify_meta,
                    )

                # Pre-visibility check for optional click steps:
                # Skip unavailable optional buttons to avoid wasting time with deadline_exceeded
                click_skip = optional_click_visibility_soft_skip(
                    browser=browser,
                    action=action,
                    step_optional=bool(step.get("optional")),
                    selectors=selectors,
                    check_selector_visibility_fn=_check_selector_visibility,
                    idx=idx,
                    step_start=step_start,
                    log=log,
                )
                if click_skip is not None:
                    step_trace.append(click_skip["trace"])
                    exec_ctx.record_graph_transition(
                        idx, action, None, "", click_skip["status"], click_skip["error"], click_skip["elapsed_ms"]
                    )
                    continue

                last_step_error, used_selector = run_generic_click_action(
                    browser=browser,
                    site_key=(site_key or ""),
                    selectors=selectors,
                    remaining_step_timeout_ms_fn=_remaining_step_timeout_ms,
                    safe_click_first_match_fn=_safe_click_first_match,
                )
        else:
            last_step_error, used_selector = run_generic_fill_or_wait_action(
                exec_ctx=exec_ctx,
                action=action,
                selectors=selectors,
                value=step.get("value"),
                remaining_step_timeout_ms_fn=_remaining_step_timeout_ms,
            )

        _raise_if_step_timed_out("post_action")

        if last_step_error is not None and action == "fill" and not step.get("optional"):
            _raise_if_step_timed_out("pre_recovery")
            role = _infer_fill_role(step)

            # HARD-GATE: For date fields on Google Flights/Skyscanner, do NOT attempt
            # generic recovery fallbacks. These can fan out selector scans and exceed the
            # per-step wall-clock cap after the primary bounded path has already failed.
            # Also skip generic recovery when Google origin/dest force-bind already used the
            # bounded combobox commit helper.
            site_key_norm = (site_key or "").strip().lower()
            skip_recovery = bool(
                (site_key_norm == "google_flights" and role in {"depart", "return"})
                or (site_key_norm == "skyscanner" and role in {"depart", "return"})
                or (
                    site_key_norm == "skyscanner"
                    and role in {"origin", "dest"}
                    and isinstance(fill_commit_meta, dict)
                    and bool(fill_commit_meta)
                    and not bool(fill_commit_meta.get("ok"))
                )
                or (
                    site_key_norm == "google_flights"
                    and role in {"origin", "dest"}
                    and bool(step.get("force_bind_commit"))
                )
            )

            if skip_recovery:
                if site_key_norm == "google_flights" and role in {"origin", "dest"}:
                    log.info(
                        "scenario.google_route_fill.recovery_blocked role=%s reason=%s",
                        role,
                        str(last_step_error),
                    )
                elif site_key_norm == "skyscanner" and role in {"origin", "dest"}:
                    log.info(
                        "scenario.skyscanner_route_fill.recovery_blocked role=%s reason=%s",
                        role,
                        str(last_step_error),
                    )
                elif site_key_norm == "skyscanner" and role in {"depart", "return"}:
                    log.info(
                        "scenario.skyscanner_date.recovery_blocked role=%s reason=%s",
                        role,
                        str(last_step_error),
                    )
                else:
                    log.info(
                        "scenario.google_date.recovery_blocked role=%s reason=%s",
                        role,
                        str(last_step_error),
                    )
                # Do not attempt any fallback recovery for date fields
            elif role and site_key:
                activation_clicks = _service_fill_activation_clicks(site_key, role)
                activation_timeout_ms = _remaining_step_timeout_ms()
                if activation_timeout_ms is None:
                    activation_timeout_ms = int(
                        get_threshold("browser_action_selector_timeout_ms", 4000)
                    )
                activation_timeout_ms = _normalize_selector_timeout_ms(
                    activation_timeout_ms,
                    site_key=site_key or "",
                    action="fill_recovery",
                )

                activated = False
                if activation_clicks:
                    # Keep activation probing bounded; large selector banks can otherwise exceed step caps.
                    scoped_activation_clicks = list(activation_clicks)
                    min_recovery_reserve_ms = 7_000
                    if (site_key or "").strip().lower() == "google_flights":
                        max_activation_selectors = max(
                            1,
                            min(
                                24,
                                int(
                                    get_threshold(
                                        "google_flights_fill_recovery_max_activation_selectors",
                                        12,
                                    )
                                ),
                            ),
                        )
                        scoped_activation_clicks = scoped_activation_clicks[:max_activation_selectors]
                        min_recovery_reserve_ms = int(
                            get_threshold("google_flights_fill_recovery_min_reserve_ms", 9_000)
                        )

                    # Try to activate/open the target control before one more fill pass.
                    activation_error = RuntimeError("activation_not_attempted")
                    for act_sel in scoped_activation_clicks:
                        _raise_if_step_timed_out("recovery_activation")
                        per_try_timeout_ms = _remaining_step_timeout_ms()
                        if per_try_timeout_ms is None:
                            per_try_timeout_ms = activation_timeout_ms
                        if int(per_try_timeout_ms) <= int(min_recovery_reserve_ms):
                            log.info(
                                "scenario.fill_recovery.activation_short_circuit site=%s role=%s remaining_ms=%s reserve_ms=%s",
                                site_key,
                                role,
                                int(per_try_timeout_ms),
                                int(min_recovery_reserve_ms),
                            )
                            break
                        per_try_timeout_ms = max(1, min(int(activation_timeout_ms), int(per_try_timeout_ms)))
                        activation_error, _ = exec_ctx.run_step_action(
                            "click",
                            [act_sel],
                            timeout_ms=per_try_timeout_ms,
                        )
                        if activation_error is None:
                            activated = True
                            break

                # Selectorless activation fallback for dynamic/custom controls.
                if (
                    not activated
                    and hasattr(browser, "activate_field_by_keywords")
                ):
                    _raise_if_step_timed_out("recovery_keyword_activation")
                    keyword_timeout_ms = _remaining_step_timeout_ms()
                    if keyword_timeout_ms is None:
                        keyword_timeout_ms = activation_timeout_ms
                    keyword_timeout_ms = max(1, min(int(activation_timeout_ms), int(keyword_timeout_ms)))
                    try:
                        activated = bool(
                            browser.activate_field_by_keywords(
                                _service_fill_activation_keywords(site_key, role),
                                timeout_ms=keyword_timeout_ms,
                            )
                        )
                    except Exception:
                        activated = False

                if activated:
                    retry_error, retry_used_selector = exec_ctx.run_step_action(
                        "fill",
                        selectors,
                        value=step.get("value"),
                        timeout_ms=_remaining_step_timeout_ms(),
                    )
                    if retry_error is None:
                        last_step_error = None
                        used_selector = retry_used_selector
                    else:
                        last_step_error = retry_error

                # Selectorless direct-fill fallback for dynamic forms.
                if (
                    last_step_error is not None
                    and hasattr(browser, "fill_by_keywords")
                    and role in {"origin", "dest", "depart", "return"}
                ):
                    _raise_if_step_timed_out("recovery_keyword_fill")
                    keyword_fill_timeout_ms = _remaining_step_timeout_ms()
                    if keyword_fill_timeout_ms is None:
                        keyword_fill_timeout_ms = activation_timeout_ms
                    keyword_fill_timeout_ms = max(1, min(int(activation_timeout_ms), int(keyword_fill_timeout_ms)))
                    try:
                        keyword_filled = bool(
                            browser.fill_by_keywords(
                                _service_fill_activation_keywords(site_key, role),
                                step.get("value"),
                                timeout_ms=keyword_fill_timeout_ms,
                            )
                        )
                    except Exception:
                        keyword_filled = False
                    if keyword_filled:
                        if (
                            (site_key or "").strip().lower() == "google_flights"
                            and role in {"origin", "dest"}
                        ):
                            expected_value = str(step.get("value", "") or "").strip().upper()
                            observed_value = ""
                            try:
                                probe_target = getattr(browser, "page", None) or browser
                                form_state = _extract_google_flights_form_state(probe_target)
                                observed_value = str(form_state.get(role, "") or "").strip().upper()
                            except Exception:
                                observed_value = ""
                            semantic_match = bool(
                                observed_value
                                and expected_value
                                and _google_form_value_matches_airport(
                                    observed_value,
                                    expected_value,
                                    role=role,
                                    locale=_current_mimic_locale(),
                                )
                            )
                            if semantic_match:
                                log.info(
                                    "scenario.step.fill_keyword_recovery site=%s role=%s",
                                    site_key,
                                    role,
                                )
                                last_step_error = None
                                used_selector = "keyword_fill_recovery"
                            else:
                                log.warning(
                                    "scenario.step.fill_keyword_recovery_unverified site=%s role=%s expected=%s observed=%s",
                                    site_key,
                                    role,
                                    expected_value,
                                    observed_value,
                                )
                                last_step_error = RuntimeError(
                                    f"keyword_fill_unverified_{role}"
                                )
                        else:
                            log.info(
                                "scenario.step.fill_keyword_recovery site=%s role=%s",
                                site_key,
                                role,
                            )
                            last_step_error = None
                            used_selector = "keyword_fill_recovery"

                # Final fallback: type into the active element for route/date fields.
                # HARD-GATE: For Google Flights depart/return, do NOT allow typing fallback.
                # Only gf_set_date is allowed.
                is_google_flights_date = (
                    (site_key or "").strip().lower() == "google_flights"
                    and role in {"depart", "return"}
                )

                if (
                    last_step_error is not None
                    and hasattr(browser, "type_active")
                    and role in {"origin", "dest", "depart", "return"}
                    and not is_google_flights_date  # HARD-GATE: Block typing for GF dates
                    and (
                        allow_type_active_recovery
                        or (allow_date_type_active_recovery and role in {"depart", "return"})
                    )
                    and (
                        (site_key != "google_flights")
                        or activated
                        or (role in {"depart", "return"} and allow_date_type_active_recovery)
                    )
                ):
                    # Validation: For date fills, check if input element exists before typing
                    skip_type_active = False
                    if role in {"depart", "return"}:
                        try:
                            # Try to verify that an input element can be found
                            input_found = False
                            for sel in selectors:
                                try:
                                    el = browser.page.query_selector(sel)
                                    if el and el.is_visible():
                                        input_found = True
                                        break
                                except Exception:
                                    continue
                            if not input_found:
                                log.info(
                                    "scenario.google_date_fallback skipped_no_input role=%s site=%s",
                                    role,
                                    site_key,
                                )
                                skip_type_active = True
                        except Exception:
                            # If check fails, continue with type_active attempt
                            pass

                    if not skip_type_active:
                        previous_error = last_step_error
                        _raise_if_step_timed_out("recovery_type_active")
                        type_active_timeout_ms = _remaining_step_timeout_ms()
                        if type_active_timeout_ms is None:
                            type_active_timeout_ms = activation_timeout_ms
                        type_active_timeout_ms = max(
                            1,
                            min(int(activation_timeout_ms), int(type_active_timeout_ms)),
                        )
                        try:
                            browser.type_active(
                                step.get("value"),
                                timeout_ms=type_active_timeout_ms,
                            )
                            log.info(
                                "scenario.step.fill_type_active_recovery site=%s role=%s",
                                site_key,
                                role,
                            )
                            last_step_error = None
                            used_selector = "type_active_recovery"
                        except Exception as type_exc:
                            # Keep prior root cause if active-element typing was simply unavailable.
                            if str(type_exc) == "no_active_typing_target":
                                last_step_error = previous_error
                            else:
                                last_step_error = type_exc

        if last_step_error is not None:
            if action == "fill" and (
                (
                    role == "depart"
                    and (site_key or "").strip().lower() == "google_flights"
                )
                or (
                    role in {"depart", "return"}
                    and (site_key or "").strip().lower() == "skyscanner"
                )
            ):
                log.warning(
                    "scenario.date.soft_fail site=%s role=%s error=%s",
                    site_key,
                    role,
                    str(last_step_error),
                )
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "role": role,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "soft_fail",
                        "error": str(last_step_error),
                        "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                    }
                )
                continue
            if action == "fill" and not step.get("optional"):
                role = _infer_fill_role(step)
                try:
                    current_html = browser.content()
                except Exception:
                    current_html = ""
                skyscanner_url_bound = bool(
                    (site_key or "").strip().lower() == "skyscanner"
                    and role in {"origin", "dest"}
                    and _is_skyscanner_route_value_already_bound_from_url(
                        browser,
                        role=role,
                        value=str(step.get("value", "") or ""),
                    )
                )
                if (
                    role in {"origin", "dest", "depart", "return"}
                    and (
                        skyscanner_url_bound
                        or _is_fill_value_already_bound(
                    current_html,
                    role=role,
                    value=str(step.get("value", "") or ""),
                        )
                    )
                ):
                    # Google Flights route fields require semantic form-state agreement before soft-pass.
                    if (
                        (site_key or "").strip().lower() == "google_flights"
                        and role in {"origin", "dest"}
                    ):
                        expected_value = str(step.get("value", "") or "").strip().upper()
                        observed_value = ""
                        try:
                            probe_target = getattr(browser, "page", None) or browser
                            form_state = _extract_google_flights_form_state(probe_target)
                            observed_value = str(form_state.get(role, "") or "").strip().upper()
                        except Exception:
                            observed_value = ""
                        semantic_match = bool(
                            observed_value
                            and expected_value
                            and _google_form_value_matches_airport(
                                observed_value,
                                expected_value,
                                role=role,
                                locale=_current_mimic_locale(),
                            )
                        )
                        if not semantic_match:
                            log.info(
                                "scenario.step.fill_already_bound_rejected site=%s role=%s expected=%s observed=%s",
                                site_key,
                                role,
                                expected_value,
                                observed_value,
                            )
                        else:
                            log.info(
                                "scenario.step.fill_already_bound_soft_pass site=%s role=%s",
                                site_key,
                                role,
                            )
                            elapsed_ms = int((time.monotonic() - step_start) * 1000)
                            log.info(
                                "scenario.step.end step_index=%d action=%s status=already_bound_soft_pass elapsed_ms=%d",
                                idx,
                                action or "unknown",
                                elapsed_ms,
                            )
                            step_trace.append(
                                {
                                    "index": idx,
                                    "action": action,
                                    "role": role,
                                    "selectors": list(selectors),
                                    "used_selector": used_selector,
                                    "status": "already_bound_soft_pass",
                                    "error": str(last_step_error),
                                    "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                                }
                            )
                            continue
                    else:
                        log.info(
                            "scenario.step.fill_already_bound_soft_pass site=%s role=%s",
                            site_key,
                            role,
                        )
                        elapsed_ms = int((time.monotonic() - step_start) * 1000)
                        log.info(
                            "scenario.step.end step_index=%d action=%s status=already_bound_soft_pass elapsed_ms=%d",
                            idx,
                            action or "unknown",
                            elapsed_ms,
                        )
                        step_trace.append(
                            {
                                "index": idx,
                                "action": action,
                                "role": role,
                                "selectors": list(selectors),
                                "used_selector": used_selector,
                                "status": "already_bound_soft_pass",
                                "error": str(last_step_error),
                                "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                            }
                        )
                        continue
            if action == "fill" and step.get("optional"):
                if bool(step.get("required_for_actionability")):
                    log.warning(
                        "scenario.step.fill_required_optional_escalated selectors=%s error=%s",
                        selectors,
                        last_step_error,
                    )
                else:
                    log.warning(
                        "scenario.step.fill_optional_soft_fail selectors=%s error=%s",
                        selectors,
                        last_step_error,
                    )
                    elapsed_ms = int((time.monotonic() - step_start) * 1000)
                    log.info(
                        "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                        idx,
                        action or "unknown",
                        elapsed_ms,
                    )
                    step_trace.append(
                        {
                            "index": idx,
                            "action": action,
                            "role": role,
                            "selectors": list(selectors),
                            "used_selector": used_selector,
                            "status": "soft_fail",
                            "error": str(last_step_error),
                            "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                        }
                    )
                    continue
            if action == "wait":
                log.warning(
                    "scenario.step.wait_soft_fail selectors=%s error=%s",
                    selectors,
                    last_step_error,
                )
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "soft_fail",
                        "error": str(last_step_error),
                    }
                )
                continue
            if action == "click" and _selectors_look_search_submit(selectors):
                # Many sites auto-run search after date/route changes; do not hard-fail
                # if explicit search button is absent or detached.
                log.warning(
                    "scenario.step.search_click_soft_fail selectors=%s error=%s",
                    selectors,
                    last_step_error,
                )
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                step_status = (
                    "route_fill_mismatch"
                    if str(last_step_error) == "route_fill_mismatch"
                    else "soft_fail"
                )
                log.info(
                    "scenario.step.end step_index=%d action=%s status=%s elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    step_status,
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": step_status,
                        "error": str(last_step_error),
                        "route_verify": route_verify_meta if isinstance(route_verify_meta, dict) else {},
                    }
                )
                continue
            if action == "click" and step.get("optional"):
                log.warning(
                    "scenario.step.click_optional_soft_fail selectors=%s error=%s",
                    selectors,
                    last_step_error,
                )
                elapsed_ms = int((time.monotonic() - step_start) * 1000)
                log.info(
                    "scenario.step.end step_index=%d action=%s status=soft_fail elapsed_ms=%d",
                    idx,
                    action or "unknown",
                    elapsed_ms,
                )
                step_trace.append(
                    {
                        "index": idx,
                        "action": action,
                        "selectors": list(selectors),
                        "used_selector": used_selector,
                        "status": "soft_fail",
                        "error": str(last_step_error),
                    }
                )
                continue
            elapsed_ms = int((time.monotonic() - step_start) * 1000)
            log.error(
                "scenario.step.end step_index=%d action=%s status=hard_fail elapsed_ms=%d error=%s",
                idx,
                action or "unknown",
                elapsed_ms,
                str(last_step_error),
            )

            # Detect stuck_step: elapsed > cap OR timeout in error message
            if router is not None:
                if step_wall_clock_cap_ms and elapsed_ms >= step_wall_clock_cap_ms:
                    router.record_event(
                        "stuck_step",
                        step_index=idx,
                        action=action or "unknown",
                        elapsed_ms=elapsed_ms,
                        cap_ms=step_wall_clock_cap_ms,
                    )
                elif "timeout" in str(last_step_error).lower() or "timed" in str(last_step_error).lower():
                    router.record_event(
                        "stuck_step",
                        step_index=idx,
                        action=action or "unknown",
                        elapsed_ms=elapsed_ms,
                        reason="timeout_error",
                    )

            # Detect deadline_bug: remaining budget extremely small relative to configured timeout
            if router is not None and step_deadline:
                remaining_ms = (step_deadline - time.monotonic()) * 1000
                min_threshold_ms = max(step_wall_clock_cap_ms * 0.1, 5000)
                if remaining_ms < min_threshold_ms:
                    router.record_event(
                        "deadline_bug",
                        remaining_ms=int(remaining_ms),
                        configured_timeout_ms=step_wall_clock_cap_ms,
                        min_threshold_ms=int(min_threshold_ms),
                    )

            step_trace.append(
                {
                    "index": idx,
                    "action": action,
                    "role": role,
                    "optional": bool(step.get("optional")),
                    "required_for_actionability": bool(step.get("required_for_actionability")),
                    "selectors": list(selectors),
                    "used_selector": used_selector,
                    "status": "hard_fail",
                    "error": str(last_step_error),
                    "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
                }
            )
            exec_ctx.record_graph_transition(
                idx, action, role, used_selector or "", "hard_fail", str(last_step_error), elapsed_ms
            )
            selectors_for_error = selectors if selectors else "<none>"
            raise RuntimeError(
                f"Step failed action={action} role={role or 'none'} selectors={selectors_for_error}: {last_step_error}"
            )
        elapsed_ms = int((time.monotonic() - step_start) * 1000)
        log.info(
            "scenario.step.end step_index=%d action=%s status=ok elapsed_ms=%d",
            idx,
            action or "unknown",
            elapsed_ms,
        )
        step_trace.append(
            {
                "index": idx,
                "action": action,
                "role": role,
                "selectors": list(selectors),
                "used_selector": used_selector,
                "status": "ok",
                "error": "",
                "fill_commit": dict(fill_commit_meta) if fill_commit_meta else {},
            }
        )
        exec_ctx.record_graph_transition(idx, action, role, used_selector or "", "ok", "", elapsed_ms)
    return step_trace


def _try_agent_v0_optional(
    browser: Any,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    site_key: str,
) -> Tuple[str, bool]:
    """Try agent v0 optional flow for Google Flights.

    This is behind FLIGHT_WATCHER_AGENT_V0 feature flag and runs
    independendly: if successful (page becomes ready), returns HTML.
    Otherwise falls back to legacy flow.

    NOTE: This function is part of the legacy entry point. Future usage should
    prefer core.site_adapter.SiteAdapter interface via get_global_registry().
    See INV-ADAPTER-001 (docs/kb/00_foundation/architecture_invariants.md § M).

    HARDENING GUARANTEE: Never returns None for HTML. HTML string is
    always returned as first element of tuple. Page may not be ready,
    but HTML is always valid (either from successful agent runs or
    fallback to last known page content).

    Args:
        browser: BrowserSession instance.
        url: Current URL.
        origin: Intended origin (IATA code).
        dest: Intended destination (IATA code).
        depart: Intended departure date (YYYY-MM-DD).
        return_date: Optional return date.
        site_key: Service key (e.g., "google_flights").

    Returns:
        (html_str, was_ready)
        If successful and ready: (html, True)
        If not ready or agent disabled: (last_known_html, False) - never None
    """
    # Capture initial HTML for fallback guarantee
    last_html = browser.content() or ""

    agent_enabled = bool(
        int(os.getenv("FLIGHT_WATCHER_AGENT_V0", "0") or "0")
    )
    if not agent_enabled or site_key != "google_flights":
        log.debug("agent.v0.disabled site=%s enabled=%s", site_key, agent_enabled)
        return last_html, False

    try:
        log.info("agent.v0.start site=%s", site_key)
        ctx = RunContext(
            site_key=site_key,
            url=url,
            locale="ja-JP",  # v0: JA only
            inputs={
                "origin": origin,
                "dest": dest,
                "depart": depart,
                "return_date": return_date or "",
            },
        )

        plugin = GoogleFlightsPlugin()
        engine = AgentEngine(plugin, log=log)

        # Run up to 3 turns
        html_current = last_html
        for turn in range(3):
            html_current, obs, trace_events = engine.run_once(
                browser,
                html_current,
                ctx,
            )
            # Update last_html after each turn to maintain fallback guarantee
            last_html = html_current or last_html

            log.info(
                "agent.v0.turn=%d page_class=%s route_bound=%s confidence=%s",
                turn,
                obs.page_class,
                obs.route_bound,
                obs.confidence,
            )

            if plugin.readiness(obs, ctx):
                log.info("agent.v0.ready=True turn=%d", turn)
                return last_html, True

        log.info("agent.v0.exhausted_turns=3 returning_false with_fallback_html")
        return last_html, False

    except Exception as exc:
        log.exception("agent.v0.exception: %s", exc)
        return last_html, False


from functools import partial




# Run self-check on module import (only in development)
if __name__ == "__main__":
    import inspect

    assert callable(run_agentic_scenario), "run_agentic_scenario must be callable"
    sig = inspect.signature(run_agentic_scenario)
    params = list(sig.parameters.keys())
    for param in ("url", "origin", "dest", "depart"):
        assert param in params, f"Missing required parameter: {param}"

    assert callable(_maybe_run_initial_vlm_ui_assist), "_maybe_run_initial_vlm_ui_assist must exist"
    assert callable(_compose_local_hint_with_notes), "_compose_local_hint_with_notes must exist"

    vlm_sig = inspect.signature(_maybe_run_initial_vlm_ui_assist)
    vlm_params = list(vlm_sig.parameters.keys())
    assert "site_key" in vlm_params, "_maybe_run_initial_vlm_ui_assist missing site_key param"
    assert "url" in vlm_params, "_maybe_run_initial_vlm_ui_assist missing url param"

    hint_sig = inspect.signature(_compose_local_hint_with_notes)
    hint_params = list(hint_sig.parameters.keys())
    assert "local_knowledge_hint" in hint_params, "_compose_local_hint_with_notes missing local_knowledge_hint"
    assert "planner_notes" in hint_params, "_compose_local_hint_with_notes missing planner_notes"
    assert "trace_memory_hint" in hint_params, "_compose_local_hint_with_notes missing trace_memory_hint"

    result = _compose_local_hint_with_notes(
        local_knowledge_hint="test hint",
        planner_notes=["note1", "note2"],
        trace_memory_hint="memory",
    )
    assert isinstance(result, str), "_compose_local_hint_with_notes must return string"
    print("✓ Scenario runner self-check passed")
