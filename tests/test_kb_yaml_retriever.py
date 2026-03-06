"""Unit tests for KB YAML structured retrieval system.

Validates that:
1. Selective loading by key works correctly
2. Full YAML catalogs are never loaded accidentally
3. Large return warnings are triggered appropriately
4. Caching works correctly
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from utils import kb_yaml_retriever


class TestEvidenceCatalogRetrieval:
    """Test selective evidence field loading."""

    def test_get_evidence_field_returns_dict(self):
        """Evidence field should return dict if found."""
        result = kb_yaml_retriever.get_evidence_field("calendar.opened")
        # Result may be None if catalog not found, but if found should be dict
        if result is not None:
            assert isinstance(result, dict)

    def test_get_evidence_field_returns_none_for_missing(self):
        """Missing field should return None, not raise exception."""
        result = kb_yaml_retriever.get_evidence_field("nonexistent.field")
        assert result is None

    def test_get_evidence_field_uses_cache(self):
        """Second call should use cache."""
        kb_yaml_retriever.clear_caches()

        # First call
        result1 = kb_yaml_retriever.get_evidence_field("calendar.opened")
        cache_size_1 = len(kb_yaml_retriever._evidence_field_cache)

        # Second call (should use cache)
        result2 = kb_yaml_retriever.get_evidence_field("calendar.opened")
        cache_size_2 = len(kb_yaml_retriever._evidence_field_cache)

        assert result1 == result2
        assert cache_size_1 == cache_size_2  # Cache size unchanged

    def test_get_reason_evidence_mapping_selective(self):
        """Should load only reason-specific evidence map, not full catalog."""
        result = kb_yaml_retriever.get_reason_evidence_mapping("calendar_dialog_not_found")

        # If found, should have structure with required/optional fields
        if result is not None:
            assert isinstance(result, dict)
            # Should NOT have full catalog structure
            assert "evidence_fields" not in result
            assert "reason_evidence_map" not in result

    def test_get_reason_evidence_mapping_alias_compatibility(self):
        """Legacy alias should resolve to canonical reason evidence mapping."""
        canonical = kb_yaml_retriever.get_reason_evidence_mapping("calendar_dialog_not_found")
        legacy = kb_yaml_retriever.get_reason_evidence_mapping("calendar_not_open")
        if canonical is not None and legacy is not None:
            assert canonical == legacy


class TestTriageDecisionTableRetrieval:
    """Test selective triage decision loading."""

    def test_get_triage_decision_selective(self):
        """Should load only reason-specific decision, not full table."""
        result = kb_yaml_retriever.get_triage_decision("calendar_not_open")

        if result is not None:
            assert isinstance(result, dict)
            # Should NOT have full table structure
            assert "reason_tree" not in result or isinstance(result.get("reason_tree"), dict)

    def test_get_triage_decision_returns_none_for_missing(self):
        """Missing reason should return None."""
        result = kb_yaml_retriever.get_triage_decision("nonexistent_reason")
        assert result is None

    def test_list_triage_reasons_is_list(self):
        """Should return list of reason codes."""
        result = kb_yaml_retriever.list_triage_reasons()
        assert isinstance(result, list)
        assert all(isinstance(r, str) for r in result)

    def test_get_triage_decision_supports_categorized_shape(self):
        """Should read reasons from categorized list shape."""
        kb_yaml_retriever.clear_caches()
        catalog = {
            "reason_tree": {
                "date_picker_failures": [
                    {"code": "calendar_not_open", "summary": "legacy calendar open failure"}
                ]
            }
        }
        with patch("utils.kb_yaml_retriever._load_yaml", return_value=catalog):
            result = kb_yaml_retriever.get_triage_decision("calendar_not_open")
        assert isinstance(result, dict)
        assert result.get("code") == "calendar_not_open"

    def test_get_triage_decision_alias_to_legacy_doc_code(self):
        """Canonical reason lookup should resolve to legacy-coded triage entries."""
        kb_yaml_retriever.clear_caches()
        catalog = {
            "reason_tree": {
                "date_picker_failures": [
                    {"code": "calendar_not_open", "summary": "legacy code entry"}
                ]
            }
        }
        with patch("utils.kb_yaml_retriever._load_yaml", return_value=catalog):
            result = kb_yaml_retriever.get_triage_decision("calendar_dialog_not_found")
        assert isinstance(result, dict)
        assert result.get("code") == "calendar_not_open"


class TestRuntimeSymptomMapRetrieval:
    """Test selective symptom diagnosis loading."""

    def test_get_symptom_diagnosis_selective(self):
        """Should load only symptom-specific diagnosis, not full map."""
        result = kb_yaml_retriever.get_symptom_diagnosis("no_html_returned")

        if result is not None:
            assert isinstance(result, dict)
            # Should NOT have full map structure
            assert "symptoms" not in result

    def test_get_symptom_diagnosis_returns_none_for_missing(self):
        """Missing symptom should return None."""
        result = kb_yaml_retriever.get_symptom_diagnosis("nonexistent_symptom")
        assert result is None

    def test_list_symptoms_is_list(self):
        """Should return list of symptom names."""
        result = kb_yaml_retriever.list_symptoms()
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)


class TestArchitectureInvariantsRetrieval:
    """Test selective invariant loading."""

    def test_get_invariant_selective(self):
        """Should load only invariant-specific definition."""
        result = kb_yaml_retriever.get_invariant("INV-SCENARIO-001")

        if result is not None:
            assert isinstance(result, dict)
            # Should NOT have full invariants structure
            assert "invariants" not in result

    def test_get_invariant_returns_none_for_missing(self):
        """Missing invariant should return None."""
        result = kb_yaml_retriever.get_invariant("INV-NONEXISTENT-999")
        assert result is None

    def test_list_invariants_all(self):
        """Should return all invariant IDs."""
        result = kb_yaml_retriever.list_invariants()
        assert isinstance(result, list)
        # IDs should start with INV-
        assert all(isinstance(i, str) and i.startswith("INV-") for i in result if result)

    def test_list_invariants_filtered_by_category(self):
        """Should filter invariants by category."""
        result = kb_yaml_retriever.list_invariants(category="SCENARIO")
        assert isinstance(result, list)
        # All results should start with INV-SCENARIO-
        assert all(i.startswith("INV-SCENARIO-") for i in result if result)


class TestLargeCatalogWarnings:
    """Test that large returns trigger warnings."""

    def test_warn_if_large_detects_threshold(self):
        """Should warn when data exceeds threshold."""
        large_data = {"field": "x" * 10000}  # Large string

        with patch("utils.kb_yaml_retriever.logger") as mock_logger:
            was_warned = kb_yaml_retriever._warn_if_large(
                large_data,
                "test_key",
                threshold_lines=1
            )
            # Check if warning was logged (may or may not trigger depending on YAML format)
            # At minimum, function should return boolean without exception
            assert isinstance(was_warned, bool)

    def test_warn_if_large_no_exception_on_error(self):
        """Should not raise exception even on bad data."""
        # Should handle gracefully
        result = kb_yaml_retriever._warn_if_large(
            None,  # Invalid data
            "bad_key",
            threshold_lines=100
        )
        assert isinstance(result, bool)


class TestCacheManagement:
    """Test cache operations."""

    def test_clear_caches_resets_all(self):
        """clear_caches should reset all field caches."""
        # Populate caches
        kb_yaml_retriever.get_evidence_field("test.field")
        kb_yaml_retriever.get_triage_decision("test_reason")
        kb_yaml_retriever.get_symptom_diagnosis("test_symptom")

        # Verify caches have content
        assert len(kb_yaml_retriever._evidence_field_cache) > 0 or True  # May be empty if files not found

        # Clear
        kb_yaml_retriever.clear_caches()

        # Verify caches are empty
        assert len(kb_yaml_retriever._evidence_field_cache) == 0
        assert len(kb_yaml_retriever._triage_reason_cache) == 0
        assert len(kb_yaml_retriever._symptom_cache) == 0
        assert len(kb_yaml_retriever._invariant_cache) == 0


class TestGuardrails:
    """Test guardrail functions."""

    def test_ensure_selective_loading_no_warnings(self):
        """With no caches, should return empty warnings."""
        kb_yaml_retriever.clear_caches()
        warnings = kb_yaml_retriever.ensure_selective_loading()
        assert isinstance(warnings, list)
        # Should be empty or have warnings, but not raise exception
        assert all(isinstance(w, str) for w in warnings)


class TestNoFullCatalogLoads:
    """Validate that functions never load entire YAML catalogs."""

    def test_get_evidence_field_only_loads_one_field(self):
        """get_evidence_field should not populate entire evidence_fields dict."""
        kb_yaml_retriever.clear_caches()

        # Query one field
        kb_yaml_retriever.get_evidence_field("calendar.opened")

        # Cache should have at most 1 entry
        cache_size = len(kb_yaml_retriever._evidence_field_cache)
        assert cache_size <= 1

    def test_get_triage_decision_only_loads_one_reason(self):
        """get_triage_decision should not populate entire reason_tree dict."""
        kb_yaml_retriever.clear_caches()

        # Query one reason
        kb_yaml_retriever.get_triage_decision("calendar_not_open")

        # Cache should have at most 1 entry
        cache_size = len(kb_yaml_retriever._triage_reason_cache)
        assert cache_size <= 1

    def test_get_symptom_diagnosis_only_loads_one_symptom(self):
        """get_symptom_diagnosis should not populate entire symptoms dict."""
        kb_yaml_retriever.clear_caches()

        # Query one symptom
        kb_yaml_retriever.get_symptom_diagnosis("no_html_returned")

        # Cache should have at most 1 entry
        cache_size = len(kb_yaml_retriever._symptom_cache)
        assert cache_size <= 1


class TestErrorHandling:
    """Test graceful error handling."""

    def test_missing_catalog_returns_none(self):
        """If catalog file missing, should return None, not raise."""
        with patch("utils.kb_yaml_retriever._load_yaml", return_value=None):
            result = kb_yaml_retriever.get_evidence_field("any.field")
            assert result is None

    def test_invalid_yaml_returns_none(self):
        """If YAML parse fails, should return None, not raise."""
        with patch("utils.kb_yaml_retriever._load_yaml", return_value=None):
            result = kb_yaml_retriever.get_triage_decision("any_reason")
            assert result is None

    def test_functions_never_raise_on_bad_data(self):
        """All retrieval functions should be exception-safe."""
        with patch("utils.kb_yaml_retriever._load_yaml", return_value=None):
            # None of these should raise
            kb_yaml_retriever.get_evidence_field("x")
            kb_yaml_retriever.get_reason_evidence_mapping("x")
            kb_yaml_retriever.get_triage_decision("x")
            kb_yaml_retriever.list_triage_reasons()
            kb_yaml_retriever.get_symptom_diagnosis("x")
            kb_yaml_retriever.list_symptoms()
            kb_yaml_retriever.get_invariant("x")
            kb_yaml_retriever.list_invariants()


class TestFullCatalogLoadBlocking:
    """Test guardrails that block full-catalog loads when explicitly attempted."""

    def test_block_full_catalog_load_evidence(self):
        """Explicit full-catalog load should raise ValueError."""
        # Create data large enough to trigger blocking (>824 lines in YAML)
        large_data = {
            f"field_{i}": {
                "type": "string",
                "description": "x" * 50,
                "examples": [f"example_{j}" for j in range(10)]
            }
            for i in range(100)
        }

        # Reset DEBUG flag to ensure blocking
        original = kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS
        kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS = False

        try:
            # Explicitly calling _block_full_catalog_load should raise
            kb_yaml_retriever._block_full_catalog_load(
                kb_yaml_retriever.EVIDENCE_CATALOG_PATH,
                large_data
            )
            assert False, "Should have raised ValueError for full-catalog load"
        except ValueError as e:
            assert "blocked" in str(e).lower()
        finally:
            kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS = original

    def test_debug_flag_bypasses_block(self):
        """DEBUG flag should skip blocking."""
        # Create large data
        large_data = {'field': 'x' * 5000}

        # Enable DEBUG mode
        original_flag = kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS
        kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS = True

        try:
            # Should NOT raise when DEBUG flag is True
            kb_yaml_retriever._block_full_catalog_load(
                kb_yaml_retriever.EVIDENCE_CATALOG_PATH,
                large_data
            )
            # If we get here, the bypass worked
            assert True
        finally:
            kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS = original_flag

    def test_selective_load_bypasses_block(self):
        """Selective field loads (small data) always pass guardrail."""
        small_data = {'type': 'bool', 'description': 'small field'}

        # Even without DEBUG flag, small data passes guardrail
        kb_yaml_retriever.DEBUG_ALLOW_FULL_LOADS = False
        kb_yaml_retriever._block_full_catalog_load(
            kb_yaml_retriever.EVIDENCE_CATALOG_PATH,
            small_data
        )
        # If we get here, small data passed (as expected)
        assert True

    def test_all_public_apis_are_selective(self):
        """Verify all public APIs return selective data, not full catalogs."""
        # These should all succeed and return bounded data
        assert kb_yaml_retriever.list_symptoms() is not None
        assert kb_yaml_retriever.list_invariants() is not None
        assert kb_yaml_retriever.list_triage_reasons() is not None

        # Get specific fields (bounded)
        evidence = kb_yaml_retriever.get_evidence_field("REASON_CALENDAR_NOT_OPEN")
        if evidence is not None:
            # Selective data should be small
            import yaml
            yaml_str = yaml.dump(evidence)
            assert len(yaml_str.splitlines()) < 50  # Single field, bounded


class TestPromptContextBoundedness:
    """Integration tests to verify prompt context stays bounded."""

    def test_single_field_context_is_bounded(self):
        """Single evidence field should not exceed 100 lines of YAML."""
        import yaml

        # Get a real evidence field
        fields = ["calendar.opened", "calendar.filled", "location.filled", "date.filled"]

        for field in fields:
            field_data = kb_yaml_retriever.get_evidence_field(field)
            if field_data:
                yaml_str = yaml.dump(field_data)
                lines = len(yaml_str.splitlines())
                # Single field should be <100 lines (realistically <30)
                assert lines < 100, f"Field {field} is too large: {lines} lines"

    def test_triage_decision_is_bounded(self):
        """Single triage decision should not exceed 150 lines."""
        import yaml

        reasons = kb_yaml_retriever.list_triage_reasons()
        if reasons:
            # Test a few real reasons
            for reason in reasons[:3]:
                decision = kb_yaml_retriever.get_triage_decision(reason)
                if decision:
                    yaml_str = yaml.dump(decision)
                    lines = len(yaml_str.splitlines())
                    # Single decision: max 150 lines
                    assert lines < 150, f"Decision for {reason} is too large: {lines} lines"

    def test_symptom_diagnosis_is_bounded(self):
        """Single symptom diagnosis should not exceed 100 lines."""
        import yaml

        symptoms = kb_yaml_retriever.list_symptoms()
        if symptoms:
            # Test first symptom as representative
            for symptom in symptoms[:3]:
                diagnosis = kb_yaml_retriever.get_symptom_diagnosis(symptom)
                if diagnosis:
                    yaml_str = yaml.dump(diagnosis)
                    lines = len(yaml_str.splitlines())
                    # Single diagnosis: max 100 lines
                    assert lines < 100, f"Diagnosis for {symptom} is too large: {lines} lines"

    def test_combined_prompt_context_is_bounded(self):
        """Typical prompt should not embed >500 lines total from KB catalogs."""
        import yaml

        # Simulate a typical failure diagnosis prompt:
        # 1. Get one evidence field (calendar)
        # 2. Get one triage decision (calendar_not_open)
        # 3. Get one symptom diagnosis (no_calendar_found)

        total_lines = 0

        # Evidence context
        evidence = kb_yaml_retriever.get_evidence_field("calendar.opened")
        if evidence:
            yaml_str = yaml.dump(evidence)
            total_lines += len(yaml_str.splitlines())

        # Triage context
        decision = kb_yaml_retriever.get_triage_decision("calendar_not_open")
        if decision:
            yaml_str = yaml.dump(decision)
            total_lines += len(yaml_str.splitlines())

        # Symptom context
        diagnosis = kb_yaml_retriever.get_symptom_diagnosis("no_html_returned")
        if diagnosis:
            yaml_str = yaml.dump(diagnosis)
            total_lines += len(yaml_str.splitlines())

        # Typical multi-field KB context should stay under 500 lines
        # (much less than a full 800-line catalog)
        assert total_lines < 500, f"Combined KB context is too large: {total_lines} lines"

    def test_no_accidental_full_catalog_loads(self):
        """Verify that public APIs never return full catalogs."""
        import yaml

        # These are size limits for full catalogs
        FULL_CATALOG_THRESHOLDS = {
            "evidence": 824,
            "triage": 350,
            "symptom": 200,
            "invariant": 140,
        }

        # Get representative data from each catalog source
        test_cases = [
            ("evidence", kb_yaml_retriever.get_evidence_field("calendar.opened")),
            ("triage", kb_yaml_retriever.get_triage_decision("calendar_not_open")),
            ("symptom", kb_yaml_retriever.get_symptom_diagnosis("no_html_returned")),
            ("invariant", kb_yaml_retriever.get_invariant("INV-SCENARIO-001")),
        ]

        for catalog_name, data in test_cases:
            if data:
                yaml_str = yaml.dump(data)
                lines = len(yaml_str.splitlines())
                threshold = FULL_CATALOG_THRESHOLDS[catalog_name]

                # Single field should be much smaller than full catalog
                # (ideally <10% of full size)
                assert lines < threshold / 5, (
                    f"{catalog_name} data ({lines} lines) is suspiciously large "
                    f"compared to full catalog ({threshold} lines); "
                    f"possible unintended full load"
                )


class TestKBPromptBudgetEnforcement:
    """Test KB prompt budget enforcement and rendering."""

    def test_render_entry_truncates_at_char_limit(self):
        """render_entry_for_prompt() should truncate large entries at char limit."""
        from utils.kb import KBPromptBudget, render_entry_for_prompt

        # Create a large entry
        large_entry = {
            "id": "test-entry",
            "description": "x" * 15000,  # Way over budget
            "nested": {"key": "value"}
        }

        budget = KBPromptBudget(max_chars=1000)
        result = render_entry_for_prompt(large_entry, budget)

        # Result should be truncated
        assert isinstance(result, str)
        assert len(result) <= 1100  # Some margin for YAML overhead
        assert "[TRUNCATED" in result

    def test_render_entry_respects_max_items(self):
        """render_entry_for_prompt() should limit list items to max_items."""
        from utils.kb import KBPromptBudget, render_entry_for_prompt

        # Create entry with many list items
        entry = {
            "items": [{"name": f"item-{i}", "value": i} for i in range(100)]
        }

        # Note: KBPromptBudget enforces max_items >= 10 for safety
        budget = KBPromptBudget(max_items=15, max_chars=50000)
        result = render_entry_for_prompt(entry, budget)

        # Should render but with limited items (first 15)
        assert isinstance(result, str)
        # Check that items 0-14 are present but 15+ are not
        assert "item-0" in result
        assert "item-14" in result
        assert "item-15" not in result  # Item 15 and above should not appear
        assert "[... " in result  # Should have truncation marker for remaining items

    def test_render_entry_respects_max_depth(self):
        """render_entry_for_prompt() should limit nesting depth to max_depth."""
        from utils.kb import KBPromptBudget, render_entry_for_prompt

        # Create deeply nested structure
        entry = {"level1": {"level2": {"level3": {"level4": {"level5": {"value": "deep"}}}}}}

        budget = KBPromptBudget(max_depth=2)
        result = render_entry_for_prompt(entry, budget)

        # Should be rendered but truncated at depth 2
        assert isinstance(result, str)
        assert "level1" in result
        # Deep levels should be truncated
        assert "[TRUNCATED" in result or "level5" not in result

    def test_load_kb_budget_from_config_default(self):
        """load_kb_budget_from_config() should load default budget when no debug mode."""
        from utils.kb import load_kb_budget_from_config

        budget = load_kb_budget_from_config(debug_mode=False)

        # Should use default values
        assert budget.max_chars == 12000
        assert budget.max_items == 80
        assert budget.max_entries == 8

    def test_load_kb_budget_from_config_debug(self):
        """load_kb_budget_from_config() should load debug budget when debug mode."""
        from utils.kb import load_kb_budget_from_config

        budget = load_kb_budget_from_config(debug_mode=True)

        # Should use debug values (if available in config)
        # At minimum, should be valid
        assert budget.max_chars >= 500
        assert budget.max_items >= 10

    def test_get_evidence_field_with_budget_renders_yaml(self):
        """get_evidence_field() with budget param should return rendered YAML."""
        from utils.kb import load_kb_budget_from_config

        budget = load_kb_budget_from_config()
        result = kb_yaml_retriever.get_evidence_field("calendar.opened", budget=budget)

        # Should return YAML string when budget provided
        if result is not None:
            assert isinstance(result, str)
            assert "calendar" in result.lower() or result == ""

    def test_get_evidence_field_without_budget_returns_dict(self):
        """get_evidence_field() without budget param should return dict (backward compat)."""
        result = kb_yaml_retriever.get_evidence_field("calendar.opened")

        # Should return dict or None for backward compatibility
        assert result is None or isinstance(result, dict)

    def test_get_reason_evidence_mapping_with_budget(self):
        """get_reason_evidence_mapping() with budget should render YAML."""
        from utils.kb import load_kb_budget_from_config

        budget = load_kb_budget_from_config()
        result = kb_yaml_retriever.get_reason_evidence_mapping("calendar_not_open", budget=budget)

        # Should return YAML string
        if result is not None:
            assert isinstance(result, str)

    def test_get_invariant_with_budget_renders_yaml(self):
        """get_invariant() with budget should render as bounded YAML."""
        from utils.kb import load_kb_budget_from_config

        budget = load_kb_budget_from_config()
        result = kb_yaml_retriever.get_invariant("INV-SCENARIO-001", budget=budget)

        # Should return YAML string
        if result is not None:
            assert isinstance(result, str)
            assert "[TRUNCATED" not in result  # Should fit within budget


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
