"""Deterministic KB reference resolver for fixture triage metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


_CARD_ROOT = Path("docs/kb/40_cards/cards")
_PATTERN_DATE_PICKER = "docs/kb/30_patterns/date_picker.md"
_PATTERN_COMBOBOX = "docs/kb/30_patterns/combobox_commit.md"
_PATTERN_SELECTORS = "docs/kb/30_patterns/selectors.md"
_REASON_CODE_PATTERN_MAP = {
    "calendar_not_open": _PATTERN_DATE_PICKER,
    "month_nav_exhausted": _PATTERN_DATE_PICKER,
    "calendar_day_not_found": _PATTERN_DATE_PICKER,
    "iata_mismatch": _PATTERN_COMBOBOX,
    "no_suggestion_match": _PATTERN_COMBOBOX,
    "selector_not_found": _PATTERN_SELECTORS,
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


def _existing_kb_path(rel_path: str) -> bool:
    root = _repo_root()
    return (root / rel_path).exists()


def _locale_tokens(locale_hint: Optional[str]) -> List[str]:
    hint = str(locale_hint or "").strip().lower()
    if not hint:
        return []
    out: List[str] = []
    if hint.startswith("ja"):
        out.extend(["ja", "jp"])
    elif hint.startswith("en"):
        out.append("en")
    else:
        out.append(hint.split("-", 1)[0])
    return out


def find_best_kb_card(
    site: str,
    reason_code: Optional[str],
    *,
    locale_hint: Optional[str] = None,
) -> Optional[str]:
    """Return best matching KB card path for site/reason, or None."""
    reason = str(reason_code or "").strip()
    site_key = str(site or "").strip()
    if not reason or not site_key:
        return None

    root = _repo_root()
    card_dir = root / _CARD_ROOT / site_key / reason
    if not card_dir.exists() or not card_dir.is_dir():
        return None

    files = sorted([p for p in card_dir.glob("*.md") if p.is_file()], key=lambda p: p.name)
    if not files:
        return None

    locale_tokens = _locale_tokens(locale_hint)
    if locale_tokens:
        for token in locale_tokens:
            for path in files:
                if token and token in path.name.lower():
                    return str(path.relative_to(root).as_posix())

    return str(files[0].relative_to(root).as_posix())


def suggest_kb_patterns(page_kind: str, signals: Dict[str, Any]) -> List[str]:
    """Suggest at most one pattern path based on page kind + signals."""
    page = str(page_kind or "").strip().lower()
    sig = signals if isinstance(signals, dict) else {}
    has_calendar = bool(sig.get("has_calendar_dialog", False))
    has_inputs = bool(sig.get("has_origin_dest_inputs", False))
    has_results = bool(sig.get("has_results_list", False))
    has_price = bool(sig.get("has_price_token", False))

    if has_calendar or "calendar" in page or "date" in page:
        return [_PATTERN_DATE_PICKER] if _existing_kb_path(_PATTERN_DATE_PICKER) else []

    if has_inputs:
        return [_PATTERN_COMBOBOX] if _existing_kb_path(_PATTERN_COMBOBOX) else []

    # Conservative selector fallback for ambiguous/non-result or missing-price pages.
    if (page in {"unknown", "error", "search_form"} or (has_results and not has_price)):
        return [_PATTERN_SELECTORS] if _existing_kb_path(_PATTERN_SELECTORS) else []

    return []


def _pattern_for_reason_code(reason_code: Optional[str]) -> Optional[str]:
    reason = str(reason_code or "").strip().lower()
    if not reason:
        return None
    path = _REASON_CODE_PATTERN_MAP.get(reason)
    if not path:
        return None
    return path if _existing_kb_path(path) else None


def build_kb_refs(
    site: str,
    locale_hint: str,
    page_kind: str,
    signals: Dict[str, Any],
    expected: Dict[str, Any],
    *,
    max_refs: int = 2,
) -> List[Dict[str, str]]:
    """Build deterministic KB refs (max 1 card + max 1 pattern, then cap)."""
    refs: List[Dict[str, str]] = []
    expected_obj = expected if isinstance(expected, dict) else {}
    ui_driver = expected_obj.get("ui_driver", {}) if isinstance(expected_obj.get("ui_driver"), dict) else {}
    extraction = expected_obj.get("extraction", {}) if isinstance(expected_obj.get("extraction"), dict) else {}

    preferred_reason_codes = [
        ui_driver.get("reason_code"),
        extraction.get("reason_code"),
    ]
    card_path: Optional[str] = None
    for reason_code in preferred_reason_codes:
        card_path = find_best_kb_card(site, reason_code, locale_hint=locale_hint)
        if card_path:
            break
    if card_path and card_path.startswith("docs/kb/"):
        refs.append({"type": "card", "path": card_path})

    reason_pattern: Optional[str] = None
    for reason_code in preferred_reason_codes:
        reason_pattern = _pattern_for_reason_code(reason_code)
        if reason_pattern:
            break

    pattern_candidates: List[str] = []
    if reason_pattern:
        pattern_candidates.append(reason_pattern)
    pattern_candidates.extend(suggest_kb_patterns(page_kind, signals)[:1])

    for pattern_path in pattern_candidates:
        if pattern_path.startswith("docs/kb/"):
            refs.append({"type": "pattern", "path": pattern_path})
            break

    # De-dup by path preserving order.
    deduped: List[Dict[str, str]] = []
    seen = set()
    for ref in refs:
        path = ref.get("path")
        if not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        deduped.append(ref)

    cap = max(0, int(max_refs))
    return deduped[:cap] if cap else []


def validate_kb_refs(kb_refs: List[Dict[str, Any]]) -> List[str]:
    """Validate kb_refs list; return warnings (no exceptions)."""
    warnings: List[str] = []
    root = _repo_root()
    if not isinstance(kb_refs, list):
        return ["kb_refs must be a list"]

    for idx, ref in enumerate(kb_refs):
        if not isinstance(ref, dict):
            warnings.append(f"kb_refs[{idx}] must be object")
            continue
        ref_type = ref.get("type")
        path = ref.get("path")
        if ref_type not in {"card", "pattern"}:
            warnings.append(f"kb_refs[{idx}].type invalid: {ref_type}")
        if not isinstance(path, str) or not path.strip():
            warnings.append(f"kb_refs[{idx}].path missing/invalid")
            continue
        if not path.startswith("docs/kb/"):
            warnings.append(f"kb_refs[{idx}].path must start with docs/kb/: {path}")
            continue
        if path.startswith("docs/archive/"):
            warnings.append(f"kb_refs[{idx}].path must not point to archive: {path}")
            continue
        if not (root / path).exists():
            warnings.append(f"kb_refs[{idx}].path does not exist: {path}")
    return warnings
