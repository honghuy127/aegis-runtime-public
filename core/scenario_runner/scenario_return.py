"""Scenario return builder for run_agentic_scenario."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from utils.graph_policy_stats import GraphPolicyStats
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ReturnBuilderContext:
    """Context for building scenario return payload."""
    scenario_started_at: float
    site_key: Optional[str]
    scenario_run_id: str
    router: Any  # EventRouter
    url: str
    origin: Optional[str]
    dest: Optional[str]
    depart: Optional[str]
    return_date: Optional[str]
    graph_stats: Optional[GraphPolicyStats]
    browser: Any  # BrowserDriver
    write_evidence_checkpoint_fn: Callable  # _write_evidence_checkpoint
    write_progress_snapshot_fn: Callable  # _write_progress_snapshot
    build_route_state_fallback_fn: Callable  # _build_route_state_return_fallback_payload
    build_extract_verdict_fn: Callable  # _build_route_state_scenario_extract_verdict
    write_route_state_debug_fn: Callable  # _write_route_state_debug
    get_artifacts_dir_fn: Callable  # get_artifacts_dir


def scenario_return_impl(
    html_text: str,
    *,
    ready: bool,
    reason: str,
    scope_class: str = "unknown",
    route_bound: Optional[bool] = None,
    route_support: str = "unknown",
    context: ReturnBuilderContext,
) -> str:
    """Build and return scenario result with hardened HTML guarantees."""
    elapsed_s = time.monotonic() - context.scenario_started_at
    log.info(
        "scenario.span site=%s run_id=%s elapsed_s=%.3f ready=%s scope_class=%s route_bound=%s route_support=%s reason=%s",
        context.site_key,
        context.scenario_run_id,
        elapsed_s,
        ready,
        scope_class,
        route_bound,
        route_support,
        reason,
    )
    # Log router summary before return
    summary = context.router.get_event_summary()
    if summary:
        log.info(
            "scenario.router_summary site=%s %s",
            context.site_key,
            " ".join(f"{k}={v}" for k, v in summary.items()),
        )
    context.write_evidence_checkpoint_fn(
        "after_results_ready_check",
        payload={
            "readiness": {
                "ready": bool(ready),
                "override_reason": str(reason or ""),
            },
            "scope_guard": {
                "page_class": str(scope_class or "unknown"),
            },
            "route_bind": {
                "route_bound": route_bound,
                "support": str(route_support or "unknown"),
            },
        },
    )
    try:
        context.write_progress_snapshot_fn(
            stage="scenario_return",
            run_id=context.scenario_run_id,
            site_key=context.site_key,
            url=context.url,
            ready=bool(ready),
            reason=str(reason or ""),
            scope_class=str(scope_class or "unknown"),
            route_bound=route_bound,
            route_support=str(route_support or "unknown"),
        )
    except Exception:
        pass
    try:
        route_state_path = context.get_artifacts_dir_fn(str(context.scenario_run_id)) / f"route_state_{context.site_key}.json"
        route_state_payload: Dict[str, Any] = {}
        if route_state_path.exists():
            try:
                raw = json.loads(route_state_path.read_text(encoding="utf-8"))
                route_state_payload = dict(raw) if isinstance(raw, dict) else {}
            except Exception:
                route_state_payload = {}
        if not route_state_payload:
            route_state_payload = context.build_route_state_fallback_fn(
                run_id=context.scenario_run_id,
                site_key=context.site_key,
                origin=context.origin or "",
                dest=context.dest or "",
                depart=context.depart or "",
                return_date=context.return_date or "",
                reason=str(reason or ""),
                ready=bool(ready),
                scope_class=str(scope_class or "unknown"),
                route_bound=route_bound if isinstance(route_bound, bool) else None,
                route_support=str(route_support or "unknown"),
            )
        route_state_payload["run_id"] = str(context.scenario_run_id or "")
        route_state_payload["service"] = str(context.site_key or "")
        route_state_payload.setdefault(
            "expected",
            {
                "origin": str(context.origin or ""),
                "dest": str(context.dest or ""),
                "depart": str(context.depart or ""),
                "return": str(context.return_date or ""),
            },
        )
        existing_route_verdict = (
            route_state_payload.get("route_bind_verdict")
            if isinstance(route_state_payload.get("route_bind_verdict"), dict)
            else {}
        )
        route_verdict = dict(existing_route_verdict)
        if isinstance(route_bound, bool):
            route_verdict["route_bound"] = bool(route_bound)
        if str(route_support or "").strip():
            route_verdict["support"] = str(route_support or "")
        route_state_payload["route_bind_verdict"] = route_verdict
        scope_verdicts = (
            route_state_payload.get("scope_verdicts")
            if isinstance(route_state_payload.get("scope_verdicts"), dict)
            else {}
        )
        scope_final_existing = str((scope_verdicts or {}).get("final", "") or "").strip().lower()
        scope_final_value = str(scope_class or "unknown").strip().lower() or "unknown"
        if scope_final_existing not in {"", "unknown"} and scope_final_value == "unknown":
            scope_final_for_verdict = scope_final_existing
        else:
            scope_final_for_verdict = scope_final_value
        route_state_payload["scenario_return_summary"] = {
            "ready": bool(ready),
            "reason": str(reason or ""),
            "scope_class": scope_final_for_verdict,
        }
        route_state_payload["scenario_extract_verdict"] = context.build_extract_verdict_fn(
            site_key=str(context.site_key or ""),
            route_bind_verdict=route_verdict,
            scope_final=scope_final_for_verdict,
            ready=bool(ready),
            scenario_reason=str(reason or ""),
        )
        context.write_route_state_debug_fn(
            run_id=context.scenario_run_id,
            site_key=context.site_key,
            payload=route_state_payload,
        )
    except Exception as route_state_fallback_exc:
        log.warning(
            "scenario.route_state.return_fallback_failed run_id=%s site=%s error=%s",
            context.scenario_run_id,
            context.site_key,
            route_state_fallback_exc,
        )

    # Save graph policy stats if collected (gated by config)
    if context.graph_stats and context.graph_stats.transitions:
        try:
            artifacts_dir = Path("storage/runs") / context.scenario_run_id / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            stats_file = artifacts_dir / "graph_policy_stats.json"
            context.graph_stats.save_to_file(stats_file)
            log.info("graph_stats.saved path=%s transitions=%d", stats_file, len(context.graph_stats.transitions))
        except Exception as save_exc:
            log.warning("graph_stats.save_failed error=%s", save_exc)

    # Guarantee string return: attempt last-chance browser capture if needed
    if not isinstance(html_text, str) or not html_text:
        log.warning(
            "scenario.return.html_not_valid type=%s empty=%s attempting_browser_content",
            type(html_text).__name__,
            isinstance(html_text, str) and not html_text,
        )
        try:
            fallback_html = context.browser.content()
            if isinstance(fallback_html, str) and fallback_html:
                log.info(
                    "scenario.return.using_fallback_html source=browser.content length=%d reason=%s",
                    len(fallback_html),
                    reason,
                )
                return fallback_html
            elif isinstance(fallback_html, str):
                log.error(
                    "scenario.return.browser_content_empty returning_empty_string reason=%s",
                    reason,
                )
                log.info(
                    "scenario.return.using_fallback_html source=empty_string reason=%s",
                    reason,
                )
                return ""
            else:
                log.error(
                    "scenario.return.browser_content_invalid type=%s returning_empty_string",
                    type(fallback_html).__name__,
                )
                log.info(
                    "scenario.return.using_fallback_html source=empty_string reason=%s",
                    reason,
                )
                return ""
        except Exception as exc:
            log.error(
                "scenario.return.browser_content_failed error=%s returning_empty_string",
                exc,
            )
            log.info(
                "scenario.return.using_fallback_html source=empty_string_after_exception reason=%s",
                reason,
            )
            return ""
    return html_text
