#!/usr/bin/env python3
"""Sanity checker for KB card registry.

Scans docs/kb/40_cards/cards/**/*.md and validates:
- YAML frontmatter required keys
- Allowed actions from closed set
- Namespaced evidence keys
- Reason codes exist in registry (unless unknown_reason_code)
- Card structure and content
"""

import sys
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# Closed set of allowed action tokens (from CARDS_AUTHORING_PROMPT.md)
ALLOWED_ACTIONS = {
    "adjust_selector",
    "adjust_timeout",
    "add_evidence_key",
    "add_guardrail",
    "add_retry_gate",
    "add_debug_snapshot",
    "update_reason_mapping",
    "update_locale_token",
    "update_extraction_rule",
    "update_config_default",
    "add_test",
    "update_docs",
}

# Evidence key namespaces (required to match one of these prefixes)
ALLOWED_NAMESPACES = {
    "ui",
    "time",
    "budget",
    "calendar",
    "verify",
    "suggest",
    "dom",
    "net",
    "input",
}

# Required YAML fields (from template schema)
REQUIRED_YAML_FIELDS = {
    "id",
    "site",
    "scope",
    "page_kind",
    "locale",
    "reason_code",
    "symptoms",
    "evidence_required",
    "actions_allowed",
    "risk",
    "confidence",
    "last_updated",
}

# Valid reason codes (canonical codes from core/scenario/reasons.py)
# This is a subset; full registry is in core/scenario/reasons.py
VALID_REASON_CODES = {
    "calendar_dialog_not_found",
    "month_nav_exhausted",
    "calendar_day_not_found",
    "date_picker_unverified",
    "iata_mismatch",
    "suggestion_not_found",
    "budget_hit",
    "deadline_hit",
    "wall_clock_timeout",
    "selector_not_found",
    "unknown_reason_code",  # Special marker for unclassified cards
}

# Valid risk levels
VALID_RISK_LEVELS = {"low", "medium", "high"}

# Required body sections (from template)
REQUIRED_BODY_SECTIONS = {
    "## When to use",
    "## Preconditions",
    "## Evidence required",
    "## Diagnosis",
    "## Best patch plan",
    "## Rollback",
    "## Tests",
    "## Notes",
    "## Anti-patterns",
}

# Forbidden words (indicates narrative language)
FORBIDDEN_WORDS = {"we ", "recently", "phase", "eventually", "later", "future", "planned"}


