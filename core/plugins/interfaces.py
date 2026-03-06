"""Core plugin interface contracts (incremental, behavior-preserving)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ServicePlugin(Protocol):
    """Site-level plugin contract for URL/domain and optional scenario hooks."""

    # Canonical metadata fields for plugin-first routing.
    service_key: str
    display_name: str
    default_url: str
    base_domains: List[str]
    ui_profile_key: str

    # Backward-compatible aliases used by early adapters/tests.
    key: str
    name: str
    domains: List[str]

    def url_candidates(
        self,
        preferred_url: Optional[str] = None,
        is_domestic: Optional[bool] = None,
        *,
        knowledge: Optional[Dict[str, Any]] = None,
        seed_hints: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Return ordered URL candidates for one service."""

    def ui_profile(self) -> Optional[Dict[str, Any]]:
        """Optional UI profile payload for planner/repair flows."""

    def scenario_profile(self) -> Dict[str, Any]:
        """Service UI profile payload used by scenario/repair routing."""

    def readiness_probe(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Optional service-specific readiness probe.

        Expected keys:
        - ready: bool
        - page_class: flight_only|flight_hotel_package|garbage_page|irrelevant_page|unknown
        - trip_product: flight_only|flight_hotel_package|unknown
        - route_bound: bool|None
        - reason: str
        """

    def extraction_hints(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Optional extraction hints (selector overrides / known containers)."""

    def readiness_hints(
        self,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Optional scenario-readiness hints (e.g., wait selectors)."""

    def scope_hints(
        self,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Optional scenario scope hints (e.g., product/mode labels)."""


@runtime_checkable
class ExtractionStrategy(Protocol):
    """Extraction strategy contract for html/image/multimodal parsing."""

    key: str

    def strategy_key(self) -> str:
        """Return stable strategy identifier (e.g., html_llm, vlm_image)."""

    def extract(
        self,
        *,
        html: str,
        screenshot_path: Optional[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return one normalized extraction payload."""


@runtime_checkable
class LLMProvider(Protocol):
    """Provider contract for model calls."""

    key: str

    def call(self, prompt: str, model: str, **kwargs: Any) -> str:
        """Execute one model call and return raw text."""
