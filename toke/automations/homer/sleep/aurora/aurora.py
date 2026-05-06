#!/usr/bin/env python3
"""
Homer L6 — AURORA (sleep-time agent: routing weight tuner)
==========================================================
Aurora was the Roman goddess of the dawn. In Homer, she runs before the user
wakes — mining Brain's decisions.jsonl + advisor_calls.jsonl telemetry
and proposing routing_manifest.toml weight adjustments for the next day.

Aurora does NOT apply her proposals. She writes them as a dated JSON file
for the user to review + greenlight (Sacred Rule #2 + #4).

Input:
- `~/.claude/telemetry/brain/decisions.jsonl` — every classified prompt
- `~/.claude/telemetry/brain/advisor_calls.jsonl` (if exists) — advisor fires
- `Toke/automations/brain/routing_manifest.toml` — current weights

Output:
- `Toke/automations/homer/sleep/aurora/proposals/tuning_YYYY-MM-DD.json`
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

AURORA_ROOT = Path(__file__).parent
PROPOSALS_DIR = AURORA_ROOT / "proposals"

sys.path.insert(0, str(AURORA_ROOT.parent))
try:
    from _division import (  # type: ignore
        iter_decisions_for_division,
        load_division_spec,
        compute_activation_status,
    )
    DIVISION_SUPPORT = True
except ImportError:
    DIVISION_SUPPORT = False
DECISIONS_JSONL = Path.home() / ".claude" / "telemetry" / "brain" / "decisions.jsonl"
ADVISOR_JSONL = Path.home() / ".claude" / "telemetry" / "brain" / "advisor_calls.jsonl"
HOMER_ROOT = AURORA_ROOT.parent.parent
ROUTING_MANIFEST = HOMER_ROOT.parent / "brain" / "routing_manifest.toml"

# v0.2 (2026-04-22): Aurora also reads the neuron's learning profile
LEARNING_PROFILE_TOML = HOMER_ROOT.parent / "brain" / "rationale" / "learning_profile.toml"


@dataclass
class TierDistribution:
    total: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)
    guardrails_fired: dict[str, int] = field(default_factory=dict)
    uncertainty_escalated_count: int = 0
    corrections_detected: int = 0
    avg_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "by_tier": dict(sorted(self.by_tier.items())),
            "by_model": dict(sorted(self.by_model.items(), key=lambda x: -x[1])),
            "guardrails_fired": dict(sorted(self.guardrails_fired.items(), key=lambda x: -x[1])),
            "uncertainty_escalated_count": self.uncertainty_escalated_count,
            "uncertainty_escalated_pct": round(
                100 * self.uncertainty_escalated_count / max(1, self.total), 1
            ),
            "corrections_detected": self.corrections_detected,
            "corrections_pct": round(100 * self.corrections_detected / max(1, self.total), 1),
            "avg_confidence": round(self.avg_confidence, 3),
        }


def analyze_decisions(
    jsonl_path: Path | None = None,
    division: str | None = None,
) -> TierDistribution:
    """
    Read decisions.jsonl and compute tier / model / guardrail distributions.

    If division is provided, filters decisions to only those Director-classified
    into that division (cross-join via prompt_text against director_decisions.jsonl).
    Ecosystem-wide mode preserved when division is None (default behavior).
    """
    jsonl_path = jsonl_path if jsonl_path is not None else DECISIONS_JSONL
    dist = TierDistribution()

    if division is not None:
        if not DIVISION_SUPPORT:
            raise RuntimeError(
                "division filter requested but _division.py not importable — check sleep/ layout"
            )
        rows: list[dict] = list(iter_decisions_for_division(
            division,
            decisions_jsonl=jsonl_path,
        ))
    else:
        if not jsonl_path.exists():
            return dist
        rows = []
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return dist

    confidences: list[float] = []
    for d in rows:
        r = d.get("result", {})
        tier = r.get("tier", "?")
        model = r.get("model", "?")
        dist.total += 1
        dist.by_tier[tier] = dist.by_tier.get(tier, 0) + 1
        dist.by_model[model] = dist.by_model.get(model, 0) + 1
        for g in r.get("guardrails_fired", []):
            dist.guardrails_fired[g] = dist.guardrails_fired.get(g, 0) + 1
        if r.get("uncertainty_escalated"):
            dist.uncertainty_escalated_count += 1
        if r.get("correction_detected_in_prompt"):
            dist.corrections_detected += 1
        conf = r.get("confidence", 0.0)
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))

    if confidences:
        dist.avg_confidence = sum(confidences) / len(confidences)
    return dist


def read_current_manifest() -> dict:
    """Read routing_manifest.toml for current weight values. Returns parsed dict or empty."""
    if not ROUTING_MANIFEST.exists():
        return {}
    try:
        import tomllib
        with open(ROUTING_MANIFEST, "rb") as f:
            return tomllib.load(f)
    except (ImportError, Exception):
        return {}


def propose_weight_adjustments(dist: TierDistribution) -> list[dict]:
    """
    Propose routing_manifest.toml weight adjustments from observed data.
    Now reads current manifest values to anchor proposals to actual config.

    Rules:
    - If uncertainty_escalated > 40% → classifier is unsure too often, tier floor should rise
    - If corrections > 5% → classifier is miscalibrated, escalation weights should bump up
    - If a guardrail fires > 20% → maybe raise its threshold
    - If a guardrail fires 0 times with 100+ decisions → maybe unused, flag for review
    """
    proposals: list[dict] = []
    manifest = read_current_manifest()

    if dist.total == 0:
        return proposals

    unc_pct = 100 * dist.uncertainty_escalated_count / dist.total
    if unc_pct > 40:
        proposals.append({
            "id": "raise_tier_floor",
            "rationale": f"uncertainty_escalated fires {unc_pct:.1f}% of the time (>40% threshold) — "
                         f"classifier is too uncertain",
            "recommendation": "consider raising fail_open_tier from S3 to S4 OR increasing confidence threshold",
            "severity": "medium",
            "evidence": {"uncertainty_escalated_count": dist.uncertainty_escalated_count, "total": dist.total},
        })

    corr_pct = 100 * dist.corrections_detected / dist.total
    if corr_pct > 5:
        proposals.append({
            "id": "bump_escalation_weights",
            "rationale": f"corrections detected in {corr_pct:.1f}% of prompts (>5% threshold) — "
                         f"classifier is missing corrections",
            "recommendation": "increase weight on correction_keywords in routing_manifest.toml [signals]",
            "severity": "high",
            "evidence": {"corrections_detected": dist.corrections_detected, "total": dist.total},
        })

    for guardrail, count in dist.guardrails_fired.items():
        pct = 100 * count / dist.total
        if pct > 20:
            proposals.append({
                "id": f"raise_guardrail_threshold_{guardrail}",
                "rationale": f"guardrail '{guardrail}' fires {pct:.1f}% of prompts — possibly over-sensitive",
                "recommendation": f"review threshold for {guardrail} in routing_manifest.toml [guardrails]",
                "severity": "low",
                "evidence": {"guardrail": guardrail, "fire_count": count, "total": dist.total, "pct": pct},
            })
        elif count == 0 and dist.total >= 100:
            proposals.append({
                "id": f"dead_guardrail_{guardrail}",
                "rationale": f"guardrail '{guardrail}' has fired 0 times over {dist.total} decisions",
                "recommendation": f"verify {guardrail} is still needed or retire it",
                "severity": "low",
                "evidence": {"guardrail": guardrail, "fire_count": 0, "total": dist.total},
            })

    if dist.avg_confidence < 0.5:
        proposals.append({
            "id": "low_avg_confidence",
            "rationale": f"avg confidence {dist.avg_confidence:.3f} is below 0.5 — classifier often uncertain",
            "recommendation": "review signal weights; may need recalibration or new signals",
            "severity": "medium",
            "evidence": {"avg_confidence": dist.avg_confidence, "total": dist.total},
        })

    return proposals


def read_learning_profile(path: Path | None = None) -> dict:
    """Load the neuron's learning_profile.toml. Returns parsed dict or empty."""
    path = path if path is not None else LEARNING_PROFILE_TOML
    if not path.exists():
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def analyze_learning_profile(profile: dict) -> dict:
    """
    Walk per-context paths in learning_profile.toml and surface:
      - high-trust paths (score > +0.5) — candidates for default-route promotion
      - low-trust paths (score < -0.5) — candidates for demotion or guardrail tightening
      - high-volume paths — where most traces land (telemetry weight)
    """
    summary = {
        "total_paths": 0,
        "total_contexts": 0,
        "high_trust": [],       # [{context, path_key, score, tier, skill, chain, counts}, ...]
        "low_trust": [],
        "high_volume": [],      # top 5 paths by total outcome count
    }
    contexts = profile.get("context") or {}
    summary["total_contexts"] = len(contexts)

    all_paths: list[dict] = []
    for ctx_name, ctx_body in contexts.items():
        paths = (ctx_body or {}).get("paths") or {}
        for path_key, path_body in paths.items():
            score = float(path_body.get("score", 0.0))
            total_count = sum(
                int(v) for k, v in path_body.items()
                if k in ("explicit_positive", "explicit_negative", "implicit_negative",
                         "implicit_positive", "override", "positive", "unknown")
                and isinstance(v, int)
            )
            entry = {
                "context": ctx_name,
                "path_key": path_key,
                "score": round(score, 3),
                "tier": path_body.get("tier"),
                "skill": path_body.get("skill"),
                "chain": path_body.get("chain"),
                "total_observations": total_count,
                "counts": {
                    k: path_body[k] for k in (
                        "explicit_positive", "explicit_negative", "implicit_negative",
                        "implicit_positive", "override", "positive", "unknown"
                    ) if k in path_body
                },
            }
            all_paths.append(entry)
            summary["total_paths"] += 1
            if score > 0.5:
                summary["high_trust"].append(entry)
            elif score < -0.5:
                summary["low_trust"].append(entry)

    summary["high_volume"] = sorted(all_paths, key=lambda e: e["total_observations"], reverse=True)[:5]
    # Cap output lists for readability
    summary["high_trust"] = sorted(summary["high_trust"], key=lambda e: -e["score"])[:10]
    summary["low_trust"] = sorted(summary["low_trust"], key=lambda e: e["score"])[:10]
    return summary


