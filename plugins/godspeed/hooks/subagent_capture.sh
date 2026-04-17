#!/usr/bin/env bash
# SubagentStop Hook — delegates to Python for clean JSON handling
# Cross-OS Python detection: python3 on Linux/Mac, python on Windows git-bash.
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
[ -n "$PY" ] || exit 0

exec "$PY" -u -c "
import json, sys, pathlib, datetime

TELEMETRY = pathlib.Path.home() / '.claude' / 'telemetry' / 'brain'
LOG = TELEMETRY / 'subagent_completions.jsonl'

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

entry = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
    'event': 'SubagentStop',
    'session_id': data.get('session_id', ''),
    'agent_id': data.get('agent_id', ''),
    'agent_type': data.get('agent_type', ''),
    'transcript_path': data.get('agent_transcript_path', ''),
    'last_message_preview': (data.get('last_assistant_message', '') or '')[:200],
}
TELEMETRY.mkdir(parents=True, exist_ok=True)
with open(LOG, 'a', encoding='utf-8') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null
