#!/usr/bin/env bash
# ============================================================================
# Toke Homer — SessionEnd hook (auto-learn + sleep agents)
# ============================================================================
# Fires on every session close. Two jobs:
#   1. Write a minimal Mnemos Recall breadcrumb (session ID + timestamp)
#   2. Fire sleep agents in BACKGROUND (Nyx + Hesper + Aurora)
#
# Contract:
#   - MUST NOT block session teardown. All errors swallowed.
#   - Exit code always 0.
#   - Sleep agents run backgrounded — Claude doesn't wait for them.
#   - Mnemos write is fast (one SQLite INSERT, <50ms).
# ============================================================================

set +e

# Portability (2026-04-17): TOKE_ROOT env override; fallback to the user's layout.
TOKE_ROOT="${TOKE_ROOT:-$HOME/Desktop/T1/Toke}"
HOMER="$TOKE_ROOT/automations/homer"
MNEMOS="$HOMER/mnemos/mnemos.py"
SLEEP_CLI="$HOMER/sleep/sleep_cli.py"

# Bail silently if Homer isn't installed
[ -f "$MNEMOS" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Read session_id from hook stdin
STDIN_DATA=$(cat)
SESSION_ID=$(echo "$STDIN_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="unknown"

# 1. Mnemos Recall breadcrumb (fast — one SQLite INSERT)
python3 -c "
import sys
sys.path.insert(0, '$HOMER/mnemos')
from mnemos import MnemosStore
store = MnemosStore()
store.write_recall(
    topic='session-end:$SESSION_ID',
    content='Session closed. Auto-logged by toke_session_learn.sh hook.',
    citations=['session:$SESSION_ID'],
)
" 2>/dev/null

# 2. Sleep agents in BACKGROUND (Nyx theater + Hesper distill + Aurora tune)
#    Backgrounded so session teardown is not blocked.
#    Output goes to sleep agent report files, not stdout/stderr.
if [ -f "$SLEEP_CLI" ]; then
    python3 "$SLEEP_CLI" run all >/dev/null 2>&1 &
fi

exit 0
