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

# Portability (2026-04-17): TOKE_ROOT env override; fallback to the user's layout.
TOKE_ROOT="${TOKE_ROOT:-$HOME/Desktop/T1/Toke}"
BREAKDOWN="$TOKE_ROOT/tokens/per_turn_breakdown.py"
COST_LOG="$HOME/.claude/telemetry/brain/session_costs.log"

# Bail if tools aren't available
[ -f "$BREAKDOWN" ] || exit 0
command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 || exit 0

# Get the Python executable
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

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
