"""Google Flights core functions - form state, selectors, verification, planning."""

import copy
import re
import html as html_lib
from typing import Any, Dict, List, Optional, Set, Tuple

from core.route_binding import classify_google_deeplink_page_state_recovery_reason
from core.run_input_config import load_run_input_config
from core.service_ui_profiles import get_service_ui_profile, profile_role_token_list
from core.ui_tokens import build_button_text_selectors, prioritize_tokens
from llm.thresholds_helpers import get_threshold
from utils.knowledge_rules import get_knowledge_rule_tokens, get_tokens

from core.scenario_runner.google_flights.service_runner_bridge import (
    _contains_any_token,
    _strip_nonvisible_html,
    _google_route_alias_tokens,
    _google_date_tokens,
    _google_default_date_reference_year,
    _google_has_iata_token,
    _normalize_google_form_date_text,
    _google_form_text_looks_date_like,
    _google_form_text_looks_instructional_noise,
    _google_role_i18n_token_bank,
    _google_role_tokens,
    _google_display_locale_hint_from_url,
    _env_bool,
    _env_int,
    _dedupe_selectors,
    _selector_candidates,
)

_PRICE_TOKEN_RE = re.compile(
    r"(?:¥\s*\d[\d,]*|\$\s*\d[\d,]*|€\s*\d[\d,]*|£\s*\d[\d,]*|"
    r"JPY\s*\d[\d,]*|USD\s*\d[\d,]*|EUR\s*\d[\d,]*|GBP\s*\d[\d,]*)",
    re.IGNORECASE,
)


# ========== FORM STATE & VALIDATION ==========

def _is_google_dest_placeholder(value: str) -> bool:
    """Return True when destination text is placeholder/explore copy."""
    from core.ui_tokens import is_placeholder, normalize_visible_text
    from core.scenario_runner import _current_mimic_locale

    raw = str(value or "").strip()
    if not raw:
        return True
    tokens = get_tokens("placeholders", "dest")
    tokens = list(tokens) + _google_role_tokens("dest", "selector_ja") + _google_role_tokens("dest", "selector_en")
    if not tokens:
        return False
    normalized = normalize_visible_text(raw)
    prioritized = prioritize_tokens(tokens, locale_hint=_current_mimic_locale())
    normalized_tokens = [normalize_visible_text(str(token or "")) for token in prioritized if str(token or "").strip()]

    if normalized not in normalized_tokens:
        raw_lower = raw.lower()
        if ("空港" in raw and len(raw) >= 5) or ("airport" in raw_lower and len(raw_lower) >= 8):
            strong_placeholder_markers = ("探索", "where to", "explore", "search")
            if not any(marker in normalized for marker in strong_placeholder_markers):
                return False

    return is_placeholder(normalized, prioritized)


def _google_form_value_matches_airport(value: str, expected_code: str) -> bool:
    """Return True when observed value includes expected airport alias token."""
    observed = str(value or "").strip()
    expected = str(expected_code or "").strip().upper()
    if not observed or not expected:
        return True
    if _is_google_dest_placeholder(observed):
        return False
    aliases = _google_route_alias_tokens(expected)
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


def _google_origin_needs_iata_support(value: str) -> bool:
    """Return True for generic origin labels that are too weak by themselves."""
    from core.ui_tokens import normalize_visible_text

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

    raw = str(value or "").strip()
    if not raw:
        return False
    if _google_has_iata_token(raw):
        return False
    expected = str(expected_origin or "").strip().upper()
    if expected and _google_form_value_matches_airport(raw, expected):
        return False

    region_tokens = prioritize_tokens(
        get_knowledge_rule_tokens("region_like_origin_tokens"),
        locale_hint="",
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
        out[role] = tuple(_dedupe_selectors(ja_tokens + en_tokens + extras))
    return out


def _google_form_candidates_from_html(html: str) -> list:
    """Extract `(label,value,text)` candidates from one HTML snapshot."""
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
    from core.scenario_runner.google_flights.service_runner_bridge import (
        _parse_google_deeplink_context,
    )

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
        _verification_confidence_rank_internal(origin_confidence),
        _verification_confidence_rank_internal(dest_confidence),
        _verification_confidence_rank_internal(depart_confidence),
    ) >= _verification_confidence_rank_internal("medium"):
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


def _assessment_form_score(item: dict, role: str) -> int:
    """Score form field candidate for relevance to role."""
    score = 0
    if item.get("value"):
        score += 28
    else:
        score -= 4
    if item.get("input_like"):
        score += 18
    if item.get("role") == "combobox":
        score += 8
    elif "combobox" in (item.get("role") or ""):
        score += 4
    if item.get("tag") == "input":
        score += 8
    elif item.get("tag") in {"button", "div", "span"}:
        score -= 1
    if item.get("aria_hidden") == "true":
        score -= 8
    if item.get("disabled"):
        score -= 6
    return score


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

    default_state["current_url"] = current_url
    return default_state


def _allow_bare_text_fallback() -> bool:
    """Check if bare text fallback selection is enabled."""
    return _env_bool("FLIGHT_WATCHER_ALLOW_BARE_TEXT_FALLBACK", False)


def _profile_localized_list(section: dict, *, prefer_ja: bool) -> list:
    """Extract localized list from profile section preferring JA or EN.

    Interleaves locales so bounded truncation (e.g. [:3]) preserves cross-locale fallbacks.
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
        raw_secondary = section.get("ja", default_bucket) if prefer_ja else []

    def _clean(values) -> list:
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
    """Extract role-specific list from profile section."""
    section = (profile or {}).get(key, {})
    role_key = str(role or "").strip().lower()
    role_section = section.get(role_key, {})
    return _profile_localized_list(role_section, prefer_ja=prefer_ja)


def _verification_confidence_rank_internal(label: str) -> int:
    """Rank confidence label for comparison."""
    rank_map = {"low": 1, "medium": 2, "high": 3}
    return rank_map.get(str(label or "").lower(), 0)


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

    # Results match check (simplified)
    results_itinerary_match = bool(
        expected_origin
        and expected_dest
        and any(token in html for token in [expected_origin, expected_dest])
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
            "results_itinerary_match": results_itinerary_match,
        }

    if (
        results_itinerary_match
        and _verification_confidence_rank_internal(confidence) < _verification_confidence_rank_internal(min_confidence)
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
            "results_itinerary_match": True,
        }

    if _verification_confidence_rank_internal(confidence) < _verification_confidence_rank_internal(min_confidence):
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
        "results_itinerary_match": results_itinerary_match,
    }
