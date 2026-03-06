"""Extraction strategies: cached selector path and LLM fallback path."""

import hashlib
import os
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import parse_qs, urlparse
from bs4 import BeautifulSoup

from core.services import is_supported_service
from core.scope_reconciliation import evaluate_irrelevant_page_downgrade
from core.plugins.registry import (
    get_provider as get_provider_plugin,
    get_service as get_service_plugin,
    get_strategy as get_strategy_plugin,
)
from core.plugins.adapters.services_adapter import plugin_strategy_enabled
from core.plugins.runtime_extraction import run_plugin_extraction_router
from core.plugins.services.config_loader import load_service_plugin_config
from core.plugins.services.skyscanner import extract_price_from_html as extract_skyscanner_price_from_html
from core.ui_tokens import normalize_visible_text
from core.extr_helpers_config import (
    _env_bool,
    _env_int,
    _normalize_page_class,
)
from core.extr_helpers_vision import (
    _vision_screenshot_fingerprint,
    _vision_cached_stage_call,
    _normalize_vision_extract_assist_result,
)
from core.extr_helpers_google_shared import (
    _PRICE_PATTERN,
    _SYMBOL_TO_CURRENCY,
    HEURISTIC_MIN_PRICE,
    HEURISTIC_MAX_PRICE,
    _load_token_group,
    _compile_hint_token_patterns,
    _hint_token_count,
    _hint_token_any,
    _RESULT_HINT_TOKENS,
    _AUTH_HINT_TOKENS,
    _ROUTE_HINT_TOKENS,
    _RESULT_HINT_MATCHERS,
    _AUTH_HINT_MATCHERS,
    _ROUTE_HINT_MATCHERS,
    _extract_price_candidates,
    _contains_route_token,
)
from core.extr_helpers_google import (
    _is_google_flights_site,
    _google_visible_text,
    _google_visible_price_values,
    _google_price_is_grounded_in_html,
    _google_route_aliases,
    _parse_google_deeplink_context,
    _google_deeplink_context_matches,
    _extract_google_embedded_price,
    _google_page_context_matches,
    _google_non_flight_scope_detected,
)
from utils.price import extract_number
from utils.thresholds import get_threshold
from utils.knowledge_rules import get_knowledge_rule_tokens, get_tokens
from llm.code_model import (
    parse_html_with_llm,
    assess_html_quality_with_llm,
    parse_image_with_vlm,
    parse_page_multimodal_with_vlm,
    analyze_page_ui_with_vlm,
    analyze_filled_route_with_vlm,
    assess_trip_product_scope_with_llm,
    assess_vlm_price_candidate_with_llm,
)
from llm.attempt_policy import LLMCallBudget, load_llm_budget_from_config
from llm.selector_quality import classify_selector_stability
from llm.language_signals import detect_ui_language
from core.route_binding import (
    dom_route_bind_probe,
    fuse_route_bind_verdict,
    vlm_route_bind_probe,
)
from storage.shared_knowledge_store import (
    get_airport_aliases,
    get_airport_aliases_for_provider,
    map_airport_code_for_provider,
)
from utils.logging import get_logger

# Phase 5: Coordination bridge and monitoring (optional)
from core.scenario.coordination_integration import (
    ExtractionCoordinationBridge,
    evaluate_extraction_gates,
)
from core.scenario.coordination_monitoring import ExtractionObserver


log = get_logger(__name__)


def _load_token_group(
    *,
    group: str,
    key: str,
    legacy_key: Optional[str] = None,
    fallback: Optional[List[str]] = None,
) -> List[str]:
    """Load one token list from grouped rules with legacy+default fallback.

    Note: keep this extractor-local so reload-time monkeypatching of
    `utils.knowledge_rules` is reflected in tests and runtime.
    """
    tokens = get_tokens(group, key)
    if not tokens and legacy_key:
        tokens = get_knowledge_rule_tokens(legacy_key)
    if not tokens:
        tokens = list(fallback or [])
    out: List[str] = []
    seen = set()
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        marker = normalize_visible_text(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


_RESULT_HINT_TOKENS = _load_token_group(
    group="hints",
    key="results",
    fallback=[
        "flight",
        "flights",
        "itinerary",
        "result",
        "results",
        "search result",
        "運賃",
        "料金",
        "便",
        "検索結果",
        "最安",
        "往路",
        "復路",
    ],
)
_AUTH_HINT_TOKENS = _load_token_group(
    group="hints",
    key="auth",
    fallback=[
        "email",
        "password",
        "login",
        "register",
        "account",
        "member",
        "メール",
        "会員",
        "ログイン",
        "パスワード",
        "氏名",
        "お名前",
        "電話",
    ],
)
_ROUTE_HINT_TOKENS = _load_token_group(
    group="hints",
    key="route_fields",
    fallback=[
        "from",
        "to",
        "where from",
        "where to",
        "origin",
        "destination",
        "depart",
        "departure",
        "return",
        "出発地",
        "目的地",
        "到着地",
        "出発",
        "復路",
        "帰り",
    ],
)
_RESULT_HINT_MATCHERS = _compile_hint_token_patterns(_RESULT_HINT_TOKENS)
_AUTH_HINT_MATCHERS = _compile_hint_token_patterns(_AUTH_HINT_TOKENS)
_ROUTE_HINT_MATCHERS = _compile_hint_token_patterns(_ROUTE_HINT_TOKENS)


def _price_grounding_tolerance(target: float) -> float:
    """Compute grounding tolerance via extractor-local threshold lookups.

    Compatibility note:
    tests monkeypatch `core.extractor.get_threshold`, so this wrapper must use
    the local symbol rather than the helper module's imported threshold lookup.
    """
    try:
        target_val = abs(float(target))
    except Exception:
        target_val = 0.0
    try:
        tolerance_ratio = float(get_threshold("extract_vlm_price_grounding_tolerance_ratio", 0.03))
    except Exception:
        tolerance_ratio = 0.03
    try:
        tolerance_abs = float(get_threshold("extract_vlm_price_grounding_tolerance_abs", 2500))
    except Exception:
        tolerance_abs = 2500.0
    computed = max(tolerance_abs, target_val * tolerance_ratio)
    return float(computed)


_PACKAGE_PAGE_TOKENS = [
    t.lower()
    for t in _load_token_group(
        group="page",
        key="package",
        legacy_key="page_package_tokens",
        fallback=[
            "ダイナミックパッケージ",
            "航空券+ホテル",
            "航空券＋ホテル",
            "flight + hotel",
            "flight+hotel",
            "air + hotel",
            "air+hotel",
        ],
    )
]
_PACKAGE_URL_TOKENS = [t.lower() for t in get_knowledge_rule_tokens("url_package_tokens")]
_HOTEL_TOKENS = tuple(
    token.lower()
    for token in _load_token_group(
        group="page",
        key="hotel",
        fallback=["hotel", "hotels", "ホテル", "宿"],
    )
)
_FLIGHT_TOKENS = tuple(
    token.lower()
    for token in _load_token_group(
        group="page",
        key="flight",
        fallback=["flight", "flights", "air", "航空券", "飛行機"],
    )
)
_BUNDLE_WORD_TOKENS = tuple(
    token.lower()
    for token in _load_token_group(
        group="google",
        key="bundle_word",
        fallback=["package", "パッケージ"],
    )
)



def _clamp01(value: float) -> float:
    """Clamp value to [0.0, 1.0] for confidence score normalization."""
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _confidence_label_score(label: str) -> float:
    """Map legacy confidence labels to numeric scores."""
    normalized = str(label or "").strip().lower()
    if normalized == "high":
        return 0.8
    if normalized == "medium":
        return 0.6
    return 0.35


def _source_base_confidence_score(source: str, *, llm_mode: str) -> float:
    """Map extraction source to deterministic baseline score."""
    src = str(source or "").strip().lower()
    if src == "heuristic_embedded":
        return 0.65
    if src == "cached_selector":
        return 0.85
    if src == "heuristic_html":
        return 0.45
    if src == "heuristic_chunk":
        return 0.50
    if src.startswith("llm"):
        return 0.55 if llm_mode == "light" else 0.60
    if src in {"vlm", "vlm_multimodal"}:
        return 0.55
    if src in {"vlm_scope_guard", "heuristic_guard"}:
        return 0.05
    return 0.4


def _compute_confidence_score(payload: Dict[str, Any], *, llm_mode: str) -> float:
    """Compute normalized confidence score [0,1] from source/result metadata."""
    source = str(payload.get("source", "") or "")
    base = _source_base_confidence_score(source, llm_mode=llm_mode)
    reason = str(payload.get("reason", "") or "").strip().lower()
    price = payload.get("price")
    if price is None:
        if source == "vlm_scope_guard" or "scope" in reason:
            return 0.0
        if "garbage" in reason:
            return 0.05
        if reason.startswith("llm_request_failed_") or "request_failed" in reason:
            return 0.1
        return _clamp01(min(base, 0.2))
    score = (base + _confidence_label_score(str(payload.get("confidence", "low")))) / 2.0
    if source in {"vlm", "vlm_multimodal"} and payload.get("price_grounded_in_html") is False:
        score = min(score, 0.35)
    return _clamp01(score)


def _confidence_rank(label: str) -> int:
    """Map confidence label to comparable rank."""
    text = str(label or "").strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    return 1


def _confidence_from_rank(rank: int) -> str:
    """Map confidence rank back to label."""
    if rank >= 3:
        return "high"
    if rank == 2:
        return "medium"
    return "low"


def _extract_wall_clock_cap_payload() -> Dict[str, Any]:
    """Conservative fail-closed payload for extraction watchdog aborts."""
    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "source": "watchdog",
        "reason": "extract_wall_clock_cap",
        "route_bound": False,
    }


def _selector_hint_with_stability(selector_hint: Any) -> Optional[Dict[str, Any]]:
    """Normalize selector_hint payload and attach additive stability annotation."""
    if not isinstance(selector_hint, dict):
        return None
    out = dict(selector_hint)
    if not bool(get_threshold("extract_selector_stability_normalize_enabled", True)):
        return out
    css = out.get("css")
    if isinstance(css, str) and css.strip():
        out["stability"] = classify_selector_stability(css)
    return out


def _has_strong_route_scope_evidence(payload: Dict[str, Any]) -> bool:
    """Return True when route/scope evidence is strong enough to bypass brittle penalty."""
    source = str(payload.get("source", "") or "").strip().lower()
    route_bind_support = str(payload.get("route_bind_support", "") or "").strip().lower()
    if source == "cached_selector" and payload.get("route_bound") is True and route_bind_support == "strong":
        return True
    if payload.get("route_bound") is not True:
        return False
    if _normalize_page_class(str(payload.get("page_class", "") or "")) != "flight_only":
        return False
    scope_guard = str(payload.get("scope_guard", "") or "").strip().lower()
    if scope_guard and scope_guard not in {"pass", "conflict_resolved"}:
        return False
    return True


def _downgrade_confidence_one_level(
    label: str,
    *,
    min_label: str = "low",
) -> str:
    """Downgrade confidence by one level while respecting minimum floor."""
    rank = _confidence_rank(label)
    floor = _confidence_rank(min_label)
    return _confidence_from_rank(max(floor, rank - 1))


def _normalize_extractor_output(
    payload: Dict[str, Any],
    *,
    llm_mode: str,
    default_scope_guard: str = "skip",
    default_scope_guard_basis: str = "deterministic",
) -> Dict[str, Any]:
    """Add additive normalized fields while preserving existing payload semantics."""
    out = dict(payload or {})
    out.setdefault("price", None)
    out.setdefault("currency", None)
    out.setdefault("selector_hint", None)
    out["source"] = str(out.get("source", "unknown") or "unknown")
    out["reason"] = str(out.get("reason", "") or "")
    confidence = str(out.get("confidence", "low") or "low").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    out["confidence"] = confidence
    normalized_selector_hint = _selector_hint_with_stability(out.get("selector_hint"))
    out["selector_hint"] = normalized_selector_hint
    if (
        bool(get_threshold("extract_confidence_downgrade_on_brittle_selector", True))
        and isinstance(normalized_selector_hint, dict)
        and str(normalized_selector_hint.get("stability", "")).strip().lower() == "brittle"
        and not _has_strong_route_scope_evidence(out)
    ):
        min_confidence = str(
            get_threshold("extract_confidence_downgrade_min", "low")
        ).strip().lower() or "low"
        out["confidence"] = _downgrade_confidence_one_level(
            out.get("confidence", "low"),
            min_label=min_confidence,
        )
    scope_guard = str(out.get("scope_guard", default_scope_guard) or default_scope_guard).strip().lower()
    if scope_guard not in {"pass", "fail", "skip", "conflict_resolved"}:
        scope_guard = default_scope_guard
    out["scope_guard"] = scope_guard
    scope_basis = str(out.get("scope_guard_basis", default_scope_guard_basis) or default_scope_guard_basis).strip().lower()
    if scope_basis not in {"deterministic", "vlm", "llm", "mixed"}:
        scope_basis = default_scope_guard_basis
    out["scope_guard_basis"] = scope_basis
    confidence_factors = out.get("confidence_factors")
    if not isinstance(confidence_factors, list):
        factors: List[str] = []
        source = str(out.get("source", "") or "").strip().lower()
        reason = str(out.get("reason", "") or "").strip().lower()
        if source:
            factors.append(f"base:{source}")
        if out.get("scope_guard") == "fail":
            factors.append("scope:fail")
        if source in {"vlm", "vlm_multimodal"} and out.get("price_grounded_in_html") is False:
            factors.append("cap:vlm_price_not_grounded")
        if reason.startswith("llm_request_failed_"):
            factors.append(f"reason:{reason}")
        if reason == "google_route_context_unbound":
            factors.append("route:unbound")
        if (
            isinstance(normalized_selector_hint, dict)
            and str(normalized_selector_hint.get("stability", "")).strip().lower() == "brittle"
        ):
            if _has_strong_route_scope_evidence(out):
                factors.append("selector:brittle_bypass_strong_route_scope")
            else:
                factors.append("selector:brittle_confidence_downgraded")
        out["confidence_factors"] = factors
    out["confidence_score"] = _compute_confidence_score(out, llm_mode=llm_mode)
    return out


