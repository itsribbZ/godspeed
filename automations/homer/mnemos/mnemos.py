#!/usr/bin/env python3
"""
Homer L5 — MNEMOS
=================
Three-tier memory store for the Toke Pantheon. Follows the Letta MemGPT
pattern (Core / Recall / Archival) with one hard addition: every write and
edit must include a citation in an accepted format. Zero dark edits. Every
Core compaction keeps a back-pointer to the archived original.

Tiers:
- Core:     context-resident ~5K tokens, most-critical patterns, auto-injected
- Recall:   SQLite FTS5-searchable conversation history
- Archival: cold markdown storage, one file per entry, back-pointer protocol

Citation formats accepted:
    path:line                e.g. "Toke/research/brain_synthesis.md:42"
    path:line-line           e.g. "brain_cli.py:100-150"
    https://...              e.g. "https://anthropic.com/docs"
    arxiv:YYYY.NNNN          e.g. "arxiv:2603.18897"
    mnemos:archival_<id>     internal back-pointer format
    decisions:<session_id>   Brain decision reference
    session:<session_id>     session-local reference

Rejected citations: empty string, "around line 50", "see somewhere", or
anything without a structured source locator.

Design principles (match Brain v2.3 + VAULT):
- Stdlib only (sqlite3, json, pathlib, datetime, re, secrets)
- Human-readable formats (markdown for Core/Archival, SQLite for Recall)
- Windows UTF-8 safe
- Sacred Rule #2 (no dark deletes — compaction writes to Archival)
- Sacred Rule #5 (diagnostics are features — compaction is a normal operation)
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Windows UTF-8 hardening (learned from Brain v2.3 cp1252 quirk)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

MNEMOS_ROOT = Path(__file__).parent
CORE_DIR = MNEMOS_ROOT / "core"
RECALL_DIR = MNEMOS_ROOT / "recall"
ARCHIVAL_DIR = MNEMOS_ROOT / "archival"

CORE_FILE = CORE_DIR / "core_memory.md"
RECALL_DB = RECALL_DIR / "recall.db"

# ── Citation validation ──────────────────────────────────────────────────────

CITATION_PATTERNS = [
    re.compile(r"^[^\s:]+:\d+(-\d+)?$"),           # file:line or file:line-line
    re.compile(r"^https?://\S+$"),                 # URL
    re.compile(r"^arxiv:\d{4}\.\d{4,5}$"),         # arxiv id
    re.compile(r"^mnemos:archival_[\w\-]+$"),      # internal back-pointer
    re.compile(r"^decisions:[\w\-]+$"),            # Brain decision id
    re.compile(r"^session:[\w\-]+$"),              # session id
]

# Token estimate: 1 token ~= 4 chars for English prose (rough)
CHARS_PER_TOKEN_ESTIMATE = 4
CORE_TOKEN_BUDGET = 5000
CORE_COMPACT_TARGET = 4000  # compact to this when over budget

# ── Embedding infrastructure (optional — graceful fallback) ──────────────────
# Vector embeddings for semantic recall. sentence-transformers + numpy are
# optional — if missing, Mnemos falls back transparently to FTS5/LIKE. Zero
# breaking changes when the libs are unavailable.

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIMS = 384  # all-MiniLM-L6-v2 output dimension
SEMANTIC_MIN_SIMILARITY = 0.30  # cosine floor for a semantic match to count

_embedder = None
_embedder_unavailable = False


def _silence_hf_and_transformers() -> None:
    """Silence sentence-transformers / HuggingFace Hub / transformers output.

    Without this, every Mnemos load prints:
      - HF Hub unauthenticated-request warning
      - tqdm "Loading weights: 100%|██████████|" progress bar
      - BertModel LOAD REPORT (ANSI-bold) + UNEXPECTED row
      - Notes block
    Those ANSI escape sequences leak into terminals that don't render them
    cleanly and clutter the user's UI. Standard fix: env vars + logger levels.
    Applied once per process, before the first SentenceTransformer() call.
    """
    import os
    import logging
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    # Tokenizers parallelism warning is loud and useless in this context.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in ("sentence_transformers", "transformers", "huggingface_hub",
                 "urllib3", "filelock"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _get_embedder():
    """Lazy-load the sentence-transformer model. Returns None if unavailable.

    sentence-transformers prints BertModel LOAD REPORT via direct print() calls
    (not a logger) that include ANSI escape codes. The logger silencing in
    _silence_hf_and_transformers handles loggers; we also redirect stdout/stderr
    during the actual load() call to swallow the direct prints.
    """
    global _embedder, _embedder_unavailable
    if _embedder_unavailable:
        return None
    if _embedder is not None:
        return _embedder
    _silence_hf_and_transformers()
    import contextlib
    import io
    import os
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                _embedder = SentenceTransformer(EMBED_MODEL_NAME)
        return _embedder
    except Exception:
        _embedder_unavailable = True
        return None


def _encode_text(text: str):
    """Encode text to a float32 numpy vector. Returns None if unavailable."""
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        import numpy as np  # type: ignore
        vec = embedder.encode(text, convert_to_numpy=True, show_progress_bar=False)
        return vec.astype(np.float32)
    except Exception:
        return None


def _serialize_vec(vec) -> bytes:
    """Serialize a numpy float32 array to bytes for SQLite BLOB storage."""
    if vec is None:
        return b""
    return vec.tobytes()


def _deserialize_vec(blob):
    """Deserialize a BLOB back to a numpy float32 array. Returns None if empty."""
    if not blob:
        return None
    try:
        import numpy as np  # type: ignore
        return np.frombuffer(blob, dtype=np.float32)
    except Exception:
        return None


def _cosine_similarity(a, b) -> float:
    """Cosine similarity between two numpy vectors. Returns 0.0 on degenerate input."""
    try:
        import numpy as np  # type: ignore
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
    except Exception:
        return 0.0


def _row_to_public_dict(row) -> dict:
    """Convert a sqlite3.Row to a dict, stripping the embedding BLOB so callers don't see raw bytes."""
    d = {k: row[k] for k in row.keys() if k != "embedding"}
    return d


