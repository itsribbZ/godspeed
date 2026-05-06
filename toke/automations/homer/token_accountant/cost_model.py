#!/usr/bin/env python3
"""
Cost model for token-accountant.
================================
Loads $USD pricing from `automations/brain/routing_manifest.toml` (the same
single source of truth `per_turn_breakdown.py` uses) and computes per-turn
$USD cost from a TranscriptTurn or a raw usage dict.

Why this file exists:
- per_turn_breakdown.py:46-68 already has price_for_model + load_manifest_prices.
- We DELIBERATELY do NOT import it: per_turn_breakdown.py iterates the
  transcript without msg.id dedupe (overcount bug, see transcript_loader.py
  module docstring). Re-using its parser would inherit the overcount.
- Reimplementing the pricing helpers here is small (~25 LOC) and lets us
  evolve token-accountant pricing rules independently (e.g. cache-write split
  fix from PRICING_NOTES.md §"Cache write formula" without touching
  per_turn_breakdown.py).

Cache-write pricing fix (per PRICING_NOTES.md):
    5m cache write = base_input × 1.25
    1h cache write = base_input × 2.00
    per_turn_breakdown.py:156-157 split these correctly already.
    brain_cli.py _price_model uses 1.25 uniformly — undercounts 1h writes by
    37.5%. We use the correct split here.

Sacred Rule alignment:
    Rule 2: read-only — pricing is loaded once, never written back.
    Rule 11: every $USD figure cites this module + manifest TOML version.
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


HOME = Path.home()
MANIFEST_PATH = HOME / "Desktop" / "T1" / "Toke" / "automations" / "brain" / "routing_manifest.toml"


# -----------------------------------------------------------------------------
# Pricing loader
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PricePoint:
    """$USD per million tokens. Manifest is the source of truth."""
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float

    @property
    def cache_write_5m_per_mtok(self) -> float:
        return self.input_per_mtok * 1.25

    @property
    def cache_write_1h_per_mtok(self) -> float:
        return self.input_per_mtok * 2.00


def _load_manifest_models(path: Path = MANIFEST_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            m = tomllib.load(f)
        return m.get("models", {}) or {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _alias_for_model(model_id: str) -> str:
    """Map raw model id (e.g. 'claude-opus-4-7') → manifest alias.

    Manifest aliases (per routing_manifest.toml [models.*]):
        haiku, sonnet, opus, opus[1m]
    """
    mid = (model_id or "").lower()
    if "opus" in mid and "1m" in mid:
        return "opus[1m]"
    if "opus" in mid:
        return "opus"
    if "sonnet" in mid:
        return "sonnet"
    if "haiku" in mid:
        return "haiku"
    return "unknown"


# Lazy singleton — load once per process (manifest doesn't change mid-run)
_PRICE_CACHE: dict[str, PricePoint] | None = None


def _get_prices() -> dict[str, PricePoint]:
    global _PRICE_CACHE
    if _PRICE_CACHE is not None:
        return _PRICE_CACHE
    raw = _load_manifest_models()
    out: dict[str, PricePoint] = {}
    for alias, cfg in raw.items():
        try:
            out[alias] = PricePoint(
                input_per_mtok=float(cfg.get("cost_input_per_mtok", 5.0)),
                output_per_mtok=float(cfg.get("cost_output_per_mtok", 25.0)),
                cache_read_per_mtok=float(cfg.get("cost_cache_read_per_mtok", 0.50)),
            )
        except (TypeError, ValueError):
            continue
    # Conservative defaults (Opus 4.6) for unknown alias
    if "unknown" not in out:
        out["unknown"] = PricePoint(5.0, 25.0, 0.50)
    _PRICE_CACHE = out
    return out


def price_for(model_id: str) -> PricePoint:
    """Return the PricePoint for any raw model id."""
    return _get_prices().get(_alias_for_model(model_id)) or _get_prices()["unknown"]


# -----------------------------------------------------------------------------
# Cost calc
# -----------------------------------------------------------------------------


def cost_from_usage(
    *,
    model_id: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
) -> float:
    """Compute $USD for one turn given its usage envelope."""
    p = price_for(model_id)
    return (
        (input_tokens / 1_000_000) * p.input_per_mtok
        + (output_tokens / 1_000_000) * p.output_per_mtok
        + (cache_read / 1_000_000) * p.cache_read_per_mtok
        + (cache_create_5m / 1_000_000) * p.cache_write_5m_per_mtok
        + (cache_create_1h / 1_000_000) * p.cache_write_1h_per_mtok
    )


def cost_from_turn(turn) -> float:  # accepts TranscriptTurn (duck-typed)
    """Compute $USD for a TranscriptTurn."""
    return cost_from_usage(
        model_id=turn.model,
        input_tokens=turn.input_tokens,
        cache_read=turn.cache_read,
        cache_create_5m=turn.cache_create_5m,
        cache_create_1h=turn.cache_create_1h,
        output_tokens=turn.output_tokens,
    )


def cost_breakdown(turn) -> dict[str, float]:
    """Per-component $USD breakdown (useful for spike-cause diagnosis)."""
    p = price_for(turn.model)
    return {
        "input_usd": (turn.input_tokens / 1_000_000) * p.input_per_mtok,
        "output_usd": (turn.output_tokens / 1_000_000) * p.output_per_mtok,
        "cache_read_usd": (turn.cache_read / 1_000_000) * p.cache_read_per_mtok,
        "cache_write_5m_usd": (turn.cache_create_5m / 1_000_000) * p.cache_write_5m_per_mtok,
        "cache_write_1h_usd": (turn.cache_create_1h / 1_000_000) * p.cache_write_1h_per_mtok,
    }


# -----------------------------------------------------------------------------
# Tier prediction (for predicted-vs-actual reconciliation)
# -----------------------------------------------------------------------------
#
# Brain tiers map to model expectations (per routing_manifest.toml [tiers.*]):
#     S0 → haiku
#     S1 → haiku
#     S2 → sonnet
#     S3 → sonnet (extended thinking)
#     S4 → opus
#     S5 → opus (extended thinking)
#
# This is a CHEAP heuristic for the reconciliation report — flags decisions
# whose ACTUAL model didn't match the tier-recommended one (cache-misalignment
# or routing failure indicator).

TIER_TO_ALIAS = {
    "S0": "haiku",
    "S1": "haiku",
    "S2": "sonnet",
    "S3": "sonnet",
    "S4": "opus",
    "S5": "opus",
}


def alias_for_tier(tier: str) -> str:
    return TIER_TO_ALIAS.get((tier or "").upper(), "opus")


def tier_predicted_cost_per_call(tier: str, *, est_input: int = 30_000,
                                 est_output: int = 500,
                                 cache_hit_rate: float = 0.90) -> float:
    """Rough predicted-cost-per-call for a tier (used as a flag threshold).

    Defaults assume a cached steady-state turn: 30K input total with 90%
    coming from cache (~3K fresh + 27K cache_read), 500 output. These are
    deliberately conservative — the reconciliation report flags decisions
    whose actual cost is >2× this prediction, NOT decisions that simply
    spent more than a Haiku turn would.

    NOTE: For Toke godspeed sessions (200K+ context), use the dynamic
    `tier_baseline_from_observed()` instead — see Cycle 3 fix for the
    misleading 92× aggregate ratio that came from this conservative default.
    """
    alias = alias_for_tier(tier)
    p = _get_prices().get(alias) or _get_prices()["unknown"]
    fresh = int(est_input * (1 - cache_hit_rate))
    cached = int(est_input * cache_hit_rate)
    return (
        (fresh / 1_000_000) * p.input_per_mtok
        + (cached / 1_000_000) * p.cache_read_per_mtok
        + (est_output / 1_000_000) * p.output_per_mtok
    )


# -----------------------------------------------------------------------------
# Dynamic per-tier baseline (Cycle 3 fix for misleading aggregate ratio)
# -----------------------------------------------------------------------------


def tier_baseline_from_observed(tier_costs: dict[str, list[float]],
                                tier: str) -> tuple[float, str]:
    """Return (baseline_usd, source_label) for a tier given observed cost data.

    `tier_costs[tier]` is the list of actual transcript-derived turn costs
    that drove decisions of that tier. We use the MEDIAN as the baseline —
    drift = "actual > 2x median for this tier" is a much sharper signal than
    "actual > 2x conservative-30K-baseline."

    Falls back to the conservative tier_predicted_cost_per_call when there
    are insufficient samples (n<10) — first-time-running case.

    Returns:
        (baseline_usd, source) where source ∈ {"observed-median", "conservative-default"}
    """
    samples = tier_costs.get(tier, []) or []
    n = len(samples)
    if n >= 10:
        # Median across observed turns of this tier
        s = sorted(samples)
        median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
        # Floor at the conservative default — we don't want a zero-baseline
        # if every prior turn happened to be cheap.
        floor = tier_predicted_cost_per_call(tier)
        return (max(median, floor), "observed-median")
    return (tier_predicted_cost_per_call(tier), "conservative-default")


# -----------------------------------------------------------------------------
# CLI (smoke / inspection)
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="cost_model")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="show loaded prices")

    p_calc = sub.add_parser("calc", help="calc $USD for a usage envelope")
    p_calc.add_argument("--model", required=True)
    p_calc.add_argument("--input", type=int, default=0)
    p_calc.add_argument("--cache-read", type=int, default=0)
    p_calc.add_argument("--cw5", type=int, default=0)
    p_calc.add_argument("--cw1", type=int, default=0)
    p_calc.add_argument("--output", type=int, default=0)

    p_tier = sub.add_parser("tier-predict", help="predicted-cost-per-call for a tier")
    p_tier.add_argument("--tier", required=True)
    p_tier.add_argument("--input", type=int, default=30_000)
    p_tier.add_argument("--output", type=int, default=500)
    p_tier.add_argument("--cache-hit", type=float, default=0.90)

    args = p.parse_args(argv)

    if args.cmd == "show":
        for alias, pp in _get_prices().items():
            print(f"{alias:12s}  in=${pp.input_per_mtok:>5.2f}/Mtok  "
                  f"out=${pp.output_per_mtok:>6.2f}/Mtok  "
                  f"cache_read=${pp.cache_read_per_mtok:>4.2f}/Mtok  "
                  f"cw5=${pp.cache_write_5m_per_mtok:>5.2f}/Mtok  "
                  f"cw1=${pp.cache_write_1h_per_mtok:>5.2f}/Mtok")
        return 0

    if args.cmd == "calc":
        c = cost_from_usage(
            model_id=args.model,
            input_tokens=args.input,
            cache_read=args.cache_read,
            cache_create_5m=args.cw5,
            cache_create_1h=args.cw1,
            output_tokens=args.output,
        )
        print(f"${c:.6f}")
        return 0

    if args.cmd == "tier-predict":
        c = tier_predicted_cost_per_call(
            args.tier, est_input=args.input,
            est_output=args.output, cache_hit_rate=args.cache_hit,
        )
        print(f"tier={args.tier} predicted=${c:.6f}/call")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
