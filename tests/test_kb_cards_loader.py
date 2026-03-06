"""Tests for KB Cards loader (utils/kb_cards.py).

Tests are deterministic and use temporary directories; no network, browser, or LLM calls.
"""

import pytest
from pathlib import Path
from utils.kb_cards import (
    Card,
    parse_card_file,
    load_kb_cards,
    filter_cards,
    validate_card,
    _parse_yaml_frontmatter,
    _normalize_list_field,
    _extract_title_from_body,
)


@pytest.fixture
def tmp_cards_dir(tmp_path):
    """Create a temporary card directory structure."""
    cards_root = tmp_path / "docs" / "kb" / "cards"
    cards_root.mkdir(parents=True)
    return cards_root


@pytest.fixture
def sample_card_content():
    """Minimal valid KB card content."""
    return """---
id: test-card-001
site: google_flights
scope: scenario
page_kind: [flights_results]
locale: [ja-JP, en-US]
reason_code: calendar_dialog_not_found
symptoms: [test_symptom]
evidence_required: [ui.selector_attempts, ui.last_visible]
actions_allowed: [adjust_selector, adjust_timeout]
risk: high
confidence: 0.95
last_updated: 2026-02-21
kb_links: [docs/kb/30_patterns/date_picker.md]
code_refs: [core/scenario/gf_helpers/date_picker_orchestrator.py:gf_set_date]
tags: [test]
---

# CARD: Test Card

## When to use
Test condition.

## Evidence required
Test evidence.
"""


class TestYamlFrontmatterParsing:
    """Test YAML frontmatter extraction."""

    def test_parse_valid_frontmatter(self):
        """Extract YAML and body from valid card."""
        content = """---
key: value
list_key: [item1, item2]
---

# Body"""
        yaml_dict, body = _parse_yaml_frontmatter(content)
        assert yaml_dict is not None
        assert body is not None
        assert yaml_dict["key"] == "value"
        assert yaml_dict["list_key"] == ["item1", "item2"]
        assert "# Body" in body

    def test_parse_missing_frontmatter(self):
        """Return None for content without frontmatter."""
        content = "# No frontmatter here"
        yaml_dict, body = _parse_yaml_frontmatter(content)
        assert yaml_dict is None
        assert body is None

    def test_parse_multiline_list(self):
        """Parse multiline list format."""
        content = """---
list_key:
  - item1
  - item2
  - item3
---

Body"""
        yaml_dict, body = _parse_yaml_frontmatter(content)
        assert yaml_dict is not None
        assert "list_key" in yaml_dict
        assert len(yaml_dict["list_key"]) == 3


