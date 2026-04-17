# Toke — Skills & Commands Index

Reference for the 16 skills + 2 slash commands this repo installs into `~/.claude/`.

---

## Slash commands (in `~/.claude/commands/`)

| Command | Role |
|---|---|
| `/brain-score <prompt>` | Classify any prompt on the S0–S5 tier scale. Returns tier, recommended model, effort, and signal breakdown. |
| `/toke` | One-screen Toke workbench: Brain cost state, Homer layer readiness, Mnemos health, current-session stats. |

---

## Skills (in `~/.claude/skills/`)

### Trigger skills (what you type to invoke them)

| Skill | Model | Trigger | Role |
|---|---|---|---|
| `godspeed` | opus | "godspeed" | Max-execution mode. Phase -1 tick → Phase 0.5 brain score → auto-Zeus on S3+ → atomic Oracle→Mnemos gate on synthesis. Core of the whole pipeline. |
| `toke-init` | sonnet | "init" / "toke init" | Session pre-flight. Loads project context, runs health check across Brain + Homer + Mnemos + Zeus, renders a one-screen briefing. |
| `close-session` | sonnet | "close session" | Session closure + memory persistence. Appends session entry to `project_status.md`, flags bible updates, verifies skill learnings pipeline. v2.2 context-discipline rules keep it inside the 200K window. |

### Core engine — Brain + Homer pantheon

| Skill | Model | Role |
|---|---|---|
| `brain` | sonnet | Routing classifier workbench. `brain score TEXT`, `brain scan` (cost analysis), `brain history`, `brain advise` (advisor escalation). |
| `zeus` | opus | Orchestrator (Homer L2). Decomposes S3+ tasks, dispatches parallel MUSES on Sonnet, synthesizes, critic-gates the memory write via `zeus gate-write`. |
| `calliope` | sonnet | Research muse (Homer L3). Deep synthesis from web + local sources with T1-T3 citations. Read-only. |
| `clio` | sonnet | Code-archaeology muse (Homer L3). Maps existing codebases, finds call sites, builds dependency graphs. Read-only; every claim cites `file:line`. |
| `urania` | sonnet | Measurement muse (Homer L3). Numeric receipts from telemetry (decisions.jsonl, tool logs, cost scans). Read-only. |
| `sybil` | opus | Advisor escalation (Homer L4). Invokes Anthropic's `advisor_20260301` API when a muse returns ROI=0 or Zeus is stuck. Cost-capped (max 2/session). |
| `mnemos` | opus | Three-tier memory (Homer L5). Core (context-resident) / Recall (SQLite FTS5 + vector search) / Archival (cold markdown). Citation-enforced writes. Progressive disclosure on reads. |
| `oracle` | opus | Synthesis critic (Homer L7). Scores Zeus output against rule checks + rubric + theater detection. Gates Mnemos writes: HARD_FAIL blocks, PASS/SOFT_FAIL writes. |

### Nightly maintenance agents

| Skill | Model | Role |
|---|---|---|
| `aurora` | sonnet | Sleep-time routing-weight tuner. Mines `decisions.jsonl` for drift, proposes weight adjustments to the manifest. Never auto-applies. |
| `hesper` | sonnet | Sleep-time learning distiller. Mines `_learnings.md` across skills + Mnemos archival, extracts top-N patterns by composite score, writes dated best-practices KB. |
| `nyx` | sonnet | Sleep-time theater auditor. Scans every SKILL.md for performative bloat using Oracle's pattern. Flags PRUNE and INVESTIGATE candidates. |

### Utility skills

| Skill | Model | Role |
|---|---|---|
| `verify` | sonnet | Build/test health check. Auto-detects project type from CWD (Python / Node / C++ / Rust / Go / custom) and runs the appropriate verification. Pass/fail verdict. |
| `sitrep` | haiku | Cross-project status aggregator. Scans `~/.claude/projects/` for all `project_status.md` files, reports staleness, flags blockers, surfaces "what needs attention." |

---

## How the skills stack

```
 user types a prompt
        │
        ▼
 UserPromptSubmit hook fires brain classifier
        │
        ├─ S0/S1/S2  →  direct handling (Haiku or main-thread)
        └─ S3+       →  godspeed skill auto-invokes zeus skill
                              │
                              ├─ plan decomposition
                              ├─ parallel dispatch: calliope / clio / urania
                              ├─ synthesis
                              ├─ oracle scores synthesis
                              ├─ PASS/SOFT_FAIL → mnemos.write_recall
                              │  HARD_FAIL     → block write, re-plan
                              └─ sybil escalates on ROI=0 or stuck state
```

Nightly (via Windows Task Scheduler or cron): aurora + hesper + nyx run on recent telemetry, write dated proposal reports under `toke/automations/homer/sleep/<agent>/`. User reviews before applying any proposal.

---

## Related documentation

- **[toke/README.md](README.md)** — architecture, design decisions, reproducible benchmark
- **[toke/PROJECT_BRIEF.md](PROJECT_BRIEF.md)** — 8-stage pipeline analysis of Claude Code internals
- **[../README.md](../README.md)** — godspeed top-level pitch + install flow

---

**Maintained by**: `close-session`. Update this file when a skill is added, renamed, or removed from `toke/skills/`.
