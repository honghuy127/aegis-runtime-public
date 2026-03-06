"""ExtractionStrategy adapters over existing llm.code_model entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.plugins.extraction.normalize import normalize_plugin_candidate
from llm import code_model


def _fallback_payload(*, site: str, task: str, reason: str) -> Dict[str, Any]:
    """Minimal normalized failure payload for strategy wrappers."""
    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "selector_hint": None,
        "site": site,
        "task": task,
        "reason": reason,
    }


def _normalize_preserving_legacy_shape(
    raw: Dict[str, Any],
    *,
    strategy_key: str,
) -> Dict[str, Any]:
    """Use canonical normalization while preserving adapter parity shape."""
    normalized = normalize_plugin_candidate(
        raw,
        strategy_key=strategy_key if "strategy_key" in raw else "",
        source_default=str(raw.get("source", "plugin_strategy") or "plugin_strategy"),
    )
    if not normalized:
        return {}

    # Keep backward-compatible keys used by existing adapters/tests.
    if "site" in raw:
        normalized["site"] = raw.get("site")
    if "task" in raw:
        normalized["task"] = raw.get("task")
    if "source" not in raw:
        normalized.pop("source", None)
    if "strategy_key" not in raw:
        normalized.pop("strategy_key", None)
    return normalized


@dataclass(frozen=True)
class HtmlLLMExtractionStrategy:
    """Adapter for `parse_html_with_llm`."""

    key: str = "html_llm"

    def strategy_key(self) -> str:
        return self.key

    def extract(
        self,
        *,
        html: str,
        screenshot_path: Optional[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run HTML extraction via existing code-model parser."""
        _ = screenshot_path
        site = str(context.get("site", "") or "")
        task = str(context.get("task", "price") or "price")
        timeout_sec = context.get("timeout_sec")
        try:
            raw = code_model.parse_html_with_llm(
                html=html,
                site=site,
                task=task,
                timeout_sec=timeout_sec,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            raw = code_model.parse_html_with_llm(
                html=html,
                site=site,
                task=task,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}


@dataclass(frozen=True)
class VLMImageExtractionStrategy:
    """Adapter for `parse_image_with_vlm`."""

    key: str = "vlm_image"

    def strategy_key(self) -> str:
        return self.key

    def extract(
        self,
        *,
        html: str,
        screenshot_path: Optional[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run screenshot-only extraction via existing VLM parser."""
        _ = html
        site = str(context.get("site", "") or "")
        task = str(context.get("task", "price") or "price")
        timeout_sec = context.get("timeout_sec")
        if not isinstance(screenshot_path, str) or not screenshot_path.strip():
            return _fallback_payload(site=site, task=task, reason="vlm_image_unavailable")
        try:
            raw = code_model.parse_image_with_vlm(
                screenshot_path.strip(),
                site=site,
                task=task,
                origin=str(context.get("origin", "") or ""),
                dest=str(context.get("dest", "") or ""),
                depart=str(context.get("depart", "") or ""),
                return_date=str(context.get("return_date", "") or ""),
                timeout_sec=timeout_sec,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            raw = code_model.parse_image_with_vlm(
                screenshot_path.strip(),
                site=site,
                task=task,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}


@dataclass(frozen=True)
class VLMMultimodalExtractionStrategy:
    """Adapter for `parse_page_multimodal_with_vlm`."""

    key: str = "vlm_multimodal"

    def strategy_key(self) -> str:
        return self.key

    def extract(
        self,
        *,
        html: str,
        screenshot_path: Optional[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run screenshot+DOM extraction via existing multimodal parser."""
        site = str(context.get("site", "") or "")
        task = str(context.get("task", "price") or "price")
        timeout_sec = context.get("timeout_sec")
        if not isinstance(screenshot_path, str) or not screenshot_path.strip():
            return _fallback_payload(site=site, task=task, reason="vlm_image_unavailable")
        try:
            raw = code_model.parse_page_multimodal_with_vlm(
                image_path=screenshot_path.strip(),
                html=html,
                site=site,
                task=task,
                origin=str(context.get("origin", "") or ""),
                dest=str(context.get("dest", "") or ""),
                depart=str(context.get("depart", "") or ""),
                return_date=str(context.get("return_date", "") or ""),
                timeout_sec=timeout_sec,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            raw = code_model.parse_page_multimodal_with_vlm(
                image_path=screenshot_path.strip(),
                html=html,
                site=site,
                task=task,
            )
            if isinstance(raw, dict):
                return _normalize_preserving_legacy_shape(raw, strategy_key=self.key)
            return {}


def build_default_extraction_strategies() -> Dict[str, object]:
    """Default strategy registry entries mapped to existing behavior."""
    return {
        "html_llm": HtmlLLMExtractionStrategy(),
        "vlm_image": VLMImageExtractionStrategy(),
        "vlm_multimodal": VLMMultimodalExtractionStrategy(),
    }
