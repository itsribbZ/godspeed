# Agent E — Opus vs Sonnet vs Haiku Quality Deltas

Research burst for Toke Brain — 2026-04-10. The guardrail doc: where is routing down safe, where is it dangerous?

---

## Part 1: Benchmark Tables

### 1. SWE-bench Verified
Real GitHub issues — model must identify root cause, write patch, pass tests. Closest proxy to "edit production code correctly."

| Model | Score | Delta vs Opus |
|-------|-------|---------------|
| Opus 4.6 | **80.8%** | — |
| Sonnet 4.6 | **79.6%** | −1.2 pts |
| Haiku 4.5 | **73.3%** | −7.5 pts |

**Verdict:** Sonnet → Opus = **SAFE**. Haiku → Opus = **BORDERLINE** for production patches.

Sources: morphllm.com/claude-benchmarks, benchlm.ai/models/claude-opus-4-6, llm-stats.com

---

### 2. Aider Polyglot
225 Exercism exercises across C++, Go, Java, JS, Python, Rust. The gold standard for "can this model edit real code." Most relevant single benchmark for the user's workload.

| Model | Score | Delta vs Opus |
|-------|-------|---------------|
| Opus 4.5 (proxy for 4.6) | **89.4%** | — |
| Sonnet 4.5 (proxy for 4.6) | **78.8%** | −10.6 pts |
| Haiku 4.5 | **~28-35%** | **−55+ pts** |

Note: Aider leaderboard hasn't published 4.6-specific runs. 4.5→4.6 upgrade maintained/improved scores elsewhere.

**Verdict:** Sonnet → Opus = **BORDERLINE** (10.6 pts meaningful for polyglot multi-file). Haiku = **DANGEROUS** for any real code editing.

Sources: aider.chat/docs/leaderboards, huggingface Laser585/claude-4-benchmarks

---

### 3. τ-bench / Tau2 / Terminal-Bench (Agentic)
Multi-step tool use — critical for the user's tool-heavy sessions.

| Model | Tau2 Retail | Tau2 Telecom | Tau2 Airline | Terminal-Bench 2.0 |
|-------|------------|-------------|-------------|-------------------|
| Opus 4.6 | ~91% | ~98% | ~75% | **65.4%** |
| Sonnet 4.6 | **91.7%** | **97.9%** | ~72% | **59.1%** |
| Haiku 4.5 | **83.2%** | **83.0%** | **63.6%** | **41.0%** |

**Verdict:** Terminal-Bench gap of 6.3 pts = **BORDERLINE** for unattended agentic runs. Haiku 24.4 pt drop = **DANGEROUS** for multi-step tool sessions.

Sources: llm-stats.com comparison, morphllm.com/claude-code-models

---

### 4. MMLU / MMLU-Pro
Broad academic knowledge across 57 subjects.

| Model | MMLU | MMLU-Pro |
|-------|------|---------|
| Opus 4.6 | **92.1%** | **89.1%** |
| Sonnet 4.6 | **89.7%** | **89.3%** |
| Haiku 4.5 | **80.4-90.8%** (noisy) | **83.0%** |

**Verdict:** Nearly saturated at top. Sonnet → Opus = **SAFE**. Haiku = **SAFE for factual lookup**.

---

### 5. HumanEval (Python Functions)
Largely saturated.

| Model | HumanEval |
|-------|-----------|
| Opus 4.6 | **97.6%** |
| Sonnet 4.6 | **96.8%** |
| Haiku 4.5 | **92.0%** |

**Verdict:** Too saturated to differentiate. Don't use for routing decisions.

---

### 6. GPQA Diamond — THE CLIFF
PhD-level science reasoning (chemistry, biology, physics). Not Googleable. Hardest general-reasoning benchmark besides math.

| Model | Score | Delta vs Opus |
|-------|-------|---------------|
| Opus 4.6 | **91.3%** | — |
| Sonnet 4.6 | **74.1%** | **−17.2 pts** |
| Haiku 4.5 | **73.0%** | **−18.3 pts** |

**VERDICT: THE CLIFF.** 17.2 pt drop Opus→Sonnet is the single largest delta across all benchmarks. For deep research synthesis, hard architectural reasoning, complex debugging — **Sonnet is DANGEROUS**. Haiku ≈ Sonnet here, so GPQA is an Opus-only domain.

**Sonnet and Haiku fall off the same cliff.** Sonnet has no meaningful edge over Haiku on hard reasoning.

Sources: nxcode.io Sonnet4.6 vs Opus4.6 comparison, llm-stats.com, morphllm.com

---

### 7. Math (AIME 2025, MATH-500)

| Model | AIME 2025 | MATH-500 |
|-------|-----------|---------|
| Opus 4.6 | **99.8%** (extended thinking) | **91.5-97.1%** |
| Sonnet 4.6 | ~87-90% | **86.4%** |
| Haiku 4.5 | **80.7%** | **74.2-95.3%** (noisy) |

**Verdict:** Sonnet = **SAFE** for most math. Haiku = **BORDERLINE** without extended thinking. AIME-level = Opus, but unlikely the user workload.

---

### 8. Anthropic Published Headlines (2026 Launches)

