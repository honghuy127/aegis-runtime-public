"""Selector quality heuristics for extraction confidence handling."""

from __future__ import annotations

import re


_HASHY_CLASS_RE = re.compile(r"\.[a-z0-9]{5,}(?:[._-][a-z0-9]{2,})?", re.IGNORECASE)


def classify_selector_stability(css: str) -> str:
    """Classify selector stability as stable/semi_stable/brittle."""
    if not isinstance(css, str):
        return "brittle"
    selector = css.strip()
    if not selector:
        return "brittle"
    lowered = selector.lower()

    stable_tokens = (
        "[data-testid",
        "[data-test",
        "[aria-",
        "role=",
        "[role=",
    )
    if any(token in lowered for token in stable_tokens):
        return "stable"

    if "#" in selector:
        id_match = re.search(r"#([A-Za-z][\w:-]{2,})", selector)
        if id_match and not re.search(r"[0-9a-f]{6,}", id_match.group(1), re.IGNORECASE):
            return "stable"

    if re.search(r"\[name=['\"][^'\"]+['\"]\]", lowered):
        return "stable"

    if ":nth-child(" in lowered or ":nth-of-type(" in lowered:
        return "brittle"
    if lowered.count(">") >= 2 or lowered.count(" ") >= 4:
        return "brittle"

    class_chain_count = len(re.findall(r"\.[A-Za-z0-9_-]+", selector))
    if class_chain_count >= 2:
        return "brittle"
    if _HASHY_CLASS_RE.search(selector):
        return "brittle"

    if class_chain_count == 1:
        class_name = re.findall(r"\.([A-Za-z0-9_-]+)", selector)[0]
        if len(class_name) >= 4 and re.search(r"[a-z]", class_name):
            return "semi_stable"
    return "brittle"
