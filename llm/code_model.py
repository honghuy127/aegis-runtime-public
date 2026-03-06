"""LLM-facing adapters for extraction, scenario planning, and plan repair."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from llm.prompts import (
    HTML_QUALITY_PROMPT,
    LLM_TRIP_PRODUCT_GUARD_PROMPT,
    PRICE_EXTRACTION_PROMPT,
    VLM_MULTIMODAL_EXTRACTION_PROMPT,
    VLM_PRICE_EXTRACTION_PROMPT,
    VLM_PRICE_VERIFICATION_PROMPT,
    VLM_FILL_ROI_PROMPT,
    VLM_ROI_VALUE_PROMPT,
    VLM_UI_ASSIST_PROMPT,
    SCENARIO_PROMPT,
    REPAIR_PROMPT,
)
from llm.prompts.registry import (
    PROMPT_HTML_QUALITY,
    PROMPT_LLM_TRIP_PRODUCT_GUARD,
    get_prompt,
)
from llm.prompts.validate import validate_prompt_output
from llm.vlm import image_preprocess as _vlm_image_preprocess
from llm.html_compaction import (
    compact_html_for_prompt as _compact_html_for_prompt_impl,
    semantic_html_chunks_for_prompt as _semantic_html_chunks_for_prompt_impl,
)
from llm.json_parsing import (
    coerce_price_payload_from_raw as _coerce_price_payload_from_raw_impl,
    parse_json_from_raw as _parse_json_from_raw_impl,
)
from llm.language_signals import (
    detect_page_language as _detect_page_language_impl,
    detect_ui_language as _detect_ui_language_impl,
    expected_language_from_locale as _expected_language_from_locale_impl,
    page_signal_scores as _page_signal_scores_impl,
)
from llm.thresholds_helpers import (
    llm_mode as _llm_mode_impl,
    llm_runtime_options as _llm_runtime_options_impl,
    threshold_bool as _threshold_bool_impl,
    threshold_float as _threshold_float_impl,
    threshold_int as _threshold_int_impl,
)
from llm.llm_client import call_llm, reset_llm_circuit_state
from llm.selector_quality import classify_selector_stability
from llm.attempt_policy import LLMCallBudget, AttemptDecider, load_llm_budget_from_config
from storage.shared_knowledge_store import get_airport_aliases
from utils.logging import get_logger
from utils.thresholds import get_threshold


log = get_logger(__name__)
MODELS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "models.yaml"
REQUIRED_MODEL_KEYS = ("planner", "coder", "vision")


def _load_models_config() -> Dict[str, str]:
    """Load required model names from configs/models.yaml by key."""
    if not MODELS_CONFIG_PATH.exists():
        raise RuntimeError(f"Missing model config file: {MODELS_CONFIG_PATH}")

    models: Dict[str, str] = {}

    try:
        for raw_line in MODELS_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key in REQUIRED_MODEL_KEYS and value:
                models[key] = value
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read model config at {MODELS_CONFIG_PATH}: {exc}"
        ) from exc

    missing = [k for k in REQUIRED_MODEL_KEYS if not models.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required model keys in {MODELS_CONFIG_PATH}: {missing}"
        )
    return models


_MODELS = _load_models_config()
PLANNER_MODEL = _MODELS["planner"]
CODER_MODEL = _MODELS["coder"]
VISION_MODEL = _MODELS["vision"]
MAX_HTML_PROMPT_CHARS = 18000
MAX_SCENARIO_HTML_PROMPT_CHARS = 22000
_PRICE_TOKEN_RE = re.compile(
    r"(?:¥\s*\d[\d,]*|\$\s*\d[\d,]*|€\s*\d[\d,]*|£\s*\d[\d,]*|"
    r"JPY\s*\d[\d,]*|USD\s*\d[\d,]*|EUR\s*\d[\d,]*|GBP\s*\d[\d,]*)",
    re.IGNORECASE,
)
_ROUTE_HINT_RE = re.compile(
    r"\b(where from|where to|from|to|departure|depart|return|round trip|one way|nonstop)\b",
    re.IGNORECASE,
)
_JA_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_AUTH_HINT_RE = re.compile(
    r"(?:email|password|login|sign[\s_-]*in|sign[\s_-]*up|register|account|"
    r"newsletter|subscribe|member|メール|会員|ログイン|パスワード|氏名|お名前|電話)",
    re.IGNORECASE,
)
_MODAL_HINT_RE = re.compile(
    r"(?:cookie|consent|accept|agree|close|dismiss|×|閉じる|同意)",
    re.IGNORECASE,
)
_NOTE_SANITIZE_RE = re.compile(r"[`\r\n\t]+")


def _sanitize_short_note(raw: Any, *, max_chars: int = 180) -> str:
    """Normalize optional free-form planner note to short single-line text."""
    if not isinstance(raw, str):
        return ""
    text = _NOTE_SANITIZE_RE.sub(" ", raw).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def _compact_hint_dict(raw: Dict[str, Any], *, max_chars: int = 320) -> str:
    """Serialize a compact one-line hint payload for planner context fields."""
    if not isinstance(raw, dict) or not raw:
        return ""
    parts: List[str] = []
    for key in (
        "page_scope",
        "page_class",
        "trip_product",
        "blocked_by_modal",
        "reason",
    ):
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None:
            continue
        text = _sanitize_short_note(str(value), max_chars=96)
        if not text:
            continue
        parts.append(f"{key}={text}")
    fill_labels = raw.get("fill_labels")
    if isinstance(fill_labels, dict):
        for role in ("origin", "dest", "depart", "return", "search"):
            labels = fill_labels.get(role)
            if not isinstance(labels, list) or not labels:
                continue
            joined = ",".join(str(item).strip() for item in labels[:2] if str(item).strip())
            joined = _sanitize_short_note(joined, max_chars=42)
            if joined:
                parts.append(f"{role}=[{joined}]")
    target_regions = raw.get("target_regions")
    if isinstance(target_regions, dict):
        region_parts: List[str] = []
        for key in ("origin", "dest", "depart", "return", "search", "modal_close"):
            bbox = target_regions.get(key)
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                continue
            try:
                x, y, w, h = [float(v) for v in bbox]
            except Exception:
                continue
            region_parts.append(f"{key}@{x:.2f},{y:.2f},{w:.2f},{h:.2f}")
            if len(region_parts) >= 4:
                break
        if region_parts:
            parts.append("regions=" + ",".join(region_parts))
    merged = " ; ".join(parts)
    if len(merged) > max_chars:
        merged = merged[: max_chars - 3].rstrip() + "..."
    return merged


def _compact_prompt_json_blob(
    raw: Any,
    *,
    max_chars: int = 900,
    max_depth: int = 2,
    max_items: int = 8,
) -> str:
    """Serialize prompt context as bounded JSON for multimodal hints/judging."""
    if raw is None:
        return ""

    def _trim(value: Any, depth: int) -> Any:
        if depth < 0:
            return "..."
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for idx, (key, inner) in enumerate(value.items()):
                if idx >= max_items:
                    out["_truncated"] = True
                    break
                out[str(key)[:64]] = _trim(inner, depth - 1)
            return out
        if isinstance(value, (list, tuple)):
            items = [_trim(item, depth - 1) for item in list(value)[:max_items]]
            if len(value) > max_items:
                items.append("...")
            return items
        if isinstance(value, str):
            return _sanitize_short_note(value, max_chars=220)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _sanitize_short_note(str(value), max_chars=120)

    try:
        text = json.dumps(_trim(raw, max_depth), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = _sanitize_short_note(str(raw), max_chars=max_chars)
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def _classify_llm_error(exc: Exception) -> str:
    """Classify LLM request failures for clearer runtime diagnostics."""
    text = str(exc).lower()
    if "token_cap" in text or "done_reason=length" in text:
        return "token_cap"
    if "timeout" in text or "readtimeout" in text or "timeout_budget_exhausted" in text:
        return "timeout"
    if "empty_output" in text:
        return "empty_output"
    if "circuit_open" in text:
        return "circuit_open"
    if "httperror" in text or "500" in text:
        return "server_error"
    return "unknown"


def _reason_category(reason: str) -> str:
    """Extract error category from standardized request-failed reason."""
    text = str(reason or "").strip().lower()
    prefix = "llm_request_failed_"
    if text.startswith(prefix):
        return text[len(prefix):] or "unknown"
    return ""


def _llm_mode() -> str:
    """Resolve LLM runtime mode (full|light), defaulting to full."""
    return _llm_mode_impl()


def _threshold_int(key: str, default: int) -> int:
    """Read integer threshold with safe fallback."""
    return _threshold_int_impl(key, default)


def _threshold_float(key: str, default: float) -> float:
    """Read float threshold with safe fallback."""
    return _threshold_float_impl(key, default)


def _threshold_bool(key: str, default: bool) -> bool:
    """Read bool threshold with safe fallback."""
    return _threshold_bool_impl(key, default)


def _llm_runtime_options(kind: str) -> Dict[str, float]:
    """Return num_ctx/num_predict/temperature for current LLM mode."""
    return _llm_runtime_options_impl(kind)


def _resolve_vision_model() -> str:
    """Resolve VLM model with env override and config fallback."""
    env_model = (os.getenv("FLIGHT_WATCHER_VLM_MODEL") or "").strip()
    if env_model:
        return env_model
    return VISION_MODEL


def _expected_language_from_locale(mimic_locale: Optional[str]) -> str:
    """Derive language code from locale hint (e.g., ja-JP -> ja)."""
    return _expected_language_from_locale_impl(mimic_locale)


def _detect_page_language(html: str) -> str:
    """Detect rough page language from visible text signal."""
    return _detect_page_language_impl(html)


def _detect_ui_language(html: str, mimic_locale: Optional[str]) -> Tuple[str, str]:
    """Detect UI language hint from HTML and locale."""
    return _detect_ui_language_impl(html, mimic_locale)


def build_ui_language_hint_block(
    *,
    html: str,
    mimic_locale: str,
    site: str,
) -> Tuple[str, str, str]:
    """Build UI language hint instruction block and return (block, lang, source)."""
    lang_hint, source = _detect_ui_language(html, mimic_locale)
    if lang_hint != "unknown":
        block = (
            f"UI_LANGUAGE_HINT: {lang_hint}.\n"
            "Prefer reading labels and values in this language first.\n"
            "Do not assume Chinese when the UI is Japanese; only treat as Chinese if clearly Chinese UI text is present."
        )
    else:
        block = (
            "UI_LANGUAGE_HINT: unknown.\n"
            "Read the UI language as-is without guessing; rely on clear numeric and structural evidence."
        )

    if site in {"google_flights", "skyscanner"}:
        block += (
            "\nSITE_RULE: Google Flights/Skyscanner often contains bilingual UI.\n"
            "If both English and local-language labels appear,\n"
            "prefer interpreting English labels for:\n"
            "- airport names\n"
            "- route chips\n"
            "- date fields\n"
            "- price labels\n"
            "Do NOT translate or reinterpret numbers--only prefer clearer labels."
        )
    return block, lang_hint, source


def _page_signal_scores(html: str) -> Dict[str, int]:
    """Compute simple signal counts to steer planner away from auth forms."""
    return _page_signal_scores_impl(html)


def _parse_json_from_raw(raw: str) -> Optional[Any]:
    """Parse JSON from raw model output, tolerating noisy wrappers."""
    return _parse_json_from_raw_impl(raw)


def _coerce_price_payload_from_raw(raw: str) -> Optional[Dict[str, Any]]:
    """Recover a minimal price payload from imperfect model output."""
    return _coerce_price_payload_from_raw_impl(raw)


def _normalize_selector_hint(selector_hint: Any) -> Optional[Dict[str, Any]]:
    """Normalize selector hint with additive stability classification."""
    if not isinstance(selector_hint, dict):
        return None
    out = dict(selector_hint)
    if not _threshold_bool("extract_selector_stability_normalize_enabled", True):
        return out
    css = out.get("css")
    if isinstance(css, str) and css.strip():
        out["stability"] = classify_selector_stability(css)
    return out


def _compact_html_for_prompt(html: str, max_chars: int = MAX_HTML_PROMPT_CHARS) -> str:
    """Strip noisy tags and prefer price/result-focused snippets for prompt input."""
    return _compact_html_for_prompt_impl(html, max_chars=max_chars)


def _semantic_html_chunks_for_prompt(
    html: str,
    *,
    max_chunks: int = 3,
    chunk_chars: int = 4200,
    max_total_chars: int = 12000,
) -> List[Dict[str, Any]]:
    """Build scored semantic DOM chunks to augment planner/repair prompts."""
    return _semantic_html_chunks_for_prompt_impl(
        html,
        max_chunks=max_chunks,
        chunk_chars=chunk_chars,
        max_total_chars=max_total_chars,
    )


def _format_semantic_chunks_for_prompt(chunks: List[Dict[str, Any]]) -> str:
    """Render semantic chunks into a bounded prompt section."""
    if not isinstance(chunks, list) or not chunks:
        return ""
    lines: List[str] = []
    for idx, item in enumerate(chunks[:4]):
        if not isinstance(item, dict):
            continue
        html_chunk = str(item.get("html") or "").strip()
        if not html_chunk:
            continue
        score = int(item.get("score", 0))
        reason = _sanitize_short_note(item.get("reason", ""), max_chars=120)
        tag = _sanitize_short_note(item.get("tag", ""), max_chars=40)
        lines.append(f"CHUNK[{idx + 1}] score={score} tag={tag} reason={reason}")
        lines.append(html_chunk)
    return "\n".join(lines).strip()


def _encode_image_base64(image_path: str) -> Optional[str]:
    """Read image and return base64 payload for multimodal Ollama requests."""
    return _vlm_image_preprocess._encode_image_base64(image_path)


def _sips_binary() -> Optional[str]:
    """Return path to macOS `sips` binary when available."""
    return _vlm_image_preprocess._sips_binary()


def _sips_dimensions(sips_bin: str, image_path: str) -> Optional[Tuple[int, int]]:
    """Read image width/height through `sips` metadata."""
    return _vlm_image_preprocess._sips_dimensions(sips_bin, image_path)


def _sips_make_variant(
    sips_bin: str,
    src_path: str,
    out_path: str,
    *,
    max_side: int,
    jpeg_quality: int,
    crop_h: Optional[int] = None,
    crop_w: Optional[int] = None,
    offset_y: int = 0,
    offset_x: int = 0,
) -> bool:
    """Create one resized/cropped JPEG variant using `sips`."""
    return _vlm_image_preprocess._sips_make_variant(
        sips_bin,
        src_path,
        out_path,
        max_side=max_side,
        jpeg_quality=jpeg_quality,
        crop_h=crop_h,
        crop_w=crop_w,
        offset_y=offset_y,
        offset_x=offset_x,
    )


def _encode_image_base64_variants(image_path: str, *, profile: str = "default") -> List[str]:
    """Build compact image variants (downsample/crops) and return base64 payloads."""
    return _vlm_image_preprocess._encode_image_base64_variants(
        image_path,
        profile=profile,
        threshold_bool_fn=_threshold_bool,
        threshold_int_fn=_threshold_int,
        threshold_float_fn=_threshold_float,
        sips_binary_fn=_sips_binary,
        sips_dimensions_fn=_sips_dimensions,
        sips_make_variant_fn=_sips_make_variant,
        logger=log,
    )


def _vlm_attempt_timeouts(total_timeout_sec: int, attempts: int) -> List[int]:
    """Split one timeout budget into bounded per-attempt timeouts."""
    attempts = max(1, int(attempts))
    total = max(1, int(total_timeout_sec))
    min_attempt = max(10, _threshold_int("vlm_attempt_timeout_min_sec", 90))
    max_attempt = max(min_attempt, _threshold_int("vlm_attempt_timeout_max_sec", 240))
    # Respect caller's total budget first; only enforce min when budget allows it.
    share = max(1, int(total / attempts))
    if share >= min_attempt:
        base = min(share, max_attempt)
    else:
        base = share
    return [base for _ in range(attempts)]


def _effective_vlm_endpoint_policy(
    requested_policy: str,
    *,
    per_attempt_timeout_sec: int,
) -> str:
    """Pick a safe endpoint policy for VLM under constrained per-attempt budgets."""
    policy = (requested_policy or "").strip().lower()
    if policy not in {"generate_only", "prefer_generate", "auto", "chat_only"}:
        policy = "generate_only"
    if policy == "chat_only":
        return policy
    dual_endpoint_min_timeout = max(
        30,
        _threshold_int("vlm_dual_endpoint_min_timeout_sec", 180),
    )
    if policy in {"prefer_generate", "auto"} and int(per_attempt_timeout_sec) < dual_endpoint_min_timeout:
        log.info(
            "llm.vlm_endpoint_policy.adjusted requested=%s effective=generate_only per_attempt_timeout_sec=%s min_dual_endpoint_timeout_sec=%s",
            policy,
            per_attempt_timeout_sec,
            dual_endpoint_min_timeout,
        )
        return "generate_only"
    return policy


def _clamp01(value: float) -> float:
    """Clamp numeric value into [0.0, 1.0]."""
    return _vlm_image_preprocess._clamp01(value)


def _normalize_roi_bbox(raw: Any) -> Optional[Tuple[float, float, float, float]]:
    """Normalize ROI bbox as (x,y,w,h) in [0,1] coordinates."""
    return _vlm_image_preprocess._normalize_roi_bbox(raw)


def _crop_roi_base64(
    image_path: str,
    bbox: Tuple[float, float, float, float],
) -> Optional[str]:
    """Crop one normalized ROI from original image and return base64 JPEG payload."""
    return _vlm_image_preprocess._crop_roi_base64(
        image_path,
        bbox,
        threshold_float_fn=_threshold_float,
        threshold_int_fn=_threshold_int,
        sips_binary_fn=_sips_binary,
        sips_dimensions_fn=_sips_dimensions,
        sips_make_variant_fn=_sips_make_variant,
        encode_image_base64_fn=_encode_image_base64,
    )


def _date_match_tokens(value: str) -> List[str]:
    """Generate common date render variants for matching UI field text."""
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return [text] if text else []
    year, month, day = text.split("-")
    m = str(int(month))
    d = str(int(day))
    mm = f"{int(month):02d}"
    dd = f"{int(day):02d}"
    return [
        text,
        f"{year}/{mm}/{dd}",
        f"{mm}/{dd}",
        f"{m}/{d}",
        f"{m}月{d}日",
        f"{mm}月{dd}日",
        f"{month}-{day}",
        f"{m}-{d}",
    ]


def _contains_token_ci(text: str, token: str) -> bool:
    """Case-insensitive token check with boundary handling for ASCII tokens."""
    if not token or not text:
        return False
    if token.isascii():
        upper_blob = text.upper()
        needle = token.upper()
        if re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", upper_blob):
            return True
        return len(needle) >= 5 and needle in upper_blob
    return token in text


def _value_matches_expected(role: str, observed: str, expected: str) -> bool:
    """Role-aware matching for field verification."""
    if not expected:
        return True
    blob = (observed or "").strip()
    if not blob:
        return False
    role_key = (role or "").strip().lower()
    if role_key in {"origin", "dest"}:
        aliases = set(get_airport_aliases(expected))
        aliases.add((expected or "").strip().upper())
        for alias in aliases:
            if _contains_token_ci(blob, alias):
                return True
        return False
    if role_key in {"depart", "return"}:
        for token in _date_match_tokens(expected):
            if _contains_token_ci(blob, token):
                return True
        return False
    return _contains_token_ci(blob, expected)


def analyze_filled_route_with_vlm(
    image_path: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    trip_type: str = "one_way",
    html_context: str = "",
    locale: str = "",
    timeout_sec: Optional[int] = None,
    budget: Optional[LLMCallBudget] = None,
) -> Dict[str, Any]:
    """Verify if key route/date fields appear filled correctly using VLM + ROI crops.

    Args:
        budget: Optional LLMCallBudget for gated retry management.
                If provided and insufficient time remains, returns empty dict.
    """
    image_payloads = _encode_image_base64_variants(image_path)
    if not image_payloads:
        return {}

    # NOTE: Budget gating deferred to extraction phase (budget integration incomplete).
    # TODO(phase-2): implement AttemptDecider-based gating when budget contract stabilizes.
    if budget is not None:
        log.debug(
            "llm.vlm_fill_verify.budget_present budget_remaining_s=%.1f (gating deferred)",
            budget.remaining_s,
        )

    base_payload = image_payloads[0]

    total_timeout = (
        int(timeout_sec)
        if timeout_sec is not None
        else _threshold_int("scenario_vlm_fill_verify_timeout_sec", 240)
    )

    # Cap timeout to remaining budget if budget provided
    if budget is not None:
        decider = AttemptDecider()
        budget_timeout = decider.next_timeout_s("vlm_fill_verify", "vision", budget)
        total_timeout = min(total_timeout, int(budget_timeout))
        log.info("llm.vlm_fill_verify.timeout_capped budget_timeout_s=%.1f final_timeout_s=%d", budget_timeout, total_timeout)

    vlm_model = _resolve_vision_model()
    locate_timeout = max(20, int(total_timeout * 0.45))
    per_field_timeout = max(10, int(total_timeout * 0.12))

    opts = _llm_runtime_options("coder")
    vlm_endpoint_policy = str(
        get_threshold(
            "vlm_fill_verify_endpoint_policy",
            get_threshold("vlm_endpoint_policy", "generate_only"),
        )
    ).strip().lower() or "generate_only"
    effective_endpoint_policy = _effective_vlm_endpoint_policy(
        vlm_endpoint_policy,
        per_attempt_timeout_sec=locate_timeout,
    )
    vlm_strict_json = _threshold_bool("vlm_strict_json", False)
    vlm_think = _threshold_bool(
        "vlm_fill_verify_think",
        _threshold_bool("vlm_think", False),
    )
    locate_num_predict = max(
        384,
        _threshold_int("vlm_fill_verify_locate_num_predict", 1024),
    )
    read_num_predict = max(
        128,
        _threshold_int("vlm_fill_verify_read_num_predict", 320),
    )
    crop_enabled = _threshold_bool("vlm_fill_verify_roi_crop_enabled", True)

    lang_block, lang_hint, lang_source = build_ui_language_hint_block(
        html=html_context,
        mimic_locale=locale,
        site=site,
    )
    log.info(
        "vision.language_hint stage=%s site=%s lang=%s source=%s",
        "fill_verify",
        site,
        lang_hint,
        lang_source,
    )
    context = (
        f"Site: {site}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
        f"TripType: {trip_type}\n"
    )
    prompt = VLM_FILL_ROI_PROMPT + "\n\n" + lang_block + "\n\n" + context

    try:
        raw = call_llm(
            prompt,
            model=vlm_model,
            think=vlm_think,
            json_mode=True,
            timeout_sec=locate_timeout,
            num_ctx=int(opts["num_ctx"]),
            num_predict=locate_num_predict,
            temperature=float(opts["temperature"]),
            images=[base_payload],
            endpoint_policy=effective_endpoint_policy,
            strict_json=vlm_strict_json,
            fail_fast_on_timeout=True,
        )
    except Exception as exc:
        log.warning("llm.vlm_fill_verify.locate_failed category=%s error=%s", _classify_llm_error(exc), exc)
        return {}

    parsed = _parse_json_from_raw(raw)
    if not isinstance(parsed, dict):
        log.warning("llm.vlm_fill_verify.locate_parse_failed raw_head=%s", str(raw)[:500])
        return {}

    expected_fields = {
        "origin": (origin or "").strip(),
        "dest": (dest or "").strip(),
        "depart": (depart or "").strip(),
    }
    if trip_type == "round_trip" and (return_date or "").strip():
        expected_fields["return"] = (return_date or "").strip()

    fields: Dict[str, Dict[str, Any]] = {}
    all_required_matched = True
    for role, expected_value in expected_fields.items():
        payload = parsed.get(role) if isinstance(parsed.get(role), dict) else {}
        visible_text = str(payload.get("visible_text", "") or "").strip()
        bbox = _normalize_roi_bbox(payload.get("bbox"))
        observed = visible_text
        source = "locate_visible_text"

        if crop_enabled and bbox is not None:
            roi_payload = _crop_roi_base64(image_path, bbox)
            if roi_payload:
                roi_prompt = (
                    VLM_ROI_VALUE_PROMPT
                    + "\n\n"
                    + lang_block
                    + "\n\n"
                    + f"Role: {role}\nExpectedValue: {expected_value}\nSite: {site}\n"
                )
                try:
                    roi_raw = call_llm(
                        roi_prompt,
                        model=vlm_model,
                        think=vlm_think,
                        json_mode=True,
                        timeout_sec=per_field_timeout,
                        num_ctx=int(opts["num_ctx"]),
                        num_predict=read_num_predict,
                        temperature=float(opts["temperature"]),
                        images=[roi_payload],
                        endpoint_policy=effective_endpoint_policy,
                        strict_json=vlm_strict_json,
                        fail_fast_on_timeout=True,
                    )
                    roi_parsed = _parse_json_from_raw(roi_raw)
                    if isinstance(roi_parsed, dict):
                        value = str(roi_parsed.get("value", "") or "").strip()
                        if value:
                            observed = value
                            source = "roi_read"
                except Exception as exc:
                    log.warning(
                        "llm.vlm_fill_verify.roi_read_failed role=%s category=%s error=%s",
                        role,
                        _classify_llm_error(exc),
                        exc,
                    )

        matched = _value_matches_expected(role, observed, expected_value)
        fields[role] = {
            "expected": expected_value,
            "observed": observed,
            "matched": bool(matched),
            "source": source,
            "bbox": list(bbox) if bbox is not None else None,
        }
        all_required_matched = all_required_matched and bool(matched)

    out = {
        "route_bound": bool(all_required_matched),
        "fields": fields,
        "reason": _sanitize_short_note(parsed.get("reason", ""), max_chars=160),
    }

    # Track attempt and add evidence if budget provided
    if budget is not None:
        budget.mark_attempt("vlm_fill_verify")
        out["llm_budget_remaining_s"] = budget.remaining_s
        out["llm_attempt_index"] = budget.attempt_count.get("vlm_fill_verify", 0)

    return out


def _extract_price_with_vlm_once(
    image_path: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    html_context: str = "",
    locale: str = "",
    timeout_sec: Optional[int] = None,
    variant_profile: str = "default",
    budget: Optional[LLMCallBudget] = None,
):
    """Ask a vision model to parse a flight price from screenshot image.

    Args:
        budget: Optional LLMCallBudget for gated retry management.
                If provided and insufficient time remains or max attempts reached, returns None.
    """
    # NOTE: Budget gating deferred to extraction phase (budget integration incomplete).
    # TODO(phase-2): implement AttemptDecider-based gating when budget contract stabilizes.
    if budget is not None:
        log.debug(
            "llm.vlm_extract.budget_present budget_remaining_s=%.1f (gating deferred)",
            budget.remaining_s,
        )

    image_payloads = _encode_image_base64_variants(image_path, profile=variant_profile)
    if not image_payloads:
        return None

    lang_block, _, _ = build_ui_language_hint_block(
        html=html_context,
        mimic_locale=locale,
        site=site,
    )
    context = (
        f"Site: {site}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
    )
    prompt = VLM_PRICE_EXTRACTION_PROMPT + "\n\n" + lang_block + "\n\n" + context
    opts = _llm_runtime_options("coder")
    vlm_model = _resolve_vision_model()
    vlm_endpoint_policy = str(
        get_threshold(
            "vlm_extract_endpoint_policy",
            get_threshold("vlm_endpoint_policy", "generate_only"),
        )
    ).strip().lower() or "generate_only"
    vlm_strict_json = _threshold_bool("vlm_strict_json", False)
    vlm_think = _threshold_bool(
        "vlm_extract_think",
        _threshold_bool("vlm_think", False),
    )
    vlm_num_predict = max(
        256,
        _threshold_int("vlm_extract_num_predict", max(512, int(opts["num_predict"]))),
    )
    total_timeout = (
        int(timeout_sec)
        if timeout_sec is not None
        else _threshold_int("vlm_extract_timeout_sec", 45)
    )

    # Cap timeout to remaining budget if budget provided
    if budget is not None:
        decider = AttemptDecider()
        budget_timeout = decider.next_timeout_s("vlm_extract", "vision", budget)
        total_timeout = min(total_timeout, int(budget_timeout))
        log.info("llm.vlm_extract.timeout_capped budget_timeout_s=%.1f final_timeout_s=%d", budget_timeout, total_timeout)

    attempt_timeouts = _vlm_attempt_timeouts(total_timeout, len(image_payloads))
    effective_endpoint_policy = _effective_vlm_endpoint_policy(
        vlm_endpoint_policy,
        per_attempt_timeout_sec=max(attempt_timeouts) if attempt_timeouts else total_timeout,
    )
    skip_remaining_on_timeout = _threshold_bool(
        "vlm_extract_skip_remaining_variants_on_timeout",
        True,
    )
    skip_remaining_on_token_cap = _threshold_bool(
        "vlm_extract_skip_remaining_variants_on_token_cap",
        False,
    )
    token_cap_retries = max(0, _threshold_int("vlm_extract_token_cap_retries", 1))
    token_cap_retry_num_predict = max(
        vlm_num_predict,
        _threshold_int(
            "vlm_extract_token_cap_retry_num_predict",
            max(vlm_num_predict * 2, 8192),
        ),
    )
    token_cap_retry_timeout_backoff = max(
        1.0,
        _threshold_float("vlm_extract_token_cap_retry_timeout_backoff", 1.5),
    )
    token_cap_retry_timeout_cap = max(
        30,
        _threshold_int(
            "vlm_extract_token_cap_retry_timeout_cap_sec",
            max(attempt_timeouts) if attempt_timeouts else total_timeout,
        ),
    )
    token_cap_retry_endpoint_policy = str(
        get_threshold("vlm_extract_token_cap_retry_endpoint_policy", "generate_only")
    ).strip().lower() or "generate_only"
    log.info(
        "llm.vlm_extract.image_variants count=%s max_variant_timeout_sec=%s endpoint_policy=%s",
        len(image_payloads),
        max(attempt_timeouts) if attempt_timeouts else 0,
        effective_endpoint_policy,
    )
    last_parse_error = None
    for idx, image_b64 in enumerate(image_payloads):
        raw = None
        category = ""
        exc: Optional[Exception] = None
        variant_timeout = attempt_timeouts[idx]
        variant_num_predict = vlm_num_predict
        variant_endpoint_policy = effective_endpoint_policy
        for retry_idx in range(token_cap_retries + 1):
            try:
                raw = call_llm(
                    prompt,
                    model=vlm_model,
                    think=vlm_think,
                    json_mode=True,
                    timeout_sec=variant_timeout,
                    num_ctx=int(opts["num_ctx"]),
                    num_predict=variant_num_predict,
                    temperature=float(opts["temperature"]),
                    images=[image_b64],
                    endpoint_policy=variant_endpoint_policy,
                    strict_json=vlm_strict_json,
                    fail_fast_on_timeout=True,
                )
                break
            except Exception as failed_exc:
                exc = failed_exc
                category = _classify_llm_error(failed_exc)
                # Update circuit state for timeout/circuit_open errors
                if budget is not None and category in {"timeout", "circuit_open"}:
                    budget.set_circuit_open(vlm_model, until_sec=120)
                    log.info(
                        "llm.vlm_extract.circuit_opened model=%s category=%s cooldown_sec=120",
                        vlm_model,
                        category,
                    )
                if category == "token_cap" and retry_idx < token_cap_retries:
                    variant_num_predict = max(variant_num_predict, token_cap_retry_num_predict)
                    variant_timeout = min(
                        token_cap_retry_timeout_cap,
                        max(
                            variant_timeout,
                            int(round(variant_timeout * token_cap_retry_timeout_backoff)),
                        ),
                    )
                    variant_endpoint_policy = _effective_vlm_endpoint_policy(
                        token_cap_retry_endpoint_policy,
                        per_attempt_timeout_sec=variant_timeout,
                    )
                    log.info(
                        "llm.vlm_extract.token_cap_retry variant=%s retry=%s/%s num_predict=%s timeout_sec=%s endpoint_policy=%s",
                        idx + 1,
                        retry_idx + 1,
                        token_cap_retries,
                        variant_num_predict,
                        variant_timeout,
                        variant_endpoint_policy,
                    )
                    try:
                        reset_llm_circuit_state(vlm_model)
                    except Exception:
                        pass
                    continue
                break

        if raw is None:
            category = category or _classify_llm_error(exc or RuntimeError("unknown"))
            err_text = str(exc).lower()
            if "not found" in err_text and "model" in err_text:
                log.warning(
                    "llm.vlm_extract.model_missing model=%s hint=ollama pull %s",
                    vlm_model,
                    vlm_model,
                )
            log.warning(
                "llm.vlm_extract.request_failed category=%s attempt=%s/%s error=%s",
                category,
                idx + 1,
                len(image_payloads),
                exc,
            )
            if skip_remaining_on_timeout and category == "timeout":
                return {
                    "price": None,
                    "currency": None,
                    "confidence": "low",
                    "selector_hint": None,
                    "reason": f"llm_request_failed_{category}",
                }
            if skip_remaining_on_token_cap and category == "token_cap":
                return {
                    "price": None,
                    "currency": None,
                    "confidence": "low",
                    "selector_hint": None,
                    "reason": f"llm_request_failed_{category}",
                }
            # Token-cap and timeout can recover on alternate cropped variants.
            if category in {"token_cap", "timeout", "circuit_open", "empty_output", "unknown"} and (idx + 1) < len(image_payloads):
                try:
                    reset_llm_circuit_state(vlm_model)
                except Exception:
                    pass
                continue
            return {
                "price": None,
                "currency": None,
                "confidence": "low",
                "selector_hint": None,
                "reason": f"llm_request_failed_{category}",
            }

        try:
            raw_payload = _parse_json_from_raw(raw)
            parsed = _coerce_price_payload_from_raw(raw)
            if parsed is None and isinstance(raw_payload, dict):
                # Some VLM responses are valid JSON objects with price=null and no explicit
                # reason. Treat these as parsed no-price results instead of parse failures.
                has_price_key = "price" in raw_payload
                price_is_null = raw_payload.get("price") is None
                if has_price_key and price_is_null:
                    currency_val = raw_payload.get("currency")
                    currency = (
                        str(currency_val).strip().upper()
                        if isinstance(currency_val, str) and str(currency_val).strip()
                        else None
                    )
                    confidence_val = str(raw_payload.get("confidence", "") or "").strip().lower()
                    confidence = confidence_val if confidence_val in {"low", "medium", "high"} else "low"
                    parsed = {
                        "price": None,
                        "currency": currency,
                        "confidence": confidence,
                        "selector_hint": None,
                        "reason": str(raw_payload.get("reason", "") or "").strip() or "vlm_json_no_price",
                    }
                else:
                    raise ValueError("json_payload_unusable")
            if parsed is None:
                raise ValueError("no_json_payload")
            if isinstance(raw_payload, dict):
                page_class = _normalize_page_class(raw_payload.get("page_class", ""))
                if page_class != "unknown":
                    parsed["page_class"] = page_class
                trip_product = str(raw_payload.get("trip_product", "") or "").strip().lower()
                if trip_product in {"flight_only", "flight_hotel_package", "unknown"}:
                    parsed["trip_product"] = trip_product
                if "route_bound" in raw_payload:
                    parsed["route_bound"] = bool(raw_payload.get("route_bound"))
                visible_price_text = _sanitize_short_note(
                    raw_payload.get("visible_price_text", ""),
                    max_chars=80,
                )
                if visible_price_text:
                    parsed["visible_price_text"] = visible_price_text
            return parsed
        except Exception as exc:
            last_parse_error = exc
            log.warning(
                "llm.vlm_extract.parse_failed attempt=%s/%s error=%s raw_head=%s",
                idx + 1,
                len(image_payloads),
                exc,
                raw[:500],
            )

    if last_parse_error is not None:
        log.warning("llm.vlm_extract.parse_failed_final error=%s", last_parse_error)
    return None


def _threshold_csv_set(key: str, default_csv: str) -> set[str]:
    """Parse comma-separated threshold value into a normalized string set."""
    raw = get_threshold(key, default_csv)
    if isinstance(raw, (list, tuple, set)):
        values = [str(item).strip().lower() for item in raw]
    else:
        values = [part.strip().lower() for part in str(raw or "").split(",")]
    return {value for value in values if value}


def _confidence_rank(value: Any) -> int:
    """Map confidence label to monotonic rank."""
    text = str(value or "").strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    if text == "low":
        return 1
    return 0


def _support_rank(value: Any) -> int:
    """Map optional support label to rank for tie-breaking."""
    text = str(value or "").strip().lower()
    if text == "strong":
        return 3
    if text == "weak":
        return 2
    if text == "none":
        return 1
    return 0


def _vlm_result_score(result: Dict[str, Any]) -> Tuple[int, int, int]:
    """Score VLM result for adaptive retry selection."""
    if not isinstance(result, dict):
        return (0, 0, 0)
    has_price = 1 if result.get("price") is not None else 0
    return (
        has_price,
        _confidence_rank(result.get("confidence")),
        _support_rank(result.get("support")),
    )


def extract_price_with_vlm(
    image_path: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    html_context: str = "",
    locale: str = "",
    timeout_sec: Optional[int] = None,
    budget: Optional[LLMCallBudget] = None,
):
    """Ask a vision model to parse a flight price from screenshot image.

    Args:
        budget: Optional LLMCallBudget for gated retry management.
    """
    primary_profile = str(
        get_threshold("vlm_extract_adaptive_retry_variant_profile_primary", "default")
    ).strip() or "default"
    retry_profile = str(
        get_threshold("vlm_extract_adaptive_retry_variant_profile_retry", "diverse")
    ).strip() or "diverse"
    primary = _extract_price_with_vlm_once(
        image_path=image_path,
        site=site,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        html_context=html_context,
        locale=locale,
        timeout_sec=timeout_sec,
        variant_profile=primary_profile,
        budget=budget,
    )
    if not isinstance(primary, dict):
        return primary

    base_timeout = (
        int(timeout_sec)
        if timeout_sec is not None
        else _threshold_int("vlm_extract_timeout_sec", 45)
    )
    out = dict(primary)
    out.setdefault("vlm_adaptive_retry_attempted", False)
    out.setdefault("vlm_adaptive_retry_reason", None)
    out.setdefault("vlm_adaptive_retry_chosen", "primary")
    out.setdefault("vlm_adaptive_retry_profile", None)

    adaptive_enabled = _threshold_bool("vlm_extract_adaptive_retry_enabled", True)
    max_attempts = max(0, _threshold_int("vlm_extract_adaptive_retry_max_attempts", 1))
    retry_reason_set = _threshold_csv_set(
        "vlm_extract_adaptive_retry_on_reasons",
        "non_flight_scope,fabricated_or_unreadable,price_not_found",
    )
    reason = str(primary.get("reason", "") or "").strip().lower()
    should_retry = bool(reason and reason in retry_reason_set)

    if (not adaptive_enabled) or max_attempts <= 0 or not should_retry:
        return out

    backoff_ratio = max(
        0.1,
        min(1.0, _threshold_float("vlm_extract_adaptive_retry_timeout_backoff_ratio", 0.8)),
    )
    retry_timeout_min = max(
        30,
        _threshold_int("vlm_extract_adaptive_retry_min_timeout_sec", 120),
    )
    retry_timeout = max(retry_timeout_min, int(round(float(base_timeout) * backoff_ratio)))
    retry_timeout = max(1, retry_timeout)

    log.info(
        "llm.vlm_extract.adaptive_retry triggered reason=%s profile=%s timeout_sec=%s",
        reason or "unknown",
        retry_profile,
        retry_timeout,
    )

    chosen = out
    improvement = "none"
    for _ in range(max_attempts):
        retry_result = _extract_price_with_vlm_once(
            image_path=image_path,
            site=site,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            html_context=html_context,
            locale=locale,
            timeout_sec=retry_timeout,
            variant_profile=retry_profile,
            budget=budget,
        )
        if not isinstance(retry_result, dict):
            break
        if _vlm_result_score(retry_result) > _vlm_result_score(primary):
            chosen = dict(retry_result)
            improvement = "improved"
        break

    chosen["vlm_adaptive_retry_attempted"] = True
    chosen["vlm_adaptive_retry_reason"] = reason or None
    chosen["vlm_adaptive_retry_chosen"] = "retry" if chosen is not out else "primary"
    chosen["vlm_adaptive_retry_profile"] = retry_profile
    log.info(
        "llm.vlm_extract.adaptive_retry.result chosen=%s improvement=%s",
        chosen.get("vlm_adaptive_retry_chosen"),
        improvement,
    )
    return chosen


def parse_image_with_vlm(
    image_path: str,
    *,
    site: str,
    task: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    html_context: str = "",
    locale: str = "",
    timeout_sec: Optional[int] = None,
    budget: Optional[LLMCallBudget] = None,
) -> dict:
    """Return normalized extraction payload from visual parsing; never None.

    Args:
        budget: Optional LLMCallBudget for gated retry management.
    """
    parsed = extract_price_with_vlm(
        image_path=image_path,
        site=site,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        html_context=html_context,
        locale=locale,
        timeout_sec=timeout_sec,
        budget=budget,
    )
    if isinstance(parsed, dict):
        reason = parsed.get("reason", "")
        if parsed.get("price") is None and not reason:
            reason = "price_not_found"
        out = {
            "price": parsed.get("price"),
            "currency": parsed.get("currency"),
            "confidence": parsed.get("confidence", "low"),
            "selector_hint": None,
            "site": site,
            "task": task,
            "reason": reason,
            "source": "vlm",
        }
        page_class = _normalize_page_class(parsed.get("page_class", ""))
        if page_class != "unknown":
            out["page_class"] = page_class
        trip_product = str(parsed.get("trip_product", "") or "").strip().lower()
        if trip_product in {"flight_only", "flight_hotel_package", "unknown"}:
            out["trip_product"] = trip_product
        if "route_bound" in parsed:
            out["route_bound"] = bool(parsed.get("route_bound"))
        visible_price_text = _sanitize_short_note(parsed.get("visible_price_text", ""), max_chars=80)
        if visible_price_text:
            out["visible_price_text"] = visible_price_text
        if "vlm_adaptive_retry_attempted" in parsed:
            out["vlm_adaptive_retry_attempted"] = bool(parsed.get("vlm_adaptive_retry_attempted"))
            out["vlm_adaptive_retry_reason"] = (
                str(parsed.get("vlm_adaptive_retry_reason", "")).strip() or None
            )
            chosen = str(parsed.get("vlm_adaptive_retry_chosen", "primary") or "primary").strip().lower()
            out["vlm_adaptive_retry_chosen"] = chosen if chosen in {"primary", "retry"} else "primary"
            profile = str(parsed.get("vlm_adaptive_retry_profile", "")).strip()
            out["vlm_adaptive_retry_profile"] = profile or None
        return out

    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "site": site,
        "task": task,
        "reason": "vlm_parse_failed",
        "source": "vlm",
    }


def _normalize_vlm_labels(raw: Any, *, max_items: int = 8) -> List[str]:
    """Normalize VLM label outputs to a short deduped string list."""
    values: List[str] = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [item for item in raw if isinstance(item, str)]
    out: List[str] = []
    seen = set()
    for item in values:
        label = _sanitize_short_note(item, max_chars=32)
        if not label:
            continue
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= max_items:
            break
    return out


def _normalize_page_class(raw: Any) -> str:
    """Normalize multi-class page scope labels from LLM/VLM responses."""
    value = str(raw or "").strip().lower()
    if value in {
        "flight_only",
        "flight_hotel_package",
        "garbage_page",
        "irrelevant_page",
        "unknown",
    }:
        return value
    return "unknown"


def analyze_page_ui_with_vlm(
    image_path: str,
    *,
    site: str,
    is_domestic: Optional[bool] = None,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    locale: str = "",
    html_context: str = "",
    include_dom_context: Optional[bool] = None,
    timeout_sec: Optional[int] = None,
    max_variants: Optional[int] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze screenshot for scope/product split and fill label hints."""
    image_payloads = _encode_image_base64_variants(image_path)
    if not image_payloads:
        return {}
    if max_variants is not None:
        try:
            limit = int(max_variants)
        except Exception:
            limit = 0
        if limit > 0 and len(image_payloads) > limit:
            image_payloads = image_payloads[:limit]
    use_dom_context = (
        bool(include_dom_context)
        if include_dom_context is not None
        else _threshold_bool("planner_multimodal_include_dom_context", True)
    )
    dom_context_max_chars = max(
        3000,
        _threshold_int("planner_multimodal_dom_context_max_chars", 12000),
    )
    lang_block, lang_hint, lang_source = build_ui_language_hint_block(
        html=html_context,
        mimic_locale=locale,
        site=site,
    )
    if stage:
        log.info(
            "vision.language_hint stage=%s site=%s lang=%s source=%s",
            stage,
            site,
            lang_hint,
            lang_source,
        )
    context = (
        f"Site: {site}\n"
        f"RequestedDomestic: {is_domestic}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
        f"Locale: {locale}\n"
    )
    if use_dom_context and isinstance(html_context, str) and html_context.strip():
        compact_html = _compact_html_for_prompt(
            html_context,
            max_chars=dom_context_max_chars,
        )
        detected_language = _detect_page_language(compact_html)
        signals = _page_signal_scores(compact_html)
        context += (
            f"DetectedPageLanguage: {detected_language}\n"
            f"RouteSignalScore: {signals.get('route', 0)}\n"
            f"AuthSignalScore: {signals.get('auth', 0)}\n"
            f"ModalSignalScore: {signals.get('modal', 0)}\n"
            f"DOMSummary:\n{compact_html}\n"
        )
    prompt = VLM_UI_ASSIST_PROMPT + "\n\n" + lang_block + "\n\n" + context
    opts = _llm_runtime_options("coder")
    vlm_model = _resolve_vision_model()
    vlm_endpoint_policy = str(
        get_threshold(
            "vlm_ui_endpoint_policy",
            get_threshold("vlm_endpoint_policy", "generate_only"),
        )
    ).strip().lower() or "generate_only"
    vlm_strict_json = _threshold_bool("vlm_strict_json", False)
    vlm_think = _threshold_bool(
        "vlm_ui_think",
        _threshold_bool("vlm_think", False),
    )
    vlm_ui_num_predict = max(
        384,
        _threshold_int("vlm_ui_num_predict", max(768, int(opts["num_predict"]))),
    )
    total_timeout = (
        int(timeout_sec)
        if timeout_sec is not None
        else _threshold_int("scenario_vlm_ui_assist_timeout_sec", 30)
    )
    attempt_timeouts = _vlm_attempt_timeouts(total_timeout, len(image_payloads))
    effective_endpoint_policy = _effective_vlm_endpoint_policy(
        vlm_endpoint_policy,
        per_attempt_timeout_sec=max(attempt_timeouts) if attempt_timeouts else total_timeout,
    )
    skip_remaining_on_timeout = _threshold_bool(
        "vlm_ui_skip_remaining_variants_on_timeout",
        True,
    )
    log.info(
        "llm.vlm_ui.image_variants count=%s max_variant_timeout_sec=%s endpoint_policy=%s",
        len(image_payloads),
        max(attempt_timeouts) if attempt_timeouts else 0,
        effective_endpoint_policy,
    )
    parsed: Optional[Dict[str, Any]] = None
    for idx, image_b64 in enumerate(image_payloads):
        try:
            raw = call_llm(
                prompt,
                model=vlm_model,
                think=vlm_think,
                json_mode=True,
                timeout_sec=attempt_timeouts[idx],
                num_ctx=int(opts["num_ctx"]),
                num_predict=vlm_ui_num_predict,
                temperature=float(opts["temperature"]),
                images=[image_b64],
                endpoint_policy=effective_endpoint_policy,
                strict_json=vlm_strict_json,
                fail_fast_on_timeout=True,
            )
        except Exception as exc:
            err_text = str(exc).lower()
            if "not found" in err_text and "model" in err_text:
                log.warning(
                    "llm.vlm_ui.model_missing model=%s hint=ollama pull %s",
                    vlm_model,
                    vlm_model,
                )
            log.warning(
                "llm.vlm_ui.request_failed category=%s attempt=%s/%s error=%s",
                _classify_llm_error(exc),
                idx + 1,
                len(image_payloads),
                exc,
            )
            category = _classify_llm_error(exc)
            if skip_remaining_on_timeout and category in {"timeout", "token_cap"}:
                return {}
            if category in {"token_cap", "timeout", "circuit_open", "empty_output", "unknown"} and (idx + 1) < len(image_payloads):
                try:
                    reset_llm_circuit_state(vlm_model)
                except Exception:
                    pass
                continue
            return {}

        try:
            candidate = _parse_json_from_raw(raw)
            if isinstance(candidate, dict):
                parsed = candidate
                break
            raise ValueError("no_json_payload")
        except Exception as exc:
            log.warning(
                "llm.vlm_ui.parse_failed attempt=%s/%s error=%s raw_head=%s",
                idx + 1,
                len(image_payloads),
                exc,
                raw[:500],
            )

    if not isinstance(parsed, dict):
        return {}

    page_scope = str(parsed.get("page_scope", "")).strip().lower()
    if page_scope not in {"domestic", "international", "mixed", "unknown"}:
        page_scope = "unknown"
    page_class = _normalize_page_class(parsed.get("page_class"))
    trip_product = str(parsed.get("trip_product", "")).strip().lower()
    if trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
        trip_product = "unknown"
    # Backward-compatible inference when model only returns one field.
    if page_class == "unknown" and trip_product in {"flight_only", "flight_hotel_package"}:
        page_class = trip_product
    if trip_product == "unknown" and page_class in {"flight_only", "flight_hotel_package"}:
        trip_product = page_class
    blocked = bool(parsed.get("blocked_by_modal", False))

    mode_labels = parsed.get("mode_labels") if isinstance(parsed.get("mode_labels"), dict) else {}
    fill_labels = parsed.get("fill_labels") if isinstance(parsed.get("fill_labels"), dict) else {}
    raw_target_regions = (
        parsed.get("target_regions")
        if isinstance(parsed.get("target_regions"), dict)
        else {}
    )
    target_regions: Dict[str, Optional[List[float]]] = {}
    for key in ("origin", "dest", "depart", "return", "search", "modal_close"):
        bbox = _normalize_roi_bbox(raw_target_regions.get(key))
        target_regions[key] = list(bbox) if bbox is not None else None
    return {
        "page_scope": page_scope,
        "page_class": page_class,
        "trip_product": trip_product,
        "blocked_by_modal": blocked,
        "mode_labels": {
            "domestic": _normalize_vlm_labels(mode_labels.get("domestic")),
            "international": _normalize_vlm_labels(mode_labels.get("international")),
        },
        "product_labels": _normalize_vlm_labels(parsed.get("product_labels")),
        "fill_labels": {
            "origin": _normalize_vlm_labels(fill_labels.get("origin")),
            "dest": _normalize_vlm_labels(fill_labels.get("dest")),
            "depart": _normalize_vlm_labels(fill_labels.get("depart")),
            "return": _normalize_vlm_labels(fill_labels.get("return")),
            "search": _normalize_vlm_labels(fill_labels.get("search")),
        },
        "target_regions": target_regions,
        "reason": _sanitize_short_note(parsed.get("reason", ""), max_chars=160),
    }


