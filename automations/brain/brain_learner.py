#!/usr/bin/env python3
"""
Toke Brain v2.0 — Learning Module
=================================
EWMA weight updater + override detection + skill auto-bump + drift computation
+ correction detection + session cost estimator. Pure Python stdlib only.

Reads: ~/.claude/telemetry/brain/decisions.jsonl
       ~/.claude/telemetry/brain/tools.jsonl
Writes: ~/.claude/telemetry/brain/signal_posteriors.json (optional cache)

Contract with brain_cli.py:
    from brain_learner import (
        read_decisions, read_tools,
        detect_correction, detect_overrides,
        compute_tier_drift, compute_session_cost,
        compute_skill_override_counts, ewma_update,
        summarize_learning_state,
    )
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Ensure UTF-8 stdout/stderr on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


TELEMETRY_DIR = Path.home() / ".claude" / "telemetry" / "brain"
DECISIONS_LOG = TELEMETRY_DIR / "decisions.jsonl"
TOOLS_LOG = TELEMETRY_DIR / "tools.jsonl"
POSTERIORS_FILE = TELEMETRY_DIR / "signal_posteriors.json"
OVERRIDES_CACHE = TELEMETRY_DIR / "overrides_cache.json"


# =============================================================================
# JSONL reading primitives
# =============================================================================


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read a JSONL file, newest first. Safely skips malformed lines."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = raw.strip().split("\n")
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if limit and len(entries) >= limit:
            break
    return entries


def read_decisions(limit: int | None = None) -> list[dict[str, Any]]:
    """Read decisions.jsonl newest first."""
    return _read_jsonl(DECISIONS_LOG, limit)


def read_tools(limit: int | None = None) -> list[dict[str, Any]]:
    """Read tools.jsonl newest first."""
    return _read_jsonl(TOOLS_LOG, limit)


# =============================================================================
# Correction detection — implicit negative signal
# =============================================================================


DEFAULT_CORRECTION_KEYWORDS = [
    "that's wrong",
    "thats wrong",
    "you missed",
    "redo",
    "fix this",
    "not right",
    "incorrect",
    "undo",
    "nevermind",
    "wrong answer",
    "try again",
    "no that",
    "actually no",
    "that is wrong",
    "doesn't work",
    "didnt work",
    "didn't work",
]


def detect_correction(prompt_text: str, keywords: list[str] | None = None) -> bool:
    """Return True if prompt contains any correction keyword (case-insensitive substring)."""
    if not prompt_text:
        return False
    kws = keywords if keywords is not None else DEFAULT_CORRECTION_KEYWORDS
    text_lower = prompt_text.lower()
    return any(kw.lower() in text_lower for kw in kws)


# =============================================================================
# Override detection — did user manually switch model after our recommendation
# =============================================================================


def detect_overrides(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find decisions where recommended model != actual model on next turn in same session.

    Returns list of {session_id, recommended, actual, ts} dicts.
    Telemetry is newest-first; we pair each decision with the NEXT chronological decision.
    """
    overrides: list[dict[str, Any]] = []
    chrono = list(reversed(decisions))
    for i in range(len(chrono) - 1):
        cur = chrono[i]
        nxt = chrono[i + 1]
        if cur.get("session_id") != nxt.get("session_id"):
            continue
        cur_result = cur.get("result") or {}
        recommended = (cur_result.get("model") or "").lower()
        actual = (nxt.get("current_model") or "").lower()
        if not recommended or not actual:
            continue
        if recommended not in actual and actual not in recommended:
            overrides.append({
                "session_id": cur.get("session_id", ""),
                "recommended": recommended,
                "actual": actual,
                "ts": cur.get("ts", ""),
                "tier": cur_result.get("tier", ""),
                "skill_override": cur_result.get("skill_override"),
            })
    return overrides


def compute_skill_override_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    """Count overrides per skill across all decisions. Skill auto-bump reads this."""
    overrides = detect_overrides(decisions)
    per_skill: dict[str, int] = defaultdict(int)
    for o in overrides:
        skill = o.get("skill_override")
        if skill:
            per_skill[skill] += 1
    return dict(per_skill)


# =============================================================================
# Correction-follow detection — did a correction follow a routing decision
# =============================================================================


