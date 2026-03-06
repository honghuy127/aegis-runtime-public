"""Shared extraction helpers for token patterns and price parsing.

Extracted from core.extractor to decouple Google-specific helpers without
introducing circular imports.
"""

from typing import Any, Dict, List, Optional
import re

from core.ui_tokens import normalize_visible_text
from utils.knowledge_rules import get_knowledge_rule_tokens, get_tokens
from utils.thresholds import get_threshold


# Conservative currency parsing for deterministic fallback when LLM misses.
_PRICE_PATTERN = re.compile(
    r"(?:(?P<symbol>¥|￥|\$|€|£)\s*(?P<v1>\d[\d,]*(?:\.\d{1,2})?)|"
    r"(?P<code>JPY|USD|EUR|GBP)\s*(?P<v2>\d[\d,]*(?:\.\d{1,2})?)|"
    r"(?P<v3>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<code2>JPY|USD|EUR|GBP)|"
    r"(?P<v4>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<yen_suffix>円))",
    re.IGNORECASE,
)
_SYMBOL_TO_CURRENCY = {
    "¥": "JPY",
    "￥": "JPY",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}
HEURISTIC_MIN_PRICE = float(get_threshold("heuristic_min_price", 500))
HEURISTIC_MAX_PRICE = float(get_threshold("heuristic_max_price", 5_000_000))


def _load_token_group(
    *,
    group: str,
    key: str,
    legacy_key: Optional[str] = None,
    fallback: Optional[List[str]] = None,
) -> List[str]:
    """Load one token list from grouped rules with legacy+default fallback."""
    tokens = get_tokens(group, key)
    if not tokens and legacy_key:
        tokens = get_knowledge_rule_tokens(legacy_key)
    if not tokens:
        tokens = list(fallback or [])
    out: List[str] = []
    seen = set()
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        marker = normalize_visible_text(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def _compile_hint_token_patterns(tokens: List[str]) -> List[Dict[str, Any]]:
    """Compile normalized token matchers for lightweight hint counting."""
    out: List[Dict[str, Any]] = []
    for token in tokens:
        normalized = normalize_visible_text(token)
        if not normalized:
            continue
        if normalized.isascii():
            out.append(
                {
                    "kind": "ascii",
                    "pattern": re.compile(
                        rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                        re.IGNORECASE,
                    ),
                }
            )
        else:
            out.append({"kind": "text", "value": normalized})
    return out


def _hint_token_count(text: str, matchers: List[Dict[str, Any]]) -> int:
    """Count token hits with conservative ASCII boundary matching."""
    normalized = normalize_visible_text(text)
    if not normalized:
        return 0
    count = 0
    for matcher in matchers:
        if matcher.get("kind") == "ascii":
            pattern = matcher.get("pattern")
            if hasattr(pattern, "findall"):
                count += len(pattern.findall(normalized))
            continue
        token = str(matcher.get("value", "") or "")
        if token:
            count += normalized.count(token)
    return count


def _hint_token_any(text: str, matchers: List[Dict[str, Any]]) -> bool:
    """Return True when one hint token matches."""
    return _hint_token_count(text, matchers) > 0


_RESULT_HINT_TOKENS = _load_token_group(
    group="hints",
    key="results",
    fallback=[
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
)
_AUTH_HINT_TOKENS = _load_token_group(
    group="hints",
    key="auth",
    fallback=[
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
)
_ROUTE_HINT_TOKENS = _load_token_group(
    group="hints",
    key="route_fields",
    fallback=[
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
)
_RESULT_HINT_MATCHERS = _compile_hint_token_patterns(_RESULT_HINT_TOKENS)
_AUTH_HINT_MATCHERS = _compile_hint_token_patterns(_AUTH_HINT_TOKENS)
_ROUTE_HINT_MATCHERS = _compile_hint_token_patterns(_ROUTE_HINT_TOKENS)


def _extract_price_candidates(text: str):
    """Yield `(price, currency)` tuples parsed from one text blob."""
    if not text:
        return
    for match in _PRICE_PATTERN.finditer(text):
        raw_value = match.group("v1") or match.group("v2") or match.group("v3") or match.group("v4")
        if not raw_value:
            continue
        try:
            value = float(raw_value.replace(",", ""))
        except Exception:
            continue

        currency = None
        symbol = match.group("symbol")
        if symbol:
            currency = _SYMBOL_TO_CURRENCY.get(symbol)
        elif match.group("code"):
            currency = match.group("code").upper()
        elif match.group("code2"):
            currency = match.group("code2").upper()
        elif match.group("yen_suffix"):
            currency = "JPY"

        yield value, currency


def _price_grounding_tolerance(target: float) -> float:
    """Compute absolute tolerance used to ground VLM price in HTML text."""
    tolerance_ratio = float(get_threshold("extract_vlm_price_grounding_tolerance_ratio", 0.03))
    tolerance_abs = float(get_threshold("extract_vlm_price_grounding_tolerance_abs", 2500))
    return max(tolerance_abs, abs(float(target)) * max(0.0, tolerance_ratio))


def _contains_route_token(raw_blob: str, upper_blob: str, token: str) -> bool:
    """Token match helper to avoid false positives like TYO inside TOKYO."""
    if not token:
        return False
    if token.isascii():
        needle = token.upper()
        if re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", upper_blob):
            return True
        # Fallback: keep broad substring behavior for longer natural-language tokens.
        if len(needle) >= 5 and needle in upper_blob:
            return True
        return False
    return token in raw_blob