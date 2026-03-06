"""Reusable JSON parsing/coercion helpers for LLM adapter outputs."""

import json
import re
from typing import Any, Dict, Optional


def parse_json_from_raw(raw: str) -> Optional[Any]:
    """Parse JSON from raw model output, tolerating noisy wrappers."""
    if not isinstance(raw, str) or not raw.strip():
        return None

    text = raw.strip()

    # Fast path: already clean JSON.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Remove common markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    def _try_parse_object_fragment(fragment_text: str) -> Optional[Any]:
        frag = str(fragment_text or "").strip()
        if not frag or frag[:1] in "{[":
            return None
        key_match = re.search(r'["\']?[A-Za-z_][A-Za-z0-9_\- ]*["\']?\s*:', frag)
        if not key_match or key_match.start() > 8:
            return None
        start = key_match.start()
        for end in range(len(frag), start, -1):
            chunk = frag[start:end].strip()
            if not chunk:
                continue
            chunk = chunk.rstrip(",")
            try:
                return json.loads("{" + chunk + "}")
            except Exception:
                continue
        return None

    fragment_parsed = _try_parse_object_fragment(text)
    if fragment_parsed is not None:
        return fragment_parsed

    # Extract first JSON-like object/array block.
    start_candidates = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
    if not start_candidates:
        return None
    start = min(start_candidates)

    for end in range(len(text), start, -1):
        chunk = text[start:end].strip()
        if not chunk:
            continue
        if chunk[0] not in "{[":
            continue
        if chunk[-1] not in "}]":
            continue
        try:
            return json.loads(chunk)
        except Exception:
            continue

    return None


def coerce_price_payload_from_raw(raw: str) -> Optional[Dict[str, Any]]:
    """Recover a minimal price payload from imperfect model output."""
    parsed = parse_json_from_raw(raw)
    if isinstance(parsed, dict):
        price_raw = parsed.get("price")
        try:
            price = float(str(price_raw).replace(",", "")) if price_raw is not None else None
        except Exception:
            price = None
        currency = str(parsed.get("currency", "") or "").strip().upper() or None
        confidence = str(parsed.get("confidence", "") or "low").strip().lower() or "low"
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        reason = str(parsed.get("reason", "") or "").strip()
        if price is not None:
            return {
                "price": price,
                "currency": currency,
                "confidence": confidence,
                "reason": reason,
            }
        if reason:
            # Preserve explicit no-price explanations (e.g., hotel page / non-flight scope).
            return {
                "price": None,
                "currency": currency,
                "confidence": confidence,
                "reason": reason,
            }

    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return None

    price_match = re.search(
        r"[\"']?price[\"']?\s*:\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not price_match:
        # Keep explicit no-price explanations when model emits near-JSON with price=null.
        has_null_price = bool(
            re.search(
                r"[\"']?price[\"']?\s*:\s*null\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        if not has_null_price:
            return None

        reason_match = re.search(
            r"[\"']?reason[\"']?\s*:\s*[\"'](.+?)[\"'](?:\s*[,}\n]|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        reason = reason_match.group(1).strip() if reason_match else ""
        if not reason:
            return None

        currency_match = re.search(
            r"[\"']?currency[\"']?\s*:\s*[\"']([A-Za-z]{3})[\"']",
            text,
            flags=re.IGNORECASE,
        )
        currency = currency_match.group(1).upper() if currency_match else None
        confidence_match = re.search(
            r"[\"']?confidence[\"']?\s*:\s*[\"'](low|medium|high)[\"']",
            text,
            flags=re.IGNORECASE,
        )
        confidence = confidence_match.group(1).lower() if confidence_match else "low"
        return {
            "price": None,
            "currency": currency,
            "confidence": confidence,
            "reason": reason,
        }
    try:
        price = float(price_match.group(1).replace(",", ""))
    except Exception:
        return None

    currency_match = re.search(
        r"[\"']?currency[\"']?\s*:\s*[\"']([A-Za-z]{3})[\"']",
        text,
        flags=re.IGNORECASE,
    )
    currency = currency_match.group(1).upper() if currency_match else None
    if currency is None and ("¥" in text or "￥" in text or "円" in text):
        currency = "JPY"

    confidence_match = re.search(
        r"[\"']?confidence[\"']?\s*:\s*[\"'](low|medium|high)[\"']",
        text,
        flags=re.IGNORECASE,
    )
    confidence = confidence_match.group(1).lower() if confidence_match else "low"
    return {
        "price": price,
        "currency": currency,
        "confidence": confidence,
        "reason": "llm_near_json_recovered",
    }
