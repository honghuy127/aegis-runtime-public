"""Tests for failure reason codes registry and metadata.

Tests validate:
- ReasonMeta minimal structure (code, summary, emitter, required_evidence, kb_links, etc.)
- Is_valid_reason_code() and aliases
- normalize_reason() and get_reason_meta()
- No operational guidance in code (that lives in TRIAGE_RUNBOOK.md)
- Evidence field namespace constraint (e.g., "ui.selector_attempts")
- KB links exist under docs/kb/
"""

import re
from dataclasses import is_dataclass
from pathlib import Path
import pytest
from core.scenario.reasons import (
    REASON_REGISTRY,
    REASON_ALIASES,
    is_valid_reason_code,
    normalize_reason,
    get_reason_meta,
)


class TestReasonMetaStructure:
    """Tests for ReasonMeta dataclass (minimal structure)."""

    def test_reason_registry_not_empty(self):
        """Registry should contain reason codes."""
        assert len(REASON_REGISTRY) >= 8, "Need at least 8 required codes"

    def test_reason_registry_upper_bound(self):
        """Registry should be bounded to avoid code explosion."""
        assert len(REASON_REGISTRY) <= 20, "Registry should not exceed 20 codes"

    def test_all_registry_entries_are_reasonmeta(self):
        """All registry values should be ReasonMeta instances."""
        for code, meta in REASON_REGISTRY.items():
            assert is_dataclass(meta), f"{code}: meta should be dataclass"
            assert hasattr(meta, "code"), f"{code}: meta missing code"
            assert hasattr(meta, "summary"), f"{code}: meta missing summary"
            assert hasattr(meta, "emitter"), f"{code}: meta missing emitter"
            assert hasattr(meta, "required_evidence"), f"{code}: meta missing required_evidence"
            assert hasattr(meta, "kb_links"), f"{code}: meta missing kb_links"
            assert meta.code == code

    def test_required_codes_exist(self):
        """All required canonical codes must be present."""
        required = {
            "calendar_dialog_not_found",
            "month_nav_exhausted",
            "calendar_day_not_found",
            "date_picker_unverified",
            "budget_hit",
            "deadline_hit",
            "iata_mismatch",
            "suggestion_not_found",
            "wall_clock_timeout",
            "selector_not_found",
        }
        for code in required:
            assert code in REASON_REGISTRY, f"Missing required code: {code}"

    def test_all_reasons_have_minimal_metadata(self):
        """Every reason should have required fields."""
        for code, meta in REASON_REGISTRY.items():
            assert meta.code, f"{code}: code empty"
            assert meta.summary, f"{code}: summary empty"
            assert len(meta.summary) <= 120, f"{code}: summary >120 chars"
            assert meta.emitter, f"{code}: emitter empty"
            assert isinstance(meta.required_evidence, list), f"{code}: required_evidence not list"
            assert len(meta.required_evidence) > 0, f"{code}: no required_evidence"
            assert isinstance(meta.kb_links, list), f"{code}: kb_links not list"
            assert len(meta.kb_links) > 0, f"{code}: no kb_links"
            assert meta.retry_hint in {
                "no_retry",
                "safe_retry",
                "retry_after_wait",
            }, f"{code}: invalid retry_hint"
            assert meta.severity in {
                "warning",
                "error",
                "critical",
            }, f"{code}: invalid severity"

    def test_emitter_format_is_stable(self):
        """Emitter should follow 'module.path:function' format."""
        pattern = re.compile(r"^[a-z0-9_.]+:[a-z_][a-z0-9_]*$")
        for code, meta in REASON_REGISTRY.items():
            assert pattern.match(
                meta.emitter
            ), f"{code}: emitter '{meta.emitter}' doesn't match format 'module.path:function'"

    def test_summary_is_concise(self):
        """Summaries should be one-line, not multi-paragraph."""
        for code, meta in REASON_REGISTRY.items():
            assert "\n" not in meta.summary, f"{code}: summary contains newlines"
            assert len(meta.summary) >= 10, f"{code}: summary too short"


