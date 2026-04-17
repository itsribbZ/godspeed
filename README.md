# Godspeed

**A Claude Code workflow trigger backed by Toke — a routing classifier and multi-agent orchestration engine.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-compatible-8b5cf6.svg)](https://docs.claude.com/en/docs/claude-code)

Type `godspeed` in a Claude Code session, the Toke engine activates for the rest of the turn: each prompt is scored on complexity (S0–S5), routed to the cheapest model that will handle it well, and — for complex tasks — decomposed into parallel Sonnet workers whose synthesis is critic-gated before it lands in a self-improving memory.

Zero fork of Claude Code. Everything wires in through the hook API.

---

## What you get

| | |
|---|---|
| **Cost** | ~30–50% measured savings at quality parity. $750/month saved on $15K/month Opus spend via subagent routing alone. |
| **Accuracy** | Classifier scores 69.0% exact on a 200-prompt held-out eval. Beats the best naive baseline by +31.5 percentage points. |
| **Latency** | Fast-path hook at ~90 ms warm, ~160 ms cold. |
| **Compounding** | Every successful synthesis lands in a vector-embedded memory. Next session's similar prompt retrieves the prior answer in milliseconds. |

Reproduce the benchmark locally: `python toke/automations/brain/eval/brain_vs_baselines.py --json out.json`.

---

## Install

### Option A — Claude Code plugin (recommended, ~30 seconds)

Inside a Claude Code session:

```
/plugin marketplace add itsribbZ/godspeed
/plugin install godspeed@itsribbZ-godspeed
```

That's it. The plugin ships the full engine — all 16 skills, 2 commands, 5 hooks, and the Python automation stack land in Claude Code's plugin cache and register automatically. No shell profile edits, no manifest pasting, no restart required.

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
├── README.md          ← this file (the trigger pitch)
├── install.sh         ← one-command install (macOS / Linux / Git Bash)
├── install.ps1        ← Windows PowerShell installer
├── LICENSE            ← MIT
└── toke/              ← the engine
    ├── README.md      ← detailed architecture + design docs
    ├── skills/        ← 16 Claude Code skills (installer copies to ~/.claude/skills/)
    ├── commands/      ← 2 slash commands (installer copies to ~/.claude/commands/)
    ├── hooks/         ← Claude Code lifecycle hook scripts
    ├── automations/   ← classifier + orchestrator + maintenance agents
    ├── pipeline/      ← 8-stage measurement of Claude Code internals
    ├── tokens/        ← cost-accounting tools
    └── research/      ← literature review that fed the classifier design
```

Godspeed (the trigger) is one skill inside `toke/skills/`. It's what fires when you type the word. Everything else in `toke/` is the machinery that skill commands.

---

## Deeper docs

For architecture, design decisions, reproducible benchmarks, references, and usage examples, see **[toke/README.md](toke/README.md)**.

## License

MIT. See `LICENSE`.
