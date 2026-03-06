"""Tests for KB loader (utils/kb.py)."""

import json
import tempfile
from pathlib import Path

import pytest

from utils.kb import (
    DocRef,
    KBIndex,
    format_docs_hint,
    get_docs_for_reason,
    get_docs_for_topic,
    get_entrypoints,
    get_kb,
    load_kb_index,
    search_topics,
)


@pytest.fixture
def sample_kb_data():
    """Sample KB index data structure."""
    return {
        "version": "1.0",
        "last_updated": "2026-02-21",
        "entrypoints": [
            {
                "topic": "doctrine",
                "description": "Core design principles",
                "files": [
                    {
                        "path": "kb/doctrine.md",
                        "priority": 1,
                        "tags": ["principles", "agentic"]
                    }
                ]
            },
            {
                "topic": "runtime_playbook",
                "description": "Troubleshooting guide",
                "files": [
                    {
                        "path": "kb/runtime_playbook.md",
                        "priority": 1,
                        "tags": ["troubleshooting"]
                    }
                ]
            }
        ],
        "topics": [
            {
                "name": "date_picker",
                "description": "Date picker pattern",
                "files": [
                    {
                        "path": "kb/patterns/date_picker.md",
                        "priority": 1,
                        "tags": ["date-picker", "actionbudget"]
                    },
                    {
                        "path": "kb/contracts/scenario_runner.md",
                        "priority": 2,
                        "tags": ["scenario-runner"]
                    }
                ]
            },
            {
                "name": "budgets_timeouts",
                "description": "ActionBudget and timeout strategies",
                "files": [
                    {
                        "path": "kb/contracts/budgets_timeouts.md",
                        "priority": 1,
                        "tags": ["actionbudget", "timeout"]
                    }
                ]
            },
            {
                "name": "selectors",
                "description": "Selector strategies",
                "files": [
                    {
                        "path": "kb/patterns/selectors.md",
                        "priority": 1,
                        "tags": ["selectors"]
                    }
                ]
            }
        ],
        "configuration": [
            {
                "name": "config_guide",
                "description": "Configuration reference",
                "files": [
                    {
                        "path": "CONFIG.md",
                        "priority": 1,
                        "tags": ["config"]
                    }
                ]
            }
        ],
        "symptom_index": [
            {
                "symptom": "date_picker_failed",
                "docs": [
                    "kb/runtime_playbook.md#symptom-date-picker-failed",
                    "kb/patterns/date_picker.md"
                ]
            },
            {
                "symptom": "timeout_error",
                "docs": [
                    "kb/runtime_playbook.md#symptom-timeout-error",
                    "kb/contracts/budgets_timeouts.md"
                ]
            }
        ]
    }


@pytest.fixture
def temp_kb_yaml(tmp_path, sample_kb_data):
    """Create temporary kb_index.yaml file."""
    docs_kb_dir = tmp_path / "docs" / "kb"
    docs_kb_dir.mkdir(parents=True)

    kb_file = docs_kb_dir / "kb_index.yaml"

    # Write as YAML-like format (simple dict format works for safe_load)
    try:
        import yaml
        with open(kb_file, 'w') as f:
            yaml.dump(sample_kb_data, f)
    except ImportError:
        # Fallback: create JSON version
        json_file = docs_kb_dir / "kb_index.json"
        with open(json_file, 'w') as f:
            json.dump(sample_kb_data, f)

    return tmp_path


@pytest.fixture
def temp_kb_json(tmp_path, sample_kb_data):
    """Create temporary kb_index.json file (no YAML)."""
    docs_kb_dir = tmp_path / "docs" / "kb"
    docs_kb_dir.mkdir(parents=True)

    kb_file = docs_kb_dir / "kb_index.json"
    with open(kb_file, 'w') as f:
        json.dump(sample_kb_data, f)

    return tmp_path


def test_load_kb_index_from_yaml(temp_kb_yaml):
    """Test loading KB index from YAML file."""
    kb = load_kb_index(str(temp_kb_yaml))

    assert kb.version == "1.0"
    assert len(kb.entrypoints) == 2
    assert "doctrine" in kb.topics
    assert "date_picker" in kb.topics
    assert "budgets_timeouts" in kb.topics


def test_load_kb_index_from_json(temp_kb_json):
    """Test loading KB index from JSON fallback."""
    kb = load_kb_index(str(temp_kb_json))

    assert kb.version == "1.0"
    assert len(kb.entrypoints) == 2
    assert "doctrine" in kb.topics


def test_load_kb_index_missing_files(tmp_path):
    """Test loading KB index with missing files returns empty index."""
    kb = load_kb_index(str(tmp_path))

    assert kb.version == "0"
    assert len(kb.entrypoints) == 0
    assert len(kb.topics) == 0


