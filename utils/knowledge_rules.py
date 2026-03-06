"""Config loader for knowledge-store heuristic rules."""

import ast
from pathlib import Path
from typing import Dict, List, Any


_RULES_PATH = Path(__file__).resolve().parent.parent / "configs" / "knowledge_rules.yaml"
_DEFAULT_LIST_RULES: Dict[str, List[str]] = {
    "domestic_tokens": ["domestic", "国内"],
    "international_tokens": ["international", "intl", "海外", "国際"],
    "search_submit_tokens": ["search", "submit", "検索", "空席照会"],
    "modal_control_tokens": [
        "cookie",
        "consent",
        "close",
        "dismiss",
        "accept",
        "同意",
        "閉じる",
        "許可",
    ],
    "fill_role_origin_tokens": [
        "origin",
        "from",
        "where from",
        "departureairport",
        "outwarddeparture",
        "出発地",
        "出発空港",
    ],
    "fill_role_dest_tokens": [
        "destination",
        "arrival",
        "where to",
        "to",
        "outwardarrival",
        "目的地",
        "到着地",
        "到着空港",
    ],
    "fill_role_depart_tokens": [
        "depart",
        "departure",
        "departdate",
        "出発日",
        "往路",
    ],
    "fill_role_return_tokens": [
        "return",
        "returndate",
        "復路",
        "帰り",
        "帰路",
    ],
    "url_domestic_tokens": [
        "domestic",
        "kokunai",
        "japan.html",
        "air/domestic",
        "国内",
    ],
    "url_international_tokens": [
        "international",
        "intl",
        "kaigai",
        "overseas",
        "air/_.html",
        "kaigai_package",
        "海外",
        "国際",
    ],
    "url_package_tokens": [
        "kokunai-trip",
        "dynamicpackage",
        "airhotel",
        "flight-hotel",
        "/package/",
        "/kaigai_package/",
        "航空券+ホテル",
        "航空券＋ホテル",
        "ダイナミックパッケージ",
    ],
    "page_package_tokens": [
        "ダイナミックパッケージ",
        "航空券+ホテル",
        "航空券＋ホテル",
        "flight + hotel",
        "flight+hotel",
        "air + hotel",
        "air+hotel",
    ],
    "placeholder_dest_tokens": [
        "目的地を探索",
        "explore destinations",
        "where to",
        "to",
        "destination",
        "目的地",
    ],
    "placeholder_origin_tokens": [
        "where from",
        "from",
        "origin",
        "出発地",
        "出発",
    ],
    "region_like_origin_tokens": [
        "東京都",
        "東京",
        "tokyo",
        "japan",
        "日本",
    ],
    "action_search_tokens": [
        "検索",
        "search",
        "search flights",
    ],
    "action_done_tokens": [
        "完了",
        "done",
        "ok",
    ],
    "action_reset_tokens": [
        "リセット",
        "消去",
        "reset",
        "clear",
    ],
    "tab_flights_tokens": [
        "フライト",
        "航空券",
        "flights",
        "flight",
        "air",
    ],
    "tab_hotels_tokens": [
        "ホテル",
        "hotels",
        "hotel",
    ],
}
_DEFAULT_TOKEN_GROUP_RULES: Dict[str, Dict[str, List[str]]] = {
    "page": {
        "hotel": ["hotel", "hotels", "ホテル", "宿"],
        "flight": ["flight", "flights", "air", "航空券", "飛行機"],
        "package": [
            "ダイナミックパッケージ",
            "航空券+ホテル",
            "航空券＋ホテル",
            "flight + hotel",
            "flight+hotel",
            "air + hotel",
            "air+hotel",
        ],
    },
    "hints": {
        "auth": [
            "email",
            "password",
            "login",
            "register",
            "account",
            "member",
            "メール",
            "会員",
            "ログイン",
            "パスワード",
            "氏名",
            "お名前",
            "電話",
        ],
        "results": [
            "flight",
            "flights",
            "itinerary",
            "result",
            "results",
            "search result",
            "運賃",
            "料金",
            "便",
            "検索結果",
            "最安",
            "往路",
            "復路",
        ],
        "route_fields": [
            "from",
            "to",
            "where from",
            "where to",
            "origin",
            "destination",
            "depart",
            "departure",
            "return",
            "出発地",
            "目的地",
            "到着地",
            "出発",
            "復路",
            "帰り",
        ],
    },
    "google": {
        "non_flight_map": [
            "地図を表示",
            "リストを表示",
            "地図データ",
            "gmp-internal-camera-control",
            "map data",
        ],
        "non_flight_hotel": [
            "hotel",
            "hotels",
            "ホテル",
            "宿泊",
            "check-in",
            "check out",
            "チェックイン",
        ],
        "bundle_word": ["package", "パッケージ"],
    },
}
_DEFAULT_FAILURE_REASON_RULES: Dict[str, List[str]] = {
    "hidden_input": ['input of type "hidden" cannot be filled', 'type "hidden"'],
    "auth_field": ["auth/profile fill selectors"],
    "semantic_fill_mismatch": ["semantic fill mismatches"],
    "timeout": ["timeout", "readtimeout"],
    "scope_guard_non_flight": [
        "scope_guard_non_flight",
        "scope_non_flight",
        "html_non_flight_scope",
        "vlm_non_flight_scope",
    ],
    "repair_failed_no_dom_change": ["no dom change and plan repair failed"],
    "followup_plan_failed": ["unable to produce follow-up plan"],
}
_CACHE: Dict[str, Any] = {}
_CACHE_PATH: Path = Path()
_TOKEN_GROUP_ALIASES: Dict[str, str] = {
    "action": "actions",
    "actions": "actions",
    "tab": "tabs",
    "tabs": "tabs",
    "placeholder": "placeholders",
    "placeholders": "placeholders",
}
_TOKEN_GROUP_LEGACY_PREFIX: Dict[str, str] = {
    "actions": "action",
    "tabs": "tab",
    "placeholders": "placeholder",
}


