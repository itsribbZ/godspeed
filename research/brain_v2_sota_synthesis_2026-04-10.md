# Brain v2.0 — SOTA Research Synthesis

**Session:** 2026-04-10 evening (Brain v2.0 deep research)
**Trigger:** the user asked for SOTA rigor, rooted in Anthropic's `/advisor` base framework, zero gaps, improves with ecosystem
**Agents deployed:** 5 parallel Sonnet subagents (`advisor`, `quality amplification`, `orchestration`, `eval`, `feedback loops`)
**Research duration:** ~5 minutes wall time (parallel burst)

---

## Part 1: The `advisor_20260301` Discovery (Agent 1)

### Headline
**Anthropic shipped `advisor_20260301` on April 9, 2026.** The real base framework the user pointed us at is a server-side API tool, not a Claude Code slash command.

- **Docs:** https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool
- **Launch blog:** https://claude.com/blog/the-advisor-strategy (2026-04-09)
- **Beta header:** `anthropic-beta: advisor-tool-2026-03-01`
- **NOT a Claude Code slash command** — the community `/advisor` reference is premature/third-party

### The Inversion Pattern
Classical routing: *big model decomposes → delegates to small models*.
Advisor pattern: **small model DRIVES end-to-end, selectively escalates to Opus when stuck**.

### Architecture
```
Tool type: advisor_20260301
Supported pairs:
  - Haiku 4.5 executor   → Opus 4.6 advisor
  - Sonnet 4.6 executor  → Opus 4.6 advisor
  - Opus 4.6 executor    → Opus 4.6 advisor (self-review)
```

**Tool definition in request body:**
```json
{
  "type": "advisor_20260301",
  "name": "advisor",
  "model": "claude-opus-4-6",
  "max_uses": 3,
  "caching": {"type": "ephemeral", "ttl": "5m"}
}
```

**Escalation mechanics:**
1. Executor emits `server_tool_use` block with EMPTY input
2. Anthropic runs Opus inference SERVER-SIDE with full transcript
3. Opus's thinking blocks stripped before return
4. Executor receives `advisor_tool_result` with plaintext advice
5. All in ONE `/v1/messages` request — no client-side orchestration

**Cost accounting:**
- Advisor tokens billed at Opus rates, executor at Sonnet/Haiku rates
- Reported separately in `usage.iterations[]` (type: `advisor_message` vs `message`)
- Typical advisor output: 400-700 text tokens (1,400-1,800 with thinking)

**Published benchmarks:**
| Benchmark | Config | Score | Delta |
|---|---|---|---|
| SWE-bench Multilingual | Sonnet 4.6 + Opus advisor | **74.8%** | +2.7pp over Sonnet solo, −11.9% cost vs Opus solo |
| BrowseComp | Haiku 4.5 + Opus advisor | **41.2%** | +109% over Haiku solo, −85% cost vs Sonnet |
| Terminal-Bench 2.0 | Sonnet + Opus advisor | improved | lower per-task cost |

### What This Means for Brain
**The Brain classifier is fundamentally different from the advisor pattern.** Brain v1 is a CLASSIFIER that picks a model BEFORE the task runs. Advisor is an INVERSION that lets the small model decide DURING the task whether to consult Opus.

These are COMPLEMENTARY, not competitive:
- **Brain layer:** session-level and skill-level model assignment (which model DRIVES)
- **Advisor layer:** turn-level escalation during a task (when to consult Opus mid-execution)

Brain v2.0 should:
1. Continue classifier-based Zone 2 routing (unchanged — works well)
2. ADD an `[advisor]` config section that prepares for native Claude Code `/advisor` integration when it ships
3. Track which sessions would benefit from advisor mode (S3+ with multi-step characteristics)
4. Document the pattern so future work can wire it up when Claude Code adds the native command

**Key constraint:** Claude Code doesn't natively expose `advisor_20260301` yet. We CAN'T use it from hooks or skills today. But we CAN position Brain to slot it in when the native integration lands — that's ecosystem improvement by design.

