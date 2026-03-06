"""Extraction pipeline integration with model coordination layer.

Shows how to integrate UiSnapshot, DomSlice, and gating decisions
into the existing price extraction workflow.
"""

from typing import Dict, Any, Optional
import logging

from core.scenario.ui_contracts import UiSnapshot, DomSlice
from utils.dom_slice import build_dom_slice
from llm.prompts.coordination_prompts import (
    build_vlm_prompt_for_page,
    build_llm_prompt_for_dom_slice,
    parse_vlm_response,
    parse_llm_response,
)

log = logging.getLogger(__name__)


class CoordinatedExtractionRouter:
    """Orchestrates VLM and LLM calls with deterministic gating.

    Implements the coordination layer workflow:
    1. Gate 0: Check route binding
    2. Gate 1: Verify flight scope
    3. Gate 2: Budget available
    4. VLM: Analyze page → UiSnapshot
    5. DOM Slice: Build compact fragment using anchors
    6. Gate 3: DomSlice valid
    7. LLM: Extract prices from DomSlice
    """

    def __init__(self, budget=None, logger=None):
        """Initialize coordinator.

        Args:
            budget: Optional LLMCallBudget for monitoring
            logger: Optional logger instance
        """
        self.budget = budget
        self.log = logger or log
        self.evidence: Dict[str, Any] = {}

    def extract_with_coordination(
        self,
        html: str,
        route_verdict: Dict[str, Any],
        verify_result: Dict[str, Any],
        page_screenshot: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Extract price through coordination gates.

        Args:
            html: Full page HTML
            route_verdict: Route binding check result
            verify_result: Flight scope verification result
            page_screenshot: Optional screenshot path for VLM

        Returns:
            Price extraction result or None if gated
        """
        self.evidence = {}

        # Gate 0: Route binding check
        if not self._gate_route_match(route_verdict):
            self.evidence["coordination.gated_at"] = "route_mismatch"
            return None

        # Gate 1: Flight scope check
        if not self._gate_flight_scope(verify_result):
            self.evidence["coordination.gated_at"] = "non_flight_scope"
            return None

        # Gate 2: Budget check
        if not self._gate_budget():
            self.evidence["coordination.gated_at"] = "budget_insufficient"
            return None

        # Step 1: VLM analyzes page (if screenshot available)
        ui_snapshot = None
        if page_screenshot:
            ui_snapshot = self._call_vlm_for_snapshot(page_screenshot, html)
            if not ui_snapshot:
                self.log.info("coordination.vlm_snapshot_failed skipped screenshot")

        # Step 2: Build DomSlice using VLM anchors (or defaults)
        dom_slice = self._build_dom_slice(html, ui_snapshot)
        if not dom_slice or dom_slice.is_empty:
            self.evidence["coordination.gated_at"] = "empty_dom_slice"
            self.log.info("coordination.dom_slice_empty reason=%s",
                         dom_slice.evidence.get("domslice.skip_reason", "unknown"))
            return None

        # Gate 3: DomSlice size check
        if not self._gate_dom_slice(dom_slice):
            self.evidence["coordination.gated_at"] = "oversized_dom_slice"
            return None

        # Step 3: LLM extracts prices from DomSlice
        result = self._call_llm_for_prices(dom_slice, ui_snapshot)

        # Tag evidence with coordination metadata
        if result:
            result["evidence"] = {
                **result.get("evidence", {}),
                "coordination.router": "CoordinatedExtractionRouter",
                "coordination.ui_snapshot_used": ui_snapshot is not None,
                "coordination.dom_slice_selector": dom_slice.selector_used,
                "coordination.gates_passed": ["route", "flight", "budget", "domslice"],
            }

        return result

    def _gate_route_match(self, route_verdict: Dict[str, Any]) -> bool:
        """Gate 0: Check route binding.

        Args:
            route_verdict: Route binding verdict

        Returns:
            True if route is matched; False to skip models
        """
        route_bound = route_verdict.get("route_bound", False)
        support = route_verdict.get("support", "none")

        if not route_bound:
            self.log.info(
                "coordination.gate_0_route_failed route_bound=%s support=%s",
                route_bound,
                support,
            )
            self.evidence["coordination.route_bound"] = False
            self.evidence["coordination.route_support"] = support
            return False

        self.evidence["coordination.route_bound"] = True
        return True

    def _gate_flight_scope(self, verify_result: Dict[str, Any]) -> bool:
        """Gate 1: Check flight scope.

        Args:
            verify_result: Scope verification (page_class, is_flight)

        Returns:
            True if flight page; False to skip expensive calls
        """
        is_flight = verify_result.get("is_flight", False)
        page_class = verify_result.get("page_class", "unknown")

        if not is_flight:
            self.log.info(
                "coordination.gate_1_scope_failed is_flight=%s page_class=%s",
                is_flight,
                page_class,
            )
            self.evidence["coordination.is_flight"] = False
            self.evidence["coordination.page_class"] = page_class
            return False

        self.evidence["coordination.is_flight"] = True
        return True

    def _gate_budget(self) -> bool:
        """Gate 2: Check budget available.

        Returns:
            True if budget sufficient; False to skip models
        """
        if not self.budget:
            return True  # No budget tracking; allow

        remaining = self.budget.remaining_wall_clock_s()
        min_required = self.budget.min_remaining_s_for_attempt or 20

        if remaining < min_required:
            self.log.info(
                "coordination.gate_2_budget_failed remaining_s=%.1f min_required_s=%d",
                remaining,
                min_required,
            )
            self.evidence["coordination.budget_remaining_s"] = remaining
            self.evidence["coordination.budget_min_required_s"] = min_required
            return False

        self.evidence["coordination.budget_remaining_s"] = remaining
        return True

    def _gate_dom_slice(self, dom_slice: DomSlice) -> bool:
        """Gate 3: Check DomSlice validity.

        Args:
            dom_slice: DOM slice to validate

        Returns:
            True if valid; False to skip LLM
        """
        if dom_slice.is_empty:
            self.log.info(
                "coordination.gate_3_domslice_empty text_len=%d node_count=%d",
                dom_slice.text_len,
                dom_slice.node_count,
            )
            self.evidence["coordination.domslice_valid"] = False
            return False

        if dom_slice.is_oversized:
            self.log.warning(
                "coordination.gate_3_domslice_oversized text_len=%d",
                dom_slice.text_len,
            )
            # Flag but allow (we've capped it)
            self.evidence["coordination.domslice_oversized"] = True

        self.evidence["coordination.domslice_valid"] = True
        return True

    def _call_vlm_for_snapshot(
        self,
        screenshot_path: str,
        html: str,
    ) -> Optional[UiSnapshot]:
        """Call VLM to analyze page and produce UiSnapshot.

        Args:
            screenshot_path: Path to page screenshot
            html: Full page HTML (for context)

        Returns:
            UiSnapshot or None if failed
        """
        try:
            # This would call the actual VLM model
            # For now, return stub to show integration pattern
            self.log.info(
                "coordination.vlm_call_would_happen screenshot=%s html_len=%d",
                screenshot_path,
                len(html),
            )
            # Actual implementation:
            # prompt = build_vlm_prompt_for_page(html)
            # response = call_ollama_vision(screenshot, prompt, model="minicpm-v:8b")
            # data = parse_vlm_response(response)
            # if data:
            #     return UiSnapshot(**data)
            return None
        except Exception as exc:
            self.log.error("coordination.vlm_failed error=%s", exc)
            return None

    def _build_dom_slice(
        self,
        html: str,
        ui_snapshot: Optional[UiSnapshot],
    ) -> Optional[DomSlice]:
        """Build DOM slice using VLM anchors or defaults.

        Args:
            html: Full page HTML
            ui_snapshot: Optional UiSnapshot with anchors

        Returns:
            DomSlice or None
        """
        # If VLM produced anchors, use them as selector priority
        if ui_snapshot and ui_snapshot.anchors:
            selectors = list(ui_snapshot.anchors.values())
        else:
            selectors = None  # Use defaults

        dom_slice = build_dom_slice(html, selectors_priority=selectors)

        # Attach anchors from snapshot to slice (for LLM context)
        if ui_snapshot and ui_snapshot.anchors:
            dom_slice.anchors = ui_snapshot.anchors

        self.log.info(
            "coordination.domslice_built selector=%s text_len=%d node_count=%d",
            dom_slice.selector_used,
            dom_slice.text_len,
            dom_slice.node_count,
        )

        return dom_slice

    def _call_llm_for_prices(
        self,
        dom_slice: DomSlice,
        ui_snapshot: Optional[UiSnapshot],
    ) -> Optional[Dict[str, Any]]:
        """Call LLM to extract prices from DomSlice.

        Args:
            dom_slice: Compact DOM fragment
            ui_snapshot: Optional VLM context

        Returns:
            Price extraction result or None
        """
        try:
            # This would call the actual LLM model
            # For now, return stub to show integration pattern
            self.log.info(
                "coordination.llm_call_would_happen domslice_len=%d anchors=%s",
                dom_slice.text_len,
                len(dom_slice.anchors or {}),
            )
            # Actual implementation:
            # prompt = build_llm_prompt_for_dom_slice(dom_slice)
            # response = call_ollama_llm(prompt, model="qwen2.5-coder:7b")
            # candidates = parse_llm_response(response)
            # if candidates:
            #     best = max(candidates, key=lambda x: x["confidence"])
            #     return {
            #         "price": best["price"],
            #         "currency": best["currency"],
            #         "source": "coordination_llm",
            #         "evidence": {...}
            #     }
            return None
        except Exception as exc:
            self.log.error("coordination.llm_failed error=%s", exc)
            return None


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

def example_usage():
    """Show how to use CoordinatedExtractionRouter.

    This is a reference implementation showing integration points.
    """
    # In extract_with_llm() or similar extraction function:

    html = "<html>...</html>"
    route_verdict = {"route_bound": True, "support": "strong"}
    verify_result = {"is_flight": True, "page_class": "search_results"}
    screenshot_path = "/tmp/screenshot.png"
    budget = None  # Optional: pass LLMCallBudget instance

    router = CoordinatedExtractionRouter(budget=budget)

    result = router.extract_with_coordination(
        html=html,
        route_verdict=route_verdict,
        verify_result=verify_result,
        page_screenshot=screenshot_path,
    )

    if result is None:
        print(f"Gated at: {router.evidence.get('coordination.gated_at')}")
    else:
        print(f"Extracted: {result['price']} {result['currency']}")
        print(f"Gates passed: {result['evidence']['coordination.gates_passed']}")
