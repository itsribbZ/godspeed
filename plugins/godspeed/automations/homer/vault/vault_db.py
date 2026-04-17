#!/usr/bin/env python3
"""
Homer L0 — VAULT v2 SQLite Backend
====================================
SQLite-backed durable execution store for Homer workflows.

4 tables: workflows, steps, signals, timers.
5 primitives: @checkpoint, retry, durable_sleep, send_signal/recv_signal, replay.

Stdlib only (sqlite3). Zero external dependencies.
Backward-compatible with v1 JSON checkpoints (read fallback).

Origin: VAULT_V2_BLUEPRINT.md (2026-04-11). Built 2026-04-12.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DB_PATH = Path(__file__).parent / "vault.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    topic TEXT,
    phase TEXT DEFAULT 'init',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    step_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result_json TEXT,
    error TEXT,
    attempt INT DEFAULT 0,
    max_retries INT DEFAULT 2,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(workflow_id, step_name)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    consumed BOOLEAN DEFAULT FALSE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    callback TEXT,
    fired BOOLEAN DEFAULT FALSE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_workflow ON steps(workflow_id);
CREATE INDEX IF NOT EXISTS idx_signals_workflow_topic ON signals(workflow_id, topic);
CREATE INDEX IF NOT EXISTS idx_timers_workflow ON timers(workflow_id);
"""


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------


