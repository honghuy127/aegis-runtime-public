r"""KB Cards Linter — Enforce format discipline for diagnostic cards.

This module validates KB diagnostic cards (YAML frontmatter + body) under docs/kb/40_cards/cards/.

Card schema:
  ---
  id: <string>
  site: <string>
  scope: <string>
  applies_to: <list of strings>
  signals: <list of strings>
  reason_codes: <list of strings>
  evidence_keys: <list of strings>
  actions_allowed: <integer>
  max_tokens: <integer>
  ---

  <body: markdown with sections>

Validations:
  1. All required YAML fields present and non-empty
  2. reason_codes exist in canonical registry (core.scenario.reasons)
  3. evidence_keys match regex ^[a-z]+\.[a-z0-9_]+$
  4. max_tokens is integer
  5. Body token count <= max_tokens
  6. Body sections ("Likely cause", "Best patch") have <= 3 bullets each
  7. Forbid narrative words (recently, we, phase, etc.)

Usage:
    from utils.kb_cards_lint import lint_cards
    issues = lint_cards("docs/kb/40_cards/cards/")
    if issues:
        print("\n".join(issues))
        sys.exit(1)

CLI:
    python -m utils.kb_cards_lint [directory]
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any


# Try to import reason registry; gracefully handle if not available
try:
    from core.scenario.reasons import REASON_REGISTRY, REASON_ALIASES
    REASON_CODES = set(REASON_REGISTRY.keys()) | set(REASON_ALIASES.keys())
except ImportError:
    REASON_CODES = set()
    print("[WARNING] Could not import reason registry; reason code validation skipped",
          file=sys.stderr)


# Configuration

REQUIRED_FIELDS = {
    "id", "site", "scope", "applies_to", "signals",
    "reason_codes", "evidence_keys", "actions_allowed", "max_tokens"
}

EVIDENCE_KEY_PATTERN = re.compile(r"^[a-z]+\.[a-z0-9_]+$")

FORBIDDEN_WORDS = [
    "recently",
    "in the past",
    "we ",
    "phase",
    "later",
    "eventually",
    "roadmap",
]

MAX_BULLETS_PER_SECTION = 3
BODY_TOKEN_LIMIT_MULTIPLIER = 1.1  # Allow 10% overage for robustness


def _parse_yaml_frontmatter(content: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Parse YAML frontmatter from card content.

    Returns:
        (frontmatter_dict, body_text) or (None, full_content) if no frontmatter
    """
    if not content.startswith("---"):
        return None, content

    lines = content.split("\n", 1)
    if len(lines) < 2:
        return None, content

    rest = lines[1]
    if "---" not in rest:
        return None, content

    fm_end_idx = rest.index("---")
    fm_text = rest[:fm_end_idx]
    body_text = rest[fm_end_idx + 3:].lstrip("\n")

    # Simple YAML parser for basic key: value format
    fm_dict = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue

        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()

        # Handle YAML types
        if val.lower() in ("true", "false"):
            fm_dict[key] = val.lower() == "true"
        elif val.startswith("[") and val.endswith("]"):
            # Simple list parsing: [item1, item2]
            items = val[1:-1].split(",")
            fm_dict[key] = [item.strip().strip('"\'') for item in items if item.strip()]
        else:
            # Try int, otherwise string
            try:
                fm_dict[key] = int(val)
            except ValueError:
                fm_dict[key] = val.strip().strip('"\'')

    return fm_dict, body_text


def _count_tokens(text: str) -> int:
    """Count tokens as whitespace-split words."""
    return len(text.split())


def _count_bullets_in_section(body: str, section_title: str) -> int:
    """Count bullet lines in a section.

    Section format:
        ## Likely cause
        - item1
        - item2
    """
    pattern = rf"##\s+{re.escape(section_title)}.*?(?=##|$)"
    match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
    if not match:
        return 0

    section = match.group(0)
    bullets = re.findall(r"^[-*]\s+", section, re.MULTILINE)
    return len(bullets)


