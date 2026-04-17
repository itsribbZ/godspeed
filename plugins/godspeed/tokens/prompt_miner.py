#!/usr/bin/env python3
"""
Toke — Prompt Miner for Golden Set Expansion
=============================================
Mines decisions.jsonl for boundary prompts — cases where the classifier
was uncertain, near a tier boundary, or where guardrails over/under-fired.

These are the most valuable prompts for expanding the golden_set because
they represent real-world edge cases the classifier struggles with.

CLI:
    python prompt_miner.py                        # full analysis
    python prompt_miner.py --boundary             # boundary candidates only
    python prompt_miner.py --export golden_set    # export as golden_set format
    python prompt_miner.py --stats                # tier distribution + session stats

Origin: Built 2026-04-12 for Toke Frontier #5 (Prompt mining).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DECISIONS_PATH = Path(os.path.expanduser("~/.claude/telemetry/brain/decisions.jsonl"))
GOLDEN_SET_PATH = (
    Path(os.path.expanduser("~"))
    / "Desktop" / "T1" / "Toke" / "automations" / "brain" / "eval" / "golden_set.json"
)

# Tier thresholds — loaded from routing_manifest.toml at import time so this tool
# never drifts against the live classifier. Fallback values are the 2026-04-12d
# GEPA-tuned defaults; if the manifest is missing or unreadable we fail open
# with those rather than crashing. (G10 fix 2026-04-17: was hardcoded; hardcoded
# values happened to match current manifest but would silently rot on next edit.)
_MANIFEST = (
    Path(os.path.expanduser("~"))
    / "Desktop" / "T1" / "Toke" / "automations" / "brain" / "routing_manifest.json"
)


def _load_thresholds() -> dict[str, float]:
    defaults = {"s0_max": 0.09, "s1_max": 0.16, "s2_max": 0.22, "s3_max": 0.32, "s4_max": 0.55}
    try:
        data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        t = data.get("thresholds", {})
        return {k: float(t.get(k, defaults[k])) for k in defaults}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return defaults


THRESHOLDS = _load_thresholds()

TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MinedPrompt:
    """A prompt extracted from decisions.jsonl with mining metadata."""
    prompt: str
    classified_tier: str
    score: float
    confidence: float
    guardrails: list[str]
    boundary_type: str      # low_conf, near_boundary, multi_guardrail, correction, clean
    boundary_score: float   # 0-1, higher = more boundary-like (better candidate)
    session_id: str
    timestamp: str
    suggested_tier: str     # human-review target tier


# ---------------------------------------------------------------------------
# Boundary detection
# ---------------------------------------------------------------------------


def distance_to_nearest_boundary(score: float) -> float:
    """Distance to nearest tier threshold."""
    boundaries = list(THRESHOLDS.values())
    if not boundaries:
        return 1.0
    return min(abs(score - b) for b in boundaries)


def compute_boundary_score(
    confidence: float,
    score: float,
    guardrails: list[str],
    has_correction: bool,
) -> tuple[float, str]:
    """Compute how "boundary-like" a prompt is. Higher = better candidate.

    Returns (score, type_label).
    """
    reasons = []
    boundary_val = 0.0

    # Low confidence is the strongest signal
    if confidence <= 0.10:
        boundary_val += 0.5
        reasons.append("low_conf")
    elif confidence <= 0.20:
        boundary_val += 0.3
        reasons.append("med_conf")

    # Near a tier boundary
    dist = distance_to_nearest_boundary(score)
    if dist <= 0.02:
        boundary_val += 0.3
        reasons.append("near_boundary")
    elif dist <= 0.05:
        boundary_val += 0.15
        reasons.append("close_boundary")

    # Multiple guardrails suggest competing signals
    if len(guardrails) >= 3:
        boundary_val += 0.2
        reasons.append("multi_guardrail")
    elif len(guardrails) >= 2:
        boundary_val += 0.1

    # Ceiling + floor firing together = tension
    has_ceiling = any("ceiling" in g for g in guardrails)
    has_floor = any("floor" in g for g in guardrails)
    if has_ceiling and has_floor:
        boundary_val += 0.15
        reasons.append("ceiling_floor_tension")

    # Correction detected = classifier got it wrong
    if has_correction:
        boundary_val += 0.25
        reasons.append("correction")

    boundary_val = min(boundary_val, 1.0)
    btype = reasons[0] if reasons else "clean"

    return round(boundary_val, 3), btype


def suggest_tier(classified_tier: str, score: float, guardrails: list[str]) -> str:
    """Suggest the most likely correct tier for human review.

    Uses guardrail presence and score proximity to suggest where this
    prompt SHOULD land. The human reviewer confirms or overrides.
    """
    # If ceiling fired, the classifier was pushed DOWN — classified tier is probably right
    if any("ceiling" in g for g in guardrails):
        return classified_tier

    # If only floor guardrails fired, the prompt might belong lower
    if guardrails and all("floor" in g for g in guardrails):
        idx = TIER_ORDER.index(classified_tier) if classified_tier in TIER_ORDER else 3
        return TIER_ORDER[max(idx - 1, 0)]

    # Default: trust the classifier
    return classified_tier


# ---------------------------------------------------------------------------
# Mining
# ---------------------------------------------------------------------------


def load_decisions() -> list[dict]:
    """Load all decisions with prompt_text from decisions.jsonl."""
    if not DECISIONS_PATH.exists():
        return []

    decisions = []
    with DECISIONS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only include entries with prompt text
            if d.get("prompt_text"):
                decisions.append(d)
    return decisions


def mine_prompts(decisions: list[dict]) -> list[MinedPrompt]:
    """Extract and score prompts for golden_set candidacy."""
    mined = []
    seen_prompts: set[str] = set()

    for d in decisions:
        prompt = d.get("prompt_text", "").strip()
        if not prompt or len(prompt) < 5:
            continue

        # Deduplicate by normalized prompt
        norm = prompt.lower().strip()[:100]
        if norm in seen_prompts:
            continue
        seen_prompts.add(norm)

        result = d.get("result", {})
        tier = result.get("tier", "?")
        score = result.get("score", 0.0)
        confidence = result.get("confidence", 1.0)
        guardrails = result.get("guardrails_fired", [])
        has_correction = result.get("correction_detected_in_prompt", False)

        boundary_score, boundary_type = compute_boundary_score(
            confidence, score, guardrails, has_correction,
        )

        suggested = suggest_tier(tier, score, guardrails)

        mined.append(MinedPrompt(
            prompt=prompt[:500],
            classified_tier=tier,
            score=score,
            confidence=confidence,
            guardrails=guardrails,
            boundary_type=boundary_type,
            boundary_score=boundary_score,
            session_id=d.get("session_id", ""),
            timestamp=d.get("ts", ""),
            suggested_tier=suggested,
        ))

    # Sort by boundary score descending (best candidates first)
    mined.sort(key=lambda m: m.boundary_score, reverse=True)
    return mined


# ---------------------------------------------------------------------------
# Existing golden_set check
# ---------------------------------------------------------------------------


def load_existing_golden_prompts() -> set[str]:
    """Load normalized prompts from the existing golden_set to avoid duplicates."""
    if not GOLDEN_SET_PATH.exists():
        return set()
    try:
        data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
        return {p["prompt"].lower().strip()[:100] for p in data if "prompt" in p}
    except (json.JSONDecodeError, KeyError):
        return set()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_analysis(mined: list[MinedPrompt], boundary_only: bool = False) -> None:
    """Print full analysis of mined prompts."""
    if boundary_only:
        mined = [m for m in mined if m.boundary_score >= 0.2]

    existing = load_existing_golden_prompts()
    new_candidates = [m for m in mined if m.prompt.lower().strip()[:100] not in existing]

    print(f"{'=' * 100}")
    print(f"  PROMPT MINER — Golden Set Expansion Candidates")
    print(f"{'=' * 100}")
    print(f"  Total mined: {len(mined)} unique prompts")
    print(f"  Boundary candidates (score >= 0.2): {sum(1 for m in mined if m.boundary_score >= 0.2)}")
    print(f"  Already in golden_set: {len(mined) - len(new_candidates)}")
    print(f"  New candidates: {len(new_candidates)}")
    print(f"{'=' * 100}")
    print()

    # Tier distribution
    tier_counts = Counter(m.classified_tier for m in mined)
    print("  Tier distribution (mined prompts):")
    for tier in TIER_ORDER:
        count = tier_counts.get(tier, 0)
        bar = "#" * min(count, 40)
        print(f"    {tier}: {count:>3} {bar}")
    print()

    # Boundary type distribution
    type_counts = Counter(m.boundary_type for m in mined)
    print("  Boundary types:")
    for btype, count in type_counts.most_common():
        print(f"    {btype:<25} {count}")
    print()

    # Top candidates
    display = new_candidates if boundary_only else mined
    print(f"  {'BND':>5}  {'Tier':>4}  {'Score':>6}  {'Conf':>5}  {'Type':<20}  Prompt")
    print(f"  {'-'*95}")
    for m in display[:30]:
        in_gs = "*" if m.prompt.lower().strip()[:100] in existing else " "
        prompt_display = m.prompt[:70].replace("\n", " ")
        print(
            f" {in_gs}{m.boundary_score:5.3f}  {m.classified_tier:>4}  {m.score:6.3f}  "
            f"{m.confidence:5.2f}  {m.boundary_type:<20}  {prompt_display}"
        )


def print_stats(decisions: list[dict]) -> None:
    """Print tier distribution and session stats."""
    tiers = Counter()
    sessions = set()
    with_prompt = 0
    without_prompt = 0

    # Load ALL decisions (not just those with prompts)
    if not DECISIONS_PATH.exists():
        print("No decisions.jsonl found.")
        return

    with DECISIONS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            result = d.get("result", {})
            tiers[result.get("tier", "?")] += 1
            sessions.add(d.get("session_id", ""))
            if d.get("prompt_text"):
                with_prompt += 1
            else:
                without_prompt += 1

    total = sum(tiers.values())
    print(f"  Decisions: {total} total | {with_prompt} with prompt | {without_prompt} without")
    print(f"  Sessions: {len(sessions)}")
    print()
    print(f"  Tier distribution:")
    for tier in TIER_ORDER + ["?"]:
        count = tiers.get(tier, 0)
        pct = count * 100 / total if total else 0
        bar = "#" * int(pct)
        print(f"    {tier}: {count:>4} ({pct:5.1f}%) {bar}")
    print()
    print(f"  Capture rate: {with_prompt}/{total} ({with_prompt*100/total:.0f}%) have prompt text")
    print(f"  Need: 50+ prompts with text for meaningful mining (have {with_prompt})")


def export_golden_format(mined: list[MinedPrompt], output_path: str) -> None:
    """Export top boundary candidates in golden_set format for review."""
    existing = load_existing_golden_prompts()
    candidates = [
        m for m in mined
        if m.boundary_score >= 0.15 and m.prompt.lower().strip()[:100] not in existing
    ]

    entries = []
    for m in candidates[:50]:  # Cap at 50 candidates per export
        entries.append({
            "prompt": m.prompt,
            "expected": m.suggested_tier,
            "source": "mined",
            "boundary_score": m.boundary_score,
            "boundary_type": m.boundary_type,
            "classified_as": m.classified_tier,
            "classifier_score": m.score,
            "classifier_confidence": m.confidence,
            "needs_review": True,
        })

    Path(output_path).write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Exported {len(entries)} candidates to {output_path}")
    print("  IMPORTANT: 'expected' tier is SUGGESTED — human review required before adding to golden_set")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Toke Prompt Miner")
    parser.add_argument("--boundary", action="store_true", help="Show boundary candidates only")
    parser.add_argument("--export", metavar="PATH", help="Export candidates in golden_set JSON format")
    parser.add_argument("--stats", action="store_true", help="Show tier distribution + session stats")
    args = parser.parse_args()

    if args.stats:
        print_stats([])
        return 0

    decisions = load_decisions()
    if not decisions:
        print("No decisions with prompt text found. Need v2.5+ logging (50+ sessions).")
        return 0

    mined = mine_prompts(decisions)

    if args.export:
        export_golden_format(mined, args.export)
    else:
        print_analysis(mined, boundary_only=args.boundary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
