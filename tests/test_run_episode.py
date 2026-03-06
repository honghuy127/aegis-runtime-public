"""Tests for debug mode run episode functionality."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.run_episode import (
    RunEpisode,
    cleanup_old_runs,
    ensure_run_id,
    generate_run_id,
    get_git_metadata,
    should_capture_artifacts,
)


class TestRunIdGeneration:
    """Test run ID generation and validation."""

    def test_generate_run_id_format(self):
        """Run ID should follow YYYYMMDD_HHMMSS_<6hex> format."""
        run_id = generate_run_id()

        # Should have 3 parts separated by underscores
        parts = run_id.split("_")
        assert len(parts) == 3

        # Date part: 8 digits
        assert len(parts[0]) == 8
        assert parts[0].isdigit()

        # Time part: 6 digits
        assert len(parts[1]) == 6
        assert parts[1].isdigit()

        # Random suffix: 6 hex chars
        assert len(parts[2]) == 6
        int(parts[2], 16)  # Should parse as hex

    def test_ensure_run_id_uses_provided(self):
        """ensure_run_id should use provided ID."""
        provided = "20260221_120000_abc123"
        result = ensure_run_id(provided)
        assert result == provided

    def test_ensure_run_id_generates_when_none(self):
        """ensure_run_id should generate new ID when None."""
        result = ensure_run_id(None)
        assert result is not None
        assert len(result.split("_")) == 3

    def test_ensure_run_id_generates_when_empty(self):
        """ensure_run_id should generate new ID when empty string."""
        result = ensure_run_id("   ")
        assert result is not None
        assert len(result.split("_")) == 3


class TestGitMetadata:
    """Test git metadata extraction."""

    def test_get_git_metadata_structure(self):
        """Git metadata should have expected structure."""
        metadata = get_git_metadata()

        assert "commit" in metadata
        assert "branch" in metadata
        assert "dirty" in metadata
        assert "available" in metadata

        # Should always complete without errors
        assert isinstance(metadata["dirty"], bool)
        assert isinstance(metadata["available"], bool)

    def test_get_git_metadata_in_git_repo(self):
        """Git metadata should populate when in git repo."""
        # This test assumes the project itself is in a git repo
        # If not, it will still pass but with available=False
        metadata = get_git_metadata()

        if metadata["available"]:
            # If git is available, commit should be populated
            assert metadata["commit"] is not None
            assert len(metadata["commit"]) > 0


class TestRunEpisode:
    """Test RunEpisode class functionality."""

    def test_run_episode_creates_directories(self, tmp_path):
        """RunEpisode should create run folder and artifacts subfolder."""
        run_id = "20260221_120000_test01"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            assert episode.run_dir.exists()
            assert episode.artifacts_dir.exists()
            assert episode.run_dir == tmp_path / run_id
            assert episode.artifacts_dir == tmp_path / run_id / "artifacts"

    def test_run_episode_creates_manifest(self, tmp_path):
        """RunEpisode should create manifest.json with expected fields."""
        run_id = "20260221_120000_test02"
        config = {"llm_mode": "full", "origin": "HND", "dest": "ITM"}
        services = ["google_flights"]
        models = {"planner": "qwen3:8b"}

        with RunEpisode(
            run_id=run_id,
            base_dir=tmp_path,
            config_snapshot=config,
            services=services,
            models_config=models,
        ):
            pass

        manifest_path = tmp_path / run_id / "manifest.json"
        assert manifest_path.exists()

        with open(manifest_path) as f:
            manifest = json.load(f)

        # Check required fields
        assert manifest["run_id"] == run_id
        assert "started_at" in manifest
        assert "finished_at" in manifest
        assert "git" in manifest
        assert "platform" in manifest
        assert manifest["services"] == services
        assert manifest["models"] == models
        assert manifest["config"] == config

    def test_run_episode_emits_events(self, tmp_path):
        """RunEpisode should append events to events.jsonl."""
        run_id = "20260221_120000_test03"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            episode.emit_event({
                "event": "test_event_1",
                "level": "info",
                "site": "google_flights",
            })
            episode.emit_event({
                "event": "test_event_2",
                "level": "debug",
                "action": "click",
            })

        events_path = tmp_path / run_id / "events.jsonl"
        assert events_path.exists()

        # Parse JSONL
        events = []
        with open(events_path) as f:
            for line in f:
                events.append(json.loads(line))

        assert len(events) == 2

        # Check first event
        assert events[0]["event"] == "test_event_1"
        assert events[0]["level"] == "info"
        assert events[0]["site"] == "google_flights"
        assert "ts" in events[0]
        assert "run_id" in events[0]
        assert events[0]["run_id"] == run_id
        assert "seq" in events[0]

        # Check second event
        assert events[1]["event"] == "test_event_2"
        assert events[1]["seq"] == 1  # Incremented

    def test_run_episode_saves_artifacts(self, tmp_path):
        """RunEpisode should save artifacts to artifacts/ folder."""
        run_id = "20260221_120000_test04"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            # Save text artifact
            episode.save_artifact("test content", "test.txt")

            # Save JSON artifact
            episode.save_artifact({"key": "value"}, "test.json")

            # Save binary artifact
            episode.save_artifact(b"binary data", "test.bin", binary=True)

        artifacts_dir = tmp_path / run_id / "artifacts"

        # Check text artifact
        assert (artifacts_dir / "test.txt").exists()
        assert (artifacts_dir / "test.txt").read_text() == "test content"

        # Check JSON artifact
        assert (artifacts_dir / "test.json").exists()
        with open(artifacts_dir / "test.json") as f:
            data = json.load(f)
        assert data == {"key": "value"}

        # Check binary artifact
        assert (artifacts_dir / "test.bin").exists()
        assert (artifacts_dir / "test.bin").read_bytes() == b"binary data"

    def test_run_episode_copies_error_file(self, tmp_path):
        """RunEpisode should copy scenario_last_error.json if it exists."""
        run_id = "20260221_120000_test05"

        # Create a fake error file
        error_file = tmp_path / "scenario_last_error.json"
        error_data = {"reason": "timeout", "evidence": {}}
        with open(error_file, "w") as f:
            json.dump(error_data, f)

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            episode.copy_error_file(error_file)

        # Check copied file
        copied_path = tmp_path / run_id / "scenario_last_error.json"
        assert copied_path.exists()

        with open(copied_path) as f:
            data = json.load(f)
        assert data == error_data

    def test_run_episode_logging_setup(self, tmp_path):
        """RunEpisode should create run.log file."""
        run_id = "20260221_120000_test06"

        with RunEpisode(run_id=run_id, base_dir=tmp_path):
            pass

        log_path = tmp_path / run_id / "run.log"
        # File should exist even if no logs written
        assert log_path.exists()

    def test_run_episode_context_manager(self, tmp_path):
        """RunEpisode should finalize properly when used as context manager."""
        run_id = "20260221_120000_test07"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            start_time = episode.started_at
            assert episode.finished_at is None

        # After exit, finished_at should be set
        manifest_path = tmp_path / run_id / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["started_at"] == start_time
        assert manifest["finished_at"] is not None


class TestCleanup:
    """Test cleanup utilities."""

    def test_cleanup_old_runs(self, tmp_path):
        """cleanup_old_runs should delete older run folders."""
        # Create 5 run folders with sequential names
        for i in range(5):
            run_id = f"20260221_12000{i}_test0{i}"
            run_dir = tmp_path / run_id
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text("{}")

        # Keep last 2
        deleted = cleanup_old_runs(tmp_path, keep_last=2)

        assert deleted == 3

        # Check remaining folders
        remaining = [d.name for d in tmp_path.iterdir() if d.is_dir()]
        assert len(remaining) == 2
        assert "20260221_120003_test03" in remaining
        assert "20260221_120004_test04" in remaining

    def test_cleanup_old_runs_with_zero_keep(self, tmp_path):
        """cleanup_old_runs should do nothing when keep_last=0."""
        # Create 3 run folders
        for i in range(3):
            run_id = f"20260221_12000{i}_test0{i}"
            (tmp_path / run_id).mkdir()

        deleted = cleanup_old_runs(tmp_path, keep_last=0)

        assert deleted == 0
        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 3

    def test_cleanup_old_runs_nonexistent_dir(self, tmp_path):
        """cleanup_old_runs should handle nonexistent directory gracefully."""
        nonexistent = tmp_path / "doesnotexist"
        deleted = cleanup_old_runs(nonexistent, keep_last=5)
        assert deleted == 0


class TestArtifactCapturePolicy:
    """Test artifact capture decision logic."""

    def test_should_capture_artifacts_on_not_ready(self):
        """Should capture when ready=False."""
        result = {"ready": False, "price": 100}
        assert should_capture_artifacts(result) is True

    def test_should_capture_artifacts_on_no_price(self):
        """Should capture when price is None."""
        result = {"ready": True, "price": None}
        assert should_capture_artifacts(result) is True

    def test_should_capture_artifacts_on_error_reason(self):
        """Should capture when reason contains error keywords."""
        error_reasons = [
            "timeout_error",
            "selector_not_found",
            "budget_exhausted",
            "calendar_not_open",
        ]

        for reason in error_reasons:
            result = {"ready": True, "price": 100, "reason": reason}
            assert should_capture_artifacts(result) is True

    def test_should_not_capture_on_success(self):
        """Should not capture on successful extraction."""
        result = {"ready": True, "price": 100, "reason": "ok"}
        assert should_capture_artifacts(result) is False

    def test_should_capture_when_no_result(self):
        """Should capture when no result provided."""
        assert should_capture_artifacts(None) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
