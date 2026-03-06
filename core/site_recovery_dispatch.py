"""Site-dispatched recovery orchestration helpers.

These helpers keep `core.scenario_runner` orchestration generic while allowing
site-specific recovery hooks to be provided by the caller.
"""

from typing import Any, Callable, Dict, Optional


def collab_limits_from_thresholds(
    site_key: str,
    *,
    google_limits_fn: Callable[[], Dict[str, int | bool]],
) -> Dict[str, int | bool]:
    """Return bounded collaborative-recovery limits for a site."""
    normalized = str(site_key or "").strip().lower()
    if normalized == "google_flights":
        return google_limits_fn()
    return {
        "enabled": False,
        "max_vlm": 0,
        "max_repair": 0,
        "max_planner": 0,
        "route_core_only_first": True,
        "planner_timeout_sec": 45,
    }


def pre_date_gate(
    *,
    site_key: str,
    html: str,
    page: Optional[Any] = None,
    expected_origin: str,
    expected_dest: str,
    expected_depart: str = "",
    expected_return: str = "",
    google_gate_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Run a site-specific pre-date-fill recovery gate (Phase A pattern)."""
    normalized = str(site_key or "").strip().lower()
    if normalized == "google_flights":
        return google_gate_fn(
            html=html,
            page=page,
            expected_origin=expected_origin,
            expected_dest=expected_dest,
            expected_depart=expected_depart,
            expected_return=expected_return,
        )
    return {"ok": True, "reason": "unsupported_site", "evidence": {}}


def pre_date_gate_canonical_reason(site_key: str) -> str:
    """Canonical step reason emitted when the site pre-date gate blocks."""
    normalized = str(site_key or "").strip().lower()
    if normalized == "google_flights":
        return "route_core_before_date_fill_unverified"
    return "site_recovery_before_date_fill_unverified"


def collab_trigger_reason(site_key: str) -> str:
    """Canonical date-failure reason that unlocks collaborative recovery."""
    return pre_date_gate_canonical_reason(site_key)


def collab_scope_repair_plan(
    *,
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str] = None,
    trip_type: str = "one_way",
    is_domestic: bool = False,
    scope_class: str = "unknown",
    vlm_hint: Optional[dict] = None,
    google_scope_repair_plan_fn: Callable[..., Any],
):
    """Build a site-specific bounded scope/route recovery plan."""
    normalized = str(site_key or "").strip().lower()
    if normalized == "google_flights":
        return google_scope_repair_plan_fn(
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            trip_type=trip_type,
            is_domestic=is_domestic,
            scope_class=scope_class,
            vlm_hint=vlm_hint,
        )
    return None


def collab_focus_plan(
    plan,
    *,
    site_key: str,
    origin: str = "",
    dest: str = "",
    google_focus_plan_fn: Callable[..., Any],
):
    """Apply a site-specific 'route-core-first' shaping pass to a plan."""
    normalized = str(site_key or "").strip().lower()
    if normalized == "google_flights":
        return google_focus_plan_fn(plan, origin=origin, dest=dest)
    return plan


def should_attempt_recovery_collab_after_date_failure(
    *,
    site_key: str,
    recovery_mode: bool,
    date_failure_reason: str,
) -> bool:
    """Gate collaborative recovery follow-up on site-specific trigger reasons."""
    if not recovery_mode:
        return False
    trigger_reason = collab_trigger_reason(site_key)
    return bool(trigger_reason) and str(date_failure_reason or "").strip().lower() == str(
        trigger_reason
    ).strip().lower()
