#!/usr/bin/env bash
# ============================================================================
# Toke Brain — UserPromptSubmit hook (advisor)
# ============================================================================
# v2.1: Node.js fast-path (~65ms) with Python fallback (~330ms).
# Node handles classify + log + banner in one process (no second spawn).
#
# Contract:
#   - MUST NOT block the Claude turn. All errors swallowed.
#   - Exit code always 0.
#   - stderr output (brain advisories) is visible to user.
# ============================================================================

set +e

# Portability: prefer CLAUDE_PLUGIN_ROOT (bundled engine), then TOKE_ROOT env
# override (install.sh users), finally $HOME/.toke fallback.
TOKE_ROOT="${TOKE_ROOT:-${CLAUDE_PLUGIN_ROOT:-$HOME/.toke}}"
HOOK_JS="$TOKE_ROOT/hooks/brain_hook_fast.js"
BRAIN_CLI="$TOKE_ROOT/automations/brain/brain_cli.py"
STDIN_DATA=$(cat)

# Cross-OS Python detection: python3 on Linux/Mac, python on Windows git-bash.
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

# Fast path: Node.js (exit 2 = manifest stale, fall back to Python)
if [ -f "$HOOK_JS" ] && command -v node >/dev/null 2>&1; then
    printf '%s' "$STDIN_DATA" | node "$HOOK_JS" hook
    RC=$?
    [ $RC -ne 2 ] && exit 0
    # RC=2: manifest stale, rebuild and retry
    [ -n "$PY" ] && "$PY" "$TOKE_ROOT/automations/brain/manifest_to_json.py" >/dev/null 2>&1
    printf '%s' "$STDIN_DATA" | node "$HOOK_JS" hook 2>/dev/null
    exit 0
fi

# Slow path: Python fallback (if Node unavailable)
[ -f "$BRAIN_CLI" ] || exit 0
[ -n "$PY" ] || exit 0
printf '%s' "$STDIN_DATA" | "$PY" "$BRAIN_CLI" hook

# Banner (Python slow path only — Node handles banner internally)
DECISIONS="$HOME/.claude/telemetry/brain/decisions.jsonl"
if [ -f "$DECISIONS" ] && [ -n "$PY" ]; then
    BANNER=$(tail -1 "$DECISIONS" 2>/dev/null | "$PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    r = d.get('result', {})
    tier = r.get('tier', '?')
    model = r.get('model', '?')
    if tier in ('S0', 'S1'):
        print(f'[BRAIN {tier}/{model}] this prompt does not need Opus. /effort low saves tokens.')
    elif tier == 'S5':
        print(f'[BRAIN {tier}/{model}] full complexity -- /effort max correct.')
    else:
        print(f'[BRAIN {tier}/{model}] moderate -- /effort medium is sufficient.')
except: pass
" 2>/dev/null)
    [ -n "$BANNER" ] && echo "$BANNER" >&2
fi

exit 0