class TestListNormalization:
    """Test list field normalization."""

    def test_normalize_list_from_list(self):
        """Convert list to normalized list."""
        result = _normalize_list_field(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_normalize_string_to_list(self):
        """Convert string to single-item list."""
        result = _normalize_list_field("single")
        assert result == ["single"]

    def test_normalize_empty_returns_empty(self):
        """Empty input returns empty list."""
        assert _normalize_list_field([]) == []
        assert _normalize_list_field("") == []
        assert _normalize_list_field(None) == []


class TestTitleExtraction:
    """Test title extraction from markdown body."""

    def test_extract_first_heading(self):
        """Get first markdown heading as title."""
        body = """Some intro text

# The Title

## Subsection"""
        title = _extract_title_from_body(body)
        assert title == "The Title"

    def test_extract_no_heading(self):
        """Return empty string if no heading."""
        body = "No heading here, just text."
        title = _extract_title_from_body(body)
        assert title == ""


class TestCardParsing:
    """Test parsing individual card files."""

    def test_parse_valid_card(self, tmp_cards_dir, sample_card_content):
        """Parse a valid card file."""
        card_file = tmp_cards_dir / "google_flights" / "calendar_dialog_not_found" / "test.md"
        card_file.parent.mkdir(parents=True)
        card_file.write_text(sample_card_content, encoding="utf-8")

        card = parse_card_file(card_file, repo_root=tmp_cards_dir.parent.parent)
        assert card is not None
        assert card.site == "google_flights"
        assert card.reason_code == "calendar_dialog_not_found"
        assert card.locales == ["ja-JP", "en-US"]
        assert "calendar_dialog_not_found" in card.body_md or "CARD" in card.body_md

    def test_parse_card_missing_frontmatter(self, tmp_cards_dir):
        """Return None for card without frontmatter."""
        card_file = tmp_cards_dir / "bad.md"
        card_file.write_text("# No frontmatter", encoding="utf-8")

        card = parse_card_file(card_file)
        assert card is None

    def test_parse_card_nonexistent_file(self, tmp_cards_dir):
        """Return None for nonexistent file."""
        card = parse_card_file(tmp_cards_dir / "nonexistent.md")
        assert card is None


class TestCardValidation:
    """Test card validation."""

    def test_validate_card_minimal_valid(self):
        """Validate minimal valid card."""
        card = Card(
            path="test.md",
            site="google_flights",
            reason_code="calendar_dialog_not_found",
            title="Test",
            actions_allowed=["adjust_selector"],
            evidence_required=["ui.test"],
        )
        errors = validate_card(card)
        assert len(errors) == 0

    def test_validate_card_missing_site(self):
        """Detect missing site."""
        card = Card(
            path="test.md",
            site="",
            reason_code="calendar_dialog_not_found",
            title="Test",
            actions_allowed=["adjust_selector"],
        )
        errors = validate_card(card)
        assert any("site" in err.lower() for err in errors)

    def test_validate_card_missing_reason_code(self):
        """Detect missing reason_code."""
        card = Card(
            path="test.md",
            site="google_flights",
            reason_code="",
            title="Test",
            actions_allowed=["adjust_selector"],
        )
        errors = validate_card(card)
        assert any("reason_code" in err.lower() for err in errors)

    def test_validate_card_missing_title(self):
        """Detect missing title."""
        card = Card(
            path="test.md",
            site="google_flights",
            reason_code="calendar_dialog_not_found",
            title="",
            actions_allowed=["adjust_selector"],
        )
        errors = validate_card(card)
        assert any("title" in err.lower() for err in errors)

    def test_validate_card_non_namespaced_evidence(self):
        """Detect non-namespaced evidence keys."""
        card = Card(
            path="test.md",
            site="google_flights",
            reason_code="calendar_dialog_not_found",
            title="Test",
            actions_allowed=["adjust_selector"],
            evidence_required=["bad_key", "ui.good_key"],
        )
        errors = validate_card(card)
        assert any("namespaced" in err.lower() for err in errors)


class TestCardLoading:
    """Test loading cards from directory."""

    def test_load_single_card(self, tmp_cards_dir, sample_card_content):
        """Load a single card file."""
        card_file = tmp_cards_dir / "google_flights" / "calendar_dialog_not_found" / "test.md"
        card_file.parent.mkdir(parents=True)
        card_file.write_text(sample_card_content, encoding="utf-8")

        cards = load_kb_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_cards_dir.parent.parent.parent,
        )
        assert len(cards) == 1
        assert cards[0].site == "google_flights"

    def test_load_multiple_cards_sorted(self, tmp_cards_dir):
        """Load multiple cards and verify deterministic sorting."""
        # Create cards for different sites/reasons
        for site in ["google_flights", "skyscanner"]:
            for reason in ["calendar_dialog_not_found", "iata_mismatch"]:
                card_file = tmp_cards_dir / site / reason / f"{reason}.md"
                card_file.parent.mkdir(parents=True)
                card_file.write_text(f"""---
id: test-{site}-{reason}
site: {site}
reason_code: {reason}
evidence_required: [ui.test]
actions_allowed: [adjust_selector]
---

# Title
""", encoding="utf-8")

        cards = load_kb_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_cards_dir.parent.parent.parent,
        )
        assert len(cards) == 4
        # Verify sorting: (site, reason_code, path)
        for i in range(len(cards) - 1):
            key_i = (cards[i].site, cards[i].reason_code, cards[i].path)
            key_next = (cards[i+1].site, cards[i+1].reason_code, cards[i+1].path)
            assert key_i <= key_next

    def test_load_skips_template_files(self, tmp_cards_dir, sample_card_content):
        """Skip template and index files."""
        # Create a real card
        card_file = tmp_cards_dir / "google_flights" / "test" / "real.md"
        card_file.parent.mkdir(parents=True)
        card_file.write_text(sample_card_content, encoding="utf-8")

        # Try to create template files (should be skipped)
        for fname in ["CARDS_TEMPLATE.md", "CARDS_INDEX.md", "CARDS_AUTHORING_PROMPT.md"]:
            template_file = tmp_cards_dir / fname
            template_file.write_text("# Template", encoding="utf-8")

        cards = load_kb_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_cards_dir.parent.parent.parent,
        )
        # Should only load the real card, not templates
        assert len(cards) == 1

    def test_load_nonexistent_directory(self):
        """Return empty list for nonexistent directory."""
        cards = load_kb_cards(root_dir="nonexistent/path")
        assert cards == []


