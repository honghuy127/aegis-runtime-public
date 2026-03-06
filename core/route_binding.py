"""Deterministic Google Flights route-binding probes and fusion helpers."""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, Optional

from storage.shared_knowledge_store import get_airport_aliases_for_provider


def _contains_any_token(blob: str, tokens) -> bool:
    """Case-aware token containment with ASCII boundary matching."""
    text = str(blob or "")
    upper = text.upper()
    for token in tokens or []:
        raw = str(token or "").strip()
        if not raw:
            continue
        if raw.isascii():
            needle = raw.upper()
            if re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", upper):
                return True
            if len(needle) >= 5 and needle in upper:
                return True
        elif raw in text:
            return True
    return False


def _date_tokens(iso_date: str):
    """Return common localized date tokens for one YYYY-MM-DD date."""
    if not isinstance(iso_date, str):
        return set()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso_date.strip())
    if not m:
        return {iso_date.strip()} if iso_date.strip() else set()
    y = int(m.group(1))
    mo = int(m.group(2))
    day = int(m.group(3))
    return {
        f"{y}-{mo:02d}-{day:02d}",
        f"{y}年{mo}月{day}日",
        f"{mo}月{day}日",
        f"{mo}/{day}",
        f"{mo:02d}/{day:02d}",
    }


def _google_role_tokens() -> Dict[str, tuple]:
    """Role label keywords used by Google Flights-like form controls."""
    return {
        "origin": ("出発地", "出発空港", "from", "where from", "origin"),
        "dest": ("目的地", "到着地", "到着空港", "to", "where to", "destination"),
        "depart": ("出発日", "往路", "depart", "departure", "outbound"),
        "return": ("復路", "帰り", "帰国", "return", "inbound"),
    }


def _extract_candidates_from_html(html: str) -> list:
    """Extract compact candidate field tuples from raw HTML.

    For Google Flights, prioritizes input.value and aria-valuenow over aria-label
    to avoid capturing location labels (e.g., "東京都") instead of airport codes.
    Extracts text content between tags as fallback for date fields.
    """
    if not isinstance(html, str) or not html:
        return []
    cleaned = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html)
    tag_re = re.compile(r"<(?P<tag>input|button|div|span)[^>]*>", re.IGNORECASE)
    attr_re = re.compile(
        r'([a-zA-Z_:][a-zA-Z0-9_:\-]*)\s*=\s*("([^"]*)"|\'([^\']*)\')',
        re.IGNORECASE,
    )
    out = []
    for match in tag_re.finditer(cleaned):
        raw_tag = match.group(0)
        tag_name = match.group("tag").lower()
        attrs: Dict[str, str] = {}
        for attr in attr_re.finditer(raw_tag):
            key = str(attr.group(1) or "").strip().lower()
            value = attr.group(3)
            if value is None:
                value = attr.group(4)
            attrs[key] = html_lib.unescape(str(value or "")).strip()

        # Extract text content between tags (for date fields displayed as text)
        text_content = ""
        if tag_name != "input":  # input is self-closing
            tag_end_pos = match.end()
            # Look for closing tag (simplified: take text until next tag or closing tag)
            close_pattern = re.compile(rf"</{tag_name}>", re.IGNORECASE)
            close_match = close_pattern.search(cleaned, tag_end_pos)
            if close_match:
                between = cleaned[tag_end_pos:close_match.start()]
                # Remove nested tags and extract clean text
                text_only = re.sub(r"<[^>]+>", " ", between)
                text_content = html_lib.unescape(text_only).strip()
                # Limit to first 120 chars to avoid capturing large blocks
                if len(text_content) > 120:
                    text_content = ""

        # Prioritize actual input value over labels for Google Flights
        actual_value = str(attrs.get("value", "") or "").strip()
        aria_valuenow = str(attrs.get("aria-valuenow", "") or "").strip()

        # Build label from aria-label and other attributes, but deprioritize for combobox/input
        role = str(attrs.get("role", "") or "").strip().lower()
        input_type = str(attrs.get("type", "") or "").strip().lower()
        is_form_input = role in {"combobox", "textbox"} or input_type in {"text", "date"}

        # Use text content as fallback for value
        effective_value = aria_valuenow or actual_value or text_content

        if is_form_input and effective_value:
            # For form inputs with values, use the value directly as primary source
            primary_value = effective_value
            label = " ".join(
                value
                for value in (
                    attrs.get("aria-label"),
                    attrs.get("placeholder"),
                    attrs.get("name"),
                )
                if value
            ).strip()
            out.append(
                {
                    "label": label,
                    "value": primary_value,
                    "text": primary_value,
                    "tag": str(match.group("tag") or "").strip().lower(),
                    "role": role,
                    "aria_hidden": str(attrs.get("aria-hidden", "") or "").strip().lower() == "true",
                    "disabled": ("disabled" in raw_tag.lower()) or (
                        str(attrs.get("aria-disabled", "") or "").strip().lower() == "true"
                    ),
                    "input_like": True,
                }
            )
        else:
            # For non-inputs or inputs without value, use traditional label extraction
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
            value = effective_value
            if not label and not value:
                continue
            out.append(
                {
                    "label": label,
                    "value": value,
                    "text": label or value,
                    "tag": str(match.group("tag") or "").strip().lower(),
                    "role": role,
                    "aria_hidden": str(attrs.get("aria-hidden", "") or "").strip().lower() == "true",
                    "disabled": ("disabled" in raw_tag.lower()) or (
                        str(attrs.get("aria-disabled", "") or "").strip().lower() == "true"
                    ),
                    "input_like": bool(is_form_input),
                }
            )

        if len(out) >= 1200:
            break
    return out


