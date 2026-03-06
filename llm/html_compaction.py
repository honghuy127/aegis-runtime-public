"""HTML compaction helpers for prompt budgeting."""

import re
from typing import Any, Dict, List
from bs4 import BeautifulSoup


_PRICE_TOKEN_RE = re.compile(
    r"(?:¥\s*\d[\d,]*|\$\s*\d[\d,]*|€\s*\d[\d,]*|£\s*\d[\d,]*|"
    r"JPY\s*\d[\d,]*|USD\s*\d[\d,]*|EUR\s*\d[\d,]*|GBP\s*\d[\d,]*)",
    re.IGNORECASE,
)
_ROUTE_HINT_RE = re.compile(
    r"\b(where from|where to|from|to|departure|depart|return|round trip|one way|nonstop)\b",
    re.IGNORECASE,
)
_DATE_HINT_RE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"出発|復路|往路|calendar|date)\b",
    re.IGNORECASE,
)
_RESULT_HINT_RE = re.compile(
    r"\b(itinerar|sort|filter|results?|cheapest|best|fastest|運賃|最安|経由|直行)\b",
    re.IGNORECASE,
)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def compact_html_for_prompt(html: str, max_chars: int = 18000) -> str:
    """Strip noisy tags and prefer price/result-focused snippets for prompt input."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()

    root = soup.body or soup
    cleaned = str(root)

    # Prioritize snippets that are likely to contain fare data and route context.
    focused_blocks = []
    seen_blocks = set()

    def _add_block(tag_like) -> None:
        if tag_like is None:
            return
        node = tag_like
        for _ in range(2):
            parent = getattr(node, "parent", None)
            if not parent or getattr(parent, "name", None) in ("body", "html", "[document]"):
                break
            node = parent
        block = re.sub(r"\s+", " ", str(node)).strip()
        if not block:
            return
        if len(block) > 1400:
            block = block[:1400]
        if block in seen_blocks:
            return
        seen_blocks.add(block)
        focused_blocks.append(block)

    for el in root.select("[aria-label]"):
        aria = (el.get("aria-label") or "").strip()
        if _PRICE_TOKEN_RE.search(aria) or _ROUTE_HINT_RE.search(aria):
            _add_block(el)
        if len(focused_blocks) >= 36:
            break

    if len(focused_blocks) < 36:
        for text_node in root.find_all(string=True):
            text = (str(text_node) or "").strip()
            if not text:
                continue
            if _PRICE_TOKEN_RE.search(text) or _ROUTE_HINT_RE.search(text):
                _add_block(getattr(text_node, "parent", None))
            if len(focused_blocks) >= 36:
                break

    if focused_blocks:
        focused = "\n".join(focused_blocks)
        if len(focused) >= max_chars:
            return focused[:max_chars]

        remaining = max_chars - len(focused)
        if len(cleaned) <= remaining:
            return focused + "\n" + cleaned

        tail_chunk = max(1, remaining // 2)
        head_chunk = max(0, remaining - tail_chunk)
        return (
            focused
            + "\n<!-- ...CONTEXT... -->\n"
            + cleaned[:head_chunk]
            + "\n<!-- ...SNIP... -->\n"
            + cleaned[-tail_chunk:]
        )

    if len(cleaned) <= max_chars:
        return cleaned

    chunk = max_chars // 3
    mid_start = max((len(cleaned) // 2) - (chunk // 2), 0)
    mid_end = mid_start + chunk
    return cleaned[:chunk] + "\n<!-- ...SNIP... -->\n" + cleaned[mid_start:mid_end] + "\n<!-- ...SNIP... -->\n" + cleaned[-chunk:]


def semantic_html_chunks_for_prompt(
    html: str,
    *,
    max_chunks: int = 3,
    chunk_chars: int = 4200,
    max_total_chars: int = 12000,
) -> List[Dict[str, Any]]:
    """Build scored semantic DOM chunks for planner/repair prompts."""
    if not isinstance(html, str) or not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()
    root = soup.body or soup

    selectors = [
        "main",
        "form",
        "section",
        "article",
        "[data-testid]",
        "[role='main']",
        "[role='dialog']",
        "[role='listbox']",
        "[role='grid']",
        "input[aria-label]",
        "button[aria-label]",
        "[aria-label]",
        "div[class*='Calendar']",
        "div[class*='Suggestion']",
    ]
    candidates = []
    seen_nodes = set()
    for selector in selectors:
        try:
            nodes = root.select(selector)
        except Exception:
            nodes = []
        for node in nodes:
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            candidates.append(node)
            if len(candidates) >= 180:
                break
        if len(candidates) >= 180:
            break
    if not candidates:
        candidates = [root]

    scored: List[Dict[str, Any]] = []
    seen_blocks = set()
    for order, node in enumerate(candidates):
        text = _normalize_ws(node.get_text(" ", strip=True))
        if len(text) < 10:
            continue
        attrs = _normalize_ws(
            " ".join(
                [
                    str(node.get("aria-label") or ""),
                    str(node.get("data-testid") or ""),
                    str(node.get("id") or ""),
                    str(node.get("class") or ""),
                ]
            )
        )
        probe_blob = f"{text} {attrs}"
        score = 0
        reasons: List[str] = []
        if _PRICE_TOKEN_RE.search(probe_blob):
            score += 4
            reasons.append("price")
        if _ROUTE_HINT_RE.search(probe_blob):
            score += 3
            reasons.append("route")
        if _DATE_HINT_RE.search(probe_blob):
            score += 3
            reasons.append("date")
        if _RESULT_HINT_RE.search(probe_blob):
            score += 2
            reasons.append("results")
        tag_name = str(getattr(node, "name", "") or "")
        if tag_name in {"main", "form", "section"}:
            score += 1
        if score <= 0:
            continue

        block_html = _normalize_ws(str(node))
        if not block_html:
            continue
        if len(block_html) > int(chunk_chars):
            block_html = block_html[: int(chunk_chars)]
        block_key = block_html[:360]
        if block_key in seen_blocks:
            continue
        seen_blocks.add(block_key)
        scored.append(
            {
                "html": block_html,
                "score": int(score),
                "reason": ",".join(reasons[:3]),
                "tag": tag_name,
                "order": int(order),
            }
        )
        if len(scored) >= max(24, int(max_chunks) * 8):
            break

    if not scored:
        fallback = _normalize_ws(str(root))
        if not fallback:
            return []
        return [
            {
                "html": fallback[: max(1, min(int(chunk_chars), int(max_total_chars)))],
                "score": 1,
                "reason": "fallback",
                "tag": str(getattr(root, "name", "") or "body"),
            }
        ]

    scored.sort(key=lambda item: (-int(item.get("score", 0)), int(item.get("order", 0))))
    out: List[Dict[str, Any]] = []
    consumed = 0
    for item in scored:
        if len(out) >= max(1, int(max_chunks)):
            break
        html_chunk = str(item.get("html") or "")
        if not html_chunk:
            continue
        remaining = int(max_total_chars) - consumed
        if remaining <= 0:
            break
        if len(html_chunk) > remaining:
            html_chunk = html_chunk[:remaining]
        out.append(
            {
                "html": html_chunk,
                "score": int(item.get("score", 0)),
                "reason": str(item.get("reason", "")),
                "tag": str(item.get("tag", "")),
            }
        )
        consumed += len(html_chunk)
    return out
