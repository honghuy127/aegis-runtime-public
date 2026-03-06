"""Google Flights ui_actions functions."""

from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, UTC
from utils.logging import get_logger

log = get_logger(__name__)

import time
from core.browser import BrowserSession
from core.scenario.gf_helpers.google_date_picker import (
    google_fill_date_via_picker as _google_fill_date_via_picker_impl,
)
from core.scenario_runner.google_flights.core_functions import (
    _extract_google_flights_form_state,
    _is_google_dest_placeholder,
    _google_form_value_matches_airport,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_flights_after_search_ready,
    _google_force_bind_flights_tab_selectors,
)
from core.ui_tokens import prioritize_tokens
from storage.shared_knowledge_store import get_airport_aliases_for_provider
from utils.selector_hints import promote_selector_hint
from utils.thresholds import get_threshold

def _google_fill_and_commit_location(
    browser,
    *,
    role: str,
    value: str,
    selectors,
    locale_hint: str = "",
    timeout_ms: Optional[int] = None,
    deadline: Optional[float] = None,
    debug_run_id: str = "",
    debug_attempt: int = 0,
    debug_turn: int = 0,
    debug_step_index: int = -1,
    expected_origin: str = "",
    expected_depart: str = "",
    expected_return: str = "",
) -> Dict[str, Any]:
    """Best-effort fill + commit for Google origin/destination controls.

    For legacy scenario execution, uses the unified Browser.fill_google_flights_combobox()
    method to ensure consistent combobox handling with the Actor path.
    """
    # Lazy import to avoid circular dependency
    import core.scenario_runner as sr

    role_key = str(role or "").strip().lower()
    target_value = str(value or "").strip()

    # Initialize result dict with defaults
    result: Dict[str, Any] = {
        "ok": False,
        "selector_used": "",
        "committed": False,
        "reason": "not_attempted",
    }
    postcheck_debug: Dict[str, Any] = {
        "alias_query_candidates": [],
    }

    def _append_combobox_debug_evidence(evidence: Dict[str, Any], debug_payload: Dict[str, Any]) -> None:
        if not isinstance(evidence, dict) or not isinstance(debug_payload, dict):
            return
        activation_index = debug_payload.get("activation_selector_index_used")
        if isinstance(activation_index, int):
            evidence["combobox.activation_selector_index_used"] = int(activation_index)
        input_selector_used = str(debug_payload.get("input_selector_used", "") or "").strip()
        if input_selector_used:
            evidence["combobox.input_selector_used"] = input_selector_used
        if isinstance(debug_payload.get("generic_input_selector_used"), bool):
            evidence["combobox.generic_input_selector_used"] = bool(
                debug_payload.get("generic_input_selector_used")
            )
        activation_open_probe = debug_payload.get("activation_open_probe")
        if isinstance(activation_open_probe, dict) and activation_open_probe:
            compact_probe = {
                str(k)[:40]: (str(v)[:120] if isinstance(v, str) else v)
                for k, v in list(activation_open_probe.items())[:6]
            }
            evidence["combobox.activation_open_probe"] = compact_probe
        activation_visible_prefilter = debug_payload.get("activation_visible_prefilter")
        if isinstance(activation_visible_prefilter, dict) and activation_visible_prefilter:
            compact_prefilter = {}
            for key, value in list(activation_visible_prefilter.items())[:8]:
                compact_prefilter[str(key)[:80]] = str(value)[:40]
            evidence["combobox.activation_visible_prefilter"] = compact_prefilter
        if isinstance(debug_payload.get("prefilled_match"), bool):
            evidence["combobox.prefilled_match"] = bool(debug_payload.get("prefilled_match"))
        prefilled_match_token = str(debug_payload.get("prefilled_match_token", "") or "").strip()
        if prefilled_match_token:
            evidence["combobox.prefilled_match_token"] = prefilled_match_token[:80]
        prefilled_selector_used = str(debug_payload.get("prefilled_selector_used", "") or "").strip()
        if prefilled_selector_used:
            evidence["combobox.prefilled_selector_used"] = prefilled_selector_used[:120]
        prefilled_value = str(debug_payload.get("prefilled_value", "") or "").strip()
        if prefilled_value:
            evidence["combobox.prefilled_value"] = prefilled_value[:80]
        if isinstance(debug_payload.get("keyboard_commit_attempted"), bool):
            evidence["combobox.keyboard_commit_attempted"] = bool(
                debug_payload.get("keyboard_commit_attempted")
            )
        if isinstance(debug_payload.get("option_click_succeeded"), bool):
            evidence["combobox.option_click_succeeded"] = bool(
                debug_payload.get("option_click_succeeded")
            )
        if isinstance(debug_payload.get("verify_ok"), bool):
            evidence["combobox.verify_ok"] = bool(debug_payload.get("verify_ok"))
        if isinstance(debug_payload.get("verify_semantic_fallback"), bool):
            evidence["combobox.verify_semantic_fallback"] = bool(
                debug_payload.get("verify_semantic_fallback")
            )
        commit_signal = debug_payload.get("commit_signal")
        if isinstance(commit_signal, dict) and commit_signal:
            compact_signal = {
                str(k)[:40]: (str(v)[:120] if isinstance(v, str) else v)
                for k, v in list(commit_signal.items())[:6]
            }
            evidence["combobox.commit_signal"] = compact_signal

    def _debug_route_fill_selector_probe(stage_label: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Write compact selector/DOM probe artifact for Google route fill in debug mode.

        This is debug-only and bounded: small selector lists, few matches, truncated HTML/text.
        """
        if not str(debug_run_id or "").strip():
            return
        page_obj = getattr(browser, "page", None)
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "stage": str(stage_label or "")[:80],
            "service": "google_flights",
            "role": role_key,
            "target_value": target_value,
            "attempt": int(debug_attempt) + 1,
            "turn": int(debug_turn) + 1,
            "step_index": int(debug_step_index),
            "activation_selectors": list(role_selectors[:5]) if "role_selectors" in locals() else [],
            "input_selectors": list(input_selectors[:3]) if "input_selectors" in locals() else [],
        }
        if isinstance(extra, dict) and extra:
            extra_payload = dict(extra)
            opener_debug = extra_payload.get("opener_debug")
            if isinstance(opener_debug, dict) and "calendar_debug" not in extra_payload:
                extra_payload["calendar_debug"] = dict(opener_debug)
            payload["extra"] = extra_payload
        if page_obj is None or not hasattr(page_obj, "evaluate"):
            sr._write_json_artifact_snapshot(
                debug_run_id,
                f"google_route_fill_{role_key}_{stage_label}_selector_probe.json",
                payload,
            )
            return
        selectors_for_probe = []
        for sel in list(payload.get("activation_selectors") or []) + list(payload.get("input_selectors") or []):
            s = str(sel or "").strip()
            if not s or s in selectors_for_probe:
                continue
            selectors_for_probe.append(s)
        try:
            payload["selector_dom_probe"] = sr._compact_selector_dom_probe(
                page_obj,
                selectors_for_probe[:8],
                max_selectors=8,
                max_matches=2,
                max_html_chars=360,
                max_text_chars=140,
            )
        except Exception as exc:
            payload["selector_dom_probe_error"] = str(exc)[:200]
        sr._write_json_artifact_snapshot(
            debug_run_id,
            f"google_route_fill_{role_key}_{stage_label}_selector_probe.json",
            payload,
        )

    if role_key not in {"origin", "dest"}:
        result["reason"] = "unsupported_role"
        return result
    if not target_value:
        result["reason"] = "empty_value"
        return result

    timeout_value = sr._normalize_selector_timeout_ms(
        timeout_ms,
        site_key="google_flights",
        action=f"google_fill_commit_{role_key}",
    )
    if timeout_value is None:
        timeout_value = sr._normalize_selector_timeout_ms(
            int(get_threshold("browser_action_selector_timeout_ms_google_flights", 1500)),
            site_key="google_flights",
            action=f"google_fill_commit_{role_key}",
        )
    if timeout_value is None:
        timeout_value = 1200

    # Prepare selectors for the bounded combobox method
    # Prefer role-scoped activation selectors before raw plan selectors. Some generic plan
    # variants include noisy cross-field candidates (e.g. departure button in dest step),
    # and the bounded combobox helper only probes a short prefix.
    role_selectors = sr._google_route_activation_selectors(
        role=role_key,
        value=target_value,
        plan_selectors=selectors,
    )
    input_selectors = sr._google_force_bind_location_input_selectors(role_key)
    locale_for_tokens = str(locale_hint or sr._current_mimic_locale() or "").strip().lower()
    display_lang_hint = sr._google_display_locale_hint_from_browser(browser)
    role_selectors = sr._selector_hints_overlay(
        role_selectors,
        site="google_flights",
        action="route_fill_activation",
        role=role_key,
        display_lang=display_lang_hint,
        locale_hint=locale_for_tokens,
        max_hints=2,
    )
    input_selectors = sr._selector_hints_overlay(
        input_selectors,
        site="google_flights",
        action="route_fill_input",
        role=role_key,
        display_lang=display_lang_hint,
        locale_hint=locale_for_tokens,
        max_hints=2,
        hint_allow=lambda s, rk=role_key: sr._google_route_fill_input_selector_hint_is_plausible(rk, s),
    )

    # Prepare verify tokens: city names, IATA codes, etc. for verification
    verify_tokens = sr._expected_field_tokens(role_key, locale_for_tokens)
    route_alias_tokens = set()
    if role_key in {"origin", "dest"} and target_value:
        try:
            route_alias_tokens = set(sr.get_airport_aliases_for_provider(target_value.strip(), "google_flights") or set())
        except Exception:
            route_alias_tokens = set()
    alias_query_candidates = []
    if role_key == "dest" and route_alias_tokens:
        alias_ranked = sr.prioritize_tokens(list(route_alias_tokens), locale_hint=locale_for_tokens)
        for token in alias_ranked:
            tok = str(token or "").strip()
            if not tok:
                continue
            if tok.upper() == str(target_value or "").strip().upper():
                continue
            # Prefer human-typed city/airport labels over raw IATA duplicates.
            if len(tok) < 2:
                continue
            alias_query_candidates.append(tok)
        # Keep bounded and deterministic.
        alias_query_candidates = alias_query_candidates[:2]
    verify_tokens_combobox = list(verify_tokens)
    if route_alias_tokens:
        try:
            alias_verify_ranked = sr.prioritize_tokens(
                list(route_alias_tokens), locale_hint=locale_for_tokens
            )
        except Exception:
            alias_verify_ranked = list(route_alias_tokens)
        # Keep a bounded cross-script alias set. Locale-prioritized aliases can push useful
        # Latin-script city labels (e.g., "TOKYO") beyond the first few tokens, which
        # breaks semantic matching against English-prefilled values on mixed-locale pages.
        alias_verify_tokens: List[str] = []
        for token in alias_verify_ranked[:10]:
            tok = str(token or "").strip()
            if not tok:
                continue
            alias_verify_tokens.append(tok)
        ascii_alpha_aliases = []
        non_ascii_aliases = []
        for token in alias_verify_ranked:
            tok = str(token or "").strip()
            if not tok:
                continue
            is_ascii = tok.isascii()
            has_alpha = any(ch.isalpha() for ch in tok)
            if is_ascii and has_alpha:
                ascii_alpha_aliases.append(tok)
            elif not is_ascii:
                non_ascii_aliases.append(tok)
        for tok in ascii_alpha_aliases[:4]:
            alias_verify_tokens.append(tok)
        for tok in non_ascii_aliases[:4]:
            alias_verify_tokens.append(tok)
        for token in alias_verify_tokens[:16]:
            tok = str(token or "").strip()
            if not tok:
                continue
            verify_tokens_combobox.append(tok)
        verify_tokens_combobox = list(dict.fromkeys(verify_tokens_combobox))
    postcheck_debug["alias_query_candidates"] = list(alias_query_candidates)

    # Use bounded Browser.fill_google_flights_combobox() for legacy path with CAPPED selectors
    # CRITICAL: Never merge activation selectors with input selectors; keep separate and capped
    try:
        def _attempt_combobox_fill(query_text: str):
            return browser.fill_google_flights_combobox(
                activation_selectors=role_selectors[:5],    # CAP activation at 5
                input_selectors=input_selectors[:3],        # CAP input candidates at 3
                text=query_text,
                verify_tokens=(verify_tokens_combobox + [target_value, query_text]),
                timeout_ms=timeout_value,
            )

        def _finalize_dest_editor_commit_once(postcheck: Dict[str, Any]) -> Dict[str, Any]:
            """One bounded blur/commit attempt when combobox editor value changed but chip stayed placeholder."""
            try:
                combobox_debug = dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {})
            except Exception:
                combobox_debug = {}
            commit_signal = combobox_debug.get("commit_signal") if isinstance(combobox_debug, dict) else {}
            if not isinstance(commit_signal, dict):
                commit_signal = {}
            if not bool(combobox_debug.get("keyboard_commit_attempted", False)):
                return postcheck
            if bool(combobox_debug.get("option_click_succeeded", False)):
                return postcheck
            if not bool(combobox_debug.get("verify_ok", False)):
                return postcheck
            active_value = str(commit_signal.get("active_value", "") or "").strip()
            if not active_value:
                return postcheck
            page_obj = getattr(browser, "page", None)
            keyboard = getattr(page_obj, "keyboard", None)
            if keyboard is None or not hasattr(keyboard, "press"):
                return postcheck
            try:
                keyboard.press("Tab")
                if hasattr(page_obj, "wait_for_timeout"):
                    page_obj.wait_for_timeout(180)
                else:
                    time.sleep(0.18)
                form_state_finalize = sr._extract_google_flights_form_state(probe_target)
                postcheck_debug["finalize_tab_form_state"] = {
                    "confidence": str(form_state_finalize.get("confidence", "") or ""),
                    "dest_text_raw": str(form_state_finalize.get("dest_text_raw", form_state_finalize.get("dest_text", "")) or ""),
                    "dest_is_placeholder": bool(form_state_finalize.get("dest_is_placeholder")),
                }
                finalize_conf = str(form_state_finalize.get("confidence", "low") or "low").strip().lower()
                finalize_dest_raw = str(
                    form_state_finalize.get("dest_text_raw", form_state_finalize.get("dest_text", "")) or ""
                ).strip()
                finalize_placeholder = bool(form_state_finalize.get("dest_is_placeholder")) or sr._is_google_dest_placeholder(
                    finalize_dest_raw
                )
                finalize_mismatch = (
                    bool(finalize_dest_raw)
                    and target_value
                    and finalize_conf in {"medium", "high"}
                    and not sr._google_form_value_matches_airport(finalize_dest_raw, target_value)
                )
                if not finalize_placeholder and not finalize_mismatch:
                    log.info(
                        "scenario.route_fill.location_postcheck_recovered role=%s method=tab_finalize confidence=%s observed_dest_raw=%s",
                        role_key,
                        finalize_conf,
                        finalize_dest_raw[:80],
                    )
                    return {
                        "ok": True,
                        "reason": "dest_tab_finalize_recovered",
                        "confidence": finalize_conf,
                        "observed_dest_raw": finalize_dest_raw,
                        "retry_probe": "tab_finalize",
                    }
                postcheck["retry_probe"] = str(postcheck.get("retry_probe", "") or "placeholder_persisted") + "|tab_finalize_no_commit"
                postcheck["confidence"] = finalize_conf
                if finalize_dest_raw:
                    postcheck["observed_dest_raw"] = finalize_dest_raw
                return postcheck
            except Exception:
                postcheck["retry_probe"] = str(postcheck.get("retry_probe", "") or "placeholder_persisted") + "|tab_finalize_error"
                return postcheck

        _debug_route_fill_selector_probe(
            "pre_combobox",
            extra={
                "verify_tokens": list(verify_tokens[:8]),
                "verify_tokens_combobox": list(verify_tokens_combobox[:8]),
            },
        )
        ok, selector_used = _attempt_combobox_fill(target_value)
        _debug_route_fill_selector_probe(
            "post_combobox",
            extra={
                "ok": bool(ok),
                "selector_used": str(selector_used or ""),
                "combobox_debug": dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {}),
            },
        )

        if ok:
            # Immediate bounded post-fill field probe: a Google Flights results/explore
            # surface can remain visible while the route chip stays placeholder/unbound.
            # Catch explicit placeholder state here instead of letting the mismatch drift
            # into later date-fill/scope stages.
            postcheck = {"ok": True, "reason": "not_run"}
            probe_target = getattr(browser, "page", None) or browser
            try:
                form_state = sr._extract_google_flights_form_state(probe_target)
                postcheck_debug["initial_form_state"] = {
                    "confidence": str(form_state.get("confidence", "") or ""),
                    "origin_text": str(form_state.get("origin_text", form_state.get("origin_text_raw", "")) or ""),
                    "dest_text_raw": str(form_state.get("dest_text_raw", form_state.get("dest_text", "")) or ""),
                    "dest_is_placeholder": bool(form_state.get("dest_is_placeholder")),
                    "depart_text": str(form_state.get("depart_text", form_state.get("depart_text_raw", "")) or ""),
                    "return_text": str(form_state.get("return_text", form_state.get("return_text_raw", "")) or ""),
                }
                confidence = str(form_state.get("confidence", "low") or "low").strip().lower()
                try:
                    combobox_debug_post = dict(
                        getattr(browser, "_last_google_flights_combobox_debug", {}) or {}
                    )
                except Exception:
                    combobox_debug_post = {}
                commit_signal_post = combobox_debug_post.get("commit_signal")
                if not isinstance(commit_signal_post, dict):
                    commit_signal_post = {}
                active_editor_value = str(commit_signal_post.get("active_value", "") or "").strip()
                combobox_verify_ok = bool(combobox_debug_post.get("verify_ok", False))
                if role_key == "dest":
                    observed_dest_raw = str(
                        form_state.get("dest_text_raw", form_state.get("dest_text", "")) or ""
                    ).strip()
                    explicit_dest_placeholder = bool(form_state.get("dest_is_placeholder"))
                    dest_is_placeholder = explicit_dest_placeholder or sr._is_google_dest_placeholder(
                        observed_dest_raw
                    )
                    if dest_is_placeholder and (
                        bool(observed_dest_raw)
                        or explicit_dest_placeholder
                        or confidence in {"medium", "high"}
                    ):
                        postcheck = {
                            "ok": False,
                            "reason": "dest_placeholder",
                            "confidence": confidence,
                            "observed_dest_raw": observed_dest_raw,
                        }
                    elif (
                        observed_dest_raw
                        and target_value
                        and confidence in {"medium", "high"}
                        and not sr._google_form_value_matches_airport(observed_dest_raw, target_value)
                    ):
                        # Tolerate cases where the visible route chip text equals the
                        # combobox editor active value and the combobox verification
                        # succeeded (keyboard commit). Some pages present ASCII city
                        # labels that may be missing from provider alias tokens; in
                        # that scenario prefer the editor-commit evidence to avoid
                        # false-positive mismatches.
                        if combobox_verify_ok and active_editor_value and observed_dest_raw.strip().lower() == active_editor_value.strip().lower():
                            postcheck = {
                                "ok": True,
                                "reason": "dest_match_active_editor",
                                "confidence": confidence,
                                "observed_dest_raw": observed_dest_raw,
                            }
                        else:
                            postcheck = {
                                "ok": False,
                                "reason": "dest_mismatch",
                                "confidence": confidence,
                                "observed_dest_raw": observed_dest_raw,
                            }
                    if (
                        not bool(postcheck.get("ok"))
                        and str(postcheck.get("reason", "") or "") == "dest_mismatch"
                        and sr._google_form_text_looks_date_like(observed_dest_raw)
                        and combobox_verify_ok
                        and active_editor_value
                        and sr._google_form_value_matches_airport(active_editor_value, target_value)
                    ):
                        postcheck = {
                            "ok": True,
                            "reason": "dest_postcheck_cross_field_date_recovered",
                            "confidence": confidence,
                            "observed_dest_raw": observed_dest_raw,
                        }
                        postcheck_debug["postcheck_cross_field_date_recovered"] = {
                            "role": role_key,
                            "observed_value": observed_dest_raw[:160],
                            "active_editor_value": active_editor_value[:80],
                        }
                        log.info(
                            "scenario.route_fill.location_postcheck_recovered role=%s method=cross_field_date active_value=%s",
                            role_key,
                            active_editor_value[:40],
                        )
                    if (
                        not bool(postcheck.get("ok"))
                        and str(postcheck.get("reason", "") or "") == "dest_mismatch"
                        and sr._google_form_text_looks_instructional_noise(observed_dest_raw)
                        and combobox_verify_ok
                        and active_editor_value
                        and sr._google_form_value_matches_airport(active_editor_value, target_value)
                    ):
                        postcheck = {
                            "ok": True,
                            "reason": "dest_postcheck_helper_contamination_recovered",
                            "confidence": confidence,
                            "observed_dest_raw": observed_dest_raw,
                        }
                        postcheck_debug["postcheck_helper_contamination_recovered"] = {
                            "role": role_key,
                            "observed_value": observed_dest_raw[:160],
                            "active_editor_value": active_editor_value[:80],
                        }
                        log.info(
                            "scenario.route_fill.location_postcheck_recovered role=%s method=helper_contamination active_value=%s",
                            role_key,
                            active_editor_value[:40],
                        )
                elif role_key == "origin":
                    observed_origin = str(
                        form_state.get("origin_text", form_state.get("origin_text_raw", "")) or ""
                    ).strip()
                    if (
                        observed_origin
                        and target_value
                        and confidence in {"medium", "high"}
                        and not sr._google_form_value_matches_airport(observed_origin, target_value)
                    ):
                        postcheck = {
                            "ok": False,
                            "reason": "origin_mismatch",
                            "confidence": confidence,
                            "observed_origin": observed_origin,
                        }
                    # If the combobox verification succeeded and the editor's active
                    # value equals the observed origin text, prefer the editor commit
                    # evidence to avoid false-positive mismatches on mixed-locale pages.
                    # Prefer combobox editor evidence (keyboard commit or prefilled value)
                    # when it matches the observed origin text to avoid false positives.
                    editor_evidence = active_editor_value or str(
                        combobox_debug_post.get("prefilled_value", "") or ""
                    ).strip()
                    if (
                        not bool(postcheck.get("ok"))
                        and combobox_verify_ok
                        and editor_evidence
                        and observed_origin.strip().lower() == editor_evidence.strip().lower()
                    ):
                        postcheck = {
                            "ok": True,
                            "reason": "origin_match_active_editor",
                            "confidence": confidence,
                            "observed_origin": observed_origin,
                        }
                    if (
                        not bool(postcheck.get("ok"))
                        and str(postcheck.get("reason", "") or "") == "origin_mismatch"
                        and sr._google_form_text_looks_date_like(observed_origin)
                        and combobox_verify_ok
                        and active_editor_value
                        and sr._google_form_value_matches_airport(active_editor_value, target_value)
                    ):
                        postcheck = {
                            "ok": True,
                            "reason": "origin_postcheck_cross_field_date_recovered",
                            "confidence": confidence,
                            "observed_origin": observed_origin,
                        }
                        postcheck_debug["postcheck_cross_field_date_recovered"] = {
                            "role": role_key,
                            "observed_value": observed_origin[:160],
                            "active_editor_value": active_editor_value[:80],
                        }
                        log.info(
                            "scenario.route_fill.location_postcheck_recovered role=%s method=cross_field_date active_value=%s",
                            role_key,
                            active_editor_value[:40],
                        )
                    if (
                        not bool(postcheck.get("ok"))
                        and str(postcheck.get("reason", "") or "") == "origin_mismatch"
                        and sr._google_form_text_looks_instructional_noise(observed_origin)
                        and combobox_verify_ok
                        and active_editor_value
                        and sr._google_form_value_matches_airport(active_editor_value, target_value)
                    ):
                        postcheck = {
                            "ok": True,
                            "reason": "origin_postcheck_helper_contamination_recovered",
                            "confidence": confidence,
                            "observed_origin": observed_origin,
                        }
                        postcheck_debug["postcheck_helper_contamination_recovered"] = {
                            "role": role_key,
                            "observed_value": observed_origin[:160],
                            "active_editor_value": active_editor_value[:80],
                        }
                        log.info(
                            "scenario.route_fill.location_postcheck_recovered role=%s method=helper_contamination active_value=%s",
                            role_key,
                            active_editor_value[:40],
                        )
            except Exception:
                postcheck = {"ok": True, "reason": "probe_error"}

            if (
                role_key == "dest"
                and str(postcheck.get("reason", "") or "") == "dest_placeholder"
                and str(postcheck.get("confidence", "") or "").strip().lower() == "low"
            ):
                # One bounded settle/re-probe: the combobox helper may have committed via
                # keyboard fallback while the route chip text is still transitioning.
                # Avoid failing immediately on a low-confidence placeholder probe.
                try:
                    page_obj = getattr(browser, "page", None)
                    if page_obj is not None:
                        if hasattr(page_obj, "wait_for_timeout"):
                            page_obj.wait_for_timeout(180)
                        else:
                            time.sleep(0.18)
                        form_state_retry = sr._extract_google_flights_form_state(probe_target)
                        postcheck_debug["settle_retry_form_state"] = {
                            "confidence": str(form_state_retry.get("confidence", "") or ""),
                            "dest_text_raw": str(form_state_retry.get("dest_text_raw", form_state_retry.get("dest_text", "")) or ""),
                            "dest_is_placeholder": bool(form_state_retry.get("dest_is_placeholder")),
                        }
                        retry_confidence = str(
                            form_state_retry.get("confidence", postcheck.get("confidence", "low")) or "low"
                        ).strip().lower()
                        observed_dest_retry = str(
                            form_state_retry.get("dest_text_raw", form_state_retry.get("dest_text", "")) or ""
                        ).strip()
                        dest_retry_placeholder = bool(form_state_retry.get("dest_is_placeholder")) or sr._is_google_dest_placeholder(
                            observed_dest_retry
                        )
                        if not dest_retry_placeholder and observed_dest_retry:
                            postcheck = {
                                "ok": True,
                                "reason": "dest_placeholder_settled",
                                "confidence": retry_confidence,
                                "observed_dest_raw": observed_dest_retry,
                            }
                            log.info(
                                "scenario.route_fill.location_postcheck_recovered role=%s confidence=%s observed_dest_raw=%s",
                                role_key,
                                retry_confidence,
                                observed_dest_retry[:80],
                            )
                        else:
                            postcheck["confidence"] = retry_confidence
                            postcheck["observed_dest_raw"] = observed_dest_retry or str(
                                postcheck.get("observed_dest_raw", "") or ""
                            )
                            postcheck["retry_probe"] = "placeholder_persisted"
                except Exception:
                    postcheck["retry_probe"] = "error"

            if (
                role_key == "dest"
                and str(postcheck.get("reason", "") or "") == "dest_placeholder"
                and alias_query_candidates
            ):
                postcheck = _finalize_dest_editor_commit_once(postcheck)

            if (
                role_key == "dest"
                and str(postcheck.get("reason", "") or "") == "dest_placeholder"
                and alias_query_candidates
            ):
                # More effective bounded recovery than additional waits: retry destination
                # combobox commit once with a provider alias/city token when raw IATA appears
                # to "commit" but the route chip remains placeholder.
                alias_used = ""
                for alias_query in alias_query_candidates[:1]:
                    try:
                        ok_alias, selector_used_alias = _attempt_combobox_fill(alias_query)
                    except Exception:
                        ok_alias, selector_used_alias = (False, "")
                    if not ok_alias:
                        continue
                    alias_used = alias_query
                    try:
                        page_obj = getattr(browser, "page", None)
                        if page_obj is not None:
                            if hasattr(page_obj, "wait_for_timeout"):
                                page_obj.wait_for_timeout(220)
                            else:
                                time.sleep(0.22)
                        form_state_alias = sr._extract_google_flights_form_state(probe_target)
                        postcheck_debug["alias_retry_form_state"] = {
                            "confidence": str(form_state_alias.get("confidence", "") or ""),
                            "dest_text_raw": str(form_state_alias.get("dest_text_raw", form_state_alias.get("dest_text", "")) or ""),
                            "dest_is_placeholder": bool(form_state_alias.get("dest_is_placeholder")),
                            "alias_query": alias_query,
                        }
                        alias_conf = str(form_state_alias.get("confidence", "low") or "low").strip().lower()
                        alias_dest_raw = str(
                            form_state_alias.get("dest_text_raw", form_state_alias.get("dest_text", "")) or ""
                        ).strip()
                        alias_dest_placeholder = bool(form_state_alias.get("dest_is_placeholder")) or sr._is_google_dest_placeholder(
                            alias_dest_raw
                        )
                        alias_dest_mismatch = (
                            bool(alias_dest_raw)
                            and target_value
                            and alias_conf in {"medium", "high"}
                            and not sr._google_form_value_matches_airport(alias_dest_raw, target_value)
                            and not sr._contains_any_token(alias_dest_raw, alias_dest_raw.upper(), {alias_query})
                        )
                        if not alias_dest_placeholder and not alias_dest_mismatch:
                            postcheck = {
                                "ok": True,
                                "reason": "dest_alias_retry_recovered",
                                "confidence": alias_conf,
                                "observed_dest_raw": alias_dest_raw,
                                "alias_query": alias_query,
                            }
                            selector_used = selector_used_alias or selector_used
                            log.info(
                                "scenario.route_fill.location_alias_retry_recovered role=%s alias=%s confidence=%s observed_dest_raw=%s",
                                role_key,
                                alias_query[:40],
                                alias_conf,
                                alias_dest_raw[:80],
                            )
                            break
                        postcheck["alias_query"] = alias_query
                        postcheck["retry_probe"] = str(postcheck.get("retry_probe", "") or "placeholder_persisted")
                    except Exception:
                        postcheck["alias_query"] = alias_query
                        postcheck["retry_probe"] = "alias_retry_probe_error"
                if alias_used:
                    verify_tokens = list(dict.fromkeys(verify_tokens + [alias_used]))
                    verify_tokens_combobox = list(
                        dict.fromkeys(verify_tokens_combobox + [alias_used])
                    )

            if (
                role_key == "dest"
                and str(postcheck.get("reason", "") or "") == "dest_placeholder"
                and str(postcheck.get("confidence", "") or "").strip().lower() == "low"
            ):
                # Comprehensive fallback for results pages: route-chip extraction can read
                # top-nav "目的地を探索" labels while the page already contains strong
                # HND-ITM results itinerary metadata. Accept only strong results evidence.
                try:
                    html_probe = str(browser.content() or "")
                except Exception:
                    html_probe = ""
                if sr._google_results_itinerary_matches_expected(
                    html_probe,
                    expected_origin=str(expected_origin or ""),
                    expected_dest=str(target_value or ""),
                    expected_depart=str(expected_depart or ""),
                ):
                    postcheck = {
                        "ok": True,
                        "reason": "dest_results_itinerary_recovered",
                        "confidence": "medium",
                        "observed_dest_raw": str(postcheck.get("observed_dest_raw", "") or ""),
                        "retry_probe": str(postcheck.get("retry_probe", "") or "placeholder_persisted") + "|results_itinerary",
                    }
                    postcheck_debug["results_itinerary_match"] = True
                    log.info(
                        "scenario.route_fill.location_postcheck_recovered role=%s method=results_itinerary expected_origin=%s expected_dest=%s expected_depart=%s",
                        role_key,
                        str(expected_origin or "")[:8],
                        str(target_value or "")[:8],
                        str(expected_depart or "")[:12],
                    )
                else:
                    postcheck_debug["results_itinerary_match"] = False

            if not bool(postcheck.get("ok")):
                observed_origin_for_evidence = str(postcheck.get("observed_origin", "") or "")
                observed_dest_for_evidence = str(postcheck.get("observed_dest_raw", "") or "")
                contamination_text = observed_origin_for_evidence or observed_dest_for_evidence
                postcheck_contamination = sr._google_form_text_looks_instructional_noise(
                    contamination_text
                )
                postcheck_cross_field_date = sr._google_form_text_looks_date_like(contamination_text)
                observed_kind = ""
                if postcheck_contamination:
                    observed_kind = "instructional_helper"
                elif postcheck_cross_field_date:
                    observed_kind = "date_value_cross_field"
                result["ok"] = False
                result["selector_used"] = ""
                result["committed"] = False
                result["reason"] = f"combobox_fill_unverified_{role_key}_{postcheck.get('reason', 'postcheck_failed')}"
                result["evidence"] = {
                    "verify.role": role_key,
                    "verify.postcheck_reason": str(postcheck.get("reason", "") or ""),
                    "verify.confidence": str(postcheck.get("confidence", "") or ""),
                    "verify.observed_origin": str(postcheck.get("observed_origin", "") or ""),
                    "verify.observed_dest_raw": str(postcheck.get("observed_dest_raw", "") or ""),
                    "verify.postcheck_contamination": bool(postcheck_contamination),
                    "verify.postcheck_observed_kind": observed_kind,
                    "verify.postcheck_cross_field_date": bool(postcheck_cross_field_date),
                    "verify.retry_probe": str(postcheck.get("retry_probe", "") or ""),
                    "verify.alias_query": str(postcheck.get("alias_query", "") or ""),
                }
                try:
                    combobox_debug_post = dict(
                        getattr(browser, "_last_google_flights_combobox_debug", {}) or {}
                    )
                except Exception:
                    combobox_debug_post = {}
                _append_combobox_debug_evidence(result["evidence"], combobox_debug_post)
                log.warning(
                    "scenario.route_fill.location_postcheck_failed role=%s reason=%s confidence=%s observed_origin=%s observed_dest_raw=%s",
                    role_key,
                    str(postcheck.get("reason", "") or ""),
                    str(postcheck.get("confidence", "") or ""),
                    str(postcheck.get("observed_origin", "") or "")[:80],
                    str(postcheck.get("observed_dest_raw", "") or "")[:80],
                )
                if str(debug_run_id or "").strip():
                    try:
                        combobox_debug = {}
                        try:
                            combobox_debug = dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {})
                        except Exception:
                            combobox_debug = {}
                        sr._write_json_artifact_snapshot(
                            debug_run_id,
                            f"google_route_fill_{role_key}_postcheck_failed.json",
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "stage": "route_fill_postcheck_failed",
                                "service": "google_flights",
                                "attempt": int(debug_attempt) + 1,
                                "turn": int(debug_turn) + 1,
                                "step_index": int(debug_step_index),
                                "role": role_key,
                                "target_value": target_value,
                                "selector_used": str(selector_used or ""),
                                "activation_selectors": list(role_selectors[:5]),
                                "input_selectors": list(input_selectors[:3]),
                                "result_reason": str(result.get("reason", "") or ""),
                                "postcheck": dict(postcheck),
                                "verify_tokens": list(verify_tokens[:8]),
                                "verify_tokens_combobox": list(verify_tokens_combobox[:8]),
                                "debug": dict(postcheck_debug),
                                "combobox_debug": combobox_debug,
                            },
                        )
                        sr._write_html_snapshot(
                            "google_flights",
                            str(browser.content() or ""),
                            stage=f"route_fill_{role_key}_postcheck_failed",
                            run_id=debug_run_id,
                        )
                        sr._write_image_snapshot(
                            browser,
                            "google_flights",
                            stage=f"route_fill_{role_key}_postcheck_failed",
                            run_id=debug_run_id,
                        )
                    except Exception:
                        pass
                return result

            result["ok"] = True
            result["selector_used"] = selector_used
            result["committed"] = True
            result["reason"] = "combobox_fill_success"
            log.info(
                "scenario.route_fill.location role=%s committed=%s selector=%s act_count=%d inp_count=%d",
                role_key,
                True,
                selector_used[:80] if selector_used else "",
                len(role_selectors[:5]),
                len(input_selectors[:3]),
            )
            try:
                combobox_debug = dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {})
            except Exception:
                combobox_debug = {}
            try:
                activation_selector_used = str(combobox_debug.get("activation_selector_used", "") or "").strip()
                if activation_selector_used and not activation_selector_used.startswith(":"):
                    sr.promote_selector_hint(
                        site="google_flights",
                        action="route_fill_activation",
                        role=role_key,
                        selector=activation_selector_used,
                        display_lang=display_lang_hint,
                        locale=locale_for_tokens,
                        source="runtime_verified",
                    )
                    log.info(
                        "selector_hints.promote site=google_flights action=route_fill_activation role=%s selector=%s lang=%s",
                        role_key,
                        activation_selector_used[:120],
                        display_lang_hint or "",
                    )
                input_selector_used = str(combobox_debug.get("input_selector_used", "") or "").strip()
                if (
                    input_selector_used
                    and not input_selector_used.startswith(":")
                    and not bool(combobox_debug.get("generic_input_selector_used"))
                    and sr._google_route_fill_input_selector_hint_is_plausible(role_key, input_selector_used)
                ):
                    sr.promote_selector_hint(
                        site="google_flights",
                        action="route_fill_input",
                        role=role_key,
                        selector=input_selector_used,
                        display_lang=display_lang_hint,
                        locale=locale_for_tokens,
                        source="runtime_verified",
                    )
                    log.info(
                        "selector_hints.promote site=google_flights action=route_fill_input role=%s selector=%s lang=%s",
                        role_key,
                        input_selector_used[:120],
                        display_lang_hint or "",
                    )
                elif input_selector_used and not input_selector_used.startswith(":"):
                    log.info(
                        "selector_hints.promote_skipped site=google_flights action=route_fill_input role=%s selector=%s reason=nonsemantic_selector",
                        role_key,
                        input_selector_used[:120],
                    )
            except Exception:
                pass
        else:
            result["ok"] = False
            result["reason"] = "combobox_fill_failed"
            combobox_debug = {}
            try:
                combobox_debug = dict(getattr(browser, "_last_google_flights_combobox_debug", {}) or {})
            except Exception:
                combobox_debug = {}
            if combobox_debug:
                evidence = {}
                failure_stage = str(combobox_debug.get("failure_stage", "") or "").strip()
                failure_selector = str(combobox_debug.get("failure_selector", "") or "").strip()
                if failure_stage:
                    evidence["combobox.failure_stage"] = failure_stage
                if failure_selector:
                    evidence["combobox.failure_selector"] = failure_selector
                if isinstance(combobox_debug.get("failure_remaining_ms"), (int, float)):
                    evidence["combobox.failure_remaining_ms"] = int(
                        combobox_debug.get("failure_remaining_ms")
                    )
                if isinstance(combobox_debug.get("failure_reserve_ms"), (int, float)):
                    evidence["combobox.failure_reserve_ms"] = int(
                        combobox_debug.get("failure_reserve_ms")
                    )
                activation_order = combobox_debug.get("activation_order")
                if isinstance(activation_order, list) and activation_order:
                    evidence["combobox.activation_order"] = [
                        str(s)[:120] for s in activation_order[:8]
                    ]
                activation_attempts = combobox_debug.get("activation_attempts")
                if isinstance(activation_attempts, list) and activation_attempts:
                    compact_attempts = []
                    for item in activation_attempts[:8]:
                        if not isinstance(item, dict):
                            continue
                        compact_attempts.append(
                            {
                                str(k)[:40]: (
                                    str(v)[:120] if isinstance(v, str) else v
                                )
                                for k, v in item.items()
                            }
                        )
                    if compact_attempts:
                        evidence["combobox.activation_attempts"] = compact_attempts
                activation_used = str(
                    combobox_debug.get("activation_selector_used", "") or ""
                ).strip()
                if activation_used:
                    evidence["combobox.activation_selector_used"] = activation_used
                input_source = str(combobox_debug.get("input_source", "") or "").strip()
                if input_source:
                    evidence["combobox.input_source"] = input_source
                _append_combobox_debug_evidence(evidence, combobox_debug)
                if evidence:
                    result["evidence"] = evidence
            if str(debug_run_id or "").strip():
                try:
                    _debug_route_fill_selector_probe(
                        "combobox_failed",
                        extra={
                            "result": dict(result),
                            "combobox_debug": combobox_debug,
                            "verify_tokens": list(verify_tokens[:8]),
                            "verify_tokens_combobox": list(verify_tokens_combobox[:8]),
                        },
                    )
                except Exception:
                    pass
            log.warning(
                "scenario.route_fill.location role=%s committed=%s candidates=%d failure_stage=%s",
                role_key,
                False,
                len(role_selectors),
                str(
                    (
                        (result.get("evidence") or {}).get("combobox.failure_stage", "")
                        if isinstance(result.get("evidence"), dict)
                        else ""
                    )
                ),
            )
    except Exception as exc:
        result["ok"] = False
        result["reason"] = f"combobox_exception:{str(exc)[:100]}"
        log.error(
            "scenario.route_fill.location_exception role=%s error=%s",
            role_key,
            str(exc)[:200],
        )

    return result


def _google_fill_date_via_picker(
    browser,
    *,
    role: str,
    value: str,
    selectors,
    locale_hint: str = "",
    timeout_ms: Optional[int] = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Fill date using Google Flights date picker (calendar-based selection)."""
    # Lazy import to avoid circular dependency
    import core.scenario_runner as sr

    role_key = str(role or "").strip().lower()
    timeout_value = sr._normalize_selector_timeout_ms(
        timeout_ms,
        site_key="google_flights",
        action=f"google_fill_date_{role_key}",
    )
    if timeout_value is None:
        timeout_value = sr._normalize_selector_timeout_ms(
            int(get_threshold("browser_action_selector_timeout_ms_google_flights", 1500)),
            site_key="google_flights",
            action=f"google_fill_date_{role_key}",
        )
    if timeout_value is None:
        timeout_value = 1200

    locale_for_tokens = str(locale_hint or sr._current_mimic_locale() or "").strip().lower()
    role_selectors = sr._dedupe_selectors(sr._selector_candidates(selectors))

    return _google_fill_date_via_picker_impl(
        browser,
        role=role_key,
        value=str(value or ""),
        timeout_ms=timeout_value,
        role_selectors=role_selectors,
        locale_hint=locale_for_tokens,
        logger=log,
        deadline=deadline,
    )


def _google_search_and_commit(
    browser,
    *,
    selectors,
    timeout_ms: Optional[int] = None,
    deadline: Optional[float] = None,
    page_url: str = "",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
) -> Dict[str, Any]:
    """Fast-path search commit for Google Flights: try Enter first, then click + verify."""
    # Lazy import to avoid circular dependency
    import core.scenario_runner as sr

    commit_strategy = "unknown"
    used_selector = None
    post_click_wait_ms = 0
    results_signal_found = False
    error = None
    commit_start = time.monotonic()
    search_click_attempts = 0
    click_elapsed_ms = 0
    enter_elapsed_ms = 0
    results_candidates_count = 0
    selector_candidates_count = 0
    clickable_candidates_count = 0
    post_click_ready_timeout_ms_last = 0

    # Get thresholds
    post_enter_settle_ms = int(get_threshold("browser_post_enter_settle_ms", 150))
    contextual_min_wait_ms = int(
        get_threshold("browser_search_results_contextual_min_wait_ms", 2500)
    )
    results_wait_timeout_ms = int(
        get_threshold("browser_search_results_wait_timeout_ms", 5000)
    )
    if timeout_ms is not None:
        results_wait_timeout_ms = min(results_wait_timeout_ms, timeout_ms)
    if deadline is not None:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("wall_clock_timeout google_search_commit")
        results_wait_timeout_ms = min(results_wait_timeout_ms, remaining_ms)

    route_ctx_available = bool(origin and dest and depart)
    if route_ctx_available:
        # Keep contextual-result waits above tiny step selector timeouts.
        # Search transitions on Google Flights can exceed per-selector budgets
        # even when route/date fields are already bound.
        results_wait_timeout_ms = max(
            int(results_wait_timeout_ms or 0),
            max(300, int(contextual_min_wait_ms or 0)),
        )

    def _contextual_deeplink_probe_url(current_page_url: str) -> str:
        """Use deeplink probe only when a deeplink URL is actually available."""
        for candidate in (current_page_url, page_url):
            url_text = str(candidate or "").strip()
            if url_text and "flt=" in url_text:
                return url_text
        return ""

    def _safe_browser_url() -> str:
        try:
            page_obj = getattr(browser, "page", None)
            page_now_url = getattr(page_obj, "url", "") if page_obj is not None else ""
            if callable(page_now_url):
                page_now_url = page_now_url()
            return str(page_now_url or "")
        except Exception:
            return ""

    def _url_fragment(text_url: str) -> str:
        try:
            return str(urlparse(str(text_url or "")).fragment or "")
        except Exception:
            return ""

    def _reload_error_surface_flags(html_now: str) -> Dict[str, Any]:
        text = str(html_now or "")
        lower = text.lower()
        return {
            "reload_error_surface": (
                ("oops, something went wrong" in lower)
                or ("no results returned." in lower and "reload" in lower)
            )
        }

    def _capture_commit_probe() -> Dict[str, Any]:
        html_now = ""
        try:
            html_now = str(browser.content() or "")
        except Exception:
            html_now = ""
        current_page_url = _safe_browser_url()
        contextual_ready = False
        contextual_probe_ok = False
        contextual_probe_reason = ""
        if html_now:
            try:
                contextual_ready = bool(
                    sr._is_results_ready(
                        html_now,
                        site_key="google_flights",
                        origin=origin,
                        dest=dest,
                        depart=depart,
                        return_date=return_date,
                    )
                )
            except Exception:
                contextual_ready = False
        probe_url = _contextual_deeplink_probe_url(current_page_url)
        if route_ctx_available and probe_url and html_now:
            try:
                contextual_probe_ok, contextual_probe_reason = sr._google_deeplink_probe_status(
                    html_now, probe_url
                )
            except Exception:
                contextual_probe_ok = False
                contextual_probe_reason = ""
        reload_probe = _reload_error_surface_flags(html_now)
        reload_visible_count = 0
        try:
            page_obj = getattr(browser, "page", None)
            if page_obj is not None:
                reload_visible_count = _visible_button_text_match_count(
                    ["Reload", "再読み込み"],
                    max_probe=24,
                )
        except Exception:
            reload_visible_count = 0
        results_probe_ready = bool(contextual_ready or contextual_probe_ok)
        if contextual_probe_ok:
            results_probe_reason = str(contextual_probe_reason or "ok")
        elif contextual_ready:
            results_probe_reason = "contextual_ready"
        else:
            results_probe_reason = str(contextual_probe_reason or "")
        return {
            "page_url": current_page_url,
            "page_url_fragment": _url_fragment(current_page_url),
            "html_len": len(html_now or ""),
            "contextual_ready": bool(contextual_ready),
            "results_probe_ready": bool(results_probe_ready),
            "results_probe_reason": results_probe_reason,
            "deeplink_probe_ok": bool(contextual_probe_ok),
            "deeplink_probe_reason": str(contextual_probe_reason or ""),
            "reload_error_surface": bool(reload_probe.get("reload_error_surface")),
            "reload_button_visible_count": int(reload_visible_count or 0),
        }

    def _contextual_results_ready() -> bool:
        """Google-specific ready check after a search commit attempt."""
        try:
            html_now = browser.content()
        except Exception:
            return False
        if sr._is_results_ready(
            html_now,
            site_key="google_flights",
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
        ):
            return True
        probe_url = _contextual_deeplink_probe_url(_safe_browser_url())
        if route_ctx_available and probe_url:
            try:
                probe_ok, probe_reason = sr._google_deeplink_probe_status(html_now, probe_url)
                if probe_ok:
                    return True
                log.debug(
                    "scenario.search_commit.contextual_probe_not_ready reason=%s",
                    probe_reason,
                )
            except Exception:
                pass
        return False

    def _with_commit_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["elapsed_ms"] = int((time.monotonic() - commit_start) * 1000)
        payload["search_click_attempts"] = int(search_click_attempts)
        payload["selector_candidates_count"] = int(selector_candidates_count)
        payload["clickable_candidates_count"] = int(clickable_candidates_count)
        payload["results_candidates_count"] = int(results_candidates_count)
        payload["results_wait_timeout_ms"] = int(results_wait_timeout_ms or 0)
        payload["post_click_ready_timeout_ms"] = int(post_click_ready_timeout_ms_last or 0)
        payload["click_elapsed_ms"] = int(click_elapsed_ms or 0)
        payload["enter_elapsed_ms"] = int(enter_elapsed_ms or 0)
        payload["route_ctx_available"] = bool(route_ctx_available)
        return payload

    def _selector_candidates_exact_first(raw_selectors) -> List[str]:
        ordered = sr._selector_candidates(raw_selectors)
        exact_pref = [
            "button[aria-label='Search']",
            "[role='button'][aria-label='Search']",
            "button[aria-label='検索']",
            "[role='button'][aria-label='検索']",
        ]
        out: List[str] = []
        seen: set[str] = set()
        for item in exact_pref + ordered:
            s = str(item or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _visible_selector_match_indexes(selector: str, *, max_probe: int = 6) -> List[int]:
        try:
            page_obj = getattr(browser, "page", None)
            if page_obj is None:
                return []
            return list(
                page_obj.evaluate(
                    """([sel, maxProbe]) => {
                        const nodes = Array.from(document.querySelectorAll(sel || "")).slice(0, Math.max(1, maxProbe || 1));
                        const out = [];
                        for (let i = 0; i < nodes.length; i++) {
                          const el = nodes[i];
                          if (!(el instanceof Element)) continue;
                          const cs = window.getComputedStyle(el);
                          const r = el.getBoundingClientRect();
                          const visible = !!(
                            cs &&
                            cs.display !== "none" &&
                            cs.visibility !== "hidden" &&
                            parseFloat(cs.opacity || "1") > 0 &&
                            r.width > 0 &&
                            r.height > 0
                          );
                          if (visible) out.push(i);
                        }
                        return out;
                    }""",
                    [str(selector or ""), int(max_probe)],
                )
                or []
            )
        except Exception:
            return []

    def _visible_button_text_match_count(
        text_tokens: List[str], *, max_probe: int = 24
    ) -> int:
        """CSS-safe visible button probe by text/aria-label.

        `page.evaluate()` uses native `querySelectorAll`, so Playwright-only selectors
        like `:has-text(...)` fail there. This helper scans generic button elements and
        filters by visible text/aria-label instead.
        """
        try:
            page_obj = getattr(browser, "page", None)
            if page_obj is None:
                return 0
            norm_tokens = [
                str(token or "").strip().lower()
                for token in (text_tokens or [])
                if str(token or "").strip()
            ]
            if not norm_tokens:
                return 0
            count = page_obj.evaluate(
                """([tokens, maxProbe]) => {
                    const wanted = Array.isArray(tokens)
                      ? tokens.map((t) => String(t || "").toLowerCase()).filter(Boolean)
                      : [];
                    if (!wanted.length) return 0;
                    const nodes = Array.from(document.querySelectorAll("button, [role='button']")).slice(
                      0,
                      Math.max(1, Number(maxProbe || 1))
                    );
                    let hits = 0;
                    for (const el of nodes) {
                      if (!(el instanceof Element)) continue;
                      const cs = window.getComputedStyle(el);
                      const r = el.getBoundingClientRect();
                      const visible = !!(
                        cs &&
                        cs.display !== "none" &&
                        cs.visibility !== "hidden" &&
                        parseFloat(cs.opacity || "1") > 0 &&
                        r.width > 0 &&
                        r.height > 0
                      );
                      if (!visible) continue;
                      const text = String(el.textContent || "").toLowerCase();
                      const aria = String(el.getAttribute("aria-label") || "").toLowerCase();
                      if (wanted.some((tok) => text.includes(tok) || aria.includes(tok))) {
                        hits += 1;
                      }
                    }
                    return hits;
                }""",
                [norm_tokens, int(max_probe)],
            )
            return max(0, int(count or 0))
        except Exception:
            return 0

    def _click_selector_visible_first(selector: str, *, timeout_ms: int) -> tuple[bool, str]:
        """Try direct click on visible duplicate match before browser.click fallback."""
        page_obj = getattr(browser, "page", None)
        if page_obj is None:
            return False, ""
        visible_indexes = _visible_selector_match_indexes(selector, max_probe=6)
        if not visible_indexes:
            return False, ""
        idx = int(visible_indexes[0])
        try:
            log.info(
                "scenario.search_click.visible_resolved selector=%s idx=%d visible_count=%d",
                str(selector or "")[:100],
                idx,
                len(visible_indexes),
            )
            locator = page_obj.locator(selector).nth(idx)
            locator.click(timeout=max(150, int(timeout_ms)), no_wait_after=True)
            return True, str(selector or "")
        except Exception as exc:
            log.debug(
                "scenario.search_click.visible_resolved_failed selector=%s idx=%d error=%s",
                str(selector or "")[:100],
                idx,
                exc,
            )
            return False, ""

    def _try_reload_error_surface(*, start_probe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not bool((start_probe or {}).get("reload_error_surface")):
            return None
        if int((start_probe or {}).get("reload_button_visible_count") or 0) <= 0:
            return None
        reload_selectors = [
            "button:has-text('Reload')",
            "[role='button']:has-text('Reload')",
            "button[aria-label='Reload']",
            "[role='button'][aria-label='Reload']",
            "button:has-text('再読み込み')",
            "[role='button']:has-text('再読み込み')",
        ]
        start_time = time.monotonic()
        click_timeout_ms = min(2000, max(400, results_wait_timeout_ms // 2))
        clicked_selector = None
        last_reload_error = None
        for selector in reload_selectors:
            try:
                ok, used = _click_selector_visible_first(selector, timeout_ms=click_timeout_ms)
                if ok:
                    clicked_selector = used
                else:
                    browser.click(selector, timeout_ms=click_timeout_ms, no_wait_after=True)
                    clicked_selector = selector
                log.info("scenario.search_commit.reload_click_ok selector=%s", str(clicked_selector)[:100])
                break
            except Exception as exc:
                last_reload_error = exc
                continue
        if not clicked_selector:
            if last_reload_error is not None:
                log.info("scenario.search_commit.reload_click_fail error=%s", last_reload_error)
            return None
        try:
            browser.page.wait_for_timeout(int(get_threshold("browser_post_click_settle_wait_ms", 200)))
        except Exception:
            pass
        if _wait_for_results_transition(start_time=start_time):
            probe_post_reload = _capture_commit_probe()
            return _with_commit_metrics({
                "ok": True,
                "strategy": "reload_then_verify",
                "selector_used": clicked_selector,
                "post_click_wait_ms": post_click_wait_ms,
                "results_signal_found": True,
                "probe_pre": probe_pre,
                "probe_post": probe_post_reload,
                "url_changed": probe_pre.get("page_url") != probe_post_reload.get("page_url"),
                "fragment_changed": probe_pre.get("page_url_fragment") != probe_post_reload.get("page_url_fragment"),
            })
        log.info("scenario.search_commit.reload_no_results wait_ms=%d", post_click_wait_ms)
        return _with_commit_metrics({
            "ok": False,
            "strategy": "reload_then_verify",
            "selector_used": clicked_selector,
            "post_click_wait_ms": post_click_wait_ms,
            "results_signal_found": False,
            "error": "reload_no_results_transition",
            "probe_pre": probe_pre,
            "probe_post": _capture_commit_probe(),
            "url_changed": False,
            "fragment_changed": False,
        })

    def _wait_for_results_transition(*, start_time: float) -> bool:
        """Bounded wait for contextual results after Enter/click."""
        nonlocal post_click_wait_ms, used_selector
        if route_ctx_available:
            end_time = start_time + max(0.15, results_wait_timeout_ms / 1000.0)
            while time.monotonic() < end_time:
                if _contextual_results_ready():
                    post_click_wait_ms = int((time.monotonic() - start_time) * 1000)
                    used_selector = used_selector or "[contextual_results]"
                    return True
                try:
                    browser.page.wait_for_timeout(150)
                except Exception:
                    break
            return False

        # Legacy fallback when route context is unavailable.
        for selector in results_candidates:
            try:
                remaining_ms = results_wait_timeout_ms - int((time.monotonic() - start_time) * 1000)
                if remaining_ms <= 100:
                    continue
                browser.wait(selector, timeout_ms=min(remaining_ms, 2000))
                post_click_wait_ms = int((time.monotonic() - start_time) * 1000)
                used_selector = selector
                return True
            except Exception:
                continue
        return False

    probe_pre = _capture_commit_probe()

    # If the page already transitioned to a valid Google results state before this
    # planned search-commit step runs, do not click again. Repeated Search clicks can
    # provoke error surfaces and hide the original success.
    # IMPORTANT: only skip if we're ACTUALLY on the results page (URL must contain /search)
    # Detect false positives where the form page shows a results preview/flyover.
    pre_url = str(probe_pre.get("page_url") or "").lower()
    if bool(probe_pre.get("results_probe_ready")) and "/search" in pre_url:
        log.info(
            "scenario.search_commit.already_ready_pre_click reason=%s",
            str(probe_pre.get("results_probe_reason", "") or ""),
        )
        return _with_commit_metrics({
            "ok": True,
            "strategy": "already_ready_pre_click",
            "selector_used": None,
            "post_click_wait_ms": 0,
            "results_signal_found": True,
            "probe_pre": probe_pre,
            "probe_post": dict(probe_pre),
            "url_changed": False,
            "fragment_changed": False,
        })

    # Strategy 0: if Google results page is in explicit error state, try Reload first.
    reload_result = _try_reload_error_surface(start_probe=probe_pre)
    if isinstance(reload_result, dict) and bool(reload_result.get("ok")):
        return reload_result

    # Get wait selectors to detect results
    results_selectors = sr._service_wait_fallbacks("google_flights")
    results_candidates = sr._selector_candidates(results_selectors)
    if not results_candidates:
        results_candidates = ["[role='main']", "main"]
    # [role='main'] is present on Google Flights homepage/explore surfaces and is not
    # a reliable search-results transition signal.
    results_candidates = [
        sel for sel in results_candidates
        if str(sel or "").strip().lower() not in {"[role='main']", "main"}
    ]
    results_candidates_count = len(results_candidates)

    # Strategy 1: Click search button (prefer explicit commit to avoid false Enter transitions).
    commit_strategy = "click_button"
    selector_candidates = _selector_candidates_exact_first(selectors)
    selector_candidates_count = len(selector_candidates)
    clickable_candidates = [
        selector for selector in selector_candidates
        if sr._is_clickable_selector_candidate(selector)
    ]
    if clickable_candidates:
        selector_candidates = clickable_candidates
        clickable_candidates_count = len(selector_candidates)
    else:
        clickable_candidates_count = 0

    search_click_start = time.monotonic()
    last_click_error = None
    for selector in selector_candidates:
        search_click_attempts += 1
        try:
            click_timeout_ms = min(3000, max(300, results_wait_timeout_ms // 2))
            # SPA-safe click: no_wait_after=True prevents waiting for navigation
            log.info(
                "scenario.search_click.attempt selector=%s timeout_ms=%d click_mode=spa_safe",
                selector[:100] if selector else "",
                click_timeout_ms,
            )
            clicked_fast, clicked_selector = _click_selector_visible_first(
                str(selector or ""),
                timeout_ms=click_timeout_ms,
            )
            if clicked_fast:
                used_selector = clicked_selector
            else:
                browser.click(
                    selector,
                    timeout_ms=click_timeout_ms,
                    no_wait_after=True,  # SPA-safe: don't wait for navigation
                )
                used_selector = selector
            click_elapsed_ms = int((time.monotonic() - search_click_start) * 1000)
            last_click_error = None
            log.info("scenario.search_click.ok selector=%s", selector[:100] if selector else "")
            break
        except Exception as exc:
            if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
                raise
            log.debug("scenario.search_click.attempt_failed selector=%s error=%s", selector[:100] if selector else "", exc)
            last_click_error = exc

    click_selector_used = used_selector
    click_selector_error = last_click_error

    if click_selector_used is not None:
        # Wait for results signal after click
        post_click_settle_wait_ms = int(get_threshold("browser_post_click_settle_wait_ms", 200))
        post_click_ready_timeout_ms = int(get_threshold("browser_post_click_ready_timeout_ms", 4000))
        if deadline is not None:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError("wall_clock_timeout google_search_commit")
            post_click_ready_timeout_ms = min(post_click_ready_timeout_ms, remaining_ms)
        post_click_ready_timeout_ms_last = int(post_click_ready_timeout_ms)

        try:
            browser.page.wait_for_timeout(post_click_settle_wait_ms)
        except Exception:
            pass
        search_click_elapsed_ms = int((time.monotonic() - search_click_start) * 1000)
        remaining_ready_ms = post_click_ready_timeout_ms - search_click_elapsed_ms
        log.info(
            "scenario.search_click.post_click elapsed_ms=%d remaining_ready_ms=%d",
            search_click_elapsed_ms,
            remaining_ready_ms,
        )
        if remaining_ready_ms > 100:
            original_wait_ms = results_wait_timeout_ms
            # Cap individual wait calls to remaining timeout to prevent indefinite waits
            capped_wait_ms = min(remaining_ready_ms, results_wait_timeout_ms)
            try:
                results_wait_timeout_ms = max(200, capped_wait_ms)
                results_signal_found = _wait_for_results_transition(start_time=search_click_start)
            finally:
                results_wait_timeout_ms = original_wait_ms
        if results_signal_found:
            used_selector = click_selector_used
            probe_post = _capture_commit_probe()
            log.info(
                "scenario.search_commit strategy=%s selector=%s settle_ms=%d wait_ms=%d",
                commit_strategy,
                used_selector,
                post_click_settle_wait_ms,
                post_click_wait_ms,
            )
            return _with_commit_metrics({
                "ok": True,
                "strategy": commit_strategy,
                "selector_used": used_selector,
                "post_click_wait_ms": post_click_wait_ms,
                "results_signal_found": True,
                "probe_pre": probe_pre,
                "probe_post": probe_post,
                "url_changed": probe_pre.get("page_url") != probe_post.get("page_url"),
                "fragment_changed": probe_pre.get("page_url_fragment") != probe_post.get("page_url_fragment"),
            })
        log.info(
            "scenario.search_commit no_results_after_click strategy=%s selector=%s wait_ms=%d",
            commit_strategy,
            click_selector_used,
            post_click_wait_ms,
        )

    # Strategy 2: Try pressing Enter if focus is inside an input/combobox.
    commit_strategy = "enter_then_verify"
    used_selector = None
    try:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("wall_clock_timeout google_search_commit")
        is_focused = browser.page.evaluate(
            "() => document.activeElement && "
            "(document.activeElement.tagName === 'INPUT' || "
            "document.activeElement.getAttribute('role') === 'combobox')"
        )
        if is_focused:
            start_time = time.monotonic()
            browser.page.keyboard.press('Enter')
            try:
                browser.page.wait_for_timeout(post_enter_settle_ms)
            except Exception:
                pass
            results_signal_found = _wait_for_results_transition(start_time=start_time)
            if results_signal_found:
                probe_post = _capture_commit_probe()
                log.info(
                    "scenario.search_commit strategy=%s selector=%s settle_ms=%d wait_ms=%d",
                    commit_strategy,
                    used_selector,
                    post_enter_settle_ms,
                    post_click_wait_ms,
                )
                enter_elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return _with_commit_metrics({
                    "ok": True,
                    "strategy": commit_strategy,
                    "selector_used": used_selector,
                    "post_click_wait_ms": post_click_wait_ms,
                    "results_signal_found": True,
                    "probe_pre": probe_pre,
                    "probe_post": probe_post,
                    "url_changed": probe_pre.get("page_url") != probe_post.get("page_url"),
                    "fragment_changed": probe_pre.get("page_url_fragment") != probe_post.get("page_url_fragment"),
                })
            log.info(
                "scenario.search_commit no_results_after_enter wait_ms=%d",
                post_click_wait_ms,
            )
    except Exception as exc:
        log.debug("scenario.search_commit enter_strategy_failed error=%s", exc)

    if click_selector_used is None:
        error = click_selector_error or RuntimeError("no_clickable_search_selector")
        log.warning("scenario.search_commit click_failed error=%s", error)
        return _with_commit_metrics({
            "ok": False,
            "strategy": "click_button",
            "selector_used": None,
            "error": str(error),
            "results_signal_found": False,
            "probe_pre": probe_pre,
            "probe_post": _capture_commit_probe(),
            "url_changed": False,
            "fragment_changed": False,
        })

    log.info(
        "scenario.search_commit strategy=%s used_selector=%s post_click_wait_ms=%d results_found=%s",
        commit_strategy,
        click_selector_used,
        post_click_wait_ms,
        False,
    )
    probe_post = _capture_commit_probe()
    return _with_commit_metrics({
        "ok": False,
        "strategy": commit_strategy,
        "selector_used": click_selector_used,
        "post_click_wait_ms": post_click_wait_ms,
        "results_signal_found": False,
        "error": "search_commit_no_results_transition",
        "probe_pre": probe_pre,
        "probe_post": probe_post,
        "url_changed": probe_pre.get("page_url") != probe_post.get("page_url"),
        "fragment_changed": probe_pre.get("page_url_fragment") != probe_post.get("page_url_fragment"),
    })
