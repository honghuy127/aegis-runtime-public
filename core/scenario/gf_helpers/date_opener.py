"""Date opener selector building helpers.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import re
from typing import Optional

from core.scenario.gf_helpers.date_tokens import _google_date_opener_tokens
from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors
from core.service_ui_profiles import get_service_ui_profile


def _build_google_date_opener_selectors_impl(
    *,
    role: str,
    target_date: str,
    locale_hint: Optional[str],
    role_selectors,
    max_items: int = 12,
) -> list[str]:
    """Return bounded prioritized opener selectors for Google date chips/dialogs.

    Move-only extraction from core/scenario/google_flights.py.
    Zero behavior change.

    Motivation: results pages often expose date chips as button-style controls with
    date-valued `aria-label`s. Plan-provided combobox/input selectors can crowd out
    these button selectors under the bounded opener cap, causing false `calendar_not_open`.
    """
    role_key = str(role or "").strip().lower()
    # Get date opener default selectors from service_ui_profiles
    profile = get_service_ui_profile("google_flights")
    date_opener_selectors_config = profile.get("date_opener_default_selectors", {})
    role_config = date_opener_selectors_config.get(role_key, [])

    # Use flat list of pre-interleaved locale-aware selectors
    defaults = role_config if isinstance(role_config, list) else []

    token_info = _google_date_opener_tokens(role_key, target_date, locale_hint)
    dynamic_date_selectors = []
    for token in list(token_info.get("route_date_tokens", [])) + list(token_info.get("date_tokens", [])):
        token_q = str(token or "").replace("'", "\\'")
        if not token_q:
            continue
        dynamic_date_selectors.extend(
            [
                f"[role='button'][aria-label*='{token_q}']",
                f"button[aria-label*='{token_q}']",
                f"[aria-label*='{token_q}']",
            ]
        )

    provided = list(role_selectors) if isinstance(role_selectors, list) else []
    dynamic_candidates = _dedupe_compact_selectors(dynamic_date_selectors, max_items=64)
    structural_candidates = _dedupe_compact_selectors(provided + defaults, max_items=48)
    provided_set = {str(s or "") for s in provided if str(s or "").strip()}
    candidates = _dedupe_compact_selectors(dynamic_candidates + structural_candidates, max_items=64)
    role_tokens = [str(t or "") for t in token_info.get("role_tokens", []) if str(t or "").strip()]
    date_tokens = [str(t or "") for t in token_info.get("date_tokens", []) if str(t or "").strip()]
    route_date_tokens = [str(t or "") for t in token_info.get("route_date_tokens", []) if str(t or "").strip()]
    prefer_ja = str(locale_hint or "").strip().lower().startswith("ja")
    en_prefer_markers = ("departure", "depart", "return", "inbound", "outbound", "march", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

    def _score(selector: str) -> tuple[int, int]:
        s = str(selector or "")
        lower = s.lower()
        score = 0
        # Prefer button-like date chips on results pages.
        if "[role='button']" in lower or lower.startswith("button["):
            score += 6
        elif "[role='combobox']" in lower:
            score += 3
        elif "input[" in lower:
            score += 1
        # Strong role/date tokens.
        if any(tok and tok in s for tok in route_date_tokens):
            score += 12
        if any(tok and tok in s for tok in date_tokens):
            score += 9
        if any(tok and tok in s for tok in role_tokens):
            score += 4
        # Prefer selectors whose label content aligns with locale hint while keeping
        # bilingual coverage available as fallback.
        has_cjk = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s))
        if prefer_ja:
            if has_cjk:
                score += 2
        else:
            if not has_cjk and any(marker in lower for marker in en_prefer_markers):
                score += 2
        # Generic selectors are useful fallback, but de-prioritize them.
        if s.startswith("[aria-label*=") and "[role='button']" not in s and "[role='combobox']" not in s:
            score -= 1
        # Plan/profile-provided selectors are often the most semantically stable
        # openers for the current page state and should survive bounded truncation.
        if s in provided_set:
            score += 5
        return (score, -len(s))

    ranked_dynamic = sorted(dynamic_candidates, key=_score, reverse=True)
    ranked_structural = sorted(structural_candidates, key=_score, reverse=True)
    ranked_all = sorted(candidates, key=_score, reverse=True)

    # Preserve heterogeneous coverage under the bounded cap:
    # date-valued chips are powerful when present, but they can crowd out the
    # generic/structural opener selectors (combobox/input/button role labels) that
    # remain valid across UI variants and are often the only visible controls.
    cap = max(1, int(max_items))
    out: list[str] = []
    seen: set[str] = set()

    def _append_from(source: list[str], limit: int) -> None:
        nonlocal out, seen
        if limit <= 0:
            return
        for sel in source:
            s = str(sel or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= cap or limit <= 1:
                if len(out) >= cap:
                    return
                limit -= 1
            else:
                limit -= 1
            if limit <= 0:
                return

    # Keep top 2 dynamic date chip selectors (if any) at the front.
    dynamic_head = min(2, cap)
    _append_from(ranked_dynamic, dynamic_head)
    # Reserve a bounded portion for structural selectors so they are still attempted.
    # Seed structural diversity to avoid button-only or combobox-only heads when the
    # visible opener is a plain input (common on Google Flights variants).
    def _selector_kind(selector: str) -> str:
        lower = str(selector or "").lower()
        if "[role='button']" in lower or lower.startswith("button["):
            return "button"
        if "[role='combobox']" in lower:
            return "combobox"
        if lower.startswith("input["):
            return "input"
        return "other"

    def _locale_alignment_score(selector: str) -> int:
        s = str(selector or "")
        if not s:
            return 0
        has_cjk = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s))
        if prefer_ja:
            return 2 if has_cjk else 1
        return 2 if not has_cjk else 1

    def _seed_score(selector: str, kind: str) -> tuple[int, int, int, int]:
        s = str(selector or "")
        lower = s.lower()
        locale_align = _locale_alignment_score(s)
        has_role_token = 1 if any(tok and tok in s for tok in role_tokens) else 0
        is_provided = 1 if s in provided_set else 0
        # Prefer exact role/field selectors for seeded structural slots over generic date-chip labels.
        field_specific = 0
        if kind in {"input", "combobox"}:
            # Get field-specific tokens from config
            # Config source: service_ui_profiles.json[google_flights.date_opener_field_specific_scoring_tokens.{depart,return}]
            scoring_tokens_config = profile.get("date_opener_field_specific_scoring_tokens", {}).get(role_key, {})
            en_field_tokens = scoring_tokens_config.get("en", [])
            ja_field_tokens = scoring_tokens_config.get("ja", [])
            # Empty tokens reduce scoring precision but selector ranking will still function.

            # Check English tokens
            if any(t and t in lower for t in en_field_tokens):
                field_specific += 2
            # Check Japanese tokens
            if any(t and t in s for t in ja_field_tokens):
                field_specific += 1
        base = _score(s)[0]
        return (field_specific, locale_align, has_role_token, base + is_provided)

    structural_seeded: list[str] = []
    seed_seen: set[str] = set()
    for preferred_kind in ("button", "combobox", "input"):
        candidates_of_kind = []
        for sel in ranked_structural:
            s = str(sel or "").strip()
            if not s or s in seed_seen:
                continue
            if _selector_kind(s) != preferred_kind:
                continue
            candidates_of_kind.append(s)
        if not candidates_of_kind:
            continue
        best = max(candidates_of_kind, key=lambda s, k=preferred_kind: _seed_score(s, k))
        seed_seen.add(best)
        structural_seeded.append(best)
    for sel in ranked_structural:
        s = str(sel or "").strip()
        if not s or s in seed_seen:
            continue
        seed_seen.add(s)
        structural_seeded.append(s)

    structural_reserve = min(max(2, cap // 2), max(0, cap - len(out)))
    _append_from(structural_seeded, structural_reserve)
    # Fill the remainder by overall score.
    _append_from(ranked_all, cap - len(out))

    return out