class CitationError(ValueError):
    """Raised when a citation doesn't match any valid format."""


def validate_citation(citation: str) -> bool:
    """Return True if citation matches an accepted format."""
    if not citation or not isinstance(citation, str):
        return False
    stripped = citation.strip()
    if not stripped:
        return False
    for pattern in CITATION_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def require_citation(citation: str, field_name: str = "citation") -> None:
    """Raise CitationError if invalid. Use at every write entry point."""
    if not validate_citation(citation):
        raise CitationError(
            f"{field_name} rejected: '{citation}' — must be one of "
            f"file:line | https://... | arxiv:YYYY.NNNN | mnemos:... | decisions:... | session:..."
        )


# ── Core tier ────────────────────────────────────────────────────────────────

@dataclass
class CoreEntry:
    """A single entry in Core memory."""

    key: str
    pattern: str
    citation: str
    confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    confirmed_count: int = 1
    created_at: str = ""
    last_used_at: str = ""
    archived_to: str = ""  # back-pointer if trimmed

    def __post_init__(self):
        now = datetime.datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.last_used_at:
            self.last_used_at = now

    def estimated_tokens(self) -> int:
        total = len(self.pattern) + len(self.citation) + len(self.key)
        return max(1, total // CHARS_PER_TOKEN_ESTIMATE)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CoreEntry":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class CoreMemory:
    """Core tier — markdown/JSON-lines file with ~5K token budget."""

    def __init__(self, core_file: Path | None = None, budget_tokens: int = CORE_TOKEN_BUDGET):
        self.core_file = core_file if core_file is not None else CORE_FILE
        self.budget_tokens = budget_tokens
        self.compact_target = min(int(budget_tokens * 0.8), budget_tokens - 1)
        self.core_file.parent.mkdir(parents=True, exist_ok=True)

    def _read_entries(self) -> list[CoreEntry]:
        if not self.core_file.exists():
            return []
        raw = self.core_file.read_text(encoding="utf-8")
        entries: list[CoreEntry] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                data = json.loads(s)
                entries.append(CoreEntry.from_dict(data))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue  # skip corrupt, never fail the read
        return entries

    def _write_entries(self, entries: list[CoreEntry]) -> None:
        header = [
            "# Homer L5 Core Memory",
            "# One JSON entry per line. Auto-managed by mnemos.py.",
            f"# Budget: {self.budget_tokens} tokens | Entries: {len(entries)}",
            f"# Updated: {datetime.datetime.now().isoformat()}",
            "",
        ]
        body = [json.dumps(e.to_dict(), ensure_ascii=False) for e in entries]
        self.core_file.write_text("\n".join(header + body) + "\n", encoding="utf-8")

    def write(self, pattern: str, citation: str, confidence: str = "MEDIUM", key: str = "") -> CoreEntry:
        """Write a new pattern to Core. Citation REQUIRED."""
        require_citation(citation, "citation")
        if not pattern.strip():
            raise ValueError("pattern cannot be empty")
        if confidence not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError(f"confidence must be HIGH/MEDIUM/LOW, got {confidence!r}")

        if not key:
            key = f"core_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"

        entries = self._read_entries()
        # If the key exists, treat as confirmation bump
        for e in entries:
            if e.key == key:
                e.confirmed_count += 1
                e.last_used_at = datetime.datetime.now().isoformat()
                self._write_entries(entries)
                return e

        entry = CoreEntry(
            key=key,
            pattern=pattern,
            citation=citation.strip(),
            confidence=confidence,
        )
        entries.append(entry)
        self._write_entries(entries)
        return entry

    def edit(self, key: str, new_content: str, new_citation: str, reason: str) -> CoreEntry:
        """Edit an existing Core entry. new_citation AND reason REQUIRED."""
        require_citation(new_citation, "new_citation")
        if not reason or not reason.strip():
            raise ValueError("edit reason cannot be empty — every self-edit must be justified")

        entries = self._read_entries()
        for e in entries:
            if e.key == key:
                e.pattern = new_content
                e.citation = new_citation.strip()
                e.last_used_at = datetime.datetime.now().isoformat()
                self._write_entries(entries)
                return e
        raise KeyError(f"Core entry not found: {key}")

    def read_all(self) -> list[CoreEntry]:
        return self._read_entries()

    def total_tokens(self) -> int:
        return sum(e.estimated_tokens() for e in self._read_entries())

    def budget_status(self) -> dict:
        used = self.total_tokens()
        return {
            "budget_tokens": self.budget_tokens,
            "used_tokens": used,
            "headroom_tokens": max(0, self.budget_tokens - used),
            "over_budget": used > self.budget_tokens,
            "entry_count": len(self._read_entries()),
        }

    def compact(self, archive_fn) -> dict:
        """Trim Core to compact_target. archive_fn is called for each trimmed entry."""
        entries = self._read_entries()
        total = sum(e.estimated_tokens() for e in entries)
        if total <= self.budget_tokens:
            return {"moved": 0, "reason": "under budget", "archived_ids": []}

        # Priority: HIGH confidence + recent use + high confirmed_count wins
        conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

        def priority(e: CoreEntry) -> tuple:
            return (conf_rank.get(e.confidence, 1), e.confirmed_count, e.last_used_at)

        entries.sort(key=priority, reverse=True)

        kept: list[CoreEntry] = []
        archived_ids: list[str] = []
        running_tokens = 0
        for e in entries:
            if running_tokens + e.estimated_tokens() <= self.compact_target:
                kept.append(e)
                running_tokens += e.estimated_tokens()
            else:
                # Write full entry to Archival, keep back-pointer in Core
                archival_id = archive_fn(e)
                back_pointer = CoreEntry(
                    key=e.key,
                    pattern=f"[archived — see mnemos:{archival_id}]",
                    citation=f"mnemos:{archival_id}",
                    confidence="LOW",
                    confirmed_count=e.confirmed_count,
                    created_at=e.created_at,
                    last_used_at=e.last_used_at,
                    archived_to=archival_id,
                )
                kept.append(back_pointer)
                archived_ids.append(archival_id)
                running_tokens += back_pointer.estimated_tokens()

        self._write_entries(kept)
        return {
            "moved": len(archived_ids),
            "archived_ids": archived_ids,
            "new_token_count": running_tokens,
            "kept_entries": len(kept),
        }


# ── Recall tier ──────────────────────────────────────────────────────────────

class RecallMemory:
    """Recall tier — SQLite (FTS5 if available, LIKE fallback)."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path if db_path is not None else RECALL_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fts_available = False
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recall_entries (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    accessed_count INTEGER DEFAULT 0
                )
            """)
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(recall_entries)").fetchall()}
            if "embedding" not in existing_cols:
                conn.execute("ALTER TABLE recall_entries ADD COLUMN embedding BLOB")
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS recall_fts USING fts5(
                        topic, content, content=recall_entries, content_rowid=rowid
                    )
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS recall_ai AFTER INSERT ON recall_entries BEGIN
                        INSERT INTO recall_fts(rowid, topic, content) VALUES (new.rowid, new.topic, new.content);
                    END
                """)
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False
            conn.commit()

    def write(self, topic: str, content: str, citations: list[str]) -> str:
        """Write a Recall entry. At least one valid citation required."""
        if not topic or not topic.strip():
            raise ValueError("topic cannot be empty")
        if not content or not content.strip():
            raise ValueError("content cannot be empty")
        if not citations:
            raise CitationError("Recall entries require at least one citation")
        for c in citations:
            require_citation(c, "citations[]")

        entry_id = f"recall_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        # Compute embedding over topic+content concatenation (richer than content alone).
        # Returns None if embedder unavailable — row is still written, just without semantic search.
        vec = _encode_text(f"{topic}\n\n{content}")
        embedding_blob = _serialize_vec(vec)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO recall_entries (id, topic, content, citations_json, created_at, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, topic, content, json.dumps(citations), datetime.datetime.now().isoformat(), embedding_blob),
            )
            conn.commit()
        return entry_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Hybrid search: semantic (if embedder available) first, fall back to FTS5/LIKE.

        Preserves the original list[dict] contract — callers that depended on
        search() before embeddings shipped keep working. Semantic hits add a
        `similarity` float field; keyword hits do not.
        """
        if not query or not query.strip():
            return []
        semantic = self.search_semantic(query, limit=limit)
        if semantic:
            return semantic
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if self.fts_available:
                try:
                    cursor = conn.execute("""
                        SELECT r.* FROM recall_entries r
                        JOIN recall_fts f ON r.rowid = f.rowid
                        WHERE recall_fts MATCH ?
                        ORDER BY rank LIMIT ?
                    """, (query, limit))
                    rows = cursor.fetchall()
                    if rows:
                        return [_row_to_public_dict(r) for r in rows]
                except sqlite3.OperationalError:
                    pass
            return [_row_to_public_dict(r) for r in self._like_search(conn, query, limit)]

    def _like_search(self, conn, query: str, limit: int):
        like = f"%{query}%"
        return conn.execute("""
            SELECT * FROM recall_entries
            WHERE content LIKE ? OR topic LIKE ?
            ORDER BY created_at DESC LIMIT ?
        """, (like, like, limit)).fetchall()

    def search_semantic(self, query: str, limit: int = 5,
                        min_similarity: float = SEMANTIC_MIN_SIMILARITY) -> list[dict]:
        """Pure semantic search via sentence-transformer embeddings.

        Returns entries with cosine similarity >= min_similarity, ranked desc.
        Returns [] if embedder unavailable or no rows have embeddings — callers
        combine with keyword search themselves, or use `search()` for the hybrid.
        """
        if not query or not query.strip():
            return []
        q_vec = _encode_text(query)
        if q_vec is None:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, topic, content, citations_json, created_at, accessed_count, embedding "
                "FROM recall_entries WHERE embedding IS NOT NULL AND length(embedding) > 0"
            ).fetchall()

        scored = []
        for r in rows:
            vec = _deserialize_vec(r["embedding"])
            if vec is None:
                continue
            sim = _cosine_similarity(q_vec, vec)
            if sim >= min_similarity:
                d = _row_to_public_dict(r)
                d["similarity"] = round(sim, 4)
                scored.append(d)
        scored.sort(key=lambda d: d["similarity"], reverse=True)
        return scored[:limit]

    def search_summary(self, query: str, limit: int = 5, snippet_chars: int = 80) -> list[dict]:
        """Progressive-disclosure layer 1: return only {id, topic, snippet, citations, ...}.

        Caller inspects summaries, then calls `read_by_id()` / `load_full()` on
        the handful worth expanding. Matches the claude-mem Progressive
        Disclosure workflow — the goal is ~10x token reduction vs pulling full
        content every time.
        """
        full = self.search(query, limit=limit)
        out = []
        for r in full:
            content = r.get("content", "") or ""
            snippet = content[:snippet_chars].rstrip()
            if len(content) > snippet_chars:
                snippet = snippet + "..."
            item = {
                "id": r["id"],
                "topic": r["topic"],
                "snippet": snippet,
                "citations_json": r.get("citations_json", "[]"),
                "created_at": r.get("created_at", ""),
            }
            if "similarity" in r:
                item["similarity"] = r["similarity"]
            out.append(item)
        return out

    def backfill_embeddings(self) -> dict:
        """Compute embeddings for any rows that don't have one. Idempotent.

        Useful after installing sentence-transformers on an existing recall.db,
        or after upgrading from a Mnemos version without vector support.
        """
        if _get_embedder() is None:
            return {"backfilled": 0, "skipped": 0, "total": 0, "reason": "embedder_unavailable"}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, topic, content FROM recall_entries "
                "WHERE embedding IS NULL OR length(embedding) = 0"
            ).fetchall()
            total = len(rows)
            backfilled = 0
            for (row_id, topic, content) in rows:
                vec = _encode_text(f"{topic}\n\n{content}")
                if vec is None:
                    continue
                conn.execute(
                    "UPDATE recall_entries SET embedding = ? WHERE id = ?",
                    (_serialize_vec(vec), row_id),
                )
                backfilled += 1
            conn.commit()
        return {"backfilled": backfilled, "skipped": total - backfilled, "total": total}

    def read_by_id(self, entry_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM recall_entries WHERE id = ?", (entry_id,)).fetchone()
            return _row_to_public_dict(row) if row else None

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM recall_entries").fetchone()[0]


# ── Archival tier ────────────────────────────────────────────────────────────

class ArchivalMemory:
    """Archival tier — cold markdown storage, one file per entry."""

    def __init__(self, archival_dir: Path | None = None):
        self.archival_dir = archival_dir if archival_dir is not None else ARCHIVAL_DIR
        self.archival_dir.mkdir(parents=True, exist_ok=True)

    def write_from_core(self, entry: CoreEntry) -> str:
        """Write a Core entry to Archival when it's being trimmed."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archival_id = f"archival_{entry.key}_{ts}_{secrets.token_hex(2)}"
        path = self.archival_dir / f"{archival_id}.md"
        content = "\n".join([
            f"# Archival Entry: {archival_id}",
            "",
            f"**Source:** Core memory compaction",
            f"**Original key:** `{entry.key}`",
            f"**Citation:** `{entry.citation}`",
            f"**Confidence:** {entry.confidence}",
            f"**Confirmed count:** {entry.confirmed_count}",
            f"**Created:** {entry.created_at}",
            f"**Last used:** {entry.last_used_at}",
            f"**Archived at:** {datetime.datetime.now().isoformat()}",
            "",
            "## Content",
            "",
            entry.pattern,
            "",
            "## Back-pointer",
            "",
            f"This entry lives as a back-pointer in Core memory under key `{entry.key}` ",
            f"with citation `mnemos:{archival_id}`.",
        ])
        path.write_text(content, encoding="utf-8")
        return archival_id

    def read(self, archival_id: str) -> str | None:
        path = self.archival_dir / f"{archival_id}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def count(self) -> int:
        return len(list(self.archival_dir.glob("archival_*.md")))


# ── Facade ───────────────────────────────────────────────────────────────────

class MnemosStore:
    """Unified three-tier store. Facade for Core + Recall + Archival."""

    def __init__(
        self,
        core_file: Path | None = None,
        recall_db: Path | None = None,
        archival_dir: Path | None = None,
        budget_tokens: int = CORE_TOKEN_BUDGET,
    ):
        self.core = CoreMemory(core_file=core_file, budget_tokens=budget_tokens)
        self.recall = RecallMemory(db_path=recall_db)
        self.archival = ArchivalMemory(archival_dir=archival_dir)

    def write_core(self, pattern: str, citation: str, confidence: str = "MEDIUM", key: str = "") -> CoreEntry:
        return self.core.write(pattern, citation, confidence, key)

    def edit_core(self, key: str, new_content: str, new_citation: str, reason: str) -> CoreEntry:
        return self.core.edit(key, new_content, new_citation, reason)

    def write_recall(self, topic: str, content: str, citations: list[str]) -> str:
        return self.recall.write(topic, content, citations)

    def search_recall(self, query: str, limit: int = 5) -> list[dict]:
        return self.recall.search(query, limit)

    def search_recall_semantic(self, query: str, limit: int = 5,
                               min_similarity: float = SEMANTIC_MIN_SIMILARITY) -> list[dict]:
        return self.recall.search_semantic(query, limit, min_similarity)

    def search_recall_summary(self, query: str, limit: int = 5, snippet_chars: int = 80) -> list[dict]:
        """Progressive-disclosure layer 1 on Recall. Pair with `load_full(id)`."""
        return self.recall.search_summary(query, limit, snippet_chars)

    def load_full(self, entry_id: str) -> dict | None:
        """Progressive-disclosure layer 2 — fetch full Recall entry by id."""
        return self.recall.read_by_id(entry_id)

    def backfill_recall_embeddings(self) -> dict:
        """Backfill vector embeddings for Recall rows missing them. Idempotent."""
        return self.recall.backfill_embeddings()

    def read_core(self) -> list[dict]:
        return [e.to_dict() for e in self.core.read_all()]

    def read_archival(self, archival_id: str) -> str | None:
        return self.archival.read(archival_id)

    def read_recall_by_id(self, entry_id: str) -> dict | None:
        """Read a specific Recall entry by id. Exposes RecallMemory.read_by_id on the facade."""
        return self.recall.read_by_id(entry_id)

    def compact_if_over_budget(self) -> dict:
        status = self.core.budget_status()
        if not status["over_budget"]:
            return {"compacted": False, "status": status}
        result = self.core.compact(archive_fn=self.archival.write_from_core)
        return {"compacted": True, "result": result, "new_status": self.core.budget_status()}

    def health(self) -> dict:
        # Probe embedder WITHOUT forcing a model load — just check the flag state.
        semantic_available = _get_embedder() is not None
        return {
            "core": self.core.budget_status(),
            "recall_count": self.recall.count(),
            "archival_count": self.archival.count(),
            "fts_available": self.recall.fts_available,
            "semantic_available": semantic_available,
            "embed_model": EMBED_MODEL_NAME if semantic_available else None,
        }


# ── Minimal CLI (direct testing / homer_cli integration) ────────────────────

def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    store = MnemosStore()
    cmd = argv[1]

    if cmd == "health":
        print(json.dumps(store.health(), indent=2))
        return 0

    if cmd == "write-core":
        if len(argv) < 4:
            print("usage: mnemos.py write-core PATTERN CITATION [CONFIDENCE]", file=sys.stderr)
            return 1
        confidence = argv[4] if len(argv) > 4 else "MEDIUM"
        try:
            entry = store.write_core(pattern=argv[2], citation=argv[3], confidence=confidence)
            print(json.dumps(entry.to_dict(), indent=2))
            return 0
        except (CitationError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    if cmd == "read-core":
        for e in store.read_core():
            print(json.dumps(e, indent=2, ensure_ascii=False))
        return 0

    if cmd == "search-recall":
        if len(argv) < 3:
            print("usage: mnemos.py search-recall QUERY [LIMIT]", file=sys.stderr)
            return 1
        limit = int(argv[3]) if len(argv) > 3 else 5
        for r in store.search_recall(argv[2], limit=limit):
            print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    if cmd == "search-recall-semantic":
        if len(argv) < 3:
            print("usage: mnemos.py search-recall-semantic QUERY [LIMIT]", file=sys.stderr)
            return 1
        limit = int(argv[3]) if len(argv) > 3 else 5
        for r in store.search_recall_semantic(argv[2], limit=limit):
            print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    if cmd == "search-recall-summary":
        if len(argv) < 3:
            print("usage: mnemos.py search-recall-summary QUERY [LIMIT]", file=sys.stderr)
            return 1
        limit = int(argv[3]) if len(argv) > 3 else 5
        for r in store.search_recall_summary(argv[2], limit=limit):
            print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    if cmd == "load-full":
        if len(argv) < 3:
            print("usage: mnemos.py load-full ENTRY_ID", file=sys.stderr)
            return 1
        result = store.load_full(argv[2])
        if result is None:
            print(f"not found: {argv[2]}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if cmd == "backfill-embeddings":
        result = store.backfill_recall_embeddings()
        print(json.dumps(result, indent=2))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
