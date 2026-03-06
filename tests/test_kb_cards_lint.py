"""Tests for KB Cards Linter — Enforce format discipline.

Tests cover:
- Valid card fixture
- Missing required fields
- Invalid reason codes
- Invalid evidence key formats
- Body exceeding max_tokens
- Bullet limit violations
- Forbidden narrative words
"""

import tempfile
from pathlib import Path
import pytest

from utils.kb_cards_lint import (
    lint_card, lint_cards,
    _parse_yaml_frontmatter,
    _count_tokens,
    _count_bullets_in_section,
    _has_forbidden_words,
)


# ============================================================================
# Fixtures
# ============================================================================

VALID_CARD_CONTENT = """---
id: calendar_not_open_diagnostic
site: google_flights
scope: date_picker_interactions
applies_to: [depart_date, return_date]
signals: [timeout_on_date_field, no_calendar_element]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector_attempts, calendar.current_month]
actions_allowed: 3
max_tokens: 150
---

## Problem
User is unable to set departure date on Google Flights because calendar does not open.

## Likely cause
- Selector is stale and not matching current DOM
- Calendar initialization JavaScript failed
- Date field is disabled or hidden

## Best patch
- Refresh the date field selector with current DOM state
- Verify date input element visibility
- Check for JavaScript errors in console

## References
- docs/kb/30_patterns/date_picker.md
- docs/kb/20_decision_system/triage_runbook.md#calendar_not_open
"""


@pytest.fixture
def temp_cards_dir():
    """Create temporary cards directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ============================================================================
# Test: YAML Parsing
# ============================================================================

class TestYamlFrontmatterParsing:
    """Test frontmatter extraction and parsing."""

    def test_parse_valid_frontmatter(self):
        """Valid frontmatter parses correctly."""
        fm, body = _parse_yaml_frontmatter(VALID_CARD_CONTENT)
        assert fm is not None
        assert fm["id"] == "calendar_not_open_diagnostic"
        assert fm["site"] == "google_flights"
        assert fm["max_tokens"] == 150
        assert "Problem" in body

    def test_parse_missing_frontmatter(self):
        """Missing frontmatter returns None."""
        content = "# No frontmatter\nJust content"
        fm, body = _parse_yaml_frontmatter(content)
        assert fm is None
        assert content in body

    def test_parse_list_fields(self):
        """List fields parse correctly."""
        fm, _ = _parse_yaml_frontmatter(VALID_CARD_CONTENT)
        assert isinstance(fm["applies_to"], list)
        assert "depart_date" in fm["applies_to"]
        assert isinstance(fm["reason_codes"], list)


# ============================================================================
# Test: Token Counting
# ============================================================================

class TestTokenCounting:
    """Test token (word) counting."""

    def test_count_tokens_simple(self):
        """Simple word count."""
        assert _count_tokens("hello world") == 2

    def test_count_tokens_multiline(self):
        """Token count across lines."""
        text = "hello world\nfoo bar"
        assert _count_tokens(text) == 4

    def test_count_tokens_extra_whitespace(self):
        """Extra whitespace doesn't double-count."""
        assert _count_tokens("hello    world") == 2


# ============================================================================
# Test: Bullet Section Parsing
# ============================================================================

class TestBulletSectionParsing:
    """Test bullet counting in sections."""

    def test_count_bullets_valid_section(self):
        """Bullet count in valid section."""
        body = """## Likely cause
- first item
- second item
- third item
"""
        count = _count_bullets_in_section(body, "Likely cause")
        assert count == 3

    def test_count_bullets_no_section(self):
        """Missing section returns 0."""
        body = "## Some other section\n- item"
        count = _count_bullets_in_section(body, "Likely cause")
        assert count == 0

    def test_count_bullets_case_insensitive(self):
        """Section matching is case-insensitive."""
        body = "## LIKELY CAUSE\n- item1\n- item2"
        count = _count_bullets_in_section(body, "Likely cause")
        assert count == 2


# ============================================================================
# Test: Forbidden Words
# ============================================================================

class TestForbiddenWords:
    """Test forbidden word detection."""

    def test_no_forbidden_words(self):
        """Clean text returns None."""
        text = "This is a normal diagnostic card."
        assert _has_forbidden_words(text) is None

    def test_forbidden_word_recently(self):
        """Detects 'recently'."""
        text = "We recently fixed this issue."
        assert _has_forbidden_words(text) == "recently"

    def test_forbidden_word_we(self):
        """Detects 'we '."""
        text = "We recommend checking the selector."
        assert _has_forbidden_words(text) == "we "

    def test_forbidden_word_phase(self):
        """Detects 'phase'."""
        text = "In phase 2 this was implemented."
        assert _has_forbidden_words(text) == "phase"

    def test_case_insensitive_match(self):
        """Matching is case-insensitive."""
        text = "RECENTLY we added this."
        forbidden = _has_forbidden_words(text)
        assert forbidden is not None