def test_entrypoints_parsing(temp_kb_yaml):
    """Test entrypoint parsing and priority sorting."""
    kb = load_kb_index(str(temp_kb_yaml))
    entrypoints = get_entrypoints(kb)

    assert len(entrypoints) == 2
    assert all(ep.kind == "entrypoint" for ep in entrypoints)
    assert entrypoints[0].topic == "doctrine"
    assert entrypoints[0].path == "kb/doctrine.md"
    assert entrypoints[0].priority == 1


def test_topics_parsing_and_priority(temp_kb_yaml):
    """Test topic parsing with priority ordering."""
    kb = load_kb_index(str(temp_kb_yaml))

    date_picker_docs = get_docs_for_topic(kb, "date_picker")
    assert len(date_picker_docs) == 2

    # Check priority ordering (lower number = higher priority)
    assert date_picker_docs[0].priority == 1
    assert date_picker_docs[0].path == "kb/patterns/date_picker.md"
    assert date_picker_docs[1].priority == 2
    assert date_picker_docs[1].path == "kb/contracts/scenario_runner.md"


def test_get_docs_for_topic_case_insensitive(temp_kb_yaml):
    """Test case-insensitive topic lookup."""
    kb = load_kb_index(str(temp_kb_yaml))

    # All these should work
    assert len(get_docs_for_topic(kb, "date_picker")) == 2
    assert len(get_docs_for_topic(kb, "Date_Picker")) == 2
    assert len(get_docs_for_topic(kb, "DATE_PICKER")) == 2
    assert len(get_docs_for_topic(kb, "DaTe_PiCkEr")) == 2


def test_get_docs_for_topic_not_found(temp_kb_yaml):
    """Test topic lookup for nonexistent topic."""
    kb = load_kb_index(str(temp_kb_yaml))

    docs = get_docs_for_topic(kb, "nonexistent_topic")
    assert docs == []


def test_search_topics_substring_match(temp_kb_yaml):
    """Test topic search with substring matching."""
    kb = load_kb_index(str(temp_kb_yaml))

    # Search for "picker" should find "date_picker"
    results = search_topics(kb, "picker")
    assert "date_picker" in results

    # Search for "budget" should find "budgets_timeouts"
    results = search_topics(kb, "budget")
    assert "budgets_timeouts" in results

    # Case insensitive
    results = search_topics(kb, "PICKER")
    assert "date_picker" in results


def test_search_topics_no_matches(temp_kb_yaml):
    """Test topic search with no matches."""
    kb = load_kb_index(str(temp_kb_yaml))

    results = search_topics(kb, "xyz123")
    assert results == []


def test_search_topics_sorted(temp_kb_yaml):
    """Test topic search results are sorted."""
    kb = load_kb_index(str(temp_kb_yaml))

    # Search for "e" should match multiple topics
    results = search_topics(kb, "e")
    assert results == sorted(results)


def test_get_docs_for_reason_basic(temp_kb_yaml):
    """Test getting docs for a failure reason."""
    kb = load_kb_index(str(temp_kb_yaml))

    # calendar_not_open -> ["date_picker"]
    docs = get_docs_for_reason(kb, "calendar_not_open")
    assert len(docs) > 0
    assert any(doc.topic == "date_picker" for doc in docs)


def test_get_docs_for_reason_multiple_topics(temp_kb_yaml):
    """Test reason mapping to multiple topics."""
    kb = load_kb_index(str(temp_kb_yaml))

    # month_nav_exhausted -> ["date_picker", "budgets_timeouts"]
    docs = get_docs_for_reason(kb, "month_nav_exhausted")

    topics = {doc.topic for doc in docs}
    assert "date_picker" in topics
    assert "budgets_timeouts" in topics


def test_get_docs_for_reason_deduplication(temp_kb_yaml):
    """Test deduplication when same doc appears in multiple topics."""
    kb = load_kb_index(str(temp_kb_yaml))

    # If a doc appears in multiple topics, it should only appear once
    docs = get_docs_for_reason(kb, "verify_mismatch")

    # Check no duplicate paths
    paths = [doc.path for doc in docs]
    assert len(paths) == len(set(paths))


def test_get_docs_for_reason_priority_ordering(temp_kb_yaml):
    """Test docs for reason are sorted by priority."""
    kb = load_kb_index(str(temp_kb_yaml))

    docs = get_docs_for_reason(kb, "month_nav_exhausted")

    # Should be sorted by priority (lower = higher)
    priorities = [doc.priority for doc in docs]
    assert priorities == sorted(priorities)


def test_get_docs_for_reason_unknown(temp_kb_yaml):
    """Test unknown reason code returns empty list."""
    kb = load_kb_index(str(temp_kb_yaml))

    docs = get_docs_for_reason(kb, "unknown_reason_xyz")
    assert docs == []


