"""Rule-driven inference helpers for knowledge extraction from plans/errors."""

import ast
import re
from typing import Dict, List, Optional

from utils.knowledge_rules import get_failure_reason_rules, get_knowledge_rule_tokens


def contains_domestic_token(selector: str) -> bool:
    """Return True if selector text indicates domestic mode."""
    lowered = (selector or "").lower()
    for token in get_knowledge_rule_tokens("domestic_tokens"):
        if token.lower() in lowered:
            return True
    return False


def contains_international_token(selector: str) -> bool:
    """Return True if selector text indicates international mode."""
    lowered = (selector or "").lower()
    for token in get_knowledge_rule_tokens("international_tokens"):
        if token.lower() in lowered:
            return True
    return False


def selector_looks_search_submit(selector: str) -> bool:
    """Return True if selector likely targets search/submit action."""
    lowered = (selector or "").lower()
    for token in get_knowledge_rule_tokens("search_submit_tokens"):
        if token.lower() in lowered:
            return True
    return False


def selector_looks_modal_control(selector: str) -> bool:
    """Return True if selector likely dismisses consent/cookie/modal layers."""
    lowered = (selector or "").lower()
    for token in get_knowledge_rule_tokens("modal_control_tokens"):
        if token.lower() in lowered:
            return True
    return False


def infer_fill_role_from_selector(selector: str) -> Optional[str]:
    """Infer fill role from selector semantics."""
    lowered = (selector or "").lower()
    if any(
        token.lower() in lowered
        for token in get_knowledge_rule_tokens("fill_role_return_tokens")
    ):
        return "return"
    if any(
        token.lower() in lowered
        for token in get_knowledge_rule_tokens("fill_role_depart_tokens")
    ):
        return "depart"
    if any(
        token.lower() in lowered
        for token in get_knowledge_rule_tokens("fill_role_dest_tokens")
    ):
        return "dest"
    if any(
        token.lower() in lowered
        for token in get_knowledge_rule_tokens("fill_role_origin_tokens")
    ):
        return "origin"
    return None


def extract_failed_selectors(error_message: str) -> List[str]:
    """Best-effort parse of selectors list from runtime error string."""
    if not isinstance(error_message, str):
        return []
    marker = "selectors="
    start = error_message.find(marker)
    if start < 0:
        return []
    i = error_message.find("[", start + len(marker))
    if i < 0:
        return []

    # Parse bracketed list while respecting quoted strings that may contain ']'.
    depth = 0
    in_quote = ""
    escaped = False
    end = -1
    for j in range(i, len(error_message)):
        ch = error_message[j]
        if in_quote:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == in_quote:
                in_quote = ""
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == "[":
            depth += 1
            continue
        if ch == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end < 0:
        return []

    blob = error_message[i:end]
    try:
        parsed = ast.literal_eval(blob)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [value for value in parsed if isinstance(value, str) and value.strip()]


def failure_reason(error_message: str) -> str:
    """Map runtime error text into compact reason categories."""
    if not isinstance(error_message, str):
        return "unknown"
    lowered = error_message.lower()
    for reason, patterns in get_failure_reason_rules().items():
        for phrase in patterns:
            if phrase.lower() in lowered:
                return reason
    return "other"


def failure_action(error_message: str) -> Optional[str]:
    """Extract failed action name from runtime error text."""
    if not isinstance(error_message, str):
        return None
    m = re.search(r"action=([a-z_]+)", error_message, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()


def url_looks_domestic(url: str) -> bool:
    """Return True if URL path/text strongly suggests domestic flight flow."""
    lowered = (url or "").lower()
    for token in get_knowledge_rule_tokens("url_domestic_tokens"):
        if token.lower() in lowered:
            return True
    return False


def url_looks_international(url: str) -> bool:
    """Return True if URL path/text strongly suggests international flight flow."""
    lowered = (url or "").lower()
    for token in get_knowledge_rule_tokens("url_international_tokens"):
        if token.lower() in lowered:
            return True
    return False


def url_looks_package_bundle(url: str) -> bool:
    """Return True when URL likely points to bundled flight+hotel package flow."""
    lowered = (url or "").lower()
    for token in get_knowledge_rule_tokens("url_package_tokens"):
        if token.lower() in lowered:
            return True
    return False


def suggested_turns(turn_histogram: Dict[str, int]) -> Optional[int]:
    """Return most common successful turn count if enough evidence exists."""
    if not isinstance(turn_histogram, dict):
        return None
    best_turn = None
    best_score = 0
    for key, value in turn_histogram.items():
        try:
            turns = int(key)
        except Exception:
            continue
        score = max(0, int(value) if isinstance(value, int) else 0)
        if turns < 1 or score < 1:
            continue
        if score > best_score:
            best_turn = turns
            best_score = score
    return best_turn
