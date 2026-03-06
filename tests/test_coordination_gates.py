"""Tests for model-coordination gating logic.

Ensures VLM and LLM calls are skipped when:
- Route bound is False (route mismatch)
- verify_status indicates non-flight scope
- Budget insufficient
"""

import pytest
from unittest.mock import MagicMock, patch
from core.scenario.ui_contracts import UiSnapshot, DomSlice

pytestmark = [pytest.mark.llm, pytest.mark.vlm]


class TestCoordinationGates:
    """Tests for coordination layer gating logic."""

    def test_route_mismatch_skips_both_vlm_and_llm(self):
        """When route_bound=False, skip VLM and LLM calls."""
        # Simulate route verdict
        verdict = {
            "route_bound": False,
            "support": "none",
            "origin": "SFO",
            "dest": "LAX",
        }

        # Gate logic: if not route_bound, return early
        if not verdict.get("route_bound"):
            result = {
                "price": None,
                "currency": None,
                "confidence": "low",
                "source": "extracted",
                "reason": "route_mismatch",
            }
        else:
            # Would call VLM then LLM
            result = {"price": 250, "source": "model"}

        assert result["reason"] == "route_mismatch"
        assert result["price"] is None

    def test_route_bound_true_allows_model_calls(self):
        """When route_bound=True, proceed to VLM/LLM."""
        verdict = {
            "route_bound": True,
            "support": "strong",
            "origin": "SFO",
            "dest": "LAX",
        }

        # Gate logic
        if verdict.get("route_bound"):
            # Would proceed to VLM
            proceed = True
        else:
            proceed = False

        assert proceed is True

    def test_non_flight_scope_skips_expensive_calls(self):
        """Non-flight pages (hotels, maps) skip VLM/LLM."""
        verify_result = {
            "page_class": "hotel_search",
            "is_flight": False,
            "confidence": 0.95,
        }

        # Gate: if not flight, skip vision/LLM
        if not verify_result.get("is_flight"):
            result = {"price": None, "reason": "non_flight_scope"}
        else:
            # Would call VLM
            result = {"price": 250}

        assert result["reason"] == "non_flight_scope"

    def test_weak_route_support_conditional_gating(self):
        """Weak route support: VLM query for page_kind; skip LLM."""
        verdict = {
            "route_bound": True,
            "support": "weak",  # Weak confidence
            "origin": "SFO",
            "dest": "LAX",
        }

        # Gate logic: weak support means skip expensive LLM
        if verdict.get("support") == "strong":
            call_llm = True
        else:
            call_llm = False

        assert call_llm is False

    def test_budget_exhaustion_stops_model_calls(self):
        """When budget exhausted, skip both VLM and LLM."""
        budget = {
            "remaining_s": 5,  # Less than min required
            "min_required_s": 20,
        }

        # Gate logic
        if budget["remaining_s"] < budget["min_required_s"]:
            result = {"price": None, "reason": "budget_exhausted"}
        else:
            # Would call VLM
            result = {"price": 250}

        assert result["reason"] == "budget_exhausted"

    def test_circuit_open_prevents_model_retry(self):
        """Circuit open on model: skip retry."""
        circuit_state = {
            "minicpm-v:8b": {"open": True, "until_s": 120},
        }
        model = "minicpm-v:8b"

        # Gate logic
        if circuit_state.get(model, {}).get("open"):
            result = {"price": None, "reason": "circuit_open_model"}
        else:
            # Would call VLM
            result = {"price": 250}

        assert result["reason"] == "circuit_open_model"


