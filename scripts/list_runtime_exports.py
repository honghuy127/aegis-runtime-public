#!/usr/bin/env python3
"""Backward-compatible wrapper for moved scenario_runner runtime export script."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    target = Path(__file__).with_name("scenario_runner") / "list_runtime_exports.py"
    runpy.run_path(str(target), run_name="__main__")
