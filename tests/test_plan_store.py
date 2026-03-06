"""Tests for plan store note-aware persistence."""

import storage.plan_store as ps


def _patch_plan_store_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "SEED_STORE_PATH", tmp_path / "plan_store.seed.json")
    monkeypatch.setattr(ps, "STORE_PATH", tmp_path / "plan_store.local.json")


def test_plan_store_roundtrip_steps_and_notes(tmp_path, monkeypatch):
    """Plan store should persist step list plus short planner notes."""
    _patch_plan_store_paths(tmp_path, monkeypatch)
    plan = [{"action": "wait", "selector": ["body"]}]
    ps.save_plan("google_flights", plan, notes=["first note", "second note"])
    assert ps.get_plan("google_flights") == plan
    assert ps.get_plan_notes("google_flights") == ["first note", "second note"]


def test_plan_store_reads_legacy_list_entry(tmp_path, monkeypatch):
    """Legacy list-only entries should remain readable after note-aware migration."""
    _patch_plan_store_paths(tmp_path, monkeypatch)
    ps.save_store(
        {
            "google_flights": [
                {"action": "wait", "selector": ["body"]},
            ]
        }
    )
    assert ps.get_plan("google_flights") == [{"action": "wait", "selector": ["body"]}]
    assert ps.get_plan_notes("google_flights") == []


def test_plan_store_merges_seed_and_local_overlay(tmp_path, monkeypatch):
    """Merged reads should use local entries as overlay while preserving seed entries."""
    _patch_plan_store_paths(tmp_path, monkeypatch)
    ps.SEED_STORE_PATH.write_text(
        """{
  "google_flights": {"steps": [{"action": "wait", "selector": ["body"]}], "notes": ["seed"]},
  "skyscanner": {"steps": [{"action": "click", "selector": ["main"]}]}
}""",
        encoding="utf-8",
    )
    ps.save_store(
        {
            "google_flights": {
                "steps": [{"action": "wait", "selector": ["[role='main']"]}],
                "notes": ["local"],
            }
        }
    )
    assert ps.get_plan("google_flights") == [{"action": "wait", "selector": ["[role='main']"]}]
    assert ps.get_plan_notes("google_flights") == ["local"]
    assert ps.get_plan("skyscanner") == [{"action": "click", "selector": ["main"]}]


def test_save_plan_writes_local_overlay_only_and_keeps_seed_unchanged(tmp_path, monkeypatch):
    """save_plan should not rewrite the committed seed file."""
    _patch_plan_store_paths(tmp_path, monkeypatch)
    ps.SEED_STORE_PATH.write_text(
        """{"google_flights": {"steps": [{"action": "wait", "selector": ["body"]}]}}""",
        encoding="utf-8",
    )
    seed_before = ps.SEED_STORE_PATH.read_text(encoding="utf-8")
    ps.save_plan("google_flights", [{"action": "click", "selector": ["main"]}], notes=["runtime"])
    assert ps.SEED_STORE_PATH.read_text(encoding="utf-8") == seed_before
    assert ps.STORE_PATH.exists()
    assert ps.get_plan("google_flights") == [{"action": "click", "selector": ["main"]}]
    assert ps.get_plan_notes("google_flights") == ["runtime"]


def test_plan_store_filters_transient_route_probe_notes(tmp_path, monkeypatch):
    _patch_plan_store_paths(tmp_path, monkeypatch)
    ps.save_plan(
        "google_flights",
        [{"action": "wait", "selector": ["body"]}],
        notes=[
            "verify.route_core_observed_dest=0",
            "PhaseBDeterministicFallback: route_core only",
            "keep me",
        ],
    )
    assert ps.get_plan_notes("google_flights") == ["keep me"]
