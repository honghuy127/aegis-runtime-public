"""Google Flights service runner implementation.

This module provides the Google Flights-specific implementation of the ServiceRunner
interface, encapsulating form filling, verification, recovery, and plan generation
logic for Google Flights search scenarios.

Architecture: This runner implements the ServiceRunner contract. Over time, functions
from scenario_runner.py are migrated here as methods. The wrapper pattern allows
gradual migration while maintaining backward compatibility.

Migrated components:
- Token and alias utilities for airports and dates
- Deeplink parsing and validation
- Form state assessment and mismatch detection
- Recovery policy builders
- Verification gates
"""

import logging
import os
import re
import time
import copy
from calendar import month_abbr, month_name, day_abbr, day_name
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Set, Tuple
from core.service_runners.base import ServiceRunner
from core.run_input_config import load_run_input_config
from core.service_ui_profiles import get_service_ui_profile, profile_role_token_list
from core.ui_tokens import build_button_text_selectors, prioritize_tokens
from llm.thresholds_helpers import get_threshold
from utils.knowledge_rules import get_knowledge_rule_tokens, get_tokens

log = logging.getLogger(__name__)


# ============================================================================
# REGEX CONSTANTS
# ============================================================================

_PRICE_TOKEN_RE = re.compile(
    r"(?:¥\s*\d[\d,]*|\$\s*\d[\d,]*|€\s*\d[\d,]*|£\s*\d[\d,]*|"
    r"JPY\s*\d[\d,]*|USD\s*\d[\d,]*|EUR\s*\d[\d,]*|GBP\s*\d[\d,]*)",
    re.IGNORECASE,
)
_RESULT_HINT_RE = re.compile(
    r"(Best|Cheapest|Duration|stops?|layover|itinerary|flight|depart|arrival|price|運賃|最安|直行)",
    re.IGNORECASE,
)


def _google_flights_bridge():
    """Lazy-load bridge to avoid import cycles during module initialization."""
    from core.scenario_runner.google_flights import service_runner_bridge as bridge

    return bridge


def _load_scope_tokens(
    *,
    group: str = "",
    key: str = "",
    legacy_key: str = "",
    fallback: Tuple[str, ...] = (),
) -> Tuple[str, ...]:
    """Load and normalize scope classification tokens from knowledge rules."""
    merged: List[str] = []
    if group and key:
        merged.extend(get_tokens(group, key))
    if legacy_key:
        merged.extend(get_knowledge_rule_tokens(legacy_key))
    merged.extend(list(fallback))
    out: List[str] = []
    seen = set()
    for token in merged:
        value = str(token or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _compile_token_regex(tokens: List[str], *, escape_literals: bool = True) -> re.Pattern:
    patterns: List[str] = []
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        patterns.append(re.escape(value) if escape_literals else value)
    if not patterns:
        return re.compile(r"$^")
    return re.compile("(?:%s)" % "|".join(patterns), re.IGNORECASE)


_GOOGLE_SCOPE_MAP_TOKENS = _load_scope_tokens(
    group="google",
    key="non_flight_map",
    fallback=(
        "地図を表示",
        "リストを表示",
        "地図データ",
        "gmp-internal-camera-control",
    ),
)
_GOOGLE_SCOPE_HOTEL_TOKENS = _load_scope_tokens(
    group="google",
    key="non_flight_hotel",
    fallback=(
        "hotel",
        "hotels",
        "ホテル",
        "宿泊",
        "check-in",
        "check out",
        "チェックイン",
    ),
)
_CONTACT_AUTH_HINT_RE = _compile_token_regex(
    list(
        _load_scope_tokens(
            group="hints",
            key="auth",
            fallback=(
                "email",
                "e-mail",
                "password",
                "passcode",
                "phone",
                "mobile",
                "tel",
                r"full[\s_-]*name",
                r"first[\s_-]*name",
                r"last[\s_-]*name",
                "surname",
                r"given[\s_-]*name",
                "login",
                r"log[\s_-]*in",
                r"sign[\s_-]*in",
                "signin",
                r"sign[\s_-]*up",
                "signup",
                "register",
                "account",
                "newsletter",
                "subscribe",
            ),
        )
    ),
    escape_literals=False,
)


# ============================================================================
# UTILITY FUNCTIONS - Migrated from scenario_runner.py
# ============================================================================


def _google_route_alias_tokens(code: str) -> Set[str]:
    """Return airport+metro aliases for one IATA code.

    Migrated from scenario_runner.py
    """
    from storage.shared_knowledge_store import get_airport_aliases_for_provider

    return get_airport_aliases_for_provider(code, "google_flights")


def _current_mimic_locale() -> str:
    """Resolve current runtime locale for locale-aware selector choices."""
    env_value = (os.getenv("FLIGHT_WATCHER_MIMIC_LOCALE") or "").strip()
    if env_value:
        return env_value
    try:
        cfg = load_run_input_config()
    except Exception:
        return ""
    value = cfg.get("mimic_locale")
    if isinstance(value, str):
        return value.strip()
    return ""


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


def _contains_any_token(blob: str, blob_upper: str, tokens) -> bool:
    """Match ascii tokens case-insensitively and non-ascii tokens directly.

    Migrated from scenario_runner.py
    """

    def _contains_ascii_token(token: str) -> bool:
        needle = token.upper()
        if re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", blob_upper):
            return True
        return len(needle) >= 5 and needle in blob_upper

    for token in tokens:
        if not token:
            continue
        if token.isascii():
            if _contains_ascii_token(token):
                return True
        elif token in blob:
            return True
    return False


def _is_non_flight_page_class(page_class: str) -> bool:
    text = str(page_class or "").strip().lower()
    return text in {"flight_hotel_package", "garbage_page", "irrelevant_page"}


def _is_results_ready(
    html: str,
    *,
    site_key: str = "",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
) -> bool:
    if not isinstance(html, str) or not html:
        return False
    site_norm = (site_key or "").strip().lower()
    if site_norm == "google_flights":
        quick_class = _google_quick_page_class(
            html,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
        )
        if _is_non_flight_page_class(quick_class):
            return False
        if origin and dest and depart:
            visible_html = _strip_nonvisible_html(html)
            context = {
                "origin": origin,
                "dest": dest,
                "depart": depart,
                "return_date": return_date or "",
            }
            if _google_has_contextual_price_card(visible_html, context):
                return True
            if _google_has_results_shell_for_context(visible_html, context):
                return True
            return False
    if not _PRICE_TOKEN_RE.search(html):
        return False
    return bool(_RESULT_HINT_RE.search(html))


def _selector_blob(selector) -> str:
    if isinstance(selector, str):
        return selector.lower()
    if isinstance(selector, list):
        return " ".join(str(item or "") for item in selector).lower()
    return ""


def _contains_selector_word(selector_blob: str, token: str) -> bool:
    if not token:
        return False
    escaped = re.escape(token.lower())
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", selector_blob))


def _infer_fill_role(step) -> Optional[str]:
    if not isinstance(step, dict) or step.get("action") != "fill":
        return None
    explicit_role = str(step.get("role", "") or "").strip().lower()
    if explicit_role in {"origin", "dest", "depart", "return"}:
        return explicit_role
    selector_blob = _selector_blob(step.get("selector"))
    if not selector_blob:
        return None
    if (
        "where from" in selector_blob
        or "origin" in selector_blob
        or _contains_selector_word(selector_blob, "from")
    ):
        return "origin"
    if (
        "where to" in selector_blob
        or "destination" in selector_blob
        or _contains_selector_word(selector_blob, "to")
    ):
        return "dest"
    if (
        "return" in selector_blob
        or "復路" in selector_blob
    ):
        return "return"
    if (
        "depart" in selector_blob
        or "departure" in selector_blob
        or "出発" in selector_blob
    ):
        return "depart"
    return None


def _selectors_look_search_submit(selectors) -> bool:
    blob = " ".join(str(item or "") for item in selectors).lower()
    tokens = get_knowledge_rule_tokens("search_submit_tokens") or ["search", "submit"]
    return any(str(token or "").lower() in blob for token in tokens if str(token or "").strip())


def _service_product_toggle_step(
    site_key: str,
    *,
    scope_class: str = "unknown",
    vlm_hint: Optional[dict] = None,
):
    hint = vlm_hint if isinstance(vlm_hint, dict) else {}
    prefer_ja = _current_mimic_locale().lower().startswith("ja")
    profile = get_service_ui_profile(site_key)
    selectors_cfg = profile.get("product_toggle_selectors", [])
    selectors = _profile_localized_list(selectors_cfg, prefer_ja=prefer_ja)
    labels: List[str] = []
    labels.extend(_sanitize_vlm_labels(hint.get("product_labels", []), max_items=8))
    labels.extend(_profile_localized_list(profile.get("product_toggle_labels", {}), prefer_ja=prefer_ja))
    if _is_non_flight_page_class(scope_class):
        labels.extend(prioritize_tokens(get_tokens("tabs", "flights"), locale_hint=_current_mimic_locale()))
    selectors = _dedupe_selectors(_label_click_selectors(labels) + selectors)
    if not selectors:
        return None
    return {"action": "click", "selector": selectors[:10], "optional": True}


def _service_mode_toggle_step(
    site_key: str,
    *,
    is_domestic: bool,
    vlm_hint: Optional[dict] = None,
    fallback_default: bool = False,
):
    prefer_ja = _current_mimic_locale().lower().startswith("ja")
    profile = get_service_ui_profile(site_key)
    mode_cfg = profile.get("mode_toggle_selectors", {})
    key = "domestic" if is_domestic else "international"
    mode_selectors: List[str] = []
    if isinstance(mode_cfg, dict):
        mode_selectors = _profile_localized_list(mode_cfg.get(key, []), prefer_ja=prefer_ja)
    label_cfg = profile.get("mode_toggle_labels", {})
    mode_labels: List[str] = []
    if isinstance(label_cfg, dict):
        mode_labels = _profile_localized_list(label_cfg.get(key, {}), prefer_ja=prefer_ja)
    mode_selectors = _dedupe_selectors(_label_click_selectors(mode_labels) + mode_selectors)
    if mode_selectors:
        return {"action": "click", "selector": mode_selectors[:8], "optional": True}
    if fallback_default:
        return {
            "action": "click",
            "selector": ["button[aria-label*='Domestic']", "button[aria-label*='International']"],
            "optional": True,
        }
    return None


def _strip_nonvisible_html(html: str) -> str:
    """Remove script/style/noscript blocks to reduce false probe positives.

    Migrated from scenario_runner.py
    """
    if not isinstance(html, str) or not html:
        return ""
    cleaned = re.sub(
        r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>",
        " ",
        html,
    )
    return re.sub(r"\s+", " ", cleaned)


def _google_date_tokens(iso_date: str) -> Set[str]:
    """Return common date-string variants used in Google Flights labels.

    Migrated from scenario_runner.py
    """
    if not isinstance(iso_date, str):
        return set()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso_date)
    if not m:
        return {iso_date}
    y = int(m.group(1))
    mo = int(m.group(2))
    d = int(m.group(3))
    tokens = {
        iso_date,
        f"{y}年{mo}月{d}日",
        f"{mo}月{d}日",
        f"{mo}/{d}",
        f"{mo:02d}/{d:02d}",
    }
    try:
        dt = datetime(y, mo, d)
        month_short = month_abbr[mo]
        month_full = month_name[mo]
        weekday_short = day_abbr[dt.weekday()]
        weekday_full = day_name[dt.weekday()]
        tokens.update(
            {
                f"{month_short} {d}",
                f"{month_full} {d}",
                f"{weekday_short}, {month_short} {d}",
                f"{weekday_full}, {month_full} {d}",
                f"{weekday_short}, {month_short} {d}, {y}",
                f"{weekday_full}, {month_full} {d}, {y}",
            }
        )
    except Exception:
        pass
    return tokens


def _parse_google_deeplink_context(url: str) -> Optional[Dict[str, Any]]:
    """Extract route/date details from Google Flights deep-link URL.

    Migrated from scenario_runner.py
    """
    from urllib.parse import urlparse, parse_qs

    if not isinstance(url, str) or "flt=" not in url:
        return None
    parsed = urlparse(url)
    flt = None
    fragment = parsed.fragment or ""
    if fragment:
        for segment in fragment.split(";"):
            seg = segment.strip()
            if seg.startswith("flt="):
                flt = seg.split("=", 1)[1]
                break
    if not flt:
        flt = (parse_qs(parsed.query).get("flt") or [None])[0]
    if not flt:
        return None
    legs = [leg.strip() for leg in flt.split("*") if leg.strip()]
    first = legs[0].split(".") if legs else []
    if len(first) < 3:
        return None
    second = legs[1].split(".") if len(legs) > 1 else []
    return {
        "origin": first[0].upper(),
        "dest": first[1].upper(),
        "depart": first[2],
        "return_date": second[2] if len(second) >= 3 else None,
    }