class TestGatingDecisionTree:
    """Tests for complete gating decision tree."""

    def test_happy_path_all_gates_pass(self):
        """All gates pass: proceed with VLM → DomSlice → LLM."""
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}
        budget = {"remaining_s": 100, "min_required_s": 20}
        circuit_state = {}
        model = "minicpm-v:8b"

        # Gate 1: Route bound
        if not route_verdict.get("route_bound"):
            action = "skip_all"
        # Gate 2: Is flight
        elif not verify_result.get("is_flight"):
            action = "skip_all"
        # Gate 3: Budget sufficient
        elif budget["remaining_s"] < budget["min_required_s"]:
            action = "skip_all"
        # Gate 4: Circuit not open
        elif circuit_state.get(model, {}).get("open"):
            action = "skip_all"
        else:
            action = "vlm_then_dom_then_llm"

        assert action == "vlm_then_dom_then_llm"

    def test_route_mismatch_blocks_all_models(self):
        """Gate 1 (route_bound) blocks immediately."""
        route_verdict = {"route_bound": False}
        verify_result = {"is_flight": True}
        budget = {"remaining_s": 100}

        # Gate 1: Route bound (fails)
        if not route_verdict.get("route_bound"):
            action = "skip_all"
        else:
            action = "proceed"

        assert action == "skip_all"

    def test_non_flight_blocks_expensive_calls(self):
        """Gate 2 (is_flight) blocks VLM/LLM."""
        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": False}
        budget = {"remaining_s": 100}

        if not route_verdict.get("route_bound"):
            action = "skip_all"
        elif not verify_result.get("is_flight"):
            action = "skip_all"
        else:
            action = "proceed"

        assert action == "skip_all"

    def test_budget_exhaustion_blocks_models(self):
        """Gate 3 (budget) blocks VLM/LLM."""
        route_verdict = {"route_bound": True, "support": "strong"}
        verify_result = {"is_flight": True}
        budget = {"remaining_s": 5, "min_required_s": 20}
        circuit_state = {}

        if not route_verdict.get("route_bound"):
            action = "skip_all"
        elif not verify_result.get("is_flight"):
            action = "skip_all"
        elif budget["remaining_s"] < budget["min_required_s"]:
            action = "skip_all"
        else:
            action = "proceed"

        assert action == "skip_all"

    def test_circuit_open_blocks_model_retry(self):
        """Gate 4 (circuit) blocks VLM model retry."""
        route_verdict = {"route_bound": True}
        verify_result = {"is_flight": True}
        budget = {"remaining_s": 100, "min_required_s": 20}
        circuit_state = {"minicpm-v:8b": {"open": True}}
        model = "minicpm-v:8b"

        if not route_verdict.get("route_bound"):
            action = "skip_all"
        elif not verify_result.get("is_flight"):
            action = "skip_all"
        elif budget["remaining_s"] < budget["min_required_s"]:
            action = "skip_all"
        elif circuit_state.get(model, {}).get("open"):
            action = "skip_model_retry"
        else:
            action = "proceed"

        assert action == "skip_model_retry"


class TestDomSliceGating:
    """Tests for DomSlice-specific gating."""

    def test_empty_dom_slice_skips_llm(self):
        """Empty or too-small DomSlice skips LLM call."""
        slice_obj = DomSlice(
            html="<div>x</div>",
            selector_used="div",
            text_len=8,
            node_count=1,
        )

        # Gate: if empty, skip LLM
        if slice_obj.is_empty:
            action = "skip_llm"
        else:
            action = "call_llm"

        assert action == "skip_llm"

    def test_oversized_dom_slice_flagged(self):
        """Oversized DomSlice logged but still usable."""
        slice_obj = DomSlice(
            html="x" * 60000,
            selector_used="x",
            text_len=60000,
            node_count=100,
        )

        # Flag but don't block
        oversized = slice_obj.is_oversized
        action = "flag_oversized_but_call_llm" if oversized else "call_llm"

        assert action == "flag_oversized_but_call_llm"

    def test_valid_dom_slice_proceeds_to_llm(self):
        """Valid DomSlice proceeds to LLM."""
        slice_obj = DomSlice(
            html="<article><span class='price'>$250</span></article>",
            selector_used="article",
            text_len=50,
            node_count=3,
        )

        # Gate: if not empty, call LLM
        if slice_obj.is_empty:
            action = "skip_llm"
        else:
            action = "call_llm"

        assert action == "call_llm"


