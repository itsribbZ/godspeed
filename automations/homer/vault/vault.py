#!/usr/bin/env python3
"""
Homer L0 — VAULT
================
Sandboxed state + checkpointing for the Toke Pantheon.

Every Homer run writes a checkpoint to vault/state/. Checkpoints are
compaction-resilient: mid-session state loss is recovered by reading the
latest checkpoint for the active session. Follows the §14 Session State
Persistence pattern from ~/.claude/shared/_shared_protocols.md.

Design principles (all received from Brain v2.3 discipline):
- Stdlib only (no external deps)
- JSON one-file-per-checkpoint (human-readable, grepable)
- 4-char hex collision suffix (prevents races on concurrent runs)
- Auto-archive stale checkpoints (>24h) to vault/state/archive/
- Windows UTF-8 safe (cp1252 hardening on stdout/stderr)
- Immutable once phase="done" (Sacred Rule 5 — diagnostics are features)
- No deletions without explicit caller intent (Sacred Rule 2)

Checkpoint schema v1.0:
    {
      "schema_version": "1.0",
      "checkpoint_id": "homer_<topic>_<YYYYMMDD_HHMMSS>_<4hex>",
      "session_id": "...",
      "skill": "homer",
      "topic": "...",
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "phase": "init|plan|dispatch|synthesize|eval|memory|done",
      "tasks": [{"id": N, "priority": "...", "status": "...", "description": "..."}],
      "agents": [{"id": "...", "role": "...", "status": "...", "roi": N}],
      "escalations": [{"level": N, "trigger": "...", "resolution": "..."}],
      "memory_refs": [{"tier": "core|recall|archival", "key": "...", "citation": "..."}],
      "notes": "free-form continuation notes"
    }
"""

from __future__ import annotations

import datetime
import json
import secrets
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Windows UTF-8 hardening (learned from Brain v2.3 cp1252 quirk)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

SCHEMA_VERSION = "1.0"
STATE_DIR = Path(__file__).parent / "state"
ARCHIVE_DIR = STATE_DIR / "archive"
STALE_THRESHOLD_SECONDS = 24 * 3600  # 24 hours

VALID_PHASES = ("init", "plan", "dispatch", "synthesize", "eval", "memory", "done")


@dataclass
class Checkpoint:
    """A single Homer checkpoint — one JSON file in vault/state/."""

    checkpoint_id: str
    session_id: str
    topic: str
    created_at: str
    phase: str
    skill: str = "homer"
    schema_version: str = SCHEMA_VERSION
    updated_at: str = ""
    tasks: list[dict] = field(default_factory=list)
    agents: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)
    memory_refs: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    outcome: str = ""  # "pass" | "soft_fail" | "hard_fail" | ""
    effort_override: int | None = None  # v1.1: manual effort level (1-5) if set, None = Brain auto
    notes: str = ""

    def __post_init__(self):
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        # Drop unknown keys gracefully (forward-compat for future schema growth)
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def validate(self) -> tuple[bool, str]:
        """Returns (ok, error_msg)."""
        if self.schema_version != SCHEMA_VERSION:
            return False, f"schema_version mismatch: {self.schema_version} != {SCHEMA_VERSION}"
        if self.phase not in VALID_PHASES:
            return False, f"invalid phase: {self.phase} (valid: {VALID_PHASES})"
        if not self.checkpoint_id:
            return False, "empty checkpoint_id"
        if not self.session_id:
            return False, "empty session_id"
        if not self.topic:
            return False, "empty topic"
        return True, ""


