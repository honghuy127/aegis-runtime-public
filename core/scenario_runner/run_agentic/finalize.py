"""Finalization helpers for run_agentic_scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Optional


def finalize_retries_exhausted_return(
    *,
    browser: Any,
    site_key: str,
    scenario_run_id: str,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    trip_type: str,
    is_domestic: Optional[bool],
    max_transit: Optional[int],
    max_retries: int,
    max_turns: int,
    last_error: Optional[Exception],
    plan: Any,
    write_debug_snapshot_fn: Callable[..., None],
    write_html_snapshot_fn: Callable[..., None],
    write_image_snapshot_fn: Callable[..., None],
    scenario_return_fn: Callable[..., str],
    logger: Any,
) -> str:
    """Persist retries-exhausted diagnostics and return fallback html."""
    write_debug_snapshot_fn(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "stage": "retries_exhausted",
            "site_key": site_key,
            "url": url,
            "origin": origin,
            "dest": dest,
            "depart": depart,
            "return_date": return_date,
            "trip_type": trip_type,
            "is_domestic": is_domestic,
            "max_transit": max_transit,
            "max_retries": max_retries,
            "max_turns": max_turns,
            "error": str(last_error),
            "exception_type": type(last_error).__name__ if last_error else None,
            "plan": plan,
        },
        run_id=scenario_run_id,
    )
    try:
        write_html_snapshot_fn(site_key, browser.content(), stage="retries_exhausted", run_id=scenario_run_id)
        write_image_snapshot_fn(browser, site_key, stage="retries_exhausted", run_id=scenario_run_id)
    except Exception as retries_snapshot_exc:
        logger.warning(
            "scenario.retries_exhausted.snapshot_failed site=%s run_id=%s error=%s",
            site_key,
            scenario_run_id,
            retries_snapshot_exc,
        )

    logger.warning(
        "scenario.retries_exhausted site=%s retries=%d returning_fallback_html",
        site_key,
        max_retries,
    )
    fallback_html = ""
    try:
        fallback_html = browser.content()
    except Exception as html_exc:
        logger.error(
            "scenario.retries_exhausted.html_capture_failed error=%s",
            html_exc,
        )

    return scenario_return_fn(
        fallback_html,
        ready=False,
        reason="retries_exhausted",
        scope_class="unknown",
        route_bound=False,
        route_support="none",
    )
