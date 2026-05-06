#!/usr/bin/env python3
"""
Weekly waste report — aggregates the Cycle 2 modules into one digest.
=====================================================================
Combines:
  - Total session $USD across the window
  - Cache-thrash worst offenders (smoothed_hit_rate < 0.60)
  - Long-tail spike cohorts (n>=30, multi-gate)
  - Predicted-vs-actual drift (decisions actual > 2x predicted)
  - Per-division ROI (sum actual $ by division attribution)
  - Top-cost sessions
  - Top-cost models

Output:
  reports/weekly_YYYY-MM-DD.md (timestamped) — durable artifact.
  Printed to stdout when invoked via CLI.

Use:
    python weekly_report.py --window 7
    python weekly_report.py --window 30 --no-write

Sacred Rule alignment:
  Rule 6:  every section pulls from existing module reports — no synthesis
           that isn't already attested by transcript / decisions / tools data.
  Rule 11: every aggregate cites its source module + window + sample count.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_loader import find_all_transcripts, parse_transcript  # noqa: E402
from cost_model import cost_from_turn, alias_for_tier  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


HOME = Path.home()
TELEMETRY_DIR = HOME / ".claude" / "telemetry" / "brain"
TOOLS_FILE = TELEMETRY_DIR / "tools.jsonl"
TOKE_ROOT = HOME / "Desktop" / "T1" / "Toke"
TA_DIR = TOKE_ROOT / "automations" / "homer" / "token_accountant"
REPORTS_DIR = TA_DIR / "reports"


# -----------------------------------------------------------------------------
# Aggregate over window
# -----------------------------------------------------------------------------


def _iso_z_to_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def aggregate(window_days: int) -> dict:
    """Walk transcripts in window, return per-(session, division, model)
    aggregate.

    Joins to tools.jsonl for division attribution (transcripts don't carry it
    natively — Director attribution lives only in tools.jsonl entries).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # First pass: walk transcripts → cost-by-(session, model, skill)
    # skills_by_session[sid][sk] = {"cost": float, "turns": int}
    by_session: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0,
        "turns": 0,
        "models": set(),
        "skills": defaultdict(lambda: {"cost": 0.0, "turns": 0}),
        "earliest_ts": None,
        "latest_ts": None,
    })
    by_model: dict[str, float] = defaultdict(float)
    grand_cost = 0.0
    grand_turns = 0

    for path in find_all_transcripts(since_ts=cutoff):
        for t in parse_transcript(path):
            cost = cost_from_turn(t)
            sid = t.session_id
            s = by_session[sid]
            s["cost"] += cost
            s["turns"] += 1
            s["models"].add(t.model)
            t_dt = _iso_z_to_dt(t.ts)
            if t_dt:
                if s["earliest_ts"] is None or t_dt < s["earliest_ts"]:
                    s["earliest_ts"] = t_dt
                if s["latest_ts"] is None or t_dt > s["latest_ts"]:
                    s["latest_ts"] = t_dt
            skills = t.skills or ["(no skill)"]
            cost_share = cost / len(skills)
            for sk in skills:
                s["skills"][sk]["cost"] += cost_share
                s["skills"][sk]["turns"] += 1
            by_model[t.model or "(unknown)"] += cost
            grand_cost += cost
            grand_turns += 1

    # Second pass: tools.jsonl → division attribution per session
    by_division: dict[str, dict] = defaultdict(lambda: {
        "fires": 0,
        "sessions": set(),
        "skills": defaultdict(int),
    })
    if TOOLS_FILE.exists():
        with TOOLS_FILE.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts = _iso_z_to_dt(d.get("ts", ""))
                if ts is None or ts < cutoff:
                    continue
                div = d.get("division")
                if not div:
                    continue
                stat = by_division[div]
                stat["fires"] += 1
                stat["sessions"].add(d.get("session_id", ""))
                stat["skills"][d.get("skill_name", "?")] += 1

    # Cross-join: pro-rate session $ across divisions touched
    # (a session with N division-attributed fires gets cost split by fire share)
    division_cost: dict[str, float] = defaultdict(float)
    for sid, s in by_session.items():
        # Find divisions touched in this session
        touched: dict[str, int] = defaultdict(int)
        if TOOLS_FILE.exists():
            try:
                with TOOLS_FILE.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if d.get("session_id") != sid:
                            continue
                        ts = _iso_z_to_dt(d.get("ts", ""))
                        if ts is None or ts < cutoff:
                            continue
                        div = d.get("division")
                        if div:
                            touched[div] += 1
            except OSError:
                pass
        if touched:
            total_fires = sum(touched.values())
            for div, n in touched.items():
                division_cost[div] += s["cost"] * (n / total_fires)
        # else: session has no division-attributed fires — counts toward "(unattributed)"

    return {
        "window_days": window_days,
        "cutoff": cutoff,
        "grand_cost": grand_cost,
        "grand_turns": grand_turns,
        "by_session": by_session,
        "by_model": dict(by_model),
        "by_division": dict(by_division),
        "division_cost": dict(division_cost),
    }


# -----------------------------------------------------------------------------
# Compose report
# -----------------------------------------------------------------------------


