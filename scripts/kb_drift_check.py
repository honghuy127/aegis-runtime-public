#!/usr/bin/env python3
"""KB drift checker - detects mismatches between code and YAML registries.

Validates that:
- Reason codes emitted in code exist in triage YAML
- Evidence keys written in code exist in evidence YAML
- Invariant IDs referenced in tests exist in invariants YAML

Usage:
    python scripts/kb_drift_check.py
    python scripts/kb_drift_check.py --warnings
    python scripts/kb_drift_check.py --json
    python scripts/kb_drift_check.py --json > storage/debug/kb_drift_report.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kb_drift import detect_drift


def main():
    parser = argparse.ArgumentParser(
        description="KB drift detector - find mismatches between code and YAML registries"
    )
    parser.add_argument(
        "--warnings",
        action="store_true",
        help="Include warnings in output (default: errors only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format instead of human-readable",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Write report to file (default: stdout)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    # Detect drift
    report = detect_drift()

    # Format output
    if args.json:
        output = json.dumps(report.to_dict(), indent=2)
    else:
        output = report.format_report(include_warnings=args.warnings)

    # Write output
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Report written to: {args.output}", file=sys.stderr)
    else:
        print(output)

    # Exit with error code if errors found
    sys.exit(1 if report.has_errors() else 0)


if __name__ == "__main__":
    main()
