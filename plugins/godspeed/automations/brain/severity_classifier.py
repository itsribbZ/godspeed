#!/usr/bin/env python3
"""
Toke Brain — Severity Classifier
================================
Pure-Python stdlib-only classifier for routing Claude Code tasks to the
cheapest model that preserves quality. No LLM calls. No dependencies beyond
Python 3.11+ stdlib (for tomllib).

Design contract: Toke/research/brain_synthesis_2026-04-10.md (sections 3-6)

CLI:
    echo '{"prompt_text": "list files"}' | python3 severity_classifier.py

Library:
    from severity_classifier import classify
    result = classify(prompt_text="...", context_tokens=1000)
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

# Ensure UTF-8 stdout/stderr on Windows (cp1252 default breaks on em-dashes, arrows, etc.)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DEFAULT_MANIFEST_PATH = Path(__file__).parent / "routing_manifest.toml"


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    tier: str
    model: str
    effort: str
    score: float
    signals: dict[str, float]
    guardrails_fired: list[str]
    skill_override: str | None
    reasoning: str
    # v2.0 fields
    confidence: float = 1.0                     # 0.0-1.0; how far from nearest tier boundary
    extended_thinking_budget: int = 0           # tokens; 0 = disabled
    uncertainty_escalated: bool = False         # True if low confidence bumped the tier
    context_turns_seen: int = 0                 # how many prior session turns informed this decision
    correction_detected_in_prompt: bool = False # True if prompt looks like a correction follow-up

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Token estimation
# -----------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Character-to-token estimate: ~4 chars per token for English."""
    return max(len(text) // 4, 0)


# -----------------------------------------------------------------------------
# Signal extractors (regex only, sub-millisecond)
# -----------------------------------------------------------------------------


_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_FILE_REF_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s`'\"]+"
    r"|(?:\./|~/|/)[^\s`'\"]+"
    r"|@[A-Za-z0-9_./-]+"
    r"|\b[\w-]+\.(?:py|ts|tsx|js|jsx|cpp|c|h|hpp|cs|go|rs|rb|java|md|json|jsonl|toml|yaml|yml|sh|bash|sql|html|css)\b)"
)


def count_code_blocks(text: str) -> int:
    return len(_CODE_BLOCK_RE.findall(text))


def count_file_refs(text: str) -> int:
    return len(_FILE_REF_RE.findall(text))


def count_keywords(text: str, keywords: list[str]) -> int:
    """Case-insensitive. Multi-word phrases use substring; single words use \\b boundaries."""
    if not keywords:
        return 0
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if not kw_lower:
            continue
        if " " in kw_lower or "-" in kw_lower or "." in kw_lower:
            count += text_lower.count(kw_lower)
        else:
            count += len(re.findall(rf"\b{re.escape(kw_lower)}\b", text_lower))
    return count


# -----------------------------------------------------------------------------
# Signal computation
# -----------------------------------------------------------------------------


def compute_signals(
    prompt_text: str,
    context_tokens: int,
    manifest: dict[str, Any],
) -> dict[str, float]:
    """Extract and normalize all signals to 0.0-1.0 floats."""
    prompt_tokens = estimate_tokens(prompt_text)
    keywords = manifest.get("keywords", {})
    norms = manifest.get("normalization", {})

    def norm(value: float, cap_key: str, default: float) -> float:
        cap = float(norms.get(cap_key, default))
        return min(value / cap, 1.0) if cap > 0 else 0.0

    return {
        "prompt_length": norm(prompt_tokens, "prompt_length_cap", 500),
        "code_blocks": norm(count_code_blocks(prompt_text), "code_blocks_cap", 3),
        "file_refs": norm(count_file_refs(prompt_text), "file_refs_cap", 5),
        "reasoning": norm(count_keywords(prompt_text, keywords.get("reasoning", [])), "reasoning_cap", 5),
        "multi_step": norm(count_keywords(prompt_text, keywords.get("multi_step", [])), "multi_step_cap", 3),
        "ambiguity": norm(count_keywords(prompt_text, keywords.get("ambiguity", [])), "ambiguity_cap", 3),
        "tool_calls": norm(count_keywords(prompt_text, keywords.get("tool_calls", [])), "tool_calls_cap", 4),
        "context_size": norm(float(context_tokens), "context_tokens_cap", 150000),
        "code_action": norm(count_keywords(prompt_text, keywords.get("code_action", [])), "code_action_cap", 2),
        "system_scope": norm(count_keywords(prompt_text, keywords.get("system_scope", [])), "system_scope_cap", 2),
    }