def render_report(window_days: int = 7, *, write_to_disk: bool = True) -> str:
    agg = aggregate(window_days)
    md = []
    now = datetime.now(timezone.utc)
    md.append(f"# Weekly Waste Report — {now.strftime('%Y-%m-%d')}\n")
    md.append(f"**Window:** last {window_days} days "
              f"(since {agg['cutoff'].date()})\n")
    md.append(f"**Generated:** {now.isoformat()}\n")
    md.append("")
    md.append("## Top-line\n")
    md.append(f"- Total cost: **${agg['grand_cost']:.2f}**")
    md.append(f"- API messages (msg.id-deduped): **{agg['grand_turns']:,}**")
    md.append(f"- Sessions: **{len(agg['by_session'])}**")
    if agg["grand_turns"]:
        md.append(f"- Avg cost/turn: **${agg['grand_cost']/agg['grand_turns']:.4f}**")
    md.append("")

    # Per-model
    if agg["by_model"]:
        md.append("## $USD by Model\n")
        md.append("| model | total $ | share |")
        md.append("|---|---:|---:|")
        for m, c in sorted(agg["by_model"].items(), key=lambda x: -x[1]):
            share = (c / agg["grand_cost"] * 100) if agg["grand_cost"] else 0
            md.append(f"| `{m or '(unknown)'}` | ${c:.2f} | {share:.1f}% |")
        md.append("")

    # Per-division
    if agg["division_cost"]:
        md.append("## $USD by Division (attributed via tools.jsonl)\n")
        md.append("| division | fires | sessions | est. $ |")
        md.append("|---|---:|---:|---:|")
        for div, cost in sorted(agg["division_cost"].items(), key=lambda x: -x[1]):
            n = agg["by_division"].get(div, {}).get("fires", 0)
            sessions = len(agg["by_division"].get(div, {}).get("sessions", set()))
            md.append(f"| `{div}` | {n} | {sessions} | ${cost:.2f} |")
        md.append("")
        md.append("*Pro-rated by tool-fire share within each session — sessions with "
                  "zero division-attributed fires roll into Top-Cost-Sessions only.*\n")
    else:
        md.append("## $USD by Division\n\n"
                  "*No division-attributed tool fires in window.*\n")

    # Top-cost sessions
    md.append("## Top-Cost Sessions (10)\n")
    md.append("| session | turns | $USD | top skill | start |")
    md.append("|---|---:|---:|---|---|")
    sessions_sorted = sorted(
        agg["by_session"].items(), key=lambda x: -x[1]["cost"]
    )[:10]
    for sid, s in sessions_sorted:
        top_skill = (max(s["skills"].items(), key=lambda x: x[1]["cost"])[0]
                     if s["skills"] else "(none)")
        start = s["earliest_ts"].isoformat()[:19] if s["earliest_ts"] else "?"
        md.append(f"| `{sid[:8]}` | {s['turns']} | ${s['cost']:.2f} | "
                  f"`{top_skill}` | {start} |")
    md.append("")

    # Per-skill aggregate (across all sessions)
    skill_total: dict[str, float] = defaultdict(float)
    skill_turns_total: dict[str, int] = defaultdict(int)
    for s in agg["by_session"].values():
        for sk, agg_skill in s["skills"].items():
            skill_total[sk] += agg_skill["cost"]
            skill_turns_total[sk] += agg_skill["turns"]
    if skill_total:
        md.append("## Top Skills by $USD\n")
        md.append("| skill | total $ | turns | avg/turn |")
        md.append("|---|---:|---:|---:|")
        for sk, c in sorted(skill_total.items(), key=lambda x: -x[1])[:10]:
            n = skill_turns_total[sk]
            avg = c / n if n else 0
            md.append(f"| `{sk}` | ${c:.2f} | {n} | ${avg:.4f} |")
        md.append("")

    # Sub-reports inline (cache-thrash + long-tail + drift)
    md.append("## Cache-Thrash Embedded\n")
    try:
        from cache_thrash import run as run_thrash
        md.append("```")
        md.append(run_thrash(window_days=window_days, write_proposals=False))
        md.append("```")
    except Exception as e:  # noqa: BLE001
        md.append(f"*cache-thrash failed: {e}*\n")
    md.append("")
    md.append("## Long-Tail Embedded\n")
    try:
        from long_tail import run as run_long_tail
        md.append("```")
        md.append(run_long_tail(window_days=window_days))
        md.append("```")
    except Exception as e:  # noqa: BLE001
        md.append(f"*long-tail failed: {e}*\n")
    md.append("")
    md.append("## Predicted-vs-Actual Drift Embedded\n")
    try:
        from reconciliation import run as run_recon
        md.append("```")
        md.append(run_recon(last_n=200))
        md.append("```")
    except Exception as e:  # noqa: BLE001
        md.append(f"*reconciliation failed: {e}*\n")

    text = "\n".join(md) + "\n"

    if write_to_disk:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORTS_DIR / f"weekly_{now.strftime('%Y-%m-%d')}.md"
        out.write_text(text, encoding="utf-8")
        print(f"weekly report written: {out}", file=sys.stderr)

    return text


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="weekly_report")
    p.add_argument("--window", type=int, default=7)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    print(render_report(window_days=args.window, write_to_disk=not args.no_write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
