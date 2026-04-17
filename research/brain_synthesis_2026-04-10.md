# The Toke Brain — Unified Design Synthesis

> **Mission:** A severity-based model router for Claude Code that preserves near-Opus-1M output quality while cutting token cost 20-35% per month, built around the constraints and optimization surface Claude Code actually exposes.

**Session date:** 2026-04-10
**Source research:** agent_a (mechanisms), agent_b (pricing), agent_c (literature), agent_d (production routers), agent_e (quality cliffs)

---

## 1. Executive Summary

### What the Brain is
A hybrid heuristic-and-guardrail classifier that scores every task on a 0-1 severity scale, maps the score to a tier (S0-S5), and — through the model-selection mechanisms Claude Code actually exposes — routes the task to the cheapest model that still clears the quality floor.

### Current state (measured, not guessed)
- **the user's 30-day Claude Code cost: ~$10,250** (stats-cache.json, computed)
- **97.8% is Opus 4.6 spend** — the only model that gets routed today
- **3,000:1 cache read/input ratio** — cache discipline is already saving ~$60K/month
- Zero subagent model overrides. Zero skills pinned. No auto-routing. `opusplan` unused.

### Target
- **20-35% cost reduction**: ~$2,000-3,500/month savings
- Achieved by routing 50% of Opus workload to Sonnet + 20% of remaining to Haiku
- **Quality floor preserved via four hard guardrails** (see §6)
- No session-time friction — the user doesn't babysit it

### The one unavoidable constraint
**Hooks cannot force-switch the main session's model for the next Claude turn.** Period. This is a documented Claude Code limitation (Agent A report). The Brain operates in two zones:
- **Zone 1 (main session):** advisory only — recommends, warns, logs
- **Zone 2 (subagents + skills):** automatic via frontmatter, Agent tool `model` param, and `CLAUDE_CODE_SUBAGENT_MODEL` env var

This split is the core architectural insight. Don't fight it — exploit it.

---

## 2. What Claude Code Actually Exposes (The Routing Surface)

From Agent A research, the complete model-resolution priority stack (highest → lowest):

| Priority | Mechanism | Scope | Brain uses it? |
|----------|-----------|-------|----------------|
| 1 | `CLAUDE_CODE_SUBAGENT_MODEL` env var | All subagents | **YES — primary Zone 2 lever** |
| 2 | `--model` CLI flag / `/model` command | Session-level | Advisory (recommend to the user) |
| 3 | `ANTHROPIC_MODEL` env var | Session default | YES — set boot default |
| 4 | `settings.json` `model` field | Session start | YES — pin default |
| 5 | Agent tool per-invocation `model` param | Per-call | YES — main session uses this when spawning agents |
| 6 | Subagent/skill frontmatter `model:` | Per-skill | **YES — most underused surface** |
| 7 | Plan default | Fallback | — |

**Zero-cost wins just sitting there:**
1. **`opusplan` alias** auto-switches Opus (plan mode) → Sonnet (execute mode). the user isn't using it.
2. **`CLAUDE_CODE_EFFORT_LEVEL=max`** env var persists the `max` effort level the user types manually every session.
3. **Skill frontmatter `model:`** — zero of the user's 32 skills use it. Massive optimization surface.
4. **Fast mode is a 6× price trap** — document clearly, never invoke by accident.

---

## 3. The Classifier — Design

### Philosophy (learned from Agent C + Agent D)
- **Heuristics beat learned routers in production** until outcome data exists to train on
- **Scalar score prediction collapses** (arxiv:2602.03478) — use ranking + hard gates, not regression
- **LiteLLM's complexity_router is the proven pattern** — sub-1ms, zero API calls, keyword + token scoring
- **Conservative defaults:** burden of proof is "why can we safely route down", not "why should we escalate"
- **Default tier = S3 (Sonnet-high), not S0** — route down only when signals are clearly simple

### 7 Signals (from Agent C's literature + Agent D's production patterns)

| ID | Signal | Cost | Weight | Source |
|----|--------|------|--------|--------|
| S1 | Prompt token count | 0ms | 0.15 | LLMRank, HybridLLM |
| S2 | Code block count + size | 0ms regex | 0.15 | "Not All Code Is Equal" |
| S3 | File reference count | 0ms | 0.15 | Claude Code-specific |
| S4 | Reasoning markers (why, design, architecture, refactor, prove, explain) | 0ms regex | 0.15 | LLMRank |
| S5 | Multi-step markers (step by step, first...then, numbered lists) | 0ms regex | 0.10 | LiteLLM complexity_router |
| S6 | Ambiguity markers (could, might, depends, unclear) | 0ms regex | 0.10 | LLMRank |
| S7 | Tool-call expectation (build, verify, check, run, test) | 0ms regex | 0.10 | Claude Code hook-stage awareness |
| S8 | Skill / domain tag (if invoked through a skill) | lookup | **0.30** (override) | Agent D — "skills are typed contracts" |

