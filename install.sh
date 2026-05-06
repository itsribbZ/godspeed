#!/usr/bin/env bash
# Godspeed / Toke installer — macOS / Linux / Windows (Git Bash, WSL)
#
# Run from the godspeed/ repo root. The engine lives in ./toke/.
#
# What this does:
#   1. Sets TOKE_ROOT to ./toke/ inside this checkout
#   2. Copies all skills from ./toke/skills/ into ~/.claude/skills/
#   3. Copies all slash commands from ./toke/commands/ into ~/.claude/commands/
#   4. Syncs the Brain routing manifest (TOML -> JSON)
#   5. Runs the full test suite to verify the install
#   6. Prints the settings.json snippet you need to paste to wire hooks
#
# What this does NOT do:
#   - Modify your ~/.claude/settings.json (you decide when to wire the hooks)
#   - Install Python / Node / sentence-transformers (see README for deps)
#   - Overwrite existing skills with the same name (it will warn and skip)
#
# Usage:
#   bash install.sh           # install + verify + print hook snippet
#   bash install.sh --force   # overwrite existing skills/commands (backup to .bak first)
#   bash install.sh --skip-tests  # skip the test suite verification at the end

set -euo pipefail

# ── Helpers ────────────────────────────────────────────────────────────────────
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
log()   { echo "${CYAN}[toke]${RESET} $*"; }
ok()    { echo "${GREEN}  ✓${RESET} $*"; }
warn()  { echo "${YELLOW}  ⚠${RESET} $*"; }
die()   { echo "${RED}  ✗${RESET} $*"; exit 1; }

FORCE=0
SKIP_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "Unknown arg: $arg" ;;
  esac
done

# ── Pre-flight ─────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
TOKE_ROOT="$REPO_ROOT/toke"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
SKILLS_DIR="$CLAUDE_HOME/skills"
COMMANDS_DIR="$CLAUDE_HOME/commands"

log "Godspeed / Toke installer"
log "REPO_ROOT   = $REPO_ROOT"
log "TOKE_ROOT   = $TOKE_ROOT"
log "CLAUDE_HOME = $CLAUDE_HOME"

[ -d "$TOKE_ROOT" ] || die "No toke/ subfolder in $REPO_ROOT. Are you running from the godspeed/ repo root?"

command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 || die "Python 3.10+ is required but not found on PATH"
PY=$(command -v python3 >/dev/null 2>&1 && echo python3 || echo python)
log "python      = $($PY --version 2>&1)"

[ -d "$TOKE_ROOT/skills" ] || die "No skills/ dir in $TOKE_ROOT. Are you running from the repo root?"
[ -d "$TOKE_ROOT/commands" ] || die "No commands/ dir in $TOKE_ROOT."

mkdir -p "$SKILLS_DIR" "$COMMANDS_DIR"

# ── Copy skills ────────────────────────────────────────────────────────────────
log "Installing skills into $SKILLS_DIR/ ..."
installed=0; skipped=0; overwrote=0
for src in "$TOKE_ROOT/skills/"*/; do
  name=$(basename "$src")
  dest="$SKILLS_DIR/$name"
  if [ -d "$dest" ]; then
    if [ "$FORCE" -eq 1 ]; then
      mv "$dest" "${dest}.bak.$(date +%s)"
      cp -r "$src" "$dest"
      overwrote=$((overwrote+1))
      ok "overwrote $name (backup at ${dest}.bak.*)"
    else
      skipped=$((skipped+1))
      warn "skipped $name — already installed (use --force to overwrite)"
    fi
  else
    cp -r "$src" "$dest"
    installed=$((installed+1))
    ok "installed $name"
  fi
done
log "Skills: $installed new, $overwrote overwritten, $skipped skipped"

# ── Copy slash commands ────────────────────────────────────────────────────────
log "Installing slash commands into $COMMANDS_DIR/ ..."
for src in "$TOKE_ROOT/commands/"*.md; do
  name=$(basename "$src")
  dest="$COMMANDS_DIR/$name"
  if [ -f "$dest" ] && [ "$FORCE" -eq 0 ]; then
    warn "skipped $name — already exists (use --force to overwrite)"
  else
    cp "$src" "$dest"
    ok "installed /$(basename "$name" .md)"
  fi
done

# ── Sync Brain routing manifest ───────────────────────────────────────────────
log "Syncing Brain routing manifest (TOML -> JSON) ..."
if [ -f "$TOKE_ROOT/automations/brain/manifest_to_json.py" ]; then
  (cd "$TOKE_ROOT" && $PY automations/brain/manifest_to_json.py >/dev/null 2>&1) && ok "manifest synced" || warn "manifest sync failed (non-blocking)"
fi

# ── Run tests ──────────────────────────────────────────────────────────────────
if [ "$SKIP_TESTS" -eq 0 ]; then
  log "Running test suite (this takes ~30 seconds) ..."

  echo "  [Brain]"
  (cd "$TOKE_ROOT" && $PY automations/brain/brain_tests.py 2>&1 | tail -3) || warn "Brain tests reported issues"

  echo "  [Mnemos]"
  (cd "$TOKE_ROOT/automations/homer/mnemos" && $PY test_mnemos.py 2>&1 | tail -2) || warn "Mnemos tests reported issues"

  echo "  [Homer integration]"
  (cd "$TOKE_ROOT/automations/homer" && $PY homer_integration_test.py 2>&1 | tail -2) || warn "Homer integration reported issues"
fi

# ── Final instructions ────────────────────────────────────────────────────────
cat <<EOF

${GREEN}Toke installed.${RESET}

Next steps:

1. Persist TOKE_ROOT in your shell profile (add one line):

     export TOKE_ROOT="$TOKE_ROOT"

   (bash: ~/.bashrc, zsh: ~/.zshrc, Git Bash on Windows: ~/.bash_profile)

2. (Recommended) Route subagents to Sonnet by default:

     export CLAUDE_CODE_SUBAGENT_MODEL="sonnet"

3. Wire the hooks into Claude Code. Add this to ~/.claude/settings.json
   (5 lifecycle events — matches the plugin install's hooks.json):

     {
       "hooks": {
         "UserPromptSubmit": [
           { "command": "python \$TOKE_ROOT/hooks/godspeed_fuzzy_trigger.py" },
           { "command": "bash \$TOKE_ROOT/hooks/brain_advisor.sh" }
         ],
         "PostToolUse": [
           { "command": "bash \$TOKE_ROOT/hooks/brain_tools_hook.sh" }
         ],
         "PreCompact": [
           { "command": "bash \$TOKE_ROOT/hooks/pre_compact_snapshot.sh" }
         ],
         "SubagentStop": [
           { "command": "bash \$TOKE_ROOT/hooks/subagent_capture.sh" }
         ],
         "SessionEnd": [
           { "command": "bash \$TOKE_ROOT/hooks/session_cost_report.sh" },
           { "command": "bash \$TOKE_ROOT/hooks/toke_session_learn.sh" }
         ]
       }
     }

4. Start a new Claude Code session and try it:

     /brain-score "refactor my distributed cache across 4 files"
     # -> should classify as S4 (Opus)

     godspeed
     # -> activates full pipeline for the rest of the turn

5. (Optional) Nightly sleep agents — see README for schtasks / cron setup.

${CYAN}Verify any time with:${RESET}  bash $REPO_ROOT/install.sh --skip-tests
EOF