def decisions_with_correction_follow(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return decisions that were followed by a correction keyword in the next prompt."""
    results: list[dict[str, Any]] = []
    chrono = list(reversed(decisions))
    for i in range(len(chrono) - 1):
        cur = chrono[i]
        nxt = chrono[i + 1]
        if cur.get("session_id") != nxt.get("session_id"):
            continue
        next_prompt = _extract_prompt(nxt)
        if detect_correction(next_prompt):
            results.append(cur)
    return results


def _extract_prompt(entry: dict[str, Any]) -> str:
    """Best-effort extraction of prompt text from a decision entry."""
    # Try several known shapes
    if "prompt" in entry:
        return entry["prompt"] or ""
    result = entry.get("result") or {}
    if "prompt_text" in result:
        return result["prompt_text"] or ""
    return ""


# =============================================================================
# Tier drift detection — weekly vs monthly distribution
# =============================================================================


def compute_tier_drift(
    decisions: list[dict[str, Any]],
    short_days: int = 7,
    long_days: int = 30,
) -> dict[str, Any]:
    """Compare recent tier distribution against baseline distribution.

    Returns {tier: delta_percentage_points, ...} plus totals.
    Positive delta = more frequent recently; negative = less frequent.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    short_cutoff = now - datetime.timedelta(days=short_days)
    long_cutoff = now - datetime.timedelta(days=long_days)

    short_counts: dict[str, int] = defaultdict(int)
    long_counts: dict[str, int] = defaultdict(int)

    for d in decisions:
        ts_str = d.get("ts", "")
        if not ts_str:
            continue
        try:
            ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        result = d.get("result") or {}
        tier = result.get("tier")
        if not tier:
            continue
        if ts >= long_cutoff:
            long_counts[tier] += 1
        if ts >= short_cutoff:
            short_counts[tier] += 1

    short_total = sum(short_counts.values()) or 1
    long_total = sum(long_counts.values()) or 1

    drift: dict[str, float] = {}
    for tier in ["S0", "S1", "S2", "S3", "S4", "S5"]:
        short_pct = short_counts.get(tier, 0) / short_total * 100
        long_pct = long_counts.get(tier, 0) / long_total * 100
        drift[tier] = round(short_pct - long_pct, 2)

    return {
        "drift_pp": drift,
        "short_window_days": short_days,
        "short_window_total": short_total,
        "long_window_days": long_days,
        "long_window_total": long_total,
    }


# =============================================================================
# Session cost estimator
# =============================================================================


def compute_session_cost(
    tools: list[dict[str, Any]],
    session_id: str,
    pricing: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Estimate cost of a session from tool call telemetry.

    pricing must be the `models` table from routing_manifest.toml.
    """
    session_tools = [t for t in tools if t.get("session_id") == session_id]
    total_cost = 0.0
    by_model: dict[str, float] = defaultdict(float)
    by_model_tokens: dict[str, int] = defaultdict(int)

    for t in session_tools:
        model = (t.get("model") or "").lower()
        in_size = t.get("input_size", 0) or 0
        out_size = t.get("output_size", 0) or 0

        if "opus" in model:
            prices = pricing.get("opus", {})
        elif "sonnet" in model:
            prices = pricing.get("sonnet", {})
        elif "haiku" in model:
            prices = pricing.get("haiku", {})
        else:
            continue

        # Rough estimate: sizes are byte-counts of JSON; 4 chars per token.
        in_tok = in_size / 4
        out_tok = out_size / 4
        cost = (
            (in_tok / 1_000_000) * prices.get("cost_input_per_mtok", 0)
            + (out_tok / 1_000_000) * prices.get("cost_output_per_mtok", 0)
        )
        total_cost += cost
        by_model[model] += cost
        by_model_tokens[model] += int(in_tok + out_tok)

    return {
        "session_id": session_id,
        "tool_count": len(session_tools),
        "total_cost_usd": round(total_cost, 4),
        "by_model": {k: round(v, 4) for k, v in by_model.items()},
        "by_model_tokens": dict(by_model_tokens),
    }


def compute_active_session_cost(
    tools: list[dict[str, Any]],
    pricing: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Compute cost for the CURRENT session (most recent session_id in telemetry)."""
    if not tools:
        return {"session_id": None, "tool_count": 0, "total_cost_usd": 0.0, "by_model": {}, "by_model_tokens": {}}
    # Most recent tool entry gives us the active session
    latest_session = tools[0].get("session_id")
    if not latest_session:
        return {"session_id": None, "tool_count": 0, "total_cost_usd": 0.0, "by_model": {}, "by_model_tokens": {}}
    return compute_session_cost(tools, latest_session, pricing)


# =============================================================================
# EWMA weight updater
# =============================================================================


def ewma_update(current_weight: float, label: int, alpha: float = 0.005) -> float:
    """Exponentially weighted moving average update.

    label: +1 for positive signal (keep current behavior), -1 for negative (penalize).
    alpha: learning rate (default 0.005 for stability at the user's scale).
    """
    return round(current_weight + alpha * label, 6)


def propose_weight_adjustments(
    current_weights: dict[str, float],
    override_events: list[dict[str, Any]],
    alpha: float = 0.005,
) -> dict[str, float]:
    """Compute proposed weight adjustments based on override events.

    For each override, infer which signal MOST contributed to the bad recommendation
    and nudge it DOWN. Then suggest new weights.

    This is a conservative tuner: we only adjust the single dominant signal per override,
    and alpha is small, so dozens of overrides are needed before meaningful change.
    """
    adjusted = dict(current_weights)
    for o in override_events:
        tier = o.get("tier", "")
        # Without per-decision signal breakdown in telemetry, use tier as a proxy:
        # If user overrode DOWN (recommended Opus, used Sonnet), the reasoning signals
        # were too aggressive -> decrement reasoning weight.
        # If user overrode UP (recommended Haiku, used Opus), prompt_length or reasoning
        # were too permissive -> increment.
        recommended = (o.get("recommended") or "").lower()
        actual = (o.get("actual") or "").lower()
        direction = 0
        if "opus" in recommended and ("sonnet" in actual or "haiku" in actual):
            direction = -1  # over-escalated
        elif "haiku" in recommended and ("sonnet" in actual or "opus" in actual):
            direction = +1  # under-escalated
        elif "sonnet" in recommended and "opus" in actual:
            direction = +1  # under-escalated
        elif "sonnet" in recommended and "haiku" in actual:
            direction = -1  # over-escalated
        if direction == 0:
            continue
        # Apply to reasoning + file_refs as the dominant code-task signals
        for key in ("reasoning", "file_refs"):
            if key in adjusted:
                adjusted[key] = ewma_update(adjusted[key], direction, alpha)
    return adjusted


# =============================================================================
# Confidence scoring
# =============================================================================


def compute_confidence(final_score: float, thresholds: dict[str, float]) -> float:
    """Compute confidence based on distance from the nearest tier boundary.

    1.0 = very confident (score far from any boundary).
    0.0 = low confidence (score right on a boundary).

    The scale is: 0.1 score-distance from boundary = confidence 1.0.
    """
    boundaries = [
        thresholds.get("s0_max", 0.08),
        thresholds.get("s1_max", 0.18),
        thresholds.get("s2_max", 0.35),
        thresholds.get("s3_max", 0.55),
        thresholds.get("s4_max", 0.80),
    ]
    min_dist = min(abs(final_score - b) for b in boundaries)
    confidence = min(min_dist / 0.10, 1.0)
    return round(confidence, 3)


# =============================================================================
# Learning state summary (consumed by brain scan)
# =============================================================================


def summarize_learning_state() -> dict[str, Any]:
    """Produce a compact summary of feedback-loop state for display in brain scan."""
    decisions = read_decisions(limit=1000)
    tools = read_tools(limit=1000)

    overrides = detect_overrides(decisions)
    correction_follows = decisions_with_correction_follow(decisions)
    skill_overrides = compute_skill_override_counts(decisions)
    drift = compute_tier_drift(decisions)

    return {
        "decisions_seen": len(decisions),
        "tools_seen": len(tools),
        "override_events": len(overrides),
        "correction_follows": len(correction_follows),
        "top_overridden_skills": sorted(
            skill_overrides.items(), key=lambda kv: kv[1], reverse=True
        )[:5],
        "tier_drift_pp_7d_vs_30d": drift.get("drift_pp", {}),
        "short_window_total": drift.get("short_window_total", 0),
        "long_window_total": drift.get("long_window_total", 0),
    }


# =============================================================================
# Multi-turn context reader
# =============================================================================


def get_recent_session_context(
    session_id: str,
    turns: int = 3,
) -> list[dict[str, Any]]:
    """Return the last N decisions for a specific session, oldest first."""
    all_decisions = read_decisions(limit=500)
    session_decisions = [d for d in all_decisions if d.get("session_id") == session_id]
    # Newest-first -> reverse -> take last N
    session_decisions.reverse()
    return session_decisions[-turns:] if turns > 0 else session_decisions


# =============================================================================
# Human behavioral metrics (v2.3 — Katanforoosh gap closure)
# =============================================================================


def _parse_ts(ts_str: str) -> datetime.datetime | None:
    """Parse ISO timestamp from decisions.jsonl entry."""
    if not ts_str:
        return None
    try:
        # Handle both 'Z' suffix and '+00:00'
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _delegation_mode(confidence: float, consecutive_corrections: int,
                     guardrails_fired: list) -> str:
    """Classify human-agent delegation mode from decision signals."""
    if consecutive_corrections >= 2 or guardrails_fired:
        return "veto"
    if confidence < 0.30:
        return "checkpoint"
    if confidence < 0.70:
        return "supervised"
    return "full"


def _is_override(entry: dict) -> bool:
    """Check if a decisions.jsonl entry represents a model override."""
    current = entry.get("current_model", "").lower()
    result = entry.get("result", {})
    recommended = result.get("model", "").lower() if isinstance(result, dict) else ""
    if not current or not recommended:
        return False
    return recommended not in current and current not in recommended


def compute_human_metrics(
    session_id: str,
    prompt_text: str,
    classification_result: dict,
) -> dict[str, Any]:
    """Compute human behavioral layer for a single decisions.jsonl entry.

    Reads recent session history to derive:
    - turn_index: this turn's position in the session
    - turns_since_correction: how many turns since last correction_detected
    - consecutive_corrections: how many correction turns in a row (ending now)
    - session_override_count: total overrides in this session so far
    - session_reprompt_count: total correction detections in this session
    - prompt_token_count: rough estimate (len // 4)
    - inter_turn_gap_seconds: seconds since last decision in this session
    - delegation_mode: full/supervised/checkpoint/veto
    """
    # Read all session decisions (newest first from read_decisions)
    all_recent = read_decisions(limit=500)
    session_entries = [d for d in all_recent if d.get("session_id") == session_id]
    # Reverse to chronological order (oldest first)
    session_entries.reverse()

    turn_index = len(session_entries)  # this is the next turn (0-indexed after append)

    # Inter-turn gap
    inter_turn_gap: float = 0.0
    if session_entries:
        last_ts = _parse_ts(session_entries[-1].get("ts", ""))
        now = datetime.datetime.now(datetime.timezone.utc)
        if last_ts:
            inter_turn_gap = (now - last_ts).total_seconds()

    # Scan session history for corrections and overrides
    corrections_in_session = 0
    overrides_in_session = 0
    turns_since_correction = turn_index  # default: no correction seen
    consecutive_corrections = 0

    for i, entry in enumerate(session_entries):
        result = entry.get("result", {})
        if isinstance(result, dict) and result.get("correction_detected_in_prompt"):
            corrections_in_session += 1
            turns_since_correction = turn_index - i - 1
        if _is_override(entry):
            overrides_in_session += 1

    # Count consecutive corrections ending at the most recent entry
    for entry in reversed(session_entries):
        result = entry.get("result", {})
        if isinstance(result, dict) and result.get("correction_detected_in_prompt"):
            consecutive_corrections += 1
        else:
            break

    # Check if THIS prompt is also a correction
    if classification_result.get("correction_detected_in_prompt"):
        corrections_in_session += 1
        consecutive_corrections += 1
        turns_since_correction = 0

    # Prompt token estimate
    prompt_token_count = max(1, len(prompt_text) // 4)

    # Delegation mode
    confidence = classification_result.get("confidence", 1.0)
    guardrails = classification_result.get("guardrails_fired", [])
    mode = _delegation_mode(confidence, consecutive_corrections, guardrails)

    return {
        "turn_index": turn_index,
        "turns_since_correction": turns_since_correction,
        "consecutive_corrections": consecutive_corrections,
        "session_override_count": overrides_in_session,
        "session_reprompt_count": corrections_in_session,
        "prompt_token_count": prompt_token_count,
        "inter_turn_gap_seconds": round(inter_turn_gap, 1),
        "delegation_mode": mode,
    }


def summarize_human_state(days: int = 30) -> dict[str, Any]:
    """Compute aggregate human behavioral metrics over N days.

    Returns dict suitable for adding to brain scan output.
    """
    all_decisions = read_decisions(limit=5000)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    recent: list[dict] = []
    for d in all_decisions:
        ts = _parse_ts(d.get("ts", ""))
        if ts and ts >= cutoff:
            recent.append(d)
    recent.reverse()  # chronological

    if not recent:
        return {"error": "no decisions in window", "days": days}

    total = len(recent)

    # Override rate
    overrides = sum(1 for d in recent if _is_override(d))
    override_rate = overrides / total if total else 0

    # Per-tier override rate
    tier_overrides: dict[str, list[bool]] = defaultdict(list)
    for d in recent:
        tier = d.get("result", {}).get("tier", "?") if isinstance(d.get("result"), dict) else "?"
        tier_overrides[tier].append(_is_override(d))
    tier_override_rates = {
        t: sum(v) / len(v) if v else 0 for t, v in sorted(tier_overrides.items())
    }

    # Correction / reprompt rate
    corrections = sum(
        1 for d in recent
        if isinstance(d.get("result"), dict)
        and d["result"].get("correction_detected_in_prompt")
    )
    reprompt_rate = corrections / total if total else 0

    # Session-level metrics
    sessions: dict[str, list[dict]] = defaultdict(list)
    for d in recent:
        sid = d.get("session_id", "unknown")
        sessions[sid].append(d)

    # Abandonment: sessions with correction in first 3 turns, then no more turns
    abandoned = 0
    for sid, entries in sessions.items():
        if len(entries) <= 3:
            has_early_correction = any(
                isinstance(e.get("result"), dict)
                and e["result"].get("correction_detected_in_prompt")
                for e in entries[:3]
            )
            if has_early_correction:
                abandoned += 1
    abandonment_rate = abandoned / len(sessions) if sessions else 0

    # Delegation mode distribution (from human{} layer if available)
    mode_counts: dict[str, int] = defaultdict(int)
    for d in recent:
        human = d.get("human", {})
        if isinstance(human, dict) and "delegation_mode" in human:
            mode_counts[human["delegation_mode"]] += 1
        else:
            # Fallback: compute from result
            result = d.get("result", {})
            if isinstance(result, dict):
                conf = result.get("confidence", 1.0)
                guardrails = result.get("guardrails_fired", [])
                mode = _delegation_mode(conf, 0, guardrails)
                mode_counts[mode] += 1

    mode_total = sum(mode_counts.values())
    mode_pcts = {
        m: round(c / mode_total * 100, 1) if mode_total else 0
        for m, c in sorted(mode_counts.items())
    }

    # Inter-turn gaps (from human{} layer if available)
    gaps: list[float] = []
    for d in recent:
        human = d.get("human", {})
        if isinstance(human, dict) and "inter_turn_gap_seconds" in human:
            g = human["inter_turn_gap_seconds"]
            if isinstance(g, (int, float)) and g > 0:
                gaps.append(g)

    gap_p50 = sorted(gaps)[len(gaps) // 2] if gaps else None
    gap_p95 = sorted(gaps)[int(len(gaps) * 0.95)] if gaps else None

    # Trust calibration: correlation between correction and low confidence
    # Simple: avg confidence when correction vs when not
    conf_on_correction: list[float] = []
    conf_on_normal: list[float] = []
    for d in recent:
        result = d.get("result", {})
        if not isinstance(result, dict):
            continue
        conf = result.get("confidence", 1.0)
        if result.get("correction_detected_in_prompt"):
            conf_on_correction.append(conf)
        else:
            conf_on_normal.append(conf)

    avg_conf_correction = (
        sum(conf_on_correction) / len(conf_on_correction) if conf_on_correction else None
    )
    avg_conf_normal = (
        sum(conf_on_normal) / len(conf_on_normal) if conf_on_normal else None
    )

    return {
        "days": days,
        "total_decisions": total,
        "total_sessions": len(sessions),
        "override_rate": round(override_rate, 4),
        "override_rate_by_tier": {t: round(v, 4) for t, v in tier_override_rates.items()},
        "reprompt_rate": round(reprompt_rate, 4),
        "abandonment_rate": round(abandonment_rate, 4),
        "delegation_modes_pct": mode_pcts,
        "inter_turn_gap_p50_s": gap_p50,
        "inter_turn_gap_p95_s": gap_p95,
        "trust_calibration": {
            "avg_confidence_on_correction": (
                round(avg_conf_correction, 3) if avg_conf_correction is not None else None
            ),
            "avg_confidence_on_normal": (
                round(avg_conf_normal, 3) if avg_conf_normal is not None else None
            ),
            "healthy": (
                avg_conf_correction is not None
                and avg_conf_normal is not None
                and avg_conf_correction < avg_conf_normal
            ),
        },
    }


if __name__ == "__main__":
    # When run directly, print a human-readable learning state summary
    state = summarize_learning_state()
    print(json.dumps(state, indent=2, sort_keys=False))
