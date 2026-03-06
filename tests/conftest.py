"""Pytest bootstrap for stable imports when running from repo root."""

from pathlib import Path
import sys
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.thresholds import reset_active_threshold_profile


def pytest_addoption(parser):
    """Add custom CLI flags for LLM/VLM test execution."""
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.llm (LLM-dependent tests)",
    )
    parser.addoption(
        "--run-vlm",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.vlm (VLM-dependent tests)",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip LLM/VLM tests unless explicitly requested via --run-llm or --run-vlm."""
    run_llm = config.getoption("--run-llm")
    run_vlm = config.getoption("--run-vlm")

    skip_llm = pytest.mark.skip(reason="skipped: requires --run-llm flag (LLM not available by default)")
    skip_vlm = pytest.mark.skip(reason="skipped: requires --run-vlm flag (VLM not available by default)")

    for item in items:
        if "llm" in item.keywords and not run_llm:
            item.add_marker(skip_llm)
        if "vlm" in item.keywords and not run_vlm:
            item.add_marker(skip_vlm)


@pytest.fixture(autouse=True)
def _reset_threshold_profile_between_tests():
    """Prevent process-global threshold profile state from leaking across tests."""
    reset_active_threshold_profile()
    yield
    reset_active_threshold_profile()
