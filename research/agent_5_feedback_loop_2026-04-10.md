# Agent 5 — Brain Feedback Loop: Online Learning for a Personal Router

> **Mission:** Document how to add a minimum viable feedback loop to Toke Brain so routing weights improve from real usage data — without external ML services, without multi-tenant complexity, on ~200-300 sessions/month from one user.

**Session date:** 2026-04-10
**Reads from:** brain_synthesis, agent_c, agent_d, severity_classifier.py, routing_manifest.toml, brain_cli.py

---

## The Core Problem (grounded in what Brain actually is)

Brain's scoring is a weighted sum: `score = sum(signal[k] * weight[k])`. The weights in `routing_manifest.toml` are hand-tuned constants — `file_refs = 0.22`, `reasoning = 0.20`, etc. They were set once on 2026-04-10 and will never change unless the user edits them.

The problem: the user's workflow shifts. New projects, new skills, new prompt patterns. A weight that correctly routes `file_refs`-heavy prompts in April may under-weight them by July when the user's UE5 work style changes. The guardrails are similarly static — they may start firing too aggressively or not enough.

A frozen router is quietly wrong. It never tells you it's wrong.

---

## Research Question 1: Personal-Scale Router Learning at 200-300 Sessions/Month

**The constraint is real:** 200-300 sessions/month ≈ ~5,000-10,000 prompt decisions/month if each session has 20-50 turns. That sounds like enough data until you break it by arm: Brain has 8 signal dimensions × 6 tiers × implicit feedback. You have sparse coverage per cell.

**What works at this scale:**

### Option A — Exponentially Weighted Moving Average (EWMA) on Signal Weights
**Mechanism:** Each time a routing decision is made and an outcome signal is observed, update the corresponding signal's weight by a small step toward the direction that would have improved the outcome. No model, no matrix, no training loop — just:
```
w_k(t+1) = (1 - alpha) * w_k(t) + alpha * grad_k(outcome)
```
Where `alpha` = learning rate (0.001-0.01), `grad_k` = direction the weight should move based on implicit feedback.

**Data requirement:** ~30 observations per signal to stabilize. At 5k decisions/month, you reach that in days. The issue is outcome labels, not data volume.

**Computational cost:** 8 multiplications per update. Negligible.

**Explainability:** Perfect. You can print `current_weights` from the manifest and see exactly what changed. Diff against baseline.

**Applicability to Brain:** This is the primary recommended mechanism. The weights live in `routing_manifest.toml` — the update loop just rewrites them incrementally. the user can see exactly what shifted and why.

**Failure modes:** Alpha too high = instability; weights oscillate. Alpha too low = weights never escape initial values. No recovery from a run of correlated bad outcomes (e.g., a week of UE5-heavy work that spikes all weights toward Opus temporarily).

---

### Option B — Contextual Bandit (LinUCB)
**Mechanism:** Treats routing as a multi-armed bandit where each "arm" is a (tier, model) choice. LinUCB (Li et al. 2010) uses linear regression on context features to estimate the expected reward for each arm, plus an exploration bonus proportional to uncertainty. Chooses the arm with highest `estimated_reward + exploration_bonus`.

The update equation after observing reward r at context x with arm a:
```
A_a = A_a + x * x^T      (d×d matrix, updated online)
b_a = b_a + r * x         (d-vector, updated online)
theta_a = A_a^{-1} * b_a  (current weight estimate)
```

**Data requirement:** 50-100 per arm to get stable estimates. With 6 tiers as arms and 8 context features, you need ~300-600 observations minimum. You hit that in 1-2 months.

**Computational cost:** 8×8 matrix inverse per arm update = 6 arm matrices. Negligible on CPU. Full pass in microseconds.

**Explainability:** Moderate. You can print `theta_a` (the weight vector per arm) and explain "the router thinks file_refs = 0.31 when choosing Opus." But matrix arithmetic is less immediately readable than EWMA.