class TestEvidenceFieldNamespace:
    """Tests for required_evidence field naming (namespace constraint)."""

    def test_evidence_fields_follow_namespace_pattern(self):
        """Evidence keys should follow 'namespace.key' pattern."""
        pattern = re.compile(r"^[a-z]+(\.[a-z0-9_]+)+$")
        for code, meta in REASON_REGISTRY.items():
            for field in meta.required_evidence:
                assert pattern.match(field), (
                    f"{code}: evidence field '{field}' doesn't match namespace pattern. "
                    f"Use 'namespace.key' format (e.g., 'ui.selector_attempts')"
                )

    def test_evidence_fields_are_lowercase(self):
        """Evidence keys should not include uppercase or camelCase characters."""
        for code, meta in REASON_REGISTRY.items():
            for field in meta.required_evidence:
                assert field == field.lower(), (
                    f"{code}: evidence field '{field}' must be lowercase"
                )

    def test_evidence_field_namespaces_are_consistent(self):
        """Evidence field namespaces should be from a controlled set."""
        allowed_namespaces = {
            "ui",
            "verify",
            "budget",
            "time",
            "calendar",
            "suggest",
            "input",
        }
        for code, meta in REASON_REGISTRY.items():
            for field in meta.required_evidence:
                ns = field.split(".")[0]
                assert ns in allowed_namespaces, (
                    f"{code}: unknown namespace '{ns}' in '{field}'. "
                    f"Allowed: {sorted(allowed_namespaces)}"
                )

    def test_no_duplicate_evidence_keys_within_reason(self):
        """Evidence keys should not repeat within a single reason."""
        for code, meta in REASON_REGISTRY.items():
            keys = meta.required_evidence
            assert len(keys) == len(set(keys)), f"{code}: duplicate evidence keys: {keys}"

    def test_no_identical_evidence_sets_across_reasons(self):
        """Avoid copy-paste drift: evidence sets should be distinct per canonical reason."""
        seen = {}
        for code, meta in REASON_REGISTRY.items():
            key = frozenset(meta.required_evidence)
            seen.setdefault(key, []).append(code)
        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        assert not duplicates, f"Duplicate required_evidence sets found: {duplicates}"


class TestKBLinksValidation:
    """Tests that KB links exist and are well-formed."""

    def test_kb_links_are_well_formed(self):
        """KB links should follow 'kb/path.md' or 'kb/path.md#anchor' format."""
        pattern = re.compile(r"^docs/kb/[a-zA-Z0-9_/\-]+\.md(#.+)?$")
        for code, meta in REASON_REGISTRY.items():
            for link in meta.kb_links:
                assert pattern.match(link), (
                    f"{code}: KB link '{link}' malformed. "
                    f"Expected: 'docs/kb/path/file.md' or 'docs/kb/path/file.md#anchor'"
                )

    def test_kb_link_files_exist(self):
        """All KB link files should exist on disk (ignore anchors)."""
        for code, meta in REASON_REGISTRY.items():
            for link in meta.kb_links:
                # Strip anchor if present
                file_path = link.split("#")[0]
                full_path = Path(file_path)
                assert full_path.exists(), f"{code}: KB file doesn't exist: {full_path}"

    def test_triage_runbook_linked(self):
        """Most reasons should link to triage_runbook.md for diagnosis."""
        for code, meta in REASON_REGISTRY.items():
            has_triage_link = any(
                "docs/kb/20_decision_system/triage_runbook.md" in link for link in meta.kb_links
            )
            assert has_triage_link, (
                f"{code}: Should link to docs/kb/20_decision_system/triage_runbook.md for diagnostic guidance"
            )


class TestAliasSupport:
    """Tests for backward compatibility aliases."""

    def test_reason_aliases_dict_exists(self):
        """REASON_ALIASES should exist and map legacy to canonical codes."""
        assert isinstance(REASON_ALIASES, dict)
        # Some aliases are expected
        assert len(REASON_ALIASES) >= 1, "Should have at least one alias"

    def test_alias_keys_are_not_canonical(self):
        """Alias keys must not exist in REASON_REGISTRY."""
        for legacy in REASON_ALIASES:
            assert legacy not in REASON_REGISTRY, (
                f"Alias '{legacy}' should not be in registry"
            )

    def test_aliases_point_to_canonical_codes(self):
        """All alias targets should be canonical codes."""
        for legacy, canonical in REASON_ALIASES.items():
            assert canonical in REASON_REGISTRY, (
                f"Alias '{legacy}' -> '{canonical}', but '{canonical}' not in registry"
            )

    def test_aliases_do_not_point_to_other_aliases(self):
        """Aliases must map directly to canonical codes."""
        for legacy, canonical in REASON_ALIASES.items():
            assert canonical not in REASON_ALIASES, (
                f"Alias '{legacy}' -> '{canonical}' should not target another alias"
            )

    def test_aliases_have_no_circular_chains(self):
        """Alias chains must not be circular."""
        for legacy in REASON_ALIASES:
            visited = set()
            current = legacy
            while current in REASON_ALIASES:
                if current in visited:
                    raise AssertionError(f"Circular alias chain detected at '{current}'")
                visited.add(current)
                current = REASON_ALIASES[current]
            assert current in REASON_REGISTRY, (
                f"Alias '{legacy}' did not resolve to canonical code"
            )

    def test_is_valid_reason_code_accepts_aliases(self):
        """is_valid_reason_code should return True for aliases."""
        for alias in REASON_ALIASES.keys():
            assert is_valid_reason_code(alias), f"Alias '{alias}' not recognized"

    def test_normalize_reason_handles_aliases(self):
        """normalize_reason should return canonical code for aliases."""
        for alias, canonical in REASON_ALIASES.items():
            normalized = normalize_reason(alias)
            assert normalized == canonical, (
                f"normalize_reason('{alias}') = '{normalized}', expected '{canonical}'"
            )