def _pick_observed_value(candidates, role: str) -> str:
    """Pick best observed field value for one role from candidates."""
    tokens = tuple(token.lower() for token in _google_role_tokens().get(role, ()))
    if not tokens:
        return ""

    def _is_numeric_noise(text: str) -> bool:
        raw = str(text or "").strip()
        return bool(raw) and bool(re.fullmatch(r"\d{1,3}", raw))

    def _ascii_token_match(haystack: str, token: str) -> bool:
        parts = [re.escape(p) for p in token.split() if p]
        if not parts:
            return False
        pattern = r"(?<![a-z0-9])" + r"\s+".join(parts) + r"(?![a-z0-9])"
        return bool(re.search(pattern, haystack))

    def _token_match_weight(token: str) -> int:
        t = str(token or "").strip()
        if not t:
            return 0
        if t.isascii():
            compact = t.replace(" ", "")
            word_count = len([p for p in t.split() if p])
            if word_count >= 2:
                return 5
            if len(compact) <= 2:
                return -2
            if len(compact) <= 4:
                return 1
            return 3
        if len(t) <= 2:
            return 2
        return 4

    def _normalized_token_key(text: str) -> str:
        raw = str(text or "").strip().lower()
        if not raw:
            return ""
        if raw.isascii():
            return re.sub(r"[^a-z0-9]+", " ", raw).strip()
        return re.sub(r"\s+", "", raw)

    role_token_keys = {
        key
        for key in (_normalized_token_key(token) for token in tokens)
        if key
    }

    def _label_score(label_lower: str) -> int:
        score = 0
        for token in tokens:
            tok = str(token or "").strip().lower()
            if not tok:
                continue
            matched = _ascii_token_match(label_lower, tok) if tok.isascii() else (tok in label_lower)
            if matched:
                score += _token_match_weight(tok)
        return score

    def _candidate_payload_score(candidate_text: str) -> int:
        score = 0
        candidate = str(candidate_text or "").strip()
        lowered = candidate.lower()
        norm_key = _normalized_token_key(lowered)
        if role in {"origin", "dest"} and norm_key and norm_key in role_token_keys:
            # "Where to?" / "Where from?" placeholders are role labels, not route values.
            score -= 24
        if role in {"origin", "dest"} and re.search(r"(?<![A-Z0-9])[A-Z]{3}(?![A-Z0-9])", candidate.upper()):
            score += 10
        if role in {"origin", "dest"} and len(candidate) >= 6:
            score += 1
        if role in {"origin", "dest"} and len(candidate.split()) >= 2:
            score += 1
        if role in {"depart", "return"} and re.search(r"\d{4}-\d{2}-\d{2}", candidate):
            score += 6
        return score

    def _looks_instructional_control_text(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        lowered = raw.lower()
        # General UI-control and helper/action text markers; used as penalty only.
        control_tokens = (
            "swap",
            "reset",
            "search",
            "done",
            "previous",
            "next",
            "select ",
            "press ",
            "switch ",
            "mode",
        )
        return any(tok in lowered for tok in control_tokens)

    def _looks_repeated_label_noise(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if re.search(r"\d", raw):
            return False
        parts = [p for p in re.split(r"\s+", raw) if p]
        if len(parts) == 2 and parts[0].lower() == parts[1].lower():
            return True
        # "Departure Departure", "Return Return" and similar duplicated labels.
        compact = re.sub(r"[^a-zA-Z]+", " ", raw).strip().lower()
        parts2 = [p for p in compact.split() if p]
        return len(parts2) == 2 and parts2[0] == parts2[1]

    best_value = ""
    best_score = -10_000
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "") or "")
        label_lower = label.lower()
        if not any(token in label_lower for token in tokens):
            continue
        value = str(item.get("value", "") or "").strip()
        text = str(item.get("text", "") or "").strip()
        candidate = value or text
        if not candidate:
            continue
        item_tag = str(item.get("tag", "") or "").strip().lower()
        item_role = str(item.get("role", "") or "").strip().lower()
        item_input_like = bool(item.get("input_like"))
        item_aria_hidden = bool(item.get("aria_hidden"))
        item_disabled = bool(item.get("disabled"))
        score = _label_score(label_lower)
        if value:
            score += 2
        if item_input_like or item_role in {"combobox", "textbox"} or item_tag == "input":
            score += 6
        elif item_tag in {"button"} or item_role == "button":
            score -= 4
        if item_aria_hidden:
            score -= 6
        if item_disabled:
            score -= 4
        if _is_numeric_noise(candidate):
            if role in {"origin", "dest"}:
                score -= 100
            else:
                score -= 5
        if _looks_repeated_label_noise(candidate):
            score -= 12
        if _looks_instructional_control_text(candidate):
            if role in {"origin", "dest"}:
                score -= 24
            else:
                score -= 12
        score += _candidate_payload_score(candidate)
        if len(candidate) >= 3:
            score += 1
        if len(candidate) >= 5:
            score += 1
        if score > best_score:
            best_score = score
            best_value = candidate[:140]
    if role in {"origin", "dest"} and _is_numeric_noise(best_value):
        return ""
    return best_value


