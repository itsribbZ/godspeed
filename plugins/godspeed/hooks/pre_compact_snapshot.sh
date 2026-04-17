#!/usr/bin/env bash
# PreCompact Hook — delegates to Python for clean JSON handling
# Cross-OS Python detection: python3 on Linux/Mac, python on Windows git-bash.
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
[ -n "$PY" ] || exit 0

exec "$PY" -u -c "
import json, sys, pathlib, datetime

TELEMETRY = pathlib.Path.home() / '.claude' / 'telemetry' / 'brain'
SNAPSHOT = TELEMETRY / 'pre_compact_snapshots.jsonl'
DECISIONS = TELEMETRY / 'decisions.jsonl'

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

sid = data.get('session_id', 'unknown')
total, session = 0, 0
if DECISIONS.exists():
    lines = DECISIONS.read_text(encoding='utf-8').splitlines()
    total = len(lines)
    session = sum(1 for l in lines if sid in l)

entry = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
    'event': 'PreCompact',
    'session_id': sid,
    'session_decisions': session,
    'total_decisions': total,
}
TELEMETRY.mkdir(parents=True, exist_ok=True)
with open(SNAPSHOT, 'a', encoding='utf-8') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null
