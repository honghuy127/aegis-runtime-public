"""Skyscanner interstitial detection and grace handling."""

import base64
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse
from core.browser.manual_intervention_policy import manual_intervention_diagnostic_selectors
from core.browser.verification_challenges import classify_verification_challenge_multiclass
from utils.thresholds import get_threshold
from utils.logging import get_logger

log = get_logger(__name__)


def _is_skyscanner_transport_results_url(url: str) -> bool:
    url_l = str(url or "").strip().lower()
    return "/transport/flights/" in url_l and "captcha-v2/index.html" not in url_l


def _decode_px_challenge_target_url(raw_url: str, *, fallback_url: str = "") -> str:
    """Decode Skyscanner PX challenge `url=` payload into a concrete page URL."""
    current = str(raw_url or "").strip()
    if not current:
        return str(fallback_url or "").strip()
    try:
        parsed = urlparse(current)
    except Exception:
        return str(fallback_url or "").strip()
    params = parse_qs(parsed.query or "")
    encoded = ""
    values = params.get("url") or []
    if values:
        encoded = str(values[0] or "").strip()
    if not encoded:
        return str(fallback_url or "").strip()
    padded = encoded + ("=" * ((4 - (len(encoded) % 4)) % 4))
    decoded = ""
    try:
        decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="ignore")
    except Exception:
        return str(fallback_url or "").strip()
    decoded = str(decoded or "").strip()
    if not decoded:
        return str(fallback_url or "").strip()
    if decoded.startswith("http://") or decoded.startswith("https://"):
        decoded_lower = decoded.rstrip("/").lower()
        if decoded_lower in {"https://www.skyscanner.com", "https://skyscanner.com"}:
            return str(fallback_url or "").strip()
        return decoded
    if decoded.startswith("/"):
        # Captcha payload often encodes "/" (Lw==). Returning homepage root increases
        # route-context drift after challenge clear. Prefer caller fallback in that case.
        if decoded.strip() == "/":
            return str(fallback_url or "").strip()
        return f"https://www.skyscanner.com{decoded}"
    return str(fallback_url or "").strip()


def _derive_skyscanner_challenge_target_url(browser: Any, *, fallback_url: str = "") -> str:
    """Prefer route-preserving target URL when currently on captcha page."""
    page = getattr(browser, "page", None)
    current_url = ""
    if page is not None:
        try:
            current_url = str(getattr(page, "url", "") or "")
        except Exception:
            current_url = ""
    decoded = _decode_px_challenge_target_url(current_url, fallback_url=fallback_url)
    if decoded:
        return decoded
    return str(fallback_url or "").strip()


def _captcha_manual_wait_sec() -> int:
    """Bounded manual window for captcha pages to reduce churn/reload exposure."""
    return max(10, min(120, int(get_threshold("skyscanner_captcha_manual_wait_sec", 75) or 75)))


def _is_browser_page_open(browser: Any) -> bool:
    """Best-effort page liveness check to avoid reload loops on closed targets."""
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        if hasattr(page, "is_closed") and page.is_closed():
            return False
    except Exception:
        return False
    context = getattr(page, "context", None)
    if context is not None:
        try:
            if hasattr(context, "pages") and len(context.pages) <= 0:
                return False
        except Exception:
            return False
    return True


def _is_skyscanner_captcha_url(browser: Any) -> bool:
    """Detect known Skyscanner captcha URL surfaces independent of HTML tokens."""
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "").lower()
    except Exception:
        return False
    if not current_url:
        return False
    return (
        "/sttc/px/captcha-v2/" in current_url
        or "/px/captcha" in current_url
        or "captcha-v2/index.html" in current_url
    )


def probe_skyscanner_shadow_challenge_state(browser: Any) -> Dict[str, Any]:
    """Probe hidden PX/challenge runtime when results URL renders as a white shell."""
    out: Dict[str, Any] = {
        "suspected": False,
        "reason": "",
        "px_signature_prefix": "",
        "px_iframe_count": 0,
        "px_iframe_visible_count": 0,
        "search_form_visible": False,
        "failed_challenge_hosts": 0,
        "failed_challenge_hosts_blocked_by_client": 0,
        "runtime_diag": {},
    }
    if not hasattr(browser, "collect_runtime_diagnostics"):
        return out

    try:
        runtime_diag = browser.collect_runtime_diagnostics(
            selectors=manual_intervention_diagnostic_selectors(site_key="skyscanner")
            + [
                "#originInput-input",
                "#destinationInput-input",
                "input[name='originInput-search']",
                "input[name='destinationInput-search']",
                "#px-captcha",
                "iframe[src*='px-cloud.net']",
                "iframe[title*='Human verification' i]",
            ]
        )
    except Exception:
        return out

    if not isinstance(runtime_diag, dict):
        return out
    out["runtime_diag"] = runtime_diag

    selector_probe = runtime_diag.get("selector_probe", [])
    search_form_visible = False
    if isinstance(selector_probe, list):
        for probe in selector_probe[:20]:
            if not isinstance(probe, dict):
                continue
            if int(probe.get("count", 0) or 0) <= 0 or not bool(probe.get("visible", False)):
                continue
            selector = str(probe.get("selector", "") or "").lower()
            if (
                "origininput-input" in selector
                or "destinationinput-input" in selector
                or "origininput-search" in selector
                or "destinationinput-search" in selector
            ):
                search_form_visible = True
                break

    dom_probe = runtime_diag.get("dom_probe", {})
    if isinstance(dom_probe, dict):
        out["px_signature_prefix"] = str(dom_probe.get("px_challenge_signature", "") or "")[:32]
        out["px_iframe_count"] = int(dom_probe.get("px_iframe_count", 0) or 0)
        out["px_iframe_visible_count"] = int(dom_probe.get("px_iframe_visible_count", 0) or 0)
    out["search_form_visible"] = bool(search_form_visible)

    network_window = {}
    network = runtime_diag.get("network", {})
    if isinstance(network, dict) and isinstance(network.get("window"), dict):
        network_window = network.get("window", {})
    out["failed_challenge_hosts"] = int(network_window.get("failed_challenge_hosts", 0) or 0)
    out["failed_challenge_hosts_blocked_by_client"] = int(
        network_window.get("failed_challenge_hosts_blocked_by_client", 0) or 0
    )

    px_signature = str(dom_probe.get("px_challenge_signature", "") or "") if isinstance(dom_probe, dict) else ""
    px_iframe_count = int(out.get("px_iframe_count", 0) or 0)
    px_iframe_visible_count = int(out.get("px_iframe_visible_count", 0) or 0)
    failed_challenge_hosts = int(out.get("failed_challenge_hosts", 0) or 0)

    if (
        px_iframe_count > 0
        and px_iframe_visible_count <= 0
        and bool(px_signature)
        and not bool(search_form_visible)
    ):
        out["suspected"] = True
        out["reason"] = "px_runtime_shadow_shell"
    elif (
        px_iframe_count > 0
        and failed_challenge_hosts > 0
        and not bool(search_form_visible)
    ):
        out["suspected"] = True
        out["reason"] = "px_runtime_failed_challenge_hosts"
    return out