def _airport_match(observed: str, expected_code: str) -> Optional[bool]:
    """Return airport-match verdict for one observed/expected pair."""
    obs = str(observed or "").strip()
    exp = str(expected_code or "").strip().upper()
    if not exp:
        return True
    if not obs:
        return None
    aliases = get_airport_aliases_for_provider(exp, "google_flights")
    if not aliases:
        aliases = {exp}
    return _contains_any_token(obs, aliases)


def _date_match(observed: str, expected_date: str) -> Optional[bool]:
    """Return date-match verdict for one observed/expected pair."""
    obs = str(observed or "").strip()
    exp = str(expected_date or "").strip()
    if not exp:
        return True
    if not obs:
        return None
    return any(token in obs for token in _date_tokens(exp))


def dom_route_bind_probe(
    html: str,
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
) -> Dict[str, Any]:
    """Return deterministic DOM route-binding support probe for Google Flights."""
    if not (str(origin or "").strip() and str(dest or "").strip() and str(depart or "").strip()):
        return {
            "route_bound": False,
            "support": "none",
            "source": "unknown",
            "reason": "missing_expected_context",
            "observed": {"origin": "", "dest": "", "depart": "", "return": ""},
            "mismatch_fields": [],
        }
    candidates = _extract_candidates_from_html(html)
    observed = {
        "origin": _pick_observed_value(candidates, "origin"),
        "dest": _pick_observed_value(candidates, "dest"),
        "depart": _pick_observed_value(candidates, "depart"),
        "return": _pick_observed_value(candidates, "return"),
    }

    # Debug log: show extracted raw values with confidence indicator
    log = logging.getLogger(__name__)
    log.debug(
        "route_bind.dom_probe observed_origin=%s observed_dest=%s observed_depart=%s observed_return=%s candidates_count=%d",
        observed["origin"][:40] if observed["origin"] else "",
        observed["dest"][:40] if observed["dest"] else "",
        observed["depart"][:40] if observed["depart"] else "",
        observed["return"][:40] if observed["return"] else "",
        len(candidates),
    )

    # Special case: dest uncommitted (origin populated but dest empty)
    if observed["origin"] and not observed["dest"]:
        return {
            "route_bound": False,
            "support": "none",
            "source": "dom",
            "reason": "dest_uncommitted",
            "observed": observed,
            "mismatch_fields": ["dest"],
        }

    checks = {
        "origin": _airport_match(observed["origin"], origin),
        "dest": _airport_match(observed["dest"], dest),
        "depart": _date_match(observed["depart"], depart),
    }
    if str(return_date or "").strip():
        checks["return"] = _date_match(observed["return"], return_date)

    mismatch_fields = [name for name, verdict in checks.items() if verdict is False]
    matched = [name for name, verdict in checks.items() if verdict is True]
    unknown = [name for name, verdict in checks.items() if verdict is None]
    if mismatch_fields:
        support = "none"
        reason = "dom_explicit_mismatch"
    elif checks and len(matched) == len(checks):
        support = "strong"
        reason = "dom_all_required_matched"
    elif matched or unknown:
        support = "weak"
        reason = "dom_partial_or_unknown"
    else:
        required_flags = {
            "origin": _contains_any_token(
                str(html or ""),
                get_airport_aliases_for_provider(origin or "", "google_flights"),
            ),
            "dest": _contains_any_token(
                str(html or ""),
                get_airport_aliases_for_provider(dest or "", "google_flights"),
            ),
            "depart": any(token in str(html or "") for token in _date_tokens(depart or "")),
        }
        if str(return_date or "").strip():
            required_flags["return"] = any(
                token in str(html or "") for token in _date_tokens(return_date or "")
            )
        present = [name for name, flag in required_flags.items() if bool(flag)]
        if required_flags and len(present) == len(required_flags):
            support = "strong"
            reason = "dom_context_tokens_matched"
        elif present:
            support = "weak"
            reason = "dom_context_tokens_partial"
        else:
            support = "none"
            reason = "dom_no_evidence"
    return {
        "route_bound": support == "strong",
        "support": support,
        "source": "dom",
        "reason": reason,
        "observed": observed,
        "mismatch_fields": mismatch_fields,
    }


