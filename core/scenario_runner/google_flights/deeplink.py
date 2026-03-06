"""Google Flights deeplink functions."""

from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from utils.logging import get_logger

log = get_logger(__name__)

import time
from core.browser import BrowserSession, safe_min_timeout_ms
from core.route_binding import classify_google_deeplink_page_state_recovery_reason, dom_route_bind_probe
from core.plugins.services.google_flights import build_google_flights_deeplink
from core.scenario_runner.google_flights.route_recovery import (
    google_activate_route_form_recovery as _google_activate_route_form_recovery_impl,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_deeplink_page_state_recovery_policy,
    _google_deeplink_recovery_plan,
    _google_deeplink_probe_status,
    _is_google_flights_deeplink,
    _parse_google_deeplink_context,
    _service_fill_activation_keywords,
)

import core.scenario_runner as sr

def _should_attempt_google_deeplink_page_state_recovery(
    *,
    trigger_reason: str,
    enabled: bool,
    uses: int,
    max_extra_actions: int,
) -> bool:
    """Return True when phase-3 deeplink page-state recovery may run once."""
    if not bool(enabled):
        return False
    if int(max_extra_actions) <= 0:
        return False
    if int(uses) >= int(max_extra_actions):
        return False
    decision = classify_google_deeplink_page_state_recovery_reason(trigger_reason)
    return bool(decision.get("eligible"))


def _attempt_google_deeplink_page_state_recovery(
    browser,
    *,
    trigger_reason: str,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
    enabled: bool,
    uses: int,
    max_extra_actions: int,
    recovery_hook: Optional[Callable[..., Dict[str, Any]]] = None,
    rebind_hook: Optional[Callable[..., Tuple[bool, str, str]]] = None,
) -> Dict[str, Any]:
    """Run one bounded page-state recovery + rebind attempt for deeplink fast path."""
    decision = classify_google_deeplink_page_state_recovery_reason(trigger_reason)
    if not _should_attempt_google_deeplink_page_state_recovery(
        trigger_reason=trigger_reason,
        enabled=enabled,
        uses=uses,
        max_extra_actions=max_extra_actions,
    ):
        return {
            "used": False,
            "uses": int(uses),
            "ready": False,
            "fail_fast": False,
            "reason": "",
            "html": "",
            "scope_class": str(decision.get("scope_class", "unknown") or "unknown"),
            "trigger_reason": str(trigger_reason or ""),
        }

    next_uses = int(uses) + 1
    recovery_impl = recovery_hook or _google_activate_route_form_recovery_impl
    rebind_impl = rebind_hook or _google_deeplink_quick_rebind
    recovery_result = {}
    try:
        recovery_result = recovery_impl(browser, locale_hint="")
    except TypeError:
        recovery_result = recovery_impl(browser)  # test hooks / backward compatibility
    except Exception as exc:  # pragma: no cover - defensive fallback
        recovery_result = {"ok": False, "reason": f"recovery_hook_error:{exc}"}

    recovery_ok = bool(isinstance(recovery_result, dict) and recovery_result.get("ok"))
    recovery_reason = str(
        (recovery_result.get("reason") if isinstance(recovery_result, dict) else "") or ""
    )
    recovery_html = str(
        (recovery_result.get("html") if isinstance(recovery_result, dict) else "") or ""
    )
    if not recovery_html:
        try:
            recovery_html = str(browser.content() or "")
        except Exception:
            recovery_html = ""

    if not recovery_ok:
        canonical = str(decision.get("canonical_reason", "") or "non_flight_scope_unknown")
        return {
            "used": True,
            "uses": next_uses,
            "ready": False,
            "fail_fast": True,
            "reason": f"deeplink_page_state_recovery_failed_{canonical}",
            "html": recovery_html,
            "scope_class": str(decision.get("scope_class", "unknown") or "unknown"),
            "trigger_reason": str(trigger_reason or ""),
            "recovery_reason": recovery_reason or "route_form_activation_failed",
        }

    rebound_ok, rebound_reason, rebound_html = rebind_impl(
        browser,
        url=url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
    )
    if rebound_ok:
        return {
            "used": True,
            "uses": next_uses,
            "ready": True,
            "fail_fast": False,
            "reason": "deeplink_page_state_recovery_ready",
            "html": rebound_html,
            "scope_class": "flight_only",
            "trigger_reason": str(trigger_reason or ""),
            "recovery_reason": recovery_reason or "activated_route_form",
            "rebind_reason": rebound_reason,
        }

    canonical = str(decision.get("canonical_reason", "") or "non_flight_scope_unknown")
    return {
        "used": True,
        "uses": next_uses,
        "ready": False,
        # Route-form activation succeeded, so keep the explicit reason code but let the
        # normal bounded deeplink recovery plan continue instead of aborting early.
        "fail_fast": False,
        "reason": f"deeplink_page_state_recovery_unready_{canonical}",
        "html": rebound_html or recovery_html,
        "scope_class": str(decision.get("scope_class", "unknown") or "unknown"),
        "trigger_reason": str(trigger_reason or ""),
        "recovery_reason": recovery_reason or "activated_route_form",
        "rebind_reason": rebound_reason or "",
    }


