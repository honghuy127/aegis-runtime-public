"""VLM/Vision probing and caching helpers."""

import hashlib
import os
from typing import Any, Callable, Dict

from core.scenario_runner.notes import _sanitize_runtime_note
from core.scenario_runner.google_flights.service_runner_bridge import (
    _dedupe_selectors,
    _verification_confidence_rank,
)
from core.ui_tokens import build_button_text_selectors, prioritize_tokens
from utils.knowledge_rules import get_knowledge_rule_tokens


def _normalize_page_class(value: str) -> str:
    """Normalize scope class labels used by LLM/VLM judges."""
    # Import locally to avoid circular dependency
    from core.scenario_runner import _normalize_page_class as _impl
    return _impl(value)


def _vision_screenshot_fingerprint(path: str, *, max_prefix_bytes: int = 65536) -> str:
    """Build stable fingerprint for one screenshot path to support VLM result cache."""
    image_path = str(path or "").strip()
    if not image_path:
        return ""
    try:
        stat = os.stat(image_path)
    except Exception:
        return ""
    digest = hashlib.sha1()
    digest.update(str(image_path).encode("utf-8", errors="ignore"))
    digest.update(str(int(stat.st_size)).encode("ascii"))
    digest.update(str(int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))).encode("ascii"))
    try:
        with open(image_path, "rb") as handle:
            digest.update(handle.read(max(1024, int(max_prefix_bytes))))
    except Exception:
        return ""
    return digest.hexdigest()


