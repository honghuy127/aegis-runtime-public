"""Language and page-signal helpers for planning context."""

import re
from typing import Dict, Optional, Tuple


_JA_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_ROUTE_HINT_RE = re.compile(
    r"\b(where from|where to|from|to|departure|depart|return|round trip|one way|nonstop)\b",
    re.IGNORECASE,
)
_AUTH_HINT_RE = re.compile(
    r"(?:email|password|login|sign[\s_-]*in|sign[\s_-]*up|register|account|"
    r"newsletter|subscribe|member|メール|会員|ログイン|パスワード|氏名|お名前|電話)",
    re.IGNORECASE,
)
_MODAL_HINT_RE = re.compile(
    r"(?:cookie|consent|accept|agree|close|dismiss|×|閉じる|同意)",
    re.IGNORECASE,
)
_HTML_LANG_RE = re.compile(r"<html[^>]*\blang\s*=\s*['\"]?([^'\"\s>]+)", re.IGNORECASE)
_META_TAG_RE = re.compile(r"<meta\s+[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r"([a-zA-Z0-9_-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"'>=<>`]+)",
    re.IGNORECASE,
)
_KANA_RE = re.compile(r"[ぁ-んァ-ヶ]")
_HANGUL_RE = re.compile(r"[가-힣]")


def _map_lang_code(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if text.startswith("ja"):
        return "ja"
    if text.startswith("en"):
        return "en"
    if text.startswith("zh"):
        return "zh"
    if text.startswith("ko"):
        return "ko"
    return ""


def _parse_meta_language(html: str) -> str:
    for tag in _META_TAG_RE.findall(html or ""):
        attrs = {}
        for match in _META_ATTR_RE.findall(tag):
            key = match[0].strip().lower()
            value = match[1].strip().strip("\"").strip("'")
            attrs[key] = value
        http_equiv = attrs.get("http-equiv", "").strip().lower()
        name = attrs.get("name", "").strip().lower()
        if http_equiv == "content-language":
            content = attrs.get("content", "")
            mapped = _map_lang_code(content)
            if mapped:
                return mapped
        if name == "language":
            content = attrs.get("content", "")
            mapped = _map_lang_code(content)
            if mapped:
                return mapped
    return ""


def expected_language_from_locale(mimic_locale: Optional[str]) -> str:
    """Derive language code from locale hint (e.g., ja-JP -> ja)."""
    if not isinstance(mimic_locale, str):
        return ""
    text = mimic_locale.strip()
    if not text:
        return ""
    return text.split("-", 1)[0].lower()


def detect_ui_language(html: str, mimic_locale: Optional[str]) -> Tuple[str, str]:
    """Detect UI language from HTML signals and locale hint."""
    html_text = html or ""
    match = _HTML_LANG_RE.search(html_text)
    if match:
        mapped = _map_lang_code(match.group(1))
        if mapped:
            return mapped, "html_lang"

    meta_lang = _parse_meta_language(html_text)
    if meta_lang:
        return meta_lang, "meta_lang"

    if _KANA_RE.search(html_text):
        return "ja", "kana"

    if _HANGUL_RE.search(html_text):
        return "ko", "hangul"

    locale_lang = _map_lang_code(expected_language_from_locale(mimic_locale))
    if locale_lang:
        return locale_lang, "locale"

    return "unknown", "unknown"


def detect_page_language(html: str) -> str:
    """Detect rough page language from visible text signal."""
    if not isinstance(html, str) or not html:
        return "unknown"
    sample = re.sub(r"<[^>]+>", " ", html)
    sample = sample[:30000]
    ja_count = len(_JA_CHAR_RE.findall(sample))
    latin_count = len(_LATIN_CHAR_RE.findall(sample))
    if ja_count >= 24 and ja_count > (latin_count * 0.15):
        return "ja"
    if latin_count >= 80 and ja_count < 12:
        return "en"
    if ja_count > 0 and latin_count > 0:
        return "mixed"
    return "unknown"


def page_signal_scores(html: str) -> Dict[str, int]:
    """Compute simple signal counts to steer planner away from auth forms."""
    if not isinstance(html, str) or not html:
        return {"route": 0, "auth": 0, "modal": 0}
    route = len(_ROUTE_HINT_RE.findall(html))
    auth = len(_AUTH_HINT_RE.findall(html))
    modal = len(_MODAL_HINT_RE.findall(html))
    return {"route": route, "auth": auth, "modal": modal}
