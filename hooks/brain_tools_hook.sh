#!/usr/bin/env bash
# ============================================================================
# Toke Brain — PostToolUse hook (tool telemetry)
# ============================================================================
# v2.1: Node.js fast-path (~65ms) with Python fallback (~330ms).
#
# Contract:
#   - MUST NOT block the Claude turn. All errors swallowed.
#   - Exit code always 0.
#   - No stdout (PostToolUse stdout can interfere with tool results).
# ============================================================================

set +e

# Portability (2026-04-17): TOKE_ROOT env override; fallback to the user's layout.
TOKE_ROOT="${TOKE_ROOT:-$HOME/Desktop/T1/Toke}"
HOOK_JS="$TOKE_ROOT/hooks/brain_hook_fast.js"
BRAIN_CLI="$TOKE_ROOT/automations/brain/brain_cli.py"
STDIN_DATA=$(cat)

# Fast path: Node.js
if [ -f "$HOOK_JS" ] && command -v node >/dev/null 2>&1; then
    printf '%s' "$STDIN_DATA" | node "$HOOK_JS" telemetry >/dev/null 2>&1
    exit 0
fi

# Slow path: Python fallback
[ -f "$BRAIN_CLI" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
printf '%s' "$STDIN_DATA" | python3 "$BRAIN_CLI" telemetry >/dev/null 2>&1

exit 0
