"""
Tests for KB Taxonomy Validator

Ensures the KB follows the Constitution rules:
- Lowercase snake_case filenames
- No dates in filenames
- Correct folder structure
"""

from pathlib import Path

import pytest

# Import the validator functions
from utils.kb_taxonomy_check import (
    check_filename_format,
    check_kb_structure,
    get_repo_root,
)


class TestFilenameFormat:
    """Test filename validation rules."""

    def test_lowercase_snake_case_valid(self):
        """Valid filenames should pass."""
        valid_names = [
            "evidence.md",
            "date_picker.md",
            "budget_management.md",
            "triage_runbook.md",
            "combobox_commit.md",
            "index.md",
            "kb_constitution.md",
        ]
        for name in valid_names:
            errors = check_filename_format(name)
            assert (
                len(errors) == 0
            ), f"Should accept valid filename '{name}', but got errors: {errors}"

    def test_uppercase_detected(self):
        """Uppercase filenames should fail."""
        invalid_names = [
            "Evidence.md",
            "evidence_FIELD.md",
            "DatePicker.md",
        ]
        for name in invalid_names:
            errors = check_filename_format(name)
            assert any(
                "lowercase" in err for err in errors
            ), f"Should reject uppercase in '{name}'"

    def test_dates_in_filename_detected(self):
        """Filenames with dates should fail."""
        invalid_names = [
            "evidence_2026-02-21.md",
            "report_20260221.md",
            "triage_runbook_2026-02.md",
        ]
        for name in invalid_names:
            errors = check_filename_format(name)
            assert any(
                "date" in err for err in errors
            ), f"Should reject date in '{name}'"

    def test_dot_files_skipped(self):
        """Hidden files (starting with .) should be skipped."""
        errors = check_filename_format(".hidden_file")
        assert len(errors) == 0, "Should skip dot files"


class TestKBStructure:
    """Test KB folder structure validation."""

    @pytest.fixture
    def repo_root(self):
        """Get repo root."""
        return get_repo_root()

    def test_no_archive_under_kb(self, repo_root):
        """archive/ folder must NOT exist under docs/kb/."""
        kb_root = repo_root / "docs" / "kb"
        archive_path = kb_root / "archive"
        assert (
            not archive_path.exists()
        ), "archive/ must not be under docs/kb/ (should be at docs/archive/)"

    def test_no_stray_files_at_kb_root(self, repo_root):
        """Only index.md, kb_index.yaml, and INDEX.md allowed at KB root."""
        kb_root = repo_root / "docs" / "kb"
        allowed = {"index.md", "kb_index.yaml", "INDEX.md"}
        for item in kb_root.iterdir():
            if item.is_file() and not item.name.startswith("."):
                assert (
                    item.name in allowed
                ), f"Stray file at KB root: '{item.name}'. Use taxonomy folders instead."


class TestValidatorScript:
    """Test the validator script itself."""

    def test_get_repo_root(self):
        """Should find repo root."""
        root = get_repo_root()
        assert root.exists()
        assert (root / "docs" / "kb").exists()
