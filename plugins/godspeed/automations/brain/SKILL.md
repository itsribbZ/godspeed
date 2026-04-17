---
name: brain
description: Brain workbench — the model routing classifier and savings analyzer. Invoke ANY time the user says "brain scan", "brain score", "brain audit", "brain audit-skills", "brain pin", "brain apply-env", "brain test", or "/brain [subcommand]". Classifies tasks by severity (S0-S5) to route between Haiku/Sonnet/Opus while preserving Opus quality via hard guardrails. Reads from telemetry to project routing savings.
model: sonnet
effort: medium
---

# Brain — Workbench

This skill is the user-facing entry point for the Brain model router. All
commands are thin wrappers around `brain_cli.py`.

## Canonical location
`${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py`

The Brain has three layers:
1. **Classifier** — `severity_classifier.py` (pure Python, stdlib only)
2. **Manifest** — `routing_manifest.toml` (single source of truth for weights, tiers, guardrails, skill assignments)
3. **Workbench** — `brain_cli.py` (this skill's backend)

Plus two hooks that wire into Claude Code:
- `brain_advisor.sh` (UserPromptSubmit) — logs classification + emits model advisory
- `brain_tools_hook.sh` (PostToolUse) — logs tool call metadata for `brain scan`

## Commands

### `brain scan`
30-day cost analysis + routing savings projection. Reads `~/.claude/stats-cache.json` and applies current Anthropic pricing to compute actual cost, then projects what would be saved by routing 50% of Opus → Sonnet and 20% → Haiku.

```bash
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" scan
```

### `brain score TEXT`
Classify an arbitrary prompt and show the full signal breakdown with bar chart.

```bash
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" score "refactor auth module across 5 files"
```

### `brain audit-skills`
Show every skill and its assigned routing tier. Flags any skills in `~/.claude/skills/` that don't have a tier assigned yet.

```bash
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" audit-skills
```

### `brain pin SKILL [--write]`
Add `model:` and `effort:` frontmatter to a skill file based on its assigned tier in the manifest. Dry-run by default; add `--write` to apply.

```bash
# Dry-run
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" pin verify

# Apply
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" pin verify --write
```

### `brain apply-env`
Print shell env exports to source. These enable Zone 2 automatic routing:
- `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` — all subagents default to Sonnet
- `CLAUDE_CODE_EFFORT_LEVEL=max` — persist /effort max across sessions
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` — pin background Haiku version

```bash
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" apply-env
```

### `brain test`
Run the classifier smoke tests. Sample prompts → expected tiers.

```bash
python "${CLAUDE_PLUGIN_ROOT}/automations/brain/brain_cli.py" test
```

## Workflow

1. **First-time setup**: `brain scan` to see baseline and projected savings
2. **Audit**: `brain audit-skills` to see the current skill tier assignments
3. **Test**: `brain test` to confirm the classifier is behaving correctly
4. **Tune**: edit `routing_manifest.toml` directly (weights, thresholds, guardrails, skill tiers) — no code changes needed
5. **Enable Zone 2**: `brain apply-env`, add to shell rc, restart terminal
6. **Pin skills**: `brain pin SKILLNAME --write` for each skill you want explicitly routed
7. **Monitor**: run `brain scan` weekly to track actual cost vs projected savings

## Hard constraint
Claude Code hooks **cannot** force-switch the main session model for the next
turn. This is a documented Claude Code limitation. The Brain's main-session
routing is advisory only — it logs and suggests. All automatic routing
happens in Zone 2:
- Subagent overrides via `CLAUDE_CODE_SUBAGENT_MODEL` env var
- Per-skill overrides via `model:` frontmatter
- Per-Agent-tool-call overrides via the `model` parameter

Do not attempt to make the advisor hook auto-switch the main model. It will
silently fail. The advisory is the correct behavior.
