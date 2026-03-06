"""Tests for global/local scenario knowledge persistence."""

from storage import knowledge_store as ks


def test_record_success_updates_global_and_local_knowledge(tmp_path, monkeypatch):
    """A successful plan should update both global and site-local selector stats."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")

    plan = [
        {"action": "click", "selector": ["text=国内"]},
        {"action": "fill", "selector": ["input[name='origin']"], "value": "HND"},
        {"action": "wait", "selector": ["[role='main']"]},
    ]
    ks.record_success(
        "google_flights",
        plan,
        is_domestic=True,
        source_url="https://www.google.com/travel/flights",
        turns_used=2,
        user_id="user@example.com",
    )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert knowledge["site_success_count"] == 1
    assert "[role='main']" in knowledge["global_wait_selectors"]
    assert "text=国内" in knowledge["local_domestic_toggles"]
    assert "https://www.google.com/travel/flights" in knowledge["local_domestic_url_hints"]
    assert "input[name='origin']" in knowledge["local_fill_origin_selectors"]
    assert knowledge["suggested_turns"] == 2
    assert isinstance(knowledge["last_success_plan"], list)


def test_get_knowledge_handles_empty_store(tmp_path, monkeypatch):
    """Unknown site should still return a fully shaped knowledge payload."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")

    knowledge = ks.get_knowledge("google_flights", user_id="gh:alice")
    assert knowledge["global_selectors"] == []
    assert knowledge["local_selectors"] == []
    assert knowledge["local_domestic_toggles"] == []
    assert knowledge["last_success_plan"] is None


def test_get_knowledge_is_scoped_per_user(tmp_path, monkeypatch):
    """Different user IDs should have isolated site knowledge namespaces."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")
    plan = [{"action": "wait", "selector": ["body"]}]

    ks.record_success(
        "google_flights",
        plan,
        source_url="https://u1.example/his",
        user_id="user1@example.com",
    )
    ks.record_success(
        "google_flights",
        plan,
        source_url="https://u2.example/his",
        user_id="user2@example.com",
    )

    k1 = ks.get_knowledge("google_flights", user_id="user1@example.com")
    k2 = ks.get_knowledge("google_flights", user_id="user2@example.com")
    assert "https://u1.example/his" in k1["local_url_hints"]
    assert "https://u2.example/his" not in k1["local_url_hints"]
    assert "https://u2.example/his" in k2["local_url_hints"]


def test_site_type_can_learn_split_from_urls_without_toggle(tmp_path, monkeypatch):
    """Split-flow site type should be inferred from domestic/international URL evidence."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")
    plan = [{"action": "wait", "selector": ["body"]}]

    ks.record_success(
        "google_flights",
        plan,
        is_domestic=True,
        source_url="https://www.google.com/travel/flights",
        user_id="user@example.com",
    )
    ks.record_success(
        "google_flights",
        plan,
        is_domestic=False,
        source_url="https://www.google.com/travel/flights",
        user_id="user@example.com",
    )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert knowledge["site_type"] == "domestic_international_split"
    assert "https://www.google.com/travel/flights" in knowledge["local_domestic_url_hints"]
    assert "https://www.google.com/travel/flights" in knowledge["local_international_url_hints"]


def test_single_flow_type_ignores_is_domestic_when_url_is_generic(tmp_path, monkeypatch):
    """Single-flow URLs should not be mislabeled as domestic/international hints."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")
    plan = [{"action": "wait", "selector": ["body"]}]

    for _ in range(3):
        ks.record_success(
            "google_flights",
            plan,
            is_domestic=True,
            source_url="https://www.google.com/travel/flights",
            user_id="user@example.com",
        )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert knowledge["site_type"] == "single_flow"
    assert knowledge["local_domestic_url_hints"] == []


def test_record_failure_tracks_selectors_and_reason(tmp_path, monkeypatch):
    """Failure evidence should be retained as avoid-selectors for future runs."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")

    error = (
        "Step failed action=fill selectors=[\"input[name='email']\", \"input[name='origin']\"]: "
        "Locator.wait_for: Timeout 4000ms exceeded."
    )
    ks.record_failure(
        "google_flights",
        error_message=error,
        user_id="user@example.com",
    )
    ks.record_failure(
        "google_flights",
        error_message=error,
        user_id="user@example.com",
    )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert "input[name='email']" in knowledge["local_failed_selectors"]
    assert "timeout" in knowledge["failure_reason_top"]
    assert "fill" in knowledge["failed_action_top"]


def test_record_package_url_hint_is_exposed_in_knowledge(tmp_path, monkeypatch):
    """Package URL hints should be persisted and surfaced for ranking de-priority."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")

    ks.record_package_url_hint(
        "google_flights",
        source_url="https://www.google.com/travel/packages/ana/",
        user_id="user@example.com",
    )
    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert "https://www.google.com/travel/packages/ana/" in knowledge["local_package_url_hints"]


def test_record_success_ignores_foreign_domain_url_hints(tmp_path, monkeypatch):
    """Success URL from another service domain must not be stored as local URL hint."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")
    plan = [{"action": "wait", "selector": ["body"]}]

    ks.record_success(
        "google_flights",
        plan,
        source_url="https://www.skyscanner.com/flights",  # Foreign service domain
        user_id="user@example.com",
    )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert "https://www.skyscanner.com/flights" not in knowledge["local_url_hints"]


def test_record_package_url_hint_ignores_foreign_domain(tmp_path, monkeypatch):
    """Package URL hints from a different service domain should be ignored."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")

    ks.record_package_url_hint(
        "google_flights",
        source_url="https://www.skyscanner.com/flights",  # Foreign service domain
        user_id="user@example.com",
    )

    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")
    assert "https://www.skyscanner.com/flights" not in knowledge["local_package_url_hints"]


def test_purge_url_hints_removes_matching_entries(tmp_path, monkeypatch):
    """Purge should remove matching bad URL hints from local and global maps."""
    monkeypatch.setattr(ks, "STORE_PATH", tmp_path / "knowledge_store.json")
    plan = [{"action": "wait", "selector": ["body"]}]

    ks.record_success(
        "google_flights",
        plan,
        source_url="https://www.google.com/travel/packages/ana/",
        user_id="user@example.com",
    )
    stats = ks.purge_url_hints(
        site_key="google_flights",
        user_id="user@example.com",
        patterns=["google.com/travel/packages/ana"],
    )
    knowledge = ks.get_knowledge("google_flights", user_id="user@example.com")

    assert stats["url_entries_removed"] >= 1
    assert "https://www.google.com/travel/packages/ana/" not in knowledge["local_url_hints"]
