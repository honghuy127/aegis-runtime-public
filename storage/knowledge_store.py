"""Persistent global/local interaction knowledge for scenario planning."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.services import is_supported_service, url_matches_service_domain
from storage.knowledge_inference import (
    contains_domestic_token,
    contains_international_token,
    extract_failed_selectors,
    failure_action,
    failure_reason,
    infer_fill_role_from_selector,
    selector_looks_modal_control,
    selector_looks_search_submit,
    suggested_turns,
    url_looks_domestic,
    url_looks_international,
    url_looks_package_bundle,
)


STORE_PATH = Path("storage/knowledge_store.json")
DEFAULT_USER_KEY = "public"

_DEFAULT_SITE_PAYLOAD: Dict[str, Any] = {
    "selector_counts": {},
    "wait_selector_counts": {},
    "fill_role_selector_counts": {
        "origin": {},
        "dest": {},
        "depart": {},
        "return": {},
    },
    "search_click_selector_counts": {},
    "modal_selector_counts": {},
    "domestic_toggle_selector_counts": {},
    "international_toggle_selector_counts": {},
    "failed_selector_counts": {},
    "failed_action_counts": {},
    "failure_reason_counts": {},
    "url_hints": {
        "generic": {},
        "domestic": {},
        "international": {},
        "package": {},
    },
    "site_type_scores": {
        "split": 0,
        "single": 0,
    },
    "site_type": None,
    "domain_mode_success_counts": {
        "domestic": 0,
        "international": 0,
    },
    "turn_histogram": {},
    "multi_turn_scores": {
        "single_turn": 0,
        "multi_turn": 0,
    },
    "success_count": 0,
    "last_success_plan": None,
}

_DEFAULT_USER_PAYLOAD: Dict[str, Any] = {
    "global": {
        "selector_counts": {},
        "wait_selector_counts": {},
        "fill_role_selector_counts": {
            "origin": {},
            "dest": {},
            "depart": {},
            "return": {},
        },
        "search_click_selector_counts": {},
        "failed_selector_counts": {},
        "site_success_counts": {},
        "url_hint_counts": {},
    },
    "local": {},
}

_DEFAULT_STORE: Dict[str, Any] = {
    "users": {},
}


def _safe_int(value: Any) -> int:
    """Convert unknown values to non-negative int safely."""
    try:
        out = int(value)
    except Exception:
        return 0
    return max(0, out)


def _deep_copy_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-safe deep copy of a dictionary payload."""
    return json.loads(json.dumps(payload))


def _normalize_user_id(user_id: Optional[str]) -> str:
    """Normalize user namespace key for shared knowledge storage."""
    if not isinstance(user_id, str):
        return DEFAULT_USER_KEY
    text = user_id.strip().lower()
    if not text:
        return DEFAULT_USER_KEY
    return text


def _normalize_counter_map(payload: Any) -> Dict[str, int]:
    """Normalize selector/url counter payload to string->int mapping."""
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, int] = {}
    for key, value in payload.items():
        text = str(key).strip()
        if not text:
            continue
        out[text] = _safe_int(value)
    return out


def _normalize_url_hints(payload: Any) -> Dict[str, Dict[str, int]]:
    """Normalize url-hint groups into generic/domestic/international maps."""
    out = {
        "generic": {},
        "domestic": {},
        "international": {},
        "package": {},
    }
    if isinstance(payload, dict):
        for key in ("generic", "domestic", "international", "package"):
            out[key] = _normalize_counter_map(payload.get(key))
    return out


def _normalize_fill_role_counts(payload: Any) -> Dict[str, Dict[str, int]]:
    """Normalize fill-role selector counters keyed by field role."""
    out = {
        "origin": {},
        "dest": {},
        "depart": {},
        "return": {},
    }
    if not isinstance(payload, dict):
        return out
    for role in out.keys():
        out[role] = _normalize_counter_map(payload.get(role))
    return out


def _normalize_domain_mode_success_counts(payload: Any) -> Dict[str, int]:
    """Normalize domestic/international success counters."""
    out = {"domestic": 0, "international": 0}
    if not isinstance(payload, dict):
        return out
    out["domestic"] = _safe_int(payload.get("domestic", 0))
    out["international"] = _safe_int(payload.get("international", 0))
    return out


