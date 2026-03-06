"""Configuration and scope helpers extracted from core.extractor.

This module provides utilities for environment variable parsing and
page scope normalization. Used by extraction logic to resolve runtime
configuration and validate scope context.
"""

import os
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse integer environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw.strip())
    except Exception:
        return int(default)


def _normalize_page_class(value: str) -> str:
    """Normalize multiclass scope labels produced by VLM/LLM judges."""
    text = str(value or "").strip().lower()
    if text in {
        "flight_only",
        "flight_hotel_package",
        "garbage_page",
        "irrelevant_page",
        "unknown",
    }:
        return text
    return "unknown"
