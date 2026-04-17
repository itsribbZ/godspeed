#!/usr/bin/env python3
"""
Toke Brain — Eval Harness Runner
=================================
Runs eval prompts through the Brain classifier pipeline and scores accuracy.
Foundation for GEPA optimization — this is the evaluator that scores candidates.

CLI:
    python eval_harness.py                          # keyword-only, eval_prompts.json
    python eval_harness.py --llm                    # keyword + LLM fallback (~$0.12)
    python eval_harness.py --dataset golden_set     # use 200-prompt golden_set
    python eval_harness.py --output results.json    # save structured results
    python eval_harness.py -v                       # show each prompt result

Library:
    from eval_harness import run_eval, make_evaluator
    results = run_eval()                            # returns EvalResults
    evaluator = make_evaluator()                    # GEPA-compatible function
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Import classifier from parent directory
BRAIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BRAIN_DIR))
from severity_classifier import classify, load_manifest, DEFAULT_MANIFEST_PATH  # noqa: E402

EVAL_DIR = Path(__file__).parent
EVAL_PROMPTS = EVAL_DIR / "eval_prompts.json"
GOLDEN_SET = EVAL_DIR / "golden_set.json"
TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PromptResult:
    id: str
    prompt: str
    expected: str
    predicted: str
    score: float  # 1.0=exact, 0.5=adjacent, 0.0=wrong
    confidence: float
    category: str
    guardrails_fired: list[str]
    reasoning: str
    llm_override: str | None = None


@dataclass
class EvalResults:
    dataset: str
    total: int
    exact: int
    adjacent: int
    wrong: int
    accuracy: float
    weighted_score: float
    by_category: dict[str, dict[str, Any]]
    confusion: dict[str, dict[str, int]]
    results: list[PromptResult]
    under_routed: int = 0
    over_routed: int = 0


# ---------------------------------------------------------------------------
# Dataset loaders
# ---------------------------------------------------------------------------


def load_eval_prompts() -> list[dict[str, str]]:
    """Load eval_prompts.json into a flat list."""
    data = json.loads(EVAL_PROMPTS.read_text(encoding="utf-8"))
    prompts: list[dict[str, str]] = []
    for cat_name, cat_data in data.get("categories", {}).items():
        for p in cat_data.get("prompts", []):
            prompts.append({
                "id": p.get("id", ""),
                "prompt": p.get("prompt", ""),
                "expected": p.get("target_tier", ""),
                "category": cat_name,
            })
    return prompts


def load_golden_set() -> list[dict[str, str]]:
    """Load golden_set.json into a flat list."""
    data = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    return [
        {"id": f"G{i}", "prompt": p["prompt"], "expected": p["expected"], "category": "golden"}
        for i, p in enumerate(data, 1)
    ]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _tier_idx(tier: str) -> int:
    """Return tier index, or -1 for unknown tiers."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


