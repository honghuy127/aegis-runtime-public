"""HTML convenience helpers for tiny selector-based extraction use-cases."""

from bs4 import BeautifulSoup


def select_text(html: str, selector: str):
    """Select the first element by CSS and return stripped text, if found."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(selector)
    return el.get_text(strip=True) if el else None
