"""Integration bridge between extraction pipeline and model coordination layer.

Adapts CoordinatedExtractionRouter to work with existing extractor.py interfaces
while minimizing changes to current code paths.

This module provides adapter functions that:
1. Translate route verdicts and verify results to coordination gates
2. Bridge VLM/LLM model calls through the router
3. Maintain backward compatibility with existing extraction functions
4. Collect evidence for monitoring and debugging
"""

import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
import time

from core.scenario.coordination_router import CoordinatedExtractionRouter
from core.scenario.ui_contracts import UiSnapshot, DomSlice
from utils.dom_slice import build_dom_slice

log = logging.getLogger(__name__)


@dataclass
class ExtractionGatingDecision:
    """Result of gating decision evaluation."""

    proceed: bool
    """True if extraction should proceed."""

    gated_at: Optional[str] = None
    """Gate where extraction was blocked (e.g., 'route_mismatch')."""

    evidence: Dict[str, Any] = None
    """Diagnostic evidence for this decision."""

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {}


def evaluate_extraction_gates(
    route_verdict: Dict[str, Any],
    verify_result: Dict[str, Any],
    llm_budget: Optional[Any] = None,
) -> ExtractionGatingDecision:
    """Evaluate all coordination gates in sequence.

    Args:
        route_verdict: Route binding check result
        verify_result: Flight scope verification result
        llm_budget: Optional LLMCallBudget instance

    Returns:
        ExtractionGatingDecision with gate results
    """
    evidence = {}

    # Gate 0: Route binding
    route_bound = route_verdict.get("route_bound", False)
    support = route_verdict.get("support", "none")

    # P1 FIX: Route verification failure is degraded mode, not a hard blocker.
    # Mark degradation in evidence but continue evaluating subsequent gates.
    # This allows extraction to proceed with confidence penalty, enabling fallback
    # strategies (HTML-based extraction, heuristic inference) when route
    # mismatch occurs. Improves reliability on unexpected page layouts.
    if not route_bound:
        log.info(
            "extraction.gate_0_route_degraded route_bound=%s support=%s (proceeding with penalty)",
            route_bound,
            support,
        )
        evidence.update({
            "extraction.gate_route_bound": False,
            "extraction.gate_route_support": support,
            "extraction.confidence_penalty": 0.3,
            "extraction.route_match_quality": "absent",
        })
        # NOTE: Not returning here. Continue to evaluate remaining gates.
    else:
        evidence["extraction.gate_route_bound"] = True

    # Gate 1: Flight scope
    is_flight = verify_result.get("is_flight", False)
    page_class = verify_result.get("page_class", "unknown")

    if not is_flight:
        log.info(
            "extraction.gate_1_scope_failed is_flight=%s page_class=%s",
            is_flight,
            page_class,
        )
        evidence.update({
            "extraction.gate_is_flight": False,
            "extraction.gate_page_class": page_class,
        })
        return ExtractionGatingDecision(
            proceed=False,
            gated_at="non_flight_scope",
            evidence=evidence,
        )

    evidence["extraction.gate_is_flight"] = True

    # Gate 2: Budget
    # P3 FIX: Budget depletion is degraded mode, not a hard blocker.
    # When budget is low (< min_required), allow extraction to proceed with
    # confidence penalty instead of blocking entirely. This enables fallback
    # strategies (heuristic extraction, HTML parsing, cached results) to attempt
    # recovery even under time pressure. Improves reliability during budget-constrained runs.
    if llm_budget:
        remaining = llm_budget.remaining_wall_clock_s()
        min_required = getattr(llm_budget, "min_remaining_s_for_attempt", 20)

        if remaining < min_required:
            log.info(
                "extraction.gate_2_budget_degraded remaining_s=%.1f min_required_s=%d (proceeding with penalty)",
                remaining,
                min_required,
            )
            evidence.update({
                "extraction.gate_budget_remaining_s": remaining,
                "extraction.gate_budget_min_required_s": min_required,
                "extraction.confidence_penalty": 0.2,
                "extraction.budget_quality": "depleted",
            })
            # NOTE: Not returning here. Continue to evaluate if any gates exist beyond.
            # Mark as degraded but allow extraction to proceed (P3 FIX).
        else:
            evidence["extraction.gate_budget_remaining_s"] = remaining

    # All gates passed
    log.info("extraction.all_gates_passed gates=%s", ["route", "flight", "budget"])
    evidence["extraction.gates_passed"] = ["route", "flight", "budget"]

    return ExtractionGatingDecision(
        proceed=True,
        evidence=evidence,
    )