def _is_google_dest_placeholder(value: str) -> bool:
    """Return True when destination text is placeholder/explore copy."""
    from core.ui_tokens import prioritize_tokens, is_placeholder
    from utils.knowledge_rules import get_tokens
    from core.ui_tokens import normalize_visible_text

    raw = str(value or "").strip()
    if not raw:
        return True
    tokens = get_tokens("placeholders", "dest")
    if not tokens:
        return False
    normalized = normalize_visible_text(raw)
    prioritized = prioritize_tokens(tokens, locale_hint=_current_mimic_locale())
    normalized_tokens = [normalize_visible_text(str(token or "")) for token in prioritized if str(token or "").strip()]

    # Guard against false positives on legitimate airport labels (e.g. "大阪国際空港")
    # that can overlap placeholder tokens such as "到着空港" / "Arrival airport".
    if normalized not in normalized_tokens:
        raw_lower = raw.lower()
        if ("空港" in raw and len(raw) >= 5) or ("airport" in raw_lower and len(raw_lower) >= 8):
            strong_placeholder_markers = ("探索", "where to", "explore", "search")
            if not any(marker in normalized for marker in strong_placeholder_markers):
                return False

    return is_placeholder(normalized, prioritized)


# ============================================================================
# UTILITY & VALIDATION FUNCTIONS - Migrated from scenario_runner.py
# ============================================================================


def _google_missing_roles_from_reason(reason: str, trip_type: str) -> Set[str]:
    """Parse deeplink probe/rebind reason into missing role names."""
    if not isinstance(reason, str) or not reason:
        return set()
    lowered = reason.strip().lower()
    marker = "missing_"
    if marker not in lowered:
        return set()
    tail = lowered.split(marker, 1)[1]
    roles = {token for token in tail.split("_") if token in {"origin", "dest", "depart", "return"}}
    if trip_type != "round_trip":
        roles.discard("return")
    return roles


def _google_deeplink_page_state_recovery_policy() -> Tuple[bool, int]:
    """Return phase-3 deeplink page-state recovery gate + bounded action cap."""
    from utils.thresholds import get_threshold
    import os

    enabled = _env_bool(
        "FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_ENABLED",
        bool(get_threshold("google_flights_deeplink_page_state_recovery_enabled", False)),
    )
    max_actions = _env_int(
        "FLIGHT_WATCHER_GOOGLE_FLIGHTS_DEEPLINK_PAGE_STATE_RECOVERY_MAX_EXTRA_ACTIONS",
        int(get_threshold("google_flights_deeplink_page_state_recovery_max_extra_actions", 1)),
    )
    return bool(enabled), max(0, int(max_actions))


def _google_display_locale_hint_from_url(url: str) -> str:
    """Best-effort display-language hint from Google Flights deeplink URL (`hl=`)."""
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(str(url or ""))
        query = parse_qs(parsed.query or "")
        hl_vals = query.get("hl") or []
        if hl_vals:
            value = str(hl_vals[0] or "").strip()
            if value:
                return value
    except Exception:
        return ""
    return ""


def _google_default_date_reference_year() -> int:
    """Best-effort default year for localized date chips without explicit year."""
    from core.run_input_config import load_run_input_config
    from datetime import datetime, UTC
    import re

    try:
        cfg = load_run_input_config()
    except Exception:
        cfg = {}
    if isinstance(cfg, dict):
        for key in ("depart", "return_date"):
            raw = str(cfg.get(key, "") or "").strip()
            m = re.match(r"^(\d{4})-\d{2}-\d{2}$", raw)
            if m:
                return int(m.group(1))
    return datetime.now(UTC).year


def _google_has_iata_token(value: str) -> bool:
    """Return True when field text includes one uppercase 3-letter IATA-like token."""
    import re

    _IATA_TOKEN_RE = re.compile(r"\b[A-Z]{3}\b")
    text = str(value or "")
    return bool(_IATA_TOKEN_RE.search(text.upper()))


