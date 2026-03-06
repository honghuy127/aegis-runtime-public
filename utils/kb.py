"""Runtime KB loader for authoritative documentation retrieval.

Loads docs/kb/kb_index.yaml at runtime to provide agents with access to
authoritative documentation based on failure reasons, topics, and symptoms.

Design principles:
- Deterministic: Same input -> same output
- Graceful degradation: Missing docs -> empty results, not exceptions
- Lightweight: No external dependencies beyond stdlib
- Cached: Load once per process unless force_reload=True
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Module-level cache
_kb_cache: Optional["KBIndex"] = None
_kb_cache_root: Optional[str] = None


@dataclass
class KBPromptBudget:
    """Hard budget limits for KB content going into LLM prompts.

    Prevents accidental context bloat by enforcing deterministic truncation.
    All caps are hard limits: no exceptions except explicit debug override.
    """
    max_chars: int = 12000            # Max characters per KB chunk
    max_items: int = 80               # Max list items per KB entry
    max_entries: int = 8              # Max KB entries per prompt
    max_depth: int = 4                # Max recursion depth in dicts/lists
    max_files_referenced: int = 8     # Max distinct KB files per run

    def __post_init__(self):
        """Enforce safety minimums."""
        if self.max_chars < 500:
            self.max_chars = 500  # Must allow at least 500 chars
        if self.max_items < 10:
            self.max_items = 10   # Must allow at least 10 items


def render_entry_for_prompt(
    entry: Dict,
    budget: Optional[KBPromptBudget] = None,
    key_prefix: str = "",
) -> str:
    """Render KB entry for LLM prompt with budget enforcement.

    Deterministically truncates large entries to prevent prompt bloat.
    Uses concise format (drops low-priority keys, limits lists/depth).

    Args:
        entry: KB entry data (dict)
        budget: KBPromptBudget with max_chars, max_items, etc.
                defaults to KBPromptBudget() if None
        key_prefix: For logging context (e.g., "evidence_fields[calendar.opened]")

    Returns:
        Rendered YAML string bounded by budget.
        Includes [TRUNCATED] marker if content was trimmed.

    Example:
        >>> entry = {"id": "INV-001", "statement": "...", "refs": [...]}
        >>> budget = KBPromptBudget(max_chars=1000)
        >>> output = render_entry_for_prompt(entry, budget)
        >>> assert len(output) <= 1000
    """
    import yaml

    if budget is None:
        budget = KBPromptBudget()  # Use defaults

    # Step 1: Create concise copy, prioritize important keys
    concise = {}
    priority_keys = [
        "id", "name", "type", "statement", "description", "summary",
        "key", "value", "reason", "required", "optional", "evidence",
        "action", "recovery", "status"
    ]

    # Add priority keys first
    for key in priority_keys:
        if key in entry:
            concise[key] = entry[key]

    # Add remaining keys (except internals)
    for key, val in entry.items():
        if key not in concise and not key.startswith("_"):
            concise[key] = val

    # Step 2: Recursively truncate lists and limit depth
    def truncate_recursive(obj: any, depth: int = 0) -> any:
        """Truncate lists and limit recursion depth."""
        if depth > budget.max_depth:
            return "[TRUNCATED: max_depth exceeded]"

        if isinstance(obj, dict):
            return {k: truncate_recursive(v, depth + 1) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            if len(obj) > budget.max_items:
                truncated = list(obj[:budget.max_items])
                truncated.append(f"[... {len(obj) - budget.max_items} more items]")
                return truncated
            return [truncate_recursive(item, depth + 1) for item in obj]
        return obj

    concise = truncate_recursive(concise)

    # Step 3: Dump to YAML
    try:
        yaml_output = yaml.dump(concise, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.warning(f"KB render failed: {e}, falling back to str()")
        yaml_output = str(concise)

    # Step 4: Enforce character budget
    if len(yaml_output) > budget.max_chars:
        truncation_marker = (
            f"\n\n[TRUNCATED: reason=budget_exceeded "
            f"cap={budget.max_chars} actual={len(yaml_output)}]"
        )
        # Reserve space for marker
        available = budget.max_chars - len(truncation_marker)
        if available > 100:
            yaml_output = yaml_output[:available].rstrip() + truncation_marker
        else:
            yaml_output = yaml_output[:budget.max_chars]

        # Log truncation event
        logger.warning(
            f"kb.prompt_budget.truncate: {key_prefix} "
            f"(original={len(yaml_output)} chars, limit={budget.max_chars})"
        )

    return yaml_output


def load_kb_budget_from_config(debug_mode: bool = False) -> KBPromptBudget:
    """Load KB budget limits from thresholds.yaml configuration.

    Args:
        debug_mode: If True, load kb_prompt_budget_debug (relaxed limits).
                   If False, load kb_prompt_budget (default limits).

    Returns:
        KBPromptBudget with values from config, or defaults if not found.

    Raises:
        No exceptions; falls back to defaults gracefully.
    """
    import yaml

    try:
        config_path = _find_repo_root() / "configs" / "thresholds.yaml"
        if not config_path.exists():
            logger.debug(f"thresholds.yaml not found, using KB budget defaults")
            return KBPromptBudget()

        with open(config_path) as f:
            thresholds = yaml.safe_load(f) or {}

        # Select the appropriate profile
        profile_key = "kb_prompt_budget_debug" if debug_mode else "kb_prompt_budget"
        budget_config = thresholds.get(profile_key, {})

        if not budget_config:
            logger.debug(f"No {profile_key} in config, using KB budget defaults")
            return KBPromptBudget()

        # Create KBPromptBudget from config (filter to valid fields)
        valid_fields = {
            "max_chars", "max_items", "max_entries", "max_depth", "max_files_referenced"
        }
        budget_kwargs = {k: v for k, v in budget_config.items() if k in valid_fields}
        return KBPromptBudget(**budget_kwargs)

    except Exception as e:
        logger.debug(f"Failed to load KB budget from config: {e}, using defaults")
        return KBPromptBudget()


@dataclass
class DocRef:
    """Reference to a documentation file with metadata."""

    path: str
    priority: int
    topic: str
    kind: str  # "entrypoint" | "topic" | "configuration"

    def __hash__(self):
        """Make DocRef hashable for deduplication."""
        return hash(self.path)

    def __eq__(self, other):
        """Equality based on path for deduplication."""
        if not isinstance(other, DocRef):
            return False
        return self.path == other.path


@dataclass
class KBIndex:
    """Parsed knowledge base index with all topics and entrypoints."""

    version: str
    entrypoints: List[DocRef] = field(default_factory=list)
    topics: Dict[str, List[DocRef]] = field(default_factory=dict)
    symptom_map: Dict[str, List[str]] = field(default_factory=dict)  # symptom -> topic names

    def __post_init__(self):
        """Ensure topics dict has lowercase keys for case-insensitive lookup."""
        # Normalize topic keys to lowercase while preserving original case in DocRef.topic
        normalized = {}
        for key, docs in self.topics.items():
            normalized[key.lower()] = docs
        self.topics = normalized


# Reason -> topic mapping
# This maps StepResult.reason codes to relevant documentation topics
REASON_TO_TOPICS = {
    "calendar_not_open": ["date_picker"],
    "month_nav_exhausted": ["date_picker", "budgets_timeouts"],
    "budget_hit": ["budgets_timeouts", "evidence"],
    "verify_mismatch": ["date_picker", "selectors"],
    "action_deadline_exceeded_before_click": ["budgets_timeouts", "selectors"],
    "timeout_error": ["budgets_timeouts", "scenario_runner"],
    "selector_not_found": ["selectors", "budgets_timeouts"],
    "iata_mismatch": ["combobox_commit", "evidence"],
    "price_extraction_failed": ["plugins", "evidence"],
    "scope_conflict": ["plugins", "scenario_runner"],
    "selector_spam": ["selectors", "budgets_timeouts"],
    "infinite_recovery": ["scenario_runner", "budgets_timeouts"],
}


def _find_repo_root() -> Path:
    """Find repository root by looking for marker files."""
    current = Path(__file__).resolve()

    # Walk up looking for characteristic files
    for parent in [current.parent] + list(current.parents):
        if (parent / "docs" / "kb" / "kb_index.yaml").exists():
            return parent
        if (parent / ".git").exists():
            return parent

    # Fallback to parent of utils/
    return current.parent.parent


def load_kb_index(root_dir: Optional[str] = None) -> KBIndex:
    """Load KB index from YAML or JSON fallback.

    Args:
        root_dir: Repository root directory. If None, auto-detect.

    Returns:
        KBIndex with parsed documentation structure.
        Returns empty KBIndex (version="0") if files not found.
    """
    if root_dir is None:
        root_path = _find_repo_root()
    else:
        root_path = Path(root_dir)

    yaml_path = root_path / "docs" / "kb" / "kb_index.yaml"
    json_path = root_path / "docs" / "kb" / "kb_index.json"

    # Try YAML first
    if yaml_path.exists():
        try:
            import yaml
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            logger.debug(f"Loaded KB index from {yaml_path}")
            return _parse_kb_index(data, str(root_path))
        except ImportError:
            logger.warning("PyYAML not available, trying JSON fallback")
        except Exception as e:
            logger.warning(f"Failed to load KB index from YAML: {e}")

    # Try JSON fallback
    if json_path.exists():
        try:
            with open(json_path) as f:
                data = json.load(f)
            logger.debug(f"Loaded KB index from {json_path}")
            return _parse_kb_index(data, str(root_path))
        except Exception as e:
            logger.warning(f"Failed to load KB index from JSON: {e}")

    # Return empty index
    logger.warning("No KB index found, returning empty index")
    return KBIndex(version="0")


def _parse_kb_index(data: dict, root_path: str) -> KBIndex:
    """Parse KB index data structure into KBIndex."""
    version = str(data.get("version", "0"))
    entrypoints: List[DocRef] = []
    topics: Dict[str, List[DocRef]] = {}
    symptom_map: Dict[str, List[str]] = {}

    # Parse entrypoints
    for entry in data.get("entrypoints", []):
        topic_name = entry.get("topic", "unknown")
        for file_entry in entry.get("files", []):
            doc = DocRef(
                path=file_entry["path"],
                priority=file_entry.get("priority", 1),
                topic=topic_name,
                kind="entrypoint"
            )
            entrypoints.append(doc)

            # Also add to topics dict
            if topic_name.lower() not in topics:
                topics[topic_name.lower()] = []
            topics[topic_name.lower()].append(doc)

    # Parse regular topics
    for topic_entry in data.get("topics", []):
        topic_name = topic_entry.get("name", "unknown")
        topic_docs = []

        for file_entry in topic_entry.get("files", []):
            doc = DocRef(
                path=file_entry["path"],
                priority=file_entry.get("priority", 1),
                topic=topic_name,
                kind="topic"
            )
            topic_docs.append(doc)

        # Sort by priority (lower number = higher priority)
        topic_docs.sort(key=lambda d: d.priority)
        topics[topic_name.lower()] = topic_docs

    # Parse configuration topics
    for config_entry in data.get("configuration", []):
        topic_name = config_entry.get("name", "unknown")
        config_docs = []

        for file_entry in config_entry.get("files", []):
            doc = DocRef(
                path=file_entry["path"],
                priority=file_entry.get("priority", 1),
                topic=topic_name,
                kind="configuration"
            )
            config_docs.append(doc)

        config_docs.sort(key=lambda d: d.priority)
        topics[topic_name.lower()] = config_docs

    # Parse symptom index
    for symptom_entry in data.get("symptom_index", []):
        symptom = symptom_entry.get("symptom", "")
        if not symptom:
            continue

        # Extract topic names from doc paths
        docs = symptom_entry.get("docs", [])
        if isinstance(docs, str):
            docs = [docs]

        topic_names = []
        for doc_path in docs:
            # Extract topic from path: kb/patterns/date_picker.md -> date_picker
            if "patterns/" in doc_path:
                topic_names.append(doc_path.split("patterns/")[1].split(".md")[0])
            elif "contracts/" in doc_path:
                topic_names.append(doc_path.split("contracts/")[1].split(".md")[0])

        if topic_names:
            symptom_map[symptom] = topic_names

    # Sort entrypoints by priority
    entrypoints.sort(key=lambda d: d.priority)

    return KBIndex(
        version=version,
        entrypoints=entrypoints,
        topics=topics,
        symptom_map=symptom_map
    )


def get_kb(root_dir: Optional[str] = None, force_reload: bool = False) -> KBIndex:
    """Get cached KB index, loading if necessary.

    Args:
        root_dir: Repository root directory. If None, auto-detect.
        force_reload: If True, reload from disk even if cached.

    Returns:
        Cached or newly loaded KBIndex.
    """
    global _kb_cache, _kb_cache_root

    # Check if we need to reload
    if force_reload or _kb_cache is None or _kb_cache_root != root_dir:
        _kb_cache = load_kb_index(root_dir)
        _kb_cache_root = root_dir
        logger.debug(f"KB cache updated (version={_kb_cache.version})")

    return _kb_cache


def get_entrypoints(kb: KBIndex) -> List[DocRef]:
    """Get entrypoint documentation references.

    Args:
        kb: KBIndex instance.

    Returns:
        List of entrypoint DocRefs, sorted by priority.
    """
    return kb.entrypoints


def get_docs_for_topic(kb: KBIndex, topic: str) -> List[DocRef]:
    """Get documentation for a specific topic.

    Args:
        kb: KBIndex instance.
        topic: Topic name (case-insensitive).

    Returns:
        List of DocRefs for the topic, sorted by priority.
        Returns empty list if topic not found.
    """
    return kb.topics.get(topic.lower(), [])


def search_topics(kb: KBIndex, query: str) -> List[str]:
    """Search for topics matching query string.

    Args:
        kb: KBIndex instance.
        query: Search query (case-insensitive substring match).

    Returns:
        List of matching topic names, sorted alphabetically.
    """
    query_lower = query.lower()
    matches = [
        topic_name
        for topic_name in kb.topics.keys()
        if query_lower in topic_name
    ]
    return sorted(matches)


def get_docs_for_reason(kb: KBIndex, reason: str) -> List[DocRef]:
    """Get documentation for a failure reason code.

    Maps StepResult.reason to relevant documentation topics.

    Args:
        kb: KBIndex instance.
        reason: Failure reason code (e.g., "calendar_not_open").

    Returns:
        List of relevant DocRefs, deduplicated and sorted by priority.
        Returns empty list if reason not mapped.
    """
    # Get topic names for this reason
    topic_names = REASON_TO_TOPICS.get(reason, [])

    # Also check symptom map
    symptom_topics = kb.symptom_map.get(reason, [])
    topic_names.extend(symptom_topics)

    # Collect all docs
    all_docs: List[DocRef] = []
    for topic_name in topic_names:
        docs = get_docs_for_topic(kb, topic_name)
        all_docs.extend(docs)

    # Deduplicate by path (DocRef.__eq__ and __hash__ use path)
    unique_docs = list(dict.fromkeys(all_docs))

    # Sort by priority
    unique_docs.sort(key=lambda d: d.priority)

    return unique_docs


def format_docs_hint(docs: List[DocRef], max_items: int = 5) -> str:
    """Format a short documentation hint string.

    Args:
        docs: List of DocRef instances.
        max_items: Maximum number of items to include.

    Returns:
        Formatted hint string like:
        "Docs: date_picker -> docs/kb/30_patterns/date_picker.md"
    """
    if not docs:
        return ""

    items = docs[:max_items]
    parts = [f"{doc.topic} -> {doc.path}" for doc in items]

    hint = "Docs: " + "; ".join(parts)

    if len(docs) > max_items:
        hint += f" (+{len(docs) - max_items} more)"

    return hint
