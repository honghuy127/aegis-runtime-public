"""Skyscanner UI action helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _extract_iata(value: str) -> str:
    token = str(value or "").strip().upper()
    return token if len(token) == 3 and token.isalpha() else ""


def _parse_iso_date_parts(raw: str) -> Optional[Dict[str, int]]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return {"year": int(dt.year), "month": int(dt.month), "day": int(dt.day)}
        except Exception:
            continue
    return None


def _click_first_selector(
    browser: Any,
    selectors: List[str],
    *,
    timeout_ms: int,
) -> str:
    page = getattr(browser, "page", None)
    last_error: Exception | None = None
    for selector in [str(s or "").strip() for s in selectors if str(s or "").strip()]:
        try:
            browser.click(selector, timeout_ms=timeout_ms)
            return selector
        except Exception as exc:  # pragma: no cover - best effort fallback
            last_error = exc
            if _dom_click_first_visible_selector(page, selector):
                return selector
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("no_opener_selector")


def _dom_click_first_visible_selector(page: Any, selector: str) -> bool:
    if page is None or not hasattr(page, "evaluate"):
        return False
    payload = {"selector": str(selector or "").strip()}
    if not payload["selector"]:
        return False
    try:
        out = page.evaluate(
            """
            (payload) => {
              const selector = String(payload.selector || "").trim();
              if (!selector) return false;
              let nodes = [];
              try {
                nodes = Array.from(document.querySelectorAll(selector));
              } catch (_err) {
                return false;
              }
              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const isEnabled = (el) => {
                if (!el) return false;
                if (el.hasAttribute("disabled")) return false;
                const ariaDisabled = String(el.getAttribute("aria-disabled") || "").toLowerCase();
                return ariaDisabled !== "true";
              };
              const target = nodes.find((el) => isVisible(el) && isEnabled(el));
              if (!target) return false;
              try {
                target.scrollIntoView({block: "center", inline: "center", behavior: "instant"});
              } catch (_err) {
              }
              try {
                target.click();
                return true;
              } catch (_err) {
                return false;
              }
            }
            """,
            payload,
        )
        return bool(out)
    except Exception:
        return False


def _wait_for_calendar_root(page: Any, timeout_ms: int) -> bool:
    if page is None:
        return False
    candidates = [
        "[data-testid='CustomCalendarContainer']",
        "[data-testid='CustomCalendarContainer'] [role='grid']",
        "div._CustomCalendar_fwwz9_114",
        "div[class*='CustomCalendar']",
    ]
    for sel in candidates:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=max(400, int(timeout_ms)))
            return True
        except Exception:
            continue
    return False


def _click_target_day(page: Any, *, year: int, month: int, day: int) -> Dict[str, Any]:
    if page is None or not hasattr(page, "evaluate"):
        return {"ok": False, "reason": "page_unavailable"}
    payload = {"year": int(year), "month": int(month), "day": int(day)}
    try:
        out = page.evaluate(
            """
            (target) => {
              const yy = Number(target.year || 0);
              const mm = Number(target.month || 0);
              const dd = Number(target.day || 0);
              if (!(yy > 0 && mm > 0 && dd > 0)) return { ok: false, reason: "invalid_date" };

              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const ymdJp = `${yy}年${mm}月${dd}日`;
              const ymdSlash = `${yy}/${String(mm).padStart(2, "0")}/${String(dd).padStart(2, "0")}`;
              const buttons = Array.from(document.querySelectorAll(
                "[data-testid='CustomCalendarContainer'] button[aria-label], div[class*='CustomCalendar'] button[aria-label]"
              ));
              const match = buttons.find((btn) => {
                if (!btn || !isVisible(btn)) return false;
                const disabled = String(btn.getAttribute("aria-disabled") || "").toLowerCase() === "true";
                if (disabled) return false;
                const label = String(btn.getAttribute("aria-label") || "");
                return label.includes(ymdJp) || label.includes(ymdSlash);
              });
              if (!match) return { ok: false, reason: "day_not_found" };
              match.click();
              return { ok: true, reason: "day_clicked", aria_label: String(match.getAttribute("aria-label") || "") };
            }
            """,
            payload,
        )
        return dict(out) if isinstance(out, dict) else {"ok": False, "reason": "unknown"}
    except Exception:
        return {"ok": False, "reason": "eval_failed"}


def _calendar_contains_target_month(page: Any, *, year: int, month: int) -> bool:
    if page is None or not hasattr(page, "evaluate"):
        return False
    payload = {"year": int(year), "month": int(month)}
    try:
        out = page.evaluate(
            """
            (target) => {
              const yy = Number(target.year || 0);
              const mm = Number(target.month || 0);
              if (!(yy > 0 && mm > 0)) return false;
              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const token = `${yy}年${mm}月`;
              const dayBtns = Array.from(
                document.querySelectorAll(
                  "[data-testid='CustomCalendarContainer'] button[aria-label], div[class*='CustomCalendar'] button[aria-label]"
                )
              ).filter((btn) => isVisible(btn));
              return dayBtns.some((btn) => String(btn.getAttribute("aria-label") || "").includes(token));
            }
            """,
            payload,
        )
        return bool(out)
    except Exception:
        return False


def _click_next_month(page: Any) -> bool:
    if page is None:
        return False
    selectors = [
        "button[aria-label*='来月']",
        "button[aria-label*='次の月']",
        "button[aria-label*='Next month']",
        "button[class*='NextBtn']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=800)
            loc.click(timeout=800)
            return True
        except Exception:
            continue
    try:
        clicked = page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const buttons = Array.from(document.querySelectorAll(
                "[data-testid='CustomCalendarContainer'] button, div[class*='CustomCalendar'] button"
              ));
              const next = buttons.find((btn) => {
                if (!btn || !isVisible(btn)) return false;
                const ariaDisabled = String(btn.getAttribute("aria-disabled") || "").toLowerCase();
                if (ariaDisabled === "true" || btn.hasAttribute("disabled")) return false;
                const label = String(btn.getAttribute("aria-label") || "").toLowerCase();
                const cls = String(btn.className || "").toLowerCase();
                return (
                  label.includes("来月")
                  || label.includes("次の月")
                  || label.includes("next month")
                  || cls.includes("nextbtn")
                );
              });
              if (!next) return false;
              next.click();
              return true;
            }
            """
        )
        if bool(clicked):
            return True
    except Exception:
        pass
    return False


