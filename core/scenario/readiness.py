"""Pure readiness helpers for scenario flows."""


def scope_is_irrelevant(scope_class: str) -> bool:
    """Return True when scope class indicates irrelevant/garbage page."""
    normalized = str(scope_class or "").strip().lower()
    return normalized in {"irrelevant_page", "garbage_page"}