def _normalize_turn_histogram(payload: Any) -> Dict[str, int]:
    """Normalize turn histogram map ('1','2',...) -> count."""
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, int] = {}
    for key, value in payload.items():
        text = str(key).strip()
        if not text:
            continue
        try:
            turns = int(text)
        except Exception:
            continue
        if turns < 1:
            continue
        out[str(turns)] = _safe_int(value)
    return out


def _normalize_multi_turn_scores(payload: Any) -> Dict[str, int]:
    """Normalize coarse single-vs-multi-turn evidence scores."""
    out = {"single_turn": 0, "multi_turn": 0}
    if not isinstance(payload, dict):
        return out
    out["single_turn"] = _safe_int(payload.get("single_turn", 0))
    out["multi_turn"] = _safe_int(payload.get("multi_turn", 0))
    return out


def _normalize_site_payload(payload: Any) -> Dict[str, Any]:
    """Normalize one site payload while preserving backward compatibility."""
    site = _deep_copy_dict(_DEFAULT_SITE_PAYLOAD)
    if not isinstance(payload, dict):
        return site

    site["selector_counts"] = _normalize_counter_map(payload.get("selector_counts"))
    site["wait_selector_counts"] = _normalize_counter_map(payload.get("wait_selector_counts"))
    site["fill_role_selector_counts"] = _normalize_fill_role_counts(
        payload.get("fill_role_selector_counts")
    )
    site["search_click_selector_counts"] = _normalize_counter_map(
        payload.get("search_click_selector_counts")
    )
    site["modal_selector_counts"] = _normalize_counter_map(payload.get("modal_selector_counts"))
    site["domestic_toggle_selector_counts"] = _normalize_counter_map(
        payload.get("domestic_toggle_selector_counts")
    )
    site["international_toggle_selector_counts"] = _normalize_counter_map(
        payload.get("international_toggle_selector_counts")
    )
    site["failed_selector_counts"] = _normalize_counter_map(payload.get("failed_selector_counts"))
    site["failed_action_counts"] = _normalize_counter_map(payload.get("failed_action_counts"))
    site["failure_reason_counts"] = _normalize_counter_map(payload.get("failure_reason_counts"))
    site["url_hints"] = _normalize_url_hints(payload.get("url_hints"))

    # Backward-compatible migrations.
    legacy_generic_urls = payload.get("url_hint_counts")
    if isinstance(legacy_generic_urls, dict):
        site["url_hints"]["generic"] = _normalize_counter_map(legacy_generic_urls)

    scores = payload.get("site_type_scores")
    if isinstance(scores, dict):
        site["site_type_scores"]["split"] = _safe_int(scores.get("split", 0))
        site["site_type_scores"]["single"] = _safe_int(scores.get("single", 0))

    site_type = payload.get("site_type")
    if site_type in ("domestic_international_split", "single_flow"):
        site["site_type"] = site_type
    else:
        site["site_type"] = None

    site["success_count"] = _safe_int(payload.get("success_count", 0))
    site["domain_mode_success_counts"] = _normalize_domain_mode_success_counts(
        payload.get("domain_mode_success_counts")
    )
    site["turn_histogram"] = _normalize_turn_histogram(payload.get("turn_histogram"))
    site["multi_turn_scores"] = _normalize_multi_turn_scores(payload.get("multi_turn_scores"))
    site["last_success_plan"] = payload.get("last_success_plan") if isinstance(
        payload.get("last_success_plan"),
        list,
    ) else None
    return site