def assess_html_quality_with_llm(
    html: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    timeout_sec: Optional[int] = None,
) -> Dict[str, str]:
    """Classify extraction quality for one HTML snapshot."""
    compact_html = _compact_html_for_prompt(html, max_chars=12000)
    context = (
        f"Site: {site}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
    )
    template = get_prompt(PROMPT_HTML_QUALITY, fallback=HTML_QUALITY_PROMPT)
    prompt = template + "\n\n" + context + "\nHTML:\n" + compact_html
    opts = _llm_runtime_options("coder")
    try:
        raw = call_llm(
            prompt,
            model=CODER_MODEL,
            think=False,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("llm_html_quality_timeout_sec", 18)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=min(160, int(opts["num_predict"])),
            temperature=float(opts["temperature"]),
        )
    except Exception as exc:
        log.warning(
            "llm.quality.request_failed category=%s error=%s",
            _classify_llm_error(exc),
            exc,
        )
        return {"quality": "unknown", "reason": "llm_quality_failed"}

    try:
        parsed = _parse_json_from_raw(raw)
        ok, error_code, normalized = validate_prompt_output(PROMPT_HTML_QUALITY, parsed, raw)
        if ok and isinstance(normalized, dict):
            parsed = normalized
        elif not ok:
            log.warning("llm.quality.soft_validate_failed error_code=%s raw_head=%s", error_code, raw[:200])
        if not isinstance(parsed, dict):
            raise ValueError("no_json_payload")
        quality = str(parsed.get("quality", "")).strip().lower()
        if quality not in {"good", "uncertain", "garbage"}:
            quality = "uncertain"
        reason = _sanitize_short_note(parsed.get("reason", ""), max_chars=120)
        return {"quality": quality, "reason": reason or "llm_quality_judged"}
    except Exception as exc:
        log.warning("llm.quality.parse_failed error=%s raw_head=%s", exc, raw[:500])
        return {"quality": "unknown", "reason": "llm_quality_parse_failed"}


