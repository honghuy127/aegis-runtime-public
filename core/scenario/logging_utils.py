"""Small logging helpers for scenario modules."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def compact_context(**payload: Any) -> Dict[str, Any]:
    """Return payload without empty-string values."""
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if value == "":
            continue
        out[key] = value
    return out


def get_docs_hint_for_reason(reason: str, max_items: int = 3) -> Optional[str]:
    """Get documentation hint for a failure reason code.

    Safe wrapper around utils.kb that never raises. Returns None if KB unavailable
    or if reason not mapped.

    Args:
        reason: StepResult.reason code (e.g., "calendar_not_open").
        max_items: Maximum docs to include in hint.

    Returns:
        Formatted docs hint or None if unavailable.

    Example:
        >>> hint = get_docs_hint_for_reason("calendar_not_open")
        >>> # "Docs: date_picker -> kb/patterns/date_picker.md"
    """
    try:
        from utils.kb import get_kb, get_docs_for_reason, format_docs_hint

        kb = get_kb()
        docs = get_docs_for_reason(kb, reason)

        if not docs:
            return None

        return format_docs_hint(docs, max_items=max_items)

    except Exception as e:
        # Never crash; just log at debug and return None
        logger.debug(f"Failed to get docs hint for reason '{reason}': {e}")
        return None


# NOTE: Optional integration hook
# To add docs hints to failure logs, use get_docs_hint_for_reason() in error handlers:
#
#   if not step.ok:
#       docs_hint = get_docs_hint_for_reason(step.reason)
#       if docs_hint:
#           logger.debug(docs_hint)
#       logger.error(f"Step failed: {step.reason}")
#