**Applicability to Brain:** Overkill for Brain v2. Designed for multi-user systems with many arms. the user has one user and 6 tiers. LinUCB's exploration behavior (occasionally picking a suboptimal arm to gather data) is actively wrong for Brain — you don't want to route a critical task to Haiku "for exploration."

**Failure modes:** Exploration penalty. LinUCB will occasionally pick worse arms to reduce uncertainty. Unacceptable for a production tool. Requires disabling exploration (pure exploitation mode = just linear regression, not really LinUCB anymore).

---

### Option C — Thompson Sampling (Bayesian)
**Mechanism:** Maintain a Beta(alpha, beta) distribution per (signal, outcome) pair representing the belief about signal predictive quality. On each decision, sample from the posterior; on each outcome, update alpha (successes) or beta (failures) by 1. The Beta posterior converges to the true rate with minimal data — it works at small N precisely because it encodes uncertainty explicitly.

For Brain specifically: maintain `Beta(alpha_k, beta_k)` per signal k, where alpha increments when signal k fired AND the routing was confirmed correct, beta increments when signal k fired AND routing was wrong.

**Data requirement:** 5-10 observations for meaningful posterior shaping. The math works at the user's scale.

**Computational cost:** 8 Beta distributions. Sampling = standard library `random.betavariate()`. Zero external dependencies.

**Explainability:** Good. You can report `alpha/(alpha+beta)` per signal as "how often this signal predicted correctly." This is directly interpretable.

**Applicability to Brain:** Best fit for per-signal confidence estimation — not for replacing weights directly, but for telling the user "the `tool_calls` signal has only been right 40% of the time, consider reducing its weight."

**Failure modes:** Beta posteriors assume outcomes are IID Bernoulli. Routing outcomes aren't — context autocorrelation (two similar prompts back-to-back) can skew posterior. Slow recovery if a sequence of bad outcomes shifts a signal's posterior hard negative.

---

## Research Question 2: Best Algorithm for Brain

**Verdict:** EWMA weight updates + Thompson Sampling confidence tracking, not a full bandit.

Why not LinUCB/full bandit:
- Exploration is dangerous for a personal routing tool
- Bandit formulation assumes clear reward signal — Brain has implicit, noisy outcomes
- 6 tiers × 8 signals = 48 parameters; with 200-300 sessions/month, you're in the underdetermined regime for matrix factorization

The right mental model: **the manifest weights are a running estimate of signal importance.** EWMA keeps them current. Thompson Sampling tells you how confident each signal estimate is.

---

## Research Question 3: Implicit Feedback Signals (What Already Exists in Telemetry)

Brain already logs `~/.claude/telemetry/brain/decisions.jsonl` and `tools.jsonl`. Every entry has `session_id`, `ts`, `current_model`, `result.tier`, `result.model`.

**Signals extractable WITHOUT new instrumentation:**

| Signal | How to compute | What it implies |
|---|---|---|
| **Model override event** | Compare `recommended model` in decisions.jsonl to `model` field in next tool call | the user manually switched → Brain was wrong |
| **Prompt repetition** | Same session_id, semantically similar prompt within N turns | Brain's response didn't satisfy; the user restated it |
| **Session cost** | Sum from tools.jsonl `output_size` (proxy) or from stats-cache.json post-session | High cost on a task Brain rated S1/S2 = routing was wrong |
| **Tool call count per prompt** | Count tool entries between two UserPromptSubmit events | More tool calls = more complexity; if Brain said S1 but 8 tools fired, under-routed |
| **Session continuation rate** | Did session produce any output after this turn? | Abandoned session = bad outcome |
| **Error tool patterns** | `tool_name` = "Bash" with exit code errors in output_size | Error conditions may indicate model was underpowered |
| **Short turn count** | Turns in session < 3 = quick task; > 30 = deep work | Calibrate S0/S1 thresholds against actual session depth |

**The richest single signal: model override.** When the user runs `/model opus` right after Brain said S2 (Sonnet), that is a perfect negative outcome label. The decisions.jsonl already has the Brain recommendation; the tools.jsonl has the model used. The delta is a label.

