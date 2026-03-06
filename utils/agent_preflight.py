"""KB-first preflight helper for coding agents.

Purpose:
- Print the mandatory KB entrypoint reading order
- Map reasons/topics/files to relevant KB docs before planning/coding
- Provide a lightweight local mechanism that agents can run as a planning gate

Examples:
    python -m utils.agent_preflight
    python -m utils.agent_preflight --reason calendar_not_open
    python -m utils.agent_preflight --topic date_picker --path core/scenario/gf_helpers/date_picker_orchestrator.py
    python -m utils.agent_preflight --path core/scenario_runner.py --strict
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from utils.kb import KBIndex, DocRef, get_docs_for_reason, get_docs_for_topic, get_entrypoints, get_kb


def _infer_topics_for_path(path_str: str) -> List[str]:
    path = str(path_str or "").strip().lower()
    if not path:
        return []
    topics: List[str] = []
    if "scenario_runner" in path:
        topics.extend(["scenario_runner", "evidence", "budgets_timeouts"])
    if "/scenario/" in path or path.startswith("core/scenario/"):
        topics.extend(["scenario_runner", "evidence"])
    if "date" in path or "calendar" in path:
        topics.append("date_picker")
    if "combobox" in path or "airport" in path:
        topics.append("combobox_commit")
    if "route_binding" in path or "scope" in path:
        topics.extend(["selectors", "evidence"])
    if "threshold" in path or "config" in path:
        topics.append("budgets_timeouts")
    if "extract" in path or "plugins/services" in path:
        topics.extend(["plugins", "evidence"])
    if "agent/" in path or "plugin" in path:
        topics.append("selectors")
    # Deduplicate preserving order
    out: List[str] = []
    for t in topics:
        if t not in out:
            out.append(t)
    return out


def _dedupe_docs(docs: List[DocRef]) -> List[DocRef]:
    seen = set()
    out: List[DocRef] = []
    for d in docs:
        if d.path in seen:
            continue
        seen.add(d.path)
        out.append(d)
    return out


def _serialize_docs(docs: List[DocRef]) -> List[Dict[str, object]]:
    return [
        {
            "path": d.path,
            "topic": d.topic,
            "priority": d.priority,
            "kind": d.kind,
        }
        for d in docs
    ]


def build_preflight(
    *,
    kb: KBIndex,
    reasons: List[str],
    topics: List[str],
    paths: List[str],
) -> Dict[str, object]:
    entrypoints = list(get_entrypoints(kb))
    resolved_docs: List[DocRef] = []
    by_reason: Dict[str, List[DocRef]] = {}
    by_topic: Dict[str, List[DocRef]] = {}
    by_path: Dict[str, Dict[str, object]] = {}

    for reason in reasons:
        docs = list(get_docs_for_reason(kb, reason))
        by_reason[reason] = docs
        resolved_docs.extend(docs)

    for topic in topics:
        docs = list(get_docs_for_topic(kb, topic))
        by_topic[topic] = docs
        resolved_docs.extend(docs)

    for path in paths:
        inferred_topics = _infer_topics_for_path(path)
        docs: List[DocRef] = []
        for topic in inferred_topics:
            docs.extend(get_docs_for_topic(kb, topic))
        docs = _dedupe_docs(docs)
        by_path[path] = {
            "inferred_topics": inferred_topics,
            "docs": docs,
        }
        resolved_docs.extend(docs)

    prioritized = _dedupe_docs(sorted(resolved_docs, key=lambda d: d.priority))
    return {
        "entrypoints": entrypoints,
        "by_reason": by_reason,
        "by_topic": by_topic,
        "by_path": by_path,
        "prioritized": prioritized,
    }


def _print_section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _print_docs(docs: List[DocRef], *, limit: int = 10) -> None:
    if not docs:
        print("  (none)")
        return
    for doc in docs[:limit]:
        print(f"  - {doc.path}  [topic={doc.topic} priority={doc.priority} kind={doc.kind}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="KB-first preflight helper for coding agents")
    parser.add_argument("--reason", action="append", default=[], help="Failure reason code (repeatable)")
    parser.add_argument("--topic", action="append", default=[], help="KB topic name (repeatable)")
    parser.add_argument("--path", action="append", default=[], help="Code/config path to infer docs for (repeatable)")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if targeted (reason/topic/path) lookups resolve no docs",
    )
    args = parser.parse_args()

    kb = get_kb()
    result = build_preflight(
        kb=kb,
        reasons=[str(x).strip() for x in args.reason if str(x).strip()],
        topics=[str(x).strip() for x in args.topic if str(x).strip()],
        paths=[str(x).strip() for x in args.path if str(x).strip()],
    )

    if args.json:
        payload = {
            "entrypoints": _serialize_docs(result["entrypoints"]),  # type: ignore[arg-type]
            "by_reason": {
                key: _serialize_docs(val) for key, val in result["by_reason"].items()  # type: ignore[union-attr]
            },
            "by_topic": {
                key: _serialize_docs(val) for key, val in result["by_topic"].items()  # type: ignore[union-attr]
            },
            "by_path": {
                key: {
                    "inferred_topics": value.get("inferred_topics", []),
                    "docs": _serialize_docs(value.get("docs", [])),
                }
                for key, value in result["by_path"].items()  # type: ignore[union-attr]
            },
            "prioritized": _serialize_docs(result["prioritized"]),  # type: ignore[arg-type]
        }
        print(json.dumps(payload, indent=2))
    else:
        print("KB-first Preflight (run before planning/coding)")
        print("Checklist: read entrypoints -> map reason/topic/path -> cite consulted docs in plan")
        _print_section("Mandatory Entrypoints")
        _print_docs(result["entrypoints"], limit=12)  # type: ignore[arg-type]

        if result["by_reason"]:  # type: ignore[truthy-bool]
            _print_section("Reason Mappings")
            for reason, docs in result["by_reason"].items():  # type: ignore[union-attr]
                print(f"* {reason}")
                _print_docs(docs, limit=8)

        if result["by_topic"]:  # type: ignore[truthy-bool]
            _print_section("Topic Mappings")
            for topic, docs in result["by_topic"].items():  # type: ignore[union-attr]
                print(f"* {topic}")
                _print_docs(docs, limit=8)

        if result["by_path"]:  # type: ignore[truthy-bool]
            _print_section("Path-Inferred Mappings")
            for path, item in result["by_path"].items():  # type: ignore[union-attr]
                inferred = item.get("inferred_topics", [])
                print(f"* {path}")
                print(f"  inferred_topics={inferred or []}")
                _print_docs(item.get("docs", []), limit=8)

        _print_section("Prioritized KB Set For This Task")
        _print_docs(result["prioritized"], limit=12)  # type: ignore[arg-type]

    if args.strict:
        targeted_count = (
            len(result["by_reason"])  # type: ignore[arg-type]
            + len(result["by_topic"])  # type: ignore[arg-type]
            + len(result["by_path"])  # type: ignore[arg-type]
        )
        resolved_count = len(result["prioritized"])  # type: ignore[arg-type]
        if targeted_count > 0 and resolved_count == 0:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