def assess_trip_product_scope_with_llm(
    html: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    timeout_sec: Optional[int] = None,
) -> Dict[str, str]:
    """Classify page scope as flight-only vs package using text HTML evidence."""
    compact_html = _compact_html_for_prompt(html, max_chars=12000)
    context = (
        f"Site: {site}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
    )
    template = get_prompt(PROMPT_LLM_TRIP_PRODUCT_GUARD, fallback=LLM_TRIP_PRODUCT_GUARD_PROMPT)
    prompt = template + "\n\n" + context + "\nHTML:\n" + compact_html
    opts = _llm_runtime_options("coder")
    endpoint_policy = str(
        get_threshold("extract_llm_scope_guard_endpoint_policy", "chat_only")
    ).strip().lower() or "chat_only"
    try:
        raw = call_llm(
            prompt,
            model=CODER_MODEL,
            think=False,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("extract_llm_scope_guard_timeout_sec", 120)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=min(220, int(opts["num_predict"])),
            temperature=float(opts["temperature"]),
            endpoint_policy=endpoint_policy,
        )
    except Exception as exc:
        log.warning(
            "llm.scope_guard.request_failed category=%s error=%s",
            _classify_llm_error(exc),
            exc,
        )
        return {
            "page_class": "unknown",
            "trip_product": "unknown",
            "reason": "llm_scope_guard_failed",
        }

    try:
        parsed = _parse_json_from_raw(raw)
        ok, error_code, normalized = validate_prompt_output(PROMPT_LLM_TRIP_PRODUCT_GUARD, parsed, raw)
        if ok and isinstance(normalized, dict):
            parsed = normalized
        elif not ok:
            log.warning("llm.scope_guard.soft_validate_failed error_code=%s raw_head=%s", error_code, raw[:200])
        if not isinstance(parsed, dict):
            raise ValueError("no_json_payload")
        page_class = _normalize_page_class(parsed.get("page_class"))
        trip_product = str(parsed.get("trip_product", "")).strip().lower()
        if trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
            trip_product = "unknown"
        if page_class == "unknown" and trip_product in {"flight_only", "flight_hotel_package"}:
            page_class = trip_product
        if trip_product == "unknown" and page_class in {"flight_only", "flight_hotel_package"}:
            trip_product = page_class
        reason = _sanitize_short_note(parsed.get("reason", ""), max_chars=120)
        return {
            "page_class": page_class,
            "trip_product": trip_product,
            "reason": reason or "llm_scope_guard_judged",
        }
    except Exception as exc:
        log.warning("llm.scope_guard.parse_failed error=%s raw_head=%s", exc, raw[:500])
        return {
            "page_class": "unknown",
            "trip_product": "unknown",
            "reason": "llm_scope_guard_parse_failed",
        }


