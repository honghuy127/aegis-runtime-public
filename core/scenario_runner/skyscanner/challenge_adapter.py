"""Skyscanner challenge adapter helpers for attempt-gate orchestration."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from core.browser.manual_intervention_policy import (
    is_skyscanner_px_captcha_url,
    manual_intervention_diagnostic_selectors,
)
from core.scenario_runner.skyscanner.interstitials import detect_skyscanner_interstitial_block


def _is_skyscanner_captcha_url(browser: Any) -> bool:
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "")
    except Exception:
        return False
    return bool(is_skyscanner_px_captcha_url(current_url))


def _wait_ms(browser: Any, wait_ms: int) -> None:
    duration = max(0, int(wait_ms or 0))
    if duration <= 0:
        return
    page = getattr(browser, "page", None)
    try:
        if page is not None and hasattr(page, "wait_for_timeout"):
            page.wait_for_timeout(duration)
            return
    except Exception:
        pass
    try:
        time.sleep(duration / 1000.0)
    except Exception:
        return


def _route_key_from_url(url: str) -> str:
    raw = str(url or "").strip().lower()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    path = str(parsed.path or "")
    if "/transport/flights/" not in path:
        return ""
    tail = path.split("/transport/flights/", 1)[-1].strip("/")
    return tail


def _clearance_probe(browser: Any, html_text: str) -> Dict[str, Any]:
    """Assess whether Skyscanner interstitial indicators are still present."""
    html_now = str(html_text or "")
    current_url = ""
    page = getattr(browser, "page", None)
    if page is not None:
        try:
            current_url = str(getattr(page, "url", "") or "")
        except Exception:
            current_url = ""
    current_route_key = _route_key_from_url(current_url)
    route_results_surface = bool(current_route_key) and not _is_skyscanner_captcha_url(browser)
    hard_block: Dict[str, Any] = {}
    try:
        hard_block = detect_skyscanner_interstitial_block(html_now) or {}
    except Exception:
        hard_block = {}
    if not hard_block and _is_skyscanner_captcha_url(browser):
        hard_block = {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"url.captcha_surface": True},
        }

    runtime_diag: Dict[str, Any] = {}
    selector_blocked = False
    cookie_runtime_blocked = False
    if hasattr(browser, "collect_runtime_diagnostics"):
        try:
            runtime_diag = browser.collect_runtime_diagnostics(
                selectors=manual_intervention_diagnostic_selectors(site_key="skyscanner")
                + [
                    "input#originInput-input",
                    "input#destinationInput-input",
                    "input[name='originInput-search']",
                    "input[name='destinationInput-search']",
                ]
            )
        except Exception:
            runtime_diag = {}

    selector_probe = (runtime_diag or {}).get("selector_probe", [])
    search_form_visible = False
    px_iframe_visible_count = 0
    if isinstance(selector_probe, list):
        for probe in selector_probe[:16]:
            if not isinstance(probe, dict):
                continue
            selector = str(probe.get("selector", "") or "").lower()
            count = int(probe.get("count", 0) or 0)
            visible = bool(probe.get("visible", False))
            if count <= 0:
                continue
            if visible and (
                "origininput-input" in selector
                or "destinationinput-input" in selector
                or "origininput-search" in selector
                or "destinationinput-search" in selector
            ):
                search_form_visible = True
            if "px-cloud.net" in selector and visible:
                px_iframe_visible_count += count
            if "px-captcha" in selector or "human verification" in selector:
                selector_blocked = True
                break
            if visible and ("captcha" in selector or "resolve" in selector):
                selector_blocked = True
                break

    dom_probe = (runtime_diag or {}).get("dom_probe", {})
    px_runtime_active = False
    challenge_scripts_blocked = False
    challenge_script_blocked_count = 0
    challenge_failed_count = 0
    if isinstance(dom_probe, dict):
        cookie_enabled = dom_probe.get("cookie_enabled")
        cookie_probe_settable = dom_probe.get("cookie_probe_settable")
        if cookie_enabled is False or cookie_probe_settable is False:
            cookie_runtime_blocked = True
        try:
            px_iframe_count = int(dom_probe.get("px_iframe_count", 0) or 0)
        except Exception:
            px_iframe_count = 0
        try:
            px_iframe_visible_count = max(
                px_iframe_visible_count,
                int(dom_probe.get("px_iframe_visible_count", 0) or 0),
            )
        except Exception:
            pass
        px_signature = str(dom_probe.get("px_challenge_signature", "") or "").strip()
        if px_iframe_count > 0 and px_signature:
            px_runtime_active = True

    network_diag = (runtime_diag or {}).get("network", {})
    network_window = (network_diag or {}).get("window", {}) if isinstance(network_diag, dict) else {}
    if isinstance(network_window, dict):
        challenge_script_blocked_count = int(
            network_window.get("failed_challenge_hosts_blocked_by_client", 0) or 0
        )
        challenge_failed_count = int(network_window.get("failed_challenge_hosts", 0) or 0)
        challenge_scripts_blocked = challenge_script_blocked_count > 0

    if not hard_block and selector_blocked:
        hard_block = {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"selector_probe.blocked": True},
        }
    if not hard_block and cookie_runtime_blocked:
        hard_block = {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"cookie.runtime_disabled": True},
        }
    if (
        hard_block
        and str((hard_block.get("reason", "") or "")).strip().lower() == "blocked_interstitial_captcha"
        and not selector_blocked
        and not cookie_runtime_blocked
        and px_iframe_visible_count <= 0
        and not challenge_scripts_blocked
        and (search_form_visible or route_results_surface)
        and not _is_skyscanner_captcha_url(browser)
    ):
        # Hidden PX telemetry can persist on the flights page after a successful solve.
        # Do not classify as blocked unless challenge shell remains visible/active.
        hard_block = {}
    if not hard_block and px_runtime_active and px_iframe_visible_count > 0:
        hard_block = {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"dom_probe.px_runtime_active": True},
        }
    if not hard_block and challenge_scripts_blocked:
        hard_block = {
            "reason": "blocked_interstitial_challenge_script_blocked",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {
                "network.failed_challenge_hosts_blocked_by_client": int(
                    challenge_script_blocked_count
                ),
                "network.failed_challenge_hosts": int(challenge_failed_count),
            },
        }

    return {
        "blocked": bool(hard_block),
        "reason": str((hard_block or {}).get("reason", "")),
        "hard_block": hard_block,
        "runtime_diag": runtime_diag,
        "selector_blocked": selector_blocked,
        "cookie_runtime_blocked": cookie_runtime_blocked,
        "px_runtime_active": px_runtime_active,
        "challenge_scripts_blocked": challenge_scripts_blocked,
        "challenge_script_blocked_count": int(challenge_script_blocked_count),
        "challenge_failed_count": int(challenge_failed_count),
        "search_form_visible": bool(search_form_visible),
        "px_iframe_visible_count": int(px_iframe_visible_count),
        "px_signature": str((dom_probe or {}).get("px_challenge_signature", "") or "")
        if isinstance(dom_probe, dict)
        else "",
        "current_url": str(current_url or ""),
        "current_route_key": str(current_route_key or ""),
        "route_results_surface": bool(route_results_surface),
        "html_len": len(html_now),
    }


def validate_skyscanner_interstitial_clearance(
    *,
    browser: Any,
    html_text: str,
    get_threshold_fn: Callable[[str, Any], Any],
    grace_probe: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Require multiple bounded non-blocked probes before accepting interstitial clear."""
    checks = max(1, min(6, int(get_threshold_fn("skyscanner_interstitial_clearance_checks", 4))))
    interval_ms = max(
        250, min(2200, int(get_threshold_fn("skyscanner_interstitial_clearance_interval_ms", 1100)))
    )
    cooldown_probe_ms = max(
        1200,
        min(
            12_000,
            int(get_threshold_fn("skyscanner_interstitial_clearance_cooldown_probe_ms", 9000)),
        ),
    )
    html_now = str(html_text or "")
    probes: list[Dict[str, Any]] = []
    last_runtime_diag: Dict[str, Any] = {}
    px_signature_last = ""
    px_signature_churn = 0
    grace_payload = dict(grace_probe or {}) if isinstance(grace_probe, dict) else {}
    press_hold_executed = bool(grace_payload.get("press_hold_executed", False))
    press_hold_success = bool(grace_payload.get("press_hold_success", False))
    expected_route_url = str(grace_payload.get("expected_route_url", "") or "").strip()
    expected_route_key = _route_key_from_url(expected_route_url)
    if press_hold_executed and not press_hold_success:
        cooldown_probe_ms = max(cooldown_probe_ms, 2600)

    for idx in range(checks):
        probe = _clearance_probe(browser, html_now)
        last_runtime_diag = (
            probe.get("runtime_diag", {}) if isinstance(probe.get("runtime_diag"), dict) else {}
        )
        current_route_key = str(probe.get("current_route_key", "") or "").strip()
        route_results_surface = bool(probe.get("route_results_surface", False))
        px_signature_now = str(probe.get("px_signature", "") or "").strip()
        if px_signature_now:
            if px_signature_last and px_signature_now != px_signature_last:
                px_signature_churn += 1
            px_signature_last = px_signature_now
        probes.append(
            {
                "check": idx + 1,
                "blocked": bool(probe.get("blocked", False)),
                "reason": str(probe.get("reason", "")),
                "selector_blocked": bool(probe.get("selector_blocked", False)),
                "cookie_runtime_blocked": bool(probe.get("cookie_runtime_blocked", False)),
                "px_runtime_active": bool(probe.get("px_runtime_active", False)),
                "challenge_scripts_blocked": bool(probe.get("challenge_scripts_blocked", False)),
                "challenge_script_blocked_count": int(
                    probe.get("challenge_script_blocked_count", 0) or 0
                ),
                "challenge_failed_count": int(probe.get("challenge_failed_count", 0) or 0),
                "px_signature_prefix": px_signature_now[:24],
                "px_signature_churn": int(px_signature_churn),
                "press_hold_executed": bool(press_hold_executed),
                "press_hold_success": bool(press_hold_success),
                "route_results_surface": bool(route_results_surface),
                "route_key_present": bool(current_route_key),
                "html_len": int(probe.get("html_len", 0) or 0),
            }
        )
        if (
            idx == 0
            and not bool(probe.get("blocked", False))
            and route_results_surface
            and not bool(probe.get("selector_blocked", False))
            and not bool(probe.get("cookie_runtime_blocked", False))
            and not bool(probe.get("challenge_scripts_blocked", False))
            and int(probe.get("px_iframe_visible_count", 0) or 0) <= 0
        ):
            if expected_route_key and current_route_key and current_route_key != expected_route_key:
                return {
                    "cleared": False,
                    "reason": "blocked_interstitial_route_context_mismatch",
                    "html": html_now,
                    "probes": probes,
                    "runtime_diag": last_runtime_diag,
                    "evidence": {
                        "route.expected": expected_route_url,
                        "route.current": str(probe.get("current_url", "") or ""),
                    },
                }
            return {
                "cleared": True,
                "reason": "route_ready_fast_path",
                "html": html_now,
                "probes": probes,
                "runtime_diag": last_runtime_diag,
            }
        if bool(probe.get("blocked", False)):
            blocked_reason = str(probe.get("reason", "") or "blocked_interstitial_captcha")
            if px_signature_churn > 0:
                blocked_reason = "blocked_interstitial_reissued_after_manual"
            return {
                "cleared": False,
                "reason": blocked_reason,
                "html": html_now,
                "probes": probes,
                "runtime_diag": last_runtime_diag,
            }

        if idx + 1 >= checks:
            break
        _wait_ms(browser, interval_ms)
        try:
            html_now = str(browser.content() or "")
        except Exception:
            html_now = ""

    # Post-clear cooldown probe catches fast challenge re-issues that appear right
    # after first successful press-and-hold.
    _wait_ms(browser, cooldown_probe_ms)
    try:
        html_now = str(browser.content() or "")
    except Exception:
        html_now = ""
    final_probe = _clearance_probe(browser, html_now)
    final_runtime_diag = (
        final_probe.get("runtime_diag", {}) if isinstance(final_probe.get("runtime_diag"), dict) else {}
    )
    final_signature = str(final_probe.get("px_signature", "") or "").strip()
    if final_signature and px_signature_last and final_signature != px_signature_last:
        px_signature_churn += 1
    probes.append(
        {
            "check": checks + 1,
            "blocked": bool(final_probe.get("blocked", False)),
            "reason": str(final_probe.get("reason", "")),
            "selector_blocked": bool(final_probe.get("selector_blocked", False)),
            "cookie_runtime_blocked": bool(final_probe.get("cookie_runtime_blocked", False)),
            "px_runtime_active": bool(final_probe.get("px_runtime_active", False)),
            "challenge_scripts_blocked": bool(final_probe.get("challenge_scripts_blocked", False)),
            "challenge_script_blocked_count": int(
                final_probe.get("challenge_script_blocked_count", 0) or 0
            ),
            "challenge_failed_count": int(final_probe.get("challenge_failed_count", 0) or 0),
            "px_signature_prefix": final_signature[:24],
            "px_signature_churn": int(px_signature_churn),
            "cooldown_probe_ms": int(cooldown_probe_ms),
            "press_hold_executed": bool(press_hold_executed),
            "press_hold_success": bool(press_hold_success),
            "route_results_surface": bool(final_probe.get("route_results_surface", False)),
            "route_key_present": bool(str(final_probe.get("current_route_key", "") or "").strip()),
            "html_len": int(final_probe.get("html_len", 0) or 0),
        }
    )
    if bool(final_probe.get("blocked", False)):
        final_reason = str(final_probe.get("reason", "") or "blocked_interstitial_captcha")
        if px_signature_churn > 0:
            final_reason = "blocked_interstitial_reissued_after_manual"
        elif press_hold_executed and not press_hold_success:
            final_reason = "blocked_interstitial_press_hold_unsuccessful"
        return {
            "cleared": False,
            "reason": final_reason,
            "html": html_now,
            "probes": probes,
            "runtime_diag": final_runtime_diag or last_runtime_diag,
        }
    if px_signature_churn >= 2:
        return {
            "cleared": False,
            "reason": "blocked_interstitial_reissued_after_manual",
            "html": html_now,
            "probes": probes,
            "runtime_diag": final_runtime_diag or last_runtime_diag,
        }
    if (
        press_hold_executed
        and not press_hold_success
        and bool(final_probe.get("px_runtime_active", False))
    ):
        return {
            "cleared": False,
            "reason": "blocked_interstitial_press_hold_unsuccessful",
            "html": html_now,
            "probes": probes,
            "runtime_diag": final_runtime_diag or last_runtime_diag,
        }
    if expected_route_key:
        current_url = ""
        page = getattr(browser, "page", None)
        if page is not None:
            try:
                current_url = str(getattr(page, "url", "") or "")
            except Exception:
                current_url = ""
        current_route_key = _route_key_from_url(current_url)
        if not current_route_key:
            return {
                "cleared": False,
                "reason": "blocked_interstitial_route_context_lost",
                "html": html_now,
                "probes": probes,
                "runtime_diag": final_runtime_diag or last_runtime_diag,
                "evidence": {
                    "route.expected": expected_route_url,
                    "route.current": current_url,
                },
            }
        if current_route_key != expected_route_key:
            return {
                "cleared": False,
                "reason": "blocked_interstitial_route_context_mismatch",
                "html": html_now,
                "probes": probes,
                "runtime_diag": final_runtime_diag or last_runtime_diag,
                "evidence": {
                    "route.expected": expected_route_url,
                    "route.current": current_url,
                },
            }

    return {
        "cleared": True,
        "reason": "stable_clear",
        "html": html_now,
        "probes": probes,
        "runtime_diag": final_runtime_diag or last_runtime_diag,
    }


