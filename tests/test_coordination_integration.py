"""Integration tests for extraction coordination bridge.

Tests show how to use the coordination layer in real extraction scenarios
with minimal changes to existing code.
"""

import pytest
from unittest.mock import MagicMock
from core.scenario.coordination_integration import (
    ExtractionGatingDecision,
    evaluate_extraction_gates,
    build_dom_slice_with_coordination,
    should_skip_coordinated_extraction,
    ExtractionCoordinationBridge,
)
from core.scenario.ui_contracts import DomSlice


class TestExtractionGatingDecision:
    """Tests for gating decision structure."""

    def test_proceeding_decision_has_no_gate(self):
        """Proceeding decision has gated_at=None."""
        decision = ExtractionGatingDecision(proceed=True)
        assert decision.proceed is True
        assert decision.gated_at is None

    def test_gated_decision_has_reason(self):
        """Gated decision has specific reason."""
        decision = ExtractionGatingDecision(
            proceed=False,
            gated_at="route_mismatch",
        )
        assert decision.proceed is False
        assert decision.gated_at == "route_mismatch"

    def test_decision_carries_evidence(self):
        """Decision includes diagnostic evidence."""
        evidence = {"extraction.route_bound": False}
        decision = ExtractionGatingDecision(
            proceed=False,
            gated_at="route_mismatch",
            evidence=evidence,
        )
        assert decision.evidence["extraction.route_bound"] is False


class TestEvaluateExtractionGates:
    """Tests for gate evaluation logic."""

    def test_all_gates_pass_when_conditions_met(self):
        """All gates return proceed=True when route and flight valid."""
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}

        decision = evaluate_extraction_gates(route_verdict, verify_result)

        assert decision.proceed is True
        assert decision.gated_at is None
        assert "extraction.gates_passed" in decision.evidence

    def test_gate_0_route_bound_proceeds_with_penalty(self):
        """Gate 0 (route_bound) allows extraction with degraded confidence when route_bound=False."""
        route_verdict = {"route_bound": False, "support": "none"}
        verify_result = {"is_flight": True}

        decision = evaluate_extraction_gates(route_verdict, verify_result)

        # P1 FIX: Route verification failure is now degraded mode, not hard blocker
        assert decision.proceed is True
        assert decision.gated_at is None
        assert decision.evidence["extraction.gate_route_bound"] is False
        assert decision.evidence["extraction.confidence_penalty"] == 0.3
        assert decision.evidence["extraction.route_match_quality"] == "absent"

    def test_gate_1_flight_scope_blocks(self):
        """Gate 1 (is_flight) blocks if is_flight=False."""
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": False, "page_class": "hotel_search"}

        decision = evaluate_extraction_gates(route_verdict, verify_result)

        assert decision.proceed is False
        assert decision.gated_at == "non_flight_scope"
        assert decision.evidence["extraction.gate_is_flight"] is False

    def test_gate_2_budget_proceeds_with_penalty(self):
        """Gate 2 (budget) allows extraction with degraded confidence when insufficient."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 5
        budget.min_remaining_s_for_attempt = 20

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}

        decision = evaluate_extraction_gates(route_verdict, verify_result, llm_budget=budget)

        # P3 FIX: Budget exhaustion is now degraded mode, not hard block
        assert decision.proceed is True
        assert decision.gated_at is None
        assert decision.evidence["extraction.gate_budget_remaining_s"] == 5
        assert decision.evidence["extraction.confidence_penalty"] == 0.2
        assert decision.evidence["extraction.budget_quality"] == "depleted"

    def test_gates_continue_even_when_route_degraded(self):
        """Gate 0 allows extraction with penalty; Gate 1 still checked and can block."""
        route_verdict = {"route_bound": False}  # Gate 0 degrades but allows
        verify_result = {"is_flight": False}     # Gate 1 blocks

        decision = evaluate_extraction_gates(route_verdict, verify_result)

        # P1 FIX: Gate 0 no longer blocks; Gate 1 continues evaluation and blocks
        assert decision.proceed is False
        assert decision.gated_at == "non_flight_scope"
        # Gate 0 evidence recorded (degraded mode)
        assert decision.evidence["extraction.gate_route_bound"] is False
        assert decision.evidence["extraction.confidence_penalty"] == 0.3
        # Gate 1 evaluated and blocked
        assert decision.evidence["extraction.gate_is_flight"] is False


class TestBuildDomSliceWithCoordination:
    """Tests for coordinated DOM slice building."""

    def test_slice_uses_vlm_anchors_if_provided(self):
        """Slice uses VLM anchors as selector priority."""
        html = """
        <div>
            <article class="trip-card">
                <span class="price-amt">$250</span>
            </article>
        </div>
        """
        anchors = {
            "price": ".price-amt",
            "card": ".trip-card",
        }

        dom_slice = build_dom_slice_with_coordination(html, vlm_anchors=anchors)

        assert dom_slice.anchors == anchors
        assert dom_slice.evidence["domslice.guided_by_vlm"] is True

    def test_slice_works_without_anchors(self):
        """Slice builds successfully with no VLM anchors."""
        html = "<div class='price'>$100</div>"

        dom_slice = build_dom_slice_with_coordination(html, vlm_anchors=None)

        assert dom_slice is not None
        assert "domslice.build_time_ms" in dom_slice.evidence

    def test_slice_respects_max_chars(self):
        """Slice respects max_chars limit."""
        html = "x" * 50000

        dom_slice = build_dom_slice_with_coordination(html, max_chars=5000)

        assert dom_slice.text_len <= 5000

    def test_slice_logs_build_time(self):
        """Slice records build time in evidence."""
        html = "<div>Content</div>"

        dom_slice = build_dom_slice_with_coordination(html)

        assert "domslice.build_time_ms" in dom_slice.evidence
        assert dom_slice.evidence["domslice.build_time_ms"] >= 0


class TestShouldSkipCoordinatedExtraction:
    """Tests for extraction skip decision."""

    def test_skip_when_budget_exhausted(self):
        """Skip if remaining budget < 10s."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 5

        should_skip = should_skip_coordinated_extraction({}, llm_budget=budget)

        assert should_skip is True

    def test_dont_skip_when_budget_sufficient(self):
        """Don't skip if remaining budget > 10s."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 50

        should_skip = should_skip_coordinated_extraction({}, llm_budget=budget)

        assert should_skip is False

    def test_skip_when_circuit_open(self):
        """Skip if circuit open on primary model."""
        evidence = {
            "llm.circuit_state": {
                "minicpm-v:8b": {"open": True}
            }
        }

        should_skip = should_skip_coordinated_extraction(evidence)

        assert should_skip is True

    def test_dont_skip_when_no_circuit_open(self):
        """Don't skip if circuit not open."""
        evidence = {"llm.circuit_state": {}}

        should_skip = should_skip_coordinated_extraction(evidence)

        assert should_skip is False


