#!/usr/bin/env python3
"""
KB Taxonomy Validator

Enforces KB Constitution rules:
- Lowercase snake_case filenames only
- No dates in filenames
- Correct folder placement (00_foundation, 10_runtime_contracts, etc.)
- Cards under 40_cards/ only
- No archive/ folder under docs/kb
- No stray files directly under docs/kb/ except index.md and kb_index.yaml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List


def get_repo_root() -> Path:
    """Find the repo root (contains docs/kb)."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "docs" / "kb").exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find repo root (no docs/kb found)")


def check_filename_format(filename: str) -> List[str]:
    """Check filename follows lowercase_snake_case with no dates."""
    errors = []

    if filename.startswith("."):
        return []

    name = filename.rsplit(".", 1)[0]

    if name != name.lower():
        errors.append(
            f"  ❌ Filename must be lowercase: '{filename}' (has uppercase: {[c for c in name if c.isupper()]})"
        )

    if re.search(r"\d{4}[-]?\d{2}(?:[-]?\d{2})?", filename):
        errors.append(
            f"  ❌ Filename contains date (dates belong in history/archive, not KB): '{filename}'"
        )

    return errors


def check_kb_structure(kb_root: Path) -> List[str]:
    """Check KB folder structure and file placement."""
    errors = []

    archive_under_kb = kb_root / "archive"
    if archive_under_kb.exists() and archive_under_kb.is_dir():
        errors.append(
            "  ❌ Found 'archive/' under KB. Archives must be at: docs/archive/ (not docs/kb/archive/)"
        )

    allowed_root_files = {"index.md", "kb_index.yaml", "INDEX.md"}
    for item in kb_root.iterdir():
        if item.is_file() and item.name not in allowed_root_files:
            errors.append(
                f"  ❌ Stray file at KB root: '{item.name}'. All docs must be in taxonomy folders (00_foundation, 10_runtime_contracts, etc.) or be index.md/kb_index.yaml/INDEX.md"
            )

    valid_taxonomy_folders = {
        "00_foundation",
        "10_runtime_contracts",
        "20_decision_system",
        "30_patterns",
        "40_cards",
        "50_governance",
    }

    for taxonomy_folder in kb_root.iterdir():
        if not taxonomy_folder.is_dir() or taxonomy_folder.name.startswith("."):
            continue

        if taxonomy_folder.name not in valid_taxonomy_folders:
            if taxonomy_folder.name in {"patterns", "contracts"}:
                errors.append(
                    f"  ❌ Old KB folder found: '{taxonomy_folder.name}'. These have been reorganized into numbered taxonomy folders (e.g., 30_patterns, 10_runtime_contracts). Please move/merge content."
                )
            else:
                errors.append(
                    f"  ❌ Invalid taxonomy folder: '{taxonomy_folder.name}'. Must be one of: {', '.join(sorted(valid_taxonomy_folders))}"
                )
            continue

        for root, dirs, files in taxonomy_folder.walk():
            if "archive" in dirs:
                errors.append(
                    f"  ❌ Found 'archive/' subfolder under {taxonomy_folder.name}. Archives must be under docs/archive/, not docs/kb/"
                )
                dirs.remove("archive")

            for filename in files:
                if filename.startswith("."):
                    continue
                if taxonomy_folder.name == "40_cards" and "-" in filename and filename.endswith(".md"):
                    continue
                errors.extend(check_filename_format(filename))

    cards_folder = kb_root / "40_cards"
    if cards_folder.exists():
        for item in cards_folder.rglob("*.md"):
            rel_path = item.relative_to(cards_folder)
            if item.parent == cards_folder:
                if not re.match(r"^[A-Z_]+_INDEX\.md$", item.name):
                    if not re.match(r"^[a-z_]+\.md$", item.name):
                        pass
            elif "cards" not in rel_path.parts:
                errors.append(f"  ❌ Card doc outside cards/ structure: {item.relative_to(kb_root)}")

    return errors


def check_temporal_language(kb_root: Path) -> List[str]:
    """Warn about temporal language in KB docs (not an error, but flagged)."""
    warnings = []
    temporal_patterns = [
        (r"\brecently\b", "recently"),
        (r"\bwe added\b", "we added"),
        (r"\bas of \d+", "as of [year]"),
        (r"\bPhase [0-9]\b", "Phase [number]"),
    ]
    tier_context_patterns = r"(Phase|rollout|migration|implement|Tier [0-9].*(?:first|second|third|initially|eventually|next))"
    rule_files = {"authoring_rules.md", "precommit_guide.md", "kb_constitution.md"}

    for doc_path in kb_root.rglob("*.md"):
        if doc_path.name in rule_files:
            continue
        try:
            content = doc_path.read_text()
            lines = content.split("\n")
            for line_num, line in enumerate(lines, 1):
                if line.strip().startswith("```"):
                    continue
                for pattern, label in temporal_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        rel_path = doc_path.relative_to(kb_root)
                        warnings.append(
                            f"  ⚠️  Temporal language ({label}) in KB doc: {rel_path}:{line_num}"
                        )
                if re.search(r"Tier [0-9]", line, re.IGNORECASE):
                    if re.search(tier_context_patterns, line, re.IGNORECASE | re.VERBOSE):
                        rel_path = doc_path.relative_to(kb_root)
                        warnings.append(
                            f"  ⚠️  Temporal language (Tier with rollout context) in KB doc: {rel_path}:{line_num}"
                        )
        except Exception:
            pass

    return warnings


def main():
    """Run all KB taxonomy checks."""
    try:
        repo_root = get_repo_root()
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1

    kb_root = repo_root / "docs" / "kb"

    print("=" * 70)
    print("KB TAXONOMY CHECK")
    print("=" * 70)

    errors = []
    errors.extend(check_kb_structure(kb_root))

    warnings = []
    warnings.extend(check_temporal_language(kb_root))

    if errors:
        print("\n❌ VIOLATIONS FOUND:\n")
        for error in errors:
            print(error)
        print()

    if warnings:
        print("\n⚠️  WARNINGS (non-blocking):\n")
        for warning in warnings:
            print(warning)
        print()

    if not errors and not warnings:
        print("\n✅ KB structure is compliant with KB Constitution.\n")
        return 0

    if errors:
        print("=" * 70)
        print("FIXES:")
        print("  - Move docs to correct taxonomy folder (00_foundation, 10_runtime_contracts, etc.)")
        print("  - Rename files to lowercase_snake_case (no dates, no uppercase)")
        print("  - Move cards to: docs/kb/40_cards/cards/<site>/<reason_code>/")
        print("  - Move archives to: docs/archive/ (not docs/kb/)")
        print("=" * 70)
        print()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
