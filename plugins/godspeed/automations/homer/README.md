# Homer — Toke's Multi-Agent Pantheon

> A coordinated pantheon of specialized agents: orchestrator, advisor, memory, workers, critics, and sleep-time improvers. Built to beat SOTA on the 5 dimensions nobody else measures, while matching SOTA on the 10 pillars everyone is competing on.

**Status:** 2026-04-11 — **FULL PANTHEON SHIPPED (8/8)**. VAULT L0, Zeus L2, 3 MUSES L3, Sybil L4, Mnemos L5, Oracle L7, 3 Sleep-Time Agents L6 (Nyx/Hesper/Aurora). **104/104 tests green.** Beat-SOTA commitments #1-5 all operational.

---

## Mission

Built 2026-04-11 as Toke's response to April 2026 SOTA research (Anthropic Multi-Agent Research System, Claude Managed Agents, LangGraph, OpenHands, Letta MemGPT, Manus, Cursor 3). The leading agentic pattern in April 2026 is **orchestrator-worker multi-agent** with three-tier self-editing memory, sandboxed checkpointing, advisor escalation, and eval-driven continuous learning. Homer is Toke's native implementation of that pattern — personalized to the user, receipts-first, theater-aware.

Homer does NOT replace Brain, godspeed, Author, or any existing Toke tool. It evolves them into named layers of a single coordinated pantheon. All existing work is preserved and promoted.

## The 7-Layer Architecture

```
╔═══════════════════════════════════════════════════════════════╗
║                       TOKE HOMER v1.0                         ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║ L7  ORACLE         🟢 P3  critic / evaluator SHIPPED           ║
║ L6  SLEEP-TIME     🟢 P3  Nyx / Hesper / Aurora SHIPPED        ║
║ L5  MNEMOS         🟢 P2  three-tier memory SHIPPED            ║
║ L4  SYBIL          🟢 P1  advisor_20260301 wiring SHIPPED      ║
║ L3  MUSES          🟢 P1  Calliope / Clio / Urania SHIPPED     ║
║ L2  ZEUS           🟢 P0  orchestrator SHIPPED                 ║
║ L1  BRAIN          ✅     severity classifier (existing v2.3)  ║
║ L0  VAULT          🟢 P0  checkpointing / state SHIPPED        ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

| Layer | Codename | Function | Status | Build Session |
|---|---|---|---|---|
| L0 | **VAULT** | Sandboxed state + checkpointing. Compaction-resilient. 4-hex collision. Auto-archive stale > 24h. | 🔵 **P0 SHIPPED** | 1 (2026-04-11) |
| L1 | **BRAIN** | Severity classifier → tier routing, guardrails, $ tracking. | ✅ Pre-existing v2.3 | — |
| L2 | **ZEUS** | Orchestrator. Decomposes task → plans via extended thinking → delegates workers → synthesizes → hands to Oracle. | 🔵 **P0 SHIPPED** (SKILL.md) | 1 (2026-04-11) |
| L3 | **MUSES** | 3 parallel scoped Sonnet subagents (Calliope research, Clio code archaeology, Urania measurement) per Anthropic MARS pattern. Each has distinct role, tool allowlist, output format, and boundary discipline. | 🟢 **P1 SHIPPED** | 2 (2026-04-11) |
| L4 | **SYBIL** | Advisor escalation. Native `advisor_20260301` wire-in via `sybil.py` wrapper. Max 2 escalations/session, preconditions enforced (API key, creative-content refusal, session cap). 13/13 smoke tests green. | 🟢 **P1 SHIPPED** | 2 (2026-04-11) |
| L5 | **MNEMOS** | Three-tier memory: Core (JSON-lines, ~5K tokens, auto-compacted) / Recall (SQLite FTS5 searchable) / Archival (cold markdown, back-pointer protocol). Every write + self-edit requires a valid citation. 35/35 smoke tests green. | 🟢 **P2 SHIPPED** | 3 (2026-04-11) |
| L6 | **Sleep-Time Agents** | Background agents improving Homer between sessions: **Nyx** (theater audit across all skills using Oracle heuristic), **Hesper** (learning distillation — absorbs Kiln mission), **Aurora** (routing weight proposals from decisions.jsonl). Unified dispatcher via `sleep_cli.py`. 18/18 tests green. | 🟢 **P3 SHIPPED** | 4 (2026-04-11) |
| L7 | **ORACLE** | Critic / eval harness. Scores outputs against 10 Sacred Rule detection heuristics + rubric (length/receipts/citations) + theater detection (reproduces yesterday's godspeed audit). Regression detection vs baseline. 26/26 tests green. | 🟢 **P3 SHIPPED** | 4 (2026-04-11) |

## Directory Layout

```
Toke/automations/homer/
├── README.md              # this file
├── homer_cli.py           # unified CLI: init / status / checkpoint / test
├── vault/                 # L0 VAULT
│   ├── vault.py           # VaultStore, Checkpoint, persistence
│   ├── test_vault.py      # 9 smoke tests
│   └── state/             # one JSON per checkpoint
│       └── archive/       # stale checkpoints > 24h
├── zeus/                  # L2 ZEUS orchestrator
│   └── SKILL.md           # lean orchestrator-worker skill
├── muses/                 # L3 MUSES workers (P1)
├── sybil/                 # L4 SYBIL advisor (P1)
├── mnemos/                # L5 MNEMOS memory (P2)
├── oracle/                # L7 ORACLE eval (P3)
└── sleep/                 # L6 Sleep-time agents (P3)
```

## The 5 "Beat SOTA" Commitments

1. **Personalization > Anthropic MARS** — Homer is pre-tuned on the user's 148 live tier classifications, 13 sacred rules, and 500+ session history. Every MUSES worker spawn injects relevant sacred-rule context.
2. **Introspection > Claude Managed Agents** — Oracle scores every Homer output against yesterday's theater-audit pattern. Outputs with confidence below threshold get flagged for review.
3. **Accountability > Letta** — Every MNEMOS self-edit must include file:line citations from the original source. Zero dark edits. Consolidated entries keep back-pointers to archived originals.
4. **Cost-per-task > OpenHands** — Every Homer run logs `(tier, tokens_in, tokens_out, $, outcome)`. Aurora re-tunes `routing_manifest.toml` weekly against observed cost-vs-quality deltas.
5. **Offline learning > Cursor Background Agents** — Sleep-time agents run while the user sleeps. Nyx audits theater, Hesper distills learnings, Aurora tunes weights. the user wakes to a measurably-smarter Homer.

## Quick Start

```bash
# First-time setup — creates VAULT state dir + first checkpoint
python Toke/automations/homer/homer_cli.py init

