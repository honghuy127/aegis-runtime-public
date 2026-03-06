"""Tests for UiSnapshot and DomSlice data contracts."""

import pytest
from core.scenario.ui_contracts import (
    UiSnapshot,
    DomSlice,
    validate_ui_snapshot,
    validate_dom_slice,
)


class TestUiSnapshot:
    """Tests for UiSnapshot dataclass."""

    def test_minimal_ui_snapshot_creation(self):
        """Create UiSnapshot with required fields only."""
        snapshot = UiSnapshot(
            page_kind="search_results",
            confidence=0.95,
        )
        assert snapshot.page_kind == "search_results"
        assert snapshot.confidence == 0.95
        assert snapshot.anchors == {}
        assert snapshot.validate() is None

    def test_full_ui_snapshot_with_anchors(self):
        """Create UiSnapshot with anchors and metadata."""
        snapshot = UiSnapshot(
            page_kind="details",
            confidence=0.87,
            anchors={
                "price_region": ".itinerary-price",
                "trip_card": "li[data-trip-id]",
                "header": "header.main",
            },
            route_form_state={
                "origin": "SFO",
                "dest": "LAX",
                "depart": "2026-03-15",
            },
            ui_tokens=["$250", "roundtrip", "2 hrs"],
            evidence={
                "ui.snapshot.received_at": "2026-02-21T10:30:00Z",
                "ui.snapshot.model_version": "minicpm-v:8b",
            },
        )
        assert snapshot.validate() is None
        assert len(snapshot.anchors) == 3
        assert snapshot.route_form_state["origin"] == "SFO"

    def test_confidence_validation_bounds_checked(self):
        """Confidence must be in [0.0, 1.0] range."""
        # Too high
        snapshot = UiSnapshot(page_kind="test", confidence=1.5)
        assert snapshot.validate() is not None

        # Too low
        snapshot = UiSnapshot(page_kind="test", confidence=-0.1)
        assert snapshot.validate() is not None

        # Valid bounds
        snapshot = UiSnapshot(page_kind="test", confidence=0.0)
        assert snapshot.validate() is None
        snapshot = UiSnapshot(page_kind="test", confidence=1.0)
        assert snapshot.validate() is None

    def test_page_kind_required_non_empty(self):
        """page_kind must be non-empty string."""
        snapshot = UiSnapshot(page_kind="", confidence=0.5)
        assert snapshot.validate() is not None

        snapshot = UiSnapshot(page_kind="valid_page", confidence=0.5)
        assert snapshot.validate() is None

    def test_anchors_must_be_dict_or_none(self):
        """anchors field accepts dict or None."""
        snapshot = UiSnapshot(page_kind="test", confidence=0.5, anchors={})
        assert snapshot.validate() is None

        snapshot = UiSnapshot(page_kind="test", confidence=0.5, anchors=None)
        assert snapshot.validate() is None

        snapshot = UiSnapshot(page_kind="test", confidence=0.5, anchors="invalid")
        assert snapshot.validate() is not None

    def test_route_form_state_optional(self):
        """route_form_state is optional but must be dict if provided."""
        snapshot = UiSnapshot(page_kind="test", confidence=0.5, route_form_state=None)
        assert snapshot.validate() is None

        snapshot = UiSnapshot(
            page_kind="test",
            confidence=0.5,
            route_form_state={"origin": "SFO"},
        )
        assert snapshot.validate() is None

        snapshot = UiSnapshot(
            page_kind="test",
            confidence=0.5,
            route_form_state="invalid",
        )
        assert snapshot.validate() is not None

    def test_ui_tokens_optional(self):
        """ui_tokens is optional but must be list if provided."""
        snapshot = UiSnapshot(page_kind="test", confidence=0.5, ui_tokens=None)
        assert snapshot.validate() is None

        snapshot = UiSnapshot(
            page_kind="test",
            confidence=0.5,
            ui_tokens=["token1", "token2"],
        )
        assert snapshot.validate() is None

        snapshot = UiSnapshot(
            page_kind="test",
            confidence=0.5,
            ui_tokens="invalid",
        )
        assert snapshot.validate() is not None


class TestUiSnapshotValidation:
    """Tests for validate_ui_snapshot function."""

    def test_valid_snapshot_json_passes(self):
        """Valid UiSnapshot JSON returns no error."""
        data = {
            "page_kind": "search_results",
            "confidence": 0.92,
            "anchors": {"price": ".fare-box"},
        }
        err = validate_ui_snapshot(data)
        assert err is None

    def test_missing_page_kind_fails(self):
        """Missing page_kind field fails validation."""
        data = {"confidence": 0.5}
        err = validate_ui_snapshot(data)
        assert err is not None
        assert "page_kind" in err or "required" in err

    def test_missing_confidence_fails(self):
        """Missing confidence field fails validation."""
        data = {"page_kind": "test"}
        err = validate_ui_snapshot(data)
        assert err is not None

    def test_invalid_confidence_fails(self):
        """Out-of-bounds confidence fails."""
        data = {
            "page_kind": "test",
            "confidence": 1.5,
        }
        err = validate_ui_snapshot(data)
        assert err is not None

    def test_non_dict_input_fails(self):
        """Non-dict input rejected."""
        err = validate_ui_snapshot("not a dict")
        assert err is not None

    def test_optional_fields_accepted(self):
        """Optional fields (anchors, route_form_state) can be included."""
        data = {
            "page_kind": "details",
            "confidence": 0.8,
            "anchors": {"price": ".amount"},
            "route_form_state": {"origin": "LAX"},
            "ui_tokens": ["round trip"],
        }
        err = validate_ui_snapshot(data)
        assert err is None


