"""Validation helpers for plausibility, outliers, and confidence adjustments."""

from typing import Optional, Dict, Any, List
from statistics import median
from utils.thresholds import get_threshold


PLAUSIBLE_MAX_PRICE = float(get_threshold("plausible_max_price", 10_000_000))


# -------------------------
# Basic plausibility
# -------------------------

def is_plausible_price(price: Optional[float]) -> bool:
    """Return True when a parsed price is positive and within hard bounds."""
    if price is None:
        return False

    if price <= 0:
        return False

    # Upper bound guard (anti garbage parse)
    if price > PLAUSIBLE_MAX_PRICE:
        return False

    return True


# -------------------------
# Outlier detection
# -------------------------

def is_outlier(
    price: float,
    historical_prices: List[float],
    tolerance_ratio: float = 0.6,
) -> bool:
    """Return True when price deviates too far from historical median."""
    if not historical_prices:
        return False

    med = median(historical_prices)

    if med == 0:
        return False

    lower_bound = med * (1 - tolerance_ratio)
    upper_bound = med * (1 + tolerance_ratio)

    return not (lower_bound <= price <= upper_bound)


# -------------------------
# Cross-check selector vs LLM
# -------------------------

def cross_validate(
    selector_result: Optional[Dict[str, Any]],
    llm_result: Optional[Dict[str, Any]],
    tolerance_ratio: float = 0.05,
) -> str:
    """Compare selector and LLM prices and return a validation status label."""
    if not selector_result or not llm_result:
        return "insufficient_data"

    p1 = selector_result.get("price")
    p2 = llm_result.get("price")

    if p1 is None or p2 is None:
        return "insufficient_data"

    if p1 == 0 or p2 == 0:
        return "conflict"

    diff_ratio = abs(p1 - p2) / max(p1, p2)

    if diff_ratio <= tolerance_ratio:
        return "match"

    if diff_ratio <= 0.2:
        return "minor_mismatch"

    return "conflict"


# -------------------------
# Confidence adjustment
# -------------------------

def adjust_confidence_by_validation(
    result: Dict[str, Any],
    outlier: bool = False,
    cross_status: str = "insufficient_data",
) -> Dict[str, Any]:
    """Adjust confidence/reason fields based on outlier and cross-check signals."""

    confidence = result.get("confidence", "low")

    if outlier:
        result["confidence"] = "low"
        result["reason"] = "price_outlier_detected"
        return result

    if cross_status == "conflict":
        result["confidence"] = "low"
        result["reason"] = "selector_llm_conflict"
        return result

    if cross_status == "minor_mismatch":
        result["confidence"] = "medium"
        return result

    return result
