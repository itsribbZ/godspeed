# Agent 2: Quality-Preserving Routing & Output Amplification — SOTA 2025-2026

**Mission:** Document techniques that make cheap model (Sonnet 4.6, Haiku 4.5) output match Opus 4.6 quality through technique, not luck.  
**Date:** 2026-04-10  
**Key constraint:** This is quality *amplification*, not just cost optimization.

---

## Baseline: The Actual Sonnet/Opus Gap (April 2026)

Before amplification techniques matter, we need to know the gap we're closing.

**Current benchmark reality** (Sonnet 4.6 vs Opus 4.6):

| Benchmark | Sonnet 4.6 | Opus 4.6 | Gap |
|---|---|---|---|
| SWE-bench Verified | 79.6% | 80.8% | 1.2 pts |
| OSWorld (computer use) | 72.5% | 72.7% | 0.2 pts |
| Agentic Tool Use | 91.7% | 91.9% | 0.2 pts |
| Terminal/Agentic Coding | 59.1% | 65.4% | **6.3 pts** |
| Novel Problem Solving | 58.3% | 68.8% | **10.5 pts** |
| Deep Scientific Reasoning (GPQA) | 74.1% | 91.3% | **17.2 pts** |
| Aider Polyglot (Claude 4.x) | ~61% | ~72% | **~11 pts** |

**Key insight:** The SWE-bench gap is nearly closed natively. The real gap lives in novel/complex/multi-step tasks. Quality amplification techniques matter most precisely there.

---

## Top 10 Techniques: Evidence-Based Analysis

---

### 1. Extended Thinking (Test-Time Compute Scaling)

**Source:** Anthropic production data, 2025–2026; Sonnet 4.5 SWE-bench report (2025)

**Mechanism:** The `budget_tokens` parameter allows Claude to run internal chain-of-thought reasoning before outputting. This is not CoT in the prompt — it's a compute budget allocated to the model's private scratchpad. Larger budgets enable more thorough analysis. Anthropic uses up to 200K thinking tokens on their own SWE-bench evaluations.

**Quality recovery:**
- Sonnet 4.5 base: 77.2% SWE-bench Verified
- Sonnet 4.5 with extended thinking (200K budget): 82.0% SWE-bench Verified
- That's a **+4.8 points** improvement — exceeding Opus 4.6 (80.8%) entirely
- Haiku 4.5 with extended thinking (128K budget): 73.3% SWE-bench Verified

**Cost multiplier:** Thinking tokens billed at same rate as output tokens. A 32K thinking budget = ~$0.096 extra per call on Sonnet 4.6 at $3/MTok. The 200K budget used for SWE-bench is ~$0.60 extra per call — significant but bounded.

**Latency impact:** 32K thinking budget: +8–15 seconds. 200K: +45–90 seconds. Not suitable for interactive latency budgets.

**Applicability to Claude Code:** Direct. Claude Code already supports extended thinking via API. Routing strategy: use 32K budget for complex/ambiguous tasks, skip for file reads and simple edits.

**Implementation difficulty:** 1. Drop-in via `thinking: {type: "enabled", budget_tokens: 32000}`. No infrastructure changes.

**Failure mode:** Over-applying it. Routine tasks with extended thinking waste tokens and add latency. The win comes from selective application.

---

### 2. Architect-Editor Split (Role Decomposition)

**Source:** Aider.chat blog, "R1+Sonnet set SOTA on aider's polyglot benchmark," January 2025

**Mechanism:** Two-model pipeline. An "architect" model (higher capability or reasoning-focused) produces a structured plan describing how to solve the problem. An "editor" model (cheaper, faster) executes the plan into concrete code edits. The editor never needs to reason about *what* to do — only *how* to express it. This maps naturally onto Aider's existing `--architect` flag.

**Quality recovery:**
- R1 (architect) + Sonnet 3.5 (editor): 64.0% Aider Polyglot
- o1 alone: 61.7% Aider Polyglot
- Cost: $13.29 total vs $186.50 for o1 — **14x cheaper with higher quality**
- This is a 2.3-point quality *improvement* over the expensive baseline at 14x lower cost

**Cost multiplier:** 1.3–1.5x the cost of running the architect alone (editor calls are short execution tasks). The architect's reasoning is the expensive part.

**Latency impact:** +2–5 seconds for the additional editor call. Low overhead since editor calls are fast.

