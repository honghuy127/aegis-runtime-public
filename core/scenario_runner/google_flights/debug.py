"""Google Flights debug and evidence collection helpers.

Move-only extraction from core/scenario_runner.py.
No behavior changes.
"""

from datetime import datetime, UTC
from typing import Any, Callable, Dict, List, Optional


def write_google_search_commit_probe_artifact(
    *,
    run_id: str,
    browser,
    artifact_label: str,
    selectors: List[str],
    search_result: Dict[str, Any],
    site_key: str = "google_flights",
    attempt: int = 0,
    turn: int = 0,
    step_index: Optional[int] = None,
    page_url: str = "",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    compact_selector_dom_probe_fn: Callable = None,
    write_json_artifact_fn: Callable = None,
) -> None:
    """Persist a compact pre/post search-commit probe artifact for triage.

    Args:
        run_id: Evidence run identifier
        browser: BrowserSession instance
        artifact_label: Label for artifact file (e.g., "pre_click", "post_click")
        selectors: List of search button selectors
        search_result: Search commit result dict with ok/strategy/error/timing
        site_key: Site identifier (default: google_flights)
        attempt: Scenario attempt number
        turn: Plan turn number
        step_index: Optional step index in plan
        page_url: Current page URL
        origin: Origin airport code
        dest: Destination airport code
        depart: Departure date
        return_date: Return date
        compact_selector_dom_probe_fn: Function to probe DOM with selectors
        write_json_artifact_fn: Function to write JSON artifact
    """
    if not run_id:
        return
    if (site_key or "").strip().lower() != "google_flights":
        return
    result = dict(search_result or {})
    selector_list = [str(s or "")[:180] for s in list(selectors or [])[:10]]
    payload: Dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "google_flights",
        "stage": "search_commit_probe",
        "label": str(artifact_label or "")[:80],
        "attempt": int(attempt) + 1,
        "turn": int(turn) + 1,
        "step_index": (int(step_index) if isinstance(step_index, int) else None),
        "intended": {
            "url": str(page_url or ""),
            "origin": str(origin or ""),
            "dest": str(dest or ""),
            "depart": str(depart or ""),
            "return_date": str(return_date or ""),
        },
        "selectors": selector_list,
        "search_commit": {
            "ok": bool(result.get("ok")),
            "strategy": str(result.get("strategy", "") or ""),
            "selector_used": str(result.get("selector_used", "") or ""),
            "results_signal_found": bool(result.get("results_signal_found")),
            "error": str(result.get("error", "") or ""),
            "post_click_wait_ms": int(result.get("post_click_wait_ms") or 0),
            "url_changed": bool(result.get("url_changed", False)),
            "fragment_changed": bool(result.get("fragment_changed", False)),
            "elapsed_ms": int(result.get("elapsed_ms") or 0),
            "click_elapsed_ms": int(result.get("click_elapsed_ms") or 0),
            "enter_elapsed_ms": int(result.get("enter_elapsed_ms") or 0),
            "results_wait_timeout_ms": int(result.get("results_wait_timeout_ms") or 0),
            "post_click_ready_timeout_ms": int(result.get("post_click_ready_timeout_ms") or 0),
            "search_click_attempts": int(result.get("search_click_attempts") or 0),
            "selector_candidates_count": int(result.get("selector_candidates_count") or 0),
            "clickable_candidates_count": int(result.get("clickable_candidates_count") or 0),
            "results_candidates_count": int(result.get("results_candidates_count") or 0),
            "route_ctx_available": bool(result.get("route_ctx_available", False)),
            "probe_pre": dict(result.get("probe_pre") or {}),
            "probe_post": dict(result.get("probe_post") or {}),
        },
    }
    try:
        page_obj = getattr(browser, "page", None)
        if page_obj is not None and compact_selector_dom_probe_fn is not None:
            payload["selector_dom_probe"] = compact_selector_dom_probe_fn(
                page_obj,
                [str(s) for s in selector_list],
                max_selectors=10,
                max_matches=2,
                max_html_chars=320,
                max_text_chars=120,
            )
    except Exception as exc:
        payload["selector_dom_probe_error"] = str(exc)[:200]
    if write_json_artifact_fn is not None:
        write_json_artifact_fn(
            run_id,
            f"google_search_commit_{str(artifact_label or 'unknown').strip().lower()}_probe.json",
            payload,
        )