class TestExtractionCoordinationBridge:
    """Tests for extraction coordination bridge."""

    def test_bridge_initialization(self):
        """Bridge initializes with budget and logger."""
        budget = MagicMock()
        bridge = ExtractionCoordinationBridge(budget=budget)

        assert bridge.budget == budget
        assert bridge.router is not None

    def test_bridge_checks_gates_correctly(self):
        """Bridge.check_extraction_gates respects all gates."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}

        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)

        assert gates_passed is True

    def test_bridge_gates_route_mismatch_proceeds_with_penalty(self):
        """Bridge allows extraction gate even with route mismatch (degraded mode)."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": False}
        verify_result = {"is_flight": True}

        # P1 FIX: Route mismatch now proceeds with degraded confidence
        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)

        assert gates_passed is True
        assert bridge.extraction_evidence["extraction.gate_route_bound"] is False
        assert bridge.extraction_evidence["extraction.confidence_penalty"] == 0.3

    def test_bridge_builds_dom_slice(self):
        """Bridge.build_extraction_slice creates DomSlice."""
        bridge = ExtractionCoordinationBridge()

        html = "<div class='price'>$150</div>"
        dom_slice = bridge.build_extraction_slice(html)

        assert dom_slice is not None
        assert dom_slice.text_len > 0

    def test_bridge_builds_slice_with_anchors(self):
        """Bridge builds DomSlice with VLM anchors."""
        bridge = ExtractionCoordinationBridge()

        html = "<article><span class='price'>$200</span></article>"
        anchors = {"price": ".price"}

        dom_slice = bridge.build_extraction_slice(html, vlm_anchors=anchors)

        assert dom_slice.anchors == anchors


