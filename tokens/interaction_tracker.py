#!/usr/bin/env python3
"""
Toke — Human-AI Interaction Tracker
====================================
Mines decisions.jsonl for human behavioral patterns. Closes the
"Human-AI Interaction Design" gap identified in the Katanforoosh
comparison (4/10 -> 8/10).

Commands:
    python interaction_tracker.py overview          Full human metrics dashboard
    python interaction_tracker.py overrides         Override analysis by tier
    python interaction_tracker.py delegation        Delegation mode distribution
    python interaction_tracker.py stalls            Sessions with >5min gaps
    python interaction_tracker.py progression       Skill progression over time
    python interaction_tracker.py --json            Machine-readable output
    python interaction_tracker.py --days N          Last N days only (default 30)

Data source: ~/.claude/telemetry/brain/decisions.jsonl
Dependencies: Python 3.11+ stdlib only.

Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, Phase 2A.
"""

from __future__ import annotations

import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DECISIONS_LOG = Path.home() / ".claude" / "telemetry" / "brain" / "decisions.jsonl"


# =============================================================================
# Data loading
# =============================================================================


def _read_decisions() -> list[dict[str, Any]]:
    """Read decisions.jsonl, return chronological (oldest first)."""
    if not DECISIONS_LOG.exists():
        return []
    entries: list[dict] = []
    try:
        for line in DECISIONS_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    return entries  # already chronological (appended in order)


def _parse_ts(ts_str: str) -> datetime.datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _filter_days(entries: list[dict], days: int) -> list[dict]:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return [e for e in entries if (_parse_ts(e.get("ts", "")) or cutoff) >= cutoff]


def _is_override(entry: dict) -> bool:
    current = entry.get("current_model", "").lower()
    result = entry.get("result", {})
    recommended = result.get("model", "").lower() if isinstance(result, dict) else ""
    if not current or not recommended:
        return False
    return recommended not in current and current not in recommended


def _get_result(entry: dict) -> dict:
    r = entry.get("result", {})
    return r if isinstance(r, dict) else {}


# =============================================================================
# Commands
# =============================================================================


