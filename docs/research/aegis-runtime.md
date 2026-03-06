# AegisRuntime: Governance-Aware Hybrid Agent Runtime with Bounded Recovery Graphs

## Abstract

Large Language Model (LLM) agents demonstrate strong open-ended reasoning but lack execution stability in long-horizon, interactive environments. In software orchestration and web-based automation, failure modes such as semantic drift, unbounded retries, and latent state inconsistency accumulate over time, degrading reproducibility and auditability.

We present **AegisRuntime**, a governance-aware hybrid agent runtime that enforces bounded adaptation through deterministic policy gates and a formally constrained **Dynamic Recovery Graph (DRG)**. Rather than treating LLM outputs as executable actions, AegisRuntime models them as *untrusted proposals* subject to admissibility constraints, invariant enforcement, and bounded resource budgets.

The core contribution is a runtime control model that transforms unconstrained retry loops into typed recovery trajectories with termination guarantees. The system provides explicit bounds on exploration depth, recovery attempts, and wall-clock time, while preserving adaptive reasoning through probabilistic proposal generation. We formalize hybrid execution as constrained action selection under budget vector $B_t$, and define recovery as bounded graph traversal over typed failure edges.

This work reframes agent reliability as a governance problem at the runtime layer, rather than a model-improvement problem.

**Companion formalization**: [formal_math_modeling.md](formal_math_modeling.md) provides the mathematical model for the runtime transition system, budget vector, and termination guarantees described in this report.

---

# 1. Problem Framing

## 1.1 Failure Modes of Unbounded LLM Agents

LLM-driven agents fail in systematic, reproducible ways:

- **Retry Drift**: Repeatedly attempting semantically equivalent actions without progress.
- **State Hallucination**: Acting on assumed world states not supported by observation.
- **Budget Blindness**: Continuing execution despite diminishing expected utility.
- **Opaque Failure Attribution**: Lack of typed failure classification prevents structured recovery.
- **Unbounded Recovery Loops**: No formal stop condition except external timeout.

These are not model-quality problems alone — they are **runtime governance failures**.

---

## 1.2 Design Tension

There is a structural tension:

| Deterministic System | Probabilistic System |
|----------------------|---------------------|
| Stable               | Adaptive            |
| Predictable          | Exploratory         |
| Brittle to novelty   | Prone to drift      |

AegisRuntime resolves this tension by separating:

- **Proposal generation (probabilistic)**
- **Execution admissibility (deterministic)**

This separation enables adaptive reasoning under strict bounded guarantees.

---

# 2. System Model

## 2.1 Threat Model

We assume:

- Partial observability of environment state.
- Stochastic UI or API drift.
- Unreliable action outcomes.
- Untrusted LLM outputs.
- Finite compute and time resources.

We do *not* assume adversarial LLM behavior, but we treat outputs as non-authoritative.

---

## 2.2 Hybrid Execution Formalization

Let:

- $P(o_t, s_t) \rightarrow a_t$ be probabilistic proposal.
- $D(a_t, s_t, B_t) \rightarrow \{\text{allow}, \text{deny}, \text{transform}\}$

Then executed action:

$$
a_t^* = D(P(o_t, s_t), s_t, B_t)
$$

Where:

$$
B_t = (E_t, T_t, R_t)
$$

- $E_t$: exploration attempts
- $T_t$: elapsed time
- $R_t$: per-reason recovery counter

Termination occurs when:

$$
(E_t > E_{\max}) \lor (T_t > T_{\max}) \lor (R_t > R_{\max})
$$

This ensures **finite execution under all reachable states**.

---

# 3. Dynamic Recovery Graph (DRG)

## 3.1 Definition

A DRG is a directed typed graph:

$$
G_t = (V_t, E_t)
$$

Where:

- Node $v \in V$ encodes runtime state + budget counters.
- Edge $e = (v_i, v_j, a, g)$ encodes action attempt with guard predicate.
- Recovery edges are labeled by failure reason.

### Key Property

Recovery must correspond to a **typed edge**.
There are no wildcard retries.

---

## 3.2 Failure-Driven Transition

Upon failure:

1. Classify failure into taxonomy class $r$.
2. Select admissible recovery edge $e_r$.
3. Increment bounded counter $R[r]$.
4. Transition state.
5. Emit evidence.

If no admissible edge exists → terminate.

---

## 3.3 Infinite Drift Prevention

The DRG guarantees bounded execution via:

- Per-reason recovery caps.
- State fingerprint loop detection.
- Exploration branch limit.
- Wall-clock termination.
- Guarded edge transitions.

Unlike naive retry systems, recovery is not recursive but graph-constrained.

---

# 4. Governance as First-Class Runtime Primitive

Traditional guardrails operate as filters.
AegisRuntime treats governance as structural.

Governance primitives:

- Admissibility contracts
- Budget vector enforcement
- Typed failure taxonomy
- Recovery graph integrity checks
- Evidence schema invariants

This transforms runtime into a **governed transition system**, not a best-effort agent loop.

---

# 5. Knowledge Governance and Drift

AegisRuntime explicitly models knowledge drift as a runtime risk.

We define drift signals:

- Orphaned recovery edges
- Unmapped reason codes
- Schema divergence between policy and runtime evidence
- Unreachable recovery states
- Contract references unused in execution

A **Refactor Gate** blocks deployment when drift is detected.

This prevents silent divergence between declared policy and actual behavior.

---

# 6. Comparison to Existing Agent Architectures

| Approach            | Retry Strategy     | Termination Bound | Typed Recovery | Governance Layer |
|---------------------|-------------------|------------------|----------------|------------------|
| Naive LLM Loop      | Free-form retry   | External timeout | No             | Weak             |
| Reflection Agents   | Self-critique     | Implicit         | Partial        | Weak             |
| Toolformer-like     | Tool constraints  | Per-call bound   | No             | Minimal          |
| AegisRuntime        | Graph-constrained | Formal budgets   | Yes            | Structural       |

The novelty is not improved reasoning — it is **bounded adaptation with governance guarantees**.

---

# 7. Limitations

- Guarantees are policy-bounded, not formally model-checked.
- Graph design quality influences recovery effectiveness.
- Evaluation currently domain-limited.
- Not optimized for high-throughput micro-agents.

---

# 8. Research Contributions

We claim:

1. A formalization of hybrid execution as constrained action admissibility.
2. A Dynamic Recovery Graph abstraction for bounded adaptation.
3. Explicit termination guarantees under budget vector constraints.
4. Governance as runtime architecture rather than monitoring overlay.
5. A structured failure taxonomy driving deterministic recovery selection.

---

# 9. Future Directions

- Temporal logic formalization of DRG.
- Expected recovery depth analysis under stochastic failure.
- Adaptive budget tuning under safety envelope.
- Cross-domain validation.
- Verified invariant enforcement.

---

# 10. Positioning Statement

AegisRuntime does **not** attempt to make LLMs reliable.
It assumes they are unreliable.

It makes unreliable reasoning usable by constraining it inside a bounded, governed transition system.
