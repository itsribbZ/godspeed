#!/usr/bin/env python3
"""
Toke — Prompt Engineering Skill Assessment
============================================
Tracks the user's prompt engineering skill over time. Inspired by Workera's
skill measurement (Kian Katanforoosh) — adapted for a solo dev, not enterprise.

Commands:
    python prompt_quality.py report              Full skill report (weekly)
    python prompt_quality.py trend               30-day trend line
    python prompt_quality.py --json              Machine-readable output
    python prompt_quality.py --days N            Window size (default 30)

Metrics:
    Clarity       — avg Brain confidence (higher = clearer prompts)
    Efficiency    — tokens per task (lower = more efficient)
    Correction    — reprompt rate decay (lower = fewer corrections)
    Targeting     — 1 - override rate (higher = prompts land on right tier)
    Delegation    — % full delegation mode (higher = more trust earned)
    Composite     — weighted average of above 5 (0-100 scale)

Data source: ~/.claude/telemetry/brain/decisions.jsonl
Dependencies: Python 3.11+ stdlib only.

Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, Phase 2B.
Stolen from: Workera skill assessment model (Katanforoosh).
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

# Weights for composite score (sum to 1.0)
WEIGHTS = {
    "clarity": 0.20,
    "efficiency": 0.15,
    "correction": 0.25,
    "targeting": 0.25,
    "delegation": 0.15,
}


# =============================================================================
# Data loading
# =============================================================================


def _read_decisions() -> list[dict[str, Any]]:
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
    return entries


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


def _get_result(entry: dict) -> dict:
    r = entry.get("result", {})
    return r if isinstance(r, dict) else {}


def _is_override(entry: dict) -> bool:
    current = entry.get("current_model", "").lower()
    result = _get_result(entry)
    recommended = result.get("model", "").lower()
    if not current or not recommended:
        return False
    return recommended not in current and current not in recommended


# =============================================================================
# Skill dimension scorers (each returns 0-100)
# =============================================================================


def _score_clarity(entries: list[dict]) -> float:
    """Avg confidence * 100. Higher = Brain classifies with more certainty = clearer prompts."""
    confs = [_get_result(e).get("confidence", 0.5) for e in entries]
    if not confs:
        return 50.0
    return round(sum(confs) / len(confs) * 100, 1)


def _score_efficiency(entries: list[dict]) -> float:
    """Inverse of avg prompt token count, scaled. Lower tokens = higher score."""
    token_counts = []
    for e in entries:
        human = e.get("human", {})
        if isinstance(human, dict) and "prompt_token_count" in human:
            token_counts.append(human["prompt_token_count"])
    if not token_counts:
        return 50.0  # neutral when no data
    avg_tokens = sum(token_counts) / len(token_counts)
    # Scale: 20 tokens = 100, 200 tokens = 50, 500+ tokens = 20
    score = max(10, min(100, 120 - avg_tokens * 0.35))
    return round(score, 1)


def _score_correction(entries: list[dict]) -> float:
    """100 - (reprompt_rate * 500). Lower correction rate = higher score."""
    corrections = sum(1 for e in entries if _get_result(e).get("correction_detected_in_prompt"))
    rate = corrections / len(entries) if entries else 0
    score = max(0, min(100, 100 - rate * 500))
    return round(score, 1)


def _score_targeting(entries: list[dict]) -> float:
    """(1 - override_rate) * 100. Fewer overrides = prompts land on right tier."""
    overrides = sum(1 for e in entries if _is_override(e))
    rate = overrides / len(entries) if entries else 0
    return round((1 - rate) * 100, 1)


def _score_delegation(entries: list[dict]) -> float:
    """% of decisions in 'full' delegation mode."""
    full_count = 0
    for e in entries:
        human = e.get("human", {})
        if isinstance(human, dict) and human.get("delegation_mode") == "full":
            full_count += 1
        elif not isinstance(human, dict) or "delegation_mode" not in (human or {}):
            # Fallback
            result = _get_result(e)
            if result.get("confidence", 0) >= 0.70 and not result.get("guardrails_fired"):
                full_count += 1
    return round(full_count / len(entries) * 100, 1) if entries else 50.0


def compute_skill_report(entries: list[dict]) -> dict[str, Any]:
    """Full skill assessment."""
    if len(entries) < 5:
        return {"error": "Need at least 5 decisions for skill assessment.", "count": len(entries)}

    dimensions = {
        "clarity": _score_clarity(entries),
        "efficiency": _score_efficiency(entries),
        "correction": _score_correction(entries),
        "targeting": _score_targeting(entries),
        "delegation": _score_delegation(entries),
    }

    composite = sum(dimensions[k] * WEIGHTS[k] for k in WEIGHTS)

    # Find weakest dimension
    weakest = min(dimensions, key=dimensions.get)

    return {
        "count": len(entries),
        "dimensions": dimensions,
        "weights": WEIGHTS,
        "composite": round(composite, 1),
        "weakest": weakest,
        "strongest": max(dimensions, key=dimensions.get),
    }


# =============================================================================
# Trend analysis
# =============================================================================


def compute_trend(entries: list[dict], window_days: int = 30) -> dict[str, Any]:
    """Compare 7d vs full window for progression signal."""
    now = datetime.datetime.now(datetime.timezone.utc)
    d7 = now - datetime.timedelta(days=7)

    recent_7d = [e for e in entries if (_parse_ts(e.get("ts", "")) or now) >= d7]
    full = entries

    if len(recent_7d) < 3 or len(full) < 5:
        return {"error": "Insufficient data for trend analysis.",
                "recent_7d": len(recent_7d), "full": len(full)}

    report_7d = compute_skill_report(recent_7d)
    report_full = compute_skill_report(full)

    if "error" in report_7d or "error" in report_full:
        return {"error": "Insufficient data for one or both windows."}

    deltas = {}
    for dim in WEIGHTS:
        d7_val = report_7d["dimensions"].get(dim, 50)
        full_val = report_full["dimensions"].get(dim, 50)
        deltas[dim] = round(d7_val - full_val, 1)

    composite_delta = report_7d["composite"] - report_full["composite"]

    if composite_delta > 2:
        trend = "IMPROVING"
    elif composite_delta < -2:
        trend = "DECLINING"
    else:
        trend = "STABLE"

    return {
        "window_7d": report_7d,
        "window_full": report_full,
        "deltas": deltas,
        "composite_delta": round(composite_delta, 1),
        "trend": trend,
    }


# =============================================================================
# Commands
# =============================================================================


def cmd_report(entries: list[dict], as_json: bool = False) -> dict:
    report = compute_skill_report(entries)

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        if "error" in report:
            print(report["error"])
            return report

        today = datetime.date.today().isoformat()
        print(f"PROMPT ENGINEERING SKILL REPORT — {today}")
        print("=" * 50)
        print(f"Composite skill score: {report['composite']:.0f}/100")
        print()
        for dim, score in report["dimensions"].items():
            weight = WEIGHTS[dim]
            bar = "#" * int(score / 5)
            marker = " <-- weakest" if dim == report["weakest"] else ""
            marker = " <-- strongest" if dim == report["strongest"] else marker
            print(f"  {dim:<14} {score:>5.0f}/100  (w={weight:.2f})  {bar}{marker}")
        print()
        if report["composite"] >= 80:
            print("Expert level — prompts are clear, efficient, and well-targeted.")
        elif report["composite"] >= 60:
            print("Proficient — room to improve in the weakest dimension.")
        elif report["composite"] >= 40:
            print("Developing — multiple dimensions need attention.")
        else:
            print("Novice — significant room for improvement across the board.")
        print()

    return report


def cmd_trend(entries: list[dict], days: int = 30, as_json: bool = False) -> dict:
    trend = compute_trend(entries, days)

    if as_json:
        print(json.dumps(trend, indent=2))
    else:
        if "error" in trend:
            print(trend["error"])
            return trend

        print(f"SKILL TREND (7d vs {days}d)")
        print("=" * 50)
        print(f"  {'Dimension':<14} {'7d':>8} {f'{days}d':>8} {'Delta':>8}")
        print(f"  {'-'*14} {'-'*8} {'-'*8} {'-'*8}")
        for dim in WEIGHTS:
            v7 = trend["window_7d"]["dimensions"].get(dim, 0)
            vf = trend["window_full"]["dimensions"].get(dim, 0)
            delta = trend["deltas"].get(dim, 0)
            print(f"  {dim:<14} {v7:>7.0f} {vf:>7.0f} {delta:>+7.1f}")
        print()
        print(f"Composite: {trend['window_7d']['composite']:.0f} vs {trend['window_full']['composite']:.0f}"
              f" ({trend['composite_delta']:+.1f})")
        print(f"Trend: {trend['trend']}")
        print()

    return trend


# =============================================================================
# CLI dispatch
# =============================================================================


def main() -> int:
    args = sys.argv[1:]
    as_json = "--json" in args

    days = 30
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                pass

    clean_args = [a for a in args if a not in ("--json", "--days") and not a.isdigit()]
    command = clean_args[0] if clean_args else "report"

    entries = _filter_days(_read_decisions(), days)

    if command == "report":
        cmd_report(entries, as_json)
    elif command == "trend":
        cmd_trend(entries, days, as_json)
    elif command in ("help", "--help", "-h"):
        print(__doc__)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