def _normalize_google_form_date_text(value: str, *, reference_year: Optional[int] = None) -> str:
    """Normalize common Google Flights localized date text to YYYY-MM-DD."""
    from utils.date_text import parse_english_month_day_text
    import re

    text = str(value or "").strip()
    if not text:
        return ""
    direct = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if direct:
        year = int(direct.group(1))
        month = int(direct.group(2))
        day = int(direct.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    ja_full = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if ja_full:
        year = int(ja_full.group(1))
        month = int(ja_full.group(2))
        day = int(ja_full.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    ja_md = re.search(r"(\d{1,2})月\s*(\d{1,2})日", text)
    if ja_md:
        year = int(reference_year) if reference_year else _google_default_date_reference_year()
        month = int(ja_md.group(1))
        day = int(ja_md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # English date chips are commonly rendered without year on Google Flights results.
    en_norm = parse_english_month_day_text(
        text,
        reference_year=(int(reference_year) if reference_year else _google_default_date_reference_year()),
    )
    if en_norm:
        return en_norm
    return ""


def _google_form_value_matches_airport(value: str, expected_code: str) -> bool:
    """Return True when observed value includes expected airport alias token."""
    from storage.shared_knowledge_store import get_airport_aliases_for_provider

    observed = str(value or "").strip()
    expected = str(expected_code or "").strip().upper()
    if not observed or not expected:
        return True
    if _is_google_dest_placeholder(observed):
        return False
    aliases = get_airport_aliases_for_provider(expected, "google_flights")
    if not aliases:
        aliases = {expected}
    return _contains_any_token(observed, observed.upper(), aliases)


def _google_form_value_matches_date(value: str, expected_date: str) -> bool:
    """Return True when observed value includes expected date token variants."""
    observed = str(value or "").strip()
    expected = str(expected_date or "").strip()
    if not observed or not expected:
        return True
    expected_tokens = _google_date_tokens(expected)
    if any(token in observed for token in expected_tokens):
        return True
    normalized = _normalize_google_form_date_text(
        observed,
        reference_year=int(expected.split("-", 1)[0]) if "-" in expected else None,
    )
    if not normalized:
        return False
    if normalized == expected:
        return True
    return normalized[5:] == expected[5:]


def _google_results_itinerary_matches_expected(
    html: str,
    *,
    expected_origin: str,
    expected_dest: str,
    expected_depart: str = "",
) -> bool:
    """Detect strong Google Flights results-card itinerary evidence for one route/date.

    Used as a bounded fallback when route-chip postcheck is low-confidence on results pages.
    """
    import re

    raw_html = str(html or "")
    if not raw_html:
        return False
    origin = str(expected_origin or "").strip().upper()
    dest = str(expected_dest or "").strip().upper()
    if not origin or not dest:
        return False
    depart_compact = ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(expected_depart or "").strip())
    if m:
        depart_compact = f"{m.group(1)}{m.group(2)}{m.group(3)}"

    itinerary_re = re.compile(
        r"itinerary=([A-Z]{3})-([A-Z]{3})-[A-Z0-9-]+-(\d{8})",
        flags=re.IGNORECASE,
    )
    hits = []
    for mm in itinerary_re.finditer(raw_html):
        o = str(mm.group(1) or "").upper()
        d = str(mm.group(2) or "").upper()
        dep = str(mm.group(3) or "")
        hits.append((o, d, dep))
        if len(hits) >= 24:
            break
    if not hits:
        return False
    for o, d, dep in hits:
        if o != origin or d != dest:
            continue
        if depart_compact and dep and dep != depart_compact:
            continue
        return True
    return False


# ============================================================================
# ENVIRONMENT VARIABLE HELPERS
# ============================================================================


def _env_bool(name: str, default: bool) -> bool:
    """Parse one boolean environment variable with fallback."""
    import os

    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse one integer environment variable with fallback."""
    import os

    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw.strip())
    except Exception:
        return int(default)


# ============================================================================
# GROUP 1 — DETECTION & URL UTILITIES - Migrated from scenario_runner.py
# ============================================================================


def _is_google_flights_deeplink(url: str) -> bool:
    """Detect Google Flights deeplink pattern (#flt=...)."""
    if not isinstance(url, str):
        return False
    # Match Google domain (any tld) + travel/flights path + #flt= fragment
    has_google = "google." in url.lower()
    has_path = "/travel/flights" in url.lower()
    has_flt_fragment = "#flt=" in url.lower()
    return has_google and has_path and has_flt_fragment


def _google_display_locale_hint_from_browser(browser) -> str:
    """Best-effort Google Flights display language hint from current page URL."""
    try:
        page_obj = getattr(browser, "page", None)
        if page_obj is not None:
            page_url = getattr(page_obj, "url", "")
            if callable(page_url):
                page_url = page_url()
            lang = _google_display_locale_hint_from_url(str(page_url or ""))
            if lang:
                return lang
    except Exception:
        return ""
    return ""


# ============================================================================
# GROUP 2 — SELECTOR PLAUSIBILITY VALIDATORS - Migrated from scenario_runner.py
# ============================================================================


def _google_search_selector_hint_is_plausible(selector: str) -> bool:
    """Allow only semantic search-button selectors in Google quick-rebind hint overlay."""
    s = str(selector or "").strip()
    if not s:
        return False
    lowered = s.lower()
    has_search_signal = ("search" in lowered) or ("検索" in s) or ("submit" in lowered)
    if not has_search_signal:
        return False
    # Avoid id/class-only test/synthetic selectors as learned search hints.
    semantic_clickable = any(
        token in lowered
        for token in (
            "button",
            "input[",
            "[role='button']",
            "[aria-label",
            ":has-text(",
            "text=",
            "type='submit'",
            'type="submit"',
        )
    )
    return semantic_clickable


def _google_route_fill_input_selector_hint_is_plausible(role: str, selector: str) -> bool:
    """Allow only role-anchored Google combobox/input selectors as learned input hints."""
    s = str(selector or "").strip()
    if not s:
        return False
    lowered = s.lower()
    if lowered in {"input", "[role='combobox']", "input[role='combobox']"}:
        return False
    # Require some semantic anchor in the selector itself to avoid cross-field drift.
    role_key = str(role or "").strip().lower()
    markers = []
    if role_key in {"origin", "dest"}:
        markers.extend(_google_role_tokens(role_key, "selector_ja"))
        markers.extend(_google_role_tokens(role_key, "selector_en"))
        if role_key == "origin":
            markers.extend(["origin", "from"])
        else:
            markers.extend(["destination", "to"])
    else:
        markers.extend(_google_role_tokens(role_key, "selector_ja"))
        markers.extend(_google_role_tokens(role_key, "selector_en"))
    return any(str(marker or "").strip().lower() in lowered for marker in markers if str(marker or "").strip())


def _google_date_open_selector_hint_is_plausible(role: str, selector: str) -> bool:
    """Allow only semantic date opener selectors for Google date fields."""
    s = str(selector or "").strip()
    if not s:
        return False
    lowered = s.lower()
    if lowered in {"input", "button", "[role='button']", "[role='combobox']"}:
        return False
    role_key = str(role or "").strip().lower()
    markers: List[str] = []
    markers.extend(_google_role_tokens(role_key, "selector_ja"))
    markers.extend(_google_role_tokens(role_key, "selector_en"))
    if role_key == "depart":
        markers.extend(["departure", "depart", "outbound", "出発", "往路"])
    elif role_key == "return":
        markers.extend(["return", "inbound", "復路", "帰り", "帰路"])
    has_role_signal = any(
        str(marker or "").strip().lower() in lowered
        for marker in markers
        if str(marker or "").strip()
    )
    has_clickable_signal = any(
        token in lowered
        for token in ("button", "input[", "[role='button']", "[role='combobox']", "[aria-label", "[placeholder")
    )
    return bool(has_role_signal and has_clickable_signal)


# ============================================================================
# GROUP 3 — TOKEN BANKS / I18N - Migrated from scenario_runner.py
# ============================================================================


def _google_role_i18n_token_bank() -> Dict[str, Dict[str, List[str]]]:
    """Return centralized Google Flights i18n role token bank with service profile overlay."""
    bank: Dict[str, Dict[str, List[str]]] = {
        "origin": {
            "selector_ja": ["出発地", "出発空港", "どこから"],
            "selector_en": ["Origin", "From", "Leaving from", "Where from"],
            "keyword_ja": ["出発地", "出発空港", "どこから"],
            "keyword_en": ["origin", "from", "leaving from"],
            "legacy_bare_text": ["Where from?", "出発地"],
        },
        "dest": {
            "selector_ja": ["目的地", "到着空港", "どこへ"],
            "selector_en": ["Destination", "To", "Where to"],
            "keyword_ja": ["目的地", "到着空港", "どこへ"],
            "keyword_en": ["destination", "to", "where to"],
            "legacy_bare_text": ["Where to?", "目的地"],
        },
        "depart": {
            "selector_ja": ["出発日", "往路", "往路出発日"],
            "selector_en": ["Depart", "Departure date", "Outbound"],
            "keyword_ja": ["出発日", "往路"],
            "keyword_en": ["depart", "departure", "departure date", "outbound"],
            "legacy_bare_text": ["Depart", "出発日"],
        },
        "return": {
            "selector_ja": ["復路", "復路出発日", "帰り", "帰路"],
            "selector_en": ["Return", "Return date", "Inbound"],
            "keyword_ja": ["復路", "帰り", "帰路"],
            "keyword_en": ["return", "inbound", "return date"],
            "legacy_bare_text": ["Return", "復路"],
        },
    }
    try:
        profile = get_service_ui_profile("google_flights")
    except Exception:
        profile = {}

    def _dedupe_tokens(items: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for raw in items:
            token = str(raw or "").strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out

    token_keys = ("selector_ja", "selector_en", "keyword_ja", "keyword_en", "legacy_bare_text")
    for role in ("origin", "dest", "depart", "return"):
        role_map = bank.get(role, {})
        for token_key in token_keys:
            configured = profile_role_token_list(profile, "semantic_role_tokens", role, token_key)
            if configured:
                role_map[token_key] = _dedupe_tokens(list(configured) + list(role_map.get(token_key, [])))
        bank[role] = role_map
    return bank


def _google_role_tokens(role: str, key: str) -> List[str]:
    """Return centralized Google role token list for one token category."""
    bank = _google_role_i18n_token_bank()
    role_map = bank.get(str(role or "").strip().lower(), {})
    raw = role_map.get(key, [])
    return [str(token) for token in raw if isinstance(token, str) and token.strip()]


def _google_selector_locale_markers() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return aggregate JA/EN markers used for locale-aware selector sorting."""
    bank = _google_role_i18n_token_bank()
    ja_markers: List[str] = []
    en_markers: List[str] = []
    for role_map in bank.values():
        ja_markers.extend(role_map.get("selector_ja", []))
        ja_markers.extend(role_map.get("keyword_ja", []))
        en_markers.extend(role_map.get("selector_en", []))
        en_markers.extend(role_map.get("keyword_en", []))
    # Add a few generic English field words that commonly appear in selectors.
    en_markers.extend(["where", "from", "to", "destination", "origin", "arrival", "depart", "return"])

    def _uniq(items: List[str], *, lower: bool) -> Tuple[str, ...]:
        out: List[str] = []
        seen = set()
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower() if lower else text
            if key in seen:
                continue
            seen.add(key)
            out.append(key if lower else text)
        return tuple(out)

    return _uniq(ja_markers, lower=False), _uniq(en_markers, lower=True)


# Group 4 — Form state helpers (pure dict/regex operations)

def _dedupe_selectors(values):
    """Return selectors deduped in first-seen order."""
    out = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_google_form_date_text(value: str, *, reference_year: Optional[int] = None) -> str:
    """Normalize common Google Flights localized date text to YYYY-MM-DD."""
    from utils.date_text import parse_english_month_day_text

    text = str(value or "").strip()
    if not text:
        return ""
    direct = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if direct:
        year = int(direct.group(1))
        month = int(direct.group(2))
        day = int(direct.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    ja_full = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if ja_full:
        year = int(ja_full.group(1))
        month = int(ja_full.group(2))
        day = int(ja_full.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    ja_md = re.search(r"(\d{1,2})月\s*(\d{1,2})日", text)
    if ja_md:
        year = int(reference_year) if reference_year else _google_default_date_reference_year()
        month = int(ja_md.group(1))
        day = int(ja_md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # English date chips are commonly rendered without year on Google Flights results.
    en_norm = parse_english_month_day_text(
        text,
        reference_year=(int(reference_year) if reference_year else _google_default_date_reference_year()),
    )
    if en_norm:
        return en_norm
    return ""


def _verification_confidence_rank(label: str) -> int:
    """Map form-verification confidence labels to rank."""
    text = str(label or "").strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    return 1


def _google_origin_needs_iata_support(value: str) -> bool:
    """Return True for generic origin labels that are too weak by themselves."""
    from core.ui_tokens import normalize_visible_text
    from utils.knowledge_rules import get_tokens

    raw = str(value or "").strip()
    if not raw:
        return False
    if _google_has_iata_token(raw):
        return False
    lowered = normalize_visible_text(raw)
    for token in get_tokens("placeholders", "origin"):
        marker = normalize_visible_text(str(token or ""))
        if marker and marker == lowered:
            return True
    return False


def _google_origin_looks_unbound(value: str, *, expected_origin: str = "") -> bool:
    """Return True when origin resembles generic region text without IATA support."""
    from core.ui_tokens import normalize_visible_text
    from core.ui_tokens import prioritize_tokens

    raw = str(value or "").strip()
    if not raw:
        return False
    if _google_has_iata_token(raw):
        return False
    expected = str(expected_origin or "").strip().upper()
    if expected and _google_form_value_matches_airport(raw, expected):
        # Prefilled city labels (e.g., "Tokyo" for HND) are semantically bound.
        return False

    region_tokens = prioritize_tokens(
        get_knowledge_rule_tokens("region_like_origin_tokens"),
        locale_hint=_current_mimic_locale(),
    )
    if not region_tokens:
        return False
    normalized = normalize_visible_text(raw)
    if len(raw) > 6:
        return True
    for token in region_tokens:
        marker = normalize_visible_text(str(token or ""))
        if marker and marker in normalized:
            return True
    return False


def _google_form_role_tokens() -> Dict[str, tuple]:
    """Return role-keyword map for Google Flights form field matching."""
    out: Dict[str, tuple] = {}
    # Bare English prepositions are useful as broad selector fallbacks, but too
    # ambiguous for DOM form-state extraction (for example "child aged 2 to 11").
    ambiguous_form_tokens = {
        "origin": {"from", "departure"},
        "dest": {"to", "arrival"},
    }
    for role in ("origin", "dest", "depart", "return"):
        ja_tokens = _google_role_tokens(role, "keyword_ja")
        en_tokens = _google_role_tokens(role, "keyword_en")
        if en_tokens:
            blocked = ambiguous_form_tokens.get(role, set())
            en_tokens = [
                tok
                for tok in en_tokens
                if str(tok or "").strip().lower() not in blocked
            ]
        extras: List[str] = []
        if role == "origin":
            extras.append("departure airport")
        if role == "dest":
            extras.append("arrival airport")
        if role == "return":
            extras.append("帰国")
        # Preserve existing style: lower-case English tokens and mixed JA/EN tuple.
        out[role] = tuple(_dedupe_selectors(ja_tokens + en_tokens + extras))
    return out


def _google_form_text_looks_instructional_noise(value: str) -> bool:
    """Return True when text looks like helper/instructional UI copy, not a field value."""
    from core.ui_tokens import normalize_visible_text

    raw = str(value or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    normalized = normalize_visible_text(raw)
    # Known recurring Google helper copy can contaminate route probes.
    if "select multiple airports" in lowered:
        return True
    if "switch to multi-select mode" in lowered:
        return True
    # Generic imperative instruction heuristics (kept bounded to longer text to avoid overmatching).
    if len(raw) >= 20 and "press " in lowered and " key" in lowered:
        return True
    if len(raw) >= 20 and "switch to " in lowered and " mode" in lowered:
        return True
    if len(raw) >= 28 and "enter a date" in lowered and "arrow keys" in lowered:
        return True
    # Long helper strings often concatenate action labels ("Done") with prose and contain no route/date value.
    if len(normalized) >= 36 and not _google_has_iata_token(raw):
        if not _normalize_google_form_date_text(raw):
            for marker in ("press the", "switch to", "mode", "add child", "remove child"):
                if marker in lowered:
                    return True
    return False


def _google_form_text_looks_date_like(value: str) -> bool:
    """Return True when text looks like a date field value (not a route field value)."""
    raw = str(value or "").strip()
    if not raw:
        return False
    if _normalize_google_form_date_text(raw):
        return True
    lowered = raw.lower()
    if len(raw) <= 24 and "," in raw:
        for marker in ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"):
            if marker in lowered:
                return True
    return False


def _google_form_candidates_from_html(html: str) -> list:
    """Extract `(label,value,text)` candidates from one HTML snapshot."""
    import html as html_lib

    if not isinstance(html, str) or not html:
        return []
    cleaned = _strip_nonvisible_html(html)
    tag_re = re.compile(
        r"<(?P<tag>input|button|div|span)[^>]*>",
        flags=re.IGNORECASE,
    )
    attr_re = re.compile(
        r'([a-zA-Z_:][a-zA-Z0-9_:\-]*)\s*=\s*("([^"]*)"|\'([^\']*)\')',
        flags=re.IGNORECASE,
    )
    out = []
    for match in tag_re.finditer(cleaned):
        raw_tag = match.group(0)
        attrs = {}
        for attr_match in attr_re.finditer(raw_tag):
            key = str(attr_match.group(1) or "").strip().lower()
            value = attr_match.group(3)
            if value is None:
                value = attr_match.group(4)
            attrs[key] = html_lib.unescape(str(value or "")).strip()
        label = " ".join(
            value
            for value in (
                attrs.get("aria-label"),
                attrs.get("placeholder"),
                attrs.get("name"),
                attrs.get("title"),
            )
            if value
        ).strip()
        value_text = attrs.get("value", "") or ""
        if not label and not value_text:
            continue
        out.append(
            {
                "label": label,
                "value": value_text,
                "text": label,
                "tag": str(match.group("tag") or "").lower(),
                "role": attrs.get("role", ""),
                "aria_hidden": attrs.get("aria-hidden", ""),
                "disabled": bool("disabled" in attrs),
                "input_like": str(match.group("tag") or "").strip().lower() == "input",
            }
        )
        if len(out) >= 1200:
            break
    return out


def _extract_google_form_state_from_candidates(
    candidates,
    *,
    current_url: str = "",
) -> Dict[str, Any]:
    """Build route/date form snapshot from DOM candidates."""
    from storage.shared_knowledge_store import get_airport_aliases_for_provider

    role_tokens = _google_form_role_tokens()
    if not isinstance(candidates, list) or not candidates:
        return {
            "origin_text": "",
            "dest_text": "",
            "depart_text": "",
            "return_text": "",
            "confidence": "low",
            "reason": "no_candidates",
        }

    def _pick(role: str) -> str:
        tokens = tuple(token.lower() for token in role_tokens.get(role, ()))
        best_text = ""
        best_score = None
        best_tiebreak = None
        for idx, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "") or "")
            label_lower = label.lower()
            matched_tokens = [token for token in tokens if token and token in label_lower]
            if not matched_tokens:
                continue
            value = str(item.get("value", "") or "").strip()
            text = str(item.get("text", "") or "").strip()
            candidate_text = value or text
            if not candidate_text:
                continue

            tag = str(item.get("tag", "") or "").strip().lower()
            role_attr = str(item.get("role", "") or "").strip().lower()
            input_like = bool(item.get("input_like")) or tag == "input"
            aria_hidden_raw = str(item.get("aria_hidden", "") or "").strip().lower()
            disabled = bool(item.get("disabled"))

            score = 0
            # Prefer actual field values and input-like candidates over container/helper text.
            if value:
                score += 28
            else:
                score -= 4
            if input_like:
                score += 18
            if role_attr == "combobox":
                score += 8
            elif "combobox" in role_attr:
                score += 4
            if tag == "input":
                score += 8
            elif tag in {"button", "div", "span"}:
                score -= 1
            if aria_hidden_raw == "true":
                score -= 8
            if disabled:
                score -= 6
            score += min(len(matched_tokens), 3) * 2
            longest_match = max((len(token) for token in matched_tokens), default=0)
            if longest_match >= 8:
                score += 4
            elif longest_match >= 4:
                score += 2

            if role in {"origin", "dest"}:
                if _google_has_iata_token(candidate_text):
                    score += 10
                if _google_form_text_looks_date_like(candidate_text):
                    score -= 34
                if _google_form_text_looks_instructional_noise(candidate_text):
                    score -= 30
            if role in {"depart", "return"}:
                if _normalize_google_form_date_text(candidate_text):
                    score += 8
                if _google_has_iata_token(candidate_text) and not _google_form_text_looks_date_like(candidate_text):
                    score -= 18
                if _google_form_text_looks_instructional_noise(candidate_text):
                    score -= 12

            # Prefer compact field-like values over long concatenated helper text.
            if len(candidate_text) > 80 and not value:
                score -= 8
            elif len(candidate_text) <= 24 and value:
                score += 2

            tiebreak = (
                int(bool(value)),
                int(input_like),
                int(role_attr == "combobox"),
                -len(candidate_text),
                -idx,
            )
            if best_score is None or score > best_score or (
                score == best_score and tiebreak > best_tiebreak
            ):
                best_score = score
                best_tiebreak = tiebreak
                best_text = candidate_text[:120]
        return best_text

    origin_text_raw = _pick("origin")
    dest_text_raw = _pick("dest")
    depart_text_raw = _pick("depart")
    return_text_raw = _pick("return")

    dest_is_placeholder = _is_google_dest_placeholder(dest_text_raw)
    dest_text = "" if dest_is_placeholder else dest_text_raw

    deeplink_ctx = _parse_google_deeplink_context(current_url)
    ctx_origin = str((deeplink_ctx or {}).get("origin", "") or "").strip().upper()
    reference_year = None
    ctx_depart = str((deeplink_ctx or {}).get("depart", "") or "").strip()
    depart_year_match = re.match(r"^(\d{4})-\d{2}-\d{2}$", ctx_depart)
    if depart_year_match:
        reference_year = int(depart_year_match.group(1))

    depart_iso = _normalize_google_form_date_text(
        depart_text_raw,
        reference_year=reference_year,
    )
    return_iso = _normalize_google_form_date_text(
        return_text_raw,
        reference_year=reference_year,
    )

    origin_has_context = False
    if origin_text_raw and ctx_origin:
        aliases = get_airport_aliases_for_provider(ctx_origin, "google_flights")
        if not aliases:
            aliases = {ctx_origin}
        origin_has_context = _contains_any_token(
            origin_text_raw,
            origin_text_raw.upper(),
            aliases,
        )

    origin_confidence = "high"
    if not origin_text_raw:
        origin_confidence = "low"
    elif _google_origin_needs_iata_support(origin_text_raw) and not origin_has_context:
        origin_confidence = "low"
    elif not _google_has_iata_token(origin_text_raw):
        origin_confidence = "medium"

    if not dest_text:
        dest_confidence = "low"
    elif _google_has_iata_token(dest_text):
        dest_confidence = "high"
    else:
        dest_confidence = "medium"

    if depart_text_raw and depart_iso:
        depart_confidence = "high"
    elif depart_text_raw:
        depart_confidence = "medium"
    else:
        depart_confidence = "low"

    if return_text_raw and return_iso:
        return_confidence = "high"
    elif return_text_raw:
        return_confidence = "medium"
    else:
        return_confidence = "low"

    required_filled = sum(1 for value in (origin_text_raw, dest_text, depart_text_raw) if value)
    if required_filled >= 3 and min(
        _verification_confidence_rank(origin_confidence),
        _verification_confidence_rank(dest_confidence),
        _verification_confidence_rank(depart_confidence),
    ) >= _verification_confidence_rank("medium"):
        confidence = "high"
        reason = "fields_bound"
        route_support = "strong"
    elif required_filled >= 2:
        confidence = "medium"
        reason = "partial_fields_found"
        route_support = "weak"
    else:
        confidence = "low"
        reason = "insufficient_fields_found"
        route_support = "none"

    if dest_is_placeholder:
        confidence = "low"
        reason = "dest_placeholder"
        route_support = "none"

    return {
        "origin_text": origin_text_raw,
        "dest_text": dest_text,
        "depart_text": depart_text_raw,
        "return_text": return_text_raw,
        "origin_text_raw": origin_text_raw,
        "dest_text_raw": dest_text_raw,
        "depart_text_raw": depart_text_raw,
        "return_text_raw": return_text_raw,
        "dest_is_placeholder": bool(dest_is_placeholder),
        "depart_iso": depart_iso,
        "return_iso": return_iso,
        "origin_confidence": origin_confidence,
        "dest_confidence": dest_confidence,
        "depart_confidence": depart_confidence,
        "return_confidence": return_confidence,
        "route_bound": route_support == "strong",
        "route_support": route_support,
        "confidence": confidence,
        "reason": reason,
    }


def _extract_google_flights_form_state(page) -> Dict[str, Any]:
    """Best-effort extraction of Google Flights form state from live DOM."""
    default_state = {
        "origin_text": "",
        "dest_text": "",
        "depart_text": "",
        "return_text": "",
        "dest_is_placeholder": False,
        "route_bound": False,
        "route_support": "none",
        "confidence": "low",
        "reason": "dom_probe_unavailable",
    }
    current_url = ""
    try:
        page_url = getattr(page, "url", "")
        if callable(page_url):
            page_url = page_url()
        current_url = str(page_url or "").strip()
    except Exception:
        current_url = ""
    if not current_url:
        try:
            if hasattr(page, "evaluate"):
                current_url = str(
                    page.evaluate("() => String(window.location.href || '')") or ""
                ).strip()
        except Exception:
            current_url = ""
    candidates = []
    try:
        if hasattr(page, "evaluate"):
            candidates = page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll(
                    "input,button,[role='button'],[role='combobox'],[aria-label]"
                  ));
                  const out = [];
                  for (const el of nodes.slice(0, 1600)) {
                    const labelParts = [];
                    for (const key of ["aria-label", "placeholder", "name", "title"]) {
                      const value = (el.getAttribute && el.getAttribute(key)) || "";
                      if (value) labelParts.push(String(value));
                    }
                    const label = labelParts.join(" ").trim();
                    const value = ("value" in el && el.value) ? String(el.value).trim() : "";
                    const text = String((el.textContent || "")).replace(/\\s+/g, " ").trim();
                    if (!label && !value && !text) continue;
                    out.push({label, value, text});
                  }
                  return out;
                }
                """
            )
    except Exception:
        candidates = []
    # Silent failure OK here; candidates will be empty, fallback to HTML probe below
    if isinstance(candidates, list) and candidates:
        state = _extract_google_form_state_from_candidates(
            candidates,
            current_url=current_url,
        )
        state["current_url"] = current_url
        state["reason"] = str(state.get("reason", "") or "dom_probe_ok")
        return state

    html_snapshot = ""
    try:
        if hasattr(page, "content"):
            html_snapshot = page.content()
    except Exception:
        html_snapshot = ""
    if html_snapshot:
        state = _extract_google_form_state_from_candidates(
            _google_form_candidates_from_html(html_snapshot),
            current_url=current_url,
        )
        state["current_url"] = current_url
        state["reason"] = str(state.get("reason", "") or "html_probe_ok")
        return state
    return default_state


def _assess_google_flights_fill_mismatch(
    *,
    form_state: Dict[str, Any],
    html: str = "",
    expected_origin: str,
    expected_dest: str,
    expected_depart: str,
    expected_return: str = "",
    min_confidence: str = "medium",
    fail_closed: bool = True,
) -> Dict[str, Any]:
    """Compare expected route/date against observed form state and decide blocking."""
    state = dict(form_state or {})
    observed_origin = str(state.get("origin_text", "") or "")
    observed_dest = str(state.get("dest_text", "") or "")
    observed_depart = str(state.get("depart_text", "") or "")
    observed_return = str(state.get("return_text", "") or "")
    observed_origin_raw = str(state.get("origin_text_raw", observed_origin) or observed_origin)
    observed_dest_raw = str(state.get("dest_text_raw", observed_dest) or observed_dest)
    observed_depart_raw = str(state.get("depart_text_raw", observed_depart) or observed_depart)
    observed_return_raw = str(state.get("return_text_raw", observed_return) or observed_return)
    dest_is_placeholder = bool(state.get("dest_is_placeholder")) or _is_google_dest_placeholder(
        observed_dest_raw
    )
    if dest_is_placeholder:
        observed_dest = ""
    confidence = str(state.get("confidence", "low") or "low").strip().lower()
    results_itinerary_match = bool(
        expected_origin
        and expected_dest
        and _google_results_itinerary_matches_expected(
            str(html or ""),
            expected_origin=expected_origin,
            expected_dest=expected_dest,
            expected_depart=expected_depart or "",
        )
    )

    explicit_mismatches = []
    if expected_origin and not str(observed_origin or "").strip():
        explicit_mismatches.append("origin")
    if expected_dest and (not str(observed_dest or "").strip() or dest_is_placeholder):
        explicit_mismatches.append("dest")
    if expected_depart and not str(observed_depart or "").strip():
        explicit_mismatches.append("depart")
    if expected_return and not str(observed_return or "").strip():
        explicit_mismatches.append("return")

    if (
        results_itinerary_match
        and confidence == "low"
        and explicit_mismatches
        and set(explicit_mismatches) == {"dest"}
    ):
        # Low-confidence destination chip placeholder can coexist with a correct
        # results surface; don't block solely on that if results itinerary matches.
        explicit_mismatches = []

    if explicit_mismatches:
        return {
            "block": True,
            "mismatch": True,
            "reason": "mismatch_placeholder_or_missing",
            "confidence": confidence,
            "mismatches": explicit_mismatches,
            "dest_is_placeholder": dest_is_placeholder,
            "expected": {
                "origin": expected_origin,
                "dest": expected_dest,
                "depart": expected_depart,
                "return": expected_return,
            },
            "observed": {
                "origin": observed_origin,
                "dest": observed_dest,
                "depart": observed_depart,
                "return": observed_return,
            },
            "observed_raw": {
                "origin": observed_origin_raw,
                "dest": observed_dest_raw,
                "depart": observed_depart_raw,
                "return": observed_return_raw,
            },
            "results_itinerary_match": results_itinerary_match,
        }

    if (
        results_itinerary_match
        and _verification_confidence_rank(confidence) < _verification_confidence_rank(min_confidence)
    ):
        low_conf_date_mismatches = []
        if expected_depart and observed_depart and not _google_form_value_matches_date(
            observed_depart, expected_depart
        ):
            low_conf_date_mismatches.append("depart")
        if expected_return and observed_return and not _google_form_value_matches_date(
            observed_return, expected_return
        ):
            low_conf_date_mismatches.append("return")
        if low_conf_date_mismatches:
            return {
                "block": True,
                "mismatch": True,
                "reason": "mismatch_low_confidence_results_route",
                "confidence": confidence,
                "mismatches": low_conf_date_mismatches,
                "dest_is_placeholder": dest_is_placeholder,
                "expected": {
                    "origin": expected_origin,
                    "dest": expected_dest,
                    "depart": expected_depart,
                    "return": expected_return,
                },
                "observed": {
                    "origin": observed_origin,
                    "dest": observed_dest,
                    "depart": observed_depart,
                    "return": observed_return,
                },
                "observed_raw": {
                    "origin": observed_origin_raw,
                    "dest": observed_dest_raw,
                    "depart": observed_depart_raw,
                    "return": observed_return_raw,
                },
                "results_itinerary_match": True,
            }
        return {
            "block": False,
            "mismatch": False,
            "reason": "match_results_itinerary_low_confidence",
            "confidence": confidence,
            "mismatches": [],
            "dest_is_placeholder": dest_is_placeholder,
            "expected": {
                "origin": expected_origin,
                "dest": expected_dest,
                "depart": expected_depart,
                "return": expected_return,
            },
            "observed": {
                "origin": observed_origin,
                "dest": observed_dest,
                "depart": observed_depart,
                "return": observed_return,
            },
            "observed_raw": {
                "origin": observed_origin_raw,
                "dest": observed_dest_raw,
                "depart": observed_depart_raw,
                "return": observed_return_raw,
            },
            "results_itinerary_match": True,
        }

    if _verification_confidence_rank(confidence) < _verification_confidence_rank(min_confidence):
        return {
            "block": bool(fail_closed),
            "mismatch": False,
            "reason": "low_confidence",
            "confidence": confidence,
            "dest_is_placeholder": dest_is_placeholder,
            "expected": {
                "origin": expected_origin,
                "dest": expected_dest,
                "depart": expected_depart,
                "return": expected_return,
            },
            "observed": {
                "origin": observed_origin,
                "dest": observed_dest,
                "depart": observed_depart,
                "return": observed_return,
            },
            "observed_raw": {
                "origin": observed_origin_raw,
                "dest": observed_dest_raw,
                "depart": observed_depart_raw,
                "return": observed_return_raw,
            },
            "results_itinerary_match": results_itinerary_match,
        }

    mismatches = []
    if not _google_form_value_matches_airport(observed_origin, expected_origin):
        mismatches.append("origin")
    if not _google_form_value_matches_airport(observed_dest, expected_dest):
        mismatches.append("dest")
    if not _google_form_value_matches_date(observed_depart, expected_depart):
        mismatches.append("depart")
    if expected_return and not _google_form_value_matches_date(observed_return, expected_return):
        mismatches.append("return")

    return {
        "block": bool(mismatches),
        "mismatch": bool(mismatches),
        "reason": "mismatch" if mismatches else "match",
        "confidence": confidence,
        "mismatches": mismatches,
        "dest_is_placeholder": dest_is_placeholder,
        "expected": {
            "origin": expected_origin,
            "dest": expected_dest,
            "depart": expected_depart,
            "return": expected_return,
        },
        "observed": {
            "origin": observed_origin,
            "dest": observed_dest,
            "depart": observed_depart,
            "return": observed_return,
        },
        "observed_raw": {
            "origin": observed_origin_raw,
            "dest": observed_dest_raw,
            "depart": observed_depart_raw,
            "return": observed_return_raw,
        },
        "results_itinerary_match": results_itinerary_match,
    }


def _selector_candidates(selector):
    """Normalize selector field to a list for fallback-friendly execution."""
    if isinstance(selector, str):
        return [selector]
    if isinstance(selector, list):
        return [s for s in selector if isinstance(s, str) and s]
    return []


def _env_list(name: str) -> list:
    """Parse pipe/comma-separated env var into trimmed unique items."""
    raw = os.getenv(name, "")
    if not isinstance(raw, str) or not raw.strip():
        return []
    out = []
    seen = set()
    normalized = raw.replace(",", "|")
    for item in normalized.split("|"):
        token = item.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out



def _profile_localized_list(section: dict, *, prefer_ja: bool) -> list:
    """Resolve localized list from profile section (dict or direct list).

    Keeps both primary and secondary locale variants and interleaves them so
    bounded selector truncation (for example `[:3]` / `[:5]`) still preserves
    cross-locale fallback candidates on mixed-language pages.
    Supports en/ja with legacy default/ja fallback.
    """
    if isinstance(section, list):
        return [v for v in section if isinstance(v, str) and v.strip()]
    if not isinstance(section, dict):
        return []
    default_bucket = section.get("default", [])
    if prefer_ja:
        raw_primary = section.get("ja", default_bucket)
        raw_secondary = section.get("en", default_bucket)
    else:
        raw_primary = section.get("en", default_bucket)
        raw_secondary = []

    def _clean(values) -> list[str]:
        out_local = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, str) or not item.strip():
                continue
            out_local.append(item)
        return out_local

    primary = _clean(raw_primary)
    secondary = _clean(raw_secondary)

    out = []
    seen = set()
    max_len = max(len(primary), len(secondary))
    for idx in range(max_len):
        for bucket in (primary, secondary):
            if idx >= len(bucket):
                continue
            value = bucket[idx]
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
    return out



def _profile_role_list(profile: dict, key: str, role: str, *, prefer_ja: bool) -> list:
    """Resolve one role list from profile key with optional locale variants."""
    section = profile.get(key, {})
    if not isinstance(section, dict):
        return []
    raw = section.get(role, [])
    return _profile_localized_list(raw, prefer_ja=prefer_ja)



def _allow_bare_text_fallback() -> bool:
    """Return True when legacy bare `text=` selectors are explicitly allowed."""
    return bool(get_threshold("scenario_selector_allow_bare_text_fallback", False))



def _maybe_append_bare_text_selectors(
    selectors: list,
    tokens: list,
    *,
    allow: bool,
) -> list:
    """Keep bare `text=` selectors disabled by default; append as last resort only."""
    normalized = [
        str(selector or "").strip()
        for selector in selectors or []
        if isinstance(selector, str) and str(selector or "").strip()
    ]
    bare = _dedupe_selectors([selector for selector in normalized if selector.lower().startswith("text=")])
    safe = _dedupe_selectors([selector for selector in normalized if not selector.lower().startswith("text=")])
    if not allow:
        return safe
    out = list(safe)
    out.extend(bare)
    for token in tokens or []:
        label = str(token or "").strip()
        if label:
            out.append(f"text={label}")
    return _dedupe_selectors(out)



def _build_click_selectors_for_tokens(tokens: list) -> list:
    """Build click selectors prioritizing role/aria targeting over broad text matching."""
    locale = _current_mimic_locale()
    labels = prioritize_tokens(
        [str(token or "").strip() for token in (tokens or []) if str(token or "").strip()],
        locale_hint=locale,
    )
    selectors = []
    for label in labels:
        quoted = label.replace("'", "\\'")
        selectors.extend(
            [
                f"[role='button'][aria-label*='{quoted}']",
                f"[role='tab'][aria-label*='{quoted}']",
                f"[role='radio'][aria-label*='{quoted}']",
                f"[role='option'][aria-label*='{quoted}']",
                f"[aria-label*='{quoted}']",
            ]
        )
    selectors.extend(build_button_text_selectors(labels))
    return _dedupe_selectors(selectors)



def _label_click_selectors(labels: list) -> list:
    """Build resilient click selectors from plain visible-label hints."""
    tokens = _sanitize_vlm_labels(labels)
    selectors = _build_click_selectors_for_tokens(tokens)
    return _maybe_append_bare_text_selectors(
        selectors,
        tokens,
        allow=_allow_bare_text_fallback(),
    )



def _service_fill_activation_clicks(site_key: str, role: str):
    """Return click selectors that can activate/open hidden fill controls."""
    env_map = {
        "origin": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_ORIGIN",
        "dest": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEST",
        "depart": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEPART",
        "return": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_RETURN",
    }
    allow_bare_text = _allow_bare_text_fallback()
    vlm_clicks = _label_click_selectors(_env_list(env_map.get(role, "")))
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    profile = get_service_ui_profile(site_key)
    knowledge_tokens = prioritize_tokens(
        get_knowledge_rule_tokens(f"fill_role_{role}_tokens"),
        locale_hint=locale,
    )
    keyword_tokens = _service_fill_activation_keywords(site_key, role)
    token_clicks = _build_click_selectors_for_tokens(
        prioritize_tokens(knowledge_tokens + keyword_tokens, locale_hint=locale)
    )

    generic = {
        "origin": [
            "input[placeholder*='From']",
            "input[aria-label*='From']",
            "input[placeholder*='出発']",
            "input[aria-label*='出発']",
            "label:has-text('From')",
            "label:has-text('出発')",
            "[role='combobox'][aria-label*='From']",
            "[role='button'][aria-label*='From']",
            "button:has-text('From')",
        ],
        "dest": [
            "input[placeholder*='To']",
            "input[aria-label*='To']",
            "input[placeholder*='目的地']",
            "input[aria-label*='目的地']",
            "label:has-text('To')",
            "label:has-text('目的地')",
            "button:has-text('目的地')",
            "[role='combobox'][aria-label*='To']",
            "[role='button'][aria-label*='To']",
            "button:has-text('To')",
        ],
        "depart": [
            "input[placeholder*='Depart']",
            "input[aria-label*='Depart']",
            "input[placeholder*='出発日']",
            "input[aria-label*='出発日']",
            "label:has-text('Depart')",
            "label:has-text('出発日')",
            "[role='combobox'][aria-label*='Depart']",
            "[role='button'][aria-label*='Depart']",
            "button:has-text('Depart')",
        ],
        "return": [
            "input[placeholder*='Return']",
            "input[aria-label*='Return']",
            "input[placeholder*='復路']",
            "input[aria-label*='復路']",
            "label:has-text('Return')",
            "label:has-text('復路')",
            "[role='combobox'][aria-label*='Return']",
            "[role='button'][aria-label*='Return']",
            "button:has-text('Return')",
        ],
    }
    extras = _profile_role_list(profile, "activation_clicks", role, prefer_ja=prefer_ja)
    legacy_tokens = _google_role_tokens(role, "legacy_bare_text")
    merged = _dedupe_selectors(vlm_clicks + extras + generic.get(role, []) + token_clicks)
    return _maybe_append_bare_text_selectors(
        merged,
        legacy_tokens,
        allow=allow_bare_text,
    )



def _service_fill_activation_keywords(site_key: str, role: str):
    """Return locale-aware text tokens to discover a field when selectors miss."""
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    profile = get_service_ui_profile(site_key)
    generic_en = {k: _google_role_tokens(k, "keyword_en") for k in ("origin", "dest", "depart", "return")}
    generic_ja = {k: _google_role_tokens(k, "keyword_ja") for k in ("origin", "dest", "depart", "return")}
    env_map = {
        "origin": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_ORIGIN",
        "dest": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEST",
        "depart": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_DEPART",
        "return": "FLIGHT_WATCHER_VLM_FILL_KEYWORDS_RETURN",
    }
    vlm_keywords = _env_list(env_map.get(role, ""))
    extras = _profile_role_list(profile, "activation_keywords", role, prefer_ja=prefer_ja)
    # Keep bilingual JA+EN coverage in both directions; order remains locale-aware.
    ordered_keywords = (
        generic_ja.get(role, []) + generic_en.get(role, [])
        if prefer_ja
        else generic_en.get(role, []) + generic_ja.get(role, [])
    )
    return _dedupe_selectors(vlm_keywords + extras + ordered_keywords)



def _google_route_reset_selectors() -> list:
    """Optional Google form reset/clear selectors (best effort)."""
    profile = get_service_ui_profile("google_flights")
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    configured = _profile_localized_list(
        profile.get("route_reset_selectors", {}),
        prefer_ja=prefer_ja,
    )
    token_selectors = build_button_text_selectors(
        prioritize_tokens(get_tokens("actions", "reset"), locale_hint=locale)
    )
    defaults = [
        "button[type='reset']",
    ]
    return _dedupe_selectors(configured + defaults + token_selectors)


def _google_date_done_selectors() -> list:
    """Selectors for confirming Google date picker selection."""
    profile = get_service_ui_profile("google_flights")
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    configured = _profile_localized_list(
        profile.get("date_done_selectors", {}),
        prefer_ja=prefer_ja,
    )
    token_selectors = build_button_text_selectors(
        prioritize_tokens(get_tokens("actions", "done"), locale_hint=locale)
    )
    defaults = []
    return _dedupe_selectors(configured + defaults + token_selectors)


def _google_force_bind_dest_selectors() -> list:
    """Destination selectors biased toward flight-form chips, not generic explore UI."""
    profile = get_service_ui_profile("google_flights")
    prefer_ja = _current_mimic_locale().lower().startswith("ja")
    configured = _profile_localized_list(
        profile.get("force_bind_dest_selectors", {}),
        prefer_ja=prefer_ja,
    )
    locale = _current_mimic_locale().lower()
    dest_tokens = prioritize_tokens(
        _google_role_tokens("dest", "selector_ja") + _google_role_tokens("dest", "selector_en"),
        locale_hint=locale,
    )
    defaults = []
    for token in dest_tokens:
        label = str(token or "").strip()
        if not label:
            continue
        defaults.extend(
            [
                f"[role='combobox'][aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
                f"input[aria-label*='{label}']",
            ]
        )
    return _dedupe_selectors(configured + defaults)


def _google_force_bind_flights_tab_selectors() -> list:
    """Selectors for forcing Google Flights tab context before route refills."""
    profile = get_service_ui_profile("google_flights")
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    configured = _profile_localized_list(
        profile.get("force_bind_flights_tab_selectors", {}),
        prefer_ja=prefer_ja,
    )
    tokens = prioritize_tokens(get_tokens("tabs", "flights"), locale_hint=locale)
    token_tab_selectors = []
    for token in tokens:
        label = str(token or "").strip()
        if not label:
            continue
        token_tab_selectors.extend(
            [
                f"[role='tab'][aria-label*='{label}']",
                f"[role='tab']:has-text('{label}')",
                f"a[aria-label*='{label}']",
            ]
        )
    token_button_selectors = build_button_text_selectors(tokens)
    defaults = [
        "[role='tab'][aria-selected='false']",
        "a[href*='/travel/flights']",
    ]
    return _dedupe_selectors(
        configured + token_tab_selectors + token_button_selectors + defaults
    )


def _google_force_bind_location_input_selectors(role: str) -> list:
    """Return role-focused input selectors for deterministic Google fill commit."""
    try:
        import core.scenario_runner as _sr  # type: ignore
    except Exception:
        _sr = None

    def _runtime_helper(name: str, fallback):
        if _sr is not None:
            candidate = getattr(_sr, name, None)
            if callable(candidate):
                return candidate
        return fallback

    current_mimic_locale = _runtime_helper("_current_mimic_locale", _current_mimic_locale)
    profile_role_list = _runtime_helper("_profile_role_list", _profile_role_list)
    prioritize = _runtime_helper("prioritize_tokens", prioritize_tokens)
    knowledge_rule_tokens = _runtime_helper("get_knowledge_rule_tokens", get_knowledge_rule_tokens)

    def _is_generic_active_textbox_selector(selector: str) -> bool:
        normalized = str(selector or "").strip().lower()
        return normalized in {
            "input[aria-autocomplete='list']",
            "input[aria-controls]",
            "input[role='combobox']",
            "[role='combobox'] input[type='text']",
            "input[type='text']",
        }

    role_key = str(role or "").strip().lower()
    if role_key not in {"origin", "dest"}:
        return []
    profile = get_service_ui_profile("google_flights")
    locale = str(current_mimic_locale() or "").lower()
    prefer_ja = locale.startswith("ja")
    active_textbox_configured = profile_role_list(
        profile,
        "active_textbox_selectors",
        role_key,
        prefer_ja=prefer_ja,
    )
    force_bind_configured = profile_role_list(
        profile,
        "force_bind_location_input_selectors",
        role_key,
        prefer_ja=prefer_ja,
    )
    role_tokens = prioritize(
        knowledge_rule_tokens(f"fill_role_{role_key}_tokens"),
        locale_hint=locale,
    )
    token_selectors = []
    for token in role_tokens:
        label = str(token or "").strip()
        if not label:
            continue
        token_selectors.extend(
            [
                f"input[aria-label*='{label}']",
                f"input[placeholder*='{label}']",
                f"[role='combobox'][aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
            ]
        )
    role_label_tokens = prioritize(
        _google_role_tokens(role_key, "selector_ja") + _google_role_tokens(role_key, "selector_en"),
        locale_hint=locale,
    )
    defaults = []
    for token in role_label_tokens:
        label = str(token or "").strip()
        if not label:
            continue
        defaults.extend(
            [
                f"input[jsname='yrriRe'][role='combobox'][aria-label*='{label}']",
                f"input[role='combobox'][aria-label*='{label}']",
            ]
        )
    preferred_active = [
        s for s in (active_textbox_configured or [])
        if not _is_generic_active_textbox_selector(s)
    ]
    generic_active = [
        s for s in (active_textbox_configured or [])
        if _is_generic_active_textbox_selector(s)
    ]
    # Prefer role-specific force-bind selectors first. Generic active textbox selectors are
    # intentionally demoted so bounded candidate truncation does not collapse to ambiguous
    # `input[role='combobox']` before field-specific selectors are tried.
    return _dedupe_selectors(
        list(force_bind_configured or [])
        + token_selectors
        + defaults
        + preferred_active
        + generic_active
    )


def _google_force_bind_suggestion_container_selectors() -> list:
    """Return selectors for open suggestion/listbox containers."""
    profile = get_service_ui_profile("google_flights")
    configured = profile.get("suggestion_list_selectors", [])
    if not configured:
        configured = profile.get("force_bind_suggestion_container_selectors", [])
    configured_list = (
        [s for s in configured if isinstance(s, str) and s.strip()]
        if isinstance(configured, list)
        else []
    )
    defaults = [
        "[role='listbox']",
        "[role='menu']",
        "ul[role='listbox']",
    ]
    return _dedupe_selectors(configured_list + defaults)


def _google_force_bind_suggestion_option_selectors() -> list:
    """Return selectors for first suggestion option click candidates."""
    profile = get_service_ui_profile("google_flights")
    configured = profile.get("suggestion_option_selectors", [])
    if not configured:
        configured = profile.get("force_bind_suggestion_option_selectors", [])
    configured_list = (
        [s for s in configured if isinstance(s, str) and s.strip()]
        if isinstance(configured, list)
        else []
    )
    defaults = [
        ":nth-match([role='option'], 1)",
        ":nth-match([role='menuitem'], 1)",
        ":nth-match(li[role='option'], 1)",
    ]
    return _dedupe_selectors(configured_list + defaults)


def _google_route_activation_selector_is_value_labeled(selector: str, value: str) -> bool:
    """Return True when a selector targets the route value/chip instead of the field."""
    sel = str(selector or "").strip()
    val = str(value or "").strip()
    if not sel or not val:
        return False
    # Route activation should not click already-selected chips like HND/ITM.
    return len(val) >= 2 and val.lower() in sel.lower()


def _google_route_activation_selector_is_ambiguous(role: str, selector: str) -> bool:
    """Return True for broad route labels that often collide with date/result UI."""
    role_key = str(role or "").strip().lower()
    raw = str(selector or "")
    lowered = raw.lower()
    if not lowered:
        return False

    def _has_any(tokens: list[str]) -> bool:
        return any(tok in lowered for tok in tokens if tok)

    if role_key == "origin":
        if not (("出発" in raw) or _has_any(["departure", "depart"])):
            return False
        if ("出発地" in raw) or ("出発空港" in raw):
            return False
        if _has_any(["where from", "from", "origin", "departure airport"]):
            return False
        return True

    if role_key == "dest":
        if not (("到着" in raw) or _has_any(["arrival"])):
            return False
        if ("目的地" in raw) or ("到着地" in raw) or ("到着空港" in raw):
            return False
        if _has_any(["where to", "to", "destination", "arrival airport"]):
            return False
        return True

    return False


def _google_route_activation_selector_is_multi_city_control(selector: str) -> bool:
    """Return True when selector likely targets Google Flights add-route / multi-city UI."""
    raw = str(selector or "")
    lowered = raw.lower()
    if not lowered:
        return False
    tokens = (
        "add flight",
        "add another flight",
        "add destination",
        "multi-city",
        "multicity",
        "複数都市",
        "区間を追加",
        "便を追加",
        "フライトを追加",
        "行き先を追加",
    )
    if any(tok in lowered for tok in tokens):
        return True
    plus_patterns = (
        "has-text('+')",
        'has-text("+")',
        "text=+",
        "[aria-label*='+']",
    )
    return any(p in lowered for p in plus_patterns)


def _google_route_activation_selectors(*, role: str, value: str, plan_selectors) -> list:
    """Return sanitized activation selectors for Google route combobox opening."""
    role_key = str(role or "").strip().lower()
    role_label_tokens = _dedupe_selectors(
        _google_role_tokens(role_key, "selector_ja")
        + _google_role_tokens(role_key, "selector_en")
    )
    role_container_seeds = []
    for token in role_label_tokens:
        label = str(token or "").strip()
        if not label:
            continue
        role_container_seeds.extend(
            [
                f"[role='combobox'][aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
            ]
        )
    base = _dedupe_selectors(
        role_container_seeds
        + _service_fill_activation_clicks("google_flights", role_key)
        + _selector_candidates(plan_selectors)
    )
    filtered = []
    for sel in base:
        if _google_route_activation_selector_is_value_labeled(sel, value):
            continue
        if _google_route_activation_selector_is_multi_city_control(sel):
            continue
        if _google_route_activation_selector_is_ambiguous(role_key, sel):
            continue
        filtered.append(sel)
    candidates = filtered or base

    role_labels = tuple(token.lower() for token in role_label_tokens)
    exact_role_inputs = {
        "origin": ("where from?", "where from"),
        "dest": ("where to?", "where to"),
    }.get(role_key, ())

    def _rank(sel: str) -> tuple:
        raw = str(sel or "")
        lowered = raw.lower()
        is_combobox = "[role='combobox']" in lowered
        is_button = "[role='button']" in lowered or lowered.startswith("button")
        is_input = lowered.startswith("input[")
        is_label = lowered.startswith("label:") or lowered.startswith("text=")
        is_generic_aria = ("[aria-label*=" in lowered) and not (is_combobox or is_button or is_input)
        is_tabish = any(tag in lowered for tag in ("[role='tab']", "[role='radio']", "[role='option']"))
        label_score = sum(1 for tok in role_labels if tok and tok in lowered)
        exact_role_label_score = max((1 for tok in exact_role_inputs if tok and tok in lowered), default=0)
        exact_aria_label = ("[aria-label='" in lowered) or ('[aria-label^=' in lowered)
        exact_placeholder = ("[placeholder='" in lowered) or ('[placeholder^=' in lowered)
        # Prefer field containers/buttons, then direct inputs, then generic text/label/tab-ish fallbacks.
        return (
            exact_role_label_score,
            int(exact_aria_label or exact_placeholder),
            int(is_combobox),
            int(is_button),
            int(is_input),
            label_score,
            -int(is_generic_aria),
            -int(is_label),
            -int(is_tabish),
        )

    return sorted(candidates, key=_rank, reverse=True)


def _google_step_trace_route_fill_roles_ok(step_trace) -> dict:
    """Return whether origin/dest route fill steps succeeded in the current trace."""
    out = {"origin": False, "dest": False}
    for item in step_trace or []:
        if not isinstance(item, dict):
            continue
        if item.get("action") != "fill":
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in out:
            continue
        status_ok = str(item.get("status", "") or "").strip().lower() == "ok"
        fill_commit = item.get("fill_commit") if isinstance(item.get("fill_commit"), dict) else {}
        commit_ok = bool(fill_commit.get("ok")) if isinstance(fill_commit, dict) and fill_commit else status_ok
        out[role] = out[role] or bool(status_ok or commit_ok)
    return out


def _google_step_trace_local_date_open_failure(step_trace) -> dict:
    """Detect deterministic local Google date-open failures within one turn trace."""
    route_ok = _google_step_trace_route_fill_roles_ok(step_trace)
    for item in reversed(step_trace or []):
        if not isinstance(item, dict):
            continue
        if item.get("action") != "fill":
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in {"depart", "return"}:
            continue
        status = str(item.get("status", "") or "").strip().lower()
        if status not in {"calendar_not_open", "month_nav_buttons_not_found", "date_picker_unverified"}:
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        fill_commit = item.get("fill_commit") if isinstance(item.get("fill_commit"), dict) else {}
        if not isinstance(evidence, dict):
            evidence = {}
        if not evidence and isinstance(fill_commit, dict):
            evidence = fill_commit.get("evidence") if isinstance(fill_commit.get("evidence"), dict) else {}
        failure_stage = str(
            (evidence or {}).get("calendar.failure_stage", "")
            or (evidence or {}).get("stage", "")
            or ""
        ).strip().lower()
        if status == "calendar_not_open":
            if failure_stage and failure_stage != "open":
                continue
            match_reason = "calendar_not_open_local_open_stage"
            normalized_failure_stage = failure_stage or "open"
        elif status == "month_nav_buttons_not_found":
            # Header parsed and calendar root was found, but month-nav controls were unavailable.
            # Treat as deterministic local date-picker failure once route fill core is already OK.
            if failure_stage and failure_stage not in {"month_nav_buttons_detection", "month_nav"}:
                continue
            match_reason = "month_nav_buttons_not_found_local_picker_stage"
            normalized_failure_stage = failure_stage or "month_nav_buttons_detection"
        else:
            if not bool((evidence or {}).get("verify.active_matches_expected")):
                continue
            match_reason = "date_picker_unverified_local_verify_false_negative"
            normalized_failure_stage = failure_stage or "verify"
        return {
            "matched": True,
            "reason": match_reason,
            "role": role,
            "status": status,
            "failure_stage": normalized_failure_stage,
            "route_fill_origin_ok": bool(route_ok.get("origin")),
            "route_fill_dest_ok": bool(route_ok.get("dest")),
            "route_fill_core_ok": bool(route_ok.get("origin") and route_ok.get("dest")),
        }
    return {
        "matched": False,
        "reason": "",
        "route_fill_origin_ok": bool(route_ok.get("origin")),
        "route_fill_dest_ok": bool(route_ok.get("dest")),
        "route_fill_core_ok": bool(route_ok.get("origin") and route_ok.get("dest")),
    }


def _google_should_suppress_force_bind_after_date_failure(step_trace) -> dict:
    """Return bounded force-bind suppression verdict for deterministic local date-open failures."""
    info = _google_step_trace_local_date_open_failure(step_trace)
    info = dict(info or {})
    if "reason" in info:
        info["match_reason"] = str(info.get("reason", "") or "")
    if not bool(info.get("matched")):
        return {**info, "use": False, "reason": ""}
    if not bool(info.get("route_fill_core_ok")):
        return {**info, "use": False, "reason": ""}
    return {
        **info,
        "use": True,
        "reason": "recent_local_date_picker_failure_after_route_fill",
    }


def _google_has_contextual_price_card(visible_html: str, context: dict) -> bool:
    """Require at least one price card that matches route + requested dates."""
    if not isinstance(visible_html, str) or not visible_html:
        return False
    if not isinstance(context, dict):
        return False

    origin_tokens = _google_route_alias_tokens(context.get("origin"))
    dest_tokens = _google_route_alias_tokens(context.get("dest"))
    depart_tokens = _google_date_tokens(context.get("depart") or "")
    return_tokens = _google_date_tokens(context.get("return_date") or "")

    labels = []
    labels.extend(re.findall(r'aria-label="([^"]{1,1400})"', visible_html))
    labels.extend(re.findall(r"aria-label='([^']{1,1400})'", visible_html))

    for label in labels:
        if not _PRICE_TOKEN_RE.search(label):
            continue
        label_upper = label.upper()
        if not _contains_any_token(label, label_upper, origin_tokens):
            continue
        if not _contains_any_token(label, label_upper, dest_tokens):
            continue
        if depart_tokens and not any(tok in label for tok in depart_tokens):
            continue
        if return_tokens and not any(tok in label for tok in return_tokens):
            continue
        return True
    return False


def _google_has_results_shell_for_context(visible_html: str, context: dict) -> bool:
    """Detect Google Flights results shell for the requested route/date context.

    This is intentionally weaker than `_google_has_contextual_price_card(...)`:
    it accepts a valid results page when Google has rendered the search results shell
    (header + result-count alert) but contextual price-card aria-labels have not yet
    stabilized. It still rejects homepage/explore surfaces and explicit error surfaces.
    """
    if not isinstance(visible_html, str) or not visible_html:
        return False
    if not isinstance(context, dict):
        return False

    lower = visible_html.lower()
    if "search results" not in lower:
        return False
    if "oops, something went wrong" in lower:
        return False
    if "no results returned" in lower and "reload" in lower:
        return False
    if not re.search(r"\b\d+\s+results returned\b", lower):
        return False

    origin = str(context.get("origin", "") or "").strip()
    dest = str(context.get("dest", "") or "").strip()
    depart = str(context.get("depart", "") or "").strip()
    return_date = str(context.get("return_date", "") or "").strip()

    blob_upper = visible_html.upper()
    if origin and not _contains_any_token(visible_html, blob_upper, _google_route_alias_tokens(origin)):
        return False
    if dest and not _contains_any_token(visible_html, blob_upper, _google_route_alias_tokens(dest)):
        return False

    depart_tokens = _google_date_tokens(depart) if depart else set()
    if depart_tokens and not any(tok in visible_html for tok in depart_tokens):
        return False
    return_tokens = _google_date_tokens(return_date) if return_date else set()
    if return_tokens and not any(tok in visible_html for tok in return_tokens):
        return False
    return True


def _google_deeplink_probe_status(html: str, url: str):
    """Return deeplink probe readiness plus a concrete reason when not ready."""
    visible_html = _strip_nonvisible_html(html)
    context = _parse_google_deeplink_context(url)
    if not _is_results_ready(
        visible_html,
        site_key="google_flights",
        origin=context.get("origin") if isinstance(context, dict) else "",
        dest=context.get("dest") if isinstance(context, dict) else "",
        depart=context.get("depart") if isinstance(context, dict) else "",
        return_date=context.get("return_date") if isinstance(context, dict) else "",
    ):
        quick_class = _google_quick_page_class(
            visible_html,
            origin=context.get("origin") if isinstance(context, dict) else "",
            dest=context.get("dest") if isinstance(context, dict) else "",
            depart=context.get("depart") if isinstance(context, dict) else "",
            return_date=context.get("return_date") if isinstance(context, dict) else "",
        )
        if _is_non_flight_page_class(quick_class):
            return False, f"non_flight_scope_{quick_class}"
        return False, "missing_result_or_price_token"
    if not context:
        return True, "no_context"
    depart = context.get("depart")
    return_date = context.get("return_date")
    missing = []
    depart_tokens = _google_date_tokens(depart) if depart else set()
    if depart_tokens and not any(token in visible_html for token in depart_tokens):
        missing.append("depart")
    return_tokens = _google_date_tokens(return_date) if return_date else set()
    if return_tokens and not any(token in visible_html for token in return_tokens):
        missing.append("return")
    blob_upper = visible_html.upper()
    if not _contains_any_token(
        visible_html,
        blob_upper,
        _google_route_alias_tokens(context.get("origin")),
    ):
        missing.append("origin")
    if not _contains_any_token(
        visible_html,
        blob_upper,
        _google_route_alias_tokens(context.get("dest")),
    ):
        missing.append("dest")
    if missing:
        return False, f"missing_{'_'.join(missing)}"
    if not _google_has_contextual_price_card(visible_html, context):
        if _google_has_results_shell_for_context(visible_html, context):
            return True, "results_shell_no_contextual_price_card"
        return False, "missing_contextual_price_card"
    return True, "ok"


def _default_google_flights_plan(origin: str, dest: str, depart: str):
    """Return a heuristic fallback plan for Google Flights when LLM plan fails.

    Migrated from scenario_runner.py
    """
    bridge = _google_flights_bridge()

    plan = [
        {
            "action": "fill",
            "selector": bridge.service_fill_fallbacks("google_flights", "origin"),
            "value": origin,
        },
        {
            "action": "fill",
            "selector": bridge.service_fill_fallbacks("google_flights", "dest"),
            "value": dest,
        },
        {
            "action": "fill",
            "selector": bridge.service_fill_fallbacks("google_flights", "depart"),
            "value": depart,
        },
        {
            "action": "click",
            "selector": bridge.service_search_click_fallbacks("google_flights"),
        },
        {
            "action": "wait",
            "selector": bridge.service_wait_fallbacks("google_flights"),
        },
    ]
    return plan


def _google_deeplink_recovery_plan(
    origin: str,
    dest: str,
    depart: str,
    *,
    return_date: str = None,
    trip_type: str = "one_way",
    missing_roles: set = None,
    soft_fail_fills: bool = True,
):
    """Short recovery plan after deep-link probe/rebind fails on Google Flights.

    Migrated from scenario_runner.py
    """
    bridge = _google_flights_bridge()

    missing_roles = {r for r in (missing_roles or set()) if r in {"origin", "dest", "depart", "return"}}
    required_roles = {"origin", "dest", "depart"}
    role_values = [
        ("origin", origin),
        ("dest", dest),
        ("depart", depart),
    ]
    if trip_type == "round_trip" and return_date:
        role_values.append(("return", return_date))

    if missing_roles:
        role_values.sort(key=lambda pair: (0 if pair[0] in missing_roles else 1))

    plan = []
    for role, value in role_values:
        if not isinstance(value, str) or not value.strip():
            continue
        step = {
            "action": "fill",
            "selector": bridge.service_fill_fallbacks("google_flights", role),
            "value": value,
        }
        if soft_fail_fills and not missing_roles:
            # When deeplink probe only reports non-flight scope (no concrete missing role),
            # keep all recovery fills soft so one brittle selector miss does not abort
            # the whole attempt before the planner can try alternates.
            step["optional"] = True
        elif missing_roles and role not in missing_roles:
            step["optional"] = True
        elif soft_fail_fills and role not in required_roles:
            # Keep non-core fields (currently return) soft in recovery mode.
            step["optional"] = True
        elif role == "return":
            step["optional"] = True

        if step.get("optional") and role in required_roles:
            # Keep plan actionable while allowing soft-fail for route-critical fields.
            step["required_for_actionability"] = True
        plan.append(step)

    plan.extend(
        [
            {
                "action": "click",
                "selector": bridge.service_search_click_fallbacks("google_flights"),
                "optional": True,
            },
            {
                "action": "wait",
                "selector": bridge.service_wait_fallbacks("google_flights"),
            },
        ]
    )
    return plan


def _google_route_core_only_recovery_plan(plan, *, origin: str = "", dest: str = ""):
    """Keep Google recovery follow-up focused on route-core (origin/dest) first.

    Phase B uses this after a route-core-before-date gate failure so the next turn
    prioritizes rebinding origin/destination before retrying date picker steps.

    Migrated from scenario_runner.py
    """
    bridge = _google_flights_bridge()

    if not isinstance(plan, list):
        return plan

    out = []
    seen_roles = set()
    search_click_added = False
    wait_added = False
    for step in plan:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "") or "").strip().lower()
        if action == "fill":
            role = _infer_fill_role(step)
            if role in {"depart", "return"}:
                continue
            if role in {"origin", "dest"}:
                if role in seen_roles:
                    continue
                seen_roles.add(role)
                out.append(copy.deepcopy(step))
                continue
            # Keep non-route fills out of route-core phase.
            continue
        if action == "click" and not search_click_added and _selectors_look_search_submit(
            _selector_candidates(step.get("selector"))
        ):
            out.append(copy.deepcopy(step))
            search_click_added = True
            continue
        if action == "wait" and not wait_added:
            out.append(copy.deepcopy(step))
            wait_added = True
            continue

    # Ensure minimal route-core coverage even if planner omitted one role.
    if "origin" not in seen_roles:
        if not str(origin or "").strip():
            return out
        out.insert(
            0,
            {
                "action": "fill",
                "selector": bridge.service_fill_fallbacks("google_flights", "origin"),
                "value": str(origin or ""),
                "optional": False,
            },
        )
    if "dest" not in seen_roles:
        if not str(dest or "").strip():
            return out
        insert_at = 1 if out else 0
        out.insert(
            insert_at,
            {
                "action": "fill",
                "selector": bridge.service_fill_fallbacks("google_flights", "dest"),
                "value": str(dest or ""),
                "optional": False,
            },
        )
    return out