**Applicability to Claude Code:** Direct. Brain v2 can route: complex refactoring → architect (Opus or extended-thinking Sonnet) + editor (Sonnet or Haiku). Pure file edits skip architect entirely.

**Implementation difficulty:** 2. Requires splitting the agentic loop into plan + execute phases. Aider already has infrastructure for this. Claude Code needs a wrapper.

---

### 3. Adaptive Self-Consistency with Confidence Weighting

**Source:** arXiv:2601.02970 (ReASC, January 2026); arXiv:2502.06233 (CISC, February 2025); arXiv:2203.11171 (Wang et al. original, 2022)

**Mechanism:** Sample N responses from a cheap model, then aggregate via weighted majority vote where each response's weight is its confidence score. Original self-consistency (Wang 2022) used uniform voting across 40 samples. Modern variants like CISC weight by model-reported confidence, reducing required samples by 40%+ while maintaining or improving accuracy. ReASC adds a single-sample fast path: if the first sample is high-confidence, skip additional sampling. This reduces average cost to ~1.5–2x instead of 5–10x.

**Quality recovery:**
- ReASC: reduces inference cost by up to 70% vs baseline self-consistency while preserving accuracy (GSM8K, Gemma-3-4B)
- CISC: outperforms baseline self-consistency on 9 models, 4 datasets; reduces required paths by 40%+
- Multi-agent self-consistency (arXiv:2511.00751): diminishing returns after 5–10 agents; best gain: +1.6% on Math-500

**Cost multiplier:** With adaptive sampling, effective multiplier is 1.5–2.5x (vs naive 5–10x for N=5–10 samples). For code generation where correctness is binary, test execution can replace voting — cost stays at 1x.

**Latency impact:** Linear with sample count. 3 parallel samples: +0ms latency (parallel execution). Sequential: +N×base_latency.

**Applicability to Claude Code:** Best for ambiguous classification/routing decisions within Brain, or for code generation where tests exist as verifiers. Not suitable for single-correct-answer editing tasks.

**Implementation difficulty:** 2. Parallel sampling is straightforward. Confidence extraction requires parsing model output or using logprobs.

**Failure mode:** Plateau effect. Beyond 5–10 samples, all paths become redundant (arXiv:2511.00751). Don't scale past N=5 without evidence it helps.

---

### 4. Verification Cascade (FrugalGPT / C3PO Pattern)

**Source:** arXiv:2305.05176 (FrugalGPT, 2023, TMLR 2024); arXiv:2511.07396 (C3PO, NeurIPS 2025)

