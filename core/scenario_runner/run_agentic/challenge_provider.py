"""Typed challenge-provider contract for attempt-gate orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


DetectBlockFn = Callable[[str, Any], Dict[str, Any]]
AttemptGraceFn = Callable[..., Dict[str, Any]]
AttemptFallbackFn = Callable[..., Dict[str, Any]]
ValidateClearanceFn = Callable[..., Dict[str, Any]]
AttemptLastResortManualFn = Callable[..., Dict[str, Any]]


@dataclass(frozen=True)
class ChallengeProvider:
    """Provider contract for challenge/interstitial handling."""

    name: str
    detect_block: DetectBlockFn
    attempt_grace: AttemptGraceFn
    attempt_fallback: AttemptFallbackFn
    validate_clearance: ValidateClearanceFn
    attempt_last_resort_manual: AttemptLastResortManualFn
    supports_last_resort_manual: bool = False
    requires_page_open_for_clearance: bool = False

