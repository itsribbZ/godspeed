#!/usr/bin/env python3
"""
Toke — GEPA Optimizer for Brain Routing Manifest
==================================================
Custom GEPAAdapter that evolves routing_manifest.toml weights and thresholds
using GEPA's evolutionary optimization with LLM reflection.

Uses Haiku as the reflection model (~$0.10-0.30 per run).
Evaluator runs the Python classifier locally (zero API cost per eval).

CLI:
    python gepa_optimizer.py                          # default: 100 metric calls, Haiku reflection
    python gepa_optimizer.py --budget 200             # more iterations
    python gepa_optimizer.py --dataset golden_set     # use 200-prompt golden_set (default)
    python gepa_optimizer.py --dataset eval_prompts   # use 40-prompt eval set
    python gepa_optimizer.py --dry-run                # show config, don't optimize

Safety: writes ONLY to automations/gepa/proposals/ and gepa_runs/.
        Never modifies routing_manifest.toml or any Brain file.
        Proposals require the user's manual review and approval.

Origin: Built 2026-04-12 to close the S3 accuracy gap (53% exact → 80%+ target).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BRAIN_DIR = Path.home() / "Desktop" / "T1" / "Toke" / "automations" / "brain"
GEPA_DIR = Path(__file__).parent
RUNS_DIR = GEPA_DIR / "gepa_runs"
PROPOSALS_DIR = GEPA_DIR / "proposals"
MANIFEST_PATH = BRAIN_DIR / "routing_manifest.toml"
EVAL_DIR = BRAIN_DIR / "eval"
GOLDEN_SET_PATH = EVAL_DIR / "golden_set.json"
EVAL_PROMPTS_PATH = EVAL_DIR / "eval_prompts.json"

# Import classifier
sys.path.insert(0, str(BRAIN_DIR))
from severity_classifier import classify, load_manifest, DEFAULT_MANIFEST_PATH  # noqa: E402

TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class BrainDataInst:
    """A single evaluation prompt with expected tier."""
    prompt: str
    expected: str
    id: str


@dataclass
class BrainRolloutOutput:
    """Classifier output for a single prompt."""
    predicted: str
    score: float
    confidence: float
    signals: dict[str, float]
    guardrails_fired: list[str]
    reasoning: str


@dataclass
class BrainTrajectory:
    """Full trace of a single classification for reflection."""
    prompt: str
    expected: str
    predicted: str
    match_score: float  # 1.0=exact, 0.5=adjacent, 0.0=wrong
    base_score: float
    confidence: float
    signals: dict[str, float]
    guardrails_fired: list[str]
    direction: str  # exact, under_routed, over_routed, adjacent_under, adjacent_over


# ---------------------------------------------------------------------------
# Dataset loaders
# ---------------------------------------------------------------------------


def load_golden_set() -> list[BrainDataInst]:
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    return [
        BrainDataInst(prompt=p["prompt"], expected=p["expected"], id=f"G{i}")
        for i, p in enumerate(data, 1)
    ]


def load_eval_prompts() -> list[BrainDataInst]:
    data = json.loads(EVAL_PROMPTS_PATH.read_text(encoding="utf-8"))
    prompts = []
    for cat_name, cat_data in data.get("categories", {}).items():
        for p in cat_data.get("prompts", []):
            prompts.append(BrainDataInst(
                prompt=p.get("prompt", ""),
                expected=p.get("target_tier", ""),
                id=p.get("id", ""),
            ))
    return prompts


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _tier_idx(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


def score_prediction(expected: str, predicted: str) -> float:
    """1.0=exact, 0.5=adjacent, 0.0=wrong."""
    ei, pi = _tier_idx(expected), _tier_idx(predicted)
    if ei < 0 or pi < 0:
        return 0.0
    delta = abs(ei - pi)
    if delta == 0:
        return 1.0
    if delta == 1:
        return 0.5
    return 0.0


def classify_direction(expected: str, predicted: str) -> str:
    ei, pi = _tier_idx(expected), _tier_idx(predicted)
    if ei == pi:
        return "exact"
    delta = pi - ei
    if abs(delta) == 1:
        return "adjacent_over" if delta > 0 else "adjacent_under"
    return "over_routed" if delta > 0 else "under_routed"


# ---------------------------------------------------------------------------
# Candidate parsing
# ---------------------------------------------------------------------------


def parse_candidate(candidate_text: str) -> dict[str, Any]:
    """Parse a candidate TOML text into a manifest overlay dict.

    Handles both [weights] and [thresholds] sections.
    Returns a dict that can be merged into the base manifest.
    """
    overlay = {}
    try:
        # Ensure valid TOML structure
        parsed = tomllib.loads(candidate_text)
        if "weights" in parsed:
            overlay["weights"] = parsed["weights"]
        if "thresholds" in parsed:
            overlay["thresholds"] = parsed["thresholds"]
    except Exception:
        pass  # Return empty overlay — classifier uses defaults
    return overlay


def build_manifest_with_overlay(overlay: dict[str, Any]) -> dict[str, Any]:
    """Load base manifest and apply overlay."""
    base = load_manifest(DEFAULT_MANIFEST_PATH)
    if "weights" in overlay:
        base["weights"] = overlay["weights"]
    if "thresholds" in overlay:
        base["thresholds"] = overlay["thresholds"]
    return base


# ---------------------------------------------------------------------------
# GEPA Adapter
# ---------------------------------------------------------------------------


class BrainAdapter:
    """GEPAAdapter for Brain routing manifest optimization.

    - evaluate: runs classifier with candidate weights/thresholds, scores each prompt
    - make_reflective_dataset: provides misclassification details for LLM reflection
    """

    # Let GEPA use its built-in reflection proposer (reflection_lm)
    propose_new_texts = None

    def evaluate(
        self,
        batch: list[BrainDataInst],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ):
        from gepa.core.adapter import EvaluationBatch

        # Parse candidate text
        candidate_text = candidate.get("manifest", "")
        overlay = parse_candidate(candidate_text)
        manifest = build_manifest_with_overlay(overlay)

        outputs = []
        scores = []
        trajectories = [] if capture_traces else None

        for inst in batch:
            result = classify(prompt_text=inst.prompt, manifest=manifest)
            predicted = result.tier
            match_score = score_prediction(inst.expected, predicted)

            output = BrainRolloutOutput(
                predicted=predicted,
                score=result.score,
                confidence=result.confidence,
                signals=dict(result.signals),
                guardrails_fired=result.guardrails_fired,
                reasoning=result.reasoning,
            )
            outputs.append(output)
            scores.append(match_score)

            if capture_traces:
                traj = BrainTrajectory(
                    prompt=inst.prompt,
                    expected=inst.expected,
                    predicted=predicted,
                    match_score=match_score,
                    base_score=result.score,
                    confidence=result.confidence,
                    signals=dict(result.signals),
                    guardrails_fired=result.guardrails_fired,
                    direction=classify_direction(inst.expected, predicted),
                )
                trajectories.append(traj)

        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch,
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build reflection dataset showing misclassified prompts with signals.

        The reflection LLM sees:
        - Current weights/thresholds text
        - Each misclassified prompt with expected vs predicted tier
        - Signal breakdown showing which signals contributed
        - Guardrails that fired
        - Direction of error (under/over routed)

        This gives the LLM rich context for proposing weight/threshold adjustments.
        """
        trajectories = eval_batch.trajectories or []
        scores = eval_batch.scores or []

        entries = []
        for i, (traj, score) in enumerate(zip(trajectories, scores)):
            if not isinstance(traj, BrainTrajectory):
                continue

            # Focus on non-exact matches (show both wrong AND adjacent for context)
            if score >= 1.0:
                continue

            # Top contributing signals
            top_signals = sorted(
                traj.signals.items(), key=lambda x: x[1], reverse=True
            )[:5]
            signal_str = ", ".join(f"{k}={v:.2f}" for k, v in top_signals if v > 0.01)

            entry = {
                "input": traj.prompt[:120],
                "expected_output": traj.expected,
                "actual_output": traj.predicted,
                "score": score,
                "feedback": (
                    f"DIRECTION: {traj.direction} | "
                    f"BASE_SCORE: {traj.base_score:.3f} | "
                    f"CONFIDENCE: {traj.confidence:.2f} | "
                    f"TOP_SIGNALS: {signal_str} | "
                    f"GUARDRAILS: {', '.join(traj.guardrails_fired) or 'none'}"
                ),
            }
            entries.append(entry)

        # Sort: wrong first (score=0.0), then adjacent (score=0.5)
        entries.sort(key=lambda x: x["score"])

        # Return per-component reflection data
        result = {}
        for comp in components_to_update:
            result[comp] = entries
        return result


