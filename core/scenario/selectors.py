"""Selector utilities for scenario flows."""

from typing import Iterable, List


def dedupe_selectors(selectors: Iterable[str]) -> List[str]:
    """Return selectors de-duplicated in first-seen order."""
    out: List[str] = []
    seen = set()
    for selector in selectors:
        text = str(selector or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def filter_broad_text_activation_selectors(selectors: Iterable[str]) -> List[str]:
    """Drop broad text-based selector forms from activation candidates."""
    out: List[str] = []
    for selector in selectors:
        text = str(selector or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if lowered.startswith("text="):
            continue
        if ":has-text(" in lowered:
            continue
        out.append(text)
    return out


def score_fill_selector(selector: str) -> tuple:
    """Score selectors for textbox fill preference."""
    text = str(selector or "").strip().lower()
    return (
        int(not text.startswith("text=")),
        int(":has-text(" not in text),
        int("input[" in text),
        int("[role='combobox'" in text or '[role="combobox"' in text),
        int("[aria-label" in text or "placeholder" in text),
        -len(text),
    )


def score_activation_selector(selector: str) -> tuple:
    """Score selectors for field activation preference."""
    text = str(selector or "").strip().lower()
    return (
        int(not text.startswith("text=")),
        int(":has-text(" not in text),
        int("input[" not in text),
        int("[role='combobox'" in text or '[role="combobox"' in text),
        int("[role='button'" in text or '[role="button"' in text),
        int("[aria-label" in text),
        -len(text),
    )