def _normalize_user_payload(payload: Any) -> Dict[str, Any]:
    """Normalize one user-scoped payload."""
    user = _deep_copy_dict(_DEFAULT_USER_PAYLOAD)
    if not isinstance(payload, dict):
        return user

    global_in = payload.get("global", {})
    if isinstance(global_in, dict):
        user["global"]["selector_counts"] = _normalize_counter_map(
            global_in.get("selector_counts")
        )
        user["global"]["wait_selector_counts"] = _normalize_counter_map(
            global_in.get("wait_selector_counts")
        )
        user["global"]["fill_role_selector_counts"] = _normalize_fill_role_counts(
            global_in.get("fill_role_selector_counts")
        )
        user["global"]["search_click_selector_counts"] = _normalize_counter_map(
            global_in.get("search_click_selector_counts")
        )
        user["global"]["failed_selector_counts"] = _normalize_counter_map(
            global_in.get("failed_selector_counts")
        )
        user["global"]["site_success_counts"] = _normalize_counter_map(
            global_in.get("site_success_counts")
        )
        user["global"]["url_hint_counts"] = _normalize_counter_map(
            global_in.get("url_hint_counts")
        )

    local_in = payload.get("local", {})
    if isinstance(local_in, dict):
        for site_key, site_payload in local_in.items():
            name = str(site_key).strip()
            if not name:
                continue
            user["local"][name] = _normalize_site_payload(site_payload)
    return user


def _normalize_store(payload: Any) -> Dict[str, Any]:
    """Normalize on-disk JSON payload into expected dictionary shape."""
    store = _deep_copy_dict(_DEFAULT_STORE)
    if not isinstance(payload, dict):
        return store

    # New schema.
    users_payload = payload.get("users")
    if isinstance(users_payload, dict):
        for user_key, user_payload in users_payload.items():
            normalized_key = _normalize_user_id(str(user_key))
            store["users"][normalized_key] = _normalize_user_payload(user_payload)

    # Backward-compatible schema migration (single unscoped payload).
    if isinstance(payload.get("global"), dict) or isinstance(payload.get("local"), dict):
        legacy_user_payload = {
            "global": payload.get("global", {}),
            "local": payload.get("local", {}),
        }
        merged = _normalize_user_payload(legacy_user_payload)
        existing = store["users"].get(DEFAULT_USER_KEY)
        if existing:
            # Merge legacy data into existing public scope.
            for key in (
                "selector_counts",
                "wait_selector_counts",
                "fill_role_selector_counts",
                "search_click_selector_counts",
                "failed_selector_counts",
                "site_success_counts",
                "url_hint_counts",
            ):
                if key == "fill_role_selector_counts":
                    for role in ("origin", "dest", "depart", "return"):
                        existing["global"][key][role].update(
                            merged["global"][key].get(role, {})
                        )
                else:
                    existing["global"][key].update(merged["global"][key])
            existing["local"].update(merged["local"])
        else:
            store["users"][DEFAULT_USER_KEY] = merged

    if not store["users"]:
        store["users"][DEFAULT_USER_KEY] = _deep_copy_dict(_DEFAULT_USER_PAYLOAD)
    return store


def _selector_list(selector: Any) -> List[str]:
    """Normalize selector field into a flat list."""
    if isinstance(selector, str):
        return [selector] if selector.strip() else []
    if isinstance(selector, list):
        return [s for s in selector if isinstance(s, str) and s.strip()]
    return []


def _increment(counter: Dict[str, int], key: str, amount: int = 1) -> None:
    """Increment one count entry."""
    text = str(key).strip()
    if not text:
        return
    counter[text] = _safe_int(counter.get(text, 0)) + amount


def _top_items(counter: Dict[str, int], limit: int = 8) -> List[str]:
    """Return top keys ordered by frequency then lexical key."""
    ranked = sorted(counter.items(), key=lambda item: (-_safe_int(item[1]), item[0]))
    return [k for k, _ in ranked[:limit]]


def _top_items_filtered(
    counter: Dict[str, int],
    *,
    limit: int = 8,
    min_count: int = 1,
) -> List[str]:
    """Return ranked items while filtering weak evidence entries."""
    ranked = sorted(counter.items(), key=lambda item: (-_safe_int(item[1]), item[0]))
    out = []
    for key, value in ranked:
        if _safe_int(value) < max(1, int(min_count)):
            continue
        out.append(key)
        if len(out) >= limit:
            break
    return out


