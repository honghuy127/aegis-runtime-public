from __future__ import annotations

import re
from typing import List

from utils.knowledge_rules import get_tokens, get_knowledge_rule_tokens
from core.ui_tokens import normalize_visible_text


def load_rule_tokens(
    *,
    group: str = "",
    key: str = "",
    legacy_key: str = "",
    fallback: tuple[str, ...] = (),
) -> List[str]:
    """Load tokens from knowledge rules while preserving deterministic fallbacks."""
    merged: List[str] = []
    if group and key:
        merged.extend(get_tokens(group, key))
    if legacy_key:
        merged.extend(get_knowledge_rule_tokens(legacy_key))
    merged.extend(fallback)

    out: List[str] = []
    seen = set()
    for token in merged:
        value = str(token or "").strip()
        if not value:
            continue
        marker = normalize_visible_text(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def compile_token_regex(
    tokens: list[str], *, escape_literals: bool = True
) -> re.Pattern:
    """Compile one broad case-insensitive token regex from token list."""
    patterns: list[str] = []
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        patterns.append(re.escape(value) if escape_literals else value)
    if not patterns:
        return re.compile(r"$^")
    return re.compile("(?:%s)" % "|".join(patterns), re.IGNORECASE)
