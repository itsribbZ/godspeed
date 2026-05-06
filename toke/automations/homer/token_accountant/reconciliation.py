#!/usr/bin/env python3
"""
Predicted-vs-actual cost reconciliation.
========================================
Joins decisions.jsonl (Brain tier prediction) → transcript actual cost. Flags
decisions whose ACTUAL cost exceeded TIER-PREDICTED cost by >2x — these are
the routing-failure signals (Brain said S0, reality cost like S4).

Algorithm (per Phase 2 resume target #5, 2026-05-02):
  1. Read last N decisions from decisions.jsonl (default 100).
  2. For each decision, find the transcript turns within the same session
     whose ts is in [decision.ts, next_decision.ts) — those are the turns
     that responded to that prompt.
  3. Sum cost across those turns -> actual_cost.
  4. Tier-predicted cost: 30K context / 90% cache / 500 output baseline
     (cost_model.tier_predicted_cost_per_call). Conservative — only flags
     >2x deviations, not "spent more than Haiku would."
  5. Output:
       - One row per decision: tier, predicted, actual, ratio, flag.
       - Aggregate: total predicted, total actual, drift summary.
       - Top-10 worst-ratio decisions (most under-routed).

Flags:
    OK            actual <= 2x predicted
    DRIFT         actual > 2x predicted (the routing missed)
    NO_TURNS      decision had no transcript turns (rare — pre-tool-use exit)

Sacred Rule alignment:
  Rule 11: every flag cites decision_id + actual cost + predicted cost + ratio.
  Rule 1:  reports drift truthfully — never softens "the routing missed."
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_loader import (  # noqa: E402
    TranscriptTurn, find_transcript, parse_transcript, load_session_turns,
)
from cost_model import (  # noqa: E402
    cost_from_turn, tier_predicted_cost_per_call, alias_for_tier,
    tier_baseline_from_observed,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


HOME = Path.home()
DECISIONS_FILE = HOME / ".claude" / "telemetry" / "brain" / "decisions.jsonl"
DRIFT_THRESHOLD = 2.0   # actual > 2x predicted = DRIFT


# -----------------------------------------------------------------------------
# Decision walker
# -----------------------------------------------------------------------------


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def load_recent_decisions(last_n: int) -> list[dict]:
    if not DECISIONS_FILE.exists():
        return []
    out: list[dict] = []
    with DECISIONS_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("hook") == "UserPromptSubmit":
                    out.append(d)
            except (json.JSONDecodeError, ValueError):
                continue
    return out[-last_n:]


# -----------------------------------------------------------------------------
# Session-bound turn lookup with caching
# -----------------------------------------------------------------------------


_SESSION_CACHE: dict[str, list[TranscriptTurn]] = {}


def _session_turns(session_id: str) -> list[TranscriptTurn]:
    if session_id in _SESSION_CACHE:
        return _SESSION_CACHE[session_id]
    turns = load_session_turns(session_id)
    _SESSION_CACHE[session_id] = turns
    return turns


def turns_for_decision(decision: dict, next_decision_ts: str | None) -> list[TranscriptTurn]:
    """Turns in [decision.ts, next_decision.ts) within the same session."""
    sid = decision.get("session_id", "")
    if not sid:
        return []
    start = _parse_iso(decision.get("ts", ""))
    if start is None:
        return []
    end = _parse_iso(next_decision_ts) if next_decision_ts else None

    out = []
    for t in _session_turns(sid):
        t_dt = _parse_iso(t.ts)
        if t_dt is None:
            continue
        if t_dt < start:
            continue
        if end is not None and t_dt >= end:
            continue
        out.append(t)
    return out


# -----------------------------------------------------------------------------
# Reconciliation row
# -----------------------------------------------------------------------------


@dataclass
class ReconRow:
    decision_id: str
    session_id: str
    ts: str
    tier: str
    predicted_cost: float
    actual_cost: float
    n_turns: int
    flag: str
    prompt_preview: str

    @property
    def ratio(self) -> float:
        return (self.actual_cost / self.predicted_cost) if self.predicted_cost else 0.0


def build_recon(last_n: int = 100) -> tuple[list[ReconRow], str]:
    """Returns (rows, baseline_source).

    baseline_source = "observed-median" when we had enough samples to compute
    per-tier medians from the actual decision-driven costs; "conservative-default"
    when we fell back to the 30K/90%/500 fixed baseline.
    """
    decs = load_recent_decisions(last_n)
    if not decs:
        return [], "no-data"
    # Build per-session sorted index for next-ts lookup
    by_session: dict[str, list[dict]] = defaultdict(list)
    for d in decs:
        by_session[d.get("session_id", "")].append(d)
    for sid in by_session:
        by_session[sid].sort(key=lambda d: d.get("ts", ""))

    # PASS 1: gather actual costs per tier so we can compute observed median.
    # We do this in two passes so the baseline reflects the same cohort.
    actuals_by_tier: dict[str, list[float]] = defaultdict(list)
    decision_meta: list[tuple[dict, str | None, list[TranscriptTurn]]] = []
    for d in decs:
        sid = d.get("session_id", "")
        sib = by_session.get(sid, [])
        d_ts = d.get("ts", "")
        next_ts = None
        for s in sib:
            if s.get("ts", "") > d_ts:
                next_ts = s.get("ts", "")
                break
        turns = turns_for_decision(d, next_ts)
        actual = sum(cost_from_turn(t) for t in turns)
        result = d.get("result") or {}
        tier = (result.get("tier") or "S0").upper()
        if turns:  # Only collect when there's signal
            actuals_by_tier[tier].append(actual)
        decision_meta.append((d, next_ts, turns))

    # PASS 2: emit ReconRow with dynamic baseline per tier.
    # Source label is whichever fallback fires for the most-common tier.
    source_labels: dict[str, int] = defaultdict(int)
    rows: list[ReconRow] = []
    for d, next_ts, turns in decision_meta:
        sid = d.get("session_id", "")
        d_ts = d.get("ts", "")
        actual = sum(cost_from_turn(t) for t in turns)
        result = d.get("result") or {}
        tier = (result.get("tier") or "S0").upper()
        predicted, src = tier_baseline_from_observed(actuals_by_tier, tier)
        source_labels[src] += 1

        if not turns:
            flag = "NO_TURNS"
        elif predicted > 0 and actual > DRIFT_THRESHOLD * predicted:
            flag = "DRIFT"
        else:
            flag = "OK"

        rows.append(ReconRow(
            decision_id=d.get("decision_id", "") or f"_legacy_{d_ts}",
            session_id=sid,
            ts=d_ts,
            tier=tier,
            predicted_cost=predicted,
            actual_cost=actual,
            n_turns=len(turns),
            flag=flag,
            prompt_preview=(d.get("prompt_text") or "")[:60],
        ))
    dominant_src = (max(source_labels.items(), key=lambda x: x[1])[0]
                    if source_labels else "conservative-default")
    return rows, dominant_src


# -----------------------------------------------------------------------------
# Render
# -----------------------------------------------------------------------------


def run(last_n: int = 100) -> str:
    rows, baseline_src = build_recon(last_n)
    if not rows:
        return f"# Predicted-vs-Actual Reconciliation\n\nNo decisions found.\n"

    n_total = len(rows)
    n_drift = sum(1 for r in rows if r.flag == "DRIFT")
    n_no_turns = sum(1 for r in rows if r.flag == "NO_TURNS")
    n_ok = sum(1 for r in rows if r.flag == "OK")

    total_pred = sum(r.predicted_cost for r in rows)
    total_actual = sum(r.actual_cost for r in rows)
    aggregate_ratio = (total_actual / total_pred) if total_pred else 0.0

    # Per-tier rollup
    by_tier: dict[str, list[ReconRow]] = defaultdict(list)
    for r in rows:
        by_tier[r.tier].append(r)

    md = []
    md.append("# Predicted-vs-Actual Reconciliation\n")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    md.append(f"**Decisions analyzed:** {n_total} (last {last_n})\n")
    md.append("")
    md.append("## Headline\n")
    md.append(f"- Total predicted: **${total_pred:.4f}**")
    md.append(f"- Total actual:    **${total_actual:.4f}**")
    md.append(f"- Aggregate ratio: **{aggregate_ratio:.2f}x** "
              f"(baseline: {baseline_src} — see Methodology)")
    md.append("")
    md.append("## Flag Distribution\n")
    md.append(f"- `OK` — {n_ok} decisions ({n_ok/n_total*100:.0f}%)")
    md.append(f"- `DRIFT` — {n_drift} decisions ({n_drift/n_total*100:.0f}%) "
              f"(actual > {DRIFT_THRESHOLD}x predicted)")
    md.append(f"- `NO_TURNS` — {n_no_turns} decisions ({n_no_turns/n_total*100:.0f}%) "
              f"(decision logged but no assistant turns followed)")
    md.append("")
    md.append("## Per-Tier Rollup\n")
    md.append("| tier | model | n | total predicted | total actual | ratio | drift count |")
    md.append("|---|---|---:|---:|---:|---:|---:|")
    for tier in ("S0", "S1", "S2", "S3", "S4", "S5"):
        bucket = by_tier.get(tier) or []
        if not bucket:
            continue
        pred = sum(r.predicted_cost for r in bucket)
        actual = sum(r.actual_cost for r in bucket)
        drift = sum(1 for r in bucket if r.flag == "DRIFT")
        ratio = (actual / pred) if pred else 0.0
        md.append(f"| {tier} | `{alias_for_tier(tier)}` | {len(bucket)} | "
                  f"${pred:.4f} | ${actual:.4f} | {ratio:.2f}x | {drift} |")
    md.append("")
    md.append("## Top-10 Worst Ratios (DRIFT-flagged)\n")
    drifters = sorted([r for r in rows if r.flag == "DRIFT"], key=lambda r: -r.ratio)
    if not drifters:
        md.append("*No DRIFT-flagged decisions.*\n")
    else:
        md.append("| decision_id | tier | predicted | actual | ratio | turns | prompt |")
        md.append("|---|---|---:|---:|---:|---:|---|")
        for r in drifters[:10]:
            md.append(
                f"| `{r.decision_id[:24]}` | {r.tier} | ${r.predicted_cost:.4f} | "
                f"${r.actual_cost:.4f} | {r.ratio:.1f}x | {r.n_turns} | "
                f"`{r.prompt_preview}` |"
            )
    md.append("")
    md.append("## Methodology\n")
    md.append(f"- Drift threshold: actual > {DRIFT_THRESHOLD}x predicted")
    md.append(f"- Baseline source: **{baseline_src}** "
              f"(`observed-median` = per-tier median of actual cost across this "
              f"window's decisions, n>=10 floor; `conservative-default` = 30K/90%/500 "
              f"fixed baseline as fallback)")
    md.append("- Predicted-cost source: `cost_model.tier_baseline_from_observed`")
    md.append("- Turn ownership: in [decision.ts, next_decision.ts) within same session")
    md.append("- Source: transcripts via `transcript_loader` (msg.id-deduped)")
    md.append("- Caveat: NO_TURNS rows are typically the most-recent decision "
              "in a still-open session, or pre-tool-use exits. Not a routing failure.")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="reconciliation")
    p.add_argument("--last", type=int, default=100)
    args = p.parse_args(argv)
    print(run(last_n=args.last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
