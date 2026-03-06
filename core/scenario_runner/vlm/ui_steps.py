"""VLM UI step generation helpers."""

from typing import Any, Dict, Optional

from core.scenario_runner.env import _current_mimic_locale, _env_bool, _env_int
from core.scenario_runner.plan_hygiene import _plan_has_click_token
from core.scenario_runner.vlm.runtime_hints import (
    _apply_vlm_runtime_hints,
    _compose_vlm_knowledge_hint,
    _sanitize_vlm_labels,
)
from core.scenario_runner.artifacts import _snapshot_image_path
from core.scenario_runner.google_flights.core_functions import (
    _allow_bare_text_fallback,
    _profile_localized_list,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _build_click_selectors_for_tokens,
    _label_click_selectors,
    _maybe_append_bare_text_selectors,
    _dedupe_selectors,
    _is_google_flights_deeplink,
)
from core.service_ui_profiles import get_service_ui_profile
from llm.code_model import analyze_page_ui_with_vlm
from utils.knowledge_rules import get_tokens
from utils.logging import get_logger
from utils.thresholds import get_threshold
from core.ui_tokens import prioritize_tokens

log = get_logger(__name__)


def _default_domain_toggle_step(is_domestic: bool):
    """Return a conservative domestic/international mode toggle click step."""
    from core.scenario_runner.plan_toggles import _default_domain_toggle_step as _impl
    return _impl(is_domestic)


def _is_non_flight_page_class(page_class: str) -> bool:
    """Return True when page class is non-flight or unusable for extraction."""
    # Import locally to avoid circular dependency
    from core.scenario_runner import _is_non_flight_page_class as _impl
    return _impl(page_class)


def _vlm_mode_toggle_step(vlm_hint, *, is_domestic: bool):
    """Build optional mode-toggle step from VLM mode labels."""
    if not isinstance(vlm_hint, dict):
        return None
    mode = vlm_hint.get("mode_labels")
    if not isinstance(mode, dict):
        return None
    key = "domestic" if is_domestic else "international"
    labels = mode.get(key, [])
    selectors = _label_click_selectors(labels if isinstance(labels, list) else [])
    if not selectors:
        return None
    return {"action": "click", "selector": selectors[:8], "optional": True}


def _vlm_product_toggle_step(vlm_hint):
    """Build optional product-toggle step when VLM sees package/mixed product."""
    if not isinstance(vlm_hint, dict):
        return None
    product = str(vlm_hint.get("trip_product", "")).strip().lower()
    if product != "flight_hotel_package":
        return None
    labels = vlm_hint.get("product_labels", [])
    selectors = _label_click_selectors(labels if isinstance(labels, list) else [])
    if not selectors:
        return None
    return {"action": "click", "selector": selectors[:8], "optional": True}


def _service_product_toggle_step(
    site_key: str,
    *,
    scope_class: str = "unknown",
    vlm_hint: Optional[dict] = None,
):
    """Build one service-aware product toggle step (flight-only vs package/mixed)."""
    hint = vlm_hint if isinstance(vlm_hint, dict) else {}
    prefer_ja = _current_mimic_locale().lower().startswith("ja")
    profile = get_service_ui_profile(site_key)

    selectors_cfg = profile.get("product_toggle_selectors", [])
    selectors = _profile_localized_list(selectors_cfg, prefer_ja=prefer_ja)

    labels = []
    labels.extend(_sanitize_vlm_labels(hint.get("product_labels", []), max_items=8))
    labels.extend(
        _profile_localized_list(
            profile.get("product_toggle_labels", {}),
            prefer_ja=prefer_ja,
        )
    )
    if _is_non_flight_page_class(scope_class):
        labels.extend(
            prioritize_tokens(
                get_tokens("tabs", "flights"),
                locale_hint=_current_mimic_locale(),
            )
        )
    selectors = _dedupe_selectors(_label_click_selectors(labels) + selectors)
    if not selectors:
        return None
    return {"action": "click", "selector": selectors[:10], "optional": True}


def _service_mode_toggle_step(
    site_key: str,
    *,
    is_domestic: bool,
    vlm_hint: Optional[dict] = None,
    fallback_default: bool = False,
):
    """Build one service-aware domestic/international mode toggle step."""
    step = _vlm_mode_toggle_step(vlm_hint or {}, is_domestic=is_domestic)
    if isinstance(step, dict):
        return step

    prefer_ja = _current_mimic_locale().lower().startswith("ja")
    profile = get_service_ui_profile(site_key)
    mode_cfg = profile.get("mode_toggle_selectors", {})
    key = "domestic" if is_domestic else "international"
    mode_selectors = []
    if isinstance(mode_cfg, dict):
        mode_selectors = _profile_localized_list(mode_cfg.get(key, []), prefer_ja=prefer_ja)

    label_cfg = profile.get("mode_toggle_labels", {})
    mode_labels = []
    if isinstance(label_cfg, dict):
        mode_labels = _profile_localized_list(label_cfg.get(key, {}), prefer_ja=prefer_ja)
    mode_selectors = _dedupe_selectors(_label_click_selectors(mode_labels) + mode_selectors)
    if mode_selectors:
        return {"action": "click", "selector": mode_selectors[:8], "optional": True}
    if fallback_default:
        return _default_domain_toggle_step(is_domestic)
    return None