def score_prediction(expected: str, predicted: str) -> float:
    """Score: 1.0=exact, 0.5=adjacent, 0.0=wrong."""
    exp_idx = _tier_idx(expected)
    pred_idx = _tier_idx(predicted)
    if exp_idx < 0 or pred_idx < 0:
        return 0.0
    delta = abs(exp_idx - pred_idx)
    if delta == 0:
        return 1.0
    if delta == 1:
        return 0.5
    return 0.0


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_eval(
    dataset: str = "eval_prompts",
    use_llm: bool = False,
    manifest: dict[str, Any] | None = None,
    verbose: bool = False,
) -> EvalResults:
    """Run the eval harness.

    Args:
        dataset: "eval_prompts" (40) or "golden_set" (200)
        use_llm: also run LLM fallback for uncertain classifications
        manifest: optional manifest dict (for GEPA weight optimization)
        verbose: print each result as it runs
    """
    prompts = load_golden_set() if dataset == "golden_set" else load_eval_prompts()

    results: list[PromptResult] = []
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_category: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"exact": 0, "adjacent": 0, "wrong": 0, "total": 0}
    )

    for p in prompts:
        result = classify(prompt_text=p["prompt"], manifest=manifest)

        predicted = result.tier
        llm_tier: str | None = None

        # LLM fallback (if enabled and classifier is uncertain)
        if use_llm and result.uncertainty_escalated and result.confidence < 0.30:
            try:
                from brain_llm_classifier import llm_classify  # noqa: E402
                llm_tier = llm_classify(p["prompt"])
                if llm_tier and llm_tier in TIER_ORDER:
                    predicted = llm_tier
            except Exception:
                pass

        s = score_prediction(p["expected"], predicted)

        pr = PromptResult(
            id=p["id"],
            prompt=p["prompt"],
            expected=p["expected"],
            predicted=predicted,
            score=s,
            confidence=result.confidence,
            category=p["category"],
            guardrails_fired=result.guardrails_fired,
            reasoning=result.reasoning,
            llm_override=llm_tier,
        )
        results.append(pr)

        confusion[p["expected"]][predicted] += 1

        cat = by_category[p["category"]]
        cat["total"] += 1
        if s >= 1.0:
            cat["exact"] += 1
        elif s >= 0.5:
            cat["adjacent"] += 1
        else:
            cat["wrong"] += 1

        if verbose:
            mark = "+" if s >= 1.0 else ("~" if s >= 0.5 else "X")
            llm_note = f" [llm->{llm_tier}]" if llm_tier else ""
            print(
                f"  {mark} {p['id']:<5} {p['expected']}->{predicted}"
                f"  conf={result.confidence:.2f}{llm_note}"
                f"  {p['prompt'][:55]}"
            )

    exact = sum(1 for r in results if r.score >= 1.0)
    adjacent = sum(1 for r in results if 0.5 <= r.score < 1.0)
    wrong = sum(1 for r in results if r.score < 0.5)
    total = len(results)

    under = 0
    over = 0
    for r in results:
        if r.score >= 0.5:
            continue
        ei = _tier_idx(r.expected)
        pi = _tier_idx(r.predicted)
        if ei >= 0 and pi >= 0:
            if pi < ei:
                under += 1
            elif pi > ei:
                over += 1

    return EvalResults(
        dataset=dataset,
        total=total,
        exact=exact,
        adjacent=adjacent,
        wrong=wrong,
        accuracy=exact / total if total > 0 else 0.0,
        weighted_score=sum(r.score for r in results) / total if total > 0 else 0.0,
        by_category={k: dict(v) for k, v in by_category.items()},
        confusion={k: dict(v) for k, v in confusion.items()},
        results=results,
        under_routed=under,
        over_routed=over,
    )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------


def print_report(er: EvalResults) -> None:
    """Print human-readable eval report."""
    print(f"{'=' * 60}")
    print(f"  BRAIN EVAL -- {er.dataset} ({er.total} prompts)")
    print(f"{'=' * 60}")
    print()
    print(f"  Exact:    {er.exact}/{er.total} ({er.accuracy * 100:.1f}%)")
    print(f"  Adjacent: {er.adjacent}/{er.total} ({er.adjacent / max(er.total, 1) * 100:.1f}%)")
    print(f"  Wrong:    {er.wrong}/{er.total} ({er.wrong / max(er.total, 1) * 100:.1f}%)")
    print(f"  Score:    {er.weighted_score:.3f} (weighted: exact=1.0, adj=0.5)")
    print(f"  Under:    {er.under_routed}   Over: {er.over_routed}")
    print()

    print("  BY CATEGORY")
    print(f"  {'Category':<30} {'Exact':>6} {'Adj':>5} {'Wrong':>6} {'Acc':>6}")
    print(f"  {'-' * 29} {'-' * 5} {'-' * 5} {'-' * 5} {'-' * 5}")
    for cat, stats in sorted(er.by_category.items()):
        t = stats["total"]
        acc = stats["exact"] / t * 100 if t > 0 else 0
        print(f"  {cat:<30} {stats['exact']:>5} {stats['adjacent']:>5} {stats['wrong']:>5} {acc:>5.1f}%")
    print()

    # Confusion matrix
    all_tiers = sorted(
        set(list(er.confusion.keys()) + [t for row in er.confusion.values() for t in row]),
        key=lambda x: TIER_ORDER.index(x) if x in TIER_ORDER else 99,
    )
    print("  CONFUSION (rows=expected, cols=predicted)")
    header = f"  {'':>4}  " + "  ".join(f"{t:>4}" for t in all_tiers)
    print(header)
    for exp in all_tiers:
        row = er.confusion.get(exp, {})
        cells = "  ".join(f"{row.get(t, 0):>4}" for t in all_tiers)
        print(f"  {exp:>4}  {cells}")
    print()

    # Worst misses
    misses = [r for r in er.results if r.score < 0.5]
    if misses:
        print(f"  WORST MISSES ({len(misses)})")
        for r in sorted(misses, key=lambda x: abs(_tier_idx(x.expected) - _tier_idx(x.predicted)), reverse=True)[:10]:
            direction = "UNDER" if _tier_idx(r.predicted) < _tier_idx(r.expected) else "OVER"
            print(f"  {direction:>5} {r.id:<5} {r.expected}->{r.predicted}  {r.prompt[:55]}")
        print()