class VaultDB:
    """SQLite-backed VAULT store. Thread-safe via WAL mode."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self):
        """Transaction context manager."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self):
        with self._tx() as conn:
            conn.executescript(SCHEMA_SQL)

    # ---------------------------------------------------------------------------
    # Workflows
    # ---------------------------------------------------------------------------

    def create_workflow(
        self,
        workflow_id: str,
        session_id: str,
        topic: str = "",
        phase: str = "init",
        metadata: dict | None = None,
    ) -> dict:
        now = _now_iso()
        meta_json = json.dumps(metadata or {})
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO workflows (id, session_id, topic, phase, created_at, updated_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workflow_id, session_id, topic, phase, now, now, meta_json),
            )
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        return dict(row) if row else None

    def update_workflow(self, workflow_id: str, **kwargs) -> dict | None:
        allowed = {"phase", "topic", "metadata_json"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_workflow(workflow_id)
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [workflow_id]
        with self._tx() as conn:
            conn.execute(f"UPDATE workflows SET {set_clause} WHERE id = ?", values)
        return self.get_workflow(workflow_id)

    def list_workflows(
        self,
        session_id: str | None = None,
        phase: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses, params = [], []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if phase:
            clauses.append("phase = ?")
            params.append(phase)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM workflows {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------------------
    # Steps
    # ---------------------------------------------------------------------------

    def create_step(
        self,
        workflow_id: str,
        step_name: str,
        max_retries: int = 2,
    ) -> dict:
        now = _now_iso()
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO steps (workflow_id, step_name, status, attempt, max_retries, created_at) "
                "VALUES (?, ?, 'pending', 0, ?, ?)",
                (workflow_id, step_name, max_retries, now),
            )
        return self.get_step(workflow_id, step_name)

    def get_step(self, workflow_id: str, step_name: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE workflow_id = ? AND step_name = ?",
                (workflow_id, step_name),
            ).fetchone()
        return dict(row) if row else None

    def complete_step(
        self,
        workflow_id: str,
        step_name: str,
        result: Any,
        attempt: int = 0,
    ) -> dict:
        now = _now_iso()
        result_json = json.dumps(result, default=str)
        with self._tx() as conn:
            conn.execute(
                "UPDATE steps SET status = 'done', result_json = ?, attempt = ?, completed_at = ? "
                "WHERE workflow_id = ? AND step_name = ?",
                (result_json, attempt, now, workflow_id, step_name),
            )
        return self.get_step(workflow_id, step_name)

    def fail_step(
        self,
        workflow_id: str,
        step_name: str,
        error: str,
        attempt: int = 0,
    ) -> dict:
        with self._tx() as conn:
            conn.execute(
                "UPDATE steps SET status = 'failed', error = ?, attempt = ? "
                "WHERE workflow_id = ? AND step_name = ?",
                (error, attempt, workflow_id, step_name),
            )
        return self.get_step(workflow_id, step_name)

    def mark_step_running(self, workflow_id: str, step_name: str) -> dict:
        with self._tx() as conn:
            conn.execute(
                "UPDATE steps SET status = 'running' WHERE workflow_id = ? AND step_name = ?",
                (workflow_id, step_name),
            )
        return self.get_step(workflow_id, step_name)

    def get_workflow_steps(self, workflow_id: str) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE workflow_id = ? ORDER BY id",
                (workflow_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_completed_steps(self, workflow_id: str) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE workflow_id = ? AND status = 'done' ORDER BY id",
                (workflow_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_next_pending_step(self, workflow_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE workflow_id = ? AND status IN ('pending', 'failed') ORDER BY id LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------------------------
    # Signals
    # ---------------------------------------------------------------------------

    def send_signal(self, workflow_id: str, topic: str, payload: dict | None = None) -> int:
        now = _now_iso()
        payload_json = json.dumps(payload or {})
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT INTO signals (workflow_id, topic, payload_json, consumed, created_at) "
                "VALUES (?, ?, ?, FALSE, ?)",
                (workflow_id, topic, payload_json, now),
            )
            return cursor.lastrowid

    def recv_signal(self, workflow_id: str, topic: str) -> dict | None:
        """Consume the oldest unconsumed signal on topic. Returns payload dict or None."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM signals WHERE workflow_id = ? AND topic = ? AND consumed = FALSE "
                "ORDER BY id LIMIT 1",
                (workflow_id, topic),
            ).fetchone()
            if row:
                conn.execute("UPDATE signals SET consumed = TRUE WHERE id = ?", (row["id"],))
                return json.loads(row["payload_json"])
        return None

    def list_signals(self, workflow_id: str, include_consumed: bool = False) -> list[dict]:
        if include_consumed:
            where = "WHERE workflow_id = ?"
            params = (workflow_id,)
        else:
            where = "WHERE workflow_id = ? AND consumed = FALSE"
            params = (workflow_id,)
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM signals {where} ORDER BY id", params
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------------------
    # Timers
    # ---------------------------------------------------------------------------

    def create_timer(
        self,
        workflow_id: str,
        wake_at: datetime | str,
        callback: str = "",
    ) -> int:
        now = _now_iso()
        wake_str = wake_at.isoformat() if isinstance(wake_at, datetime) else wake_at
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT INTO timers (workflow_id, wake_at, callback, fired, created_at) "
                "VALUES (?, ?, ?, FALSE, ?)",
                (workflow_id, wake_str, callback, now),
            )
            return cursor.lastrowid

    def get_expired_timers(self, workflow_id: str) -> list[dict]:
        """Return unfired timers whose wake_at has passed."""
        now = _now_iso()
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM timers WHERE workflow_id = ? AND fired = FALSE AND wake_at <= ? ORDER BY wake_at",
                (workflow_id, now),
            ).fetchall()
        return [dict(r) for r in rows]

    def fire_timer(self, timer_id: int) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE timers SET fired = TRUE WHERE id = ?", (timer_id,))

    def check_and_fire_timers(self, workflow_id: str) -> list[str]:
        """Check for expired timers, mark them fired, return callback step_names."""
        expired = self.get_expired_timers(workflow_id)
        callbacks = []
        for t in expired:
            self.fire_timer(t["id"])
            if t.get("callback"):
                callbacks.append(t["callback"])
        return callbacks

    # ---------------------------------------------------------------------------
    # Health / stats
    # ---------------------------------------------------------------------------

    def health_report(self) -> dict:
        with self._tx() as conn:
            wf_count = conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
            step_count = conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
            signal_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            timer_count = conn.execute("SELECT COUNT(*) FROM timers").fetchone()[0]

            phase_rows = conn.execute(
                "SELECT phase, COUNT(*) as c FROM workflows GROUP BY phase"
            ).fetchall()
            phase_counts = {r["phase"]: r["c"] for r in phase_rows}

            latest = conn.execute(
                "SELECT * FROM workflows ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            done_steps = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE status = 'done'"
            ).fetchone()[0]
            failed_steps = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE status = 'failed'"
            ).fetchone()[0]

        return {
            "workflows": wf_count,
            "steps": step_count,
            "steps_done": done_steps,
            "steps_failed": failed_steps,
            "signals": signal_count,
            "timers": timer_count,
            "phase_counts": phase_counts,
            "latest_id": dict(latest)["id"] if latest else None,
            "latest_phase": dict(latest)["phase"] if latest else None,
            "latest_topic": dict(latest)["topic"] if latest else None,
            "db_path": self.db_path,
            "schema": "v2.0",
        }


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def checkpoint(step_name: str, max_retries: int = 2, backoff_base: float = 1.0):
    """Decorator: cache step results in VAULT. Skip on replay. Retry with backoff.

    Usage:
        db = VaultDB()

        @checkpoint("extract_data")
        def extract_data(workflow_id, db, ...):
            ...
            return {"rows": 42}

    The decorated function MUST accept `workflow_id` and `db` as first two args.
    If the step is already 'done', returns the cached result without re-running.
    On failure, retries up to max_retries times with exponential backoff.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(workflow_id: str, db: VaultDB, *args, **kwargs):
            # Ensure step row exists
            db.create_step(workflow_id, step_name, max_retries)
            existing = db.get_step(workflow_id, step_name)

            # Skip if already done (replay-safe)
            if existing and existing["status"] == "done" and existing["result_json"]:
                return json.loads(existing["result_json"])

            # Run with retry
            for attempt in range(max_retries + 1):
                try:
                    db.mark_step_running(workflow_id, step_name)
                    result = func(workflow_id, db, *args, **kwargs)
                    db.complete_step(workflow_id, step_name, result, attempt)
                    return result
                except Exception as e:
                    db.fail_step(workflow_id, step_name, str(e), attempt)
                    if attempt < max_retries:
                        delay = backoff_base * (2 ** attempt)
                        time.sleep(delay)
                    else:
                        raise
            return None  # unreachable
        return wrapper
    return decorator


def durable_sleep(db: VaultDB, workflow_id: str, step_name: str, seconds: float) -> int:
    """Schedule a wake-up. Returns timer_id. Check with check_timers later."""
    wake_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return db.create_timer(workflow_id, wake_at, callback=step_name)


def replay(db: VaultDB, workflow_id: str) -> dict:
    """Get the state needed to resume an incomplete workflow.

    Returns:
        {
            "workflow": {...},
            "completed_steps": [...],
            "next_step": {...} or None,
            "all_steps": [...],
            "resumable": True/False,
        }
    """
    workflow = db.get_workflow(workflow_id)
    if not workflow:
        return {"workflow": None, "resumable": False, "error": "workflow not found"}

    all_steps = db.get_workflow_steps(workflow_id)
    completed = [s for s in all_steps if s["status"] == "done"]
    next_step = db.get_next_pending_step(workflow_id)

    return {
        "workflow": workflow,
        "completed_steps": completed,
        "next_step": next_step,
        "all_steps": all_steps,
        "resumable": next_step is not None and workflow.get("phase") != "done",
    }


# ---------------------------------------------------------------------------
# JSON v1 migration
# ---------------------------------------------------------------------------


def migrate_json_to_sqlite(
    json_dir: Path,
    db: VaultDB | None = None,
    archive_dir: Path | None = None,
) -> int:
    """Migrate v1 JSON checkpoints to SQLite. Returns count migrated.

    - Reads all homer_*.json from json_dir
    - Creates a workflow per checkpoint (maps v1 fields → v2 schema)
    - Skips duplicates (if workflow_id already in SQLite)
    - Does NOT delete JSON files (they become read-only fallback)
    """
    if db is None:
        db = VaultDB()

    migrated = 0
    json_files = list(json_dir.glob("homer_*.json"))
    if archive_dir:
        json_files += list(archive_dir.glob("homer_*.json"))

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        wf_id = data.get("checkpoint_id", "")
        if not wf_id:
            continue

        # Skip if already migrated
        if db.get_workflow(wf_id):
            continue

        # Map v1 → v2
        metadata = {
            "schema_version": data.get("schema_version", "1.0"),
            "skill": data.get("skill", "homer"),
            "tasks": data.get("tasks", []),
            "agents": data.get("agents", []),
            "escalations": data.get("escalations", []),
            "memory_refs": data.get("memory_refs", []),
            "tokens_in": data.get("tokens_in", 0),
            "tokens_out": data.get("tokens_out", 0),
            "cost_usd": data.get("cost_usd", 0.0),
            "outcome": data.get("outcome", ""),
            "effort_override": data.get("effort_override"),
            "notes": data.get("notes", ""),
            "source": "json_migration",
        }

        db.create_workflow(
            workflow_id=wf_id,
            session_id=data.get("session_id", "unknown"),
            topic=data.get("topic", ""),
            phase=data.get("phase", "done"),
            metadata=metadata,
        )
        migrated += 1

    return migrated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
