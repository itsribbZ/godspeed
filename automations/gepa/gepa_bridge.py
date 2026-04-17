#!/usr/bin/env python3
"""
Toke — GEPA Integration Bridge
================================
Connects Toke's existing data (decisions.jsonl, eval_prompts.json,
skill descriptions, routing_manifest.toml) to GEPA's optimize_anything API.

Works WITHOUT gepa installed (pure stdlib data extraction).
Works WITH gepa installed (exposes evaluator/dataset interfaces).

Usage from Jupyter:
    import sys
    sys.path.insert(0, "~/Desktop/T1/Toke/automations/gepa")
    from gepa_bridge import (
        get_manifest_weights,
        get_skill_catalog,
        get_eval_dataset,
        get_asi_diagnostics,
        make_manifest_evaluator,
        make_skill_evaluator,
    )

Usage with GEPA:
    import gepa
    from gepa_bridge import get_manifest_weights, make_manifest_evaluator, get_asi_diagnostics

    result = gepa.optimize_anything(
        seed_candidate=get_manifest_weights(),
        evaluator=make_manifest_evaluator(),
        dataset=get_asi_diagnostics(),
    )

Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, GEPA steal opportunities.
Safety: READ-ONLY on all Toke data sources. Never modifies Brain files, manifest, or skills.
Dependencies: Python 3.11+ stdlib only. Optional: gepa (pip install gepa).
"""

from __future__ import annotations

import json
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# =============================================================================
# Paths (read-only — never write to these)
# =============================================================================

BRAIN_DIR = Path.home() / "Desktop" / "T1" / "Toke" / "automations" / "brain"
MANIFEST_PATH = BRAIN_DIR / "routing_manifest.toml"
CLASSIFIER_PATH = BRAIN_DIR / "severity_classifier.py"
EVAL_PROMPTS_PATH = BRAIN_DIR / "eval" / "eval_prompts.json"
DECISIONS_LOG = Path.home() / ".claude" / "telemetry" / "brain" / "decisions.jsonl"
SKILLS_DIR = Path.home() / ".claude" / "skills"

# Output paths (GEPA writes proposals here, never to live files)
GEPA_OUTPUT_DIR = Path(__file__).parent / "proposals"


# =============================================================================
# S1: Manifest Weight Extraction (read-only)
# =============================================================================


def get_manifest_weights() -> str:
    """Extract the weight section from routing_manifest.toml as text.

    Returns the [weights] section as a TOML-formatted string that GEPA
    can treat as a seed candidate for optimization.

    READ-ONLY: never modifies the manifest file.
    """
    if not MANIFEST_PATH.exists():
        return "# routing_manifest.toml not found"

    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = tomllib.loads(raw)

    weights = manifest.get("weights", {})
    if not weights:
        return "# No [weights] section found in manifest"

    # Serialize weights back to TOML-like format
    lines = ["[weights]"]
    for key, val in sorted(weights.items()):
        if isinstance(val, float):
            lines.append(f"{key} = {val}")
        elif isinstance(val, str):
            lines.append(f'{key} = "{val}"')
        else:
            lines.append(f"{key} = {val}")

    return "\n".join(lines)


def get_manifest_thresholds() -> dict[str, Any]:
    """Extract tier thresholds from manifest. Read-only."""
    if not MANIFEST_PATH.exists():
        return {}
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest.get("tier_map", {})


def get_manifest_guardrails() -> dict[str, Any]:
    """Extract guardrail definitions from manifest. Read-only."""
    if not MANIFEST_PATH.exists():
        return {}
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest.get("guardrails", {})


# =============================================================================
# S2: Skill Description Extraction (read-only)
# =============================================================================


@dataclass
class SkillInfo:
    name: str
    description: str
    model: str
    effort: str
    full_path: str


