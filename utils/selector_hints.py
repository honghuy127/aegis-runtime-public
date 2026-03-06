"""Runtime-learned UI selector hints (bounded overlay on canonical profiles).

This module stores per-site selector hints under ``storage/state/ui_selector_hints/``.
Hints are non-canonical: they bias selector ordering for future runs but never replace
the fallback selectors generated from ``configs/service_ui_profiles.json``.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

from utils.run_paths import get_artifacts_dir, get_run_dir


DEFAULT_HINTS_ROOT = Path("storage/state/ui_selector_hints")
_SAFE_SEGMENT_RE = re.compile(r"[^a-z0-9_]+")


def _norm_key(value: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return _SAFE_SEGMENT_RE.sub("_", text).strip("_")


def _norm_display_lang(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text.split("-", 1)[0]


def _is_seedable_selector(value: str) -> bool:
    selector = str(value or "").strip()
    if not selector:
        return False
    if selector.startswith(":"):
        return False
    return True


def _site_file(site: str, *, hints_root: Path = DEFAULT_HINTS_ROOT) -> Path:
    site_key = _norm_key(site) or "unknown"
    return Path(hints_root) / f"{site_key}.json"


def _load_store(site: str, *, hints_root: Path = DEFAULT_HINTS_ROOT) -> Dict[str, Any]:
    path = _site_file(site, hints_root=hints_root)
    if not path.exists():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "entries": []}
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        return {"version": int(data.get("version", 1) or 1), "entries": entries}
    except Exception:
        return {"version": 1, "entries": []}


def _write_store(site: str, store: Dict[str, Any], *, hints_root: Path = DEFAULT_HINTS_ROOT) -> Path:
    path = _site_file(site, hints_root=hints_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "entries": list(store.get("entries", []) or []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _entry_matches_query(entry: Dict[str, Any], *, action: str, role: str, display_lang: str) -> bool:
    if _norm_key(entry.get("action", "")) != _norm_key(action):
        return False
    if _norm_key(entry.get("role", "")) != _norm_key(role):
        return False
    entry_lang = _norm_display_lang(entry.get("display_lang", ""))
    query_lang = _norm_display_lang(display_lang)
    if query_lang:
        return entry_lang in {"", query_lang}
    return True


def get_selector_hints(
    *,
    site: str,
    action: str,
    role: str = "",
    display_lang: str = "",
    locale: str = "",
    region: str = "",
    hints_root: Path = DEFAULT_HINTS_ROOT,
    max_selectors: int = 4,
) -> List[str]:
    """Return ordered selector hints for one site/action/role tuple.

    Matching is exact on ``site`` and normalized ``action``/``role``. ``display_lang``
    is preferred but empty-language hints remain eligible as fallback.
    """
    site_key = _norm_key(site)
    if not site_key:
        return []
    store = _load_store(site_key, hints_root=hints_root)
    query_lang = _norm_display_lang(display_lang)
    query_locale = str(locale or "").strip().lower()
    query_region = str(region or "").strip().upper()
    scored: List[tuple] = []
    for raw in list(store.get("entries", []) or []):
        if not isinstance(raw, dict):
            continue
        selector = str(raw.get("selector", "") or "").strip()
        if not selector:
            continue
        if not _entry_matches_query(raw, action=action, role=role, display_lang=query_lang):
            continue
        entry_lang = _norm_display_lang(raw.get("display_lang", ""))
        entry_locale = str(raw.get("locale", "") or "").strip().lower()
        entry_region = str(raw.get("region", "") or "").strip().upper()
        lang_score = 2 if (query_lang and entry_lang == query_lang) else (1 if not entry_lang else 0)
        locale_score = 1 if (query_locale and entry_locale and entry_locale == query_locale) else 0
        region_score = 1 if (query_region and entry_region and entry_region == query_region) else 0
        score = int(raw.get("score", 0) or 0)
        successes = int(raw.get("successes", 0) or 0)
        failures = int(raw.get("failures", 0) or 0)
        last_seen = str(raw.get("last_seen", "") or "")
        scored.append(
            (
                lang_score,
                locale_score,
                region_score,
                score,
                successes,
                -failures,
                last_seen,
                selector,
            )
        )
    scored.sort(reverse=True)
    out: List[str] = []
    for _lang, _loc, _reg, _score, _succ, _fail, _ts, selector in scored:
        if selector in out:
            continue
        out.append(selector)
        if len(out) >= max(0, int(max_selectors or 0)):
            break
    return out


def _upsert_selector_hint(
    *,
    site: str,
    action: str,
    role: str,
    selector: str,
    display_lang: str = "",
    locale: str = "",
    region: str = "",
    source: str = "runtime",
    fingerprint: Optional[Dict[str, Any]] = None,
    mode: str = "seed",
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> bool:
    selector_text = str(selector or "").strip()
    if not _is_seedable_selector(selector_text):
        return False
    site_key = _norm_key(site)
    if not site_key:
        return False
    store = _load_store(site_key, hints_root=hints_root)
    entries = [e for e in list(store.get("entries", []) or []) if isinstance(e, dict)]
    now = datetime.now(UTC).isoformat()
    act = _norm_key(action)
    role_key = _norm_key(role)
    disp = _norm_display_lang(display_lang)
    locale_norm = str(locale or "").strip()
    region_norm = str(region or "").strip().upper()
    matched = None
    for item in entries:
        if (
            _norm_key(item.get("action", "")) == act
            and _norm_key(item.get("role", "")) == role_key
            and _norm_display_lang(item.get("display_lang", "")) == disp
            and str(item.get("selector", "") or "").strip() == selector_text
        ):
            matched = item
            break
    if matched is None:
        matched = {
            "site": site_key,
            "action": act,
            "role": role_key,
            "selector": selector_text,
            "display_lang": disp,
            "locale": locale_norm,
            "region": region_norm,
            "successes": 0,
            "failures": 0,
            "score": 0,
            "created_at": now,
            "last_seen": now,
            "source": source,
        }
        if isinstance(fingerprint, dict) and fingerprint:
            matched["fingerprint"] = dict(fingerprint)
        entries.append(matched)
    matched["last_seen"] = now
    if locale_norm:
        matched["locale"] = locale_norm
    if region_norm:
        matched["region"] = region_norm
    if source:
        matched["source"] = str(source)[:40]
    if isinstance(fingerprint, dict) and fingerprint:
        matched["fingerprint"] = dict(fingerprint)
    if mode == "promote":
        matched["successes"] = int(matched.get("successes", 0) or 0) + 1
        matched["score"] = int(matched.get("score", 0) or 0) + 1
    elif mode == "overwrite":
        matched["successes"] = max(1, int(matched.get("successes", 0) or 0))
        matched["score"] = max(2, int(matched.get("score", 0) or 0))
    else:  # seed
        matched["successes"] = max(1, int(matched.get("successes", 0) or 0))
        matched["score"] = max(1, int(matched.get("score", 0) or 0))

    # Keep file bounded.
    entries.sort(
        key=lambda e: (
            int(e.get("score", 0) or 0),
            int(e.get("successes", 0) or 0),
            -int(e.get("failures", 0) or 0),
            str(e.get("last_seen", "") or ""),
        ),
        reverse=True,
    )
    store["entries"] = entries[:200]
    _write_store(site_key, store, hints_root=hints_root)
    return True


def _mutate_selector_hint(
    *,
    site: str,
    action: str,
    role: str,
    selector: str,
    display_lang: str = "",
    mutate: str = "fail",
    reason: str = "",
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> bool:
    site_key = _norm_key(site)
    selector_text = str(selector or "").strip()
    if not site_key or not selector_text:
        return False
    store = _load_store(site_key, hints_root=hints_root)
    entries = [e for e in list(store.get("entries", []) or []) if isinstance(e, dict)]
    act = _norm_key(action)
    role_key = _norm_key(role)
    disp = _norm_display_lang(display_lang)
    now = datetime.now(UTC).isoformat()
    changed = False
    kept = []
    for item in entries:
        same = (
            _norm_key(item.get("action", "")) == act
            and _norm_key(item.get("role", "")) == role_key
            and str(item.get("selector", "") or "").strip() == selector_text
            and (
                not disp
                or _norm_display_lang(item.get("display_lang", "")) in {"", disp}
            )
        )
        if not same:
            kept.append(item)
            continue
        changed = True
        if mutate == "quarantine":
            continue
        item["last_seen"] = now
        item["failures"] = int(item.get("failures", 0) or 0) + 1
        item["score"] = max(-5, int(item.get("score", 0) or 0) - 1)
        if reason:
            item["last_failure_reason"] = str(reason)[:80]
        kept.append(item)
    if not changed:
        return False
    store["entries"] = kept[:200]
    _write_store(site_key, store, hints_root=hints_root)
    return True


def promote_selector_hint(
    *,
    site: str,
    action: str,
    role: str,
    selector: str,
    display_lang: str = "",
    locale: str = "",
    region: str = "",
    source: str = "runtime",
    fingerprint: Optional[Dict[str, Any]] = None,
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> bool:
    return _upsert_selector_hint(
        site=site,
        action=action,
        role=role,
        selector=selector,
        display_lang=display_lang,
        locale=locale,
        region=region,
        source=source,
        fingerprint=fingerprint,
        mode="promote",
        hints_root=hints_root,
    )


def record_selector_hint_failure(
    *,
    site: str,
    action: str,
    role: str,
    selector: str,
    display_lang: str = "",
    reason: str = "",
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> bool:
    """Demote one selector hint after deterministic failure/rejection."""
    return _mutate_selector_hint(
        site=site,
        action=action,
        role=role,
        selector=selector,
        display_lang=display_lang,
        mutate="fail",
        reason=reason,
        hints_root=hints_root,
    )


def quarantine_selector_hint(
    *,
    site: str,
    action: str,
    role: str,
    selector: str,
    display_lang: str = "",
    reason: str = "",
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> bool:
    """Remove one selector hint when proven non-semantic/poisoned."""
    return _mutate_selector_hint(
        site=site,
        action=action,
        role=role,
        selector=selector,
        display_lang=display_lang,
        mutate="quarantine",
        reason=reason,
        hints_root=hints_root,
    )


def seed_selector_hints(
    hints: Iterable[Dict[str, Any]],
    *,
    overwrite: bool = False,
    hints_root: Path = DEFAULT_HINTS_ROOT,
) -> Dict[str, int]:
    entries = [h for h in (hints or []) if isinstance(h, dict)]
    if not entries:
        return {"seeded": 0, "skipped": 0, "groups_overwritten": 0}
    groups = set()
    if overwrite:
        for item in entries:
            groups.add(
                (
                    _norm_key(item.get("site", "")),
                    _norm_key(item.get("action", "")),
                    _norm_key(item.get("role", "")),
                    _norm_display_lang(item.get("display_lang", "")),
                )
            )
        _overwrite_groups(groups, hints_root=hints_root)
    seeded = 0
    skipped = 0
    for item in entries:
        ok = _upsert_selector_hint(
            site=str(item.get("site", "") or ""),
            action=str(item.get("action", "") or ""),
            role=str(item.get("role", "") or ""),
            selector=str(item.get("selector", "") or ""),
            display_lang=str(item.get("display_lang", "") or ""),
            locale=str(item.get("locale", "") or ""),
            region=str(item.get("region", "") or ""),
            source=str(item.get("source", "seed") or "seed"),
            fingerprint=item.get("fingerprint") if isinstance(item.get("fingerprint"), dict) else None,
            mode="overwrite" if overwrite else "seed",
            hints_root=hints_root,
        )
        if ok:
            seeded += 1
        else:
            skipped += 1
    return {"seeded": seeded, "skipped": skipped, "groups_overwritten": len(groups) if overwrite else 0}


def _overwrite_groups(groups: Iterable[tuple], *, hints_root: Path = DEFAULT_HINTS_ROOT) -> None:
    by_site: Dict[str, List[tuple]] = {}
    for item in groups:
        if not isinstance(item, tuple) or len(item) != 4:
            continue
        site, action, role, display_lang = item
        if not site or not action:
            continue
        by_site.setdefault(site, []).append((site, action, role, display_lang))
    for site, site_groups in by_site.items():
        store = _load_store(site, hints_root=hints_root)
        keep = []
        for raw in list(store.get("entries", []) or []):
            if not isinstance(raw, dict):
                continue
            group = (
                _norm_key(raw.get("site", site)),
                _norm_key(raw.get("action", "")),
                _norm_key(raw.get("role", "")),
                _norm_display_lang(raw.get("display_lang", "")),
            )
            if group in site_groups:
                continue
            keep.append(raw)
        store["entries"] = keep
        _write_store(site, store, hints_root=hints_root)


def _parse_google_display_lang_from_url(url: str) -> str:
    try:
        q = parse_qs(urlparse(str(url or "")).query or "")
        values = q.get("hl") or []
        if values:
            return _norm_display_lang(values[0])
    except Exception:
        return ""
    return ""


def _artifact_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def collect_selector_hints_from_run(
    run_id: str,
    *,
    site_filter: str = "google_flights",
) -> List[Dict[str, Any]]:
    """Collect seedable selector hints from one debug run's logs/artifacts."""
    run_dir = get_run_dir(run_id)
    artifacts_dir = get_artifacts_dir(run_id)
    site_key = _norm_key(site_filter)
    out: List[Dict[str, Any]] = []
    if site_key == "google_flights":
        # Route fill selector probe artifacts (successful combobox stage only).
        for path in sorted(artifacts_dir.glob("google_route_fill_*_post_combobox_selector_probe.json")):
            data = _artifact_json(path)
            extra = data.get("extra", {}) if isinstance(data.get("extra"), dict) else {}
            if not bool(extra.get("ok")):
                continue
            role = str(data.get("role", "") or "").strip().lower()
            combobox_debug = extra.get("combobox_debug", {}) if isinstance(extra.get("combobox_debug"), dict) else {}
            probe = data.get("selector_dom_probe", {}) if isinstance(data.get("selector_dom_probe"), dict) else {}
            display_lang = _parse_google_display_lang_from_url(str(probe.get("url", "") or ""))
            fingerprint = {}
            active = probe.get("active_element", {}) if isinstance(probe.get("active_element"), dict) else {}
            aria_label = str(active.get("aria_label", "") or "").strip()
            tag = str(active.get("tag", "") or "").strip()
            if aria_label:
                fingerprint["active_aria_label"] = aria_label[:120]
            if tag:
                fingerprint["active_tag"] = tag[:20]

            activation_selector = str(combobox_debug.get("activation_selector_used", "") or "").strip()
            if _is_seedable_selector(activation_selector):
                out.append(
                    {
                        "site": "google_flights",
                        "action": "route_fill_activation",
                        "role": role,
                        "selector": activation_selector,
                        "display_lang": display_lang,
                        "source": "debug_seed",
                        "fingerprint": fingerprint,
                    }
                )
            input_selector = str(combobox_debug.get("input_selector_used", "") or "").strip()
            generic_input = bool(combobox_debug.get("generic_input_selector_used"))
            if _is_seedable_selector(input_selector) and not generic_input:
                out.append(
                    {
                        "site": "google_flights",
                        "action": "route_fill_input",
                        "role": role,
                        "selector": input_selector,
                        "display_lang": display_lang,
                        "source": "debug_seed",
                        "fingerprint": fingerprint,
                    }
                )

        # Quick rebind search click success from run.log.
        run_log = run_dir / "run.log"
        if run_log.exists():
            text = run_log.read_text(encoding="utf-8", errors="replace")
            display_lang = ""
            for line in text.splitlines():
                if "scenario.start " in line and " url=" in line:
                    m = re.search(r"\burl=(\S+)", line)
                    if m:
                        display_lang = _parse_google_display_lang_from_url(m.group(1))
                if "gf.deeplink.quick_rebind.search_click_ok selector=" not in line:
                    continue
                m = re.search(r"selector=(.+)$", line)
                if not m:
                    continue
                selector = str(m.group(1) or "").strip()
                if not selector:
                    continue
                out.append(
                    {
                        "site": "google_flights",
                        "action": "quick_rebind_search",
                        "role": "",
                        "selector": selector,
                        "display_lang": display_lang,
                        "source": "debug_seed",
                    }
                )

    # Dedupe while preserving first-seen order.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for item in out:
        key = (
            _norm_key(item.get("site", "")),
            _norm_key(item.get("action", "")),
            _norm_key(item.get("role", "")),
            _norm_display_lang(item.get("display_lang", "")),
            str(item.get("selector", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("seed", "overwrite"):
        sp = sub.add_parser(name, help=f"{name.title()} selector hints from debug run artifacts")
        sp.add_argument("--run-id", required=True, help="Canonical run id under storage/runs/")
        sp.add_argument("--site", default="google_flights", help="Site key (default: google_flights)")
        sp.add_argument("--hints-root", default=str(DEFAULT_HINTS_ROOT), help="Hints storage directory")
        sp.add_argument("--print-only", action="store_true", help="Print extracted hints without writing")

    show = sub.add_parser("show", help="Show stored selector hints for one site")
    show.add_argument("--site", required=True, help="Site key")
    show.add_argument("--hints-root", default=str(DEFAULT_HINTS_ROOT), help="Hints storage directory")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    hints_root = Path(getattr(args, "hints_root", str(DEFAULT_HINTS_ROOT)))
    cmd = str(args.cmd)
    if cmd in {"seed", "overwrite"}:
        hints = collect_selector_hints_from_run(str(args.run_id), site_filter=str(args.site))
        if args.print_only:
            print(json.dumps(hints, ensure_ascii=False, indent=2))
            return 0
        stats = seed_selector_hints(hints, overwrite=(cmd == "overwrite"), hints_root=hints_root)
        print(
            json.dumps(
                {
                    "cmd": cmd,
                    "run_id": str(args.run_id),
                    "site": str(args.site),
                    "hints_root": str(hints_root),
                    "extracted": len(hints),
                    **stats,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if cmd == "show":
        store = _load_store(str(args.site), hints_root=hints_root)
        print(json.dumps(store, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
