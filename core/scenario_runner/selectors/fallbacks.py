"""Selector fallback and hint overlay helpers."""

import re
from typing import Any, Callable, Dict, List, Optional

from core.scenario_runner.env import _current_mimic_locale
from core.scenario_runner.selectors.probes import (
    _is_clickable_selector_candidate,
    _looks_non_fillable_selector_blob,
)
from core.scenario_runner.selectors.priority import _reorder_search_selectors_for_locale
from core.scenario_runner.google_flights.core_functions import (
    _allow_bare_text_fallback,
    _profile_localized_list,
    _profile_role_list,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _dedupe_selectors,
    _selector_candidates,
    _google_role_tokens,
    _google_search_selector_hint_is_plausible,
    _maybe_append_bare_text_selectors,
    _google_selector_locale_markers,
)
from core.service_ui_profiles import get_service_ui_profile
from core.ui_tokens import build_button_text_selectors, prioritize_tokens
from utils.knowledge_rules import get_knowledge_rule_tokens, get_tokens
from utils.logging import get_logger

from utils.selector_hints import (
    get_selector_hints as _get_selector_hints_impl,
    quarantine_selector_hint,
    record_selector_hint_failure,
)

# Re-export for test monkeypatching compatibility
get_selector_hints = _get_selector_hints_impl

log = get_logger(__name__)


def _selectors_look_search_submit(selectors) -> bool:
    """Return True when selector list looks like a search/submit control."""
    blob = " ".join(selectors).lower()
    tokens = get_knowledge_rule_tokens("search_submit_tokens")
    if not tokens:
        tokens = ["search", "submit"]
    return any(token in blob for token in tokens)


def _selectors_look_domain_toggle(selectors) -> bool:
    """Return True when selector list targets domestic/international switch controls."""
    blob = " ".join(selectors).lower()
    tokens = ("domestic", "international", "国内", "海外", "国際")
    return any(token in blob for token in tokens)


