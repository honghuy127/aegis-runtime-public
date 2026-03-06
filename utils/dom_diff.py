"""DOM snapshot comparison helpers for change detection between page states."""

import hashlib


def hash_dom(html: str):
    """Return a stable SHA-256 hash for an HTML snapshot."""
    return hashlib.sha256(html.encode()).hexdigest()


def dom_changed(old_html: str, new_html: str):
    """Return True when two HTML snapshots differ by hash."""
    return hash_dom(old_html) != hash_dom(new_html)


def dom_size_delta(old_html: str, new_html: str):
    """Return absolute character-length delta between two HTML snapshots."""
    return abs(len(new_html) - len(old_html))