def vlm_route_bind_probe(
    verify: Any,
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
) -> Dict[str, Any]:
    """Convert VLM fill-verification payload into route-binding support probe."""
    required = {"origin", "dest", "depart"}
    if str(return_date or "").strip():
        required.add("return")
    observed = {"origin": None, "dest": None, "depart": None, "return": None}
    if not isinstance(verify, dict) or not verify:
        return {
            "route_bound": False,
            "support": "none",
            "source": "unknown",
            "reason": "vlm_unavailable",
            "observed": observed,
            "mismatch_fields": [],
        }
    fields = verify.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    matched_count = 0
    mismatch_fields = []
    for role in required:
        info = fields.get(role)
        if not isinstance(info, dict):
            continue
        observed[role] = str(info.get("observed") or "").strip() or None
        if info.get("matched") is True:
            matched_count += 1
        elif info.get("matched") is False:
            mismatch_fields.append(role)
    if matched_count >= len(required) and required:
        support = "strong"
        reason = "vlm_all_required_matched"
    elif matched_count > 0:
        support = "weak"
        reason = "vlm_partial_match"
    else:
        support = "none"
        reason = "vlm_no_match"
    return {
        "route_bound": support == "strong",
        "support": support,
        "source": "vlm",
        "reason": reason,
        "observed": observed,
        "mismatch_fields": mismatch_fields,
    }


