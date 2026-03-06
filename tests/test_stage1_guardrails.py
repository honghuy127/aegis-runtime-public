"""
test_stage1_guardrails.py

Enforces Stage 1 Step 0 invariants:
- Root markdown policy (no new .md at root except allowed)
- Canonical docs existence (docs/kb/INDEX.md, kb_index.yaml, ARCHITECTURE_INVARIANTS.md)
- No accidental deletions of KEY files

See: docs/kb/50_governance/stage0_guardrails.md for policy.
"""

import os
from pathlib import Path


class TestStage1RootMarkdownPolicy:
    """Enforce that no new .md files appear at repo root."""

    ALLOWED_ROOT_MD = {"README.md", "AGENTS.md", "SECURITY.md", "LICENSE"}

    def test_no_new_root_markdown_files(self):
        """Root directory MUST NOT contain new .md files (except whitelisted)."""
        root = Path(__file__).parent.parent
        found_md = set()

        for file in root.glob("*.md"):
            found_md.add(file.name)

        # Check for forbidden files
        forbidden = found_md - self.ALLOWED_ROOT_MD
        assert not forbidden, (
            f"Forbidden root markdown files found: {forbidden}. "
            f"All docs must go in docs/kb/ (canonical). See docs/kb/50_governance/stage0_guardrails.md"
        )


class TestStage1CanonicalDocsExist:
    """Enforce that canonical docs in docs/kb/ remain in place."""

    REQUIRED_KB_FILES = {
        "docs/kb/INDEX.md",
        "docs/kb/kb_index.yaml",
        "docs/kb/00_foundation/architecture_invariants.md",
    }

    def test_canonical_docs_exist(self):
        """Canonical docs in docs/kb/ MUST exist and not be deleted."""
        root = Path(__file__).parent.parent

        for doc_path in self.REQUIRED_KB_FILES:
            file_obj = root / doc_path
            assert file_obj.exists(), (
                f"Required canonical doc deleted or moved: {doc_path}. "
                f"See docs/kb/50_governance/stage0_guardrails.md#canonical-documents"
            )

    def test_stage1_guardrails_doc_exists(self):
        """Stage 0 guardrails doc MUST exist."""
        root = Path(__file__).parent.parent
        guardrails = root / "docs/kb/50_governance/stage0_guardrails.md"
        assert guardrails.exists(), (
            "docs/kb/50_governance/stage0_guardrails.md not found. "
            "This is a required deliverable for Stage 0."
        )


class TestStage1NoAccidentalDeletions:
    """Prevent accidental deletion of key scripts and root files."""

    PROTECTED_FILES = {
        "AGENTS.md",
        "README.md",
        "SECURITY.md",
        "LICENSE",
        "pytest.ini",
        "main.py",
        "requirements.txt",
    }

    def test_protected_files_not_deleted(self):
        """Key root files MUST not be deleted during Stage 1."""
        root = Path(__file__).parent.parent

        missing = []
        for fname in self.PROTECTED_FILES:
            if not (root / fname).exists():
                missing.append(fname)

        assert not missing, (
            f"Protected files deleted: {missing}. "
            f"Stage 1 Step 0 MUST NOT delete files. "
            f"See docs/kb/STAGE1_STEP0_GUARDRAILS.md#what-counts-as-behavior-change-in-stage-1"
        )


class TestStage1ToolingExist:
    """Verify that Step 0 tooling is in place."""

    def test_safety_check_script_exists(self):
        """scripts/foundation_guardrails_check.sh MUST exist after Step 0."""
        root = Path(__file__).parent.parent
        script = root / "scripts/foundation_guardrails_check.sh"
        assert script.exists(), "scripts/foundation_guardrails_check.sh not found (required deliverable)"

    def test_safety_check_script_executable(self):
        """scripts/foundation_guardrails_check.sh MUST be executable."""
        root = Path(__file__).parent.parent
        script = root / "scripts/foundation_guardrails_check.sh"
        assert os.access(script, os.X_OK), "scripts/foundation_guardrails_check.sh not executable"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