class TestValidationFunctions:
    """Tests for validate_reason_code, is_valid_reason_code, get_reason."""

    def test_is_valid_reason_code_canonical(self):
        """Should return True for canonical codes."""
        assert is_valid_reason_code("calendar_dialog_not_found")
        assert is_valid_reason_code("month_nav_exhausted")
        assert is_valid_reason_code("budget_hit")

    def test_is_valid_reason_code_unknown(self):
        """Should return False for unknown codes."""
        assert not is_valid_reason_code("unknown_reason")
        assert not is_valid_reason_code("")

    def test_normalize_reason_canonical(self):
        """Should return code unchanged for canonical codes."""
        assert normalize_reason("calendar_dialog_not_found") == "calendar_dialog_not_found"
        assert normalize_reason("budget_hit") == "budget_hit"

    def test_normalize_reason_case_insensitive(self):
        """Should lowercase and recognize codes."""
        assert normalize_reason("BUDGET_HIT") == "budget_hit"
        assert normalize_reason("CALENDAR_DIALOG_NOT_FOUND") == "calendar_dialog_not_found"

    def test_normalize_reason_unknown(self):
        """Should return 'unknown' for unrecognized codes."""
        assert normalize_reason("unknown_code") == "unknown"
        assert normalize_reason("") == "unknown"

    def test_get_reason_meta_valid(self):
        """Should return metadata for valid codes."""
        meta = get_reason_meta("calendar_dialog_not_found")
        assert meta is not None
        assert meta.code == "calendar_dialog_not_found"

    def test_get_reason_meta_alias(self):
        """Should return metadata for alias codes."""
        for alias, canonical in REASON_ALIASES.items():
            meta = get_reason_meta(alias)
            assert meta is not None
            assert meta.code == canonical

    def test_get_reason_meta_invalid_no_default(self):
        """Should return None for invalid codes without default."""
        meta = get_reason_meta("unknown_code")
        assert meta is None

    def test_get_reason_meta_invalid_with_default(self):
        """Should return default for invalid codes."""
        default = REASON_REGISTRY["budget_hit"]
        meta = get_reason_meta("unknown_code", default=default)
        assert meta is not None
        assert meta.code == "budget_hit"


class TestRegistryConsistency:
    """Tests for overall registry consistency."""

    def test_no_duplicate_codes(self):
        """All reason codes should be unique."""
        codes = list(REASON_REGISTRY.keys())
        assert len(codes) == len(set(codes)), "Duplicate reason codes found"

    def test_no_duplicate_summaries(self):
        """Summaries should be unique."""
        summaries = [m.summary for m in REASON_REGISTRY.values()]
        assert len(summaries) == len(set(summaries)), "Duplicate summaries found"

    def test_canonical_reason_suffixes(self):
        """Canonical codes should follow approved suffix patterns."""
        suffix_pattern = re.compile(
            r"^(?:[a-z0-9_]+)_(not_found|mismatch|exhausted|hit|timeout|unverified)$"
        )
        for code in REASON_REGISTRY:
            assert suffix_pattern.match(code), (
                f"Canonical code '{code}' does not follow approved suffix patterns"
            )

    def test_no_canonical_substring_variants(self):
        """Avoid split variants: no canonical code should be strict substring of another."""
        codes = sorted(REASON_REGISTRY.keys())
        for idx, code in enumerate(codes):
            for other in codes[idx + 1 :]:
                assert code not in other, (
                    f"Canonical code '{code}' is a substring of '{other}'"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