def _detect_skyscanner_results_overlay(page: Any) -> Dict[str, Any]:
    if page is None or not hasattr(page, "evaluate"):
        return {"overlay_present": False, "reason": "page_unavailable"}
    try:
        out = page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 24 && r.height > 24;
              };
              const bodyText = String(document.body?.innerText || "").toLowerCase();
              const alertTokens = [
                "新しいプライスアラートを設定",
                "プライスアラート",
                "price alert",
                "メールアドレスでログイン",
                "ログイン",
                "sign in",
              ];
              const hasAlertToken = alertTokens.some((token) => bodyText.includes(String(token).toLowerCase()));
              const dialogCandidates = Array.from(document.querySelectorAll(
                "[role='dialog'], [aria-modal='true'], div[class*='modal'], section[class*='modal']"
              ));
              const visibleDialogs = dialogCandidates.filter(isVisible);
              const overlayPresent = Boolean(hasAlertToken && visibleDialogs.length > 0);
              return {
                overlay_present: overlayPresent,
                has_alert_token: hasAlertToken,
                visible_dialog_count: Number(visibleDialogs.length || 0),
              };
            }
            """
        )
        if isinstance(out, dict):
            return dict(out)
    except Exception:
        return {"overlay_present": False, "reason": "probe_failed"}
    return {"overlay_present": False, "reason": "probe_unknown"}


def _dom_click_overlay_close_control(page: Any) -> bool:
    if page is None or not hasattr(page, "evaluate"):
        return False
    try:
        out = page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const dialogs = Array.from(document.querySelectorAll("[role='dialog'], [aria-modal='true'], div[class*='modal']"))
                .filter(isVisible);
              const roots = dialogs.length ? dialogs : [document];
              const closeToken = ["閉じる", "close", "skip", "not now", "later", "後で"];
              for (const root of roots) {
                const controls = Array.from(root.querySelectorAll("button, [role='button'], [aria-label]"));
                for (const el of controls) {
                  if (!isVisible(el)) continue;
                  const aria = String(el.getAttribute("aria-label") || "").toLowerCase();
                  const text = String(el.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  const cls = String(el.className || "").toLowerCase();
                  const looksClose = closeToken.some((token) => aria.includes(token) || text.includes(token))
                    || cls.includes("close")
                    || text === "x"
                    || text === "×";
                  if (!looksClose) continue;
                  try {
                    el.click();
                    return true;
                  } catch (_err) {
                  }
                }
              }
              return false;
            }
            """
        )
        return bool(out)
    except Exception:
        return False