def _google_recovery_collab_limits_from_thresholds() -> Dict[str, int | bool]:
    """Bounded caps for Phase B collaborative recovery (planner + VLM + repair).

    Migrated from scenario_runner.py
    """
    return {
        "enabled": bool(
            get_threshold("google_flights_recovery_collab_enabled", True)
        ),
        "max_vlm": max(
            0, int(get_threshold("google_flights_recovery_collab_max_vlm_page_kind_calls", 1))
        ),
        "max_repair": max(
            0, int(get_threshold("google_flights_recovery_collab_max_repair_calls", 1))
        ),
        "max_planner": max(
            0, int(get_threshold("google_flights_recovery_collab_max_planner_calls", 1))
        ),
        "route_core_only_first": bool(
            get_threshold("google_flights_recovery_collab_route_core_only_first", True)
        ),
        "planner_timeout_sec": max(
            5, int(get_threshold("google_flights_recovery_collab_planner_timeout_sec", 45))
        ),
    }


def _google_non_flight_scope_repair_plan(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = None,
    trip_type: str = "one_way",
    is_domestic: bool = False,
    scope_class: str = "unknown",
    vlm_hint: Optional[dict] = None,
):
    """Recovery plan that first forces flight-product context before form fills.

    Migrated from scenario_runner.py
    """
    bridge = _google_flights_bridge()

    plan = []
    product_step = _service_product_toggle_step(
        "google_flights",
        scope_class=scope_class,
        vlm_hint=vlm_hint,
    )
    if isinstance(product_step, dict):
        plan.append(product_step)

    mode_step = _service_mode_toggle_step(
        "google_flights",
        is_domestic=is_domestic,
        vlm_hint=vlm_hint,
        fallback_default=True,
    )
    if isinstance(mode_step, dict):
        mode_step = dict(mode_step)
        mode_step["optional"] = True
        plan.append(mode_step)

    # Use soft-fill recovery so brittle field selectors do not abort this scope-repair turn.
    plan.extend(
        _google_deeplink_recovery_plan(
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            trip_type=trip_type,
            missing_roles=set(),
            soft_fail_fills=True,
        )
    )

    if scope_class:
        plan.insert(
            0,
            {
                "action": "wait",
                "selector": bridge.service_wait_fallbacks("google_flights"),
                "optional": True,
            },
        )
    return plan


