#!/usr/bin/env python3
"""Capture and sanitize deterministic HTML fixtures from local run artifacts.

Supports:
- skyscanner
- google_flights
- all (best effort per site)

This tool is intentionally HTML-only and stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


SUPPORTED_SITES = ("skyscanner", "google_flights")
DEFAULT_OUT_DIR = Path("tests/fixtures")
DEFAULT_MAX_BYTES = 250000
ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "storage" / "runs"
DEBUG_HTML_DIR = ROOT / "storage" / "debug_html"
SCENARIO_LAST_ERROR = ROOT / "storage" / "scenario_last_error.json"


@dataclass
class Candidate:
    site: str
    source_kind: str
    path: Path
    run_id: str
    score: int
    mtime_ns: int


@dataclass
class SanitizeResult:
    html: str
    stats: Dict[str, int]
    bytes_before: int
    bytes_after: int


def _parse_bool_int(text: str) -> bool:
    val = str(text).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected 0/1 bool, got: {text}")


def _site_tokens(site: str) -> List[str]:
    if site == "google_flights":
        return ["google_flights", "google", "flights"]
    if site == "skyscanner":
        return ["skyscanner"]
    return [site]


def _looks_like_site_file(site: str, path: Path) -> bool:
    name = path.name.lower()
    tokens = _site_tokens(site)
    if site == "google_flights":
        return ("google" in name and "flight" in name) or "google_flights" in name
    return any(token in name for token in tokens)


def _looks_like_other_supported_site(site: str, path: Path) -> bool:
    for other in SUPPORTED_SITES:
        if other == site:
            continue
        if _looks_like_site_file(other, path):
            return True
    return False


def _artifact_candidate_score(site: str, path: Path) -> int:
    name = path.name.lower()
    score = 0
    if _looks_like_site_file(site, path):
        score += 100
    if "last" in name:
        score += 40
    if "scenario_" in name:
        score += 20
    if "initial" not in name:
        score += 10
    return score


def _safe_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except Exception:
        return 0


def _iter_run_dirs() -> Iterable[Path]:
    if not RUNS_DIR.exists():
        return []
    dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    dirs.sort(key=_safe_mtime_ns, reverse=True)
    return dirs


def _run_artifact_candidates_for_site(run_id: str, site: str) -> List[Candidate]:
    run_dir = RUNS_DIR / run_id / "artifacts"
    if not run_dir.exists():
        return []
    out: List[Candidate] = []
    for path in run_dir.glob("*.html"):
        if _looks_like_other_supported_site(site, path):
            continue
        out.append(
            Candidate(
                site=site,
                source_kind="artifact",
                path=path,
                run_id=run_id,
                score=_artifact_candidate_score(site, path),
                mtime_ns=_safe_mtime_ns(path),
            )
        )
    return sorted(out, key=lambda c: (c.score, c.mtime_ns), reverse=True)


def _debug_html_candidates_for_site(site: str) -> List[Candidate]:
    if not DEBUG_HTML_DIR.exists():
        return []
    patterns = [
        f"scenario_{site}_last.html",
        f"last_{site}_*.html",
        f"*{site}*.html",
    ]
    if site == "google_flights":
        patterns.extend(["*google*flight*.html", "*google_flights*.html"])

    seen: set[Path] = set()
    out: List[Candidate] = []
    for pattern in patterns:
        for path in DEBUG_HTML_DIR.glob(pattern):
            if not path.is_file() or path in seen:
                continue
            if _looks_like_other_supported_site(site, path):
                continue
            seen.add(path)
            score = 0
            name = path.name.lower()
            if f"scenario_{site}_last" in name:
                score += 120
            if "last" in name:
                score += 50
            if _looks_like_site_file(site, path):
                score += 80
            out.append(
                Candidate(
                    site=site,
                    source_kind="debug_html",
                    path=path,
                    run_id="",
                    score=score,
                    mtime_ns=_safe_mtime_ns(path),
                )
            )
    return sorted(out, key=lambda c: (c.score, c.mtime_ns), reverse=True)


def _extract_html_paths_from_json(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_extract_html_paths_from_json(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_extract_html_paths_from_json(v))
    elif isinstance(value, str):
        s = value.strip()
        if s.lower().endswith(".html"):
            out.append(s)
    return out


def _scenario_last_error_candidates(site: str) -> List[Candidate]:
    if not SCENARIO_LAST_ERROR.exists():
        return []
    try:
        payload = json.loads(SCENARIO_LAST_ERROR.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_paths = _extract_html_paths_from_json(payload)
    out: List[Candidate] = []
    for raw in raw_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = (ROOT / raw).resolve()
        if not p.exists() or not p.is_file():
            continue
        if _looks_like_other_supported_site(site, p):
            continue
        name = p.name.lower()
        score = 20
        if _looks_like_site_file(site, p):
            score += 80
        if "last" in name:
            score += 30
        run_id = ""
        parts = list(p.parts)
        if "runs" in parts:
            try:
                idx = parts.index("runs")
                run_id = parts[idx + 1]
            except Exception:
                run_id = ""
        out.append(
            Candidate(
                site=site,
                source_kind="scenario_last_error",
                path=p,
                run_id=run_id,
                score=score,
                mtime_ns=_safe_mtime_ns(p),
            )
        )
    return sorted(out, key=lambda c: (c.score, c.mtime_ns), reverse=True)


def _latest_run_id_for_site(site: str) -> str:
    for run_dir in _iter_run_dirs():
        cands = _run_artifact_candidates_for_site(run_dir.name, site)
        if any(c.score >= 100 for c in cands):
            return run_dir.name
    run_dirs = list(_iter_run_dirs())
    return run_dirs[0].name if run_dirs else ""


def discover_source(
    *,
    site: str,
    run_id: Optional[str],
    source: str,
) -> Optional[Candidate]:
    chosen_run_id = (run_id or "").strip()
    if not chosen_run_id and source in {"auto", "artifact", "last_html"}:
        chosen_run_id = _latest_run_id_for_site(site)

    if source == "artifact":
        if chosen_run_id:
            items = _run_artifact_candidates_for_site(chosen_run_id, site)
            return items[0] if items else None
        return None
    elif source == "debug_html":
        items = _debug_html_candidates_for_site(site)
        return items[0] if items else None
    elif source == "last_html":
        if chosen_run_id:
            lastish = [
                c for c in _run_artifact_candidates_for_site(chosen_run_id, site)
                if "last" in c.path.name.lower()
            ]
            if lastish:
                return lastish[0]
        items = _debug_html_candidates_for_site(site)
        return items[0] if items else None
    elif source == "auto":
        if chosen_run_id:
            items = _run_artifact_candidates_for_site(chosen_run_id, site)
            if items:
                return items[0]
        items = _debug_html_candidates_for_site(site)
        if items:
            return items[0]
        items = _scenario_last_error_candidates(site)
        if items:
            return items[0]
        return None
    else:
        raise ValueError(f"Unsupported source: {source}")
    return None


def _replace_and_count(pattern: re.Pattern[str], repl: str, text: str) -> Tuple[str, int]:
    out, count = pattern.subn(repl, text)
    return out, count


def _sanitize_query_params_in_urls(text: str) -> Tuple[str, int]:
    redactions = 0

    def _clean_url(url_text: str) -> str:
        nonlocal redactions
        try:
            parsed = urlparse(url_text)
            if not parsed.scheme and not parsed.netloc and "?" not in url_text:
                return url_text
            kept = []
            changed = False
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                k = key.lower()
                if k.startswith("utm_") or k in {"gclid", "fbclid"}:
                    changed = True
                    redactions += 1
                    continue
                kept.append((key, value))
            if not changed:
                return url_text
            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(kept, doseq=True),
                    parsed.fragment,
                )
            )
        except Exception:
            return url_text

    url_pattern = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
    return url_pattern.sub(lambda m: _clean_url(m.group(0)), text), redactions


def sanitize_html(html: str, *, max_bytes: int) -> SanitizeResult:
    text = html if isinstance(html, str) else ""
    stats: Dict[str, int] = {}
    bytes_before = len(text.encode("utf-8", errors="ignore"))

    transforms: List[Tuple[str, re.Pattern[str], str]] = [
        (
            "script_blocks_removed",
            re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL),
            "",
        ),
        (
            "inline_event_handlers_removed",
            re.compile(r"\s+on[a-zA-Z0-9_-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE),
            "",
        ),
        (
            "data_uri_blobs_redacted",
            re.compile(r"data:[^\"'\s>]{0,120};base64,[A-Za-z0-9+/=]{80,}", re.IGNORECASE),
            "data:[REDACTED_DATA_URI]",
        ),
        (
            "email_redactions",
            re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
            "[REDACTED_EMAIL]",
        ),
        (
            "token_key_redactions",
            re.compile(
                r"(?i)\b(authorization|bearer|access[_-]?token|id[_-]?token|csrf(?:[_-]?token)?|session(?:id)?)\b"
                r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s<>{}\"]+)"
            ),
            r"\1\2\"[REDACTED]\"",
        ),
        (
            "cookie_redactions",
            re.compile(
                r"(?i)\b(cookie|set-cookie)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\r\n<>{}]+)"
            ),
            r"\1\2\"[REDACTED_COOKIE]\"",
        ),
        (
            "long_token_redactions",
            re.compile(r"\b(?:[A-Fa-f0-9]{32,}|[A-Za-z0-9+/_=-]{40,})\b"),
            "[REDACTED_TOKEN]",
        ),
        (
            "long_numeric_ids_redacted",
            re.compile(r"\b\d{16,}\b"),
            "[ID]",
        ),
    ]

    for key, pattern, repl in transforms:
        text, count = _replace_and_count(pattern, repl, text)
        stats[key] = count

    text, query_redactions = _sanitize_query_params_in_urls(text)
    stats["tracking_query_params_removed"] = query_redactions

    # Remove standalone tracking params outside absolute URLs (e.g., in href snippets).
    text, qp_count = _replace_and_count(
        re.compile(r"([?&])(utm_[A-Za-z0-9_]+|gclid|fbclid)=[^&#\"'\s>]*", re.IGNORECASE),
        r"\1",
        text,
    )
    stats["tracking_query_params_removed"] += qp_count

    # Collapse repeated whitespace conservatively.
    text, ws_count_a = _replace_and_count(re.compile(r"[ \t]{2,}"), " ", text)
    text, ws_count_b = _replace_and_count(re.compile(r"\n{3,}"), "\n\n", text)
    stats["whitespace_collapses"] = ws_count_a + ws_count_b

    text = text.strip()
    text = _enforce_size_budget(text, max_bytes=max_bytes, stats=stats)
    bytes_after = len(text.encode("utf-8", errors="ignore"))
    return SanitizeResult(
        html=text,
        stats=stats,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return (m.group(1).strip() if m else "")


def _extract_main_subtree(html: str) -> str:
    m = re.search(r"<main\b[^>]*>.*?</main\s*>", html, re.IGNORECASE | re.DOTALL)
    return m.group(0).strip() if m else ""


def _utf8_truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def _head_tail_budget(html: str, max_bytes: int) -> str:
    marker = "\n<!-- [TRUNCATED_FOR_FIXTURE_SIZE_BUDGET] -->\n"
    marker_bytes = len(marker.encode("utf-8"))
    if max_bytes <= marker_bytes + 32:
        return _utf8_truncate(html, max_bytes)
    remain = max_bytes - marker_bytes
    head_budget = remain // 2
    tail_budget = remain - head_budget
    raw = html.encode("utf-8", errors="ignore")
    if len(raw) <= max_bytes:
        return html
    head = raw[:head_budget]
    tail = raw[-tail_budget:] if tail_budget > 0 else b""
    head_s = _utf8_truncate(head.decode("utf-8", errors="ignore"), head_budget)
    tail_s = _utf8_truncate(tail.decode("utf-8", errors="ignore"), tail_budget)
    out = f"{head_s}{marker}{tail_s}"
    return _utf8_truncate(out, max_bytes)


def _enforce_size_budget(html: str, *, max_bytes: int, stats: Dict[str, int]) -> str:
    if len(html.encode("utf-8", errors="ignore")) <= max_bytes:
        stats["size_budget_strategy"] = 0
        return html

    title = _extract_title(html)
    main_block = _extract_main_subtree(html)
    if title or main_block:
        compact = (
            "<!doctype html><html><head>"
            + (f"<title>{title}</title>" if title else "")
            + "</head><body>"
            + (main_block or "")
            + "</body></html>"
        )
        if len(compact.encode("utf-8", errors="ignore")) <= max_bytes:
            stats["size_budget_strategy"] = 1
            return compact
        html = compact

    stats["size_budget_strategy"] = 2
    return _head_tail_budget(html, max_bytes)


def _classify_fixture_kind(site: str, html: str) -> str:
    text = (html or "").lower()
    if site == "skyscanner":
        if "skyscanner" in text and ("search-results" in text or "itinerary" in text or "flight" in text):
            if "$" in text or "£" in text or "€" in text or "¥" in text or "price" in text:
                return "flights_results_sample"
    elif site == "google_flights":
        if ("google" in text and "travel/flights" in text) or "best flights" in text:
            if "$" in text or "£" in text or "€" in text or "¥" in text or "price" in text:
                return "flights_results_sample"

    non_flight_tokens = [
        "consent",
        "privacy",
        "cookies",
        "flight + hotel",
        "packages",
        "captcha",
        "access denied",
    ]
    if any(tok in text for tok in non_flight_tokens):
        return "non_flight_scope_sample"
    return "page_sample"


def _next_available_stem(out_dir: Path, stem_base: str, overwrite: bool) -> str:
    if overwrite:
        return stem_base
    for idx in range(1, 1000):
        stem = f"{stem_base}_{idx:02d}"
        if not (out_dir / f"{stem}.html").exists() and not (out_dir / f"{stem}.meta.json").exists():
            return stem
    raise RuntimeError(f"Could not find available fixture name for base={stem_base}")


def _resolve_fixture_stem(
    *,
    site: str,
    sanitized_html: str,
    name_override: Optional[str],
    out_dir: Path,
    overwrite: bool,
) -> str:
    if name_override:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", name_override.strip()).strip("._-")
        if not base:
            base = "page_sample"
    else:
        base = _classify_fixture_kind(site, sanitized_html)
    if re.search(r"_\d{2}$", base):
        return base if overwrite or not (out_dir / f"{base}.html").exists() else _next_available_stem(out_dir, re.sub(r"_\d{2}$", "", base), overwrite=False)
    return _next_available_stem(out_dir, base, overwrite)


def _read_html(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def capture_one(
    *,
    site: str,
    run_id: Optional[str],
    source: str,
    out_dir_root: Path,
    name_override: Optional[str],
    max_bytes: int,
    overwrite: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    candidate = discover_source(site=site, run_id=run_id, source=source)
    if candidate is None:
        return {
            "site": site,
            "status": "not_found",
            "run_id": run_id or "",
            "source_path": "",
            "message": "No HTML source found for requested site/source.",
        }

    raw_html = _read_html(candidate.path)
    sanitized = sanitize_html(raw_html, max_bytes=max_bytes)

    site_out_dir = out_dir_root / site
    stem = _resolve_fixture_stem(
        site=site,
        sanitized_html=sanitized.html,
        name_override=name_override,
        out_dir=site_out_dir,
        overwrite=overwrite,
    )
    html_out = site_out_dir / f"{stem}.html"
    meta_out = site_out_dir / f"{stem}.meta.json"

    meta = {
        "site": site,
        "source_path": str(candidate.path.resolve()),
        "run_id": candidate.run_id or (run_id or ""),
        "captured_at": datetime.now(UTC).isoformat(),
        "bytes_before": sanitized.bytes_before,
        "bytes_after": sanitized.bytes_after,
        "sanitizer_stats": sanitized.stats,
        "notes": "",
    }

    if not dry_run:
        _write_text(html_out, sanitized.html)
        _write_json(meta_out, meta)

    redactions_total = sum(int(v) for k, v in sanitized.stats.items() if k != "size_budget_strategy")
    return {
        "site": site,
        "status": "ok",
        "run_id": candidate.run_id or (run_id or ""),
        "source_kind": candidate.source_kind,
        "source_path": str(candidate.path.resolve()),
        "output_path": str(html_out),
        "meta_path": str(meta_out),
        "bytes_before": sanitized.bytes_before,
        "bytes_after": sanitized.bytes_after,
        "redactions": redactions_total,
        "dry_run": dry_run,
    }


def _choose_sites(site_arg: str) -> List[str]:
    if site_arg == "all":
        return list(SUPPORTED_SITES)
    return [site_arg]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", choices=[*SUPPORTED_SITES, "all"], required=True)
    p.add_argument("--run-id", default="", help="Optional run_id under storage/runs/<run_id>/")
    p.add_argument("--source", choices=["auto", "artifact", "last_html", "debug_html"], default="auto")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--name", default="", help="Optional fixture name stem")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    p.add_argument("--overwrite", type=_parse_bool_int, default=False)
    p.add_argument("--dry-run", type=_parse_bool_int, default=False)
    p.add_argument("--strict", type=_parse_bool_int, default=False)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir_root = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    sites = _choose_sites(args.site)
    any_missing = False

    for site in sites:
        run_id = (args.run_id or "").strip() or None
        result = capture_one(
            site=site,
            run_id=run_id,
            source=args.source,
            out_dir_root=out_dir_root,
            name_override=(args.name or "").strip() or None,
            max_bytes=max(1024, int(args.max_bytes)),
            overwrite=bool(args.overwrite),
            dry_run=bool(args.dry_run),
        )
        if result.get("status") != "ok":
            any_missing = True
            print(
                f"[WARN] site={site} status=not_found source={args.source} run_id={run_id or ''} "
                f"message={result.get('message', '')}"
            )
            continue

        print(
            "site={site} source={source_kind} input={input_path} output={output_path} "
            "bytes_before={bytes_before} bytes_after={bytes_after} redactions={redactions} dry_run={dry_run}".format(
                site=result["site"],
                source_kind=result.get("source_kind", ""),
                input_path=result.get("source_path", ""),
                output_path=result.get("output_path", ""),
                bytes_before=result.get("bytes_before", 0),
                bytes_after=result.get("bytes_after", 0),
                redactions=result.get("redactions", 0),
                dry_run=int(bool(result.get("dry_run", False))),
            )
        )
        if not result.get("dry_run", False):
            print(f"meta={result.get('meta_path', '')}")

    if any_missing and bool(args.strict):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