def _skyscanner_dismiss_results_overlay(
    *,
    browser: Any,
    timeout_ms: int = 800,
    max_clicks: int = 2,
) -> Dict[str, Any]:
    """Best-effort dismiss for results-page overlays (price alert/login modal)."""
    page = getattr(browser, "page", None)
    probe_before = _detect_skyscanner_results_overlay(page)
    if not bool((probe_before or {}).get("overlay_present")):
        return {
            "ok": True,
            "reason": "overlay_not_present",
            "selector_used": "",
            "evidence": {"probe": probe_before},
        }

    selectors = [
        "[role='dialog'] button[aria-label*='閉じる']",
        "[role='dialog'] button[aria-label*='Close']",
        "[aria-modal='true'] button[aria-label*='閉じる']",
        "[aria-modal='true'] button[aria-label*='Close']",
        "button[aria-label*='閉じる']",
        "button[aria-label*='Close']",
        "button:has-text('閉じる')",
        "button:has-text('後で')",
        "button:has-text('あとで')",
        "button:has-text('Skip')",
        "button:has-text('Not now')",
        "button:has-text('No thanks')",
    ]
    clicks = 0
    selector_used = ""
    for selector in selectors:
        if clicks >= max(1, min(3, int(max_clicks or 2))):
            break
        try:
            browser.click(selector, timeout_ms=max(250, int(timeout_ms or 800)))
            clicks += 1
            selector_used = str(selector)
        except Exception:
            continue
        if page is not None and hasattr(page, "wait_for_timeout"):
            try:
                page.wait_for_timeout(180)
            except Exception:
                pass
        probe_after_click = _detect_skyscanner_results_overlay(page)
        if not bool((probe_after_click or {}).get("overlay_present")):
            return {
                "ok": True,
                "reason": "overlay_dismissed",
                "selector_used": selector_used,
                "evidence": {
                    "clicks": int(clicks),
                    "probe_before": probe_before,
                    "probe_after": probe_after_click,
                },
            }

    dom_clicked = _dom_click_overlay_close_control(page)
    probe_after = _detect_skyscanner_results_overlay(page)
    if bool(dom_clicked) and not bool((probe_after or {}).get("overlay_present")):
        return {
            "ok": True,
            "reason": "overlay_dismissed_dom_click",
            "selector_used": selector_used,
            "evidence": {
                "clicks": int(clicks),
                "dom_click_used": True,
                "probe_before": probe_before,
                "probe_after": probe_after,
            },
        }

    return {
        "ok": False,
        "reason": "overlay_dismiss_failed",
        "selector_used": selector_used,
        "evidence": {
            "clicks": int(clicks),
            "dom_click_used": bool(dom_clicked),
            "probe_before": probe_before,
            "probe_after": probe_after,
        },
    }


def _skyscanner_search_click_selectors(step_selectors: List[str] | None = None) -> List[str]:
    defaults = [
        "button:has-text('検索')",
        "button[aria-label*='検索']",
        "button[data-testid*='search']",
        "button[type='submit']",
    ]
    out: List[str] = []
    for item in defaults + list(step_selectors or []):
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    # Avoid broad role-text fallbacks that cause long fan-out churn on Skyscanner.
    return [s for s in out if not s.startswith("[role='button']")]