# ---------------------------------------------------------------------------
# GEPA evaluator interface
# ---------------------------------------------------------------------------


def make_evaluator(
    dataset: str = "eval_prompts",
    use_llm: bool = False,
) -> Callable[[str, dict[str, Any] | None], tuple[float, dict[str, Any]]]:
    """Create a GEPA-compatible evaluator function.

    Returns: function(candidate_text, example=None) -> (score, diagnostics)

    The candidate is a TOML-format manifest weights string.
    The evaluator runs the classifier with those weights and returns accuracy.
    """
    import tomllib

    def evaluator(
        candidate: str, example: dict[str, Any] | None = None
    ) -> tuple[float, dict[str, Any]]:
        # Parse candidate weights into manifest overlay
        manifest: dict[str, Any] | None = None
        try:
            base_manifest = load_manifest(DEFAULT_MANIFEST_PATH)
            # If candidate contains [weights], parse and overlay
            if "[weights]" in candidate or "=" in candidate:
                candidate_toml = candidate if "[weights]" in candidate else f"[weights]\n{candidate}"
                parsed = tomllib.loads(candidate_toml)
                if "weights" in parsed:
                    base_manifest["weights"] = parsed["weights"]
            manifest = base_manifest
        except Exception:
            manifest = None  # fall back to default

        er = run_eval(dataset=dataset, use_llm=use_llm, manifest=manifest)

        return er.weighted_score, {
            "accuracy": f"{er.accuracy * 100:.1f}%",
            "exact": er.exact,
            "adjacent": er.adjacent,
            "wrong": er.wrong,
            "total": er.total,
            "under_routed": er.under_routed,
            "over_routed": er.over_routed,
            "by_category": er.by_category,
            "wrong_prompts": [
                {
                    "id": r.id,
                    "expected": r.expected,
                    "predicted": r.predicted,
                    "prompt": r.prompt[:80],
                }
                for r in er.results
                if r.score < 0.5
            ],
        }

    return evaluator


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Brain Eval Harness -- tier routing accuracy")
    ap.add_argument("--dataset", choices=["eval_prompts", "golden_set"], default="eval_prompts")
    ap.add_argument("--llm", action="store_true", help="Enable LLM fallback (~$0.12 Haiku)")
    ap.add_argument("--output", "-o", type=Path, help="Save JSON results to file")
    ap.add_argument("--verbose", "-v", action="store_true", help="Show each prompt result")
    args = ap.parse_args()

    er = run_eval(dataset=args.dataset, use_llm=args.llm, verbose=args.verbose)
    print_report(er)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "dataset": er.dataset,
            "total": er.total,
            "exact": er.exact,
            "adjacent": er.adjacent,
            "wrong": er.wrong,
            "accuracy": round(er.accuracy, 4),
            "weighted_score": round(er.weighted_score, 4),
            "under_routed": er.under_routed,
            "over_routed": er.over_routed,
            "by_category": er.by_category,
            "confusion": er.confusion,
            "results": [asdict(r) for r in er.results],
        }
        args.output.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Results saved: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