def _split_csv(raw: str) -> List[str]:
    """Split one comma-separated scalar config value into tokens."""
    if not isinstance(raw, str):
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _normalize_token_key(value: str) -> str:
    """Normalize token group/item keys into snake_case identifiers."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _strip_inline_comment(line: str) -> str:
    """Strip trailing # comments while preserving quoted values."""
    out: List[str] = []
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
            out.append(char)
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            out.append(char)
            continue
        if char == "#" and not in_single and not in_double:
            break
        out.append(char)
    return "".join(out).rstrip()


def _parse_inline_list(raw: str) -> List[str]:
    """Parse one inline list value like ['a', 'b'] with safe fallback."""
    text = str(raw or "").strip()
    if not text:
        return []
    if not (text.startswith("[") and text.endswith("]")):
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out: List[str] = []
    for item in parsed:
        value = str(item or "").strip()
        if value:
            out.append(value)
    return out


def _flatten_token_groups(groups: Dict[str, Dict[str, List[str]]]) -> Dict[str, List[str]]:
    """Flatten grouped token rules into list_rules key shape."""
    out: Dict[str, List[str]] = {}
    for group, items in (groups or {}).items():
        normalized_group = _normalize_token_key(group)
        if not normalized_group or not isinstance(items, dict):
            continue
        for item_key, tokens in items.items():
            normalized_item = _normalize_token_key(item_key)
            if not normalized_item or not isinstance(tokens, list):
                continue
            values = [str(token or "").strip() for token in tokens if str(token or "").strip()]
            out[f"{normalized_group}_{normalized_item}_tokens"] = values
    return out


def _parse_grouped_tokens(lines: List[str]) -> Dict[str, Dict[str, List[str]]]:
    """Parse optional nested YAML-ish `tokens:` groups without PyYAML dependency."""
    groups: Dict[str, Dict[str, List[str]]] = {}
    in_tokens = False
    current_group = ""
    pending_item = ""
    for raw_line in lines:
        line = _strip_inline_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            in_tokens = stripped == "tokens:"
            current_group = ""
            pending_item = ""
            continue
        if not in_tokens:
            continue

        if indent == 2 and stripped.endswith(":"):
            current_group = _normalize_token_key(stripped[:-1])
            pending_item = ""
            if current_group and current_group not in groups:
                groups[current_group] = {}
            continue

        if not current_group:
            continue

        if indent == 4 and ":" in stripped:
            item_key, value = stripped.split(":", 1)
            normalized_item = _normalize_token_key(item_key)
            if not normalized_item:
                pending_item = ""
                continue
            parsed_inline = _parse_inline_list(value.strip())
            if parsed_inline:
                groups[current_group][normalized_item] = parsed_inline
                pending_item = ""
            elif value.strip():
                groups[current_group][normalized_item] = _split_csv(value.strip())
                pending_item = ""
            else:
                groups[current_group].setdefault(normalized_item, [])
                pending_item = normalized_item
            continue

        if indent >= 6 and pending_item and stripped.startswith("- "):
            token = stripped[2:].strip().strip("'").strip('"')
            if token:
                groups[current_group].setdefault(pending_item, []).append(token)
            continue

        if indent <= 4:
            pending_item = ""
    return groups


