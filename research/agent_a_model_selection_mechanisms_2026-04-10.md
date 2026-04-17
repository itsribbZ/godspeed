# Agent A — Claude Code Model Selection Mechanisms

Research burst for Toke Brain — 2026-04-10. Source base: `code.claude.com/docs/en/` (docs migrated from `docs.anthropic.com`). All receipts live-sourced.

---

## 1. Slash Commands

Source: `https://code.claude.com/docs/en/model-config`

| Command | What it does | Scope | Gotchas |
|---|---|---|---|
| `/model <alias\|name>` | Switches active model mid-session. Accepts any alias or full model ID | Whole session (until changed again) | Does not persist to settings.json. Hosts an effort slider via left/right arrow keys. |
| `/model opus[1m]` | Switches to Opus with 1M context | Whole session | Only available if plan supports it |
| `/effort low\|medium\|high\|max` | Changes effort level (adaptive reasoning budget) | Whole session | `max` = Opus 4.6 only; does NOT persist across sessions (except via env var) |

**CONFIRMED ABSENT:** No `/fast`, `/opus`, `/sonnet`, `/haiku` standalone slash commands. The "fast mode" concept has been replaced by the effort system + `opusplan` alias.

---

## 2. CLI Flags

Source: `https://code.claude.com/docs/en/cli-reference`

| Flag | Syntax | Scope |
|---|---|---|
| `--model` | `claude --model opus` or `claude --model claude-opus-4-6` | Single session, not persisted |
| `--effort` | `claude --effort high` | Single session |
| `--fallback-model` | `claude -p --fallback-model sonnet "query"` | Print mode only — activates when default model overloaded |
| `--agent` | `claude --agent my-custom-agent` | Session-level — selects which subagent runs as main session |
| `--agents` | `claude --agents '{"name":{"model":"sonnet",...}}'` | Session-only — defines throwaway subagents with model overrides via JSON |

Accepted aliases: `sonnet`, `opus`, `haiku`, `best`, `default`, `opusplan`, `sonnet[1m]`, `opus[1m]`.

---

## 3. settings.json Model Fields

Source: `https://code.claude.com/docs/en/model-config`, `https://code.claude.com/docs/en/settings`

**the user's current `~/.claude/settings.json` has NO `model` field** — sessions run on plan default.

| Key | Type | What it does |
|---|---|---|
| `model` | string | Sets initial model at session start. Not enforcement — user can still `/model` switch |
| `effortLevel` | string | `"low"`, `"medium"`, `"high"`. Persists across sessions. **`max` CANNOT be set here** — only via env var |
| `availableModels` | array | Restricts the `/model` picker. Strict enforcement only in enterprise/managed |
| `modelOverrides` | object | Maps Anthropic model IDs → provider-specific IDs (Bedrock/Vertex) |
| `env` | object | Can inject env vars like `ANTHROPIC_DEFAULT_SONNET_MODEL` for version pinning |

**Keys that DO NOT exist:** `defaultModel`, `fastModel`, `smallModel`, `largeModel`. Don't hallucinate.

---

## 4. Agent Tool `model` Parameter

Source: `https://code.claude.com/docs/en/sub-agents`

The Agent tool (renamed from Task tool in v2.1.63) accepts a `model` parameter. Resolution order for subagent model:

1. `CLAUDE_CODE_SUBAGENT_MODEL` env var — **nukes everything below**
2. Per-invocation `model` parameter passed by Claude when calling Agent tool
3. Subagent definition's `model` frontmatter field
4. Main conversation's model (inherit)

The built-in `Explore` agent is **hardcoded to Haiku** regardless of session model.

---

## 5. Subagent & Skill Frontmatter

Source: `https://code.claude.com/docs/en/sub-agents`

`.claude/agents/*.md` and skill `SKILL.md` files both support `model` and `effort` in frontmatter:

```yaml
---
name: code-reviewer
description: Reviews code for quality
model: sonnet          # sonnet | opus | haiku | full ID | inherit
effort: high           # low | medium | high | max (Opus 4.6 only)
tools: Read, Grep, Glob
---
```

**Local audit finding:** the user's `~/.claude/agents/` directory does not exist yet. Zero of his existing skill files at `~/.claude/skills/` use `model:` in frontmatter. **Massive untapped optimization surface.**

