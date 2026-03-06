"""Google Flights selector generation helpers.

Move-only extraction from core/scenario_runner.py.
No behavior changes.
"""

from typing import Callable, List


def build_google_fill_fallback_selectors(
    role_name: str,
    locale_hint: str,
    google_role_tokens_fn: Callable,
    prioritize_tokens_fn: Callable,
    dedupe_selectors_fn: Callable,
) -> List[str]:
    """Generate robust Google Flights fill selector fallbacks for route fields.

    Args:
        role_name: Field role (origin, dest, depart, return)
        locale_hint: Locale string for token prioritization
        google_role_tokens_fn: Function to get Google role tokens
        prioritize_tokens_fn: Function to prioritize tokens by locale
        dedupe_selectors_fn: Function to deduplicate selector list

    Returns:
        List of selector strings ordered by priority
    """
    label_tokens = prioritize_tokens_fn(
        google_role_tokens_fn(role_name, "selector_ja") + google_role_tokens_fn(role_name, "selector_en"),
        locale_hint=locale_hint,
    )
    selectors = []
    for token in label_tokens:
        label = str(token or "").strip()
        if not label:
            continue
        selectors.extend(
            [
                f"[role='combobox'][aria-label*='{label}']",
                f"[role='button'][aria-label*='{label}']",
                f"input[aria-label*='{label}']",
                f"input[placeholder*='{label}']",
                f"[aria-label*='{label}']",
            ]
        )
    if role_name == "origin":
        selectors.extend(["input[name*='origin']", "input[name*='from']", "[data-testid*='origin']", "[data-testid*='from']"])
    elif role_name == "dest":
        selectors.extend(
            [
                "input[name*='destination']",
                "input[name*='to']",
                "[data-testid*='destination']",
                "[data-testid*='to']",
            ]
        )
    elif role_name == "depart":
        selectors.extend(["input[name*='depart']", "[data-testid*='depart']"])
    elif role_name == "return":
        selectors.extend(["input[name*='return']", "[data-testid*='return']"])
        # Common lowercase variant appears on some sites.
        selectors.append("input[aria-label*='return']")
    return dedupe_selectors_fn(selectors)
