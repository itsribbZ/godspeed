#!/usr/bin/env python3
"""
Brain vs Baselines — Portfolio Benchmark (2026-04-17)
======================================================
Measures Brain's golden_set accuracy against four baseline classifiers:

  1. Brain (the classifier we ship)
  2. Length-only heuristic — naive "longer prompts = harder"
  3. Keyword-only heuristic — tier by simple lexical rules (no weighting, no guardrails)
  4. Random — uniform over {S0..S5}
  5. Majority-class — always predict the most common tier in training (S3)

Same 200-prompt golden_set. Same scoring rules. Receipts only — no vendors, no API.

Why this exists: the saleability assessment flagged "no benchmark narrative" as
the #2 blocker to portfolio value. "66.5% exact" means nothing without context.
This tool produces a single JSON file a recruiter or blog reader can cite.

Usage:
    python brain_vs_baselines.py                # print report
    python brain_vs_baselines.py --json out.json  # write structured results
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))
from severity_classifier import classify  # noqa: E402

TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]
GOLDEN_PATH = Path(__file__).parent / "golden_set.json"


def tier_idx(t: str) -> int:
    return TIER_ORDER.index(t) if t in TIER_ORDER else -1


def score_classifier(predictions: list[tuple[str, str]]) -> dict:
    """Given (expected, predicted) pairs, compute accuracy metrics."""
    exact = adjacent = wrong = 0
    per_tier: dict[str, dict[str, int]] = {}
    wrong_cases: list[tuple[str, str, str]] = []
    for (expected, predicted), original in zip(predictions, _GOLDEN):
        if expected not in per_tier:
            per_tier[expected] = {"exact": 0, "adjacent": 0, "wrong": 0, "total": 0}
        per_tier[expected]["total"] += 1
        d = abs(tier_idx(predicted) - tier_idx(expected))
        if d == 0:
            exact += 1
            per_tier[expected]["exact"] += 1
        elif d == 1:
            adjacent += 1
            per_tier[expected]["adjacent"] += 1
        else:
            wrong += 1
            per_tier[expected]["wrong"] += 1
            wrong_cases.append((expected, predicted, original["prompt"][:80]))
    total = len(predictions)
    return {
        "total": total,
        "exact": exact,
        "adjacent": adjacent,
        "wrong": wrong,
        "exact_pct": round(exact / total * 100, 1),
        "weighted": round((exact + 0.5 * adjacent) / total, 3),
        "per_tier": per_tier,
        "wrong_cases": wrong_cases[:10],  # cap to 10 for report readability
    }


# ─────────── Classifiers ───────────


def classify_brain(prompt: str) -> str:
    return classify(prompt_text=prompt).tier


def classify_length_only(prompt: str) -> str:
    """Naive length buckets. The obvious heuristic if you had zero thought."""
    n = len(prompt)
    if n < 30:
        return "S0"
    if n < 60:
        return "S1"
    if n < 120:
        return "S2"
    if n < 250:
        return "S3"
    if n < 500:
        return "S4"
    return "S5"


_S5_KEYWORDS = {"architecture", "refactor", "design", "from scratch", "orchestrat", "every file"}
_S4_KEYWORDS = {"implement", "build", "debug", "diagnose", "research"}
_S3_KEYWORDS = {"create", "write", "add", "wire", "fix"}
_S2_KEYWORDS = {"update", "edit", "rename", "refactor this"}
_S1_KEYWORDS = {"what", "how", "why", "when", "explain", "show"}


def classify_keyword_only(prompt: str) -> str:
    """Lexical-only classifier — pure keyword bucketing with no weights or guardrails."""
    lower = prompt.lower()
    if any(k in lower for k in _S5_KEYWORDS):
        return "S5"
    if any(k in lower for k in _S4_KEYWORDS):
        return "S4"
    if any(k in lower for k in _S3_KEYWORDS):
        return "S3"
    if any(k in lower for k in _S2_KEYWORDS):
        return "S2"
    if any(k in lower for k in _S1_KEYWORDS):
        return "S1"
    return "S0"


def classify_random(prompt: str, rng: random.Random) -> str:
    return rng.choice(TIER_ORDER)


def classify_majority(prompt: str) -> str:
    """Always predict S3 — the modal tier in the golden_set (75/200)."""
    return "S3"


# ─────────── Driver ───────────


_GOLDEN: list[dict] = []


def main() -> int:
    ap = argparse.ArgumentParser(description="Brain vs Baselines — golden_set benchmark")
    ap.add_argument("--json", dest="json_out", help="Write JSON report to PATH")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for random baseline")
    args = ap.parse_args()

    global _GOLDEN
    _GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    rng = random.Random(args.seed)

    classifiers: list[tuple[str, object]] = [
        ("Brain (v2.6.3)", classify_brain),
        ("Keyword-only", classify_keyword_only),
        ("Length-only", classify_length_only),
        ("Majority-class (always S3)", classify_majority),
        ("Random (seed=42)", lambda p: classify_random(p, rng)),
    ]

    all_results: dict[str, dict] = {}
    for name, fn in classifiers:
        predictions = [(e["expected"], fn(e["prompt"])) for e in _GOLDEN]
        all_results[name] = score_classifier(predictions)

    # ── Print report ─────────────────────────────
    print("=" * 74)
    print(" BRAIN vs BASELINES — 200-prompt golden_set benchmark (2026-04-17)")
    print("=" * 74)
    print()
    print(f"{'Classifier':<32} {'Exact':>8} {'Weighted':>10} {'Wrong':>7}")
    print("-" * 74)
    for name in all_results:
        r = all_results[name]
        print(f"{name:<32} {r['exact_pct']:>7.1f}% {r['weighted']:>10.3f} {r['wrong']:>7}")
    print()

    # Deltas vs Brain
    brain = all_results["Brain (v2.6.3)"]
    print("Brain's advantage over each baseline:")
    for name, r in all_results.items():
        if name == "Brain (v2.6.3)":
            continue
        d_exact = brain["exact_pct"] - r["exact_pct"]
        d_weighted = brain["weighted"] - r["weighted"]
        print(f"  vs {name:<30}  +{d_exact:5.1f} pp exact   +{d_weighted:.3f} weighted")
    print()

    # Per-tier breakdown for Brain + best baseline
    print(f"Brain per-tier:")
    for t in TIER_ORDER:
        if t in brain["per_tier"]:
            b = brain["per_tier"][t]
            print(f"  {t}: {b['exact']:>3}/{b['total']:<3} exact "
                  f"({100*b['exact']/b['total']:>5.1f}%)  {b['adjacent']:>2} adj  {b['wrong']:>2} wrong")
    print()

    # ── Write JSON ────────────────────────────────
    if args.json_out:
        out = {
            "benchmark_date": "2026-04-17",
            "golden_set_size": len(_GOLDEN),
            "brain_version": "v2.6.3",
            "classifiers": all_results,
            "metadata": {
                "methodology": "exact + adjacent + wrong scoring; weighted = exact + 0.5*adjacent",
                "baseline_list": [n for n, _ in classifiers],
            },
        }
        Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"JSON written: {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