---

## Research Question 4: Active Learning — Maximizing the user's Explicit Signals

the user won't rate every turn. But when he gives a signal, it should count more than 10 implicit signals.

**Maximizing explicit signal value:**

1. **Pair it with context.** When the user says `/brain bad` (or `/brain good`), immediately capture the full feature vector of the previous decision — not just the signal values but the raw prompt hash (for deduplication), tier, model, and guardrails that fired. This is a gold-labeled training example.

2. **Use it to recalibrate thresholds, not just weights.** Explicit feedback on a borderline decision (score = 0.34, just barely S2) has high information — it tells you whether the boundary is in the right place, not just whether a weight is off.

3. **Anchor against explicit labels during EWMA update.** Give explicit labels a 10× weight multiplier over implicit signals in the update equation. One explicit `/brain bad` = 10 implicit no-ops.

4. **Track the signal that was most responsible** (the highest-weighted signal in `result.signals`) and preferentially update that weight. If Brain recommended S2 because `file_refs = 0.89` and the user overrode to Opus, the `file_refs` weight deserves a higher nudge than ambient signals.

---

## Research Question 5: Confidence Calibration

Brain outputs `score = 0.22` but this is NOT a probability. It's a weighted sum of normalized signals, not calibrated against outcomes. "0.22" doesn't mean 22% chance of needing Opus.

**Why calibration matters:** Brain needs to express "I'm uncertain about this decision" differently from "I'm confident." Uncalibrated scores can't distinguish a 0.34 (confident S2) from a 0.34 (ambiguous S3/S2 boundary).

**Production approaches:**

### Temperature Scaling (Guo et al. 2017, "On Calibration of Modern Neural Networks")
**Mechanism:** Learn a single scalar `T` that divides the logit (pre-softmax output) to stretch/compress the probability distribution: `p_calibrated = softmax(logit / T)`. For Brain: fit `T` such that when Brain says "score = X", the fraction of times routing X leads to good outcomes actually equals X. Requires ~50 labeled outcomes per score bucket to fit. Uses `scipy.optimize.minimize` on log-loss — one scalar, one optimization.

For Brain's use case, simplified: learn a single `T` per tier boundary. When score is within 0.05 of a tier boundary, flag as low-confidence.

**Applicability:** Brain can implement a simplified version: track rolling outcome rate per score decile (0.0-0.1, 0.1-0.2, etc.). If the 0.30-0.40 bucket has 60% bad outcomes (the user kept overriding), that bucket is miscalibrated toward Sonnet and needs an upward adjustment.

### Isotonic Regression (Niculescu-Mizil & Caruana 2005)
**Mechanism:** Non-parametric monotone mapping from raw scores to calibrated probabilities. Fits a step-function using pool-adjacent-violators algorithm. More flexible than temperature scaling; handles non-linear miscalibration.

**Data requirement:** ~100 labeled points per bucket. At the user's scale, this requires 6-12 months of data before it's meaningful. Not viable for Brain v2.

**Recommendation for Brain v2:** Temperature scaling only, simplified to per-tier-boundary adjustment. The key deliverable is a `calibrated_confidence` flag: "this decision is within 0.05 of the S2/S3 boundary — uncertainty is high."

---

## Research Question 6: Drift Adaptation

the user's workflow changes. New projects spike new signal distributions. The `file_refs` signal was tuned in April during multi-project research work; by December during a pure-Sonnet skill sprint, the tuned weights may be systematically wrong.

**Detection:**