def propose_profile_based_adjustments(profile_summary: dict) -> list[dict]:
    """
    For each low-trust path, propose a concrete action:
      - If skill is named → propose tier bump (demote routing for that skill in this context)
      - If chain is named → propose reviewing whether that chain fits the context
      - If neither → propose adding a correction keyword or a guardrail
    For high-trust paths → propose "keep or promote"
    """
    proposals: list[dict] = []
    for low in profile_summary.get("low_trust", []):
        context = low.get("context")
        skill = low.get("skill") or "(none)"
        tier = low.get("tier") or "?"
        chain = low.get("chain") or "(none)"
        score = low.get("score")
        counts = low.get("counts") or {}
        explicit_neg = counts.get("explicit_negative", 0) + counts.get("implicit_negative", 0)

        if explicit_neg < 2:  # not enough signal to act
            continue

        rec = []
        if skill != "(none)":
            rec.append(f"consider raising tier for '{skill}' when context='{context}'")
        if chain != "(none)":
            rec.append(f"review whether chain '{chain}' is the right route for context='{context}'")
        if skill == "(none)" and chain == "(none)":
            rec.append(f"base tier {tier} may be too low for context='{context}' — review signal weights")

        proposals.append({
            "id": f"distrust_{context}_{tier}_{skill}",
            "rationale": f"learning profile shows score {score} for context='{context}', tier={tier}, "
                         f"skill={skill}, chain={chain} with {explicit_neg} negative signals",
            "recommendation": " OR ".join(rec) if rec else "investigate",
            "severity": "high" if score < -0.7 else "medium",
            "evidence": {
                "context": context, "tier": tier, "skill": skill, "chain": chain,
                "score": score, "counts": counts,
            },
        })

    for high in profile_summary.get("high_trust", []):
        context = high.get("context")
        skill = high.get("skill") or "(none)"
        tier = high.get("tier") or "?"
        chain = high.get("chain") or "(none)"
        score = high.get("score")
        counts = high.get("counts") or {}
        explicit_pos = counts.get("explicit_positive", 0) + counts.get("implicit_positive", 0) + counts.get("positive", 0)

        if explicit_pos < 5:  # not enough signal to trust the trust
            continue

        proposals.append({
            "id": f"trust_{context}_{tier}_{skill}",
            "rationale": f"learning profile shows score {score} for context='{context}', tier={tier}, "
                         f"skill={skill}, chain={chain} — {explicit_pos} positive observations",
            "recommendation": f"keep this route; consider promoting as default when context='{context}' is detected",
            "severity": "low",
            "evidence": {
                "context": context, "tier": tier, "skill": skill, "chain": chain,
                "score": score, "counts": counts,
            },
        })

    return proposals


