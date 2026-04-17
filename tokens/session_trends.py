#!/usr/bin/env python3
"""
Toke — session_trends.py

Cross-session analytics: cost trends, duration vs cost, skill tracking,
compaction prediction. Covers gap audit items D1, D2, D3, D6.

Modes:
  trends     Cost per session over time with direction indicator
  duration   Session duration vs cost correlation
  skills     Skill invocation frequency + cost across sessions
  compact    Compaction events with predictive context-fill-rate
  all        Full dashboard

Usage:
  python session_trends.py [trends|duration|skills|compact|all]
  python session_trends.py --project Toke trends
  python session_trends.py --days 7 all
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))

# ── Session parser ──────────────────────────────────────────────────────────

def parse_all_sessions(project_filter: str = "", days: int = 0) -> list[dict]:
    cutoff = ""
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()[:10]

    sessions = []
    for proj in sorted(CLAUDE_PROJECTS.iterdir()):
        if not proj.is_dir():
            continue
        proj_name = proj.name.replace("C--Users-user-", "").replace("-", "/")[:35]
        if project_filter and project_filter.lower() not in proj_name.lower():
            continue
        for transcript in proj.glob("*.jsonl"):
            s = _parse_one(transcript, proj_name)
            if s and (not cutoff or s["date"] >= cutoff):
                sessions.append(s)
    sessions.sort(key=lambda s: s["first_ts"])
    return sessions


def _parse_one(path: Path, proj_name: str) -> dict | None:
    total_out = total_cr = total_cw = total_inp = turns = tools = 0
    peak_ctx = 0
    first_ts = last_ts = ""
    skill_calls: list[str] = []
    compaction_events: list[dict] = []
    prev_ctx = 0
    tool_counter: Counter = Counter()

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message", {})
                usage = msg.get("usage", {})
                if not usage:
                    continue
                turns += 1
                cc = usage.get("cache_creation", {})
                inp = usage.get("input_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cw = cc.get("ephemeral_1h_input_tokens", 0) + cc.get("ephemeral_5m_input_tokens", 0)
                out = usage.get("output_tokens", 0)
                eff = inp + cr + cw
                delta = eff - prev_ctx

                if delta < -5000 and turns > 1:
                    compaction_events.append({"turn": turns, "delta": delta, "before": prev_ctx, "after": eff})

                total_out += out
                total_cr += cr
                total_cw += cw
                total_inp += inp
                peak_ctx = max(peak_ctx, eff)
                prev_ctx = eff

                ts = d.get("timestamp", "")
                if not first_ts:
                    first_ts = ts
                last_ts = ts

                for b in msg.get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        tools += 1
                        tool_counter[name] += 1
                        if name == "Skill":
                            inp_data = b.get("input", {})
                            skill_name = inp_data.get("skill", "?") if isinstance(inp_data, dict) else "?"
                            skill_calls.append(skill_name)
    except (PermissionError, OSError):
        return None

    if turns == 0:
        return None

    cost = (total_inp / 1e6) * 5 + (total_out / 1e6) * 25 + (total_cr / 1e6) * 0.5 + (total_cw / 1e6) * 10

    # Duration in minutes
    dur_min = 0
    try:
        t0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        dur_min = max(1, int((t1 - t0).total_seconds() / 60))
    except (ValueError, TypeError):
        pass

    return {
        "sid": path.stem[:10],
        "project": proj_name,
        "turns": turns,
        "tools": tools,
        "cost": cost,
        "output": total_out,
        "peak_ctx": peak_ctx,
        "ctx_util": peak_ctx / 1_000_000,
        "hit_rate": total_cr / (total_cr + total_cw) if (total_cr + total_cw) else 0,
        "date": first_ts[:10],
        "first_ts": first_ts[:19],
        "last_ts": last_ts[:19],
        "dur_min": dur_min,
        "cost_per_turn": cost / turns,
        "cost_per_min": cost / max(dur_min, 1),
        "skill_calls": skill_calls,
        "compactions": compaction_events,
        "tool_counter": dict(tool_counter),
    }


# ── Spark bar ───────────────────────────────────────────────────────────────

def spark(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = min(width, max(0, int(round(value / max_val * width))))
    return "|" * filled + " " * (width - filled)


# ── Analysis: trends ────────────────────────────────────────────────────────

def analysis_trends(sessions: list[dict]) -> None:
    print(f"  COST TREND ({len(sessions)} sessions)")
    print()
    if len(sessions) < 2:
        print("  Not enough sessions for trending.")
        return

    max_cost = max(s["cost"] for s in sessions)
    prev_cost = 0.0
    total = 0.0
    print(f"  {'date':<12}  {'$cost':>8}  {'trend':>6}  {'turns':>5}  {'$/turn':>7}  {'bar':<22}  project")
    print("  " + "-" * 90)
    for s in sessions:
        total += s["cost"]
        if prev_cost > 0:
            pct = ((s["cost"] - prev_cost) / prev_cost) * 100
            trend = f"{pct:>+5.0f}%" if abs(pct) < 1000 else "  NEW"
        else:
            trend = "  ---"
        bar = spark(s["cost"], max(max_cost, 0.01), 20)
        print(f"  {s['date']:<12}  ${s['cost']:>6.2f}  {trend}  {s['turns']:>5}  ${s['cost_per_turn']:>5.3f}  {bar}  {s['project'][:25]}")
        prev_cost = s["cost"]

    print()
    avg = total / len(sessions)
    print(f"  Total: ${total:>.2f}  |  Avg: ${avg:>.2f}/session  |  Sessions: {len(sessions)}")


# ── Analysis: duration ──────────────────────────────────────────────────────

def analysis_duration(sessions: list[dict]) -> None:
    valid = [s for s in sessions if s["dur_min"] > 1]
    if not valid:
        print("  No sessions with duration data.")
        return
    print(f"  SESSION DURATION vs COST ({len(valid)} sessions with timing)")
    print()

    # Sort by duration
    by_dur = sorted(valid, key=lambda s: s["dur_min"])
    max_dur = by_dur[-1]["dur_min"]
    max_cost = max(s["cost"] for s in valid)

    print(f"  {'dur':>6}  {'$cost':>8}  {'$/min':>7}  {'turns':>5}  {'hit%':>5}  {'cost_bar':<22}  project")
    print("  " + "-" * 85)
    for s in by_dur:
        bar = spark(s["cost"], max(max_cost, 0.01), 20)
        hr = f"{s['dur_min']//60}h{s['dur_min']%60:02d}m"
        print(f"  {hr:>6}  ${s['cost']:>6.2f}  ${s['cost_per_min']:>5.3f}  {s['turns']:>5}  {s['hit_rate']*100:>4.0f}%  {bar}  {s['project'][:25]}")

    # Correlation check
    short = [s for s in valid if s["dur_min"] < 30]
    long = [s for s in valid if s["dur_min"] >= 60]
    if short and long:
        avg_short_cpt = sum(s["cost_per_turn"] for s in short) / len(short)
        avg_long_cpt = sum(s["cost_per_turn"] for s in long) / len(long)
        print()
        print(f"  SHORT sessions (<30m):  avg ${avg_short_cpt:.4f}/turn  ({len(short)} sessions)")
        print(f"  LONG sessions (>=60m):  avg ${avg_long_cpt:.4f}/turn  ({len(long)} sessions)")
        if avg_long_cpt < avg_short_cpt:
            savings = (1 - avg_long_cpt / avg_short_cpt) * 100
            print(f"  FINDING: Long sessions are {savings:.0f}% cheaper per turn (cache amortization confirmed)")
        else:
            print(f"  FINDING: No per-turn savings from longer sessions")


# ── Analysis: skills ────────────────────────────────────────────────────────

def analysis_skills(sessions: list[dict]) -> None:
    skill_totals: Counter = Counter()
    skill_sessions: defaultdict = defaultdict(set)
    for s in sessions:
        for sk in s["skill_calls"]:
            skill_totals[sk] += 1
            skill_sessions[sk].add(s["sid"])

    if not skill_totals:
        print("  No Skill tool invocations found in transcripts.")
        return

    print(f"  SKILL INVOCATION TRACKING ({sum(skill_totals.values())} calls across {len(sessions)} sessions)")
    print()
    ranked = skill_totals.most_common(20)
    max_cnt = ranked[0][1]
    print(f"  {'skill':<25}  {'calls':>5}  {'sessions':>8}  {'bar':<22}")
    print("  " + "-" * 65)
    for name, cnt in ranked:
        bar = spark(cnt, max_cnt, 20)
        sess_cnt = len(skill_sessions[name])
        print(f"  {name:<25}  {cnt:>5}  {sess_cnt:>8}  {bar}")


# ── Analysis: compaction ────────────────────────────────────────────────────

def analysis_compact(sessions: list[dict]) -> None:
    events = []
    for s in sessions:
        for c in s["compactions"]:
            events.append({**c, "sid": s["sid"], "project": s["project"],
                           "peak_ctx": s["peak_ctx"], "turns": s["turns"]})

    print(f"  COMPACTION EVENTS ({len(events)} across {len(sessions)} sessions)")
    if not events:
        print("  No compaction events detected.")
        # Prediction
        high_ctx = [s for s in sessions if s["ctx_util"] > 0.3]
        if high_ctx:
            print()
            print(f"  RISK SESSIONS (>30% context utilization — compaction candidates):")
            for s in sorted(high_ctx, key=lambda s: s["ctx_util"], reverse=True)[:5]:
                print(f"    {s['sid']}  peak={s['peak_ctx']:,} ({s['ctx_util']*100:.1f}%)  turns={s['turns']}  {s['project']}")
        return

    print()
    print(f"  {'session':>10}  {'turn':>5}  {'freed':>10}  {'before':>10}  {'fill%':>6}  project")
    print("  " + "-" * 70)
    for e in sorted(events, key=lambda e: abs(e["delta"]), reverse=True):
        freed = abs(e["delta"])
        fill = e["before"] / 1_000_000
        print(f"  {e['sid']:>10}  {e['turn']:>5}  {freed:>10,}  {e['before']:>10,}  {fill*100:>5.1f}%  {e['project'][:25]}")

    # Prediction model
    fill_rates = [e["before"] / 1_000_000 for e in events]
    avg_fill = sum(fill_rates) / len(fill_rates)
    avg_turn = sum(e["turn"] for e in events) / len(events)
    print()
    print(f"  PREDICTION: Compaction fires at ~{avg_fill*100:.0f}% fill (~{avg_fill*1e6:,.0f} tok), avg turn {avg_turn:.0f}")
    at_risk = [s for s in sessions if s["ctx_util"] > avg_fill * 0.7 and not s["compactions"]]
    if at_risk:
        print(f"  AT RISK (>70% of avg fill, no compaction yet): {len(at_risk)} sessions")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    project = ""
    days = 0
    mode = "all"

    # Parse args
    i = 0
    clean = []
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif args[i] == "--days" and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            clean.append(args[i])
            i += 1
    if clean:
        mode = clean[0]

    sessions = parse_all_sessions(project_filter=project, days=days)
    if not sessions:
        print("No sessions found.", file=sys.stderr)
        return 1

    W = 95
    print("=" * W)
    label = f"project={project}" if project else "all projects"
    print(f"  SESSION TRENDS — {label} ({len(sessions)} sessions)")
    print("=" * W)

    if mode in ("trends", "all"):
        analysis_trends(sessions)
        if mode == "all":
            print()
    if mode in ("duration", "all"):
        analysis_duration(sessions)
        if mode == "all":
            print()
    if mode in ("skills", "all"):
        analysis_skills(sessions)
        if mode == "all":
            print()
    if mode in ("compact", "all"):
        analysis_compact(sessions)

    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
