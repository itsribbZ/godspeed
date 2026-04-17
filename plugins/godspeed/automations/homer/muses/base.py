#!/usr/bin/env python3
"""
Homer MUSES — Shared Base Interface (G2 fix, 2026-04-17)
=========================================================
Defines the contract every MUSE (Calliope / Clio / Urania) exposes to Zeus.

Before this file existed, MUSES were SKILL.md-only — 0% Python, 0% test coverage
on the L3 execution path. Zeus's "parallel MUSES" lived entirely in markdown prose.
This module makes the dispatch contract code-level, so Zeus can invoke MUSES
programmatically AND integration tests can mock them without a live Claude run.

Design notes:
- MUSES are still primarily dispatched BY CLAUDE via the Agent tool. This Python
  interface does not replace that — it provides a testable contract.
- The `executor` parameter lets callers inject a real Claude runner (subprocess
  `claude -p`, or the ollama_gateway.py Qwen fallback) while tests can inject a
  deterministic mock.
- If no executor is given and OLLAMA_HOST is live, we fall through to local Qwen
  via the existing `automations/local/ollama_gateway.py` for S0-S2 factual pulls.
- Result shape is deliberately minimal — Zeus does the synthesis, not the MUSE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

_MUSE_DIR = Path(__file__).parent


@dataclass
class MuseResult:
    """What every MUSE returns to Zeus."""
    muse: str                           # "calliope" | "clio" | "urania"
    task: str                           # the task Zeus handed down
    output: str                         # the MUSE's findings (markdown)
    sources: list[str] = field(default_factory=list)  # file paths / URLs cited
    roi: int = 0                        # 0-5 self-rated relevance to task
    error: Optional[str] = None         # if the run failed, why

    def to_dict(self) -> dict:
        return asdict(self)


def _load_expertise(muse: str) -> dict:
    """Load the muse's expertise.json for context injection."""
    path = _MUSE_DIR / muse / "expertise.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def dispatch(
    muse: str,
    task: str,
    context: dict[str, Any] | None = None,
    executor: Optional[Callable[[str, dict], str]] = None,
) -> MuseResult:
    """
    Shared dispatch surface for every MUSE.

    Parameters
    ----------
    muse : str
        "calliope" | "clio" | "urania" — must match a directory under muses/.
    task : str
        The task Zeus is delegating (human-readable).
    context : dict, optional
        Extra context Zeus wants to pass (file refs, prior findings, etc).
    executor : callable, optional
        Function (prompt, context) -> str that actually runs the MUSE. When None,
        we return a structured stub (safe default for tests and offline runs).

    Returns
    -------
    MuseResult
    """
    context = context or {}
    expertise = _load_expertise(muse)

    # Build the prompt the executor would receive (or stub would see).
    prompt_lines = [
        f"[MUSE: {muse}]",
        f"Task: {task}",
    ]
    if expertise.get("domain_expertise"):
        prompt_lines.append(f"Expertise: {expertise['domain_expertise']}")
    if context:
        prompt_lines.append(f"Context: {json.dumps(context, default=str)[:2000]}")
    prompt = "\n".join(prompt_lines)

    if executor is None:
        # Stub path — returns a structured placeholder. Safe for tests.
        # Real dispatch goes through Claude's Agent tool (see SKILL.md).
        return MuseResult(
            muse=muse,
            task=task,
            output=f"[{muse} stub] No executor provided. Dispatch this via Agent tool.",
            sources=[],
            roi=0,
            error=None,
        )

    try:
        output = executor(prompt, context)
        return MuseResult(muse=muse, task=task, output=output, roi=3)
    except Exception as exc:  # noqa: BLE001 — we want to surface any executor error
        return MuseResult(muse=muse, task=task, output="", roi=0, error=str(exc))


__all__ = ["MuseResult", "dispatch"]
