#!/usr/bin/env python3
"""
Transcript JSONL loader for token-accountant.
=============================================
Walks `~/.claude/projects/<cwd-encoded>/<session_id>.jsonl` files. For each
assistant turn extracts the usage object (the only authoritative source of
input/cache/output API tokens), the firing model, and any tool_use blocks
(including Skill invocations with their `skill` input field).

Why this file exists:
- tools.jsonl carries CHAR counts, NOT API tokens. Cache/cost analysis is only
  trustworthy from the transcript usage object (per_turn_breakdown.py:138-145).
- skill_cost_attribution.py:130-140 already proves the pattern for skill-name
  extraction from tool_use.input.skill — we lift the same shape here.
- Keeping it isolated from token_accountant.py keeps the join-vs-extract
  responsibilities separate; cache_thrash.py and long_tail.py both consume this.

Source contract (Anthropic transcript shape):
    {"type": "assistant",
     "timestamp": "2026-05-02T...Z",
     "sessionId": "<uuid>",
     "requestId": "<short>",
     "message": {
        "id": "<msgID>",   # SAME id across N entries that share one API call
        "model": "claude-opus-4-7",
        "usage": {                                     # DUPLICATED on every entry
            "input_tokens": int,                       #   sharing this msg.id
            "cache_read_input_tokens": int,
            "cache_creation": {"ephemeral_5m_input_tokens": int,
                               "ephemeral_1h_input_tokens": int},
            "output_tokens": int,
        },
        "content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "..."}, "id": "..."},
            {"type": "text", "text": "..."},
            {"type": "thinking", "thinking": "..."},   # optional
            ...
        ]
     }}

CRITICAL DEDUPE — msg.id grouping (2026-05-02 finding):
    Claude Code splits one logical Anthropic API call across N JSONL `assistant`
    entries (typically 1 per content block: thinking + each tool_use). EVERY
    entry carries the SAME `message.id`, the SAME `requestId`, and the SAME
    `usage` envelope. Naively summing usage per entry overcounts by 2-4× in
    practice (verified across 9 sample transcripts: 2.0× thru 4.4×).
    Per_turn_breakdown.py:118-169 and skill_cost_attribution.py:101-152 both
    iterate per-entry without dedupe — they OVERCOUNT. We dedupe by msg.id:
    one TranscriptTurn per unique msg.id, with content union and usage from
    any one of the duplicate entries.

Sacred Rule alignment:
- Rule 2: read-only — never mutates transcripts.
- Rule 6: parses receipts only — no synthesis or invention.
- Rule 11: every load-bearing field cites its transcript source path.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))


# -----------------------------------------------------------------------------
# Data shape
# -----------------------------------------------------------------------------


@dataclass
class TranscriptTurn:
    """One assistant turn from the transcript JSONL."""
    session_id: str
    transcript_path: str           # absolute path for citation
    turn_num: int                  # 1-indexed within transcript
    ts: str                        # ISO-8601 with Z or +00:00
    model: str                     # raw model id from transcript
    input_tokens: int              # fresh prompt input
    cache_read: int                # cache_read_input_tokens
    cache_create_5m: int           # ephemeral_5m_input_tokens
    cache_create_1h: int           # ephemeral_1h_input_tokens
    output_tokens: int             # output_tokens
    thinking_chars: int            # sum of thinking-block text length (chars, not tokens)
    tool_uses: list[dict] = field(default_factory=list)  # raw tool_use blocks
    skills: list[str] = field(default_factory=list)      # skill names if Skill fired

    @property
    def cache_create_total(self) -> int:
        return self.cache_create_5m + self.cache_create_1h

    @property
    def effective_ctx(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_create_total

    @property
    def cache_hit_rate(self) -> float:
        denom = self.cache_read + self.cache_create_total + self.input_tokens
        return (self.cache_read / denom) if denom else 0.0


# -----------------------------------------------------------------------------
# Transcript discovery
# -----------------------------------------------------------------------------


def find_transcript(session_id: str, *, projects_root: Path = CLAUDE_PROJECTS) -> Path | None:
    """Find the transcript JSONL for a session_id by scanning every project dir.

    Returns the most-recently-modified match (sessions can be resumed across
    project dirs in rare cases).
    """
    if not projects_root.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    try:
        for proj in projects_root.iterdir():
            if not proj.is_dir():
                continue
            target = proj / f"{session_id}.jsonl"
            if target.exists():
                try:
                    candidates.append((target.stat().st_mtime, target))
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_all_transcripts(*, projects_root: Path = CLAUDE_PROJECTS,
                         since_ts: datetime | None = None) -> list[Path]:
    """List every transcript JSONL across all project dirs.

    Args:
        since_ts: only include transcripts modified at or after this time.

    Skips unreadable dirs/files silently (Sacred Rule 4 — fail-open on perms).
    """
    out: list[Path] = []
    if not projects_root.exists():
        return out
    cutoff = since_ts.timestamp() if since_ts else None
    try:
        for proj in projects_root.iterdir():
            if not proj.is_dir():
                continue
            try:
                for f in proj.glob("*.jsonl"):
                    try:
                        mt = f.stat().st_mtime
                    except (PermissionError, OSError):
                        continue
                    if cutoff is not None and mt < cutoff:
                        continue
                    out.append(f)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return out


# -----------------------------------------------------------------------------
# Transcript parsing
# -----------------------------------------------------------------------------


def _extract_skills(content: list) -> list[str]:
    """Pull skill names from Skill tool_use blocks. Mirrors
    skill_cost_attribution.py:130-140.
    """
    skills: list[str] = []
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        if b.get("name") != "Skill":
            continue
        inp = b.get("input") or {}
        sk = (inp.get("skill") or inp.get("skill_name") or "").strip()
        if sk:
            skills.append(sk)
    return skills


def _extract_thinking_chars(content: list) -> int:
    """Sum text length of thinking blocks. Char count is a proxy — Anthropic
    bills extended thinking on output_tokens already, so this is informational
    for spike-cause diagnosis (long_tail.py), not for cost.
    """
    total = 0
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") != "thinking":
            continue
        total += len(b.get("thinking", "") or "")
    return total


def parse_transcript(path: Path) -> Iterator[TranscriptTurn]:
    """Stream TranscriptTurn — one yield per UNIQUE message.id (deduped).

    Walks the file in two passes (single open, single read pass + group):
      1) Group all assistant entries by msg.id, union content blocks across
         duplicates, capture usage from the first entry seen (all entries
         share the same usage envelope).
      2) Yield in insertion order (first-seen ts wins).

    Tolerates malformed lines, missing usage, and partial fields. Entries
    without msg.id (legacy / pre-Claude-Code-split format) get a synthetic
    one-entry-per-line key — preserving old behavior for those rows.
    """
    if not path.exists():
        return
    session_id = path.stem
    try:
        f = path.open("r", encoding="utf-8", errors="replace")
    except (PermissionError, OSError):
        return

    # Insertion-ordered groups: msg.id -> {ts, model, usage, content[]}
    groups: dict[str, dict] = {}
    synth_counter = 0
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message", {}) or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue

            mid = msg.get("id") or ""
            if not mid:
                # Legacy / unsplit format: synthesize a unique key per entry
                synth_counter += 1
                mid = f"__synth_{synth_counter}"

            content_chunk = msg.get("content") or []
            if mid not in groups:
                groups[mid] = {
                    "ts": d.get("timestamp", "") or "",
                    "model": msg.get("model") or d.get("model", "") or "",
                    "usage": usage,
                    "content": list(content_chunk),
                }
            else:
                # Same logical message — append content blocks, KEEP first usage.
                groups[mid]["content"].extend(content_chunk)

    for turn_num, (mid, g) in enumerate(groups.items(), start=1):
        usage = g["usage"]
        cc = usage.get("cache_creation") or {}
        content = g["content"]
        tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        yield TranscriptTurn(
            session_id=session_id,
            transcript_path=str(path),
            turn_num=turn_num,
            ts=g["ts"],
            model=g["model"],
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_create_5m=int(cc.get("ephemeral_5m_input_tokens", 0) or 0),
            cache_create_1h=int(cc.get("ephemeral_1h_input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            thinking_chars=_extract_thinking_chars(content),
            tool_uses=tool_uses,
            skills=_extract_skills(content),
        )


def load_session_turns(session_id: str) -> list[TranscriptTurn]:
    """Convenience: find + parse one session into a list."""
    path = find_transcript(session_id)
    if path is None:
        return []
    return list(parse_transcript(path))


# -----------------------------------------------------------------------------
# CLI (for ad-hoc inspection)
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="transcript_loader")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="locate transcript path for a session_id")
    p_find.add_argument("--session", required=True)

    p_dump = sub.add_parser("dump", help="dump per-turn usage for a session_id")
    p_dump.add_argument("--session", required=True)
    p_dump.add_argument("--limit", type=int, default=None)

    p_recent = sub.add_parser("recent", help="list recent transcripts modified in last N hours")
    p_recent.add_argument("--hours", type=int, default=24)

    args = p.parse_args(argv)

    if args.cmd == "find":
        path = find_transcript(args.session)
        if path is None:
            print(f"NOT FOUND: no transcript for session {args.session}", file=sys.stderr)
            return 1
        print(path)
        return 0

    if args.cmd == "dump":
        turns = load_session_turns(args.session)
        if not turns:
            print(f"NOT FOUND or empty: {args.session}", file=sys.stderr)
            return 1
        if args.limit:
            turns = turns[: args.limit]
        for t in turns:
            tools = ",".join(b.get("name", "?") for b in t.tool_uses) or "-"
            skills = ",".join(t.skills) or "-"
            print(
                f"#{t.turn_num:03d} {t.ts}  model={t.model}  "
                f"in={t.input_tokens:>6} cr={t.cache_read:>7} "
                f"cw5={t.cache_create_5m:>5} cw1={t.cache_create_1h:>6} "
                f"out={t.output_tokens:>5} think_chars={t.thinking_chars:>5}  "
                f"tools=[{tools}]  skills=[{skills}]"
            )
        return 0

    if args.cmd == "recent":
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        paths = find_all_transcripts(since_ts=cutoff)
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p_ in paths[:50]:
            mt = datetime.fromtimestamp(p_.stat().st_mtime, tz=timezone.utc)
            print(f"{mt.isoformat()}  {p_}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
