"""Date verification helpers for Google Flights date picker."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.browser import wall_clock_remaining_ms
from core.scenario.gf_helpers.date_fields import (
    _gf_field_value_matches_date as _gf_field_value_matches_date_impl,
    _gf_read_date_field_value as _gf_read_date_field_value_impl,
)
from core.scenario.gf_helpers.helpers import _prefer_locale_token_order


def _role_label_matches_date_field(
    *,
    role_key: str,
    aria_label: str,
    placeholder: str,
    profile: dict,
    locale_hint: str,
) -> bool:
    blob = " ".join(
        [
            str(aria_label or "").strip().lower(),
            str(placeholder or "").strip().lower(),
        ]
    )
    if not blob.strip():
        return False
    scoring_tokens_config = profile.get("date_opener_field_specific_scoring_tokens", {}).get(role_key, {})
    ja_tokens = scoring_tokens_config.get("ja", [])
    en_tokens = scoring_tokens_config.get("en", [])
    if not ja_tokens or not en_tokens:
        if role_key == "depart":
            ja_tokens = ["出発", "往路"]
            en_tokens = ["departure", "depart", "outbound"]
        else:
            ja_tokens = ["復路", "帰り", "帰路"]
            en_tokens = ["return", "inbound"]
    tokens = _prefer_locale_token_order(
        ja_tokens=ja_tokens,
        en_tokens=en_tokens,
        locale_hint=locale_hint,
    )
    return any(str(tok or "").strip().lower() in blob for tok in tokens if str(tok or "").strip())


def verify_date_commitment_impl(
    *,
    page,
    role_key: str,
    target_date: str,
    locale_hint: str,
    profile: dict,
    logger,
    verify_selectors: list[str],
    evidence_dict: Dict[str, Any],
    parsing_method: Optional[str],
    close_method: str,
    close_scope_used: str,
    done_clicked: bool,
    day_clicked: bool,
    nav_steps: int,
    deadline,
    budget,
    budget_used_start: int,
    expected_peer_date: str,
    date_field_selector: str,
) -> Tuple[Optional[Dict[str, Any]], str, str, Dict[str, Any]]:
    """Verify the date commit; return (failure_dict_or_none, verified_value, verify_method, evidence_dict)."""
    verified = False
    verified_value: Optional[str] = None
    verify_method = ""

    active_snapshot: Dict[str, Any] = {}
    try:
        if hasattr(page, "evaluate"):
            active_snapshot = page.evaluate(
                """() => {
                    const e = document.activeElement;
                    if (!e || (e.tagName !== 'INPUT' && e.tagName !== 'TEXTAREA')) return {};
                    return {
                        tag: String(e.tagName || '').toLowerCase(),
                        value: String(e.value || ''),
                        aria_label: String(e.getAttribute('aria-label') || ''),
                        placeholder: String(e.getAttribute('placeholder') || '')
                    };
                }""",
                timeout=200,
            ) or {}
    except Exception:
        active_snapshot = {}

    active_value = str((active_snapshot or {}).get("value", "") or "")
    active_aria = str((active_snapshot or {}).get("aria_label", "") or "")
    active_placeholder = str((active_snapshot or {}).get("placeholder", "") or "")
    active_role_match = _role_label_matches_date_field(
        role_key=role_key,
        aria_label=active_aria,
        placeholder=active_placeholder,
        profile=profile,
        locale_hint=locale_hint,
    )
    active_value_match = bool(
        active_value
        and active_role_match
        and _gf_field_value_matches_date_impl(active_value, target_date)
    )

    if active_value_match:
        verified = True
        verified_value = active_value
        verify_method = "active_input_semantic"

    for verify_sel in verify_selectors[:5]:
        if verified:
            break
        try:
            locator_group = page.locator(verify_sel)
            try:
                verify_count = int(locator_group.count())
            except Exception:
                verify_count = 1
            for idx in range(max(1, min(verify_count, 4))):
                field_locator = locator_group.nth(idx)
                try:
                    if not field_locator.is_visible(timeout=120):
                        continue
                except Exception:
                    continue
                field_value = field_locator.input_value(timeout=200) or ""
                if not field_value:
                    field_value = field_locator.get_attribute("value", timeout=200) or ""
                if not field_value:
                    try:
                        field_value = field_locator.text_content(timeout=200) or ""
                    except Exception:
                        field_value = ""
                field_value = str(field_value or "").strip()
                if not field_value:
                    continue

                verified_value = field_value

                if _gf_field_value_matches_date_impl(field_value, target_date):
                    verified = True
                    verify_method = "selector_semantic"
                    break
            if verified:
                break
        except Exception:
            pass

    if not verified:
        try:
            fallback_value = _gf_read_date_field_value_impl(
                page,
                role_key=role_key,
                locale_hint=locale_hint,
                role_selectors=None,
                target_date=target_date,
            )
        except Exception:
            fallback_value = ""
        if fallback_value:
            verified_value = fallback_value
            if _gf_field_value_matches_date_impl(fallback_value, target_date):
                verified = True
                verify_method = "field_read_fallback_semantic"

    if not verified:
        logger.warning(
            "gf_set_date.verify.failed role=%s expected_date=%s verified_value=%s nav_steps=%d",
            role_key,
            target_date,
            verified_value or "not_found",
            nav_steps,
        )
        return (
            {
                "ok": False,
                "reason": "date_picker_unverified",
                "evidence": {
                    "calendar.parsing_method": parsing_method or "unknown",
                    "verify.committed": day_clicked,
                    "verify.value": verified_value or "not_found",
                    "verify.expected_date": target_date,
                    "verify.nav_steps": nav_steps,
                    "verify.close_method": close_method,
                    "verify.close_scope": close_scope_used if done_clicked else "escape",
                    "verify.method": verify_method or "none",
                    "verify.active_value": active_value or "",
                    "verify.active_aria_label": active_aria or "",
                    "verify.active_placeholder": active_placeholder or "",
                    "verify.active_role_match": bool(active_role_match),
                    "verify.active_matches_expected": bool(active_value_match),
                    "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                    "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
                    "ui.selectors_tried": len(verify_selectors),
                },
                "selector_used": date_field_selector,
                "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
            },
            verified_value or "",
            verify_method,
            evidence_dict,
        )

    peer_date = str(expected_peer_date or "").strip()
    if role_key == "return" and peer_date and peer_date != target_date:
        depart_value = _gf_read_date_field_value_impl(
            page,
            role_key="depart",
            locale_hint=locale_hint,
            target_date=peer_date,
        )
        if depart_value and _gf_field_value_matches_date_impl(depart_value, target_date):
            logger.warning(
                "gf_set_date.verify.invariant_failed role=return reason=return_overwrote_depart depart_value=%s expected_depart=%s return_value=%s",
                depart_value[:60],
                peer_date[:16],
                (verified_value or "")[:60] if verified_value else "",
            )
            return (
                {
                    "ok": False,
                    "reason": "date_picker_unverified",
                    "evidence": {
                        **evidence_dict,
                        "calendar.parsing_method": parsing_method or "unknown",
                        "verify.committed": day_clicked,
                        "verify.value": verified_value or "not_found",
                        "verify.expected_date": target_date,
                        "verify.nav_steps": nav_steps,
                        "verify.close_method": close_method,
                        "verify.round_trip_invariant": "return_overwrote_depart",
                        "verify.depart_value": depart_value,
                        "verify.expected_depart": peer_date,
                        "time.deadline_ms_remaining": wall_clock_remaining_ms(deadline) or -1,
                        "budget.actions_used": budget.max_actions - budget.remaining - budget_used_start,
                    },
                    "selector_used": date_field_selector,
                    "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
                },
                verified_value or "",
                verify_method,
                evidence_dict,
            )
        evidence_dict["verify.round_trip_invariant"] = "ok"

    return None, verified_value or "", verify_method, evidence_dict
