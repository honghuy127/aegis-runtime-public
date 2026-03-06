#!/usr/bin/env python3
"""Generate deterministic fixture triage metadata for HTML fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_HINT = _SCRIPT_DIR.parent
if str(_REPO_ROOT_HINT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_HINT))

from utils.fixture_triage import (
    build_fixture_triage_metadata,
    load_json_if_exists,
    validate_fixture_triage_metadata,
)
from utils.kb_ref_resolver import build_kb_refs, validate_kb_refs


SUPPORTED_SITES = ("google_flights", "skyscanner")


def _parse_bool_int(text: str) -> bool:
    val = str(text).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected 0/1 bool, got: {text}")


def _repo_root() -> Path:
    current = Path.cwd()
    while current != current.parent:
        if (current / "tests" / "fixtures").exists():
            return current
        current = current.parent
    return Path.cwd()


def _sites_from_arg(site_arg: str) -> List[str]:
    return list(SUPPORTED_SITES) if site_arg == "all" else [site_arg]


def discover_fixture_html(fixtures_dir: Path, site: str) -> List[Path]:
    site_dir = fixtures_dir / site
    if not site_dir.exists():
        return []
    return sorted(
        [p for p in site_dir.glob("*.html") if p.is_file()],
        key=lambda p: p.name,
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def process_fixture(
    fixture_path: Path,
    *,
    site: str,
    repo_root: Path,
    write: bool,
    overwrite: bool,
    autolink: bool,
    max_refs: int,
) -> Dict[str, object]:
    html = _read_text(fixture_path)
    capture_meta = load_json_if_exists(fixture_path.with_suffix(".meta.json"))
    triage_path = fixture_path.with_suffix(".triage.json")
    existing_triage = load_json_if_exists(triage_path)

    try:
        rel_fixture = fixture_path.resolve().relative_to(repo_root.resolve())
        rel_fixture_str = rel_fixture.as_posix()
    except ValueError:
        rel_fixture = fixture_path.resolve()
        rel_fixture_str = rel_fixture.as_posix()
    triage_data = build_fixture_triage_metadata(
        site=site,
        fixture_path=rel_fixture,
        html_text=html,
        existing_capture_meta=capture_meta,
        existing_triage_meta=existing_triage,
    )
    existing_kb_refs = triage_data.get("kb_refs")
    should_autolink = bool(autolink) or not isinstance(existing_kb_refs, list) or len(existing_kb_refs) == 0
    if should_autolink:
        triage_data["kb_refs"] = build_kb_refs(
            site=str(triage_data.get("site") or site),
            locale_hint=str(triage_data.get("locale_hint") or "unknown"),
            page_kind=str(triage_data.get("page_kind") or "unknown"),
            signals=triage_data.get("signals") if isinstance(triage_data.get("signals"), dict) else {},
            expected=triage_data.get("expected") if isinstance(triage_data.get("expected"), dict) else {},
            max_refs=max_refs,
        )

    errors = validate_fixture_triage_metadata(triage_data, repo_root=repo_root)
    kb_ref_warnings = validate_kb_refs(triage_data.get("kb_refs") if isinstance(triage_data.get("kb_refs"), list) else [])
    errors.extend(kb_ref_warnings)

    written = False
    if write and (overwrite or not triage_path.exists()) and not errors:
        _write_json(triage_path, triage_data)
        written = True

    return {
        "site": site,
        "name": fixture_path.stem,
        "fixture_path": rel_fixture_str,
        "page_kind": triage_data.get("page_kind"),
        "locale_hint": triage_data.get("locale_hint"),
        "expected_extraction_status": (
            (triage_data.get("expected") or {}).get("extraction") or {}
        ).get("status"),
        "kb_refs_count": len(triage_data.get("kb_refs", [])) if isinstance(triage_data.get("kb_refs"), list) else 0,
        "warnings": errors,
        "warnings_count": len(errors),
        "written": written,
        "triage_path": (
            str(triage_path.resolve().relative_to(repo_root.resolve()))
            if triage_path.resolve().is_relative_to(repo_root.resolve())
            else str(triage_path.resolve())
        ),
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", choices=[*SUPPORTED_SITES, "all"], required=True)
    p.add_argument("--fixtures-dir", default="tests/fixtures")
    p.add_argument("--write", type=_parse_bool_int, default=False)
    p.add_argument("--overwrite", type=_parse_bool_int, default=False)
    p.add_argument("--autolink", type=_parse_bool_int, default=True)
    p.add_argument("--max-refs", type=int, default=2)
    p.add_argument("--strict", type=_parse_bool_int, default=False)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = _repo_root()
    fixtures_dir = (repo_root / args.fixtures_dir).resolve() if not Path(args.fixtures_dir).is_absolute() else Path(args.fixtures_dir)
    any_warnings = False
    cap_limit = 3 if bool(args.strict) else 2
    effective_max_refs = max(0, min(int(args.max_refs), cap_limit))

    for site in _sites_from_arg(args.site):
        fixtures = discover_fixture_html(fixtures_dir, site)
        if not fixtures:
            print(f"[WARN] site={site} fixtures=0 message=no_fixtures_found")
            continue

        for fixture_path in fixtures:
            result = process_fixture(
                fixture_path,
                site=site,
                repo_root=repo_root,
                write=bool(args.write),
                overwrite=bool(args.overwrite),
                autolink=bool(args.autolink),
                max_refs=effective_max_refs,
            )
            warnings_count = int(result["warnings_count"])
            if warnings_count:
                any_warnings = True
            print(
                "site={site} name={name} page_kind={page_kind} locale_hint={locale_hint} "
                "extraction_status={status} kb_refs={kb_refs} warnings={warnings} written={written}".format(
                    site=result["site"],
                    name=result["name"],
                    page_kind=result["page_kind"],
                    locale_hint=result["locale_hint"],
                    status=result["expected_extraction_status"],
                    kb_refs=result["kb_refs_count"],
                    warnings=warnings_count,
                    written=int(bool(result["written"])),
                )
            )
            for warning in result["warnings"][:8]:
                print(f"  - {warning}")

    if any_warnings and bool(args.strict):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
