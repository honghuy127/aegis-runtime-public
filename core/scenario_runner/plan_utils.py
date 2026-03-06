from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.scenario_runner.google_flights.service_runner_bridge import (
    _selector_candidates,
    _dedupe_selectors,
    _maybe_append_bare_text_selectors,
    _build_click_selectors_for_tokens,
)
from core.scenario_runner.plan_hygiene import _infer_fill_role, _compatible_for_role_impl


def is_valid_plan(plan) -> bool:
    if not isinstance(plan, list) or not plan:
        return False
    allowed_actions = {"fill", "click", "wait", "wait_msec"}
    for step in plan:
        if not isinstance(step, dict):
            return False
        action = step.get("action")
        selector = step.get("selector")
        if action not in allowed_actions:
            return False
        if action == "wait_msec":
            if not isinstance(step.get("duration_ms"), int):
                return False
        else:
            if isinstance(selector, str):
                if not selector:
                    return False
            elif isinstance(selector, list):
                if not selector or not all(isinstance(s, str) and s for s in selector):
                    return False
            else:
                return False
        if action == "fill":
            value = step.get("value")
            if not isinstance(value, str) or not value:
                return False
    return True


def plan_has_required_fill_roles(plan, trip_type: str, site_key: str = "") -> bool:
    if not isinstance(plan, list):
        return False
    seen = set()
    nonoptional_roles = set()
    for step in plan:
        if not isinstance(step, dict):
            continue
        if step.get("action") != "fill":
            continue
        # is_irrelevant_contact_fill_step moved elsewhere; infer role instead
        role = _infer_fill_role(step)
        if role in {"origin", "dest", "depart", "return"}:
            seen.add(role)
            if (not bool(step.get("optional"))) or bool(step.get("required_for_actionability")):
                nonoptional_roles.add(role)

    required = {"origin", "dest", "depart"}
    if not required.issubset(seen):
        return False
    if (site_key or "").strip().lower() == "google_flights":
        if not required.issubset(nonoptional_roles):
            return False
    if trip_type == "round_trip" and "return" not in seen:
        return True
    return True


def is_actionable_plan(plan, trip_type: str, site_key: str = "") -> bool:
    return is_valid_plan(plan) and plan_has_required_fill_roles(plan, trip_type, site_key=site_key)