def _ensure_skyscanner_flights_context(
    browser: Any,
    *,
    timeout_ms: int = 6000,
) -> Dict[str, Any]:
    """Best-effort recovery when Skyscanner drifts to Hotels tab/surface."""
    page = getattr(browser, "page", None)
    url_before = str(getattr(page, "url", "") or "")
    if page is None:
        return {"ok": False, "reason": "no_page", "url_before": url_before, "url_after": url_before}
    url_before_lower = url_before.lower()
    if "/hotels" not in url_before_lower and "/flights" in url_before_lower:
        return {"ok": True, "reason": "already_flights", "url_before": url_before, "url_after": url_before}

    click_selectors = [
        "a#airli[role='tab']",
        "a#airli",
        "a[data-cy='airli-feature']",
        "a[data-analytics-name='flights'][role='tab']",
    ]
    selector_used = ""
    click_error = ""
    for sel in click_selectors:
        try:
            browser.click(sel, timeout_ms=max(800, int(timeout_ms)))
            selector_used = sel
            break
        except Exception as exc:
            click_error = str(exc)
            if _dom_click_first_visible_selector(page, sel):
                selector_used = sel
                break
            continue

    if not selector_used and "/hotels" in url_before_lower:
        # URL-level fallback for cases where tab anchor is hidden by transient overlays.
        target_url = url_before
        if "/hotels/" in url_before_lower:
            prefix = url_before[: url_before_lower.find("/hotels/")]
            target_url = f"{prefix}/flights"
        elif url_before_lower.endswith("/hotels"):
            target_url = f"{url_before[:-7]}/flights"
        if target_url != url_before:
            try:
                browser.goto(target_url)
                selector_used = "goto:/flights"
            except Exception as exc:
                click_error = str(exc)

    try:
        page.wait_for_timeout(900)
    except Exception:
        pass
    url_after = str(getattr(page, "url", "") or "")
    url_after_lower = url_after.lower()
    ok = ("/hotels" not in url_after_lower) and ("/flights" in url_after_lower or "/transport/flights/" in url_after_lower)
    return {
        "ok": bool(ok),
        "reason": "rebound_to_flights" if ok else "rebound_failed",
        "url_before": url_before,
        "url_after": url_after,
        "selector_used": selector_used,
        "error": click_error,
    }


def _skyscanner_date_openers(role: str, step_selectors: List[str] | None = None) -> List[str]:
    role_key = str(role or "").strip().lower()
    defaults: List[str]
    if role_key == "return":
        defaults = [
            "button[data-testid='return-btn']",
            "[data-testid='return-btn'] button",
            "button:has(span:has-text('復路'))",
            "button:has-text('復路')",
        ]
    else:
        defaults = [
            "button[data-testid='depart-btn']",
            "[data-testid='depart-btn'] button",
            "button:has(span:has-text('出発'))",
            "button:has-text('出発')",
        ]
    out: List[str] = []
    extra_non_input: List[str] = []
    extra_input: List[str] = []
    for item in list(step_selectors or []):
        text = str(item or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if "input" in lowered:
            extra_input.append(text)
        else:
            extra_non_input.append(text)
    capped_extra_non_input = extra_non_input[:3]
    capped_extra_input = extra_input[:2]
    for item in defaults + capped_extra_non_input + capped_extra_input:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[:8]


def _skyscanner_route_input_selectors(role: str, step_selectors: List[str] | None = None) -> List[str]:
    role_key = str(role or "").strip().lower()
    defaults: List[str]
    if role_key == "dest":
        defaults = [
            "input#destinationInput-input",
            "input[name='destinationInput-search']",
            "input[id='destinationInput-input'][role='combobox']",
            "input[name*='destination']",
            "input[name*='to']",
        ]
    else:
        defaults = [
            "input#originInput-input",
            "input[name='originInput-search']",
            "input[id='originInput-input'][role='combobox']",
            "input[name*='origin']",
            "input[name*='from']",
        ]
    out: List[str] = []
    for item in list(step_selectors or []) + defaults:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _listbox_ids_for_role(role: str) -> List[str]:
    role_key = str(role or "").strip().lower()
    if role_key == "dest":
        return ["destinationInput-menu", "destinationInput-listbox"]
    return ["originInput-menu", "originInput-listbox"]


def _wait_for_suggestion_listbox(page: Any, role: str, timeout_ms: int) -> bool:
    if page is None:
        return False
    selectors = [f"ul#{box_id}[role='listbox']" for box_id in _listbox_ids_for_role(role)]
    selectors.extend(
        [
            "ul[role='listbox'][id*='originInput']",
            "ul[role='listbox'][id*='destinationInput']",
            "ul[role='listbox']",
        ]
    )
    for sel in selectors:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=max(350, int(timeout_ms)))
            return True
        except Exception:
            continue
    return False


