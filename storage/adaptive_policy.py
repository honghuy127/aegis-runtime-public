"""Adaptive runtime policy for self-healing planning/extraction behavior."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from storage.runs import list_llm_metrics
from utils.thresholds import get_threshold


STORE_PATH = Path("storage/adaptive_policy.json")

_DEFAULT_SITE_STATE = {
    "success_count": 0,
    "failure_count": 0,
    "consecutive_success": 0,
    "consecutive_failure": 0,
    "price_found_count": 0,
    "heuristic_miss_count": 0,
    "llm_parse_failed_count": 0,
    "scenario_timeout_count": 0,
    "last_error": "",
    "last_reason": "",
}


def _safe_int(value: Any) -> int:
    """Convert value to non-negative integer."""
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _safe_bool(value: Any) -> bool:
    """Interpret common boolean values safely."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_site_state(payload: Any) -> Dict[str, Any]:
    """Normalize one site policy state payload."""
    state = dict(_DEFAULT_SITE_STATE)
    if not isinstance(payload, dict):
        return state
    for key in (
        "success_count",
        "failure_count",
        "consecutive_success",
        "consecutive_failure",
        "price_found_count",
        "heuristic_miss_count",
        "llm_parse_failed_count",
        "scenario_timeout_count",
    ):
        state[key] = _safe_int(payload.get(key, 0))
    state["last_error"] = str(payload.get("last_error", "") or "")
    state["last_reason"] = str(payload.get("last_reason", "") or "")
    return state


def load_policy() -> Dict[str, Any]:
    """Load adaptive policy store from disk."""
    if not STORE_PATH.exists():
        return {"sites": {}}
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sites": {}}
    if not isinstance(payload, dict):
        return {"sites": {}}
    sites = payload.get("sites", {})
    if not isinstance(sites, dict):
        sites = {}
    normalized = {}
    for site_key, site_payload in sites.items():
        key = str(site_key).strip()
        if not key:
            continue
        normalized[key] = _normalize_site_state(site_payload)
    return {"sites": normalized}


