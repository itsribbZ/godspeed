# Pipeline Stage 3 — Intent Routing

> **Goal:** document how Claude Code decides which tools, skills, and subagents to invoke after context assembly (Stage 2), and map the boundary between Claude Code's authoritative routing and Brain's advisory layer.

**Status:** first-pass research with receipts. Measured against 180 Brain decisions across 29 sessions (2026-04-11 to 2026-04-12). Claude Code routing behavior cross-referenced with official docs via claude-code-guide agent.

---

## 1. What "intent routing" actually is

After Stage 2 packs the full context into the API payload, something has to decide: does this prompt need a Bash call? A file read? A skill invocation? A subagent? Or just a text response?

**The answer: it's entirely model-decided.** Claude Code has no deterministic routing layer. There is no pre-model classifier, no dispatch table, no rule engine inside the harness that routes prompts to tools before the model sees them. The model receives:

1. The full assembled context (system prompt + CLAUDE.md + tools + history + current prompt)
2. A `tools` array with JSON schemas for every available tool
3. Soft routing hints from skill descriptions, system instructions, and CLAUDE.md

Then the model decides which tools to call, in what order, with what parameters. Every "routing decision" is a model inference, not a code path.

**What the harness DOES control:**
- Which tools are in the `tools` array (built-in + MCP + deferred)
- Which skill descriptions are in context (all model-invocable skills, ~250 chars each)
- Whether the prompt reaches the model at all (hooks can block)
- Post-decision permission gates (PreToolUse hooks, permission modes)

**What the harness does NOT control:**
- Which tool the model picks
- Whether the model calls tools or responds with text
- How many tools the model calls per turn
- The order of tool calls within a turn

---

## 2. The two-layer routing model

the user's ecosystem has TWO routing layers operating on every prompt:

### Layer 1 — Claude Code (authoritative)

The model sees the full context and decides. This is the real router. It's informed by:

| Signal | Source | Weight |
|---|---|---|
| Tool schemas | Built-in + MCP + ToolSearch-fetched | High — defines what's callable |
| Skill descriptions | All model-invocable skills (~250 char each) | Medium — soft routing hints |
| System prompt | Hard-coded harness instructions | High — contains routing preferences (e.g. "prefer Grep over Bash for search") |
| CLAUDE.md chain | ~/CLAUDE.md + cwd/CLAUDE.md | High — project-specific instructions |
| Turn history | Prior tool calls + results in session | Medium — model learns what worked |
| Current prompt | What the user just typed | Primary trigger |

### Layer 2 — Brain (advisory)

Brain's classifier runs as a `UserPromptSubmit` hook BEFORE the model sees the prompt. It:
- Classifies the prompt into tiers S0-S5
- Recommends a model (haiku/sonnet/opus)
- Logs the decision to `decisions.jsonl`
- Emits stderr advisories when the current model doesn't match

**Brain CANNOT:**
- Force a model switch on the main session
- Override which tool the model picks
- Block or modify the prompt
- Change the tool schemas

**Brain CAN:**
- Set subagent model via `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` env var (Zone 2 — automatic)
- Pin skill models via `model:` frontmatter (22 skills pinned)
- Log every decision for offline analysis
- Surface cost advisories to stderr

**The boundary is clean:** Brain advises, Claude Code decides. Brain's real power is in Zone 2 (subagents + skills), not Zone 1 (main session).

---

## 3. Measured reality — 180 Brain decisions

From `~/.claude/telemetry/brain/decisions.jsonl`, 29 sessions:

### Tier distribution

| Tier | Count | % | Model | What it means |
|---|---|---|---|---|
| S0 | 76 | 42.2% | Haiku | Trivial — greeting, acknowledgment, short answer |
| S1 | 57 | 31.7% | Haiku | Simple — single-file read, one-step task |
| S2 | 16 | 8.9% | Sonnet | Moderate — multi-step, some tool use |
| S3 | 6 | 3.3% | Sonnet | Complex — multi-file, research needed |
| **S4** | **0** | **0.0%** | **Opus** | **Architecture — never fires** |
| S5 | 25 | 13.9% | Opus [1M] | Max — godspeed, multi-file refactor, deep work |

**The S4 hole persisted at 0/180 — RESOLVED 2026-04-12.** Thresholds redistributed in routing_manifest.toml: s2_max 0.35→0.22, s3_max 0.55→0.32, s4_max 0.80→0.55. S4 is now reachable. S1 tier accuracy separately fixed 2026-04-17 via `informational_question_floor` guardrail. Original analysis preserved:
- The S4 threshold band is too narrow (S3→S5 jumps over it)
- the user's prompts are bimodal: either simple or full-godspeed, rarely mid-architecture
- The guardrails (`architecture_work`, `multi_file_refactor`) escalate directly to S5, skipping S4