def _should_use_passive_px_recovery(meta: Dict[str, Any]) -> bool:
    """Identify PerimeterX states where passive behavioral dwell is more useful than reload churn."""
    if not isinstance(meta, dict):
        return False
    if bool(meta.get("press_hold_executed", False)):
        return False
    if bool(meta.get("vision_guided_press_executed", False)):
        return False
    if bool(meta.get("px_container_hold_executed", False)):
        return False
    attempts = int(meta.get("press_hold_probe_attempts", 0) or 0)
    if attempts <= 0:
        return False
    probe_list = meta.get("press_hold_probes", [])
    last_probe = probe_list[-1] if isinstance(probe_list, list) and probe_list else {}
    if not isinstance(last_probe, dict):
        last_probe = {}
    px_shell_seen = bool(last_probe.get("px_shell_present", False)) or bool(meta.get("px_shell_nudged", False))
    if not px_shell_seen:
        return False
    if int(last_probe.get("px_iframe_visible", 0) or 0) > 0:
        return False
    return True


def _extract_press_hold_outcome(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize press-and-hold execution fields from grace metadata."""
    if not isinstance(meta, dict):
        return {
            "press_hold_executed": False,
            "press_hold_success": False,
            "press_hold_success_signal": "",
        }
    return {
        "press_hold_executed": bool(meta.get("press_hold_executed", False)),
        "press_hold_success": bool(meta.get("press_hold_success", False)),
        "press_hold_success_signal": str(meta.get("press_hold_success_signal", "") or ""),
    }


def detect_skyscanner_interstitial_block(html_text: str) -> dict[str, Any]:
    """One bounded grace window for transient Skyscanner captcha pages.

    Purpose:
    - Prevent expensive selector cycling on verification-gate pages that do not expose
      the expected flight search form controls.
    - Keep retries bounded and diagnostics explicit.

    Returns empty dict when no hard block is detected.
    """
    html = str(html_text or "")
    lower = html.lower()
    if not html:
        return {}

    # Precise detector for Skyscanner verification surfaces.
    token_hits: list[str] = []
    for token in (
        "px-captcha",
        "captcha.js",
        "captcha-v2",
        "are you a person or a robot",
        "human verification challenge",
        "still having problems accessing the page",
        "cookies turned on",
        "turn cookies on",
    ):
        if token in lower:
            token_hits.append(token)

    classification = classify_verification_challenge_multiclass(
        html_text=html,
        use_vision_light=False,
    )
    protector_label = str(classification.get("protector_label", "no_protection") or "no_protection")
    solution = str(classification.get("solution", "") or "")
    is_skyscanner = "skyscanner" in lower
    is_hard_challenge = protector_label != "no_protection"
    has_explicit_challenge_tokens = len(token_hits) > 0
    has_search_form_surface = (
        "origininput-search" in lower
        or "destinationinput-search" in lower
        or "origininput-input" in lower
        or "destinationinput-input" in lower
        or "name=\"origininput-search\"" in lower
        or "name=\"destinationinput-search\"" in lower
        or "aria-label=\"from\"" in lower
        or "aria-label=\"to\"" in lower
    )
    has_route_results_surface = (
        "/transport/flights/" in lower
        and (
            "day-view" in lower
            or "updatedpriceamount" in lower
            or "itinerary" in lower
            or "search-results" in lower
        )
    )
    px_telemetry_only_surface = (
        ("client.px-cloud.net" in lower or "js.px-cloud.net" in lower)
        and not has_explicit_challenge_tokens
    )
    strong_px_surface = (
        "px-captcha" in lower
        or "captcha.js" in lower
        or "captcha-v2" in lower
        or "human verification challenge" in lower
    )

    legacy_px_gate = (
        strong_px_surface
        or (
            "are you a person or a robot" in lower
            and ("captcha" in lower or "captcha.js" in lower)
        )
        or (
            ("cookies turned on" in lower or "turn cookies on" in lower)
            and ("javascript" in lower or "cookie" in lower)
        )
    )

    # Avoid false positives on normal flights pages where PX telemetry iframe exists
    # but no challenge shell is present and route fields are available.
    if (
        protector_label == "no_protection"
        and px_telemetry_only_surface
        and has_search_form_surface
        and not has_explicit_challenge_tokens
    ):
        return {}
    if (
        protector_label == "no_protection"
        and has_search_form_surface
        and token_hits
        and all(hit == "captcha.js" for hit in token_hits)
    ):
        return {}
    # Route-bound results pages can still include anti-bot library strings
    # (for example recaptcha references) without any active challenge shell.
    if (
        has_route_results_surface
        and not has_explicit_challenge_tokens
        and not strong_px_surface
    ):
        return {}

    if (is_skyscanner or strong_px_surface) and (is_hard_challenge or legacy_px_gate):
        return {
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {
                "html.length": len(html),
                "ui.token_hits": token_hits[:6],
                "ui.site_brand_detected": True,
                "verification.classifier": {
                    "protector_label": protector_label,
                    "solution": solution,
                },
                "ui.px_telemetry_only_surface": bool(px_telemetry_only_surface),
                "ui.search_form_surface_detected": bool(has_search_form_surface),
                "ui.route_results_surface_detected": bool(has_route_results_surface),
            },
        }

    return {}


def attempt_skyscanner_interstitial_grace(
    browser,
    *,
    hard_block: Dict[str, Any],
    human_mimic: bool,
    grace_ms: int,
) -> Dict[str, Any]:
    """One bounded grace window for transient Skyscanner captcha pages.

    Args:
        browser: Browser instance with page control.
        hard_block: Block detection result dict.
        human_mimic: Whether human mimic is enabled.
        grace_ms: Grace window duration in milliseconds.

    Returns:
        Result dict with keys: used, cleared, html, reason, and probe metadata.
    """
    block_reason = str((hard_block or {}).get("reason", "") or "")
    duration_ms = max(0, int(grace_ms or 0))

    if block_reason != "blocked_interstitial_captcha":
        return {"used": False, "cleared": False, "html": "", "reason": "not_captcha"}
    if not bool(human_mimic):
        return {"used": False, "cleared": False, "html": "", "reason": "human_mimic_disabled"}
    if duration_ms < 500:
        return {"used": False, "cleared": False, "html": "", "reason": "grace_disabled"}

    def _detect_block_after_html(html_text: str) -> Dict[str, Any]:
        hard_block_local = detect_skyscanner_interstitial_block(html_text)
        if not hard_block_local and _is_skyscanner_captcha_url(browser):
            hard_block_local = {
                "reason": block_reason or "blocked_interstitial_captcha",
                "page_kind": "interstitial",
                "block_type": "captcha",
                "evidence": {"url.captcha_surface": True},
            }
        if not str(html_text or "").strip() and not _is_browser_page_open(browser):
            hard_block_local = {
                "reason": block_reason or "blocked_interstitial_captcha",
                "page_kind": "interstitial",
                "block_type": "captcha",
                "evidence": {"snapshot.unreliable": True, "snapshot.page_open": False},
            }
        return hard_block_local

    manual_intervention = {"used": False, "reason": "not_attempted"}
    intervention_mode = str(getattr(browser, "human_intervention_mode", "") or "").strip().lower()
    if intervention_mode not in {"off", "assist", "demo"}:
        intervention_mode = "assist" if bool(getattr(browser, "allow_human_intervention", False)) else "off"
    manual_first_mode = bool(getattr(browser, "allow_human_intervention", False)) and hasattr(
        browser, "allow_manual_verification_intervention"
    )
    if manual_first_mode:
        try:
            manual_wait_sec = _captcha_manual_wait_sec()
            manual_rounds = 1
            if intervention_mode == "assist":
                manual_rounds = 3
            html_after_manual = ""
            hard_block_after_manual: Dict[str, Any] = {
                "reason": block_reason or "blocked_interstitial_captcha",
            }
            for round_idx in range(manual_rounds):
                manual_intervention = browser.allow_manual_verification_intervention(
                    reason=f"skyscanner_interstitial_grace_preemptive_manual_r{round_idx + 1}",
                    wait_sec=manual_wait_sec,
                )
                if isinstance(manual_intervention, dict):
                    manual_intervention["manual_round"] = round_idx + 1
                    manual_intervention["manual_rounds_total"] = manual_rounds
                log.info(
                    "captcha.manual_intervention.result site=skyscanner stage=grace mode=manual_first intervention_mode=%s round=%s/%s used=%s reason=%s wait_sec=%s elapsed_ms=%s request=%s",
                    intervention_mode,
                    round_idx + 1,
                    manual_rounds,
                    bool((manual_intervention or {}).get("used", False)),
                    str((manual_intervention or {}).get("reason", "")),
                    int((manual_intervention or {}).get("wait_sec", 0) or 0),
                    int((manual_intervention or {}).get("elapsed_ms", 0) or 0),
                    str((manual_intervention or {}).get("requested_reason", "")),
                )
                html_after_manual = ""
                try:
                    html_after_manual = str(browser.content() or "")
                except Exception as post_manual_exc:
                    manual_dict = dict(manual_intervention) if isinstance(manual_intervention, dict) else {}
                    manual_dict["post_check_error"] = str(type(post_manual_exc).__name__)
                    manual_reason = str(manual_dict.get("reason", "") or "")
                    manual_error = str(manual_dict.get("error", "") or "")
                    if (
                        (
                            manual_reason == "manual_intervention_target_closed"
                            or manual_error == "TargetClosedError"
                        )
                        and hasattr(browser, "recover_page_after_target_closed")
                    ):
                        preferred_after_close = _derive_skyscanner_challenge_target_url(
                            browser,
                            fallback_url="https://www.skyscanner.com/flights",
                        )
                        try:
                            recovery = browser.recover_page_after_target_closed(
                                preferred_url=preferred_after_close,
                            )
                        except Exception as recovery_exc:
                            recovery = {
                                "attempted": True,
                                "recovered": False,
                                "reason": "recovery_exception",
                                "error": str(type(recovery_exc).__name__),
                            }
                        manual_dict["post_manual_page_recovery"] = recovery
                        log.info(
                            "captcha.manual_intervention.recovery site=skyscanner stage=grace attempted=%s recovered=%s reason=%s",
                            bool((recovery or {}).get("attempted", False)),
                            bool((recovery or {}).get("recovered", False)),
                            str((recovery or {}).get("reason", "")),
                        )
                        if bool((recovery or {}).get("recovered", False)):
                            try:
                                html_after_manual = str(browser.content() or "")
                            except Exception as retry_exc:
                                manual_dict["post_recovery_check_error"] = str(type(retry_exc).__name__)
                    manual_intervention = manual_dict
                hard_block_after_manual = _detect_block_after_html(html_after_manual)
                if not hard_block_after_manual and str(html_after_manual or "").strip():
                    manual_automation_count = int(
                        (
                            (
                                (manual_intervention or {}).get(
                                    "automation_activity_during_manual",
                                    {},
                                )
                                or {}
                            ).get("count", 0)
                        )
                        or 0
                    )
                    if manual_automation_count > 0:
                        hard_block_after_manual = {
                            "reason": "manual_intervention_interference_detected",
                            "page_kind": "interstitial",
                            "block_type": "captcha",
                            "evidence": {
                                "manual.automation_activity_count": int(
                                    manual_automation_count
                                )
                            },
                        }
                    else:
                        break
                if intervention_mode != "assist":
                    break
            if not hard_block_after_manual and str(html_after_manual or "").strip():
                return {
                    "used": True,
                    "cleared": True,
                    "html": html_after_manual,
                    "reason": "cleared",
                    "press_hold_probe_attempts": 0,
                    "press_hold_executed": False,
                    "press_hold_success": False,
                    "press_hold_success_signal": "",
                    "press_hold_probes": [],
                    "px_shell_nudged": False,
                    "px_container_hold_attempted": False,
                    "px_container_hold_executed": False,
                    "vision_guided_press_attempted": False,
                    "vision_guided_press_executed": False,
                    "vision_guided_hint": {},
                    "manual_intervention": manual_intervention,
                    "manual_first_mode": True,
                    "automation_paused_for_manual": True,
                    "passive_behavior": {
                        "mouse_moves": 0,
                        "scroll_events": 0,
                        "js_triggers": 0,
                        "elapsed_ms": 0,
                    },
                }
            return {
                "used": True,
                "cleared": False,
                "html": html_after_manual,
                "reason": str((hard_block_after_manual or {}).get("reason", "") or "blocked_interstitial_captcha"),
                "press_hold_probe_attempts": 0,
                "press_hold_executed": False,
                "press_hold_success": False,
                "press_hold_success_signal": "",
                "press_hold_probes": [],
                "px_shell_nudged": False,
                "px_container_hold_attempted": False,
                "px_container_hold_executed": False,
                "vision_guided_press_attempted": False,
                "vision_guided_press_executed": False,
                "vision_guided_hint": {},
                "manual_intervention": manual_intervention,
                "manual_first_mode": True,
                "automation_paused_for_manual": True,
                "passive_behavior": {
                    "mouse_moves": 0,
                    "scroll_events": 0,
                    "js_triggers": 0,
                    "elapsed_ms": 0,
                },
            }
        except Exception as e:
            manual_intervention = {
                "used": True,
                "reason": "manual_intervention_exception",
                "error": str(type(e).__name__),
            }

    try:
        if hasattr(browser, "human_mimic_interstitial_grace"):
            browser.human_mimic_interstitial_grace(duration_ms=duration_ms)
        else:
            page = getattr(browser, "page", None)
            if page is not None and hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(duration_ms)
            else:
                time.sleep(duration_ms / 1000.0)
    except Exception:
        pass

    try:
        html_after = str(browser.content() or "")
    except Exception:
        html_after = ""

    hard_block_after = _detect_block_after_html(html_after)
    grace_meta = getattr(browser, "_last_interstitial_grace_meta", {})
    hold_outcome = _extract_press_hold_outcome(grace_meta if isinstance(grace_meta, dict) else {})
    press_hold_probe_attempts = (
        int(grace_meta.get("press_hold_probe_attempts", 0) or 0)
        if isinstance(grace_meta, dict)
        else 0
    )

    # Extract behavioral performance signals
    passive_behavior = {}
    if isinstance(grace_meta, dict):
        passive_behavior = grace_meta.get("passive_behavior", {})
    log.info(
        "captcha.grace.result site=skyscanner cleared=%s reason=%s probes=%d press_hold_executed=%s press_hold_success=%s success_signal=%s manual_reason=%s",
        not bool(hard_block_after),
        "cleared" if not hard_block_after else str(hard_block_after.get("reason", "")),
        int(press_hold_probe_attempts),
        bool(hold_outcome.get("press_hold_executed", False)),
        bool(hold_outcome.get("press_hold_success", False)),
        str(hold_outcome.get("press_hold_success_signal", "")),
        str((manual_intervention or {}).get("reason", "")),
    )

    return {
        "used": True,
        "cleared": not bool(hard_block_after),
        "html": html_after,
        "reason": "cleared" if not hard_block_after else str(hard_block_after.get("reason", "")),
        "press_hold_probe_attempts": int(press_hold_probe_attempts),
        "press_hold_executed": bool(hold_outcome.get("press_hold_executed", False)),
        "press_hold_success": bool(hold_outcome.get("press_hold_success", False)),
        "press_hold_success_signal": str(hold_outcome.get("press_hold_success_signal", "") or ""),
        "press_hold_probes": list(grace_meta.get("press_hold_probes", []) or [])[:6]
        if isinstance(grace_meta, dict)
        else [],
        "px_shell_nudged": bool(grace_meta.get("px_shell_nudged", False))
        if isinstance(grace_meta, dict)
        else False,
        "px_container_hold_attempted": bool(grace_meta.get("px_container_hold_attempted", False))
        if isinstance(grace_meta, dict)
        else False,
        "px_container_hold_executed": bool(grace_meta.get("px_container_hold_executed", False))
        if isinstance(grace_meta, dict)
        else False,
        "vision_guided_press_attempted": bool(grace_meta.get("vision_guided_press_attempted", False))
        if isinstance(grace_meta, dict)
        else False,
        "vision_guided_press_executed": bool(grace_meta.get("vision_guided_press_executed", False))
        if isinstance(grace_meta, dict)
        else False,
        "vision_guided_hint": dict(grace_meta.get("vision_guided_hint", {}) or {})
        if isinstance(grace_meta, dict)
        else {},
        "manual_intervention": manual_intervention,
        # NEW: Behavioral signal tracking for PerimeterX passive challenges
        "passive_behavior": {
            "mouse_moves": int(passive_behavior.get("mouse_moves", 0) or 0),
            "scroll_events": int(passive_behavior.get("scroll_events", 0) or 0),
            "js_triggers": int(passive_behavior.get("js_triggers", 0) or 0),
            "elapsed_ms": int(passive_behavior.get("elapsed_ms", 0) or 0),
        },
    }


def attempt_skyscanner_interstitial_fallback_reload(
    browser,
    url: str,
    *,
    grace_result: Dict[str, Any],
    human_mimic: bool,
    grace_ms_extended: int = 22000,  # Extended to support long-hold challenges with readiness dwell.
    max_reload_attempts: int = 3,
    allow_manual_escalation: bool = True,
    success_html_predicate: Optional[Callable[[str, str], bool]] = None,
) -> Dict[str, Any]:
    """Fallback reload attempts with optimized browser headers and extended grace.

    If the initial grace window fails to clear the captcha, try bounded reload retries
    with better browser mimicking headers and a longer grace period. This helps
    when PerimeterX detection is triggered by incomplete headers or insufficient
    behavioral signals.

    Key improvements over initial grace:
    - More complete request headers (cache control, user-initiated signals)
    - Longer grace window (22s class vs initial short grace)
    - More aggressive mouse movement and scrolling due to longer duration

    Args:
        browser: Browser instance.
        url: Current page URL to reload.
        grace_result: Result of the initial grace attempt.
        human_mimic: Whether human mimic is enabled.
        grace_ms_extended: Extended grace duration for fallback reload.
        allow_manual_escalation: Whether fallback may open manual assist before final retry.
        success_html_predicate: Optional validator for successful reload HTML.
            When provided, fallback is treated as cleared only if this callable
            returns True for ``(html_text, current_url)``.

    Returns:
        Result dict with fallback attempt details and captcha status.
    """
    # Only attempt fallback if:
    # 1. Initial grace was used but captcha persists
    # 2. Human mimic is enabled
    # 3. We haven't exceeded attempts
    if not grace_result.get("used"):
        return {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "grace_not_used",
        }

    if grace_result.get("cleared"):
        return {
            "used": False,
            "attempted": False,
            "cleared": True,
            "reason": "already_cleared",
        }

    if not human_mimic:
        return {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "human_mimic_disabled",
        }

    attempts = max(1, min(3, int(max_reload_attempts or 1)))
    passive_px_mode = _should_use_passive_px_recovery(grace_result)
    manual_result = (
        dict((grace_result or {}).get("manual_intervention", {}) or {})
        if isinstance((grace_result or {}).get("manual_intervention", {}), dict)
        else {}
    )
    intervention_mode = str(getattr(browser, "human_intervention_mode", "") or "").strip().lower()
    if intervention_mode not in {"off", "assist", "demo"}:
        intervention_mode = "assist" if bool(getattr(browser, "allow_human_intervention", False)) else "off"
    if (
        intervention_mode == "off"
        and not bool(getattr(browser, "allow_human_intervention", False))
        and bool(getattr(browser, "last_resort_manual_when_disabled", False))
        and not bool(getattr(browser, "headless", False))
    ):
        # Keep deterministic machine retries first, then escalate to last-resort manual.
        # Immediate handoff after one grace attempt is too aggressive and hurts recovery.
        attempts = max(attempts, 2)
    assist_follow_up_mode = (
        intervention_mode == "assist"
        and bool(manual_result.get("used", False))
    )
    reload_target_url = _derive_skyscanner_challenge_target_url(browser, fallback_url=url)
    expected_route_url = reload_target_url if _is_skyscanner_transport_results_url(reload_target_url) else ""
    log.info(
        "captcha.fallback_reload attempting url=%s resolved_target=%s grace_ms=%d attempts=%d passive_px_mode=%s assist_follow_up_mode=%s",
        str(url or "")[:80],
        str(reload_target_url or "")[:120],
        grace_ms_extended,
        attempts,
        passive_px_mode,
        assist_follow_up_mode,
    )

    # NOTE:
    # Avoid mutating per-page request headers here. Earlier static client-hint/header
    # overrides persisted beyond fallback attempts and correlated with repeated
    # post-challenge white-shell loops. Keep fallback reload transport-neutral.
    log.debug(
        "captcha.fallback_reload header_injection_skipped reason=avoid_persistent_header_override"
    )

    # Perform bounded reload retries with extended grace.
    last_html = ""
    last_reason = "fallback_reload_failed"
    last_meta: Dict[str, Any] = {}
    attempt_trace: list[Dict[str, Any]] = []
    for attempt_idx in range(attempts):
        if not _is_browser_page_open(browser):
            attempt_trace.append(
                {
                    "attempt": attempt_idx + 1,
                    "reload": False,
                    "assist_follow_up_mode": assist_follow_up_mode,
                    "error": "TargetClosedError",
                }
            )
            return {
                "used": True,
                "attempted": bool(attempt_idx > 0),
                "cleared": False,
                "reason": "fallback_reload_page_closed",
                "html": last_html,
                "grace_extended_ms": grace_ms_extended,
                "press_hold_executed": False,
                "press_hold_success": False,
                "press_hold_success_signal": "",
                "fallback_meta": {
                    "attempt_index": attempt_idx + 1,
                    "error": "TargetClosedError",
                    "note": "reload skipped because browser/page is unavailable",
                    "attempt_trace": list(attempt_trace),
                },
                "attempts": attempt_idx,
            }
        try:
            attempt_grace_ms = max(1000, int(grace_ms_extended or 0))
            # Keep enough budget for long PRESS & HOLD flows that require readiness dwell
            # before the hold can begin. Apply this floor only when the browser
            # exposes interstitial choreography support.
            if hasattr(browser, "human_mimic_interstitial_grace"):
                attempt_grace_ms = max(attempt_grace_ms, 21000)
            use_reload = not assist_follow_up_mode
            if passive_px_mode and attempt_idx == 0:
                # Bounded extra settle on first retry for PX passive-profile pages.
                attempt_grace_ms = max(attempt_grace_ms, 12000)
            if assist_follow_up_mode:
                # In assist follow-up, avoid additional reload churn and prioritize long hold windows.
                attempt_grace_ms = max(15_000, attempt_grace_ms)

            attempt_entry: Dict[str, Any] = {
                "attempt": attempt_idx + 1,
                "reload": bool(use_reload),
                "assist_follow_up_mode": assist_follow_up_mode,
                "attempt_grace_ms": int(attempt_grace_ms),
            }

            if use_reload:
                if hasattr(browser, "goto"):
                    browser.goto(reload_target_url)
                else:
                    page = getattr(browser, "page", None)
                    if page is not None:
                        page.goto(reload_target_url, wait_until="domcontentloaded", timeout=30000)

            if hasattr(browser, "human_mimic_interstitial_grace"):
                browser.human_mimic_interstitial_grace(duration_ms=attempt_grace_ms)
            else:
                time.sleep(attempt_grace_ms / 1000.0)

            fallback_meta = getattr(browser, "_last_interstitial_grace_meta", {})
            last_meta = fallback_meta if isinstance(fallback_meta, dict) else {}
            hold_outcome = _extract_press_hold_outcome(last_meta if isinstance(last_meta, dict) else {})

            cooldown_ms = 0
            if bool(hold_outcome.get("press_hold_executed", False)):
                cooldown_ms = 1600 if assist_follow_up_mode else 1000
            if cooldown_ms > 0:
                page_obj = getattr(browser, "page", None)
                try:
                    if page_obj is not None and hasattr(page_obj, "wait_for_timeout"):
                        page_obj.wait_for_timeout(cooldown_ms)
                    else:
                        time.sleep(cooldown_ms / 1000.0)
                except Exception:
                    pass
            attempt_entry["cooldown_ms"] = int(cooldown_ms)
            attempt_entry["press_hold_executed"] = bool(hold_outcome.get("press_hold_executed", False))
            attempt_entry["press_hold_success"] = bool(hold_outcome.get("press_hold_success", False))
            attempt_entry["press_hold_success_signal"] = str(
                hold_outcome.get("press_hold_success_signal", "") or ""
            )

            try:
                html_reload = str(browser.content() or "")
            except Exception:
                html_reload = ""
            last_html = html_reload

            hard_block_reload = detect_skyscanner_interstitial_block(html_reload)
            if not hard_block_reload and _is_skyscanner_captcha_url(browser):
                hard_block_reload = {
                    "reason": "blocked_interstitial_captcha",
                    "page_kind": "interstitial",
                    "block_type": "captcha",
                    "evidence": {"url.captcha_surface": True},
                }
            if isinstance(last_meta, dict):
                last_meta["attempt_grace_ms"] = attempt_grace_ms
            success_predicate_ok = True
            success_predicate_reason = ""
            if not hard_block_reload and callable(success_html_predicate):
                current_url_now = str(getattr(getattr(browser, "page", None), "url", "") or "")
                try:
                    success_predicate_ok = bool(success_html_predicate(html_reload, current_url_now))
                except Exception as predicate_exc:
                    success_predicate_ok = False
                    success_predicate_reason = (
                        f"success_predicate_exception_{type(predicate_exc).__name__}"
                    )
                if not success_predicate_ok and not success_predicate_reason:
                    success_predicate_reason = "success_predicate_failed"
                if not success_predicate_ok:
                    hard_block_reload = {
                        "reason": success_predicate_reason,
                        "page_kind": "interstitial",
                        "block_type": "predicate_gate",
                        "evidence": {"predicate": "success_html_predicate"},
                    }

            attempt_entry["blocked"] = bool(hard_block_reload)
            attempt_entry["blocked_reason"] = str((hard_block_reload or {}).get("reason", "") or "")
            attempt_entry["success_predicate_ok"] = bool(success_predicate_ok)
            attempt_entry["html_len"] = len(str(html_reload or ""))
            attempt_entry["press_hold_probe_attempts"] = int(
                (last_meta or {}).get("press_hold_probe_attempts", 0) or 0
            )
            attempt_trace.append(attempt_entry)
            log.info(
                "captcha.fallback_reload.attempt site=skyscanner attempt=%d/%d reload=%s grace_ms=%d probes=%d press_hold_executed=%s press_hold_success=%s success_signal=%s blocked=%s blocked_reason=%s html_len=%d",
                attempt_idx + 1,
                attempts,
                bool(use_reload),
                int(attempt_grace_ms),
                int(attempt_entry.get("press_hold_probe_attempts", 0) or 0),
                bool(hold_outcome.get("press_hold_executed", False)),
                bool(hold_outcome.get("press_hold_success", False)),
                str(hold_outcome.get("press_hold_success_signal", "")),
                bool(attempt_entry.get("blocked", False)),
                str(attempt_entry.get("blocked_reason", "")),
                int(attempt_entry.get("html_len", 0) or 0),
            )

            if not hard_block_reload:
                log.info(
                    "captcha.fallback_reload succeeded attempt=%d/%d",
                    attempt_idx + 1,
                    attempts,
                )
                if isinstance(last_meta, dict):
                    last_meta["attempt_trace"] = list(attempt_trace)
                return {
                    "used": True,
                    "attempted": True,
                    "cleared": True,
                    "reason": "fallback_assist_follow_up_cleared"
                    if assist_follow_up_mode
                    else "fallback_reload_cleared",
                    "html": html_reload,
                    "grace_extended_ms": grace_ms_extended,
                    "attempt_grace_ms": attempt_grace_ms,
                    "press_hold_executed": bool(hold_outcome.get("press_hold_executed", False)),
                    "press_hold_success": bool(hold_outcome.get("press_hold_success", False)),
                    "press_hold_success_signal": str(hold_outcome.get("press_hold_success_signal", "") or ""),
                    "fallback_meta": last_meta,
                    "expected_route_url": expected_route_url,
                    "reload_target_url": reload_target_url,
                    "attempts": attempt_idx + 1,
                }

            # For passive PX pages, avoid immediate churn: one bounded extra dwell before next reload.
            if (
                passive_px_mode
                and not assist_follow_up_mode
                and attempt_idx + 1 < attempts
                and hasattr(browser, "human_mimic_interstitial_grace")
            ):
                passive_settle_ms = min(3000, max(1200, attempt_grace_ms // 3))
                try:
                    browser.human_mimic_interstitial_grace(duration_ms=passive_settle_ms)
                    html_after_settle = str(browser.content() or "")
                except Exception:
                    html_after_settle = ""
                if html_after_settle:
                    last_html = html_after_settle
                    hard_block_after_settle = detect_skyscanner_interstitial_block(html_after_settle)
                    success_predicate_ok_after_settle = True
                    if not hard_block_after_settle and callable(success_html_predicate):
                        settle_url = str(getattr(getattr(browser, "page", None), "url", "") or "")
                        try:
                            success_predicate_ok_after_settle = bool(
                                success_html_predicate(html_after_settle, settle_url)
                            )
                        except Exception:
                            success_predicate_ok_after_settle = False
                        if not success_predicate_ok_after_settle:
                            hard_block_after_settle = {
                                "reason": "success_predicate_failed",
                                "page_kind": "interstitial",
                                "block_type": "predicate_gate",
                                "evidence": {"predicate": "success_html_predicate"},
                            }
                    fallback_meta = getattr(browser, "_last_interstitial_grace_meta", {})
                    last_meta = fallback_meta if isinstance(fallback_meta, dict) else {}
                    if isinstance(last_meta, dict):
                        last_meta["attempt_grace_ms"] = attempt_grace_ms
                        last_meta["passive_settle_ms"] = passive_settle_ms
                        last_meta["success_predicate_ok"] = bool(success_predicate_ok_after_settle)
                    if not hard_block_after_settle:
                        if isinstance(last_meta, dict):
                            last_meta["attempt_trace"] = list(attempt_trace)
                        return {
                            "used": True,
                            "attempted": True,
                            "cleared": True,
                            "reason": "fallback_passive_settle_cleared",
                            "html": html_after_settle,
                            "grace_extended_ms": grace_ms_extended,
                            "attempt_grace_ms": attempt_grace_ms,
                            "press_hold_executed": bool(hold_outcome.get("press_hold_executed", False)),
                            "press_hold_success": bool(hold_outcome.get("press_hold_success", False)),
                            "press_hold_success_signal": str(hold_outcome.get("press_hold_success_signal", "") or ""),
                            "fallback_meta": last_meta,
                            "expected_route_url": expected_route_url,
                            "reload_target_url": reload_target_url,
                            "attempts": attempt_idx + 1,
                        }
                    last_reason = str(hard_block_after_settle.get("reason", "fallback_reload_failed"))

            if bool(hold_outcome.get("press_hold_executed", False)) and not bool(
                hold_outcome.get("press_hold_success", False)
            ):
                last_reason = "fallback_press_hold_unsuccessful"
            else:
                last_reason = str(hard_block_reload.get("reason", "fallback_reload_failed"))
            if attempt_idx + 1 >= attempts:
                break

            # Final controlled path: allow a human to solve challenge in headed mode.
            if (
                allow_manual_escalation
                and hasattr(browser, "allow_manual_verification_intervention")
                and not assist_follow_up_mode
                and attempt_idx + 2 == attempts
            ):
                manual_wait_sec = _captcha_manual_wait_sec()
                manual_result = browser.allow_manual_verification_intervention(
                    reason=f"skyscanner_interstitial_retry_{attempt_idx + 1}",
                    wait_sec=manual_wait_sec,
                )
                manual_reason = str((manual_result or {}).get("reason", "") or "").strip().lower()
                manual_used = bool((manual_result or {}).get("used", False))
                log.info(
                    "captcha.manual_intervention.result site=skyscanner stage=fallback attempt=%d/%d used=%s reason=%s wait_sec=%s elapsed_ms=%s request=%s",
                    attempt_idx + 1,
                    attempts,
                    manual_used,
                    str((manual_result or {}).get("reason", "")),
                    int((manual_result or {}).get("wait_sec", 0) or 0),
                    int((manual_result or {}).get("elapsed_ms", 0) or 0),
                    str((manual_result or {}).get("requested_reason", "")),
                )
                if isinstance(last_meta, dict):
                    last_meta["manual_intervention"] = manual_result
                if (not manual_used) and manual_reason in {
                    "manual_intervention_disabled",
                    "headless_mode",
                    "page_unavailable",
                }:
                    last_reason = f"fallback_manual_intervention_unavailable_{manual_reason}"
                    if isinstance(last_meta, dict):
                        last_meta["manual_intervention_unavailable_reason"] = manual_reason
                    break
                if manual_used:
                    manual_automation_count = int(
                        (
                            (
                                (manual_result or {}).get(
                                    "automation_activity_during_manual",
                                    {},
                                )
                                or {}
                            ).get("count", 0)
                        )
                        or 0
                    )
                    if manual_automation_count > 0:
                        last_reason = "fallback_manual_intervention_interference_detected"
                        break
                    try:
                        html_after_manual = str(browser.content() or "")
                    except Exception:
                        html_after_manual = ""
                    if html_after_manual:
                        last_html = html_after_manual
                        hard_block_after_manual = detect_skyscanner_interstitial_block(html_after_manual)
                        if not hard_block_after_manual:
                            return {
                                "used": True,
                                "attempted": True,
                                "cleared": True,
                                "reason": "fallback_manual_intervention_cleared",
                                "html": html_after_manual,
                                "grace_extended_ms": grace_ms_extended,
                                "press_hold_executed": bool(hold_outcome.get("press_hold_executed", False)),
                                "press_hold_success": bool(hold_outcome.get("press_hold_success", False)),
                                "press_hold_success_signal": str(hold_outcome.get("press_hold_success_signal", "") or ""),
                                "fallback_meta": last_meta,
                                "expected_route_url": expected_route_url,
                                "reload_target_url": reload_target_url,
                                "attempts": attempt_idx + 1,
                            }
                        last_reason = str(hard_block_after_manual.get("reason", "fallback_reload_failed"))
                    else:
                        last_reason = "fallback_manual_intervention_no_html"
                    break

        except Exception as e:
            error_type = str(type(e).__name__)
            log.error(
                "captcha.fallback_reload exception attempt=%d/%d error=%s",
                attempt_idx + 1,
                attempts,
                str(e)[:150],
            )
            attempt_trace.append(
                {
                    "attempt": attempt_idx + 1,
                    "reload": not assist_follow_up_mode,
                    "assist_follow_up_mode": assist_follow_up_mode,
                    "error": error_type,
                }
            )
            if error_type == "TargetClosedError":
                last_reason = "fallback_reload_page_closed"
                last_meta = {"error": error_type}
                break
            last_reason = "fallback_reload_exception"
            last_meta = {"error": error_type}

    hold_outcome = _extract_press_hold_outcome(last_meta if isinstance(last_meta, dict) else {})
    if isinstance(last_meta, dict):
        last_meta["attempt_trace"] = list(attempt_trace)
    return {
        "used": True,
        "attempted": True,
        "cleared": False,
        "reason": last_reason,
        "html": last_html,
        "grace_extended_ms": grace_ms_extended,
        "press_hold_executed": bool(hold_outcome.get("press_hold_executed", False)),
        "press_hold_success": bool(hold_outcome.get("press_hold_success", False)),
        "press_hold_success_signal": str(hold_outcome.get("press_hold_success_signal", "") or ""),
        "fallback_meta": last_meta,
        "expected_route_url": expected_route_url,
        "reload_target_url": reload_target_url,
        "attempts": attempts,
    }
