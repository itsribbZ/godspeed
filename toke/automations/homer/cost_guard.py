#!/usr/bin/env python3
"""
cost_guard.py — Phase 3i godspeed cost / efficiency guard.

Maps Brain severity tiers (S0-S5) to USD budget ceilings, gates live agent
invocations so unbounded iter loops can't quietly burn $5+, and writes
post-flight efficiency receipts to ~/.claude/telemetry/brain/cost_efficiency.jsonl.

Three concerns:
1. Pre-flight: route_full stamps budget_usd + tier on the dispatch envelope so
   the caller sees the cost contract before invocation.
2. Mid-flight: invoke_live calls is_breach(running_cost, budget_usd) after each
   tool-use iteration; on breach it aborts with verdict=BUDGET_EXCEEDED.
3. Post-flight: invoke() builds a CostReceipt and appends it to the JSONL log
   for downstream rollups (aurora, dashboard, learnings ROI).

Sacred Rule alignment:
- Rule 1 (truthful): caps based on actual measured cost, no estimation drift
- Rule 2 (non-destructive): receipts append-only, never delete telemetry
- Rule 4 (only-asked): does NOT auto-escalate or auto-downgrade tier;
  surfaces BUDGET_EXCEEDED and lets caller decide whether to retry on a
  bigger budget or downgrade to a cheaper agent
- Rule 9 (no options): single canonical budget table, deterministic gate
- Rule 11 (AAA): every receipt row reproducible — same input yields same row
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Budget table: Brain tier → USD ceiling per agent invocation.
# Calibrated against LIVE Sonnet receipts 2026-05-02..03 in agent_invocations.jsonl:
#   urania $0.0190 (no tools), $0.0274 (PASS w/ tools), $0.0571 (Windows shell bug),
#   $0.1169 (max_iter cap hit at 8) — S3 ceiling at $0.50 covers worst-case 4×.
# Opus budgets sized for extended-thinking + multi-step blueprints (S4-S5).
TIER_BUDGETS_USD = {
    "S0": 0.005,   # trivial chit-chat / status check (Haiku-class)
    "S1": 0.020,   # quick lookup / single tool call (Haiku-class)
    "S2": 0.100,   # Sonnet research-agent call (urania $0.0274 verified)
    "S3": 0.500,   # Sonnet multi-iteration tool loop
    "S4": 2.000,   # Opus extended-thinking architecture work
    "S5": 5.000,   # Opus deep-synthesis multi-step blueprint
}

# Soft-cap multiplier — invocations abort when running cost crosses this
# fraction of budget. 1.5× lets normal variance through but catches runaway
# tool loops (e.g. agent stuck calling bash 12× at $0.05/iter on Sonnet).
BUDGET_BREACH_MULTIPLIER = 1.5

# Default tier inference from agent model when caller doesn't pass tier explicitly.
MODEL_TO_TIER_DEFAULT = {
    "haiku":  "S1",
    "sonnet": "S2",
    "opus":   "S4",
}

RECEIPT_PATH = Path.home() / ".claude" / "telemetry" / "brain" / "cost_efficiency.jsonl"


def budget_for_tier(tier: str | None) -> float:
    """Return USD budget ceiling for a Brain tier; defaults to S2 if unknown/None."""
    if not tier:
        return TIER_BUDGETS_USD["S2"]
    return TIER_BUDGETS_USD.get(tier.upper(), TIER_BUDGETS_USD["S2"])


def tier_for_model(model: str | None) -> str:
    """Best-effort tier when no Brain/Director tier is supplied."""
    if not model:
        return "S2"
    return MODEL_TO_TIER_DEFAULT.get(model.lower(), "S2")


def is_breach(running_cost_usd: float, budget_usd: float) -> bool:
    """Return True if running cost has crossed the breach threshold.

    Threshold is rounded to 6 decimals so e.g. 0.15 clearly meets the
    1.5× S2 boundary instead of getting clipped by float multiplication
    drift (0.1 * 1.5 == 0.15000000000000002 in IEEE-754).
    """
    if budget_usd <= 0:
        return False
    threshold = round(budget_usd * BUDGET_BREACH_MULTIPLIER, 6)
    return running_cost_usd >= threshold


@dataclass
class CostReceipt:
    """One post-flight cost-efficiency receipt."""
    ts: str
    session_id: str
    agent: str
    tier: str
    budget_usd: float
    actual_cost_usd: float
    iterations: int
    cache_hit_rate: Optional[float]
    verdict: str
    breach: bool
    efficiency_ratio: float  # actual / budget — < 1.0 = under, ≥ 1.5 = breach
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "session_id": self.session_id,
            "agent": self.agent,
            "tier": self.tier,
            "budget_usd": round(self.budget_usd, 6),
            "actual_cost_usd": round(self.actual_cost_usd, 6),
            "iterations": self.iterations,
            "cache_hit_rate": (
                round(self.cache_hit_rate, 4) if self.cache_hit_rate is not None else None
            ),
            "verdict": self.verdict,
            "breach": self.breach,
            "efficiency_ratio": round(self.efficiency_ratio, 4),
            "notes": self.notes,
        }


def build_receipt(
    *,
    agent: str,
    tier: str,
    actual_cost_usd: float,
    iterations: int,
    cache_hit_rate: Optional[float] = None,
    verdict: str = "UNKNOWN",
    session_id: Optional[str] = None,
    notes: Optional[list[str]] = None,
) -> CostReceipt:
    """Build a CostReceipt with computed fields filled in."""
    budget = budget_for_tier(tier)
    breach = is_breach(actual_cost_usd, budget)
    ratio = (actual_cost_usd / budget) if budget > 0 else 0.0
    return CostReceipt(
        ts=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        session_id=session_id or os.environ.get("CLAUDE_SESSION_ID", ""),
        agent=agent,
        tier=tier,
        budget_usd=budget,
        actual_cost_usd=actual_cost_usd,
        iterations=iterations,
        cache_hit_rate=cache_hit_rate,
        verdict=verdict,
        breach=breach,
        efficiency_ratio=ratio,
        notes=list(notes or []),
    )


def write_receipt(receipt: CostReceipt) -> bool:
    """Append one receipt row. Non-blocking — returns False on any failure."""
    try:
        RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECEIPT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(receipt.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def load_receipts(limit: int = 0) -> list[dict]:
    """Read receipts (newest first if limit>0). Returns empty list on missing file."""
    if not RECEIPT_PATH.exists():
        return []
    with open(RECEIPT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if limit > 0:
        rows = rows[-limit:]
    return rows


def rollup_efficiency(rows: Optional[list[dict]] = None) -> dict:
    """Aggregate receipts into a summary: total spend, breach rate, avg efficiency."""
    rows = rows if rows is not None else load_receipts()
    if not rows:
        return {
            "receipt_count": 0,
            "total_actual_usd": 0.0,
            "total_budget_usd": 0.0,
            "breach_count": 0,
            "breach_rate": 0.0,
            "avg_efficiency_ratio": 0.0,
            "by_agent": {},
            "by_tier": {},
        }
    total_actual = sum(r.get("actual_cost_usd", 0.0) for r in rows)
    total_budget = sum(r.get("budget_usd", 0.0) for r in rows)
    breach_count = sum(1 for r in rows if r.get("breach"))
    avg_ratio = sum(r.get("efficiency_ratio", 0.0) for r in rows) / len(rows)

    by_agent: dict[str, dict] = {}
    by_tier: dict[str, dict] = {}
    for r in rows:
        agent = r.get("agent", "?")
        tier = r.get("tier", "?")
        for bucket, key in ((by_agent, agent), (by_tier, tier)):
            entry = bucket.setdefault(key, {
                "fires": 0, "actual_usd": 0.0, "budget_usd": 0.0, "breaches": 0,
            })
            entry["fires"] += 1
            entry["actual_usd"] += r.get("actual_cost_usd", 0.0)
            entry["budget_usd"] += r.get("budget_usd", 0.0)
            if r.get("breach"):
                entry["breaches"] += 1

    for entry in (*by_agent.values(), *by_tier.values()):
        entry["actual_usd"] = round(entry["actual_usd"], 6)
        entry["budget_usd"] = round(entry["budget_usd"], 6)

    return {
        "receipt_count": len(rows),
        "total_actual_usd": round(total_actual, 6),
        "total_budget_usd": round(total_budget, 6),
        "breach_count": breach_count,
        "breach_rate": round(breach_count / len(rows), 4),
        "avg_efficiency_ratio": round(avg_ratio, 4),
        "by_agent": by_agent,
        "by_tier": by_tier,
    }


def cache_hit_rate(input_tokens: int, cache_read_tokens: int, cache_creation_tokens: int) -> Optional[float]:
    """Same definition as agent_runner.telemetry_rollup — kept here so cost_guard
    is standalone without importing back into agent_runner (avoid circular import).
    """
    denom = input_tokens + cache_read_tokens + cache_creation_tokens
    if denom <= 0:
        return None
    return cache_read_tokens / denom


# CLI for ad-hoc inspection.
def _main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="cost_guard")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("budgets", help="Print the tier→USD budget table")
    sub.add_parser("rollup", help="Aggregate cost_efficiency.jsonl receipts")
    pr = sub.add_parser("recent", help="Show most-recent N receipts")
    pr.add_argument("--n", type=int, default=10)

    args = p.parse_args(argv[1:])
    if args.cmd == "budgets":
        print(f"{'Tier':<6} {'Budget USD':>12}  {'Breach @':>12}")
        for t, b in TIER_BUDGETS_USD.items():
            print(f"{t:<6} ${b:>11,.4f}  ${b * BUDGET_BREACH_MULTIPLIER:>11,.4f}")
        return 0
    if args.cmd == "rollup":
        roll = rollup_efficiency()
        print(json.dumps(roll, indent=2))
        return 0
    if args.cmd == "recent":
        rows = load_receipts(limit=args.n)
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
