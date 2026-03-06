import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scenario_runner" / "list_runtime_exports.py"


def test_list_runtime_exports_help_describes_runtime_audit_semantics():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "runtime-patch symbol wiring" in proc.stdout
    assert "generic Python export listing utility" in proc.stdout
    assert "runtime_names_count=0" in proc.stdout
    assert "can be normal" in proc.stdout


def test_list_runtime_exports_emits_expected_json_shape():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    for key in (
        "runner_path",
        "bootstrap_path",
        "runtime_names_count",
        "exported_runtime_count",
        "not_provided_count",
        "not_provided",
        "provided_by_imports_count",
    ):
        assert key in payload