def extract_yaml_frontmatter(content: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Extract YAML frontmatter and body from card content.

    Returns:
        (yaml_dict, body) or (None, None) if no frontmatter found.
    """
    if not content.startswith("---"):
        return None, None

    lines = content.split("\n")
    end_idx = None

    for i in range(1, len(lines)):
        if lines[i].startswith("---"):
            end_idx = i
            break

    if end_idx is None:
        return None, None

    yaml_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])

    # Simple YAML parsing
    yaml_dict = {}
    for line in yaml_text.split("\n"):
        if not line.strip() or line.startswith("#"):
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        # Handle simple lists
        if value.startswith("[") and value.endswith("]"):
            # Parse simple list
            value = [v.strip() for v in value[1:-1].split(",")]

        yaml_dict[key] = value

    return yaml_dict, body


def validate_yaml_fields(yaml_dict: Dict, filepath: str) -> List[str]:
    """Validate required YAML fields."""
    errors = []

    for field in REQUIRED_YAML_FIELDS:
        if field not in yaml_dict:
            errors.append(f"  ❌ Missing required field: {field}")

    return errors


def validate_reason_code(yaml_dict: Dict, filepath: str) -> List[str]:
    """Validate reason_code is in registry."""
    errors = []

    reason_code = yaml_dict.get("reason_code", "").strip()
    if reason_code not in VALID_REASON_CODES:
        errors.append(f"  ❌ Invalid reason_code '{reason_code}' (not in registry)")

    return errors


def validate_actions(yaml_dict: Dict, filepath: str) -> List[str]:
    """Validate actions_allowed are from closed set."""
    errors = []

    actions_str = yaml_dict.get("actions_allowed", "")
    if isinstance(actions_str, str):
        # Parse list-like string
        actions = [a.strip() for a in actions_str.split(",")]
    elif isinstance(actions_str, list):
        actions = actions_str
    else:
        actions = []

    for action in actions:
        if action and action not in ALLOWED_ACTIONS:
            errors.append(f"  ❌ Invalid action '{action}' (not in closed set)")

    return errors


def validate_evidence_keys(yaml_dict: Dict, filepath: str) -> List[str]:
    """Validate evidence_required keys are namespaced."""
    errors = []

    evidence_str = yaml_dict.get("evidence_required", "")
    if isinstance(evidence_str, str):
        # Parse list-like string
        keys = [k.strip() for k in evidence_str.split(",")]
    elif isinstance(evidence_str, list):
        keys = evidence_str
    else:
        keys = []

    for key in keys:
        if not key:
            continue

        # Check if has dot separator
        if "." not in key:
            errors.append(f"  ❌ Evidence key not namespaced: '{key}' (missing dot separator)")
            continue

        namespace = key.split(".")[0]
        if namespace not in ALLOWED_NAMESPACES:
            errors.append(f"  ❌ Invalid evidence namespace '{namespace}' in '{key}'")

    return errors


def validate_risk_confidence(yaml_dict: Dict, filepath: str) -> List[str]:
    """Validate risk level and confidence score."""
    errors = []

    risk = yaml_dict.get("risk", "").strip().lower()
    if risk not in VALID_RISK_LEVELS:
        errors.append(f"  ❌ Invalid risk level '{risk}' (must be low, medium, or high)")

    confidence_str = yaml_dict.get("confidence", "").strip()
    try:
        confidence = float(confidence_str)
        if not (0.0 <= confidence <= 1.0):
            errors.append(f"  ❌ Confidence {confidence} out of range [0.0, 1.0]")
    except ValueError:
        errors.append(f"  ❌ Invalid confidence value '{confidence_str}' (must be float)")

    return errors


def validate_body_structure(body: str, filepath: str) -> List[str]:
    """Validate body contains all required sections."""
    errors = []

    for section in REQUIRED_BODY_SECTIONS:
        if section not in body:
            errors.append(f"  ❌ Missing required section: {section}")

    return errors


def validate_forbidden_words(content: str, filepath: str) -> List[str]:
    """Check for forbidden narrative language."""
    errors = []

    content_lower = content.lower()
    for word in FORBIDDEN_WORDS:
        if word in content_lower:
            errors.append(f"  ⚠️  Found forbidden word: '{word}' (indicates narrative language)")

    return errors


def validate_card(filepath: Path) -> Tuple[bool, List[str]]:
    """Validate a single card file.

    Returns:
        (is_valid, errors_list)
    """
    errors = []

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"  ❌ Failed to read file: {e}"]

    yaml_dict, body = extract_yaml_frontmatter(content)

    if yaml_dict is None:
        return False, ["  ❌ No YAML frontmatter found (missing '---' markers)"]

    # Run all validation checks
    errors.extend(validate_yaml_fields(yaml_dict, str(filepath)))
    errors.extend(validate_reason_code(yaml_dict, str(filepath)))
    errors.extend(validate_actions(yaml_dict, str(filepath)))
    errors.extend(validate_evidence_keys(yaml_dict, str(filepath)))
    errors.extend(validate_risk_confidence(yaml_dict, str(filepath)))
    errors.extend(validate_body_structure(body or "", str(filepath)))
    errors.extend(validate_forbidden_words(content, str(filepath)))

    return len(errors) == 0, errors


def main():
    """Scan and validate all cards in docs/kb/40_cards/cards/."""
    repo_root = Path(__file__).resolve().parents[1]
    cards_dir = repo_root / "docs" / "kb" / "40_cards" / "cards"

    if not cards_dir.exists():
        print(f"❌ Cards directory not found: {cards_dir}")
        return 1

    card_files = sorted(cards_dir.glob("**/*.md"))

    if not card_files:
        print(f"⚠️  No cards found in {cards_dir}")
        return 0

    total = len(card_files)
    valid_count = 0
    all_errors = []

    print(f"\n📋 KB Cards Sanity Check\n")
    print(f"Scanning {total} card(s) in {cards_dir}\n")
    print("-" * 80)

    for filepath in card_files:
        is_valid, errors = validate_card(filepath)

        relative_path = filepath.relative_to(cards_dir.parent.parent)
        status = "✅" if is_valid else "❌"

        print(f"\n{status} {relative_path}")

        if errors:
            for error in errors:
                print(error)
            all_errors.append((relative_path, errors))
        else:
            valid_count += 1

    print("\n" + "-" * 80)
    print(f"\n📊 Results: {valid_count}/{total} cards valid")

    if all_errors:
        print(f"\n❌ Found issues in {len(all_errors)} card(s)")
        return 1
    else:
        print(f"\n✅ All cards passed validation!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