def _read_input_value(page: Any, selector: str) -> str:
    if page is None or not hasattr(page, "evaluate"):
        return ""
    try:
        out = page.evaluate(
            """
            (selector) => {
              const el = document.querySelector(String(selector || ""));
              if (!el) return "";
              return String((el.value || el.getAttribute("value") || "") || "");
            }
            """,
            str(selector or ""),
        )
        return str(out or "")
    except Exception:
        return ""


def _dom_set_input_value_exact(page: Any, selector: str, value: str) -> bool:
    if page is None or not hasattr(page, "evaluate"):
        return False
    payload = {"selector": str(selector or ""), "value": str(value or "")}
    if not payload["selector"]:
        return False
    try:
        out = page.evaluate(
            """
            (payload) => {
              const selector = String(payload.selector || "");
              const value = String(payload.value || "");
              const el = document.querySelector(selector);
              if (!el) return false;
              try {
                el.focus();
              } catch (_err) {
              }
              try {
                el.value = value;
              } catch (_err) {
                return false;
              }
              for (const evt of ["input", "change", "keyup"]) {
                try {
                  el.dispatchEvent(new Event(evt, { bubbles: true }));
                } catch (_err) {
                }
              }
              return String(el.value || "") === value;
            }
            """,
            payload,
        )
        return bool(out)
    except Exception:
        return False


def _click_best_suggestion(page: Any, *, role: str, raw_value: str) -> Dict[str, Any]:
    if page is None or not hasattr(page, "evaluate"):
        return {"ok": False, "reason": "page_unavailable"}
    payload = {"value": str(raw_value or ""), "role": str(role or "")}
    try:
        out = page.evaluate(
            """
            (payload) => {
              const role = String(payload.role || "").toLowerCase();
              const raw = String(payload.value || "").trim();
              if (!raw) return { ok: false, reason: "empty_query" };
              const up = raw.toUpperCase();
              const iata = /^[A-Z]{3}$/.test(up) ? up : "";
              const listboxIds = role === "dest"
                ? ["destinationInput-menu", "destinationInput-listbox"]
                : ["originInput-menu", "originInput-listbox"];

              const isVisible = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                return !!r && r.width > 0 && r.height > 0;
              };
              const normalize = (text) => String(text || "").replace(/\\s+/g, " ").trim().toUpperCase();

              let listbox = null;
              for (const id of listboxIds) {
                const node = document.getElementById(id);
                if (node && isVisible(node)) {
                  listbox = node;
                  break;
                }
              }
              if (!listbox) {
                const fallback = Array.from(document.querySelectorAll("ul[role='listbox']")).find(isVisible);
                listbox = fallback || null;
              }
              if (!listbox) return { ok: false, reason: "listbox_not_visible" };

              const options = Array.from(listbox.querySelectorAll("li[role='option'], [role='option']"))
                .filter(isVisible);
              if (!options.length) return { ok: false, reason: "suggestion_not_found" };

              const scored = options.map((opt, idx) => {
                const section = opt.querySelector("section[aria-label]") || opt.querySelector("section");
                const aria = normalize((section && section.getAttribute("aria-label")) || opt.getAttribute("aria-label") || "");
                const secId = normalize((section && section.id) || "");
                const txt = normalize(opt.textContent || "");
                const testIdNode = opt.querySelector("[data-testid]");
                const testId = normalize((testIdNode && testIdNode.getAttribute("data-testid")) || "");
                let score = 0;
                let iataHit = false;
                if (iata) {
                  if (secId === iata) {
                    score += 140;
                    iataHit = true;
                  }
                  if (aria.includes(`(${iata})`)) {
                    score += 120;
                    iataHit = true;
                  }
                  if (txt.includes(`(${iata})`)) {
                    score += 110;
                    iataHit = true;
                  }
                  if (testId.includes(`(${iata})`)) {
                    score += 90;
                    iataHit = true;
                  }
                  if (aria.includes(iata)) {
                    score += 80;
                    iataHit = true;
                  }
                  if (txt.includes(iata)) {
                    score += 70;
                    iataHit = true;
                  }
                }
                const rawNorm = normalize(raw);
                if (rawNorm) {
                  if (aria.includes(rawNorm)) score += 60;
                  if (txt.includes(rawNorm)) score += 50;
                  if (testId.includes(rawNorm)) score += 40;
                }
                return { opt, idx, score, aria, secId, iataHit };
              });

              scored.sort((a, b) => b.score - a.score || a.idx - b.idx);
              const best = scored[0];
              if (!best || best.score <= 0) return { ok: false, reason: "suggestion_not_found" };
              if (iata && !best.iataHit) return { ok: false, reason: "suggestion_not_found" };
              best.opt.click();
              return {
                ok: true,
                reason: "suggestion_clicked",
                option_index: Number(best.idx || 0),
                option_score: Number(best.score || 0),
                option_aria: String(best.aria || ""),
                option_id: String(best.secId || ""),
              };
            }
            """,
            payload,
        )
        return dict(out) if isinstance(out, dict) else {"ok": False, "reason": "unknown"}
    except Exception:
        return {"ok": False, "reason": "eval_failed"}