def _compute_google_route_bind_verdict(
    *,
    html: str,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    screenshot_path: Optional[str],
    trip_type: Optional[str] = None,
    use_vlm_verify: bool = True,
    vlm_timeout_sec: int = 180,
    require_strong: bool = True,
    fail_closed_on_mismatch: bool = True,
    budget: Optional[LLMCallBudget] = None,
) -> Dict[str, Any]:
    """Compute fused route-binding verdict (DOM + optional VLM verification).

    Args:
        budget: Optional LLMCallBudget for gated retry management.
    """
    dom_probe = dom_route_bind_probe(
        html,
        origin=str(origin or ""),
        dest=str(dest or ""),
        depart=str(depart or ""),
        return_date=str(return_date or ""),
    )
    vlm_probe = {
        "route_bound": False,
        "support": "none",
        "source": "unknown",
        "reason": "vlm_skipped",
        "observed": {"origin": None, "dest": None, "depart": None, "return": None},
        "mismatch_fields": [],
    }
    if (
        use_vlm_verify
        and str(dom_probe.get("support", "none")).strip().lower() != "strong"
        and isinstance(screenshot_path, str)
        and screenshot_path.strip()
    ):
        try:
            verify = analyze_filled_route_with_vlm(
                screenshot_path.strip(),
                site="google_flights",
                origin=str(origin or ""),
                dest=str(dest or ""),
                depart=str(depart or ""),
                return_date=str(return_date or ""),
                trip_type=str(trip_type or ("round_trip" if return_date else "one_way")),
                html_context=html,
                locale="",
                timeout_sec=max(10, int(vlm_timeout_sec)),
                budget=budget,
            )
        except Exception:
            verify = {}
        vlm_probe = vlm_route_bind_probe(
            verify,
            origin=str(origin or ""),
            dest=str(dest or ""),
            depart=str(depart or ""),
            return_date=str(return_date or ""),
        )
    verdict = fuse_route_bind_verdict(
        dom_probe=dom_probe,
        vlm_probe=vlm_probe,
        require_strong=bool(require_strong),
        fail_closed_on_mismatch=bool(fail_closed_on_mismatch),
    )
    verdict["dom_probe"] = dom_probe
    verdict["vlm_probe"] = vlm_probe
    return verdict


def _route_not_bound_payload(
    base: Dict[str, Any],
    *,
    verdict: Dict[str, Any],
) -> Dict[str, Any]:
    """Build fail-closed payload when route binding is insufficient."""
    out = dict(base or {})
    out["price"] = None
    out["currency"] = None
    out["confidence"] = "low"
    out["reason"] = "route_not_bound"
    out["route_bound"] = False
    out["route_bind_support"] = str(verdict.get("support", "none") or "none")
    out["route_bind_source"] = str(verdict.get("source", "unknown") or "unknown")
    out["route_bind_reason"] = str(verdict.get("reason", "route_not_bound") or "route_not_bound")
    observed = verdict.get("observed")
    if isinstance(observed, dict):
        out["route_bind_observed"] = observed
    return out