# ---------------------------------------------------------------------------
# Seed candidate builder
# ---------------------------------------------------------------------------


def build_seed_candidate() -> dict[str, str]:
    """Extract current weights + thresholds from manifest as seed candidate."""
    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = tomllib.loads(raw)

    weights = manifest.get("weights", {})
    thresholds = manifest.get("thresholds", {})

    lines = [
        "# Brain routing manifest weights and thresholds",
        "# Weights: each signal contributes weight × normalized_value to final score",
        "# Thresholds: tier boundaries — score < boundary → that tier",
        "# Goal: maximize exact tier matches on the golden_set (200 prompts)",
        "# Constraint: weights should sum to ~1.0-1.2 (not wildly different from current)",
        "# Constraint: thresholds must be strictly increasing (s0 < s1 < s2 < s3 < s4 < 1.0)",
        "",
        "[weights]",
    ]
    for key, val in sorted(weights.items()):
        lines.append(f"{key} = {val}")

    lines.append("")
    lines.append("[thresholds]")
    for key, val in sorted(thresholds.items()):
        lines.append(f"{key} = {val}")

    return {"manifest": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_optimization(
    dataset: str = "golden_set",
    max_metric_calls: int = 3000,
    reflection_model: str = "claude-haiku-4-5-20251001",
    minibatch_size: int = 5,
    run_dir: str | None = None,
    dry_run: bool = False,
    val_size: int = 40,
) -> dict[str, Any]:
    """Run GEPA optimization on Brain routing manifest.

    GEPA counts per-example evaluations as metric calls. With 200 trainset
    prompts: initial valset eval = val_size calls, each proposal iteration =
    minibatch + val_size calls. Budget must account for this.

    Returns a dict with:
    - best_candidate: the optimized weights/thresholds text
    - best_score: weighted accuracy score
    - improvement: delta from seed score
    - proposal_path: where the proposal was saved
    """
    import datetime
    import random

    from gepa.api import optimize

    # Load full dataset
    full_data = load_golden_set() if dataset == "golden_set" else load_eval_prompts()
    seed = build_seed_candidate()
    adapter = BrainAdapter()

    # Split into train + val: train gets the full set for rich reflection,
    # val gets a stratified subset to keep metric_calls per iteration low
    rng = random.Random(42)
    by_tier: dict[str, list[BrainDataInst]] = defaultdict(list)
    for inst in full_data:
        by_tier[inst.expected].append(inst)

    val_data: list[BrainDataInst] = []
    train_data: list[BrainDataInst] = list(full_data)  # train = full set
    for tier, insts in by_tier.items():
        # Stratified sample: proportional to tier size, min 2 per tier
        n = max(2, round(val_size * len(insts) / len(full_data)))
        sampled = rng.sample(insts, min(n, len(insts)))
        val_data.extend(sampled)

    # Establish baseline
    print(f"Dataset: {dataset} ({len(full_data)} prompts)")
    print(f"Train: {len(train_data)} | Val: {len(val_data)} (stratified)")
    print(f"Seed candidate: {len(seed['manifest'])} chars")
    print(f"Reflection model: {reflection_model}")
    print(f"Budget: {max_metric_calls} metric calls")
    print(f"  Est. iterations: ~{(max_metric_calls - len(val_data)) // (minibatch_size + len(val_data))}")
    print(f"Minibatch: {minibatch_size}")
    print()

    # Evaluate seed on full dataset
    seed_eval = adapter.evaluate(full_data, seed, capture_traces=True)
    seed_score = sum(seed_eval.scores) / len(seed_eval.scores)
    seed_exact = sum(1 for s in seed_eval.scores if s >= 1.0)
    seed_wrong = sum(1 for s in seed_eval.scores if s < 0.5)
    print(f"Seed baseline: {seed_score:.3f} weighted | {seed_exact}/{len(full_data)} exact | {seed_wrong} wrong")

    if dry_run:
        print(f"\n[DRY RUN] Would optimize with the above config. Exiting.")
        return {"seed_score": seed_score, "seed_exact": seed_exact, "dry_run": True}

    # Set up run directory
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if run_dir is None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_dir = str(RUNS_DIR / f"run_{ts}")

    print(f"Run dir: {run_dir}")
    print(f"\nStarting GEPA optimization...\n{'=' * 50}")

    # Run GEPA with separate train/val split
    result = optimize(
        seed_candidate=seed,
        trainset=train_data,
        valset=val_data,
        adapter=adapter,
        reflection_lm=reflection_model,
        candidate_selection_strategy="current_best",
        frontier_type="instance",
        reflection_minibatch_size=minibatch_size,
        max_metric_calls=max_metric_calls,
        perfect_score=1.0,
        run_dir=run_dir,
        display_progress_bar=True,
        seed=42,
    )

    # Extract best candidate
    best = result.best_candidate
    best_text = best.get("manifest", "")

    # Evaluate best on full dataset (not just val subset)
    best_eval = adapter.evaluate(full_data, best, capture_traces=True)
    best_score = sum(best_eval.scores) / len(best_eval.scores)
    best_exact = sum(1 for s in best_eval.scores if s >= 1.0)
    best_wrong = sum(1 for s in best_eval.scores if s < 0.5)

    improvement = best_score - seed_score
    exact_delta = best_exact - seed_exact

    print(f"\n{'=' * 50}")
    print(f"OPTIMIZATION COMPLETE")
    print(f"{'=' * 50}")
    print(f"  Seed:  {seed_score:.3f} weighted | {seed_exact}/{len(full_data)} exact | {seed_wrong} wrong")
    print(f"  Best:  {best_score:.3f} weighted | {best_exact}/{len(full_data)} exact | {best_wrong} wrong")
    print(f"  Delta: {improvement:+.3f} weighted | {exact_delta:+d} exact")
    print()

    # Save proposal
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    proposal_path = PROPOSALS_DIR / f"manifest_weights_{ts}.toml"
    header = (
        f"# GEPA Proposal — Brain Routing Manifest Weights + Thresholds\n"
        f"# Generated: {ts}\n"
        f"# Dataset: {dataset} ({len(full_data)} prompts)\n"
        f"# Metric calls: {max_metric_calls}\n"
        f"# Reflection model: {reflection_model}\n"
        f"# Seed score: {seed_score:.3f} ({seed_exact} exact, {seed_wrong} wrong)\n"
        f"# Best score: {best_score:.3f} ({best_exact} exact, {best_wrong} wrong)\n"
        f"# Improvement: {improvement:+.3f} weighted, {exact_delta:+d} exact\n"
        f"# STATUS: PENDING REVIEW — do not apply without the user's approval\n\n"
    )
    proposal_path.write_text(header + best_text, encoding="utf-8")
    print(f"Proposal saved: {proposal_path}")

    # Save comparison report
    report_path = PROPOSALS_DIR / f"comparison_{ts}.json"
    report = {
        "timestamp": ts,
        "dataset": dataset,
        "dataset_size": len(full_data),
        "metric_calls": max_metric_calls,
        "reflection_model": reflection_model,
        "seed": {
            "score": round(seed_score, 4),
            "exact": seed_exact,
            "wrong": seed_wrong,
        },
        "best": {
            "score": round(best_score, 4),
            "exact": best_exact,
            "wrong": best_wrong,
        },
        "improvement": {
            "score_delta": round(improvement, 4),
            "exact_delta": exact_delta,
        },
        "best_candidate_text": best_text,
        "proposal_path": str(proposal_path),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report saved: {report_path}")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="GEPA optimizer for Brain routing manifest")
    ap.add_argument("--dataset", choices=["golden_set", "eval_prompts"], default="golden_set")
    ap.add_argument("--budget", type=int, default=3000, help="Max metric calls (default 3000)")
    ap.add_argument("--reflection-model", default="claude-haiku-4-5-20251001",
                    help="Model for GEPA reflection (default: Haiku)")
    ap.add_argument("--minibatch", type=int, default=5, help="Reflection minibatch size")
    ap.add_argument("--run-dir", type=str, default=None, help="Resume from existing run dir")
    ap.add_argument("--dry-run", action="store_true", help="Show config, don't optimize")
    args = ap.parse_args()

    result = run_optimization(
        dataset=args.dataset,
        max_metric_calls=args.budget,
        reflection_model=args.reflection_model,
        minibatch_size=args.minibatch,
        run_dir=args.run_dir,
        dry_run=args.dry_run,
    )

    if not result.get("dry_run"):
        delta = result["improvement"]["score_delta"]
        if delta > 0:
            print(f"\nGEPA found improvement: {delta:+.3f} weighted score")
            print("Review proposal in proposals/ before applying to routing_manifest.toml")
        else:
            print(f"\nNo improvement found ({delta:+.3f}). Current weights may be near-optimal for this dataset.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