def test_get_docs_for_reason_symptom_map(temp_kb_yaml):
    """Test reason lookup using symptom_map from YAML."""
    kb = load_kb_index(str(temp_kb_yaml))

    # timeout_error should map via symptom_index
    docs = get_docs_for_reason(kb, "timeout_error")

    # Should find budgets_timeouts from both REASON_TO_TOPICS and symptom_map
    topics = {doc.topic for doc in docs}
    assert "budgets_timeouts" in topics


def test_cache_behavior(temp_kb_yaml):
    """Test KB caching works correctly."""
    # Clear any existing cache
    import utils.kb
    utils.kb._kb_cache = None
    utils.kb._kb_cache_root = None

    # First call loads
    kb1 = get_kb(str(temp_kb_yaml))
    assert kb1.version == "1.0"

    # Second call uses cache (same instance)
    kb2 = get_kb(str(temp_kb_yaml))
    assert kb2 is kb1

    # Force reload gets new instance
    kb3 = get_kb(str(temp_kb_yaml), force_reload=True)
    assert kb3 is not kb1
    assert kb3.version == kb1.version


def test_format_docs_hint_basic():
    """Test formatting docs hint string."""
    docs = [
        DocRef(path="kb/patterns/date_picker.md", priority=1, topic="date_picker", kind="topic"),
        DocRef(path="kb/contracts/budgets_timeouts.md", priority=1, topic="budgets_timeouts", kind="topic"),
    ]

    hint = format_docs_hint(docs)
    assert "date_picker -> kb/patterns/date_picker.md" in hint
    assert "budgets_timeouts -> kb/contracts/budgets_timeouts.md" in hint


def test_format_docs_hint_empty():
    """Test formatting empty docs list."""
    hint = format_docs_hint([])
    assert hint == ""


def test_format_docs_hint_max_items():
    """Test max_items limiting."""
    docs = [
        DocRef(path=f"kb/doc{i}.md", priority=i, topic=f"topic{i}", kind="topic")
        for i in range(10)
    ]

    hint = format_docs_hint(docs, max_items=3)
    assert "topic0" in hint
    assert "topic1" in hint
    assert "topic2" in hint
    assert "topic3" not in hint
    assert "(+7 more)" in hint


def test_doc_ref_equality():
    """Test DocRef equality based on path."""
    doc1 = DocRef(path="kb/test.md", priority=1, topic="test", kind="topic")
    doc2 = DocRef(path="kb/test.md", priority=2, topic="other", kind="topic")
    doc3 = DocRef(path="kb/other.md", priority=1, topic="test", kind="topic")

    # Same path = equal
    assert doc1 == doc2
    assert doc1 != doc3

    # Hashable
    assert hash(doc1) == hash(doc2)
    assert hash(doc1) != hash(doc3)


def test_doc_ref_deduplication():
    """Test DocRef deduplication using dict.fromkeys."""
    docs = [
        DocRef(path="kb/test.md", priority=1, topic="test1", kind="topic"),
        DocRef(path="kb/test.md", priority=2, topic="test2", kind="topic"),
        DocRef(path="kb/other.md", priority=1, topic="test3", kind="topic"),
    ]

    unique = list(dict.fromkeys(docs))
    assert len(unique) == 2
    assert unique[0].path == "kb/test.md"
    assert unique[1].path == "kb/other.md"


def test_configuration_topics(temp_kb_yaml):
    """Test configuration topics are parsed correctly."""
    kb = load_kb_index(str(temp_kb_yaml))

    config_docs = get_docs_for_topic(kb, "config_guide")
    assert len(config_docs) == 1
    assert config_docs[0].path == "CONFIG.md"
    assert config_docs[0].kind == "configuration"


def test_kb_index_auto_detect_root(temp_kb_yaml):
    """Test auto-detection of repo root."""
    # This is harder to test reliably, but we can check it doesn't crash
    # when called without root_dir
    # Note: May return empty index if auto-detect fails
    kb = load_kb_index(None)
    assert isinstance(kb, KBIndex)
    assert isinstance(kb.version, str)


def test_logging_utils_integration():
    """Test integration with core.scenario.logging_utils."""
    from core.scenario.logging_utils import get_docs_hint_for_reason

    # Should work with real KB
    hint = get_docs_hint_for_reason("calendar_not_open")

    # May be None if KB can't load, or a string if successful
    assert hint is None or isinstance(hint, str)

    # Should never raise even with invalid reason
    hint = get_docs_hint_for_reason("invalid_xyz_reason")
    assert hint is None or isinstance(hint, str)

    # Should never raise even with weird input
    hint = get_docs_hint_for_reason("")
    assert hint is None or isinstance(hint, str)
