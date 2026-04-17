#!/usr/bin/env python3
"""
Toke Brain — Retrospective Audit
================================
Grades Brain's routing decisions using Claude-as-judge. Produces the first
ground-truth labeled dataset for classifier training (DSPy/GEPA-ready format).

Modes:
  golden     — runs Brain against a curated 30-prompt golden set spanning S0-S5
               and grades each classification. Sorted worst-first, DSPy-ready.
  historical — stubbed (v2): joins decisions.jsonl with session transcripts to
               grade real past routings. Requires transcript join (not yet built).

Usage:
    python brain_audit.py                       # golden mode, sonnet judge
    python brain_audit.py --judge opus          # opus judge (5x cost, higher quality)
    python brain_audit.py --limit 5             # dry-run first 5 prompts
    python brain_audit.py --output path.json    # custom output path

Dependencies: anthropic (already installed) + Python stdlib only.
Auth: reads ANTHROPIC_API_KEY from env.
Design: Toke stdlib+anthropic discipline. Output JSON is DSPy training-set ready.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))
from severity_classifier import classify  # noqa: E402

try:
    import anthropic
except ImportError:
    print("ERROR: `anthropic` package required. pip install anthropic", file=sys.stderr)
    sys.exit(1)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

BRAIN_DIR = Path(__file__).parent
EVAL_DIR = BRAIN_DIR / "eval"
EVAL_DIR.mkdir(exist_ok=True)

DEFAULT_OUTPUT = EVAL_DIR / f"audit_{int(time.time())}.json"
GOLDEN_SET_PATH = EVAL_DIR / "golden_set.json"  # optional external override

JUDGE_MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}


# -----------------------------------------------------------------------------
# Judge rubric
# -----------------------------------------------------------------------------

RUBRIC = """You are an expert grader for Brain — a Claude model routing classifier.

Brain routes prompts to tiers S0-S5:
- S0 (Haiku low)     : Trivial — one-word answers, filename lookups, yes/no
- S1 (Haiku medium)  : Short Q&A, lookups needing 1-2 sentences
- S2 (Sonnet medium) : Single-file edits, standard code changes, 3-10 line patches
- S3 (Sonnet + 16K)  : Multi-step reasoning, 10-50 lines, dependency chains
- S4 (Opus + 32K)    : Architecture, novel debugging, cross-file refactors
- S5 (Opus 1M + 64K) : Overnight runs, long-context synthesis, production-scale

You receive a PROMPT and the TIER Brain chose. Grade with this rubric:
  1.0 = correct (tier exactly matches task complexity)
  0.5 = adjacent (one tier off — usable but suboptimal)
  0.0 = wrong   (two+ tiers off — significant quality or cost harm)

Asymmetric penalty: undershooting on S4/S5 complexity (routing to S1/S2) is
CATASTROPHIC and MUST score 0.0. Overshooting S0/S1 trivial tasks to S3+ is
wasteful but not catastrophic — score 0.5 maximum.