def coerce_plan_bundle(payload):
    if isinstance(payload, list):
        return payload, []
    if isinstance(payload, dict):
        steps = payload.get("steps")
        if not isinstance(steps, list):
            for key in ("plan", "actions"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    steps = candidate
                    break
        notes = payload.get("notes")
        if isinstance(notes, str):
            notes = [notes]
        elif not isinstance(notes, list):
            notes = []
        return steps if isinstance(steps, list) else None, notes
    return None, []


def is_irrelevant_contact_fill_step(step) -> bool:
    if not isinstance(step, dict) or step.get("action") != "fill":
        return False
    # selector_blob is provided by scenario_runner.selector_utils via wrapper
    from core.scenario_runner.selector_utils import selector_blob

    selector_blob_val = selector_blob(step.get("selector"))
    if not selector_blob_val:
        return False
    from core.scenario_runner import _ROUTE_FIELD_HINT_RE, _CONTACT_AUTH_HINT_RE  # type: ignore

    if _ROUTE_FIELD_HINT_RE.search(selector_blob_val):
        return False
    return bool(_CONTACT_AUTH_HINT_RE.search(selector_blob_val))


def plan_auth_profile_fill_selectors(plan):
    if not isinstance(plan, list):
        return []
    suspicious = []
    for step in plan:
        if not is_irrelevant_contact_fill_step(step):
            continue
        for selector in _selector_candidates(step.get("selector")):
            if selector not in suspicious:
                suspicious.append(selector)
    return suspicious


def plan_has_click_token(plan, tokens) -> bool:
    if not isinstance(plan, list):
        return False
    wanted = [t for t in tokens if isinstance(t, str) and t]
    if not wanted:
        return False
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "click":
            continue
        blob = " ".join(_selector_candidates(step.get("selector"))).lower()
        if any(token.lower() in blob for token in wanted):
            return True
    return False


def maybe_enrich_wait_step(plan, knowledge):
    if not isinstance(plan, list):
        return plan

    learned_wait = []
    for key in ("local_wait_selectors", "global_wait_selectors"):
        raw = knowledge.get(key, []) if isinstance(knowledge, dict) else []
        if isinstance(raw, list):
            learned_wait.extend([s for s in raw if isinstance(s, str) and s.strip()])

    if not learned_wait:
        return plan

    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "wait":
            continue
        existing = set(_selector_candidates(step.get("selector")))
        if existing.intersection(set(learned_wait)):
            return plan

    merged = []
    for selector in learned_wait:
        if selector not in merged:
            merged.append(selector)
        if len(merged) >= 8:
            break
    if not merged:
        return plan
    return list(plan) + [{"action": "wait", "selector": merged}]


def maybe_prepend_modal_step(plan, knowledge):
    if not isinstance(plan, list):
        return plan
    selectors = knowledge.get("local_modal_selectors", []) if isinstance(knowledge, dict) else []
    if not isinstance(selectors, list):
        selectors = []
    selectors = [s for s in selectors if isinstance(s, str) and s.strip()][:6]
    if not selectors:
        return plan

    for step in plan[:2]:
        if not isinstance(step, dict) or step.get("action") != "click":
            continue
        text = " ".join(_selector_candidates(step.get("selector"))).lower()
        if any(token in text for token in ("cookie", "consent", "close", "dismiss", "同意", "閉じる")):
            return plan
    return [{"action": "click", "selector": selectors, "optional": True}] + list(plan)


def prepend_ranked_selectors(current, prioritized, *, limit: int = 12):
    merged = []
    for value in prioritized + current:
        if not isinstance(value, str) or not value.strip():
            continue
        if value not in merged:
            merged.append(value)
        if len(merged) >= max(1, int(limit)):
            break
    return merged


def maybe_prioritize_fill_steps_from_knowledge(plan, knowledge):
    if not isinstance(plan, list):
        return plan
    out = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            out.append(step)
            continue
        role = _infer_fill_role(step)
        if not role:
            out.append(step)
            continue
        from core.scenario_runner.knowledge_helpers import fill_role_knowledge_key

        local_key = fill_role_knowledge_key(role, local=True)
        global_key = fill_role_knowledge_key(role, local=False)
        local_vals = knowledge.get(local_key, []) if isinstance(knowledge, dict) else []
        global_vals = knowledge.get(global_key, []) if isinstance(knowledge, dict) else []
        local_vals = [s for s in local_vals if isinstance(s, str) and s.strip()]
        global_vals = [s for s in global_vals if isinstance(s, str) and s.strip()]

        def _compatible_for_role(selector: str) -> bool:
            return _compatible_for_role_impl(selector, role)

        local_vals = [s for s in local_vals if _compatible_for_role(s)]
        global_vals = [s for s in global_vals if _compatible_for_role(s)]
        if not local_vals and not global_vals:
            out.append(step)
            continue
        selectors = _selector_candidates(step.get("selector"))
        merged = prepend_ranked_selectors(selectors, local_vals[:6] + global_vals[:4], limit=12)
        new_step = dict(step)
        new_step["selector"] = merged if len(merged) > 1 else merged[0]
        out.append(new_step)
    return out


def maybe_filter_failed_selectors(plan, knowledge):
    if not isinstance(plan, list):
        return plan
    blocked = []
    if isinstance(knowledge, dict):
        blocked.extend([s for s in knowledge.get("local_failed_selectors", []) if isinstance(s, str) and s.strip()])
        blocked.extend([s for s in knowledge.get("global_failed_selectors", []) if isinstance(s, str) and s.strip()])
    blocked_set = set(blocked)
    if not blocked_set:
        return plan
    out = []
    for step in plan:
        if not isinstance(step, dict):
            out.append(step)
            continue
        selectors = _selector_candidates(step.get("selector"))
        if len(selectors) <= 1:
            out.append(step)
            continue
        kept = [s for s in selectors if s not in blocked_set]
        if kept:
            kept = kept + [s for s in selectors if s in blocked_set]
        else:
            kept = selectors
        new_step = dict(step)
        new_step["selector"] = kept if len(kept) > 1 else kept[0]
        out.append(new_step)
    return out


def reorder_search_selectors_for_locale(selectors: list[str], *, locale_hint: str = "") -> list[str]:
    items = [s for s in (selectors or []) if isinstance(s, str) and s.strip()]
    if not items:
        return []
    from core.ui_tokens import normalize_visible_text

    lang = normalize_visible_text(str(locale_hint or "")).split("-", 1)[0]
    if lang not in {"ja", "en"}:
        return list(items)
    preferred: list[str] = []
    neutral: list[str] = []
    fallback: list[str] = []
    for selector in items:
        lowered = str(selector or "").lower()
        # simple heuristics for en/ja preferred buckets
        has_ja = any(ch >= "\u3040" and ch <= "\u30ff" for ch in selector)
        if lang == "ja" and has_ja:
            preferred.append(selector)
        elif lang == "en" and selector.isascii():
            preferred.append(selector)
        else:
            neutral.append(selector)
    return preferred + neutral + fallback
