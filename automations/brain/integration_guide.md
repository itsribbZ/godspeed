# Toke Brain — Integration Guide

Step-by-step wire-in. Each step is reversible. See `rollback_guide.md` for undo steps.

---

## Pre-flight (MANDATORY)

### Step 0 — Backup settings.json
**Do this before any changes to `~/.claude/settings.json`.**

```bash
cp ~/.claude/settings.json "~/Desktop/T1/Toke/hooks/settings_backup_$(date +%Y-%m-%d_%H%M%S).json"
```

### Step 1 — Verify prerequisites
- **Python 3.11+** (required for stdlib `tomllib`): `python3 --version`
- **Brain files exist:**
  - `~/Desktop/T1/Toke/automations/brain/severity_classifier.py`
  - `~/Desktop/T1/Toke/automations/brain/routing_manifest.toml`
  - `~/Desktop/T1/Toke/automations/brain/brain_cli.py`
  - `~/Desktop/T1/Toke/automations/brain/brain_tests.py`
  - `~/Desktop/T1/Toke/hooks/brain_advisor.sh`
  - `~/Desktop/T1/Toke/hooks/brain_telemetry.sh`

### Step 2 — Smoke test the classifier
```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" test
```
Expected: all test cases pass. If any fail, inspect the manifest — most likely a keyword is missing or a threshold needs tuning.

Sanity test with a specific prompt:
```bash
echo '{"prompt_text": "list files"}' | python3 "~/Desktop/T1/Toke/automations/brain/severity_classifier.py"
```
Expected: JSON output with `"tier": "S0"` or `"S1"`, `"model": "haiku"`.

```bash
echo '{"prompt_text": "refactor EXOSeedSubsystem across 5 files and design the event dispatcher"}' | python3 "~/Desktop/T1/Toke/automations/brain/severity_classifier.py"
```
Expected: `"tier": "S4"` or `"S5"`, `"model": "opus"` or `"opus[1m]"`, guardrails list contains `multi_file_or_ue5`.

### Step 3 — Run the baseline scan
```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" scan
```
This shows the current 30-day cost breakdown + projected savings if Brain were fully applied. **Capture this output — it's the baseline to measure against.**

---

## Phase 1 — Zone 2 (Automatic Subagent + Skill Routing)

This is where the real cost savings live. No hooks, no settings.json changes — just env vars and skill frontmatter.

### Step 4 — Enable subagent override env var
```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" apply-env
```
Copy the output and append to `~/.bashrc` (or `~/.bash_profile` on some setups). Restart your terminal so the vars take effect.

**Effect:** All subagent calls (Agent tool, internal research agents) will default to Sonnet instead of inheriting Opus from the main session. Main session stays on whatever you set.

**Expected impact:** 15-20% cost reduction on total spend.

### Step 5 — Audit the skill tier assignments
```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" audit-skills
```
Review the output. Everything listed under S1 (Haiku) and S2 (Sonnet) is a candidate for pinning.

### Step 6 — Pin Haiku-safe skills
```bash
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin sitrep --write
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin pulse --write
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin find --write
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin keybindings-help --write
python3 "~/Desktop/T1/Toke/automations/brain/brain_cli.py" pin update-config --write
```

### Step 7 — Pin Sonnet-safe skills (optional but recommended)
The full list is in the `brain audit-skills` output. One command per skill. These are the init skills, verify, close-session, organizer, finder, reference, blend-master, player1, scanner, simplify, etc.

### Step 8 — Install the Brain skill (optional)
The `/brain` workbench skill lives at `~/Desktop/T1/Toke/automations/brain/SKILL.md`. To activate it as a user-invokable skill, copy it into `~/.claude/skills/brain/`:

```bash
mkdir -p ~/.claude/skills/brain
cp "~/Desktop/T1/Toke/automations/brain/SKILL.md" ~/.claude/skills/brain/SKILL.md
```

After this, saying "brain scan" or "brain audit-skills" in a Claude Code session will auto-invoke the workbench.

---

## Phase 2 — Zone 1 (Advisory Hooks)