def write_google_date_selector_probe(
    *,
    browser,
    site_key: str,
    evidence_run_id: str,
    stage_label: str,
    role_key: str,
    target_value: str,
    selectors_for_probe: List[str],
    attempt: int,
    turn: int,
    extra: Optional[Dict[str, Any]] = None,
    compact_selector_dom_probe_fn: Callable,
    write_json_artifact_fn: Callable,
) -> None:
    """Write bounded selector/DOM probe artifact for Google date open/fill steps.

    Args:
        browser: BrowserSession instance
        site_key: Site identifier
        evidence_run_id: Evidence run identifier
        stage_label: Stage label (e.g., "pre_open", "post_fill")
        role_key: Role key (depart, return)
        target_value: Target date value
        selectors_for_probe: List of selectors to probe
        attempt: Scenario attempt number
        turn: Plan turn number
        extra: Optional extra metadata
        compact_selector_dom_probe_fn: Function to probe DOM with selectors
        write_json_artifact_fn: Function to write JSON artifact
    """
    if (site_key or "").strip().lower() != "google_flights":
        return
    if not evidence_run_id:
        return
    page_obj = getattr(browser, "page", None)
    payload: Dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "stage": str(stage_label or "")[:80],
        "service": "google_flights",
        "role": str(role_key or "")[:24],
        "target_value": str(target_value or "")[:32],
        "attempt": int(attempt) + 1,
        "turn": int(turn) + 1,
        "selectors": [str(s)[:180] for s in list(selectors_for_probe or [])[:10]],
    }
    if isinstance(extra, dict) and extra:
        payload["extra"] = dict(extra)
    try:
        if page_obj is not None:
            payload["selector_dom_probe"] = compact_selector_dom_probe_fn(
                page_obj,
                [str(s) for s in list(selectors_for_probe or [])[:10]],
                max_selectors=10,
                max_matches=2,
                max_html_chars=360,
                max_text_chars=140,
            )
    except Exception as exc:
        payload["selector_dom_probe_error"] = str(exc)[:200]
    write_json_artifact_fn(
        evidence_run_id,
        f"google_date_fill_{str(role_key or '').strip().lower()}_{str(stage_label or '').strip().lower()}_selector_probe.json",
        payload,
    )


def create_google_date_debug_probe_callback(
    evidence_run_id: str,
    role: str,
    date_fill_value: str,
    date_fill_selectors: List[str],
    debug_probe_fn: Callable,
) -> Callable[[str, Dict[str, Any]], None]:
    """Create callback for Google date fill debug probing.

    Returns a callback that collects selector candidates from debug payload
    and triggers debug probe artifact creation.

    Args:
        evidence_run_id: Evidence run identifier
        role: Fill role (depart, return)
        date_fill_value: Date value being filled
        date_fill_selectors: Primary date fill selectors
        debug_probe_fn: Debug probe function to call

    Returns:
        Callback function that accepts (stage_label, payload)
    """
    def callback(stage_label: str, payload: Dict[str, Any]) -> None:
        if not evidence_run_id:
            return
        extra = dict(payload or {}) if isinstance(payload, dict) else {}
        probe_selectors: List[str] = []
        for key in ("opener_selectors", "role_selectors", "selectors_tried"):
            vals = extra.get(key)
            if isinstance(vals, list):
                for item in vals:
                    s = str(item or "").strip()
                    if s and s not in probe_selectors:
                        probe_selectors.append(s)
        if not probe_selectors:
            probe_selectors = list(date_fill_selectors)
        debug_probe_fn(
            stage_label=str(stage_label or ""),
            role_key=str(role or ""),
            target_value=date_fill_value,
            selectors_for_probe=probe_selectors[:10],
            extra=extra,
        )

    return callback
