"""KB Cards loader and filter (disabled by default, gated by config).

Provides lightweight read-only access to KB diagnostic cards stored in docs/kb/cards/.
Cards are normalized to CARDS_TEMPLATE.md schema with YAML frontmatter.

Module is intentionally minimal and stable:
- No narrative heuristics or smart inference
- No runtime behavior changes when kb_cards_enabled=false
- Deterministic filtering and caching
- Forward-compatible YAML parsing (flexible on unknown fields)

Usage:
    from utils.kb_cards import load_kb_cards, filter_cards

    # Load all cards from default location (docs/kb/cards/)
    cards = load_kb_cards()

    # Filter by site and reason code
    matching = filter_cards(
        cards,
        site="google_flights",
        reason_code="calendar_dialog_not_found",
        locale="ja-JP",
        limit=3
    )
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Card:
    """Parsed KB diagnostic card with minimal normalized fields."""

    path: str
    """Repository-relative path to card file."""

    site: str
    """Target site (e.g., 'google_flights', 'skyscanner', 'unknown')."""

    reason_code: str
    """Canonical reason code from core/scenario/reasons.py registry."""

    locales: List[str] = field(default_factory=list)
    """List of locale codes (e.g., ['ja-JP', 'en-US']). Empty means wildcard."""

    page_kinds: List[str] = field(default_factory=list)
    """List of page kinds (e.g., ['flights_results']). Empty means wildcard."""

    scope: Optional[str] = None
    """Optional scope (e.g., 'scenario', 'extraction'). Usually from frontmatter."""

    actions_allowed: List[str] = field(default_factory=list)
    """Allowed action tokens from closed set (e.g., 'adjust_selector', 'adjust_timeout')."""

    evidence_required: List[str] = field(default_factory=list)
    """Required evidence keys with namespaces (e.g., 'ui.selector_attempts', 'calendar.target_month')."""

    kb_links: List[str] = field(default_factory=list)
    """References to KB docs (e.g., ['docs/kb/20_decision_system/triage_runbook.md#calendar_dialog_not_found'])."""

    code_refs: List[str] = field(default_factory=list)
    """Code references (e.g., ['core/scenario/google_flights.py:gf_set_date'])."""

    title: str = ""
    """Card title/heading from markdown body."""

    body_md: str = ""
    """Markdown body without YAML frontmatter."""

    frontmatter: Dict[str, Any] = field(default_factory=dict)
    """Raw parsed YAML frontmatter for forward compatibility."""


def _normalize_list_field(value: Any) -> List[str]:
    """Normalize a field to a list of strings.

    Args:
        value: Value to normalize (str, list, None, etc).

    Returns:
        List of strings, or empty list if value is None/empty.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    if isinstance(value, list):
        items = []
        for item in value:
            s = str(item).strip() if item else ""
            if s:
                items.append(s)
        return items
    return []


def _extract_title_from_body(body: str) -> str:
    """Extract first heading from markdown body as title.

    Args:
        body: Markdown body content.

    Returns:
        Title from first H1/H2/H3, or empty string if not found.
    """
    if not body:
        return ""
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            # Remove # symbols and trim
            title = line.lstrip("#").strip()
            return title
    return ""


