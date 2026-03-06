#!/usr/bin/env python3
"""Backward-compatible wrapper for moved scenario_runner scan script."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    target = Path(__file__).with_name("scenario_runner") / "scan_run_agentic_underscored.py"
    runpy.run_path(str(target), run_name="__main__")
