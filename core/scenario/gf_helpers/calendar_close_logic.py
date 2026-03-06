"""Calendar close dialog helpers for Google Flights date picker."""

from __future__ import annotations

import time


def close_calendar_dialog_impl(
    *,
    page,
    calendar_root,
    profile: dict,
    locale_hint: str,
    role_key: str,
    nav_steps: int,
    logger,
    budgeted_timeout_fn,
) -> tuple[bool, str, str]:
    """Attempt to close the calendar dialog and return (done_clicked, close_method, close_scope_used)."""
    close_method = "unknown"
    close_tokens_config = profile.get("calendar_close_button_tokens", {}).get("done", {})
    ja_close_tokens = close_tokens_config.get("ja", ["完了", "適用"])
    en_close_tokens = close_tokens_config.get("en", ["Done", "Apply"])

    close_selectors = [
        # Exact aria-label matches (EN and JP)
    ]
    for en_token in en_close_tokens:
        close_selectors.extend(
            [
                f"button[aria-label='{en_token}']",
                f"[role='button'][aria-label='{en_token}']",
            ]
        )
    for ja_token in ja_close_tokens:
        close_selectors.extend(
            [
                f"[role='button'][aria-label*='{ja_token}']",
            ]
        )
    # Prefix matches
    for en_token in en_close_tokens:
        close_selectors.extend(
            [
                f"button[aria-label*='{en_token}']",
                f"[role='button'][aria-label*='{en_token}']",
            ]
        )
    # Text content matches
    for en_token in en_close_tokens:
        close_selectors.extend(
            [
                f"button:has-text('{en_token}'):visible",
                f"[role='button']:has-text('{en_token}'):visible",
            ]
        )
    for ja_token in ja_close_tokens:
        close_selectors.extend(
            [
                f"button:has-text('{ja_token}'):visible",
                f"[role='button']:has-text('{ja_token}'):visible",
            ]
        )

    done_clicked = False
    close_scope_used = "calendar_root"
    for close_scope_name, close_scope in (("calendar_root", calendar_root), ("page_fallback", page)):
        if done_clicked or close_scope is None:
            continue
        for close_sel in close_selectors:
            try:
                btn_locator = close_scope.locator(close_sel).first
                if btn_locator.is_visible(timeout=150):
                    btn_locator.click(timeout=budgeted_timeout_fn())
                    done_clicked = True
                    close_method = "done_button"
                    close_scope_used = close_scope_name
                    logger.info(
                        "gf_set_date.done.clicked role=%s nav_steps=%d scope=%s",
                        role_key,
                        nav_steps,
                        close_scope_name,
                    )
                    time.sleep(0.1)
                    break
            except Exception:
                pass

    if not done_clicked:
        # Fallback: Escape key
        try:
            if hasattr(page, "keyboard"):
                page.keyboard.press("Escape")
                close_method = "escape"
                logger.info("gf_set_date.close.escape role=%s nav_steps=%d", role_key, nav_steps)
                time.sleep(0.1)
        except Exception:
            logger.debug("gf_set_date.close.escape_failed role=%s", role_key)

    return done_clicked, close_method, close_scope_used
