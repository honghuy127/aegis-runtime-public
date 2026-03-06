"""Scenario runner artifact helpers."""

import json
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging import get_logger
from utils.run_paths import get_artifacts_dir, get_run_dir, normalize_run_id, write_latest_run_id
from utils.thresholds import get_threshold

log = get_logger("core.scenario_runner")


def write_debug_snapshot(payload, run_id: str) -> None:
    """
    Persist the latest scenario failure details to canonical run directory.
    Canonical location: storage/runs/<run_id>/scenario_last_error.json
    """
    valid_run_id = normalize_run_id(run_id)
    canonical_dir = get_run_dir(valid_run_id)
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_debug_path = canonical_dir / "scenario_last_error.json"

    body = json.dumps(payload, indent=2, ensure_ascii=False)

    try:
        canonical_debug_path.write_text(body, encoding="utf-8")
    except Exception as exc:
        log.warning("scenario.debug_snapshot.canonical_write_failed path=%s error=%s", canonical_debug_path, exc)
    try:
        write_latest_run_id(valid_run_id)
    except Exception as exc:
        log.warning("scenario.debug_snapshot.latest_run_pointer_failed run_id=%s error=%s", valid_run_id, exc)


def write_progress_snapshot(stage: str, run_id: str, **payload) -> None:
    """Write periodic scenario status snapshots for crash-safe diagnostics."""
    record = {"timestamp": datetime.now(UTC).isoformat(), "stage": stage}
    record.update(payload)
    write_debug_snapshot(record, run_id=run_id)


def write_html_snapshot(site_key: str, html: str, stage: str = "last", run_id: str = "") -> None:
    """Persist rolling HTML snapshot to canonical run directory.

    Canonical location: storage/runs/<run_id>/artifacts/scenario_<site>_<stage>.html
    """
    if not isinstance(html, str):
        return
    valid_run_id = normalize_run_id(run_id)
    try:
        canonical_dir = get_artifacts_dir(valid_run_id)
        canonical_dir.mkdir(parents=True, exist_ok=True)
        safe_site = (site_key or "unknown").strip().lower() or "unknown"
        safe_stage = (stage or "last").strip().lower() or "last"
        path = canonical_dir / f"scenario_{safe_site}_{safe_stage}.html"
        path.write_text(html, encoding="utf-8")
        html_dir = canonical_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        (html_dir / f"{safe_site}_{safe_stage}.html").write_text(html, encoding="utf-8")
    except Exception as exc:
        log.warning("scenario.html_snapshot.write_failed site=%s stage=%s run_id=%s error=%s", site_key, stage, run_id, exc)


def write_json_artifact_snapshot(run_id: str, filename: str, payload: Dict[str, Any]) -> None:
    """Persist a structured JSON artifact under the canonical run artifacts directory."""
    if not isinstance(payload, dict):
        return
    valid_run_id = normalize_run_id(run_id)
    raw_name = str(filename or "").strip()
    parts = [p for p in Path(raw_name).parts if p not in {"", ".", ".."}]
    if not parts:
        parts = ["debug.json"]
    safe_parts = [re.sub(r"[^A-Za-z0-9._-]+", "_", str(part or "")) or "x" for part in parts]
    if not safe_parts[-1].endswith(".json"):
        safe_parts[-1] = f"{safe_parts[-1]}.json"
    safe_rel_path = Path(*safe_parts)
    try:
        canonical_dir = get_artifacts_dir(valid_run_id)
        canonical_dir.mkdir(parents=True, exist_ok=True)
        path = canonical_dir / safe_rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning(
            "scenario.json_artifact.write_failed run_id=%s file=%s error=%s",
            valid_run_id,
            str(safe_rel_path),
            exc,
        )


