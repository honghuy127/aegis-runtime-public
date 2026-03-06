"""Phase: commit actions for Google date picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

from typing import List, Tuple

from core.service_ui_profiles import get_service_ui_profile


def build_legacy_done_selectors(profile: dict) -> List[str]:
    """Build legacy done button selectors from config (depart flow)."""
    legacy_done_tokens = profile.get("legacy_depart_done_button_tokens", {}) if isinstance(profile, dict) else {}
    ja_done = legacy_done_tokens.get("ja", ["完了"])
    en_done = legacy_done_tokens.get("en", ["Done"])

    done_selectors: List[str] = []
    for ja_token in ja_done:
        done_selectors.extend(
            [
                f"button:has-text('{ja_token}')",
                f"[role='button']:has-text('{ja_token}')",
            ]
        )
    for en_token in en_done:
        done_selectors.extend(
            [
                f"button:has-text('{en_token}')",
                f"[role='button']:has-text('{en_token}')",
            ]
        )
    return done_selectors


def commit_depart_date(
    page,
    *,
    done_selectors: List[str],
    deadline_exceeded,
    budgeted_timeout_fn,
    logger,
) -> Tuple[bool, str, bool]:
    """Commit depart date via Done/Enter/blur."""
    commit_method = "unknown"
    done_clicked = False
    for selector in done_selectors:
        if deadline_exceeded("commit"):
            return False, commit_method, True
        try:
            if page is not None:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=150):
                    locator.click(timeout=budgeted_timeout_fn())
                    done_clicked = True
                    commit_method = "done"
                    break
        except Exception:
            continue
    if not done_clicked:
        try:
            if page is not None and hasattr(page, "keyboard"):
                page.keyboard.press("Enter")
                commit_method = "enter"
        except Exception:
            try:
                if page is not None:
                    page.locator("body").click(timeout=200)
                    commit_method = "blur"
            except Exception:
                commit_method = "unknown"

    logger.info("gf.date.commit method=%s", commit_method)
    return done_clicked, commit_method, False


def click_done_or_apply(
    page,
    *,
    locale_hint: str,
    budgeted_timeout_fn,
    profile: dict | None = None,
) -> Tuple[bool, str]:
    """Click Done/Apply button using profile tokens (return flow)."""
    if page is None:
        return False, ""
    if profile is None:
        profile = get_service_ui_profile("google_flights") or {}

    close_tokens_config = profile.get("calendar_close_button_tokens", {}).get("done", {})
    ja_tokens = close_tokens_config.get("ja", ["完了", "適用"])
    en_tokens = close_tokens_config.get("en", ["Done", "Apply"])

    done_selectors: List[str] = []
    for en_token in en_tokens:
        done_selectors.extend(
            [
                f"button:has-text('{en_token}')",
                f"[role='button']:has-text('{en_token}')",
                f"button[aria-label*='{en_token}']",
            ]
        )
    for ja_token in ja_tokens:
        done_selectors.extend(
            [
                f"button:has-text('{ja_token}')",
                f"[role='button']:has-text('{ja_token}')",
                f"button[aria-label*='{ja_token}']",
                f"[role='button'][aria-label*='{ja_token}']",
            ]
        )

    for selector in done_selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=150):
                locator.click(timeout=budgeted_timeout_fn())
                return True, selector
        except Exception:
            continue
    return False, ""