**Skill tag is a soft override**: if the incoming task is invoked through a known skill with a declared tier, use that tier directly and skip scoring.

### Score → Tier Mapping

```
score = weighted_sum(signals) + guardrail_bonuses
score = clip(score, 0.0, 1.0)

tier =
  S0 Trivial      if score < 0.08
  S1 Light        if score < 0.18
  S2 Standard     if score < 0.35     ← most common
  S3 Heavy        if score < 0.55     ← default when signals unclear
  S4 Critical     if score < 0.80
  S5 Ultra        otherwise
```

### Tier → Model + Effort Map

| Tier | Model | Effort | Cost vs Opus | Use case |
|------|-------|--------|--------------|----------|
| S0 Trivial | Haiku 4.5 | low | 20% | One-line lookups, status checks, shell commands |
| S1 Light | Haiku 4.5 | medium | 20% | Short factual Q, formatting, file reads, simple summaries |
| S2 Standard | Sonnet 4.6 | medium | 60% | Single-file edits, Python scripts, documentation |
| S3 Heavy | Sonnet 4.6 | high | 60% | Multi-step work, moderate debugging, research summaries |
| S4 Critical | Opus 4.6 | high | 100% | Architecture, multi-file refactor, novel debugging, synthesis |
| S5 Ultra | Opus 4.6 1M | max | 100%* | 1M context workloads, overnight unattended runs, UE5 full-system work |

*Opus 4.6 1M has zero pricing premium — it's "free" at the API level (confirmed Agent B)

---

## 4. The Four Hard Guardrails (Opus-mandatory overrides)

Score-based routing is overridden to **S4 or higher** whenever any of these fire. Source: Agent E cliffs.

### Guardrail 1 — GPQA-class reasoning
Keywords: `prove`, `theorem`, `quantum`, `particle physics`, `complexity analysis`, `big-O`, `formal`, `first principles`, `fundamental`, hard science domain terms.
**Why:** GPQA Diamond cliff = 17.2 pts Opus→Sonnet. This is the largest quality delta in all benchmarks.

### Guardrail 2 — Multi-file refactor / UE5 C++
Signals: ≥ 3 file references in prompt, or presence of `UPROPERTY`/`UCLASS`/`.uproject`/`UE5`, or explicit "refactor X across Y".
**Why:** Aider polyglot −10.6 pts Sonnet→Opus + UE5 header/macro complexity compounds errors.

### Guardrail 3 — Long-context load (>150K tokens)
Signal: Current context usage + referenced file sizes > 150K tokens.
**Why:** MRCR v2: Opus 76% vs Sonnet 4.5 18.5% — catastrophic cliff on long-context retrieval. Route to Opus 1M.

### Guardrail 4 — Creative / game design / GDD
Keywords: `lore`, `story`, `character`, `mechanic design`, `GDD`, `game feel`, `narrative`.
**Why:** Sacred Rule 6 (never auto-generate lore) + these are novel-reasoning tasks where GPQA cliff applies. Opus minimum.

---

## 5. The Two Zones

### Zone 1 — Main Session Model (Advisory)
**The Brain cannot switch this automatically.** What it can do:

1. **Boot-time recommendation**: `/brain recommend` at session start suggests `--model opus` or `opusplan` based on detected project and recent task distribution.
2. **Turn-time advisory**: `UserPromptSubmit` hook classifies the incoming prompt. If current model is higher than needed (e.g., Opus for a trivial task), hook emits a visible advisory: *"[brain] S1 task — consider `/model sonnet` for this turn"*.
3. **Session-end report**: shows routing decisions vs actual model used, with a dollar delta.

### Zone 2 — Subagents + Skills (Automatic)
**This is where the actual cost savings happen.** Brain controls:

1. **`CLAUDE_CODE_SUBAGENT_MODEL` env var**: set globally to `sonnet` as default for all subagents. Opus-mandatory agents override via explicit param.
2. **Skill frontmatter**: auto-pinned `model:` field on each of the user's 32 skills based on declared tier.
3. **Agent tool invocations**: when the main session spawns a subagent via Agent tool, Brain recommends the `model` parameter based on the subagent task description.

**Expected Zone 2 impact alone:** ~15-20% cost reduction without touching main session model.

---

## 6. Hidden Optimizations (the "not in plain sight" wins)

These are things the research surfaced that the user almost certainly isn't using:

### 1. `opusplan` alias — free money
Built-in automatic Opus→Sonnet handoff between plan mode and execution mode. Set via `--model opusplan`. the user's overnight sessions (which are plan-heavy) would automatically shift execution work to cheaper Sonnet.

