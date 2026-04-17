#!/usr/bin/env python3
"""
Clio — Homer L3 Code Archaeology Muse (Python interface, G2 fix 2026-04-17)
===========================================================================
Maps existing codebases. Finds call sites. Builds dependency graphs. Spots dead
code. Every claim cites file:line. Read-only. Dispatched by Zeus when the plan
needs "what's already in the codebase" before new work.
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
    """Invoke Clio on a code-archaeology task. See muses/base.py for the contract."""
    return dispatch("clio", task, context, executor)


__all__ = ["run"]
