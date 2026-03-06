"""Vision helpers for run_agentic_scenario orchestration."""

from typing import Any, Callable, Dict, Optional

from core.browser import BrowserSession
from core.scenario_runner.vlm.probes import (
    _should_run_vision_page_kind_probe,
    _normalize_vision_page_kind_result,
    _vision_cached_stage_call,
)
from core.scenario_runner.vlm.ui_steps import _service_mode_toggle_step
from core.scenario_runner.google_flights.service_runner_bridge import (
    _strip_nonvisible_html,
)
from llm.code_model import analyze_page_ui_with_vlm
from utils.logging import get_logger

log = get_logger(__name__)


def _run_vision_page_kind_probe_impl(
    *,
    html_text: str,
    screenshot_stage: str,
    trigger_reason: str,
    site_key: str,
    scenario_run_id: str,
    is_domestic: bool,
    mimic_locale: Optional[str],
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    vision_stage_cache: Any,
    vision_stage_cooldown: Any,
    get_threshold_fn: Callable,
    snapshot_image_path_fn: Callable,
) -> Dict[str, Any]:
    """Run Stage-A vision page-kind probe with cache/cooldown and strict normalization."""
    if site_key != "google_flights":
        return {}
    if not _should_run_vision_page_kind_probe(
        enabled=bool(get_threshold_fn("scenario_vision_page_kind_enabled", True)),
        trigger_reason=trigger_reason,
        scope_class="unknown",
    ):
        return {}
    screenshot = snapshot_image_path_fn(site_key, screenshot_stage, run_id=scenario_run_id)
    if not screenshot.exists():
        return {}
    dom_summary = _strip_nonvisible_html(html_text)[:8000] if isinstance(html_text, str) else ""
    result, meta = _vision_cached_stage_call(
        cache=vision_stage_cache,
        cooldown=vision_stage_cooldown,
        stage="page_kind",
        screenshot_path=str(screenshot),
        runner=lambda: analyze_page_ui_with_vlm(
            str(screenshot),
            site=site_key,
            is_domestic=is_domestic,
            origin=origin or "",
            dest=dest or "",
            depart=depart or "",
            return_date=return_date or "",
            locale=mimic_locale or "",
            html_context=dom_summary,
            include_dom_context=bool(dom_summary),
            timeout_sec=int(get_threshold_fn("scenario_vlm_ui_assist_timeout_sec", 30)),
            max_variants=max(
                1,
                int(get_threshold_fn("scenario_vlm_ui_assist_max_variants", 1)),
            ),
            stage="page_kind",
        ),
    )
    normalized = _normalize_vision_page_kind_result(result)
    payload = {
        "site": site_key,
        "trigger_reason": str(trigger_reason or ""),
        "cached": bool(meta.get("cached", False)),
        "cooldown_skip": bool(meta.get("cooldown_skip", False)),
        "page_kind": normalized.get("page_kind", "unknown"),
        "confidence": normalized.get("confidence", "low"),
        "reason": normalized.get("reason", ""),
        "action_hints": dict(normalized.get("action_hints", {}) or {}),
    }
    log.info("vision.page_kind %s", payload)
    return normalized


def _apply_vision_page_kind_hints_impl(
    page_kind_payload: Dict[str, Any],
    *,
    browser: BrowserSession,
    site_key: str,
    is_domestic: bool,
    vlm_ui_hint: Optional[str],
    optional_click_timeout_ms_fn: Callable,
    safe_click_first_match_fn: Callable,
    service_mode_toggle_step_fn: Callable,
    selector_candidates_fn: Callable,
    vision_modal_dismiss_selectors_fn: Callable,
    google_force_bind_flights_tab_selectors_fn: Callable,
) -> bool:
    """Apply deterministic optional actions from Stage-A hints."""
    hints = (
        page_kind_payload.get("action_hints", {})
        if isinstance(page_kind_payload, dict)
        else {}
    )
    if not isinstance(hints, dict):
        return False
    acted = False
    timeout_ms = optional_click_timeout_ms_fn(site_key or "")
    if bool(hints.get("dismiss_consent")):
        _, selector = safe_click_first_match_fn(
            browser,
            vision_modal_dismiss_selectors_fn(),
            timeout_ms=timeout_ms,
            require_clickable=True,
        )
        acted = acted or bool(selector)
    if bool(hints.get("click_flights_tab")):
        _, selector = safe_click_first_match_fn(
            browser,
            google_force_bind_flights_tab_selectors_fn(),
            timeout_ms=timeout_ms,
            require_clickable=True,
        )
        acted = acted or bool(selector)
    if bool(hints.get("click_domestic_toggle")):
        mode_step = service_mode_toggle_step_fn(
            "google_flights",
            is_domestic=bool(is_domestic),
            vlm_hint=vlm_ui_hint,
            fallback_default=False,
        )
        selectors = selector_candidates_fn((mode_step or {}).get("selector"))
        if selectors:
            _, selector = safe_click_first_match_fn(
                browser,
                selectors,
                timeout_ms=timeout_ms,
                require_clickable=True,
            )
            acted = acted or bool(selector)
    return acted
