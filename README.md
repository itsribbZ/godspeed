# Toke

> **A routing classifier + multi-agent orchestrator for Claude Code.** Picks Haiku/Sonnet/Opus per prompt. Saves 4-8% of Claude Code spend without quality loss. Beats naive baselines by 31-52 percentage points on accuracy.

---

## What Toke does

Toke sits between your prompts and Anthropic's API. For every prompt:

1. **Brain** classifies task complexity (S0-S5) via manifest-driven signals + guardrails
2. **Homer** (optional) decomposes complex tasks into parallel Sonnet workers with Oracle-gated memory writes
3. **Hooks** wire both into Claude Code's UserPromptSubmit / PostToolUse / SessionEnd events
4. **Sleep agents** (nightly) tune weights, distill learnings, audit theater — no manual maintenance

Everything runs locally. No external API beyond Anthropic's. Stdlib-only for Brain; SQLite for Homer; Node.js for the fast-path hook (~90ms warm).

---

## Measured results

200-prompt held-out benchmark (2026-04-17). See [brain_vs_baselines_2026-04-17.json](automations/brain/eval/brain_vs_baselines_2026-04-17.json).

```
Classifier                        Exact   Weighted   Wrong
─────────────────────────────────────────────────────────
Brain (v2.6.3)                   69.0%     0.843       1
Majority-class (always S3)       37.5%     0.603      34
Keyword-only                     28.5%     0.490      61
Length-only                      25.0%     0.532      37
Random (seed=42)                 17.0%     0.320     106
```

Brain beats the best naive baseline by **31.5 pp exact match**. One wrong classification in 200. 199/200 are either exact or adjacent (one tier off, same model in most cases).

**Monthly cost impact** (measured on ~$15K Opus spend): Zone-2 subagent routing alone saves ~$750/mo. Auto-Zeus orchestration on S3+ tasks projects to $600-1,200/mo additional savings at quality parity (MARS pattern).

---

## Architecture

```
                Claude Code prompt
                       │
          UserPromptSubmit hook (~90ms warm)
                       │
                  Brain classify
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
     S0-S2         S3 routed       S4-S5
     (Haiku)       to Zeus         (Opus)
                       │
                       ▼
              Zeus decompose
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
     Calliope       Clio          Urania      (parallel Sonnet muses)
     (research)   (code arch)  (measurement)
                       │
                       ▼
              Oracle scores synthesis
             (sacred rules + rubric + theater)
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
       PASS / SOFT_FAIL        HARD_FAIL
           │                       │
           ▼                       ▼
     Mnemos writes          Block write,
     (3-tier memory)        return verdict
           │
           ▼
     VAULT v2 SQLite
     (WAL, retry/backoff, replay)
```

---

## Install

### Requirements
- Python 3.10+ (stdlib only for Brain; `sentence-transformers` optional for Mnemos vector search)
- Node.js 18+ (for the fast-path hook)
- Claude Code CLI (the hooks wire into it)

### One-command install (recommended)

Clone the repo, then run the installer for your platform. It copies all 16 skills + 2 slash commands into `~/.claude/`, syncs the Brain manifest, and runs the 65-test verification.

```bash
git clone https://github.com/<your-username>/toke
cd toke

# macOS / Linux / Git Bash on Windows
bash install.sh

# Windows PowerShell
.\install.ps1
```

The installer will NOT overwrite existing skills by default. Pass `--force` (bash) or `-Force` (PowerShell) to replace them (old versions are backed up to `.bak.<timestamp>` directories).

### What gets installed

```
~/.claude/skills/
├── godspeed/      ← max-execution mode (activates on "godspeed")
├── brain/         ← S0-S5 classifier workbench
├── zeus/          ← orchestrator (auto-dispatched on S3+ via godspeed Phase 0.5)
├── calliope/      ← research muse
├── clio/          ← code-archaeology muse
├── urania/        ← measurement muse
├── sybil/         ← advisor escalation (advisor_20260301)
├── mnemos/        ← 3-tier memory with hybrid semantic+FTS5 search
├── oracle/        ← synthesis critic (Sacred Rules + theater detection)
├── aurora/        ← nightly routing-weight tuner
├── hesper/        ← nightly learning distiller
├── nyx/           ← nightly theater auditor
├── toke-init/     ← session pre-flight
├── close-session/ ← session closure + memory persistence
├── verify/        ← build/test health check (auto-detects project type)
└── sitrep/        ← cross-project status aggregator

~/.claude/commands/
├── toke.md        ← /toke — one-screen workbench
└── brain-score.md ← /brain-score — classify any prompt
```

### Wire hooks into Claude Code