def _skyscanner_fill_and_commit_location(
    *,
    browser: Any,
    role: str,
    value: str,
    selectors: List[str] | None = None,
    timeout_ms: int,
) -> Dict[str, Any]:
    """Fill Skyscanner route field and commit by selecting from suggestion listbox."""
    page = getattr(browser, "page", None)
    role_key = str(role or "").strip().lower()
    target_value = str(value or "").strip()
    if role_key not in {"origin", "dest"}:
        return {"ok": False, "reason": "invalid_role", "selector_used": "", "evidence": {"role": role_key}}
    if not target_value:
        return {"ok": False, "reason": "empty_value", "selector_used": "", "evidence": {"role": role_key}}

    input_selectors = _skyscanner_route_input_selectors(role_key, selectors)
    expected_iata = _extract_iata(target_value)
    per_fill_timeout = max(500, min(2000, int(timeout_ms or 1200)))
    attempts = 2 if expected_iata else 1
    selector_used = ""
    last_error: Exception | None = None
    last_pick: Dict[str, Any] = {}
    last_typed_value = ""

    for _attempt_idx in range(attempts):
        selector_used = ""
        for sel in input_selectors[:8]:
            try:
                browser.fill(sel, target_value, timeout_ms=per_fill_timeout)
                selector_used = sel
                break
            except Exception as exc:
                last_error = exc
                continue
        if not selector_used:
            continue

        if page is None:
            return {
                "ok": True,
                "reason": "fill_only_no_page",
                "selector_used": selector_used,
                "evidence": {"selectors": input_selectors[:8]},
            }

        if expected_iata and hasattr(page, "evaluate"):
            last_typed_value = _read_input_value(page, selector_used)
            if last_typed_value.strip().upper() != expected_iata:
                forced = _dom_set_input_value_exact(page, selector_used, expected_iata)
                if forced:
                    last_typed_value = _read_input_value(page, selector_used)
                if last_typed_value.strip().upper() != expected_iata:
                    continue

        if not _wait_for_suggestion_listbox(
            page, role_key, timeout_ms=max(600, min(1600, int(timeout_ms or 1200)))
        ):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            continue

        picked = _click_best_suggestion(page, role=role_key, raw_value=target_value)
        last_pick = dict(picked) if isinstance(picked, dict) else {}
        if not bool((picked or {}).get("ok")):
            continue
        if expected_iata:
            option_id = str((picked or {}).get("option_id", "") or "").upper()
            option_aria = str((picked or {}).get("option_aria", "") or "").upper()
            if option_id != expected_iata and f"({expected_iata})" not in option_aria:
                # Common transient: first key lost during type => wrong suggestion selected.
                continue
        return {
            "ok": True,
            "reason": "combobox_fill_success",
            "selector_used": selector_used,
            "evidence": {
                "option_index": int((picked or {}).get("option_index", -1)),
                "option_score": int((picked or {}).get("option_score", 0)),
                "option_aria": str((picked or {}).get("option_aria", "") or "")[:180],
                "option_id": str((picked or {}).get("option_id", "") or "")[:40],
                "expected_iata": expected_iata,
                "typed_value_last": str(last_typed_value or "")[:40],
            },
        }

    failure_reason = "input_fill_failed" if not selector_used else "suggestion_not_found"
    if expected_iata and selector_used and bool(last_pick.get("ok")):
        failure_reason = "suggestion_mismatch_expected_iata"
    return {
        "ok": False,
        "reason": failure_reason,
        "selector_used": selector_used,
        "evidence": {
            "selectors": input_selectors[:8],
            "error": str(type(last_error).__name__) if last_error else "",
            "pick_result": dict(last_pick) if isinstance(last_pick, dict) else {},
            "expected_iata": expected_iata,
            "typed_value_last": str(last_typed_value or "")[:40],
        },
    }