def _parse_yaml_frontmatter(content: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse YAML frontmatter and body text from card content.

    Extracts frontmatter between leading '---' and next '---'.
    Returns (frontmatter_dict, body_text) or (None, None) if no frontmatter found.

    Args:
        content: Full card file content.

    Returns:
        (yaml_dict, body) or (None, None) if parsing fails.
    """
    if not content.startswith("---"):
        return None, None

    lines = content.split("\n")
    end_idx = None

    # Find closing '---'
    for i in range(1, len(lines)):
        if lines[i].startswith("---"):
            end_idx = i
            break

    if end_idx is None:
        return None, None

    yaml_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:]).strip()

    # Simple key: value YAML parser (no dependencies)
    yaml_dict = {}
    current_list_key = None
    current_list_items = []

    for line in yaml_text.split("\n"):
        # Skip empty lines and comments
        if not line.strip() or line.strip().startswith("#"):
            # Flush current list if switching key
            if current_list_key and current_list_items:
                yaml_dict[current_list_key] = current_list_items
                current_list_key = None
                current_list_items = []
            continue

        # Handle list item (indented with "- ")
        if line.startswith("  - "):
            if current_list_key:
                item = line[4:].strip()
                current_list_items.append(item)
            continue

        # Handle new key: value (starts at column 0)
        if ":" not in line or line.startswith(" "):
            continue

        # Flush previous list before processing new key
        if current_list_key and current_list_items:
            yaml_dict[current_list_key] = current_list_items
            current_list_key = None
            current_list_items = []

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        # Handle list values [item1, item2, ...]
        if value.startswith("[") and value.endswith("]"):
            items = [item.strip() for item in value[1:-1].split(",")]
            yaml_dict[key] = items
        elif value == "":
            # Empty value might be start of multiline list
            current_list_key = key
            current_list_items = []
        elif value.startswith("["):
            # Start of multiline list (shouldn't happen, but handle it)
            current_list_key = key
            current_list_items = []
        else:
            # Regular key: value pair
            yaml_dict[key] = value

    # Flush any remaining list
    if current_list_key and current_list_items:
        yaml_dict[current_list_key] = current_list_items

    return yaml_dict, body


def validate_card(
    card: Card,
    reason_registry: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> List[str]:
    """Validate a Card object for required fields and constraints.

    Args:
        card: Card object to validate.
        reason_registry: Optional reason code registry for validation.
        strict: If True, raise on validation errors; else return error list.

    Returns:
        List of error messages (empty if valid).
    """
    errors = []

    # Required fields
    if not card.site or not card.site.strip():
        errors.append(f"{card.path}: Missing or empty 'site'")

    if not card.reason_code or not card.reason_code.strip():
        errors.append(f"{card.path}: Missing or empty 'reason_code'")
    elif reason_registry:
        # Optionally validate reason code is registered
        if card.reason_code not in reason_registry:
            errors.append(
                f"{card.path}: reason_code '{card.reason_code}' not in registry"
            )

    if not card.title:
        errors.append(f"{card.path}: No title found in body (first heading)")

    if not card.actions_allowed:
        errors.append(f"{card.path}: Missing 'actions_allowed'")

    # Evidence keys must be namespaced
    if card.evidence_required:
        bad_keys = [k for k in card.evidence_required if "." not in k]
        if bad_keys:
            errors.append(
                f"{card.path}: Evidence keys not namespaced: {bad_keys}"
            )

    if strict and errors:
        raise ValueError("\n".join(errors))

    return errors


def parse_card_file(
    filepath: Path,
    repo_root: Path = Path("."),
    reason_registry: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Optional[Card]:
    """Parse a single markdown card file into a Card object.

    Args:
        filepath: Path to .md file.
        repo_root: Root of repository for relative path calculation.
        reason_registry: Optional reason code registry for validation.
        strict: If True, raise on parse errors; else return None.

    Returns:
        Card object or None if parsing fails.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read {filepath}: {e}")
        if strict:
            raise
        return None

    frontmatter_dict, body = _parse_yaml_frontmatter(content)
    if frontmatter_dict is None:
        logger.warning(f"No YAML frontmatter found in {filepath}")
        if strict:
            raise ValueError(f"Missing frontmatter in {filepath}")
        return None

    # Extract fields with safe defaults
    site = str(frontmatter_dict.get("site", "unknown")).strip()
    reason_code = str(frontmatter_dict.get("reason_code", "unknown_reason_code")).strip()
    scope = frontmatter_dict.get("scope")
    if scope:
        scope = str(scope).strip()

    locales = _normalize_list_field(frontmatter_dict.get("locale", []))
    page_kinds = _normalize_list_field(frontmatter_dict.get("page_kind", []))
    actions_allowed = _normalize_list_field(frontmatter_dict.get("actions_allowed", []))
    evidence_required = _normalize_list_field(frontmatter_dict.get("evidence_required", []))
    kb_links = _normalize_list_field(frontmatter_dict.get("kb_links", []))
    code_refs = _normalize_list_field(frontmatter_dict.get("code_refs", []))

    title = _extract_title_from_body(body or "")

    # Calculate relative path
    try:
        rel_path = str(filepath.relative_to(repo_root))
    except ValueError:
        rel_path = str(filepath)

    card = Card(
        path=rel_path,
        site=site,
        reason_code=reason_code,
        locales=locales,
        page_kinds=page_kinds,
        scope=scope,
        actions_allowed=actions_allowed,
        evidence_required=evidence_required,
        kb_links=kb_links,
        code_refs=code_refs,
        title=title,
        body_md=body or "",
        frontmatter=frontmatter_dict,
    )

    # Validate
    errors = validate_card(card, reason_registry=reason_registry, strict=False)
    if errors:
        logger.debug(f"Card validation warnings for {rel_path}:\n" + "\n".join(errors))

    return card


def load_kb_cards(
    root_dir: str = "docs/kb/40_cards/cards",
    repo_root: Optional[Path] = None,
    reason_registry: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> List[Card]:
    """Load all KB cards from a directory.

    Recursively scans root_dir for *.md files and parses each as a Card.
    Returns deterministically sorted list (by site, reason_code, path).
    Skips parsing errors unless strict=True.

    Args:
        root_dir: Directory containing cards (e.g., 'docs/kb/40_cards/cards').
                  Defaults to docs/kb/40_cards/cards relative to repo root.
        repo_root: Repository root for relative path calculation. Defaults to Path.cwd().
        reason_registry: Optional reason code registry for validation.
        strict: If True, raise on parse errors; else skip with warnings.

    Returns:
        List of parsed Card objects, sorted deterministically.
    """
    if repo_root is None:
        repo_root = Path.cwd()
    else:
        repo_root = Path(repo_root)

    cards_path = repo_root / root_dir
    if not cards_path.exists():
        logger.debug(f"Cards directory does not exist: {cards_path}")
        return []

    cards = []
    for md_file in sorted(cards_path.glob("**/*.md")):
        # Skip template and index files (both old and new names for backward compat)
        if md_file.name in {"template.md", "cards_index.md", "authoring_rules.md", "precommit_guide.md",
                            "CARDS_TEMPLATE.md", "CARDS_INDEX.md", "CARDS_AUTHORING_PROMPT.md", "CARDS_PRECOMMIT_GUIDE.md"}:
            continue

        card = parse_card_file(
            md_file,
            repo_root=repo_root,
            reason_registry=reason_registry,
            strict=strict,
        )
        if card:
            cards.append(card)

    # Sort deterministically: (site, reason_code, path)
    cards.sort(key=lambda c: (c.site, c.reason_code, c.path))
    return cards


def filter_cards(
    cards: List[Card],
    site: Optional[str] = None,
    reason_code: Optional[str] = None,
    locale: Optional[str] = None,
    page_kind: Optional[str] = None,
    scope: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Card]:
    """Filter cards by multiple criteria.

    Matching rules:
    - If filter is None: no constraint on that field
    - For locale/page_kind:
      - If card.locales/page_kinds is empty: wildcard (matches any filter)
      - Else: must contain the requested value (exact match in list)
    - For site:
      - Exact match required (unless card.site is "unknown" and filter is None)
    - For reason_code:
      - Exact match required
    - For scope:
      - Exact match required (if filter provided and card has scope)

    Results are deterministically ordered (stable sort by path) and limited.

    Args:
        cards: List of cards to filter.
        site: Filter by site (e.g., 'google_flights').
        reason_code: Filter by reason_code.
        locale: Filter by locale (e.g., 'ja-JP').
        page_kind: Filter by page_kind.
        scope: Filter by scope.
        limit: Maximum results to return (None = unlimited).

    Returns:
        Filtered list, limited and deterministically ordered.
    """
    results = []

    for card in cards:
        # Site filter
        if site is not None and card.site != site:
            # Allow wildcard cards only if no site filter
            if card.site not in {"unknown", "any"}:
                continue

        # Reason code filter
        if reason_code is not None and card.reason_code != reason_code:
            continue

        # Locale filter
        if locale is not None:
            if card.locales and locale not in card.locales:
                continue

        # Page kind filter
        if page_kind is not None:
            if card.page_kinds and page_kind not in card.page_kinds:
                continue

        # Scope filter
        if scope is not None and card.scope and card.scope != scope:
            continue

        results.append(card)

    # Stable sort by path for determinism
    results.sort(key=lambda c: c.path)

    # Apply limit
    if limit is not None:
        results = results[:limit]

    return results


def check_cards(
    root_dir: str = "docs/kb/cards",
    repo_root: Optional[Path] = None,
    strict: bool = False,
) -> tuple[int, Dict[str, Any]]:
    """Check KB Cards for consistency and validity (smoke test).

    Validates:
    - Required fields (site, reason_code, title)
    - Namespaced evidence keys
    - KB links point to existing files/anchors
    - No duplicate IDs or (site, reason_code, title) tuples

    Args:
        root_dir: Directory containing cards.
        repo_root: Repository root for relative path calculation.
        strict: If True, return exit code 1 on any issues. Else return 0 with warnings.

    Returns:
        (exit_code, results_dict) where exit_code is 0 or 1, results_dict contains:
        - total_cards: int
        - by_site: Dict[str, int]
        - by_reason: Dict[str, int]
        - invalid_cards: List[str] (error messages)
        - bad_kb_links: List[str] (error messages)
        - duplicate_ids: List[str] (error messages)
    """
    if repo_root is None:
        repo_root = Path.cwd()
    else:
        repo_root = Path(repo_root)

    cards = load_kb_cards(
        root_dir=root_dir,
        repo_root=repo_root,
        strict=False,
    )

    results = {
        "total_cards": len(cards),
        "by_site": {},
        "by_reason": {},
        "invalid_cards": [],
        "bad_kb_links": [],
        "duplicate_ids": [],
    }

    # Count by site and reason
    for card in cards:
        results["by_site"][card.site] = results["by_site"].get(card.site, 0) + 1
        results["by_reason"][card.reason_code] = results["by_reason"].get(
            card.reason_code, 0
        ) + 1

    # Validate each card
    for card in cards:
        errors = validate_card(card, strict=False)
        if errors:
            results["invalid_cards"].extend(errors)

        # Check KB links
        for link in card.kb_links:
            if not link:
                continue
            # Parse link: could be "docs/kb/20_decision_system/triage_runbook.md" or "docs/kb/20_decision_system/triage_runbook.md#section"
            if "#" in link:
                file_path, anchor = link.split("#", 1)
            else:
                file_path, anchor = link, None

            full_path = repo_root / file_path
            if not full_path.exists():
                results["bad_kb_links"].append(
                    f"{card.path}: kb_link file not found: {file_path}"
                )
            elif anchor:
                # Basic anchor check: look for markdown heading matching anchor
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    # Convert anchor to markdown format (e.g. "my-section" -> "# My Section")
                    # This is a simple check; markdown heading anchors vary
                    if f"#{anchor}" not in content.lower():
                        results["bad_kb_links"].append(
                            f"{card.path}: anchor not found in {file_path}: #{anchor}"
                        )
                except Exception as e:
                    results["bad_kb_links"].append(
                        f"{card.path}: error reading {file_path}: {e}"
                    )

    # Check for duplicate IDs and (site, reason_code, title) tuples
    seen_ids = {}
    seen_tuples = {}
    for card in cards:
        # Check ID if present in frontmatter
        if card.frontmatter and "id" in card.frontmatter:
            card_id = card.frontmatter["id"]
            if card_id in seen_ids:
                results["duplicate_ids"].append(
                    f"Duplicate id '{card_id}': {seen_ids[card_id]} and {card.path}"
                )
            else:
                seen_ids[card_id] = card.path

        # Check (site, reason_code, title) uniqueness
        key = (card.site, card.reason_code, card.title)
        if key in seen_tuples:
            results["duplicate_ids"].append(
                f"Duplicate (site, reason_code, title): {seen_tuples[key]} and {card.path}"
            )
        else:
            seen_tuples[key] = card.path

    # Determine exit code
    has_issues = (
        bool(results["invalid_cards"])
        or bool(results["bad_kb_links"])
        or bool(results["duplicate_ids"])
    )

    exit_code = 1 if (strict and has_issues) else 0
    return exit_code, results


def print_check_report(results: Dict[str, Any], strict: bool = False) -> None:
    """Print human-readable check report.

    Args:
        results: Dictionary from check_cards().
        strict: Whether running in strict mode.
    """
    print(f"\nKB Cards Smoke Test Report")
    print("=" * 60)
    print(f"\nTotal cards: {results['total_cards']}")

    if results["by_site"]:
        print(f"\nCards by site:")
        for site in sorted(results["by_site"].keys()):
            print(f"  {site}: {results['by_site'][site]}")

    if results["by_reason"]:
        print(f"\nCards by reason code (top 10):")
        for reason in sorted(results["by_reason"].keys())[:10]:
            print(f"  {reason}: {results['by_reason'][reason]}")

    if results["invalid_cards"]:
        status = "ERROR" if strict else "WARN"
        print(f"\n[{status}] Invalid cards ({len(results['invalid_cards'])}):")
        for msg in results["invalid_cards"][:10]:
            print(f"  - {msg}")
        if len(results["invalid_cards"]) > 10:
            print(
                f"  ... and {len(results['invalid_cards']) - 10} more"
            )

    if results["bad_kb_links"]:
        status = "ERROR" if strict else "WARN"
        print(f"\n[{status}] Bad KB links ({len(results['bad_kb_links'])}):")
        for msg in results["bad_kb_links"][:10]:
            print(f"  - {msg}")
        if len(results["bad_kb_links"]) > 10:
            print(f"  ... and {len(results['bad_kb_links']) - 10} more")

    if results["duplicate_ids"]:
        status = "ERROR" if strict else "WARN"
        print(f"\n[{status}] Duplicates ({len(results['duplicate_ids'])}):")
        for msg in results["duplicate_ids"][:10]:
            print(f"  - {msg}")
        if len(results["duplicate_ids"]) > 10:
            print(f"  ... and {len(results['duplicate_ids']) - 10} more")

    if (
        not results["invalid_cards"]
        and not results["bad_kb_links"]
        and not results["duplicate_ids"]
    ):
        print("\n✓ All checks passed!")

    print()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="KB Cards smoke test and contract check"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run smoke test and contract checks",
    )
    parser.add_argument(
        "--cards-root",
        default="docs/kb/cards",
        help="Root directory for KB cards (default: docs/kb/cards)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any issues found",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    if args.check:
        exit_code, results = check_cards(
            root_dir=args.cards_root,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            strict=args.strict,
        )
        print_check_report(results, strict=args.strict)
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(0)
