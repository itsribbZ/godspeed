#!/usr/bin/env bash
# ============================================================================
# Toke — SessionEnd cost reporter hook
# ============================================================================
# Fires on session close. Runs per_turn_breakdown.py summary on the current
# session transcript and logs the one-line cost summary to a persistent file.
#
# Contract:
#   - MUST NOT block session exit. All errors swallowed.
#   - Exit code always 0.
#   - Appends one line to session_costs.log per session.
# ============================================================================

set +e

# Portability: prefer CLAUDE_PLUGIN_ROOT (bundled engine), then TOKE_ROOT env
# override (install.sh users), finally $HOME/.toke fallback.
TOKE_ROOT="${TOKE_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.toke}}"
BREAKDOWN="$TOKE_ROOT/tokens/per_turn_breakdown.py"
COST_LOG="$HOME/.claude/telemetry/brain/session_costs.log"

# Cross-OS Python detection: python3 on Linux/Mac, python on Windows git-bash.
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

# Bail if tools aren't available
[ -f "$BREAKDOWN" ] || exit 0
[ -n "$PY" ] || exit 0

# Read session_id from hook stdin
STDIN_DATA=$(cat)
SESSION_ID=$(echo "$STDIN_DATA" | "$PY" -c "import sys,json; print(json.loads(sys.stdin.read()).get('session_id','?'))" 2>/dev/null)

# Run summary and extract the cost line
COST=$("$PY" "$BREAKDOWN" --current --json 2>/dev/null | "$PY" -c "
import sys, json
turns = json.loads(sys.stdin.read())
if turns:
    total = sum(t['cost'] for t in turns)
    n = len(turns)
    print(f'{n} turns | \${total:.2f}')
else:
    print('0 turns | \$0.00')
" 2>/dev/null)

# Append to log
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "${TS} | session=${SESSION_ID:-?} | ${COST:-unknown}" >> "$COST_LOG" 2>/dev/null

exit 0