def _has_forbidden_words(text: str) -> Optional[str]:
    """Check for forbidden narrative words. Return first match or None."""
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in text_lower:
            return word
    return None


def lint_card(file_path: str) -> List[str]:
    """Lint a single card file.

    Returns:
        List of error messages (empty if no errors)
    """
    errors = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return [f"{file_path}: Failed to read: {e}"]

    # Parse frontmatter
    fm, body = _parse_yaml_frontmatter(content)
    if fm is None:
        return [f"{file_path}: No YAML frontmatter found (must start with ---)"]

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"{file_path}: Missing required field: {field}")
        elif not fm[field]:  # empty value
            errors.append(f"{file_path}: Field '{field}' is empty")

    if errors:
        return errors

    # Validate reason_codes
    reason_codes = fm.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        reason_codes = [reason_codes]

    for code in reason_codes:
        if REASON_CODES and code not in REASON_CODES:
            errors.append(f"{file_path}: Unknown reason code: {code}")

    # Validate evidence_keys format
    evidence_keys = fm.get("evidence_keys", [])
    if not isinstance(evidence_keys, list):
        evidence_keys = [evidence_keys]

    for key in evidence_keys:
        if not EVIDENCE_KEY_PATTERN.match(key):
            errors.append(f"{file_path}: Invalid evidence key format: {key} (expected: ^[a-z]+\\.[a-z0-9_]+$)")

    # Validate max_tokens is integer
    max_tokens = fm.get("max_tokens")
    if not isinstance(max_tokens, int):
        errors.append(f"{file_path}: max_tokens must be integer, got: {type(max_tokens).__name__}")

    # Validate body token count
    if isinstance(max_tokens, int):
        body_tokens = _count_tokens(body)
        allowed_tokens = int(max_tokens * BODY_TOKEN_LIMIT_MULTIPLIER)
        if body_tokens > allowed_tokens:
            errors.append(f"{file_path}: Body token count {body_tokens} exceeds max_tokens {max_tokens} (allowed: {allowed_tokens})")

    # Validate section bullet counts
    likely_cause_bullets = _count_bullets_in_section(body, "Likely cause")
    if likely_cause_bullets > MAX_BULLETS_PER_SECTION:
        errors.append(f"{file_path}: 'Likely cause' section has {likely_cause_bullets} bullets (max: {MAX_BULLETS_PER_SECTION})")

    best_patch_bullets = _count_bullets_in_section(body, "Best patch")
    if best_patch_bullets > MAX_BULLETS_PER_SECTION:
        errors.append(f"{file_path}: 'Best patch' section has {best_patch_bullets} bullets (max: {MAX_BULLETS_PER_SECTION})")

    # Check for forbidden words in body
    forbidden_word = _has_forbidden_words(body)
    if forbidden_word:
        errors.append(f"{file_path}: Body contains forbidden word: '{forbidden_word}'")

    return errors


def lint_cards(directory: str) -> List[str]:
    """Lint all cards in a directory.

    Returns:
        List of all error messages
    """
    cards_dir = Path(directory)
    if not cards_dir.exists():
        return [f"Cards directory not found: {directory}"]

    if not cards_dir.is_dir():
        return [f"Not a directory: {directory}"]

    all_errors = []

    # Find all .md files in cards directory
    md_files = sorted(cards_dir.glob("*.md"))
    if not md_files:
        return [f"No .md files found in {directory}"]

    for md_file in md_files:
        errors = lint_card(str(md_file))
        all_errors.extend(errors)

    return all_errors


def main():
    """CLI entrypoint."""
    directory = sys.argv[1] if len(sys.argv) > 1 else "docs/kb/40_cards/cards"

    errors = lint_cards(directory)

    if errors:
        print(f"❌ KB Cards Linter found {len(errors)} issue(s):")
        print()
        for error in errors:
            print(f"  • {error}")
        print()
        return 1
    else:
        print(f"✅ KB Cards Linter: All cards valid in {directory}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
