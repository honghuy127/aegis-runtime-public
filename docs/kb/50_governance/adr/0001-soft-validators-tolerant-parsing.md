# ADR 0001: Soft Validators + Tolerant Parsing

## Status
Accepted

## Context
LLM/VLM outputs can be malformed or partially schema-compliant, especially on smaller local models.
Hard-failing on first schema error causes unnecessary run failures.

## Decision
Use strict-ish schema validators at prompt boundaries, but keep them soft-fail.
If validation fails (`invalid_json`, `missing_keys`, `invalid_enum`, `wrong_shape`),
fall back to tolerant parsing and existing recovery paths.

## Consequences
- Better reliability under model variance.
- Preserves backward compatibility with legacy parser behavior.
- Validation errors remain visible for diagnostics without breaking runtime flow.
