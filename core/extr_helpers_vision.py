"""Vision extraction caching helpers extracted from core.extractor.

This module provides utilities for vision-based price extraction stage caching,
screenshot fingerprinting, and result normalization. Used by multimodal extraction
logic to manage vision API calls and cache results with per-attempt cooldown.
"""

import hashlib
import os
from typing import Any, Dict


def _vision_screenshot_fingerprint(path: str, *, max_prefix_bytes: int = 65536) -> str:
    """Build stable screenshot fingerprint for vision-stage cache keys."""
    image_path = str(path or "").strip()
    if not image_path:
        return ""
    try:
        stat = os.stat(image_path)
    except Exception:
        return ""
    digest = hashlib.sha1()
    digest.update(str(image_path).encode("utf-8", errors="ignore"))
    digest.update(str(int(stat.st_size)).encode("ascii"))
    digest.update(str(int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))).encode("ascii"))
    try:
        with open(image_path, "rb") as handle:
            digest.update(handle.read(max(1024, int(max_prefix_bytes))))
    except Exception:
        return ""
    return digest.hexdigest()


def _vision_cached_stage_call(
    *,
    cache: Dict[str, Dict[str, Dict[str, Any]]],
    cooldown: Dict[str, str],
    stage: str,
    screenshot_path: str,
    runner,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one vision stage with cache + per-attempt cooldown keyed by screenshot fingerprint."""
    stage_key = str(stage or "").strip().lower() or "unknown"
    fingerprint = _vision_screenshot_fingerprint(screenshot_path)
    if not fingerprint:
        fingerprint = f"path:{str(screenshot_path or '').strip()}"
    stage_cache = cache.setdefault(stage_key, {})
    meta = {
        "cached": False,
        "cooldown_skip": False,
        "fingerprint": fingerprint,
        "stage": stage_key,
    }
    if fingerprint in stage_cache:
        meta["cached"] = True
        cooldown[stage_key] = fingerprint
        return dict(stage_cache.get(fingerprint) or {}), meta
    if cooldown.get(stage_key) == fingerprint:
        meta["cooldown_skip"] = True
        return {}, meta
    out: Dict[str, Any] = {}
    try:
        raw = runner()
        if isinstance(raw, dict):
            out = dict(raw)
    except Exception:
        out = {}
    stage_cache[fingerprint] = dict(out)
    cooldown[stage_key] = fingerprint
    return dict(out), meta


def _normalize_vision_extract_assist_result(raw: Any) -> Dict[str, Any]:
    """Normalize Stage-C vision extraction payload to strict compact schema."""
    out = {
        "price": None,
        "currency": None,
        "evidence": "",
        "confidence": "low",
        "reason": "",
    }
    if not isinstance(raw, dict):
        return out
    try:
        if raw.get("price") is not None:
            out["price"] = float(raw.get("price"))
    except Exception:
        out["price"] = None
    currency = str(raw.get("currency", "") or "").strip().upper()
    out["currency"] = currency or None
    confidence = str(raw.get("confidence", "") or "").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium" if out.get("price") is not None else "low"
    out["confidence"] = confidence
    evidence = str(raw.get("evidence", "") or "").strip()
    if not evidence:
        evidence = str(raw.get("visible_price_text", "") or "").strip()
    out["evidence"] = evidence[:140]
    out["reason"] = str(raw.get("reason", "") or "").strip()[:140]
    return out