---

## Part 2: Quality Amplification SOTA (Agent 2)

### The Gap Being Closed (task-dependent)
| Task | Opus-Sonnet Gap | Amplification strategy |
|---|---|---|
| SWE-bench Verified | 1.2 pts | Already closed — no amplification needed |
| Terminal/Agentic | 6.3 pts | Extended thinking + few-shot |
| Novel Problem | 10.5 pts | Architect-editor + critic-refine |
| Aider Polyglot | ~11 pts | Architect-editor (proven +10.3 pts) |
| GPQA Diamond | 17.2 pts | **Cannot amplify — route to Opus** |

### Top 5 Techniques (Ranked by ROI)

**1. Extended Thinking — drop-in, beats Opus on SWE-bench**
- Sonnet 4.5 + 200K thinking budget: **82.0% SWE-bench** vs Opus 4.6's 80.8%
- API: `thinking: {type: "enabled", budget_tokens: 32000}`
- Cost: ~$0.096 extra per call at 32K budget
- Difficulty: **1/5** (drop-in)
- **Brain v2 integration: add `extended_thinking_budget` to tier_map entries**

**2. Architect-Editor Split — proven production pattern**
- R1 (architect) + Sonnet (editor) = 64.0% Aider Polyglot vs o1's 61.7% at **14× lower cost** (aider.chat Jan 2025)
- Opus plans in prose → Sonnet translates to edit blocks
- Difficulty: **2/5**
- **Brain v2 integration: add `architect_mode` flag per tier, document the handoff pattern**

**3. Verification Cascade (FrugalGPT / C3PO)**
- C3PO (NeurIPS 2025): conformal prediction bounds cost with formal guarantees
- FrugalGPT: 98% cost reduction at matched accuracy
- AutoMix POMDP router: <1ms overhead
- Difficulty: **3/5** (requires calibrated quality scorer)
- **Brain v2 integration: deferred to v3 — needs eval harness first**

**4. Few-Shot Anchoring (Prompt-Based Distillation)**
- Load Sonnet with 2-3 Opus-quality examples matching task type
- 5-15% quality lift on matching tasks
- With prompt caching: marginal cost ~1.01-1.03× after first use
- Difficulty: **2/5**
- **Brain v2 integration: `examples/` library, referenced by tier when task matches pattern**

**5. Critic-Refine with Test Execution**
- GPT-4o-mini + error feedback: +21.76 pts assertion correctness
- **HARD CAP 2-3 rounds** — 5+ rounds causes 37.6% more security vulnerabilities (IEEE-ISTAS 2025)
- Use test execution as critic signal, NOT LLM self-critique
- Difficulty: **2/5**
- **Brain v2 integration: deferred — requires tool execution integration**

### Failure Modes (confirmed killers)
1. **Naive self-refinement > 3 rounds** → security degradation (37.6% more CVEs)
2. **High-N self-consistency > N=5** → plateau, no gain
3. **Static keyword routing** (= current Brain v1 limitation)
4. **Majority voting on code** — syntactic diversity ≠ semantic equivalence
5. **LLM-as-judge on novel domains** — drops from 80%+ to 60% accuracy

### Recommended v2.0 Layering
```
Layer 0: Brain classifier (assigns tier + effort + thinking budget)
Layer 1: Sonnet + 2-3 cached few-shot examples (70% of tasks)
Layer 2: Sonnet + extended thinking 32K (20% of tasks)
Layer 3: Architect-editor split via opusplan (8% of tasks)
Layer 4: Opus escalation — genuine S4/S5 (2% of tasks)

Cross-cutting: test execution as quality gate where possible
               critic-refine capped at 2 rounds, test-signal only
```

---

## Part 3: Multi-Model Orchestration (Agent 3)

### Aider's architect_coder.py — The Canonical Handoff