**Mechanism:** Sequential cascade: cheap model answers → quality scorer evaluates confidence → if below threshold, escalate to expensive model. FrugalGPT uses a learned answer scorer (not the model's self-assessment). C3PO (NeurIPS 2025) extends this with probabilistic cost constraints and conformal prediction, providing theoretical guarantees that inference cost stays within budget with high probability.

**Quality recovery:**
- FrugalGPT: matches GPT-4 performance with up to **98% cost reduction**, or improves over GPT-4 by 4% at same cost
- C3PO: SOTA on GSM8K, MATH-500, BigBench-Hard, AIME reasoning benchmarks
- AutoMix (arXiv:2310.12963): reduces compute by 50%+ at comparable performance; POMDP router adds <1ms overhead

**Cost multiplier:** 0.1–0.5x when cheap model succeeds (most queries). 1.2x on escalated queries (cheap + expensive). Overall savings depend on escalation rate — typically 10–30% of queries escalate.

**Latency impact:** Cheap model first: +base_cheap_latency. Escalation path: +base_cheap + base_expensive latency. For latency-sensitive: run cheap+expensive in parallel, discard expensive if cheap is confident.

**Applicability to Claude Code:** Directly applicable. Brain already does static routing; dynamic cascade is the next tier. Haiku handles routing/planning → Sonnet handles coding → Opus handles novel/complex only.

**Implementation difficulty:** 3. Requires training or calibrating a quality scorer, or using self-consistency as the confidence signal (AutoMix approach). C3PO requires calibration dataset but provides formal guarantees.

---

### 5. Critic-Refine Loop (Iterative Self-Correction)

**Source:** arXiv:2502.09183 (RefineCoder, February 2025); LLMLOOP (ICSME 2025); Self-Refine (NeurIPS 2023, still SOTA mechanism)

**Mechanism:** Generator produces code → critic (same or different model) evaluates and produces structured feedback → generator refines. RefineCoder's Adaptive Critique Refinement (ACR) runs 3 iterations of this loop with 6.7B–7B parameter models.

**Quality recovery:**
- RefineCoder-QW-7B after 3 iterations: +3.0 pts average across benchmarks
- Best case (MBPP+): +7.9 points from iteration 0 to iteration 3
- GPT-4o-mini with error feedback: +21.76 pts assertion correctness (53.62% → 75.38%)
- Gemini-2.0-Flash: +32 pts with iterative repair
- Does NOT reach GPT-4o (87.2%) from a 7B model (81.1% on HumanEval+) — gap narrows but doesn't close

**Cost multiplier:** 3x for 3 rounds (assuming equal-cost critique and generation). Critic calls can be cheaper than generation calls.

**Latency impact:** Sequential by design. 3 rounds = 3x base latency. Minimum practical loop: 2 rounds.

**Applicability to Claude Code:** Best for complex code generation where tests exist as ground truth. Use test execution output as critic signal — no extra model call needed for critique. Pure self-critique (no external signal) risks security regressions: 37.6% increase in critical vulnerabilities after 5 iterations (IEEE-ISTAS 2025).

**Implementation difficulty:** 2. Straightforward loop implementation. The critic signal source is the key design decision.

**Failure mode (critical):** Without external verification signal, iterative refinement degrades security. A 2025 IEEE study (arXiv:2506.11022) found 37.6% increase in critical vulnerabilities after 5+ iterations. Cap at 2–3 rounds and validate with tests, not just LLM critique.

---

### 6. Prompt-Based Distillation (Few-Shot Anchoring)

**Source:** ACL/EMNLP 2025 findings; arXiv:2510.21631 (Few-Shot Knowledge Distillation with Counterfactuals, October 2025)

**Mechanism:** Load a cheap model's context with high-quality examples produced by an expensive model. The examples serve as style and reasoning anchors. Unlike fine-tuning, this requires no weight updates — it's purely in-context. For coding, this means: collect Opus-generated solutions to representative problems, then prepend 3–5 as few-shot examples in Sonnet/Haiku prompts. The CoD (Counterfactual-explanation-infused Distillation) variant adds contrastive examples showing what the wrong approach looks like and why.

**Quality recovery:** No direct Opus→Sonnet gap numbers published with coding tasks. For general tasks, few-shot examples from stronger models consistently lift weaker model quality by 5–15% relative. EMNLP 2025 findings show task-aware few-shot distillation competitive with full fine-tuning in few-data regimes.

**Cost multiplier:** 1.1–1.3x (example tokens add to context). With prompt caching, example tokens cost ~10% of base rate after first use — effective overhead drops to 1.01–1.03x.

**Latency impact:** Minimal. Context window increase adds minor prefill latency.

**Applicability to Claude Code:** High. The most underused technique in production Claude Code stacks. For Brain v2: curate a library of Opus-quality responses to common task types; inject 2–3 as cached few-shot examples. Pairs well with prompt caching for near-zero marginal cost.

**Implementation difficulty:** 2. Requires building the example library (one-time Opus cost) and retrieval system to select relevant examples. Caching handles the ongoing cost.

---

### 7. Ensemble Routing with Higher-Order Aggregation

**Source:** arXiv:2510.01499 (Beyond Majority Voting, October 2025); arXiv:2511.15714 (Majority Rules, November 2025); Awesome-LLM-Ensemble survey (GitHub, 2025)

**Mechanism:** Run query through multiple cheap models (or multiple configurations of one model), then aggregate via Optimal Weight (OW) or Inverse Surprising Popularity (ISP) algorithms rather than simple majority vote. OW uses both first-order frequency and second-order correlation between model outputs. For code: run 3 Haiku/Sonnet instances, aggregate with a lightweight aggregator.

**Quality recovery:**
- Ensembles with sufficient diversity surpass the strongest individual model and approach human expert annotation consistency
- RouteLLM: 85% cost reduction while maintaining 95% quality (production routing, not pure ensemble)
- MixLLM: 97.25% of GPT-4's quality at 24.18% of cost
- Simple majority vote fails for code (non-discrete outputs) — requires an LLM aggregator call

**Cost multiplier:** N models = Nx cost plus aggregator. Practical minimum: 3x. Partially offset if cheap models are Haiku-class.

**Latency impact:** Parallel execution → same as single call. Sequential: Nx.

**Applicability to Claude Code:** Moderate. Works well for classification/routing decisions in Brain itself. For code generation, majority voting requires test execution or an LLM aggregator — adds complexity.

**Implementation difficulty:** 3. Simple majority vote: easy. OW/ISP aggregation: requires calibration data and more infrastructure.

---

### 8. Agent-as-Orchestrator (Hierarchical Delegation)

**Source:** arXiv:2506.12508 (AgentOrchestra, June 2025); arXiv:2602.03786 (AOrchestra, February 2026); arXiv:2506.02153 (Small LMs as Future of Agentic AI, June 2025)

**Mechanism:** Reverse the cost hierarchy: a small, fast model serves as the orchestrator (task decomposition, routing, state tracking), while large expensive models execute only the specific subtasks that require their capability. AOrchestra achieves 16.28% relative improvement vs strongest baseline with Gemini-3-Flash. CoAct-1 uses o3 for orchestration, o4-mini for programming, and a vision model for GUI — each specialized.

**Quality recovery:** AOrchestra: +16.28% relative improvement on complex task benchmarks vs single strong model. Small model coordination reduces hallucinations in planning by keeping high-capability model focused on execution.

**Cost multiplier:** 0.4–0.7x depending on how often expensive model is called. Orchestrator (cheap) can handle 60–80% of turns; expensive model handles key execution moments only.

**Latency impact:** Additional orchestration call: +200–500ms. Offset by fewer expensive model calls.

**Applicability to Claude Code:** High strategic fit for Brain v2. Haiku as orchestrator for routing/planning/state, Sonnet for code execution, Opus only for novel problems. Maps directly onto existing Claude Code subagent architecture.

**Implementation difficulty:** 3. Requires defining clean interfaces between orchestrator and executor roles. Harder than it sounds because agentic loops don't have natural decomposition points.

---

### 9. Chain-of-Draft (Efficient Structured Reasoning)

**Source:** arXiv:2502.18600 (Chain of Draft, February 2025)

**Mechanism:** A CoT variant where the model produces concise intermediate steps ("drafts") rather than verbose full reasoning. Reduces token cost of reasoning by 5–7.5x vs standard CoT while maintaining comparable accuracy. For code: produce pseudocode or step sketches, then implement from sketch. Graph-of-Thought and Tree-of-Thought extensions offer better accuracy on branching problems but at higher token cost.

**Quality recovery:** CoT consistently improves coding quality vs no CoT. The Sonnet→Opus gap on novel problems is ~10 pts. CoT alone narrows this by ~30–40% on structured problems. Chain-of-Draft achieves this at much lower token cost than full CoT.

**Cost multiplier:** 1.5–2x (draft tokens cheap; shorter than full CoT). Full CoT: 3–5x. Tree-of-Thought: 5–10x.

**Latency impact:** Chain-of-Draft: +30–60% of base latency. Full ToT: +3–10x.

**Applicability to Claude Code:** Medium. CoD is essentially what extended thinking already does internally. As an explicit prompt technique, useful for Haiku on structured coding tasks where extended thinking isn't available or too expensive.

**Implementation difficulty:** 1. Pure prompt engineering.

---

### 10. LLM-as-Judge with Calibration

**Source:** ICLR 2025 "Trust or Escalate" paper; AXIOM framework arXiv:2512.20159; Judge's Verdict OpenReview 2025

**Mechanism:** A separate model (same or larger) evaluates the primary model's output for quality, providing a verification signal that can trigger re-generation or escalation. For code: judge evaluates correctness, style, security compliance. LLM judges achieve 80–90% human agreement in general tasks; AXIOM specifically addresses code quality calibration.

**Quality recovery:** Judge + escalate can recover 50–70% of quality gap on tasks where judge accuracy is high. Degrades to 60–70% accuracy in specialized domains (vs 80%+ general).

**Cost multiplier:** 1.5–2x (judge call for every generation).

**Latency impact:** +1 sequential model call. ~+2–5 seconds.

**Applicability to Claude Code:** Best as a pre-escalation gate in the cascade. "Cheap model generates → judge evaluates → escalate if below threshold" is lower-cost than always escalating.

**Implementation difficulty:** 2. Requires prompt engineering the judge for code-specific rubrics.

---

## Failure Modes: Techniques That Look Good But Fail

1. **Naive iterative self-refinement without external signal:** 37.6% increase in security vulnerabilities after 5+ iterations (IEEE 2025). The model optimizes for what the critic notices, not holistic correctness. Critic must be grounded in test execution.

2. **High-N self-consistency for code:** Above 5–10 samples, all new paths are redundant (arXiv:2511.00751). Token cost scales linearly; quality improvement plateaus. N=5 is the practical ceiling.

3. **Static keyword routing (what Brain v1 does):** Cannot detect difficulty within a category. "Write a Python function" can be trivial or PhD-level. Keyword routing has no difficulty signal, only domain signal.

4. **Majority voting on free-form code:** Syntactically different code can be semantically identical, and majority vote can't detect this. Requires test execution or semantic diffing — not just text matching.

5. **Few-shot distillation with mismatched examples:** If the few-shot examples don't match the task structure closely, they backfire by confusing the model. Example retrieval quality determines whether this technique helps or hurts.

6. **LLM-as-judge for novel problems:** Judge accuracy drops from 80%+ to 60% on specialized technical domains (2025 literature). Judge models make confident wrong calls. Always pair with statistical calibration or human spot-checks.

---

## Top 5 by Implementation ROI (Quality Recovery > 50% of Gap)

Ranked by: (quality_recovery × applicability) / implementation_difficulty

| Rank | Technique | Gap Closed | Cost Mult | Impl. Diff | ROI Score |
|---|---|---|---|---|---|
| 1 | Extended Thinking | >100% on SWE-bench (Sonnet beats Opus at 200K budget) | 1.5–3x | 1 | A+ |
| 2 | Architect-Editor Split | 2.3pts over expensive baseline at 14x cheaper | 1.3–1.5x | 2 | A |
| 3 | Verification Cascade | 50–98% cost reduction at equivalent quality | 0.1–0.5x typical | 3 | A |
| 4 | Prompt-Based Distillation | 5–15% relative lift, near-zero marginal cost with caching | 1.01–1.3x | 2 | A- |
| 5 | Critic-Refine (2–3 rounds) | +7–22 pts on specific code benchmarks | 2–3x | 2 | B+ |

---

## Recommended Stack for Brain v2.0

### Layer 0 — Classification (Haiku, ~$0.0003/call)
Route by: domain + estimated complexity score (not just keywords). Use BEST-Route style difficulty estimation from query embedding. Output: {domain, difficulty: low/medium/high, novel: bool}.

### Layer 1 — Cheap-with-Amplification (Sonnet 4.6, standard)
For low/medium difficulty tasks. Apply prompt-based distillation (cached few-shot examples from Opus library). Skip extended thinking. Target: 80%+ of Opus quality at 1/5 the cost.

### Layer 2 — Sonnet with Extended Thinking (32K budget)
For medium-high difficulty, structured tasks. Budget_tokens: 32K. Expect +4–6 points quality lift over standard Sonnet. Cost: ~$0.096 extra per call. Apply when confidence from Layer 1 is below threshold.

### Layer 3 — Architect-Editor (Sonnet architect + Sonnet/Haiku editor)
For refactoring, multi-file edits, and complex code generation. Architect writes plan in structured format. Editor executes. Prevents hallucination drift in long tasks.

### Layer 4 — Opus 4.6 (full, or with extended thinking)
Reserved for: novel algorithms, GPQA-class scientific reasoning, multi-hop planning where Layer 2 has failed twice. Hard cap to prevent budget bleed.

### Verification Layer (cross-cutting)
After any code generation: test-execution verification before committing. If tests fail: trigger 1–2 rounds of critic-refine (error message as critic signal). If still failing after 2 rounds: escalate to next layer rather than infinite looping.

---

## Minimal Quality-Amplification Recipe for Code Tasks

For a single coding task routed to Sonnet:

```
1. Classify task difficulty (Haiku, ~50 tokens)
   - High confidence + low difficulty → skip to step 4
   - Medium/high difficulty → continue

2. Inject 2–3 cached few-shot examples (Opus-generated, matching task type)
   - Cost: ~$0.001 amortized with cache hit
   
3. Set extended thinking budget:
   - Medium difficulty: budget_tokens = 16000
   - High difficulty: budget_tokens = 32000
   
4. Generate with Sonnet 4.6

5. Run tests (if available):
   - Pass → done
   - Fail → extract error, run 1–2 critic-refine rounds (Sonnet, same context)
   - Still failing after 2 rounds → escalate to Opus

Expected outcome:
- 70% of tasks: Sonnet standard (no amplification needed)
- 20% of tasks: Sonnet + extended thinking (closes to within 1–2pts of Opus)
- 8% of tasks: architect-editor or critic-refine (closes most remaining gap)
- 2% of tasks: genuine Opus escalation (novel/scientific/high-stakes)
```

---

## Key Numbers Summary

| Claim | Source | Number |
|---|---|---|
| Sonnet 4.6 vs Opus 4.6 SWE-bench gap | Anthropic 2026 | 1.2 pts (79.6 vs 80.8) |
| Sonnet 4.5 + extended thinking (200K) vs Opus 4.6 | Anthropic SWE-bench 2025 | +1.2 pts OVER Opus |
| R1+Sonnet vs o1 on Aider Polyglot | aider.chat Jan 2025 | 64% vs 61.7% at 14x lower cost |
| FrugalGPT cost reduction at equivalent quality | arXiv:2305.05176 | Up to 98% |
| Critic-refine quality lift (GPT-4o-mini) | LLMLOOP/ICSME 2025 | +21.76 pts assertion correctness |
| Self-consistency diminishing returns plateau | arXiv:2511.00751 | ~5–10 agents |
| ReASC cost reduction vs baseline self-consistency | arXiv:2601.02970 | 70% |
| Security vulnerability increase from over-iteration | IEEE-ISTAS 2025 | +37.6% after 5 iterations |
| AOrchestra quality improvement vs best baseline | arXiv:2602.03786 | +16.28% relative |
| MixLLM quality vs GPT-4 at fraction of cost | arXiv survey 2026 | 97.25% quality at 24.18% cost |

---

## Sources

- [arXiv:2601.02970 — ReASC, January 2026](https://arxiv.org/abs/2601.02970)
- [arXiv:2502.06233 — CISC, February 2025](https://arxiv.org/abs/2502.06233)
- [arXiv:2511.00751 — Reevaluating Self-Consistency in Multi-Agent Systems](https://arxiv.org/html/2511.00751)
- [arXiv:2203.11171 — Wang et al. Self-Consistency original](https://arxiv.org/abs/2203.11171)
- [arXiv:2305.05176 — FrugalGPT](https://arxiv.org/abs/2305.05176)
- [arXiv:2310.12963 — AutoMix](https://arxiv.org/abs/2310.12963)
- [arXiv:2511.07396 — C3PO, NeurIPS 2025](https://arxiv.org/abs/2511.07396)
- [arXiv:2502.09183 — RefineCoder, February 2025](https://arxiv.org/html/2502.09183v1)
- [arXiv:2507.06920 — Verification for LLM Code Generation](https://arxiv.org/abs/2507.06920)
- [arXiv:2506.11022 — Security Degradation in Iterative AI Code Generation, IEEE-ISTAS 2025](https://arxiv.org/html/2506.11022)
- [arXiv:2510.01499 — Beyond Majority Voting, October 2025](https://arxiv.org/abs/2510.01499)
- [arXiv:2602.03786 — AOrchestra, February 2026](https://arxiv.org/html/2602.03786v1)
- [arXiv:2506.02153 — Small LMs as Future of Agentic AI](https://arxiv.org/pdf/2506.02153)
- [arXiv:2506.12508 — AgentOrchestra](https://arxiv.org/html/2506.12508v1)
- [arXiv:2502.18600 — Chain of Draft](https://arxiv.org/pdf/2502.18600)
- [arXiv:2603.04445 — Dynamic Model Routing Survey, 2026](https://arxiv.org/abs/2603.04445)
- [aider.chat — R1+Sonnet SOTA blog post](https://aider.chat/2025/01/24/r1-sonnet.html)
- [Anthropic — Introducing Claude Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5)
- [Anthropic — Extended Thinking Docs](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [NxCode — Claude Sonnet 4.6 Complete Guide 2026](https://www.nxcode.io/resources/news/claude-sonnet-4-6-complete-guide-benchmarks-pricing-2026)
- [Medium — Sonnet 4.6 Nearly Matches Opus](https://medium.com/@cognidownunder/claude-sonnet-4-6-nearly-matches-opus-and-it-costs-one-fifth-the-price-3fac116b12fd)
- [AXIOM — arXiv:2512.20159](https://arxiv.org/html/2512.20159v1)