This is consistent with the 165-decision measurement from Stage 1. Needs 500+ decisions to confirm. *[Superseded 2026-04-12: resolved at 194 decisions as a threshold design issue, not data gap.]*

### Signal analysis

Which input signals drive routing decisions:

| Signal | Fires (nonzero) | Total weight | What it detects |
|---|---|---|---|
| `prompt_length` | 177/180 (98%) | 26.6 | Longer prompts → higher complexity |
| `multi_step` | 41/180 (23%) | 28.5 | "then", "after that", sequential intent |
| `tool_calls` | 29/180 (16%) | 17.3 | References to tools, files, code |
| `reasoning` | 27/180 (15%) | 16.3 | "why", "how", analytical keywords |
| `file_refs` | 22/180 (12%) | 17.0 | Path references, specific file names |
| `ambiguity` | 10/180 (6%) | 6.5 | Vague or open-ended prompts |
| `code_blocks` | 0/180 | 0.0 | Never fires — no pasted code blocks in hook input |
| `context_size` | 0/180 | 0.0 | Never fires — context size not available at hook time |

**`code_blocks` and `context_size` are permanently zero.** `code_blocks` can't fire because `UserPromptSubmit` hooks don't receive the full formatted prompt with code fence detection. `context_size` would need the API's token counter, which isn't available to hooks.

### Guardrail fire rates

| Guardrail | Count | Effect |
|---|---|---|
| `multi_file_refactor` | 12 | Escalates to S5 |
| `gpqa_hard_reasoning` | 12 | Escalates to S5 |
| `ue5_mention_floor` | 5 | Floor to S2+ |
| `architecture_work` | 4 | Escalates to S5 |
| `ue5_code_work` | 4 | Floor to S3+ |
| `code_edit_floor` | 2 | Floor to S2+ |
| `debug_floor` | 1 | Floor to S2+ |
| `creative_game_design` | 1 | Floor to S3+ |

Guardrails fire on 22.8% of decisions (41/180). The escalation guardrails (`multi_file_refactor`, `architecture_work`, `gpqa_hard_reasoning`) jump directly to S5, bypassing S3-S4. This is the primary S4 bypass mechanism.

### Confidence and uncertainty

- Average confidence: 0.543 (moderate)
- Uncertainty escalated: 68/180 (37.8%)
- Corrections detected: 2/180 (1.1%)
- Skill overrides: 0/180 (0%)

37.8% uncertainty escalation means Brain is unsure on more than 1 in 3 prompts. This is expected — the classifier is running on raw prompt text without context, tool results, or turn history. The model (Layer 1) has dramatically more signal to route with.

---

## 4. Tool schema injection

Claude Code injects tools via the standard Anthropic API `tools` array:

