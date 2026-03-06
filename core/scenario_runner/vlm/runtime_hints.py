"""VLM runtime hints management."""

import os
import re
from typing import Any, Dict

from core.scenario_runner.notes import _sanitize_runtime_note


def _sanitize_vlm_label(text: str, max_chars: int = 32) -> str:
    """Normalize one VLM label hint into compact safe text."""
    if not isinstance(text, str):
        return ""
    cleaned = re.sub(r"[\r\n\t`]+", " ", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip() + "..."
    return cleaned


def _sanitize_vlm_labels(values, max_items: int = 8) -> list:
    """Normalize VLM label list with dedupe and bounded size."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for item in values:
        label = _sanitize_vlm_label(item)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= max_items:
            break
    return out


def _clear_vlm_runtime_hints() -> None:
    """Clear per-run VLM UI hint env vars to avoid cross-run contamination."""
    keys = [
        "FLIGHT_WATCHER_VLM_PAGE_SCOPE",
        "FLIGHT_WATCHER_VLM_PAGE_CLASS",
        "FLIGHT_WATCHER_VLM_TRIP_PRODUCT",
        "FLIGHT_WATCHER_VLM_DOMESTIC_LABELS",
        "FLIGHT_WATCHER_VLM_INTERNATIONAL_LABELS",
        "FLIGHT_WATCHER_VLM_PRODUCT_LABELS",
        "FLIGHT_WATCHER_VLM_SEARCH_KEYWORDS",
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_ORIGIN",
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEST",
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEPART",
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_RETURN",
    ]
    for key in keys:
        os.environ.pop(key, None)


def _apply_vlm_runtime_hints(hint: dict) -> None:
    """Publish VLM UI hint labels as env vars consumed by selector/keyword fallbacks."""
    if not isinstance(hint, dict):
        return
    mode = hint.get("mode_labels") if isinstance(hint.get("mode_labels"), dict) else {}
    fill = hint.get("fill_labels") if isinstance(hint.get("fill_labels"), dict) else {}

    mapping = {
        "FLIGHT_WATCHER_VLM_DOMESTIC_LABELS": _sanitize_vlm_labels(mode.get("domestic")),
        "FLIGHT_WATCHER_VLM_INTERNATIONAL_LABELS": _sanitize_vlm_labels(mode.get("international")),
        "FLIGHT_WATCHER_VLM_PRODUCT_LABELS": _sanitize_vlm_labels(hint.get("product_labels")),
        "FLIGHT_WATCHER_VLM_SEARCH_KEYWORDS": _sanitize_vlm_labels(fill.get("search")),
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_ORIGIN": _sanitize_vlm_labels(fill.get("origin")),
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEST": _sanitize_vlm_labels(fill.get("dest")),
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEPART": _sanitize_vlm_labels(fill.get("depart")),
        "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_RETURN": _sanitize_vlm_labels(fill.get("return")),
    }
    for key, values in mapping.items():
        if values:
            os.environ[key] = "|".join(values)
    page_scope = str(hint.get("page_scope", "")).strip().lower()
    if page_scope in {"domestic", "international", "mixed", "unknown"}:
        os.environ["FLIGHT_WATCHER_VLM_PAGE_SCOPE"] = page_scope
    page_class = str(hint.get("page_class", "")).strip().lower()
    if page_class in {
        "flight_only",
        "flight_hotel_package",
        "garbage_page",
        "irrelevant_page",
        "unknown",
    }:
        os.environ["FLIGHT_WATCHER_VLM_PAGE_CLASS"] = page_class
    trip_product = str(hint.get("trip_product", "")).strip().lower()
    if trip_product in {"flight_only", "flight_hotel_package", "unknown"}:
        os.environ["FLIGHT_WATCHER_VLM_TRIP_PRODUCT"] = trip_product


def _compose_vlm_knowledge_hint(hint: dict, *, is_domestic: bool) -> str:
    """Serialize VLM UI analysis into compact planner-consumable guidance."""
    if not isinstance(hint, dict):
        return ""
    lines = []
    scope = str(hint.get("page_scope", "")).strip().lower()
    page_class = str(hint.get("page_class", "")).strip().lower()
    product = str(hint.get("trip_product", "")).strip().lower()
    blocked = bool(hint.get("blocked_by_modal", False))
    reason = _sanitize_vlm_label(hint.get("reason", ""), max_chars=96)
    if scope:
        lines.append(f"VLMPageScope={scope}")
    if page_class:
        lines.append(f"VLMPageClass={page_class}")
    if product:
        lines.append(f"VLMTripProduct={product}")
    if blocked:
        lines.append("VLMBlockedByModal=true")
    if reason:
        lines.append(f"VLMReason={reason}")

    fill = hint.get("fill_labels") if isinstance(hint.get("fill_labels"), dict) else {}
    fill_keys = (
        ("origin", "VLMOriginLabels"),
        ("dest", "VLMDestLabels"),
        ("depart", "VLMDepartLabels"),
        ("return", "VLMReturnLabels"),
        ("search", "VLMSearchLabels"),
    )
    for key, label in fill_keys:
        raw = fill.get(key, [])
        normalized = _sanitize_vlm_labels(raw, max_items=3)
        if normalized:
            lines.append(f"{label}={' | '.join(normalized)}")

    return "\n".join(lines)
