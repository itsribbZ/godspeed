# Agent D — Production LLM Routers in the Wild

Research burst for Toke Brain — 2026-04-10. How real coding agents and gateways route between models today.

---

## Comparison Table

| Tool | Auto-routes? | Signals | Roles | Fallback | Config | Decision rule |
|------|-------------|---------|-------|----------|--------|---------------|
| **Aider** | Partial — static YAML | Task type (edit/commit/summary) | `main_model` (architect), `editor_model` (applier), `weak_model` (commit msgs, summaries) | `weak_model = self` if unset | `--model`, `--editor-model`, `--weak-model` | Opus-as-main + Sonnet-as-editor + Haiku-as-weak — roles static, not per-request |
| **Continue.dev** | No — user picks per role | Role assignment at config time | 8 roles: chat, autocomplete, embed, rerank, edit, apply, summarize, subagent | None — throws if role missing | `config.yaml` `roles` array | Lookup table, no live classification |
| **Cursor "auto"** | Yes — closed/proprietary | Unknown (rumored: task type, context size, file count) | None documented | Unknown | "auto" UI toggle | **Black box** — HN speculation only |
| **Claude Code** | No today | N/A | N/A | N/A | `--model` flag | No auto-routing. `/fast` does not exist in current docs. `opusplan` alias is the only automatic switch (plan→execute). |
| **OpenRouter** | Partial — provider routing | Provider latency, availability | None | `fallbacks` array | Per-request `route` header | Routes providers, not models |
| **LiteLLM** | Yes — 6 strategies | TPM, RPM, latency, cost, tags, semantic embedding, keyword scoring | None (models peers) | `fallbacks`, `context_window_fallbacks`, `content_policy_fallbacks` | `routing_strategy` in Router init | Keyword + token-count scoring → SIMPLE/MEDIUM/COMPLEX/REASONING → tier map |
| **Martian** | Formerly yes, pivoted | Was: task type + quality | Was: quality routing | Was: quality degradation fallback | Was: API-level | **Dead as router** — pivoted to interpretability research |
| **GitHub Copilot** | Yes — closed | Inferred: request type | Chat: GPT-4o/Sonnet; Inline: smaller; Agent: unspecified | Unknown | VS Code settings | Unconfirmed switching logic |
| **Cline** | No | N/A | N/A | Retry same model | Single model per session | User picks one |
| **Codex CLI / Goose / swe-agent** | No meaningful routing | N/A | Single model per session | N/A | — | "User picks one" pattern |

---

## Top 3 Patterns Worth Stealing

### 1. Aider's Role Hierarchy — Architect / Editor / Weak
Concrete, proven in production. `main_model` does reasoning and produces a plan. `editor_model` (cheaper) applies the structured diff. `weak_model` (Haiku-class) handles commit messages, git summaries, yes/no confirms.

Rough ratio: **Opus plans, Sonnet edits, Haiku bookkeeps.**

**For Brain:** Map to hook stages. PreTool hook classifies task:
- "plan a refactor" → Opus
- "apply a diff already written" → Sonnet
- "summarize conversation for memory" → Haiku

### 2. LiteLLM's Complexity Router (Keyword + Token Scoring)
**Completely local, sub-1ms, zero API calls.**

Scores on 7 weighted dimensions: token count, code keywords, reasoning markers, technical terms, simple indicators, multi-step patterns, question count.

Tier boundaries (from `config.py`):
- score < 0.15 → **SIMPLE**
- 0.15 – 0.35 → **MEDIUM**
- 0.35 – 0.60 → **COMPLEX**
- ≥ 0.60 → **REASONING**

Each tier maps to a concrete model.

**For Brain:** Fastest viable classifier. Copy keyword lists and thresholds directly. The "reasoning markers" list (`"step by step"`, `"think through"`, `"chain of thought"`) is particularly good as Opus trigger.

### 3. LiteLLM's `context_window_fallbacks` — Separate Fallback Class
LiteLLM separates three fallback types: generic (rate limit / 5xx), context window overflow, content policy.

**Context window fallback is smart:** if a prompt exceeds model A's window, auto-route to model with bigger context, NOT to cheaper/better one. Prevents silent truncation.

**For Brain:** Track context_used vs model's max_context in a hook. If usage > 90% of Haiku's 200K, auto-escalate to Sonnet. Don't let model silently truncate.

---

## Top 3 Failed Patterns (Known-Bad)

### 1. Cursor "auto" — Black Box, No Observability
No user-visible signal about which model was picked. HN reports: users surprised when expensive requests routed to cheap models produce low-quality output. User can't override at task level, can't learn.

**A router with no observability is a liability.**

### 2. Martian's Quality-Based Routing — Pre-Classification Tax
Routed based on expected output quality using a second model call to assess complexity BEFORE the main call. Shipped, got users, company pivoted away by early 2026.

Most likely cause: pre-classification call itself expensive and slow; quality signal noisy; users didn't trust decisions. **Two-model-call pre-routing adds latency and cost that often exceed savings.**

### 3. Continue.dev's 8-Role Static Config — Too Granular, No Fallback
Requiring users to pre-assign models to 8 specific roles means config is a maintenance burden. If any role has no model → call throws. No default cascade.

**Result:** Most users assign the same model to all roles, defeating the purpose.

---

## Claude Code-Specific Opportunities

### 1. Hook Stage is the Cleanest Routing Injection Point in Existence
Every other tool routes at the API call level and has to infer task type from prompt text. **Claude Code hooks fire at named lifecycle stages with structured context.**

- `PreToolUse` knows tool name and input
- `PostToolUse` knows the result
- `UserPromptSubmit` gets raw prompt text

We can route on `tool_name == "Bash"` → Sonnet, `tool_name == "Edit" AND file is new` → Opus, `tool_name == "Read"` → Haiku. **Zero keyword inference needed — the tool name IS the task type.**

### 2. Skills are Typed Task Contracts
When a skill is invoked, we know its declared purpose (`analyst`, `godspeed`, `blueprint`, etc.). **No other tool has this.**

Brain maintains a `skill → tier` table:
- `blueprint` → Opus
- `professor` → Opus
- `holy-trinity` → Opus
- `verify` → Sonnet
- `sitrep` → Haiku
- `pulse` → Haiku
- `find` → Haiku

Routes before the skill even starts executing.

### 3. Session-Level Cost Budget with Per-Task Escalation
Hooks can maintain state (via sidecar JSON or shared memory). Brain tracks cumulative session cost and applies dynamic escalation rules:
- "if session cost > $0.50 already, default Haiku-first"
- "require explicit override for Opus after threshold"

**No other tool has hook-level session state access.** This closes the gap between LiteLLM's budget_limiter (proxy-level) and real user-intent signals.

---

## Sources

- https://github.com/paul-gauthier/aider/blob/main/aider/models.py
- https://github.com/paul-gauthier/aider/blob/main/aider/resources/model-settings.yml
- https://github.com/paul-gauthier/aider/blob/main/aider/coders/architect_coder.py
- https://github.com/continuedev/continue/blob/main/packages/config-yaml/src/schemas/models.ts
- https://github.com/BerriAI/litellm/tree/main/litellm/router_strategy
- https://github.com/BerriAI/litellm/blob/main/litellm/router_strategy/complexity_router/complexity_router.py
- https://github.com/BerriAI/litellm/blob/main/litellm/router_strategy/complexity_router/config.py
- https://github.com/BerriAI/litellm/blob/main/litellm/router_strategy/auto_router/auto_router.py
