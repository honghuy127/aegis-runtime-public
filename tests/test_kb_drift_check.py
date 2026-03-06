"""Tests for KB drift detector.

Tests drift detection between code and YAML registries:
- Reason codes
- Evidence keys
- Invariant IDs
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from utils.kb_drift import (
    detect_drift,
    load_yaml_reasons,
    load_yaml_evidence_keys,
    load_yaml_invariants,
    extract_code_reasons,
    extract_code_evidence_keys,
    extract_code_invariants,
    DriftReport,
    DriftItem,
)


@pytest.fixture
def fake_repo(tmp_path):
    """Create a fake repo structure with YAML and code files."""
    # Create directory structure
    (tmp_path / "docs/kb/20_decision_system").mkdir(parents=True)
    (tmp_path / "docs/kb/10_runtime_contracts").mkdir(parents=True)
    (tmp_path / "docs/kb/00_foundation").mkdir(parents=True)
    (tmp_path / "core/scenario").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)

    # Create triage YAML with reason codes
    triage_yaml = {
        "version": "1.0",
        "reason_tree": {
            "calendar_not_open": {"severity": "error"},
            "budget_hit": {"severity": "error"},
            "orphan_reason": {"severity": "warning"},  # Never used in code
        }
    }
    with open(tmp_path / "docs/kb/20_decision_system/triage_decision_table.yaml", "w") as f:
        yaml.dump(triage_yaml, f)

    # Create evidence YAML with keys
    evidence_yaml = {
        "version": "1.0",
        "namespaces": {
            "calendar": {
                "fields": [
                    {"key": "calendar.opened", "type": "bool"},
                    {"key": "calendar.nav_steps", "type": "int"},
                ]
            },
            "ui": {
                "fields": [
                    {"key": "ui.selector_attempts", "type": "int"},
                ]
            }
        }
    }
    with open(tmp_path / "docs/kb/10_runtime_contracts/evidence_fields.yaml", "w") as f:
        yaml.dump(evidence_yaml, f)

    # Create invariants YAML with IDs
    invariants_yaml = {
        "version": "1.0",
        "invariants": [
            {"id": "INV-SCENARIO-001", "statement": "Test invariant 1"},
            {"id": "INV-BUDGET-002", "statement": "Test invariant 2"},
            {"id": "INV-ORPHAN-999", "statement": "Never referenced"},  # Orphan
        ]
    }
    with open(tmp_path / "docs/kb/00_foundation/invariants_registry.yaml", "w") as f:
        yaml.dump(invariants_yaml, f)

    # Create artifacts YAML (empty for now)
    artifacts_yaml = {
        "version": "1.0",
        "artifacts": {}
    }
    with open(tmp_path / "docs/kb/10_runtime_contracts/evidence_artifacts.yaml", "w") as f:
        yaml.dump(artifacts_yaml, f)

    # Create reasons.py with canonical registry
    reasons_py = '''"""Reason codes."""
REASON_REGISTRY = {
    "calendar_not_open": {"summary": "Calendar not open"},
    "budget_hit": {"summary": "Budget exceeded"},
    "missing_in_yaml": {"summary": "Not in YAML"},  # Missing from YAML
}
'''
    with open(tmp_path / "core/scenario/reasons.py", "w") as f:
        f.write(reasons_py)

    # Create code file that uses evidence keys
    code_py = '''"""Test code."""
def test_function():
    evidence = {}
    evidence["calendar.opened"] = True  # Exists in YAML
    evidence["calendar.nav_steps"] = 5  # Exists in YAML
    evidence["calendar.missing_key"] = "x"  # Missing from YAML
    evidence["ui.selector_attempts"] = 3  # Exists in YAML
    evidence["verify.missing"] = False  # Missing from YAML
    return evidence
'''
    with open(tmp_path / "core/scenario/test_module.py", "w") as f:
        f.write(code_py)

    # Create test file that references invariants
    test_py = '''"""Test file."""
def test_invariant_1():
    """Verifies INV-SCENARIO-001."""
    pass

def test_invariant_2():
    """Verifies INV-BUDGET-002."""
    pass

def test_invariant_missing():
    """Verifies INV-MISSING-123."""  # Missing from YAML
    pass
'''
    with open(tmp_path / "tests/test_invariants.py", "w") as f:
        f.write(test_py)

    return tmp_path


class TestDriftReport:
    """Test DriftReport data structure."""

    def test_empty_report(self):
        report = DriftReport()
        assert not report.has_errors()
        assert not report.has_warnings()
        assert "No KB drift" in report.format_report()

    def test_report_with_errors(self):
        report = DriftReport()
        report.add_error(DriftItem(
            category="reason",
            item_key="test_reason",
            drift_type="missing_in_yaml",
            severity="error",
        ))

        assert report.has_errors()
        assert not report.has_warnings()
        formatted = report.format_report()
        assert "KB DRIFT ERRORS: 1" in formatted
        assert "test_reason" in formatted

    def test_report_with_warnings(self):
        report = DriftReport()
        report.add_warning(DriftItem(
            category="evidence",
            item_key="test.key",
            drift_type="orphaned_in_yaml",
            severity="warning",
        ))

        assert not report.has_errors()
        assert report.has_warnings()
        formatted = report.format_report()
        assert "KB DRIFT WARNINGS: 1" in formatted

    def test_report_to_dict(self):
        report = DriftReport()
        report.add_error(DriftItem(
            category="reason",
            item_key="test",
            drift_type="missing_in_yaml",
            severity="error",
            source_location="test.py",
        ))

        data = report.to_dict()
        assert data["summary"]["errors"] == 1
        assert data["summary"]["warnings"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["item_key"] == "test"


class TestYAMLLoaders:
    """Test YAML loading functions."""

    def test_load_yaml_reasons(self, fake_repo):
        reasons = load_yaml_reasons(fake_repo)
        assert "calendar_not_open" in reasons
        assert "budget_hit" in reasons
        assert "orphan_reason" in reasons
        assert len(reasons) == 3

    def test_load_yaml_evidence_keys(self, fake_repo):
        evidence_keys = load_yaml_evidence_keys(fake_repo)
        assert "calendar.opened" in evidence_keys
        assert "calendar.nav_steps" in evidence_keys
        assert "ui.selector_attempts" in evidence_keys
        assert len(evidence_keys) >= 3

    def test_load_yaml_invariants(self, fake_repo):
        invariants = load_yaml_invariants(fake_repo)
        assert "INV-SCENARIO-001" in invariants
        assert "INV-BUDGET-002" in invariants
        assert "INV-ORPHAN-999" in invariants
        assert len(invariants) == 3


class TestCodeExtractors:
    """Test code extraction functions."""

    def test_extract_code_reasons(self, fake_repo):
        reasons = extract_code_reasons(fake_repo)
        # Should include reasons from REASON_REGISTRY
        assert "calendar_not_open" in reasons
        assert "budget_hit" in reasons
        assert "missing_in_yaml" in reasons

    def test_extract_code_evidence_keys(self, fake_repo):
        evidence_keys = extract_code_evidence_keys(fake_repo)
        assert "calendar.opened" in evidence_keys
        assert "calendar.nav_steps" in evidence_keys
        assert "calendar.missing_key" in evidence_keys
        assert "ui.selector_attempts" in evidence_keys
        assert "verify.missing" in evidence_keys

    def test_extract_code_invariants(self, fake_repo):
        invariants = extract_code_invariants(fake_repo)
        assert "INV-SCENARIO-001" in invariants
        assert "INV-BUDGET-002" in invariants
        assert "INV-MISSING-123" in invariants


class TestDriftDetection:
    """Test full drift detection."""

    def test_detect_reason_drift(self, fake_repo):
        """Should detect reasons in code but missing from YAML."""
        report = detect_drift(fake_repo)

        # "missing_in_yaml" reason is in REASON_REGISTRY but not in triage YAML
        error_keys = [e.item_key for e in report.errors if e.category == "reason"]
        assert "missing_in_yaml" in error_keys

    def test_detect_orphan_reasons(self, fake_repo):
        """Should warn about reasons in YAML never used in code."""
        report = detect_drift(fake_repo)

        # "orphan_reason" is in YAML but never used in code
        warning_keys = [w.item_key for w in report.warnings if w.category == "reason"]
        assert "orphan_reason" in warning_keys

    def test_detect_evidence_drift(self, fake_repo):
        """Should detect evidence keys in code but missing from YAML."""
        report = detect_drift(fake_repo)

        # calendar.missing_key and verify.missing are in code but not YAML
        error_keys = [e.item_key for e in report.errors if e.category == "evidence"]
        assert "calendar.missing_key" in error_keys
        assert "verify.missing" in error_keys

        # Should NOT report keys that exist in YAML
        assert "calendar.opened" not in error_keys
        assert "ui.selector_attempts" not in error_keys

    def test_detect_invariant_drift(self, fake_repo):
        """Should detect invariants in tests but missing from YAML."""
        report = detect_drift(fake_repo)

        # INV-MISSING-123 is in tests but not in YAML
        error_keys = [e.item_key for e in report.errors if e.category == "invariant"]
        assert "INV-MISSING-123" in error_keys

    def test_detect_orphan_invariants(self, fake_repo):
        """Should warn about invariants in YAML never referenced in tests."""
        report = detect_drift(fake_repo)

        # INV-ORPHAN-999 is in YAML but never referenced
        warning_keys = [w.item_key for w in report.warnings if w.category == "invariant"]
        assert "INV-ORPHAN-999" in warning_keys

    def test_skip_diagnostic_evidence_keys(self, fake_repo):
        """Should not report drift for evidence keys starting with diag."""
        # Add code that uses diag.* keys
        code_py = '''
def test():
    evidence = {}
    evidence["diag.internal_state"] = "x"  # Should be ignored
'''
        with open(fake_repo / "core/scenario/diag_test.py", "w") as f:
            f.write(code_py)

        report = detect_drift(fake_repo)

        # diag.* keys should not be reported as errors
        error_keys = [e.item_key for e in report.errors if e.category == "evidence"]
        assert not any(k.startswith("diag.") for k in error_keys)

    def test_skip_test_fixture_evidence_keys(self, fake_repo):
        """Should not report drift for common test fixture keys."""
        # Add code that uses test fixture keys
        code_py = '''
def test():
    evidence = {}
    evidence["x"] = 1  # Test fixture, should be ignored
    evidence["test"] = 2  # Test fixture, should be ignored
'''
        with open(fake_repo / "tests/test_fixtures.py", "w") as f:
            f.write(code_py)

        report = detect_drift(fake_repo)

        # Fixture keys should not be reported
        error_keys = [e.item_key for e in report.errors if e.category == "evidence"]
        assert "x" not in error_keys
        assert "test" not in error_keys

    def test_reason_alias_and_canonical_are_treated_equivalent(self, fake_repo):
        """Alias in YAML and canonical in code should not produce reason drift."""
        triage_yaml = {
            "version": "1.0",
            "reason_tree": {
                "date_picker_failures": [
                    {"code": "calendar_not_open", "summary": "legacy alias form"}
                ]
            },
        }
        with open(fake_repo / "docs/kb/20_decision_system/triage_decision_table.yaml", "w") as f:
            yaml.dump(triage_yaml, f)

        reasons_py = '''"""Reason codes."""
REASON_REGISTRY = {
    "calendar_dialog_not_found": {"summary": "Canonical calendar open failure"},
}
'''
        with open(fake_repo / "core/scenario/reasons.py", "w") as f:
            f.write(reasons_py)

        report = detect_drift(fake_repo)
        reason_errors = [e.item_key for e in report.errors if e.category == "reason"]
        assert "calendar_dialog_not_found" not in reason_errors
        assert "calendar_not_open" not in reason_errors


class TestRealRepo:
    """Test on real repo to ensure it runs without crashing."""

    def test_detect_drift_on_real_repo(self):
        """Should run on real repo without errors (may have drift)."""
        report = detect_drift()

        # Should return a valid report (may have errors/warnings)
        assert isinstance(report, DriftReport)
        assert isinstance(report.errors, list)
        assert isinstance(report.warnings, list)

        # Format should work
        formatted = report.format_report()
        assert isinstance(formatted, str)

        # JSON export should work
        data = report.to_dict()
        assert "summary" in data
        assert "errors" in data
        assert "warnings" in data
