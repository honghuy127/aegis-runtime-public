from __future__ import annotations

import re
from typing import List


def error_signature(error_text: str) -> str:
    """Normalize runtime error text into a repeat-detection signature."""
    if not isinstance(error_text, str):
        return ""
    head = error_text.split("Call log:", 1)[0].strip()
    first = head.splitlines()[0].strip() if head else ""
    if not first:
        return ""
    normalized = re.sub(r"selectors=\[[^\]]*\]", "selectors=[...]", first)
    normalized = re.sub(r"Timeout \d+ms exceeded", "Timeout <n>ms exceeded", normalized)
    return normalized


def local_programming_exception_reason(exc: Exception) -> str:
    """Classify deterministic local runtime exceptions for no-burn fail-fast handling."""
    if exc is None:
        return ""
    if isinstance(exc, UnboundLocalError):
        return "unbound_local_error"
    if isinstance(exc, NameError):
        return "name_error"
    return ""


def sanitize_runtime_note(note: str, max_chars: int = 180) -> str:
    """Normalize planner free-form note into one short line."""
    if not isinstance(note, str):
        return ""
    text = re.sub(r"[\r\n\t`]+", " ", note).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def merge_planner_notes(existing, incoming, *, max_notes: int = 8):
    """Merge/sanitize planner notes with stable ordering and dedupe."""
    merged: List[str] = []
    seen = set()
    for raw in list(existing or []) + list(incoming or []):
        note = sanitize_runtime_note(raw)
        if not note or note in seen:
            continue
        seen.add(note)
        merged.append(note)
    if len(merged) > max_notes:
        merged = merged[-max_notes:]
    return merged


def planner_notes_hint(notes) -> str:
    """Serialize short planner notes as compact context for follow-up turns."""
    cleaned = [sanitize_runtime_note(note) for note in list(notes or [])]
    cleaned = [note for note in cleaned if note]
    if not cleaned:
        return ""
    tail = cleaned[-3:]
    return "PlannerNotes: " + " | ".join(tail)


def step_trace_memory_hint(step_trace, *, max_items: int = 4) -> str:
    """Build compact per-run memory hint from recent failed/soft-failed steps."""
    if not isinstance(step_trace, list):
        step_trace = []
    lines: List[str] = []
    seen = set()
    for item in reversed(step_trace):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip().lower()
        if status not in {"soft_fail", "hard_fail"}:
            continue
        action = str(item.get("action", "")).strip().lower()
        role = str(item.get("role", "")).strip().lower()
        selector = str(item.get("used_selector", "") or "").strip()
        if not selector:
            selectors = item.get("selectors")
            if isinstance(selectors, list) and selectors:
                selector = str(selectors[0] or "").strip()
        note = sanitize_runtime_note(
            f"avoid_repeat action={action} role={role or 'na'} selector={selector or 'na'}",
            max_chars=160,
        )
        if not note or note in seen:
            continue
        seen.add(note)
        lines.append(note)
        if len(lines) >= max_items:
            break
    if not lines:
        return ""
    return "TraceMemory: " + " | ".join(lines)