def _maybe_prepend_vlm_ui_steps(plan, *, vlm_hint, is_domestic: bool):
    """Inject optional VLM-derived UI toggle steps before the core plan."""
    if not isinstance(plan, list) or not isinstance(vlm_hint, dict):
        return plan

    steps = []

    product_step = _vlm_product_toggle_step(vlm_hint)
    if product_step and not _plan_has_click_token(plan, ("flight", "航空券", "air")):
        steps.append(product_step)

    mode_step = _vlm_mode_toggle_step(vlm_hint, is_domestic=is_domestic)
    if mode_step:
        if is_domestic:
            guard_tokens = ("domestic", "国内")
        else:
            guard_tokens = ("international", "海外", "国際")
        if not _plan_has_click_token(plan, guard_tokens):
            steps.append(mode_step)

    if not steps:
        return plan
    return steps + list(plan)


def _maybe_run_initial_vlm_ui_assist(
    *,
    site_key: str,
    url: str,
    initial_html: str,
    is_domestic: Optional[bool],
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    mimic_locale: Optional[str],
    local_knowledge_hint: str,
    scenario_run_id: str,
) -> tuple:
    """
    Run VLM UI assist on initial page load if enabled.

    Returns:
        tuple: (vlm_ui_hint: dict, updated_local_knowledge_hint: str)
    """
    vlm_ui_hint = {}

    if not _env_bool(
        "FLIGHT_WATCHER_VLM_UI_ASSIST_ENABLED",
        bool(get_threshold("scenario_vlm_ui_assist_enabled", False)),
    ):
        return vlm_ui_hint, local_knowledge_hint

    # Determine if we should skip VLM on first pass (deeplink optimization)
    # This code runs before any retry/turn loops, so it's always the initial pass
    is_initial_pass = True
    skip_vlm_first_pass = (
        is_initial_pass
        and bool(get_threshold("scenario_deeplink_skip_vlm_first_pass", True))
        and _is_google_flights_deeplink(url)
    )
    page_kind_strategy = "skip_vlm_first_pass" if skip_vlm_first_pass else "vlm_first_pass"

    if not skip_vlm_first_pass:
        try:
            ui_assist_max_variants = max(
                1,
                _env_int(
                    "FLIGHT_WATCHER_VLM_UI_ASSIST_MAX_VARIANTS",
                    int(get_threshold("scenario_vlm_ui_assist_max_variants", 1)),
                ),
            )
            initial_png = _snapshot_image_path(site_key, "initial", run_id=scenario_run_id)
            if initial_png.exists():
                ui_assist_timeout_sec = _env_int(
                    "FLIGHT_WATCHER_VLM_UI_ASSIST_TIMEOUT_SEC",
                    int(get_threshold("scenario_vlm_ui_assist_timeout_sec", 30)),
                )
                ui_assist_timeout_cap_sec = _env_int(
                    "FLIGHT_WATCHER_VLM_UI_ASSIST_TIMEOUT_CAP_SEC",
                    int(get_threshold("scenario_vlm_ui_assist_timeout_cap_sec", 300)),
                )
                if ui_assist_timeout_cap_sec > 0:
                    ui_assist_timeout_sec = min(
                        ui_assist_timeout_sec,
                        max(1, ui_assist_timeout_cap_sec),
                    )
                vlm_ui_hint = analyze_page_ui_with_vlm(
                    str(initial_png),
                    site=site_key,
                    is_domestic=is_domestic,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date or "",
                    locale=mimic_locale or "",
                    html_context=initial_html,
                    include_dom_context=_env_bool(
                        "FLIGHT_WATCHER_VLM_UI_ASSIST_INCLUDE_DOM_CONTEXT",
                        bool(
                            get_threshold(
                                "scenario_vlm_ui_assist_include_dom_context",
                                True,
                            )
                        ),
                    ),
                    timeout_sec=ui_assist_timeout_sec,
                    max_variants=ui_assist_max_variants,
                )
        except Exception as exc:
            log.warning("scenario.vlm_ui.assist_failed site=%s error=%s", site_key, exc)
            vlm_ui_hint = {}
    else:
        log.info(
            "scenario.vlm_ui.assist_skipped strategy=%s url_pattern=%s turn=%d attempt=%d",
            page_kind_strategy,
            "deeplink" if _is_google_flights_deeplink(url) else "unknown",
            1,  # Initial pass is always turn 1
            1,  # Initial pass is always attempt 1
        )

    # Apply VLM hints if we got any
    if isinstance(vlm_ui_hint, dict) and vlm_ui_hint:
        _apply_vlm_runtime_hints(vlm_ui_hint)
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
        log.info(
            "scenario.vlm_ui.assist_applied site=%s page_scope=%s trip_product=%s blocked_by_modal=%s",
            site_key,
            vlm_ui_hint.get("page_scope"),
            vlm_ui_hint.get("trip_product"),
            bool(vlm_ui_hint.get("blocked_by_modal")),
        )

    return vlm_ui_hint, local_knowledge_hint
