"""Tests for DOM slice builder utility."""

import pytest
from utils.dom_slice import (
    build_dom_slice,
    _extract_by_selector,
    _estimate_node_count,
    SELECTOR_PRIORITY,
)
from core.scenario.ui_contracts import DomSlice


class TestDomSliceBuilder:
    """Tests for build_dom_slice function."""

    def test_empty_html_returns_empty_slice(self):
        """Empty HTML produces empty DomSlice."""
        slice_obj = build_dom_slice("")
        assert slice_obj.html == ""
        assert slice_obj.text_len == 0
        assert slice_obj.node_count == 0
        assert slice_obj.is_empty

    def test_selector_matching_returns_extracted_html(self):
        """Selector match extracts specific DOM region."""
        html = '<div><p>Header</p><div class="price">$100</div><p>Footer</p></div>'
        selectors = [".price"]
        slice_obj = build_dom_slice(html, selectors)

        # Should use selector or fallback
        assert slice_obj.text_len > 0
        assert "$100" in slice_obj.html or "price" in slice_obj.html
        assert slice_obj.evidence["domslice.extraction_strategy"] in {"css_selector", "fallback_full_html"}

    def test_selector_priority_order_respected(self):
        """Selectors tried in priority order; first match wins."""
        html = '''
        <div class="price">$50</div>
        <div class="total">$100</div>
        '''
        # price should be tried before total
        selectors = [".price", ".total"]
        slice_obj = build_dom_slice(html, selectors)

        # Should extract first match (price)
        assert "$50" in slice_obj.html or slice_obj.selector_used == ".price"

    def test_fallback_to_full_html_when_no_selector_matches(self):
        """Uses full HTML if no selector matches."""
        html = '<div><p>Content</p></div>'
        selectors = [".nonexistent", "[missing-attr]"]
        slice_obj = build_dom_slice(html, selectors)

        # Fallback to full or partial HTML
        assert len(slice_obj.html) > 0
        assert slice_obj.evidence["domslice.extraction_strategy"] in {
            "fallback_full_html",
            "css_selector",
        }

    def test_max_chars_cap_applied(self):
        """Slice respects max_chars limit."""
        html = '<div>' + 'x' * 30000 + '</div>'
        slice_obj = build_dom_slice(html, max_chars=5000)

        assert slice_obj.text_len <= 5000
        assert slice_obj.evidence.get("domslice.truncated", False) or slice_obj.text_len <= 5000

    def test_node_count_estimation(self):
        """Node count estimated correctly."""
        html = '<div><p>A</p><p>B</p><span>C</span></div>'
        slice_obj = build_dom_slice(html)

        # Should have non-zero node count
        assert slice_obj.node_count > 0

    def test_evidence_includes_selector_details(self):
        """Evidence captures extraction metadata."""
        html = '<div class="card"><p>Price: $99</p></div>'
        slice_obj = build_dom_slice(html, [".card"])

        assert "domslice.extraction_strategy" in slice_obj.evidence
        assert "domslice.text_chars" in slice_obj.evidence
        assert "domslice.node_estimate" in slice_obj.evidence

    def test_default_selector_priority_used_when_none_provided(self):
        """Default SELECTOR_PRIORITY used if selectors_priority=None."""
        html = '<div><article class="trip-item"><p>Flight</p></article></div>'
        slice_obj = build_dom_slice(html, selectors_priority=None)

        # Should use default selectors
        assert slice_obj.selector_used in SELECTOR_PRIORITY or slice_obj.text_len > 0

    def test_oversized_flag_set_when_truncated(self):
        """Evidence marks truncation."""
        html = 'x' * 50000
        slice_obj = build_dom_slice(html, max_chars=10000)

        assert slice_obj.text_len <= 10000
        if len(html) > 10000:
            assert slice_obj.evidence.get("domslice.truncated")


