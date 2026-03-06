"""Calendar state snapshot capture for self-healing and KB authoring.

When calendar set_date() fails, captures compact artifacts:
- HTML fragment of dialog/calendar region
- Selector attempts and outcomes
- Month header texts and parse results
- Target date, locale, strategy metadata
- Output to storage/runs/<run_id>/artifacts/calendar_snapshot_<role>_<ts>.json

Constraints:
- Cheap in normal mode: only write on failures or debug thresholds
- No external dependencies beyond stdlib
- Snapshot size < 200 KB (HTML truncation enforced)
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SelectorAttempt:
    """Single selector attempt during calendar operation."""
    step: str  # "open_dialog" | "read_header" | "click_next" | "click_prev" | "click_day" | "verify"
    selector: str
    action: str  # "find" | "click" | "read" | "wait"
    outcome: str  # "ok" | "fail" | "timeout" | "not_visible" | "not_enabled" | "overlay_detected"
    visible: bool = False
    enabled: bool = False
    overlay_detected: bool = False
    timeout_ms: Optional[int] = None
    remaining_ms: Optional[int] = None


@dataclass
class MonthParseResult:
    """Result of parsing calendar month header text."""
    ok: bool
    year: Optional[int] = None
    month: Optional[int] = None
    source_text: Optional[str] = None
    parsing_method: Optional[str] = None  # "parse_month_year" | "regex" | etc.


@dataclass
class CalendarSnapshot:
    """Compact snapshot of calendar state at failure moment."""
    # Metadata
    run_id: str
    site: str
    role: str  # "depart" | "return"
    locale: str  # e.g., "ja-JP"
    target_date: str  # "YYYY-MM-DD"
    strategy_used: str  # "direct_input" | "pick_by_aria_label" | "nav_scan_pick"

    # Failure context
    failure_reason_code: str  # "calendar_not_open" | "month_nav_exhausted" | "date_not_committed"
    failure_stage: str  # "open" | "detect_month" | "navigate" | "click_day" | "verify"

    # Calendar state observed
    month_header_texts: List[str] = field(default_factory=list)  # Candidate month headers seen
    month_parse: Optional[MonthParseResult] = None

    # Attempt log
    selector_attempts: List[SelectorAttempt] = field(default_factory=list)

    # HTML artifact (truncated)
    html_fragment: str = ""  # Truncated HTML; may have "...TRUNCATED..." marker
    html_source: str = "full_page"  # "full_page" | "dialog_extracted" | "fallback_head"

    # Pointers to related artifacts
    stdout_log_path: Optional[str] = None
    run_dir_path: Optional[str] = None
    last_html_path: Optional[str] = None

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def truncate_html(html: str, max_chars: int = 120000) -> tuple[str, bool]:
    """Truncate HTML safely, preserving structure when possible.

    Args:
        html: HTML string to truncate
        max_chars: Maximum character limit

    Returns:
        (truncated_html, was_truncated)
    """
    if len(html) <= max_chars:
        return (html, False)

    # Try to truncate at a safe boundary (before a closing tag)
    truncated = html[:max_chars]

    # Find last safe break point (before a closing tag)
    last_tag_pos = truncated.rfind("</")
    if last_tag_pos > max_chars * 0.8:  # If not too early, use it
        truncated = html[:last_tag_pos + 4]  # Include the </tag>

    truncated += "\n...TRUNCATED..."
    return (truncated, True)


def extract_dialog_fragment(full_html: str, max_chars: int = 60000) -> tuple[str, str]:
    """Extract calendar dialog fragment from full HTML.

    Tries to locate:
    1. role="dialog" container
    2. [role="grid"] or calendar grid patterns
    3. aria-label containing month/date patterns

    Args:
        full_html: Full page HTML
        max_chars: Max chars to extract

    Returns:
        (html_fragment, source_type)
        where source_type is "dialog" | "grid" | "head" (fallback)
    """
    if not full_html:
        return ("", "empty")

    # Try 1: Find role="dialog"
    dialog_match = re.search(r'<[^>]*\s+role=["\'"]dialog["\'"]\s*[^>]*>.*?</[^>]+>',
                            full_html, re.IGNORECASE | re.DOTALL)
    if dialog_match and len(dialog_match.group()) < max_chars:
        return (dialog_match.group(), "dialog")

    # Try 2: Find [role="grid"] (calendar grid)
    grid_match = re.search(r'<[^>]*\s+role=["\'"]grid["\'"]\s*[^>]*>.*?</[^>]+>',
                          full_html, re.IGNORECASE | re.DOTALL)
    if grid_match and len(grid_match.group()) < max_chars:
        return (grid_match.group(), "grid")

    # Try 3: Look for aria-label with month/day patterns
    month_patterns = [
        r'<[^>]*aria-label=["\']([^"\']*(?:月|January|February|March|April|May|June|July|August|September|October|November|December)[^"\']*)["\'][^>]*>.*?</[^>]+>',
        r'<div[^>]*class=["\']([^"\']*calendar[^"\']*)["\'][^>]*>.*?</div>',
    ]

    for pattern in month_patterns:
        match = re.search(pattern, full_html, re.IGNORECASE | re.DOTALL)
        if match:
            start = max(0, match.start() - 500)
            end = min(len(full_html), match.end() + 2000)
            fragment = full_html[start:end]
            if len(fragment) < max_chars:
                return (fragment, "month_label")

    # Fallback: Take head + first section
    head_match = re.search(r'<head>.*?</head>', full_html, re.DOTALL)
    if head_match:
        head = head_match.group()
        body_start = full_html.find("<body")
        if body_start > 0:
            body_head = full_html[body_start:body_start + max_chars - len(head)]
            return (head + "\n" + body_head + "\n...FALLBACK_TRUNCATED...", "head_fallback")

    # Ultimate fallback: just head of HTML
    return (full_html[:max_chars] + "\n...FALLBACK_HEAD...", "head_fallback")


def write_calendar_snapshot(
    snapshot: CalendarSnapshot,
    run_dir: Path,
    include_md: bool = False
) -> tuple[Path, Optional[Path]]:
    """Write calendar snapshot to JSON (and optionally MD).

    Args:
        snapshot: CalendarSnapshot dataclass instance
        run_dir: Path to storage/runs/<run_id>/
        include_md: If True, also write .md for human reading

    Returns:
        (json_path, md_path_or_none)
    """
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    ts = snapshot.created_at.replace(":", "-").replace(".", "-")
    base_name = f"calendar_snapshot_{snapshot.role}_{ts}"
    json_path = artifacts_dir / f"{base_name}.json"

    # Prepare snapshot dict
    snapshot_dict = asdict(snapshot)

    # Normalize selector_attempts to dicts
    snapshot_dict["selector_attempts"] = [asdict(a) for a in snapshot.selector_attempts]

    # Normalize month_parse
    if snapshot.month_parse:
        snapshot_dict["month_parse"] = asdict(snapshot.month_parse)

    # Write JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot_dict, f, indent=2, default=str)

    md_path = None
    if include_md:
        md_path = artifacts_dir / f"{base_name}.md"
        md_content = _build_markdown(snapshot, json_path)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    return (json_path, md_path)


def _build_markdown(snapshot: CalendarSnapshot, json_path: Path) -> str:
    """Generate human-readable markdown from snapshot.

    Includes KB card draft section for authoring.
    """
    lines = []

    # Title
    lines.append(f"# Calendar Snapshot: {snapshot.role} ({snapshot.site})")
    lines.append(f"__{snapshot.created_at}__")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(f"- **Site**: {snapshot.site}")
    lines.append(f"- **Run ID**: {snapshot.run_id}")
    lines.append(f"- **Failure**: {snapshot.failure_reason_code} (stage: {snapshot.failure_stage})")
    lines.append(f"- **Target Date**: {snapshot.target_date} (locale: {snapshot.locale})")
    lines.append(f"- **Strategy Used**: {snapshot.strategy_used}")
    lines.append("")

    # What we saw
    lines.append("## What We Saw")
    if snapshot.month_header_texts:
        lines.append("### Month Headers Detected")
        for text in snapshot.month_header_texts[:5]:  # Show first 5
            lines.append(f"- `{text}`")
        lines.append("")

    if snapshot.month_parse:
        lines.append("### Month Parsing Result")
        p = snapshot.month_parse
        lines.append(f"- **Parsed OK**: {p.ok}")
        if p.ok:
            lines.append(f"- **Date**: {p.year}-{str(p.month or 0).zfill(2)}")
        lines.append(f"- **Method**: {p.parsing_method}")
        lines.append(f"- **Source**: {p.source_text}")
        lines.append("")

    # Selectors tried
    if snapshot.selector_attempts:
        lines.append("### Selectors Tried")
        lines.append("| Step | Selector | Action | Outcome | Visible | Enabled |")
        lines.append("|------|----------|--------|---------|---------|---------|")
        for attempt in snapshot.selector_attempts[:10]:  # Show first 10
            selector_short = attempt.selector[:40] + "..." if len(attempt.selector) > 40 else attempt.selector
            lines.append(
                f"| {attempt.step} | `{selector_short}` | {attempt.action} | "
                f"{attempt.outcome} | {attempt.visible} | {attempt.enabled} |"
            )
        lines.append("")

    # Artifacts
    lines.append("## Related Artifacts")
    if snapshot.stdout_log_path:
        lines.append(f"- **Stdout Log**: {snapshot.stdout_log_path}")
    if snapshot.last_html_path:
        lines.append(f"- **Last HTML**: {snapshot.last_html_path}")
    lines.append(f"- **Snapshot JSON**: {json_path}")
    lines.append("")

    # KB Card Draft
    lines.append("## KB Card Draft (for authoring)")
    lines.append("")
    lines.append("```yaml")
    lines.append("file: kb/TRIAGE_RUNBOOK.md")
    lines.append("section: <reason_code>")
    lines.append("")
    lines.append("reason_code: " + snapshot.failure_reason_code)
    lines.append("page_kind: calendar")
    lines.append(f"site: {snapshot.site}")
    lines.append("locale: " + snapshot.locale)
    lines.append("")
    lines.append("symptoms:")
    lines.append(f"  - Calendar fails at: {snapshot.failure_stage}")
    if snapshot.month_header_texts:
        lines.append(f"  - Month header texts seen: {', '.join(snapshot.month_header_texts[:3])}")
    lines.append("")
    lines.append("best_patch:")
    lines.append("  # Patch recommendation based on what failed:")
    if snapshot.failure_stage == "open":
        lines.append("  - [ ] Review opener selectors in calendar_drive.py")
        lines.append("  - [ ] Check if dialog role changed or is hidden")
    elif snapshot.failure_stage == "detect_month":
        lines.append("  - [ ] Month header text changed; update parse patterns")
        lines.append("  - [ ] Consider adding to parse_month_year() utility")
    elif snapshot.failure_stage == "navigate":
        lines.append("  - [ ] Navigation buttons (prev/next) may have new selectors")
    elif snapshot.failure_stage == "click_day":
        lines.append("  - [ ] Day cell selector may have changed (aria-label format)")
    elif snapshot.failure_stage == "verify":
        lines.append("  - [ ] Input field selector may have changed")
        lines.append("  - [ ] Date format in field may be non-standard")
    lines.append("")
    lines.append("selectors:")
    if snapshot.selector_attempts:
        lines.append("  tried:")
        for attempt in snapshot.selector_attempts[:5]:
            lines.append(f"    - {attempt.selector}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)
