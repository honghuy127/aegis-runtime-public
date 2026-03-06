"""Reason alias health checks for CI.

Purpose:
- Block unmapped legacy alias introductions in structured KB docs.
- Keep code-side non-canonical reason/status tokens explicit via a small baseline allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from core.scenario.reasons import REASON_ALIASES, REASON_REGISTRY, normalize_reason
from utils.kb_drift import _NON_TRIAGE_REASON_CODES


REPO_ROOT = Path(__file__).resolve().parent.parent

TRIAGE_TABLE_PATH = REPO_ROOT / "docs/kb/20_decision_system/triage_decision_table.yaml"
SYMPTOM_MAP_PATH = REPO_ROOT / "docs/kb/20_decision_system/runtime_symptom_map.yaml"

REASON_LITERAL_PATTERNS = (
    re.compile(r"reason(?:_code)?\s*[:=]\s*['\"]([a-z][a-z0-9_]*)['\"]"),
    re.compile(r"StepResult\.failure\(\s*['\"]([a-z][a-z0-9_]*)['\"]"),
)

# Existing non-canonical values emitted in code as telemetry/status/detail codes.
# Keep this explicit so any newly introduced non-canonical token is reviewed.
NON_CANONICAL_CODE_ALLOWLIST = {
    "already_bound",
    "blocked_interstitial_manual_exception",
    "blocked_interstitial_manual_interrupted",
    "blocked_interstitial_manual_no_effect",
    "blocked_interstitial_manual_reissue_suspected_target_closed",
    "blocked_interstitial_manual_target_closed",
    "blocked_interstitial_press_hold_unsuccessful",
    "blocked_interstitial_reissued_after_manual",
    # Skyscanner parser/extractor detail status (not a canonical failure reason).
    "brand_and_search_form_markers_detected",
    "context_exit",
    "date_not_committed",
    "date_parsed",
    "date_string_valid",
    "day_clicked",
    "day_not_found_in_current_view",
    "demo_mode",
    "demo_mode_final_html_unavailable",
    "demo_mode_manual_interrupted",
    "demo_mode_manual_observation_complete_target_closed",
    "demo_mode_manual_reissue_suspected_target_closed",
    "demo_mode_manual_target_closed",
    "demo_mode_observation_complete",
    "direct_input_not_available",
    "empty_query",
    "expected_new_page",
    "fallback_manual_intervention_interference_detected",
    "fallback_manual_intervention_no_html",
    "fallback_press_hold_unsuccessful",
    "fallback_reload_exception",
    "fallback_reload_failed",
    "fallback_reload_page_closed",
    "fallback_skipped_manual_disrupted",
    "fallback_skipped_manual_intervention_used",
    "fallback_skipped_manual_no_effect",
    "google_route_context_unbound",
    "human_interaction_proxy_detected",
    "input_fill_failed",
    "invalid_date",
    "invalid_date_format",
    "listbox_not_visible",
    "manual_capture_rearmed_after_navigation",
    "manual_clearance_reached_continue_demo",
    "manual_clearance_unstable",
    "manual_finalize",
    "manual_heartbeat",
    "manual_in_progress",
    "manual_intervention_exception",
    "manual_intervention_reissue_suspected_target_closed",
    "manual_start",
    "manual_started",
    "manual_window_extended_for_captcha_reissue",
    "nav_buttons_not_found",
    "page_unavailable",
    "prerequisites_missing",
    "primary_page_closed",
    "rebind_home_exception",
    "rebind_home_form_visible",
    "rebind_not_attempted",
    "recovery_new_page",
    "role_valid",
    "route_bind_corroborated_url",
    "skip_wait_after_local_date_fail",
    # Scenario loop-guard terminal status used for bounded suppression path.
    "skyscanner_blank_shell_persistent_after_post_clear_refill",
    "skyscanner_blank_shell_recovery",
    # Skyscanner flights-flow guard status when run drifts to Hotels context.
    "skyscanner_hotels_context_detected",
    "skyscanner_interstitial_detected",
    "skyscanner_interstitial_last_resort_when_manual_disabled",
    "skyscanner_manual_recovery_requires_headed",
    # Scenario return status for bounded snapshot salvage after manual target-close.
    "skyscanner_results_snapshot_after_manual_target_closed",
    "skyscanner_results_hydration_incomplete",
    "strategy_exception",
    # Predicate-gate status for bounded fallback reload validation.
    "success_predicate_failed",
    "suggestion_clicked",
    "suggestion_mismatch_expected_iata",
    "unexpected_new_page_closed",
    "unsupported_role",
    "verification_surface_allowed",
    "verification_surface_detected",
}


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def test_triage_declared_legacy_aliases_are_mapped():
    """Every alias declared in triage table must map in REASON_ALIASES."""
    table = _load_yaml(TRIAGE_TABLE_PATH)
    reason_tree = table.get("reason_tree", {})

    declared_aliases = set()
    if isinstance(reason_tree, dict):
        for value in reason_tree.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                for alias in item.get("legacy_aliases", []) or []:
                    if isinstance(alias, str) and alias.strip():
                        declared_aliases.add(alias.strip())

    missing_mappings = sorted(alias for alias in declared_aliases if alias not in REASON_ALIASES)
    assert not missing_mappings, (
        "Found legacy aliases in triage_decision_table.yaml without REASON_ALIASES mapping: "
        f"{missing_mappings}"
    )


def test_runtime_symptom_map_alias_like_tokens_are_mapped():
    """Alias-like tokens in runtime symptom map should resolve via REASON_ALIASES."""
    symptom_map = _load_yaml(SYMPTOM_MAP_PATH)
    symptoms = symptom_map.get("symptoms", {})

    alias_like = set()

    if isinstance(symptoms, dict):
        for symptom_name, details in symptoms.items():
            if isinstance(symptom_name, str):
                normalized = normalize_reason(symptom_name)
                if normalized != "unknown" and normalized != symptom_name:
                    alias_like.add(symptom_name)
            if not isinstance(details, dict):
                continue
            reasons = details.get("reasons", [])
            if not isinstance(reasons, list):
                continue
            for reason in reasons:
                if not isinstance(reason, str):
                    continue
                normalized = normalize_reason(reason)
                if normalized != "unknown" and normalized != reason:
                    alias_like.add(reason)

    unmapped_alias_like = sorted(code for code in alias_like if code not in REASON_ALIASES)
    assert not unmapped_alias_like, (
        "Alias-like tokens in runtime_symptom_map.yaml are not mapped in REASON_ALIASES: "
        f"{unmapped_alias_like}"
    )


def test_core_utils_reason_tokens_are_canonical_mapped_or_allowlisted():
    """Fail on newly introduced non-canonical, unmapped reason/status tokens in code."""
    roots = [REPO_ROOT / "core", REPO_ROOT / "utils"]
    discovered_tokens = set()

    for root in roots:
        for py_file in root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in REASON_LITERAL_PATTERNS:
                discovered_tokens.update(match.group(1) for match in pattern.finditer(text))

    allowed = (
        set(REASON_REGISTRY.keys())
        | set(REASON_ALIASES.keys())
        | set(_NON_TRIAGE_REASON_CODES)
        | NON_CANONICAL_CODE_ALLOWLIST
        | {"success", "unknown", "commit_success"}
    )
    unknown = sorted(token for token in discovered_tokens if token not in allowed)

    assert not unknown, (
        "Detected non-canonical reason/status tokens not mapped or allowlisted. "
        f"Add REASON_ALIASES mapping or explicitly allowlist with rationale: {unknown}"
    )