def _route_bind_fields_from_verdict(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """Project additive route-bind debug fields from one fused verdict."""
    if not isinstance(verdict, dict):
        return {}
    support = str(verdict.get("support", "none") or "none").strip().lower()
    if support not in {"strong", "weak", "none"}:
        support = "none"
    out: Dict[str, Any] = {
        "route_bound": bool(verdict.get("route_bound")),
        "route_bind_support": support,
        "route_bind_source": str(verdict.get("source", "unknown") or "unknown"),
        "route_bind_reason": str(verdict.get("reason", "unknown") or "unknown"),
    }
    observed = verdict.get("observed")
    if isinstance(observed, dict):
        out["route_bind_observed"] = observed
    return out


def _apply_google_route_bind_gate(
    candidate: Dict[str, Any],
    *,
    html: str,
    site: str,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    screenshot_path: Optional[str],
    verdict_getter=None,
) -> Dict[str, Any]:
    """Apply feature-flagged Google Flights route-binding acceptance gate."""
    out = dict(candidate or {})
    if not _is_google_flights_site(site):
        return out
    if not bool(get_threshold("scenario_route_bind_gate_enabled", True)):
        return out
    if out.get("price") is None:
        return out
    if not (origin and dest and depart):
        return out

    confidence = str(out.get("confidence", "low") or "low").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    requires_strong = bool(get_threshold("scenario_route_bind_gate_requires_strong", True))
    fail_closed_mismatch = bool(
        get_threshold("scenario_route_bind_fail_closed_on_mismatch", True)
    )

    verdict = {}
    if callable(verdict_getter):
        try:
            verdict = verdict_getter()
        except Exception:
            verdict = {}
    if not isinstance(verdict, dict) or not verdict:
        verdict = _compute_google_route_bind_verdict(
            html=html,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            screenshot_path=screenshot_path,
            require_strong=requires_strong,
            fail_closed_on_mismatch=fail_closed_mismatch,
        )
    out.update(_route_bind_fields_from_verdict(verdict))
    support = str(out.get("route_bind_support", "none") or "none").strip().lower()
    route_bound = bool(out.get("route_bound"))
    reason = str(verdict.get("reason", "") or "")
    if requires_strong:
        accepted_bound = route_bound and support == "strong"
    else:
        accepted_bound = route_bound or support in {"strong", "weak"}

    if fail_closed_mismatch and reason == "explicit_mismatch":
        return _route_not_bound_payload(out, verdict=verdict)

    if confidence != "low" and not accepted_bound:
        return _route_not_bound_payload(out, verdict=verdict)

    if support == "weak":
        out["confidence"] = "low"
    return out


def _first_route_token_index(raw_blob: str, upper_blob: str, token: str) -> Optional[int]:
    """Return first token index using the same token-matching policy."""
    if not token:
        return None
    if token.isascii():
        needle = token.upper()
        match = re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", upper_blob)
        if match:
            return match.start()
        if len(needle) >= 5:
            idx = upper_blob.find(needle)
            return idx if idx >= 0 else None
        return None
    idx = raw_blob.find(token)
    return idx if idx >= 0 else None


def _normalize_space(text: str) -> str:
    """Collapse repeated whitespace into one space."""
    return re.sub(r"\s+", " ", text or "").strip()


def _route_alias_match_count(
    html: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
) -> int:
    """Count how many requested route endpoints are visible in one blob."""
    raw_blob = html or ""
    upper_blob = raw_blob.upper()
    matched = 0

    origin_aliases = get_airport_aliases(origin or "")
    if origin_aliases and any(
        _contains_route_token(raw_blob, upper_blob, token) for token in origin_aliases
    ):
        matched += 1

    dest_aliases = get_airport_aliases(dest or "")
    if dest_aliases and any(
        _contains_route_token(raw_blob, upper_blob, token) for token in dest_aliases
    ):
        matched += 1

    return matched


def _html_quality_signals(
    html: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
) -> Dict[str, int]:
    """Compute lightweight quality signals for one raw HTML snapshot."""
    blob = html or ""
    return {
        "length": len(blob),
        "price_hits": len(_PRICE_PATTERN.findall(blob)),
        "result_hits": _hint_token_count(blob, _RESULT_HINT_MATCHERS),
        "auth_hits": _hint_token_count(blob, _AUTH_HINT_MATCHERS),
        "route_hint_hits": _hint_token_count(blob, _ROUTE_HINT_MATCHERS),
        "route_alias_hits": _route_alias_match_count(blob, origin=origin, dest=dest),
        "date_hits": int(bool(depart and depart in blob))
        + int(bool(return_date and return_date in blob)),
    }


def _determine_html_quality(
    html: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify HTML quality into good/uncertain/garbage using deterministic signals."""
    signals = _html_quality_signals(
        html,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )
    if signals["length"] < 120 and signals["price_hits"] == 0:
        return {"quality": "garbage", "reason": "html_too_short", "signals": signals}
    if (
        signals["price_hits"] == 0
        and signals["result_hits"] == 0
        and signals["route_alias_hits"] == 0
        and signals["auth_hits"] >= 2
    ):
        return {"quality": "garbage", "reason": "auth_or_interstitial", "signals": signals}
    if (
        signals["price_hits"] > 0
        and (signals["route_alias_hits"] > 0 or signals["result_hits"] > 0)
    ):
        return {"quality": "good", "reason": "price_with_context", "signals": signals}
    if (
        signals["route_alias_hits"] == 2
        and signals["date_hits"] >= 1
        and signals["result_hits"] > 0
    ):
        return {"quality": "good", "reason": "route_date_context", "signals": signals}
    return {"quality": "uncertain", "reason": "insufficient_context", "signals": signals}


def _semantic_html_chunks(
    html: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build ranked semantic chunks for route-aware extraction on very long pages."""
    min_html_chars = int(get_threshold("extract_semantic_chunk_min_html_chars", 120000))
    if not isinstance(html, str) or len(html) < max(5000, min_html_chars):
        return []

    max_chunks = int(get_threshold("extract_semantic_chunk_max_chunks", 8))
    max_chunk_chars = int(get_threshold("extract_semantic_chunk_chars", 8000))
    max_nodes = int(get_threshold("extract_semantic_chunk_max_nodes", 280))

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()
    root = soup.body or soup

    chunks: List[Dict[str, Any]] = []
    seen = set()
    nodes_added = 0

    def _push(node) -> None:
        nonlocal nodes_added
        if node is None:
            return
        anchor = node
        for _ in range(2):
            parent = getattr(anchor, "parent", None)
            if not parent or getattr(parent, "name", None) in ("body", "html", "[document]"):
                break
            anchor = parent
        block_html = str(anchor)
        block_text = _normalize_space(" ".join(anchor.stripped_strings))
        if len(block_text) < 70:
            return
        if len(block_text) > max_chunk_chars:
            block_text = block_text[:max_chunk_chars]
        if len(block_html) > (max_chunk_chars * 2):
            block_html = block_html[: max_chunk_chars * 2]
        signature = block_text[:2200]
        if signature in seen:
            return
        seen.add(signature)

        route_score = _route_match_score(
            block_text,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            provider="google_flights",
        )
        strict_score = _route_strict_anchor_score(
            block_text,
            origin=origin,
            dest=dest,
            provider="google_flights",
        )
        price_hits = len(list(_extract_price_candidates(block_text)))
        date_hits = int(bool(depart and depart in block_text)) + int(
            bool(return_date and return_date in block_text)
        )
        signal_score = (route_score * 6) + (strict_score * 8) + (min(price_hits, 3) * 3) + date_hits
        if signal_score <= 0 and price_hits == 0:
            return

        chunks.append(
            {
                "html": block_html,
                "text": block_text,
                "score": signal_score,
                "price_hits": price_hits,
            }
        )
        nodes_added += 1

    for el in root.select("[aria-label]"):
        aria = _normalize_space(el.get("aria-label") or "")
        if not aria:
            continue
        if _PRICE_PATTERN.search(aria) or _hint_token_any(aria, _ROUTE_HINT_MATCHERS):
            _push(el)
        if nodes_added >= max_nodes:
            break

    if nodes_added < max_nodes:
        for text_node in root.find_all(string=True):
            raw = _normalize_space(str(text_node))
            if not raw:
                continue
            if (
                _PRICE_PATTERN.search(raw)
                or _hint_token_any(raw, _ROUTE_HINT_MATCHERS)
                or (depart and depart in raw)
                or (return_date and return_date in raw)
            ):
                _push(getattr(text_node, "parent", None))
            if nodes_added >= max_nodes:
                break

    chunks.sort(key=lambda item: (item.get("score", 0), item.get("price_hits", 0)), reverse=True)
    return chunks[: max(1, max_chunks)]


def _extract_with_semantic_chunks(
    html: str,
    site: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    page_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run heuristic extraction on ranked semantic chunks and return the best hit."""
    chunks = _semantic_html_chunks(
        html,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )
    if not chunks:
        return None

    best = None
    best_score = -1
    for chunk in chunks:
        chunk_text = str(chunk.get("text", "") or chunk.get("html", ""))
        extracted = _extract_with_heuristics_from_text(
            text=chunk_text,
            site=site,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            page_url=page_url,
        )
        if not extracted and isinstance(chunk.get("html"), str):
            extracted = _extract_with_heuristics(
                html=chunk.get("html", ""),
                site=site,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                page_url=page_url,
            )
        if not extracted:
            continue
        score = int(chunk.get("score", 0))
        if best is None or score > best_score:
            best = dict(extracted)
            best_score = score
        elif score == best_score and best and extracted.get("price") is not None:
            if best.get("price") is None or float(extracted["price"]) < float(best["price"]):
                best = dict(extracted)

    if best:
        best["source"] = "heuristic_chunk"
        best["reason"] = "semantic_chunk_route_match"
    return best


def _extract_with_heuristics_from_text(
    text: str,
    site: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    page_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort deterministic extraction from precomputed visible text."""
    if not is_supported_service(site):
        return None
    blob = _normalize_space(str(text or ""))
    if not blob:
        return None
    if looks_package_bundle_page(html=blob, site=site, url=page_url or ""):
        return None

    google_site = _is_google_flights_site(site)
    best = None
    best_score = _route_match_score(
        blob,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        provider="google_flights" if google_site else "",
    )
    best_strict_score = _route_strict_anchor_score(
        blob,
        origin=origin,
        dest=dest,
        provider="google_flights" if google_site else "",
    )
    best_has_depart = bool(depart and depart in blob)
    for price, currency in _extract_price_candidates(blob):
        if HEURISTIC_MIN_PRICE <= price <= HEURISTIC_MAX_PRICE:
            if best is None or price < best[0]:
                best = (price, currency)

    if best is None:
        return None
    if origin and dest and best_score <= 0:
        return None
    if google_site and origin and dest and best_strict_score <= 0:
        return None
    if depart and not best_has_depart:
        if not (
            google_site
            and best_strict_score > 0
            and _google_page_context_matches(
                blob,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                page_url=page_url,
            )
        ):
            return None

    return {
        "price": best[0],
        "currency": best[1],
        "confidence": "low",
        "selector_hint": None,
        "source": "heuristic_html",
        "reason": "heuristic_min_price",
    }


def looks_package_bundle_page(html: str, site: str = "", url: str = "") -> bool:
    """Best-effort detector for bundled hotel+flight pages (not air-only fares)."""
    blob = (html or "").lower()
    url_blob = (url or "").lower()
    has_hotel = any(token in blob for token in _HOTEL_TOKENS)
    has_flight = any(token in blob for token in _FLIGHT_TOKENS)
    has_bundle_word = any(token in blob for token in _BUNDLE_WORD_TOKENS)
    if any(token and token in url_blob for token in _PACKAGE_URL_TOKENS):
        if (has_hotel and has_flight) or (has_bundle_word and has_hotel):
            return True
    guard_enabled = False
    try:
        guard_enabled = bool(
            _plugin_for_site(site).package_bundle_page_guard_enabled(
                {"site": str(site or "").strip().lower()}
            )
        )
    except Exception:
        guard_enabled = False
    if not guard_enabled:
        return False
    if any(token and token in blob for token in _PACKAGE_PAGE_TOKENS):
        return True
    return has_hotel and has_flight and has_bundle_word


def _is_request_failure_reason(reason: str) -> bool:
    """Return True for transport/runtime request failures (not parse misses)."""
    return isinstance(reason, str) and reason.startswith("llm_request_failed_")


def _looks_non_flight_scope_reason(reason: str) -> bool:
    """Detect strong VLM non-flight scope reasons."""
    text = str(reason or "").strip().lower()
    if not text:
        return False
    hotel_tokens = tuple(_HOTEL_TOKENS) + ("map",)
    non_flight_tokens = (
        "not flight",
        "no flight",
        "no flight-related",
        "flight prices are not visible",
        "no airfare",
        "航空券が見つからない",
        "フライト情報がない",
    )
    return any(token in text for token in hotel_tokens) and any(
        token in text for token in non_flight_tokens
    )


class ServiceExtractorPlugin:
    """Service-specific extraction hooks behind a shared pipeline."""

    name = "default"

    def package_bundle_page_guard_enabled(self, ctx: Dict[str, Any]) -> bool:
        """Whether site-specific package-bundle text guard is enabled."""
        return False

    def pre_guard(self, html: str, url: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Short-circuit guard before deterministic/LLM/VLM extraction."""
        if looks_package_bundle_page(html=html, site=str(ctx.get("site", "")), url=url or ""):
            return {
                "price": None,
                "currency": None,
                "confidence": "low",
                "selector_hint": None,
                "source": "heuristic_guard",
                "reason": "package_bundle_page",
                "scope_guard": "fail",
                "scope_guard_basis": "deterministic",
                "scope_guard_trigger": "deterministic",
            }
        return None

    def heuristic_extract_overrides(self, html: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Service-specific deterministic override before generic heuristics."""
        return None

    def fast_scope_guard(self, html: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Service-specific fast scope guard before heavier extraction paths."""
        return None

    def should_enforce_route_context(self, ctx: Dict[str, Any]) -> bool:
        """Whether post-candidate route-context enforcement should run."""
        return False

    def validate_vlm_candidate(
        self,
        candidate: Dict[str, Any],
        html: str,
        ctx: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Service-specific validation for raw VLM extraction candidates."""
        return candidate

    def post_candidate_scope_guard(
        self,
        candidate: Dict[str, Any],
        html: str,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Service-specific post-candidate scope guard."""
        out = dict(candidate or {})
        out.setdefault("scope_guard", "skip")
        out.setdefault("scope_guard_basis", "deterministic")
        return out

    def extra_confidence_factors(
        self,
        candidate: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> List[str]:
        """Optional service-specific confidence annotations."""
        return []


class GoogleFlightsPlugin(ServiceExtractorPlugin):
    """Google Flights hook implementation."""

    name = "google_flights"

    @staticmethod
    def _with_scope(payload: Dict[str, Any], status: str, basis: str) -> Dict[str, Any]:
        out = dict(payload or {})
        out["scope_guard"] = status
        out["scope_guard_basis"] = basis
        return out

    def heuristic_extract_overrides(self, html: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return _extract_google_embedded_price(
            html=html,
            origin=ctx.get("origin"),
            dest=ctx.get("dest"),
            depart=ctx.get("depart"),
            return_date=ctx.get("return_date"),
        )

    def fast_scope_guard(self, html: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not bool(ctx.get("google_non_flight_fast_guard_enabled", True)):
            return None
        if not _google_non_flight_scope_detected(
            html,
            origin=ctx.get("origin"),
            dest=ctx.get("dest"),
            depart=ctx.get("depart"),
            return_date=ctx.get("return_date"),
            page_url=ctx.get("page_url"),
        ):
            return None
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "source": "heuristic_guard",
            "reason": "html_non_flight_scope",
            "scope_guard": "fail",
            "scope_guard_basis": "deterministic",
            "scope_guard_trigger": "deterministic",
        }

    def should_enforce_route_context(self, ctx: Dict[str, Any]) -> bool:
        return bool(
            ctx.get("google_require_route_context")
            and ctx.get("origin")
            and ctx.get("dest")
            and ctx.get("depart")
            and ((not ctx.get("light_mode")) or bool(ctx.get("screenshot_path")))
        )

    def evaluate_route_binding(self, html: str, ctx: Dict[str, Any]) -> bool:
        route_bind_getter = ctx.get("route_bind_verdict_getter")
        if callable(route_bind_getter):
            try:
                verdict = route_bind_getter()
            except Exception:
                verdict = {}
            if isinstance(verdict, dict) and verdict:
                support = str(verdict.get("support", "none") or "none").strip().lower()
                route_bound = bool(verdict.get("route_bound"))
                requires_strong = bool(get_threshold("scenario_route_bind_gate_requires_strong", True))
                if requires_strong:
                    return route_bound and support == "strong"
                # Weak/strong fused support is route-bound enough when strong evidence is not required.
                return route_bound or support in {"strong", "weak"}

        # Fallback when fused verdict is unavailable: retain deterministic DOM context matcher.
        cache = ctx.setdefault("_plugin_cache", {})
        key = (
            str(ctx.get("origin") or "").strip().upper(),
            str(ctx.get("dest") or "").strip().upper(),
            str(ctx.get("depart") or "").strip(),
            str(ctx.get("return_date") or "").strip(),
            str(ctx.get("page_url") or "").strip(),
            len(html or ""),
        )
        bucket = cache.setdefault("google_route_binding", {})
        if key in bucket:
            return bool(bucket[key])
        matched = _google_page_context_matches(
            html,
            origin=ctx.get("origin"),
            dest=ctx.get("dest"),
            depart=ctx.get("depart"),
            return_date=ctx.get("return_date"),
            page_url=ctx.get("page_url"),
        )
        bucket[key] = bool(matched)
        return bool(matched)

    def load_vlm_scope_probe(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        cache = ctx.setdefault("_plugin_cache", {})
        if "vlm_scope_probe" in cache:
            return cache["vlm_scope_probe"]
        scope: Dict[str, Any] = {}
        screenshot_path = str(ctx.get("screenshot_path") or "").strip()
        if not bool(ctx.get("vlm_scope_guard_enabled", True)) or not screenshot_path:
            cache["vlm_scope_probe"] = scope
            return scope
        try:
            scope = analyze_page_ui_with_vlm(
                screenshot_path,
                site=str(ctx.get("site") or ""),
                origin=str(ctx.get("origin") or ""),
                dest=str(ctx.get("dest") or ""),
                depart=str(ctx.get("depart") or ""),
                return_date=str(ctx.get("return_date") or ""),
                timeout_sec=int(ctx.get("vlm_scope_guard_timeout_sec", 120)),
                max_variants=int(ctx.get("vlm_scope_guard_max_variants", 1)),
            )
        except Exception:
            scope = {}
        if not isinstance(scope, dict):
            scope = {}
        cache["vlm_scope_probe"] = scope
        return scope

    def load_llm_scope_probe(self, html: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        cache = ctx.setdefault("_plugin_cache", {})
        if "llm_scope_probe" in cache:
            return cache["llm_scope_probe"]
        scope: Dict[str, Any] = {}
        if not bool(ctx.get("llm_scope_guard_enabled", True)):
            cache["llm_scope_probe"] = scope
            return scope
        has_screenshot_hint = bool(str(ctx.get("screenshot_path") or "").strip())
        has_any_route_signal = bool(
            str(ctx.get("origin") or "").strip()
            or str(ctx.get("dest") or "").strip()
            or str(ctx.get("depart") or "").strip()
            or str(ctx.get("return_date") or "").strip()
        )
        has_full_route_context = bool(
            ctx.get("origin") and ctx.get("dest") and ctx.get("depart")
        )
        if has_any_route_signal and not has_full_route_context:
            cache["llm_scope_probe"] = scope
            return scope
        if not has_full_route_context and not has_screenshot_hint:
            cache["llm_scope_probe"] = scope
            return scope
        try:
            scope = assess_trip_product_scope_with_llm(
                html,
                site=str(ctx.get("site") or ""),
                origin=str(ctx.get("origin") or ""),
                dest=str(ctx.get("dest") or ""),
                depart=str(ctx.get("depart") or ""),
                return_date=str(ctx.get("return_date") or ""),
                timeout_sec=int(ctx.get("llm_scope_guard_timeout_sec", 120)),
            )
        except Exception:
            scope = {}
        if not isinstance(scope, dict):
            scope = {}
        cache["llm_scope_probe"] = scope
        return scope

    def compute_price_grounding(
        self,
        html: str,
        price: Any,
        currency: Optional[str],
        ctx: Dict[str, Any],
    ) -> bool:
        checker = ctx.get("price_grounded_in_html_checker")
        if callable(checker):
            return bool(checker(price, currency))
        return _google_price_is_grounded_in_html(
            html,
            price=price,
            currency=currency,
        )

    def resolve_scope_conflict(
        self,
        *,
        candidate_source: str,
        vlm_non_flight: bool,
        llm_non_flight: bool,
        deterministic_flight_evidence: bool,
        route_bind_support: str = "none",
        vlm_affirms_flight: bool,
        price_grounded: bool,
        ctx: Dict[str, Any],
        llm_page_class: str,
        llm_trip_product: str,
    ) -> Dict[str, Any]:
        resolved = False
        reason = ""
        if (
            llm_non_flight
            and not vlm_non_flight
            and deterministic_flight_evidence
            and str(route_bind_support or "none").strip().lower() == "strong"
        ):
            can_override = True
            if candidate_source in {"vlm", "vlm_multimodal"}:
                can_override = vlm_affirms_flight and (
                    price_grounded
                    or not bool(ctx.get("vlm_price_grounding_required_on_conflict", True))
                )
            if can_override:
                resolved = True
                reason = "llm_non_flight_overridden_by_route_context"
                llm_non_flight = False
                if llm_page_class in {"flight_hotel_package", "garbage_page", "irrelevant_page"}:
                    llm_page_class = "unknown"
                if llm_trip_product == "flight_hotel_package":
                    llm_trip_product = "unknown"
            else:
                reason = "scope_conflict_unresolved_for_vlm_price"
        elif llm_non_flight and not vlm_non_flight and deterministic_flight_evidence:
            reason = "scope_conflict_route_support_not_strong"
        return {
            "resolved": resolved,
            "reason": reason,
            "llm_non_flight": llm_non_flight,
            "llm_page_class": llm_page_class,
            "llm_trip_product": llm_trip_product,
        }

    def build_guard_fail_payload(self, reason: str, **meta: Any) -> Dict[str, Any]:
        has_explicit_trigger = "scope_guard_trigger" in meta
        out = {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "source": "vlm_scope_guard" if reason != "package_bundle_page" else "heuristic_guard",
            "reason": reason,
            "scope_guard": "fail",
            "scope_guard_basis": "deterministic",
            "scope_guard_trigger": "deterministic",
        }
        out.update(meta)
        basis = str(out.get("scope_guard_basis", "deterministic") or "deterministic").strip().lower()
        if basis not in {"deterministic", "vlm", "llm", "mixed"}:
            basis = "deterministic"
        out["scope_guard_basis"] = basis
        trigger_seed = out.get("scope_guard_trigger") if has_explicit_trigger else basis
        trigger = str(trigger_seed or basis).strip().lower()
        if trigger not in {"deterministic", "vlm", "llm", "mixed"}:
            trigger = basis
        out["scope_guard_trigger"] = trigger
        return out

    def validate_vlm_candidate(
        self,
        candidate: Dict[str, Any],
        html: str,
        ctx: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        out = dict(candidate or {})
        if out.get("price") is None:
            return out
        if not (ctx.get("origin") and ctx.get("dest") and ctx.get("depart")):
            return out
        self.evaluate_route_binding(html, ctx)
        visible_price_text = str(out.get("visible_price_text", "") or "").strip()
        if isinstance(out.get("price"), (int, float)):
            vlm_price = float(out.get("price"))
            if visible_price_text:
                text_prices = [v for v, _ in _extract_price_candidates(visible_price_text)]
                if text_prices:
                    nearest = min(abs(v - vlm_price) for v in text_prices)
                    candidate_currency = str(out.get("currency", "") or "").strip().upper()
                    tolerance = max(
                        200.0 if candidate_currency == "JPY" else 2.0,
                        abs(vlm_price) * 0.005,
                    )
                    if nearest > tolerance:
                        return None
                    out["price_grounded_in_html"] = True
                else:
                    out["price_grounded_in_html"] = False
            else:
                out["price_grounded_in_html"] = False
        if (
            bool(ctx.get("vlm_llm_price_verify_enabled", False))
            and isinstance(out.get("price"), (int, float))
        ):
            verify = assess_vlm_price_candidate_with_llm(
                html,
                site=str(ctx.get("site") or ""),
                price=float(out.get("price")),
                currency=str(out.get("currency", "") or ""),
                origin=str(ctx.get("origin") or ""),
                dest=str(ctx.get("dest") or ""),
                depart=str(ctx.get("depart") or ""),
                return_date=str(ctx.get("return_date") or ""),
                timeout_sec=int(ctx.get("vlm_llm_price_verify_timeout_sec", 180)),
            )
            accept = verify.get("accept")
            if accept is False:
                return {
                    "price": None,
                    "currency": None,
                    "confidence": "low",
                    "selector_hint": None,
                    "source": "vlm",
                    "reason": "vlm_price_rejected_by_llm_verify",
                    "vlm_verify_reason": verify.get("reason", ""),
                    "vlm_verify_support": verify.get("support", "none"),
                    "scope_guard": "fail",
                    "scope_guard_basis": "llm",
                    "scope_guard_trigger": "llm",
                }
            if accept == "unknown" and bool(ctx.get("vlm_llm_price_verify_fail_closed", False)):
                return {
                    "price": None,
                    "currency": None,
                    "confidence": "low",
                    "selector_hint": None,
                    "source": "vlm",
                    "reason": "vlm_price_verify_unknown",
                    "vlm_verify_reason": verify.get("reason", ""),
                    "vlm_verify_support": verify.get("support", "none"),
                    "scope_guard": "fail",
                    "scope_guard_basis": "llm",
                    "scope_guard_trigger": "llm",
                }
        return out

    def post_candidate_scope_guard(
        self,
        candidate: Dict[str, Any],
        html: str,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        out = dict(candidate or {})
        source = str(out.get("source", "") or "").strip().lower()
        reason = str(out.get("reason", "") or "").strip().lower()
        if out.get("price") is None:
            if "scope_guard" in out and "scope_guard_basis" in out:
                return out
            if source == "vlm_scope_guard":
                if reason in {"html_non_flight_scope", "google_route_context_unbound"}:
                    return self._with_scope(out, "fail", "deterministic")
                if reason in {"vlm_non_flight_scope", "scope_conflict_unresolved_vlm_price", "vlm_scope_unknown"}:
                    return self._with_scope(out, "fail", "mixed")
                return self._with_scope(out, "fail", "deterministic")
            if source == "vlm" and reason.startswith("vlm_"):
                return self._with_scope(out, "fail", "vlm")
            return self._with_scope(out, "skip", "deterministic")
        if source == "heuristic_embedded":
            return self._with_scope(out, "pass", "deterministic")
        if self.fast_scope_guard(html, ctx):
            return self.build_guard_fail_payload("html_non_flight_scope")
        if self.should_enforce_route_context(ctx) and not self.evaluate_route_binding(html, ctx):
            return self.build_guard_fail_payload("google_route_context_unbound")

        scope = self.load_vlm_scope_probe(ctx)
        llm_scope = self.load_llm_scope_probe(html, ctx)
        vlm_page_class = _normalize_page_class(scope.get("page_class") if isinstance(scope, dict) else "")
        llm_page_class = _normalize_page_class(llm_scope.get("page_class") if isinstance(llm_scope, dict) else "")
        vlm_trip_product = str(scope.get("trip_product", "") or "").strip().lower()
        llm_trip_product = str(llm_scope.get("trip_product", "") or "").strip().lower()
        if vlm_trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
            vlm_trip_product = "unknown"
        if llm_trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
            llm_trip_product = "unknown"
        if vlm_page_class == "unknown" and vlm_trip_product in {"flight_only", "flight_hotel_package"}:
            vlm_page_class = vlm_trip_product
        if llm_page_class == "unknown" and llm_trip_product in {"flight_only", "flight_hotel_package"}:
            llm_page_class = llm_trip_product
        if vlm_trip_product == "unknown" and vlm_page_class in {"flight_only", "flight_hotel_package"}:
            vlm_trip_product = vlm_page_class
        if llm_trip_product == "unknown" and llm_page_class in {"flight_only", "flight_hotel_package"}:
            llm_trip_product = llm_page_class

        non_flight_classes = {"flight_hotel_package", "garbage_page", "irrelevant_page"}
        route_bind_support = "none"
        route_bind_getter = ctx.get("route_bind_verdict_getter")
        if callable(route_bind_getter):
            try:
                route_bind_verdict = route_bind_getter()
            except Exception:
                route_bind_verdict = {}
            if isinstance(route_bind_verdict, dict):
                route_bind_support = str(
                    route_bind_verdict.get("support", "none") or "none"
                ).strip().lower()
        if route_bind_support not in {"strong", "weak", "none"}:
            route_bind_support = "none"
        if route_bind_support == "none":
            if bool(
                ctx.get("origin")
                and ctx.get("dest")
                and ctx.get("depart")
                and self.evaluate_route_binding(html, ctx)
            ):
                route_bind_support = "strong"
        deterministic_flight_evidence = route_bind_support == "strong"
        price_grounded_in_html = self.compute_price_grounding(
            html,
            out.get("price"),
            out.get("currency"),
            ctx,
        )
        vlm_non_flight = (
            vlm_page_class in non_flight_classes
            or vlm_trip_product == "flight_hotel_package"
        )
        llm_non_flight = (
            llm_page_class in non_flight_classes
            or llm_trip_product == "flight_hotel_package"
        )
        conflict = self.resolve_scope_conflict(
            candidate_source=source,
            vlm_non_flight=vlm_non_flight,
            llm_non_flight=llm_non_flight,
            deterministic_flight_evidence=deterministic_flight_evidence,
            route_bind_support=route_bind_support,
            vlm_affirms_flight=bool(vlm_page_class == "flight_only" or vlm_trip_product == "flight_only"),
            price_grounded=bool(price_grounded_in_html),
            ctx=ctx,
            llm_page_class=llm_page_class,
            llm_trip_product=llm_trip_product,
        )
        scope_guard_conflict_resolved = bool(conflict.get("resolved"))
        scope_guard_conflict_reason = str(conflict.get("reason") or "")
        llm_non_flight = bool(conflict.get("llm_non_flight"))
        llm_page_class = str(conflict.get("llm_page_class") or llm_page_class)
        llm_trip_product = str(conflict.get("llm_trip_product") or llm_trip_product)

        flight_evidence = (
            vlm_page_class == "flight_only"
            or llm_page_class == "flight_only"
            or vlm_trip_product == "flight_only"
            or llm_trip_product == "flight_only"
            or deterministic_flight_evidence
        )
        non_flight_evidence = bool(vlm_non_flight or llm_non_flight)
        dominant_non_flight = "flight_hotel_package"
        for klass in (vlm_page_class, llm_page_class):
            if klass in non_flight_classes:
                dominant_non_flight = klass
                break
        vlm_has_scope_signal = bool(
            vlm_page_class != "unknown"
            or vlm_trip_product != "unknown"
            or (isinstance(scope, dict) and bool(scope))
        )
        llm_has_scope_signal = bool(
            llm_page_class != "unknown"
            or llm_trip_product != "unknown"
            or (isinstance(llm_scope, dict) and bool(llm_scope))
        )
        if vlm_has_scope_signal and llm_has_scope_signal:
            pass_basis = "mixed"
        elif vlm_has_scope_signal:
            pass_basis = "vlm"
        elif llm_has_scope_signal:
            pass_basis = "llm"
        else:
            pass_basis = "deterministic"

        if source in {"vlm", "vlm_multimodal"} and llm_non_flight and not scope_guard_conflict_resolved:
            blocked = self.build_guard_fail_payload(
                "scope_conflict_unresolved_vlm_price",
                page_class=dominant_non_flight,
                vlm_page_class=vlm_page_class,
                llm_page_class=llm_page_class,
                vlm_trip_product=vlm_trip_product,
                llm_trip_product=llm_trip_product,
                price_grounded_in_html=bool(price_grounded_in_html),
                scope_guard_basis="mixed",
            )
            if scope_guard_conflict_reason:
                blocked["scope_guard_conflict_reason"] = scope_guard_conflict_reason
            return blocked

        if non_flight_evidence and not flight_evidence:
            basis = "mixed"
            if vlm_non_flight and not llm_non_flight:
                basis = "vlm"
            elif llm_non_flight and not vlm_non_flight:
                basis = "llm"

            # Check for irrelevant_page downgrade (Phase 3.1 VLM downgrade)
            # If heuristic blocks as irrelevant_page but VLM affirms flights_results with medium+ confidence,
            # downgrade the block to allow processing to continue, up to a limit of 2 overrides per scenario.
            downgrade_result = {}
            if dominant_non_flight == "irrelevant_page" and isinstance(scope, dict):
                downgrade_result = evaluate_irrelevant_page_downgrade(
                    vlm_probe=scope,
                    heuristic_reason="scope_guard_non_flight_irrelevant_page",
                    context=ctx,
                    max_overrides=2,
                )

            if downgrade_result.get("should_downgrade"):
                # Downgrade applied: continue with pass verdict instead of block
                log.info(
                    "extractor.scope_override_irrelevant_page site=%s reason=%s count=%s",
                    ctx.get("site"),
                    downgrade_result.get("reason", ""),
                    downgrade_result.get("override_count", 0),
                )
                out["scope_guard"] = "pass"
                out["scope_guard_basis"] = "mixed"
                out["scope_conflict_detected"] = True
                out["resolved_via_vlm"] = True
                out["scope_override_count"] = downgrade_result.get("override_count", 0)
                out["scope_override_reason"] = downgrade_result.get("reason", "")
                out["vlm_page_class"] = vlm_page_class
                out["llm_page_class"] = llm_page_class
                out["vlm_trip_product"] = vlm_trip_product
                out["llm_trip_product"] = llm_trip_product
                out["price_grounded_in_html"] = bool(price_grounded_in_html)
                return out

            blocked = self.build_guard_fail_payload(
                "vlm_non_flight_scope",
                page_class=dominant_non_flight,
                vlm_page_class=vlm_page_class,
                llm_page_class=llm_page_class,
                vlm_trip_product=vlm_trip_product,
                llm_trip_product=llm_trip_product,
                price_grounded_in_html=bool(price_grounded_in_html),
                scope_guard_basis=basis,
            )
            if scope_guard_conflict_resolved:
                blocked["scope_guard_conflict_resolved"] = True
                blocked["scope_guard_conflict_reason"] = scope_guard_conflict_reason
            return blocked

        if (
            ((bool(ctx.get("vlm_scope_guard_fail_closed")) and not scope)
             or (bool(ctx.get("llm_scope_guard_fail_closed")) and not llm_scope))
            and not flight_evidence
        ):
            basis = "mixed"
            if bool(ctx.get("vlm_scope_guard_fail_closed")) and not scope and not (
                bool(ctx.get("llm_scope_guard_fail_closed")) and not llm_scope
            ):
                basis = "vlm"
            elif bool(ctx.get("llm_scope_guard_fail_closed")) and not llm_scope and not (
                bool(ctx.get("vlm_scope_guard_fail_closed")) and not scope
            ):
                basis = "llm"
            blocked = self.build_guard_fail_payload(
                "vlm_scope_unknown",
                vlm_page_class=vlm_page_class,
                llm_page_class=llm_page_class,
                vlm_trip_product=vlm_trip_product,
                llm_trip_product=llm_trip_product,
                price_grounded_in_html=bool(price_grounded_in_html),
                scope_guard_basis=basis,
            )
            if scope_guard_conflict_resolved:
                blocked["scope_guard_conflict_resolved"] = True
                blocked["scope_guard_conflict_reason"] = scope_guard_conflict_reason
            return blocked

        if (
            vlm_page_class != "unknown"
            or llm_page_class != "unknown"
            or vlm_trip_product != "unknown"
            or llm_trip_product != "unknown"
        ):
            if route_bind_support != "none":
                out["route_bind_support"] = route_bind_support
            if vlm_page_class != "unknown":
                out["vlm_page_class"] = vlm_page_class
            if llm_page_class != "unknown":
                out["llm_page_class"] = llm_page_class
            if vlm_trip_product != "unknown":
                out["vlm_trip_product"] = vlm_trip_product
            if llm_trip_product != "unknown":
                out["llm_trip_product"] = llm_trip_product
            out["price_grounded_in_html"] = bool(price_grounded_in_html)
            if scope_guard_conflict_resolved:
                out["scope_guard_conflict_resolved"] = True
                out["scope_guard_conflict_reason"] = scope_guard_conflict_reason
                return self._with_scope(out, "conflict_resolved", "mixed")
            return self._with_scope(out, "pass", pass_basis)

        if scope_guard_conflict_resolved:
            out["scope_guard_conflict_resolved"] = True
            out["scope_guard_conflict_reason"] = scope_guard_conflict_reason
            return self._with_scope(out, "conflict_resolved", "mixed")
        return self._with_scope(out, "pass", "deterministic")

    def extra_confidence_factors(
        self,
        candidate: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> List[str]:
        factors: List[str] = []
        reason = str(candidate.get("reason", "") or "").strip().lower()
        if reason == "google_route_context_unbound":
            factors.append("route:unbound")
        return factors


_DEFAULT_EXTRACTOR_PLUGIN = ServiceExtractorPlugin()
_PLUGIN_BY_SITE: Dict[str, ServiceExtractorPlugin] = {
    "google_flights": GoogleFlightsPlugin(),
}


def _plugin_for_site(site: str) -> ServiceExtractorPlugin:
    """Return plugin instance for service key."""
    return _PLUGIN_BY_SITE.get((site or "").strip().lower(), _DEFAULT_EXTRACTOR_PLUGIN)


def _route_match_score(
    text: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    provider: Optional[str] = None,
) -> int:
    """Return coarse relevance score between one text snippet and target route."""
    raw_blob = text or ""
    blob = raw_blob.upper()
    if not raw_blob:
        return 0

    def _route_aliases(code: Optional[str]):
        if provider:
            return get_airport_aliases_for_provider(code or "", provider)
        return get_airport_aliases(code or "")

    def _contains_any(token_set):
        for token in token_set:
            if not token:
                continue
            if _contains_route_token(raw_blob, blob, token):
                return True
        return False

    o = (origin or "").strip().upper()
    d = (dest or "").strip().upper()
    if not o or not d:
        return 0
    score = 0
    origin_tokens = _route_aliases(o)
    dest_tokens = _route_aliases(d)
    if _contains_any(origin_tokens):
        score += 3
    if _contains_any(dest_tokens):
        score += 3

    def _first_index(tokens):
        idx = None
        for token in tokens:
            position = _first_route_token_index(raw_blob, blob, token)
            if position is not None and (idx is None or position < idx):
                idx = position
        return idx

    first_origin = _first_index(origin_tokens)
    first_dest = _first_index(dest_tokens)
    if first_origin is not None and first_dest is not None:
        if first_origin < first_dest:
            score += 2
        else:
            score += 1

    if depart and depart in raw_blob:
        score += 3
    if return_date and return_date in raw_blob:
        score += 2
    return score


def _route_strict_anchor_score(
    text: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    provider: str = "google_flights",
) -> int:
    """Score strict route anchors (exact airport or provider-mapped metro code)."""
    raw_blob = text or ""
    blob = raw_blob.upper()
    if not raw_blob:
        return 0

    def _contains(token: str) -> bool:
        return _contains_route_token(raw_blob, blob, token)

    o = (origin or "").strip().upper()
    d = (dest or "").strip().upper()
    if not o or not d:
        return 0
    mapped_o = map_airport_code_for_provider(o, provider)
    mapped_d = map_airport_code_for_provider(d, provider)

    score = 0
    for token in (o, mapped_o):
        if _contains(token):
            score += 1
            break
    for token in (d, mapped_d):
        if _contains(token):
            score += 1
            break
    return score


def _extract_with_heuristics(
    html: str,
    site: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    page_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort deterministic fallback for very noisy DOM snapshots."""
    if not is_supported_service(site):
        return None
    if looks_package_bundle_page(html=html, site=site, url=page_url or ""):
        # Package pages mix hotel+flight pricing and skew air-only monitoring.
        return None

    google_site = _is_google_flights_site(site)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidate_texts = []

    for el in soup.select("[aria-label]"):
        aria_label = (el.get("aria-label") or "").strip()
        if not aria_label:
            continue
        lowered = aria_label.lower()
        if (
            "flight" in lowered
            or "from " in lowered
            or "price" in lowered
            or _PRICE_PATTERN.search(aria_label)
        ):
            candidate_texts.append(aria_label)
        if len(candidate_texts) >= 400:
            break

    if len(candidate_texts) < 400:
        for text in soup.stripped_strings:
            if len(text) > 160:
                continue
            if _PRICE_PATTERN.search(text):
                candidate_texts.append(text)
            if len(candidate_texts) >= 400:
                break

    best = None
    best_score = -1
    best_strict_score = -1
    best_has_depart = False
    for text in candidate_texts:
        relevance = _route_match_score(
            text,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            provider="google_flights" if google_site else "",
        )
        strict_score = _route_strict_anchor_score(
            text,
            origin=origin,
            dest=dest,
            provider="google_flights" if google_site else "",
        )
        has_depart = bool(depart and depart in text)
        for price, currency in _extract_price_candidates(text):
            # Very broad but avoids tiny counters and absurdly large IDs.
            if HEURISTIC_MIN_PRICE <= price <= HEURISTIC_MAX_PRICE:
                if relevance > best_score:
                    best_score = relevance
                    best_strict_score = strict_score
                    best = (price, currency)
                    best_has_depart = has_depart
                elif relevance == best_score:
                    if strict_score > best_strict_score:
                        best_strict_score = strict_score
                        best = (price, currency)
                        best_has_depart = has_depart
                    elif strict_score == best_strict_score and (
                        best is None or price < best[0]
                    ):
                        best = (price, currency)
                        best_has_depart = has_depart

    if best is None:
        return None
    if origin and dest and best_score <= 0:
        # Route context was provided but no price snippet matched it.
        return None
    if google_site and origin and dest and best_strict_score <= 0:
        # Avoid unrelated metro suggestions (e.g., KIX<->NRT cards for a different trip).
        return None
    if depart and not best_has_depart:
        # Date-aware route extraction is required for volatile pages like Google Flights.
        if not (
            google_site
            and best_strict_score > 0
            and _google_page_context_matches(
                html,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                page_url=page_url,
            )
        ):
            return None

    return {
        "price": best[0],
        "currency": best[1],
        "confidence": "low",
        "selector_hint": None,
        "source": "heuristic_html",
        "reason": "heuristic_min_price",
    }


# -------------------------
# Selector-based extraction
# -------------------------

def extract_with_selector(
    html: str,
    selector_entry: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract a price using a cached selector entry; return None on failure."""
    selector = selector_entry["selector"]["css"]
    attribute = selector_entry["selector"].get("attribute", "text")

    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(selector)

    if not el:
        return None

    if attribute == "text":
        raw = el.get_text(strip=True)
    else:
        raw = el.get(attribute)

    if not raw:
        return None

    price = extract_number(raw)
    if price is None:
        return None

    return {
        "price": price,
        "currency": selector_entry.get("selector", {}).get("currency_hint"),
        "confidence": "high",
        "source": "cached_selector",
    }


# -------------------------
# LLM-based extraction
# -------------------------

def extract_with_llm(
    html: str,
    site: str,
    task: str,
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract a price via LLM parsing and return a normalized result payload."""
    llm_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    plugin = _plugin_for_site(site)
    light_mode = llm_mode == "light"
    light_skip_llm = bool(get_threshold("light_mode_skip_llm_extract", True))
    light_try_llm_on_miss = _env_bool(
        "FLIGHT_WATCHER_LIGHT_TRY_LLM_EXTRACT_ON_HEURISTIC_MISS",
        bool(get_threshold("light_mode_try_llm_extract_on_heuristic_miss", True)),
    )
    light_llm_extract_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_LIGHT_EXTRACT_TIMEOUT_SEC",
        int(get_threshold("llm_light_extract_timeout_sec", 25)),
    )
    quality_gate_enabled = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_HTML_QUALITY_GATE_ENABLED",
        bool(get_threshold("extract_html_quality_gate_enabled", True)),
    )
    light_try_llm_quality_judge = _env_bool(
        "FLIGHT_WATCHER_LIGHT_TRY_LLM_HTML_QUALITY_JUDGE",
        bool(get_threshold("light_mode_try_llm_html_quality_judge", True)),
    )
    light_llm_quality_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_LIGHT_HTML_QUALITY_TIMEOUT_SEC",
        int(get_threshold("llm_light_html_quality_timeout_sec", 12)),
    )
    chunking_enabled = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_SEMANTIC_CHUNK_ENABLED",
        bool(get_threshold("extract_semantic_chunk_enabled", True)),
    )
    llm_chunk_attempts = max(1, int(get_threshold("llm_extract_chunk_attempts", 3)))
    llm_light_chunk_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_LIGHT_CHUNK_TIMEOUT_SEC",
        int(get_threshold("llm_light_extract_chunk_timeout_sec", 12)),
    )
    llm_chunk_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_CHUNK_TIMEOUT_SEC",
        int(get_threshold("llm_extract_chunk_timeout_sec", 30)),
    )
    llm_extract_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_EXTRACT_TIMEOUT_SEC",
        int(get_threshold("llm_extract_timeout_sec", 90)),
    )
    vlm_enabled = _env_bool(
        "FLIGHT_WATCHER_VLM_EXTRACT_ENABLED",
        bool(get_threshold("vlm_extract_enabled", False)),
    )
    light_try_vlm_on_miss = _env_bool(
        "FLIGHT_WATCHER_LIGHT_TRY_VLM_ON_MISS",
        bool(get_threshold("light_mode_try_vlm_extract_on_heuristic_miss", True)),
    )
    vlm_default_timeout = int(
        get_threshold(
            "vlm_extract_timeout_sec",
            light_llm_extract_timeout_sec if light_mode else 45,
        )
    )
    # Keep VLM budget independent from light LLM extract timeout.
    # Vision parsing is typically slower and was getting unintentionally capped
    # (e.g. 150s -> 60s when adaptive profile reduced light LLM timeout).
    vlm_default_timeout = max(3, vlm_default_timeout)
    vlm_timeout_sec = _env_int(
        "FLIGHT_WATCHER_VLM_EXTRACT_TIMEOUT_SEC",
        vlm_default_timeout,
    )
    multimodal_mode = os.getenv(
        "FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE",
        str(get_threshold("agentic_multimodal_mode", "off")),
    ).strip().lower()
    if multimodal_mode not in {"off", "assist", "primary", "judge", "judge_primary"}:
        multimodal_mode = "off"
    multimodal_timeout_sec = _env_int(
        "FLIGHT_WATCHER_MULTIMODAL_EXTRACT_TIMEOUT_SEC",
        int(get_threshold("multimodal_extract_timeout_sec", 1200)),
    )
    vlm_scope_guard_enabled = _env_bool(
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_ENABLED",
        bool(get_threshold("extract_vlm_scope_guard_enabled", True)),
    )
    vlm_scope_guard_timeout_sec = _env_int(
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_TIMEOUT_SEC",
        int(get_threshold("extract_vlm_scope_guard_timeout_sec", 120)),
    )
    vlm_scope_guard_timeout_cap_sec = _env_int(
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_TIMEOUT_CAP_SEC",
        int(get_threshold("extract_vlm_scope_guard_timeout_cap_sec", 300)),
    )
    if vlm_scope_guard_timeout_cap_sec > 0:
        vlm_scope_guard_timeout_sec = min(
            vlm_scope_guard_timeout_sec,
            max(1, vlm_scope_guard_timeout_cap_sec),
        )
    vlm_scope_guard_max_variants = max(
        1,
        _env_int(
            "FLIGHT_WATCHER_VLM_SCOPE_GUARD_MAX_VARIANTS",
            int(get_threshold("extract_vlm_scope_guard_max_variants", 1)),
        ),
    )
    vlm_scope_guard_fail_closed = _env_bool(
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_FAIL_CLOSED",
        bool(get_threshold("extract_vlm_scope_guard_fail_closed", False)),
    )
    llm_scope_guard_enabled = _env_bool(
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_ENABLED",
        bool(get_threshold("extract_llm_scope_guard_enabled", True)),
    )
    llm_scope_guard_timeout_sec = _env_int(
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_TIMEOUT_SEC",
        int(get_threshold("extract_llm_scope_guard_timeout_sec", 120)),
    )
    llm_scope_guard_timeout_cap_sec = _env_int(
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_TIMEOUT_CAP_SEC",
        int(get_threshold("extract_llm_scope_guard_timeout_cap_sec", 240)),
    )
    if llm_scope_guard_timeout_cap_sec > 0:
        llm_scope_guard_timeout_sec = min(
            llm_scope_guard_timeout_sec,
            max(1, llm_scope_guard_timeout_cap_sec),
        )
    llm_scope_guard_fail_closed = _env_bool(
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_FAIL_CLOSED",
        bool(get_threshold("extract_llm_scope_guard_fail_closed", False)),
    )
    vlm_llm_price_verify_enabled = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED",
        bool(get_threshold("extract_vlm_llm_price_verify_enabled", True)),
    )
    vlm_llm_price_verify_timeout_sec = _env_int(
        "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_TIMEOUT_SEC",
        int(get_threshold("extract_vlm_llm_price_verify_timeout_sec", 180)),
    )
    vlm_llm_price_verify_timeout_cap_sec = _env_int(
        "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_TIMEOUT_CAP_SEC",
        int(get_threshold("extract_vlm_llm_price_verify_timeout_cap_sec", 300)),
    )
    if vlm_llm_price_verify_timeout_cap_sec > 0:
        vlm_llm_price_verify_timeout_sec = min(
            vlm_llm_price_verify_timeout_sec,
            max(1, vlm_llm_price_verify_timeout_cap_sec),
        )
    vlm_llm_price_verify_fail_closed = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_FAIL_CLOSED",
        bool(get_threshold("extract_vlm_llm_price_verify_fail_closed", False)),
    )
    vlm_price_grounding_required_on_conflict = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_VLM_PRICE_GROUNDING_REQUIRED_ON_CONFLICT",
        bool(get_threshold("extract_vlm_price_grounding_required_on_conflict", True)),
    )
    google_require_route_context = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_GOOGLE_REQUIRE_ROUTE_CONTEXT",
        bool(get_threshold("extract_google_require_route_context", True)),
    )
    google_non_flight_fast_guard_enabled = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_GOOGLE_NON_FLIGHT_FAST_GUARD",
        bool(get_threshold("extract_google_non_flight_fast_guard", True)),
    )
    scenario_route_bind_gate_enabled = _env_bool(
        "FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_GATE_ENABLED",
        bool(get_threshold("scenario_route_bind_gate_enabled", True)),
    )
    scenario_route_bind_gate_requires_strong = _env_bool(
        "FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_GATE_REQUIRES_STRONG",
        bool(get_threshold("scenario_route_bind_gate_requires_strong", True)),
    )
    scenario_route_bind_vlm_verify_enabled = _env_bool(
        "FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_VLM_VERIFY_ENABLED",
        bool(get_threshold("scenario_route_bind_vlm_verify_enabled", True)),
    )
    scenario_route_bind_vlm_timeout_sec = _env_int(
        "FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_VLM_TIMEOUT_SEC",
        int(get_threshold("scenario_route_bind_vlm_timeout_sec", 180)),
    )
    scenario_route_bind_fail_closed_on_mismatch = _env_bool(
        "FLIGHT_WATCHER_SCENARIO_ROUTE_BIND_FAIL_CLOSED_ON_MISMATCH",
        bool(get_threshold("scenario_route_bind_fail_closed_on_mismatch", True)),
    )
    vision_price_assist_enabled = _env_bool(
        "FLIGHT_WATCHER_EXTRACT_VISION_PRICE_ASSIST_ENABLED",
        bool(get_threshold("extract_vision_price_assist_enabled", True)),
    )
    visible_text_cache: Optional[str] = None
    semantic_chunks_cache: Optional[List[Dict[str, Any]]] = None
    route_bind_verdict_cache: Optional[Dict[str, Any]] = None
    vision_stage_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
    vision_stage_cooldown: Dict[str, str] = {}

    # Create LLM budget for this extraction phase
    llm_budget = load_llm_budget_from_config()
    log.info(
        "llm.extraction_phase.budget_initialized total_s=%.1f min_remaining_s=%.1f",
        llm_budget.total_wall_clock_s,
        llm_budget.min_remaining_s_for_attempt,
    )

    # Phase 5: Initialize coordination observer if enabled
    coordination_enabled = _env_bool(
        "FLIGHT_WATCHER_COORDINATION_ENABLED",
        bool(get_threshold("coordination_enabled", False)),
    )
    coordination_observer = ExtractionObserver() if coordination_enabled else None
    if coordination_observer:
        log.info("coordination.extraction_phase.observer_initialized")

    def _get_visible_text() -> str:
        """Memoized visible-text extraction for one HTML snapshot."""
        nonlocal visible_text_cache
        if visible_text_cache is None:
            visible_text_cache = _google_visible_text(html)
        return visible_text_cache

    def _price_grounded_in_html_cached(price: Any, currency: Optional[str]) -> bool:
        """Memoized grounding check using one visible-text parse per extraction run."""
        try:
            target = float(price)
        except Exception:
            return False
        tolerance = _price_grounding_tolerance(target)
        visible_text = _get_visible_text()
        if not visible_text:
            return False
        wanted = str(currency or "").strip().upper()
        for candidate, parsed_currency in _extract_price_candidates(visible_text):
            if wanted and parsed_currency and parsed_currency.upper() != wanted:
                continue
            if abs(float(candidate) - target) <= tolerance:
                return True
        return False

    def _get_semantic_chunks_cached() -> List[Dict[str, Any]]:
        """Memoized semantic chunk generation for long HTML."""
        nonlocal semantic_chunks_cache
        if semantic_chunks_cache is None:
            semantic_chunks_cache = _semantic_html_chunks(
                html,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
            )
        return semantic_chunks_cache

    def _get_route_bind_verdict_cached() -> Dict[str, Any]:
        """Memoized fused route-binding verdict per extraction attempt."""
        nonlocal route_bind_verdict_cache
        if route_bind_verdict_cache is not None:
            return route_bind_verdict_cache
        if not (origin and dest and depart):
            route_bind_verdict_cache = {
                "route_bound": False,
                "support": "none",
                "source": "unknown",
                "reason": "missing_expected_context",
                "observed": {"origin": None, "dest": None, "depart": None, "return": None},
                "mismatch_fields": [],
                "dom_probe": {},
                "vlm_probe": {},
            }
            return route_bind_verdict_cache
        use_vlm_verify = (
            bool(scenario_route_bind_vlm_verify_enabled)
            and _is_google_flights_site(site)
            and bool(screenshot_path)
        )
        route_bind_verdict_cache = _compute_google_route_bind_verdict(
            html=html,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            screenshot_path=screenshot_path,
            trip_type="round_trip" if return_date else "one_way",
            use_vlm_verify=use_vlm_verify,
            vlm_timeout_sec=scenario_route_bind_vlm_timeout_sec,
            require_strong=scenario_route_bind_gate_requires_strong,
            fail_closed_on_mismatch=scenario_route_bind_fail_closed_on_mismatch,
            budget=llm_budget,
        )
        return route_bind_verdict_cache

    def _finalize(
        payload: Dict[str, Any],
        *,
        default_scope_guard: str = "skip",
        default_scope_guard_basis: str = "deterministic",
    ) -> Dict[str, Any]:
        """Apply additive normalization fields to extraction outputs."""
        return _normalize_extractor_output(
            payload,
            llm_mode=llm_mode,
            default_scope_guard=default_scope_guard,
            default_scope_guard_basis=default_scope_guard_basis,
        )

    def _call_parse_with_timeout(timeout_sec: Optional[int] = None):
        """Back-compat wrapper for monkeypatched parse_html_with_llm signatures."""
        if plugin_strategy_on:
            strategy_ctx = dict(ctx)
            strategy_ctx["timeout_sec"] = timeout_sec
            strategy_ctx["service_plugin"] = service_plugin
            strategy_ctx["llm_provider"] = llm_provider
            try:
                return extraction_strategy.extract(
                    html=html,
                    screenshot_path=screenshot_path,
                    context=strategy_ctx,
                )
            except Exception:
                # Safety fallback: keep legacy behavior when plugin path fails.
                pass
        try:
            return parse_html_with_llm(
                html=html,
                site=site,
                task=task,
                timeout_sec=timeout_sec,
                budget=llm_budget,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return parse_html_with_llm(
                html=html,
                site=site,
                task=task,
            )

    def _call_parse_on_blob(blob: str, timeout_sec: Optional[int] = None):
        """Back-compat wrapper for chunk-level parse calls."""
        if plugin_strategy_on:
            strategy_ctx = dict(ctx)
            strategy_ctx["timeout_sec"] = timeout_sec
            strategy_ctx["service_plugin"] = service_plugin
            strategy_ctx["llm_provider"] = llm_provider
            try:
                return extraction_strategy.extract(
                    html=blob,
                    screenshot_path=screenshot_path,
                    context=strategy_ctx,
                )
            except Exception:
                # Safety fallback: keep legacy behavior when plugin path fails.
                pass
        try:
            return parse_html_with_llm(
                html=blob,
                site=site,
                task=task,
                timeout_sec=timeout_sec,
                budget=llm_budget,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return parse_html_with_llm(
                html=blob,
                site=site,
                task=task,
            )

    # Keep inner parse path legacy by default; plugin default-on is applied at
    # the top-level extraction router in `extract_price`.
    plugin_strategy_on = (
        _env_bool("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", False)
        and not _env_bool("FLIGHT_WATCHER_DISABLE_PLUGINS", False)
    )
    strategy_key = str(
        os.getenv(
            "FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY",
            get_threshold("extract_strategy_plugin_key", "html_llm"),
        )
    ).strip().lower() or "html_llm"
    service_plugin = get_service_plugin(site) if plugin_strategy_on else None
    llm_provider = get_provider_plugin("default") if plugin_strategy_on else None
    extraction_strategy = (
        get_strategy_plugin(strategy_key) if plugin_strategy_on else None
    )
    plugin_service_runtime_cfg: Dict[str, Any] = {}
    plugin_service_url_candidates: List[str] = []
    if plugin_strategy_on:
        services_cfg_path = os.getenv(
            "FLIGHT_WATCHER_SERVICES_CONFIG",
            "configs/services.yaml",
        )
        try:
            normalized_services_cfg = load_service_plugin_config(services_cfg_path)
            per_service_cfg = normalized_services_cfg.get("per_service", {})
            plugin_service_runtime_cfg = dict(per_service_cfg.get(site, {}) or {})
            plugin_service_runtime_cfg["enabled_service_keys"] = list(
                normalized_services_cfg.get("enabled_service_keys", [])
            )
            if service_plugin is not None:
                plugin_service_url_candidates = list(
                    service_plugin.url_candidates(
                        preferred_url=plugin_service_runtime_cfg.get("preferred_url"),
                        is_domestic=None,
                        knowledge=None,
                        seed_hints=plugin_service_runtime_cfg.get("seed_hints"),
                    )
                )
        except Exception:
            plugin_service_runtime_cfg = {}
            plugin_service_url_candidates = []

    ctx: Dict[str, Any] = {
        "site": site,
        "task": task,
        "origin": origin,
        "dest": dest,
        "depart": depart,
        "return_date": return_date,
        "screenshot_path": screenshot_path,
        "page_url": page_url,
        "llm_mode": llm_mode,
        "light_mode": light_mode,
        "light_skip_llm": light_skip_llm,
        "quality_gate_enabled": quality_gate_enabled,
        "chunking_enabled": chunking_enabled,
        "llm_chunk_attempts": llm_chunk_attempts,
        "llm_light_chunk_timeout_sec": llm_light_chunk_timeout_sec,
        "llm_chunk_timeout_sec": llm_chunk_timeout_sec,
        "llm_extract_timeout_sec": llm_extract_timeout_sec,
        "vlm_enabled": vlm_enabled,
        "light_try_vlm_on_miss": light_try_vlm_on_miss,
        "vlm_timeout_sec": vlm_timeout_sec,
        "multimodal_mode": multimodal_mode,
        "multimodal_timeout_sec": multimodal_timeout_sec,
        "vlm_scope_guard_enabled": vlm_scope_guard_enabled,
        "vlm_scope_guard_timeout_sec": vlm_scope_guard_timeout_sec,
        "vlm_scope_guard_max_variants": vlm_scope_guard_max_variants,
        "vlm_scope_guard_fail_closed": vlm_scope_guard_fail_closed,
        "llm_scope_guard_enabled": llm_scope_guard_enabled,
        "llm_scope_guard_timeout_sec": llm_scope_guard_timeout_sec,
        "llm_scope_guard_fail_closed": llm_scope_guard_fail_closed,
        "vlm_llm_price_verify_enabled": vlm_llm_price_verify_enabled,
        "vlm_llm_price_verify_timeout_sec": vlm_llm_price_verify_timeout_sec,
        "vlm_llm_price_verify_fail_closed": vlm_llm_price_verify_fail_closed,
        "vlm_price_grounding_required_on_conflict": vlm_price_grounding_required_on_conflict,
        "google_require_route_context": google_require_route_context,
        "google_non_flight_fast_guard_enabled": google_non_flight_fast_guard_enabled,
        "scenario_route_bind_gate_enabled": scenario_route_bind_gate_enabled,
        "scenario_route_bind_gate_requires_strong": scenario_route_bind_gate_requires_strong,
        "scenario_route_bind_vlm_verify_enabled": scenario_route_bind_vlm_verify_enabled,
        "scenario_route_bind_vlm_timeout_sec": scenario_route_bind_vlm_timeout_sec,
        "scenario_route_bind_fail_closed_on_mismatch": scenario_route_bind_fail_closed_on_mismatch,
        "route_bind_verdict_getter": _get_route_bind_verdict_cached,
        "plugin_strategy_enabled": plugin_strategy_on,
        "strategy_key": strategy_key,
        "service_plugin_runtime_config": plugin_service_runtime_cfg,
        "service_plugin_url_candidates": plugin_service_url_candidates,
        "price_grounded_in_html_checker": _price_grounded_in_html_cached,
        "_plugin_cache": {},
    }

    vlm_attempted = False
    multimodal_attempted = False
    dom_probe_attempted = False
    vision_assist_attempted = False

    def _maybe_vlm_extract():
        nonlocal vlm_attempted
        if vlm_attempted:
            return None
        vlm_attempted = True
        if not vlm_enabled or not isinstance(screenshot_path, str) or not screenshot_path.strip():
            return None
        try:
            vlm_result = parse_image_with_vlm(
                screenshot_path.strip(),
                site=site,
                task=task,
                origin=origin or "",
                dest=dest or "",
                depart=depart or "",
                return_date=return_date or "",
                html_context=html,
                locale="",
                timeout_sec=vlm_timeout_sec,
                budget=llm_budget,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            vlm_result = parse_image_with_vlm(
                screenshot_path.strip(),
                site=site,
                task=task,
                html_context=html,
                locale="",
            )
        if vlm_result.get("price") is None:
            reason = str(vlm_result.get("reason", "") or "")
            if _looks_non_flight_scope_reason(reason):
                candidate = {
                    "price": None,
                    "currency": None,
                    "confidence": vlm_result.get("confidence", "low"),
                    "selector_hint": None,
                    "source": "vlm",
                    "reason": "vlm_non_flight_scope",
                }
                checked = plugin.validate_vlm_candidate(candidate, html, ctx)
                return checked if isinstance(checked, dict) else None
            return None
        out = {
            "price": vlm_result.get("price"),
            "currency": vlm_result.get("currency"),
            "confidence": vlm_result.get("confidence", "low"),
            "selector_hint": None,
            "source": "vlm",
            "reason": vlm_result.get("reason", ""),
        }
        for key in ("page_class", "trip_product", "route_bound", "visible_price_text"):
            if key in vlm_result:
                out[key] = vlm_result.get(key)
        checked = plugin.validate_vlm_candidate(out, html, ctx)
        return checked if isinstance(checked, dict) else None

    def _maybe_vision_price_assist():
        """Stage-C vision assist gated by accepted route binding after LLM miss."""
        nonlocal vision_assist_attempted, vlm_attempted
        if vision_assist_attempted:
            return None
        vision_assist_attempted = True
        if not bool(vision_price_assist_enabled):
            return None
        if not _is_google_flights_site(site):
            return None
        if not (origin and dest and depart):
            return None
        if not isinstance(screenshot_path, str) or not screenshot_path.strip():
            return None
        verdict = _get_route_bind_verdict_cached()
        if not isinstance(verdict, dict):
            return None
        support = str(verdict.get("support", "none") or "none").strip().lower()
        route_bound = bool(verdict.get("route_bound"))
        if scenario_route_bind_gate_requires_strong:
            accepted_bound = route_bound and support == "strong"
        else:
            accepted_bound = route_bound or support in {"strong", "weak"}
        if not accepted_bound:
            log.info(
                "vision.extract_assist.route_gated skipped route_bound=%s support=%s reason=insufficient_route_context",
                route_bound,
                support,
            )
            return None

        lang_hint, lang_source = detect_ui_language(html or "", "")
        log.info(
            "vision.language_hint stage=%s site=%s lang=%s source=%s",
            "extract_assist",
            site,
            lang_hint,
            lang_source,
        )

        def _runner():
            try:
                return parse_image_with_vlm(
                    screenshot_path.strip(),
                    site=site,
                    task=task,
                    origin=origin or "",
                    dest=dest or "",
                    depart=depart or "",
                    return_date=return_date or "",
                    html_context=html,
                    locale="",
                    timeout_sec=vlm_timeout_sec,
                    budget=llm_budget,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                return parse_image_with_vlm(
                    screenshot_path.strip(),
                    site=site,
                    task=task,
                    html_context=html,
                    locale="",
                )

        raw, meta = _vision_cached_stage_call(
            cache=vision_stage_cache,
            cooldown=vision_stage_cooldown,
            stage="extract_assist",
            screenshot_path=screenshot_path.strip(),
            runner=_runner,
        )
        # Avoid paying for the same image twice via generic VLM fallback.
        vlm_attempted = True
        normalized = _normalize_vision_extract_assist_result(raw)
        log.info(
            "vision.extract_assist %s",
            {
                "site": site,
                "cached": bool(meta.get("cached", False)),
                "cooldown_skip": bool(meta.get("cooldown_skip", False)),
                "route_support": support,
                "route_bound": route_bound,
                "accepted_bound": accepted_bound,
                "price": normalized.get("price"),
                "currency": normalized.get("currency"),
                "confidence": normalized.get("confidence", "low"),
                "reason": normalized.get("reason", ""),
            },
        )
        if normalized.get("price") is None:
            return None
        candidate = {
            "price": normalized.get("price"),
            "currency": normalized.get("currency"),
            "confidence": normalized.get("confidence", "low"),
            "selector_hint": None,
            "source": "vision_price_assist",
            "reason": normalized.get("reason", "vision_extract_assist"),
            "vision_evidence": normalized.get("evidence", ""),
        }
        candidate.update(_route_bind_fields_from_verdict(verdict))
        checked = plugin.validate_vlm_candidate(candidate, html, ctx)
        return checked if isinstance(checked, dict) else None

    def _maybe_multimodal_extract():
        """Optional multimodal extraction path: screenshot + bounded DOM/code summary."""
        nonlocal multimodal_attempted
        if multimodal_attempted:
            return None
        multimodal_attempted = True
        if multimodal_mode == "off":
            return None
        if not isinstance(screenshot_path, str) or not screenshot_path.strip():
            return None
        judge_mode_enabled = multimodal_mode in {"judge", "judge_primary"}
        multimodal_code_judge_context = None
        if judge_mode_enabled:
            quality_signals = quality_probe.get("signals") if isinstance(quality_probe, dict) else {}
            if not isinstance(quality_signals, dict):
                quality_signals = {}
            multimodal_code_judge_context = {
                "judge_mode": "vlm_plus_code_model",
                "site": site,
                "page_url": page_url or "",
                "llm_mode": llm_mode,
                "task": task,
                "quality_probe": {
                    "quality": str(quality_probe.get("quality", "") or ""),
                    "reason": str(quality_probe.get("reason", "") or ""),
                    "route_alias_hits": int(quality_signals.get("route_alias_hits", 0) or 0),
                    "auth_hits": int(quality_signals.get("auth_hits", 0) or 0),
                    "modal_hits": int(quality_signals.get("modal_hits", 0) or 0),
                    "price_hits": int(quality_signals.get("price_hits", 0) or 0),
                },
            }
            if origin and dest and depart:
                verdict_for_judge = _get_route_bind_verdict_cached()
                if isinstance(verdict_for_judge, dict) and verdict_for_judge:
                    multimodal_code_judge_context["route_bind_verdict"] = {
                        "route_bound": bool(verdict_for_judge.get("route_bound", False)),
                        "support": str(verdict_for_judge.get("support", "none") or "none"),
                        "source": str(verdict_for_judge.get("source", "") or ""),
                        "reason": str(verdict_for_judge.get("reason", "") or ""),
                        "mismatch_fields": list(verdict_for_judge.get("mismatch_fields", []) or [])[:4],
                    }
        try:
            mm_result = parse_page_multimodal_with_vlm(
                image_path=screenshot_path.strip(),
                html=html,
                site=site,
                task=task,
                origin=origin or "",
                dest=dest or "",
                depart=depart or "",
                return_date=return_date or "",
                multimodal_mode=multimodal_mode,
                code_judge_context=multimodal_code_judge_context,
                timeout_sec=multimodal_timeout_sec,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            mm_result = parse_page_multimodal_with_vlm(
                image_path=screenshot_path.strip(),
                html=html,
                site=site,
                task=task,
            )
        if mm_result.get("price") is None:
            return None
        out = {
            "price": mm_result.get("price"),
            "currency": mm_result.get("currency"),
            "confidence": mm_result.get("confidence", "low"),
            "selector_hint": mm_result.get("selector_hint"),
            "source": "vlm_multimodal",
            "reason": mm_result.get("reason", ""),
        }
        for key in ("page_class", "trip_product", "route_bound"):
            if key in mm_result:
                out[key] = mm_result.get(key)
        if judge_mode_enabled and isinstance(out.get("price"), (int, float)):
            verify = assess_vlm_price_candidate_with_llm(
                html,
                site=str(site or ""),
                price=float(out.get("price")),
                currency=str(out.get("currency", "") or ""),
                origin=str(origin or ""),
                dest=str(dest or ""),
                depart=str(depart or ""),
                return_date=str(return_date or ""),
                timeout_sec=int(ctx.get("vlm_llm_price_verify_timeout_sec", 180)),
            )
            accept = verify.get("accept")
            out["multimodal_judge_support"] = verify.get("support", "none")
            out["multimodal_judge_reason"] = verify.get("reason", "")
            out["multimodal_judge_accept"] = accept
            log.info(
                "llm.vlm_multimodal.judge_result site=%s accept=%s support=%s reason=%s",
                site,
                accept,
                verify.get("support", "none"),
                verify.get("reason", ""),
            )
            fail_closed = bool(ctx.get("vlm_llm_price_verify_fail_closed", False))
            if accept is False or (accept == "unknown" and fail_closed):
                return None
        return out

    def _maybe_dom_price_probe():
        """Cheap DOM-first price probe to skip vision paths when route bind is already strong."""
        nonlocal dom_probe_attempted
        if dom_probe_attempted:
            return None
        dom_probe_attempted = True
        if not _is_google_flights_site(site):
            return None
        if not (origin and dest and depart):
            return None
        verdict = _get_route_bind_verdict_cached()
        if not isinstance(verdict, dict):
            return None
        support = str(verdict.get("support", "none") or "none").strip().lower()
        if support != "strong":
            return None
        visible_text = _get_visible_text()
        if not visible_text:
            return None
        max_scan = max(1, int(get_threshold("extract_dom_probe_max_price_candidates", 1200)))
        best_price = None
        best_currency = None
        scanned = 0
        for value, currency in _extract_price_candidates(visible_text):
            scanned += 1
            if scanned > max_scan:
                break
            if not (HEURISTIC_MIN_PRICE <= float(value) <= HEURISTIC_MAX_PRICE):
                continue
            if best_price is None or float(value) < float(best_price):
                best_price = float(value)
                best_currency = currency
        if best_price is None:
            return None
        return {
            "price": best_price,
            "currency": best_currency,
            "confidence": "low",
            "selector_hint": None,
            "source": "heuristic_dom_probe",
            "reason": "dom_visible_price_strong_route_bind",
        }

    def _extract_with_cached_semantic_chunks() -> Optional[Dict[str, Any]]:
        """Heuristic extraction on memoized semantic chunks (no repeated soup parse)."""
        chunks = _get_semantic_chunks_cached()
        if not chunks:
            return None
        best: Optional[Dict[str, Any]] = None
        best_score = -1
        for chunk in chunks:
            chunk_text = str(chunk.get("text", "") or chunk.get("html", ""))
            extracted = _extract_with_heuristics_from_text(
                text=chunk_text,
                site=site,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                page_url=page_url,
            )
            if not extracted and isinstance(chunk.get("html"), str):
                extracted = _extract_with_heuristics(
                    html=chunk.get("html", ""),
                    site=site,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=return_date,
                    page_url=page_url,
                )
            if not extracted:
                continue
            score = int(chunk.get("score", 0))
            if best is None or score > best_score:
                best = dict(extracted)
                best_score = score
            elif score == best_score and best and extracted.get("price") is not None:
                if best.get("price") is None or float(extracted["price"]) < float(best["price"]):
                    best = dict(extracted)
        if not best:
            return None
        best["source"] = "heuristic_chunk"
        best["reason"] = "semantic_chunk_route_match"
        return best

    # ------------------------------------------------------------------
    # Phase 1: runtime quality evaluation and LLM quality-judge override.
    # ------------------------------------------------------------------
    quality_probe = _determine_html_quality(
        html=html,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )
    quality = str(quality_probe.get("quality", "uncertain")).lower()
    llm_quality_used = False
    if (
        quality_gate_enabled
        and light_mode
        and quality == "uncertain"
        and light_try_llm_quality_judge
        and len(html or "") >= 12000
    ):
        judged = assess_html_quality_with_llm(
            html,
            site=site,
            origin=origin or "",
            dest=dest or "",
            depart=depart or "",
            return_date=return_date or "",
            timeout_sec=light_llm_quality_timeout_sec,
        )
        judged_quality = str(judged.get("quality", "")).strip().lower()
        if judged_quality in {"good", "uncertain", "garbage"}:
            quality = judged_quality
            llm_quality_used = True

    def _emit(
        payload: Dict[str, Any],
        *,
        through_scope_guard: bool = False,
        default_scope_guard: str = "skip",
        default_scope_guard_basis: str = "deterministic",
    ) -> Dict[str, Any]:
        """Finalize one output payload with plugin scope guard + additive fields."""
        out = dict(payload or {})
        if (
            str(out.get("source", "") or "").strip().lower() == "cached_selector"
            and _is_google_flights_site(site)
            and origin
            and dest
            and depart
        ):
            verdict = _get_route_bind_verdict_cached()
            if isinstance(verdict, dict) and verdict:
                out.update(_route_bind_fields_from_verdict(verdict))
        if through_scope_guard:
            guarded = plugin.post_candidate_scope_guard(out, html, ctx)
            out = guarded if isinstance(guarded, dict) else out
        out = _apply_google_route_bind_gate(
            out,
            html=html,
            site=site,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            screenshot_path=screenshot_path,
            verdict_getter=_get_route_bind_verdict_cached,
        )
        if llm_quality_used:
            out["quality_judged_by_llm"] = True
        debug_confidence = out.get("debug_confidence")
        if not isinstance(debug_confidence, dict):
            debug_confidence = {}
        debug_confidence.setdefault("plugin", plugin.name)
        out["debug_confidence"] = debug_confidence
        plugin_factors = plugin.extra_confidence_factors(out, ctx)
        if plugin_factors:
            existing = out.get("confidence_factors")
            if not isinstance(existing, list):
                existing = []
            for factor in plugin_factors:
                if factor not in existing:
                    existing.append(factor)
            out["confidence_factors"] = existing
        return _finalize(
            out,
            default_scope_guard=default_scope_guard,
            default_scope_guard_basis=default_scope_guard_basis,
        )

    # ------------------------------------------------------------------
    # Phase 2: plugin pre-guards + deterministic guards + deterministic heuristics.
    # ------------------------------------------------------------------
    pre_guard_out = plugin.pre_guard(html, page_url or "", ctx)
    if isinstance(pre_guard_out, dict):
        return _emit(
            pre_guard_out,
            through_scope_guard=False,
        )

    fast_scope_guard_out = plugin.fast_scope_guard(html, ctx)
    if isinstance(fast_scope_guard_out, dict):
        return _emit(
            fast_scope_guard_out,
            through_scope_guard=False,
            default_scope_guard="fail",
            default_scope_guard_basis="deterministic",
        )

    override_heuristic = plugin.heuristic_extract_overrides(html, ctx)
    if override_heuristic:
        return _emit(override_heuristic, through_scope_guard=True)

    heuristic_result = _extract_with_heuristics(
        html=html,
        site=site,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        page_url=page_url,
    )
    if heuristic_result:
        return _emit(heuristic_result, through_scope_guard=True)

    # ------------------------------------------------------------------
    # Phase 3: optional deterministic semantic-chunk heuristic fallback.
    # ------------------------------------------------------------------
    if chunking_enabled:
        chunk_heuristic = _extract_with_cached_semantic_chunks()
        if chunk_heuristic:
            return _emit(chunk_heuristic, through_scope_guard=True)

    # ------------------------------------------------------------------
    # Phase 4: escalation (multimodal/vlm/llm/chunk-llm) by mode.
    # ------------------------------------------------------------------
    dom_probe_out = _maybe_dom_price_probe()
    if dom_probe_out:
        return _emit(dom_probe_out, through_scope_guard=True)

    if multimodal_mode in {"primary", "judge_primary"}:
        multimodal_out = _maybe_multimodal_extract()
        if multimodal_out:
            return _emit(multimodal_out, through_scope_guard=True)

    if light_mode:
        should_try_llm = light_try_llm_on_miss and quality != "garbage"
        if light_try_vlm_on_miss:
            vlm_out = _maybe_vlm_extract()
            if vlm_out:
                return _emit(vlm_out, through_scope_guard=True)
        if should_try_llm and (_PRICE_PATTERN.search(html or "") or quality == "good"):
            llm_result = _call_parse_with_timeout(light_llm_extract_timeout_sec)
            if llm_result.get("price") is not None:
                out = {
                    "price": llm_result.get("price"),
                    "currency": llm_result.get("currency"),
                    "confidence": llm_result.get("confidence", "low"),
                    "selector_hint": llm_result.get("selector_hint"),
                    "source": "llm_light_escalation",
                    "reason": llm_result.get("reason", ""),
                }
                return _emit(out, through_scope_guard=True)
            if chunking_enabled and not _is_request_failure_reason(
                str(llm_result.get("reason", ""))
            ):
                chunks = _get_semantic_chunks_cached()
                for chunk in chunks[:llm_chunk_attempts]:
                    chunk_result = _call_parse_on_blob(
                        chunk.get("html", ""),
                        llm_light_chunk_timeout_sec,
                    )
                    if chunk_result.get("price") is None:
                        continue
                    out = {
                        "price": chunk_result.get("price"),
                        "currency": chunk_result.get("currency"),
                        "confidence": chunk_result.get("confidence", "low"),
                        "selector_hint": chunk_result.get("selector_hint"),
                        "source": "llm_light_chunk_escalation",
                        "reason": chunk_result.get("reason", ""),
                    }
                    return _emit(out, through_scope_guard=True)
        if multimodal_mode in {"assist", "judge"}:
            multimodal_out = _maybe_multimodal_extract()
            if multimodal_out:
                return _emit(multimodal_out, through_scope_guard=True)
        if light_skip_llm:
            out = {
                "price": None,
                "currency": None,
                "confidence": "low",
                "selector_hint": None,
                "source": "heuristic_html",
                "reason": (
                    "html_quality_garbage"
                    if quality_gate_enabled and quality == "garbage"
                    else "heuristic_no_route_match"
                ),
            }
            return _emit(out)

    llm_result = _call_parse_with_timeout(llm_extract_timeout_sec)

    if llm_result.get("price") is None:
        dom_probe_out = _maybe_dom_price_probe()
        if dom_probe_out:
            return _emit(dom_probe_out, through_scope_guard=True)
        vision_assist_out = _maybe_vision_price_assist()
        if vision_assist_out:
            return _emit(vision_assist_out, through_scope_guard=True)
        vlm_out = _maybe_vlm_extract()
        if vlm_out:
            return _emit(vlm_out, through_scope_guard=True)
        if chunking_enabled and not _is_request_failure_reason(
            str(llm_result.get("reason", ""))
        ):
            chunks = _get_semantic_chunks_cached()
            for chunk in chunks[:llm_chunk_attempts]:
                chunk_result = _call_parse_on_blob(
                    chunk.get("html", ""),
                    llm_chunk_timeout_sec,
                )
                if chunk_result.get("price") is None:
                    continue
                return _emit(
                    {
                        "price": chunk_result.get("price"),
                        "currency": chunk_result.get("currency"),
                        "confidence": chunk_result.get("confidence", "low"),
                        "selector_hint": chunk_result.get("selector_hint"),
                        "source": "llm_chunk",
                        "reason": chunk_result.get("reason", ""),
                    },
                    through_scope_guard=True,
                )
        if multimodal_mode in {"assist", "judge"}:
            multimodal_out = _maybe_multimodal_extract()
            if multimodal_out:
                return _emit(multimodal_out, through_scope_guard=True)

    # ------------------------------------------------------------------
    # Phase 5: final payload assembly and additive normalization.
    # ------------------------------------------------------------------
    out = {
        "price": llm_result.get("price"),
        "currency": llm_result.get("currency"),
        "confidence": llm_result.get("confidence", "low"),
        "selector_hint": llm_result.get("selector_hint"),
        "source": llm_result.get("source", "llm"),
        "reason": (
            llm_result.get("reason", "")
            if llm_result.get("reason", "")
            else (
                "html_quality_garbage"
                if quality_gate_enabled and quality == "garbage"
                else ""
            )
        ),
    }
    return _emit(out, through_scope_guard=True)


def _build_plugin_router_scope_guard_fn(
    *,
    html: str,
    site: str,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    screenshot_path: Optional[str],
    page_url: Optional[str],
):
    """Build one callable that applies existing post-candidate scope guard exactly once."""
    llm_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    if llm_mode not in {"full", "light"}:
        llm_mode = "full"
    plugin = _plugin_for_site(site)
    ctx: Dict[str, Any] = {
        "site": site,
        "origin": origin,
        "dest": dest,
        "depart": depart,
        "return_date": return_date,
        "screenshot_path": screenshot_path,
        "page_url": page_url,
        "light_mode": llm_mode == "light",
        "vlm_scope_guard_enabled": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_VLM_SCOPE_GUARD_ENABLED",
            bool(get_threshold("extract_vlm_scope_guard_enabled", True)),
        ),
        "vlm_scope_guard_timeout_sec": _env_int(
            "FLIGHT_WATCHER_EXTRACT_VLM_SCOPE_GUARD_TIMEOUT_SEC",
            int(get_threshold("extract_vlm_scope_guard_timeout_sec", 120)),
        ),
        "vlm_scope_guard_max_variants": max(
            1,
            _env_int(
                "FLIGHT_WATCHER_EXTRACT_VLM_SCOPE_GUARD_MAX_VARIANTS",
                int(get_threshold("extract_vlm_scope_guard_max_variants", 1)),
            ),
        ),
        "vlm_scope_guard_fail_closed": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_VLM_SCOPE_GUARD_FAIL_CLOSED",
            bool(get_threshold("extract_vlm_scope_guard_fail_closed", False)),
        ),
        "llm_scope_guard_enabled": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_LLM_SCOPE_GUARD_ENABLED",
            bool(get_threshold("extract_llm_scope_guard_enabled", True)),
        ),
        "llm_scope_guard_timeout_sec": _env_int(
            "FLIGHT_WATCHER_EXTRACT_LLM_SCOPE_GUARD_TIMEOUT_SEC",
            int(get_threshold("extract_llm_scope_guard_timeout_sec", 120)),
        ),
        "llm_scope_guard_fail_closed": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_LLM_SCOPE_GUARD_FAIL_CLOSED",
            bool(get_threshold("extract_llm_scope_guard_fail_closed", False)),
        ),
        "google_non_flight_fast_guard_enabled": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_GOOGLE_NON_FLIGHT_FAST_GUARD",
            bool(get_threshold("extract_google_non_flight_fast_guard", True)),
        ),
        "google_require_route_context": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_GOOGLE_REQUIRE_ROUTE_CONTEXT",
            bool(get_threshold("extract_google_require_route_context", True)),
        ),
        "vlm_llm_price_verify_enabled": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_ENABLED",
            bool(get_threshold("extract_vlm_llm_price_verify_enabled", True)),
        ),
        "vlm_llm_price_verify_timeout_sec": _env_int(
            "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_TIMEOUT_SEC",
            int(get_threshold("extract_vlm_llm_price_verify_timeout_sec", 180)),
        ),
        "vlm_llm_price_verify_fail_closed": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_VLM_LLM_PRICE_VERIFY_FAIL_CLOSED",
            bool(get_threshold("extract_vlm_llm_price_verify_fail_closed", False)),
        ),
        "vlm_price_grounding_required_on_conflict": _env_bool(
            "FLIGHT_WATCHER_EXTRACT_VLM_PRICE_GROUNDING_REQUIRED_ON_CONFLICT",
            bool(get_threshold("extract_vlm_price_grounding_required_on_conflict", True)),
        ),
        "price_grounded_in_html_checker": (
            lambda price, currency: _google_price_is_grounded_in_html(
                html,
                price=price,
                currency=currency,
            )
        ),
        "_plugin_cache": {},
    }

    def _scope_guard(candidate: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return plugin.post_candidate_scope_guard(candidate, html, ctx)
        except Exception:
            return {}

    return _scope_guard


def extract_price(
    html: str,
    site: str = "unknown",
    task: str = "price",
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Top-level extractor used by current runtime paths."""
    site_key = str(site or "").strip().lower()
    llm_mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    if llm_mode not in {"full", "light"}:
        llm_mode = "full"

    extract_cap_sec = int(get_threshold("extract_wall_clock_cap_sec", 0))
    extract_started_at = time.monotonic()

    def _extract_wall_cap_hit() -> bool:
        if extract_cap_sec <= 0:
            return False
        return (time.monotonic() - extract_started_at) >= float(extract_cap_sec)

    if _extract_wall_cap_hit():
        return _extract_wall_clock_cap_payload()

    # Skyscanner deterministic-first extraction: avoid expensive model escalation
    # when a straightforward service parser can already read the current results page.
    if site_key == "skyscanner":
        try:
            deterministic = extract_skyscanner_price_from_html(html, page_url=page_url)
        except Exception:
            deterministic = {}
        if (
            isinstance(deterministic, dict)
            and bool(deterministic.get("ok"))
            and deterministic.get("price") is not None
        ):
            payload = {
                "price": deterministic.get("price"),
                "currency": deterministic.get("currency"),
                "confidence": "medium",
                "selector_hint": None,
                "source": "heuristic_skyscanner_service",
                "reason": "skyscanner_service_parser",
                "page_kind": deterministic.get("page_kind", "unknown"),
                "extraction_strategy": deterministic.get("extraction_strategy"),
                "evidence": deterministic.get("evidence") if isinstance(deterministic.get("evidence"), dict) else {},
            }
            return _normalize_extractor_output(
                payload,
                llm_mode=llm_mode,
                default_scope_guard="pass",
                default_scope_guard_basis="deterministic",
            )

    plugin_strategy_on = plugin_strategy_enabled()
    plugin_extract_router_enabled = _env_bool(
        "FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED",
        True,
    )
    if plugin_strategy_on and plugin_extract_router_enabled and site_key == "skyscanner":
        # The current plugin-router default (`html_llm`) is costly on script-heavy
        # Skyscanner pages and often duplicates later extraction stages.
        plugin_extract_router_enabled = False
        log.info("extractor.skyscanner.skip_plugin_router reason=deterministic_first")
    if plugin_strategy_on and plugin_extract_router_enabled:
        plugin_out = run_plugin_extraction_router(
            html=html,
            site=site,
            task=task,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            trip_type=None,
            is_domestic=None,
            screenshot_path=screenshot_path,
            page_url=page_url,
            existing_scope_guard_fn=_build_plugin_router_scope_guard_fn(
                html=html,
                site=site,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                screenshot_path=screenshot_path,
                page_url=page_url,
            ),
            thresholds_getter=get_threshold,
            finalize_output_fn=lambda payload: _normalize_extractor_output(
                payload,
                llm_mode=llm_mode,
                default_scope_guard="skip",
                default_scope_guard_basis="deterministic",
            ),
        )
        if isinstance(plugin_out, dict) and plugin_out:
            gated_plugin_out = _apply_google_route_bind_gate(
                plugin_out,
                html=html,
                site=site,
                origin=origin,
                dest=dest,
                depart=depart,
                return_date=return_date,
                screenshot_path=screenshot_path,
            )
            if isinstance(gated_plugin_out, dict) and gated_plugin_out.get("price") is not None:
                return gated_plugin_out
        if _extract_wall_cap_hit():
            return _extract_wall_clock_cap_payload()

    # Phase 5: Coordination layer gate evaluation (early exit if gated)
    coordination_enabled = _env_bool(
        "FLIGHT_WATCHER_COORDINATION_ENABLED",
        bool(get_threshold("coordination_enabled", False)),
    )
    if coordination_enabled:
        # Initialize observer for this extraction
        observer = ExtractionObserver()
        observer.on_extraction_start(f"extract_price_{id(html)}")

        # Evaluate coordination gates before calling extract_with_llm
        # For now, we'll proceed with all extractions, but gates can be added here
        # Example gate: check route binding verdict if available
        observer.on_gate_evaluation("coordination_enabled", passed=True)

    out = extract_with_llm(
        html=html,
        site=site,
        task=task,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        screenshot_path=screenshot_path,
        page_url=page_url,
    )

    # Coordination mode keeps source taxonomy aligned with plugin-router labeling.
    if coordination_enabled and isinstance(out, dict):
        source = str(out.get("source", "")).strip()
        if source == "llm":
            out["source"] = "plugin_html_llm"

    # Phase 5: Log coordination metrics if enabled
    if coordination_enabled:
        observer.on_extraction_complete(
            gates_passed=["coordination_enabled"],
            llm_called=out.get("source") in ("llm", "llm_chunk", "plugin_html_llm"),
            price_extracted=out.get("price") is not None,
            price_value=out.get("price"),
        )
        metrics = observer.get_metrics()
        summary = metrics.get_summary()
        log.info(
            "coordination.extraction_metric total_extractions=%d gating_rate=%.2f llm_call_rate=%.2f",
            summary.get("total_extractions", 1),
            summary.get("gating_rate", 0.0),
            summary.get("llm_call_rate", 0.0),
        )

    if _extract_wall_cap_hit():
        return _extract_wall_clock_cap_payload()
    return out
