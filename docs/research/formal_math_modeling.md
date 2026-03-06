# Addendum: Formal Mathematical Modeling of AegisRuntime

This section expands upon the high-level architecture of the **AegisRuntime** to provide a rigorous, formal mathematical modeling of the hybrid runtime transition system. By formalizing the execution loop, we establish verifiable guarantees around system termination, bounded recovery, and state consistency.

---

## 1. Core Spaces and Variables

Let the runtime execution be defined over discrete time steps $t \in \mathbb{N}$. We define the fundamental spaces of the system as follows:

* $\mathcal{S}$: The set of true, underlying environment states (which are only partially observable).
* $\mathcal{O}$: The set of observations. Let $\Omega: \mathcal{S} \to \mathcal{O}$ be the observation function yielding observation $o_t$ at time $t$.
* $\mathcal{A}$: The set of all possible executable actions.
* $\mathcal{F}$: A finite taxonomy of typed failure reasons (e.g., `Timeout`, `StaleElement`, `AssertionError`), where $|\mathcal{F}| = K$.

## 2. The Budget Vector (Bounded Resources)

To structurally prevent infinite loops and "budget blindness," the state of the agent includes a monotonically increasing budget vector $B_t$:

$$B_t = \langle E_t, T_t, \mathbf{R}_t \rangle$$

Where:
* $E_t \in \mathbb{N}$: The total number of exploration/action attempts executed so far.
* $T_t \in \mathbb{R}^+$: The elapsed wall-clock time since task initiation.
* $\mathbf{R}_t \in \mathbb{N}^K$: A state vector tracking the cumulative number of times each specific failure reason $r \in \mathcal{F}$ has occurred.

The system enforces strict, administrator-defined upper bounds: $B_{\max} = \langle E_{\max}, T_{\max}, \mathbf{R}_{\max} \rangle$.

## 3. The Probabilistic Proposer (LLM)

Unlike standard architectures that treat the LLM as a direct, authoritative policy $\pi: \mathcal{O} \to \mathcal{A}$, AegisRuntime models the LLM as an **untrusted proposal generator** $P$:

$$P: \mathcal{O} \times B_t \to \Delta(\mathcal{A})$$

At time $t$, the LLM samples a *proposed* action: 
$$\hat{a}_t \sim P(\cdot | o_t, B_t)$$

## 4. The Deterministic Policy Gate (Governance)

The system evaluates the proposed action $\hat{a}_t$ against a deterministic admissibility function (or "Gate") $D$. This is where governance is enforced prior to environment interaction.

$$D: \mathcal{A} \times \mathcal{O} \times B_t \to \mathcal{A} \cup \{\bot\}$$

The actual executed action $a_t^*$ is defined as:

$$
a_t^* = D(\hat{a}_t, o_t, B_t) = 
\begin{cases} 
\hat{a}_t & \text{if explicitly allowed} \\
f(\hat{a}_t) & \text{if transformed (safe fallback)} \\
\bot & \text{if denied (forces halt)}
\end{cases}
$$

## 5. Dynamic Recovery Graph (DRG) Transition Dynamics

The system's execution and recovery are constrained by a directed, typed Dynamic Recovery Graph $G = (V, E)$.

* **Nodes ($V$)**: Represent the tuple of the current internal state and the budget $v_t = (o_t, B_t)$.
* **Edges ($E$)**: Represent guarded transitions $e = (v_i, v_j, a^*, g)$, where $g: V \times \mathcal{A} \to \{0,1\}$ is a deterministic guard predicate evaluating admissibility.

When the executed action $a_t^*$ interacts with the environment, it yields a result $y_t \in \{\text{Success}\} \cup \mathcal{F}$.

### State Transition Rules:
1. **If $y_t = \text{Success}$**: Progress is made. The budget updates exploration and time, but carries forward the error state:
   $$B_{t+1} = \langle E_t + 1, T_{\text{now}}, \mathbf{R}_t \rangle$$

2. **If $y_t = r$ (where $r \in \mathcal{F}$ is a specific typed failure)**: 
   The runtime queries the DRG for an admissible recovery edge $e_r$. If $e_r$ exists and passes the guard predicate $g$, the budget vector is updated by incrementing the specific failure counter:
   $$B_{t+1} = \langle E_t + 1, T_{\text{now}}, \mathbf{R}_t + \mathbf{1}_r \rangle$$
   *(where $\mathbf{1}_r$ is a one-hot vector for failure reason $r$.)*

## 6. Formal Termination Guarantee

A core theoretical claim of AegisRuntime is the elimination of infinite loops (Retry Drift) through runtime architecture rather than prompt engineering. We define the system's stopping time $\tau$ as:

$$
\tau = \inf \left\lbrace t \ge 0 \mid (E_t > E_{\max}) \lor (T_t > T_{\max}) \lor \left( \exists r \in \mathcal{F}, \mathbf{R}_t[r] > \mathbf{R}_{\max}[r] \right) \lor (a_t^* = \bot) \lor (e_r \notin E) \right\rbrace
$$

### Proof of Almost-Sure Termination:
Because $E$, $T$, and $\mathbf{R}[r]$ are strictly monotonically increasing properties at every time step $t$:

$$
E_{t+1} > E_t, \quad T_{t+1} > T_t, \quad \mathbf{R}_{t+1}[r] \ge \mathbf{R}_t[r]
$$

And because they are bounded by finite, static constants ($E_{\max}$, $T_{\max}$, and $\mathbf{R}_{\max}$), the condition for $\tau$ is mathematically guaranteed to be met in a finite number of steps.

Therefore, the system will **always** halt. It will terminate by either:
1. Successfully completing the task (terminal success state).
2. Being explicitly blocked by the deterministic gate $D$ ($a_t^* = \bot$).
3. Exhausting a bounded budget constraint.
4. Encountering an untyped failure without a defined recovery edge in the DRG ($e_r \notin E$).

### Conclusion of Model
By formalizing the execution loop as a **Governed Transition System**, AegisRuntime mathematically separates adaptive reasoning (the probabilistic proposal $\hat{a}_t$) from system stability (the structural governance and bounds $B_t$).