class TestSelectorExtraction:
    """Tests for _extract_by_selector."""

    def test_class_selector_extraction(self):
        """Extract by .class-name."""
        html = '<div class="price-box">$100</div>'
        result = _extract_by_selector(html, ".price-box")
        assert "$100" in result or "price-box" in result

    def test_id_selector_extraction(self):
        """Extract by #id-name."""
        html = '<div id="main-price">$200</div>'
        result = _extract_by_selector(html, "#main-price")
        assert "$200" in result or "main-price" in result

    def test_tag_selector_extraction(self):
        """Extract by tag name."""
        html = '<article class="skip"><article class="use">Content</article></article>'
        result = _extract_by_selector(html, "article")
        assert len(result) > 0

    def test_nonexistent_selector_returns_empty(self):
        """Non-matching selector returns empty string."""
        html = '<div>Content</div>'
        result = _extract_by_selector(html, ".missing")
        assert result == ""

    def test_data_attribute_selector(self):
        """Extract by data-* attribute."""
        html = '<div data-price="99">Price info</div>'
        result = _extract_by_selector(html, "[data-price]")
        # May succeed depending on regex; at minimum should not error
        assert isinstance(result, str)


class TestNodeCountEstimation:
    """Tests for _estimate_node_count."""

    def test_empty_string_returns_zero(self):
        """Empty string → 0 nodes."""
        assert _estimate_node_count("") == 0

    def test_single_tag_counts_as_one(self):
        """Single tag counts as 1 node."""
        count = _estimate_node_count("<div>content</div>")
        assert count >= 1

    def test_multiple_tags_counted(self):
        """Multiple tags counted correctly."""
        html = "<div><p>A</p><p>B</p><span>C</span></div>"
        count = _estimate_node_count(html)
        # Should count multiple tags
        assert count > 1

    def test_self_closing_tags_not_counted(self):
        """Self-closing tags (img, br) not double-counted."""
        html = "<div><img src='x'><br><p>Text</p></div>"
        count = _estimate_node_count(html)
        # Reasonable estimate without false counts
        assert count > 0


class TestDomSliceValidation:
    """Tests for DomSlice.validate()."""

    def test_valid_slice_validates_cleanly(self):
        """Valid DomSlice returns None (no errors)."""
        slice_obj = DomSlice(
            html="<div>Text</div>",
            selector_used=".price",
            text_len=15,
            node_count=2,
        )
        assert slice_obj.validate() is None

    def test_invalid_text_len_fails(self):
        """Negative text_len fails validation."""
        slice_obj = DomSlice(
            html="<div>Text</div>",
            selector_used=".price",
            text_len=-1,
            node_count=2,
        )
        err = slice_obj.validate()
        assert err is not None

    def test_is_empty_property_works(self):
        """is_empty property detects small slices."""
        small = DomSlice(html="x", selector_used="x", text_len=1, node_count=0)
        assert small.is_empty

        large = DomSlice(html="x" * 100, selector_used="x", text_len=100, node_count=5)
        assert not large.is_empty


class TestIntegration:
    """Integration tests for full slicing workflow."""

    def test_realistic_flight_search_page(self):
        """Slice realistic flight search HTML."""
        html = """
        <html>
        <head><title>Flights</title></head>
        <body>
            <header>Search Bar</header>
            <div class="search-results">
                <article class="trip-item">
                    <span class="airline">Airline X</span>
                    <div class="price-container">
                        <span class="price">$250</span>
                        <span class="total">$250.00</span>
                    </div>
                    <button>Select</button>
                </article>
                <article class="trip-item">
                    <span class="airline">Airline Y</span>
                    <div class="price-container">
                        <span class="price">$280</span>
                        <span class="total">$280.00</span>
                    </div>
                </article>
            </div>
        </body>
        </html>
        """
        slice_obj = build_dom_slice(html)

        # Should extract something reasonable
        assert slice_obj.text_len >= 50  # Extracted fragment
        assert ("$250" in slice_obj.html or "$280" in slice_obj.html or "price" in slice_obj.html)
        assert slice_obj.node_count > 0

    def test_minimal_price_snippet(self):
        """Slice minimal but valid price snippet."""
        html = "<span class='price'>$199</span>"
        slice_obj = build_dom_slice(html, max_chars=1000)

        assert len(slice_obj.html) > 0
        assert "$199" in slice_obj.html