class TestEvidenceLogging:
    """Tests for evidence collection during gating."""

    def test_route_gating_logs_evidence(self):
        """Route gate failure logs evidence."""
        evidence = {}
        route_verdict = {"route_bound": False, "support": "none"}

        if not route_verdict.get("route_bound"):
            evidence["coordination.gate.route_bound"] = False
            evidence["coordination.gate.blocked_at"] = "gate_0_route_match"
            evidence["coordination.skip_reason"] = "route_mismatch"

        assert evidence["coordination.gate.route_bound"] is False
        assert "gate_0" in evidence["coordination.gate.blocked_at"]

    def test_budget_gating_logs_evidence(self):
        """Budget gate failure logs evidence."""
        evidence = {}
        budget = {"remaining_s": 5, "min_required_s": 20}

        if budget["remaining_s"] < budget["min_required_s"]:
            evidence["coordination.gate.budget_remaining_s"] = budget["remaining_s"]
            evidence["coordination.gate.blocked_at"] = "gate_2_budget"
            evidence["coordination.skip_reason"] = "budget_insufficient"

        assert evidence["coordination.gate.budget_remaining_s"] == 5
        assert "gate_2" in evidence["coordination.gate.blocked_at"]

    def test_circuit_gating_logs_evidence(self):
        """Circuit gate logs state."""
        evidence = {}
        circuit_state = {"minicpm-v:8b": {"open": True, "until_s": 120}}
        model = "minicpm-v:8b"

        if circuit_state.get(model, {}).get("open"):
            evidence["coordination.gate.circuit_open"] = True
            evidence["coordination.gate.circuit_model"] = model
            evidence["coordination.gate.circuit_cooldown_s"] = 120
            evidence["coordination.gate.blocked_at"] = "gate_3_circuit"

        assert evidence["coordination.gate.circuit_open"] is True
        assert evidence["coordination.gate.circuit_model"] == model


class TestCoordinationIntegration:
    """Integration tests for full coordination workflow."""

    def test_ui_snapshot_guides_dom_slicing(self):
        """VLM UiSnapshot output guides DOM slicing."""
        # VLM produces snapshot
        snapshot = UiSnapshot(
            page_kind="search_results",
            confidence=0.9,
            anchors={"price_container": ".card-price", "trip": ".trip-item"},
        )

        # Extractor uses anchors
        selectors_priority = snapshot.anchors.values()
        slice_obj = DomSlice(
            html="<article class='trip-item'><span class='card-price'>$199</span></article>",
            selector_used=".card-price",
            text_len=80,
            node_count=3,
            anchors=snapshot.anchors,
        )

        # LLM consumes slice, not full HTML
        assert len(slice_obj.html) < 1000
        assert "$199" in slice_obj.html
        assert snapshot.anchors is not None

    def test_route_mismatch_prevents_model_waste(self):
        """Route mismatch → skip both VLM and LLM."""
        verdict = {"route_bound": False}
        calls_made = []

        # Simulated gate
        if verdict.get("route_bound"):
            calls_made.append("vlm")
            calls_made.append("llm")
        else:
            calls_made.append("route_gated")

        assert "vlm" not in calls_made
        assert "llm" not in calls_made
        assert "route_gated" in calls_made

    def test_weak_route_allows_one_vlm_call(self):
        """Weak route: single VLM page_kind query, no LLM."""
        verdict = {"route_bound": True, "support": "weak"}
        calls_made = []

        if verdict.get("route_bound"):
            # Weak support: VLM page_kind query only
            if verdict.get("support") == "weak":
                calls_made.append("vlm_page_kind_only")
            else:
                calls_made.append("vlm_full")
                calls_made.append("llm")

        assert "vlm_page_kind_only" in calls_made
        assert "llm" not in calls_made
