#!/usr/bin/env python3
"""
Cache-thrash detection for token-accountant.
============================================
Per-(skill, model) cache hit-rate analysis over a rolling window. Surfaces
prompts whose cache is being invalidated faster than it pays off — those are
the cache-restructure candidates.

Algorithm (per research brief Gap 2, 2026-05-02):
  1. Walk every transcript modified within `window_days` (default 7).
  2. Group turns by (skill_or_no-skill, model).
  3. Hit rate = cache_read / (cache_read + cache_create_5m + cache_create_1h
                              + input_tokens_fresh).
  4. Bayesian smoothing: shrink toward an 80% prior with weight (alpha=8 hits,
     beta=2 misses) — first-fire skills get a fair score instead of 0% on a
     single create-only turn.
  5. Status (post-smoothing):
       thrash       <0.50
       warn         <0.60
       ok          >=0.60
       below_min   reads+creates==0 AND fresh>0  (totally uncached)
       insufficient_samples fires<5 (separate flag, not lumped)
  6. Dynamic-prefix divergence (proposal-grade): for each (skill, model)
     cohort with >=3 turns, hash 256-token chunks of the cache_creation
     INPUT prefix per turn. The first chunk index where consecutive turns
     diverge is the "cache-invalidating prefix." Surfaced as a restructure
     proposal: "the first 256·N tokens are stable; bytes after that are
     where caching is being lost."

Output:
  - Markdown report on stdout.
  - Optional `proposals/cache_restructure_YYYY-MM-DD.jsonl` for downstream
    consumption (skill-curator can ingest these as low-cost-source signals).

Sacred Rule alignment:
  Rule 11: every (skill, model) row cites its sample count + window. No
           "cache rate is bad" without n + window.
  Rule 6:  proposals are diagnostic only — they suggest a prefix split, they
           do NOT auto-restructure prompts.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
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


HOME = Path.home()
TA_DIR = HOME / "Desktop" / "T1" / "Toke" / "automations" / "homer" / "token_accountant"
PROPOSALS_DIR = TA_DIR / "proposals"


# -----------------------------------------------------------------------------
# Bayesian smoothing
# -----------------------------------------------------------------------------

PRIOR_ALPHA = 8.0   # 8 prior "hits" => 80% prior weight
PRIOR_BETA = 2.0    # 2 prior "misses"


def smoothed_hit_rate(hits_tok: int, total_tok: int) -> float:
    """Bayesian-smoothed hit-rate. hits = cache_read tokens; total = denom.

    Token-weighted (not call-weighted) — a 100K-token cache hit counts more
    than a 1K-token cache hit, which matches actual $USD impact.
    """
    return (hits_tok + PRIOR_ALPHA * 1000) / (total_tok + (PRIOR_ALPHA + PRIOR_BETA) * 1000)


# -----------------------------------------------------------------------------
# Per-cohort stats
# -----------------------------------------------------------------------------


@dataclass
class CohortStats:
    skill: str
    model: str
    fires: int = 0
    fresh_input: int = 0
    cache_read: int = 0
    cache_create_5m: int = 0
    cache_create_1h: int = 0
    output: int = 0
    cost_usd: float = 0.0
    sessions: set = field(default_factory=set)
    turns: list[TranscriptTurn] = field(default_factory=list)

    @property
    def total_input_demand(self) -> int:
        return self.fresh_input + self.cache_read + self.cache_create_5m + self.cache_create_1h

    @property
    def raw_hit_rate(self) -> float:
        d = self.total_input_demand
        return (self.cache_read / d) if d else 0.0

    @property
    def smoothed_hit_rate(self) -> float:
        return smoothed_hit_rate(self.cache_read, self.total_input_demand)

    @property
    def status(self) -> str:
        if self.fires < 5:
            return "insufficient_samples"
        if self.cache_read == 0 and self.cache_create_5m == 0 and self.cache_create_1h == 0 \
                and self.fresh_input > 0:
            return "below_min"
        sr = self.smoothed_hit_rate
        if sr < 0.50:
            return "thrash"
        if sr < 0.60:
            return "warn"
        return "ok"

    def to_summary_row(self) -> dict:
        return {
            "skill": self.skill,
            "model": self.model,
            "fires": self.fires,
            "sessions": len(self.sessions),
            "raw_hit_rate": round(self.raw_hit_rate, 4),
            "smoothed_hit_rate": round(self.smoothed_hit_rate, 4),
            "status": self.status,
            "cost_usd": round(self.cost_usd, 4),
            "fresh_tok": self.fresh_input,
            "cache_read_tok": self.cache_read,
            "cache_create_5m_tok": self.cache_create_5m,
            "cache_create_1h_tok": self.cache_create_1h,
        }


# -----------------------------------------------------------------------------
# Dynamic-prefix divergence
# -----------------------------------------------------------------------------

CHUNK_TOKENS = 256          # block size for divergence detection
CHUNK_BYTES = CHUNK_TOKENS * 4   # rough 4-bytes-per-token approximation


def _chunk_hashes_from_turn(t: TranscriptTurn) -> list[str]:
    """Hash 256-token chunks of the cache-relevant prefix.

    We don't have raw text in the transcript usage object (transcript stores
    content blocks, but we only see assistant content here, not the full
    user-side prompt that drove the cache). Instead, we hash the SERIALIZED
    tool_uses + skills + model id as a cohort fingerprint per turn — turns
    in the same cohort that diverge here are doing different work even
    though they share (skill, model). This is a coarse proxy; Cycle 3 will
    upgrade to true prompt-prefix hashing when we wire SessionEnd to capture
    full prompt text.
    """
    blob = json.dumps({
        "model": t.model,
        "skills": sorted(t.skills),
        "tool_names": sorted(b.get("name", "?") for b in t.tool_uses),
    }, sort_keys=True)
    h = hashlib.md5(blob.encode("utf-8")).hexdigest()
    return [h]  # single-chunk proxy — Cycle 3 unlocks per-256-tok hashing


def divergence_proposal(stats: CohortStats) -> dict | None:
    """If turns in the cohort have diverging fingerprints, emit a proposal."""
    if len(stats.turns) < 3:
        return None
    seen: dict[str, int] = defaultdict(int)
    for t in stats.turns:
        for h in _chunk_hashes_from_turn(t):
            seen[h] += 1
    if len(seen) < 2:
        return None
    # Multiple distinct fingerprints in same (skill, model) cohort → suspect
    # prompt drift. Propose a per-tool-set split.
    return {
        "skill": stats.skill,
        "model": stats.model,
        "fires": stats.fires,
        "distinct_fingerprints": len(seen),
        "recommendation": (
            f"Cohort `({stats.skill}, {stats.model})` has {len(seen)} distinct "
            f"tool-set/skill fingerprints across {stats.fires} fires. The "
            f"cache prefix is fracturing — consider stable system-prompt "
            f"sections per fingerprint or a single canonical tool roster."
        ),
        "fingerprint_distribution": dict(sorted(seen.items(), key=lambda x: -x[1])[:5]),
    }


# -----------------------------------------------------------------------------
# Main run
# -----------------------------------------------------------------------------


def collect_cohorts(window_days: int) -> dict[tuple[str, str], CohortStats]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    cohorts: dict[tuple[str, str], CohortStats] = {}
    for path in find_all_transcripts(since_ts=cutoff):
        for t in parse_transcript(path):
            # Bucket per (skill, model). Turns with multiple skills count
            # toward each (split-evenly tokens to avoid double-counting).
            skills = t.skills or ["(no skill)"]
            n = len(skills)
            for sk in skills:
                key = (sk, t.model or "(unknown)")
                stat = cohorts.setdefault(key, CohortStats(skill=sk, model=key[1]))
                stat.fires += 1
                stat.fresh_input += t.input_tokens // n
                stat.cache_read += t.cache_read // n
                stat.cache_create_5m += t.cache_create_5m // n
                stat.cache_create_1h += t.cache_create_1h // n
                stat.output += t.output_tokens // n
                stat.cost_usd += cost_from_turn(t) / n
                stat.sessions.add(t.session_id)
                stat.turns.append(t)
    return cohorts


def run(window_days: int = 7, *, write_proposals: bool = True) -> str:
    cohorts = collect_cohorts(window_days)
    if not cohorts:
        return (f"# Cache-Thrash Report\n\n**Window:** last {window_days} days\n\n"
                "No transcripts in window.\n")

    rows = sorted(
        (c for c in cohorts.values()),
        key=lambda c: (c.smoothed_hit_rate, -c.fires),
    )

    # Status counts
    status_counts: dict[str, int] = defaultdict(int)
    for c in cohorts.values():
        status_counts[c.status] += 1

    # Proposals
    proposals = []
    for c in cohorts.values():
        if c.status in ("thrash", "warn"):
            p = divergence_proposal(c)
            if p:
                proposals.append(p)

    # Render markdown
    md = []
    md.append(f"# Cache-Thrash Report\n")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    md.append(f"**Window:** last {window_days} days "
              f"(transcripts modified since {(datetime.now(timezone.utc)-timedelta(days=window_days)).date()})\n")
    md.append(f"**Cohorts:** {len(cohorts)} distinct (skill, model) pairs\n")
    md.append("")
    md.append("## Status Distribution\n")
    for s in ("thrash", "warn", "ok", "below_min", "insufficient_samples"):
        md.append(f"- `{s}` — {status_counts.get(s, 0)} cohorts")
    md.append("")
    md.append("## Worst Offenders (smoothed hit-rate ascending)\n")
    md.append("| skill | model | fires | sessions | smoothed | raw | cost USD | status |")
    md.append("|---|---|---:|---:|---:|---:|---:|---|")
    for c in rows[:20]:
        if c.fires < 5:
            continue
        md.append(
            f"| `{c.skill}` | `{c.model}` | {c.fires} | {len(c.sessions)} | "
            f"{c.smoothed_hit_rate:.3f} | {c.raw_hit_rate:.3f} | "
            f"${c.cost_usd:.4f} | {c.status} |"
        )
    md.append("")
    if proposals:
        md.append("## Cache-Restructure Proposals\n")
        for p in proposals[:10]:
            md.append(f"- **{p['skill']} / {p['model']}** "
                      f"({p['fires']} fires, {p['distinct_fingerprints']} fingerprints): "
                      f"{p['recommendation']}")
        md.append("")
        if write_proposals:
            PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
            date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            out = PROPOSALS_DIR / f"cache_restructure_{date_part}.jsonl"
            with out.open("w", encoding="utf-8") as f:
                for p in proposals:
                    f.write(json.dumps(p) + "\n")
            md.append(f"*Proposals written: `{out}` ({len(proposals)} entries)*\n")
    md.append("\n## Methodology\n")
    md.append(f"- Smoothing: Bayesian prior with alpha={PRIOR_ALPHA}, "
              f"beta={PRIOR_BETA} (80% baseline, weight 10K tokens)")
    md.append("- Status thresholds: thrash <0.50, warn <0.60, ok >=0.60")
    md.append("- Below-min: zero cache activity, all-fresh input")
    md.append("- Insufficient: fires <5 (separate flag, not lumped into thrash)")
    md.append("- Source: transcripts via `transcript_loader` (msg.id-deduped)")
    md.append("- Pricing: `cost_model.cost_from_turn`")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="cache_thrash")
    p.add_argument("--window", type=int, default=7)
    p.add_argument("--no-proposals", action="store_true")
    args = p.parse_args(argv)
    print(run(window_days=args.window, write_proposals=not args.no_proposals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
