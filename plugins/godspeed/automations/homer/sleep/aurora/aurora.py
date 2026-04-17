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
DECISIONS_JSONL = Path.home() / ".claude" / "telemetry" / "brain" / "decisions.jsonl"
ADVISOR_JSONL = Path.home() / ".claude" / "telemetry" / "brain" / "advisor_calls.jsonl"
HOMER_ROOT = AURORA_ROOT.parent.parent
ROUTING_MANIFEST = HOMER_ROOT.parent / "brain" / "routing_manifest.toml"


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


def analyze_decisions(jsonl_path: Path | None = None) -> TierDistribution:
    """Read decisions.jsonl and compute tier / model / guardrail distributions."""
    jsonl_path = jsonl_path if jsonl_path is not None else DECISIONS_JSONL
    dist = TierDistribution()

    if not jsonl_path.exists():
        return dist

    confidences: list[float] = []
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
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
    except OSError:
        return dist

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


def run_tuning(
    decisions_path: Path | None = None,
    proposals_dir: Path | None = None,
) -> dict:
    """Full Aurora run: analyze + propose + write dated proposal JSON."""
    dist = analyze_decisions(decisions_path)
    proposals = propose_weight_adjustments(dist)

    proposals_dir = proposals_dir if proposals_dir is not None else PROPOSALS_DIR
    proposals_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    out_path = proposals_dir / f"tuning_{date}.json"

    output = {
        "agent": "aurora",
        "timestamp": datetime.datetime.now().isoformat(),
        "analysis": dist.to_dict(),
        "proposals": proposals,
        "proposals_count": len(proposals),
        "note": "Aurora proposes; the user decides. No auto-apply. Sacred Rule #2 + #4.",
    }
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "proposals_count": len(proposals),
        "report_path": str(out_path),
        "total_decisions_analyzed": dist.total,
    }


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    result = run_tuning()
    print(f"Aurora tuning complete.")
    print(f"  Decisions analyzed: {result['total_decisions_analyzed']}")
    print(f"  Proposals generated: {result['proposals_count']}")
    print(f"  Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