EWMA-based drift detection (Roberts 1959, adapted for router use by Vowpal Wabbit's --cb_explore_adf):
- Maintain a rolling 7-day average outcome rate per tier
- Compare to 30-day baseline via CUSUM (Cumulative Sum Control Chart)
- If the 7-day CUSUM exceeds threshold → trigger drift alert

For Brain, simplified: track `outcome_rate_7d[tier]` and `outcome_rate_30d[tier]`. If `abs(7d - 30d) > 0.20` for any tier, emit drift warning.

**Adaptation mechanism:** Increase EWMA `alpha` (learning rate) when drift is detected to let the weights move faster. Decrease alpha when stable to prevent noise-driven oscillation.

**Explicit drift alert implementation:**
```python
# In brain_cli.py brain scan output, add:
drift_score = max(abs(tier_rate_7d[t] - tier_rate_30d[t]) for t in tiers)
if drift_score > 0.20:
    print(f"[DRIFT ALERT] Routing pattern shifted {drift_score:.0%} in last 7 days")
    print("  Consider running: brain tune --review")
```

---

## Research Question 7: Feedback-Free Learning from Telemetry Alone

Even without any outcome labels, Brain can learn from telemetry patterns:

**Pattern 1 — Override rate by signal pattern:**
If `file_refs >= 3` consistently precedes model override events, the `multi_file_refactor` guardrail threshold is wrong. No outcome labels needed — the override itself is observable.

**Pattern 2 — Session depth vs tier:**
Correlate session turn count (from tools.jsonl grouped by session_id) against Brain's initial tier recommendation. Long sessions (30+ turns) that were initially rated S1 = Brain underestimated complexity. This correlation requires no labels, just counting.

**Pattern 3 — Tier distribution drift:**
Track weekly fraction of decisions per tier. If S0/S1 fraction drops from 30% to 5%, the user's work mix shifted toward complex tasks. Manifest weights may need global rescaling.

**Pattern 4 — Cost per tier:**
Compute average `output_size + input_size` from tools.jsonl per Brain-recommended tier. If S2 sessions consistently produce more tokens than S4 sessions, something is miscategorized at the input.

---

## Research Question 8: Preference Pair Collection

RouteLLM (Berkeley/LMSYS, 2024) trained on Chatbot Arena preference pairs — "given this prompt, did humans prefer GPT-4 or Mixtral?" That's 1M+ labeled pairs from thousands of users.

the user is one user at 200-300 sessions/month. The preference pair approach doesn't directly apply, but the structure does.

**Minimum viable preference dataset for Brain:**

A preference pair for Brain looks like: `(prompt_features, recommended_tier, actual_tier_used, outcome_signal)`.

You need ~50 confirmed pairs to fit a simple threshold adjustment. To collect them:

1. Every model override event is automatically a preference pair: `(features, S2, S4_actual, implicit_bad)`.
2. Every session where the user doesn't override is a weak positive pair: `(features, S3, S3_actual, implicit_ok)` — but weak because non-override ≠ satisfied.
3. Explicit `/brain good` and `/brain bad` commands generate gold pairs.

At the user's scale, you'll have ~50 gold pairs in 2-3 months of active use. That's enough to fit temperature scaling and EWMA per-signal adjustments.

**The RouteLLM insight that transfers:** Weight pairs by outcome certainty, not just by occurrence. An explicit `/brain bad` = weight 10. A model override = weight 3. Session continuation without override = weight 1.

---

## Research Question 9: Incremental Manifest Tuning

This is the most practical angle given Brain's architecture. The weights in `routing_manifest.toml` can be auto-tuned directly.

**The mechanism:**

```python
# brain_learner.py — stdlib only
import json, pathlib, tomllib, tomli_w  # tomli_w for writing TOML

ALPHA = 0.005          # learning rate: small enough to be stable, big enough to converge in months
OUTCOME_WINDOW = 7     # days

def update_weights(manifest_path, outcomes):
    """
    outcomes: list of {signal_values: dict, outcome: +1 or -1}
    +1 = routing confirmed correct (no override, session continued)
    -1 = routing wrong (model override event observed)
    """
    manifest = load_manifest(manifest_path)
    weights = manifest["weights"]

    for obs in outcomes:
        for signal, value in obs["signal_values"].items():
            if signal not in weights:
                continue
            # Gradient: if outcome good, reinforce weight for active signals
            # If outcome bad, reduce weight for most-active signal
            grad = obs["outcome"] * value          # signal contribution
            weights[signal] = round(
                max(0.01, min(0.50,
                    (1 - ALPHA) * weights[signal] + ALPHA * (weights[signal] + grad * 0.05)
                )), 4
            )

    manifest["weights"] = weights
    write_manifest(manifest_path, manifest)
```

**Skill tier auto-bump rule:**
If the user routes a pinned-Haiku skill to Sonnet manually 5 times in a row (5 consecutive override events for that skill), bump that skill's tier in `[skills]` from S1 to S2. This is a deterministic rule, not ML — no uncertainty, fully auditable.

```python
def check_skill_tier_bumps(decisions_log_path):
    consecutive_overrides = {}  # skill -> count
    for entry in read_decisions_log(decisions_log_path):
        if not entry.get("skill_override"):
            continue
        skill = entry["result"]["skill_override"]
        if model_was_overridden_up(entry):
            consecutive_overrides[skill] = consecutive_overrides.get(skill, 0) + 1
        else:
            consecutive_overrides[skill] = 0  # reset on no-override
        if consecutive_overrides[skill] >= 5:
            return {"skill": skill, "action": "bump_tier_up"}
    return None
```

---

## Research Question 10: Explainable Learning

the user is an engineer. He wants to know WHY the router changed, not just that it did.

**Production reference:** Vowpal Wabbit's `--readable_model` flag exports human-readable feature weights after every update. LiteLLM's auto_router logs `routing_reason` per decision. RouteLLM exposes `threshold` and `calibration_curve` in its CLI.

**For Brain — three transparency outputs:**

### 1. Weight Diff Report (after each tuning pass)
```
[brain tune] Weight changes since 2026-04-10 baseline:
  file_refs:   0.22 -> 0.27  (+23%) — 31 override events had high file_refs
  reasoning:   0.20 -> 0.18  (-10%) — reasoning signal over-fired on simple edits
  tool_calls:  0.08 -> 0.06  (-25%) — low correlation with actual complexity
  (6 signals unchanged)
```

### 2. Signal Accuracy Report (Thompson Sampling posterior)
```
[brain signals] Estimated accuracy per signal (Beta posterior):
  file_refs:    0.82  (n=103, high confidence)
  reasoning:    0.71  (n=89)
  code_blocks:  0.68  (n=61)
  tool_calls:   0.44  (n=33, LOW — consider reducing weight)
  ambiguity:    0.39  (n=12, SPARSE — do not trust yet)
```

### 3. "Why did Brain change its mind?" explanation (per session)
```
[brain explain --decision <id>]
  Original weight routed: S2 (Sonnet)
  Current weight would route: S3 (Sonnet-high)
  Reason: file_refs weight bumped from 0.22->0.27 after 8 overrides involving
  3+ file refs. Your prompt had file_refs=0.67, which now pushes score over
  the S2/S3 boundary.
```

---

## Top 3 Feedback Loop Designs for Brain v2.0

**Ranked by ROI at the user's scale:**

### Rank 1 — EWMA Weight Updater (30 lines of code, no deps)
**ROI:** Highest. Directly mutates `routing_manifest.toml` weights based on model override events. Self-explanatory output. Works from day 1 with zero labeling infrastructure. The manifest is already the config format; the updater just adjusts numbers within it.

**Integration point:** `brain tune` command. Runs offline after session, reads decisions.jsonl, writes updated weights to manifest.

**Data requirement:** 10 override events to start moving weights. 50+ for stable convergence.

---

### Rank 2 — Override Event Collector + Skill Auto-Bump
**ROI:** High. Zero ML. Deterministic rules on top of existing telemetry. Skill auto-bump is the highest-value single change — if Brain keeps under-routing a skill, the skill map in the manifest is just wrong, and 5-consecutive-overrides is a clean signal to fix it.

**Integration point:** Runs as post-session hook. Reads decisions.jsonl and tools.jsonl delta since last run.

**Data requirement:** 5 consecutive override events for a given skill. Observable immediately.

---

### Rank 3 — Thompson Sampling Signal Tracker
**ROI:** Medium-high. Produces per-signal accuracy estimates that feed the EWMA updater AND give the user direct visibility into which signals are trustworthy. Doesn't change weights directly — informs the weight update step.

**Integration point:** `brain scan` output adds a "signal accuracy" section. The Beta(alpha, beta) state persists in a lightweight JSON sidecar at `~/.claude/telemetry/brain/signal_posteriors.json`.

**Data requirement:** 20+ observations per signal for meaningful posterior. 3-4 months at the user's volume for all 8 signals.

---

## Minimum Viable Design

### Data structures

```python
# ~/.claude/telemetry/brain/signal_posteriors.json
{
  "file_refs":   {"alpha": 45, "beta": 12},  # 45 correct, 12 wrong
  "reasoning":   {"alpha": 38, "beta": 14},
  "code_blocks": {"alpha": 29, "beta": 11},
  "tool_calls":  {"alpha": 14, "beta": 19},  # below 0.5 — flag for weight reduction
  ...
}

# ~/.claude/telemetry/brain/weight_history.jsonl (append-only)
{
  "ts": "2026-04-17T14:22:00Z",
  "trigger": "manual_tune",
  "weights_before": {"file_refs": 0.22, ...},
  "weights_after":  {"file_refs": 0.25, ...},
  "outcomes_used": 47,
  "override_events": 12
}
```

### Update equations

```
# EWMA weight update (per override event)
alpha = 0.005
signal_k_score = entry["result"]["signals"][k]         # 0.0-1.0
outcome = -1.0  # override = Brain was wrong
weight[k] = (1 - alpha) * weight[k] + alpha * max(0.01, weight[k] + outcome * signal_k_score * 0.1)

# Thompson Sampling posterior update (per confirmed outcome)
if outcome == +1:
    posterior[k]["alpha"] += signal_k_score  # fractional update
else:
    posterior[k]["beta"]  += signal_k_score

# Signal accuracy (used for reporting, not for direct weight update)
accuracy[k] = posterior[k]["alpha"] / (posterior[k]["alpha"] + posterior[k]["beta"])
```

### Integration points with current Brain

1. **`brain_cli.py`**: add `brain tune` command — reads decisions.jsonl, computes EWMA updates, writes to manifest. `brain scan` output adds signal accuracy section.
2. **`severity_classifier.py`**: no changes needed. The manifest is already the interface.
3. **`routing_manifest.toml`**: add `[learning]` section:
   ```toml
   [learning]
   alpha = 0.005
   override_event_weight = 3.0
   explicit_signal_weight = 10.0
   skill_bump_threshold = 5
   drift_alert_threshold = 0.20
   last_tuned = "2026-04-10"
   tune_count = 0
   ```
4. **New file: `brain_learner.py`** — the update logic. Reads telemetry, computes updates, calls manifest writer. Called by `brain tune` and optionally by a post-session hook.

---

## Implicit Signal List (No New Instrumentation)

All of these are derivable from the existing `decisions.jsonl` + `tools.jsonl`:

| Signal | Source | Outcome meaning |
|---|---|---|
| `current_model != result.model` in next tool event | tools.jsonl | Override event → routing was wrong |
| Turn count between two UserPromptSubmit events | decisions.jsonl | High turn count → task was more complex than Brain thought |
| Session total output_size | tools.jsonl sum | High tokens on low-tier recommendation = under-routed |
| Consecutive non-override sessions | decisions.jsonl | Implicit positive — routing was accepted |
| Time between prompt and next event | ts delta | Unusually short = task trivial (consistent with low tier) |
| Skill_override != null in decisions | decisions.jsonl | Skill routing path, separate from score path |

---

## Explicit Signal UX

Lowest-friction option: two CLI commands that the user types voluntarily.

```
brain good     # Thumbs up on the last routing decision
brain bad      # Thumbs down on the last routing decision
```

These append a `+1` or `-1` outcome to the last entry in `decisions.jsonl`. No UI, no popups, no interruption. When the user types them, the EWMA update runs immediately with a 10× weight multiplier.

Optional: `brain bad --reason "needed opus for architecture work"` — the reason is stored but only used for logs/reporting, not for the weight update math.

The UX lives entirely in `brain_cli.py`. Two new commands, ~20 lines.

---

## Calibration Pipeline

Brain currently has `score = 0.22` as a raw weighted sum. The calibration pipeline converts this to actionable uncertainty output:

1. **Collect:** Track `{score_bucket, outcome}` pairs in `signal_posteriors.json`. Score bucket = round to nearest 0.05.
2. **Compute:** For each bucket, `calibrated_p = outcomes_correct / outcomes_total`.
3. **Flag:** If `abs(score - tier_boundary) < 0.05` AND `calibrated_p < 0.65`, emit `[low confidence]` in Brain's advisory.
4. **Report:** `brain scan` output includes calibration table:
   ```
   Score bucket  Predicted tier  Correct rate  Calibration
   0.30-0.35     S2              0.72          OK
   0.33-0.36     S2/S3 boundary  0.51          LOW — this bucket needs review
   ```

This requires ~50 labeled observations per bucket. At the user's scale, the 0.30-0.40 range (most contested) will have enough data in 3-4 months.

---

## Drift Alert

Add to `brain scan` output:

```
[DRIFT CHECK] 7-day vs 30-day routing distribution:
  S0/S1 (Haiku):    12% this week vs 18% baseline  (-6pp)
  S2/S3 (Sonnet):   41% this week vs 45% baseline  (-4pp)
  S4/S5 (Opus):     47% this week vs 37% baseline  (+10pp)

  WARNING: Opus routing up 10pp in 7 days.
  Possible causes: new complex project, guardrail over-firing, or weights stale.
  Run: brain tune --review
```

Trigger condition: any tier's 7-day fraction differs from 30-day baseline by >10pp. Alert stored in `~/.claude/telemetry/brain/drift_log.jsonl`.

---

## What This Is NOT

- Not an ML training pipeline
- Not a neural network
- Not a dependency on numpy, scikit-learn, or any external library
- Not an automated manifest rewriter without the user's consent — `brain tune` is a dry-run by default, `--write` to apply
- Not trying to replace the guardrails (those stay deterministic, they protect quality floors)

---

## Summary

The minimum viable feedback loop for Brain v2.0 is:

1. **EWMA weight updater** reading override events from existing telemetry → updates manifest weights incrementally. Pure stdlib. `brain tune` command.
2. **Skill auto-bump** — deterministic rule: 5 consecutive overrides = tier bump. Zero ML.
3. **Thompson Sampling signal tracker** — Beta posterior per signal, reports accuracy in `brain scan`. Tells the user which signals to trust.
4. **`brain good` / `brain bad`** — two-command explicit feedback with 10× weight on updates.
5. **Drift alert** in `brain scan` output — 7-day vs 30-day tier distribution check.

Total new code: ~150 lines. No new dependencies. One new file (`brain_learner.py`). Two new manifest sections. All updates dry-run by default.

A router without a feedback loop is frozen in time. These five mechanisms unfreeze it without breaking the simplicity that makes it fast.

---

*Sources: RouteLLM (arxiv:2406.18665), Hybrid LLM (arxiv:2404.14618), AutoMix (arxiv:2310.12963), LLMRank (arxiv:2510.01234), When Routing Collapses (arxiv:2602.03478), Vowpal Wabbit contextual bandit docs, Guo et al. 2017 temperature scaling, LinUCB (Li et al. 2010), brain_synthesis_2026-04-10.md, agent_c_complexity_classification_literature_2026-04-10.md, agent_d_production_routers_2026-04-10.md, severity_classifier.py, routing_manifest.toml*
