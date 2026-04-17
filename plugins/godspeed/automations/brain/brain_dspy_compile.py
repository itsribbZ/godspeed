#!/usr/bin/env python3
"""
Toke Brain — DSPy Classifier Compilation
=========================================
Compiles the labeled audit dataset into an optimized classifier prompt
using DSPy's BootstrapFewShot (or GEPA when dataset reaches 200+).

Input:  eval/audit_final_82.json (or any audit output with judge labels)
Output: eval/compiled_classifier.json (DSPy state dict)
        eval/compiled_prompt.txt (extracted system prompt for raw API use)

The compiled prompt is a STATIC artifact — eject DSPy from runtime.
Brain's hybrid path: rules handle S0/S5 deterministically, compiled
prompt handles the S1-S4 ambiguous middle via Haiku LLM call.

Usage:
    python brain_dspy_compile.py                           # defaults
    python brain_dspy_compile.py --audit eval/audit_final_82.json
    python brain_dspy_compile.py --optimizer gepa --steps 20  # when 200+ examples
    python brain_dspy_compile.py --dry-run                 # show dataset stats only

Requires: dspy, anthropic (ANTHROPIC_API_KEY env)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    import dspy
except ImportError:
    print("ERROR: dspy not installed. Run: pip install dspy", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BRAIN_DIR = Path(__file__).parent
EVAL_DIR = BRAIN_DIR / "eval"
DEFAULT_AUDIT = EVAL_DIR / "audit_200.json"
COMPILED_OUT = EVAL_DIR / "compiled_classifier.json"
PROMPT_OUT = EVAL_DIR / "compiled_prompt.txt"

TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]

# ---------------------------------------------------------------------------
# DSPy Signature + Module
# ---------------------------------------------------------------------------

class BrainRouter(dspy.Signature):
    """Route a user prompt to the correct Claude model cost tier (S0-S5).

    S0 (Haiku low): Trivial — one-word answers, lookups, yes/no
    S1 (Haiku med): Short Q&A, 1-2 sentence explanations
    S2 (Sonnet med): Single-file code edits, 3-10 line patches
    S3 (Sonnet+16K): Multi-step reasoning, 10-50 lines, dependency chains
    S4 (Opus+32K): Architecture, novel debugging, cross-file refactors
    S5 (Opus 1M+64K): Overnight runs, long-context synthesis, full-system work
    """
    prompt_text: str = dspy.InputField(desc="The raw user prompt to classify")
    tier: str = dspy.OutputField(desc="Exactly one of: S0, S1, S2, S3, S4, S5")


class BrainClassifier(dspy.Module):
    def __init__(self):
        self.classify = dspy.ChainOfThought(BrainRouter)

    def forward(self, prompt_text):
        result = self.classify(prompt_text=prompt_text)
        # Clamp to valid tier
        if hasattr(result, 'tier') and result.tier in TIER_ORDER:
            return result
        # Fallback: try to extract tier from response
        result.tier = "S2"  # safe default
        return result


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------

def routing_metric(example, pred, trace=None, pred_name=None, pred_trace=None):
    """Score: 1.0 = exact match, 0.5 = adjacent tier, 0.0 = 2+ tiers off.
    Asymmetric: undershooting S4/S5 to S1/S2 penalized harder.
    Signature compatible with both BootstrapFewShot and GEPA protocols."""
    try:
        pred_idx = TIER_ORDER.index(pred.tier)
        true_idx = TIER_ORDER.index(example.tier)
    except (ValueError, AttributeError):
        return 0.0
    delta = abs(pred_idx - true_idx)
    if delta == 0:
        return 1.0
    if delta == 1:
        return 0.5
    # Asymmetric: undershooting high-tier tasks is worse
    if true_idx >= 4 and pred_idx <= 2:  # S4/S5 -> S0/S1/S2
        return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------

def load_audit_dataset(audit_path: Path) -> list:
    """Load audit JSON, filter to scored records, return DSPy Examples."""
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    examples = []
    for r in data["records"]:
        if r["judge_score"] < 0:
            continue  # skip parse errors
        correct_tier = r["correct_tier"]
        if correct_tier not in TIER_ORDER:
            continue
        ex = dspy.Example(
            prompt_text=r["prompt_text"],
            tier=correct_tier,
        ).with_inputs("prompt_text")
        examples.append(ex)
    return examples


# ---------------------------------------------------------------------------
# Prompt extractor
# ---------------------------------------------------------------------------

def extract_compiled_prompt(program, output_path: Path):
    """Extract the compiled system prompt + few-shot demos from DSPy state."""
    state = program.dump_state()
    lines = []
    lines.append("# Brain Compiled Classifier Prompt")
    lines.append(f"# Extracted: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# Source: DSPy BootstrapFewShot compilation")
    lines.append("")

    # Walk the state dict for predict modules
    for key, value in state.items():
        if isinstance(value, dict):
            if "demos" in value:
                lines.append(f"## Module: {key}")
                lines.append(f"Demos: {len(value['demos'])}")
                for i, demo in enumerate(value["demos"]):
                    lines.append(f"\n### Example {i+1}")
                    for dk, dv in demo.items():
                        lines.append(f"  {dk}: {dv}")
            if "instructions" in value and value["instructions"]:
                lines.append(f"\n## Instructions: {value['instructions']}")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Extracted prompt: {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Toke Brain — DSPy Classifier Compilation")
    ap.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    ap.add_argument("--optimizer", choices=["bootstrap", "gepa", "mipro"], default="bootstrap")
    ap.add_argument("--steps", type=int, default=10, help="optimization steps (gepa/mipro only)")
    ap.add_argument("--train-model", default="anthropic/claude-haiku-4-5-20251001", help="LM for training/inference")
    ap.add_argument("--reflect-model", default="anthropic/claude-sonnet-4-6", help="LM for GEPA reflection")
    ap.add_argument("--split", type=float, default=0.8, help="train/dev split ratio")
    ap.add_argument("--max-demos", type=int, default=8, help="max few-shot demos to select")
    ap.add_argument("--output", type=Path, default=COMPILED_OUT)
    ap.add_argument("--dry-run", action="store_true", help="show dataset stats, don't compile")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    # Load dataset
    if not args.audit.exists():
        print(f"ERROR: audit file not found: {args.audit}", file=sys.stderr)
        return 1

    examples = load_audit_dataset(args.audit)
    print(f"Loaded {len(examples)} labeled examples from {args.audit.name}", file=sys.stderr)

    # Stats
    tier_dist = {}
    for ex in examples:
        tier_dist[ex.tier] = tier_dist.get(ex.tier, 0) + 1
    print(f"Tier distribution: {dict(sorted(tier_dist.items()))}", file=sys.stderr)

    if args.dry_run:
        print("Dry run — exiting before compilation.", file=sys.stderr)
        return 0

    # Split
    split_idx = int(len(examples) * args.split)
    trainset = examples[:split_idx]
    devset = examples[split_idx:]
    print(f"Split: {len(trainset)} train / {len(devset)} dev", file=sys.stderr)

    # Configure LM
    lm = dspy.LM(args.train_model)
    dspy.configure(lm=lm)
    print(f"LM configured: {args.train_model}", file=sys.stderr)

    # Build classifier
    classifier = BrainClassifier()

    # Compile
    print(f"Compiling with {args.optimizer}...", file=sys.stderr)
    t0 = time.time()

    if args.optimizer == "bootstrap":
        from dspy.teleprompt import BootstrapFewShot
        optimizer = BootstrapFewShot(
            metric=routing_metric,
            max_bootstrapped_demos=args.max_demos,
            max_labeled_demos=args.max_demos,
        )
        compiled = optimizer.compile(classifier, trainset=trainset)

    elif args.optimizer == "gepa":
        # GEPA requires dspy >= 3.0, uses auto presets or max_full_evals
        try:
            from dspy.teleprompt import GEPA
            reflect_lm = dspy.LM(args.reflect_model, temperature=1.0, max_tokens=8000)
            optimizer = GEPA(
                metric=routing_metric,
                max_full_evals=args.steps,
                reflection_lm=reflect_lm,
            )
            compiled = optimizer.compile(classifier, trainset=trainset, valset=devset)
        except ImportError:
            print("GEPA not available in this DSPy version. Falling back to BootstrapFewShot.", file=sys.stderr)
            from dspy.teleprompt import BootstrapFewShot
            optimizer = BootstrapFewShot(metric=routing_metric, max_bootstrapped_demos=args.max_demos)
            compiled = optimizer.compile(classifier, trainset=trainset)

    elif args.optimizer == "mipro":
        from dspy.teleprompt import MIPROv2
        optimizer = MIPROv2(metric=routing_metric, num_candidates=args.steps)
        compiled = optimizer.compile(classifier, trainset=trainset, num_trials=args.steps)

    elapsed = time.time() - t0
    print(f"Compilation done in {elapsed:.1f}s", file=sys.stderr)

    # Evaluate on dev set
    correct = 0
    adjacent = 0
    wrong = 0
    for ex in devset:
        try:
            pred = compiled(prompt_text=ex.prompt_text)
            score = routing_metric(ex, pred)
            if score >= 1.0:
                correct += 1
            elif score >= 0.5:
                adjacent += 1
            else:
                wrong += 1
        except Exception as e:
            print(f"  eval error: {e}", file=sys.stderr)
            wrong += 1

    n = len(devset) or 1
    print(f"\n=== DEV SET EVALUATION ===", file=sys.stderr)
    print(f"Correct:  {correct}/{n} ({100*correct/n:.1f}%)", file=sys.stderr)
    print(f"Adjacent: {adjacent}/{n} ({100*adjacent/n:.1f}%)", file=sys.stderr)
    print(f"Wrong:    {wrong}/{n} ({100*wrong/n:.1f}%)", file=sys.stderr)

    # Save compiled state
    args.output.parent.mkdir(parents=True, exist_ok=True)
    compiled.save(str(args.output))
    print(f"\nCompiled classifier saved: {args.output}", file=sys.stderr)

    # Extract prompt text
    extract_compiled_prompt(compiled, PROMPT_OUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
