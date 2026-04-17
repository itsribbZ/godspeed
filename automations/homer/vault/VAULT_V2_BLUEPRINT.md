# VAULT v2 Blueprint — SQLite Durable Execution Upgrade

**Status:** Ready to implement. ~9 hours. Single-session work.
**Source:** 2026-04-11 agent tooling research (Temporal/DBOS/Restate comparison).
**Decision:** DIY upgrade in-place (not Temporal, not DBOS — wrong scale for solo dev).

## Architecture

VAULT v1 (current): one JSON file per checkpoint, no replay, no retry, no signals.
VAULT v2 (target): SQLite-backed with 4 new primitives, backward-compatible JSON fallback.

```
vault.db (SQLite)
├── workflows      — top-level orchestration runs
├── steps          — per-step checkpoints within a workflow
├── signals        — inter-workflow communication
└── timers         — durable sleep/wake scheduling
```

## Schema (~20 lines SQL)

```sql
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    topic TEXT,
    phase TEXT DEFAULT 'init',  -- init/plan/dispatch/synthesize/eval/done/failed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    step_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending/running/done/failed/skipped
    result_json TEXT,
    error TEXT,
    attempt INT DEFAULT 0,
    max_retries INT DEFAULT 2,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(workflow_id, step_name)
);

CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    callback TEXT,  -- step_name to resume
    fired BOOLEAN DEFAULT FALSE,
    created_at TEXT NOT NULL
);
```

## New Primitives (~200 lines Python)

### 1. @checkpoint decorator (~40 lines)
```python
def checkpoint(step_name: str, max_retries: int = 2):
    """If step already completed, return cached result. Otherwise run and cache."""
    def decorator(func):
        def wrapper(workflow_id, *args, **kwargs):
            existing = db.get_step(workflow_id, step_name)
            if existing and existing['status'] == 'done':
                return json.loads(existing['result_json'])
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    db.complete_step(workflow_id, step_name, result, attempt)
                    return result
                except Exception as e:
                    db.fail_step(workflow_id, step_name, str(e), attempt)
                    if attempt == max_retries:
                        raise
            return None
        return wrapper
    return decorator
```

### 2. Retry with backoff (~30 lines)
Built into the @checkpoint decorator. Exponential backoff: `delay * 2^attempt`.

### 3. Durable sleep/timer (~25 lines)
```python
def durable_sleep(workflow_id: str, step_name: str, seconds: float):
    """Store wake_at timestamp. On resume, check if timer expired."""
    wake_at = datetime.now() + timedelta(seconds=seconds)
    db.create_timer(workflow_id, wake_at, step_name)

def check_timers(workflow_id: str) -> list[str]:
    """Return step_names whose timers have expired."""
    return db.get_expired_timers(workflow_id)
```

### 4. Signal/recv primitives (~40 lines)
```python
def send_signal(workflow_id: str, topic: str, payload: dict):
    """Send a signal into a workflow."""
    db.insert_signal(workflow_id, topic, payload)

def recv_signal(workflow_id: str, topic: str, timeout: float = 0) -> dict | None:
    """Block until signal received on topic, or timeout."""
    deadline = time.time() + timeout
    while True:
        sig = db.consume_signal(workflow_id, topic)
        if sig:
            return json.loads(sig['payload_json'])
        if time.time() > deadline:
            return None
        time.sleep(0.5)
```

### 5. Replay from checkpoint (~40 lines)
```python
def replay(workflow_id: str) -> dict:
    """Resume an incomplete workflow from its last successful step."""
    workflow = db.get_workflow(workflow_id)
    completed = db.get_completed_steps(workflow_id)
    next_step = db.get_next_pending_step(workflow_id)
    return {
        'workflow': workflow,
        'completed': completed,
        'resume_from': next_step,
    }
```

## Migration from v1

1. Keep JSON checkpoint files as fallback (read-only)
2. SQLite is the primary store for new workflows
3. `vault.py` gains `migrate_json_to_sqlite()` for one-time migration
4. homer_cli.py `vault` subcommands work against SQLite
5. Zero breaking changes to Zeus SKILL.md

## Effort Breakdown

| Task | Hours |
|------|-------|
| SQLite schema + db.py module | 1 |
| @checkpoint decorator + retry | 2 |
| Timer + signal primitives | 2 |
| Replay from checkpoint | 1 |
| Integration with homer_cli.py | 1 |
| Tests + JSON migration | 2 |
| **Total** | **9** |

## Dependencies
- Python stdlib only (sqlite3 is stdlib)
- No new packages
- Backward-compatible with existing vault.py JSON interface
