"""Plan hygiene and validation helpers."""

import copy
import re
from typing import Any, Dict, List, Optional

from core.scenario_runner.selectors import (
    _contains_selector_word,
    _selector_blob,
    _service_fill_fallbacks,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_role_tokens,
    _selector_candidates,
)
from utils.logging import get_logger

log = get_logger(__name__)

# Regex patterns for value validation
_DATE_VALUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IATA_VALUE_RE = re.compile(r"^[A-Za-z]{3}$")


def _compile_token_regex(
    tokens: list[str],
    *,
    escape_literals: bool = True,
) -> re.Pattern:
    """Compile one broad case-insensitive token regex from token list."""
    # Import locally to avoid circular dependency
    from core.ui_tokens import normalize_visible_text

    patterns: list[str] = []
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        patterns.append(re.escape(value) if escape_literals else value)
    if not patterns:
        return re.compile(r"$^")
    return re.compile("(?:%s)" % "|".join(patterns), re.IGNORECASE)


def _load_rule_tokens(
    *,
    group: str = "",
    key: str = "",
    legacy_key: str = "",
    fallback: tuple[str, ...] = (),
) -> list[str]:
    """Load tokens from knowledge rules while preserving deterministic fallbacks."""
    from core.ui_tokens import normalize_visible_text
    from utils.knowledge_rules import get_tokens, get_knowledge_rule_tokens

    merged: list[str] = []
    if group and key:
        merged.extend(get_tokens(group, key))
    if legacy_key:
        merged.extend(get_knowledge_rule_tokens(legacy_key))
    merged.extend(fallback)

    out: list[str] = []
    seen = set()
    for token in merged:
        value = str(token or "").strip()
        if not value:
            continue
        marker = normalize_visible_text(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


# Pattern matching tokens for route field detection
_ROUTE_FIELD_HINT_RE = _compile_token_regex(
    _load_rule_tokens(
        group="hints",
        key="route_fields",
        fallback=(
            "where from",
            "where to",
            "origin",
            "destination",
            "from",
            "to",
            "depart",
            "departure",
            "return",
            "出発地",
            "出発空港",
            "出発日",
            "目的地",
            "到着地",
            "到着空港",
            "復路",
            "帰り",
            "帰路",
        ),
    )
)

# Pattern matching tokens for auth/contact field detection
_CONTACT_AUTH_HINT_RE = _compile_token_regex(
    _load_rule_tokens(
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
)


def _is_valid_plan(plan) -> bool:
    """Return True when plan is a non-empty list of supported action steps."""
    if not isinstance(plan, list) or not plan:
        return False

    allowed_actions = {"fill", "click", "wait", "wait_msec"}
    for step in plan:
        if not isinstance(step, dict):
            return False
        action = step.get("action")
        selector = step.get("selector")
        if action not in allowed_actions:
            return False
        if action == "wait_msec":
            # wait_msec actions don't require selector (they use duration_ms instead)
            if not isinstance(step.get("duration_ms"), int):
                return False
        else:
            # fill, click, and wait all require selector
            if isinstance(selector, str):
                if not selector:
                    return False
            elif isinstance(selector, list):
                if not selector or not all(isinstance(s, str) and s for s in selector):
                    return False
            else:
                return False
        if action == "fill":
            value = step.get("value")
            if not isinstance(value, str) or not value:
                return False
    return True


def _is_irrelevant_contact_fill_step(step) -> bool:
    """Return True for fill steps that look like auth/contact/profile forms."""
    if not isinstance(step, dict) or step.get("action") != "fill":
        return False
    selector_blob = _selector_blob(step.get("selector"))
    if not selector_blob:
        return False
    if _ROUTE_FIELD_HINT_RE.search(selector_blob):
        return False
    return bool(_CONTACT_AUTH_HINT_RE.search(selector_blob))


def _infer_fill_role(step):
    """Infer which flight field a fill step targets based on selectors."""
    if not isinstance(step, dict) or step.get("action") != "fill":
        return None
    explicit_role = str(step.get("role", "") or "").strip().lower()
    if explicit_role in {"origin", "dest", "depart", "return"}:
        return explicit_role

    selector_blob = _selector_blob(step.get("selector"))
    if not selector_blob:
        return None
    step_value = step.get("value")
    value_is_iso_date = isinstance(step_value, str) and bool(_DATE_VALUE_RE.match(step_value.strip()))
    google_origin_markers = tuple(token.lower() for token in _google_role_tokens("origin", "selector_en")) + tuple(
        token.lower() for token in _google_role_tokens("origin", "selector_ja")
    )
    google_dest_markers = tuple(token.lower() for token in _google_role_tokens("dest", "selector_en")) + tuple(
        token.lower() for token in _google_role_tokens("dest", "selector_ja")
    )
    google_depart_markers = tuple(token.lower() for token in _google_role_tokens("depart", "selector_en")) + tuple(
        token.lower() for token in _google_role_tokens("depart", "selector_ja")
    )
    google_return_markers = tuple(token.lower() for token in _google_role_tokens("return", "selector_en")) + tuple(
        token.lower() for token in _google_role_tokens("return", "selector_ja")
    ) + ("帰国",)

    def _marker_match(marker: str) -> bool:
        if not isinstance(marker, str) or not marker:
            return False
        if any(ord(ch) > 127 for ch in marker) or " " in marker:
            return marker in selector_blob
        return _contains_selector_word(selector_blob, marker)

    if any(_marker_match(marker) for marker in google_return_markers):
        return "return"
    # Date semantics must take precedence over airport-code hints because selector
    # lists can legitimately contain mixed legacy tokens (e.g. "Departure airport"
    # and "Departure date") after plan enrichment/profile merging.
    if _selector_expects_date(selector_blob):
        if any(_marker_match(marker) for marker in google_depart_markers):
            return "depart"
        if any(_marker_match(marker) for marker in google_return_markers):
            return "return"
    if _selector_expects_airport_code(selector_blob):
        if (
            "arrival" in selector_blob
            or "destination" in selector_blob
            or _contains_selector_word(selector_blob, "to")
        ):
            return "dest"
        return "origin"
    if (
        any(_marker_match(marker) for marker in google_depart_markers)
    ):
        return "depart"
    # Compatibility fallback for generic cached/generated selectors such as
    # "input[aria-label*='Departure']". Only apply when the value is an ISO date
    # and the selector does not look like an airport-code field.
    if value_is_iso_date and not _selector_expects_airport_code(selector_blob):
        if _contains_selector_word(selector_blob, "return") or _contains_selector_word(selector_blob, "inbound"):
            return "return"
        if _contains_selector_word(selector_blob, "depart") or _contains_selector_word(selector_blob, "departure"):
            return "depart"
    if (
        "where from" in selector_blob
        or any(marker in selector_blob for marker in google_origin_markers if " " in marker or any(ord(c) > 127 for c in marker))
        or _contains_selector_word(selector_blob, "from")
        or "origin" in selector_blob
    ):
        return "origin"
    if (
        "where to" in selector_blob
        or any(marker in selector_blob for marker in google_dest_markers if " " in marker or any(ord(c) > 127 for c in marker))
        or _contains_selector_word(selector_blob, "to")
        or "destination" in selector_blob
        or "dest" in selector_blob
    ):
        return "dest"
    return None


def _compatible_for_role_impl(selector: str, role: str) -> bool:
    """Return True when selector is compatible with the given fill role.

    This is the extracted implementation of the nested helper previously defined
    inside `_maybe_prioritize_fill_steps_from_knowledge` in
    `core/scenario_runner.py`. It intentionally takes `role` as an explicit
    parameter so it can be invoked from other modules without capturing
    closure variables.
    """
    if not isinstance(selector, str) or not selector.strip():
        return False
    inferred = _infer_fill_role({"action": "fill", "selector": selector})
    # Keep unknown selectors (generic but potentially valid), reject explicit
    # cross-role contamination.
    return inferred in {None, role}


def _plan_has_required_fill_roles(plan, trip_type: str, site_key: str = "") -> bool:
    """Ensure plan contains core route/date fill steps (origin, dest, depart)."""
    if not isinstance(plan, list):
        return False
    seen = set()
    nonoptional_roles = set()
    for step in plan:
        if not isinstance(step, dict):
            continue
        if step.get("action") != "fill":
            continue
        if _is_irrelevant_contact_fill_step(step):
            continue
        role = _infer_fill_role(step)
        if role in {"origin", "dest", "depart", "return"}:
            seen.add(role)
            if (not bool(step.get("optional"))) or bool(
                step.get("required_for_actionability")
            ):
                nonoptional_roles.add(role)

    required = {"origin", "dest", "depart"}
    if not required.issubset(seen):
        return False
    if (site_key or "").strip().lower() == "google_flights":
        # Route/date input steps must be mandatory for Google Flights.
        # If a role appears both optional+mandatory, accept it (mandatory exists).
        if not required.issubset(nonoptional_roles):
            return False
    if trip_type == "round_trip" and "return" not in seen:
        # Return field is often dynamic/optional; do not hard-require it.
        return True
    return True


def _is_actionable_plan(plan, trip_type: str, site_key: str = "") -> bool:
    """Return True when plan is syntactically valid and covers core fill roles."""
    return _is_valid_plan(plan) and _plan_has_required_fill_roles(
        plan,
        trip_type,
        site_key=site_key,
    )


def _annotate_fill_roles(plan):
    """Freeze inferred fill roles before selector enrichment mutates selector ordering."""
    if not isinstance(plan, list):
        return plan
    out = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            out.append(step)
            continue
        if str(step.get("role", "") or "").strip().lower() in {"origin", "dest", "depart", "return"}:
            out.append(step)
            continue
        role = _infer_fill_role(step)
        if role not in {"origin", "dest", "depart", "return"}:
            out.append(step)
            continue
        new_step = dict(step)
        new_step["role"] = role
        out.append(new_step)
    return out


def _return_date_step(value: str, site_key: str = None):
    """Construct a conservative fill step for the return-date field."""
    selectors = _service_fill_fallbacks(site_key or "", "return")
    if not selectors:
        selectors = [
            "input[aria-label*='Return']",
            "input[placeholder*='Return']",
            "input[aria-label*='return']",
        ]
    return {
        "action": "fill",
        "selector": selectors,
        "value": value,
        # Synthetic fallback step: do not kill the full run if not present on this site.
        "optional": True,
    }


def _retarget_plan_inputs(
    plan,
    origin,
    dest,
    depart,
    return_date,
    trip_type,
    site_key: str = None,
):
    """Rebind stored/generated plan values to current user trip inputs."""
    if not isinstance(plan, list):
        return plan

    rebound = []
    seen_return_step = False
    depart_index = None
    canonical_values = {origin, dest, depart}
    if return_date:
        canonical_values.add(return_date)

    for idx, step in enumerate(plan):
        new_step = copy.deepcopy(step)
        role = _infer_fill_role(new_step)

        if new_step.get("action") == "fill":
            if _is_irrelevant_contact_fill_step(new_step):
                continue
            if role == "origin":
                new_step["value"] = origin
            elif role == "dest":
                new_step["value"] = dest
            elif role == "depart":
                new_step["value"] = depart
                depart_index = idx
            elif role == "return":
                seen_return_step = True
                if trip_type != "round_trip" or not return_date:
                    continue
                new_step["value"] = return_date
            else:
                value = new_step.get("value")
                if not isinstance(value, str):
                    continue
                if value.strip() not in canonical_values:
                    continue

        rebound.append(new_step)

    if trip_type == "round_trip" and return_date and not seen_return_step:
        insert_at = len(rebound)
        if depart_index is not None:
            insert_at = min(depart_index + 1, len(rebound))
        rebound.insert(insert_at, _return_date_step(return_date, site_key=site_key))

    return rebound


def _reconcile_fill_plan_roles_and_values(
    plan,
    *,
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
):
    """Repair fill-step selector/value role mismatches after knowledge/hardening.

    Some follow-up plans acquire mixed selector lists during rewind/knowledge enrichment.
    If a step value is rebound to one canonical field but selector semantics drift toward
    another field, execution can run `role=origin` with `value=ITM`, etc. This pass
    rewrites the selector set to match the canonical value-role before execution.
    """
    if not isinstance(plan, list):
        return plan
    canonical_by_role = {
        "origin": str(origin or ""),
        "dest": str(dest or ""),
        "depart": str(depart or ""),
        "return": str(return_date or ""),
    }
    role_by_value = {
        str(v): r
        for r, v in canonical_by_role.items()
        if isinstance(v, str) and v.strip() and (r != "return" or trip_type == "round_trip")
    }
    out = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            out.append(step)
            continue
        new_step = copy.deepcopy(step)
        inferred_role = _infer_fill_role(new_step)
        value_text = str(new_step.get("value", "") or "").strip()
        value_role = role_by_value.get(value_text)
        if inferred_role and value_role and inferred_role != value_role:
            fallback_selectors = _service_fill_fallbacks(site_key or "", value_role)
            if fallback_selectors:
                new_step["selector"] = (
                    fallback_selectors if len(fallback_selectors) > 1 else fallback_selectors[0]
                )
            new_step["value"] = canonical_by_role.get(value_role, value_text)
            log.warning(
                "scenario.plan.fill_role_value_reconciled site=%s inferred_role=%s value_role=%s value=%s",
                site_key,
                inferred_role,
                value_role,
                value_text[:40],
            )
        out.append(new_step)
    return out


def _is_irrelevant_contact_fill_step(step) -> bool:
    """Return True for fill steps that look like auth/contact/profile forms."""
    if not isinstance(step, dict) or step.get("action") != "fill":
        return False
    selector_blob = _selector_blob(step.get("selector"))
    if not selector_blob:
        return False
    if _ROUTE_FIELD_HINT_RE.search(selector_blob):
        return False
    return bool(_CONTACT_AUTH_HINT_RE.search(selector_blob))


def _plan_auth_profile_fill_selectors(plan):
    """Return fill selectors that look like auth/profile/contact fields."""
    if not isinstance(plan, list):
        return []
    suspicious = []
    for step in plan:
        if not _is_irrelevant_contact_fill_step(step):
            continue
        for selector in _selector_candidates(step.get("selector")):
            if selector not in suspicious:
                suspicious.append(selector)
    return suspicious


def _selector_expects_airport_code(selector_blob: str) -> bool:
    """Return True when selector likely targets airport/IATA/code fields."""
    tokens = (
        "airportcode",
        "iata",
        "origincode",
        "destinationcode",
        "departureairport",
        "arrivalairport",
    )
    return any(token in selector_blob for token in tokens)


def _selector_expects_date(selector_blob: str) -> bool:
    """Return True when selector likely targets departure/return date fields."""
    tokens = (
        "departdate",
        "departuredate",
        "returndate",
        "return date",
        "departure date",
        "出発日",
        "復路",
        "帰り",
        "帰路",
    )
    if any(token in selector_blob for token in tokens):
        return True
    # "departure"/"return" with explicit date-ish context.
    return ("departure" in selector_blob or "return" in selector_blob) and ("day" in selector_blob)


def _plan_semantic_fill_mismatches(plan):
    """Return fill steps where selector semantics conflict with value type."""
    mismatches = []
    if not isinstance(plan, list):
        return mismatches
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            continue
        value = step.get("value")
        if not isinstance(value, str):
            continue
        selector_blob = _selector_blob(step.get("selector"))
        if not selector_blob:
            continue
        if _DATE_VALUE_RE.match(value.strip()) and _selector_expects_airport_code(selector_blob):
            mismatches.append(
                {
                    "selector": _selector_candidates(step.get("selector")),
                    "value": value,
                    "reason": "date_into_airport_code_field",
                }
            )
            continue
        if (
            _IATA_VALUE_RE.match(value.strip())
            and _selector_expects_date(selector_blob)
            and not _selector_expects_airport_code(selector_blob)
        ):
            mismatches.append(
                {
                    "selector": _selector_candidates(step.get("selector")),
                    "value": value,
                    "reason": "airport_code_into_date_field",
                }
            )
    return mismatches


def _plan_has_click_token(plan, tokens) -> bool:
    """Return True when plan already contains click selectors with any token."""
    if not isinstance(plan, list):
        return False
    wanted = [t for t in tokens if isinstance(t, str) and t]
    if not wanted:
        return False
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "click":
            continue
        blob = " ".join(_selector_candidates(step.get("selector"))).lower()
        if any(token.lower() in blob for token in wanted):
            return True
    return False
