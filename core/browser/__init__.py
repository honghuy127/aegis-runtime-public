"""Browser automation module providing Playwright-based page interaction primitives.

This module exports the main BrowserSession class and timeout utilities for
orchestration and scenario runner code.
"""

from core.browser.session import BrowserSession
from core.browser.timeouts import (
    apply_selector_timeout_strategy,
    safe_min_timeout_ms,
    wall_clock_deadline,
    wall_clock_remaining_ms,
    wall_clock_exhausted,
    enforce_wall_clock_deadline,
)
from core.browser.stealth import (
    _human_mimic_stealth_init_script,
    _human_mimic_chromium_user_agent,
)

__all__ = [
    "BrowserSession",
    "apply_selector_timeout_strategy",
    "safe_min_timeout_ms",
    "wall_clock_deadline",
    "wall_clock_remaining_ms",
    "wall_clock_exhausted",
    "enforce_wall_clock_deadline",
    "_human_mimic_stealth_init_script",
    "_human_mimic_chromium_user_agent",
]
