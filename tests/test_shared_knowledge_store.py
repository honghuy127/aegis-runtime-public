"""Tests for shared airport/metro knowledge database."""

import json

import storage.shared_knowledge_store as sks


def test_shared_knowledge_store_loads_aliases_and_provider_map(tmp_path, monkeypatch):
    """Store should parse alias and provider rewrite maps from JSON."""
    store_path = tmp_path / "shared_knowledge_store.json"
    store_path.write_text(
        json.dumps(
            {
                "airport_aliases": {
                    "HND": ["HND", "TYO", "東京"],
                    "ITM": ["ITM", "OSA", "大阪"],
                },
                "provider_airport_code_map": {
                    "google_flights": {"HND": "TYO", "ITM": "OSA"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sks, "STORE_PATH", store_path)

    loaded = sks.load_shared_knowledge(force_reload=True)
    assert loaded["airport_aliases"]["HND"] == ["HND", "TYO", "東京"]
    assert loaded["provider_airport_code_map"]["google_flights"]["HND"] == "TYO"

    assert "TYO" in sks.get_airport_aliases("hnd")
    assert sks.map_airport_code_for_provider("hnd", "google_flights") == "TYO"
    assert sks.map_airport_code_for_provider("hnd", "other_provider") == "HND"


def test_shared_knowledge_store_upsert_helpers(tmp_path, monkeypatch):
    """Upsert helpers should persist alias/provider rewrites to disk."""
    store_path = tmp_path / "shared_knowledge_store.json"
    monkeypatch.setattr(sks, "STORE_PATH", store_path)
    sks.save_shared_knowledge({"airport_aliases": {}, "provider_airport_code_map": {}})

    sks.upsert_airport_aliases("hnd", ["HND", "TYO", "東京"])
    sks.upsert_provider_airport_code_map("google_flights", "hnd", "tyo")

    loaded = sks.load_shared_knowledge(force_reload=True)
    assert loaded["airport_aliases"]["HND"] == ["HND", "TYO", "東京"]
    assert loaded["provider_airport_code_map"]["google_flights"]["HND"] == "TYO"


def test_shared_knowledge_store_provider_fallback_aliases_are_deterministic(tmp_path, monkeypatch):
    """Built-in fallback should provide base code + Google metro aliases without store data."""
    store_path = tmp_path / "shared_knowledge_store.json"
    monkeypatch.setattr(sks, "STORE_PATH", store_path)
    sks.load_shared_knowledge(force_reload=True)

    plain_aliases = sks.get_airport_aliases("HND")
    google_aliases = sks.get_airport_aliases_for_provider("HND", "google_flights")

    assert "HND" in plain_aliases
    assert "HND" in google_aliases
    assert "TYO" in google_aliases


def test_shared_knowledge_store_provider_fallback_includes_min_jp_tokens(tmp_path, monkeypatch):
    """Google fallback aliases should include deterministic JP display tokens in fresh env."""
    store_path = tmp_path / "shared_knowledge_store.json"
    monkeypatch.setattr(sks, "STORE_PATH", store_path)
    sks.load_shared_knowledge(force_reload=True)

    hnd_aliases = sks.get_airport_aliases_for_provider("HND", "google_flights")
    itm_aliases = sks.get_airport_aliases_for_provider("ITM", "google_flights")

    assert "HND" in hnd_aliases
    assert "TYO" in hnd_aliases
    assert "羽田" in hnd_aliases
    assert "ITM" in itm_aliases
    assert "OSA" in itm_aliases
    assert "伊丹" in itm_aliases