def _selector_hints_overlay(
    selectors: List[str],
    *,
    site: str,
    action: str,
    role: str = "",
    display_lang: str = "",
    locale_hint: str = "",
    region: str = "",
    max_hints: int = 2,
    hint_allow: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """Prepend bounded runtime-learned selectors while preserving canonical fallback order."""
    from core import scenario_runner as _sr

    base = [str(s) for s in (selectors or []) if isinstance(s, str) and s.strip()]
    if not base:
        return base
    get_hints_fn = getattr(_sr, "get_selector_hints", None)
    if not callable(get_hints_fn):
        get_hints_fn = get_selector_hints
    hint_list = get_hints_fn(
        site=site,
        action=action,
        role=role,
        display_lang=display_lang,
        locale=locale_hint,
        region=region,
        max_selectors=max_hints,
    )
    if not hint_list:
        return base
    overlay = []
    for sel in hint_list:
        s = str(sel or "").strip()
        if not s or s in overlay:
            continue
        if callable(hint_allow):
            try:
                if not bool(hint_allow(s)):
                    try:
                        if quarantine_selector_hint(
                            site=site,
                            action=action,
                            role=role,
                            selector=s,
                            display_lang=display_lang,
                            reason="plausibility_rejected",
                        ):
                            log.info(
                                "selector_hints.quarantine site=%s action=%s role=%s selector=%s reason=plausibility_rejected",
                                site,
                                action,
                                role or "",
                                s[:120],
                            )
                        else:
                            record_selector_hint_failure(
                                site=site,
                                action=action,
                                role=role,
                                selector=s,
                                display_lang=display_lang,
                                reason="plausibility_rejected",
                            )
                    except Exception:
                        pass
                    continue
            except Exception:
                continue
        overlay.append(s)
    if not overlay:
        return base
    return _dedupe_selectors(overlay + base)


def _service_search_click_fallbacks(site_key: str, *, locale_hint_override: str = ""):
    """Return conservative search/submit click selectors per service."""
    from core.scenario_runner.google_flights.service_runner_bridge import _env_list

    vlm_search_labels = _env_list("FLIGHT_WATCHER_VLM_SEARCH_KEYWORDS")
    locale = str(locale_hint_override or _current_mimic_locale() or "").lower()
    vlm_search_labels = prioritize_tokens(vlm_search_labels, locale_hint=locale)
    vlm_selectors = build_button_text_selectors(vlm_search_labels)
    prefer_ja = locale.startswith("ja")
    profile = get_service_ui_profile(site_key)

    label_cfg = profile.get("search_labels", {})
    labels = _profile_localized_list(label_cfg, prefer_ja=prefer_ja)
    labels.extend(get_tokens("actions", "search"))
    labels = prioritize_tokens(labels, locale_hint=locale)

    selectors = build_button_text_selectors(labels)
    selectors = _dedupe_selectors(
        [
            "button[type='submit']",
            "input[type='submit']",
        ]
        + selectors
    )
    base_selectors = profile.get("search_selectors", [])
    if isinstance(base_selectors, list):
        selectors.extend([s for s in base_selectors if isinstance(s, str) and s.strip()])
    merged = _dedupe_selectors(vlm_selectors + selectors)
    clickable = [selector for selector in merged if _is_clickable_selector_candidate(selector)]
    if str(site_key or "").strip().lower() == "google_flights":
        clickable = _reorder_search_selectors_for_locale(clickable, locale_hint=locale)
        clickable = _selector_hints_overlay(
            clickable,
            site="google_flights",
            action="quick_rebind_search",
            display_lang=locale,
            locale_hint=locale,
            max_hints=2,
            hint_allow=_google_search_selector_hint_is_plausible,
        )
    if clickable:
        return clickable
    return merged


def _service_fill_fallbacks(site_key: str, role: str):
    """Return robust fill-selector fallbacks for one field role."""
    locale = _current_mimic_locale().lower()
    prefer_ja = locale.startswith("ja")
    role_key = str(role or "").strip().lower()

    def _google_common_fill_fallbacks(role_name: str) -> list:
        label_tokens = prioritize_tokens(
            _google_role_tokens(role_name, "selector_ja") + _google_role_tokens(role_name, "selector_en"),
            locale_hint=locale,
        )
        selectors = []
        for token in label_tokens:
            label = str(token or "").strip()
            if not label:
                continue
            selectors.extend(
                [
                    f"[role='combobox'][aria-label*='{label}']",
                    f"[role='button'][aria-label*='{label}']",
                    f"input[aria-label*='{label}']",
                    f"input[placeholder*='{label}']",
                    f"[aria-label*='{label}']",
                ]
            )
        if role_name == "origin":
            selectors.extend(["input[name*='origin']", "input[name*='from']", "[data-testid*='origin']", "[data-testid*='from']"])
        elif role_name == "dest":
            selectors.extend(
                [
                    "input[name*='destination']",
                    "input[name*='to']",
                    "[data-testid*='destination']",
                    "[data-testid*='to']",
                ]
            )
        elif role_name == "depart":
            selectors.extend(["input[name*='depart']", "[data-testid*='depart']"])
        elif role_name == "return":
            selectors.extend(["input[name*='return']", "[data-testid*='return']"])
            # Common lowercase variant appears on some sites.
            selectors.append("input[aria-label*='return']")
        return _dedupe_selectors(selectors)

    common = {
        "origin": [
            "[role='combobox'][aria-label*='出発地']",
            "[role='combobox'][aria-label*='出発']",
            "[role='button'][aria-label*='出発地']",
            "[role='button'][aria-label*='出発']",
            "[role='combobox'][aria-label*='From']",
            "[role='combobox'][aria-label*='Where from']",
            "input[aria-label*='Where from']",
            "input[aria-label*='From']",
            "input[placeholder*='Where from']",
            "input[placeholder*='From']",
            "input[placeholder*='出発地']",
            "input[placeholder*='出発']",
            "input[aria-label*='出発地']",
            "input[aria-label*='出発']",
            "input[name*='origin']",
            "input[name*='from']",
            "[aria-label*='出発地']",
            "[aria-label*='From']",
            "[aria-label*='出発']",
            "[data-testid*='origin']",
            "[data-testid*='from']",
        ],
        "dest": [
            "[role='combobox'][aria-label*='目的地']",
            "[role='combobox'][aria-label*='到着地']",
            "[role='combobox'][aria-label*='到着']",
            "[role='button'][aria-label*='目的地']",
            "[role='button'][aria-label*='到着地']",
            "[role='button'][aria-label*='到着']",
            "[role='combobox'][aria-label*='To']",
            "[role='combobox'][aria-label*='Where to']",
            "input[aria-label*='Where to']",
            "input[aria-label*='To']",
            "input[placeholder*='Where to']",
            "input[placeholder*='To']",
            "input[placeholder*='目的地']",
            "input[aria-label*='目的地']",
            "input[placeholder*='到着地']",
            "input[aria-label*='到着地']",
            "input[placeholder*='到着']",
            "input[aria-label*='到着']",
            "input[name*='destination']",
            "input[name*='to']",
            "[aria-label*='To']",
            "[aria-label*='目的地']",
            "[aria-label*='到着地']",
            "[aria-label*='到着']",
            "[data-testid*='destination']",
            "[data-testid*='to']",
        ],
        "depart": [
            "[role='combobox'][aria-label*='Departure']",
            "[role='combobox'][aria-label*='Depart']",
            "[role='combobox'][aria-label*='出発']",
            "[role='button'][aria-label*='Departure']",
            "[role='button'][aria-label*='出発']",
            "input[aria-label*='Departure']",
            "input[placeholder*='Departure']",
            "input[aria-label*='Depart']",
            "input[name*='depart']",
            "input[placeholder*='出発日']",
            "input[aria-label*='出発日']",
            "[aria-label*='Departure']",
            "[aria-label*='出発日']",
            "[data-testid*='depart']",
        ],
        "return": [
            "[role='combobox'][aria-label*='Return']",
            "[role='combobox'][aria-label*='復路']",
            "[role='button'][aria-label*='Return']",
            "[role='button'][aria-label*='復路']",
            "input[aria-label*='Return']",
            "input[placeholder*='Return']",
            "input[aria-label*='return']",
            "input[name*='return']",
            "input[placeholder*='復路']",
            "input[aria-label*='復路']",
            "[aria-label*='Return']",
            "[aria-label*='復路']",
            "[data-testid*='return']",
        ],
    }
    if (site_key or "").strip().lower() == "google_flights" and role_key in {"origin", "dest", "depart", "return"}:
        common = {**common, role_key: _google_common_fill_fallbacks(role_key)}
    google_ja_markers, google_en_markers = _google_selector_locale_markers()

    def _selector_locale_bucket(selector: str) -> str:
        lowered = selector.lower()
        ja_hit = any(token in selector for token in google_ja_markers)
        en_hit = any(token in lowered for token in google_en_markers)
        if ja_hit and not en_hit:
            return "ja"
        if en_hit and not ja_hit:
            return "en"
        if ja_hit and en_hit:
            return "mixed"
        return "neutral"

    def _interleave_selector_locales(values: list) -> list:
        if not isinstance(values, list) or len(values) < 3:
            return values
        buckets = {"ja": [], "en": [], "mixed": [], "neutral": []}
        for item in values:
            buckets[_selector_locale_bucket(item)].append(item)

        primary_key = "ja" if prefer_ja else "en"
        secondary_key = "en" if prefer_ja else "ja"
        primary = buckets[primary_key]
        secondary = buckets[secondary_key]
        mixed = buckets["mixed"]
        neutral = buckets["neutral"]

        # Keep locale preference first, but always keep cross-locale backups nearby.
        out = []
        while primary or secondary:
            if primary:
                out.append(primary.pop(0))
            if secondary:
                out.append(secondary.pop(0))
        out.extend(mixed)
        out.extend(neutral)
        return out

    profile = get_service_ui_profile(site_key)
    extras = _profile_role_list(profile, "fill_selectors", role, prefer_ja=prefer_ja)
    prepend = bool(profile.get("fill_extras_prepend", True))
    selectors = (
        extras + common.get(role, [])
        if prepend
        else common.get(role, []) + extras
    )
    selectors = _dedupe_selectors(selectors)

    if bool(profile.get("fill_locale_sort", False)):
        def _selector_rank(selector: str) -> tuple:
            lowered = selector.lower()
            ja_hit = any(token in selector for token in google_ja_markers)
            en_hit = any(token in lowered for token in google_en_markers)
            combobox_or_button = ("[role='combobox']" in lowered) or ("[role='button']" in lowered)
            inputish = "input[" in lowered
            if prefer_ja:
                return (
                    int(ja_hit),
                    int(combobox_or_button),
                    int(inputish),
                    int(en_hit),
                )
            return (
                int(en_hit),
                int(combobox_or_button),
                int(inputish),
                int(ja_hit),
            )

        selectors.sort(key=_selector_rank, reverse=True)
        if role in {"origin", "dest", "depart", "return"}:
            selectors = _interleave_selector_locales(selectors)
    return selectors


def _service_wait_fallbacks(site_key: str):
    """Return conservative wait selectors to prevent brittle single-point waits."""
    # Import _PLUGIN_SCENARIO_HINTS from parent module to avoid circular import
    import core.scenario_runner as sr_module

    profile = get_service_ui_profile(site_key)
    generic = profile.get("wait_selectors", ["[role='main']", "main", "body"])
    if not isinstance(generic, list):
        generic = ["[role='main']", "main", "body"]
    site = (site_key or "").strip().lower()
    hint_wait = []
    if site:
        _PLUGIN_SCENARIO_HINTS = getattr(sr_module, '_PLUGIN_SCENARIO_HINTS', {})
        raw_hints = _PLUGIN_SCENARIO_HINTS.get(site, {})
        if isinstance(raw_hints, dict):
            value = raw_hints.get("wait_selectors", [])
            if isinstance(value, list):
                hint_wait = [v for v in value if isinstance(v, str) and v.strip()]
    merged = _dedupe_selectors(list(generic) + hint_wait)
    return [s for s in merged if isinstance(s, str) and s.strip()]