class TestDomSlice:
    """Tests for DomSlice dataclass."""

    def test_minimal_dom_slice_creation(self):
        """Create DomSlice with required fields."""
        slice_obj = DomSlice(
            html="<div>Content</div>",
            selector_used=".content",
            text_len=20,
            node_count=2,
        )
        assert slice_obj.html == "<div>Content</div>"
        assert slice_obj.selector_used == ".content"
        assert slice_obj.validate() is None

    def test_dom_slice_with_anchors_and_evidence(self):
        """Create DomSlice with optional fields."""
        slice_obj = DomSlice(
            html="<article>Price: $99</article>",
            selector_used="article",
            text_len=30,
            node_count=1,
            anchors={
                "price_box": ".price",
                "header": "header.main",
            },
            evidence={
                "domslice.extracted_at": "2026-02-21T10:30:00Z",
                "domslice.selector_strategy": "priority_match",
            },
        )
        assert slice_obj.validate() is None
        assert len(slice_obj.anchors) == 2
        assert "extracted_at" in str(slice_obj.evidence)

    def test_text_len_must_be_non_negative(self):
        """text_len must be >= 0."""
        slice_obj = DomSlice(
            html="<div>Text</div>",
            selector_used=".text",
            text_len=-5,
            node_count=1,
        )
        assert slice_obj.validate() is not None

    def test_node_count_must_be_non_negative(self):
        """node_count must be >= 0."""
        slice_obj = DomSlice(
            html="<div>Text</div>",
            selector_used=".text",
            text_len=10,
            node_count=-1,
        )
        assert slice_obj.validate() is not None

    def test_is_empty_property_small_slices(self):
        """is_empty = True when text_len < 10 or node_count == 0."""
        # Too small
        slice_obj = DomSlice(html="x", selector_used="x", text_len=5, node_count=1)
        assert slice_obj.is_empty

        # No nodes
        slice_obj = DomSlice(html="x" * 100, selector_used="x", text_len=100, node_count=0)
        assert slice_obj.is_empty

        # Valid
        slice_obj = DomSlice(html="x" * 50, selector_used="x", text_len=50, node_count=5)
        assert not slice_obj.is_empty

    def test_is_oversized_property(self):
        """is_oversized based on max_chars limit (50000)."""
        slice_obj = DomSlice(
            html="x" * 60000,
            selector_used="x",
            text_len=60000,
            node_count=100,
        )
        # Default limit is 50000
        assert slice_obj.is_oversized

        slice_obj = DomSlice(
            html="x" * 10000,
            selector_used="x",
            text_len=10000,
            node_count=50,
        )
        assert not slice_obj.is_oversized


class TestDomSliceValidation:
    """Tests for validate_dom_slice function."""

    def test_valid_dom_slice_json_passes(self):
        """Valid DomSlice JSON returns no error."""
        data = {
            "html": "<div>Content</div>",
            "selector_used": ".content",
            "text_len": 20,
            "node_count": 2,
        }
        err = validate_dom_slice(data)
        assert err is None

    def test_missing_required_fields_fails(self):
        """Missing required fields cause validation failure."""
        data = {
            "html": "<div>Content</div>",
            "selector_used": ".content",
            # Missing text_len and node_count
        }
        err = validate_dom_slice(data)
        assert err is not None

    def test_non_dict_input_fails(self):
        """Non-dict input rejected."""
        err = validate_dom_slice([1, 2, 3])
        assert err is not None

    def test_optional_anchors_field(self):
        """anchors field is optional."""
        data = {
            "html": "<div>Content</div>",
            "selector_used": ".content",
            "text_len": 20,
            "node_count": 2,
            "anchors": {"price": ".amount"},
        }
        err = validate_dom_slice(data)
        assert err is None

    def test_negative_text_len_fails(self):
        """Negative text_len fails."""
        data = {
            "html": "<div>Content</div>",
            "selector_used": ".content",
            "text_len": -10,
            "node_count": 2,
        }
        err = validate_dom_slice(data)
        assert err is not None


class TestIntegration:
    """Integration tests for data contract workflow."""

    def test_ui_snapshot_to_dom_slice_workflow(self):
        """Create UiSnapshot, use anchors to guide DomSlice."""
        # VLM produces snapshot
        snapshot = UiSnapshot(
            page_kind="search_results",
            confidence=0.9,
            anchors={
                "price_container": ".card-price",
                "trip_card": ".trip-item",
            },
        )
        assert snapshot.validate() is None

        # Extractor uses anchors in DomSlice
        slice_obj = DomSlice(
            html="<div class='card-price'>$199</div>",
            selector_used=".card-price",
            text_len=40,
            node_count=2,
            anchors=snapshot.anchors,
        )
        assert slice_obj.validate() is None
        assert slice_obj.anchors["price_container"] == ".card-price"

    def test_full_coordination_happy_path(self):
        """Full model coordination: VLM → DomSlice → LLM."""
        # 1. VLM analyzes page
        snapshot = UiSnapshot(
            page_kind="search_results",
            confidence=0.88,
            anchors={"price": ".price-box", "card": "article"},
        )
        assert snapshot.validate() is None

        # 2. Extractor builds slice using anchors
        slice_obj = DomSlice(
            html="<article><span class='price-box'>$250</span></article>",
            selector_used=".price-box",
            text_len=55,
            node_count=3,
            anchors=snapshot.anchors,
            evidence={
                "domslice.guided_by_snapshot": True,
            },
        )
        assert slice_obj.validate() is None
        assert not slice_obj.is_empty

        # 3. LLM consumes slice (not full HTML)
        assert "$250" in slice_obj.html
        assert len(slice_obj.html) < 100  # Compact
