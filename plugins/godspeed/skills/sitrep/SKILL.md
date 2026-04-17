---
name: sitrep
description: Cross-project status aggregator. One command, full birds-eye view of every project with a `project_status.md` file in `~/.claude/projects/`. Flags stale or blocked projects, shows learning pipeline health, gives a prioritized "what needs attention" list.
model: haiku
---

# Sitrep — Cross-Project Status

Triggers: "sitrep", "status", "what's the state of everything", "where am I on stuff"

---

## What it does

Scans `~/.claude/projects/*/memory/` for any file matching `project_status.md` (or `project_*_status.md`). For each one found:

1. Read the most recent `## Session:` entry
2. Extract the date and one-line summary
3. Compute staleness bucket: Fresh (< 3 days), Idle (3-7), Stale (7-14), Dormant (14+)
4. Flag any "blocked" or "in-progress" items with no resolution

Aggregates into a single dashboard.

---

## Protocol

```bash
# 1. Discover projects
find ~/.claude/projects -maxdepth 3 -name "project_status.md" 2>/dev/null

# 2. For each, read the latest session entry + compute staleness
#    (use head / grep / date arithmetic — no external deps)

# 3. Check learning pipeline health
for skill in $(ls -d ~/.claude/skills/*/); do
  name=$(basename "$skill")
  count=$(grep -c '^###' "$skill/_learnings.md" 2>/dev/null || echo 0)
  # Flag any skill with <5 entries after 10+ invocations
done

# 4. Brain telemetry glance
wc -l ~/.claude/telemetry/brain/decisions.jsonl 2>/dev/null
cat ~/.claude/telemetry/brain/godspeed_count.txt 2>/dev/null
```

---

## Output format

```
═══════════════════════════════════════════════════
  SITREP — <date>
═══════════════════════════════════════════════════

PROJECTS (ranked by recency)
  [Fresh]    <project-slug>       <date>    <one-line summary>
  [Idle]     <project-slug>       <date>    <one-line summary>
  [Stale]    <project-slug>       <date>    <one-line summary>
  [Dormant]  <project-slug>       <date>    <one-line summary>

BLOCKERS (across all projects)
  - <project>: <item flagged as blocked>

LEARNING PIPELINE
  Skills with <5 entries after 10+ invocations: [list]
  Staleness: [N] entries >60 days without confirmation

BRAIN
  Decisions logged: <N>
  Godspeed tick: <N>

WHAT NEEDS ATTENTION
  1. <highest-priority item with reasoning>
  2. <next>
  3. <next>
═══════════════════════════════════════════════════
```

---

## Rules

- **Read-only.** Sitrep never modifies project state. Inspection tool, not an orchestrator.
- **Fail-open.** If a project_status.md is malformed, skip it with a warning — don't block the full report.
- **One-screen output.** Keep the final render ≤ 40 lines.
- **No commentary on content.** Sitrep reports state, not opinions. Strategic decisions come from the user.
