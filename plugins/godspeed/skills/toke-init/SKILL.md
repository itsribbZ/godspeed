---
name: toke-init
description: Fast session-start pre-flight for Toke. Loads project context, runs a health check across Brain + Homer + hooks, and delivers a one-screen briefing so godspeed routes to the current state, not stale memory.
model: sonnet
---

# Toke Init — Session Pre-Flight

Invoke at session start to load Toke context and verify system health.

## Project Paths

| Item | Path |
|------|------|
| **Toke root** | `$TOKE_ROOT` (set via env var; defaults to the cloned repo location) |
| **Project status** | `~/.claude/projects/<current-slug>/memory/project_status.md` |
| **Memory index** | `~/.claude/projects/<current-slug>/memory/MEMORY.md` |
| **Global settings** | `~/.claude/settings.json` |
| **Installed skills** | `~/.claude/skills/` |
| **Slash commands** | `~/.claude/commands/` |

---

## Phase 1: Load (parallel)

Read these in one parallel batch so nothing blocks:

1. **Project status** — `~/.claude/projects/<current-slug>/memory/project_status.md` (if exists) — where we left off
2. **Memory index** — `~/.claude/projects/<current-slug>/memory/MEMORY.md` (if exists) — pointer list
3. **Toke README** — `$TOKE_ROOT/README.md` — big-picture refresher (optional — skip if already loaded)

If a file is missing → warn but continue. Never block init on a missing file.

---

## Phase 2: Health Check (fast, ≤ 5 seconds)

Parallel shell block — catches broken pipelines early:

```bash
# Brain telemetry
DECISIONS=$(wc -l < ~/.claude/telemetry/brain/decisions.jsonl 2>/dev/null || echo 0)
TOOLS=$(wc -l < ~/.claude/telemetry/brain/tools.jsonl 2>/dev/null || echo 0)
TICK=$(cat ~/.claude/telemetry/brain/godspeed_count.txt 2>/dev/null || echo 0)
echo "Brain: ${DECISIONS} decisions, ${TOOLS} tool calls, tick ${TICK}"

# Homer pantheon
python $TOKE_ROOT/automations/homer/homer_cli.py status 2>&1 | head -12

# Mnemos store
python $TOKE_ROOT/automations/homer/mnemos/mnemos.py health 2>&1 | head -12

# Zeus CLI
python $TOKE_ROOT/automations/homer/zeus/zeus_cli.py status 2>&1 | tail -5
```

Report inline. Flag any layer that fails its health check. Do NOT block init on a health warning — surface it in the briefing.

---

## Phase 3: Briefing

Render a one-screen summary — real data from Phase 1+2, no placeholders:

```
═══════════════════════════════════════
  TOKE — SESSION READY
═══════════════════════════════════════

LAST SESSION: [date from project_status.md] — [one-line summary]
  Completed:  [bullets from status]
  In-Progress:[bullets from status]
  Next Up:    [bullets from status]

TOKE SYSTEMS:
  Brain:  [N] decisions logged, tick [N]
  Homer:  [N]/8 layers live, [N]/[N] integration tests green
  Mnemos: [N] recall rows, semantic_available=[T/F]
  Zeus:   atomic gate-write CLI [OK/missing]

READY. What are we building?
═══════════════════════════════════════
```

---

## Session Rules (active after init)

- **Receipts or it didn't happen** — every claim about pipeline behavior or telemetry needs a reproducible command.
- **Test before declaring done** — `python automations/homer/homer_integration_test.py` and `python automations/homer/mnemos/test_mnemos.py` both go green before merging code.
- **One stage at a time** — don't sprawl across unrelated layers in a single task. Depth over breadth.
- **Never modify `~/.claude/settings.json`** without explicit user consent — it affects every session.
- **Gate-write MUST land** for Zeus (S3+) runs. If `written: false` comes back, surface the reason — don't quietly swallow the failure.

---

## On-Demand Content

Do NOT pre-read these. Load when the session task actually needs them:

| Task involves | Load |
|---|---|
| Brain classifier work | `$TOKE_ROOT/automations/brain/` |
| Homer/Mnemos/Zeus internals | `$TOKE_ROOT/automations/homer/<layer>/` |
| Hook authoring | `~/.claude/settings.json` + existing `$TOKE_ROOT/hooks/` |
| Designing a new skill | 2-3 existing skills in `~/.claude/skills/` as reference |
| Claude Code feature questions | Claude Code docs (context7 MCP or web) |
| Anthropic API / SDK questions | Anthropic docs (context7 MCP or web) |

---

## Learning Protocol

After init, if any path failed, context was stale, or a briefing number was wrong, note it. Don't suppress — the briefing is the first verification step of the session.