def _normalize_google_deeplink_with_mimic(
    *,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
    mimic_locale: str,
    mimic_region: str,
    mimic_currency: str,
) -> str:
    """Normalize Google deeplink to current mimic locale/region/currency params."""
    if "flt=" not in str(url or ""):
        return url
    if not bool(sr.get_threshold("google_flights_deeplink_use_mimic_params", True)):
        return url
    try:
        candidate = build_google_flights_deeplink(
            {
                "origin": origin,
                "dest": dest,
                "depart": depart,
                "return_date": return_date,
                "trip_type": trip_type,
            },
            {
                "mimic_locale": mimic_locale,
                "mimic_region": mimic_region,
                "mimic_currency": mimic_currency,
            },
            base_url=url,
        )
        try:
            parsed = urlparse(str(candidate or ""))
            frag = str(parsed.fragment or "")
            frag_lower = frag.lower()
            if "flt=" not in frag_lower:
                return url
            expected_depart = str(depart or "").strip()
            if expected_depart and expected_depart not in frag:
                return url
            if str(trip_type or "").strip().lower() == "round_trip":
                expected_return = str(return_date or "").strip()
                if expected_return and expected_return not in frag:
                    return url
        except Exception:
            return url
        return candidate
    except Exception:
        return url


def _google_deeplink_quick_rebind(
    browser,
    *,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
):
    """Try a short selectorless rebind flow before giving up in light mode."""
    # Lazy import to avoid circular dependency
    from core.scenario_runner import (
        _service_fill_activation_keywords,
        _service_search_click_fallbacks,
        _current_mimic_locale,
        _google_display_locale_hint_from_url,
        _visible_selector_subset,
    )

    action_timeout = int(
        sr.get_threshold("google_flights_quick_rebind_action_timeout_ms", 2500)
    )
    settle_timeout = int(
        sr.get_threshold("google_flights_quick_rebind_settle_timeout_ms", 12000)
    )
    step_pause = int(sr.get_threshold("google_flights_quick_rebind_step_pause_ms", 300))
    search_click_max_selectors = max(
        0,
        int(sr.get_threshold("google_flights_quick_rebind_search_click_max_selectors", 4)),
    )
    search_visibility_probe_ms = max(
        0,
        int(sr.get_threshold("google_flights_quick_rebind_search_visibility_probe_ms", 80)),
    )

    roles = [("origin", origin), ("dest", dest), ("depart", depart)]
    if trip_type == "round_trip" and return_date:
        roles.append(("return", return_date))

    for role, value in roles:
        if not isinstance(value, str) or not value.strip():
            continue
        keywords = _service_fill_activation_keywords("google_flights", role)
        filled = False
        if hasattr(browser, "fill_by_keywords"):
            try:
                filled = bool(
                    browser.fill_by_keywords(
                        keywords,
                        value,
                        timeout_ms=action_timeout,
                    )
                )
            except Exception:
                filled = False
        if not filled and hasattr(browser, "activate_field_by_keywords") and hasattr(
            browser, "type_active"
        ):
            try:
                activated = bool(
                    browser.activate_field_by_keywords(
                        keywords,
                        timeout_ms=action_timeout,
                    )
                )
                if activated:
                    browser.type_active(value, timeout_ms=action_timeout)
                    filled = True
            except Exception:
                filled = False
        if not filled:
            return False, f"rebind_failed_{role}", browser.content()
        try:
            browser.page.wait_for_timeout(max(50, step_pause))
        except Exception:
            pass
    last_html = browser.content()
    try:
        pre_click_ready, pre_click_reason = sr._google_deeplink_probe_status(last_html, url)
    except Exception:
        pre_click_ready, pre_click_reason = False, "probe_error"
    log.info(
        "gf.deeplink.quick_rebind.pre_search_probe ready=%s reason=%s html_len=%s",
        pre_click_ready,
        pre_click_reason,
        len(last_html or ""),
    )
    if pre_click_ready:
        return True, "rebind_ready", last_html

    clicked_search_selector = ""
    search_locale_hint = _google_display_locale_hint_from_url(url) or _current_mimic_locale()
    search_selectors = _service_search_click_fallbacks(
        "google_flights",
        locale_hint_override=search_locale_hint,
    )
    if search_click_max_selectors <= 0:
        log.info(
            "gf.deeplink.quick_rebind.search_click_skipped reason=max_selectors_zero total=%s",
            len(search_selectors),
        )
    else:
        visible_search = _visible_selector_subset(
            browser,
            search_selectors,
            per_selector_timeout_ms=search_visibility_probe_ms,
            max_candidates=search_click_max_selectors,
        )
        click_candidates = visible_search
        if not click_candidates:
            click_candidates = list(search_selectors[:search_click_max_selectors])
        log.info(
            "gf.deeplink.quick_rebind.search_candidates total=%s visible=%s max_clicks=%s using_visible=%s locale_hint=%s",
            len(search_selectors),
            len(visible_search),
            search_click_max_selectors,
            bool(visible_search),
            str(search_locale_hint or "")[:16],
        )
        for selector in click_candidates:
            try:
                browser.click(
                    selector,
                    timeout_ms=safe_min_timeout_ms(action_timeout, 700),
                )
                clicked_search_selector = selector
                log.info(
                    "gf.deeplink.quick_rebind.search_click_ok selector=%s",
                    selector[:160],
                )
                break
            except Exception as exc:
                log.info(
                    "gf.deeplink.quick_rebind.search_click_fail selector=%s error=%s",
                    str(selector)[:160],
                    str(exc)[:200],
                )
                continue
        if not clicked_search_selector:
            log.info(
                "gf.deeplink.quick_rebind.search_click_skipped reason=no_click_success attempted=%s",
                len(click_candidates),
            )

    deadline = time.monotonic() + max(2.0, settle_timeout / 1000.0)
    last_reason = "not_checked"
    while time.monotonic() <= deadline:
        ready, reason = sr._google_deeplink_probe_status(last_html, url)
        last_reason = reason
        if ready:
            if clicked_search_selector:
                try:
                    sr.promote_selector_hint(
                        site="google_flights",
                        action="quick_rebind_search",
                        role="",
                        selector=clicked_search_selector,
                        display_lang=str(search_locale_hint or ""),
                        locale=str(_current_mimic_locale() or ""),
                        source="runtime_verified",
                    )
                    log.info(
                        "selector_hints.promote site=google_flights action=quick_rebind_search selector=%s lang=%s",
                        clicked_search_selector[:120],
                        str(search_locale_hint or "")[:16],
                    )
                except Exception:
                    pass
            return True, "rebind_ready", last_html
        try:
            browser.page.wait_for_timeout(700)
        except Exception:
            pass
        last_html = browser.content()
    return False, f"rebind_unready_{last_reason}", last_html
