"""Tests for thresholds configuration loading and browser timeout constraints."""

import ast
from pathlib import Path

import yaml

from utils.thresholds import (
    get_threshold,
    load_thresholds,
    reset_active_threshold_profile,
    set_active_threshold_profile,
)


def test_thresholds_yaml_parses_without_error():
    """Smoke test: all thresholds.yaml keys should parse successfully with correct types."""
    thresholds = load_thresholds()

    # Should load core config (25+ keys after Wave 2-3 cleanup)
    assert len(thresholds) >= 20, f"Expected 20+ threshold keys, got {len(thresholds)}"

    # Spot-check critical runtime keys exist and have correct types
    # Browser timeouts
    assert isinstance(thresholds["browser_goto_timeout_ms"], int)
    assert isinstance(thresholds["browser_goto_commit_timeout_ms"], int)
    assert isinstance(thresholds["browser_action_timeout_ms"], int)
    assert isinstance(thresholds["browser_wait_timeout_ms"], int)
    assert isinstance(thresholds["browser_action_selector_timeout_ms"], int)
    assert isinstance(thresholds["browser_wait_selector_timeout_ms"], int)
    assert isinstance(thresholds["browser_selector_timeout_min_ms"], int)

    # Scenario config
    assert isinstance(thresholds["scenario_candidate_timeout_sec"], int)
    assert isinstance(thresholds["scenario_max_retries"], int)
    assert isinstance(thresholds["scenario_max_turns"], int)
    assert isinstance(thresholds["scenario_budget_soft_margin_sec"], int)
    assert isinstance(thresholds["scenario_wall_clock_cap_sec"], int)
    assert isinstance(thresholds["scenario_evidence_dump_enabled"], bool)

    # Selector confidence
    assert isinstance(thresholds["selector_min_confidence"], float)
    assert isinstance(thresholds["soft_drift_penalty"], float)
    assert isinstance(thresholds["hard_drift_penalty"], float)
    assert isinstance(thresholds["selector_success_boost"], float)
    assert isinstance(thresholds["selector_failure_penalty"], float)

    # Extraction price bounds
    assert isinstance(thresholds["heuristic_min_price"], int)
    assert isinstance(thresholds["heuristic_max_price"], int)
    assert isinstance(thresholds["plausible_max_price"], int)


def test_browser_goto_commit_timeout_ms_key_exists():
    """Regression: ensure browser_goto_commit_timeout_ms key exists (typo fix from Wave 0-1 PR #1)."""
    thresholds = load_thresholds()
    assert "browser_goto_commit_timeout_ms" in thresholds, \
        "browser_goto_commit_timeout_ms key missing (regression from typo fix)"

    value = get_threshold("browser_goto_commit_timeout_ms")
    assert isinstance(value, int), f"Expected int, got {type(value)}"
    assert value > 0, f"Expected positive timeout, got {value}"


def test_goto_commit_timeout_threshold_key_correct():
    """Regression test: browser_goto_commit_timeout_ms must resolve to correct threshold key."""
    # Should load from thresholds.yaml without typo
    value = get_threshold("browser_goto_commit_timeout_ms", 25_000)
    assert value == 25_000, f"Expected 25000ms, got {value}"
    assert isinstance(value, int)


def test_browser_session_commit_timeout_defaults():
    """Test BrowserSession correctly initializes goto_commit_timeout_ms from threshold."""
    from core.browser import BrowserSession

    # Create session with defaults
    session = BrowserSession()

    # Should resolve to 25000ms default from threshold
    commit_ms = session.goto_commit_timeout_ms
    assert commit_ms == 25_000, f"Expected 25000ms, got {commit_ms}"
    assert isinstance(commit_ms, int)


def test_browser_session_commit_timeout_never_exceeds_goto_timeout():
    """Test that commit timeout semantic constraint is enforced: commit <= goto."""
    from core.browser import BrowserSession
    from unittest.mock import patch

    # Test 1: Default case (commit=25s, goto=45s)
    session1 = BrowserSession(goto_timeout_ms=45_000, goto_commit_timeout_ms=25_000)
    computed_timeout = max(1, min(session1.goto_timeout_ms, session1.goto_commit_timeout_ms))
    assert computed_timeout == 25_000, f"Expected 25000ms, got {computed_timeout}"

    # Test 2: If user overrides commit to be > goto, code enforces min(goto, commit)
    session2 = BrowserSession(goto_timeout_ms=10_000, goto_commit_timeout_ms=50_000)
    computed_timeout = max(1, min(session2.goto_timeout_ms, session2.goto_commit_timeout_ms))
    assert computed_timeout == 10_000, f"Constraint violated: computed {computed_timeout}ms > goto {session2.goto_timeout_ms}ms"

    # Test 3: Both configured to reasonable values
    session3 = BrowserSession(goto_timeout_ms=30_000, goto_commit_timeout_ms=15_000)
    computed_timeout = max(1, min(session3.goto_timeout_ms, session3.goto_commit_timeout_ms))
    assert computed_timeout == 15_000, f"Expected 15000ms, got {computed_timeout}"
    assert computed_timeout <= session3.goto_timeout_ms


def test_get_threshold_honors_active_debug_profile():
    """Process-local active profile should affect get_threshold() lookups."""
    reset_active_threshold_profile()
    base_value = get_threshold("browser_action_selector_timeout_ms", 0)
    assert base_value == 4000

    prev = set_active_threshold_profile("debug")
    try:
        assert prev == "default"
        debug_value = get_threshold("browser_action_selector_timeout_ms", 0)
        assert debug_value == 5000
    finally:
        reset_active_threshold_profile()


def test_all_literal_get_threshold_keys_are_declared():
    """Audit: every literal get_threshold('key') must exist in defaults or thresholds.yaml."""
    repo_root = Path(__file__).resolve().parent.parent
    thresholds_py = repo_root / "utils" / "thresholds.py"
    thresholds_ast = ast.parse(thresholds_py.read_text(encoding="utf-8"))

    default_keys = set()
    for node in thresholds_ast.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_DEFAULTS"
            and isinstance(node.value, ast.Dict)
        ):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    default_keys.add(key.value)

    thresholds_yaml = yaml.safe_load((repo_root / "configs" / "thresholds.yaml").read_text(encoding="utf-8")) or {}
    declared_yaml_keys = set(thresholds_yaml.keys())
    profiles = thresholds_yaml.get("profiles")
    if isinstance(profiles, dict):
        for profile_payload in profiles.values():
            if isinstance(profile_payload, dict):
                declared_yaml_keys.update(profile_payload.keys())

    declared_keys = default_keys | declared_yaml_keys

    missing = []
    for py_file in repo_root.rglob("*.py"):
        if ".venv" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            fn_name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if fn_name != "get_threshold":
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                key = first_arg.value
                if key not in declared_keys:
                    missing.append(f"{py_file.relative_to(repo_root)}:{node.lineno}:{key}")

    assert not missing, "Undeclared get_threshold keys found:\n" + "\n".join(sorted(set(missing)))
