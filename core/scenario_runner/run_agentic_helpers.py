"""Helper functions for run_agentic_scenario orchestration."""

from typing import Any, Callable, Dict, Optional

from core.route_binding import dom_route_bind_probe as _dom_route_bind_probe_fn
from utils.evidence import write_service_evidence_checkpoint


def _route_probe_for_html_impl(
    html_text: str,
    *,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
) -> Dict[str, Any]:
    """Probe HTML for route binding information."""
    if not (origin and dest and depart):
        return {}
    try:
        probe = _dom_route_bind_probe_fn(
            html_text,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date or "",
        )
    except Exception:
        probe = {}
    if not isinstance(probe, dict):
        return {}
    return {
        "route_bound": bool(probe.get("route_bound")),
        "support": str(probe.get("support", "none") or "none"),
        "reason": str(probe.get("reason", "") or ""),
        "observed": dict(probe.get("observed", {}) or {}),
    }


def _write_evidence_checkpoint_impl(
    checkpoint: str,
    *,
    scenario_run_id: str,
    site_key: str,
    url: str,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    trip_type: Optional[str],
    evidence_dump_enabled: bool,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Write evidence checkpoint to storage."""
    if not evidence_dump_enabled:
        return
    body = {
        "run_id": scenario_run_id,
        "service": site_key,
        "url": url,
        "intended": {
            "origin": origin or "",
            "dest": dest or "",
            "depart": depart or "",
            "return_date": return_date or "",
            "trip_type": trip_type or "",
        },
    }
    if isinstance(payload, dict) and payload:
        body.update(payload)
    write_service_evidence_checkpoint(
        run_id=scenario_run_id,
        service=site_key,
        checkpoint=checkpoint,
        payload=body,
        enabled=True,
    )