---

## 6. Hooks and Model Selection — HARD CONSTRAINT

Source: `https://code.claude.com/docs/en/hooks`

**Hooks CANNOT change the model for the next Claude turn.**

- **Can read**: `SessionStart` event input includes current `model` field (read-only)
- **Hook-internal only**: A `type: prompt` hook can declare its own `model` for evaluating the hook's prompt — but this only governs hook evaluation, not main session
- **Cannot**: No hook output field exists to force-switch Claude Code's model for subsequent turns

**Implication for Brain:** Hooks can observe, log, advise, warn, and inject context — but cannot auto-route the main turn. The Brain's main-session routing is advisory. Subagent routing can be automatic via env var + frontmatter.

---

## 7. MCP and Model Switching

No MCP server can switch the calling client's model. No protocol field exists for this.

---

## 8. Environment Variables (Complete List)

Source: `https://code.claude.com/docs/en/model-config#environment-variables`, `https://code.claude.com/docs/en/env-vars`

| Variable | What it does |
|---|---|
| `ANTHROPIC_MODEL` | Primary model selection. Alias or full ID. Priority 3 of 4 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Pins what `opus` alias resolves to |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Pins what `sonnet` alias resolves to |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Pins what `haiku` alias resolves to; controls background Haiku usage |
| `CLAUDE_CODE_SUBAGENT_MODEL` | **Overrides model for ALL subagents** regardless of frontmatter |
| `CLAUDE_CODE_EFFORT_LEVEL` | Sets effort level. Values: `low`, `medium`, `high`, `max`, `auto`. **Only way to set `max` persistently.** |
| `ANTHROPIC_CUSTOM_MODEL_OPTION` | Adds a custom entry to `/model` picker (LLM gateways) |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT` | Set to `1` to remove 1M variants from picker |
| `ANTHROPIC_SMALL_FAST_MODEL` | **DEPRECATED** — use `ANTHROPIC_DEFAULT_HAIKU_MODEL` |

---

## 9. Context Windows and Plan Availability

Source: `https://code.claude.com/docs/en/model-config#extended-context`

| Model | Default context | 1M context |
|---|---|---|
| Opus 4.6 | 200K | Yes — `opus[1m]` alias |
| Sonnet 4.6 | 200K | Yes — `sonnet[1m]` alias |
| Haiku 4.5 | Standard ~200K | Not mentioned |

**1M context plan gates:**
- Max/Team/Enterprise: Opus 1M included; Sonnet 1M requires extra usage
- Pro: Both require extra usage
- API pay-as-you-go: Full access, standard pricing

No premium for 1M context beyond 200K — billed at standard per-token rate.

---

## 10. The "Fast Mode" Question

No `/fast` command in current docs. Historical fast mode replaced by:

- **`effort` system** — `low` effort = faster/cheaper; `max` effort = deepest reasoning (Opus only)
- **`haiku` alias** — routes to fast/cheap model for simple tasks
- **`opusplan` alias** — **hybrid mode: Opus during plan mode → Sonnet during execution** (automatic switch)

**`opusplan` is the closest native analog to a built-in router and the user is not currently using it.**

---

## Brain Router — Model Resolution Priority Stack (highest → lowest)

1. `CLAUDE_CODE_SUBAGENT_MODEL` env var (subagents only, nukes frontmatter)
2. `--model` CLI flag / `/model` slash command (session-level)
3. `ANTHROPIC_MODEL` env var
4. `settings.json` `model` field
5. Per-invocation Agent tool `model` parameter
6. Subagent/skill frontmatter `model` field
7. Plan default

## Cleanest Brain Router Entry Points

- **Skill frontmatter `model:`** — zero-friction, per-skill
- **Subagent frontmatter `model:`** — per-subagent-type
- **`CLAUDE_CODE_SUBAGENT_MODEL`** — blunt hammer locking ALL subagents to one model
- **`--agents` JSON flag** — session-scoped dynamic subagents with model override, no file needed
- **`opusplan` alias** — underutilized native auto-switch

## Sources
- https://code.claude.com/docs/en/model-config
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/env-vars
- https://code.claude.com/docs/en/slash-commands