```python
# architect_coder.py: reply_completed()
editor_model = self.main_model.editor_model or self.main_model

kwargs["main_model"] = editor_model
kwargs["edit_format"] = self.main_model.editor_edit_format
kwargs["map_tokens"] = 0          # editor does NOT see repo map
kwargs["cache_prompts"] = False

editor_coder = Coder.create(**new_kwargs)
editor_coder.cur_messages = []    # editor gets BLANK message history
editor_coder.done_messages = []
editor_coder.run(with_message=content, preproc=False)
# ^^ architect's prose output becomes editor's sole input
```

**Critical insight:** Architect sees the full repo map + chat history. Editor sees ONLY the architect's prose + files in chat. Context is PARTITIONED, not shared.

### Prompt Engineering Per Role
- **Architect system prompt:** "Act as an expert architect engineer and provide direction to your editor engineer. DO NOT show the entire updated function/file."
- **Editor system prompt:** "Act as an expert software developer. Output a copy of each file that needs changes." (or SEARCH/REPLACE blocks)

### Benchmark Receipts
| Config | Aider Polyglot |
|---|---|
| o1-preview solo | 79.7% |
| o1-preview architect + DeepSeek editor | **85%** (SOTA at time) |
| Claude Opus 4 + Claude Sonnet 4 (editor) | + about +3 pts vs Opus solo |
| o1-mini solo | 61.1% |
| o1-mini + DeepSeek editor | 71.4% (+10.3 pts, biggest win) |

### Top 3 Patterns to Adopt
1. **Reasoning/Editing Split** — architect in prose, editor in format
2. **Per-Agent Model Assignment** — explicit dict not heuristics (OpenHands `agent_to_llm_config` pattern)
3. **Specialized Apply/Format** — tiny model for mechanical format conversion (Continue.dev apply role)

### Brain v2 Concrete Integration
```
Task arrives -> Brain classifier -> tier
  If tier in {S3, S4, S5} AND multi_file AND code_edit:
    architect_mode = True
    advisory: "Consider /model opusplan for this turn (Opus plans, Sonnet edits)"
  Else:
    normal routing
```

This is **documentation + advisory**, not automation, because hooks can't split the main session. But Brain can DETECT when architect mode would help and recommend it.

---

## Part 4: Eval Methodology (Agent 4)

### The Minimum Viable Eval Harness
**N = 30 prompts × 3 categories:**
- **Category A (15):** Coding tasks in Sonnet-safe zone — expect Sonnet ≈ Opus
- **Category B (10):** Reasoning tasks in Opus cliff zone — expect Opus > Sonnet
- **Category C (5):** Trivial tasks in Haiku-safe zone — expect all equivalent

### Scoring
- LLM-as-judge (Sonnet-as-judge) with explicit rubric: correctness (0-3), completeness (0-3), reasoning depth (0-2), code quality (0-2)
- Position-swap averaging to eliminate order bias
- Category C: exact match, no judge needed

### Cost
- 30 prompts × 3 models × ~500 tokens = 45K tokens
- 30 judge calls × ~1K tokens = 30K tokens
- Total: ~75K tokens @ Sonnet pricing = **~$0.23 per eval run**
- Weekly: **$1/month**

### Sensitivity
- 30 prompts: detect ~7-8% quality delta reliably
- 50 prompts: detect 5% with 80% power
- 7-8% is acceptable for Brain's cliffs (17.2pt GPQA, 10.6pt Aider)

### Regression Alerts for `brain scan`
1. **Tier distribution shift:** S0+S1+S2 % > 1.5σ above 4-week baseline
2. **Correction rate spike:** redo-keyword detection > 2× baseline for 3+ days
3. **Guardrail fire rate drop:** < 5% of sessions (expected 15-25%)
4. **Eval score delta:** Category B scores > 0.8 below baseline

### Shadow Routing (Optional, One-Time)
- Run Brain recommendation AND Opus in parallel for 5% of Zone 2 calls
- Collect 50 shadow pairs, judge batch, validate
- Cost: ~$1.80 one-time

---

## Part 5: Feedback Loop Design (Agent 5)