| Category | When loaded | Cacheable | Count (the user's setup) |
|---|---|---|---|
| **Built-in tools** | Always present in every API call | ✅ 1h cache | ~10 (Bash, Read, Edit, Write, Grep, Glob, Agent, Skill, ToolSearch, etc.) |
| **MCP tool names** | Session boot (name only, no schema) | ✅ 1h cache | ~25 (figma, context7, circleback) |
| **MCP tool schemas** | On-demand via ToolSearch | ✅ 1h cache after fetch | Variable |
| **Skill tool** | Always present (one tool: `Skill`) | ✅ 1h cache | 1 (dispatches to all skills) |

**Key insight:** Skills are NOT individual tools. There's one `Skill` tool. When the model calls it, it passes the skill name as a parameter. The model routes to a skill by reading the skill's description from context and deciding "this skill matches." The skill descriptions (~250 chars each, ~70 skills) are soft routing hints, not hard tool bindings.

**Deferred tools mechanism (ToolSearch):** MCP tools load name-only at boot. When the model needs one, it calls `ToolSearch` to fetch the full JSON schema on demand. This uses `tool_reference` blocks in the API (Sonnet 4+ / Opus 4+ feature). Built-in tools are never deferred.

---

## 5. Skill routing

Skills influence routing through two mechanisms:

### 5a. Description matching (model-decided)

Every model-invocable skill has a description truncated to 250 chars, loaded at session boot. The model reads these and matches against the prompt. Example:

```
skill: godspeed — Maximum execution mode v4.0...
skill: debug — Active debug orchestration pipeline...
skill: verify — Build and deployment verification...
```

The model sees ~70 of these descriptions and decides which (if any) to invoke. No scoring. No embedding similarity. Pure in-context reasoning.

### 5b. Slash command bypass (harness-decided)

When the user types `/skill-name`, the harness resolves it directly. The model doesn't "choose" — it receives the skill invocation as a synthetic tool call. This is the one case where routing is deterministic.

### 5c. Model pinning via frontmatter

Skills can specify `model: sonnet` or `model: opus` in their SKILL.md frontmatter. This doesn't affect WHETHER the skill is invoked — only which model runs it once invoked. 22 of the user's skills are pinned.

---

## 6. Hook pre-gating (Stage 1 → Stage 3 bridge)

`UserPromptSubmit` hooks fire BEFORE the model sees the prompt. A hook returning `{"decision": "block", "reason": "..."}` erases the prompt from context entirely. The model never routes on it.

This is the only deterministic routing gate in the pipeline. In the user's setup:
- `brain_advisor.sh` fires on every prompt, classifies it, and logs to `decisions.jsonl`
- It never blocks — Brain is advisory-only
- The `additionalContext` field from hook output gets injected into the model's context as a system message

Hooks can also inject additional context that biases routing. A hook could add "This is a UE5 task, use Bionics" and the model would factor that in. Brain currently doesn't inject routing hints — it only logs.

---

## 7. The Claude Code execution loop

Once the model decides to call tools, the execution follows this loop:

```
Model receives context + prompt
    ↓
Model decides: [tool_call_1, tool_call_2, ...] or text_response
    ↓
For each tool_call:
    PreToolUse hook fires (can block via {"decision":"block"})
    ↓
    Permission check (dontAsk mode → auto-approve)
    ↓
    Tool executes
    ↓
    PostToolUse hook fires (telemetry, monitoring)
    ↓
    Result added to context
    ↓
Model sees all results, decides next action
    ↓
Loop until model emits text_response (no more tool calls)
    ↓
Stop hook fires
```

**Parallel tool calls:** The model can emit multiple tool calls in a single turn. Claude Code executes independent calls in parallel. This is a model decision, not a harness feature — the model formats its output with multiple `tool_use` blocks.

**The loop is unbounded.** There's no hard limit on iterations. The model decides when to stop. The only external brakes are:
- Context window limit (1M tokens)
- Rate limits
- User interrupt (Ctrl+C)
- Stop hook (can override)

---

## 8. Where Brain's routing data actually matters

Brain classifies 73.9% of the user's prompts as Haiku-tier (S0+S1). But the user runs `/effort max` which locks the main session on Opus. Brain's Zone 1 advisory is systematically overridden by user preference.

**Where Brain's routing IS authoritative:**

| Zone | What | How | Impact |
|---|---|---|---|
| Zone 2 auto | Subagent model selection | `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` env var | Every `Agent` call defaults Sonnet instead of Opus |
| Zone 2 pinned | Skill model selection | `model:` frontmatter in SKILL.md | 22 skills pinned (sitrep/pulse/find = Haiku, verify/init = Sonnet, etc.) |

**Where it's advisory only:**

| Zone | What | Why it doesn't stick |
|---|---|---|
| Zone 1 main | Main session model | `/effort max` → Opus. Hooks can't force-switch. |
| Zone 1 cost | Cost advisories | Stderr only. Model doesn't see them. |

The practical savings from Brain's routing come almost entirely from Zone 2. The $500-1,500/mo achievable estimate is subagent + skill model downgrades, not main-session changes.

---

## 9. What Claude Code cannot currently do at Stage 3

| Limitation | Impact | Workaround |
|---|---|---|
| No deterministic skill dispatch | Model can ignore skill descriptions | Use `/skill-name` for guaranteed invocation |
| No tool priority ordering | Model picks tools by reasoning, not by ranked preference | System prompt / CLAUDE.md can bias, but not force |
| No routing feedback loop | Model doesn't learn "last time I used Grep for this and it worked" | Brain logs decisions, but model doesn't read them |
| No cost-aware routing | Model doesn't know Opus costs 50× Haiku | Brain advises, but can't enforce |
| No context-size-aware routing | Model doesn't adapt behavior as context grows | Could instrument via hook injection |
| Hooks can't inject tool preferences | UserPromptSubmit can add context but can't modify tool schemas | Would need PreToolUse or custom system message injection |

---

## 10. The routing gap — Brain vs reality

Brain recommends models based on prompt complexity. The model (Layer 1) routes based on the full context. These are operating on different information:

| Factor | Brain sees | Model sees |
|---|---|---|
| Prompt text | ✅ | ✅ |
| Turn history | Last 3 turns (limited) | Full session history |
| Tool results | ❌ | ✅ |
| File contents | ❌ | ✅ (if Read was called) |
| Skill descriptions | ❌ | ✅ (all 70) |
| System prompt | ❌ | ✅ |
| CLAUDE.md | ❌ | ✅ |

Brain is structurally under-informed compared to the model. Its 37.8% uncertainty rate reflects this gap. The classifier is doing the best it can with raw prompt text, but the model has 10-100× more signal.

**Routing accuracy measurement (2026-04-12, 181 measurable decisions):**
- **Overall accuracy: 57.5%** — barely better than a coin flip
- **Under-routing: 27.6%** (50 decisions) — Brain said S0/trivial, model ran 3-62 tools. S0 is the worst offender: 36/82 S0 calls triggered significant work. Root cause: short prompts like "ok", "continue", "godspeed" score near-zero but trigger massive tool chains because of session context and active skills.
- **Over-routing: 14.9%** (27 decisions) — Brain said S5/opus, but the actual work was A1 (1-4 tools). Mostly S5-locked sessions where every prompt inherited the S5 guardrail.
- **The confusion matrix reveals:** A1 (1-4 tools) is the dominant actual complexity at 54.1%, but Brain distributes most prompts to S0 (45.3%). The mismatch is structural — Brain sees the prompt, not the context.

**Implication for Toke:** Brain's highest-value role isn't routing the main session (the model does that better). It's:
1. **Zone 2 model selection** — real cost savings, automatic, no model override needed
2. **Decision logging** — the only instrument that records what prompts look like BEFORE the model processes them
3. **Trend detection** — across 29 sessions, patterns emerge that single sessions can't see
4. **Correction detection** — the 2/180 correction rate is the input for future learning
5. **Accuracy baseline** — the 57.5% figure is the floor for future classifier improvements. Any signal that raises this (e.g., context_turns_seen, active_skill detection) can be measured against it.

---

## 11. Instrumentation opportunities

Questions Stage 3 leaves open:

- [ ] **Tool selection frequency** — which tools does the model actually call, and how often? (PostToolUse hook will answer this once the matcher fix activates)
- [ ] **Skill invocation patterns** — which skills get invoked by model reasoning vs `/slash-command`?
- [ ] **Routing accuracy** — when Brain says "this is S0/Haiku" but the user's prompt actually needed multi-file editing, how often does that mismatch occur?
- [ ] **Tool call chains** — do certain tool sequences repeat? (e.g., Grep → Read → Edit is a common pattern)
- [ ] **Agent spawn cost** — how many tokens does each Agent call consume vs solving in main context?
- [ ] **Parallel vs sequential** — how often does the model batch tool calls vs sequential single calls?

All of these become measurable once PostToolUse telemetry is live (next session after matcher fix).

---

## 12. Status

- ✅ Routing architecture mapped (model-decided, no deterministic harness router)
- ✅ Two-layer model documented (Claude Code authoritative + Brain advisory)
- ✅ 180-decision analysis with tier distribution, signal weights, guardrail fire rates
- ✅ S4 hole confirmed (0/180, guardrails bypass to S5) **→ RESOLVED 2026-04-12 via threshold redistribution; S1 fixed 2026-04-17**
- ✅ Tool schema injection mechanism documented
- ✅ Skill routing mechanism documented (description matching + slash bypass + model pinning)
- ✅ Hook pre-gating documented
- ✅ Execution loop mapped
- ✅ Brain vs reality information gap quantified
- ✅ PostToolUse matcher bug found and fixed (cold-reload, activates next session). **Verified 2026-04-17:** governance audit confirms `tools.jsonl ACTIVE` with 4,383 tool calls over 30-day window.
- ✅ Tool selection frequency: measured from transcripts via `per_turn_breakdown.py tools`. Bash=330, Read=164, Edit=162, Write=70 across 7 Toke sessions.
- ✅ Tool call chain analysis: `per_turn_breakdown.py chains` detects bigrams+trigrams. Top chain: Bash→Bash (10.3%), Read→Edit (1.9%), Edit→Bash (2.3% — modify-then-verify pattern).
- ✅ Routing accuracy measured: **57.5% accuracy** (104/181 decisions). Under-routing 27.6% (50 decisions — Brain said S0/trivial, model ran 3-62 tools). Over-routing 14.9% (27 — mostly S5 sessions with simple prompts). S0 is worst tier: 36/82 S0 calls actually triggered significant work. Built `routing_accuracy.py` with confusion matrix, per-tier breakdown, and examples.

Next in the pipeline: **Stage 4 — Tool Execution Loop.** How does each tool actually execute? What's the PreToolUse → execute → PostToolUse → result lifecycle? What are the failure modes? The PostToolUse telemetry fix makes this stage measurement-ready.