After the install script finishes, add this block to `~/.claude/settings.json` (the installer prints it at the end of its run):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "command": "$TOKE_ROOT/hooks/brain_advisor.sh" }
    ],
    "PostToolUse": [
      { "command": "$TOKE_ROOT/hooks/brain_tools_hook.sh" }
    ],
    "SessionEnd": [
      { "command": "$TOKE_ROOT/hooks/session_cost_report.sh" }
    ]
  }
}
```

Also persist `TOKE_ROOT` in your shell profile so the hooks can find the install location:

```bash
# bash / zsh
export TOKE_ROOT="$(pwd)"
# PowerShell (run once)
[Environment]::SetEnvironmentVariable('TOKE_ROOT', (Get-Location).Path, 'User')
```

### First test (prove it works)

Start a new Claude Code session and try:

```
/brain-score refactor my distributed cache across 4 files
```

Expected output: `Tier: S4 | Model: opus | Effort: high`.

Then type `godspeed` and ask for any multi-step task. Phase -1 tick will fire, Phase 0.5 will classify the prompt, and if the task scores S3+ Zeus will auto-dispatch with parallel MUSES + Oracle-gated Mnemos write.

### Optional: nightly sleep agents

Aurora (weight tuner), Hesper (learning distiller), Nyx (theater auditor) can run nightly via Windows Task Scheduler:

```bash
schtasks //create //tn "Toke_Homer_Sleep_Nightly" //tr "$TOKE_ROOT/automations/homer/sleep/run_sleep_nightly.bat" //sc DAILY //st 04:00 //f
```

Or on macOS/Linux, add a cron entry:

```cron
0 4 * * * $TOKE_ROOT/automations/homer/sleep/run_sleep_nightly.sh
```

---

## Project layout

```
toke/
├── README.md                  # this file
├── install.sh                 # bash installer (macOS / Linux / Git Bash)
├── install.ps1                # PowerShell installer (Windows)
├── .env.example               # env var template
├── CLAUDE.md                  # project context for AI collaborators
├── PROJECT_BRIEF.md           # 8-stage pipeline analysis
├── skills/                    # 16 Claude Code skills (installer copies to ~/.claude/skills/)
├── commands/                  # 2 slash commands (installer copies to ~/.claude/commands/)
├── pipeline/                  # measured Claude Code internals (stages 0-7)
├── tokens/                    # 12 measurement tools + reports
├── research/                  # Brain / routing literature review
├── automations/
│   ├── brain/                 # S0-S5 classifier (manifest-driven)
│   ├── gepa/                  # evolutionary weight tuner
│   ├── homer/                 # multi-agent pantheon (zeus + zeus_cli, muses, oracle, mnemos, vault, sleep)
│   ├── local/                 # Qwen 2.5 14B via Ollama fallback
│   ├── governance/            # audit_protocol.py + threat model
│   └── portability/           # migration guide
└── hooks/                     # UserPromptSubmit / PostToolUse / SessionEnd
```

---

## Usage

### Classify a prompt (Brain)

```bash
python automations/brain/brain_cli.py score "design a distributed caching layer"
# → Tier: S4 | Model: opus | Effort: high | Score: 0.400 | Reason: complex_design_floor + system_scope_floor
```

### Cost scan (30-day window)

```bash
python automations/brain/brain_cli.py scan
# → Total spend: $15,644 | Savings projection: $753 achievable (Zone 2 subagents)
```

### Run the Homer pipeline (Oracle-gated synthesis → Mnemos)

```python
from zeus.zeus_pipeline import gate_and_write
from oracle.oracle import Oracle
from mnemos.mnemos import MnemosStore

result = gate_and_write(
    oracle=Oracle(),
    store=MnemosStore(),
    synthesis="...your Zeus synthesis text...",
    topic="my_analysis",
    citations=["session:my_session_id"],
)
# result.written=True, verdict="PASS", entry_id="recall_20260417_..."
```

### Run sleep agents manually

```bash
python automations/homer/sleep/sleep_cli.py run all
# Aurora (weight tuner) + Hesper (learning distiller) + Nyx (theater auditor)
# Reports land in automations/homer/sleep/{aurora,hesper,nyx}/
```

### Benchmark against baselines

```bash
python automations/brain/eval/brain_vs_baselines.py --json out.json
```

---

## Key design decisions

1. **Manifest-driven, not learned.** Brain's tier boundaries, signal weights, and guardrails live in a TOML manifest. Anyone can audit and edit. No opaque model weights to explain.

2. **Fail-open everywhere.** If Brain crashes, default to S4 (Opus). If hooks error, exit 0 silently. No hook can block a Claude turn.

3. **Oracle gates Mnemos writes in code.** Sacred-rule HARD_FAIL blocks memory writes. Enforced by `zeus_pipeline.gate_and_write()`, not by convention.

4. **Sleep agents propose, humans decide.** Aurora doesn't auto-apply weight changes. Nyx doesn't auto-delete theatrical SKILL.md sections. Every change is a proposal awaiting greenlight.

5. **Stdlib-first.** Brain + all core Python is stdlib only. Dependency chain is short: Node.js for the fast hook, SQLite for VAULT v2. Optional: `anthropic` for LLM fallback, `dspy-ai` for GEPA.

6. **Receipts mandatory.** Every accuracy claim is backed by `eval/*.json` + reproducible via `brain_vs_baselines.py`. No magic.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKE_ROOT` | `$HOME/Desktop/T1/Toke` (author's layout) | Install location of the Toke repo |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `sonnet` | Zone 2 subagent routing (Brain Consultation §2) |
| `ANTHROPIC_API_KEY` | — | Required for `brain advise` (Sybil L4 advisor escalation) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Local Qwen fallback at `automations/local/` |

See `.env.example` for a complete template.

---

## License

MIT (pending — currently a private personal project).

---

## Author

Ribbz. 21. AI-collaborative systems engineer building solo in Tahoe.

Built with Claude Code as implementation partner. Architecture + design decisions are mine; implementation is a collaboration. See `CLAUDE.md` for the working protocol.
