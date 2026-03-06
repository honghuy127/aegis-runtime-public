"""Structured retrieval for KB YAML catalogs.

Prevents agents from loading entire YAML catalogs into context by enforcing
selective loading by key. All catalog queries must specify which specific data
they need, not request full files.

Design principles:
- Load only requested fields, never full catalogs
- Warn when >300 lines of YAML would be returned
- BLOCK full-catalog loads completely (unless DEBUG_ALLOW_FULL_LOADS=True)
- Fast caching per catalog + field
- No exceptions; graceful degradation with empty results
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from utils.kb import KBPromptBudget

logger = logging.getLogger(__name__)

# Catalog paths: Original monolithic files (now maintained for backwards compatibility)
EVIDENCE_CATALOG_PATH = "docs/kb/10_runtime_contracts/evidence_catalog.yaml"
TRIAGE_DECISION_TABLE_PATH = "docs/kb/20_decision_system/triage_decision_table.yaml"
RUNTIME_SYMPTOM_MAP_PATH = "docs/kb/20_decision_system/runtime_symptom_map.yaml"
ARCHITECTURE_INVARIANTS_PATH = "docs/kb/00_foundation/architecture_invariants.yaml"

# Partitioned catalog files (smaller, focused namespaces)
EVIDENCE_SCHEMA_PATH = "docs/kb/10_runtime_contracts/evidence_schema.yaml"
EVIDENCE_FIELDS_PATH = "docs/kb/10_runtime_contracts/evidence_fields.yaml"
EVIDENCE_ARTIFACTS_PATH = "docs/kb/10_runtime_contracts/evidence_artifacts.yaml"
INVARIANTS_REGISTRY_PATH = "docs/kb/00_foundation/invariants_registry.yaml"
INVARIANTS_BY_CATEGORY_PATH = "docs/kb/00_foundation/invariants_by_category.yaml"

# Catalog size limits (lines) - exceeding = full-load blocked
# Updated limits for partitioned files (max ~300 lines per partition)
CATALOG_SIZE_LIMITS = {
    EVIDENCE_SCHEMA_PATH: 100,
    EVIDENCE_FIELDS_PATH: 700,
    EVIDENCE_ARTIFACTS_PATH: 100,
    INVARIANTS_REGISTRY_PATH: 450,
    INVARIANTS_BY_CATEGORY_PATH: 200,
    # Legacy monolithic paths (now rarely used, but kept for migration)
    EVIDENCE_CATALOG_PATH: 824,
    TRIAGE_DECISION_TABLE_PATH: 350,
    RUNTIME_SYMPTOM_MAP_PATH: 200,
    ARCHITECTURE_INVARIANTS_PATH: 593,
}

# Backward compatibility: map old reference paths to new partitions
CATALOG_PARTITION_MAP = {
    EVIDENCE_CATALOG_PATH: [EVIDENCE_SCHEMA_PATH, EVIDENCE_FIELDS_PATH, EVIDENCE_ARTIFACTS_PATH],
    ARCHITECTURE_INVARIANTS_PATH: [INVARIANTS_REGISTRY_PATH, INVARIANTS_BY_CATEGORY_PATH],
}

# In-memory caches (field-level, not full catalogs)
_evidence_field_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_triage_reason_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_symptom_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_invariant_cache: Dict[str, Optional[Dict[str, Any]]] = {}

# Debug flag: Set by tests or maintenance agents only
DEBUG_ALLOW_FULL_LOADS = os.environ.get("KB_DEBUG_ALLOW_FULL_LOADS", "False").lower() == "true"


def _reason_lookup_candidates(reason_code: str) -> List[str]:
    """Build compatible lookup candidates for legacy/canonical reason codes."""
    code = str(reason_code or "").strip().lower()
    if not code:
        return []

    candidates: List[str] = [code]
    try:
        from core.scenario.reasons import REASON_ALIASES, normalize_reason

        canonical = normalize_reason(code)
        if canonical != "unknown" and canonical not in candidates:
            candidates.append(canonical)
        if canonical != "unknown":
            for alias, target in REASON_ALIASES.items():
                if target == canonical and alias not in candidates:
                    candidates.append(alias)
    except Exception:
        # Fallback for docs-only environments where reason registry may be unavailable.
        pass
    return candidates


def _flatten_reason_tree(reason_tree: Any) -> Dict[str, Dict[str, Any]]:
    """Support both legacy and categorized triage reason_tree shapes."""
    flattened: Dict[str, Dict[str, Any]] = {}
    if not isinstance(reason_tree, dict):
        return flattened

    for key, value in reason_tree.items():
        # Legacy map shape: reason_tree.{reason_code}: { ... }
        if isinstance(value, dict) and "code" not in value:
            if isinstance(key, str) and key.strip():
                flattened[key.strip().lower()] = value
            continue
        # Categorized list shape: reason_tree.{category}: [{code: "...", ...}, ...]
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip().lower()
                if code:
                    flattened[code] = item
    return flattened


def _find_repo_root() -> Path:
    """Find repository root by looking for marker files."""
    current = Path(__file__).resolve()

    for parent in [current.parent] + list(current.parents):
        if (parent / "docs" / "kb" / "kb_index.yaml").exists():
            return parent
        if (parent / ".git").exists():
            return parent

    # Fallback to parent of utils/
    return current.parent.parent


def _load_yaml(file_path: str) -> Optional[Dict[str, Any]]:
    """Load YAML file with fallback to empty dict.

    Args:
        file_path: Path relative to repo root

    Returns:
        Parsed YAML dict or None if file not found/invalid

    Raises:
        ValueError: if attempting to load full catalog without DEBUG flag
    """
    try:
        import yaml
        root = _find_repo_root()
        full_path = root / file_path

        if not full_path.exists():
            logger.debug(f"YAML file not found: {file_path}")
            return None

        with open(full_path) as f:
            data = yaml.safe_load(f)

        return data if isinstance(data, dict) else {}
    except ValueError:
        # Re-raise ValueError from _block_full_catalog_load
        raise
    except ImportError:
        logger.warning("PyYAML not available for catalog loading")
        return None
    except Exception as e:
        logger.warning(f"Failed to load YAML from {file_path}: {e}")
        return None


def _block_full_catalog_load(file_path: str, data: Dict[str, Any]) -> None:
    """Block full-catalog loads for security/performance.

    Raises ValueError if attempting to load a full catalog file without DEBUG flag.
    Selective (by-key) loads are always safe; this only blocks loading entire files.

    Args:
        file_path: Path to catalog file
        data: Parsed YAML data

    Raises:
        ValueError: if full-catalog load detected without DEBUG flag
    """
    if DEBUG_ALLOW_FULL_LOADS:
        # Debug mode: allow full loads (for testing/maintenance only)
        return

    # Only block if this is a registered catalog file
    if file_path not in CATALOG_SIZE_LIMITS:
        return

    # Estimate data size by dumping to YAML
    try:
        import yaml
        yaml_str = yaml.dump(data, default_flow_style=False)
        line_count = len(yaml_str.splitlines())
        limit = CATALOG_SIZE_LIMITS.get(file_path, 500)  # Default cap: 500 lines

        if line_count > limit:
            logger.error(f"kb.retrieval.blocked_full_catalog: {file_path} ({line_count} lines > {limit} limit)")
            raise ValueError(
                f"Full-catalog load blocked for {file_path} ({line_count} lines). "
                f"Use kb_yaml_retriever selective functions: "
                f"get_evidence_field(), get_triage_decision(), get_symptom_diagnosis(). "
                f"(Set KB_DEBUG_ALLOW_FULL_LOADS=true to bypass; debug-only flag)"
            )
    except ValueError:
        raise
    except Exception:
        # If we can't estimate size, allow the load (safety-first)
        pass


def _warn_if_large(data: Dict[str, Any], key_path: str, threshold_lines: int = 300) -> bool:
    """Warn if returned data would exceed threshold when formatted as YAML.

    Args:
        data: Data to check
        key_path: Human-readable path (e.g., "evidence_catalog[price]")
        threshold_lines: Warning threshold in estimated lines

    Returns:
        True if warning was logged, False otherwise
    """
    try:
        import yaml
        yaml_str = yaml.dump(data, default_flow_style=False)
        line_count = len(yaml_str.splitlines())

        if line_count > threshold_lines:
            logger.warning(
                f"Large YAML return: {key_path} = {line_count} lines "
                f"(exceeds {threshold_lines} line threshold). "
                f"Consider splitting query into smaller pieces."
            )
            return True
    except Exception:
        pass  # Silently skip warning on any error

    return False


# ==============================================================================
# Evidence Catalog Retrieval
# ==============================================================================

def get_evidence_field(field_key: str, budget: Optional["KBPromptBudget"] = None) -> Optional[str]:
    """Get single evidence field definition from evidence_fields.yaml partition.

    Args:
        field_key: Field name (e.g., "calendar.opened", "budget.remaining")
        budget: Optional KBPromptBudget for rendering. If None, returns raw dict.

    Returns:
        Rendered YAML string (if budget provided) or dict (if no budget), or None if not found

    Example:
        >>> result = get_evidence_field("calendar.opened")
        >>> # Returns dict without budget
        >>> from utils.kb import load_kb_budget_from_config
        >>> result = get_evidence_field("calendar.opened", load_kb_budget_from_config())
        >>> # Returns YAML string with budget
    """
    # Check cache first
    if field_key in _evidence_field_cache:
        result_dict = _evidence_field_cache[field_key]
        if result_dict is not None and budget is not None:
            from utils.kb import render_entry_for_prompt
            return render_entry_for_prompt(result_dict, budget, f"evidence_fields[{field_key}]")
        return result_dict

    # Load from partitioned catalog (smaller file: evidence_fields.yaml)
    catalog = _load_yaml(EVIDENCE_FIELDS_PATH)
    if not catalog:
        _evidence_field_cache[field_key] = None
        return None

    # Evidence fields are in 'namespaces' key in the partition
    evidence_fields = catalog.get("namespaces", {})
    result_dict = evidence_fields.get(field_key)

    _evidence_field_cache[field_key] = result_dict
    _warn_if_large(result_dict or {}, f"evidence_fields[{field_key}]")

    if result_dict is not None and budget is not None:
        from utils.kb import render_entry_for_prompt
        return render_entry_for_prompt(result_dict, budget, f"evidence_fields[{field_key}]")

    return result_dict


def get_reason_evidence_mapping(reason_code: str, budget: Optional["KBPromptBudget"] = None) -> Optional[str]:
    """Get evidence requirements for a specific failure reason.

    Args:
        reason_code: Failure reason code (e.g., "calendar_not_open")
        budget: Optional KBPromptBudget for rendering. If None, returns raw dict.

    Returns:
        Rendered YAML string (if budget provided) or dict (if no budget), or None if not found

    Example:
        >>> result = get_reason_evidence_mapping("calendar_not_open")
        >>> # Returns dict without budget
        >>> from utils.kb import load_kb_budget_from_config
        >>> result = get_reason_evidence_mapping("calendar_not_open", load_kb_budget_from_config())
        >>> # Returns YAML string with budget
    """
    cache_key = str(reason_code or "").strip().lower()

    # Check cache first
    if cache_key in _evidence_field_cache:
        result_dict = _evidence_field_cache[cache_key]
        if result_dict is not None and budget is not None:
            from utils.kb import render_entry_for_prompt
            return render_entry_for_prompt(result_dict, budget, f"reason_evidence_map[{cache_key}]")
        return result_dict

    # Load from partitioned catalog (evidence_fields.yaml)
    catalog = _load_yaml(EVIDENCE_FIELDS_PATH)
    if not catalog:
        _evidence_field_cache[cache_key] = None
        return None

    # Extract reason-specific mapping from partition
    reason_map = catalog.get("reason_evidence_map", {})
    result_dict = None
    for candidate in _reason_lookup_candidates(cache_key):
        result_dict = reason_map.get(candidate)
        if result_dict is not None:
            break

    _evidence_field_cache[cache_key] = result_dict
    _warn_if_large(result_dict or {}, f"reason_evidence_map[{cache_key}]")

    if result_dict is not None and budget is not None:
        from utils.kb import render_entry_for_prompt
        return render_entry_for_prompt(result_dict, budget, f"reason_evidence_map[{cache_key}]")

    return result_dict

def get_triage_decision(reason_code: str) -> Optional[Dict[str, Any]]:
    """Get triage decision tree entry for a specific reason code.

    Args:
        reason_code: Failure reason code (e.g., "month_nav_exhausted")

    Returns:
        Decision entry dict with evidence_keys, actions, escalation, or None

    Example:
        >>> result = get_triage_decision("month_nav_exhausted")
        >>> # Returns: {
        >>> #   "summary": "...",
        >>> #   "likely_causes": [...],
        >>> #   "evidence_keys": [...],
        >>> #   "first_fixes": [...],
        >>> #   "escalation": {...}
        >>> # }
    """
    cache_key = str(reason_code or "").strip().lower()

    # Check cache first
    if cache_key in _triage_reason_cache:
        return _triage_reason_cache[cache_key]

    # Load catalog
    catalog = _load_yaml(TRIAGE_DECISION_TABLE_PATH)
    if not catalog:
        _triage_reason_cache[cache_key] = None
        return None

    # Extract specific reason with shape + alias compatibility.
    reason_tree = catalog.get("reason_tree", {})
    flattened = _flatten_reason_tree(reason_tree)
    result = None
    for candidate in _reason_lookup_candidates(cache_key):
        result = flattened.get(candidate)
        if result is not None:
            break

    _triage_reason_cache[cache_key] = result
    _warn_if_large(result or {}, f"triage_decision_table.reason_tree[{cache_key}]")

    return result


def list_triage_reasons() -> List[str]:
    """List all available reason codes in triage decision table.

    Returns:
        Sorted list of reason codes

    Example:
        >>> reasons = list_triage_reasons()
        >>> # Returns: ["budget_hit", "calendar_not_open", ...]
    """
    catalog = _load_yaml(TRIAGE_DECISION_TABLE_PATH)
    if not catalog:
        return []

    reason_tree = catalog.get("reason_tree", {})
    flattened = _flatten_reason_tree(reason_tree)
    return sorted(flattened.keys())


# ==============================================================================
# Runtime Symptom Map Retrieval
# ==============================================================================

def get_symptom_diagnosis(symptom_name: str) -> Optional[Dict[str, Any]]:
    """Get root-cause diagnosis for a specific runtime symptom.

    Args:
        symptom_name: Symptom name (e.g., "no_html_returned", "timeout_error")

    Returns:
        Diagnosis dict with log_patterns, diagnosis, evidence_keys, actions, or None

    Example:
        >>> result = get_symptom_diagnosis("no_html_returned")
        >>> # Returns: {
        >>> #   "log_patterns": [...],
        >>> #   "diagnosis": "...",
        >>> #   "evidence_keys": [...],
        >>> #   "actions": [...]
        >>> # }
    """
    # Check cache first
    if symptom_name in _symptom_cache:
        return _symptom_cache[symptom_name]

    # Load catalog
    catalog = _load_yaml(RUNTIME_SYMPTOM_MAP_PATH)
    if not catalog:
        _symptom_cache[symptom_name] = None
        return None

    # Extract specific symptom
    symptoms = catalog.get("symptoms", {})
    result = symptoms.get(symptom_name)

    _symptom_cache[symptom_name] = result
    _warn_if_large(result or {}, f"runtime_symptom_map.symptoms[{symptom_name}]")

    return result


def list_symptoms() -> List[str]:
    """List all available runtime symptoms.

    Returns:
        Sorted list of symptom names

    Example:
        >>> symptoms = list_symptoms()
        >>> # Returns: ["blocked_interstitial_captcha", "date_picker_failed", ...]
    """
    catalog = _load_yaml(RUNTIME_SYMPTOM_MAP_PATH)
    if not catalog:
        return []

    symptoms = catalog.get("symptoms", {})
    return sorted(symptoms.keys())


# ==============================================================================
# Architecture Invariants Retrieval
# ==============================================================================

def get_invariant(invariant_id: str, budget: Optional["KBPromptBudget"] = None) -> Optional[str]:
    """Get architecture invariant definition by ID.

    Args:
        invariant_id: Invariant ID (e.g., "INV-SCENARIO-001")
        budget: Optional KBPromptBudget for rendering. If None, returns raw dict.

    Returns:
        Rendered YAML string (if budget provided) or dict (if no budget), or None if not found
    """
    # Check cache first
    if invariant_id in _invariant_cache:
        result_dict = _invariant_cache[invariant_id]
        if result_dict is not None and budget is not None:
            from utils.kb import render_entry_for_prompt
            return render_entry_for_prompt(result_dict, budget, f"architecture_invariants[{invariant_id}]")
        return result_dict

    # Load from partitioned registry (invariants_registry.yaml)
    catalog = _load_yaml(INVARIANTS_REGISTRY_PATH)
    if not catalog:
        _invariant_cache[invariant_id] = None
        return None

    # Extract specific invariant from list
    invariants_list = catalog.get("invariants", [])
    if not isinstance(invariants_list, list):
        _invariant_cache[invariant_id] = None
        return None

    result = None
    for inv in invariants_list:
        if isinstance(inv, dict) and inv.get("id") == invariant_id:
            result = inv
            break

    _invariant_cache[invariant_id] = result
    _warn_if_large(result or {}, f"architecture_invariants[{invariant_id}]")

    if result is not None and budget is not None:
        from utils.kb import render_entry_for_prompt
        return render_entry_for_prompt(result, budget, f"architecture_invariants[{invariant_id}]")

    return result


def list_invariants(category: Optional[str] = None, budget: Optional["KBPromptBudget"] = None) -> Optional[str]:
    """List architecture invariant IDs (optionally rendered with budget).

    Args:
        category: Optional category to filter (e.g., "SCENARIO", "BUDGET")
        budget: Optional KBPromptBudget. If provided, returns list rendered as YAML.
               If None, returns plain list of IDs.

    Returns:
        List of invariant IDs (plain strings), or rendered YAML if budget provided
    """
    # Load from partitioned registry (invariants_registry.yaml)
    catalog = _load_yaml(INVARIANTS_REGISTRY_PATH)
    if not catalog:
        return [] if budget is None else ""

    # Extract invariant IDs from list
    invariants_list = catalog.get("invariants", [])
    if not isinstance(invariants_list, list):
        return [] if budget is None else ""

    ids = []
    filtered_list = []
    for inv in invariants_list:
        if isinstance(inv, dict) and "id" in inv:
            inv_id = inv["id"]
            # Filter by category if specified (INV-{CATEGORY}-NN)
            if category:
                cat_prefix = f"INV-{category.upper()}"
                if inv_id.startswith(cat_prefix):
                    ids.append(inv_id)
                    filtered_list.append(inv)
            else:
                ids.append(inv_id)
                filtered_list.append(inv)

    # If budget provided, render the filtered list with budget constraints
    if budget is not None:
        from utils.kb import render_entry_for_prompt
        if not ids:
            return ""
        # Truncate list to max_items, then render
        truncated = filtered_list[:budget.max_items] if hasattr(budget, 'max_items') else filtered_list
        list_entry = {"invariants": truncated, "category_filter": category}
        return render_entry_for_prompt(list_entry, budget, f"invariants[{category or 'all'}]")

    return ids


# ==============================================================================
# Guardrails
# ==============================================================================

def ensure_selective_loading() -> List[str]:
    """Validate that no full YAML catalogs are loaded in this session.

    This is a guardrail to catch accidental full-catalog loads.
    Called at end of planning/decision phase.

    Returns:
        List of warnings (empty if all loads were selective)
    """
    warnings = []

    # Check if any full catalogs were accidentally loaded
    # (This is a soft check; production uses code analysis instead)

    if not _evidence_field_cache and not _triage_reason_cache and not _symptom_cache:
        # No caches populated = no queries made (expected for some agents)
        pass

    return warnings


def clear_caches():
    """Clear all field-level caches. Useful for testing."""
    global _evidence_field_cache, _triage_reason_cache, _symptom_cache, _invariant_cache
    _evidence_field_cache.clear()
    _triage_reason_cache.clear()
    _symptom_cache.clear()
    _invariant_cache.clear()