def cmd_overview(entries: list[dict], as_json: bool = False) -> dict:
    """Full human interaction metrics dashboard."""
    total = len(entries)
    if total == 0:
        msg = {"error": "No decisions found in window."}
        if as_json:
            print(json.dumps(msg, indent=2))
        else:
            print("No decisions found in window.")
        return msg

    # Override rate (overall + per tier)
    overrides = sum(1 for e in entries if _is_override(e))
    override_rate = overrides / total

    tier_data: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "overrides": 0})
    for e in entries:
        tier = _get_result(e).get("tier", "?")
        tier_data[tier]["total"] += 1
        if _is_override(e):
            tier_data[tier]["overrides"] += 1

    tier_rates = {
        t: round(d["overrides"] / d["total"] * 100, 1) if d["total"] else 0
        for t, d in sorted(tier_data.items())
    }

    # Correction / reprompt rate
    corrections = sum(1 for e in entries if _get_result(e).get("correction_detected_in_prompt"))
    reprompt_rate = corrections / total

    # Sessions
    sessions: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        sessions[e.get("session_id", "unknown")].append(e)

    # Abandonment: sessions with early correction and <= 3 total turns
    abandoned = 0
    for sid, sess_entries in sessions.items():
        if len(sess_entries) <= 3:
            has_early_correction = any(
                _get_result(e).get("correction_detected_in_prompt") for e in sess_entries[:3]
            )
            if has_early_correction:
                abandoned += 1
    abandonment_rate = abandoned / len(sessions) if sessions else 0

    # Trust calibration
    conf_correction = [_get_result(e).get("confidence", 1.0) for e in entries
                       if _get_result(e).get("correction_detected_in_prompt")]
    conf_normal = [_get_result(e).get("confidence", 1.0) for e in entries
                   if not _get_result(e).get("correction_detected_in_prompt")]
    avg_cc = sum(conf_correction) / len(conf_correction) if conf_correction else None
    avg_cn = sum(conf_normal) / len(conf_normal) if conf_normal else None
    trust_healthy = avg_cc is not None and avg_cn is not None and avg_cc < avg_cn

    # Delegation modes
    mode_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        human = e.get("human", {})
        if isinstance(human, dict) and "delegation_mode" in human:
            mode_counts[human["delegation_mode"]] += 1
        else:
            # Fallback computation
            result = _get_result(e)
            conf = result.get("confidence", 1.0)
            guardrails = result.get("guardrails_fired", [])
            if guardrails:
                mode_counts["veto"] += 1
            elif conf < 0.30:
                mode_counts["checkpoint"] += 1
            elif conf < 0.70:
                mode_counts["supervised"] += 1
            else:
                mode_counts["full"] += 1

    mode_total = sum(mode_counts.values())
    mode_pcts = {m: round(c / mode_total * 100, 1) for m, c in sorted(mode_counts.items())}

    # Inter-turn gaps (from human{} layer)
    gaps = []
    for e in entries:
        human = e.get("human", {})
        if isinstance(human, dict):
            g = human.get("inter_turn_gap_seconds", 0)
            if isinstance(g, (int, float)) and 0.5 < g < 86400:  # skip sub-second and >1d
                gaps.append(g)

    gap_p50 = sorted(gaps)[len(gaps) // 2] if gaps else None
    gap_p95 = sorted(gaps)[int(len(gaps) * 0.95)] if gaps else None

    report = {
        "total_decisions": total,
        "total_sessions": len(sessions),
        "override_rate_pct": round(override_rate * 100, 1),
        "override_rate_by_tier": tier_rates,
        "reprompt_rate_pct": round(reprompt_rate * 100, 1),
        "abandonment_rate_pct": round(abandonment_rate * 100, 1),
        "trust_calibration": {
            "avg_conf_on_correction": round(avg_cc, 3) if avg_cc else None,
            "avg_conf_on_normal": round(avg_cn, 3) if avg_cn else None,
            "healthy": trust_healthy,
        },
        "delegation_modes_pct": mode_pcts,
        "inter_turn_gap_p50_s": round(gap_p50, 1) if gap_p50 else None,
        "inter_turn_gap_p95_s": round(gap_p95, 1) if gap_p95 else None,
        "stall_sessions": sum(1 for g in gaps if g > 300),
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"HUMAN INTERACTION METRICS ({total} decisions, {len(sessions)} sessions)")
        print("=" * 60)
        print(f"Override rate:        {report['override_rate_pct']}% ({overrides}/{total})")
        tier_str = "  ".join(f"{t}: {v}%" for t, v in tier_rates.items())
        print(f"  By tier:            {tier_str}")
        print(f"Reprompt rate:        {report['reprompt_rate_pct']}% ({corrections}/{total})")
        print(f"Abandonment rate:     {report['abandonment_rate_pct']}% ({abandoned}/{len(sessions)} sessions)")
        tl = "healthy" if trust_healthy else "UNCALIBRATED"
        print(f"Trust calibration:    {tl}", end="")
        if avg_cc is not None:
            print(f" (corr={avg_cc:.3f}, normal={avg_cn:.3f})")
        else:
            print(" (insufficient correction data)")
        print(f"Delegation modes:     {' '.join(f'{m}={p}%' for m, p in mode_pcts.items())}")
        if gap_p50 is not None:
            print(f"Inter-turn gap:       {gap_p50:.0f}s (p50) / {gap_p95:.0f}s (p95)")
            print(f"Stall events (>5m):   {report['stall_sessions']}")
        else:
            print("Inter-turn gap:       (awaiting human{} layer data — populates going forward)")
        print()

    return report


def cmd_overrides(entries: list[dict], as_json: bool = False) -> dict:
    """Override analysis by tier — which tiers does the user disagree with most?"""
    tier_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "overrides": 0, "examples": []}
    )

    for e in entries:
        result = _get_result(e)
        tier = result.get("tier", "?")
        tier_data[tier]["total"] += 1
        if _is_override(e):
            tier_data[tier]["overrides"] += 1
            if len(tier_data[tier]["examples"]) < 3:
                # Truncate prompt for readability
                human = e.get("human", {})
                prompt_len = human.get("prompt_token_count", 0) if isinstance(human, dict) else 0
                tier_data[tier]["examples"].append({
                    "recommended": result.get("model", "?"),
                    "actual": e.get("current_model", "?"),
                    "confidence": result.get("confidence", 0),
                    "prompt_tokens": prompt_len,
                })

    report = {}
    for tier in ["S0", "S1", "S2", "S3", "S4", "S5"]:
        if tier not in tier_data:
            continue
        d = tier_data[tier]
        rate = d["overrides"] / d["total"] * 100 if d["total"] else 0
        report[tier] = {
            "total": d["total"],
            "overrides": d["overrides"],
            "rate_pct": round(rate, 1),
            "examples": d["examples"],
        }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("OVERRIDE ANALYSIS BY TIER")
        print("=" * 60)
        for tier, data in report.items():
            marker = " *** HIGH" if data["rate_pct"] > 30 else ""
            print(f"  {tier}: {data['rate_pct']:>5.1f}% ({data['overrides']}/{data['total']}){marker}")
            for ex in data["examples"]:
                print(f"       recommended={ex['recommended']}, actual={ex['actual']}, conf={ex['confidence']:.2f}")
        print()
        # Diagnosis
        high_tiers = [t for t, d in report.items() if d["rate_pct"] > 30 and d["total"] >= 5]
        if high_tiers:
            print(f"HIGH OVERRIDE TIERS: {', '.join(high_tiers)}")
            print("  These tiers have >30% override rate with 5+ decisions.")
            print("  Consider: adjust Brain thresholds, or the model recommendation for these tiers.")
        else:
            print("No tiers with concerning override rates (>30% with 5+ decisions).")
        print()

    return report