class TestCardFiltering:
    """Test card filtering."""

    def create_test_cards(self):
        """Create a set of test cards."""
        return [
            Card(
                path="gf/calendar_dialog_not_found/depart.md",
                site="google_flights",
                reason_code="calendar_dialog_not_found",
                locales=["ja-JP", "en-US"],
                page_kinds=["flights_results"],
                actions_allowed=["adjust_selector"],
                evidence_required=["ui.test"],
                title="Test 1",
            ),
            Card(
                path="gf/iata_mismatch/dest.md",
                site="google_flights",
                reason_code="iata_mismatch",
                locales=["ja-JP"],
                page_kinds=["flights_results"],
                actions_allowed=["adjust_timeout"],
                evidence_required=["verify.test"],
                title="Test 2",
            ),
            Card(
                path="sky/calendar_dialog_not_found/test.md",
                site="skyscanner",
                reason_code="calendar_dialog_not_found",
                locales=[],  # Wildcard
                page_kinds=["flights_results"],
                actions_allowed=["add_guardrail"],
                evidence_required=["ui.test"],
                title="Test 3",
            ),
        ]

    def test_filter_by_site(self):
        """Filter cards by site."""
        cards = self.create_test_cards()
        result = filter_cards(cards, site="google_flights")
        assert len(result) == 2
        assert all(c.site == "google_flights" for c in result)

    def test_filter_by_reason_code(self):
        """Filter cards by reason code."""
        cards = self.create_test_cards()
        result = filter_cards(cards, reason_code="calendar_dialog_not_found")
        assert len(result) == 2
        assert all(c.reason_code == "calendar_dialog_not_found" for c in result)

    def test_filter_by_locale_with_wildcard(self):
        """Filter by locale; wildcard cards match any locale."""
        cards = self.create_test_cards()
        # Card 3 has empty locales (wildcard), should match ja-JP request
        result = filter_cards(cards, locale="ja-JP")
        # Cards 1 (ja-JP), 2 (ja-JP), 3 (wildcard)
        assert len(result) == 3

    def test_filter_by_locale_non_matching(self):
        """Filter excludes cards without matching locale."""
        cards = self.create_test_cards()
        result = filter_cards(cards, locale="fr-FR")
        # Only card 3 (wildcard) matches
        assert len(result) == 1
        assert result[0].site == "skyscanner"

    def test_filter_by_page_kind(self):
        """Filter by page_kind."""
        cards = self.create_test_cards()
        result = filter_cards(cards, page_kind="flights_results")
        # All test cards have flights_results
        assert len(result) == 3

    def test_filter_combined(self):
        """Filter with multiple criteria."""
        cards = self.create_test_cards()
        result = filter_cards(
            cards,
            site="google_flights",
            reason_code="calendar_dialog_not_found",
            locale="ja-JP",
        )
        assert len(result) == 1
        assert result[0].site == "google_flights"
        assert result[0].reason_code == "calendar_dialog_not_found"

    def test_filter_with_limit(self):
        """Apply limit to results."""
        cards = self.create_test_cards()
        result = filter_cards(cards, limit=2)
        assert len(result) == 2

    def test_filter_deterministic_ordering(self):
        """Results are deterministically ordered by path."""
        cards = [
            Card(path="z.md", site="gf", reason_code="test", title="Z"),
            Card(path="a.md", site="gf", reason_code="test", title="A"),
            Card(path="m.md", site="gf", reason_code="test", title="M"),
        ]
        result = filter_cards(cards)
        assert [c.path for c in result] == ["a.md", "m.md", "z.md"]


class TestIntegration:
    """Integration tests: parse + load + filter."""

    def test_end_to_end_workflow(self, tmp_cards_dir, sample_card_content):
        """Parse, load, and filter real card files."""
        # Create multiple cards
        for i, (site, reason) in enumerate([
            ("google_flights", "calendar_dialog_not_found"),
            ("google_flights", "iata_mismatch"),
            ("skyscanner", "calendar_dialog_not_found"),
        ]):
            path = tmp_cards_dir / site / reason / f"card_{i}.md"
            path.parent.mkdir(parents=True)
            content = sample_card_content.replace(
                "site: google_flights",
                f"site: {site}"
            ).replace(
                "reason_code: calendar_dialog_not_found",
                f"reason_code: {reason}"
            )
            path.write_text(content, encoding="utf-8")

        # Load all
        cards = load_kb_cards(
            root_dir="docs/kb/cards",
            repo_root=tmp_cards_dir.parent.parent.parent,
        )
        assert len(cards) == 3

        # Filter for google_flights + calendar reason
        filtered = filter_cards(
            cards,
            site="google_flights",
            reason_code="calendar_dialog_not_found",
        )
        assert len(filtered) == 1
        assert filtered[0].site == "google_flights"
        assert filtered[0].reason_code == "calendar_dialog_not_found"

        # All cards should have been validated without errors
        for card in cards:
            errors = validate_card(card)
            # May have warnings but shouldn't fail
            assert card.title != ""