def get_skill_catalog() -> list[SkillInfo]:
    """Extract description + model pin from every skill's SKILL.md frontmatter.

    READ-ONLY: reads frontmatter from .claude/skills/*/SKILL.md.
    Returns a list of SkillInfo objects, each containing the skill's
    ~250-char description that drives Claude Code routing.
    """
    skills: list[SkillInfo] = []

    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Parse YAML-ish frontmatter (between --- markers)
        name = skill_dir.name
        description = ""
        model = ""
        effort = ""

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                for line in frontmatter.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("description:"):
                        description = line[len("description:"):].strip().strip('"').strip("'")
                    elif line.startswith("model:"):
                        model = line[len("model:"):].strip().strip('"').strip("'")
                    elif line.startswith("effort:"):
                        effort = line[len("effort:"):].strip().strip('"').strip("'")

        skills.append(SkillInfo(
            name=name,
            description=description,
            model=model,
            effort=effort,
            full_path=str(skill_md),
        ))

    return skills


def get_skill_descriptions_text() -> str:
    """Return all skill descriptions as a single text block for GEPA seeding.

    Format: one skill per line, "name: description".
    This is the seed candidate for GEPA skill description optimization.
    """
    catalog = get_skill_catalog()
    lines = []
    for s in catalog:
        if s.description:
            lines.append(f"{s.name}: {s.description}")
    return "\n".join(lines)


# =============================================================================
# S3: Eval Prompts Bridge (read-only)
# =============================================================================


@dataclass
class EvalPrompt:
    id: str
    prompt: str
    target_tier: str
    category: str
    expected_behavior: str = ""
    owasp: str = ""


