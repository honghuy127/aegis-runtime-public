"""Calendar month header parsing helpers for Google Flights.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.scenario.calendar_parsing import (
    infer_year_for_visible_month,
    parse_month_only,
    parse_month_year,
)
from core.service_ui_profiles import profile_localized_list


def _parse_header_with_context_impl(
    text_value: str,
    *,
    target_year: int,
    target_month: int,
    max_nav_steps: int,
    locale_hint: str = "",
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    year_val, month_val = parse_month_year(text_value, locale=locale_hint or "en")
    if year_val and month_val:
        return year_val, month_val, "parse_month_year_utility"

    visible_month = parse_month_only(text_value, locale=locale_hint or "en")
    if visible_month:
        inferred_year = infer_year_for_visible_month(
            visible_month=visible_month,
            target_year=target_year,
            target_month=target_month,
            max_nav_steps=max_nav_steps,
        )
        if inferred_year is not None:
            return inferred_year, visible_month, "month_only_inferred_year"

    return None, None, None


def extract_calendar_month_header_impl(
    *,
    calendar_root,
    page,
    profile: Dict[str, Any],
    locale_hint: str,
    target_year: int,
    target_month: int,
    max_nav_steps: int,
    role_key: str,
    logger,
) -> Dict[str, Any]:
    """Extract and parse month/year header from calendar root.

    Returns dict with parsed year/month, parsing method, and debug evidence.
    """
    month_header_text = None
    parsed_month = None
    parsed_year = None

    # Exclusion list: text patterns that are NOT month headers (from config)
    # GUARD: Prevent footer buttons from being mistaken for month headers
    exclusion_texts_config = profile.get("calendar_month_header_exclusion_texts", {})
    ja_exclusions = exclusion_texts_config.get("ja", [])
    en_exclusions = exclusion_texts_config.get("en", [])
    non_header_texts = set(ja_exclusions + en_exclusions) if (ja_exclusions or en_exclusions) else {
        "完了", "Done", "Close", "閉じる", "適用", "Apply", "キャンセル", "Cancel",
        "OK", "確定", "Confirm", "戻る", "Back", "次へ", "Next", "前へ", "Previous"
    }

    # Month header selectors (semantic first, scoped to calendar root)
    # DOC: See docs/kb/30_patterns/date_picker.md#month-header-detection
    # FIX-004: Expanded header selectors to handle more Japanese patterns
    # Build selectors dynamically from config tokens for month/year detection
    month_year_tokens = profile.get("calendar_month_year_aria_tokens", {})
    month_tokens = month_year_tokens.get("month", {}).get("ja", []) + month_year_tokens.get("month", {}).get("en", [])
    year_tokens = month_year_tokens.get("year", {}).get("ja", []) + month_year_tokens.get("year", {}).get("en", [])

    header_selectors = [
        "[role='heading']:visible",         # Semantic heading role
        "[aria-level]:visible",             # ARIA heading like <h1-h6>
        "h1:visible, h2:visible, h3:visible",  # HTML headings
        "[aria-current='date']:visible",    # Current date marker (sometimes shows month)
        "div[role='presentation']:visible", # Presentation container (might wrap header)
        "[class*='header']:visible",        # Calendar header container
        "[class*='title']:visible",         # Title class
        "[class*='month']:visible",         # Month-related class
    ]
    # Add tokenized month/year aria-label selectors from config
    if month_tokens:
        for token in month_tokens:
            header_selectors.extend([
                f"[aria-label*='{token}']:visible",
                f"button[aria-label*='{token}']:visible",
            ])
    if year_tokens:
        for token in year_tokens:
            header_selectors.extend([
                f"[aria-label*='{token}']:visible",
                f"button[aria-label*='{token}']:visible",
            ])

    header_selectors_tried = []
    header_text_candidates = []
    header_rejected_texts = []
    header_parse_ok = False
    parsing_method = None  # Track which parsing method succeeded

    for header_sel in header_selectors:
        try:
            # If calendar_root is available, scope to it; otherwise use page
            if calendar_root is not None:
                header_locators = calendar_root.locator(header_sel)
            else:
                header_locators = page.locator(header_sel)

            count = header_locators.count() if hasattr(header_locators, 'count') else 1
            for i in range(min(count, 3)):  # Try up to 3 matches, bounded
                try:
                    header_locator = header_locators.nth(i)
                    if header_locator.is_visible(timeout=200):
                        text = header_locator.text_content() or ""
                        text_stripped = text.strip()

                        header_text_candidates.append(text_stripped)

                        # Skip empty text
                        if not text_stripped:
                            continue

                        # Skip non-header texts (buttons, labels, etc.)
                        if text_stripped in non_header_texts:
                            header_rejected_texts.append(text_stripped)
                            logger.debug(
                                "gf_set_date.month_header.rejected_non_header role=%s text=%s",
                                role_key,
                                text_stripped,
                            )
                            continue

                        # NEW: Use parse_month_year utility (handles ja-JP, en, numeric formats)
                        parsed_year, parsed_month, parse_method = _parse_header_with_context_impl(
                            text_stripped,
                            target_year=target_year,
                            target_month=target_month,
                            max_nav_steps=max_nav_steps,
                            locale_hint=locale_hint,
                        )
                        if parsed_year and parsed_month:
                            header_parse_ok = True
                            parsing_method = parse_method or "parse_month_year_utility"
                            month_header_text = text_stripped
                            logger.debug(
                                "gf_set_date.month_header.parsed_with_utility role=%s year=%d month=%d text=%s method=%s",
                                role_key,
                                parsed_year,
                                parsed_month,
                                text_stripped[:50],
                                parsing_method,
                            )
                            break

                    header_selectors_tried.append(header_sel)
                except Exception:
                    pass

                if header_parse_ok:
                    break

        except Exception:
            header_selectors_tried.append(header_sel)
            pass

        if header_parse_ok:
            break

    # FIX-005: Fallback to month/year inference from visible date cells in grid
    # Only use if primary parse_month_year method failed
    if not header_parse_ok and calendar_root is not None:
        logger.debug("gf_set_date.month_header.fallback_to_grid_infer role=%s", role_key)
        try:
            month_tokens_config = profile.get("calendar_month_year_aria_tokens", {}).get("month", {})
            if isinstance(month_tokens_config, dict):
                month_tokens = profile_localized_list({"key": month_tokens_config}, "key", locale=locale_hint)
            else:
                month_tokens = month_tokens_config if isinstance(month_tokens_config, list) else []
            if not month_tokens:
                month_tokens = ["月"]

            aria_infer_selectors = [
                "[role='gridcell'][aria-label]",     # Legacy gridcell aria-label on cell
                "[role='grid'] [aria-label]",        # aria-label on child button inside grid
            ]
            for token in month_tokens:
                aria_infer_selectors.extend(
                    [
                        f"[role='button'][aria-label*='{token}']",
                        f"button[aria-label*='{token}']",
                    ]
                )
            infer_examined = 0
            infer_max = 12

            for infer_sel in aria_infer_selectors:
                if header_parse_ok or infer_examined >= infer_max:
                    break
                try:
                    infer_locs = calendar_root.locator(infer_sel)
                    infer_count = infer_locs.count() if hasattr(infer_locs, 'count') else 0
                except Exception:
                    continue

                for i in range(min(infer_count, infer_max - infer_examined)):
                    if header_parse_ok or infer_examined >= infer_max:
                        break
                    infer_examined += 1
                    try:
                        cell_locator = infer_locs.nth(i)
                        if not cell_locator.is_visible(timeout=100):
                            continue
                        aria_label = cell_locator.get_attribute("aria-label", timeout=100) or ""
                        if not aria_label:
                            continue
                        parsed_year, parsed_month, parse_method = _parse_header_with_context_impl(
                            aria_label,
                            target_year=target_year,
                            target_month=target_month,
                            max_nav_steps=max_nav_steps,
                            locale_hint=locale_hint,
                        )
                        if parsed_year and parsed_month:
                            header_parse_ok = True
                            base_method = "aria_label_fallback"
                            parsing_method = (
                                base_method
                                if parse_method == "parse_month_year_utility"
                                else f"{base_method}_{parse_method}"
                            )
                            month_header_text = f"[inferred: {aria_label[:30]}]"
                            logger.info(
                                "gf_set_date.month_header.inferred_from_aria_label role=%s year=%d month=%d aria_label=%s selector=%s",
                                role_key,
                                parsed_year,
                                parsed_month,
                                aria_label[:50],
                                infer_sel,
                            )
                            break
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("gf_set_date.month_header.fallback_grid_infer_failed error=%s", str(exc)[:50])

    return {
        "month_header_text": month_header_text,
        "parsed_month": parsed_month,
        "parsed_year": parsed_year,
        "header_selectors_tried": header_selectors_tried,
        "header_text_candidates": header_text_candidates,
        "header_rejected_texts": header_rejected_texts,
        "header_parse_ok": header_parse_ok,
        "parsing_method": parsing_method,
        "fallback_grid_infer_attempted": bool(not header_parse_ok and calendar_root is not None),
    }