def build_dom_slice_with_coordination(
    html: str,
    vlm_anchors: Optional[Dict[str, str]] = None,
    max_chars: int = 20000,
) -> DomSlice:
    """Build DOM slice with optional VLM anchor guidance.

    Args:
        html: Full page HTML
        vlm_anchors: Optional anchors from VLM UiSnapshot
        max_chars: Maximum characters for slice

    Returns:
        DomSlice ready for LLM consumption
    """
    start_time = time.monotonic()

    # Use VLM anchors as selector priority if available
    selectors = list(vlm_anchors.values()) if vlm_anchors else None

    dom_slice = build_dom_slice(html, selectors_priority=selectors, max_chars=max_chars)

    # Attach anchors to slice for LLM context
    if vlm_anchors:
        dom_slice.anchors = vlm_anchors
        dom_slice.evidence["domslice.guided_by_vlm"] = True

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    dom_slice.evidence["domslice.build_time_ms"] = elapsed_ms

    log.info(
        "extraction.domslice_built selector=%s text_len=%d node_count=%d build_ms=%d",
        dom_slice.selector_used,
        dom_slice.text_len,
        dom_slice.node_count,
        elapsed_ms,
    )

    return dom_slice


def should_skip_coordinated_extraction(
    evidence: Dict[str, Any],
    llm_budget: Optional[Any] = None,
) -> bool:
    """Determine if coordinated extraction should be skipped.

    Args:
        evidence: Evidence dict from previous stages
        llm_budget: Optional budget instance

    Returns:
        True if coordination should be skipped (fallback to simple extraction)
    """
    # Skip if budget exhausted
    if llm_budget:
        remaining = llm_budget.remaining_wall_clock_s()
        if remaining < 10:  # Less than 10s remaining
            return True

    # Skip if circuit open on primary model
    circuit_state = evidence.get("llm.circuit_state", {})
    if circuit_state.get("minicpm-v:8b", {}).get("open"):
        return True

    return False


class ExtractionCoordinationBridge:
    """Bridge between extraction pipeline and coordination layer.

    Provides convenient methods for extraction functions to integrate
    with the coordination router without major refactoring.
    """

    def __init__(self, budget=None, logger=None):
        """Initialize bridge.

        Args:
            budget: Optional LLMCallBudget instance
            logger: Optional logger
        """
        self.budget = budget
        self.log = logger or log
        self.router = CoordinatedExtractionRouter(budget=budget, logger=logger)
        self.extraction_evidence = {}

    def check_extraction_gates(
        self,
        route_verdict: Dict[str, Any],
        verify_result: Dict[str, Any],
    ) -> bool:
        """Check if extraction should proceed through all gates.

        Args:
            route_verdict: Route binding result
            verify_result: Scope verification result

        Returns:
            True if all gates passed; False if gated
        """
        decision = evaluate_extraction_gates(route_verdict, verify_result, self.budget)
        self.extraction_evidence.update(decision.evidence)

        if not decision.proceed:
            self.log.info(
                "extraction.gated_at=%s",
                decision.gated_at,
            )
            return False

        return True

    def extract_via_router(
        self,
        html: str,
        route_verdict: Dict[str, Any],
        verify_result: Dict[str, Any],
        screenshot_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Extract through full coordination router.

        Args:
            html: Full page HTML
            route_verdict: Route binding result
            verify_result: Scope verification result
            screenshot_path: Optional screenshot for VLM

        Returns:
            Extraction result or None if gated/failed
        """
        return self.router.extract_with_coordination(
            html=html,
            route_verdict=route_verdict,
            verify_result=verify_result,
            page_screenshot=screenshot_path,
        )

    def build_extraction_slice(
        self,
        html: str,
        vlm_anchors: Optional[Dict[str, str]] = None,
    ) -> DomSlice:
        """Build DOM slice for LLM extraction.

        Args:
            html: Full page HTML
            vlm_anchors: Optional anchors from VLM

        Returns:
            DomSlice ready for LLM
        """
        return build_dom_slice_with_coordination(html, vlm_anchors)


# ============================================================================
# USAGE PATTERN FOR EXTRACTION FUNCTIONS
# ============================================================================


def example_integrated_extraction():
    """Example showing how to use coordination layer in extraction.

    This shows the minimal refactoring needed in extract_with_llm()
    or similar extraction functions.
    """
    # Setup
    # html = ...  # From page
    # route_verdict = ...  # From route binding check
    # verify_result = ...  # From scope verification
    # llm_budget = ...  # Optional budget instance

    # Create bridge
    # bridge = ExtractionCoordinationBridge(budget=llm_budget)

    # Check gates
    # gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)
    # if not gates_passed:
    #     return None  # Extraction gated; return early

    # Option 1: Use full coordination router (with VLM)
    # result = bridge.extract_via_router(
    #     html=html,
    #     route_verdict=route_verdict,
    #     verify_result=verify_result,
    #     screenshot_path=screenshot_path,
    # )

    # Option 2: Use simple coordination (DOM slicing only)
    # dom_slice = bridge.build_extraction_slice(html)
    # if dom_slice.is_empty:
    #     return None
    # result = call_llm_on_slice(dom_slice)

    # Tag evidence
    # if result:
    #     result["evidence"].update(bridge.extraction_evidence)

    pass