def _ensure_user_payload(store: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Ensure user namespace exists and return it."""
    users = store.setdefault("users", {})
    if user_id not in users:
        users[user_id] = _deep_copy_dict(_DEFAULT_USER_PAYLOAD)
    return users[user_id]


def _ensure_site_payload(user_payload: Dict[str, Any], site_key: str) -> Dict[str, Any]:
    """Ensure site payload exists in user namespace and return it."""
    local = user_payload.setdefault("local", {})
    if site_key not in local:
        local[site_key] = _deep_copy_dict(_DEFAULT_SITE_PAYLOAD)
    return local[site_key]


def _classify_site_type(site_payload: Dict[str, Any]) -> Optional[str]:
    """Compute site type classification from accumulated evidence."""
    scores = site_payload.get("site_type_scores", {})
    split_score = _safe_int(scores.get("split", 0))
    single_score = _safe_int(scores.get("single", 0))
    url_hints = site_payload.get("url_hints", {})
    domestic_urls = url_hints.get("domestic", {}) if isinstance(url_hints, dict) else {}
    international_urls = (
        url_hints.get("international", {}) if isinstance(url_hints, dict) else {}
    )

    has_domestic_urls = bool(domestic_urls)
    has_international_urls = bool(international_urls)

    if split_score >= 2 and split_score >= single_score:
        return "domestic_international_split"
    if has_domestic_urls and has_international_urls:
        return "domestic_international_split"

    # With only one-sided split evidence, keep type unresolved UNLESS we have strong single-flow signal.
    if has_domestic_urls or has_international_urls:
        # Check if this is actually single-flow with insufficient hint evidence
        if single_score >= 3 and single_score > split_score:
            # This is single-flow with only one-sided hints - clear the one-sided hints
            if has_domestic_urls and not has_international_urls:
                url_hints["domestic"] = {}
            elif has_international_urls and not has_domestic_urls:
                url_hints["international"] = {}
            return "single_flow"
        return None

    if single_score >= 3 and single_score > split_score:
        return "single_flow"
    return None


def load_store() -> Dict[str, Any]:
    """Load and normalize knowledge store from disk."""
    if not STORE_PATH.exists():
        return _normalize_store({})
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _normalize_store({})
    return _normalize_store(payload)


def save_store(store: Dict[str, Any]) -> None:
    """Persist one normalized knowledge store payload."""
    normalized = _normalize_store(store)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")


def get_knowledge(site_key: str, *, user_id: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
    """Return aggregated global + site-local knowledge for plan generation."""
    scoped_user = _normalize_user_id(user_id)
    store = load_store()
    user_payload = _ensure_user_payload(store, scoped_user)
    global_payload = user_payload["global"]
    site_payload = user_payload["local"].get(site_key, _deep_copy_dict(_DEFAULT_SITE_PAYLOAD))
    url_hints = site_payload.get("url_hints", {})
    site_type = site_payload.get("site_type")
    global_fill = global_payload.get("fill_role_selector_counts", {})
    local_fill = site_payload.get("fill_role_selector_counts", {})
    suggested_turns_value = suggested_turns(site_payload.get("turn_histogram", {}))

    return {
        "user_id": scoped_user,
        "site_type": site_type,
        "site_type_scores": dict(site_payload.get("site_type_scores", {})),
        "global_selectors": _top_items(global_payload.get("selector_counts", {}), limit=limit),
        "global_wait_selectors": _top_items(
            global_payload.get("wait_selector_counts", {}),
            limit=limit,
        ),
        "global_url_hints": _top_items(global_payload.get("url_hint_counts", {}), limit=10),
        "local_selectors": _top_items(site_payload.get("selector_counts", {}), limit=limit),
        "local_wait_selectors": _top_items(site_payload.get("wait_selector_counts", {}), limit=limit),
        "global_fill_origin_selectors": _top_items(
            global_fill.get("origin", {}),
            limit=limit,
        ),
        "global_fill_dest_selectors": _top_items(
            global_fill.get("dest", {}),
            limit=limit,
        ),
        "global_fill_depart_selectors": _top_items(
            global_fill.get("depart", {}),
            limit=limit,
        ),
        "global_fill_return_selectors": _top_items(
            global_fill.get("return", {}),
            limit=limit,
        ),
        "local_fill_origin_selectors": _top_items(
            local_fill.get("origin", {}),
            limit=limit,
        ),
        "local_fill_dest_selectors": _top_items(
            local_fill.get("dest", {}),
            limit=limit,
        ),
        "local_fill_depart_selectors": _top_items(
            local_fill.get("depart", {}),
            limit=limit,
        ),
        "local_fill_return_selectors": _top_items(
            local_fill.get("return", {}),
            limit=limit,
        ),
        "global_search_click_selectors": _top_items(
            global_payload.get("search_click_selector_counts", {}),
            limit=limit,
        ),
        "local_search_click_selectors": _top_items(
            site_payload.get("search_click_selector_counts", {}),
            limit=limit,
        ),
        "local_modal_selectors": _top_items(
            site_payload.get("modal_selector_counts", {}),
            limit=limit,
        ),
        "local_failed_selectors": _top_items_filtered(
            site_payload.get("failed_selector_counts", {}),
            limit=limit,
            min_count=1,
        ),
        "global_failed_selectors": _top_items_filtered(
            global_payload.get("failed_selector_counts", {}),
            limit=limit,
            min_count=2,
        ),
        "failure_reason_top": _top_items(
            site_payload.get("failure_reason_counts", {}),
            limit=6,
        ),
        "failed_action_top": _top_items(
            site_payload.get("failed_action_counts", {}),
            limit=4,
        ),
        "local_domestic_toggles": _top_items(
            site_payload.get("domestic_toggle_selector_counts", {}),
            limit=4,
        ),
        "local_international_toggles": _top_items(
            site_payload.get("international_toggle_selector_counts", {}),
            limit=4,
        ),
        "local_url_hints": _top_items(url_hints.get("generic", {}), limit=10),
        "local_domestic_url_hints": _top_items(url_hints.get("domestic", {}), limit=10),
        "local_international_url_hints": _top_items(url_hints.get("international", {}), limit=10),
        "local_package_url_hints": _top_items(url_hints.get("package", {}), limit=10),
        "domain_mode_success_counts": dict(
            site_payload.get("domain_mode_success_counts", {"domestic": 0, "international": 0})
        ),
        "turn_histogram": dict(site_payload.get("turn_histogram", {})),
        "suggested_turns": suggested_turns_value,
        "multi_turn_scores": dict(site_payload.get("multi_turn_scores", {})),
        "last_success_plan": site_payload.get("last_success_plan"),
        "site_success_count": _safe_int(site_payload.get("success_count", 0)),
    }


def record_success(
    site_key: str,
    plan: List[Dict[str, Any]],
    *,
    is_domestic: Optional[bool] = None,
    source_url: Optional[str] = None,
    turns_used: Optional[int] = None,
    user_id: Optional[str] = None,
) -> None:
    """Update global and local selector/url knowledge from a successful scenario plan."""
    if not isinstance(plan, list) or not site_key or not is_supported_service(site_key):
        return

    scoped_user = _normalize_user_id(user_id)
    store = load_store()
    user_payload = _ensure_user_payload(store, scoped_user)
    global_payload = user_payload["global"]
    site_payload = _ensure_site_payload(user_payload, site_key)

    found_domestic_toggle = False
    found_international_toggle = False

    for step in plan:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        selectors = _selector_list(step.get("selector"))
        for selector in selectors:
            _increment(global_payload["selector_counts"], selector)
            _increment(site_payload["selector_counts"], selector)
            if action == "wait":
                _increment(global_payload["wait_selector_counts"], selector)
                _increment(site_payload["wait_selector_counts"], selector)
            if action == "click":
                if selector_looks_search_submit(selector):
                    _increment(global_payload["search_click_selector_counts"], selector)
                    _increment(site_payload["search_click_selector_counts"], selector)
                if selector_looks_modal_control(selector):
                    _increment(site_payload["modal_selector_counts"], selector)
                if contains_domestic_token(selector):
                    _increment(site_payload["domestic_toggle_selector_counts"], selector)
                    found_domestic_toggle = True
                if contains_international_token(selector):
                    _increment(site_payload["international_toggle_selector_counts"], selector)
                    found_international_toggle = True
            if action == "fill":
                role = infer_fill_role_from_selector(selector)
                if role:
                    _increment(global_payload["fill_role_selector_counts"][role], selector)
                    _increment(site_payload["fill_role_selector_counts"][role], selector)

    # URL-hint evidence.
    if isinstance(source_url, str) and source_url.strip():
        url = source_url.strip()
        if not url_matches_service_domain(site_key, url):
            # Ignore foreign-domain success URLs to prevent cross-service hint pollution.
            url = ""
    else:
        url = ""

    # Track domain mode before incrementing
    domain_counts_before = site_payload.get("domain_mode_success_counts", {}).copy()
    domestic_count_before = domain_counts_before.get("domestic", 0)
    international_count_before = domain_counts_before.get("international", 0)

    # Increment domain mode counts
    if is_domestic is True:
        _increment(site_payload["domain_mode_success_counts"], "domestic")
    elif is_domestic is False:
        _increment(site_payload["domain_mode_success_counts"], "international")

    if url:
        url_domestic = url_looks_domestic(url)
        url_international = url_looks_international(url)
        url_package = url_looks_package_bundle(url)
        known_split = site_payload.get("site_type") == "domestic_international_split"

        if url_package:
            # Track package/bundle flow separately so candidate ranking can de-prioritize it.
            _increment(site_payload["url_hints"]["package"], url)
        else:
            _increment(site_payload["url_hints"]["generic"], url)
            _increment(global_payload["url_hint_counts"], url)

            if url_domestic:
                _increment(site_payload["url_hints"]["domestic"], url)
            elif url_international:
                _increment(site_payload["url_hints"]["international"], url)
            elif is_domestic is True:
                # Check if we have mixed domain evidence (both domestic and international attempts recorded)
                if domestic_count_before > 0 and international_count_before > 0:
                    # We have split-flow evidence, add to both hints
                    _increment(site_payload["url_hints"]["domestic"], url)
                    _increment(site_payload["url_hints"]["international"], url)
                elif international_count_before > 0:
                    # We've seen international before, now seeing domestic - evidence of split-flow
                    # Add to BOTH hints to signal split-flow
                    _increment(site_payload["url_hints"]["domestic"], url)
                    _increment(site_payload["url_hints"]["international"], url)
                else:
                    # First domestic-mode call, add to domestic hint tentatively
                    _increment(site_payload["url_hints"]["domestic"], url)
            elif is_domestic is False:
                # Check if we have mixed domain evidence (both domestic and international attempts recorded)
                if domestic_count_before > 0 and international_count_before > 0:
                    # We have split-flow evidence, add to both hints
                    _increment(site_payload["url_hints"]["domestic"], url)
                    _increment(site_payload["url_hints"]["international"], url)
                elif domestic_count_before > 0:
                    # We've seen domestic before, now seeing international - evidence of split-flow
                    # Add to BOTH hints to signal split-flow
                    _increment(site_payload["url_hints"]["domestic"], url)
                    _increment(site_payload["url_hints"]["international"], url)
                else:
                    # First international-mode call, add to international hint tentatively
                    _increment(site_payload["url_hints"]["international"], url)

        if (
            found_domestic_toggle
            or found_international_toggle
            or url_domestic
            or url_international
        ):
            _increment(site_payload["site_type_scores"], "split")
        else:
            _increment(site_payload["site_type_scores"], "single")
    else:
        if found_domestic_toggle or found_international_toggle:
            _increment(site_payload["site_type_scores"], "split")
        else:
            _increment(site_payload["site_type_scores"], "single")

    site_payload["site_type"] = _classify_site_type(site_payload)

    turns = None
    if isinstance(turns_used, int):
        turns = max(1, turns_used)
    if turns is not None:
        _increment(site_payload["turn_histogram"], str(turns))
        if turns > 1:
            _increment(site_payload["multi_turn_scores"], "multi_turn")
        else:
            _increment(site_payload["multi_turn_scores"], "single_turn")

    _increment(global_payload["site_success_counts"], site_key)
    site_payload["success_count"] = _safe_int(site_payload.get("success_count", 0)) + 1
    site_payload["last_success_plan"] = plan

    save_store(store)


def record_failure(
    site_key: str,
    *,
    error_message: str,
    plan: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
) -> None:
    """Persist failed-step evidence so future plans can avoid repeated bad selectors."""
    if not site_key:
        return
    scoped_user = _normalize_user_id(user_id)
    store = load_store()
    user_payload = _ensure_user_payload(store, scoped_user)
    global_payload = user_payload["global"]
    site_payload = _ensure_site_payload(user_payload, site_key)

    reason = failure_reason(error_message)
    _increment(site_payload["failure_reason_counts"], reason)

    action = failure_action(error_message)
    if action:
        _increment(site_payload["failed_action_counts"], action)

    selectors = extract_failed_selectors(error_message)
    if not selectors and isinstance(plan, list):
        for step in plan:
            if not isinstance(step, dict):
                continue
            for selector in _selector_list(step.get("selector")):
                if selector_looks_modal_control(selector):
                    continue
                if selector_looks_search_submit(selector):
                    continue
                selectors.append(selector)
                if len(selectors) >= 8:
                    break
            if len(selectors) >= 8:
                break
    for selector in selectors:
        _increment(site_payload["failed_selector_counts"], selector)
        _increment(global_payload["failed_selector_counts"], selector)

    save_store(store)


def _match_any_pattern(text: str, patterns: List[str]) -> bool:
    """Return True when text contains any provided case-insensitive pattern."""
    lowered = (text or "").lower()
    for pattern in patterns:
        token = (pattern or "").strip().lower()
        if not token:
            continue
        if token in lowered:
            return True
    return False


def purge_url_hints(
    *,
    site_key: Optional[str] = None,
    user_id: Optional[str] = None,
    patterns: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Purge URL hints matching patterns from knowledge store and return stats."""
    target_patterns = [p for p in (patterns or []) if isinstance(p, str) and p.strip()]
    if not target_patterns:
        return {"users_scanned": 0, "sites_scanned": 0, "url_entries_removed": 0}

    scoped_user = _normalize_user_id(user_id) if user_id is not None else None
    store = load_store()
    users = store.get("users", {})
    removed_total = 0
    users_scanned = 0
    sites_scanned = 0

    for user_key, payload in list(users.items()):
        if scoped_user is not None and user_key != scoped_user:
            continue
        users_scanned += 1
        user_payload = _normalize_user_payload(payload)

        # Global URL hints.
        global_urls = user_payload["global"].get("url_hint_counts", {})
        for url in list(global_urls.keys()):
            if _match_any_pattern(url, target_patterns):
                removed_total += _safe_int(global_urls.get(url, 0))
                global_urls.pop(url, None)

        local_sites = user_payload.get("local", {})
        for local_site_key, local_site_payload in local_sites.items():
            if site_key and local_site_key != site_key:
                continue
            sites_scanned += 1
            hints = local_site_payload.get("url_hints", {})
            for group in ("generic", "domestic", "international", "package"):
                counter = hints.get(group, {})
                if not isinstance(counter, dict):
                    continue
                for url in list(counter.keys()):
                    if _match_any_pattern(url, target_patterns):
                        removed_total += _safe_int(counter.get(url, 0))
                        counter.pop(url, None)
            local_site_payload["site_type"] = _classify_site_type(local_site_payload)

        users[user_key] = user_payload

    save_store(store)
    return {
        "users_scanned": users_scanned,
        "sites_scanned": sites_scanned,
        "url_entries_removed": removed_total,
    }


def record_package_url_hint(
    site_key: str,
    *,
    source_url: str,
    user_id: Optional[str] = None,
) -> None:
    """Persist one URL as package/bundle flow hint for future de-prioritization."""
    if (
        not site_key
        or not is_supported_service(site_key)
        or not isinstance(source_url, str)
        or not source_url.strip()
    ):
        return
    url = source_url.strip()
    if not url_matches_service_domain(site_key, url):
        return
    scoped_user = _normalize_user_id(user_id)
    store = load_store()
    user_payload = _ensure_user_payload(store, scoped_user)
    site_payload = _ensure_site_payload(user_payload, site_key)
    _increment(site_payload["url_hints"]["package"], url)
    save_store(store)
