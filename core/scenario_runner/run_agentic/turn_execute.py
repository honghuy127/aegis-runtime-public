"""Turn execution helper for run_agentic_scenario."""

from __future__ import annotations

from typing import Any, Callable, Dict


def _derive_turn_page_kind(
    *,
    browser: Any,
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    is_results_ready_fn: Callable[..., bool],
    google_quick_page_class_fn: Callable[..., str],
) -> str:
    """Best-effort page-kind inference used for graph/evidence labeling."""
    try:
        html = str(browser.content() or "")
    except Exception:
        html = ""
    if not html:
        return "search_form"

    if (site_key or "").strip().lower() == "google_flights":
        quick = str(
            google_quick_page_class_fn(
                html,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
            )
            or "unknown"
        ).strip().lower()
        if quick == "flight_only":
            return "search_results"
        if quick in {"flight_hotel_package", "irrelevant_page", "garbage_page"}:
            return "non_flight"

    if bool(
        is_results_ready_fn(
            html,
            site_key=site_key,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
        )
    ):
        return "search_results"
    return "search_form"


def execute_turn_plan(
    *,
    execute_plan_fn: Callable[..., Any],
    browser: Any,
    plan: Any,
    site_key: str,
    blocked_selectors: Any,
    router: Any,
    evidence_dump_enabled: bool,
    scenario_run_id: str,
    url: str,
    google_recovery_mode: bool,
    get_threshold_fn: Callable[[str, Any], Any],
    graph_stats: Any,
    attempt: int,
    turn_idx: int,
    mimic_locale: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    is_results_ready_fn: Callable[..., bool],
    google_quick_page_class_fn: Callable[..., str],
) -> Any:
    """Execute one turn plan with standardized evidence context."""
    page_kind = _derive_turn_page_kind(
        browser=browser,
        site_key=site_key,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        is_results_ready_fn=is_results_ready_fn,
        google_quick_page_class_fn=google_quick_page_class_fn,
    )
    evidence_ctx: Dict[str, Any] = {
        "enabled": evidence_dump_enabled,
        "run_id": scenario_run_id,
        "service": site_key,
        "url": url,
        "checkpoint_before_search": "after_fills_before_search",
        "google_recovery_route_core_gate_enabled": bool(
            google_recovery_mode
            and bool(
                get_threshold_fn(
                    "google_flights_recovery_require_route_core_before_date_fill_enabled",
                    True,
                )
            )
        ),
    }
    return execute_plan_fn(
        browser,
        plan,
        site_key=site_key,
        blocked_selectors=blocked_selectors,
        router=router,
        evidence_ctx=evidence_ctx,
        graph_stats=graph_stats,
        attempt=attempt,
        turn=turn_idx,
        page_kind=page_kind,
        locale=mimic_locale or "",
    )