def append_jsonl_artifact(run_id: str, filename: str, payload: Dict[str, Any]) -> None:
    """Append one JSON line into an artifact file under the canonical run artifacts directory."""
    if not isinstance(payload, dict):
        return
    valid_run_id = normalize_run_id(run_id)
    raw_name = str(filename or "").strip()
    parts = [p for p in Path(raw_name).parts if p not in {"", ".", ".."}]
    if not parts:
        parts = ["events.jsonl"]
    safe_parts = [re.sub(r"[^A-Za-z0-9._-]+", "_", str(part or "")) or "x" for part in parts]
    if not safe_parts[-1].endswith(".jsonl"):
        safe_parts[-1] = f"{safe_parts[-1]}.jsonl"
    safe_rel_path = Path(*safe_parts)
    try:
        canonical_dir = get_artifacts_dir(valid_run_id)
        canonical_dir.mkdir(parents=True, exist_ok=True)
        path = canonical_dir / safe_rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")
    except Exception as exc:
        log.warning(
            "scenario.jsonl_artifact.append_failed run_id=%s file=%s error=%s",
            valid_run_id,
            str(safe_rel_path),
            exc,
        )


def write_image_snapshot(browser, site_key: str, stage: str = "last", run_id: str = "") -> None:
    """Persist rolling PNG snapshot to canonical run directory.

    Canonical location: storage/runs/<run_id>/artifacts/scenario_<site>_<stage>.png
    """
    if not bool(get_threshold("scenario_save_visual_snapshot", True)):
        return
    valid_run_id = normalize_run_id(run_id)
    try:
        canonical_dir = get_artifacts_dir(valid_run_id)
        canonical_dir.mkdir(parents=True, exist_ok=True)
        safe_site = (site_key or "unknown").strip().lower() or "unknown"
        safe_stage = (stage or "last").strip().lower() or "last"
        path = canonical_dir / f"scenario_{safe_site}_{safe_stage}.png"
        browser.screenshot(str(path), full_page=True)
        screenshot_dir = canonical_dir / "screenshot"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        browser.screenshot(str(screenshot_dir / f"{safe_site}_{safe_stage}.png"), full_page=True)
    except Exception as exc:
        log.warning("scenario.image_snapshot.write_failed site=%s stage=%s run_id=%s error=%s", site_key, stage, run_id, exc)


def write_route_state_debug(
    *,
    run_id: str,
    site_key: str,
    payload: Dict[str, Any],
) -> None:
    """Persist one compact route/scope debug artifact for current scenario run."""
    if not isinstance(payload, dict):
        return
    safe_run = re.sub(r"[^A-Za-z0-9_-]+", "_", str(run_id or "").strip()) or "run"
    safe_site = re.sub(r"[^A-Za-z0-9_-]+", "_", str(site_key or "").strip()) or "unknown"
    valid_run_id = normalize_run_id(run_id)
    try:
        path = get_artifacts_dir(valid_run_id) / f"route_state_{safe_site}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception as exc:
        log.warning(
            "scenario.route_state.write_failed run_id=%s site=%s error=%s",
            safe_run,
            safe_site,
            exc,
        )


def _snapshot_image_path(site_key: str, stage: str = "last", *, run_id: str) -> Path:
    """Return deterministic debug screenshot path for one site/stage."""
    safe_site = (site_key or "unknown").strip().lower() or "unknown"
    safe_stage = (stage or "last").strip().lower() or "last"
    return get_artifacts_dir(normalize_run_id(run_id)) / f"scenario_{safe_site}_{safe_stage}.png"


def _planner_snapshot_path(site_key: str, stages=None, *, run_id: str) -> str:
    """Resolve most relevant screenshot path for planner multimodal assist."""
    order = list(stages or ["attempt_error", "last", "initial"])
    for stage in order:
        candidate = _snapshot_image_path(site_key, stage, run_id=run_id)
        if candidate.exists():
            return str(candidate)
    return ""


def _write_google_search_commit_probe_artifact(
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
) -> None:
    """Persist a compact pre/post search-commit probe artifact for triage."""
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
        if page_obj is not None:
            from core.scenario_runner import _compact_selector_dom_probe

            payload["selector_dom_probe"] = _compact_selector_dom_probe(
                page_obj,
                [str(s) for s in selector_list],
                max_selectors=10,
                max_matches=2,
                max_html_chars=320,
                max_text_chars=120,
            )
    except Exception as exc:
        payload["selector_dom_probe_error"] = str(exc)[:200]
    _write_json_artifact_snapshot(
        run_id,
        f"google_search_commit_{str(artifact_label or 'unknown').strip().lower()}_probe.json",
        payload,
    )
