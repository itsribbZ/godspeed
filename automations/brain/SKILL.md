---
name: brain
description: Toke Brain workbench — the model routing classifier and savings analyzer. Invoke ANY time the user says "brain scan", "brain score", "brain audit", "brain audit-skills", "brain pin", "brain apply-env", "brain test", or "/brain [subcommand]". Classifies tasks by severity (S0-S5) to route between Haiku/Sonnet/Opus while preserving Opus quality via hard guardrails. Reads from stats-cache.json and telemetry to project routing savings. Source code lives at ~/Desktop/T1/Toke/automations/brain/.
model: sonnet
effort: medium
---

# Toke Brain — Workbench

This skill is the user-facing entry point for the Toke model router. All
commands are thin wrappers around `brain_cli.py`.

## Canonical location
`~/Desktop/T1/Toke/automations/brain/brain_cli.py`

The Brain has three layers:
1. **Classifier** — `severity_classifier.py` (pure Python, stdlib only)
2. **Manifest** — `routing_manifest.toml` (single source of truth for weights, tiers, guardrails, skill assignments)
3. **Workbench** — `brain_cli.py` (this skill's backend)

Plus two hooks that wire into `~/.claude/settings.json`:
- `brain_advisor.sh` (UserPromptSubmit) — logs classification + emits model advisory
- `brain_telemetry.sh` (PostToolUse) — logs tool call metadata for `brain scan`

## Commands

### `brain scan`
30-day cost analysis + routing savings projection. Reads `~/.claude/stats-cache.json` and applies current Anthropic pricing to compute actual cost, then projects what would be saved by routing 50% of Opus → Sonnet and 20% → Haiku.

```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" scan
```

### `brain score TEXT`
Classify an arbitrary prompt and show the full signal breakdown with bar chart.

```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" score "refactor EXOSeedSubsystem across 5 files"
```

### `brain audit-skills`
Show every skill and its assigned routing tier. Flags any skills in `~/.claude/skills/` that don't have a tier assigned yet.

```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" audit-skills
```

### `brain pin SKILL [--write]`
Add `model:` and `effort:` frontmatter to a skill file based on its assigned tier in the manifest. Dry-run by default; add `--write` to apply.

```bash
# Dry-run
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin sitrep

# Apply
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin sitrep --write
```

### `brain apply-env`
Print shell env exports to source. These enable Zone 2 automatic routing:
- `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` — all subagents default to Sonnet
- `CLAUDE_CODE_EFFORT_LEVEL=max` — persist /effort max across sessions
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` — pin background Haiku version

```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" apply-env
```

### `brain test`
Run the classifier smoke tests. 16 sample prompts → expected tiers.

```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" test
```

## Workflow

1. **First-time setup**: `brain scan` to see baseline and projected savings
2. **Audit**: `brain audit-skills` to see the current skill tier assignments
3. **Test**: `brain test` to confirm the classifier is behaving correctly
4. **Tune**: edit `routing_manifest.toml` directly (weights, thresholds, guardrails, skill tiers) — no code changes needed
5. **Enable Zone 2**: `brain apply-env`, add to shell rc, restart terminal
6. **Pin skills**: `brain pin SKILLNAME --write` for each skill the user wants to explicitly route
7. **Wire hooks** (optional): follow `integration_guide.md` to add `brain_advisor.sh` + `brain_telemetry.sh` to `settings.json`
8. **Monitor**: run `brain scan` weekly to track actual cost vs projected savings

## Design contract
`~/Desktop/T1/Toke/research/brain_synthesis_2026-04-10.md`

## Integration guide
`~/Desktop/T1/Toke/automations/brain/integration_guide.md`

## Rollback guide
`~/Desktop/T1/Toke/automations/brain/rollback_guide.md`

## Hard constraint
Claude Code hooks **cannot** force-switch the main session model for the next
turn. This is a documented Claude Code limitation (see
`research/agent_a_model_selection_mechanisms_2026-04-10.md`). The Brain's main-
session routing is advisory only — it logs and suggests. All automatic routing
happens in Zone 2:
- Subagent overrides via `CLAUDE_CODE_SUBAGENT_MODEL` env var
- Per-skill overrides via `model:` frontmatter
- Per-Agent-tool-call overrides via the `model` parameter

Do not attempt to make the advisor hook auto-switch the main model. It will
silently fail. The advisory is the correct behavior.
