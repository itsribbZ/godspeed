#!/usr/bin/env python3
"""
Urania — Homer L3 Measurement Muse (Python interface, G2 fix 2026-04-17)
========================================================================
Pulls numeric receipts from Toke telemetry (decisions.jsonl, _learnings.md,
stats-cache.json, Homer VAULT, brain scans). Dispatched by Zeus when the plan
needs "how many / how much / what percentage." Read-only. Every number comes
with a reproducible command.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import MuseResult, dispatch  # noqa: E402


def run(
    task: str,
    context: dict[str, Any] | None = None,
    executor: Optional[Callable[[str, dict], str]] = None,
) -> MuseResult:
    """Invoke Urania on a measurement task. See muses/base.py for the contract."""
    return dispatch("urania", task, context, executor)


__all__ = ["run"]
