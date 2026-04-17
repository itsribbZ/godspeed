#!/usr/bin/env python3
"""
Calliope — Homer L3 Epic Research Muse (Python interface, G2 fix 2026-04-17)
=============================================================================
Deep synthesis from web + local research sources. Dispatched by Zeus for
research-heavy branches of a plan. Read-only. Returns structured markdown with
T1-T3 source citations.

This Python file exposes the dispatch contract so Zeus can invoke Calliope
programmatically and integration tests can mock it. Real reasoning is still
routed through Claude via the Agent tool when an executor is wired.
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
    """Invoke Calliope on a research task. See muses/base.py for the contract."""
    return dispatch("calliope", task, context, executor)


__all__ = ["run"]
