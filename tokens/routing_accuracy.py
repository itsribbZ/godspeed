#!/usr/bin/env python3
"""
Toke — routing_accuracy.py v1.0

Measures whether Brain's tier classification matches actual session complexity.

Brain classifies S0-S5 per prompt. Actual complexity is measured from the
transcript: how many tool calls fired, how many output tokens generated,
and which tools were used before the next real user message.

Routing outcomes:
  CORRECT     — tier aligns with actual complexity
  UNDER       — Brain said trivial (S0/S1), session was complex (5+ tools)
  OVER        — Brain said complex (S4/S5), session was trivial (0 tools, <500 output)
  UNKNOWN     — no matching transcript found

Usage:
  python routing_accuracy.py                # all decisions, full report
  python routing_accuracy.py --session <sid>
  python routing_accuracy.py --top <N>      # limit examples shown (default 10)
  python routing_accuracy.py --json         # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOKE_ROOT     = Path(__file__).resolve().parent.parent
DECISIONS_PATH = Path(os.path.expanduser("~/.claude/telemetry/brain/decisions.jsonl"))
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))

# ── Tier thresholds ──────────────────────────────────────────────────────────
# Maps Brain tier → (min_score, max_score) for reference display only.
TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]

# Actual complexity → synthetic tier
# 0 tools, <500 output             → "A0" (trivial)
# 0 tools, 500+ output OR 1-2 tools → "A1" (light)
# 3-4 tools                         → "A2" (moderate)
# 5-9 tools                         → "A3" (complex)
# 10-19 tools                       → "A4" (heavy)
# 20+ tools                         → "A5" (max)
ACTUAL_TIERS = ["A0", "A1", "A2", "A3", "A4", "A5"]

def actual_tier(tool_count: int, output_tokens: int) -> str:
    if tool_count == 0 and output_tokens < 500:
        return "A0"
    if tool_count == 0 or (tool_count <= 2 and output_tokens < 2000):
        return "A1"
    if tool_count <= 4:
        return "A2"
    if tool_count <= 9:
        return "A3"
    if tool_count <= 19:
        return "A4"
    return "A5"

def tier_index(tier: str) -> int:
    """Numeric index for a Brain S-tier (0-5). Returns 0 for unknown."""
    mapping = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}
    return mapping.get(tier, 0)

def actual_tier_index(at: str) -> int:
    mapping = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
    return mapping.get(at, 0)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Decision:
    ts: str
    session_id: str
    tier: str
    score: float
    model: str
    signals: dict


@dataclass
class TurnMeasure:
    tool_count: int
    output_tokens: int
    tools_used: list[str]
    actual_tier: str


@dataclass
class RoutingResult:
    decision: Decision
    measure: TurnMeasure | None
    outcome: str          # CORRECT / UNDER / OVER / MARGIN / UNKNOWN
    tier_delta: int       # actual_index - brain_index (+ = under, - = over)

    @property
    def brain_tier(self) -> str:
        return self.decision.tier

    @property
    def actual_tier_str(self) -> str:
        return self.measure.actual_tier if self.measure else "?"


# ── Transcript loader ────────────────────────────────────────────────────────

def _is_real_user_msg(msg_field: object) -> bool:
    """True if this user message content is a real human prompt (not tool_result)."""
    if isinstance(msg_field, dict):
        content = msg_field.get("content", "")
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            has_tool_result = any(
                isinstance(c, dict) and c.get("type") == "tool_result"
                for c in content
            )
            has_text = any(
                isinstance(c, dict) and c.get("type") == "text"
                for c in content
            )
            return has_text and not has_tool_result
    return False


@dataclass
class TranscriptEvent:
    """Lightweight event from a transcript: either a real user prompt or an assistant turn."""
    kind: str            # "user_prompt" | "assistant"
    ts_iso: str
    ts_epoch: float
    tools: list[str]
    output_tokens: int


def parse_transcript_events(path: Path) -> list[TranscriptEvent]:
    """
    Reads a transcript JSONL and returns ordered TranscriptEvents.
    Only emits:
      - user_prompt events for real human messages (not tool_result)
      - assistant events with usage data
    """
    events: list[TranscriptEvent] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type", "")
            ts_iso = d.get("timestamp", "")
            ts_epoch = _parse_ts(ts_iso)

            if t == "user":
                if _is_real_user_msg(d.get("message")):
                    events.append(TranscriptEvent(
                        kind="user_prompt",
                        ts_iso=ts_iso,
                        ts_epoch=ts_epoch,
                        tools=[],
                        output_tokens=0,
                    ))

            elif t == "assistant":
                msg = d.get("message", {})
                usage = msg.get("usage", {})
                if not usage:
                    continue
                out = usage.get("output_tokens", 0)
                content = msg.get("content", [])
                tool_names = [
                    b.get("name", "?")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                events.append(TranscriptEvent(
                    kind="assistant",
                    ts_iso=ts_iso,
                    ts_epoch=ts_epoch,
                    tools=tool_names,
                    output_tokens=out,
                ))
    return events


def _parse_ts(ts: str) -> float:
    """Parse ISO timestamp → Unix epoch float. Returns 0.0 on failure."""
    if not ts:
        return 0.0
    ts = ts.rstrip("Z").replace(" ", "T")
    fmts = ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
    for fmt in fmts:
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


# ── Transcript cache & finder ────────────────────────────────────────────────

_transcript_cache: dict[Path, list[TranscriptEvent]] = {}

def get_events(path: Path) -> list[TranscriptEvent]:
    if path not in _transcript_cache:
        _transcript_cache[path] = parse_transcript_events(path)
    return _transcript_cache[path]


def find_transcript(session_id: str) -> Path | None:
    """Search all project dirs for <session_id>.jsonl"""
    if not CLAUDE_PROJECTS.exists():
        return None
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        candidate = proj / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def build_transcript_index() -> dict[str, Path]:
    """Map every session_id (stem) to its transcript path."""
    idx: dict[str, Path] = {}
    if not CLAUDE_PROJECTS.exists():
        return idx
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for jf in proj.glob("*.jsonl"):
            idx[jf.stem] = jf
    return idx


# ── Measurement ──────────────────────────────────────────────────────────────

def measure_turn(
    decision: Decision,
    events: list[TranscriptEvent],
) -> TurnMeasure | None:
    """
    Given Brain's decision (with its timestamp) and the session's transcript
    events, find the assistant turn(s) that responded to the prompt and
    measure their actual complexity.

    Strategy: locate the first user_prompt event at or just before the
    decision timestamp, then accumulate all assistant events up to the next
    user_prompt event.
    """
    dec_ts = _parse_ts(decision.ts)
    if dec_ts == 0.0:
        return None

    # Find the user_prompt event closest to the decision timestamp
    # (decision fires on UserPromptSubmit — slightly before the assistant responds)
    best_user_idx = None
    best_user_dist = float("inf")
    for i, ev in enumerate(events):
        if ev.kind != "user_prompt":
            continue
        # The decision fires at the same moment the user submits the prompt
        dist = abs(ev.ts_epoch - dec_ts)
        if dist < best_user_dist:
            best_user_dist = dist
            best_user_idx = i

    # Accept matches within 60 seconds
    if best_user_idx is None or best_user_dist > 60.0:
        return None

    # Collect all assistant events after this user_prompt up to the next user_prompt
    tool_count = 0
    output_tokens = 0
    tools_set: list[str] = []
    seen_tools: set[str] = set()
    found_any = False

    for ev in events[best_user_idx + 1:]:
        if ev.kind == "user_prompt":
            break
        if ev.kind == "assistant":
            found_any = True
            tool_count += len(ev.tools)
            output_tokens += ev.output_tokens
            for tl in ev.tools:
                if tl not in seen_tools:
                    seen_tools.add(tl)
                    tools_set.append(tl)

    if not found_any:
        return None

    at = actual_tier(tool_count, output_tokens)
    return TurnMeasure(
        tool_count=tool_count,
        output_tokens=output_tokens,
        tools_used=tools_set,
        actual_tier=at,
    )


# ── Routing classification ────────────────────────────────────────────────────

def classify_outcome(brain_tier: str, measure: TurnMeasure) -> tuple[str, int]:
    """
    Returns (outcome_label, tier_delta).
    tier_delta = actual_index - brain_index (positive = under-routed, negative = over-routed)
    """
    bi = tier_index(brain_tier)
    ai = actual_tier_index(measure.actual_tier)
    delta = ai - bi

    # Hard UNDER: S0/S1 but ≥5 tool calls
    if brain_tier in ("S0", "S1") and measure.tool_count >= 5:
        return "UNDER", delta

    # Hard OVER: S4/S5 but 0 tools and <500 output
    if brain_tier in ("S4", "S5") and measure.tool_count == 0 and measure.output_tokens < 500:
        return "OVER", delta

    # Margin: 1-tier difference (acceptable)
    if abs(delta) <= 1:
        return "CORRECT", delta

    # Soft under/over by delta
    if delta >= 2:
        return "UNDER", delta
    if delta <= -2:
        return "OVER", delta

    return "CORRECT", delta


# ── Reader ────────────────────────────────────────────────────────────────────

def load_decisions(session_filter: str | None = None) -> list[Decision]:
    decisions: list[Decision] = []
    if not DECISIONS_PATH.exists():
        return decisions
    with open(DECISIONS_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = d.get("session_id", "")
            if session_filter and sid != session_filter:
                continue
            result = d.get("result", {})
            decisions.append(Decision(
                ts=d.get("ts", ""),
                session_id=sid,
                tier=result.get("tier", "?"),
                score=float(result.get("score", 0.0)),
                model=result.get("model", "?"),
                signals=result.get("signals", {}),
            ))
    return decisions


# ── Spark bar ─────────────────────────────────────────────────────────────────

def spark(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = min(width, int(round(value / max_val * width)))
    return "|" * filled + " " * (width - filled)


# ── Confusion matrix ──────────────────────────────────────────────────────────

def confusion_matrix(results: list[RoutingResult]) -> dict[tuple[str, str], int]:
    mat: dict[tuple[str, str], int] = defaultdict(int)
    for r in results:
        if r.outcome != "UNKNOWN":
            mat[(r.brain_tier, r.actual_tier_str)] += 1
    return dict(mat)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(results: list[RoutingResult], top_n: int) -> None:
    W = 82
    print("=" * W)
    print("  BRAIN ROUTING ACCURACY v1.0")
    print("=" * W)

    total = len(results)
    measured = [r for r in results if r.outcome != "UNKNOWN"]
    unknown  = [r for r in results if r.outcome == "UNKNOWN"]
    correct  = [r for r in measured if r.outcome == "CORRECT"]
    under    = [r for r in measured if r.outcome == "UNDER"]
    over     = [r for r in measured if r.outcome == "OVER"]

    if not measured:
        print("  No measurable decisions found (no matching transcripts).")
        print("=" * W)
        return

    acc = len(correct) / len(measured)
    under_rate = len(under) / len(measured)
    over_rate  = len(over)  / len(measured)

    print(f"  Decisions total:   {total:>5}")
    print(f"  Measurable:        {len(measured):>5}   (matched to transcripts)")
    print(f"  No transcript:     {len(unknown):>5}")
    print()
    print(f"  ACCURACY          {acc:>7.1%}  ({len(correct)}/{len(measured)} decisions)")
    print(f"  Under-routing     {under_rate:>7.1%}  ({len(under)} — Brain said trivial, was complex)")
    print(f"  Over-routing      {over_rate:>7.1%}  ({len(over)} — Brain said complex, was trivial)")
    print()

    # Tier distribution
    brain_dist: Counter = Counter(r.brain_tier for r in measured)
    actual_dist: Counter = Counter(r.actual_tier_str for r in measured)
    print(f"  BRAIN TIER DISTRIBUTION")
    max_bd = max(brain_dist.values()) if brain_dist else 1
    for t in TIER_ORDER:
        cnt = brain_dist.get(t, 0)
        pct = cnt / len(measured) if measured else 0
        print(f"    {t}  {cnt:>4}  {pct:>5.1%}  {spark(cnt, max_bd, 25)}")
    print()
    print(f"  ACTUAL COMPLEXITY DISTRIBUTION")
    max_ad = max(actual_dist.values()) if actual_dist else 1
    for t in ACTUAL_TIERS:
        cnt = actual_dist.get(t, 0)
        pct = cnt / len(measured) if measured else 0
        print(f"    {t}  {cnt:>4}  {pct:>5.1%}  {spark(cnt, max_ad, 25)}")
    print()

    # Confusion matrix
    mat = confusion_matrix(results)
    print(f"  CONFUSION MATRIX  (rows=Brain tier, cols=Actual complexity)")
    header = "          " + "".join(f" {t:>5}" for t in ACTUAL_TIERS)
    print(f"  {header}")
    print("  " + "-" * (len(header) + 2))
    for bt in TIER_ORDER:
        row = f"  Brain {bt}  "
        for at in ACTUAL_TIERS:
            cnt = mat.get((bt, at), 0)
            marker = f"{cnt:>5}" if cnt > 0 else "     "
            row += f" {marker}"
        row_total = sum(mat.get((bt, at), 0) for at in ACTUAL_TIERS)
        row += f"   (n={row_total})"
        print(row)
    print()

    # Under-routing examples
    if under:
        show = under[:top_n]
        print(f"  UNDER-ROUTED EXAMPLES ({len(under)} total, showing {len(show)})")
        print(f"  {'tier':>4}  {'score':>6}  {'model':<10}  {'tools':>5}  {'output':>8}  {'actual':>6}  {'delta':>5}  tools_used")
        print("  " + "-" * 78)
        for r in show:
            m = r.measure
            top_tools = ", ".join(m.tools_used[:4]) if m else ""
            print(
                f"  {r.brain_tier:>4}  {r.decision.score:>6.3f}  "
                f"{r.decision.model:<10}  {m.tool_count:>5}  {m.output_tokens:>8,}  "
                f"{r.actual_tier_str:>6}  {r.tier_delta:>+5}  {top_tools}"
            )
        print()

    # Over-routing examples
    if over:
        show = over[:top_n]
        print(f"  OVER-ROUTED EXAMPLES ({len(over)} total, showing {len(show)})")
        print(f"  {'tier':>4}  {'score':>6}  {'model':<10}  {'tools':>5}  {'output':>8}  {'actual':>6}  {'delta':>5}  session_id")
        print("  " + "-" * 78)
        for r in show:
            m = r.measure
            print(
                f"  {r.brain_tier:>4}  {r.decision.score:>6.3f}  "
                f"{r.decision.model:<10}  {m.tool_count:>5}  {m.output_tokens:>8,}  "
                f"{r.actual_tier_str:>6}  {r.tier_delta:>+5}  {r.decision.session_id[:16]}"
            )
        print()

    # Per-tier accuracy breakdown
    print(f"  PER-TIER ACCURACY BREAKDOWN")
    print(f"  {'tier':>4}  {'n':>4}  {'correct':>7}  {'under':>6}  {'over':>5}  {'acc':>6}  {'bar':<20}")
    print("  " + "-" * 62)
    for bt in TIER_ORDER:
        t_results = [r for r in measured if r.brain_tier == bt]
        if not t_results:
            continue
        t_cor = sum(1 for r in t_results if r.outcome == "CORRECT")
        t_und = sum(1 for r in t_results if r.outcome == "UNDER")
        t_ov  = sum(1 for r in t_results if r.outcome == "OVER")
        t_acc = t_cor / len(t_results)
        print(
            f"  {bt:>4}  {len(t_results):>4}  {t_cor:>7}  {t_und:>6}  {t_ov:>5}  "
            f"{t_acc:>5.1%}  {spark(t_acc, 1.0, 20)}"
        )
    print()

    # Top tier_delta outliers (worst misroutes)
    measured_sorted = sorted(measured, key=lambda r: abs(r.tier_delta), reverse=True)
    worst = [r for r in measured_sorted if abs(r.tier_delta) >= 2][:top_n]
    if worst:
        print(f"  WORST MISROUTES (|delta| >= 2, top {len(worst)})")
        print(f"  {'brain':>5}  {'actual':>6}  {'delta':>6}  {'outcome':<7}  {'tools':>5}  {'output':>8}  session_id")
        print("  " + "-" * 70)
        for r in worst:
            m = r.measure
            print(
                f"  {r.brain_tier:>5}  {r.actual_tier_str:>6}  {r.tier_delta:>+6}  "
                f"{r.outcome:<7}  {m.tool_count:>5}  {m.output_tokens:>8,}  "
                f"{r.decision.session_id[:16]}"
            )
        print()

    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Toke routing accuracy v1.0 — Brain tier vs actual session complexity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--session", type=str, default=None,
                        help="Only analyse decisions for this session_id")
    parser.add_argument("--top", type=int, default=10,
                        help="Max examples shown per section (default 10)")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    args = parser.parse_args()

    if not DECISIONS_PATH.exists():
        print(f"No decisions file at {DECISIONS_PATH}", file=sys.stderr)
        return 1

    decisions = load_decisions(session_filter=args.session)
    if not decisions:
        print("No decisions found.", file=sys.stderr)
        return 1

    # Build index of all transcripts once
    tx_index = build_transcript_index()

    results: list[RoutingResult] = []
    # Cache events per transcript path so we don't re-parse the same file
    events_cache: dict[str, list[TranscriptEvent]] = {}

    for dec in decisions:
        sid = dec.session_id
        tx_path = tx_index.get(sid)

        if tx_path is None:
            results.append(RoutingResult(
                decision=dec,
                measure=None,
                outcome="UNKNOWN",
                tier_delta=0,
            ))
            continue

        if sid not in events_cache:
            events_cache[sid] = parse_transcript_events(tx_path)
        events = events_cache[sid]

        measure = measure_turn(dec, events)
        if measure is None:
            results.append(RoutingResult(
                decision=dec,
                measure=None,
                outcome="UNKNOWN",
                tier_delta=0,
            ))
            continue

        outcome, delta = classify_outcome(dec.tier, measure)
        results.append(RoutingResult(
            decision=dec,
            measure=measure,
            outcome=outcome,
            tier_delta=delta,
        ))

    if args.json:
        out = []
        for r in results:
            entry: dict = {
                "session_id": r.decision.session_id,
                "ts": r.decision.ts,
                "brain_tier": r.brain_tier,
                "brain_score": r.decision.score,
                "brain_model": r.decision.model,
                "outcome": r.outcome,
                "tier_delta": r.tier_delta,
            }
            if r.measure:
                entry["tool_count"] = r.measure.tool_count
                entry["output_tokens"] = r.measure.output_tokens
                entry["tools_used"] = r.measure.tools_used
                entry["actual_tier"] = r.measure.actual_tier
            out.append(entry)
        print(json.dumps(out, indent=2))
        return 0

    print_report(results, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