### 2. `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` — single env var, 15%+ savings
Every Agent tool call today runs on the main model (Opus). Most subagent research/lookup tasks don't need Opus quality. Flip this env var in `.bashrc`/`.bash_profile` or the user's session startup.

### 3. `CLAUDE_CODE_EFFORT_LEVEL=max` persistent
the user types `/effort max` every session manually. This is interactive-only and doesn't persist. Setting the env var once = permanent.

### 4. Skill frontmatter pinning
the user's skills fall into clear tiers:
- **Opus-mandatory**: `blueprint`, `professor`, `holy-trinity`, `devTeam`, `profTeam`, `cycle`, `godspeed`, `analyst`, `ue-knowledge`, `bionics`, `debug`, `marketbot`
- **Sonnet-safe**: `verify`, `close-session`, `init` (all variants), `organizer`, `finder`, `reference`, `blend-master`, `player1`, `scanner`
- **Haiku-safe**: `sitrep`, `pulse`, `find`, `keybindings-help`, `update-config`

Auto-pinning via frontmatter: one-time change, zero ongoing friction.

### 5. Cache discipline is the core economic engine
- $60K/month savings from caching already
- **Routing decisions must never break cache warmth** — avoid forcing model switches mid-session unless necessary
- Never use fast mode (6× price + cache invalidation)

### 6. Smaller context models force compression → cache breaks
If Brain routes a 180K-token session to Haiku (200K context), the user gets compression, which invalidates the cache. Must check estimated token count before routing down.

### 7. `--agents` JSON flag
Dynamic per-session subagent definitions with model override — no file writes needed. Brain can spawn throwaway cheap-model subagents for specific tasks.

### 8. The `availableModels` nuclear option
For extreme cost control, restrict the `/model` picker to `["sonnet", "haiku"]` and require explicit env var override to access Opus. Not recommended as default but available as a budget guardrail.

---

## 7. Architecture — The Deliverables

```
Toke/
├─ automations/brain/
│  ├─ severity_classifier.py       # Pure-Python classifier, no LLM calls
│  ├─ routing_manifest.yaml        # Severity→model+effort map + signal weights + guardrails + skill tiers
│  ├─ brain_cli.py                 # CLI wrapper: score, scan, recommend, apply
│  └─ skill_tier_registry.yaml     # the user's 32 skills → declared tier
├─ hooks/
│  ├─ brain_advisor.sh             # UserPromptSubmit hook — classifies, logs, emits advisory
│  └─ brain_telemetry.sh           # PostToolUse hook — tracks routing decisions vs reality
├─ research/
│  ├─ agent_a_*.md (already written)
│  ├─ agent_b_*.md
│  ├─ agent_c_*.md
│  ├─ agent_d_*.md
│  ├─ agent_e_*.md
│  └─ brain_synthesis_2026-04-10.md (this file)
```

Plus, separately installable:
```
~/.claude/skills/brain/
└─ SKILL.md                         # /brain workbench — user-invocable only
```

And update:
```
~/.claude/projects/C--Users-user-Desktop-T1-Toke/memory/
└─ project_brain.md                 # Memory entry for the Brain
```

### Component responsibilities

**`severity_classifier.py`** — single-file pure Python, stdlib only. Reads JSON from stdin (`{prompt_text, context_token_estimate, skill_name?, current_model?}`), applies weighted signals + guardrails, outputs JSON (`{tier, model, effort, score, signals, guardrails_fired, reasoning}`). Sub-millisecond. No dependencies.

**`routing_manifest.yaml`** — single source of truth for all weights, thresholds, guardrails, and skill tier assignments. Classifier reads this at startup. the user can edit this directly to tune without touching code.

**`skill_tier_registry.yaml`** — declarative map of each of the user's 32 skills to a tier. Generated by audit, editable by the user. Classifier cross-references when a skill invocation is detected.

**`brain_cli.py`** — thin wrapper exposing:
- `brain score` — score a prompt from stdin or arg
- `brain scan` — analyze recent sessions from stats-cache.json, show routing savings potential
- `brain recommend` — suggest boot-time `--model` based on detected project
- `brain pin <skill>` — add `model:` frontmatter to a skill file
- `brain apply-env` — print env var exports for the user to source in shell

**`brain_advisor.sh`** — UserPromptSubmit hook:
- Reads stdin (prompt text + metadata)
- Pipes to `severity_classifier.py`
- Logs decision to `~/.claude/telemetry/brain/decisions.jsonl`
- If classified tier model ≠ current session model AND delta is significant (e.g. Haiku-tier on Opus session), emits advisory to stderr
- Non-blocking, < 50ms total

**`brain_telemetry.sh`** — PostToolUse hook:
- Logs `{timestamp, session_id, tool_name, tool_input_size, tool_output_size}` to `~/.claude/telemetry/brain/tools.jsonl`
- Used by `brain scan` to quantify actual vs recommended routing