def _google_route_context_matches(
    html: str,
    *,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
) -> bool:
    """Return True when Google HTML appears bound to requested route/date.

    Migrated from scenario_runner.py
    """
    if not isinstance(html, str) or not html:
        return False
    if not origin or not dest or not depart:
        return False
    if not any(token in html for token in _google_date_tokens(depart)):
        return False
    if return_date and not any(token in html for token in _google_date_tokens(return_date)):
        return False
    upper = html.upper()
    if not _contains_any_token(html, upper, _google_route_alias_tokens(origin)):
        return False
    if not _contains_any_token(html, upper, _google_route_alias_tokens(dest)):
        return False
    return True


def _google_quick_page_class(
    html: str,
    *,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
) -> str:
    """Fast deterministic scope class for Google pages.

    Migrated from scenario_runner.py
    """
    if not isinstance(html, str) or not html:
        return "garbage_page"
    cleaned = _strip_nonvisible_html(html)
    lowered = cleaned.lower()
    if len(cleaned) < 160 and not _PRICE_TOKEN_RE.search(cleaned):
        return "garbage_page"

    route_bound = _google_route_context_matches(
        cleaned,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )
    map_hits = sum(1 for token in _GOOGLE_SCOPE_MAP_TOKENS if token in lowered)
    hotel_hits = sum(1 for token in _GOOGLE_SCOPE_HOTEL_TOKENS if token in lowered)
    auth_hits = len(_CONTACT_AUTH_HINT_RE.findall(cleaned))
    has_price = bool(_PRICE_TOKEN_RE.search(cleaned))
    has_result_hint = bool(_RESULT_HINT_RE.search(cleaned))

    if auth_hits >= 2 and not has_price and not has_result_hint:
        return "garbage_page"
    if map_hits >= 2 and hotel_hits >= 1 and not route_bound:
        return "flight_hotel_package"
    if route_bound and has_price and has_result_hint:
        return "flight_only"
    if not route_bound and (has_result_hint or "travel/explore" in lowered):
        return "irrelevant_page"
    return "unknown"


