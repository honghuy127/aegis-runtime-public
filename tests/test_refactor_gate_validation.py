import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_refactor_gate_with_tests(test_cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "scripts/refactor_gate.sh",
            "--file",
            "core/scenario_runner.py",
            "--entrypoints",
            "run_agentic_scenario",
            "--tests",
            test_cmd,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_refactor_gate_rejects_noop_true_test_command():
    proc = _run_refactor_gate_with_tests("true")
    assert proc.returncode != 0
    assert "no-op" in proc.stderr


def test_refactor_gate_requires_pytest_command():
    proc = _run_refactor_gate_with_tests("echo smoke")
    assert proc.returncode != 0
    assert "at least one pytest" in proc.stderr
