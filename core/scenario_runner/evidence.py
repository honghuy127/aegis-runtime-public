"""Evidence checkpoint writing for execute_plan."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from utils.evidence import write_service_evidence_checkpoint
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class EvidenceContext:
    """Context for writing evidence checkpoints."""
    evidence_enabled: bool
    evidence_run_id: Optional[str]
    evidence_service: str
    evidence_checkpoint: str
    evidence_url: str
    browser_content_fn: Callable  # browser.content() callable
    dom_route_bind_probe_fn: Callable
    expected_route_values: Dict[str, Any]


def write_before_search_evidence_impl(
    form_state: Dict[str, Any],
    route_verify: Optional[Dict[str, Any]],
    *,
    context: EvidenceContext,
) -> None:
    """Write evidence checkpoint before search step."""
    if not (context.evidence_enabled and context.evidence_run_id and context.evidence_service):
        return

    current_html = ""
    try:
        current_html = str(context.browser_content_fn() or "")
    except Exception:
        current_html = ""

    route_bind = {}
    if (
        current_html
        and context.expected_route_values.get("origin")
        and context.expected_route_values.get("dest")
        and context.expected_route_values.get("depart")
    ):
        try:
            probe = context.dom_route_bind_probe_fn(
                current_html,
                origin=context.expected_route_values.get("origin", ""),
                dest=context.expected_route_values.get("dest", ""),
                depart=context.expected_route_values.get("depart", ""),
                return_date=context.expected_route_values.get("return", ""),
            )
        except Exception:
            probe = {}
        if isinstance(probe, dict) and probe:
            route_bind = {
                "route_bound": bool(probe.get("route_bound")),
                "support": str(probe.get("support", "none") or "none"),
                "reason": str(probe.get("reason", "") or ""),
                "observed": dict(probe.get("observed", {}) or {}),
            }

    write_service_evidence_checkpoint(
        run_id=context.evidence_run_id,
        service=context.evidence_service,
        checkpoint=context.evidence_checkpoint,
        enabled=True,
        payload={
            "url": context.evidence_url,
            "intended": {
                "origin": context.expected_route_values.get("origin", ""),
                "dest": context.expected_route_values.get("dest", ""),
                "depart": context.expected_route_values.get("depart", ""),
                "return_date": context.expected_route_values.get("return", ""),
            },
            "form_state": dict(form_state or {}),
            "route_bind": route_bind,
            "readiness": {
                "ready": not bool((route_verify or {}).get("block")),
                "override_reason": str((route_verify or {}).get("reason", "") or ""),
            },
            "fill_commit": {
                "dest_committed": bool((route_verify or {}).get("dest_committed", False)),
                "dest_commit_reason": str(
                    (route_verify or {}).get("dest_commit_reason", "") or ""
                ),
                "suggestion_used": bool((route_verify or {}).get("suggestion_used", False)),
            },
        },
    )