# -----------------------------------------------------------------------------
# Guardrail evaluation
# -----------------------------------------------------------------------------


def guardrail_fires(
    prompt_text: str,
    context_tokens: int,
    guardrail_def: dict[str, Any],
    cwd_domain: str | None = None,
) -> bool:
    """Evaluate a guardrail.

    Default mode: ANY condition firing is enough (keywords OR min_file_refs OR ...).
    If `require_all = true` in the definition, ALL specified conditions must fire.
    v2.4: domain_tags — if guardrail specifies domain_tags and CWD domain doesn't match, suppress.
    """
    # v2.4: domain-scoped guardrails — suppress when CWD doesn't match
    domain_tags = guardrail_def.get("domain_tags")
    if domain_tags and cwd_domain and cwd_domain not in domain_tags:
        return False

    checks: list[bool] = []

    keywords = guardrail_def.get("keywords")
    if keywords:
        checks.append(count_keywords(prompt_text, keywords) > 0)

    min_file_refs = guardrail_def.get("min_file_refs")
    if min_file_refs is not None:
        checks.append(count_file_refs(prompt_text) >= int(min_file_refs))

    min_context_tokens = guardrail_def.get("min_context_tokens")
    if min_context_tokens is not None:
        checks.append(context_tokens >= int(min_context_tokens))

    regex_patterns = guardrail_def.get("regex")
    if regex_patterns:
        checks.append(any(re.search(p, prompt_text, re.IGNORECASE) for p in regex_patterns))

    if not checks:
        return False

    if guardrail_def.get("require_all", False):
        return all(checks)
    return any(checks)


# -----------------------------------------------------------------------------
# Tier mapping
# -----------------------------------------------------------------------------


def score_to_tier(score: float, thresholds: dict[str, float]) -> str:
    """Map a final score to a tier name."""
    if score < thresholds.get("s0_max", 0.08):
        return "S0"
    if score < thresholds.get("s1_max", 0.18):
        return "S1"
    if score < thresholds.get("s2_max", 0.35):
        return "S2"
    if score < thresholds.get("s3_max", 0.55):
        return "S3"
    if score < thresholds.get("s4_max", 0.80):
        return "S4"
    return "S5"


def compute_confidence(final_score: float, thresholds: dict[str, float]) -> float:
    """Return 0.0-1.0 confidence based on distance to nearest tier boundary.

    1.0 = very confident (0.1+ distance to any boundary)
    0.0 = uncertain (right at a boundary)

    v2.0: used for uncertainty escalation and low-confidence flagging.
    """
    boundaries = [
        thresholds.get("s0_max", 0.08),
        thresholds.get("s1_max", 0.18),
        thresholds.get("s2_max", 0.35),
        thresholds.get("s3_max", 0.55),
        thresholds.get("s4_max", 0.80),
    ]
    min_dist = min(abs(final_score - b) for b in boundaries)
    return round(min(min_dist / 0.10, 1.0), 3)


def _bump_tier(tier: str) -> str:
    """Return the next tier up (S2 -> S3, S3 -> S4, etc.). S5 stays S5."""
    order = ["S0", "S1", "S2", "S3", "S4", "S5"]
    try:
        idx = order.index(tier)
    except ValueError:
        return "S4"  # unknown tier -> escalate conservatively
    return order[min(idx + 1, len(order) - 1)]


def _detect_correction_in_text(text: str, keywords: list[str]) -> bool:
    """Return True if text contains any correction keyword (case-insensitive)."""
    if not text or not keywords:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


# -----------------------------------------------------------------------------
# Main classifier
# -----------------------------------------------------------------------------


def _detect_project_domain(cwd: str | None) -> str | None:
    """Detect project domain from the working directory path.

    Returns a domain tag (currently only "ue5") or None if unknown.
    Used to scope domain-specific guardrails so they don't fire in unrelated projects.
    """
    if not cwd:
        return None
    cwd_lower = cwd.lower().replace("\\", "/")
    # UE5 project detection: .uproject file marker in path
    if ".uproject" in cwd_lower:
        return "ue5"
    return None


