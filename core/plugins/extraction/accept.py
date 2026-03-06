"""Central acceptance gate for plugin extraction candidates."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from core.plugins.extraction.normalize import normalize_plugin_candidate
from core.plugins.extraction.schemas import NON_FLIGHT_PAGE_CLASSES


AcceptanceResult = Tuple[bool, Dict[str, Any], str]


def _is_unusable_candidate(candidate: Dict[str, Any]) -> Optional[str]:
    """Return rejection reason when candidate is empty/unusable."""
    if not isinstance(candidate, dict) or not candidate:
        return "empty_candidate"
    if candidate.get("price") is None:
        return "missing_price"
    if not isinstance(candidate.get("price"), (int, float)):
        return "invalid_price"
    return None


def _normalize_threshold_getter(
    thresholds_getter: Optional[Callable[[str, Any], Any]],
) -> Callable[[str, Any], Any]:
    if callable(thresholds_getter):
        return thresholds_getter
    return lambda _key, default: default


def accept_candidate(
    candidate: Dict[str, Any],
    *,
    html: str,
    site_key: str,
    existing_scope_guard_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    thresholds_getter: Optional[Callable[[str, Any], Any]] = None,
    strategy_key: str = "",
) -> AcceptanceResult:
    """Accept/reject one plugin candidate using existing scope-guard hooks.

    - Conservative and fail-closed on malformed candidate.
    - Never raises outward.
    - Reuses existing scope-guard callable once when provided.
    """
    _ = html
    try:
        threshold = _normalize_threshold_getter(thresholds_getter)
        normalized = normalize_plugin_candidate(candidate, strategy_key=strategy_key)
        unusable = _is_unusable_candidate(normalized)
        if unusable:
            return False, {}, unusable

        if normalized.get("route_bound") is False:
            return False, {}, "route_unbound"

        if str(normalized.get("page_class", "") or "") in NON_FLIGHT_PAGE_CLASSES:
            return False, {}, "non_flight_page_class"

        if str(normalized.get("trip_product", "") or "") == "flight_hotel_package":
            return False, {}, "non_flight_trip_product"

        require_route_bound = bool(
            threshold("extract_google_require_route_context", True)
        ) and str(site_key or "").strip().lower() == "google_flights"

        guarded = normalized
        if callable(existing_scope_guard_fn):
            try:
                guarded = existing_scope_guard_fn(normalized)
            except Exception:
                return False, {}, "scope_guard_error"

        if not isinstance(guarded, dict) or not guarded:
            return False, {}, "scope_guard_empty"

        post_unusable = _is_unusable_candidate(guarded)
        if post_unusable:
            return False, {}, post_unusable

        if guarded.get("route_bound") is False:
            return False, {}, "route_unbound"

        if str(guarded.get("page_class", "") or "") in NON_FLIGHT_PAGE_CLASSES:
            return False, {}, "non_flight_page_class"

        if str(guarded.get("trip_product", "") or "") == "flight_hotel_package":
            return False, {}, "non_flight_trip_product"

        if require_route_bound and guarded.get("route_bound") is False:
            return False, {}, "route_unbound_required"

        return True, guarded, "accepted"
    except Exception:
        return False, {}, "accept_error"