### At the user's Scale (200-300 sessions/month)
**NOT viable:**
- LinUCB / contextual bandits (exploration penalty, requires ~2 months to converge)
- Neural bandits (underdetermined regime, matrix instability)
- Full preference-pair training (need 1000s of pairs)

**VIABLE:**
- **EWMA weight updater** — alpha=0.005, nudges manifest weights from override events
- **Skill auto-bump rule** — 5 consecutive overrides → bump tier deterministically
- **Thompson Sampling signal tracker** — Beta(alpha, beta) per signal, reports accuracy in scan

### Implicit Signals (from existing telemetry)
- **Model override**: `recommended_model != model_used_in_next_tool_call` = negative label
- **Correction follow-up**: next prompt contains "fix", "that's wrong", "you missed", "redo"
- **Session abandonment**: subagent session ends with no further interaction (neutral)
- **Acceptance depth**: follow-up BUILDS on output (positive) vs REPLACES it (negative)

### Explicit Signal UX
```
brain good    # mark last decision as positive (+10 weight)
brain bad     # mark last decision as negative (+10 weight, inverted)
```
Two commands, ~20 lines each, no popups, no friction.

### Calibration
- Track outcome rate per score bucket (0.05 width)
- Decisions within 0.05 of tier boundary → flag `[low confidence]`
- Full isotonic regression deferred to v3 (needs 6+ months data)

### Drift Alert
- 7-day vs 30-day tier distribution check
- If any tier drifts > 10pp, `brain scan` emits warning
- Increase EWMA alpha during drift for faster adaptation

---

## Part 6: The Path to Zero Gaps

The phrase "0 gaps" means: no scenario where Brain routes incorrectly AND we don't know it happened.

**Current gaps (v1):**
1. Novel reasoning without keywords → mis-routed silently
2. Short code-edit prompts → fixed in v1 tune (`code_edit_floor`)
3. No feedback loop → static forever
4. No confidence score → silent low-confidence decisions
5. No multi-turn context → each prompt isolated
6. No session budget → runaway cost possible
7. No quality measurement → claims without proof
8. No ecosystem integration with `advisor_20260301`
9. No extended thinking budgets
10. No architect-editor awareness

**v2 closes gaps 3, 4, 5, 6, 9, 10 fully and 1, 7 partially (via telemetry + advisory).**
**v3 closes gaps 7, 8 fully (eval harness + native advisor integration).**

Gap 2 is already closed. Gaps 7 and 8 require external work (eval infra, Claude Code native `/advisor` rollout) — can be designed now, implemented when ready.

---

## Sources (first-party only)

### Anthropic
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool
- https://claude.com/blog/the-advisor-strategy
- https://code.claude.com/docs/en/model-config
- https://platform.claude.com/docs/en/build-with-claude/extended-thinking
- https://www.anthropic.com/news/claude-sonnet-4-5
- https://www.anthropic.com/news/claude-opus-4-6

### Research Papers
- https://arxiv.org/abs/2305.05176 — FrugalGPT
- https://arxiv.org/abs/2406.18665 — RouteLLM
- https://arxiv.org/abs/2404.14618 — Hybrid LLM
- https://arxiv.org/abs/2310.12963 — AutoMix
- https://arxiv.org/abs/2511.07396 — C3PO (NeurIPS 2025)
- https://arxiv.org/abs/2502.09183 — RefineCoder
- https://arxiv.org/abs/2506.11022 — Security degradation in refinement
- https://arxiv.org/abs/2603.04445 — Dynamic routing survey 2026

### Production Tools (source code)
- https://github.com/paul-gauthier/aider/blob/main/aider/coders/architect_coder.py
- https://github.com/All-Hands-AI/OpenHands
- https://github.com/cline/cline
- https://github.com/BerriAI/litellm
- https://aider.chat/2025/01/24/r1-sonnet.html
- https://aider.chat/2024/09/26/architect.html
- https://aider.chat/docs/leaderboards/

---

*This synthesis is the source of truth for Brain v2.0 implementation. Deviations from it must be explicitly justified.*