# ============================================================================
# Test: Full Card Linting
# ============================================================================

class TestCardLinting:
    """Test full card validation."""

    def test_valid_card(self, temp_cards_dir):
        """Valid card passes all checks."""
        card_file = temp_cards_dir / "valid.md"
        card_file.write_text(VALID_CARD_CONTENT)

        errors = lint_card(str(card_file))
        assert len(errors) == 0, f"Valid card failed: {errors}"

    def test_missing_frontmatter(self, temp_cards_dir):
        """Missing frontmatter fails."""
        card_file = temp_cards_dir / "no_fm.md"
        card_file.write_text("# Just a heading\nNo frontmatter here.")

        errors = lint_card(str(card_file))
        assert any("YAML frontmatter" in e for e in errors)

    def test_missing_required_field(self, temp_cards_dir):
        """Missing required field fails."""
        content = """---
id: test_card
site: google_flights
---
Body text.
"""
        card_file = temp_cards_dir / "missing_field.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("Missing required field" in e for e in errors)

    def test_empty_required_field(self, temp_cards_dir):
        """Empty required field fails."""
        content = """---
id: test_card
site: google_flights
scope:
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: 100
---
Body.
"""
        card_file = temp_cards_dir / "empty_field.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("empty" in e.lower() for e in errors)

    def test_invalid_evidence_key_format(self, temp_cards_dir):
        """Invalid evidence key format fails."""
        content = """---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [invalid_format, ui.good_key, UPPERCASE.key]
actions_allowed: 1
max_tokens: 100
---
Body.
"""
        card_file = temp_cards_dir / "bad_evidence_key.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("evidence key format" in e for e in errors)

    def test_max_tokens_not_integer(self, temp_cards_dir):
        """Non-integer max_tokens fails."""
        content = """---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: "not_a_number"
---
Body text.
"""
        card_file = temp_cards_dir / "bad_max_tokens.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("max_tokens must be integer" in e for e in errors)

    def test_body_exceeds_token_limit(self, temp_cards_dir):
        """Body exceeding max_tokens fails."""
        long_body = " ".join(["word"] * 200)
        content = f"""---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: 50
---
{long_body}
"""
        card_file = temp_cards_dir / "body_too_long.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("token count" in e for e in errors)

    def test_too_many_bullets_likely_cause(self, temp_cards_dir):
        """Too many bullets in Likely cause section fails."""
        content = """---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: 100
---

## Likely cause
- first cause
- second cause
- third cause
- fourth cause

Body text here.
"""
        card_file = temp_cards_dir / "too_many_bullets.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("Likely cause" in e and "bullets" in e for e in errors)

    def test_too_many_bullets_best_patch(self, temp_cards_dir):
        """Too many bullets in Best patch section fails."""
        content = """---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: 100
---

## Best patch
- fix one
- fix two
- fix three
- fix four

Body text.
"""
        card_file = temp_cards_dir / "too_many_patches.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("Best patch" in e and "bullets" in e for e in errors)

    def test_forbidden_word_detection(self, temp_cards_dir):
        """Forbidden words in body fail validation."""
        content = """---
id: test_card
site: google_flights
scope: test
applies_to: [test]
signals: [test]
reason_codes: [calendar_dialog_not_found]
evidence_keys: [ui.selector]
actions_allowed: 1
max_tokens: 100
---

We recently added this feature.
This is a known issue.
"""
        card_file = temp_cards_dir / "forbidden_word.md"
        card_file.write_text(content)

        errors = lint_card(str(card_file))
        assert any("forbidden word" in e for e in errors)


# ============================================================================
# Test: Batch Linting
# ============================================================================

class TestBatchLinting:
    """Test linting multiple cards."""

    def test_lint_multiple_cards(self, temp_cards_dir):
        """Multiple cards linted together."""
        (temp_cards_dir / "card1.md").write_text(VALID_CARD_CONTENT)
        (temp_cards_dir / "card2.md").write_text(VALID_CARD_CONTENT)

        errors = lint_cards(str(temp_cards_dir))
        assert len(errors) == 0

    def test_lint_empty_directory(self):
        """Empty cards directory handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = lint_cards(tmpdir)
            assert any("No .md files" in e for e in errors)

    def test_lint_nonexistent_directory(self):
        """Nonexistent directory reported."""
        errors = lint_cards("/nonexistent/path")
        assert any("not found" in e for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