**`SKILL.md` (the /brain skill)** — workbench for the user. User-invocable only (`disable-model-invocation: true` — per ship-plan precedent). Subcommands:
- `/brain` — show current state, recent decisions, current session recommendation
- `/brain scan` — 30-day savings analysis from telemetry
- `/brain score "text"` — classify arbitrary prompt
- `/brain audit-skills` — list all skills + their tier assignment + suggested frontmatter additions
- `/brain pin <skill>` — apply frontmatter change
- `/brain apply-env` — print env exports to source

---

## 8. Quality Floor — How We Prove "Near-Opus"

Two measurements:

### Measurement 1: Guardrails fire before quality drops
Every session logs which guardrails fired. If a session NEVER fires a guardrail but we know (from outcome) quality dropped, the guardrails are undercalibrated. Monthly review: compare guardrail fire rate to Opus route rate. Delta = Brain's cost-savings zone.

### Measurement 2: Cost vs session task mix
Track per-session task tier distribution (from telemetry) vs actual model used. If Brain recommended Sonnet and session used Opus anyway, log the delta. If Brain recommended Opus and session used Sonnet (via manual override), monitor for user-perceived quality issues in the follow-up.

### The honest limit
**We cannot automatically verify output quality.** The Brain is fundamentally a conservative router — it routes up (to Opus) whenever a guardrail fires, and routes down only when signals are clear. Quality preservation is achieved by being pessimistic about downgrade decisions, not by post-hoc quality scoring. This is by design (Agent C: AutoMix-style self-verification doubles latency).

---

## 9. 7 Laws Self-Check (pre-devTeam pass)

**Law 1 — Correctness:** Classifier is deterministic, pure functions, no hidden state. ✓
**Law 2 — Minimal interfaces:** Single JSON-in / JSON-out contract. Classifier has one public function. ✓
**Law 3 — Single responsibility:** Each component (classifier, CLI, advisor hook, telemetry hook, workbench skill) does exactly one thing. ✓
**Law 4 — No premature optimization:** Hybrid heuristic only. No ML. No embeddings. No LLM-calls-to-classify-LLM-calls. ✓
**Law 5 — No cascading changes:** Doesn't touch `~/.claude/settings.json` without explicit the user consent. Hooks are opt-in. Env vars are opt-in. Skill frontmatter is per-skill opt-in. ✓
**Law 6 — Explicit failure modes:** Classifier fails open (returns default tier S3 = Sonnet-high, safe). Hooks fail silent (never block the turn). ✓
**Law 7 — Observable:** Every decision logged to JSONL. `brain scan` turns telemetry into measured cost delta. ✓

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Routing collapse (classifier always picks Opus) | Monitor tier distribution in telemetry. Weekly review. Adjust weights if >80% of scores cluster in S4+. |
| Quality drop on a task the classifier rated as Sonnet-safe | Conservative default: S3 (Sonnet-high) when uncertain. Guardrails force Opus on cliff signals. |
| Cache invalidation from model switching | Never auto-switch mid-session. All advisory. Zone 2 (subagents) is where routing happens. |
| the user's workflow disruption | All Brain components are additive and opt-in. Full rollback path. Nothing touches existing hooks or skills without consent. |
| Classifier keyword drift | Manifest-driven. the user edits `routing_manifest.yaml` directly. No code changes needed for tuning. |
| Telemetry file bloat | JSONL append-only, sessions rotated at session end by `brain_telemetry.sh`. |

---

## 11. Build Sequence (what happens next)

1. Write `severity_classifier.py` + unit tests (sample prompts → expected tiers)
2. Write `routing_manifest.yaml` with all weights, thresholds, guardrails
3. Write `skill_tier_registry.yaml` auditing the user's 32 skills
4. Write `brain_cli.py` with all subcommands
5. Write `brain_advisor.sh` hook
6. Write `brain_telemetry.sh` hook
7. Write `SKILL.md` for the /brain workbench
8. Write `integration_guide.md` — step-by-step wire-in
9. Write `rollback_guide.md`
10. Verify classifier on sample prompts
11. devTeam-style 7 Laws pass on the built code
12. the user-facing demo: run `brain scan` against stats-cache + show the projected savings
13. Reconciliation + learnings + project_status update

---

## 12. What this is NOT

- Not a wrapper CLI around `claude`
- Not a replacement for `/model` — it's an advisory layer
- Not a mandatory policy enforcer — it's an assistant
- Not an ML-trained router — pure heuristics + guardrails
- Not coupled to any particular project — works across Sworder/your-trading-project/Toke/etc.
- Not modifying `~/.claude/settings.json` automatically — all settings changes require the user's explicit go-ahead

---

*This synthesis is the contract. The build must match it. Drift from this document = rewrite the document first.*