def assess_vlm_price_candidate_with_llm(
    html: str,
    *,
    site: str,
    price: float,
    currency: str = "",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Cross-check a VLM price candidate against HTML context using text LLM."""
    compact_html = _compact_html_for_prompt(html, max_chars=12000)
    context = (
        f"Site: {site}\n"
        f"CandidatePrice: {price}\n"
        f"CandidateCurrency: {currency}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
    )
    prompt = VLM_PRICE_VERIFICATION_PROMPT + "\n\n" + context + "\nHTML:\n" + compact_html
    opts = _llm_runtime_options("coder")
    endpoint_policy = str(
        get_threshold("extract_vlm_llm_price_verify_endpoint_policy", "chat_only")
    ).strip().lower() or "chat_only"
    num_predict = max(
        96,
        _threshold_int(
            "extract_vlm_llm_price_verify_num_predict",
            min(256, int(opts["num_predict"])),
        ),
    )
    try:
        raw = call_llm(
            prompt,
            model=CODER_MODEL,
            think=False,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("extract_vlm_llm_price_verify_timeout_sec", 180)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=num_predict,
            temperature=float(opts["temperature"]),
            endpoint_policy=endpoint_policy,
        )
    except Exception as exc:
        log.warning(
            "llm.vlm_price_verify.request_failed category=%s error=%s",
            _classify_llm_error(exc),
            exc,
        )
        return {"accept": "unknown", "support": "none", "reason": "vlm_price_verify_failed"}

    try:
        parsed = _parse_json_from_raw(raw)
        if not isinstance(parsed, dict):
            raise ValueError("no_json_payload")
        accept_raw = parsed.get("accept")
        if isinstance(accept_raw, bool):
            accept: Any = accept_raw
        elif isinstance(accept_raw, str):
            lowered = accept_raw.strip().lower()
            if lowered in {"true", "1", "yes", "accept"}:
                accept = True
            elif lowered in {"false", "0", "no", "reject"}:
                accept = False
            else:
                accept = "unknown"
        else:
            accept = "unknown"
        support = str(parsed.get("support", "") or "").strip().lower()
        if support not in {"strong", "weak", "none"}:
            support = "none"
        reason = _sanitize_short_note(parsed.get("reason", ""), max_chars=140)
        return {
            "accept": accept,
            "support": support,
            "reason": reason or "vlm_price_verify_judged",
        }
    except Exception as exc:
        log.warning("llm.vlm_price_verify.parse_failed error=%s raw_head=%s", exc, raw[:500])
        return {"accept": "unknown", "support": "none", "reason": "vlm_price_verify_parse_failed"}


def _build_multimodal_context_pack(
    html: str,
    *,
    site: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    multimodal_mode: str = "assist",
    code_judge_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build bounded DOM/code context for multimodal extraction."""
    max_chars = max(
        4_000,
        _threshold_int("multimodal_extract_max_html_chars", 30_000),
    )
    compact_html = _compact_html_for_prompt(html, max_chars=max_chars)
    detected_language = _detect_page_language(compact_html)
    signals = _page_signal_scores(compact_html)
    mode = str(multimodal_mode or "assist").strip().lower() or "assist"
    if mode not in {"assist", "primary", "judge", "judge_primary"}:
        mode = "assist"
    context = (
        f"Site: {site}\n"
        f"Origin: {origin}\n"
        f"Destination: {dest}\n"
        f"Departure: {depart}\n"
        f"ReturnDate: {return_date}\n"
        f"MultimodalMode: {mode}\n"
        f"DetectedPageLanguage: {detected_language}\n"
        f"RouteSignalScore: {signals.get('route', 0)}\n"
        f"AuthSignalScore: {signals.get('auth', 0)}\n"
        f"ModalSignalScore: {signals.get('modal', 0)}\n"
    )
    if mode in {"judge", "judge_primary"}:
        context += "JudgingPolicy: conservative_cross_check_with_code_model\n"
        judge_blob = _compact_prompt_json_blob(
            code_judge_context or {},
            max_chars=max(
                300,
                _threshold_int("multimodal_extract_code_context_max_chars", 1200),
            ),
        )
        if judge_blob:
            context += f"CodeJudgeContext: {judge_blob}\n"
    return context + f"DOMSummary:\n{compact_html}"


def parse_page_multimodal_with_vlm(
    *,
    image_path: str,
    html: str,
    site: str,
    task: str,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    multimodal_mode: str = "assist",
    code_judge_context: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Run multimodal extraction (screenshot + DOM summary) and return normalized payload."""
    image_payloads = _encode_image_base64_variants(image_path)
    if not image_payloads:
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "site": site,
            "task": task,
            "source": "vlm_multimodal",
            "reason": "vlm_image_unavailable",
        }

    prompt = (
        VLM_MULTIMODAL_EXTRACTION_PROMPT
        + "\n\n"
        + _build_multimodal_context_pack(
            html,
            site=site,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            multimodal_mode=multimodal_mode,
            code_judge_context=code_judge_context,
        )
    )
    opts = _llm_runtime_options("coder")
    vlm_model = _resolve_vision_model()
    vlm_endpoint_policy = str(
        get_threshold(
            "multimodal_extract_endpoint_policy",
            get_threshold("vlm_extract_endpoint_policy", "chat_only"),
        )
    ).strip().lower() or "chat_only"
    vlm_strict_json = _threshold_bool("vlm_strict_json", False)
    vlm_think = _threshold_bool(
        "vlm_multimodal_think",
        _threshold_bool("vlm_think", False),
    )
    multimodal_num_ctx = max(
        8192,
        _threshold_int(
            "multimodal_extract_num_ctx",
            max(12_288, int(opts["num_ctx"])),
        ),
    )
    multimodal_num_predict = max(
        512,
        _threshold_int(
            "multimodal_extract_num_predict",
            max(1024, int(opts["num_predict"])),
        ),
    )
    total_timeout = (
        int(timeout_sec)
        if timeout_sec is not None
        else _threshold_int("multimodal_extract_timeout_sec", 1200)
    )
    attempt_timeouts = _vlm_attempt_timeouts(total_timeout, len(image_payloads))
    effective_endpoint_policy = _effective_vlm_endpoint_policy(
        vlm_endpoint_policy,
        per_attempt_timeout_sec=max(attempt_timeouts) if attempt_timeouts else total_timeout,
    )

    last_parse_error: Optional[Exception] = None
    for idx, image_b64 in enumerate(image_payloads):
        try:
            raw = call_llm(
                prompt,
                model=vlm_model,
                think=vlm_think,
                json_mode=True,
                timeout_sec=attempt_timeouts[idx],
                num_ctx=multimodal_num_ctx,
                num_predict=multimodal_num_predict,
                temperature=float(opts["temperature"]),
                images=[image_b64],
                endpoint_policy=effective_endpoint_policy,
                strict_json=vlm_strict_json,
                fail_fast_on_timeout=True,
            )
        except Exception as exc:
            category = _classify_llm_error(exc)
            log.warning(
                "llm.vlm_multimodal.request_failed category=%s attempt=%s/%s error=%s",
                category,
                idx + 1,
                len(image_payloads),
                exc,
            )
            if category in {"token_cap", "timeout", "circuit_open", "empty_output", "unknown"} and (idx + 1) < len(image_payloads):
                try:
                    reset_llm_circuit_state(vlm_model)
                except Exception:
                    pass
                continue
            return {
                "price": None,
                "currency": None,
                "confidence": "low",
                "selector_hint": None,
                "site": site,
                "task": task,
                "source": "vlm_multimodal",
                "reason": f"llm_request_failed_{category}",
            }

        try:
            parsed = _parse_json_from_raw(raw)
            if not isinstance(parsed, dict):
                raise ValueError("no_json_payload")
        except Exception as exc:
            last_parse_error = exc
            log.warning(
                "llm.vlm_multimodal.parse_failed attempt=%s/%s error=%s raw_head=%s",
                idx + 1,
                len(image_payloads),
                exc,
                raw[:500],
            )
            continue

        payload = _coerce_price_payload_from_raw(raw) or {
            "price": None,
            "currency": None,
            "confidence": "low",
            "reason": str(parsed.get("reason", "") or "").strip() or "price_not_found",
        }
        out = {
            "price": payload.get("price"),
            "currency": payload.get("currency"),
            "confidence": payload.get("confidence", "low"),
            "selector_hint": _normalize_selector_hint(parsed.get("selector_hint")),
            "site": site,
            "task": task,
            "source": "vlm_multimodal",
            "reason": payload.get("reason", ""),
        }
        page_class = _normalize_page_class(parsed.get("page_class"))
        trip_product = str(parsed.get("trip_product", "") or "").strip().lower()
        if trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
            trip_product = "unknown"
        if page_class == "unknown" and trip_product in {"flight_only", "flight_hotel_package"}:
            page_class = trip_product
        if trip_product == "unknown" and page_class in {"flight_only", "flight_hotel_package"}:
            trip_product = page_class
        if page_class != "unknown":
            out["page_class"] = page_class
        if trip_product != "unknown":
            out["trip_product"] = trip_product
        out["route_bound"] = bool(parsed.get("route_bound", False))
        return out

    if last_parse_error is not None:
        log.warning("llm.vlm_multimodal.parse_failed_final error=%s", last_parse_error)
    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "site": site,
        "task": task,
        "source": "vlm_multimodal",
        "reason": "vlm_multimodal_parse_failed",
    }


def extract_price_with_llm(html: str, *, timeout_sec: Optional[int] = None):
    """Ask the LLM to parse HTML and return parsed JSON or None on parse failure."""
    compact_html = _compact_html_for_prompt(html)
    prompt = PRICE_EXTRACTION_PROMPT + "\n\nHTML:\n" + compact_html

    # Deterministic extraction task: disable thinking for lower latency/cost.
    opts = _llm_runtime_options("coder")
    endpoint_policy = str(
        get_threshold("llm_extract_endpoint_policy", "chat_only")
    ).strip().lower() or "chat_only"
    fail_fast_on_timeout = _threshold_bool("llm_extract_fail_fast_on_timeout", True)
    try:
        raw = call_llm(
            prompt,
            model=CODER_MODEL,
            think=False,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("llm_extract_timeout_sec", 120)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=int(opts["num_predict"]),
            temperature=float(opts["temperature"]),
            endpoint_policy=endpoint_policy,
            fail_fast_on_timeout=fail_fast_on_timeout,
        )
    except Exception as exc:
        category = _classify_llm_error(exc)
        log.warning(
            "llm.extract.request_failed category=%s error=%s",
            category,
            exc,
        )
        return {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": f"llm_request_failed_{category}",
        }

    try:
        parsed = _parse_json_from_raw(raw)
        if parsed is None:
            raise ValueError("no_json_payload")
        return parsed
    except Exception as exc:
        log.warning("llm.extract.parse_failed error=%s raw_head=%s", exc, raw[:500])
        return None


def parse_html_with_llm(
    html: str,
    site: str,
    task: str,
    *,
    timeout_sec: Optional[int] = None,
    budget: Optional["LLMCallBudget"] = None,
) -> dict:
    """Return a normalized extraction payload; never return None."""
    # NOTE: Budget gating deferred to extraction phase (budget integration incomplete).
    # TODO(phase-2): implement AttemptDecider-based gating when budget contract stabilizes.
    if budget is not None:
        log.debug(
            "llm.html_parse.budget_present budget_remaining_s=%.1f (gating deferred)",
            budget.remaining_s,
        )

    parsed = extract_price_with_llm(html, timeout_sec=timeout_sec)
    if isinstance(parsed, dict):
        reason = parsed.get("reason", "")
        if parsed.get("price") is None and not reason:
            reason = "price_not_found"
        return {
            "price": parsed.get("price"),
            "currency": parsed.get("currency"),
            "confidence": parsed.get("confidence", "low"),
            "selector_hint": _normalize_selector_hint(parsed.get("selector_hint")),
            "site": site,
            "task": task,
            "reason": reason,
        }

    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "site": site,
        "task": task,
        "reason": "llm_parse_failed",
    }


def _normalize_action_plan(parsed: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize common LLM plan output shapes to a list of action steps."""
    if isinstance(parsed, list):
        steps = [s for s in parsed if isinstance(s, dict)]
        return steps or None

    if isinstance(parsed, dict):
        # Common wrapper keys returned by smaller local models.
        for key in ("steps", "plan", "actions"):
            inner = parsed.get(key)
            if isinstance(inner, list):
                steps = [s for s in inner if isinstance(s, dict)]
                return steps or None

        # Single-step fallback.
        if "action" in parsed and "selector" in parsed:
            return [parsed]

    return None


def _normalize_action_plan_with_notes(parsed: Any) -> Tuple[Optional[List[Dict[str, Any]]], List[str]]:
    """Normalize action plan plus optional short free-form notes."""
    steps = _normalize_action_plan(parsed)
    notes: List[str] = []
    if isinstance(parsed, dict):
        raw_notes = parsed.get("notes")
        if isinstance(raw_notes, list):
            for item in raw_notes[:3]:
                note = _sanitize_short_note(item)
                if note:
                    notes.append(note)
        else:
            note = _sanitize_short_note(raw_notes or parsed.get("note") or parsed.get("observation"))
            if note:
                notes.append(note)
    return steps, notes


def _planner_multimodal_assist_hint(
    *,
    screenshot_path: str,
    html: str,
    site_key: str,
    is_domestic: Optional[bool],
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    mimic_locale: Optional[str],
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Collect optional VLM planner-assist hint from screenshot + DOM context."""
    path = (screenshot_path or "").strip()
    if not path:
        return {}
    try:
        return analyze_page_ui_with_vlm(
            path,
            site=site_key,
            is_domestic=is_domestic,
            origin=origin or "",
            dest=dest or "",
            depart=depart or "",
            return_date=return_date or "",
            locale=mimic_locale or "",
            html_context=html or "",
            include_dom_context=True,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        log.warning(
            "llm.plan.multimodal_hint_failed site=%s category=%s error=%s",
            site_key,
            _classify_llm_error(exc),
            exc,
        )
        return {}

def generate_action_plan(
    html: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str] = None,
    trip_type: str = "one_way",
    is_domestic: bool = False,
    max_transit: Optional[int] = None,
    turn_index: int = 0,
    global_knowledge: str = "",
    local_knowledge: str = "",
    site_key: str = "google_flights",
    mimic_locale: Optional[str] = None,
    mimic_region: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    trace_memory_hint: str = "",
    timeout_sec: Optional[int] = None,
    return_bundle: bool = False,
    model: Optional[str] = None,
):
    """Generate a Playwright action plan for a flight-search scenario."""
    model_name = model if model is not None else PLANNER_MODEL
    compact_html = _compact_html_for_prompt(html, max_chars=MAX_SCENARIO_HTML_PROMPT_CHARS)
    semantic_chunks = _semantic_html_chunks_for_prompt(
        html,
        max_chunks=3,
        chunk_chars=4200,
        max_total_chars=10500,
    )
    semantic_chunks_block = _format_semantic_chunks_for_prompt(semantic_chunks)
    expected_language = _expected_language_from_locale(mimic_locale)
    detected_language = _detect_page_language(compact_html)
    signals = _page_signal_scores(compact_html)
    multimodal_enabled = _threshold_bool("planner_multimodal_assist_enabled", False)
    multimodal_hint: Dict[str, Any] = {}
    multimodal_hint_text = ""
    if multimodal_enabled and isinstance(screenshot_path, str) and screenshot_path.strip():
        multimodal_hint = _planner_multimodal_assist_hint(
            screenshot_path=screenshot_path,
            html=html,
            site_key=site_key,
            is_domestic=is_domestic,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date or "",
            mimic_locale=mimic_locale,
            timeout_sec=_threshold_int("planner_multimodal_assist_timeout_sec", 180),
        )
        multimodal_hint_text = _compact_hint_dict(multimodal_hint, max_chars=380)
        if multimodal_hint_text:
            log.info(
                "llm.plan.multimodal_hint site=%s hint=%s",
                site_key,
                multimodal_hint_text,
            )

    context = f"""
Site: {site_key}
Origin: {origin}
Destination: {dest}
Departure: {depart}
ReturnDate: {return_date or ""}
TripType: {trip_type}
IsDomestic: {is_domestic}
MaxTransit: {max_transit if max_transit is not None else ""}
TurnIndex: {turn_index}
GlobalKnowledge: {global_knowledge}
LocalKnowledge: {local_knowledge}
LocaleHint: {mimic_locale or ""}
RegionHint: {mimic_region or ""}
ExpectedLanguageFromLocale: {expected_language}
DetectedPageLanguage: {detected_language}
RouteSignalScore: {signals.get("route", 0)}
AuthSignalScore: {signals.get("auth", 0)}
ModalSignalScore: {signals.get("modal", 0)}
PlannerMultimodalHint: {multimodal_hint_text}
TraceMemoryHint: {_sanitize_short_note(trace_memory_hint, max_chars=220)}
SemanticChunkCount: {len(semantic_chunks)}
    """
    prompt_parts = [SCENARIO_PROMPT, context, "HTML:\n" + compact_html]
    if semantic_chunks_block:
        prompt_parts.append("SEMANTIC_DOM_CHUNKS:\n" + semantic_chunks_block)
    prompt = "\n\n".join(prompt_parts)

    # Structured planning task with constrained schema: thinking is usually unnecessary.
    opts = _llm_runtime_options("planner")
    try:
        raw = call_llm(
            prompt,
            model=model_name,
            think=False,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("llm_planner_timeout_sec", 180)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=int(opts["num_predict"]),
            temperature=float(opts["temperature"]),
        )
    except Exception as exc:
        log.warning(
            "llm.plan.request_failed category=%s error=%s",
            _classify_llm_error(exc),
            exc,
        )
        return None

    try:
        parsed = _parse_json_from_raw(raw)
        if parsed is None:
            raise ValueError("no_json_payload")
        steps, notes = _normalize_action_plan_with_notes(parsed)
        if return_bundle:
            return {"steps": steps, "notes": notes}
        return steps
    except Exception as exc:
        log.warning("llm.plan.parse_failed error=%s raw_head=%s", exc, raw[:500])
        if return_bundle:
            return {"steps": None, "notes": []}
        return None

def repair_action_plan(
    old_plan,
    html,
    *,
    site_key: str = "google_flights",
    turn_index: int = 0,
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    is_domestic: Optional[bool] = None,
    mimic_locale: Optional[str] = None,
    mimic_region: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    trace_memory_hint: str = "",
    timeout_sec: Optional[int] = None,
    return_bundle: bool = False,
    model: Optional[str] = None,
):
    """Request a repaired action plan from updated DOM after a failed attempt."""
    model_name = model if model is not None else PLANNER_MODEL
    compact_html = _compact_html_for_prompt(html, max_chars=MAX_SCENARIO_HTML_PROMPT_CHARS)
    semantic_chunks = _semantic_html_chunks_for_prompt(
        html,
        max_chunks=3,
        chunk_chars=4200,
        max_total_chars=10500,
    )
    semantic_chunks_block = _format_semantic_chunks_for_prompt(semantic_chunks)
    expected_language = _expected_language_from_locale(mimic_locale)
    detected_language = _detect_page_language(compact_html)
    signals = _page_signal_scores(compact_html)
    multimodal_enabled = _threshold_bool("planner_multimodal_assist_enabled", False)
    multimodal_hint_text = ""
    if multimodal_enabled and isinstance(screenshot_path, str) and screenshot_path.strip():
        multimodal_hint = _planner_multimodal_assist_hint(
            screenshot_path=screenshot_path,
            html=html,
            site_key=site_key,
            is_domestic=is_domestic,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
            mimic_locale=mimic_locale,
            timeout_sec=_threshold_int("planner_multimodal_assist_timeout_sec", 180),
        )
        multimodal_hint_text = _compact_hint_dict(multimodal_hint, max_chars=380)
        if multimodal_hint_text:
            log.info(
                "llm.plan_repair.multimodal_hint site=%s hint=%s",
                site_key,
                multimodal_hint_text,
            )
    context = f"""
Site: {site_key}
TurnIndex: {turn_index}
LocaleHint: {mimic_locale or ""}
RegionHint: {mimic_region or ""}
ExpectedLanguageFromLocale: {expected_language}
DetectedPageLanguage: {detected_language}
RouteSignalScore: {signals.get("route", 0)}
AuthSignalScore: {signals.get("auth", 0)}
ModalSignalScore: {signals.get("modal", 0)}
PlannerMultimodalHint: {multimodal_hint_text}
TraceMemoryHint: {_sanitize_short_note(trace_memory_hint, max_chars=220)}
SemanticChunkCount: {len(semantic_chunks)}
    """
    repair_html = compact_html
    if semantic_chunks_block:
        repair_html = compact_html + "\n\n<!-- SEMANTIC_DOM_CHUNKS -->\n" + semantic_chunks_block
    prompt = REPAIR_PROMPT.format(
        plan=old_plan,
        html=repair_html,
    )
    prompt = prompt + "\n\nContext:\n" + context

    # Repair requires more contextual reasoning over a failed plan and changed DOM.
    opts = _llm_runtime_options("planner")
    try:
        raw = call_llm(
            prompt,
            model=model_name,
            think=True,
            json_mode=True,
            timeout_sec=(
                int(timeout_sec)
                if timeout_sec is not None
                else _threshold_int("llm_repair_timeout_sec", 180)
            ),
            num_ctx=int(opts["num_ctx"]),
            num_predict=int(opts["num_predict"]),
            temperature=float(opts["temperature"]),
        )
    except Exception as exc:
        log.warning(
            "llm.plan_repair.request_failed category=%s error=%s",
            _classify_llm_error(exc),
            exc,
        )
        return None

    try:
        parsed = _parse_json_from_raw(raw)
        if parsed is None:
            raise ValueError("no_json_payload")
        steps, notes = _normalize_action_plan_with_notes(parsed)
        if return_bundle:
            return {"steps": steps, "notes": notes}
        return steps
    except Exception as exc:
        log.warning("llm.plan_repair.parse_failed error=%s raw_head=%s", exc, raw[:500])
        if return_bundle:
            return {"steps": None, "notes": []}
        return None
