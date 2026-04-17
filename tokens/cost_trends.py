#!/usr/bin/env python3
"""
Toke — cost_trends.py

Cost trending across all Claude Code sessions: time-series spend, project
breakdown, tool cost attribution, and per-day aggregation.

Reads:
  ~/.claude/projects/*/*.jsonl              — session transcripts
  ~/.claude/telemetry/brain/tools.jsonl     — tool call telemetry
  Toke/automations/brain/routing_manifest.toml — pricing (single source)

Modes (default: last 7 days summary):
  python cost_trends.py                     7-day summary
  python cost_trends.py --all               full history
  python cost_trends.py --project NAME      filter to one project (substring)
  python cost_trends.py --daily             day-by-day breakdown
  python cost_trends.py --json              machine-readable output
  python cost_trends.py --top N             limit ranked views (default 15)
  python cost_trends.py --all --daily       daily breakdown over full history
  python cost_trends.py --all --json        full history as JSON

Pricing note:
  Uses routing_manifest.toml [models.*] values as the canonical price table.
  Verified against Anthropic docs 2026-04-11: Opus 4.6 = $5/$25/$0.50.
  See tokens/PRICING_NOTES.md for the full table and cache notes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 guard

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TOKE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = TOKE_ROOT / "automations" / "brain" / "routing_manifest.toml"
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
TOOLS_JSONL = Path(os.path.expanduser("~/.claude/telemetry/brain/tools.jsonl"))


# ---------------------------------------------------------------------------
# Pricing (loaded from manifest — single source of truth)
# ---------------------------------------------------------------------------
@dataclass
class Prices:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_5m_factor: float = 1.25   # 5m ephemeral write = input × 1.25
    cache_write_1h_factor: float = 2.00   # 1h ephemeral write = input × 2.00


def load_prices() -> dict[str, Prices]:
    """Load pricing from routing_manifest.toml. Falls back to hardcoded defaults."""
    defaults = {
        "haiku":     Prices(1.00,  5.00, 0.10),
        "sonnet":    Prices(3.00, 15.00, 0.30),
        "opus":      Prices(5.00, 25.00, 0.50),
        "opus[1m]":  Prices(5.00, 25.00, 0.50),
    }
    if not MANIFEST.exists():
        return defaults
    try:
        with open(MANIFEST, "rb") as f:
            m = tomllib.load(f)
        out: dict[str, Prices] = {}
        for alias, cfg in m.get("models", {}).items():
            out[alias] = Prices(
                input_per_mtok=float(cfg.get("cost_input_per_mtok", 0)),
                output_per_mtok=float(cfg.get("cost_output_per_mtok", 0)),
                cache_read_per_mtok=float(cfg.get("cost_cache_read_per_mtok", 0)),
            )
        return out or defaults
    except Exception:
        return defaults


def price_of(model_full_id: str, prices: dict[str, Prices]) -> tuple[str, Prices]:
    """Map a full model id to a manifest alias + Prices."""
    fallback = Prices(5.00, 25.00, 0.50)  # default to opus if unknown
    mid = model_full_id.lower()
    if "opus" in mid and "1m" in mid:
        return "opus[1m]", prices.get("opus[1m]") or prices.get("opus") or fallback
    if "opus" in mid:
        return "opus", prices.get("opus") or fallback
    if "sonnet" in mid:
        return "sonnet", prices.get("sonnet") or fallback
    if "haiku" in mid:
        return "haiku", prices.get("haiku") or fallback
    return "unknown", fallback


def compute_turn_cost(
    input_tok: int,
    output_tok: int,
    cache_read_tok: int,
    cache_write_5m_tok: int,
    cache_write_1h_tok: int,
    p: Prices,
) -> float:
    return (
        (input_tok / 1_000_000) * p.input_per_mtok
        + (output_tok / 1_000_000) * p.output_per_mtok
        + (cache_read_tok / 1_000_000) * p.cache_read_per_mtok
        + (cache_write_5m_tok / 1_000_000) * p.input_per_mtok * p.cache_write_5m_factor
        + (cache_write_1h_tok / 1_000_000) * p.input_per_mtok * p.cache_write_1h_factor
    )


# ---------------------------------------------------------------------------
# Session data container
# ---------------------------------------------------------------------------
@dataclass
class SessionRecord:
    session_id: str
    project: str          # human-readable project name
    project_dir: str      # raw directory name (for filtering)
    date: str             # YYYY-MM-DD of first turn
    first_ts: str         # ISO timestamp of first assistant turn
    last_ts: str          # ISO timestamp of last assistant turn
    dur_min: int          # session duration in minutes
    turns: int            # assistant turns with usage data
    user_msgs: int
    input_tok: int
    output_tok: int
    cache_read_tok: int
    cache_write_tok: int
    cost: float
    models_seen: list[str] = field(default_factory=list)

    @property
    def cost_per_turn(self) -> float:
        return self.cost / max(self.turns, 1)

    @property
    def cost_per_hour(self) -> float:
        return self.cost / max(self.dur_min / 60, 1/60)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------
def _proj_display_name(dir_name: str) -> str:
    """Convert a project directory name like C--Users-user-Desktop-T1-Toke to Toke."""
    return dir_name.replace("C--Users-user-", "").replace("-", "/")[:40]


def parse_transcript(path: Path, proj_display: str, proj_dir: str, prices: dict[str, Prices]) -> SessionRecord | None:
    total_input = total_output = total_cr = total_cw5m = total_cw1h = 0
    turns = user_msgs = 0
    first_ts = last_ts = ""
    models_seen: list[str] = []
    cost = 0.0

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                row_type = d.get("type", "")
                ts = d.get("timestamp", "")
                if ts:
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts

                if row_type == "user":
                    user_msgs += 1
                    continue

                if row_type != "assistant":
                    continue

                msg = d.get("message", {})
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage") or {}
                if not usage:
                    continue

                model = msg.get("model", "unknown")
                if model and model not in models_seen:
                    models_seen.append(model)

                cache_creation = usage.get("cache_creation") or {}
                cw_5m = int(cache_creation.get("ephemeral_5m_input_tokens", 0))
                cw_1h = int(cache_creation.get("ephemeral_1h_input_tokens", 0))
                # fall back to aggregate if split not present
                if cw_5m == 0 and cw_1h == 0:
                    total_cw_agg = int(usage.get("cache_creation_input_tokens", 0))
                    cw_5m = total_cw_agg  # attribute to 5m by default (conservative)

                inp   = int(usage.get("input_tokens", 0))
                out   = int(usage.get("output_tokens", 0))
                cr    = int(usage.get("cache_read_input_tokens", 0))

                _, p = price_of(model, prices)
                cost += compute_turn_cost(inp, out, cr, cw_5m, cw_1h, p)

                total_input  += inp
                total_output += out
                total_cr     += cr
                total_cw5m   += cw_5m
                total_cw1h   += cw_1h
                turns += 1

    except (PermissionError, OSError):
        return None

    if turns == 0:
        return None

    # Duration
    dur_min = 0
    try:
        t0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        dur_min = max(1, int((t1 - t0).total_seconds() / 60))
    except (ValueError, TypeError):
        dur_min = 1

    date = first_ts[:10] if first_ts else "1970-01-01"

    return SessionRecord(
        session_id=path.stem,
        project=proj_display,
        project_dir=proj_dir,
        date=date,
        first_ts=first_ts[:19] if first_ts else "",
        last_ts=last_ts[:19] if last_ts else "",
        dur_min=dur_min,
        turns=turns,
        user_msgs=user_msgs,
        input_tok=total_input,
        output_tok=total_output,
        cache_read_tok=total_cr,
        cache_write_tok=total_cw5m + total_cw1h,
        cost=cost,
        models_seen=models_seen,
    )


def load_sessions(
    project_filter: str = "",
    days: int = 7,
    all_history: bool = False,
    prices: dict[str, Prices] | None = None,
) -> list[SessionRecord]:
    if prices is None:
        prices = load_prices()
    if not CLAUDE_PROJECTS.exists():
        return []

    cutoff = ""
    if not all_history and days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    records: list[SessionRecord] = []
    for proj_dir in sorted(CLAUDE_PROJECTS.iterdir()):
        if not proj_dir.is_dir():
            continue
        proj_name = _proj_display_name(proj_dir.name)
        if project_filter and project_filter.lower() not in proj_name.lower() and project_filter.lower() not in proj_dir.name.lower():
            continue
        for transcript in proj_dir.glob("*.jsonl"):
            r = parse_transcript(transcript, proj_name, proj_dir.name, prices)
            if r is None:
                continue
            if cutoff and r.date < cutoff:
                continue
            records.append(r)

    records.sort(key=lambda r: r.first_ts)
    return records


# ---------------------------------------------------------------------------
# Tools telemetry
# ---------------------------------------------------------------------------
@dataclass
class ToolStats:
    name: str
    calls: int = 0
    input_bytes: int = 0
    output_bytes: int = 0
    sessions: set = field(default_factory=set)

    @property
    def total_bytes(self) -> int:
        return self.input_bytes + self.output_bytes


def load_tool_stats(
    project_filter: str = "",
    days: int = 7,
    all_history: bool = False,
) -> list[ToolStats]:
    if not TOOLS_JSONL.exists():
        return []

    cutoff = ""
    if not all_history and days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT")

    by_name: dict[str, ToolStats] = {}
    try:
        with open(TOOLS_JSONL, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts = d.get("ts", "")
                if cutoff and ts < cutoff:
                    continue
                name = d.get("tool_name", "unknown")
                sid  = d.get("session_id", "")
                ins  = int(d.get("input_size", 0))
                out  = int(d.get("output_size", 0))
                if name not in by_name:
                    by_name[name] = ToolStats(name=name)
                s = by_name[name]
                s.calls += 1
                s.input_bytes += ins
                s.output_bytes += out
                s.sessions.add(sid)
    except (PermissionError, OSError):
        return []

    return sorted(by_name.values(), key=lambda s: s.total_bytes, reverse=True)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_bytes(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}MB"
    if n >= 1_000:
        return f"{n/1_000:.0f}KB"
    return f"{n}B"


def spark(value: float, max_val: float, width: int = 18) -> str:
    if max_val <= 0:
        return " " * width
    filled = min(width, max(0, int(round(value / max_val * width))))
    return "|" * filled + "." * (width - filled)


def running_total(sessions: list[SessionRecord]) -> list[float]:
    rt, total = [], 0.0
    for s in sessions:
        total += s.cost
        rt.append(total)
    return rt


# ---------------------------------------------------------------------------
# Render: per-session summary table
# ---------------------------------------------------------------------------
def render_sessions(sessions: list[SessionRecord], top: int) -> str:
    if not sessions:
        return "  No sessions found.\n"
    lines: list[str] = []
    display = sessions[-top:] if top < len(sessions) else sessions
    rt = running_total(sessions)
    max_cost = max(s.cost for s in sessions)
    rt_start = rt[len(sessions) - len(display) - 1] if len(sessions) > len(display) else 0.0

    lines.append(f"  {'date':<12}  {'sid':<10}  {'$cost':>7}  {'cumul':>8}  {'turns':>5}  {'dur':>6}  {'$/hr':>6}  {'bar':<20}  project")
    lines.append("  " + "-" * 102)
    for i, s in enumerate(display):
        sid_short = s.session_id[:10]
        cum = rt_start + sum(x.cost for x in display[: i + 1])
        dur_label = f"{s.dur_min//60}h{s.dur_min%60:02d}m" if s.dur_min >= 60 else f"{s.dur_min}m"
        bar = spark(s.cost, max(max_cost, 0.01))
        cph = s.cost_per_hour
        lines.append(
            f"  {s.date:<12}  {sid_short:<10}  ${s.cost:>5.2f}  ${cum:>7.2f}"
            f"  {s.turns:>5}  {dur_label:>6}  ${cph:>4.2f}  {bar}  {s.project[:25]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render: daily aggregation
# ---------------------------------------------------------------------------
def render_daily(sessions: list[SessionRecord]) -> str:
    if not sessions:
        return "  No sessions found.\n"
    by_day: dict[str, dict] = {}
    for s in sessions:
        b = by_day.setdefault(s.date, {"cost": 0.0, "sessions": 0, "turns": 0, "dur_min": 0})
        b["cost"]     += s.cost
        b["sessions"] += 1
        b["turns"]    += s.turns
        b["dur_min"]  += s.dur_min

    days_sorted = sorted(by_day)
    max_cost = max(v["cost"] for v in by_day.values())
    cumulative = 0.0
    lines: list[str] = []
    lines.append(f"  {'date':<12}  {'$cost':>7}  {'cumul':>8}  {'sessions':>8}  {'turns':>6}  {'dur':>7}  {'bar':<20}")
    lines.append("  " + "-" * 80)
    for d in days_sorted:
        b = by_day[d]
        cumulative += b["cost"]
        dur = b["dur_min"]
        dur_label = f"{dur//60}h{dur%60:02d}m"
        bar = spark(b["cost"], max(max_cost, 0.01))
        lines.append(
            f"  {d:<12}  ${b['cost']:>5.2f}  ${cumulative:>7.2f}"
            f"  {b['sessions']:>8}  {b['turns']:>6}  {dur_label:>7}  {bar}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render: project breakdown
# ---------------------------------------------------------------------------
def render_projects(sessions: list[SessionRecord], top: int) -> str:
    if not sessions:
        return "  No sessions found.\n"
    by_proj: dict[str, dict] = {}
    for s in sessions:
        b = by_proj.setdefault(s.project, {"cost": 0.0, "sessions": 0, "turns": 0})
        b["cost"]     += s.cost
        b["sessions"] += 1
        b["turns"]    += s.turns

    ranked = sorted(by_proj.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    if top:
        ranked = ranked[:top]
    max_cost = max(b["cost"] for _, b in ranked) if ranked else 1
    total_cost = sum(b["cost"] for _, b in by_proj.items())
    lines: list[str] = []
    lines.append(f"  {'project':<35}  {'sessions':>8}  {'turns':>6}  {'$total':>8}  {'$/sess':>7}  {'share':>6}  {'bar':<18}")
    lines.append("  " + "-" * 100)
    for proj, b in ranked:
        avg = b["cost"] / max(b["sessions"], 1)
        share = b["cost"] / max(total_cost, 0.01) * 100
        bar = spark(b["cost"], max(max_cost, 0.01))
        lines.append(
            f"  {proj:<35}  {b['sessions']:>8}  {b['turns']:>6}  ${b['cost']:>6.2f}"
            f"  ${avg:>5.2f}  {share:>5.1f}%  {bar}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render: tool attribution
# ---------------------------------------------------------------------------
def render_tools(tool_stats: list[ToolStats], top: int) -> str:
    if not tool_stats:
        return "  No tool telemetry found.\n"
    display = tool_stats[:top]
    max_bytes = display[0].total_bytes if display else 1
    total_bytes = sum(t.total_bytes for t in tool_stats)
    lines: list[str] = []
    lines.append(f"  {'tool':<22}  {'calls':>6}  {'sessions':>8}  {'in':>9}  {'out':>9}  {'total':>9}  {'share':>6}  {'bar':<18}")
    lines.append("  " + "-" * 98)
    for t in display:
        share = t.total_bytes / max(total_bytes, 1) * 100
        bar = spark(t.total_bytes, max(max_bytes, 1))
        lines.append(
            f"  {t.name:<22}  {t.calls:>6}  {len(t.sessions):>8}"
            f"  {fmt_bytes(t.input_bytes):>9}  {fmt_bytes(t.output_bytes):>9}"
            f"  {fmt_bytes(t.total_bytes):>9}  {share:>5.1f}%  {bar}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render: summary stats block
# ---------------------------------------------------------------------------
def render_summary(sessions: list[SessionRecord]) -> str:
    if not sessions:
        return "  No sessions.\n"
    total_cost   = sum(s.cost for s in sessions)
    total_turns  = sum(s.turns for s in sessions)
    total_dur    = sum(s.dur_min for s in sessions)
    total_input  = sum(s.input_tok for s in sessions)
    total_output = sum(s.output_tok for s in sessions)
    total_cr     = sum(s.cache_read_tok for s in sessions)
    total_cw     = sum(s.cache_write_tok for s in sessions)
    num_days     = len({s.date for s in sessions})
    avg_session  = total_cost / len(sessions)
    avg_day      = total_cost / max(num_days, 1)

    # Most expensive single session
    priciest = max(sessions, key=lambda s: s.cost)

    lines = [
        f"  Sessions:        {len(sessions):>8,}  over {num_days} day(s)",
        f"  Total cost:      ${total_cost:>10,.2f}",
        f"  Avg / session:   ${avg_session:>10,.2f}",
        f"  Avg / day:       ${avg_day:>10,.2f}",
        f"  Total turns:     {total_turns:>10,}",
        f"  Total duration:  {total_dur//60:>5}h {total_dur%60:02d}m",
        f"  Tokens in:       {fmt_tok(total_input):>10}  fresh input",
        f"  Cache read:      {fmt_tok(total_cr):>10}  warm hits",
        f"  Cache write:     {fmt_tok(total_cw):>10}  written to cache",
        f"  Tokens out:      {fmt_tok(total_output):>10}  generated",
        f"",
        f"  Priciest:        ${priciest.cost:.2f}  {priciest.date}  {priciest.project[:30]}  ({priciest.session_id[:10]})",
    ]
    if num_days >= 7:
        proj_weekly = total_cost / num_days * 7
        lines.append(f"  7-day projection: ${proj_weekly:.2f}/wk  |  ${proj_weekly*4:.2f}/mo")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
def build_json(
    sessions: list[SessionRecord],
    tool_stats: list[ToolStats],
    daily: bool,
) -> dict:
    def session_to_dict(s: SessionRecord) -> dict:
        return {
            "session_id": s.session_id,
            "project": s.project,
            "date": s.date,
            "first_ts": s.first_ts,
            "last_ts": s.last_ts,
            "dur_min": s.dur_min,
            "turns": s.turns,
            "user_msgs": s.user_msgs,
            "input_tok": s.input_tok,
            "output_tok": s.output_tok,
            "cache_read_tok": s.cache_read_tok,
            "cache_write_tok": s.cache_write_tok,
            "cost_usd": round(s.cost, 6),
            "cost_per_turn": round(s.cost_per_turn, 6),
            "cost_per_hour": round(s.cost_per_hour, 4),
            "models": s.models_seen,
        }

    sessions_list = [session_to_dict(s) for s in sessions]
    total_cost = sum(s.cost for s in sessions)
    num_days = len({s.date for s in sessions})

    # Project breakdown
    by_proj: dict[str, dict] = {}
    for s in sessions:
        b = by_proj.setdefault(s.project, {"cost": 0.0, "sessions": 0, "turns": 0})
        b["cost"] += s.cost
        b["sessions"] += 1
        b["turns"] += s.turns
    projects = [
        {"project": k, "cost_usd": round(v["cost"], 4), "sessions": v["sessions"], "turns": v["turns"]}
        for k, v in sorted(by_proj.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    ]

    # Daily breakdown
    daily_list = None
    if daily:
        by_day: dict[str, dict] = {}
        for s in sessions:
            b = by_day.setdefault(s.date, {"cost": 0.0, "sessions": 0, "turns": 0})
            b["cost"] += s.cost
            b["sessions"] += 1
            b["turns"] += s.turns
        cumulative = 0.0
        daily_list = []
        for d in sorted(by_day):
            cumulative += by_day[d]["cost"]
            daily_list.append({
                "date": d,
                "cost_usd": round(by_day[d]["cost"], 4),
                "cumulative_usd": round(cumulative, 4),
                "sessions": by_day[d]["sessions"],
                "turns": by_day[d]["turns"],
            })

    # Tool stats
    tools_list = [
        {
            "tool": t.name,
            "calls": t.calls,
            "sessions": len(t.sessions),
            "input_bytes": t.input_bytes,
            "output_bytes": t.output_bytes,
            "total_bytes": t.total_bytes,
        }
        for t in tool_stats
    ]

    out: dict = {
        "meta": {
            "total_sessions": len(sessions),
            "total_days": num_days,
            "total_cost_usd": round(total_cost, 4),
            "avg_cost_per_session": round(total_cost / max(len(sessions), 1), 4),
            "avg_cost_per_day": round(total_cost / max(num_days, 1), 4),
            "pricing_source": "routing_manifest.toml [models.*]",
            "pricing_verified": "2026-04-11 against Anthropic docs",
        },
        "sessions": sessions_list,
        "projects": projects,
        "tools": tools_list,
    }
    if daily_list is not None:
        out["daily"] = daily_list
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cost trending across all Claude Code sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--all", action="store_true", help="Full history (overrides default 7-day window)")
    parser.add_argument("--project", type=str, default="", metavar="NAME", help="Filter to one project (substring match)")
    parser.add_argument("--daily", action="store_true", help="Show day-by-day breakdown")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--top", type=int, default=15, metavar="N", help="Limit ranked views (default 15)")
    parser.add_argument("--days", type=int, default=7, metavar="N", help="Window in days when not using --all (default 7)")
    args = parser.parse_args()

    prices = load_prices()

    # Load data
    sessions = load_sessions(
        project_filter=args.project,
        days=args.days,
        all_history=args.all,
        prices=prices,
    )
    tool_stats = load_tool_stats(
        project_filter=args.project,
        days=args.days,
        all_history=args.all,
    )

    # JSON mode — dump and exit
    if args.json:
        out = build_json(sessions, tool_stats, daily=args.daily)
        print(json.dumps(out, indent=2))
        return 0

    # Human-readable output
    W = 105
    window_label = "all time" if args.all else f"last {args.days} day(s)"
    proj_label   = f"  project={args.project}" if args.project else ""
    header_label = f"COST TRENDS — {window_label}{proj_label}"

    print("=" * W)
    print(f"  {header_label}")
    print("=" * W)

    # Summary block
    print()
    print("  SUMMARY")
    print("  " + "-" * 70)
    if not sessions:
        print(f"  No sessions found for window: {window_label}{proj_label}")
        print("=" * W)
        return 0
    print(render_summary(sessions))

    # Session table
    print()
    print(f"  PER-SESSION COST  (showing {min(args.top, len(sessions))} of {len(sessions)}, sorted by time)")
    print("  " + "-" * 102)
    print(render_sessions(sessions, args.top))

    # Running total line
    total_cost = sum(s.cost for s in sessions)
    rt = running_total(sessions)
    print()
    print(f"  Running total: ${total_cost:,.2f}  |  Sessions: {len(sessions)}")

    # Daily breakdown
    if args.daily:
        print()
        print(f"  DAILY BREAKDOWN")
        print("  " + "-" * 80)
        print(render_daily(sessions))

    # Project breakdown
    print()
    print(f"  PROJECT BREAKDOWN  (top {min(args.top, len({s.project for s in sessions}))})")
    print("  " + "-" * 100)
    print(render_projects(sessions, args.top))

    # Tool attribution
    if tool_stats:
        print()
        print(f"  TOOL DATA ATTRIBUTION  (top {min(args.top, len(tool_stats))} by bytes moved — proxy for LLM token cost)")
        print("  " + "-" * 98)
        print(render_tools(tool_stats, args.top))
    else:
        print()
        print(f"  TOOL DATA ATTRIBUTION")
        print(f"  No telemetry found at: {TOOLS_JSONL}")

    print()
    print(f"  pricing: manifest (verified 2026-04-11)  |  {MANIFEST}")
    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