def attempt_skyscanner_last_resort_manual(
    *,
    browser: Any,
    grace_probe: Dict[str, Any],
    fallback_result: Dict[str, Any],
    get_threshold_fn: Callable[[str, Any], Any],
) -> Dict[str, Any]:
    """Skyscanner-specific last-resort manual window policy."""
    out = {
        "attempted": False,
        "error": "",
        "attempt_html_probe": "",
        "grace_probe": dict(grace_probe) if isinstance(grace_probe, dict) else {},
        "fallback_result": dict(fallback_result)
        if isinstance(fallback_result, dict)
        else {"used": False, "attempted": False, "cleared": False},
        "manual_last_result": {},
        "cleared": False,
        "reason": "",
    }
    if not hasattr(browser, "allow_manual_verification_intervention"):
        return out
    out["attempted"] = True
    last_resort_wait_sec = max(
        10,
        int(get_threshold_fn("skyscanner_captcha_manual_wait_sec", 45)),
    )
    try:
        session_manual_timeout = int(getattr(browser, "manual_intervention_timeout_sec", 0) or 0)
        if session_manual_timeout > 0:
            last_resort_wait_sec = max(last_resort_wait_sec, min(180, session_manual_timeout))
    except Exception:
        pass
    rounds = max(1, min(3, int(get_threshold_fn("skyscanner_last_resort_manual_rounds", 2) or 2)))
    manual_last_result: Dict[str, Any] = {}
    hard_block_after_manual_last: Dict[str, Any] = {}
    page_url_after = ""
    attempt_html_probe = ""
    for round_idx in range(rounds):
        manual_last_result = browser.allow_manual_verification_intervention(
            reason="skyscanner_interstitial_last_resort_when_manual_disabled",
            wait_sec=last_resort_wait_sec,
            force=True,
            mode_override="assist",
        )
        if isinstance(manual_last_result, dict):
            manual_last_result["last_resort_round"] = int(round_idx + 1)
            manual_last_result["last_resort_rounds_total"] = int(rounds)
        try:
            attempt_html_probe = str(browser.content() or "")
        except Exception:
            attempt_html_probe = ""
        probe_after_manual = _clearance_probe(browser, attempt_html_probe)
        hard_block_after_manual_last = dict(probe_after_manual.get("hard_block", {}) or {})
        page = getattr(browser, "page", None)
        page_url_after = ""
        if page is not None:
            try:
                page_url_after = str(getattr(page, "url", "") or "")
            except Exception:
                page_url_after = ""
        if (
            not hard_block_after_manual_last
            and bool(probe_after_manual.get("blocked", False))
            and _is_skyscanner_captcha_url(browser)
        ):
            hard_block_after_manual_last = {
                "reason": "blocked_interstitial_captcha",
                "page_kind": "interstitial",
                "block_type": "captcha",
                "evidence": {"url.captcha_surface": True},
            }
        if not hard_block_after_manual_last:
            break
        # Continue bounded assist windows only when challenge still blocks.
        if round_idx + 1 >= rounds:
            break

    out["manual_last_result"] = (
        dict(manual_last_result) if isinstance(manual_last_result, dict) else {}
    )
    manual_reason = str((out["manual_last_result"] or {}).get("reason", "") or "").strip().lower()
    manual_used = bool((out["manual_last_result"] or {}).get("used", False))
    manual_automation_count = int(
        (
            (
                (out["manual_last_result"] or {}).get("automation_activity_during_manual", {})
                or {}
            ).get("count", 0)
        )
        or 0
    )
    local_fallback = dict(out["fallback_result"])
    local_fallback["last_resort_manual_intervention"] = dict(out["manual_last_result"])
    out["fallback_result"] = local_fallback

    out["attempt_html_probe"] = attempt_html_probe
    page = getattr(browser, "page", None)
    if not hard_block_after_manual_last and _is_skyscanner_captcha_url(browser):
        hard_block_after_manual_last = {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"url.captcha_surface": True},
        }
    if (
        not hard_block_after_manual_last
        and (
            not manual_used
            or manual_reason in {"manual_intervention_target_closed", "manual_intervention_exception"}
            or manual_automation_count > 0
        )
    ):
        hard_block_after_manual_last = {
            "reason": "blocked_interstitial_manual_unreliable",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {
                "manual.reason": manual_reason,
                "manual.used": bool(manual_used),
                "manual.automation_activity_count": int(manual_automation_count),
            },
        }
    if (
        not hard_block_after_manual_last
        and str(page_url_after or "").strip().lower().startswith("about:blank")
    ):
        hard_block_after_manual_last = {
            "reason": "blocked_interstitial_manual_unreliable",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"url.unreliable_after_manual": str(page_url_after or "")[:120]},
        }
    page_open = True
    if page is None:
        page_open = False
    else:
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                page_open = False
        except Exception:
            page_open = False
    if not str(attempt_html_probe or "").strip() or not page_open:
        if not hard_block_after_manual_last:
            hard_block_after_manual_last = {
                "reason": "blocked_interstitial_captcha",
                "page_kind": "interstitial",
                "block_type": "captcha",
                "evidence": {
                    "snapshot.unreliable": True,
                    "manual.reason": manual_reason,
                    "manual.used": bool(manual_used),
                    "manual.automation_activity_count": int(manual_automation_count),
                },
            }
    if not hard_block_after_manual_last:
        local_grace = dict(out["grace_probe"])
        local_grace["cleared"] = True
        local_grace["html"] = attempt_html_probe
        local_grace["reason"] = "cleared_after_last_resort_manual"
        local_grace["manual_intervention_last_resort"] = dict(out["manual_last_result"])
        out["grace_probe"] = local_grace
        out["cleared"] = True
    out["reason"] = str((out["manual_last_result"] or {}).get("reason", "") or "")
    return out
