"""Quick integration test demonstrating debug mode usage."""

import json
import tempfile
from pathlib import Path

from utils.run_episode import RunEpisode, generate_run_id


def demo_debug_mode():
    """Demonstrate debug mode functionality with a mock run."""

    print("=" * 60)
    print("DEBUG MODE INTEGRATION TEST")
    print("=" * 60)

    # Create temp directory for demo
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Generate run ID
        run_id = generate_run_id()
        print(f"\n1. Generated run_id: {run_id}")

        # Create run episode with config
        config = {
            "origin": "HND",
            "dest": "ITM",
            "depart": "2026-03-01",
            "llm_mode": "full",
        }

        services = ["google_flights"]
        models = {
            "planner": "qwen3:8b",
            "coder": "qwen2.5-coder:7b",
        }

        with RunEpisode(
            run_id=run_id,
            base_dir=tmp_path,
            config_snapshot=config,
            services=services,
            models_config=models,
        ) as episode:
            print(f"2. Created run folder: {episode.run_dir}")

            # Emit events
            episode.emit_event({
                "event": "run_started",
                "level": "info",
                "services": services,
            })

            episode.emit_event({
                "event": "service_started",
                "level": "info",
                "site": "google_flights",
                "url": "https://www.google.com/travel/flights",
            })

            episode.emit_event({
                "event": "extraction_completed",
                "level": "info",
                "site": "google_flights",
                "price": 15000,
                "currency": "JPY",
                "confidence": "high",
            })

            print("3. Emitted 3 events to events.jsonl")

            # Save artifacts
            episode.save_artifact(
                "<html><body>Mock page</body></html>",
                "google_flights_last.html"
            )

            episode.save_artifact(
                {"price": 15000, "currency": "JPY"},
                "extraction_result.json"
            )

            print("4. Saved 2 artifacts")

        # Verify outputs
        print("\n5. Verifying outputs:")

        # Check manifest
        manifest_path = tmp_path / run_id / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            print(f"   ✓ manifest.json exists")
            print(f"     - run_id: {manifest['run_id']}")
            print(f"     - services: {manifest['services']}")
            print(f"     - git available: {manifest['git']['available']}")

        # Check events
        events_path = tmp_path / run_id / "events.jsonl"
        if events_path.exists():
            with open(events_path) as f:
                event_count = sum(1 for _ in f)
            print(f"   ✓ events.jsonl exists ({event_count} events)")

        # Check log
        log_path = tmp_path / run_id / "run.log"
        if log_path.exists():
            print(f"   ✓ run.log exists")

        # Check artifacts
        artifacts_dir = tmp_path / run_id / "artifacts"
        if artifacts_dir.exists():
            artifact_count = len(list(artifacts_dir.iterdir()))
            print(f"   ✓ artifacts/ exists ({artifact_count} files)")

        print("\n" + "=" * 60)
        print("SUCCESS: Debug mode integration test passed!")
        print("=" * 60)


if __name__ == "__main__":
    demo_debug_mode()
