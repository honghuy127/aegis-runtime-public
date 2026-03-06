"""Tests for KB Cards contract and smoke check (tests/test_kb_cards_contract.py).

Tests are deterministic and use temporary directories; no network, browser, or LLM calls.
"""

import pytest
from pathlib import Path
from utils.kb_cards import (
    Card,
    check_cards,
    print_check_report,
    load_kb_cards,
    validate_card,
)


class TestCheckCardsBasic:
    """Test smoke check on valid cards."""

    def test_check_valid_cards(self, tmp_path):
        """Check reports success for valid cards."""
        # Create test cards directory
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create a valid card
        site_dir = cards_dir / "google_flights" / "test_reason"
        site_dir.mkdir(parents=True)
        card_file = site_dir / "test_card.md"
        card_file.write_text(
            """---
id: test-card-001
site: google_flights
reason_code: test_reason
locale: [ja-JP]
page_kind: [flights_results]
actions_allowed: [adjust_selector]
evidence_required: [ui.selector]
kb_links: []
---

# Test Card

Body content here.
""",
            encoding="utf-8",
        )

        # Run check
        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert exit_code == 0
        assert results["total_cards"] == 1
        assert results["by_site"]["google_flights"] == 1
        assert results["by_reason"]["test_reason"] == 1
        assert len(results["invalid_cards"]) == 0

    def test_check_invalid_missing_site(self, tmp_path):
        """Detect invalid cards missing required fields."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create card with missing site
        site_dir = cards_dir / "google_flights" / "bad_reason"
        site_dir.mkdir(parents=True)
        card_file = site_dir / "bad_card.md"
        card_file.write_text(
            """---
site:
reason_code: bad_reason
---

# Bad Card
""",
            encoding="utf-8",
        )

        # Run check
        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        # Should report warning but exit 0 in non-strict mode
        assert exit_code == 0
        assert len(results["invalid_cards"]) > 0

    def test_check_strict_mode_fails(self, tmp_path):
        """Strict mode returns exit code 1 for any issues."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create card with missing field
        site_dir = cards_dir / "google_flights" / "bad_reason"
        site_dir.mkdir(parents=True)
        card_file = site_dir / "bad_card.md"
        card_file.write_text(
            """---
site: google_flights
reason_code: bad_reason
---

# Bad Card
""",
            encoding="utf-8",
        )

        # Run check in strict mode
        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=True,
        )

        # Strict mode should return 1 when there are issues
        assert exit_code == 1
        assert len(results["invalid_cards"]) > 0  # Missing actions_allowed

    def test_check_duplicate_id(self, tmp_path):
        """Detect duplicate IDs."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create two cards with same ID
        site_dir = cards_dir / "google_flights" / "reason1"
        site_dir.mkdir(parents=True)

        for i in range(2):
            card_file = site_dir / f"card{i}.md"
            card_file.write_text(
                """---
id: duplicate-id-001
site: google_flights
reason_code: reason1
title: Test Card
actions_allowed: [test]
evidence_required: [ui.test]
---

# Test Card
""",
                encoding="utf-8",
            )

        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert len(results["duplicate_ids"]) > 0
        assert "duplicate-id-001" in str(results["duplicate_ids"])

    def test_check_duplicate_tuple(self, tmp_path):
        """Detect duplicate (site, reason_code, title) tuples."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        site_dir = cards_dir / "google_flights" / "reason1"
        site_dir.mkdir(parents=True)

        for i in range(2):
            card_file = site_dir / f"card{i}.md"
            card_file.write_text(
                """---
site: google_flights
reason_code: reason1
title: Duplicate Title
actions_allowed: [test]
evidence_required: [ui.test]
---

# Duplicate Title
""",
                encoding="utf-8",
            )

        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert len(results["duplicate_ids"]) > 0
        assert "site, reason_code, title" in str(results["duplicate_ids"])

    def test_check_bad_kb_link_file_missing(self, tmp_path):
        """Detect kb_links pointing to missing files."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        site_dir = cards_dir / "google_flights" / "reason1"
        site_dir.mkdir(parents=True)

        card_file = site_dir / "card.md"
        card_file.write_text(
            """---
site: google_flights
reason_code: reason1
title: Test Card
actions_allowed: [test]
evidence_required: [ui.test]
kb_links: [docs/kb/NONEXISTENT.md]
---

# Test Card
""",
            encoding="utf-8",
        )

        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert len(results["bad_kb_links"]) > 0
        assert "NONEXISTENT.md" in str(results["bad_kb_links"])

    def test_check_bad_kb_link_anchor_missing(self, tmp_path):
        """Detect kb_links with missing anchors."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        kb_dir = tmp_path / "docs" / "kb"
        cards_dir.mkdir(parents=True)
        kb_dir.mkdir(parents=True, exist_ok=True)

        # Create target file without the anchor
        target_file = kb_dir / "TRIAGE_RUNBOOK.md"
        target_file.write_text("# Triage Runbook\n\nContent here.\n", encoding="utf-8")

        site_dir = cards_dir / "google_flights" / "reason1"
        site_dir.mkdir(parents=True)

        card_file = site_dir / "card.md"
        card_file.write_text(
            """---
site: google_flights
reason_code: reason1
title: Test Card
actions_allowed: [test]
evidence_required: [ui.test]
kb_links: [docs/kb/TRIAGE_RUNBOOK.md#nonexistent_anchor]
---

# Test Card
""",
            encoding="utf-8",
        )

        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert len(results["bad_kb_links"]) > 0
        assert "nonexistent_anchor" in str(results["bad_kb_links"]).lower()


class TestCheckCardsIntegration:
    """Test check_cards with real KB structure."""

    def test_check_report_format(self, capsys, tmp_path):
        """Verify print_check_report output format."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        site_dir = cards_dir / "google_flights" / "reason1"
        site_dir.mkdir(parents=True)

        card_file = site_dir / "card.md"
        card_file.write_text(
            """---
site: google_flights
reason_code: reason1
title: Test
actions_allowed: [test]
evidence_required: [ui.test]
---

# Test
""",
            encoding="utf-8",
        )

        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        print_check_report(results, strict=False)
        captured = capsys.readouterr()

        assert "KB Cards Smoke Test Report" in captured.out
        assert "Total cards: 1" in captured.out
        assert "google_flights" in captured.out
        assert "reason1" in captured.out

    def test_filter_cards_after_check(self, tmp_path):
        """Verify filter_cards works after check successfully validates."""
        cards_dir = tmp_path / "docs" / "kb" / "cards"
        cards_dir.mkdir(parents=True)

        # Create multiple cards
        for site, reason in [
            ("google_flights", "calendar"),
            ("google_flights", "iata"),
            ("skyscanner", "calendar"),
        ]:
            site_dir = cards_dir / site / reason
            site_dir.mkdir(parents=True)
            card_file = site_dir / "card.md"
            card_file.write_text(
                f"""---
site: {site}
reason_code: {reason}
title: Test {site} {reason}
actions_allowed: [test]
evidence_required: [ui.test]
---

# Test {site} {reason}
""",
                encoding="utf-8",
            )

        # Load and check
        exit_code, results = check_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        assert exit_code == 0
        assert results["total_cards"] == 3

        # Load cards
        cards = load_kb_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_path,
            strict=False,
        )

        # Filter by site
        filtered = [c for c in cards if c.site == "google_flights"]
        assert len(filtered) == 2

        # Filter by reason
        filtered = [c for c in cards if c.reason_code == "calendar"]
        assert len(filtered) == 2
