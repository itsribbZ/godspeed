#!/usr/bin/env python3
"""
Toke Brain — LLM Fallback Classifier (DSPy-ejected)
====================================================
Uses the compiled DSPy few-shot demos via raw Anthropic API call.
Zero DSPy runtime dependency — just the extracted demos + Haiku.

Called by brain_cli.py when the rule-based classifier has low confidence.
Adds 1-2 seconds of latency but only fires on uncertain classifications.

Usage (standalone test):
    python brain_llm_classifier.py "design Homer's MUSES from scratch"

Integration:
    from brain_llm_classifier import llm_classify
    tier = llm_classify("ambiguous prompt text")  # returns "S0"-"S5" or None
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    import anthropic
except ImportError:
    anthropic = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COMPILED_STATE = Path(__file__).parent / "eval" / "compiled_classifier.json"
TIER_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024

# ---------------------------------------------------------------------------
# System prompt (static — the compiled DSPy signature description)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Brain, a Claude model routing classifier. Your job is to route user prompts to the cheapest model tier that preserves quality.

Tiers:
- S0 (Haiku low): Trivial — one-word answers, filename lookups, yes/no, shell commands
- S1 (Haiku medium): Short Q&A, lookups needing 1-2 sentence explanations
- S2 (Sonnet medium): Single-file code edits, standard 3-10 line patches, unit tests
- S3 (Sonnet + 16K thinking): Multi-step reasoning, 10-50 lines, dependency chains, multi-concern tasks
- S4 (Opus + 32K thinking): Architecture, novel debugging, cross-file refactors, system design from scratch
- S5 (Opus 1M + 64K thinking): Overnight runs, long-context synthesis (100K+ lines), full-system reviews

Critical Boundary Rules:
- Short prompts can be HIGH tier. "add caching" sounds S0 but requires cache strategy, invalidation, TTL = S3.
- "design" does NOT always mean S4. Designing standard patterns (retry, pagination, logging) = S3.
- Cross-file refactoring and plugin architectures = S4 even if described briefly.
- "fix" or "add" can be S4 if the fix requires understanding system-wide interactions.
- Under-routing is WORSE than over-routing. When uncertain, route UP.

Think step by step about the task's complexity, then output the tier.
Respond with ONLY valid JSON: {"reasoning": "<brief reasoning>", "tier": "<S0|S1|S2|S3|S4|S5>"}"""


# ---------------------------------------------------------------------------
# Demo loader (reads compiled DSPy state, extracts few-shot examples)
# ---------------------------------------------------------------------------

_demos_cache: list[dict] | None = None

def _load_demos() -> list[dict]:
    global _demos_cache
    if _demos_cache is not None:
        return _demos_cache
    if not COMPILED_STATE.exists():
        _demos_cache = []
        return _demos_cache
    try:
        state = json.loads(COMPILED_STATE.read_text(encoding="utf-8"))
        demos = state.get("classify", state.get("classify.predict", {})).get("demos", [])
        _demos_cache = demos
    except (json.JSONDecodeError, KeyError):
        _demos_cache = []
    return _demos_cache


def _format_demos() -> str:
    """Format compiled demos as few-shot examples for the user message."""
    demos = _load_demos()
    if not demos:
        return ""
    parts = ["Here are examples of correct routing decisions:\n"]
    for i, d in enumerate(demos, 1):
        prompt = d.get("prompt_text", "")
        reasoning = d.get("reasoning", "")
        tier = d.get("tier", "")
        parts.append(f"Example {i}:")
        parts.append(f"  Prompt: \"{prompt}\"")
        if reasoning:
            parts.append(f"  Reasoning: {reasoning}")
        parts.append(f"  Tier: {tier}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM classify (main entry point)
# ---------------------------------------------------------------------------

def llm_classify(prompt_text: str) -> str | None:
    """Classify a prompt using the compiled DSPy demos + Haiku.

    Returns a tier string ("S0"-"S5") or None on failure.
    Cost: ~$0.003 per call (Haiku, ~2K input + 200 output tokens).
    Latency: 1-2 seconds.
    """
    if anthropic is None:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    demos_text = _format_demos()
    user_msg = f"{demos_text}\nNow route this prompt. Think step by step, then output the tier.\n\nPrompt: \"{prompt_text}\"\n\nRespond with ONLY valid JSON: {{\"reasoning\": \"...\", \"tier\": \"S0|S1|S2|S3|S4|S5\"}}"

    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            raw = "\n".join(lines)
        data = json.loads(raw)
        tier = str(data.get("tier", "")).upper()
        if tier in TIER_ORDER:
            return tier
        # Try to find tier in the reasoning
        for t in reversed(TIER_ORDER):
            if t in raw:
                return t
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python brain_llm_classifier.py \"prompt text\"", file=sys.stderr)
        sys.exit(1)
    prompt = " ".join(sys.argv[1:])
    print(f"Prompt: {prompt}", file=sys.stderr)
    tier = llm_classify(prompt)
    if tier:
        print(f"LLM tier: {tier}")
    else:
        print("LLM classify failed", file=sys.stderr)
        sys.exit(1)
