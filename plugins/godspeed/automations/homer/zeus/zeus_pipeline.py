#!/usr/bin/env python3
"""
Zeus Pipeline — Oracle-Gated Mnemos Write (G3 fix, 2026-04-17)
===============================================================
Before this file existed, Zeus's "Oracle evaluates → Mnemos writes" was a convention
documented in SKILL.md prose. No code enforced the ordering. Mnemos and Oracle were
independent Python modules with no import relationship. Nothing prevented Mnemos
writes from happening before Oracle scored (or at all).

This module makes the gate CODE-ENFORCED:

    pipeline.gate_and_write(synthesis, topic, citations) -> GateResult

Oracle scores the synthesis first. Verdict determines action:
    PASS       -> Mnemos.write_recall() fires, returns success
    SOFT_FAIL  -> Mnemos write fires with a "soft_fail_warning" flag on the entry
    HARD_FAIL  -> NO Mnemos write. Returns the verdict with rule violations cited.

The pipeline function is pure — it takes state as args, returns a dataclass. Tests
can construct MnemosStore + Oracle with in-memory paths and verify the gate works
without live telemetry writes.

Usage (from Zeus skill or integration test):

    from zeus.zeus_pipeline import gate_and_write
    from oracle.oracle import Oracle
    from mnemos.mnemos import MnemosStore

    oracle = Oracle()
    store = MnemosStore()
    result = gate_and_write(
        oracle=oracle,
        store=store,
        synthesis="...markdown synthesis from MUSES...",
        topic="G1 informational floor fix",
        citations=["zeus_session_20260417"],
    )
    if result.written:
        print(f"Wrote to Mnemos id={result.entry_id}")
    else:
        print(f"Gate blocked write: {result.verdict} — {result.reason}")
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allow this module to be imported without a package install.
# Each sub-component (oracle/, mnemos/) is a directory with a single module
# inside — we add each directory to sys.path so the modules import as top-level.
_HOMER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_HOMER_DIR / "oracle"))
sys.path.insert(0, str(_HOMER_DIR / "mnemos"))
from oracle import Oracle, ScoreReport  # noqa: E402
from mnemos import MnemosStore  # noqa: E402


@dataclass
class GateResult:
    """Outcome of one Zeus pipeline invocation."""
    written: bool                       # did we write to Mnemos?
    verdict: str                        # Oracle verdict: PASS | SOFT_FAIL | HARD_FAIL
    entry_id: str | None = None         # Mnemos recall id if written
    reason: str = ""                    # why we blocked (if HARD_FAIL)
    score: float = 0.0                  # Oracle overall score
    rule_failures: list[str] = field(default_factory=list)  # hard-failed rule ids
    theater_flags: list[str] = field(default_factory=list)  # sections flagged as theater
    warning: str = ""                   # set on SOFT_FAIL writes


def gate_and_write(
    oracle: Oracle,
    store: MnemosStore,
    synthesis: str,
    topic: str,
    citations: list[str],
    context: dict[str, Any] | None = None,
) -> GateResult:
    """
    Run the Oracle → Mnemos gate. Enforces the sacred ordering in code.

    Parameters
    ----------
    oracle : Oracle
        Pre-constructed Oracle with desired rubric.
    store : MnemosStore
        Pre-constructed Mnemos facade (Core + Recall + Archival).
    synthesis : str
        The Zeus synthesis text to evaluate.
    topic : str
        Short topic label for the Mnemos Recall entry.
    citations : list[str]
        Provenance identifiers — Mnemos write requires at least one non-empty
        citation (Mnemos raises CitationError otherwise).
    context : dict, optional
        Passed to Oracle.score() for context-aware checks.

    Returns
    -------
    GateResult
    """
    report: ScoreReport = oracle.score(synthesis, context=context)
    rule_failures = [
        c.rule_id for c in report.sacred_rule_checks
        if not c.passed and c.severity == "hard"
    ]

    if report.verdict == "HARD_FAIL":
        return GateResult(
            written=False,
            verdict=report.verdict,
            reason=f"Sacred rule hard-fail(s): {', '.join(rule_failures) or 'unnamed'}",
            score=report.overall_score,
            rule_failures=rule_failures,
            theater_flags=list(report.theater_flags or []),
        )

    # PASS or SOFT_FAIL both write, but SOFT_FAIL gets a warning flag.
    try:
        entry_id = store.write_recall(topic, synthesis, citations)
    except Exception as exc:  # noqa: BLE001 — surface the write-time error
        return GateResult(
            written=False,
            verdict=report.verdict,
            reason=f"Mnemos write failed: {exc}",
            score=report.overall_score,
            rule_failures=rule_failures,
            theater_flags=list(report.theater_flags or []),
        )

    warning = ""
    if report.verdict == "SOFT_FAIL":
        soft_fails = [
            c.rule_id for c in report.sacred_rule_checks
            if not c.passed and c.severity == "soft"
        ]
        warning = f"SOFT_FAIL flags: {', '.join(soft_fails)}"

    return GateResult(
        written=True,
        verdict=report.verdict,
        entry_id=entry_id,
        score=report.overall_score,
        rule_failures=rule_failures,
        theater_flags=list(report.theater_flags or []),
        warning=warning,
    )


__all__ = ["GateResult", "gate_and_write"]
