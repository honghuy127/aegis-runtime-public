"""Deterministic fixture triage metadata helpers (tooling/tests only)."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = "fixture_triage_v1"
PAGE_KIND_ENUM = {"flights_results", "search_form", "consent", "error", "unknown"}
EXTRACTION_STATUS_ENUM = {"ok", "missing_price", "parse_error", "not_applicable"}
UI_READINESS_ENUM = {"ready", "unready", "unknown"}
KB_REF_TYPE_ENUM = {"card", "pattern", "doc"}
_PRICE_RE = re.compile(r"([$\u00a3\u20ac\u00a5])\s*([0-9][0-9,]*)")
_CURRENCY_BY_SYMBOL = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}
_HTML_LANG_RE = re.compile(r"<html[^>]*\blang\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_JA_CHAR_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_CALENDAR_RE = re.compile(r"(calendar|datepicker|date picker|カレンダー|日付)", re.IGNORECASE)
_INPUT_RE = re.compile(r"<(input|textarea)\b|role=['\"]combobox['\"]", re.IGNORECASE)


def _deep_merge_prefer_existing(base: Any, existing: Any) -> Any:
    if isinstance(base, dict) and isinstance(existing, dict):
        out = deepcopy(base)
        for key, value in existing.items():
            out[key] = _deep_merge_prefer_existing(out.get(key), value)
        return out
    return deepcopy(existing) if existing is not None else deepcopy(base)


def _detect_locale_hint(html_text: str) -> str:
    html = html_text or ""
    m = _HTML_LANG_RE.search(html)
    if m:
        lang = (m.group(1) or "").strip()
        if lang:
            low = lang.lower()
            if low == "en":
                return "en-US"
            if low == "ja":
                return "ja-JP"
            return lang
    if _JA_CHAR_RE.search(html):
        return "ja-JP"
    text_lower = html.lower()
    if any(tok in text_lower for tok in ("google flights", "skyscanner", "best flights", "search flights")):
        return "en-US"
    return "unknown"


def _shared_signals(html_text: str) -> Dict[str, bool]:
    html = html_text or ""
    lower = html.lower()
    has_price_token = bool(_PRICE_RE.search(html)) or "price" in lower or "fare" in lower
    has_results_list = any(
        tok in lower
        for tok in (
            "search-results",
            "itinerary",
            "result-card",
            "best flights",
            "flight results",
            "data-testid=\"result",
            "data-testid='result",
        )
    )
    has_calendar_dialog = bool(_CALENDAR_RE.search(html)) and (
        "dialog" in lower or "role=\"dialog\"" in lower or "role='dialog'" in lower
    )
    has_origin_dest_inputs = bool(_INPUT_RE.search(html)) and any(
        tok in lower
        for tok in (
            "origin",
            "destination",
            "from",
            "to",
            "出発地",
            "目的地",
            "到着地",
        )
    )
    return {
        "has_price_token": bool(has_price_token),
        "has_results_list": bool(has_results_list),
        "has_calendar_dialog": bool(has_calendar_dialog),
        "has_origin_dest_inputs": bool(has_origin_dest_inputs),
    }


def _is_consent_page(html_text: str) -> bool:
    lower = (html_text or "").lower()
    return any(
        tok in lower
        for tok in (
            "before you continue",
            "cookie",
            "cookies",
            "consent",
            "privacy policy",
            "gdpr",
            "同意",
            "クッキー",
            "プライバシー",
        )
    )


def _is_error_page(html_text: str) -> bool:
    lower = (html_text or "").lower()
    return any(
        tok in lower
        for tok in (
            "error",
            "exception",
            "access denied",
            "forbidden",
            "not found",
            "captcha",
            "temporarily unavailable",
        )
    )


def _classify_page_kind(html_text: str, site: str, signals: Dict[str, bool]) -> str:
    html = html_text or ""
    lower = html.lower()
    if _is_consent_page(html):
        return "consent"
    if _is_error_page(html):
        return "error"

    if site == "skyscanner":
        brand = "skyscanner" in lower
        flightish = any(tok in lower for tok in ("flight", "flights", "search flights"))
        if brand and flightish and signals.get("has_results_list", False):
            return "flights_results"
        if brand and (signals.get("has_origin_dest_inputs", False) or "search" in lower):
            return "search_form"
        return "unknown"

    if site == "google_flights":
        brand = ("google flights" in lower) or ("google.com/travel/flights" in lower) or ("travel/flights" in lower)
        if brand and (signals.get("has_results_list", False) or "best flights" in lower):
            return "flights_results"
        if brand and (signals.get("has_origin_dest_inputs", False) or "search" in lower):
            return "search_form"
        return "unknown"

    # Shared fallback for future sites
    if signals.get("has_results_list") and ("flight" in lower or "flights" in lower):
        return "flights_results"
    if signals.get("has_origin_dest_inputs"):
        return "search_form"
    return "unknown"


def _guess_currency(html_text: str) -> str:
    m = _PRICE_RE.search(html_text or "")
    if not m:
        return "unknown"
    return _CURRENCY_BY_SYMBOL.get((m.group(1) or "").strip(), "unknown")


def classify_fixture(html_text: str, site: str) -> Dict[str, Any]:
    """Classify one fixture via deterministic string/regex heuristics."""
    html = html_text or ""
    site_key = str(site or "").strip().lower()
    signals = _shared_signals(html)
    page_kind = _classify_page_kind(html, site_key, signals)
    locale_hint = _detect_locale_hint(html)
    return {
        "page_kind": page_kind if page_kind in PAGE_KIND_ENUM else "unknown",
        "locale_hint": locale_hint,
        "signals": signals,
    }


def propose_expected(site: str, page_kind: str, signals: Dict[str, bool]) -> Dict[str, Any]:
    """Return conservative expected extraction/UI-driver outcomes."""
    site_key = str(site or "").strip().lower()
    _ = site_key  # reserved for future site-specific rules
    page_kind_norm = page_kind if page_kind in PAGE_KIND_ENUM else "unknown"
    has_price = bool((signals or {}).get("has_price_token", False))
    has_results = bool((signals or {}).get("has_results_list", False))

    extraction: Dict[str, Any] = {
        "status": "not_applicable",
        "currency": "unknown",
    }
    ui_driver: Dict[str, Any] = {
        "readiness": "unknown",
    }

    if page_kind_norm == "flights_results":
        ui_driver = {"readiness": "ready" if has_results else "unknown"}
        if has_price:
            extraction = {
                "status": "ok",
                "currency": "unknown",
            }
        else:
            extraction = {
                "status": "missing_price",
                "currency": "unknown",
                "reason_code": "missing_price",
            }
    elif page_kind_norm == "search_form":
        extraction = {
            "status": "not_applicable",
            "currency": "unknown",
            "reason_code": "search_form_not_results",
        }
        ui_driver = {
            "readiness": "unready",
            "reason_code": "search_form",
        }
    elif page_kind_norm == "consent":
        extraction = {
            "status": "not_applicable",
            "currency": "unknown",
            "reason_code": "non_flight_scope",
        }
        ui_driver = {
            "readiness": "unready",
            "reason_code": "consent",
        }
    elif page_kind_norm == "error":
        extraction = {
            "status": "parse_error",
            "currency": "unknown",
            "reason_code": "error_page",
        }
        ui_driver = {
            "readiness": "unready",
            "reason_code": "error",
        }
    else:
        extraction = {
            "status": "not_applicable",
            "currency": "unknown",
            "reason_code": "unknown_page_kind",
        }
        ui_driver = {
            "readiness": "unknown",
        }

    return {
        "extraction": extraction,
        "ui_driver": ui_driver,
    }


def validate_fixture_triage_metadata(
    data: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> List[str]:
    """Return validation errors for fixture triage metadata."""
    errors: List[str] = []
    payload = data if isinstance(data, dict) else {}

    required_top = [
        "schema_version",
        "site",
        "fixture_name",
        "fixture_path",
        "captured_from",
        "page_kind",
        "locale_hint",
        "signals",
        "expected",
        "kb_refs",
        "notes",
    ]
    for key in required_top:
        if key not in payload:
            errors.append(f"missing top-level field: {key}")

    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    page_kind = payload.get("page_kind")
    if page_kind not in PAGE_KIND_ENUM:
        errors.append(f"invalid page_kind: {page_kind}")

    locale_hint = str(payload.get("locale_hint", "") or "")
    if not (locale_hint == "unknown" or re.match(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$", locale_hint)):
        errors.append(f"invalid locale_hint: {locale_hint}")

    signals = payload.get("signals")
    required_signals = [
        "has_price_token",
        "has_results_list",
        "has_calendar_dialog",
        "has_origin_dest_inputs",
    ]
    if not isinstance(signals, dict):
        errors.append("signals must be object")
    else:
        for key in required_signals:
            if key not in signals:
                errors.append(f"signals missing key: {key}")
            elif not isinstance(signals.get(key), bool):
                errors.append(f"signals.{key} must be bool")

    expected = payload.get("expected")
    if not isinstance(expected, dict):
        errors.append("expected must be object")
    else:
        extraction = expected.get("extraction", {})
        ui_driver = expected.get("ui_driver", {})
        if not isinstance(extraction, dict):
            errors.append("expected.extraction must be object")
        else:
            if extraction.get("status") not in EXTRACTION_STATUS_ENUM:
                errors.append(f"invalid expected.extraction.status: {extraction.get('status')}")
            currency = extraction.get("currency")
            if not isinstance(currency, str):
                errors.append("expected.extraction.currency must be string")
            elif currency != "unknown" and not re.match(r"^[A-Z]{3}$", currency):
                errors.append(f"invalid expected.extraction.currency: {currency}")
            for bound_key in ("price_min", "price_max"):
                if bound_key in extraction and extraction[bound_key] is not None and not isinstance(extraction[bound_key], int):
                    errors.append(f"expected.extraction.{bound_key} must be int when present")
        if not isinstance(ui_driver, dict):
            errors.append("expected.ui_driver must be object")
        else:
            if ui_driver.get("readiness") not in UI_READINESS_ENUM:
                errors.append(f"invalid expected.ui_driver.readiness: {ui_driver.get('readiness')}")

    kb_refs = payload.get("kb_refs")
    if not isinstance(kb_refs, list):
        errors.append("kb_refs must be array")
    else:
        root = repo_root or Path(".")
        for i, ref in enumerate(kb_refs):
            if not isinstance(ref, dict):
                errors.append(f"kb_refs[{i}] must be object")
                continue
            ref_type = ref.get("type")
            ref_path = ref.get("path")
            if ref_type not in KB_REF_TYPE_ENUM:
                errors.append(f"kb_refs[{i}].type invalid: {ref_type}")
            if not isinstance(ref_path, str) or not ref_path.strip():
                errors.append(f"kb_refs[{i}].path missing/invalid")
            else:
                resolved = (root / ref_path).resolve() if not Path(ref_path).is_absolute() else Path(ref_path)
                if not resolved.exists():
                    errors.append(f"kb_refs[{i}].path does not exist: {ref_path}")

    # Secret/safety guard: avoid long raw HTML snippets in metadata strings.
    def _walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(value, list):
            for idx, v in enumerate(value):
                _walk(v, f"{path}[{idx}]")
        elif isinstance(value, str):
            if "<" in value and ">" in value and len(value) > 200:
                errors.append(f"string looks like raw HTML snippet >200 chars at {path}")

    _walk(payload)
    return errors


def build_fixture_triage_metadata(
    *,
    site: str,
    fixture_path: Path,
    html_text: str,
    existing_capture_meta: Optional[Dict[str, Any]] = None,
    existing_triage_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one triage metadata object with conservative merge behavior."""
    site_key = str(site or "").strip().lower()
    fixture_name = fixture_path.stem
    classification = classify_fixture(html_text, site_key)
    proposed_expected = propose_expected(site_key, classification["page_kind"], classification["signals"])

    if proposed_expected.get("extraction", {}).get("status") == "ok":
        # Lightweight currency guess only when a price token exists.
        currency = _guess_currency(html_text)
        if currency != "unknown":
            proposed_expected["extraction"]["currency"] = currency

    capture_meta = existing_capture_meta if isinstance(existing_capture_meta, dict) else {}
    triage_existing = existing_triage_meta if isinstance(existing_triage_meta, dict) else {}

    captured_from_base: Dict[str, Any] = {}
    for key in ("run_id", "source_path", "captured_at"):
        if key in capture_meta and capture_meta.get(key):
            captured_from_base[key] = capture_meta.get(key)

    captured_from = _deep_merge_prefer_existing(
        captured_from_base,
        triage_existing.get("captured_from") if isinstance(triage_existing.get("captured_from"), dict) else {},
    )

    expected_final = _deep_merge_prefer_existing(
        proposed_expected,
        triage_existing.get("expected") if isinstance(triage_existing.get("expected"), dict) else {},
    )

    kb_refs = triage_existing.get("kb_refs") if isinstance(triage_existing.get("kb_refs"), list) else []
    notes = ""
    if isinstance(capture_meta.get("notes"), str):
        notes = capture_meta.get("notes") or ""
    if isinstance(triage_existing.get("notes"), str):
        notes = triage_existing.get("notes") or notes

    fixture_path_posix = fixture_path.as_posix()

    return {
        "schema_version": SCHEMA_VERSION,
        "site": site_key,
        "fixture_name": fixture_name,
        "fixture_path": fixture_path_posix,
        "captured_from": captured_from,
        "page_kind": classification["page_kind"],
        "locale_hint": classification["locale_hint"],
        "signals": classification["signals"],
        "expected": expected_final,
        "kb_refs": kb_refs,
        "notes": notes,
    }


def load_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
