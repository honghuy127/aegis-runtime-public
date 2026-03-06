"""Test KB paths compatibility after reorganization.

Validates that the KB structure is accessible under the new taxonomy:
- docs/kb/kb_index.yaml (machine-readable index)
- docs/kb/INDEX.md (reading guide)
- docs/kb/40_cards/ (cards content)
- Card loaders point to new locations
- No broken internal references

Tests are filesystem only; no network, no LLM calls.
"""

import pytest
from pathlib import Path
from utils.kb import load_kb_index
from utils.kb_cards import load_kb_cards


class TestKBPathsCompat:
    """Validate KB files exist at new canonical locations."""

    @pytest.fixture
    def repo_root(self):
        """Get repository root."""
        return Path(__file__).resolve().parent.parent

    def test_kb_index_exists(self, repo_root):
        """docs/kb/kb_index.yaml exists and is readable."""
        yaml_path = repo_root / "docs" / "kb" / "kb_index.yaml"
        assert yaml_path.exists(), f"KB index not found at {yaml_path}"
        assert yaml_path.stat().st_size > 0, "KB index is empty"

    def test_kb_index_canonical_md(self, repo_root):
        """docs/kb/INDEX.md exists (reading guide)."""
        index_path = repo_root / "docs" / "kb" / "INDEX.md"
        assert index_path.exists(), f"KB INDEX.md not found at {index_path}"
        assert index_path.stat().st_size > 0, "KB INDEX.md is empty"

    def test_kb_foundation_folder(self, repo_root):
        """docs/kb/00_foundation/ contains key docs."""
        foundation = repo_root / "docs" / "kb" / "00_foundation"
        assert foundation.exists(), "00_foundation folder missing"

        required = ["doctrine.md", "architecture.md", "architecture_invariants.md"]
        for fname in required:
            fpath = foundation / fname
            assert fpath.exists(), f"Required file {fname} missing from 00_foundation/"

    def test_kb_runtime_contracts_folder(self, repo_root):
        """docs/kb/10_runtime_contracts/ contains contract docs."""
        contracts = repo_root / "docs" / "kb" / "10_runtime_contracts"
        assert contracts.exists(), "10_runtime_contracts folder missing"

        required = ["budgets_timeouts.md", "evidence.md", "scenario_runner.md", "browser_contract.md", "plugins.md"]
        for fname in required:
            fpath = contracts / fname
            assert fpath.exists(), f"Required file {fname} missing from 10_runtime_contracts/"

    def test_kb_decision_system_folder(self, repo_root):
        """docs/kb/20_decision_system/ contains playbooks and triage."""
        decision = repo_root / "docs" / "kb" / "20_decision_system"
        assert decision.exists(), "20_decision_system folder missing"

        required = ["runtime_playbook.md", "triage_runbook.md"]
        for fname in required:
            fpath = decision / fname
            assert fpath.exists(), f"Required file {fname} missing from 20_decision_system/"

    def test_kb_patterns_folder(self, repo_root):
        """docs/kb/30_patterns/ contains pattern docs."""
        patterns = repo_root / "docs" / "kb" / "30_patterns"
        assert patterns.exists(), "30_patterns folder missing"

        required = ["date_picker.md", "combobox_commit.md", "selectors.md", "i18n_ja.md"]
        for fname in required:
            fpath = patterns / fname
            assert fpath.exists(), f"Required file {fname} missing from 30_patterns/"

    def test_kb_cards_folder_structure(self, repo_root):
        """docs/kb/40_cards/ has expected structure."""
        cards_root = repo_root / "docs" / "kb" / "40_cards"
        assert cards_root.exists(), "40_cards folder missing"

        # Template files should exist
        required_templates = ["template.md", "authoring_rules.md", "cards_index.md", "precommit_guide.md"]
        for fname in required_templates:
            fpath = cards_root / fname
            assert fpath.exists(), f"Template file {fname} missing from 40_cards/"

        # Cards content folder should exist
        cards_content = cards_root / "cards"
        assert cards_content.exists(), "40_cards/cards content folder missing"

    def test_kb_governance_folder(self, repo_root):
        """docs/kb/50_governance/ contains governance docs."""
        governance = repo_root / "docs" / "kb" / "50_governance"
        assert governance.exists(), "50_governance folder missing"

        # Check for key governance files
        assert (governance / "stage0_guardrails.md").exists(), "stage0_guardrails.md missing"
        assert (governance / "adr").exists(), "adr subfolder missing"