def run_tuning(
    decisions_path: Path | None = None,
    proposals_dir: Path | None = None,
    profile_path: Path | None = None,
    division: str | None = None,
) -> dict:
    """
    Full Aurora run: analyze decisions + analyze learning profile + propose +
    write dated proposal JSON.

    v0.2 (2026-04-22, Fix 3): Aurora now ALSO reads learning_profile.toml from
    the neuron and proposes concrete (context, path) adjustments. Closes the
    feedback loop — the neuron no longer just records, Aurora steers on it.

    v0.3 (2026-05-02, Phase 3 UE5 division): Aurora optionally filters decisions
    by Director division. Ecosystem-wide is the default (division=None). Per-
    division proposals land in proposals/<division>/tuning_<date>.json so the
    sleep-nightly schtask can fan-out across activated divisions without
    overwriting each other.

    Activation gate (per blueprint §4.4): when a division is specified, Aurora
    surfaces the activation status in the output but does NOT block on it —
    sub-threshold proposals are still useful as early signal.
    """
    dist = analyze_decisions(decisions_path, division=division)
    decision_proposals = propose_weight_adjustments(dist)

    # learning_profile is ecosystem-wide; per-division filtering would require
    # context-tagged learning entries which don't exist yet. Read as-is for
    # ecosystem mode; skip entirely for division mode (avoid leaking unrelated
    # context into a UE5 proposal etc.).
    if division is None:
        profile = read_learning_profile(profile_path)
        profile_summary = analyze_learning_profile(profile)
        profile_proposals = propose_profile_based_adjustments(profile_summary)
    else:
        profile_summary = {"total_paths": 0, "skipped": "division mode — learning_profile is not division-tagged"}
        profile_proposals = []

    all_proposals = decision_proposals + profile_proposals

    proposals_dir = proposals_dir if proposals_dir is not None else PROPOSALS_DIR
    if division is not None:
        proposals_dir = proposals_dir / division
    proposals_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    fname = f"tuning_{division}_{date}.json" if division else f"tuning_{date}.json"
    out_path = proposals_dir / fname

    activation = None
    if division is not None and DIVISION_SUPPORT:
        try:
            spec = load_division_spec(division)
            status = compute_activation_status(division)
            activation = {
                "activated": status.activated,
                "reason": status.reason,
                "decisions_30d": status.decisions_30d,
                "threshold": status.threshold,
                "spec_version": spec.raw.get("version", "?"),
                "tier_floor": spec.tier_floor,
                "mode": spec.mode,
            }
        except (FileNotFoundError, json.JSONDecodeError) as e:
            activation = {"error": str(e)}

    output = {
        "agent": "aurora",
        "version": "0.3",
        "timestamp": datetime.datetime.now().isoformat(),
        "division": division,
        "activation": activation,
        "analysis": dist.to_dict(),
        "learning_profile_summary": profile_summary,
        "proposals": all_proposals,
        "proposals_count": len(all_proposals),
        "proposals_from_decisions": len(decision_proposals),
        "proposals_from_profile": len(profile_proposals),
        "note": "Aurora proposes; the user decides. No auto-apply. Sacred Rule #2 + #4.",
    }
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "division": division,
        "proposals_count": len(all_proposals),
        "proposals_from_decisions": len(decision_proposals),
        "proposals_from_profile": len(profile_proposals),
        "report_path": str(out_path),
        "total_decisions_analyzed": dist.total,
        "total_paths_analyzed": profile_summary.get("total_paths", 0) if isinstance(profile_summary, dict) else 0,
        "activation": activation,
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="aurora",
        description="Homer L6 Aurora — sleep-time routing weight tuner. PROPOSE-only (Sacred Rule #2 + #4).",
    )
    parser.add_argument(
        "--division",
        default=None,
        help="Filter decisions to a specific Director division (e.g. research, infrastructure). "
             "When omitted, ecosystem-wide mode (legacy behavior preserved).",
    )
    args = parser.parse_args(argv[1:])

    result = run_tuning(division=args.division)
    print(f"Aurora tuning complete.")
    if result.get("division"):
        print(f"  Division:                {result['division']}")
        if result.get("activation"):
            act = result["activation"]
            mark = "ACTIVE" if act.get("activated") else "INACTIVE"
            print(f"  Activation:              {mark} ({act.get('reason', '?')})")
    print(f"  Decisions analyzed:      {result['total_decisions_analyzed']}")
    print(f"  Learning-profile paths:  {result.get('total_paths_analyzed', 0)}")
    print(f"  Proposals (total):       {result['proposals_count']}")
    print(f"    from decisions:        {result.get('proposals_from_decisions', 0)}")
    print(f"    from learning profile: {result.get('proposals_from_profile', 0)}")
    print(f"  Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
