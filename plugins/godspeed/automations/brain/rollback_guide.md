# Toke Brain — Rollback Guide

Every Brain component is reversible. Use the step for whatever you need to undo.

---

## Full rollback — remove everything

### 1. Restore settings.json from backup
```bash
ls ~/Desktop/T1/Toke/hooks/settings_backup_*.json
# Pick the most recent backup
cp "~/Desktop/T1/Toke/hooks/settings_backup_LATEST.json" ~/.claude/settings.json
```

### 2. Remove env exports from shell rc
Edit `~/.bashrc` (or `~/.bash_profile`) and delete these lines:
```
export CLAUDE_CODE_SUBAGENT_MODEL="sonnet"
export CLAUDE_CODE_EFFORT_LEVEL="max"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"
```
Restart your terminal.

### 3. Remove skill frontmatter pinnings
For each skill you pinned, edit `~/.claude/skills/SKILLNAME/SKILL.md` and remove the `model:` and `effort:` lines from the frontmatter block.

Or in bulk, grep for them:
```bash
grep -rn "^model: " ~/.claude/skills/*/SKILL.md
grep -rn "^effort: " ~/.claude/skills/*/SKILL.md
```

### 4. (Optional) Uninstall the brain skill
```bash
rm -rf ~/.claude/skills/brain/
```

### 5. (Optional) Delete telemetry
```bash
rm -rf ~/.claude/telemetry/brain/
```

### 6. (Optional) Delete Brain source files
```bash
rm -rf ~/Desktop/T1/Toke/automations/brain/
rm ~/Desktop/T1/Toke/hooks/brain_advisor.sh
rm ~/Desktop/T1/Toke/hooks/brain_telemetry.sh
```

---

## Partial rollbacks

### Disable advisory hook only (keep telemetry)
Edit `~/.claude/settings.json`. Remove the `brain_advisor.sh` entry from `hooks.UserPromptSubmit`. Keep `brain_telemetry.sh` in `PostToolUse` if you want to keep logging.

### Disable telemetry hook only
Edit `~/.claude/settings.json`. Remove the `brain_telemetry.sh` entry from `hooks.PostToolUse`. `task-tracker.sh` stays (it's a separate Toke hook).

### Disable subagent auto-routing (revert to all-Opus subagents)
Remove `CLAUDE_CODE_SUBAGENT_MODEL` from `~/.bashrc`. Restart terminal. Subagents will inherit the main session model again.

### Unpin a single skill
Edit `~/.claude/skills/SKILLNAME/SKILL.md`. Delete the `model:` and `effort:` lines from the frontmatter block only. Don't touch the rest of the frontmatter.

### Keep telemetry, disable routing
Leave the hooks wired (they still log, which gives you data). Remove the env vars and unpin skills. You'll get observability without any routing changes.

---

## Emergency procedures

### The advisor hook is producing too much noise
1. Immediately comment out the `brain_advisor.sh` entry in `~/.claude/settings.json`
2. Restart Claude Code (new session)
3. Verify no more `[brain]` messages in stderr

### Brain is blocking the turn somehow
This should never happen — the hooks are designed to fail silent. But if it does:
1. Remove the brain hooks from settings.json immediately
2. Test with a fresh Claude Code session
3. File an incident note in `Toke/hooks/incident_YYYY-MM-DD.md` describing the symptom

### Pinned a skill and it broke
1. Get the skill path from the pin command's output
2. Restore from git history if the skill is versioned
3. Otherwise, manually edit the SKILL.md and remove the broken frontmatter
4. The Brain pin command is idempotent — re-running it with `--write` should produce valid frontmatter

---

## Verification after rollback

To confirm Brain is fully disabled:

```bash
# 1. Env vars unset
echo "$CLAUDE_CODE_SUBAGENT_MODEL $CLAUDE_CODE_EFFORT_LEVEL $ANTHROPIC_DEFAULT_HAIKU_MODEL"
# Expected: three blank lines

# 2. No brain hooks in settings.json
grep -c "brain_" ~/.claude/settings.json
# Expected: 0

# 3. No skill frontmatter pinnings (check a few)
head -n 6 ~/.claude/skills/sitrep/SKILL.md
# Expected: no model: or effort: lines

# 4. Telemetry dir clean or gone
ls ~/.claude/telemetry/brain/ 2>/dev/null
# Expected: empty or "No such file"
```

If all four checks pass, Brain is fully rolled back.