# Show Homer status (layer readiness, latest checkpoint, vault health)
python Toke/automations/homer/homer_cli.py status

# List all checkpoints (newest first)
python Toke/automations/homer/homer_cli.py checkpoint list

# Read the latest checkpoint as JSON
python Toke/automations/homer/homer_cli.py checkpoint latest

# Run all VAULT smoke tests
python Toke/automations/homer/homer_cli.py test

# Archive stale checkpoints (>24h)
python Toke/automations/homer/homer_cli.py vault archive
```

## Non-Goals

- ❌ NOT a Claude Code replacement — Homer runs INSIDE Claude Code
- ❌ NOT a generic framework — the user-personalized only, not competing with LangGraph for breadth
- ❌ NOT a rewrite of Brain — Brain stays L1 unchanged (canonical v2.3)
- ❌ NOT competing with ECC on breadth — win on depth + receipts + personalization
- ❌ NOT cloud-sandboxed (P0) — VAULT is local disk, matches Brain's no-deps discipline
- ❌ NOT touching existing godspeed SKILL.md during P0 — the 7 theater kills from yesterday's audit require per-item greenlight (separate pass)

## Inheritance

All Sacred Rules and behavioral protocols from `~/CLAUDE.md` apply. Homer is strictly additive to Toke. The v4.4 fit-in-don't-force preservation rule is in full effect: no existing Toke file has been modified during P0. The theater kills proposed in yesterday's godspeed audit are parked pending per-item the user approval in a dedicated cleanup pass.

## SOTA Source Receipts

P0 was designed from real April 2026 SOTA research. Primary sources:

- Anthropic Multi-Agent Research System (orchestrator-worker pattern, 90.2% improvement over single-agent Opus)
- Claude Managed Agents (5-layer brain/hands architecture, public beta Apr 8 2026)
- Advisor API `advisor_20260301` (Apr 9 2026 — +2.7pp SWE-bench, −11.9% cost)
- Letta / MemGPT (three-tier memory + self-edit + sleep-time agents)
- LangGraph (checkpointed stateful workflows, time-travel debugging)
- OpenHands Index (5-category benchmark, 87% bug-ticket same-day resolution)
- Manus (context engineering > fine-tuning, <20 atomic tools, hierarchical composition)
- Cursor 3 Agents Window (Apr 2 2026 — parallel agents, Computer Use VMs)
- SWE-Bench Verified leaderboard (Claude Mythos Preview 93.9%, GPT-5.3 Codex 85%)
- Terminal-Bench 2.0 leaderboard (Claude Mythos Preview 82%)

The pantheon absorbs every pattern that passed the evidence test and leaves the hype at the door.