def _skyscanner_fill_date_via_picker(
    *,
    browser: Any,
    role: str,
    date: str,
    timeout_ms: int,
    role_selectors: List[str] | None = None,
) -> Dict[str, Any]:
    """Set Skyscanner date via calendar widget using stable test ids + aria labels."""
    parts = _parse_iso_date_parts(date)
    if parts is None:
        return {"ok": False, "reason": "invalid_date_format", "selector_used": "", "evidence": {"date": str(date or "")}}
    page = getattr(browser, "page", None)
    if page is None:
        return {"ok": False, "reason": "page_unavailable", "selector_used": "", "evidence": {}}

    openers = _skyscanner_date_openers(role, role_selectors)
    per_click_timeout = max(500, min(1400, int(timeout_ms or 1200)))
    try:
        selector_used = _click_first_selector(browser, openers, timeout_ms=per_click_timeout)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "calendar_not_open",
            "selector_used": "",
            "evidence": {"openers": openers[:8], "error": str(type(exc).__name__)},
        }

    calendar_wait_timeout = max(1100, min(3200, int(timeout_ms or 1600)))
    if not _wait_for_calendar_root(page, timeout_ms=calendar_wait_timeout):
        # Retry opener once because Skyscanner sometimes drops the first opener click while
        # route field focus changes.
        try:
            _click_first_selector(browser, openers[:3], timeout_ms=max(450, per_click_timeout // 2))
        except Exception:
            pass
    if not _wait_for_calendar_root(page, timeout_ms=calendar_wait_timeout):
        return {
            "ok": False,
            "reason": "calendar_not_open",
            "selector_used": selector_used,
            "evidence": {"openers": openers[:8], "opener_retry_attempted": True},
        }

    month_hops = 0
    max_month_hops = 15
    while month_hops <= max_month_hops:
        if not _calendar_contains_target_month(
            page,
            year=int(parts["year"]),
            month=int(parts["month"]),
        ):
            if not _click_next_month(page):
                return {
                    "ok": False,
                    "reason": "month_nav_exhausted",
                    "selector_used": selector_used,
                    "evidence": {
                        "month_hops": int(month_hops),
                        "target": dict(parts),
                        "last_reason": "target_month_not_visible",
                    },
                }
            month_hops += 1
            try:
                page.wait_for_timeout(180)
            except Exception:
                pass
            continue
        outcome = _click_target_day(
            page,
            year=int(parts["year"]),
            month=int(parts["month"]),
            day=int(parts["day"]),
        )
        if bool(outcome.get("ok", False)):
            return {
                "ok": True,
                "reason": "date_selected",
                "selector_used": selector_used,
                "evidence": {
                    "month_hops": int(month_hops),
                    "day_aria_label": str(outcome.get("aria_label", "") or ""),
                    "target": dict(parts),
                },
            }
        if not _click_next_month(page):
            return {
                "ok": False,
                "reason": "month_nav_exhausted",
                "selector_used": selector_used,
                "evidence": {
                    "month_hops": int(month_hops),
                    "target": dict(parts),
                    "last_reason": str(outcome.get("reason", "") or ""),
                },
            }
        month_hops += 1
        try:
            page.wait_for_timeout(140)
        except Exception:
            pass

    return {
        "ok": False,
        "reason": "month_nav_exhausted",
        "selector_used": selector_used,
        "evidence": {"month_hops": int(month_hops), "target": dict(parts)},
    }