Phase 1 alone captures most of the savings. Phase 2 is optional: it adds visibility into main-session routing but cannot automatically change the model.

### Step 9 — Wire the UserPromptSubmit advisory hook

Edit `~/.claude/settings.json`. Find the `"hooks"` section. Add a `UserPromptSubmit` entry (or append to it if one exists):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "**/*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/Desktop/T1/Toke/hooks/brain_advisor.sh"
          }
        ]
      }
    ]
  }
}
```

### Step 10 — Wire the PostToolUse telemetry hook

Append `brain_telemetry.sh` to `hooks.PostToolUse`. Don't replace the existing `task-tracker.sh` entry:

```json
"PostToolUse": [
  {
    "matcher": "**/*",
    "hooks": [
      { "type": "command", "command": "bash ~/.claude/hooks/task-tracker.sh" },
      { "type": "command", "command": "bash ~/Desktop/T1/Toke/hooks/brain_telemetry.sh" }
    ]
  }
]
```

### Step 11 — Test the hooks in a fresh session
Start a new Claude Code session. Type a simple prompt (e.g., "list files in current directory"). The Brain should:

1. Write an entry to `~/.claude/telemetry/brain/decisions.jsonl`
2. If the session is running on Opus and the task is S0/S1, emit an advisory to stderr: `[brain] S1 task -> /model haiku would suit this turn`

Verify:
```bash
tail -n 3 ~/.claude/telemetry/brain/decisions.jsonl
tail -n 3 ~/.claude/telemetry/brain/tools.jsonl
```

---

## Phase 3 — Monitoring

### Step 12 — Weekly scan
Run `brain scan` weekly. Compare:
- Actual Opus cost vs baseline from Step 3
- Tier distribution of decisions.jsonl
- Sessions with the most advisories (tells you which workflows are mis-routed)

### Step 13 — Tune the manifest
If Brain is under-routing to Opus (you notice quality drops), increase the relevant signal weights or add more keywords to guardrails. If it's over-routing (too much Opus when Sonnet would suffice), reduce weights.

**All tuning happens in `routing_manifest.toml`. No code changes.** Classifier reads the file on every call, so changes take effect immediately.

---

## Troubleshooting

### `tomllib` import error
Python < 3.11. `tomllib` is stdlib from 3.11 onward. Upgrade Python, or install `tomli` and modify the classifier to use it as a fallback (not done by default because zero dependencies was a design goal).

### Hook not firing
Check `~/.claude/telemetry/brain/decisions.jsonl`:
- Missing = hook isn't wired correctly in settings.json
- Present but empty = hook is wiring but classifier failing silently

Debug the classifier manually:
```bash
echo '{"prompt_text":"test"}' | bash "~/Desktop/T1/Toke/hooks/brain_advisor.sh"
```

### Advisory never shows
Advisories only emit when `current_model != recommended`. If the session is on Opus and the task is rated S4+, they match — no advisory. This is correct behavior.

### Telemetry files growing
Rotate monthly:
```bash
cd ~/.claude/telemetry/brain
mv decisions.jsonl "decisions_$(date +%Y-%m).jsonl"
mv tools.jsonl "tools_$(date +%Y-%m).jsonl"
```

### Brain scan shows wrong numbers
Check that `stats-cache.json` has recent data (`lastComputedDate` field). If stale, the scan is using old data.

### Skill pin corrupted a skill
Every `pin --write` is atomic, but if something went wrong, restore the skill from git history or the backup. The pin command prints the path before writing — you can always manually remove the `model:` and `effort:` frontmatter lines.

---

## What NOT to do

- **Don't edit `severity_classifier.py` to tune weights.** Use the manifest.
- **Don't hardcode model names in the classifier.** Use tier_map.
- **Don't modify `settings.json` without backing up first** — Toke Sacred Rule.
- **Don't try to make the advisor hook auto-switch the main model.** It can't. See the synthesis doc for why.
- **Don't delete `task-tracker.sh` when wiring telemetry** — it's a separate Toke hook, unrelated to Brain.
