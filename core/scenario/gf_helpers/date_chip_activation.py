"""Return-chip activation helper for Google Flights date picker."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.scenario.gf_helpers.date_fields import _gf_try_activate_date_chip as _gf_try_activate_date_chip_impl


def activate_return_chip_impl(
    *,
    page,
    calendar_root,
    role_key: str,
    locale_hint: str,
    logger,
    budget_check,
    budgeted_timeout_fn,
    evidence_dict: Dict[str, Any],
    date_field_selector: str,
    budget_used_start: int,
    budget,
    chip_activation_fn=None,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any], bool, str]:
    """Activate return chip/tab when filling return date.

    Returns a tuple of (failure_dict_or_none, evidence_dict, chip_ok, chip_selector).
    """
    if role_key != "return":
        return None, evidence_dict, False, ""

    if not budget_check(1):
        return (
            {
                "ok": False,
                "reason": "budget_hit",
                "evidence": {"stage": "return_chip_activate"},
                "selector_used": date_field_selector,
                "action_budget_used": budget.max_actions - budget.remaining - budget_used_start,
            },
            evidence_dict,
            False,
            "",
        )

    chip_timeout_ms = min(300, budgeted_timeout_fn())
    chip_error = ""

    # Use provided chip_activation_fn or default to _gf_try_activate_date_chip_impl
    if chip_activation_fn is None:
        chip_activation_fn = _gf_try_activate_date_chip_impl

    try:
        chip_ok, chip_selector = chip_activation_fn(
            page,
            role_key="return",
            locale_hint=locale_hint,
            calendar_root=calendar_root,
            timeout_ms=chip_timeout_ms,
        )
    except Exception as exc:
        chip_ok, chip_selector = False, ""
        chip_error = str(exc)[:120]

    evidence_dict["calendar.return_chip_attempted"] = True
    evidence_dict["calendar.return_chip_timeout_ms"] = int(chip_timeout_ms)
    evidence_dict["calendar.return_chip_activated"] = bool(chip_ok)
    if chip_selector:
        evidence_dict["calendar.return_chip_selector"] = str(chip_selector)[:120]
    if chip_error:
        evidence_dict["calendar.return_chip_error"] = chip_error
    if chip_ok:
        try:
            time.sleep(0.08)
        except Exception:
            pass
    logger.info(
        "gf_set_date.return_chip_activate role=%s ok=%s selector=%s",
        role_key,
        chip_ok,
        (chip_selector or "")[:80],
    )

    return None, evidence_dict, bool(chip_ok), str(chip_selector or "")