def cmd_delegation(entries: list[dict], as_json: bool = False) -> dict:
    """Delegation mode distribution — how autonomous is the system?"""
    mode_counts: dict[str, int] = defaultdict(int)
    mode_by_tier: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for e in entries:
        result = _get_result(e)
        tier = result.get("tier", "?")
        human = e.get("human", {})
        if isinstance(human, dict) and "delegation_mode" in human:
            mode = human["delegation_mode"]
        else:
            conf = result.get("confidence", 1.0)
            guardrails = result.get("guardrails_fired", [])
            if guardrails:
                mode = "veto"
            elif conf < 0.30:
                mode = "checkpoint"
            elif conf < 0.70:
                mode = "supervised"
            else:
                mode = "full"
        mode_counts[mode] += 1
        mode_by_tier[tier][mode] += 1

    total = sum(mode_counts.values())
    report = {
        "total": total,
        "modes": {m: {"count": c, "pct": round(c / total * 100, 1)} for m, c in sorted(mode_counts.items())},
        "by_tier": {
            t: {m: c for m, c in sorted(modes.items())}
            for t, modes in sorted(mode_by_tier.items())
        },
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("DELEGATION MODE DISTRIBUTION")
        print("=" * 60)
        print(f"Total decisions: {total}")
        print()
        for mode in ["full", "supervised", "checkpoint", "veto"]:
            d = report["modes"].get(mode, {"count": 0, "pct": 0})
            bar = "#" * int(d["pct"] / 2)
            print(f"  {mode:<14} {d['count']:>4} ({d['pct']:>5.1f}%)  {bar}")
        print()
        print("By tier:")
        for tier, modes in report["by_tier"].items():
            mode_str = " ".join(f"{m}={c}" for m, c in modes.items())
            print(f"  {tier}: {mode_str}")
        print()
        # Interpretation
        full_pct = report["modes"].get("full", {}).get("pct", 0)
        if full_pct > 70:
            print("ASSESSMENT: High autonomy — system is trusted for most tasks.")
        elif full_pct > 50:
            print("ASSESSMENT: Moderate autonomy — majority of tasks fully delegated.")
        else:
            print("ASSESSMENT: Low autonomy — most tasks require human oversight.")
        print()

    return report


def cmd_stalls(entries: list[dict], as_json: bool = False) -> dict:
    """Sessions with >5min inter-turn gaps (stall detection)."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        sessions[e.get("session_id", "unknown")].append(e)

    stall_threshold = 300  # 5 minutes

    stall_events: list[dict] = []
    for sid, sess_entries in sessions.items():
        for i in range(1, len(sess_entries)):
            ts_prev = _parse_ts(sess_entries[i - 1].get("ts", ""))
            ts_curr = _parse_ts(sess_entries[i].get("ts", ""))
            if ts_prev and ts_curr:
                gap = (ts_curr - ts_prev).total_seconds()
                if gap > stall_threshold:
                    result = _get_result(sess_entries[i])
                    stall_events.append({
                        "session_id": sid,
                        "turn": i,
                        "gap_seconds": round(gap, 1),
                        "gap_minutes": round(gap / 60, 1),
                        "after_tier": _get_result(sess_entries[i - 1]).get("tier", "?"),
                        "resume_tier": result.get("tier", "?"),
                        "correction_on_resume": result.get("correction_detected_in_prompt", False),
                    })

    report = {
        "stall_threshold_seconds": stall_threshold,
        "total_stalls": len(stall_events),
        "stalls_with_correction": sum(1 for s in stall_events if s["correction_on_resume"]),
        "events": stall_events[:20],  # cap at 20 for readability
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"STALL DETECTION (gaps > {stall_threshold // 60} minutes)")
        print("=" * 60)
        print(f"Total stalls:           {report['total_stalls']}")
        print(f"Stalls with correction: {report['stalls_with_correction']} (user returned unhappy)")
        print()
        if stall_events:
            for s in stall_events[:10]:
                corr = " [CORRECTION]" if s["correction_on_resume"] else ""
                print(
                    f"  session={s['session_id'][:8]}.. turn={s['turn']} "
                    f"gap={s['gap_minutes']}min "
                    f"({s['after_tier']}->{s['resume_tier']}){corr}"
                )
        else:
            print("  No stall events detected.")
        print()

    return report


def cmd_progression(entries: list[dict], as_json: bool = False) -> dict:
    """Skill progression over time — is the user getting better at prompting?"""
    if len(entries) < 10:
        msg = {"error": "Need at least 10 decisions for progression analysis."}
        if as_json:
            print(json.dumps(msg, indent=2))
        else:
            print("Need at least 10 decisions for progression analysis.")
        return msg

    # Split into 7d and 30d windows
    now = datetime.datetime.now(datetime.timezone.utc)
    d7 = now - datetime.timedelta(days=7)
    d30 = now - datetime.timedelta(days=30)

    recent_7d = [e for e in entries if (_parse_ts(e.get("ts", "")) or d30) >= d7]
    recent_30d = [e for e in entries if (_parse_ts(e.get("ts", "")) or d30) >= d30]

    def _metrics(subset: list[dict]) -> dict:
        if not subset:
            return {"override_rate": 0, "reprompt_rate": 0, "avg_confidence": 0, "count": 0}
        overrides = sum(1 for e in subset if _is_override(e))
        corrections = sum(1 for e in subset if _get_result(e).get("correction_detected_in_prompt"))
        confs = [_get_result(e).get("confidence", 1.0) for e in subset]
        return {
            "override_rate": round(overrides / len(subset), 4),
            "reprompt_rate": round(corrections / len(subset), 4),
            "avg_confidence": round(sum(confs) / len(confs), 4) if confs else 0,
            "count": len(subset),
        }

    m7 = _metrics(recent_7d)
    m30 = _metrics(recent_30d)

    # Deltas (positive = improving)
    override_delta = m30["override_rate"] - m7["override_rate"]
    reprompt_delta = m30["reprompt_rate"] - m7["reprompt_rate"]
    confidence_delta = m7["avg_confidence"] - m30["avg_confidence"]

    # Composite skill improvement signal
    # Positive values = improving
    improvement = (override_delta * 40) + (reprompt_delta * 30) + (confidence_delta * 30)

    report = {
        "window_7d": m7,
        "window_30d": m30,
        "deltas": {
            "override_rate": round(override_delta, 4),
            "reprompt_rate": round(reprompt_delta, 4),
            "confidence": round(confidence_delta, 4),
        },
        "composite_improvement": round(improvement, 2),
        "trend": "IMPROVING" if improvement > 0 else "STABLE" if improvement > -1 else "DECLINING",
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("SKILL PROGRESSION (7d vs 30d)")
        print("=" * 60)
        print(f"  {'Metric':<25} {'7d':>10} {'30d':>10} {'Delta':>10} {'Dir':>10}")
        print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

        def _dir(val: float, positive_good: bool = True) -> str:
            if abs(val) < 0.001:
                return "--"
            good = val > 0 if positive_good else val < 0
            return "BETTER" if good else "WORSE"

        print(
            f"  {'Override rate':<25} {m7['override_rate']*100:>9.1f}% {m30['override_rate']*100:>9.1f}% "
            f"{override_delta*100:>+9.1f}% {_dir(override_delta, True):>10}"
        )
        print(
            f"  {'Reprompt rate':<25} {m7['reprompt_rate']*100:>9.1f}% {m30['reprompt_rate']*100:>9.1f}% "
            f"{reprompt_delta*100:>+9.1f}% {_dir(reprompt_delta, True):>10}"
        )
        print(
            f"  {'Avg confidence':<25} {m7['avg_confidence']:>10.3f} {m30['avg_confidence']:>10.3f} "
            f"{confidence_delta:>+10.3f} {_dir(confidence_delta, True):>10}"
        )
        print()
        print(f"Composite improvement: {report['composite_improvement']:+.2f}")
        print(f"Trend: {report['trend']}")
        print()
        if report["trend"] == "IMPROVING":
            print("the user's prompts are landing more accurately with fewer corrections.")
        elif report["trend"] == "DECLINING":
            print("Override/correction rates rising — investigate if task complexity changed.")
        else:
            print("Metrics stable. No significant skill drift detected.")
        print()

    return report


# =============================================================================
# CLI dispatch
# =============================================================================


def main() -> int:
    args = sys.argv[1:]

    # Parse flags
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    days = 30
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                pass
    # Strip --days and its numeric value, preserve all other args
    skip_next = False
    clean_args = []
    for a in args:
        if a == "--days":
            skip_next = True
            continue
        if skip_next and a.isdigit():
            skip_next = False
            continue
        skip_next = False
        clean_args.append(a)
    args = clean_args

    command = args[0] if args else "overview"

    entries = _filter_days(_read_decisions(), days)

    commands = {
        "overview": cmd_overview,
        "overrides": cmd_overrides,
        "delegation": cmd_delegation,
        "stalls": cmd_stalls,
        "progression": cmd_progression,
    }

    if command in commands:
        commands[command](entries, as_json=as_json)
        return 0
    elif command in ("help", "--help", "-h"):
        print(__doc__)
        return 0
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Available: {', '.join(commands.keys())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