Respond ONLY with valid JSON, no markdown, no commentary:
{"score": <0.0|0.5|1.0>, "correct_tier": "<SX>", "reason": "<one sentence>"}"""


# -----------------------------------------------------------------------------
# Golden set (embedded fallback — 30 prompts spanning S0-S5)
# -----------------------------------------------------------------------------

DEFAULT_GOLDEN_SET: list[dict[str, str]] = [
    # S0 — trivial
    {"prompt": "list files here", "expected": "S0"},
    {"prompt": "what day is it", "expected": "S0"},
    {"prompt": "print hello", "expected": "S0"},
    {"prompt": "yes or no", "expected": "S0"},
    {"prompt": "cat README.md", "expected": "S0"},
    # S1 — simple Q&A
    {"prompt": "what does this regex do: ^\\d{3}-\\d{4}$", "expected": "S1"},
    {"prompt": "rename variable foo to user_count in this function", "expected": "S1"},
    {"prompt": "show the git log for main", "expected": "S1"},
    {"prompt": "what port does PostgreSQL use by default", "expected": "S1"},
    {"prompt": "convert this JSON key from camelCase to snake_case", "expected": "S1"},
    # S2 — standard code edits
    {"prompt": "fix the off-by-one error in parse_range() where the max index isn't included", "expected": "S2"},
    {"prompt": "add a --verbose flag to this CLI and wire it into the existing logger", "expected": "S2"},
    {"prompt": "write a pytest unit test for the Tier enum parser", "expected": "S2"},
    {"prompt": "refactor this for-loop into a list comprehension", "expected": "S2"},
    {"prompt": "add retry logic with exponential backoff to the HTTP client wrapper", "expected": "S2"},
    # S3 — multi-step reasoning
    {"prompt": "diagnose why the brain classifier is routing S1 for prompts that should be S3 — look at signal weights and recommend adjustments", "expected": "S3"},
    {"prompt": "implement a cost tracker that reads decisions.jsonl, aggregates by model and week, and outputs deltas as JSON", "expected": "S3"},
    {"prompt": "add an async fallback chain to the advisor wrapper: retry on 429, escalate to Opus on repeated failure, log every transition", "expected": "S3"},
    {"prompt": "build a tokenizer for routing_manifest.toml that strips comments and validates section structure against a schema", "expected": "S3"},
    {"prompt": "wire brain_cli into PreCompact events and test the end-to-end output logging path", "expected": "S3"},
    # S4 — architecture / novel debug
    {"prompt": "design Homer's MUSES parallel dispatch from scratch — how workers share state, handle partial failures, and write back to VAULT atomically without race conditions", "expected": "S4"},
    {"prompt": "refactor severity_classifier to support a hybrid trained-classifier + rule-based fallback with graceful degradation semantics", "expected": "S4"},
    {"prompt": "my AnimBP is T-posing only when the movement component is replicating — trace the root cause across blueprint, skeletal mesh, and replication graph", "expected": "S4"},
    {"prompt": "audit the entire hook pipeline for compaction safety and design a fix that preserves telemetry across /compact without manual intervention", "expected": "S4"},
    {"prompt": "design a tiered memory system implementing Letta's Core/Recall/Archival on top of existing MEMORY.md files without adding a vector database", "expected": "S4"},
    # S5 — massive context / overnight
    {"prompt": "read the entire Sworder Bible (108k lines), ue-knowledge (46k lines), and GDD — cross-reference every combat rule and build a unified decision table of canon sources with line-number receipts", "expected": "S5"},
    {"prompt": "review every skill in ~/.claude/skills, identify pattern drift against shared protocols, and produce per-skill specific edit proposals with reasoning", "expected": "S5"},
    {"prompt": "overnight: ingest 200 decisions from decisions.jsonl, cluster by failure mode, generate synthetic training data for a DSPy classifier, and compile it into an exportable prompt", "expected": "S5"},
    {"prompt": "full architecture review of Toke — Brain v2.3, Homer L0-L7, VAULT, Sybil, Muses — identify every inconsistency and produce a v3.0 redesign with migration plan", "expected": "S5"},
    {"prompt": "read every file under Desktop/atelier, generate a feature-parity PR for Ink Finder that reuses Atelier's theme system, marketplace UI, and inbox pattern, with passing tests", "expected": "S5"},
]


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class AuditRecord:
    prompt_text: str
    routed_tier: str
    routed_model: str
    routed_score: float
    routed_signals: dict
    routed_reasoning: str
    judge_score: float
    correct_tier: str
    judge_reason: str
    expected_tier: str = ""
    source: str = "golden"


# -----------------------------------------------------------------------------
# Judge
# -----------------------------------------------------------------------------

def judge_routing(client: anthropic.Anthropic, model: str, prompt: str, routed_tier: str) -> tuple[float, str, str]:
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=RUBRIC,
        messages=[{
            "role": "user",
            "content": f"PROMPT:\n{prompt}\n\nROUTED TIER: {routed_tier}\n\nGrade this routing. Return JSON only.",
        }],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        raw = "\n".join(lines)
    try:
        data = json.loads(raw)
        return float(data["score"]), str(data["correct_tier"]), str(data["reason"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return -1.0, "PARSE_ERROR", f"judge_parse_error: {type(e).__name__}; raw={raw[:160]}"


# -----------------------------------------------------------------------------
# Runners
# -----------------------------------------------------------------------------

def load_golden_set() -> list[dict[str, str]]:
    if GOLDEN_SET_PATH.exists():
        return json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    return DEFAULT_GOLDEN_SET


def run_golden(client: anthropic.Anthropic, judge_model: str, limit: int | None,
               use_llm_fallback: bool = False) -> list[AuditRecord]:
    golden = load_golden_set()
    if limit:
        golden = golden[:limit]
    records: list[AuditRecord] = []
    total = len(golden)

    # Lazy import LLM fallback
    _llm_classify = None
    if use_llm_fallback:
        try:
            from brain_llm_classifier import llm_classify as _lc
            _llm_classify = _lc
            print("LLM fallback: ENABLED (fires on low-confidence)", file=sys.stderr)
        except ImportError:
            print("LLM fallback: UNAVAILABLE (brain_llm_classifier not found)", file=sys.stderr)

    for i, item in enumerate(golden, 1):
        prompt = item["prompt"]
        expected = item.get("expected", "")
        try:
            result = classify(prompt_text=prompt)
        except Exception as e:
            print(f"[{i}/{total}] CLASSIFY ERROR: {e}", file=sys.stderr)
            continue

        # v2.1: LLM fallback override (same logic as brain_cli.py hook path)
        if _llm_classify and result.uncertainty_escalated and result.confidence < 0.30:
            try:
                llm_tier = _llm_classify(prompt)
                if llm_tier and llm_tier in ("S0", "S1", "S2", "S3", "S4", "S5"):
                    _tm = {"S0": ("haiku", "low", 0), "S1": ("haiku", "medium", 0),
                           "S2": ("sonnet", "medium", 0), "S3": ("sonnet", "high", 16000),
                           "S4": ("opus", "high", 32000), "S5": ("opus[1m]", "max", 64000)}
                    m, e, t = _tm[llm_tier]
                    result.tier = llm_tier
                    result.model = m
                    result.effort = e
                    result.extended_thinking_budget = t
                    result.reasoning += f" | llm_override:{llm_tier}"
            except Exception:
                pass

        try:
            score, correct, reason = judge_routing(client, judge_model, prompt, result.tier)
        except Exception as e:
            print(f"[{i}/{total}] JUDGE ERROR: {e}", file=sys.stderr)
            continue
        rec = AuditRecord(
            prompt_text=prompt,
            routed_tier=result.tier,
            routed_model=result.model,
            routed_score=result.score,
            routed_signals=dict(result.signals),
            routed_reasoning=result.reasoning,
            judge_score=score,
            correct_tier=correct,
            judge_reason=reason,
            expected_tier=expected,
            source="golden",
        )
        records.append(rec)
        flag = "[OK]" if score >= 1.0 else ("[~]" if score >= 0.5 else "[X]")
        print(f"[{i}/{total}] {flag} brain:{result.tier} judge:{correct} | {reason[:72]}", file=sys.stderr)
    return records


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Toke Brain retrospective audit")
    ap.add_argument("--mode", choices=["golden", "historical"], default="golden")
    ap.add_argument("--judge", choices=list(JUDGE_MODELS), default="sonnet")
    ap.add_argument("--limit", type=int, default=None, help="cap prompts (dry-run)")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--llm-fallback", action="store_true", help="enable LLM fallback on low-confidence")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    judge_model = JUDGE_MODELS[args.judge]
    client = anthropic.Anthropic()

    print(f"Brain Audit | mode={args.mode} | judge={args.judge} ({judge_model})", file=sys.stderr)

    if args.mode == "golden":
        records = run_golden(client, judge_model, args.limit, use_llm_fallback=args.llm_fallback)
    else:
        print("historical mode requires transcript join — v2 feature, not yet implemented", file=sys.stderr)
        return 2

    if not records:
        print("No records produced.", file=sys.stderr)
        return 1

    scored = [r for r in records if r.judge_score >= 0]
    n = len(scored) or 1
    correct = sum(1 for r in scored if r.judge_score >= 1.0)
    adjacent = sum(1 for r in scored if 0.5 <= r.judge_score < 1.0)
    wrong = sum(1 for r in scored if r.judge_score < 0.5)
    parse_err = sum(1 for r in records if r.judge_score < 0)
    avg = sum(r.judge_score for r in scored) / n

    records.sort(key=lambda r: r.judge_score)

    print("", file=sys.stderr)
    print("=== BRAIN AUDIT SUMMARY ===", file=sys.stderr)
    print(f"Total scored: {n}", file=sys.stderr)
    print(f"  Correct:    {correct:3d}  ({100*correct/n:5.1f}%)", file=sys.stderr)
    print(f"  Adjacent:   {adjacent:3d}  ({100*adjacent/n:5.1f}%)", file=sys.stderr)
    print(f"  Wrong:      {wrong:3d}  ({100*wrong/n:5.1f}%)", file=sys.stderr)
    print(f"  Parse err:  {parse_err}", file=sys.stderr)
    print(f"Avg score:    {avg:.3f}", file=sys.stderr)

    print("", file=sys.stderr)
    print("=== WORST 5 ===", file=sys.stderr)
    for r in records[:5]:
        print(f"  {r.judge_score:.2f} | brain:{r.routed_tier} judge:{r.correct_tier} | {r.prompt_text[:70]}", file=sys.stderr)
        print(f"       reason: {r.judge_reason[:110]}", file=sys.stderr)

    out = args.output or DEFAULT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "meta": {
            "mode": args.mode,
            "judge_model": judge_model,
            "total": len(records),
            "correct": correct,
            "adjacent": adjacent,
            "wrong": wrong,
            "parse_errors": parse_err,
            "avg_score": avg,
            "timestamp": int(time.time()),
            "format": "dspy_ready_v1",
        },
        "records": [asdict(r) for r in records],
    }, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