class TestExtractionIntegrationScenarios:
    """Integration tests with realistic extraction scenarios."""

    def test_scenario_route_mismatch_proceeds_with_degradation(self):
        """Route mismatch → extraction proceeds with degraded confidence."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": False, "origin": "NYC", "dest": "LAX"}
        verify_result = {"is_flight": True}

        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)

        # P1 FIX: Extraction now proceeds even with route mismatch
        assert gates_passed is True
        # Evidence shows degradation
        assert "extraction.gate_route_bound" in bridge.extraction_evidence
        assert bridge.extraction_evidence["extraction.confidence_penalty"] == 0.3

    def test_scenario_hotel_page_skips_extraction(self):
        """Hotel page (non-flight) → extraction blocked."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": False, "page_class": "hotel_search"}

        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)

        assert gates_passed is False
        assert bridge.extraction_evidence["extraction.gate_is_flight"] is False

    def test_scenario_valid_flight_page_builds_slice(self):
        """Valid flight page → build DomSlice for LLM."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}

        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)
        assert gates_passed is True

        # Now build slice for LLM
        html = """
        <html>
        <body>
            <div class="price-container">$299</div>
        </body>
        </html>
        """
        dom_slice = bridge.build_extraction_slice(html)

        assert dom_slice is not None
        assert not dom_slice.is_empty

    def test_scenario_budget_exhaustion_proceeds_with_degradation(self):
        """Budget exhaustion → extraction proceeds with degraded confidence."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 5
        budget.min_remaining_s_for_attempt = 20

        bridge = ExtractionCoordinationBridge(budget=budget)

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}

        gates_passed = bridge.check_extraction_gates(route_verdict, verify_result)

        # P3 FIX: Budget exhaustion now allows extraction with degraded confidence
        assert gates_passed is True
        assert bridge.extraction_evidence["extraction.gate_budget_remaining_s"] == 5
        assert bridge.extraction_evidence["extraction.confidence_penalty"] == 0.2

    def test_scenario_circuit_open_model_skips(self):
        """Circuit open on VLM → skip coordinated extraction."""
        evidence = {
            "llm.circuit_state": {
                "minicpm-v:8b": {"open": True, "until_s": 120}
            }
        }

        should_skip = should_skip_coordinated_extraction(evidence)

        assert should_skip is True

    def test_scenario_vlm_anchors_guide_slicing(self):
        """VLM produces anchors → use to guide DOM slicing."""
        bridge = ExtractionCoordinationBridge()

        # Simulate VLM output
        vlm_anchors = {
            "price_container": ".fare-box",
            "trip_card": "li[data-trip-id]",
            "header": "header.navbar",
        }

        html = """
        <li data-trip-id="123">
            <div class="fare-box">$250</div>
        </li>
        """

        # Build slice using VLM anchors
        dom_slice = bridge.build_extraction_slice(html, vlm_anchors=vlm_anchors)

        # Slice should include anchors for LLM context
        assert dom_slice.anchors == vlm_anchors
        assert dom_slice.evidence["domslice.guided_by_vlm"] is True


class TestBridgeEvidenceCollection:
    """Tests for evidence collection and tagging."""

    def test_bridge_collects_gate_evidence(self):
        """Bridge accumulates evidence from all gates."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": False, "support": "none"}
        verify_result = {"is_flight": True}

        bridge.check_extraction_gates(route_verdict, verify_result)

        # Evidence should include gate results
        assert "extraction.gate_route_bound" in bridge.extraction_evidence
        assert bridge.extraction_evidence["extraction.gate_route_bound"] is False

    def test_bridge_evidence_includes_gate_reason(self):
        """Bridge evidence includes specific gate failure reason."""
        bridge = ExtractionCoordinationBridge()

        route_verdict = {"route_bound": False, "support": "none"}
        verify_result = {"is_flight": True}

        bridge.check_extraction_gates(route_verdict, verify_result)

        assert "extraction.gate_route_support" in bridge.extraction_evidence
        assert bridge.extraction_evidence["extraction.gate_route_support"] == "none"