def _clone(rules: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-ish clone of small rules dictionaries."""
    return {
        "list_rules": {k: list(v) for k, v in rules["list_rules"].items()},
        "failure_reason_rules": {
            k: list(v) for k, v in rules["failure_reason_rules"].items()
        },
    }


def load_knowledge_rules(force_reload: bool = False) -> Dict[str, Any]:
    """Load configurable knowledge-rule tokens from configs/knowledge_rules.yaml."""
    global _CACHE
    global _CACHE_PATH
    current_path = Path(_RULES_PATH)
    if _CACHE and not force_reload and _CACHE_PATH == current_path:
        return _clone(_CACHE)

    rules = {
        "list_rules": {k: list(v) for k, v in _DEFAULT_LIST_RULES.items()},
        "failure_reason_rules": {
            k: list(v) for k, v in _DEFAULT_FAILURE_REASON_RULES.items()
        },
    }
    rules["list_rules"].update(_flatten_token_groups(_DEFAULT_TOKEN_GROUP_RULES))

    if _RULES_PATH.exists():
        lines = _RULES_PATH.read_text(encoding="utf-8").splitlines()
        grouped_tokens = _parse_grouped_tokens(lines)
        if grouped_tokens:
            rules["list_rules"].update(_flatten_token_groups(grouped_tokens))

        for raw_line in lines:
            raw_line = _strip_inline_comment(raw_line)
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if key.startswith("failure_reason_"):
                reason_key = key[len("failure_reason_") :].strip()
                if not reason_key:
                    continue
                rules["failure_reason_rules"][reason_key] = _split_csv(value)
                continue

            if key in rules["list_rules"]:
                rules["list_rules"][key] = _split_csv(value)

    _CACHE = rules
    _CACHE_PATH = current_path
    return _clone(_CACHE)


def get_knowledge_rule_tokens(key: str) -> List[str]:
    """Return one token-list rule by key."""
    rules = load_knowledge_rules()
    out = rules["list_rules"].get(key, [])
    return [item for item in out if isinstance(item, str) and item.strip()]


def get_failure_reason_rules() -> Dict[str, List[str]]:
    """Return ordered mapping of failure reason category -> matching phrases."""
    rules = load_knowledge_rules()
    return {
        key: [item for item in values if isinstance(item, str) and item.strip()]
        for key, values in rules["failure_reason_rules"].items()
    }


def get_tokens(group: str, key: str) -> List[str]:
    """Return tokens by semantic group/key mapping (e.g. actions/search)."""
    group_key = _normalize_token_key(group)
    item_key = _normalize_token_key(key)
    if not group_key or not item_key:
        return []
    group_key = _TOKEN_GROUP_ALIASES.get(group_key, group_key)
    primary_key = f"{group_key}_{item_key}_tokens"
    tokens = get_knowledge_rule_tokens(primary_key)
    if tokens:
        return tokens
    legacy_prefix = _TOKEN_GROUP_LEGACY_PREFIX.get(group_key)
    if legacy_prefix:
        return get_knowledge_rule_tokens(f"{legacy_prefix}_{item_key}_tokens")
    return []


def get_placeholder_tokens(role: str) -> List[str]:
    """Return placeholder label tokens for one form role (compat wrapper)."""
    return get_tokens("placeholders", role)


def get_action_tokens(action: str) -> List[str]:
    """Return action-label tokens used for UI fallback selectors (compat wrapper)."""
    return get_tokens("actions", action)


def get_tab_tokens(tab: str) -> List[str]:
    """Return product-tab label tokens for contextual UI toggles (compat wrapper)."""
    return get_tokens("tabs", tab)
