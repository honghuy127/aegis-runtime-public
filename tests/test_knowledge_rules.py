"""Tests for configurable knowledge-rule loading."""

from utils import knowledge_rules as kr


def test_load_knowledge_rules_defaults_contains_core_tokens():
    """Default rules should expose key token groups used by knowledge_store."""
    rules = kr.load_knowledge_rules(force_reload=True)
    list_rules = rules["list_rules"]
    reason_rules = rules["failure_reason_rules"]

    assert "国内" in list_rules["domestic_tokens"]
    assert "international" in list_rules["international_tokens"]
    assert "search" in list_rules["search_submit_tokens"]
    assert "where to" in [token.lower() for token in list_rules["placeholder_dest_tokens"]]
    assert "search" in [token.lower() for token in list_rules["action_search_tokens"]]
    assert "fill_role_origin_tokens" in list_rules
    assert "hidden_input" in reason_rules
    assert "timeout" in reason_rules


def test_load_knowledge_rules_overrides_from_file(tmp_path, monkeypatch):
    """Config file values should override defaults for matching keys."""
    cfg = tmp_path / "knowledge_rules.yaml"
    cfg.write_text(
        "\n".join(
            [
                "domestic_tokens: domestic,国内,jp_domestic",
                "fill_role_origin_tokens: origin,from,駅",
                "action_search_tokens: Search,Find",
                "failure_reason_timeout: timeout,deadline exceeded",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kr, "_RULES_PATH", cfg)

    rules = kr.load_knowledge_rules(force_reload=True)
    assert "jp_domestic" in rules["list_rules"]["domestic_tokens"]
    assert "駅" in rules["list_rules"]["fill_role_origin_tokens"]
    assert rules["list_rules"]["action_search_tokens"] == ["Search", "Find"]
    assert rules["failure_reason_rules"]["timeout"] == ["timeout", "deadline exceeded"]


def test_get_tokens_group_key_mapping():
    """Generic grouped getter should resolve configured token list keys."""
    assert kr.get_tokens("actions", "search")
    assert kr.get_tokens("placeholders", "dest")
    assert kr.get_tokens("tabs", "flights")
    assert kr.get_tokens("page", "hotel")
    assert kr.get_tokens("hints", "auth")
    assert kr.get_tokens("google", "non_flight_map")
    assert kr.get_tokens("unknown", "x") == []


def test_load_knowledge_rules_parses_nested_tokens(tmp_path, monkeypatch):
    """Nested `tokens:` groups should map into semantic get_tokens lookups."""
    cfg = tmp_path / "knowledge_rules.yaml"
    cfg.write_text(
        "\n".join(
            [
                "tokens:",
                "  page:",
                "    hotel: [\"hotel\", \"宿\"]",
                "  hints:",
                "    auth:",
                "      - \"login\"",
                "      - \"ログイン\"",
                "  google:",
                "    bundle_word: [\"package\", \"パッケージ\"]",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kr, "_RULES_PATH", cfg)

    rules = kr.load_knowledge_rules(force_reload=True)
    assert rules["list_rules"]["page_hotel_tokens"] == ["hotel", "宿"]
    assert "ログイン" in rules["list_rules"]["hints_auth_tokens"]
    assert "package" in rules["list_rules"]["google_bundle_word_tokens"]
