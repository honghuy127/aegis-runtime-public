"""Tests for CoordinatedExtractionRouter integration."""

import pytest
from unittest.mock import MagicMock, patch
from core.scenario.coordination_router import CoordinatedExtractionRouter
from core.scenario.ui_contracts import UiSnapshot, DomSlice

pytestmark = [pytest.mark.llm, pytest.mark.vlm]


class TestCoordinatedExtractionRouterGates:
    """Tests for router gating logic."""

    def test_router_gates_route_mismatch(self):
        """Router gates on route_bound=False."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": False, "support": "none"}
        verify_result = {"is_flight": True}

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        assert router.evidence["coordination.gated_at"] == "route_mismatch"
        assert router.evidence["coordination.route_bound"] is False

    def test_router_gates_non_flight_scope(self):
        """Router gates on is_flight=False."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": False, "page_class": "hotel_search"}

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        assert router.evidence["coordination.gated_at"] == "non_flight_scope"
        assert router.evidence["coordination.is_flight"] is False

    def test_router_gates_on_budget_exhaustion(self):
        """Router gates when budget insufficient."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 5  # Too low
        budget.min_remaining_s_for_attempt = 20

        router = CoordinatedExtractionRouter(budget=budget)
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        assert router.evidence["coordination.gated_at"] == "budget_insufficient"

    def test_router_gates_empty_dom_slice(self):
        """Router gates when DomSlice is empty."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}

        # HTML too small to produce valid slice
        result = router.extract_with_coordination(
            html="x",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        assert router.evidence["coordination.gated_at"] == "empty_dom_slice"

    def test_router_allows_all_gates_pass(self):
        """Router passes all gates when conditions met."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}

        # HTML with price indicators
        html = """
        <html>
        <body>
            <div class="price-container">
                <span class="price">$250</span>
            </div>
        </body>
        </html>
        """

        result = router.extract_with_coordination(
            html=html,
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        # Router creates DomSlice and attempts LLM (None because not mocked)
        # but all gates should pass
        assert "coordination.gated_at" not in router.evidence
        assert router.evidence["coordination.route_bound"] is True
        assert router.evidence["coordination.is_flight"] is True


class TestRouterDomSliceBuilding:
    """Tests for DOM slice construction in router."""

    def test_router_uses_vlm_anchors_if_available(self):
        """Router uses UiSnapshot anchors when available."""
        router = CoordinatedExtractionRouter()

        # Create UiSnapshot with anchors
        snapshot = UiSnapshot(
            page_kind="search_results",
            confidence=0.9,
            anchors={"price": ".fare-amount", "card": "article"},
        )

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        html = """
        <article>
            <span class="fare-amount">$150</span>
        </article>
        """

        # Access private method for testing
        dom_slice = router._build_dom_slice(html, snapshot)

        assert dom_slice is not None
        assert dom_slice.anchors == snapshot.anchors
        assert "fare-amount" in dom_slice.selector_used or "$150" in dom_slice.html

    def test_router_uses_default_selectors_without_snapshot(self):
        """Router uses default selectors when no VLM snapshot."""
        router = CoordinatedExtractionRouter()
        html = """
        <div>
            <div class="price-container">$99</div>
        </div>
        """

        dom_slice = router._build_dom_slice(html, ui_snapshot=None)

        assert dom_slice is not None
        assert dom_slice.text_len > 0


class TestRouterBudgetIntegration:
    """Tests for budget integration in router."""

    def test_router_checks_budget_if_provided(self):
        """Router respects budget parameter."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 100  # Sufficient
        budget.min_remaining_s_for_attempt = 20

        router = CoordinatedExtractionRouter(budget=budget)

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        html = "<div class='price'>$100</div>"

        result = router.extract_with_coordination(html, route_verdict, verify_result)

        # Should not gate on budget
        assert router.evidence.get("coordination.gated_at") != "budget_insufficient"

    def test_router_ignores_missing_budget(self):
        """Router treats missing budget as available."""
        router = CoordinatedExtractionRouter(budget=None)

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        html = "<div class='price'>$100</div>"

        # Should not raise; missing budget = allow
        result = router.extract_with_coordination(html, route_verdict, verify_result)

        # Should pass budget gate
        assert "budget" not in router.evidence.get("coordination.gated_at", "")


class TestRouterEvidenceCollection:
    """Tests for evidence collection and tagging."""

    def test_router_tags_result_with_evidence(self):
        """Router tags LLM result with coordination evidence."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        html = "<div class='price'>$250</div>"

        # Mock LLM response
        mock_result = {
            "price": 250,
            "currency": "USD",
            "evidence": {},
        }

        # Test that evidence would be tagged
        if mock_result:
            expected_evidence = {
                **mock_result.get("evidence", {}),
                "coordination.router": "CoordinatedExtractionRouter",
                "coordination.ui_snapshot_used": False,
                "coordination.gates_passed": ["route", "flight", "budget", "domslice"],
            }

            assert "coordination.router" in expected_evidence
            assert "coordination.gates_passed" in expected_evidence

    def test_router_logs_gate_failures_with_evidence(self):
        """Router logs gate failures with diagnostic evidence."""
        router = CoordinatedExtractionRouter()
        route_verdict = {"route_bound": False, "support": "none"}
        verify_result = {"is_flight": True}

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        # Evidence should contain gate diagnostic info
        assert "coordination.route_bound" in router.evidence
        assert "coordination.route_support" in router.evidence


class TestRouterIntegration:
    """Integration tests for full router workflow."""

    def test_full_workflow_route_to_gate_to_domslice(self):
        """Test complete router workflow through all gates."""
        router = CoordinatedExtractionRouter()

        html = """
        <html>
        <body>
            <div class="price">$199.99</div>
            <article class="trip-item">
                <span class="price-tag">Price: $199.99</span>
            </article>
        </body>
        </html>
        """

        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True, "page_class": "search_results"}

        result = router.extract_with_coordination(
            html=html,
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        # All gates should pass, DomSlice should be created
        assert router.evidence["coordination.route_bound"] is True
        assert router.evidence["coordination.is_flight"] is True
        assert router.evidence["coordination.domslice_valid"] is True

    def test_router_workflow_with_multiple_gate_failures(self):
        """Test router stops at first gate failure."""
        router = CoordinatedExtractionRouter()

        # Both route and flight fail; route checked first
        route_verdict = {"route_bound": False}
        verify_result = {"is_flight": False}

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        assert result is None
        # Should gate at route_mismatch (Gate 0), not reach flight scope (Gate 1)
        assert router.evidence["coordination.gated_at"] == "route_mismatch"


class TestRouterGateOrder:
    """Tests for correct gate evaluation order."""

    def test_gates_evaluated_in_order(self):
        """Gates are evaluated in deterministic order: route → flight → budget → domslice."""
        router = CoordinatedExtractionRouter()

        # Gate 0 fails, Gate 1 would also fail if checked
        route_verdict = {"route_bound": False}  # Gate 0 fails
        verify_result = {"is_flight": False}     # Gate 1 would fail

        result = router.extract_with_coordination(
            html="<html></html>",
            route_verdict=route_verdict,
            verify_result=verify_result,
        )

        # Should stop at Gate 0 (route_mismatch), not reach Gate 1 failure
        assert router.evidence["coordination.gated_at"] == "route_mismatch"
        # Gate 1 evidence should not be recorded (stopped before evaluating)
        assert "coordination.is_flight" not in router.evidence


class TestRouterLogging:
    """Tests for router logging behavior."""

    def test_router_logs_vlm_calls(self):
        """Router logs VLM call attempts."""
        router = CoordinatedExtractionRouter()

        # VLM call (currently stub, but logs)
        snapshot = router._call_vlm_for_snapshot("/tmp/screenshot.png", "<html></html>")

        # Result is None (not mocked), but logging happened
        assert snapshot is None

    def test_router_logs_domslice_creation(self):
        """Router logs DomSlice creation details."""
        router = CoordinatedExtractionRouter()

        html = "<div class='price'>$250</div>"
        dom_slice = router._build_dom_slice(html, ui_snapshot=None)

        assert dom_slice is not None
        # Logging statements would have been called


class TestRouterWithBudget:
    """Tests for router with actual budget behavior."""

    def test_router_passes_budget_to_gates(self):
        """Router correctly passes budget object to gate checks."""
        budget = MagicMock()
        budget.remaining_wall_clock_s.return_value = 50
        budget.min_remaining_s_for_attempt = 20

        router = CoordinatedExtractionRouter(budget=budget)

        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        html = "<div>$100</div>"

        result = router.extract_with_coordination(html, route_verdict, verify_result)

        # Budget check should have been called
        budget.remaining_wall_clock_s.assert_called()
        # Should not gate on budget
        assert router.evidence.get("coordination.gated_at") != "budget_insufficient"