def save_policy(policy: Dict[str, Any]) -> None:
    """Persist adaptive policy store."""
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(policy, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_site_state(policy: Dict[str, Any], site_key: str) -> Dict[str, Any]:
    """Get normalized mutable site state."""
    sites = policy.setdefault("sites", {})
    if site_key not in sites:
        sites[site_key] = dict(_DEFAULT_SITE_STATE)
    sites[site_key] = _normalize_site_state(sites[site_key])
    return sites[site_key]


def record_service_outcome(
    *,
    site_key: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error: str = "",
) -> None:
    """Record one service outcome to drive future runtime policy."""
    if not isinstance(site_key, str) or not site_key.strip():
        return
    policy = load_policy()
    state = _get_site_state(policy, site_key.strip())
    is_ok = str(status).strip().lower() == "ok"
    reason = ""
    if isinstance(result, dict):
        reason = str(result.get("reason", "") or "")

    if is_ok:
        state["success_count"] += 1
        state["consecutive_success"] += 1
        state["consecutive_failure"] = 0
        if isinstance(result, dict) and result.get("price") is not None:
            state["price_found_count"] += 1
        if reason == "heuristic_no_route_match":
            state["heuristic_miss_count"] += 1
        if reason == "llm_parse_failed":
            state["llm_parse_failed_count"] += 1
        state["last_reason"] = reason
        state["last_error"] = ""
    else:
        state["failure_count"] += 1
        state["consecutive_failure"] += 1
        state["consecutive_success"] = 0
        error_text = str(error or "")
        if "timeout" in error_text.lower():
            state["scenario_timeout_count"] += 1
        state["last_error"] = error_text

    save_policy(policy)


def _recent_llm_timeout_rate(mode: str, *, limit: int = 120) -> float:
    """Estimate recent LLM timeout/circuit pressure from persisted metrics."""
    rows = list_llm_metrics(limit=limit, mode=mode)
    if not rows:
        return 0.0
    timeout_like = 0
    for row in rows:
        status = str(row.get("status") or "").lower()
        category = str(row.get("category") or "").lower()
        if status == "error" and category in {"timeout", "circuit_open"}:
            timeout_like += 1
    return float(timeout_like) / float(len(rows))


def _default_profile() -> Dict[str, Any]:
    """Build baseline profile from thresholds."""
    return {
        "light_try_llm_plan_on_fast_plan_failure": bool(
            get_threshold("light_mode_try_llm_plan_on_fast_plan_failure", True)
        ),
        "llm_light_planner_timeout_sec": int(
            get_threshold("llm_light_planner_timeout_sec", 35)
        ),
        "light_try_llm_extract_on_heuristic_miss": bool(
            get_threshold("light_mode_try_llm_extract_on_heuristic_miss", True)
        ),
        "llm_light_extract_timeout_sec": int(
            get_threshold("llm_light_extract_timeout_sec", 25)
        ),
        "reason": "default",
    }


def recommend_runtime_profile(site_key: str, *, llm_mode: str = "full") -> Dict[str, Any]:
    """Recommend adaptive runtime profile for one service and mode."""
    profile = _default_profile()
    if str(llm_mode).strip().lower() != "light":
        profile["reason"] = "non_light_mode"
        return profile

    policy = load_policy()
    state = _get_site_state(policy, site_key)
    timeout_rate = _recent_llm_timeout_rate("light")

    reasons = []
    # Avoid compounding stalls under heavy local LLM timeout pressure.
    if timeout_rate >= 0.70:
        planner_cap = int(
            get_threshold("adaptive_high_timeout_pressure_light_planner_timeout_sec", 20)
        )
        extract_cap = int(
            get_threshold("adaptive_high_timeout_pressure_light_extract_timeout_sec", 18)
        )
        profile["light_try_llm_plan_on_fast_plan_failure"] = False
        profile["llm_light_planner_timeout_sec"] = min(
            profile["llm_light_planner_timeout_sec"],
            planner_cap,
        )
        profile["llm_light_extract_timeout_sec"] = min(
            profile["llm_light_extract_timeout_sec"],
            extract_cap,
        )
        reasons.append("high_llm_timeout_pressure")
    else:
        # Self-heal: when deterministic misses repeat, allow short LLM escalations.
        deterministic_miss = (
            state["heuristic_miss_count"] + state["llm_parse_failed_count"]
        )
        if state["consecutive_failure"] >= 2 or deterministic_miss >= 2:
            profile["light_try_llm_extract_on_heuristic_miss"] = True
            profile["llm_light_extract_timeout_sec"] = max(
                profile["llm_light_extract_timeout_sec"],
                30,
            )
            reasons.append("enable_extract_escalation")
        if state["consecutive_failure"] >= 3:
            profile["light_try_llm_plan_on_fast_plan_failure"] = True
            profile["llm_light_planner_timeout_sec"] = max(
                profile["llm_light_planner_timeout_sec"],
                45,
            )
            reasons.append("enable_planner_escalation")

    # Recover toward baseline after stable success streak.
    if state["consecutive_success"] >= 3 and timeout_rate < 0.40:
        profile = _default_profile()
        reasons = ["recovered_to_default"]

    profile["reason"] = ",".join(reasons) if reasons else "default"
    profile["timeout_rate_light"] = round(timeout_rate, 3)
    profile["consecutive_failure"] = state["consecutive_failure"]
    profile["consecutive_success"] = state["consecutive_success"]
    return profile


def apply_runtime_profile_env(profile: Dict[str, Any]) -> None:
    """Apply adaptive profile knobs via environment for current process."""
    if not isinstance(profile, dict):
        return
    os_map = {
        "FLIGHT_WATCHER_LIGHT_TRY_LLM_PLAN_ON_FAST_PLAN_FAILURE": _safe_bool(
            profile.get("light_try_llm_plan_on_fast_plan_failure", True)
        ),
        "FLIGHT_WATCHER_LLM_LIGHT_PLANNER_TIMEOUT_SEC": _safe_int(
            profile.get("llm_light_planner_timeout_sec", 35)
        ),
        "FLIGHT_WATCHER_LIGHT_TRY_LLM_EXTRACT_ON_HEURISTIC_MISS": _safe_bool(
            profile.get("light_try_llm_extract_on_heuristic_miss", True)
        ),
        "FLIGHT_WATCHER_LLM_LIGHT_EXTRACT_TIMEOUT_SEC": _safe_int(
            profile.get("llm_light_extract_timeout_sec", 25)
        ),
    }
    os.environ["FLIGHT_WATCHER_LIGHT_TRY_LLM_PLAN_ON_FAST_PLAN_FAILURE"] = (
        "1" if os_map["FLIGHT_WATCHER_LIGHT_TRY_LLM_PLAN_ON_FAST_PLAN_FAILURE"] else "0"
    )
    os.environ["FLIGHT_WATCHER_LLM_LIGHT_PLANNER_TIMEOUT_SEC"] = str(
        os_map["FLIGHT_WATCHER_LLM_LIGHT_PLANNER_TIMEOUT_SEC"]
    )
    os.environ["FLIGHT_WATCHER_LIGHT_TRY_LLM_EXTRACT_ON_HEURISTIC_MISS"] = (
        "1" if os_map["FLIGHT_WATCHER_LIGHT_TRY_LLM_EXTRACT_ON_HEURISTIC_MISS"] else "0"
    )
    os.environ["FLIGHT_WATCHER_LLM_LIGHT_EXTRACT_TIMEOUT_SEC"] = str(
        os_map["FLIGHT_WATCHER_LLM_LIGHT_EXTRACT_TIMEOUT_SEC"]
    )