def classify(
    prompt_text: str = "",
    context_tokens: int = 0,
    skill_name: str | None = None,
    current_model: str | None = None,
    manifest: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
    context_history: list[dict[str, Any]] | None = None,
    session_max_tier: str | None = None,
    cwd: str | None = None,
) -> ClassificationResult:
    """Classify a task and return the routing decision.

    v2.0: fails open to S4 (Opus, high) — quality over cost when we don't know.
    v2.0: accepts context_history (list of recent session decisions) for multi-turn awareness.
    v2.1: session_max_tier — high-water mark tier for continuation prompt flooring.
    v2.4: cwd — working directory for domain-scoped guardrails (fixes cross-project false positives).
    """
    if manifest is None:
        try:
            manifest = load_manifest(manifest_path or DEFAULT_MANIFEST_PATH)
        except Exception:
            # v2.0: fail open to S4 (Opus, high) instead of S3 — quality over cost
            return ClassificationResult(
                tier="S4",
                model="opus",
                effort="high",
                score=0.85,
                signals={},
                guardrails_fired=["manifest_load_failed"],
                skill_override=None,
                reasoning="manifest load failed - fail-open to S4 (Opus,high) for quality preservation",
                confidence=0.0,
                extended_thinking_budget=32000,
                uncertainty_escalated=True,
                context_turns_seen=0,
                correction_detected_in_prompt=False,
            )

    signals = compute_signals(prompt_text, context_tokens, manifest)
    weights = manifest.get("weights", {})

    base_score = sum(signals[k] * float(weights.get(k, 0.0)) for k in signals)

    # v2.4: detect CWD domain for guardrail scoping
    cwd_domain = _detect_project_domain(cwd)

    guardrails_fired: list[str] = []
    for g_name, g_def in manifest.get("guardrails", {}).items():
        if guardrail_fires(prompt_text, context_tokens, g_def, cwd_domain=cwd_domain):
            guardrails_fired.append(g_name)
            base_score = max(base_score, float(g_def.get("min_score", 0.0)))

    # v2.6: ceiling guardrails — cap score when specificity patterns detected
    for g_name, g_def in manifest.get("ceiling_guardrails", {}).items():
        if guardrail_fires(prompt_text, context_tokens, g_def, cwd_domain=cwd_domain):
            guardrails_fired.append(g_name)
            base_score = min(base_score, float(g_def.get("max_score", 1.0)))

    final_score = min(max(base_score, 0.0), 1.0)

    tier_from_score = score_to_tier(final_score, manifest.get("thresholds", {}))

    skill_override: str | None = None
    skill_map = manifest.get("skills", {})
    if skill_name and skill_name in skill_map:
        skill_override = skill_map[skill_name]
        tier = skill_override
    else:
        tier = tier_from_score

    # v2.0: compute confidence and apply uncertainty escalation
    thresholds = manifest.get("thresholds", {})
    confidence = compute_confidence(final_score, thresholds)

    uncertainty_cfg = manifest.get("uncertainty", {})
    escalate_on_uncertain = uncertainty_cfg.get("escalate_on_low_confidence", False)
    learning_cfg = manifest.get("learning", {})
    low_conf_threshold = float(learning_cfg.get("confidence_low_threshold", 0.30))

    uncertainty_escalated = False
    if (
        escalate_on_uncertain
        and confidence < low_conf_threshold
        and not skill_override
        and not guardrails_fired
        and tier not in ("S4", "S5")
    ):
        tier = _bump_tier(tier)
        uncertainty_escalated = True

    # v2.0: correction detection in the incoming prompt
    correction_keywords = (manifest.get("keywords", {}) or {}).get("correction", [])
    correction_detected = _detect_correction_in_text(prompt_text, correction_keywords)

    # v2.0: multi-turn context awareness — if last turn showed low confidence
    # or was overridden, bump current tier up one step (defense against repeat mistakes)
    context_turns_seen = 0
    if context_history:
        context_turns_seen = len(context_history)
        last = context_history[-1] if context_history else None
        if last:
            last_result = last.get("result") or {}
            last_overridden = last.get("current_model", "") and (
                last.get("current_model", "").lower()
                not in (last_result.get("model") or "").lower()
            )
            last_correction = last_result.get("correction_detected_in_prompt", False)
            if (last_overridden or last_correction or correction_detected) and tier not in ("S4", "S5"):
                tier = _bump_tier(tier)
                uncertainty_escalated = True

    # v2.5: session_turn_depth weighting — deep sessions rarely stay at S0.
    # After 8+ turns the session has established working context; S0 is almost always
    # a misclassified short continuation ("ok", "next", "do it"). Bump S0→S1.
    # After 15+ turns, S0 and S1 are both suspect — bump to S2 floor.
    if context_turns_seen >= 15 and tier in ("S0", "S1") and not skill_override:
        tier = "S2"
        uncertainty_escalated = True
    elif context_turns_seen >= 8 and tier == "S0" and not skill_override:
        tier = _bump_tier(tier)  # S0 → S1
        uncertainty_escalated = True

    # v2.1: session high-water mark — continuation prompts inherit session baseline.
    # Short prompts ("go", "continue", "next") mid-session should not drop to S0/S1
    # when the session is running S3+ complexity work. Floor = max_tier - 1.
    # v2.4: widened from 60 to 120 chars — catches more continuation prompts
    # like "ok do all 5" or "sounds good, hit those" which are 40-80 chars.
    if session_max_tier and len(prompt_text) <= 120:
        _tier_order = ["S0", "S1", "S2", "S3", "S4", "S5"]
        try:
            max_idx = _tier_order.index(session_max_tier)
            cur_idx = _tier_order.index(tier)
            floor_idx = max(max_idx - 1, 0)
            if max_idx >= 3 and cur_idx < floor_idx:
                tier = _tier_order[floor_idx]
                uncertainty_escalated = True
        except ValueError:
            pass

    tier_map = manifest.get("tier_map", {})
    tier_cfg = tier_map.get(tier, {})
    model = tier_cfg.get("model", "sonnet")
    effort = tier_cfg.get("effort", "high")
    extended_thinking_budget = int(tier_cfg.get("extended_thinking_budget", 0))

    # Build human-readable reasoning (ASCII only to avoid cp1252 encoding crashes on Windows)
    parts: list[str] = []
    if skill_override:
        parts.append(f"skill:{skill_name}->{skill_override}")
    else:
        parts.append(f"score={final_score:.3f}->{tier}")
        top_signals = sorted(signals.items(), key=lambda kv: kv[1], reverse=True)
        top_active = [f"{k}={v:.2f}" for k, v in top_signals if v > 0.01][:3]
        if top_active:
            parts.append("top:" + ",".join(top_active))
    if guardrails_fired:
        parts.append("guards:" + "+".join(guardrails_fired))
    if uncertainty_escalated:
        parts.append(f"escalated:conf={confidence:.2f}")
    if correction_detected:
        parts.append("correction_follow")
    if extended_thinking_budget > 0:
        parts.append(f"thinking:{extended_thinking_budget}")
    if context_turns_seen > 0:
        parts.append(f"ctx:{context_turns_seen}turns")
    reasoning = " | ".join(parts)

    return ClassificationResult(
        tier=tier,
        model=model,
        effort=effort,
        score=round(final_score, 3),
        signals={k: round(v, 3) for k, v in signals.items()},
        guardrails_fired=guardrails_fired,
        skill_override=skill_override,
        reasoning=reasoning,
        confidence=confidence,
        extended_thinking_budget=extended_thinking_budget,
        uncertainty_escalated=uncertainty_escalated,
        context_turns_seen=context_turns_seen,
        correction_detected_in_prompt=correction_detected,
    )


# -----------------------------------------------------------------------------
# Manifest loading
# -----------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the TOML manifest. Raises on missing or malformed files."""
    if not path.exists():
        raise FileNotFoundError(f"Brain manifest not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


# -----------------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------------


def _read_stdin_json() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f'{{"error": "invalid JSON on stdin: {e}"}}', file=sys.stderr)
        sys.exit(2)


def main() -> int:
    payload = _read_stdin_json()
    try:
        result = classify(
            prompt_text=payload.get("prompt_text", ""),
            context_tokens=int(payload.get("context_tokens", 0)),
            skill_name=payload.get("skill_name"),
            current_model=payload.get("current_model"),
        )
    except Exception as e:
        print(f'{{"error": "{type(e).__name__}: {e}"}}', file=sys.stderr)
        return 1
    print(json.dumps(result.to_json(), sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