def fuse_route_bind_verdict(
    *,
    dom_probe: Optional[Dict[str, Any]],
    vlm_probe: Optional[Dict[str, Any]],
    require_strong: bool = True,
    fail_closed_on_mismatch: bool = True,
) -> Dict[str, Any]:
    """Fuse DOM and VLM probes into one route-binding verdict."""
    dom = dict(dom_probe or {})
    vlm = dict(vlm_probe or {})
    dom_support = str(dom.get("support", "none") or "none").strip().lower()
    vlm_support = str(vlm.get("support", "none") or "none").strip().lower()
    dom_mismatch = list(dom.get("mismatch_fields", []) or [])
    vlm_mismatch = list(vlm.get("mismatch_fields", []) or [])
    mismatch_fields = []
    for field in dom_mismatch + vlm_mismatch:
        if field not in mismatch_fields:
            mismatch_fields.append(field)

    if fail_closed_on_mismatch and dom_mismatch:
        support = "none"
        route_bound = False
        reason = "explicit_mismatch"
    elif dom_support == "strong" or vlm_support == "strong":
        support = "strong"
        route_bound = True
        reason = "strong_evidence"
    elif dom_support == "weak" or vlm_support == "weak":
        support = "weak"
        route_bound = not require_strong
        reason = "weak_evidence"
    else:
        support = "none"
        route_bound = False
        reason = "no_evidence"

    if dom_support != "none" and vlm_support != "none":
        source = "mixed"
    elif dom_support != "none":
        source = "dom"
    elif vlm_support != "none":
        source = "vlm"
    else:
        source = "unknown"

    observed = {"origin": None, "dest": None, "depart": None, "return": None}
    for role in observed:
        dom_value = (dom.get("observed") or {}).get(role) if isinstance(dom.get("observed"), dict) else None
        vlm_value = (vlm.get("observed") or {}).get(role) if isinstance(vlm.get("observed"), dict) else None
        observed[role] = dom_value or vlm_value or None

    return {
        "route_bound": bool(route_bound),
        "support": support,
        "source": source,
        "reason": reason,
        "observed": observed,
        "mismatch_fields": mismatch_fields,
    }


def classify_google_deeplink_page_state_recovery_reason(reason: str) -> Dict[str, Any]:
    """Classify deeplink probe/rebind reason for bounded page-state recovery.

    Returns a normalized decision payload so callers avoid duplicating string
    parsing for `non_flight_scope_*` reasons.
    """
    value = str(reason or "").strip().lower()
    canonical = ""
    if value == "non_flight_scope_irrelevant_page":
        canonical = "non_flight_scope_irrelevant_page"
    elif value.startswith("rebind_unready_non_flight_scope_irrelevant_page"):
        canonical = "non_flight_scope_irrelevant_page"

    if canonical:
        return {
            "eligible": True,
            "canonical_reason": canonical,
            "scope_class": "irrelevant_page",
            "reason": value,
        }

    scope_class = ""
    if "non_flight_scope_" in value:
        scope_class = value.split("non_flight_scope_", 1)[-1]
    return {
        "eligible": False,
        "canonical_reason": "",
        "scope_class": scope_class or "unknown",
        "reason": value,
    }