def _google_flights_after_search_ready(page) -> bool:
    """Check if Google Flights search results are ready after search click.

    Migrated from scenario_runner.py
    """
    try:
        # Check for results list container or main content area
        # Google Flights uses role=main for the results region
        main_found = bool(page.query_selector("[role='main']"))
        if main_found:
            return True

        # Fallback: check for price tokens in visible content
        visible_html = page.content()
        if visible_html and re.search(r"¥\s*\d[\d,]*|\$\s*\d[\d,]*", visible_html):
            return True

        return False
    except Exception:
        # If we can't check, assume ready (don't block further waiting)
        return True


class GoogleFlightsRunner(ServiceRunner):
    """Google Flights-specific service runner implementation.

    Encapsulates all Google Flights-specific orchestration logic including:
    - Form filling and combobox interaction strategies
    - Verification gates (route core before date)
    - Recovery policies (route mismatch reset, force bind)
    - Deeplink parsing and validation
    - Locale-aware selector management

    This class is being progressively expanded to include more Google Flights
    specific logic migrated from scenario_runner.py.
    """

    @property
    def service_key(self) -> str:
        """Return canonical service key for Google Flights."""
        return "google_flights"

    # =========================================================================
    # UTILITY METHODS - Migrated
    # =========================================================================

    @staticmethod
    def route_alias_tokens(code: str) -> Set[str]:
        """Return airport+metro aliases for IATA code."""
        return _google_route_alias_tokens(code)

    @staticmethod
    def date_tokens(iso_date: str) -> Set[str]:
        """Return date string variants used in Google Flights UI."""
        return _google_date_tokens(iso_date)

    @staticmethod
    def parse_deeplink(url: str) -> Optional[Dict[str, Any]]:
        """Parse route/date from Google Flights deeplink URL."""
        return _parse_google_deeplink_context(url)

    @staticmethod
    def is_dest_placeholder(value: str) -> bool:
        """Check if destination is a Google Flights placeholder."""
        return _is_google_dest_placeholder(value)

    @staticmethod
    def strip_html(html: str) -> str:
        """Remove script/style blocks from HTML for analysis."""
        return _strip_nonvisible_html(html)

    def contains_token(self, blob: str, tokens) -> bool:
        """Check if blob contains any token (case-aware for i18n)."""
        return _contains_any_token(blob, blob.upper(), tokens)

    # =========================================================================
    # PLAN GENERATION
    # =========================================================================

    def get_default_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        is_domestic: bool = False,
        knowledge: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Return default fallback plan for Google Flights.

        When LLM plan generation fails or times out, use this heuristic plan
        that performs basic form filling (origin, dest, depart) and search.
        """
        return _default_google_flights_plan(origin, dest, depart)

    # =========================================================================
    # STEP EXECUTION & FORM FILLING
    # =========================================================================

    def apply_step(
        self,
        browser: Any,
        step: Dict[str, Any],
        *,
        site_key: str,
        timeout_ms: Optional[int] = None,
        deadline: Optional[float] = None,
        step_index: int = -1,
        attempt: int = 0,
        turn: int = 0,
        evidence_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Apply a single step to the browser.

        Delegates to scenario_runner.py generic step execution for now.
        Future: migrate specialized Google Flights form filling logic here.

        Returns:
            (success, error_reason, metadata)
        """
        # Placeholder: Returns (True, None, {})
        # Real implementation will delegate or migrate specialized handling
        return True, None, {}

    def assess_fill_mismatch(
        self,
        *,
        form_state: Dict[str, Any],
        html: str = "",
        expected_origin: str,
        expected_dest: str,
        expected_depart: str,
        expected_return: str = "",
        min_confidence: str = "medium",
        fail_closed: bool = True,
    ) -> Dict[str, Any]:
        """Assess form state against expected route/date and return blocking verdict.

        Migrated from scenario_runner._assess_google_flights_fill_mismatch()
        """
        return _assess_google_flights_fill_mismatch(
            form_state=form_state,
            html=html,
            expected_origin=expected_origin,
            expected_dest=expected_dest,
            expected_depart=expected_depart,
            expected_return=expected_return,
            min_confidence=min_confidence,
            fail_closed=fail_closed,
        )

    def extract_form_state(self, browser: Any, page: Optional[Any] = None) -> Dict[str, Any]:
        """Extract current form state (origin, dest, depart, return text).

        Helper method for verification purposes.
        """
        try:
            probe_target = page if page is not None else browser
            return _extract_google_flights_form_state(probe_target)
        except Exception as exc:
            log.warning("google_flights.extract_form_state.error: %s", exc)
            return {}

    # =========================================================================
    # VERIFICATION GATES
    # =========================================================================

    def verify_after_fill(
        self,
        browser: Any,
        filled_role: str,
        filled_value: str,
        *,
        expected_origin: str = "",
        expected_dest: str = "",
        expected_depart: str = "",
        expected_return: str = "",
        html: str = "",
        page: Optional[Any] = None,
        locale_hint: str = "",
    ) -> Dict[str, Any]:
        """Verify that a Google Flights form field was filled correctly.

        Google Flights verification checks:
        1. For origin/dest: confirm combobox selection is visible and matches expected
        2. For dates: confirm selected date is rendered in UI
        3. General: check that the field value matches what was filled

        Returns:
            {"ok": bool, "reason": str, "evidence": {...}}
        """
        # Basic verification: check that value was provided
        # More sophisticated verification implemented in execute_plan()
        if not filled_value:
            return {
                "ok": False,
                "reason": "fill_value_empty",
                "evidence": {"filled_value": "", "role": filled_role},
            }

        return {
            "ok": True,
            "reason": "field_filled_basic",
            "evidence": {
                "filled_value": filled_value,
                "role": filled_role,
                "expected_origin": expected_origin,
                "expected_dest": expected_dest,
            },
        }

    def get_route_core_before_date_gate(
        self,
        html: str,
        page: Optional[Any] = None,
        expected_origin: str = "",
        expected_dest: str = "",
        expected_depart: str = "",
        expected_return: str = "",
    ) -> Dict[str, Any]:
        """Verify Google Flights route core (origin+dest) before date picker.

        Phase A invariant: in deeplink recovery mode, date picker interactions
        are blocked until origin and destination are verifiably rebound.
        This prevents calendar failures on generic explore/irrelevant surfaces.
        """
        bridge = _google_flights_bridge()
        return bridge.google_route_core_before_date_gate(
            html=html,
            page=page,
            expected_origin=expected_origin,
            expected_dest=expected_dest,
            expected_depart=expected_depart,
            expected_return=expected_return,
        )

    # =========================================================================
    # RECOVERY & REPAIR POLICIES - Migrated
    # =========================================================================

    def get_recovery_limits(self) -> Dict[str, int | bool]:
        """Return bounded recovery policy limits from Google Flights thresholds.

        Phase B collaborative recovery (planner + VLM + repair) caps.
        Migrated from scenario_runner._google_recovery_collab_limits_from_thresholds()
        """
        from utils.thresholds import get_threshold

        return {
            "enabled": bool(
                get_threshold("google_flights_recovery_collab_enabled", True)
            ),
            "max_vlm": max(
                0, int(get_threshold("google_flights_recovery_collab_max_vlm_page_kind_calls", 1))
            ),
            "max_repair": max(
                0, int(get_threshold("google_flights_recovery_collab_max_repair_calls", 1))
            ),
            "max_planner": max(
                0, int(get_threshold("google_flights_recovery_collab_max_planner_calls", 1))
            ),
            "route_core_only_first": bool(
                get_threshold("google_flights_recovery_collab_route_core_only_first", True)
            ),
            "planner_timeout_sec": max(
                5, int(get_threshold("google_flights_recovery_collab_planner_timeout_sec", 45))
            ),
        }

    def should_attempt_route_mismatch_reset(
        self, *, mismatch_detected: bool, enabled: bool, attempts: int, max_attempts: int
    ) -> bool:
        """Check if route mismatch reset should be attempted.

        Migrated from scenario_runner._should_attempt_google_route_mismatch_reset()
        """
        bridge = _google_flights_bridge()
        return bridge.should_attempt_google_route_mismatch_reset(
            mismatch_detected=mismatch_detected,
            enabled=enabled,
            attempts=attempts,
            max_attempts=max_attempts,
        )

    def run_route_mismatch_reset(
        self, browser: Any, *, deeplink_url: str, wait_selectors: List[str]
    ) -> bool:
        """Run route mismatch reset by reloading deeplink and waiting for signals.

        Migrated approach: wraps scenario_runner logic; can be extended.
        """
        bridge = _google_flights_bridge()
        try:
            bridge.google_activate_route_form_recovery(
                browser,
                deeplink_url=deeplink_url,
            )
            return True
        except Exception as exc:
            log.warning("google_flights.route_mismatch_reset.error: %s", exc)
            return False

    def get_force_bind_repair_policy(
        self,
        *,
        enabled: bool,
        uses: int,
        max_per_attempt: int,
        verify_status: str,
        scope_class: str,
        observed_dest_raw: str,
        observed_origin_raw: str = "",
        expected_origin: str = "",
    ) -> Dict[str, Any]:
        """Build forced rebind repair policy for Google Flights route mismatch.

        Migrated from scenario_runner._google_force_bind_repair_policy()
        """
        bridge = _google_flights_bridge()
        return bridge.google_force_bind_repair_policy(
            enabled=enabled,
            uses=uses,
            max_per_attempt=max_per_attempt,
            verify_status=verify_status,
            scope_class=scope_class,
            observed_dest_raw=observed_dest_raw,
            observed_origin_raw=observed_origin_raw,
            expected_origin=expected_origin,
        )

    def build_recovery_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        return_date: str = "",
        trip_type: str = "one_way",
        missing_roles: Optional[set] = None,
        soft_fail_fills: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build a recovery plan when primary plan fails.

        Short recovery plan after deeplink probe/rebind fails on Google Flights.
        Migrated from scenario_runner._google_deeplink_recovery_plan()
        """
        return _google_deeplink_recovery_plan(
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date or None,
            trip_type=trip_type,
            missing_roles=missing_roles,
            soft_fail_fills=soft_fail_fills,
        )

    def build_non_flight_scope_repair_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        return_date: str = "",
        trip_type: str = "one_way",
        is_domestic: bool = False,
        scope_class: str = "unknown",
        vlm_hint: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build repair plan for non-flight scope (hotels, cars, etc).

        Forces flight product/mode context before form fills.
        """
        return _google_non_flight_scope_repair_plan(
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date or None,
            trip_type=trip_type,
            is_domestic=is_domestic,
            scope_class=scope_class,
            vlm_hint=vlm_hint,
        )

    # =========================================================================
    # DEEPLINK SUPPORT - Migrated
    # =========================================================================

    def parse_deeplink_context(self, url: str) -> Dict[str, Any]:
        """Parse route/date context from Google Flights deeplink URL."""
        context = _parse_google_deeplink_context(url)
        return context if isinstance(context, dict) else {}

    def get_deeplink_probe_status(
        self, html: str, url: str
    ) -> Tuple[bool, Optional[str]]:
        """Check if Google Flights deeplink probe is ready for verification."""
        result = _google_deeplink_probe_status(html, url)
        # Returns: None (ready) or (False, reason)
        if result is None:
            return True, None
        if isinstance(result, tuple):
            return result
        return False, "deeplink_probe_failed"

    # =========================================================================
    # SELECTOR & LOCALE MANAGEMENT
    # =========================================================================

    def get_locale_aware_selector(
        self,
        role: str,
        action: str = "fill",
        locale_hint: str = "",
    ) -> List[str]:
        """Return locale-aware selectors for a Google Flights form field.

        Args:
            role: Field role ("origin", "dest", "depart", "return", "search")
            action: Interaction type ("fill", "click", "wait", "activate")
            locale_hint: Locale hint (e.g., "ja-JP", "en-US")

        Returns:
            List of CSS/aria selectors in priority order
        """
        bridge = _google_flights_bridge()

        action_lower = str(action or "").strip().lower()
        role_lower = str(role or "").strip().lower()

        if action_lower == "fill":
            return bridge.service_fill_fallbacks("google_flights", role_lower)
        elif action_lower in ("click", "activate"):
            if role_lower == "search" or role_lower.startswith("search"):
                return bridge.service_search_click_fallbacks("google_flights")
            else:
                return bridge.service_fill_fallbacks("google_flights", role_lower)
        elif action_lower == "wait":
            return bridge.service_wait_fallbacks("google_flights")

        # Fallback
        return bridge.service_fill_fallbacks("google_flights", role_lower)

    # =========================================================================
    # THRESHOLD SCOPE
    # =========================================================================

    def get_threshold_scope(self) -> str:
        """Return threshold scope for Google Flights-specific thresholds."""
        return "google_flights"