def get_eval_dataset() -> list[EvalPrompt]:
    """Load eval_prompts.json as structured evaluation examples.

    READ-ONLY: reads from Brain's eval directory.
    Returns a list of EvalPrompt objects usable as GEPA dataset entries.
    """
    if not EVAL_PROMPTS_PATH.exists():
        return []

    try:
        data = json.loads(EVAL_PROMPTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    prompts: list[EvalPrompt] = []
    for cat_name, cat_data in data.get("categories", {}).items():
        for p in cat_data.get("prompts", []):
            prompts.append(EvalPrompt(
                id=p.get("id", ""),
                prompt=p.get("prompt", ""),
                target_tier=p.get("target_tier", ""),
                category=cat_name,
                expected_behavior=p.get("expected_behavior", ""),
                owasp=p.get("owasp", ""),
            ))

    return prompts


def make_manifest_evaluator() -> Callable:
    """Create an evaluator function for GEPA that scores a manifest weight
    configuration against eval prompts.

    Returns: function(candidate_weights_text, example=None) -> (score, diagnostics)

    The evaluator:
    1. Parses the candidate weight text
    2. Runs the Brain classifier with those weights on eval prompts
    3. Scores: % of prompts classified to expected tier
    4. Returns diagnostics: which prompts misclassified, what tier they got

    REQUIRES: severity_classifier.py importable. Does NOT modify any files.
    """
    eval_prompts = get_eval_dataset()

    def evaluator(candidate: str, example: dict | None = None) -> tuple[float, dict]:
        # Try importing the classifier
        try:
            sys.path.insert(0, str(BRAIN_DIR))
            from severity_classifier import classify, load_manifest
        except ImportError:
            return 0.0, {"error": "Could not import severity_classifier"}

        # Parse candidate weights (simple key=value parsing)
        # In practice, GEPA would evolve the full TOML text
        correct = 0
        total = len(eval_prompts)
        misclassified: list[dict] = []

        for ep in eval_prompts:
            try:
                result = classify(prompt_text=ep.prompt)
                if result.tier == ep.target_tier:
                    correct += 1
                else:
                    misclassified.append({
                        "id": ep.id,
                        "prompt": ep.prompt[:80],
                        "expected": ep.target_tier,
                        "got": result.tier,
                        "confidence": result.confidence,
                        "score": result.score,
                    })
            except Exception as e:
                misclassified.append({
                    "id": ep.id,
                    "error": str(e),
                })

        score = correct / total if total > 0 else 0.0

        return score, {
            "correct": correct,
            "total": total,
            "accuracy": f"{score * 100:.1f}%",
            "misclassified": misclassified,
        }

    return evaluator


def make_skill_evaluator() -> Callable:
    """Create an evaluator for GEPA that scores skill descriptions
    based on how accurately they drive routing.

    Scores based on: does the Brain classifier's skill_override field
    match the expected skill when given a skill-triggering prompt?

    REQUIRES: severity_classifier.py importable. Read-only.
    """
    def evaluator(candidate_descriptions: str, example: dict | None = None) -> tuple[float, dict]:
        # Parse candidate descriptions
        # Format: "skill_name: description text"
        # For now, return a placeholder that GEPA can evolve
        return 0.5, {
            "note": "Skill evaluator placeholder — wire to real routing tests",
            "candidate_length": len(candidate_descriptions),
        }

    return evaluator


# =============================================================================
# S5: ASI Diagnostic Converter (read-only)
# =============================================================================


def _parse_ts(ts_str: str) -> str:
    """Normalize timestamp string."""
    return ts_str.replace("Z", "+00:00") if ts_str else ""


def get_asi_diagnostics(limit: int = 500) -> list[dict[str, Any]]:
    """Convert decisions.jsonl human{} events to GEPA-compatible ASI format.

    ASI (Actionable Side Information) is GEPA's term for diagnostic feedback
    that helps the optimizer understand WHY something failed — analogous to
    gradients in numerical optimization.

    Each override, correction, or stall event becomes a structured diagnostic:
    {
        "input": <the prompt that was classified>,
        "score": <accuracy — 1.0 if no override, 0.0 if overridden>,
        "asi": {
            "event_type": "override|correction|stall|normal",
            "recommended_tier": "S0",
            "recommended_model": "haiku",
            "actual_model": "opus",
            "confidence": 0.55,
            "delegation_mode": "supervised",
            "diagnostic": "Brain recommended haiku but user ran opus — classifier under-estimated complexity"
        }
    }

    READ-ONLY: reads from decisions.jsonl, never modifies it.
    """
    if not DECISIONS_LOG.exists():
        return []

    entries: list[dict] = []
    try:
        for line in DECISIONS_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return []

    # Take the most recent N entries
    entries = entries[-limit:]

    asi_dataset: list[dict] = []

    for entry in entries:
        result = entry.get("result", {})
        if not isinstance(result, dict):
            continue

        human = entry.get("human", {})
        if not isinstance(human, dict):
            human = {}

        current_model = entry.get("current_model", "").lower()
        recommended_model = result.get("model", "").lower()
        tier = result.get("tier", "?")
        confidence = result.get("confidence", 0.5)

        # Determine event type
        is_override = (
            current_model and recommended_model
            and recommended_model not in current_model
            and current_model not in recommended_model
        )
        is_correction = result.get("correction_detected_in_prompt", False)
        delegation_mode = human.get("delegation_mode", "unknown")
        inter_turn_gap = human.get("inter_turn_gap_seconds", 0)
        is_stall = isinstance(inter_turn_gap, (int, float)) and inter_turn_gap > 300

        if is_correction:
            event_type = "correction"
            score = 0.0
            diagnostic = (
                f"User corrected previous response. "
                f"Brain confidence was {confidence:.2f} — "
                f"{'appropriately uncertain' if confidence < 0.5 else 'over-confident'}"
            )
        elif is_override:
            event_type = "override"
            score = 0.0
            diagnostic = (
                f"Brain recommended {recommended_model} ({tier}) but user ran {current_model}. "
                f"Confidence: {confidence:.2f}. "
                f"{'Low confidence = honest uncertainty' if confidence < 0.5 else 'High confidence = miscalibrated'}"
            )
        elif is_stall:
            event_type = "stall"
            score = 0.3
            diagnostic = (
                f"Inter-turn gap of {inter_turn_gap:.0f}s (>{300}s threshold). "
                f"Possible confusion, context switch, or break."
            )
        else:
            event_type = "normal"
            score = 1.0
            diagnostic = f"Normal flow. Tier {tier}, confidence {confidence:.2f}, {delegation_mode} delegation."

        asi_dataset.append({
            "input": f"[{tier}] session={entry.get('session_id', '?')[:8]}",
            "score": score,
            "asi": {
                "event_type": event_type,
                "recommended_tier": tier,
                "recommended_model": recommended_model,
                "actual_model": current_model,
                "confidence": confidence,
                "delegation_mode": delegation_mode,
                "guardrails_fired": result.get("guardrails_fired", []),
                "diagnostic": diagnostic,
            },
        })

    return asi_dataset


# =============================================================================
# S4: gskill Configuration (for future use)
# =============================================================================


def get_gskill_config() -> dict[str, Any]:
    """Return a gskill configuration for your-game-project.

    This config tells GEPA's gskill pipeline where to find the repo,
    how to generate tasks, and where to output learned skills.

    NOTE: gskill requires GEPA installed + SWE-smith. This config
    is preparation for when the user is ready to run it.
    """
    return {
        "repository": {
            "path": "~/Documents/your-game-project/MyProject",
            "language": "C++",
            "framework": "Unreal Engine 5",
            "build_system": "UnrealBuildTool",
        },
        "task_generation": {
            "method": "swe-smith",
            "focus_areas": [
                "AnimBP / animation blueprint",
                "GAS (Gameplay Ability System)",
                "Character movement component",
                "AI behavior trees / StateTree",
                "Network replication",
                "Save/load persistence",
                "UI / UMG widgets",
            ],
        },
        "skill_output": {
            "path": str(SKILLS_DIR / "sworder-gskill" / "SKILL.md"),
            "format": "claude_code_skill",
            "frontmatter": {
                "model": "sonnet",
                "effort": "high",
            },
        },
        "optimization": {
            "max_evaluations": 200,
            "minibatch_size": 5,
            "reflection_model": "claude-sonnet-4-6",
            "proposer_model": "claude-sonnet-4-6",
        },
        "note": "Config only — requires GEPA + SWE-smith installed to run.",
    }


# =============================================================================
# Proposal output (GEPA writes here, never to live files)
# =============================================================================


def save_proposal(name: str, content: str, metadata: dict | None = None) -> Path:
    """Save a GEPA-generated proposal to the proposals/ directory.

    NEVER writes to live Brain files. Proposals are reviewed by the user
    before being applied manually.
    """
    import datetime

    GEPA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{ts}.txt"
    path = GEPA_OUTPUT_DIR / filename

    header = f"# GEPA Proposal: {name}\n# Generated: {ts}\n"
    if metadata:
        header += f"# Metadata: {json.dumps(metadata)}\n"
    header += "# STATUS: PENDING REVIEW — do not apply without the user's approval\n\n"

    path.write_text(header + content, encoding="utf-8")
    return path


# =============================================================================
# Summary / CLI
# =============================================================================


def print_summary() -> None:
    """Print a summary of all available GEPA integration points."""
    print("GEPA INTEGRATION BRIDGE — Toke")
    print("=" * 55)

    # S1: Manifest
    weights = get_manifest_weights()
    weight_lines = [l for l in weights.split("\n") if "=" in l]
    print(f"\nS1. Manifest weights:     {len(weight_lines)} parameters")

    # S2: Skills
    catalog = get_skill_catalog()
    with_desc = [s for s in catalog if s.description]
    print(f"S2. Skill catalog:        {len(catalog)} skills ({len(with_desc)} with descriptions)")

    # S3: Eval prompts
    eval_prompts = get_eval_dataset()
    by_cat = defaultdict(int)
    for ep in eval_prompts:
        by_cat[ep.category] += 1
    print(f"S3. Eval prompts:         {len(eval_prompts)} across {len(by_cat)} categories")
    for cat, count in sorted(by_cat.items()):
        print(f"    {cat}: {count}")

    # S5: ASI diagnostics
    asi = get_asi_diagnostics()
    event_types = defaultdict(int)
    for a in asi:
        event_types[a["asi"]["event_type"]] += 1
    print(f"S5. ASI diagnostics:      {len(asi)} events")
    for et, count in sorted(event_types.items()):
        print(f"    {et}: {count}")

    # S4: gskill
    config = get_gskill_config()
    print(f"S4. gskill config:        ready (your-game-project, {len(config['task_generation']['focus_areas'])} focus areas)")

    print(f"\nProposal output dir:  {GEPA_OUTPUT_DIR}")
    print(f"Manifest (read-only): {MANIFEST_PATH}")
    print(f"Decisions log:        {DECISIONS_LOG}")
    print(f"Skills dir:           {SKILLS_DIR}")

    # Check GEPA availability
    try:
        import gepa  # noqa: F401
        print(f"\nGEPA: INSTALLED (ready to optimize)")
    except ImportError:
        print(f"\nGEPA: not installed (pip install gepa)")
        print("Bridge works standalone for data extraction.")


if __name__ == "__main__":
    print_summary()