def _vision_cached_stage_call(
    *,
    cache: Dict[str, Dict[str, Dict[str, Any]]],
    cooldown: Dict[str, str],
    stage: str,
    screenshot_path: str,
    runner: Callable[[], Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one vision stage with screenshot-keyed cache and same-turn cooldown."""
    stage_key = str(stage or "").strip().lower() or "unknown"
    fingerprint = _vision_screenshot_fingerprint(screenshot_path)
    if not fingerprint:
        fingerprint = f"path:{str(screenshot_path or '').strip()}"
    stage_cache = cache.setdefault(stage_key, {})
    meta = {
        "stage": stage_key,
        "fingerprint": fingerprint,
        "cached": False,
        "cooldown_skip": False,
    }
    if fingerprint in stage_cache:
        meta["cached"] = True
        cooldown[stage_key] = fingerprint
        return dict(stage_cache.get(fingerprint) or {}), meta
    if cooldown.get(stage_key) == fingerprint:
        meta["cooldown_skip"] = True
        return {}, meta
    result: Dict[str, Any] = {}
    try:
        raw = runner()
        if isinstance(raw, dict):
            result = dict(raw)
    except Exception:
        result = {}
    stage_cache[fingerprint] = dict(result)
    cooldown[stage_key] = fingerprint
    return dict(result), meta


def _normalize_vision_page_kind_result(raw: Any) -> Dict[str, Any]:
    """Normalize page-kind vision payload to strict schema with safe defaults."""
    out = {
        "page_kind": "unknown",
        "action_hints": {
            "dismiss_consent": False,
            "click_flights_tab": False,
            "click_domestic_toggle": False,
            "notes": "",
        },
        "confidence": "low",
        "reason": "",
    }
    if not isinstance(raw, dict):
        return out
    page_kind = str(raw.get("page_kind", "") or "").strip().lower()
    page_class = _normalize_page_class(str(raw.get("page_class", "") or ""))
    reason = _sanitize_runtime_note(str(raw.get("reason", "") or ""), max_chars=140)
    blocked_by_modal = bool(raw.get("blocked_by_modal"))
    trip_product = str(raw.get("trip_product", "") or "").strip().lower()
    if page_kind not in {
        "flights_search",
        "flights_results",
        "consent",
        "interstitial",
        "package",
        "irrelevant",
        "unknown",
    }:
        if blocked_by_modal or any(token in reason.lower() for token in ("consent", "cookie", "modal")):
            page_kind = "consent"
        elif page_class == "flight_only":
            page_kind = "flights_results"
        elif page_class == "flight_hotel_package" or trip_product == "flight_hotel_package":
            page_kind = "package"
        elif page_class == "irrelevant_page":
            page_kind = "irrelevant"
        elif page_class == "garbage_page":
            page_kind = "interstitial"
        else:
            page_kind = "unknown"
    hints = raw.get("action_hints")
    if isinstance(hints, dict):
        out["action_hints"]["dismiss_consent"] = bool(hints.get("dismiss_consent", False))
        out["action_hints"]["click_flights_tab"] = bool(hints.get("click_flights_tab", False))
        out["action_hints"]["click_domestic_toggle"] = bool(hints.get("click_domestic_toggle", False))
        out["action_hints"]["notes"] = _sanitize_runtime_note(
            str(hints.get("notes", "") or ""),
            max_chars=96,
        )
    if blocked_by_modal:
        out["action_hints"]["dismiss_consent"] = True
    if page_kind in {"package", "irrelevant"}:
        out["action_hints"]["click_flights_tab"] = True
    confidence = str(raw.get("confidence", "") or "").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium" if page_kind in {"flights_search", "flights_results", "consent", "package", "irrelevant"} else "low"
    out["page_kind"] = page_kind
    out["confidence"] = confidence
    out["reason"] = reason
    return out


def _normalize_vision_fill_verify_result(raw: Any) -> Dict[str, Any]:
    """Normalize post-fill verify payload to strict schema with deterministic defaults."""
    out = {
        "origin_text": "",
        "dest_text": "",
        "depart_text": "",
        "return_text": "",
        "mismatch_fields": [],
        "confidence": "low",
        "reason": "",
        "suggested_fix": {"field": "none", "hint": ""},
    }
    if not isinstance(raw, dict):
        return out
    fields = raw.get("fields")
    field_map = fields if isinstance(fields, dict) else {}
    out["origin_text"] = str((field_map.get("origin", {}) or {}).get("observed", "") or "")
    out["dest_text"] = str((field_map.get("dest", {}) or {}).get("observed", "") or "")
    out["depart_text"] = str((field_map.get("depart", {}) or {}).get("observed", "") or "")
    out["return_text"] = str((field_map.get("return", {}) or {}).get("observed", "") or "")
    mismatches = []
    for role_name in ("origin", "dest", "depart", "return"):
        info = field_map.get(role_name, {})
        if isinstance(info, dict) and info and info.get("matched") is False:
            mismatches.append(role_name)
    out["mismatch_fields"] = mismatches
    confidence = str(raw.get("confidence", "") or "").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        route_bound = bool(raw.get("route_bound"))
        if route_bound:
            confidence = "high"
        elif field_map:
            confidence = "medium"
        else:
            confidence = "low"
    out["confidence"] = confidence
    out["reason"] = _sanitize_runtime_note(str(raw.get("reason", "") or ""), max_chars=140)
    suggested_fix = raw.get("suggested_fix")
    if isinstance(suggested_fix, dict):
        field = str(suggested_fix.get("field", "none") or "none").strip().lower()
        if field not in {"origin", "dest", "depart", "return", "none"}:
            field = "none"
        out["suggested_fix"] = {
            "field": field,
            "hint": _sanitize_runtime_note(str(suggested_fix.get("hint", "") or ""), max_chars=96),
        }
    elif mismatches:
        out["suggested_fix"] = {
            "field": mismatches[0],
            "hint": "reopen_field_and_select_suggestion",
        }
    return out


def _should_run_vision_page_kind_probe(
    *,
    enabled: bool,
    trigger_reason: str,
    scope_class: str = "unknown",
) -> bool:
    """Stage-A gate: probe only for non-flight/unknown readiness paths."""
    if not bool(enabled):
        return False
    reason = str(trigger_reason or "").strip().lower()
    scope = _normalize_page_class(scope_class)
    # Page-kind VLM probes do not resolve deterministic route/date fill failures.
    # Skipping avoids long model calls after we already know the page is the wrong form state.
    if (
        reason.startswith("route_fill_mismatch")
        or reason.startswith("date_fill_failure_")
        or reason.startswith("rebind_unready_non_flight_scope_")
    ):
        return False
    if reason.startswith("non_flight_scope_"):
        return True
    return scope in {"unknown", "irrelevant_page"}


def _should_run_vision_post_fill_verify(
    *,
    enabled: bool,
    deterministic_reason: str,
    deterministic_confidence: str,
    min_confidence: str,
    commit_reason: str,
    deterministic_available: bool,
    legacy_verify_enabled: bool,
) -> bool:
    """Stage-B gate: run VLM verify only when deterministic checks are weak or unavailable."""
    if not bool(enabled):
        return False
    reason = str(deterministic_reason or "").strip().lower()
    confidence = str(deterministic_confidence or "").strip().lower()
    if reason == "mismatch_placeholder_or_missing":
        return True
    if str(commit_reason or "").strip().lower() == "commit_alias_mismatch":
        return True
    if _verification_confidence_rank(confidence) < _verification_confidence_rank(min_confidence):
        return True
    return bool(legacy_verify_enabled) and (not bool(deterministic_available))


def _vision_modal_dismiss_selectors() -> list[str]:
    """Build safe consent/modal dismiss selectors from shared token rules."""
    # Import locally to avoid circular dependency
    from core.scenario_runner.env import _current_mimic_locale

    tokens = prioritize_tokens(
        get_knowledge_rule_tokens("modal_control_tokens"),
        locale_hint=_current_mimic_locale(),
    )[:8]
    selectors: list[str] = []
    for token in tokens:
        label = str(token or "").strip()
        if not label:
            continue
        selectors.extend(
            [
                f"button[aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
            ]
        )
    selectors.extend(build_button_text_selectors(tokens))
    return _dedupe_selectors(selectors)
