# Plugin & Extraction Router

**Scope**: Plugin strategy interface, extraction router, normalization gates, scope safety
**Defines**: Strategy interface, router dispatch logic, acceptance criteria
**Does NOT define**: Individual strategy implementations, LLM/VLM fallback logic

---

## Plugin Architecture

**Flow**:
```
extraction request
  ↓
plugin.enabled? (flag-gated)
  ├─ Yes → route to strategy
  │   ├─ strategy.extract() → candidate
  │   ├─ normalize(candidate)
  │   ├─ accept_gate(candidate) → bool
  │   ├─ scope_guard(candidate) → bool
  │   └─ return candidate | {}
  └─ No → skip to legacy extraction (heuristic → LLM → VLM)
```

---

## Feature Flags

**Controlling plugin extraction**:

```yaml
# configs/thresholds.yaml
FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED: true
FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED: true
FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY: "xpaths"  # or "regex", "ml_selector", etc.
FLIGHT_WATCHER_DISABLE_PLUGINS: false  # Emergency off
```

**Flag priority**:
1. `FLIGHT_WATCHER_DISABLE_PLUGINS` (master kill-switch)
2. `FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED` (per-strategy enable)
3. `FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY` (which strategy to route)

---

## Strategy Interface

**Module**: `core/plugins/strategies/`

```python
class Strategy(ABC):
    def extract(self, html: str, locale: str, evidence: dict) -> dict | None:
        """
        Return: {"price": <float>, "currency": <str>} or None
        """
        pass
```

**Required return signature**:
```python
{
    "price": 123.45,
    "currency": "USD",
    # optional:
    "timestamp": int(unix_ms),
    "source": "xpath_strategy"
}
```

---

## Router: Normalization Gate

After strategy.extract(), normalize all candidates:
- Convert price to float
- Validate currency (ISO 4217)
- Clamp unreasonable values (e.g., >$100k)
- Mark confidence based on price pattern match

**Returns**: Normalized dict or `{}`

---

## Router: Acceptance Gate

**Criteria**:
- Price present and >0
- Currency present
- Confidence >= threshold (from `thresholds.yaml`)
- No parsing errors

**Returns**: True (accept) | False (reject→fallback)

---

## Router: Scope Guard

**Criteria**:
- Route-matching OK (from evidence)
- Scenario state ready (from evidence)
- Not blocked interstitial

**Evidence keys** (from [evidence_catalog.yaml](evidence_catalog.yaml)):
- `search.route_bound`
- `search.scenario_ready`
- `search.blocked_interstitial`

**Returns**: True (safe) | False (unsafe→fallback)

---

## Fallback Ordering

If plugin rejected:
1. Heuristic extraction (deterministic parsing)
2. LLM extraction (code generation, bounded calls)
3. VLM extraction (multimodal, vision-assisted)
4. Return `{}` (no price found)

Legacy path always available; plugin is opt-in acceleration.

---

## Related

- [Runtime Contracts](runtime_contracts.md)
- [Evidence Catalog](evidence_catalog.yaml)
- [Configuration Guide](../../CONFIG.md)