**Opus 4.6 (Feb 5, 2026):** 1M context, Terminal-Bench 2.0 leader (65.4%), MRCR v2 76% (vs Sonnet 4.5's 18.5%), BrowseComp leader, GDPval-AA Elo +144 vs GPT-5.2, "near 2× better" on life sciences vs Opus 4.5.

**Sonnet 4.6 (Feb 17, 2026):** SWE-bench 79.6% (within 1.2 pts Opus), OSWorld-Verified 72.5% (tied with Opus), OfficeQA matches Opus, 70% user preference over Sonnet 4.5, priced $3/$15 vs Opus $5/$25.

**Haiku 4.5 (Oct 15, 2025):** SWE-bench 73.3%, Terminal-Bench 41.0%, "matches Sonnet 4 performance at 1/3 cost." OSWorld 50.7%.

---

## Part 2: the user's Task Categories → Safe Floor

| Task category | Safe floor | Reasoning | Confidence |
|--------------|-----------|-----------|------------|
| Quick factual lookup | **Haiku 4.5** | MMLU ~83-90%, near-saturated | HIGH |
| Shell command generation | **Haiku 4.5** | HumanEval 92%, adequate | HIGH |
| Code editing (single file, known context) | **Sonnet 4.6** | SWE-bench −1.2 pts from Opus | MEDIUM |
| Multi-file refactor (polyglot, UE5 C++) | **Opus 4.6** | Aider polyglot ~10 pt gap; UE5 macros compound errors | HIGH |
| Architecture scoring (7 Laws, blueprints) | **Opus 4.6** | GPQA Diamond cliff; deep reasoning under uncertainty | HIGH |
| Debug root cause | **Sonnet 4.6** | SWE-bench nearly equal. Opus when trace ambiguous | MEDIUM |
| Research synthesis (multi-source) | **Opus 4.6** | GPQA cliff applies; cross-domain reasoning | HIGH |
| Creative / game design structure | **Opus 4.6** | GPQA proxy — novel reasoning, not memorized | MEDIUM |
| Tool-heavy agentic session | **Sonnet 4.6** | Terminal-Bench −6.3 pts. Anthropic positions Sonnet 4.6 for agentic. Opus for overnight unattended. | MEDIUM |
| Python scripting / automation | **Sonnet 4.6** | HumanEval saturated, SWE-bench small gap | HIGH |
| Documentation / skill writing | **Sonnet 4.6** | Instruction-following, no hard reasoning | HIGH |

---

## Part 3: The Four Known Cliffs

### CLIFF 1 — GPQA Diamond (Opus→Sonnet: −17.2 pts)
**HARD STOP.** Sonnet and Haiku nearly identical (74.1 vs 73.0). Sonnet has no edge over Haiku on hard reasoning. Route hard research/architecture/novel debugging to Opus only.

### CLIFF 2 — Aider Polyglot (Opus→Sonnet: ~−10 pts, Opus→Haiku: ~−55+ pts)
Real-world code editing. Haiku completely disqualified for code editing (28-35%). Sonnet's 10 pt gap real but not catastrophic for single-file. Multi-file refactor or complex UE5 → Opus.

### CLIFF 3 — Terminal-Bench 2.0 (Opus→Sonnet: −6.3 pts, Opus→Haiku: −24.4 pts)
Haiku disqualified for agentic sessions. Sonnet 4.6 borderline for unattended overnight — 59.1% is meaningful failure rate for complex tool chains.

### CLIFF 4 — Long-Context Retrieval (MRCR v2: Opus 76%, Sonnet 4.5 18.5%)
**Catastrophic.** For tasks requiring reasoning across 1M-token context (entire codebase, long session history), Opus is not optional. Sonnet 4.6 numbers not yet published separately, but Opus 4.6's edge was headline launch capability.

---

## The Bottom Line

**Route to Haiku only when:** Purely lookup/command/summarization. No synthesis requirement. Context < 10K tokens. No code editing.

**Route to Sonnet when:** Single-file code edits, Python scripting, documentation, known-pattern debugging, most agentic sessions during active work. **Covers ~60-70% of the user's session time.**

**HARD-route to Opus when:**
- Multi-file C++ refactor
- Any UE5 architectural decision
- Novel debugging (ambiguous root cause)
- Research synthesis combining multiple sources
- GDD mechanics evaluation
- Overnight unattended agentic runs
- Any task where 1M context is load-bearing

**Core tradeoff:** Sonnet 4.6 is $3/$15 vs Opus 4.6 $5/$25 (40% input savings, 40% output savings). Quality cost is **negligible on structured coding** (1.2 pts SWE-bench) and **severe on hard reasoning** (17.2 pts GPQA Diamond).

**The Brain must be conservative: default to Sonnet, escalate to Opus on any task triggering reasoning/research/multi-file flags. Better to overpay for Opus than silently degrade quality.**

---

## Confidence Summary
- **HIGH:** SWE-bench (multiple sources), GPQA Diamond (consistent across morphllm/nxcode/benchlm), Terminal-Bench (Anthropic launch blog direct)
- **MEDIUM:** Aider polyglot (4.6-specific runs not published, using 4.5 proxy), Tau2 Opus 4.6 (extrapolated)
- **LOW:** MMLU exact numbers (wide variance), MATH (depends on thinking budget)

---

## Sources
- https://www.anthropic.com/news/claude-opus-4-6
- https://www.anthropic.com/news/claude-sonnet-4-6
- https://www.anthropic.com/claude/haiku
- https://aider.chat/docs/leaderboards/
- https://www.morphllm.com/claude-benchmarks
- https://www.morphllm.com/claude-code-models
- https://benchlm.ai/models/claude-opus-4-6
- https://llm-stats.com/models/compare/claude-sonnet-4-6-vs-claude-haiku-4-5-20251001
- https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026
- https://tokencalculator.com/llm-benchmarks
- https://thenewstack.io/claude-sonnet-46-launch/
- https://venturebeat.com/technology/anthropics-sonnet-4-6-matches-flagship-ai-performance-at-one-fifth-the-cost
