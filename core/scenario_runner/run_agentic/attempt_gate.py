"""Attempt-start prechecks and interstitial gate for run_agentic_scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Dict, Optional

from core.browser.manual_intervention_policy import (
    is_skyscanner_px_captcha_url,
    is_verification_url,
    manual_intervention_diagnostic_selectors,
)
from core.scenario_runner.run_agentic.challenge_provider import ChallengeProvider
from core.scenario_runner.skyscanner.challenge_adapter import (
    attempt_skyscanner_last_resort_manual,
    validate_skyscanner_interstitial_clearance,
)


def _is_browser_page_open(browser: Any) -> bool:
    """Best-effort page liveness check used to avoid false interstitial clears."""
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        if hasattr(page, "is_closed") and page.is_closed():
            return False
    except Exception:
        return False
    return True


def _is_skyscanner_captcha_url(browser: Any) -> bool:
    """Detect known Skyscanner captcha URL surfaces independent of DOM tokens."""
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "").lower()
    except Exception:
        return False
    if not current_url:
        return False
    return bool(is_skyscanner_px_captcha_url(current_url))


def _is_verification_url_from_browser(browser: Any) -> bool:
    """Detect generic verification/challenge URL surfaces."""
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "")
    except Exception:
        return False
    return bool(is_verification_url(current_url))


def _normalize_site_key(site_key: str) -> str:
    return str(site_key or "").strip().lower()


def _build_challenge_provider(
    *,
    site_key: str,
    detect_site_interstitial_block_fn: Callable[[str, str], Dict[str, Any]],
    attempt_skyscanner_interstitial_grace_fn: Callable[..., Dict[str, Any]],
    attempt_skyscanner_interstitial_fallback_reload_fn: Callable[..., Dict[str, Any]],
) -> ChallengeProvider:
    """Resolve provider-specific challenge handlers behind a compact adapter."""
    site = _normalize_site_key(site_key)
    if site == "skyscanner":
        from core.scenario_runner.skyscanner import detect_skyscanner_interstitial_block

        def _detect(html_text: str, browser: Any) -> Dict[str, Any]:
            hard_block = detect_skyscanner_interstitial_block(str(html_text or "")) or {}
            if not hard_block and _is_skyscanner_captcha_url(browser):
                hard_block = {
                    "reason": "blocked_interstitial_captcha",
                    "page_kind": "interstitial",
                    "block_type": "captcha",
                    "evidence": {"url.captcha_surface": True},
                }
            return hard_block

        def _grace(
            browser: Any,
            hard_block: Dict[str, Any],
            *,
            human_mimic: bool,
            get_threshold_fn: Callable[[str, Any], Any],
        ) -> Dict[str, Any]:
            grace_ms = int(get_threshold_fn("skyscanner_blocked_interstitial_grace_ms", 16000))
            grace_ms = max(16000, grace_ms)
            return attempt_skyscanner_interstitial_grace_fn(
                browser,
                hard_block=hard_block,
                human_mimic=bool(human_mimic),
                # Long-hold challenges need enough budget for both readiness wait and hold.
                grace_ms=grace_ms,
            )

        def _fallback(
            browser: Any,
            url: str,
            grace_result: Dict[str, Any],
            *,
            human_mimic: bool,
            get_threshold_fn: Callable[[str, Any], Any],
        ) -> Dict[str, Any]:
            grace_ms_extended = int(
                get_threshold_fn("skyscanner_blocked_interstitial_grace_fallback_ms", 22000)
            )
            grace_ms_extended = max(22000, grace_ms_extended)
            return attempt_skyscanner_interstitial_fallback_reload_fn(
                browser,
                url,
                grace_result=grace_result,
                human_mimic=bool(human_mimic),
                grace_ms_extended=grace_ms_extended,
            )

        def _validate_clearance(
            *,
            browser: Any,
            html_text: str,
            get_threshold_fn: Callable[[str, Any], Any],
            grace_probe: Dict[str, Any] | None = None,
        ) -> Dict[str, Any]:
            return validate_skyscanner_interstitial_clearance(
                browser=browser,
                html_text=html_text,
                get_threshold_fn=get_threshold_fn,
                grace_probe=grace_probe,
            )

        def _attempt_last_resort_manual(
            *,
            browser: Any,
            grace_probe: Dict[str, Any],
            fallback_result: Dict[str, Any],
            get_threshold_fn: Callable[[str, Any], Any],
        ) -> Dict[str, Any]:
            return attempt_skyscanner_last_resort_manual(
                browser=browser,
                grace_probe=grace_probe,
                fallback_result=fallback_result,
                get_threshold_fn=get_threshold_fn,
            )

        return ChallengeProvider(
            name="skyscanner",
            detect_block=_detect,
            attempt_grace=_grace,
            attempt_fallback=_fallback,
            validate_clearance=_validate_clearance,
            attempt_last_resort_manual=_attempt_last_resort_manual,
            supports_last_resort_manual=True,
            requires_page_open_for_clearance=True,
        )

    def _default_detect(html_text: str, _browser: Any) -> Dict[str, Any]:
        return detect_site_interstitial_block_fn(str(html_text or ""), site_key) or {}

    def _default_grace(
        _browser: Any,
        _hard_block: Dict[str, Any],
        *,
        human_mimic: bool,  # noqa: ARG001
        get_threshold_fn: Callable[[str, Any], Any],  # noqa: ARG001
    ) -> Dict[str, Any]:
        return {"used": False, "cleared": False, "html": "", "reason": "site_not_supported"}

    def _default_fallback(
        _browser: Any,
        _url: str,
        _grace_result: Dict[str, Any],
        *,
        human_mimic: bool,  # noqa: ARG001
        get_threshold_fn: Callable[[str, Any], Any],  # noqa: ARG001
    ) -> Dict[str, Any]:
        return {"used": False, "attempted": False, "cleared": False, "reason": "site_not_supported"}

    def _default_validate_clearance(
        *,
        browser: Any,  # noqa: ARG001
        html_text: str,
        get_threshold_fn: Callable[[str, Any], Any],  # noqa: ARG001
        grace_probe: Dict[str, Any] | None = None,  # noqa: ARG001
    ) -> Dict[str, Any]:
        return {
            "cleared": True,
            "reason": "clearance_not_required",
            "html": str(html_text or ""),
            "probes": [],
            "runtime_diag": {},
        }

    def _default_attempt_last_resort_manual(
        *,
        browser: Any,  # noqa: ARG001
        grace_probe: Dict[str, Any],
        fallback_result: Dict[str, Any],
        get_threshold_fn: Callable[[str, Any], Any],  # noqa: ARG001
    ) -> Dict[str, Any]:
        return {
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

    return ChallengeProvider(
        name="generic",
        detect_block=_default_detect,
        attempt_grace=_default_grace,
        attempt_fallback=_default_fallback,
        validate_clearance=_default_validate_clearance,
        attempt_last_resort_manual=_default_attempt_last_resort_manual,
        supports_last_resort_manual=False,
        requires_page_open_for_clearance=False,
    )


def _manual_intervention_had_no_effect(browser: Any, manual_result: Dict[str, Any]) -> bool:
    """Detect manual windows that ended without changing challenge page state."""
    if not isinstance(manual_result, dict):
        return False
    if not bool(manual_result.get("used", False)):
        return False
    manual_reason = str(manual_result.get("reason", "") or "")
    if manual_reason != "manual_window_elapsed":
        return False

    before_url = str(manual_result.get("page_url_before", "") or "").strip().lower()
    after_url = str(manual_result.get("page_url_after", "") or "").strip().lower()
    same_url = bool(before_url and after_url and before_url == after_url)
    challenge_surface_after = _is_verification_url_from_browser(browser)
    if challenge_surface_after:
        return True
    if same_url and is_verification_url(before_url):
        return True
    return False


def _deterministic_interstitial_action(reason: str) -> str:
    """Map terminal interstitial reasons to deterministic next actions for triage."""
    key = str(reason or "").strip().lower()
    if key == "blocked_interstitial_challenge_script_blocked":
        return "disable_resource_blocking_for_verification"
    if key in {
        "blocked_interstitial_manual_reissue_suspected_target_closed",
        "blocked_interstitial_reissue_suspected",
        "blocked_interstitial_reissued_after_manual",
        "blocked_interstitial_press_hold_unsuccessful",
    }:
        return "assist_retry_long_press_hold_without_reload"
    if key == "blocked_interstitial_manual_target_closed":
        return "recover_live_page_then_retry_manual"
    if key == "blocked_interstitial_manual_interrupted":
        return "resume_manual_window_or_abort"
    if key == "blocked_interstitial_manual_no_effect":
        return "run_assist_follow_up_before_next_reload"
    if key == "blocked_interstitial_manual_exception":
        return "capture_debug_and_retry_bounded"
    return "retry_bounded_interstitial_flow"


def _is_route_bound_skyscanner_url(url: str) -> bool:
    u = str(url or "").strip().lower()
    if not u:
        return False
    return "/transport/flights/" in u and (not is_verification_url(u)) and (not is_skyscanner_px_captcha_url(u))


def _looks_like_skyscanner_results_snapshot(
    *,
    html_text: str,
    manual_result: Dict[str, Any],
    fallback_result: Dict[str, Any],
) -> bool:
    html = str(html_text or "")
    if len(html) < 6000:
        return False
    html_lower = html.lower()
    if "press & hold" in html_lower or "enable javascript and cookies" in html_lower:
        return False
    route_url_candidates = [
        str((manual_result or {}).get("page_url_after", "") or ""),
        str((manual_result or {}).get("page_url_before", "") or ""),
        str((fallback_result or {}).get("resolved_target_url", "") or ""),
        str((fallback_result or {}).get("route_url_after_reload", "") or ""),
        str((fallback_result or {}).get("expected_route_url", "") or ""),
    ]
    route_context_present = any(_is_route_bound_skyscanner_url(candidate) for candidate in route_url_candidates)
    if not route_context_present:
        return False
    strong_markers = [
        '"pagename":"day-view"',
        "updatedpriceamount",
        '"ancillary":"airli"',
        '"entityids"',
        "/transport/flights/",
    ]
    marker_hits = sum(1 for marker in strong_markers if marker in html_lower)
    return marker_hits >= 2


def _select_skyscanner_results_snapshot_html(
    *,
    attempt_html_probe: str,
    grace_probe: Dict[str, Any],
    fallback_result: Dict[str, Any],
    manual_result: Dict[str, Any],
) -> str:
    candidates = [
        str(attempt_html_probe or ""),
        str((grace_probe or {}).get("html", "") or ""),
        str((fallback_result or {}).get("html", "") or ""),
    ]
    for html_candidate in candidates:
        if _looks_like_skyscanner_results_snapshot(
            html_text=html_candidate,
            manual_result=manual_result,
            fallback_result=fallback_result,
        ):
            return html_candidate
    return ""


def run_attempt_precheck_and_interstitial_gate(
    *,
    browser: Any,
    site_key: str,
    url: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str],
    trip_type: str,
    is_domestic: Optional[bool],
    max_transit: Optional[int],
    attempt: int,
    max_retries: int,
    max_turns: int,
    human_mimic: bool,
    plan: Any,
    last_error: Optional[Exception],
    scenario_run_id: str,
    wall_clock_cap_exhausted_fn: Callable[[], bool],
    budget_almost_exhausted_fn: Callable[[], bool],
    budget_remaining_sec_fn: Callable[[], Optional[float]],
    get_threshold_fn: Callable[[str, Any], Any],
    detect_site_interstitial_block_fn: Callable[[str, str], Dict[str, Any]],
    attempt_skyscanner_interstitial_grace_fn: Callable[..., Dict[str, Any]],
    attempt_skyscanner_interstitial_fallback_reload_fn: Callable[..., Dict[str, Any]],
    write_progress_snapshot_fn: Callable[..., None],
    write_debug_snapshot_fn: Callable[..., None],
    write_html_snapshot_fn: Callable[..., None],
    write_image_snapshot_fn: Callable[..., None],
    write_json_artifact_snapshot_fn: Callable[..., None],
    scenario_return_fn: Callable[..., str],
    logger: Any,
) -> Dict[str, Any]:
    """Run attempt-start guards and blocked interstitial handling."""
    def _safe_browser_content() -> str:
        try:
            return str(browser.content() or "")
        except Exception:
            return ""

    if wall_clock_cap_exhausted_fn():
        return {
            "should_return": True,
            "result_html": scenario_return_fn(
                _safe_browser_content(),
                ready=False,
                reason="scenario_wall_clock_cap",
                scope_class="unknown",
                route_bound=False,
                route_support="none",
            ),
            "last_error": last_error,
            "attempt_html_probe": "",
        }
    if budget_almost_exhausted_fn():
        remaining = budget_remaining_sec_fn()
        logger.warning(
            "scenario.budget.soft_stop stage=attempt_start site=%s attempt=%s/%s remaining_s=%.2f",
            site_key,
            attempt + 1,
            max_retries,
            remaining if remaining is not None else -1.0,
        )
        return {
            "should_return": True,
            "result_html": scenario_return_fn(
                _safe_browser_content(),
                ready=False,
                reason="scenario_budget_soft_stop",
                scope_class="unknown",
                route_bound=False,
                route_support="none",
            ),
            "last_error": last_error,
            "attempt_html_probe": "",
        }
    logger.info("scenario.attempt.start attempt=%s/%s", attempt + 1, max_retries)
    write_progress_snapshot_fn(
        stage="attempt_start",
        run_id=scenario_run_id,
        site_key=site_key,
        url=url,
        attempt=attempt + 1,
        max_retries=max_retries,
        max_turns=max_turns,
    )

    try:
        attempt_html_probe = browser.content() or ""
    except Exception as html_probe_exc:
        logger.warning(
            "scenario.attempt.pre_probe_html_failed site=%s attempt=%s error=%s",
            site_key,
            attempt + 1,
            html_probe_exc,
        )
        attempt_html_probe = ""

    provider = _build_challenge_provider(
        site_key=site_key,
        detect_site_interstitial_block_fn=detect_site_interstitial_block_fn,
        attempt_skyscanner_interstitial_grace_fn=attempt_skyscanner_interstitial_grace_fn,
        attempt_skyscanner_interstitial_fallback_reload_fn=attempt_skyscanner_interstitial_fallback_reload_fn,
    )
    hard_block: Dict[str, Any] = provider.detect_block(attempt_html_probe, browser)

    if not hard_block:
        return {
            "should_return": False,
            "attempt_html_probe": attempt_html_probe,
            "last_error": last_error,
        }

    runtime_diag: Dict[str, Any] = {}
    if hasattr(browser, "collect_runtime_diagnostics"):
        try:
            runtime_diag = browser.collect_runtime_diagnostics(
                selectors=manual_intervention_diagnostic_selectors(site_key=site_key)
                + [
                    "input[name*='origin']",
                    "input[name*='from']",
                ]
            )
        except Exception:
            runtime_diag = {}

    hard_block_reason = str(hard_block.get("reason", "blocked_interstitial_page") or "blocked_interstitial_page")
    grace_probe = provider.attempt_grace(
        browser,
        hard_block,
        human_mimic=bool(human_mimic),
        get_threshold_fn=get_threshold_fn,
    )
    fallback_result: Dict[str, Any] = {"used": False, "attempted": False, "cleared": False}

    if bool(grace_probe.get("used")) and not bool(grace_probe.get("cleared")):
        manual_result = (grace_probe or {}).get("manual_intervention", {})
        manual_used = bool((manual_result or {}).get("used", False))
        manual_reason = str((manual_result or {}).get("reason", "") or "")
        manual_error = str((manual_result or {}).get("error", "") or "")
        manual_disrupted = (
            manual_reason == "manual_intervention_target_closed"
            or manual_error == "TargetClosedError"
        )
        manual_enabled = bool(getattr(browser, "allow_human_intervention", False))
        human_intervention_mode = str(
            getattr(browser, "human_intervention_mode", "assist" if manual_enabled else "off") or ""
        ).strip().lower()
        manual_no_effect = _manual_intervention_had_no_effect(browser, manual_result)
        assist_follow_up_allowed = (
            manual_used
            and manual_no_effect
            and human_intervention_mode == "assist"
            and not manual_disrupted
        )
        if manual_used:
            if assist_follow_up_allowed:
                logger.info(
                    "scenario.attempt.blocked_interstitial_fallback_assist_follow_up site=%s attempt=%s/%s mode=%s manual_reason=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    human_intervention_mode,
                    manual_reason,
                )
                fallback_result = provider.attempt_fallback(
                    browser,
                    url,
                    grace_probe,
                    human_mimic=bool(human_mimic),
                    get_threshold_fn=get_threshold_fn,
                )
                if bool(fallback_result.get("cleared")):
                    grace_probe = fallback_result
            else:
                if manual_disrupted:
                    skip_reason = "fallback_skipped_manual_disrupted"
                elif manual_no_effect:
                    skip_reason = "fallback_skipped_manual_no_effect"
                else:
                    skip_reason = "fallback_skipped_manual_intervention_used"
                fallback_result = {
                    "used": False,
                    "attempted": False,
                    "cleared": False,
                    "reason": skip_reason,
                    "manual_intervention": manual_result if isinstance(manual_result, dict) else {},
                    "manual_no_effect": bool(manual_no_effect),
                    "manual_disrupted": bool(manual_disrupted),
                    "human_intervention_mode": human_intervention_mode,
                }
                logger.info(
                    "scenario.attempt.blocked_interstitial_fallback_skipped site=%s attempt=%s/%s reason=%s mode=%s manual_reason=%s manual_error=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    skip_reason,
                    human_intervention_mode,
                    manual_reason,
                    manual_error,
                )
        else:
            fallback_result = provider.attempt_fallback(
                browser,
                url,
                grace_probe,
                human_mimic=bool(human_mimic),
                get_threshold_fn=get_threshold_fn,
            )
            if bool(fallback_result.get("cleared")):
                grace_probe = fallback_result

    manual_last_result: Dict[str, Any] = {}
    if (
        bool(provider.supports_last_resort_manual)
        and bool(grace_probe.get("used"))
        and not bool(grace_probe.get("cleared"))
        and not bool(getattr(browser, "allow_human_intervention", False))
        and bool(getattr(browser, "last_resort_manual_when_disabled", False))
    ):
        try:
            last_resort_out = provider.attempt_last_resort_manual(
                browser=browser,
                grace_probe=grace_probe,
                fallback_result=fallback_result,
                get_threshold_fn=get_threshold_fn,
            )
            if bool(last_resort_out.get("attempted", False)):
                if str(last_resort_out.get("attempt_html_probe", "") or "").strip():
                    attempt_html_probe = str(last_resort_out.get("attempt_html_probe", "") or "")
                if isinstance(last_resort_out.get("grace_probe"), dict):
                    grace_probe = dict(last_resort_out.get("grace_probe", {}) or {})
                if isinstance(last_resort_out.get("fallback_result"), dict):
                    fallback_result = dict(last_resort_out.get("fallback_result", {}) or {})
                manual_last_result = (
                    dict(last_resort_out.get("manual_last_result", {}) or {})
                    if isinstance(last_resort_out.get("manual_last_result"), dict)
                    else {}
                )
                logger.info(
                    "scenario.attempt.blocked_interstitial_last_resort_manual site=%s attempt=%s/%s used=%s reason=%s cleared=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    bool((manual_last_result or {}).get("used", False)),
                    str((manual_last_result or {}).get("reason", "")),
                    bool(last_resort_out.get("cleared", False)),
                )
            else:
                logger.info(
                    "scenario.attempt.blocked_interstitial_last_resort_manual_skipped site=%s attempt=%s/%s provider=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    str(provider.name),
                )
        except Exception as last_resort_exc:
            logger.warning(
                "scenario.attempt.blocked_interstitial_last_resort_manual_failed site=%s attempt=%s/%s error=%s",
                site_key,
                attempt + 1,
                max_retries,
                str(type(last_resort_exc).__name__),
            )

    # Last-resort manual can clear challenge while immediate snapshot is still stale.
    # Run a bounded live re-probe before terminally classifying as blocked.
    if (
        bool(provider.supports_last_resort_manual)
        and bool(grace_probe.get("used"))
        and not bool(grace_probe.get("cleared"))
        and str((manual_last_result or {}).get("reason", "") or "").strip().lower()
        == "manual_challenge_cleared"
        and _is_browser_page_open(browser)
    ):
        settle_ms = max(
            250,
            min(2500, int(get_threshold_fn("skyscanner_interstitial_clearance_interval_ms", 1100))),
        )
        try:
            page = getattr(browser, "page", None)
            if page is not None and hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(settle_ms)
        except Exception:
            pass
        try:
            refreshed_html = str(browser.content() or "")
        except Exception:
            refreshed_html = ""
        if str(refreshed_html or "").strip():
            attempt_html_probe = refreshed_html
            clearance_probe_after_manual: Dict[str, Any] = provider.validate_clearance(
                browser=browser,
                html_text=attempt_html_probe,
                get_threshold_fn=get_threshold_fn,
                grace_probe=grace_probe,
            )
            grace_probe["clearance_probe_after_last_resort"] = dict(clearance_probe_after_manual)
            if bool(clearance_probe_after_manual.get("cleared", False)):
                grace_probe["cleared"] = True
                grace_probe["reason"] = "manual_challenge_cleared_validated"
                grace_probe["html"] = str(
                    clearance_probe_after_manual.get("html", "") or attempt_html_probe
                )
                logger.info(
                    "scenario.attempt.blocked_interstitial_last_resort_manual_validated site=%s attempt=%s/%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                )
            else:
                grace_probe["reason"] = str(
                    clearance_probe_after_manual.get("reason", grace_probe.get("reason", ""))
                )
                logger.warning(
                    "scenario.attempt.blocked_interstitial_last_resort_manual_reprobe_failed site=%s attempt=%s/%s reason=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    str(grace_probe.get("reason", "")),
                )

    if bool(grace_probe.get("used")) and bool(grace_probe.get("cleared")):
        attempt_html_probe = str(grace_probe.get("html", "") or attempt_html_probe)
        clearance_probe: Dict[str, Any] = provider.validate_clearance(
            browser=browser,
            html_text=attempt_html_probe,
            get_threshold_fn=get_threshold_fn,
            grace_probe=grace_probe,
        )
        grace_probe["clearance_probe"] = dict(clearance_probe)
        if not bool(clearance_probe.get("cleared", False)):
            grace_probe["cleared"] = False
            grace_probe["reason"] = str(clearance_probe.get("reason", hard_block_reason))
            attempt_html_probe = str(clearance_probe.get("html", "") or attempt_html_probe)
            if isinstance(clearance_probe.get("runtime_diag"), dict) and clearance_probe.get("runtime_diag"):
                runtime_diag = dict(clearance_probe.get("runtime_diag"))
            logger.warning(
                "scenario.attempt.blocked_interstitial_clearance_rejected site=%s attempt=%s/%s reason=%s probes=%s",
                site_key,
                attempt + 1,
                max_retries,
                str(grace_probe.get("reason", "")),
                len(list((clearance_probe or {}).get("probes", []) or [])),
            )
            # Skyscanner route-continuity re-arm:
            # if challenge clears but lands on generic /flights page, perform one bounded
            # navigation back to expected route and re-validate clearance.
            clearance_reason = str(grace_probe.get("reason", "") or "").strip().lower()
            if (
                _normalize_site_key(site_key) == "skyscanner"
                and clearance_reason in {
                    "blocked_interstitial_route_context_lost",
                    "blocked_interstitial_route_context_mismatch",
                }
            ):
                evidence = (
                    clearance_probe.get("evidence", {})
                    if isinstance(clearance_probe.get("evidence"), dict)
                    else {}
                )
                expected_route_url = str(
                    (grace_probe.get("expected_route_url", "") or "")
                    or (fallback_result.get("expected_route_url", "") or "")
                    or (evidence.get("route.expected", "") or "")
                ).strip()
                if expected_route_url and _is_browser_page_open(browser):
                    rearm_ok = False
                    try:
                        if hasattr(browser, "goto"):
                            browser.goto(expected_route_url)
                            rearm_ok = True
                        else:
                            page = getattr(browser, "page", None)
                            if page is not None and hasattr(page, "goto"):
                                page.goto(
                                    expected_route_url,
                                    wait_until="domcontentloaded",
                                    timeout=30000,
                                )
                                rearm_ok = True
                    except Exception as rearm_nav_exc:
                        logger.warning(
                            "scenario.attempt.blocked_interstitial_route_rearm_nav_failed site=%s attempt=%s/%s url=%s error=%s",
                            site_key,
                            attempt + 1,
                            max_retries,
                            expected_route_url[:160],
                            str(type(rearm_nav_exc).__name__),
                        )
                    if rearm_ok:
                        settle_ms = max(
                            250,
                            min(
                                2500,
                                int(
                                    get_threshold_fn(
                                        "skyscanner_interstitial_clearance_interval_ms",
                                        1100,
                                    )
                                ),
                            ),
                        )
                        page = getattr(browser, "page", None)
                        try:
                            if page is not None and hasattr(page, "wait_for_timeout"):
                                page.wait_for_timeout(settle_ms)
                        except Exception:
                            pass
                        try:
                            rearm_html = str(browser.content() or "")
                        except Exception:
                            rearm_html = ""
                        if rearm_html:
                            route_rearm_probe = provider.validate_clearance(
                                browser=browser,
                                html_text=rearm_html,
                                get_threshold_fn=get_threshold_fn,
                                grace_probe=dict(grace_probe),
                            )
                            grace_probe["route_context_rearm_probe"] = dict(route_rearm_probe)
                            if bool(route_rearm_probe.get("cleared", False)):
                                grace_probe["cleared"] = True
                                grace_probe["reason"] = "route_context_rearmed"
                                grace_probe["html"] = str(
                                    route_rearm_probe.get("html", "") or rearm_html
                                )
                                attempt_html_probe = str(grace_probe.get("html", "") or rearm_html)
                                if isinstance(route_rearm_probe.get("runtime_diag"), dict) and route_rearm_probe.get(
                                    "runtime_diag"
                                ):
                                    runtime_diag = dict(route_rearm_probe.get("runtime_diag"))
                                logger.info(
                                    "scenario.attempt.blocked_interstitial_route_rearm_cleared site=%s attempt=%s/%s url=%s",
                                    site_key,
                                    attempt + 1,
                                    max_retries,
                                    expected_route_url[:160],
                                )
                            else:
                                grace_probe["reason"] = str(
                                    route_rearm_probe.get("reason", grace_probe.get("reason", ""))
                                )
                                attempt_html_probe = str(
                                    route_rearm_probe.get("html", "") or attempt_html_probe
                                )
                                logger.warning(
                                    "scenario.attempt.blocked_interstitial_route_rearm_rejected site=%s attempt=%s/%s reason=%s",
                                    site_key,
                                    attempt + 1,
                                    max_retries,
                                    str(grace_probe.get("reason", "")),
                                )
        if bool(grace_probe.get("cleared")):
            if not str(attempt_html_probe or "").strip() or (
                bool(provider.requires_page_open_for_clearance)
                and not _is_browser_page_open(browser)
            ):
                grace_probe["cleared"] = False
                grace_probe["reason"] = "grace_probe_unreliable_snapshot"
                logger.warning(
                    "scenario.attempt.blocked_interstitial_grace_clear_rejected site=%s attempt=%s/%s reason=%s html_len=%s page_open=%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                    str(grace_probe.get("reason", "")),
                    len(str(attempt_html_probe or "")),
                    _is_browser_page_open(browser),
                )
            else:
                post_clear_url = ""
                try:
                    post_clear_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                except Exception:
                    post_clear_url = ""
                post_clear_url_lower = post_clear_url.strip().lower()
                manual_used_any = bool((manual_last_result or {}).get("used", False)) or bool(
                    ((grace_probe or {}).get("manual_intervention", {}) or {}).get("used", False)
                )
                force_home_rebind = bool(
                    _normalize_site_key(site_key) == "skyscanner"
                    and (
                        "/transport/flights/" in post_clear_url_lower
                        or str((grace_probe or {}).get("reason", "") or "").strip().lower().startswith(
                            "manual_"
                        )
                        or manual_used_any
                    )
                )
                logger.info(
                    "scenario.attempt.blocked_interstitial_grace_cleared site=%s attempt=%s/%s",
                    site_key,
                    attempt + 1,
                    max_retries,
                )
                return {
                    "should_return": False,
                    "attempt_html_probe": attempt_html_probe,
                    "last_error": last_error,
                    "post_interstitial_rebind_home": force_home_rebind,
                    "post_interstitial_clear_url": post_clear_url,
                }

    terminal_reason = hard_block_reason
    manual_result_terminal = (grace_probe or {}).get("manual_intervention", {})
    manual_terminal_used = bool((manual_result_terminal or {}).get("used", False)) if isinstance(
        manual_result_terminal, dict
    ) else False
    if (not isinstance(manual_result_terminal, dict)) or (not manual_result_terminal) or (not manual_terminal_used):
        if isinstance(manual_last_result, dict) and manual_last_result:
            manual_result_terminal = manual_last_result
        else:
            manual_result_terminal = (fallback_result or {}).get("last_resort_manual_intervention", {})
            if not isinstance(manual_result_terminal, dict):
                manual_result_terminal = {}
    manual_reason_terminal = str((manual_result_terminal or {}).get("reason", "") or "")
    manual_error_terminal = str((manual_result_terminal or {}).get("error", "") or "")
    manual_no_effect_terminal = _manual_intervention_had_no_effect(browser, manual_result_terminal)
    grace_reason_terminal = str((grace_probe or {}).get("reason", "") or "")
    fallback_reason_terminal = str((fallback_result or {}).get("reason", "") or "")
    press_hold_executed_terminal = bool(
        (grace_probe or {}).get("press_hold_executed", False)
        or (fallback_result or {}).get("press_hold_executed", False)
    )
    press_hold_success_terminal = bool(
        (grace_probe or {}).get("press_hold_success", False)
        or (fallback_result or {}).get("press_hold_success", False)
    )
    if manual_reason_terminal == "manual_intervention_interrupted":
        terminal_reason = "blocked_interstitial_manual_interrupted"
    elif manual_reason_terminal == "manual_intervention_reissue_suspected_target_closed":
        terminal_reason = "blocked_interstitial_manual_reissue_suspected_target_closed"
    elif (
        manual_reason_terminal == "manual_intervention_target_closed"
        or manual_error_terminal == "TargetClosedError"
    ):
        terminal_reason = "blocked_interstitial_manual_target_closed"
    elif manual_no_effect_terminal and not bool((fallback_result or {}).get("attempted", False)):
        terminal_reason = "blocked_interstitial_manual_no_effect"
    elif manual_reason_terminal == "manual_intervention_exception":
        terminal_reason = "blocked_interstitial_manual_exception"
    elif grace_reason_terminal in {
        "blocked_interstitial_challenge_script_blocked",
        "blocked_interstitial_captcha_reissued",
        "blocked_interstitial_reissue_suspected",
        "blocked_interstitial_reissued_after_manual",
        "blocked_interstitial_press_hold_unsuccessful",
    } or fallback_reason_terminal in {
        "blocked_interstitial_challenge_script_blocked",
        "fallback_press_hold_unsuccessful",
        "blocked_interstitial_captcha_reissued",
        "blocked_interstitial_reissue_suspected",
        "blocked_interstitial_reissued_after_manual",
    }:
        terminal_reason = (
            "blocked_interstitial_challenge_script_blocked"
            if grace_reason_terminal == "blocked_interstitial_challenge_script_blocked"
            or fallback_reason_terminal == "blocked_interstitial_challenge_script_blocked"
            else "blocked_interstitial_reissued_after_manual"
        )
    elif press_hold_executed_terminal and not press_hold_success_terminal:
        terminal_reason = "blocked_interstitial_press_hold_unsuccessful"
    snapshot_salvage_html = _select_skyscanner_results_snapshot_html(
        attempt_html_probe=attempt_html_probe,
        grace_probe=grace_probe if isinstance(grace_probe, dict) else {},
        fallback_result=fallback_result if isinstance(fallback_result, dict) else {},
        manual_result=manual_result_terminal if isinstance(manual_result_terminal, dict) else {},
    )
    if (
        _normalize_site_key(site_key) == "skyscanner"
        and terminal_reason in {
            "blocked_interstitial_manual_target_closed",
            "blocked_interstitial_manual_reissue_suspected_target_closed",
        }
        and bool(snapshot_salvage_html)
    ):
        logger.info(
            "scenario.attempt.blocked_interstitial_snapshot_salvage site=%s attempt=%s/%s reason=%s action=return_snapshot_results",
            site_key,
            attempt + 1,
            max_retries,
            terminal_reason,
        )
        return {
            "should_return": True,
            "result_html": scenario_return_fn(
                snapshot_salvage_html,
                ready=True,
                reason="skyscanner_results_snapshot_after_manual_target_closed",
                scope_class="results",
                route_bound=True,
                route_support="strong",
            ),
            "last_error": last_error,
            "attempt_html_probe": snapshot_salvage_html,
        }
    recommended_action = _deterministic_interstitial_action(terminal_reason)
    last_error = RuntimeError(terminal_reason)
    logger.warning(
        "scenario.attempt.blocked_interstitial site=%s attempt=%s/%s reason=%s action=%s html_len=%s tokens=%s",
        site_key,
        attempt + 1,
        max_retries,
        terminal_reason,
        recommended_action,
        (hard_block.get("evidence", {}) or {}).get("html.length", -1),
        ",".join(((hard_block.get("evidence", {}) or {}).get("ui.token_hits", []) or [])[:4]),
    )
    write_debug_snapshot_fn(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "stage": "blocked_interstitial",
            "site_key": site_key,
            "url": url,
            "origin": origin,
            "dest": dest,
            "depart": depart,
            "return_date": return_date,
            "trip_type": trip_type,
            "is_domestic": is_domestic,
            "max_transit": max_transit,
            "attempt": attempt + 1,
            "max_retries": max_retries,
            "max_turns": max_turns,
            "error": terminal_reason,
            "recommended_action": recommended_action,
            "exception_type": type(last_error).__name__,
            "blocked_interstitial": dict(hard_block),
            "grace_probe": dict(grace_probe) if isinstance(grace_probe, dict) else {},
            "fallback_probe": dict(fallback_result) if isinstance(fallback_result, dict) else {},
            "runtime_diag": dict(runtime_diag) if isinstance(runtime_diag, dict) else {},
            "plan": plan,
        },
        run_id=scenario_run_id,
    )
    try:
        write_html_snapshot_fn(site_key, attempt_html_probe, stage="blocked_interstitial", run_id=scenario_run_id)
        grace_html = str((grace_probe or {}).get("html", "") or "")
        if grace_html:
            write_html_snapshot_fn(site_key, grace_html, stage="grace_probe", run_id=scenario_run_id)
        write_json_artifact_snapshot_fn(
            scenario_run_id,
            f"{site_key}_interstitial_grace_debug.json",
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "site_key": site_key,
                "attempt": attempt + 1,
                "reason": terminal_reason,
                "recommended_action": recommended_action,
                "hard_block": dict(hard_block),
                "grace_probe": dict(grace_probe) if isinstance(grace_probe, dict) else {},
                "fallback_probe": dict(fallback_result) if isinstance(fallback_result, dict) else {},
                "runtime_diag": dict(runtime_diag) if isinstance(runtime_diag, dict) else {},
                "grace_html_len": len(grace_html),
            },
        )
        if isinstance(runtime_diag, dict) and runtime_diag:
            write_json_artifact_snapshot_fn(
                scenario_run_id,
                f"dom_probe/{site_key}_blocked_interstitial_attempt_{attempt + 1}.json",
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "site_key": site_key,
                    "attempt": attempt + 1,
                    "stage": "blocked_interstitial",
                    "diag": runtime_diag,
                },
            )
        write_json_artifact_snapshot_fn(
            scenario_run_id,
            f"trace/graph_transition_attempt_{attempt + 1}_blocked_interstitial.json",
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "from_stage": "attempt_start",
                "to_stage": "blocked_interstitial",
                "site_key": site_key,
                "attempt": attempt + 1,
                "reason": terminal_reason,
                "recommended_action": recommended_action,
                "manual_intervention_reason": str(((grace_probe or {}).get("manual_intervention", {}) or {}).get("reason", "")),
                "fallback_reason": str((fallback_result or {}).get("reason", "")),
                "clearance_reason": str(((grace_probe or {}).get("clearance_probe", {}) or {}).get("reason", "")),
            },
        )
        write_image_snapshot_fn(browser, site_key, stage="blocked_interstitial", run_id=scenario_run_id)
    except Exception as interstitial_debug_exc:
        logger.warning(
            "scenario.blocked_interstitial.debug_artifact_failed site=%s run_id=%s error=%s",
            site_key,
            scenario_run_id,
            interstitial_debug_exc,
        )
    return {
        "should_return": True,
        "result_html": scenario_return_fn(
            attempt_html_probe,
            ready=False,
            reason=terminal_reason,
            scope_class=str(hard_block.get("page_kind", "unknown") or "unknown"),
            route_bound=False,
            route_support="none",
        ),
        "last_error": last_error,
        "attempt_html_probe": attempt_html_probe,
    }