class VaultStore:
    """Filesystem-backed store for Homer checkpoints."""

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir if state_dir is not None else STATE_DIR
        self.archive_dir = self.state_dir / "archive"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _make_id(topic: str) -> str:
        """checkpoint_id = homer_<safe_topic>_<YYYYMMDD_HHMMSS>_<4hex>"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in topic).strip("_")[:40]
        if not safe_topic:
            safe_topic = "untitled"
        suffix = secrets.token_hex(2)  # 4 hex chars
        return f"homer_{safe_topic}_{ts}_{suffix}"

    def create(
        self,
        topic: str,
        session_id: str,
        phase: str = "init",
        tasks: list[dict] | None = None,
        notes: str = "",
    ) -> Checkpoint:
        """Create a new checkpoint and write it to disk."""
        cp = Checkpoint(
            checkpoint_id=self._make_id(topic),
            session_id=session_id,
            topic=topic,
            created_at=datetime.datetime.now().isoformat(),
            phase=phase,
            tasks=tasks or [],
            notes=notes,
        )
        ok, err = cp.validate()
        if not ok:
            raise ValueError(f"checkpoint validation failed: {err}")
        self._write(cp)
        return cp

    def _write(self, cp: Checkpoint) -> Path:
        cp.updated_at = datetime.datetime.now().isoformat()
        path = self.state_dir / f"{cp.checkpoint_id}.json"
        path.write_text(json.dumps(cp.to_dict(), indent=2), encoding="utf-8")
        return path

    def update(self, cp: Checkpoint) -> Path:
        """Re-write an existing checkpoint (phase transition, task status, etc.)."""
        ok, err = cp.validate()
        if not ok:
            raise ValueError(f"checkpoint validation failed: {err}")
        return self._write(cp)

    def read(self, checkpoint_id: str) -> Checkpoint | None:
        """Read by id. Checks state/ first, then archive/."""
        path = self.state_dir / f"{checkpoint_id}.json"
        if not path.exists():
            path = self.archive_dir / f"{checkpoint_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return Checkpoint.from_dict(data)

    def list_all(self, include_archive: bool = False) -> list[Checkpoint]:
        """Return all checkpoints, newest first. Corrupt files are silently skipped."""
        paths = list(self.state_dir.glob("homer_*.json"))
        if include_archive:
            paths += list(self.archive_dir.glob("homer_*.json"))
        checkpoints: list[Checkpoint] = []
        for p in paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cp = Checkpoint.from_dict(data)
                checkpoints.append(cp)
            except (json.JSONDecodeError, TypeError, KeyError):
                continue  # skip corrupt — don't fail the listing
        checkpoints.sort(key=lambda c: c.created_at, reverse=True)
        return checkpoints

    def latest(self) -> Checkpoint | None:
        all_cps = self.list_all(include_archive=False)
        return all_cps[0] if all_cps else None

    def archive_stale(self, threshold_seconds: int = STALE_THRESHOLD_SECONDS) -> int:
        """Move checkpoints older than threshold to archive/. Returns count moved."""
        now = datetime.datetime.now()
        moved = 0
        for path in list(self.state_dir.glob("homer_*.json")):
            if path.parent != self.state_dir:
                continue  # skip archive/ subdir matches
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.datetime.fromisoformat(data["created_at"])
                age = (now - created).total_seconds()
                if age > threshold_seconds:
                    dst = self.archive_dir / path.name
                    path.rename(dst)
                    moved += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return moved

    def health_report(self) -> dict:
        """Diagnostic snapshot: counts, phases, latest, stale candidates."""
        live = self.list_all(include_archive=False)
        archived = list(self.archive_dir.glob("homer_*.json"))
        phase_counts: dict[str, int] = {}
        for cp in live:
            phase_counts[cp.phase] = phase_counts.get(cp.phase, 0) + 1
        now = datetime.datetime.now()
        stale_candidates = 0
        for cp in live:
            try:
                age = (now - datetime.datetime.fromisoformat(cp.created_at)).total_seconds()
                if age > STALE_THRESHOLD_SECONDS:
                    stale_candidates += 1
            except ValueError:
                pass
        return {
            "live_count": len(live),
            "archived_count": len(archived),
            "phase_counts": phase_counts,
            "stale_candidates": stale_candidates,
            "latest_id": live[0].checkpoint_id if live else None,
            "latest_phase": live[0].phase if live else None,
            "latest_topic": live[0].topic if live else None,
            "latest_created": live[0].created_at if live else None,
            "schema_version": SCHEMA_VERSION,
            "state_dir": str(self.state_dir),
        }


# =============================================================================
# Minimal CLI for direct vault.py testing. Use homer_cli.py for the workbench.
# =============================================================================

def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    store = VaultStore()
    cmd = argv[1]

    if cmd == "create":
        topic = argv[2] if len(argv) > 2 else "smoke_test"
        session = argv[3] if len(argv) > 3 else f"cli_{secrets.token_hex(4)}"
        cp = store.create(topic=topic, session_id=session)
        print(f"Created: {cp.checkpoint_id}")
        return 0

    if cmd == "list":
        for cp in store.list_all():
            print(f"  {cp.checkpoint_id}  phase={cp.phase}  {cp.created_at}")
        return 0

    if cmd == "latest":
        cp = store.latest()
        if cp is None:
            print("No checkpoints in vault.")
            return 0
        print(json.dumps(cp.to_dict(), indent=2))
        return 0

    if cmd == "archive":
        n = store.archive_stale()
        print(f"Archived {n} stale checkpoints.")
        return 0

    if cmd == "health":
        print(json.dumps(store.health_report(), indent=2))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
