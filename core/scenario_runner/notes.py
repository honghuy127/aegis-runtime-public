"""Runtime notes and error handling helpers."""

import os
import re
from typing import List


def _error_signature(error_text: str) -> str:
    """Normalize runtime error text into a repeat-detection signature."""
    if not isinstance(error_text, str):
        return ""
    head = error_text.split("Call log:", 1)[0].strip()
    # Keep first line only to avoid noisy stack/context differences.
    first = head.splitlines()[0].strip() if head else ""
    if not first:
        return ""
    normalized = re.sub(r"selectors=\[[^\]]*\]", "selectors=[...]", first)
    normalized = re.sub(r"Timeout \d+ms exceeded", "Timeout <n>ms exceeded", normalized)
    return normalized


def _local_programming_exception_reason(exc: Exception) -> str:
    """Classify deterministic local runtime exceptions for no-burn fail-fast handling."""
    if exc is None:
        return ""
    if isinstance(exc, UnboundLocalError):
        return "unbound_local_error"
    if isinstance(exc, NameError):
        return "name_error"
    return ""


def _should_return_latest_html_on_followup_failure() -> bool:
    """Whether to return latest HTML when follow-up plan generation is unavailable."""
    raw = os.getenv("SCENARIO_RETURN_LATEST_ON_FOLLOWUP_FAILURE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _sanitize_runtime_note(note: str, max_chars: int = 180) -> str:
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


def _merge_planner_notes(existing, incoming, *, max_notes: int = 8):
    """Merge/sanitize planner notes with stable ordering and dedupe."""
    merged = []
    seen = set()
    for raw in list(existing or []) + list(incoming or []):
        note = _sanitize_runtime_note(raw)
        if not note or note in seen:
            continue
        seen.add(note)
        merged.append(note)
    if len(merged) > max_notes:
        merged = merged[-max_notes:]
    return merged


def _planner_notes_hint(notes) -> str:
    """Serialize short planner notes as compact context for follow-up turns."""
    cleaned = [_sanitize_runtime_note(note) for note in list(notes or [])]
    cleaned = [note for note in cleaned if note]
    if not cleaned:
        return ""
    tail = cleaned[-3:]
    return "PlannerNotes: " + " | ".join(tail)


def _step_trace_memory_hint(step_trace, *, max_items: int = 4) -> str:
    """Build compact per-run memory hint from recent failed/soft-failed steps."""
    if not isinstance(step_trace, list):
        step_trace = []
    lines = []
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
        note = _sanitize_runtime_note(
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


def _compose_local_hint_with_notes(
    local_knowledge_hint: str,
    planner_notes: List[str],
    trace_memory_hint: str,
) -> str:
    """
    Compose local knowledge hint combining knowledge, planner notes, and memory.

    Args:
        local_knowledge_hint: Base local knowledge text
        planner_notes: List of planner note strings
        trace_memory_hint: Memory hint from step trace

    Returns:
        Combined knowledge hint string
    """
    notes_hint = _planner_notes_hint(planner_notes)
    parts = []
    if local_knowledge_hint:
        parts.append(local_knowledge_hint)
    if notes_hint:
        parts.append(notes_hint)
    memory_hint = _sanitize_runtime_note(trace_memory_hint, max_chars=240)
    if memory_hint:
        parts.append(memory_hint)
    return "\n".join(parts)
