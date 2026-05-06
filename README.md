# Godspeed

**A Claude Code workflow trigger backed by Toke — a routing classifier and multi-agent orchestration engine.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-compatible-8b5cf6.svg)](https://docs.claude.com/en/docs/claude-code)

Type `godspeed` in a Claude Code session, the Toke engine activates for the rest of the turn: each prompt is scored on complexity (S0–S5), routed to the cheapest model that will handle it well, and — for complex tasks — decomposed into parallel Sonnet workers whose synthesis is critic-gated before it lands in a self-improving memory.

Zero fork of Claude Code. Everything wires in through the hook API.

> **Free by default — no API key required.** Godspeed never auto-fires Anthropic API calls. The 5 lifecycle hooks use only the local-only `brain_hook_fast.js` classifier (a regex/keyword scorer running in your shell, not a network call). The Homer pantheon dispatches subagents through Claude Code's in-session `Agent` tool, which is part of your existing Claude Code session — no separate billing channel against your Anthropic key. Two paths can opt into direct API calls, both gated behind explicit user commands and an `ANTHROPIC_API_KEY` env var: `brain advise "<prompt>"` (advisor escalation) and `agent_runner.py invoke <name> --mode live` (live subagent dispatch). Unset the key, never type those commands, and the plugin runs at $0 against your Anthropic API account. The included `cost_guard.py` enforces per-tier USD ceilings on the live path so even when you opt in, you can't accidentally burn your credit balance.

---

## What you get

| | |
|---|---|
| **Cost** | Sonnet auto-routing on subagent calls via `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, plus per-tier USD ceilings (`automations/homer/cost_guard.py`) on the opt-in live-dispatch path. Real savings = your-Opus-spend × your-S3+-rate. Run `brain scan` after a few sessions to see your number. No projected dollar headline here — the savings depend entirely on your prompt mix. |
| **Accuracy** | Classifier evaluation harness ships at `toke/automations/brain/eval/eval_harness.py` + `brain_vs_baselines.py`. **Public golden set is not yet shipped** — bring your own labeled `golden_set.json` (schema documented in `toke/README.md`) and run `python toke/automations/brain/eval/brain_vs_baselines.py` to score the classifier against majority-class / keyword-only / length-only / random baselines on your prompts. A small public golden set ships in v2.5.0. |
| **Latency** | Fast-path hook ~90 ms warm, ~160 ms cold. Measured on Windows 10 / Node 18 — re-measure on your own system; hook timing is environment-sensitive. |
| **Cost guard** | Every live subagent dispatch carries a tier-stamped USD ceiling (S0 $0.005 → S5 $5.00) with a 1.5× soft-cap. Mid-flight breach triggers `BUDGET_EXCEEDED` and preserves partial work. Receipts to `~/.claude/telemetry/brain/cost_efficiency.jsonl`. Verify locally: `python plugins/godspeed/automations/homer/cost_guard.py budgets`. |
| **Compounding** | Synthesis from S3+ Zeus runs lands in Mnemos Recall (SQLite + FTS5, optional vector embeddings). Slow-burn benefit — only pays off after dozens of successful gate-writes. New users see no compounding for the first weeks. |

---

## Who this is — and isn't — for

**Strong fit:** Claude Code power-users running 4+ hour sessions on architecture-heavy or multi-file tasks, anyone who's been over-spending on Opus and wants Sonnet auto-routing for subagents, devs who want opinionated multi-agent structure baked in rather than rolling their own.

**Skip this plugin if:** your typical prompts are simple ("fix this typo / add this feature / explain this code") — those are S0–S2 tasks; the methodology adds overhead without proportional benefit. Also skip if you prefer vanilla Claude Code defaults (this changes execution flow), if you work in a team that needs shared state (this is a solo-dev workflow with no multi-user mode), or if a Greek-pantheon naming convention (Zeus, Oracle, Mnemos, Calliope, Clio, Urania) is a non-starter for you aesthetically.

This is one developer's methodology, scrubbed of project-specific names and shipped MIT. The shape reflects the maintainer's preferences. Your mileage will vary by how much your workflow looks like theirs — and the right move if you're unsure is to install it for one heavy session, run `brain scan` afterwards, and decide based on your own numbers.

---

## Install

### Option A — Claude Code plugin (recommended, ~30 seconds)

Inside a Claude Code session:

```
/plugin marketplace add itsribbZ/godspeed
/plugin install godspeed@itsribbZ-godspeed
```

That's it. The plugin ships the full engine — all 18 skills, 3 commands, 5 hooks, and the Python automation stack land in Claude Code's plugin cache and register automatically. No shell profile edits, no manifest pasting, no restart required.

### Option B — install.sh (for users who prefer `~/.claude/skills/` install)

```bash
git clone https://github.com/itsribbZ/godspeed
cd godspeed

# macOS / Linux / Git Bash on Windows
bash install.sh

# Windows PowerShell
.\install.ps1
```

The installer:
1. Copies the operational skills + `brain-score` slash command into `~/.claude/`
2. Syncs the routing manifest (TOML → JSON)
3. Prints the `settings.json` hook block you need to paste

Use Option B if you want the skills/commands accessible outside of the plugin namespace (e.g. you want `zeus` instead of `godspeed:zeus`) or if Claude Code's plugin system isn't available in your environment.

---

## Quick start

Four steps from fresh install to running Godspeed on a prompt.

### 1. Wire up the hooks (one-time)

**Plugin install (Option A):** hooks auto-register. Skip to the verification step below.

**install.sh install (Option B):** do three things, in this order:

1. **Paste** the hook block the installer printed into `~/.claude/settings.json` (under the `hooks` key — merge with any existing hooks, don't replace).
2. **Export** `TOKE_ROOT` in your shell profile. The installer prints the exact path — add it to `~/.bashrc`, `~/.zshrc`, or your PowerShell profile:
   ```bash
   export TOKE_ROOT="$HOME/godspeed/toke"
   ```
3. **Restart** Claude Code. This reloads the hooks and picks up the new env var.

**Verify either install is live:**

```
/brain-score "refactor my distributed cache across 4 files"
→ Tier: S4 | Model: opus | Effort: high
```

If you see a tier classification, Godspeed is wired in correctly.

### 2. Run Godspeed on a prompt

Prepend `godspeed` to any prompt you want the full pipeline to handle:

```
godspeed fix the T-pose in my AnimBP and add a slide ability
```

What happens automatically, in order:

| Step | What runs | Why |
|------|-----------|-----|
| 1 | **Tick + self-audit** (every 33rd run) | Catches routing drift before it compounds |
| 2 | **Brain scores** the prompt (~5 ms, keyword + regex) | Classifies complexity on the S0–S5 scale |
| 3 | **Router picks** the cheapest model that will handle it | S0–S2 → Haiku or local Qwen · S3 → Sonnet · S4–S5 → Opus |
| 4 | **For S3+:** Zeus decomposes into parallel Sonnet workers | ~10× cheaper than monolithic Opus, quality parity (Anthropic MARS pattern) |
| 5 | **Oracle critiques** the synthesis against a 10-point AAA rubric | Quality gate: PASS / SOFT_FAIL / HARD_FAIL |
| 6 | **Mnemos stores** the PASSing answer with vector embeddings | Next similar prompt retrieves in milliseconds |
| 7 | **Reconcile** — verify every triaged item was done, blocked, or deferred | Zero missed tasks |

### 3. Close the session

Type `close session` when your work is done. Decisions, learnings, and memory writes are persisted to `~/.claude/projects/<project>/memory/`. Next session's `init` picks up exactly where this one left off.

### 4. Useful commands

| Command | What it does |
|---------|-------------|
| `godspeed <prompt>` | Activate the full pipeline for the prompt |
| `godspeed info` | Render the pipeline diagram — read-only, no execution |
| `/brain-score "prompt"` | Classify complexity without running anything |
| `close session` | Persist memory, write learnings, summarize |

---

## Repo layout

```
godspeed/
├── README.md             ← this file (the trigger pitch)
├── CHANGELOG.md          ← version history
├── RELEASE.md            ← maintainer release process
├── LICENSE               ← MIT
├── install.sh            ← Option B installer (macOS / Linux / Git Bash)
├── install.ps1           ← Option B installer (Windows PowerShell)
├── .claude-plugin/       ← marketplace manifest (Option A)
├── .github/workflows/    ← CI (validates manifests + counts on every push)
├── plugins/godspeed/     ← Option A — Claude Code plugin install target
│   ├── .claude-plugin/   ← plugin.json (canonical version + description)
│   ├── skills/           ← 18 skills
│   ├── commands/         ← 3 slash commands
│   ├── hooks/            ← 5 lifecycle hooks + hooks.json manifest
│   ├── automations/      ← Python runtime (classifier + orchestrator)
│   └── shared/           ← shared protocols + learnings + PDF contract
└── toke/                 ← Option B — install.sh / install.ps1 install target
    ├── README.md         ← detailed architecture + design docs
    ├── skills/           ← 16 skills (installer copies to ~/.claude/skills/)
    ├── commands/         ← 2 slash commands
    ├── hooks/            ← Claude Code lifecycle hook scripts
    ├── automations/      ← runtime mirror of plugins/godspeed/automations/
    ├── pipeline/         ← 8-stage measurement of Claude Code internals
    ├── tokens/           ← cost-accounting tools
    └── research/         ← literature review that fed the classifier design
```

The `godspeed` skill itself ships in **both** install paths — `plugins/godspeed/skills/godspeed/` for Option A, `toke/skills/godspeed/` for Option B. Everything else in either tree is the machinery that skill commands.

### What each install path includes

The two install paths ship **different curated skill sets** — not the same set with different packaging:

| Skill | Option A (plugin) | Option B (install.sh) |
|---|:-:|:-:|
| brain, calliope, clio, close-session, godspeed, mnemos, oracle, sybil, urania, verify, zeus | ✅ | ✅ |
| blueprint, cycle, devTeam, holy-trinity, init, profTeam, professor | ✅ | — |
| aurora, hesper, nyx, sitrep, toke-init | — | ✅ |
| **Total** | **18** | **16** |

- **Option A** ships the full **research/review pipeline** (devTeam architecture scoring, profTeam multi-agent research, holy-trinity diagnose-research-implement-verify loop, professor + blueprint + cycle for deep planning).
- **Option B** exclusively adds the **maintenance + introspection** stack (aurora weight-tuning, hesper learning distillation, nyx skill-description auditing, sitrep cross-project status, toke-init session loader).

If you want both sets, install Option A via the plugin, then run `bash install.sh` — Option B's installer preserves existing skills with the same name unless `--force` is passed, so it'll only add the unique-to-B skills.

---

## Deeper docs

For architecture, design decisions, reproducible benchmarks, references, and usage examples, see **[toke/README.md](toke/README.md)**.

## License

MIT. See `LICENSE`.
