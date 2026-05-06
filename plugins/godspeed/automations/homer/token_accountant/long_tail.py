#!/usr/bin/env python3
"""
Long-tail $USD spike detection.
================================
Per-(skill, model) cost-per-fire histograms over a window. Flags cohorts
whose p95 cost is many multiples of p50 — the "rare but expensive" tail.

Algorithm (per research brief Gap 3, 2026-05-02):
  1. Walk transcripts modified within `window_days` (default 30).
  2. Bucket per (skill_or_no-skill, model). Each turn = one fire.
  3. Compute p50 / p95 / p99 / max of cost-per-fire per cohort.
  4. n>=30 minimum samples (binomial-CI floor) — below that, flag as
     "insufficient" rather than emit a noisy spike claim.
  5. Multi-gate filter — ALL three must hold for a spike candidate:
       ratio:    p95 >= 10 * p50
       absolute: p95 >= $0.50
       spread:   (p95 - p50) >= $0.10
  6. Spike-cause auto-diagnosis (worst case among flagged turns):
       extended_thinking_burst:    thinking_chars on this turn > 5x cohort median
       long_output_session:        output_tokens > 10x cohort median
       cache_miss_on_long_context: (input_fresh + cache_create) > 5x median
                                    AND cache_read share < 30%
       indeterminate:              none of the above
  7. Sort by spike-cost-contribution = (p95 - p50) * fire_count.

Output:
  Markdown report on stdout. Cycle 3 will additionally write proposals to
  proposals/long_tail_YYYY-MM-DD.jsonl.

Sacred Rule alignment:
  Rule 11: every cost claim cites the sample size + percentile breakdown.
  Rule 6:  diagnoses come from observed values, never invented.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_loader import (  # noqa: E402
    TranscriptTurn, find_all_transcripts, parse_transcript,
)
from cost_model import cost_from_turn  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# -----------------------------------------------------------------------------
# Gates
# -----------------------------------------------------------------------------

MIN_SAMPLES = 30
RATIO_GATE = 10.0
ABSOLUTE_GATE_USD = 0.50
SPREAD_GATE_USD = 0.10


# -----------------------------------------------------------------------------
# Stats
# -----------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (no numpy dep)."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class CohortFires:
    skill: str
    model: str
    fires: list[tuple[float, TranscriptTurn]] = field(default_factory=list)  # (cost, turn)

    def add(self, cost: float, t: TranscriptTurn) -> None:
        self.fires.append((cost, t))

    def costs(self) -> list[float]:
        return [c for c, _ in self.fires]

    def n(self) -> int:
        return len(self.fires)

    def p50(self) -> float:
        return percentile(self.costs(), 50)

    def p95(self) -> float:
        return percentile(self.costs(), 95)

    def p99(self) -> float:
        return percentile(self.costs(), 99)

    def max_cost(self) -> float:
        return max(self.costs(), default=0.0)

    def total_cost(self) -> float:
        return sum(self.costs())

    def is_insufficient(self) -> bool:
        return self.n() < MIN_SAMPLES

    def passes_gates(self) -> bool:
        if self.is_insufficient():
            return False
        p50, p95 = self.p50(), self.p95()
        return (p95 >= RATIO_GATE * max(p50, 1e-9)
                and p95 >= ABSOLUTE_GATE_USD
                and (p95 - p50) >= SPREAD_GATE_USD)

    def spike_contribution(self) -> float:
        """USD impact of the long tail: (p95 - p50) * n."""
        return (self.p95() - self.p50()) * self.n()


# -----------------------------------------------------------------------------
# Spike-cause diagnosis
# -----------------------------------------------------------------------------


def diagnose_spike(cohort: CohortFires) -> str:
    """Pick the dominant spike-cause across the top-cost turns in this cohort.

    Heuristic: examine the top-5 most-expensive turns. For each, classify the
    cause vs cohort medians. Return the most-common label (or 'indeterminate').
    """
    if not cohort.fires:
        return "indeterminate"

    median_thinking = statistics.median([t.thinking_chars for _, t in cohort.fires] or [0])
    median_output = statistics.median([t.output_tokens for _, t in cohort.fires] or [0])
    median_input_demand = statistics.median(
        [t.input_tokens + t.cache_create_5m + t.cache_create_1h
         for _, t in cohort.fires] or [0]
    )

    causes: dict[str, int] = defaultdict(int)
    for cost, t in sorted(cohort.fires, key=lambda x: -x[0])[:5]:
        thinking_burst = (t.thinking_chars > 5 * (median_thinking + 1))
        long_output = (t.output_tokens > 10 * (median_output + 1))
        new_demand = t.input_tokens + t.cache_create_5m + t.cache_create_1h
        denom_in = t.input_tokens + t.cache_read + t.cache_create_5m + t.cache_create_1h
        cache_share = (t.cache_read / denom_in) if denom_in else 0.0
        cache_miss_long = (new_demand > 5 * (median_input_demand + 1)
                           and cache_share < 0.30)

        if thinking_burst:
            causes["extended_thinking_burst"] += 1
        elif long_output:
            causes["long_output_session"] += 1
        elif cache_miss_long:
            causes["cache_miss_on_long_context"] += 1
        else:
            causes["indeterminate"] += 1

    if not causes:
        return "indeterminate"
    return max(causes.items(), key=lambda x: x[1])[0]


# -----------------------------------------------------------------------------
# Main run
# -----------------------------------------------------------------------------


def collect_cohorts(window_days: int) -> dict[tuple[str, str], CohortFires]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    cohorts: dict[tuple[str, str], CohortFires] = {}
    for path in find_all_transcripts(since_ts=cutoff):
        for t in parse_transcript(path):
            cost = cost_from_turn(t)
            skills = t.skills or ["(no skill)"]
            for sk in skills:
                key = (sk, t.model or "(unknown)")
                cohort = cohorts.setdefault(key, CohortFires(skill=sk, model=key[1]))
                cohort.add(cost / len(skills), t)
    return cohorts


def run(window_days: int = 30) -> str:
    cohorts = collect_cohorts(window_days)
    if not cohorts:
        return (f"# Long-Tail Spike Report\n\n**Window:** last {window_days} days\n\n"
                "No transcripts in window.\n")

    spikers = sorted(
        (c for c in cohorts.values() if c.passes_gates()),
        key=lambda c: -c.spike_contribution(),
    )
    insufficient = [c for c in cohorts.values() if c.is_insufficient()]
    clean = [c for c in cohorts.values()
             if not c.passes_gates() and not c.is_insufficient()]

    md = []
    md.append("# Long-Tail Spike Report\n")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    md.append(f"**Window:** last {window_days} days\n")
    md.append(f"**Cohorts:** {len(cohorts)} total | "
              f"{len(spikers)} flagged spikes | "
              f"{len(clean)} clean | "
              f"{len(insufficient)} insufficient (n<{MIN_SAMPLES})\n")
    md.append("")
    md.append("## Gate Definitions\n")
    md.append(f"- Ratio: p95 >= {RATIO_GATE:.0f} * p50")
    md.append(f"- Absolute: p95 >= ${ABSOLUTE_GATE_USD:.2f}")
    md.append(f"- Spread: (p95 - p50) >= ${SPREAD_GATE_USD:.2f}")
    md.append(f"- Sample floor: n >= {MIN_SAMPLES}")
    md.append("")
    if spikers:
        md.append("## Flagged Spike Cohorts (sorted by tail-cost contribution)\n")
        md.append("| skill | model | n | p50 | p95 | p99 | max | tail $ | cause |")
        md.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
        for c in spikers[:20]:
            cause = diagnose_spike(c)
            md.append(
                f"| `{c.skill}` | `{c.model}` | {c.n()} | "
                f"${c.p50():.4f} | ${c.p95():.4f} | ${c.p99():.4f} | "
                f"${c.max_cost():.4f} | ${c.spike_contribution():.2f} | {cause} |"
            )
        md.append("")
    else:
        md.append("## Flagged Spike Cohorts\n\n*None pass all three gates.*\n")
    if insufficient:
        md.append(f"## Insufficient Samples (n < {MIN_SAMPLES})\n")
        md.append(f"*{len(insufficient)} cohorts skipped — accumulate more fires before flagging.*\n")
        for c in sorted(insufficient, key=lambda c: -c.total_cost())[:10]:
            md.append(f"- `{c.skill}` / `{c.model}` — n={c.n()}, total=${c.total_cost():.4f}")
        md.append("")
    md.append("## Methodology\n")
    md.append("- Source: transcripts via `transcript_loader` (msg.id-deduped)")
    md.append("- Pricing: `cost_model.cost_from_turn`")
    md.append("- Percentiles: linear-interpolation, no numpy dep")
    md.append("- Cause diagnosis: top-5-by-cost turn vote vs cohort medians")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="long_tail")
    p.add_argument("--window", type=int, default=30)
    args = p.parse_args(argv)
    print(run(window_days=args.window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
