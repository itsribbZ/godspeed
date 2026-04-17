#!/usr/bin/env python3
"""
Toke — tool_breakdown.py

Per-tool cost and frequency analysis from transcripts + tools.jsonl.

For each session (or aggregated across sessions) shows:
  - Tool call counts by name
  - Average tool_result size (the hidden bulk cost — flows into cached input)
  - Estimated cost attribution per tool (proportional to input token share)
  - Tools that consume disproportionate context

Data sources:
  - ~/.claude/projects/<cwd-hash>/<session_id>.jsonl  (transcripts — authoritative)
  - ~/.claude/telemetry/brain/tools.jsonl             (PostToolUse telemetry, when populated)

Modes:
  --current            Most recently modified transcript
  --session <path>     Explicit transcript file
  --project <name>     All sessions in a project (case-insensitive)
  --top <N>            Show top N tools (default 15)
  --json               Machine-readable output

Pricing: reads routing_manifest.toml, same as token_snapshot.py. Verified 2026-04-11.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOKE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = TOKE_ROOT / "automations" / "brain" / "routing_manifest.toml"
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
TOOLS_JSONL = Path(os.path.expanduser("~/.claude/telemetry/brain/tools.jsonl"))


# ---------------------------------------------------------------------------
# Pricing (reuses the manifest — single source of truth)
# ---------------------------------------------------------------------------
@dataclass
class Prices:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float


def load_prices() -> dict[str, Prices]:
    with open(MANIFEST, "rb") as f:
        m = tomllib.load(f)
    models = m.get("models", {})
    out: dict[str, Prices] = {}
    for alias, cfg in models.items():
        out[alias] = Prices(
            input_per_mtok=float(cfg.get("cost_input_per_mtok", 0)),
            output_per_mtok=float(cfg.get("cost_output_per_mtok", 0)),
            cache_read_per_mtok=float(cfg.get("cost_cache_read_per_mtok", 0)),
        )
    return out


def price_alias(model_full_id: str) -> str:
    mid = model_full_id.lower()
    if "opus" in mid and "1m" in mid:
        return "opus[1m]"
    if "opus" in mid:
        return "opus"
    if "sonnet" in mid:
        return "sonnet"
    if "haiku" in mid:
        return "haiku"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-tool aggregate
# ---------------------------------------------------------------------------
@dataclass
class ToolStats:
    name: str
    calls: int = 0
    total_result_chars: int = 0  # sum of tool_result content sizes (rough token proxy)
    error_calls: int = 0
    first_ts: str = ""
    last_ts: str = ""
    sessions: set = field(default_factory=set)

    @property
    def avg_result_chars(self) -> float:
        return (self.total_result_chars / self.calls) if self.calls else 0.0

    @property
    def est_result_tokens(self) -> int:
        # Rough char→token ratio: 3.6 chars/token is a reasonable code-heavy average
        return int(self.total_result_chars / 3.6)


@dataclass
class SessionReport:
    session_id: str
    path: Path
    tools: dict[str, ToolStats] = field(default_factory=dict)
    total_turns: int = 0
    total_tool_calls: int = 0
    total_result_chars: int = 0
    models_seen: set = field(default_factory=set)

    def upsert(self, tool_name: str) -> ToolStats:
        s = self.tools.get(tool_name)
        if s is None:
            s = ToolStats(name=tool_name)
            self.tools[tool_name] = s
        return s


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------
def parse_transcript(path: Path) -> SessionReport:
    rep = SessionReport(session_id=path.stem, path=path)

    # First pass: collect tool_use_id → tool_name from assistant content blocks
    tool_name_by_id: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t == "assistant":
                msg = d.get("message", {})
                if not isinstance(msg, dict):
                    continue
                model = msg.get("model")
                if model:
                    rep.models_seen.add(model)
                rep.total_turns += 1
                content = msg.get("content")
                if isinstance(content, list):
                    ts = d.get("timestamp", "")
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool_name = c.get("name", "unknown")
                            tool_id = c.get("id", "")
                            if tool_id:
                                tool_name_by_id[tool_id] = tool_name
                            stats = rep.upsert(tool_name)
                            stats.calls += 1
                            stats.sessions.add(rep.session_id)
                            if not stats.first_ts:
                                stats.first_ts = ts
                            stats.last_ts = ts
                            rep.total_tool_calls += 1

    # Second pass: attribute tool_result sizes to the tool that produced them
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "user":
                continue
            msg = d.get("message", {})
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                tool_id = c.get("tool_use_id", "")
                tool_name = tool_name_by_id.get(tool_id)
                if not tool_name:
                    continue
                # content can be a string or list of blocks
                result_content = c.get("content", "")
                if isinstance(result_content, list):
                    size = sum(len(str(b.get("text", b))) for b in result_content if isinstance(b, dict))
                else:
                    size = len(str(result_content))
                is_error = bool(c.get("is_error"))
                stats = rep.tools.get(tool_name)
                if stats:
                    stats.total_result_chars += size
                    if is_error:
                        stats.error_calls += 1
                    rep.total_result_chars += size
    return rep


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def fmt_num(n: int | float) -> str:
    if isinstance(n, float):
        return f"{n:,.0f}"
    return f"{n:,}"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def render_text(rep: SessionReport, top_n: int) -> str:
    lines: list[str] = []
    lines.append("=" * 76)
    lines.append(f"  TOOL BREAKDOWN — session {rep.session_id[:8]}")
    lines.append("=" * 76)
    lines.append(f"  Path:        {rep.path}")
    lines.append(f"  Turns:       {rep.total_turns}")
    lines.append(f"  Tool calls:  {rep.total_tool_calls}")
    lines.append(f"  Result bulk: {fmt_tok(rep.total_result_chars)} chars (~{fmt_tok(int(rep.total_result_chars/3.6))} tokens)")
    lines.append(f"  Models:      {', '.join(sorted(rep.models_seen)) or '(none)'}")
    lines.append("")

    if not rep.tools:
        lines.append("  (no tool calls in this transcript)")
        return "\n".join(lines)

    ranked = sorted(rep.tools.values(), key=lambda s: -s.total_result_chars)
    shown = ranked[:top_n]

    lines.append(f"  Top {len(shown)} tools by result-bulk (hidden cost driver)")
    lines.append("  " + "-" * 72)
    lines.append(
        f"  {'tool':<22} {'calls':>6} {'avg_result':>12} {'total_chars':>14} {'errs':>5} {'share':>7}"
    )
    total = rep.total_result_chars or 1
    for s in shown:
        share = 100.0 * s.total_result_chars / total
        lines.append(
            f"  {s.name:<22} {s.calls:>6} {fmt_tok(int(s.avg_result_chars)):>12} "
            f"{fmt_tok(s.total_result_chars):>14} {s.error_calls:>5} {share:>6.1f}%"
        )
    lines.append("")

    # "Frequency" view — tools ranked by call count instead of bulk
    freq_ranked = sorted(rep.tools.values(), key=lambda s: -s.calls)
    lines.append("  Top tools by call count")
    lines.append("  " + "-" * 72)
    for s in freq_ranked[:top_n]:
        lines.append(f"  {s.name:<22} {s.calls:>6} calls   avg_result={fmt_tok(int(s.avg_result_chars))}")
    lines.append("")

    # Efficiency: result per call
    lines.append("  Result-bulk per call (sorted high → low)")
    lines.append("  " + "-" * 72)
    eff_ranked = sorted(
        [s for s in rep.tools.values() if s.calls >= 1],
        key=lambda s: -s.avg_result_chars,
    )
    for s in eff_ranked[:top_n]:
        lines.append(
            f"  {s.name:<22} avg={fmt_tok(int(s.avg_result_chars))}  calls={s.calls:>4}  total={fmt_tok(s.total_result_chars)}"
        )
    lines.append("")
    lines.append("=" * 76)
    return "\n".join(lines)


def render_json(rep: SessionReport) -> str:
    tools_json = {}
    for name, s in rep.tools.items():
        tools_json[name] = {
            "calls": s.calls,
            "total_result_chars": s.total_result_chars,
            "avg_result_chars": round(s.avg_result_chars, 1),
            "est_result_tokens": s.est_result_tokens,
            "error_calls": s.error_calls,
            "first_ts": s.first_ts,
            "last_ts": s.last_ts,
        }
    return json.dumps(
        {
            "session_id": rep.session_id,
            "path": str(rep.path),
            "total_turns": rep.total_turns,
            "total_tool_calls": rep.total_tool_calls,
            "total_result_chars": rep.total_result_chars,
            "models_seen": sorted(rep.models_seen),
            "tools": tools_json,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Cross-session aggregation
# ---------------------------------------------------------------------------
def aggregate(reports: list[SessionReport]) -> SessionReport:
    agg = SessionReport(session_id="AGGREGATE", path=Path("."))
    for r in reports:
        agg.total_turns += r.total_turns
        agg.total_tool_calls += r.total_tool_calls
        agg.total_result_chars += r.total_result_chars
        agg.models_seen |= r.models_seen
        for name, stats in r.tools.items():
            a = agg.upsert(name)
            a.calls += stats.calls
            a.total_result_chars += stats.total_result_chars
            a.error_calls += stats.error_calls
            a.sessions |= stats.sessions
            if not a.first_ts or (stats.first_ts and stats.first_ts < a.first_ts):
                a.first_ts = stats.first_ts
            if stats.last_ts > a.last_ts:
                a.last_ts = stats.last_ts
    return agg


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def find_current_session() -> Path | None:
    if not CLAUDE_PROJECTS.exists():
        return None
    candidates = []
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            candidates.append(f)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_project(name: str) -> Path | None:
    if not CLAUDE_PROJECTS.exists():
        return None
    for proj in CLAUDE_PROJECTS.iterdir():
        if name.lower() in proj.name.lower():
            return proj
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Per-tool cost and frequency breakdown from transcripts.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--current", action="store_true", help="Most recent transcript")
    src.add_argument("--session", type=str, help="Path to a .jsonl transcript")
    src.add_argument("--project", type=str, help="Project name substring (aggregates)")
    parser.add_argument("--top", type=int, default=15, help="Show top N tools")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if args.session:
        path = Path(args.session)
        if not path.exists():
            print(f"ERROR: not found: {path}", file=sys.stderr)
            return 2
        rep = parse_transcript(path)
        print(render_json(rep) if args.json else render_text(rep, args.top))
        return 0

    if args.project:
        proj_dir = find_project(args.project)
        if not proj_dir:
            print(f"ERROR: project not found: {args.project}", file=sys.stderr)
            return 2
        jsonls = sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not jsonls:
            print(f"ERROR: no sessions in {proj_dir}", file=sys.stderr)
            return 2
        reports = [parse_transcript(j) for j in jsonls]
        agg = aggregate(reports)
        if args.json:
            print(json.dumps(
                {
                    "project": args.project,
                    "session_count": len(reports),
                    "aggregate": json.loads(render_json(agg)),
                    "sessions": [json.loads(render_json(r)) for r in reports],
                },
                indent=2,
            ))
        else:
            print(render_text(agg, args.top))
            print(f"\n  ({len(reports)} sessions aggregated)")
        return 0

    # default: --current
    path = find_current_session()
    if not path:
        print("ERROR: no session transcript found", file=sys.stderr)
        return 2
    rep = parse_transcript(path)
    print(render_json(rep) if args.json else render_text(rep, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