class TestKBLoaders:
    """Test that KB loaders work with new paths."""

    @pytest.fixture
    def repo_root(self):
        """Get repository root."""
        return Path(__file__).resolve().parent.parent

    def test_load_kb_index(self, repo_root):
        """KB index loader finds and parses kb_index.yaml."""
        index = load_kb_index(str(repo_root))
        assert index is not None
        assert index.version != "0"  # Non-empty index loaded
        assert len(index.entrypoints) > 0, "No entrypoints loaded from KB index"
        assert len(index.topics) > 0, "No topics loaded from KB index"

    def test_load_kb_cards_new_path(self, repo_root):
        """Cards loader finds cards under new docs/kb/40_cards/cards path."""
        cards = load_kb_cards(
            root_dir="docs/kb/40_cards/cards",
            repo_root=repo_root,
            strict=False
        )
        # Should return empty list or populated list without errors
        assert isinstance(cards, list), "load_kb_cards should return a list"

    def test_kb_index_entrypoints(self, repo_root):
        """KB index contains known entrypoints."""
        index = load_kb_index(str(repo_root))

        # Check that entrypoints reference new paths
        entrypoint_paths = [ep.path for ep in index.entrypoints]

        # Should have new-style paths (e.g., 00_foundation, 10_runtime_contracts)
        new_style_paths = [p for p in entrypoint_paths if any(
            prefix in p for prefix in ["00_foundation/", "10_runtime_contracts/",
                                      "20_decision_system/", "30_patterns/",
                                      "40_cards/", "50_governance/"]
        )]

        # At least some should be new-style
        assert len(new_style_paths) > 0, "KB index should reference new taxonomy folders"

    def test_kb_index_no_old_paths(self, repo_root):
        """KB index does NOT contain old-style paths."""
        index = load_kb_index(str(repo_root))

        entrypoint_paths = [ep.path for ep in index.entrypoints]

        # Check for old folder patterns (must be at folder boundary)
        old_patterns = [
            "kb/patterns/", "kb/contracts/",  # Old subfolders (with kb/ prefix to match actual path structure)
            "CARDS_", "RUNTIME_CONTRACTS", "ARCHITECTURE_INVARIANTS", "SYSTEM_OVERVIEW",  # Old file names
            "TRIAGE_RUNBOOK",  # Should be in 20_decision_system
        ]

        for path in entrypoint_paths:
            for old_pattern in old_patterns:
                assert old_pattern not in path, f"Found old KB pattern '{old_pattern}' in path: {path}"


class TestKBPathReferences:
    """Verify internal KB doc references are updated."""

    @pytest.fixture
    def repo_root(self):
        """Get repository root."""
        return Path(__file__).resolve().parent.parent

    def test_no_old_patterns_in_python(self, repo_root):
        """Python source files don't reference old KB paths."""
        old_kb_patterns = [
            "docs/kb/patterns/",
            "docs/kb/contracts/",
            "docs/kb/CARDS_",
            "docs/kb/RUNTIME_CONTRACTS",
            "docs/kb/ARCHITECTURE_INVARIANTS",
            "docs/kb/SYSTEM_OVERVIEW",
            "docs/kb/TRIAGE_RUNBOOK",  # Should be in 20_decision_system
        ]

        py_files = list((repo_root / "core").rglob("*.py")) + \
                  list((repo_root / "utils").rglob("*.py")) + \
                  list((repo_root / "tests").rglob("*.py"))

        offenders = []
        for pfile in py_files:
            try:
                content = pfile.read_text()
                for pattern in old_kb_patterns:
                    if pattern in content:
                        offenders.append((str(pfile.relative_to(repo_root)), pattern))
            except Exception:
                pass

        # Allow some legacy references in reason codes or historical docs
        filtered = [
            (f, p) for f, p in offenders
            if not any(skip in f for skip in ["__pycache__", "test_", "archive", "history"])
        ]

        assert len(filtered) == 0, f"Found old KB paths in Python:\n" + "\n".join(
            f"  {f}: {p}" for f, p in filtered[:10]
        )

    def test_no_stale_old_folders(self, repo_root):
        """No stale docs/kb/patterns/ or docs/kb/contracts/ folders with content."""
        kb_root = repo_root / "docs" / "kb"

        old_folders = [
            kb_root / "patterns",
            kb_root / "contracts",
        ]

        for folder in old_folders:
            if folder.exists():
                # Should be empty (all files moved)
                files = list(folder.glob("**/*.md"))
                assert len(files) == 0, f"Stale KB folder has content: {folder}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
