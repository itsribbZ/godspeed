#!/usr/bin/env python3
"""
Toke Local — Decision Logger
=============================
Append-only JSONL log of every local routing decision.

Pattern matches Toke's brain/decisions.jsonl. Lives at:
  ~/.claude/telemetry/local/local_decisions.jsonl

Schema:
  timestamp        ISO-8601 UTC
  query_hash       md5[:10]
  brain_tier       S0-S5 (from upstream brain.classify())
  routed_to        local | claude_direct | override_approved | override_rejected
  confidence       float 0.0-1.0 (only if routed locally)
  entropy          float 0.0-1.0
  threshold        float (manifest [confidence].threshold)
  mode             logprobs | self_consistency | model_stated
  latency_ms       float (end-to-end)
  tokens_local     int
  tokens_claude    int
  ood_detected     bool (reserved for future activation_monitor integration)

stats() → aggregate analytics for `local stats` command.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


DEFAULT_PATH = Path.home() / ".claude" / "telemetry" / "local" / "local_decisions.jsonl"
HAIKU_COST_PER_TOKEN = 0.00025 / 1000   # ~$0.25 per million input tokens


@dataclass
class LocalRecord:
    query_hash: str
    brain_tier: str
    routed_to: str
    confidence: float
    entropy: float
    threshold: float
    mode: str
    latency_ms: float
    tokens_local: int
    tokens_claude: int
    ood_detected: bool
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class LocalStats:
    total: int
    routed_local: int
    routed_claude_direct: int
    override_requested: int
    override_approved: int
    avg_confidence: float
    avg_latency_ms: float
    tokens_local_total: int
    tokens_claude_total: int
    estimated_savings_usd: float
    tier_distribution: dict


class LocalDecisionLogger:
    """Append-only JSONL logger. Stdlib only."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_manifest(cls, manifest: dict) -> "LocalDecisionLogger":
        tel = manifest.get("telemetry", {})
        path = tel.get("decisions_path", str(DEFAULT_PATH))
        return cls(path=Path(path).expanduser())

    # ──────────────────────────────────────────────
    # WRITE
    # ──────────────────────────────────────────────

    def log(
        self,
        query: str,
        brain_tier: str,
        routed_to: str,
        confidence: float = 0.0,
        entropy: float = 0.0,
        mode: str = "logprobs",
        latency_ms: float = 0.0,
        tokens_local: int = 0,
        tokens_claude: int = 0,
        ood_detected: bool = False,
        threshold: float = 0.125,
    ) -> LocalRecord:
        query_hash = hashlib.md5(query.lower().encode()).hexdigest()[:10]
        record = LocalRecord(
            query_hash=query_hash,
            brain_tier=brain_tier,
            routed_to=routed_to,
            confidence=round(confidence, 4),
            entropy=round(entropy, 4),
            threshold=threshold,
            mode=mode,
            latency_ms=round(latency_ms, 1),
            tokens_local=tokens_local,
            tokens_claude=tokens_claude,
            ood_detected=ood_detected,
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")
        return record

    # ──────────────────────────────────────────────
    # READ
    # ──────────────────────────────────────────────

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def tail(self, n: int = 20) -> list[dict]:
        return self.read_all()[-n:]

    def stats(self, last_n: int | None = None) -> LocalStats:
        records = self.read_all()
        if last_n:
            records = records[-last_n:]

        n = len(records)
        if n == 0:
            return LocalStats(0, 0, 0, 0, 0, 0.0, 0.0, 0, 0, 0.0, {})

        routed_local = sum(1 for r in records if r["routed_to"] == "local")
        routed_claude_direct = sum(1 for r in records if r["routed_to"] == "claude_direct")
        override_requested = sum(1 for r in records if r["routed_to"] in ("override_approved", "override_rejected"))
        override_approved = sum(1 for r in records if r["routed_to"] == "override_approved")
        avg_confidence = sum(r["confidence"] for r in records) / n
        avg_latency = sum(r["latency_ms"] for r in records) / n
        tokens_local = sum(r["tokens_local"] for r in records)
        tokens_claude = sum(r["tokens_claude"] for r in records)

        # Savings vs hypothetical all-Claude routing
        hypothetical = (tokens_local + tokens_claude) * HAIKU_COST_PER_TOKEN
        actual = tokens_claude * HAIKU_COST_PER_TOKEN
        savings = hypothetical - actual

        tier_dist: dict[str, int] = {}
        for r in records:
            t = r.get("brain_tier", "unknown")
            tier_dist[t] = tier_dist.get(t, 0) + 1

        return LocalStats(
            total=n,
            routed_local=routed_local,
            routed_claude_direct=routed_claude_direct,
            override_requested=override_requested,
            override_approved=override_approved,
            avg_confidence=round(avg_confidence, 3),
            avg_latency_ms=round(avg_latency, 1),
            tokens_local_total=tokens_local,
            tokens_claude_total=tokens_claude,
            estimated_savings_usd=round(savings, 5),
            tier_distribution=tier_dist,
        )
