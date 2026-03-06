#!/usr/bin/env python3
"""Partition large KB YAML catalogs into smaller, focused namespace files."""

import yaml
import os

def partition_evidence_catalog():
    """Split 824-line evidence_catalog.yaml into 3 files."""
    base_path = "docs/kb/10_runtime_contracts"
    
    with open(f"{base_path}/evidence_catalog.yaml") as f:
        data = yaml.safe_load(f)
    
    # Schema file: core structures
    schema = {
        "version": data["version"],
        "last_updated": data["last_updated"],
        "step_result_schema": data["step_result_schema"],
        "extraction_result_schema": data.get("extraction_result_schema", {}),
    }
    
    # Fields file: evidence field definitions
    fields = {
        "version": data["version"],
        "last_updated": data["last_updated"],
        "namespaces": data.get("namespaces", {}),
        "reason_evidence_map": data.get("reason_evidence_map", {}),
        "success_evidence_map": data.get("success_evidence_map", {}),
    }
    
    # Artifacts file: artifact catalog and guidelines
    artifacts = {
        "version": data["version"],
        "last_updated": data["last_updated"],
        "artifacts": data.get("artifacts", {}),
        "guidelines": data.get("guidelines", []),
    }
    
    # Write partitioned files
    with open(f"{base_path}/evidence_schema.yaml", "w") as f:
        yaml.dump(schema, f, default_flow_style=False, sort_keys=False)
    
    with open(f"{base_path}/evidence_fields.yaml", "w") as f:
        yaml.dump(fields, f, default_flow_style=False, sort_keys=False)
    
    with open(f"{base_path}/evidence_artifacts.yaml", "w") as f:
        yaml.dump(artifacts, f, default_flow_style=False, sort_keys=False)
    
    print("✅ Evidence catalog partitioned:")
    for fname in ["evidence_schema.yaml", "evidence_fields.yaml", "evidence_artifacts.yaml"]:
        fpath = f"{base_path}/{fname}"
        lines = len(open(fpath).readlines())
        size = os.path.getsize(fpath)
        print(f"  {fname}: {lines} lines, {size//1024}KB")


def partition_architecture_invariants():
    """Split 593-line architecture_invariants.yaml into 2 files."""
    base_path = "docs/kb/00_foundation"
    
    with open(f"{base_path}/architecture_invariants.yaml") as f:
        data = yaml.safe_load(f)
    
    # Registry file: flat list of invariants
    registry = {
        "version": data["version"],
        "last_updated": data["last_updated"],
        "total_invariants": data.get("total_invariants", 0),
        "invariants": data.get("invariants", []),
    }
    
    # Category index file: hierarchical organization
    categories_obj = {
        "version": data["version"],
        "last_updated": data["last_updated"],
        "categories": data.get("categories", []),
        "decision_tree": data.get("decision_tree", {}),
    }
    
    # Write partitioned files
    with open(f"{base_path}/invariants_registry.yaml", "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
    
    with open(f"{base_path}/invariants_by_category.yaml", "w") as f:
        yaml.dump(categories_obj, f, default_flow_style=False, sort_keys=False)
    
    print("✅ Architecture invariants partitioned:")
    for fname in ["invariants_registry.yaml", "invariants_by_category.yaml"]:
        fpath = f"{base_path}/{fname}"
        lines = len(open(fpath).readlines())
        size = os.path.getsize(fpath)
        print(f"  {fname}: {lines} lines, {size//1024}KB")


if __name__ == "__main__":
    partition_evidence_catalog()
    partition_architecture_invariants()
    print("\n✅ Partitioning complete")
