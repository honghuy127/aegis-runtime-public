"""GoogleFlightsComboboxHelper: Extraction of fill_google_flights_combobox domain.

This module encapsulates the complex Google Flights combobox activation, input discovery,
typing, and verification logic from BrowserSession. The combobox interaction involves
multiple fallback paths, deadline-aware budgeting, and JavaScript-based heuristics for
robust field location and activation confirmation.
"""

import re
import time
from utils.logging import get_logger

log = get_logger(__name__)


class GoogleFlightsComboboxHelper:
    """Helper class for Google Flights combobox interaction and text entry."""

    def __init__(self, browser_session):
        """Initialize with reference to parent BrowserSession."""
        self.session = browser_session

    def fill_google_flights_combobox(
        self,
        activation_selectors: list = None,
        input_selectors: list = None,
        text: str = "",
        verify_tokens: list = None,
        timeout_ms: int = None,
    ) -> tuple:
        """Fill Google Flights combobox by ONLY activating container, then typing into real input.

        **CRITICAL**: Never fill() the [role='combobox'] container. Only click it to activate.
        After activation, locate the REAL input element and type into that.

        Args:
            activation_selectors: Selectors to click and open combobox (max 5, capped internally)
            input_selectors: Real input elements to type into (max 3, capped internally)
            text: Text value to type
            verify_tokens: Optional tokens to verify were accepted
            timeout_ms: Total timeout in milliseconds

        Returns:
            Tuple of (success: bool, activation_selector_used: str)
        """
        timeout_val = self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        deadline = time.monotonic() + (timeout_val / 1000.0)

        # CAP all selector lists strictly
        act_sel_list = [s for s in (activation_selectors or []) if isinstance(s, str) and s.strip()][:5]
        inp_sel_list = [s for s in (input_selectors or []) if isinstance(s, str) and s.strip()][:3]
        verify_token_list = [t for t in (verify_tokens or []) if isinstance(t, str) and t.strip()]
        generic_input_selector_used = False
        self.session._last_google_flights_combobox_debug = {
            "text": str(text or "")[:40],
            "timeout_ms": int(timeout_val),
            "activation_selectors": list(act_sel_list),
            "input_selectors": list(inp_sel_list),
            "verify_tokens": [str(t)[:40] for t in verify_token_list[:8]],
            "activation_selector_used": "",
            "input_selector_used": "",
            "input_source": "",
            "generic_input_selector_used": False,
            "keyboard_commit_attempted": False,
            "option_click_succeeded": False,
            "verify_ok": False,
            "verify_semantic_fallback": False,
            "commit_signal": {},
            "activation_visible_prefilter": {},
            "activation_open_probe": {},
            "activation_order": [],
            "activation_attempts": [],
            "prefilled_match": False,
            "prefilled_value": "",
            "prefilled_selector_used": "",
            "prefilled_match_token": "",
            "prefilled_probe": [],
            "editor_clear_attempted": False,
            "editor_cleared": False,
            "failure_stage": "",
            "failure_selector": "",
            "failure_remaining_ms": None,
            "failure_reserve_ms": None,
        }

        def _combobox_fail(stage: str, selector: str = "", **extra) -> tuple:
            try:
                debug = getattr(self.session, "_last_google_flights_combobox_debug", None)
                if isinstance(debug, dict):
                    debug["failure_stage"] = str(stage or "")[:80]
                    debug["failure_selector"] = str(selector or "")[:120]
                    debug["failure_remaining_ms"] = max(
                        0, int((deadline - time.monotonic()) * 1000)
                    )
                    for key, value in (extra or {}).items():
                        if key == "reserve_ms":
                            debug["failure_reserve_ms"] = (
                                int(value) if isinstance(value, (int, float)) else value
                            )
                        else:
                            debug[f"failure_{str(key)[:40]}"] = value
                    attempts = debug.get("activation_attempts")
                    if isinstance(attempts, list) and attempts:
                        log.info(
                            "gf.fill.combobox.fail_debug stage=%s selector=%s attempts=%s",
                            str(stage or "")[:40],
                            str(selector or "")[:80],
                            attempts[-4:],
                        )
            except Exception:
                pass
            return (False, "")

        if not act_sel_list or not text:
            log.warning("gf.fill.combobox.invalid_params act=%d inp=%d text_len=%d", len(act_sel_list), len(inp_sel_list), len(text) if text else 0)
            return _combobox_fail("invalid_params")

        log.info("gf.fill.combobox.start text=%s act_selectors=%d inp_selectors=%d timeout_ms=%d", text[:40], len(act_sel_list), len(inp_sel_list), timeout_val)

        def _page_evaluate_compat(script, arg=None):
            """Evaluate JS on the page with Playwright-version-compatible kwargs."""
            if not (self.session.page and hasattr(self.session.page, "evaluate")):
                raise RuntimeError("page_evaluate_unavailable")
            try:
                return self.session.page.evaluate(script, arg, timeout=200)
            except TypeError:
                # Some Playwright bindings/versions do not accept `timeout=` for page.evaluate().
                return self.session.page.evaluate(script, arg)

        def _bounded_sleep(max_ms: int) -> None:
            """Sleep briefly without spending past the combobox deadline."""
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            sleep_ms = max(0, min(int(max_ms or 0), remaining_ms))
            if sleep_ms <= 0:
                return
            time.sleep(sleep_ms / 1000.0)

        def _norm_probe_token(value: str) -> str:
            try:
                return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
            except Exception:
                return ""

        def _looks_placeholder_like_value(value: str) -> bool:
            raw = str(value or "").strip().lower()
            if not raw:
                return True
            placeholder_hints = (
                "where from",
                "where to",
                "departure",
                "return",
                "目的地を探索",
                "出発地を探索",
                "目的地",
                "出発地",
                "出発",
                "到着",
            )
            return raw in {h.strip().lower() for h in placeholder_hints}

        def _prefilled_value_matches(value: str) -> tuple[bool, str]:
            raw = str(value or "").strip()
            if not raw or _looks_placeholder_like_value(raw):
                return (False, "")
            raw_upper = raw.upper()
            raw_norm = _norm_probe_token(raw)
            if not raw_norm and not raw_upper:
                return (False, "")
            candidate_tokens = []
            seen_tokens = set()
            # Keep this bounded but larger than early debug previews; mixed-locale alias
            # coverage can place useful Latin-script city labels after localized tokens.
            for token in list(verify_token_list[:32]) + [str(text or "")]:
                tok = str(token or "").strip()
                if not tok:
                    continue
                marker = tok.lower()
                if marker in seen_tokens:
                    continue
                seen_tokens.add(marker)
                candidate_tokens.append(tok)
            for tok in candidate_tokens:
                tok_upper = tok.upper()
                tok_norm = _norm_probe_token(tok)
                # Avoid false positives such as token "To" matching the city "Tokyo".
                # Keep 3-letter IATA tokens (HND/ITM) but reject shorter label fragments.
                if len(tok_norm) < 3 and not (len(tok_upper) == 3 and tok_upper.isalpha()):
                    continue
                if tok_upper and tok_upper in raw_upper:
                    return (True, tok)
                if tok_norm and tok_norm in raw_norm:
                    return (True, tok)
            return (False, "")

        def _read_input_like_value(locator) -> str:
            try:
                if hasattr(locator, "input_value"):
                    value = locator.input_value(timeout=80)
                    if isinstance(value, str):
                        return value
            except Exception:
                pass
            try:
                if hasattr(locator, "get_attribute"):
                    value = locator.get_attribute("value", timeout=80)
                    if isinstance(value, str):
                        return value
            except TypeError:
                try:
                    value = locator.get_attribute("value")
                    if isinstance(value, str):
                        return value
                except Exception:
                    pass
            except Exception:
                pass
            return ""

        def _read_input_label_hints(locator) -> str:
            parts = []
            for attr in ("aria-label", "placeholder", "name", "title"):
                try:
                    if hasattr(locator, "get_attribute"):
                        value = locator.get_attribute(attr, timeout=80)
                    else:
                        value = None
                except TypeError:
                    try:
                        value = locator.get_attribute(attr)
                    except Exception:
                        value = None
                except Exception:
                    value = None
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            return " ".join(parts).strip()

        def _extract_role_label_hints_from_selectors() -> list[str]:
            hints = []
            seen = set()
            patterns = (
                re.compile(r"aria-label(?:\*|\^)?='([^']+)'"),
                re.compile(r'aria-label(?:\*|\^)?="([^"]+)"'),
                re.compile(r"placeholder(?:\*|\^)?='([^']+)'"),
                re.compile(r'placeholder(?:\*|\^)?="([^"]+)"'),
            )
            for selector in list(inp_sel_list[:4]) + list(act_sel_list[:4]):
                raw = str(selector or "")
                if not raw:
                    continue
                for pat in patterns:
                    for mm in pat.finditer(raw):
                        token = str(mm.group(1) or "").strip()
                        if not token:
                            continue
                        key = token.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        hints.append(token)
                        if len(hints) >= 8:
                            return hints
            return hints

        def _active_input_role_match() -> tuple[bool, str]:
            role_label_hints = _extract_role_label_hints_from_selectors()
            if not role_label_hints:
                return (False, "")
            try:
                probe = _page_evaluate_compat(
                    """
                    () => {
                      const e = document && document.activeElement;
                      if (!e) return { ok: false };
                      const tag = String(e.tagName || "").toLowerCase();
                      const isInput = tag === "input" || tag === "textarea";
                      if (!isInput) return { ok: false, tag };
                      let visible = false;
                      try {
                        const r = e.getBoundingClientRect ? e.getBoundingClientRect() : null;
                        visible = !!r && r.width > 0 && r.height > 0;
                      } catch (err) {}
                      const attrs = [];
                      for (const k of ["aria-label", "placeholder", "name", "title", "role"]) {
                        try {
                          const v = e.getAttribute ? e.getAttribute(k) : "";
                          if (v) attrs.push(String(v));
                        } catch (err) {}
                      }
                      return {
                        ok: true,
                        tag,
                        visible,
                        label: attrs.join(" "),
                      };
                    }
                    """
                )
            except Exception:
                return (False, "")
            if not isinstance(probe, dict) or not bool(probe.get("ok")):
                return (False, "")
            if not bool(probe.get("visible")):
                return (False, "")
            label_blob = str(probe.get("label", "") or "")
            lowered = label_blob.lower()
            if not lowered:
                return (False, "")
            for token in role_label_hints:
                tok = str(token or "").strip().lower()
                if tok and tok in lowered:
                    return (True, label_blob[:120])
            return (False, "")

        def _prefilled_visible_input_match() -> tuple:
            page_obj = getattr(self.session, "page", None)
            if not (page_obj and hasattr(page_obj, "locator")):
                return (False, "", None, "", "")
            prefilled_probe = []
            try:
                self.session._last_google_flights_combobox_debug["prefilled_probe"] = prefilled_probe
            except Exception:
                prefilled_probe = []
            if time.monotonic() > deadline:
                return (False, "", None, "", "")
            candidates = []
            seen = set()
            role_label_hints = _extract_role_label_hints_from_selectors()
            for sel in inp_sel_list[:3]:
                s = str(sel or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    candidates.append(s)
            generic_selector = "input[role='combobox']"
            if generic_selector not in seen:
                candidates.append(generic_selector)
            for selector in candidates[:4]:
                if time.monotonic() > deadline:
                    break
                try:
                    group = page_obj.locator(selector)
                except Exception:
                    continue
                try:
                    count = int(group.count()) if hasattr(group, "count") else 0
                except Exception:
                    count = 0
                probe_count = max(1, count) if count else 1
                probe_count = min(probe_count, 8)
                for idx in range(probe_count):
                    if time.monotonic() > deadline:
                        break
                    try:
                        locator = group.nth(idx) if hasattr(group, "nth") and count > 1 else group.first
                    except Exception:
                        locator = None
                    if not locator:
                        continue
                    try:
                        if hasattr(locator, "is_visible") and not bool(locator.is_visible(timeout=80)):
                            continue
                    except Exception:
                        continue
                    current_value = _read_input_like_value(locator)
                    matched, matched_token = _prefilled_value_matches(current_value)
                    label_hint_text = _read_input_label_hints(locator)
                    role_hint_ok = True
                    if (
                        selector == generic_selector
                        and role_label_hints
                        and isinstance(label_hint_text, str)
                    ):
                        lowered_label = label_hint_text.lower()
                        role_hint_ok = any(
                            str(tok or "").strip().lower() in lowered_label
                            for tok in role_label_hints
                            if str(tok or "").strip()
                        )
                    try:
                        prefilled_probe.append(
                            {
                                "selector": str(selector or "")[:120],
                                "idx": int(idx if count > 1 else 0),
                                "value": str(current_value or "")[:80],
                                "label": str(label_hint_text or "")[:80],
                                "matched": bool(matched),
                                "match_token": str(matched_token or "")[:40],
                                "role_hint_ok": bool(role_hint_ok),
                            }
                        )
                        if len(prefilled_probe) > 8:
                            del prefilled_probe[:-8]
                    except Exception:
                        pass
                    if not matched or not role_hint_ok:
                        continue
                    return (
                        True,
                        selector,
                        (idx if count > 1 else 0),
                        current_value,
                        str(matched_token or ""),
                    )
            return (False, "", None, "", "")

        (
            prefilled_ok,
            prefilled_sel,
            prefilled_idx,
            prefilled_value,
            prefilled_match_token,
        ) = _prefilled_visible_input_match()
        if prefilled_ok:
            self.session._last_google_flights_combobox_debug["prefilled_match"] = True
            self.session._last_google_flights_combobox_debug["prefilled_value"] = str(prefilled_value or "")[:80]
            self.session._last_google_flights_combobox_debug["prefilled_selector_used"] = str(prefilled_sel or "")[:120]
            self.session._last_google_flights_combobox_debug["prefilled_match_token"] = str(
                prefilled_match_token or ""
            )[:40]
            self.session._last_google_flights_combobox_debug["input_source"] = "prefilled_visible_input"
            self.session._last_google_flights_combobox_debug["input_selector_used"] = str(prefilled_sel or "")[:120]
            self.session._last_google_flights_combobox_debug["activation_selector_used"] = ":prefilled_match"
            if isinstance(prefilled_idx, int):
                self.session._last_google_flights_combobox_debug["activation_selector_index_used"] = int(prefilled_idx)
            self.session._last_google_flights_combobox_debug["verify_ok"] = True
            log.info(
                "gf.fill.combobox.prefilled_match inp_sel=%s idx=%s value=%s match_token=%s",
                str(prefilled_sel or "")[:80],
                int(prefilled_idx) if isinstance(prefilled_idx, int) else -1,
                str(prefilled_value or "")[:80],
                str(prefilled_match_token or "")[:40],
            )
            return (True, str(prefilled_sel or ":prefilled_match"))

        active_role_match_ok, active_role_match_label = _active_input_role_match()
        if active_role_match_ok:
            self.session._last_google_flights_combobox_debug["activation_selector_used"] = ":active_role_match"
            self.session._last_google_flights_combobox_debug["input_source"] = "focused"
            self.session._last_google_flights_combobox_debug["input_selector_used"] = ":focus"
            self.session._last_google_flights_combobox_debug["activation_open_probe"] = {
                "opened": True,
                "source": "active_role_match",
                "label": str(active_role_match_label or "")[:120],
            }
            log.info(
                "gf.fill.combobox.activation_short_circuit source=active_role_match label=%s",
                str(active_role_match_label or "")[:80],
            )
            activation_ok = True
            act_sel_used = ":active_role_match"
            act_sel_used_index = None
        else:
            activation_ok = False
            act_sel_used = None
            act_sel_used_index = None

        # STEP 1: Click activation selector (ONLY click, never fill)
        activation_visible_prefilter = {}

        def _record_activation_attempt(selector: str, **fields) -> None:
            try:
                debug = getattr(self.session, "_last_google_flights_combobox_debug", None)
                if not isinstance(debug, dict):
                    return
                attempts = debug.get("activation_attempts")
                if not isinstance(attempts, list):
                    attempts = []
                    debug["activation_attempts"] = attempts
                payload = {"selector": str(selector or "")[:120]}
                for key, value in (fields or {}).items():
                    key_name = str(key or "")[:40]
                    if isinstance(value, str):
                        payload[key_name] = value[:120]
                    elif isinstance(value, (int, float, bool)) or value is None:
                        payload[key_name] = value
                    else:
                        payload[key_name] = str(value)[:120]
                attempts.append(payload)
                if len(attempts) > 12:
                    del attempts[:-12]
            except Exception:
                pass

        def _resolve_activation_candidate(
            selector: str,
            *,
            visibility_timeout_ms: int = 0,
        ) -> tuple:
            """Return (locator, index, is_visible, match_count) for activation selector.

            Google Flights can render duplicate combobox controls (hidden clone + visible live
            instance). Using `.first` can repeatedly target the hidden clone and burn the entire
            activation budget. Prefer the first visible candidate among the first few matches.
            """
            page_obj = getattr(self.session, "page", None)
            if not (page_obj and hasattr(page_obj, "locator")):
                return (None, None, None, 0)
            try:
                locator_group = page_obj.locator(selector)
                if not locator_group:
                    return (None, None, None, 0)
            except Exception:
                return (None, None, None, 0)

            match_count = 0
            try:
                if hasattr(locator_group, "count"):
                    match_count = int(locator_group.count())
            except Exception:
                match_count = 0

            timeout_probe = max(0, int(visibility_timeout_ms or 0))
            visible_timeout = None
            if timeout_probe > 0:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms > 10:
                    visible_timeout = min(timeout_probe, max(20, remaining_ms))

            if match_count > 1 and hasattr(locator_group, "nth"):
                # Google Flights can keep many hidden clones before the live route field.
                # Probe a wider prefix so the visibility prefilter does not incorrectly
                # demote a valid selector just because the first few matches are hidden.
                max_probe = min(match_count, 10)
                first_locator = None
                first_visible = None
                for idx in range(max_probe):
                    try:
                        cand = locator_group.nth(idx)
                    except Exception:
                        cand = None
                    if not cand:
                        continue
                    if idx == 0:
                        first_locator = cand
                    cand_visible = None
                    if visible_timeout is not None:
                        try:
                            cand_visible = bool(cand.is_visible(timeout=visible_timeout))
                        except Exception:
                            cand_visible = False
                        if idx == 0:
                            first_visible = cand_visible
                        if cand_visible:
                            return (cand, idx, True, match_count)
                    elif idx == 0:
                        first_visible = None
                if first_locator is not None:
                    return (first_locator, 0, first_visible, match_count)

            locator = getattr(locator_group, "first", None)
            if not locator:
                return (None, None, None, match_count)
            locator_visible = None
            if visible_timeout is not None:
                try:
                    locator_visible = bool(locator.is_visible(timeout=visible_timeout))
                except Exception:
                    locator_visible = False
            return (locator, 0, locator_visible, match_count)

        def _fast_activation_visible(selector: str) -> bool | None:
            try:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 20:
                    return None
                _locator, _idx, locator_visible, _count = _resolve_activation_candidate(
                    selector,
                    visibility_timeout_ms=min(80, max(20, remaining_ms)),
                )
                return locator_visible
            except Exception:
                return None

        # Fast visibility prefilter to avoid burning the entire combobox budget on a
        # single missing activation selector under human_mimic click timing.
        visible_first: list[str] = []
        unknown_visibility: list[str] = []
        hidden_or_missing: list[str] = []
        for act_sel in act_sel_list:
            vis = _fast_activation_visible(act_sel)
            activation_visible_prefilter[act_sel] = vis
            if vis is True:
                visible_first.append(act_sel)
            elif vis is False:
                hidden_or_missing.append(act_sel)
            else:
                unknown_visibility.append(act_sel)
        prefer_visible_only = bool(visible_first)
        act_sel_ordered = visible_first + unknown_visibility + hidden_or_missing
        self.session._last_google_flights_combobox_debug["activation_visible_prefilter"] = {
            str(k)[:120]: ("visible" if v is True else "hidden" if v is False else "unknown")
            for k, v in activation_visible_prefilter.items()
        }
        self.session._last_google_flights_combobox_debug["activation_order"] = [
            str(s)[:120] for s in act_sel_ordered[:8]
        ]
        log.info(
            "gf.fill.combobox.activation_candidates ordered=%s",
            act_sel_ordered[:4],
        )

        def _fast_activation_click(selector: str, timeout_ms_local: int) -> tuple:
            """Use a lean click path for bounded combobox activation probes.

            Human-mimic click adds wait/move/delay that can exceed short activation probe
            budgets. For low-budget combobox activation, prefer a direct locator click and
            leave human-mimic behavior for later typing/commit actions.
            """
            try:
                locator, locator_idx, _visible, match_count = _resolve_activation_candidate(
                    selector,
                    visibility_timeout_ms=min(80, max(20, int(timeout_ms_local or 0))),
                )
                if not locator:
                    _record_activation_attempt(
                        selector,
                        mode="fast",
                        ok=False,
                        timeout_ms=int(timeout_ms_local or 0),
                        reason="no_locator",
                    )
                    return (False, None, "no_locator")
                timeout_ms_local = max(50, int(timeout_ms_local or 0))
                # Under tiny combobox probe budgets, a separate wait_for can spend the whole
                # budget before any click occurs. Visibility was already prefetched above.
                if timeout_ms_local > 260:
                    try:
                        if hasattr(locator, "wait_for"):
                            locator.wait_for(state="visible", timeout=max(50, timeout_ms_local))
                    except Exception:
                        # Fall through to click attempt; some controls are attached/interactive
                        # before Playwright reports visible within tiny probe budgets.
                        pass
                try:
                    locator.click(
                        timeout=timeout_ms_local,
                        no_wait_after=True,
                    )
                    _record_activation_attempt(
                        selector,
                        mode="fast",
                        ok=True,
                        timeout_ms=int(timeout_ms_local or 0),
                        idx=int(locator_idx) if isinstance(locator_idx, int) else None,
                        count=int(match_count or 0),
                        method="click",
                    )
                    return (True, locator_idx if isinstance(locator_idx, int) else None, "")
                except Exception as click_exc:
                    # Keep the forced fallback bounded; low-budget probes should not spend
                    # another full timeout on a second click attempt.
                    force_timeout_ms = max(40, min(90, int(timeout_ms_local * 0.4)))
                    try:
                        locator.click(
                            timeout=force_timeout_ms,
                            no_wait_after=True,
                            force=True,
                        )
                        _record_activation_attempt(
                            selector,
                            mode="fast",
                            ok=True,
                            timeout_ms=int(timeout_ms_local or 0),
                            idx=int(locator_idx) if isinstance(locator_idx, int) else None,
                            count=int(match_count or 0),
                            method="click_force",
                        )
                        return (True, locator_idx if isinstance(locator_idx, int) else None, "")
                    except Exception as forced_click_exc:
                        err_preview = str(forced_click_exc or click_exc)[:120]
                        _record_activation_attempt(
                            selector,
                            mode="fast",
                            ok=False,
                            timeout_ms=int(timeout_ms_local or 0),
                            idx=int(locator_idx) if isinstance(locator_idx, int) else None,
                            count=int(match_count or 0),
                            reason="click_failed",
                            error=err_preview,
                        )
                        return (False, None, err_preview)
            except Exception as exc:
                err_preview = str(exc)[:120]
                _record_activation_attempt(
                    selector,
                    mode="fast",
                    ok=False,
                    timeout_ms=int(timeout_ms_local or 0),
                    reason="exception",
                    error=err_preview,
                )
                return (False, None, err_preview)

        def _activation_open_confirmed(selector: str, selector_index: int | None = None) -> bool:
            """Best-effort bounded probe that the combobox activation actually opened."""
            page_obj = getattr(self.session, "page", None)
            if not (page_obj and hasattr(page_obj, "evaluate")):
                return True
            try:
                out = _page_evaluate_compat(
                    """
                    (args) => {
                      const sel = String((args && args.selector) || "");
                      const selectorIndex = Number.isInteger(args && args.selector_index)
                        ? Number(args.selector_index)
                        : -1;
                      let root = null;
                      try {
                        if (sel) {
                          if (selectorIndex >= 0) {
                            const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 12);
                            root = nodes[selectorIndex] || nodes[0] || null;
                          } else {
                            root = document.querySelector(sel);
                          }
                        }
                      } catch (e) {}
                      const active = document.activeElement;
                      const tag = String((root && root.tagName) || "").toLowerCase();
                      const activeTag = String((active && active.tagName) || "").toLowerCase();
                      const expanded = String((root && root.getAttribute && root.getAttribute("aria-expanded")) || "").trim().toLowerCase();
                      const activeExpanded = String((active && active.getAttribute && active.getAttribute("aria-expanded")) || "").trim().toLowerCase();
                      const activeIsInput = activeTag === "input" || activeTag === "textarea";
                      const rootContainsActive = !!(root && active && root.contains && root.contains(active));
                      const rootIsActiveInput = !!(root && active && root === active && activeIsInput);
                      let listboxVisible = false;
                      try {
                        const nodes = Array.from(document.querySelectorAll("[role='listbox'], [role='option']")).slice(0, 20);
                        listboxVisible = nodes.some((node) => {
                          const r = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                          return !!r && r.width > 0 && r.height > 0;
                        });
                      } catch (e) {}
                      const opened =
                        expanded === "true" ||
                        activeExpanded === "true" ||
                        rootContainsActive ||
                        rootIsActiveInput ||
                        (activeIsInput && listboxVisible);
                      return {
                        opened: !!opened,
                        expanded,
                        activeExpanded,
                        rootContainsActive,
                        listboxVisible,
                        tag,
                        activeTag,
                      };
                    }
                    """,
                    {
                        "selector": selector or "",
                        "selector_index": int(selector_index)
                        if isinstance(selector_index, int)
                        else -1,
                    },
                )
                try:
                    self.session._last_google_flights_combobox_debug["activation_open_probe"] = dict(out or {})
                except Exception:
                    pass
                return bool((out or {}).get("opened"))
            except Exception:
                return True

        reserved_post_activation_ms = max(260, min(520, int(timeout_val * 0.24)))
        minimum_commit_reserve_ms = 220 if bool(getattr(self.session, "human_mimic", False)) else 320
        for idx, act_sel in enumerate(act_sel_ordered):
            if activation_ok:
                break
            if time.monotonic() > deadline:
                log.warning("gf.fill.combobox.deadline_activation_check")
                return _combobox_fail("deadline_activation_check", selector=act_sel)
            vis = activation_visible_prefilter.get(act_sel)
            if vis is False and prefer_visible_only:
                log.debug("gf.fill.combobox.activation_skip_not_visible act_sel=%s", act_sel[:80])
                continue
            try:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                reserve_ms_for_commit = reserved_post_activation_ms
                activation_budget_ms = max(0, remaining_ms - reserve_ms_for_commit)
                if (
                    activation_budget_ms <= 80
                    and remaining_ms > (minimum_commit_reserve_ms + 100)
                    and reserve_ms_for_commit > minimum_commit_reserve_ms
                ):
                    reserve_ms_for_commit = minimum_commit_reserve_ms
                    activation_budget_ms = max(0, remaining_ms - reserve_ms_for_commit)
                    log.info(
                        "gf.fill.combobox.activation_budget_relaxed remaining_ms=%d reserve_ms=%d",
                        remaining_ms,
                        reserve_ms_for_commit,
                    )
                if activation_budget_ms <= 80:
                    log.warning(
                        "gf.fill.combobox.deadline_activation_budget remaining_ms=%d reserve_ms=%d",
                        remaining_ms,
                        reserve_ms_for_commit,
                    )
                    return _combobox_fail(
                        "deadline_activation_budget",
                        selector=act_sel,
                        reserve_ms=reserve_ms_for_commit,
                    )
                remaining_candidates = max(1, len(act_sel_ordered) - idx)
                if vis is True:
                    per_click_ms = min(max(140, activation_budget_ms), 900)
                else:
                    fair_share_ms = max(120, int(activation_budget_ms / remaining_candidates))
                    per_click_ms = min(fair_share_ms, 260)
                click_timeout_ms = max(100, per_click_ms)
                used_fast_activation = False
                resolved_activation_clicked = False
                resolved_activation_index = None
                fast_activation_attempted = False
                fast_activation_error = ""
                if bool(getattr(self.session, "human_mimic", False)) and click_timeout_ms <= 260:
                    fast_activation_attempted = True
                    (
                        used_fast_activation,
                        resolved_activation_index,
                        fast_activation_error,
                    ) = _fast_activation_click(
                        act_sel, click_timeout_ms
                    )
                    if used_fast_activation:
                        log.info(
                            "gf.fill.combobox.activation_click_fast act_sel=%s timeout_ms=%d",
                            act_sel[:80],
                            click_timeout_ms,
                        )
                    else:
                        log.debug(
                            "gf.fill.combobox.activation_click_fast_failed act_sel=%s timeout_ms=%d error=%s",
                            act_sel[:80],
                            click_timeout_ms,
                            str(fast_activation_error or "")[:120],
                        )
                if not used_fast_activation:
                    if (
                        fast_activation_attempted
                        and bool(getattr(self.session, "human_mimic", False))
                        and click_timeout_ms <= 260
                    ):
                        _record_activation_attempt(
                            act_sel,
                            mode="human_fallback",
                            ok=False,
                            timeout_ms=int(click_timeout_ms or 0),
                            reason="skipped_after_fast_fail",
                        )
                        log.info(
                            "gf.fill.combobox.activation_skip_human_fallback act_sel=%s timeout_ms=%d",
                            act_sel[:80],
                            click_timeout_ms,
                        )
                        continue
                    try:
                        (
                            resolved_locator,
                            resolved_idx,
                            _resolved_visible,
                            resolved_count,
                        ) = _resolve_activation_candidate(
                            act_sel,
                            visibility_timeout_ms=min(60, click_timeout_ms),
                        )
                        if (
                            resolved_locator
                            and isinstance(resolved_idx, int)
                            and resolved_idx > 0
                            and int(resolved_count or 0) > 1
                        ):
                            if hasattr(resolved_locator, "wait_for"):
                                try:
                                    resolved_locator.wait_for(
                                        state="visible",
                                        timeout=max(50, int(click_timeout_ms)),
                                    )
                                except Exception:
                                    pass
                            resolved_locator.click(
                                timeout=max(50, int(click_timeout_ms)),
                                no_wait_after=True,
                            )
                            resolved_activation_clicked = True
                            resolved_activation_index = resolved_idx
                            log.info(
                                "gf.fill.combobox.activation_click_resolved act_sel=%s idx=%d count=%d timeout_ms=%d",
                                act_sel[:80],
                                resolved_idx,
                                int(resolved_count or 0),
                                click_timeout_ms,
                            )
                            _record_activation_attempt(
                                act_sel,
                                mode="resolved_direct",
                                ok=True,
                                timeout_ms=int(click_timeout_ms or 0),
                                idx=int(resolved_idx),
                                count=int(resolved_count or 0),
                            )
                    except Exception:
                        resolved_activation_clicked = False
                        resolved_activation_index = None
                    if not resolved_activation_clicked:
                        self.session.click(
                            act_sel,
                            timeout_ms=click_timeout_ms,
                            no_wait_after=True,
                        )
                        _record_activation_attempt(
                            act_sel,
                            mode="human_click",
                            ok=True,
                            timeout_ms=int(click_timeout_ms or 0),
                        )
                activation_confirmed = _activation_open_confirmed(
                    act_sel,
                    selector_index=resolved_activation_index,
                )
                if not activation_confirmed and (deadline - time.monotonic()) > 0.04:
                    _bounded_sleep(40)
                    activation_confirmed = _activation_open_confirmed(
                        act_sel,
                        selector_index=resolved_activation_index,
                    )
                if not activation_confirmed:
                    log.debug("gf.fill.combobox.activation_unconfirmed act_sel=%s", act_sel[:80])
                    continue
                activation_ok = True
                act_sel_used = act_sel
                act_sel_used_index = (
                    resolved_activation_index if isinstance(resolved_activation_index, int) else None
                )
                self.session._last_google_flights_combobox_debug["activation_selector_used"] = str(act_sel or "")[:120]
                if isinstance(act_sel_used_index, int):
                    self.session._last_google_flights_combobox_debug["activation_selector_index_used"] = int(
                        act_sel_used_index
                    )
                log.info("gf.fill.combobox.activation_click act_sel=%s", act_sel[:80])
                break
            except Exception as e:
                log.debug("gf.fill.combobox.activation_click_failed act_sel=%s error=%s", act_sel[:80], str(e)[:100])

        if not activation_ok:
            log.warning("gf.fill.combobox.activation_failed")
            return _combobox_fail("activation_failed")

        # STEP 2: Find the REAL input element (never fill the combobox container)
        # Priority: focused input > input inside container > input_selectors list
        input_handle = None
        inp_sel_used = None

        if time.monotonic() > deadline:
            log.warning("gf.fill.combobox.deadline_before_input_search")
            return _combobox_fail("deadline_before_input_search", selector=act_sel_used or "")

        try:
            # Fast path: check for focused input
            if self.session.page and hasattr(self.session.page, "evaluate"):
                tried_focused = _page_evaluate_compat(
                    "() => {const e = document.activeElement; return e && (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA');}"
                )
                if tried_focused:
                    input_handle = "focused"
                    inp_sel_used = ":focus"
                    self.session._last_google_flights_combobox_debug["input_source"] = "focused"
                    self.session._last_google_flights_combobox_debug["input_selector_used"] = ":focus"
                    log.info("gf.fill.combobox.input_found source=focused")
        except Exception:
            pass

        # Fallback: find input inside or near the activated container
        if not input_handle and act_sel_used and self.session.page and hasattr(self.session.page, "locator"):
            try:
                if time.monotonic() > deadline:
                    log.warning("gf.fill.combobox.deadline_container_input_search")
                    return _combobox_fail("deadline_container_input_search", selector=act_sel_used or "")

                # Try to find input inside container
                locator_str = f"{act_sel_used} input"
                locator = self.session.page.locator(locator_str).first
                if locator and locator.is_visible(timeout=150):
                    input_handle = locator
                    inp_sel_used = locator_str
                    self.session._last_google_flights_combobox_debug["input_source"] = "container"
                    self.session._last_google_flights_combobox_debug["input_selector_used"] = str(locator_str or "")[:120]
                    log.info("gf.fill.combobox.input_found source=container inp_sel=%s", locator_str[:80])
            except Exception as e:
                log.debug("gf.fill.combobox.container_input_search_failed error=%s", str(e)[:80])

        # Last fallback: try input_selectors list
        if not input_handle:
            for inp_sel in inp_sel_list:
                if time.monotonic() > deadline:
                    log.warning("gf.fill.combobox.deadline_candidate_input_search")
                    return _combobox_fail("deadline_candidate_input_search", selector=inp_sel)
                try:
                    if self.session.page and hasattr(self.session.page, "locator"):
                        locator_group = self.session.page.locator(inp_sel)
                        selector_norm = inp_sel.strip().lower()
                        try:
                            match_count = int(locator_group.count())
                        except Exception:
                            match_count = 0
                        if match_count != 1:
                            resolved_index = None
                            if (
                                match_count > 1
                                and self.session.page
                                and hasattr(self.session.page, "evaluate")
                            ):
                                try:
                                    resolved_index = _page_evaluate_compat(
                                        """
                                        (args) => {
                                          const nodes = Array.from(document.querySelectorAll(args.selector || ""))
                                            .slice(0, 8);
                                          const tokens = Array.isArray(args.verify_tokens) ? args.verify_tokens : [];
                                          const activationSelector = String((args.activation_selector) || "");
                                          const norm = (v) => String(v || "").toLowerCase();
                                          let activationRoot = null;
                                          try { activationRoot = activationSelector ? document.querySelector(activationSelector) : null; } catch (e) {}
                                          let best = { idx: -1, score: -1 };
                                          let second = { idx: -1, score: -1 };
                                          for (let i = 0; i < nodes.length; i++) {
                                            const el = nodes[i];
                                            if (!el) continue;
                                            const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                                            const visible = !!rect && rect.width > 0 && rect.height > 0;
                                            if (!visible) continue;
                                            let score = 0;
                                            if (document.activeElement === el) score += 50;
                                            const ariaExpanded = norm(el.getAttribute && el.getAttribute("aria-expanded"));
                                            if (ariaExpanded === "true") score += 20;
                                            const ariaControls = String((el.getAttribute && el.getAttribute("aria-controls")) || "");
                                            if (ariaControls.trim()) score += 10;
                                            if (activationRoot && activationRoot.contains && activationRoot.contains(el)) score += 18;
                                            const labelBlob = norm(
                                              ((el.getAttribute && el.getAttribute("aria-label")) || "") + " " +
                                              ((el.getAttribute && el.getAttribute("placeholder")) || "")
                                            );
                                            for (const token of tokens) {
                                              const t = norm(token);
                                              if (!t) continue;
                                              if (labelBlob.includes(t)) score += 8;
                                            }
                                            if (score > best.score) {
                                              second = best;
                                              best = { idx: i, score };
                                            } else if (score > second.score) {
                                              second = { idx: i, score };
                                            }
                                          }
                                          if (best.idx < 0 || best.score <= 0) return null;
                                          if (second.idx >= 0 && best.score === second.score) return null;
                                          return best.idx;
                                        }
                                        """,
                                        {
                                            "selector": inp_sel,
                                            "verify_tokens": verify_token_list[:6],
                                            "activation_selector": act_sel_used or "",
                                        },
                                    )
                                except Exception:
                                    resolved_index = None
                            if isinstance(resolved_index, (int, float)):
                                idx_val = int(resolved_index)
                                if (
                                    idx_val >= 0
                                    and idx_val < match_count
                                    and hasattr(locator_group, "nth")
                                ):
                                    locator = locator_group.nth(idx_val)
                                    if locator and locator.is_visible(timeout=150):
                                        input_handle = locator
                                        inp_sel_used = inp_sel
                                        generic_input_selector_used = (match_count > 1)
                                        self.session._last_google_flights_combobox_debug["input_source"] = "generic_resolved"
                                        self.session._last_google_flights_combobox_debug["input_selector_used"] = str(inp_sel or "")[:120]
                                        log.info(
                                            "gf.fill.combobox.input_found source=generic_resolved inp_sel=%s idx=%d",
                                            inp_sel[:80],
                                            idx_val,
                                        )
                                        break
                            if match_count > 1:
                                log.warning(
                                    "gf.fill.combobox.ambiguous_input_candidate inp_sel=%s match_count=%d",
                                    inp_sel[:80],
                                    match_count,
                                )
                            continue
                        locator = locator_group.first
                        if locator and locator.is_visible(timeout=150):
                            input_handle = locator
                            inp_sel_used = inp_sel
                            generic_input_selector_used = selector_norm == "input[role='combobox']"
                            self.session._last_google_flights_combobox_debug["input_source"] = "candidates"
                            self.session._last_google_flights_combobox_debug["input_selector_used"] = str(inp_sel or "")[:120]
                            log.info("gf.fill.combobox.input_found source=candidates inp_sel=%s", inp_sel[:80])
                            break
                except Exception:
                    continue

        if not input_handle:
            log.warning("gf.fill.combobox.no_input_found")
            return _combobox_fail("no_input_found", selector=act_sel_used or "")
        self.session._last_google_flights_combobox_debug["generic_input_selector_used"] = bool(generic_input_selector_used)

        # STEP 3: Type into the real input (NOT the combobox container)
        try:
            if time.monotonic() > deadline:
                log.warning("gf.fill.combobox.deadline_before_typing")
                return _combobox_fail("deadline_before_typing", selector=inp_sel_used or "")

            # Explicitly clear any provider-prefilled value (e.g. "Tokyo" in "Where from?")
            # before typing route IATA. This is especially important for the `focused`
            # fallback path, which otherwise uses keyboard typing and can append.
            self.session._last_google_flights_combobox_debug["editor_clear_attempted"] = True
            editor_cleared = False
            if self.session.page and hasattr(self.session.page, "keyboard"):
                try:
                    if hasattr(input_handle, "click"):
                        try:
                            input_handle.click(timeout=max(60, int((deadline - time.monotonic()) * 1000)))
                        except Exception:
                            pass
                    if time.monotonic() <= deadline:
                        self.session.page.keyboard.press("ControlOrMeta+A")
                        self.session.page.keyboard.press("Backspace")
                        editor_cleared = True
                except Exception:
                    editor_cleared = False
            self.session._last_google_flights_combobox_debug["editor_cleared"] = bool(editor_cleared)

            if hasattr(input_handle, "fill"):
                input_handle.fill(text, timeout=max(100, int((deadline - time.monotonic()) * 1000)))
            elif hasattr(input_handle, "type"):
                input_handle.type(text, timeout=max(100, int((deadline - time.monotonic()) * 1000)))
            elif self.session.page and hasattr(self.session.page, "keyboard"):
                # Fallback: keyboard input
                self.session.page.keyboard.type(text)
            log.info("gf.fill.combobox.input_typed inp_sel=%s text_len=%d", inp_sel_used[:80] if inp_sel_used else "", len(text))
        except Exception as e:
            log.warning("gf.fill.combobox.input_type_failed error=%s", str(e)[:100])
            return _combobox_fail("input_type_failed", selector=inp_sel_used or "", error=str(e)[:120])

        # STEP 4: Press Enter to trigger suggestions
        try:
            if time.monotonic() > deadline:
                log.warning("gf.fill.combobox.deadline_before_enter")
                return _combobox_fail("deadline_before_enter", selector=inp_sel_used or "")
            if self.session.page and hasattr(self.session.page, "keyboard"):
                self.session.page.keyboard.press("Enter")
                log.info("gf.fill.commit.enter inp_sel=%s", inp_sel_used[:80] if inp_sel_used else "")
        except Exception as e:
            log.debug("gf.fill.commit.enter_failed error=%s", str(e)[:100])

        _bounded_sleep(200)

        def _try_keyboard_commit_fallback() -> bool:
            if not (self.session.page and hasattr(self.session.page, "keyboard")):
                return False
            try:
                self.session.page.keyboard.press("ArrowDown")
                self.session.page.keyboard.press("Enter")
                self.session._last_google_flights_combobox_debug["keyboard_commit_attempted"] = True
                log.info(
                    "gf.fill.commit.option_keyboard_fallback inp_sel=%s",
                    inp_sel_used[:80] if inp_sel_used else "",
                )
                return True
            except Exception:
                return False

        # STEP 5: Click first option (CAP: max 5 option selectors)
        option_click_succeeded = False
        remaining_before_pointer_ms = int((deadline - time.monotonic()) * 1000)
        keyboard_commit_attempted = False
        deadline_exceeded_before_option_click = False

        # If the remaining commit budget is already low, prefer keyboard commit first.
        if remaining_before_pointer_ms > 10 and remaining_before_pointer_ms < 450:
            keyboard_commit_attempted = _try_keyboard_commit_fallback()

        if not keyboard_commit_attempted:
            for opt_sel in ["[role='listbox'] [role='option']", "[role='option']:first-child", "li[role='option']", "[role='combobox'] [role='option']"][:4]:
                if time.monotonic() > deadline:
                    deadline_exceeded_before_option_click = True
                    break
                try:
                    self.session.click(opt_sel, timeout_ms=max(100, int((deadline - time.monotonic()) * 1000)))
                    option_click_succeeded = True
                    self.session._last_google_flights_combobox_debug["option_click_succeeded"] = True
                    log.info("gf.fill.commit.option_click selector_used=%s", opt_sel[:80])
                    break
                except Exception:
                    continue

        # Fast fallback for low-budget cases: Google often accepts first suggestion via
        # keyboard when pointer-based option click times out. Keep this bounded and
        # verify with the existing postcondition checks.
        if not option_click_succeeded and not keyboard_commit_attempted and (deadline - time.monotonic()) > 0.01:
            keyboard_commit_attempted = _try_keyboard_commit_fallback()

        _bounded_sleep(200)

        def _probe_no_option_click_commit_signal():
            return _page_evaluate_compat(
                """
                (args) => {
                  const typed = String((args && args.typed_text) || "").trim().toUpperCase();
                  const activationSelector = String((args && args.activation_selector) || "").trim();
                  const el = document.activeElement;
                  let rootText = "";
                  let rootPlaceholderLike = false;
                  try {
                    const root = activationSelector ? document.querySelector(activationSelector) : null;
                    if (root) {
                      rootText = String(root.innerText || root.textContent || "").trim();
                      const rootTextLower = rootText.toLowerCase();
                      const placeholderHints = [
                        "目的地を探索",
                        "出発地を探索",
                        "explore destinations",
                        "search destinations",
                        "where to?",
                        "where from?",
                        "where to",
                        "where from",
                      ];
                      rootPlaceholderLike = placeholderHints.some((hint) => rootTextLower.includes(String(hint).toLowerCase()));
                    }
                  } catch (e) {}
                  const rootTextPreview = String(rootText || "").slice(0, 120);
                  if (!el) {
                    return {
                      active_value: "",
                      active_expanded: "",
                      listbox_visible: false,
                      exact_typed_match: false,
                      root_placeholder_like: !!rootPlaceholderLike,
                      root_text_preview: rootTextPreview,
                      has_commit_signal: false,
                    };
                  }
                  const activeValue = String(el.value || "").trim();
                  const activeValueUpper = activeValue.toUpperCase();
                  const activeExpanded = String(el.getAttribute("aria-expanded") || "").trim().toLowerCase();
                  let listboxVisible = false;
                  try {
                    const candidates = Array.from(document.querySelectorAll("[role='listbox'], [role='option']"))
                      .slice(0, 20);
                    listboxVisible = candidates.some((node) => {
                      if (!node) return false;
                      const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                      return !!rect && rect.width > 0 && rect.height > 0;
                    });
                  } catch (e) {}
                  const exactTypedMatch = !!typed && activeValueUpper === typed;
                  let hasCommitSignal =
                    activeExpanded === "false" ||
                    (!listboxVisible && activeExpanded !== "true") ||
                    (!!typed && activeValueUpper !== typed);
                  if (exactTypedMatch && rootPlaceholderLike) {
                    hasCommitSignal = false;
                  }
                  return {
                    active_value: activeValue.slice(0, 80),
                    active_expanded: activeExpanded,
                    listbox_visible: !!listboxVisible,
                    exact_typed_match: !!exactTypedMatch,
                    root_placeholder_like: !!rootPlaceholderLike,
                    root_text_preview: rootTextPreview,
                    has_commit_signal: !!hasCommitSignal,
                  };
                }
                """,
                {
                    "typed_text": text,
                    "activation_selector": act_sel_used or "",
                },
            )

        # STEP 6: Verify postcondition
        verify_ok = True  # Assume OK since we got through the flow
        commit_signal = None
        try:
            if time.monotonic() <= deadline and self.session.page and hasattr(self.session.page, "evaluate"):
                js = f"(() => {{const el = document.activeElement; if (!el) return false; const val = (el.value || '').toUpperCase(); const txt = '{text.upper()}'; return val.includes(txt) || val.includes(txt.substring(0, 3));}})();"
                verify_ok = bool(_page_evaluate_compat(js))
        except Exception:
            pass  # Assume OK if eval fails

        # Generic combobox input fallback is only safe when it uniquely identifies the target
        # field or a suggestion click completed. If it required a generic selector and no
        # option click succeeded, ensure the active element is still scoped to the activated
        # container before trusting activeElement-based verification.
        if (
            verify_ok
            and generic_input_selector_used
            and not option_click_succeeded
            and act_sel_used
            and self.session.page
            and hasattr(self.session.page, "evaluate")
        ):
            try:
                scope_check = _page_evaluate_compat(
                    """
                    (args) => {
                      const el = document.activeElement;
                      if (!el) return false;
                      let root = null;
                      try { root = document.querySelector(args.selector || ""); } catch (e) {}
                      if (!root) return false;
                      if (root === el) return true;
                      if (root.contains && root.contains(el)) return true;
                      try {
                        if (el.closest && args.selector && el.closest(args.selector)) return true;
                      } catch (e) {}
                      return false;
                    }
                    """,
                    {"selector": act_sel_used},
                )
                if not bool(scope_check):
                    verify_ok = False
                    log.warning(
                        "gf.fill.combobox.verify_scope_failed act_sel=%s inp_sel=%s",
                        act_sel_used[:80] if act_sel_used else "",
                        inp_sel_used[:80] if inp_sel_used else "",
                    )
            except Exception:
                # Fail closed for ambiguous generic fallback when scope check cannot run.
                verify_ok = False
                log.warning(
                    "gf.fill.combobox.verify_scope_check_error act_sel=%s inp_sel=%s",
                    act_sel_used[:80] if act_sel_used else "",
                    inp_sel_used[:80] if inp_sel_used else "",
                )

        # Field-scoped verification alone can still be a false positive when the UI keeps a
        # transient draft value in the active combobox editor (e.g., destination placeholder
        # remains unbound after suggestion click timeout). When no suggestion click succeeded,
        # require one additional bounded commit signal before trusting activeElement-based
        # verification.
        if (
            verify_ok
            and not option_click_succeeded
            and self.session.page
            and hasattr(self.session.page, "evaluate")
        ):
            try:
                commit_signal = _probe_no_option_click_commit_signal()
                if isinstance(commit_signal, dict):
                    self.session._last_google_flights_combobox_debug["commit_signal"] = {
                        "active_expanded": str(commit_signal.get("active_expanded", "") or ""),
                        "listbox_visible": bool(commit_signal.get("listbox_visible", False)),
                        "exact_typed_match": bool(commit_signal.get("exact_typed_match", False)),
                        "root_placeholder_like": bool(commit_signal.get("root_placeholder_like", False)),
                        "root_text_preview": str(commit_signal.get("root_text_preview", "") or "")[:120],
                        "active_value": str(commit_signal.get("active_value", "") or "")[:80],
                        "has_commit_signal": bool(commit_signal.get("has_commit_signal", False)),
                    }
                if isinstance(commit_signal, dict) and not bool(commit_signal.get("has_commit_signal")):
                    verify_ok = False
                    log.warning(
                        "gf.fill.combobox.verify_unconfirmed_no_option_click inp_sel=%s expanded=%s listbox_visible=%s exact_typed=%s root_placeholder_like=%s",
                        inp_sel_used[:80] if inp_sel_used else "",
                        str(commit_signal.get("active_expanded", "")),
                        bool(commit_signal.get("listbox_visible", False)),
                        bool(commit_signal.get("exact_typed_match", False)),
                        bool(commit_signal.get("root_placeholder_like", False)),
                    )
            except Exception:
                # Fail closed for the no-option-click path when commit confirmation cannot run.
                verify_ok = False
                log.warning(
                    "gf.fill.combobox.verify_commit_signal_error inp_sel=%s",
                    inp_sel_used[:80] if inp_sel_used else "",
                )

        if (
            not verify_ok
            and not option_click_succeeded
            and self.session.page
            and hasattr(self.session.page, "evaluate")
        ):
            try:
                if not isinstance(commit_signal, dict):
                    commit_signal = _probe_no_option_click_commit_signal()
                if isinstance(commit_signal, dict):
                    has_commit_signal = bool(commit_signal.get("has_commit_signal"))
                    exact_typed_match = bool(commit_signal.get("exact_typed_match"))
                    root_placeholder_like = bool(commit_signal.get("root_placeholder_like"))
                    active_value = str(commit_signal.get("active_value", "") or "").strip()
                    root_text_preview = str(commit_signal.get("root_text_preview", "") or "").strip()
                    strong_semantic_commit = (
                        has_commit_signal
                        and not exact_typed_match
                        and not root_placeholder_like
                        and bool(active_value or root_text_preview)
                    )
                    if strong_semantic_commit:
                        verify_ok = True
                        self.session._last_google_flights_combobox_debug["verify_semantic_fallback"] = True
                        log.info(
                            "gf.fill.combobox.verify_semantic_commit_fallback inp_sel=%s active_len=%d root_text_len=%d",
                            inp_sel_used[:80] if inp_sel_used else "",
                            len(active_value),
                            len(root_text_preview),
                        )
            except Exception:
                pass

        self.session._last_google_flights_combobox_debug["verify_ok"] = bool(verify_ok)
        if not verify_ok:
            if deadline_exceeded_before_option_click:
                log.warning("gf.fill.combobox.deadline_option_click")
            _combobox_fail(
                "verify_failed",
                selector=inp_sel_used or act_sel_used or "",
                option_click_succeeded=bool(option_click_succeeded),
                keyboard_commit_attempted=bool(keyboard_commit_attempted),
            )
        log.info("gf.fill.combobox.verify %s inp_sel=%s", ("ok" if verify_ok else "failed"), inp_sel_used[:80] if inp_sel_used else "")
        return (verify_ok, act_sel_used if verify_ok else "")
