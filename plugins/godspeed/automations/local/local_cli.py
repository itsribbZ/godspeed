#!/usr/bin/env python3
"""
Toke Local — CLI Workbench (v1.0)
==================================
Commands:
    local query "PROMPT"        Full pipeline: brain → local Qwen → confidence → optional override
    local ping                  Health check (Ollama + model)
    local stats [N]             Aggregate routing stats from local_decisions.jsonl
    local tail [N]              Last N decisions
    local config [key=value]    Show or update local_manifest.toml
    local test                  Smoke test (3 representative prompts)
    local help                  Show this help

Pairs with brain. Brain classifies → Local routes S0/S1/S2 to Qwen, S3+ falls through.
"""
from __future__ import annotations

import json
import sys
import time
import tomllib
from dataclasses import asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Local automation imports
sys.path.insert(0, str(Path(__file__).parent))
from ollama_gateway import OllamaGateway
from confidence_monitor import ConfidenceMonitor
from claude_override import ClaudeOverride
from local_decisions import LocalDecisionLogger

# Brain (upstream classifier)
sys.path.insert(0, str(Path(__file__).parent.parent / "brain"))
from severity_classifier import classify, load_manifest as load_brain_manifest, DEFAULT_MANIFEST_PATH  # noqa: E402


LOCAL_MANIFEST_PATH = Path(__file__).parent / "local_manifest.toml"


# ──────────────────────────────────────────────
# MANIFEST
# ──────────────────────────────────────────────

def load_local_manifest(path: Path = LOCAL_MANIFEST_PATH) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


# ──────────────────────────────────────────────
# CORE PIPELINE
# ──────────────────────────────────────────────

def run_pipeline(query: str, verbose: bool = True) -> dict:
    """
    Full local pipeline:
      1. Brain classifies tier
      2. If tier in local_eligible_tiers → Qwen generates
      3. ConfidenceMonitor scores
      4. If is_critical → ClaudeOverride gate (user approval required)
      5. Log decision
    """
    manifest = load_local_manifest()
    brain_manifest = load_brain_manifest(DEFAULT_MANIFEST_PATH)

    # ── Stage 1: Brain classify ───────────────────────────
    classification = classify(query, manifest=brain_manifest)
    tier = classification.tier

    eligible = manifest["routing"]["local_eligible_tiers"]
    logger = LocalDecisionLogger.from_manifest(manifest)

    if verbose:
        print(f"\n  [brain] tier={tier}  score={classification.score:.3f}  ({classification.reasoning})")

    if tier not in eligible:
        # Falls through to standard Claude execution
        if verbose:
            print(f"  [route] tier {tier} not local-eligible — falling through to Claude")
        record = logger.log(
            query=query, brain_tier=tier, routed_to="claude_direct",
            confidence=0.0, entropy=0.0, mode="n/a",
            latency_ms=0.0, tokens_local=0, tokens_claude=0,
        )
        return {
            "routed_to": "claude_direct",
            "brain_tier": tier,
            "message": "Tier exceeds local capacity — handle in Claude session",
            "record": asdict(record),
        }

    # ── Stage 2: Local generation ─────────────────────────
    gw = OllamaGateway.from_manifest(manifest)
    if not gw.ping():
        if verbose:
            print(f"  [error] Ollama unreachable or model {gw.model} not loaded")
        record = logger.log(
            query=query, brain_tier=tier, routed_to="claude_direct",
            confidence=0.0, entropy=0.0, mode="ollama_offline",
            latency_ms=0.0, tokens_local=0, tokens_claude=0,
        )
        return {"routed_to": "claude_direct", "brain_tier": tier,
                "error": "ollama_unreachable", "record": asdict(record)}

    if verbose:
        print(f"  [local] generating with {gw.model}...")
    t0 = time.perf_counter()
    response = gw.generate(query)
    gen_latency = (time.perf_counter() - t0) * 1000

    if verbose:
        preview = (response.text[:200] + "...") if len(response.text) > 200 else response.text
        print(f"  [local] {response.tokens_generated} tokens in {gen_latency:.0f}ms")
        print(f"          {preview!r}")

    # ── Stage 3: Confidence monitor ───────────────────────
    monitor = ConfidenceMonitor.from_manifest(gw, manifest)
    conf = monitor.score_from_response(query, response)

    if verbose:
        flag = " [CRITICAL]" if conf.is_critical else ""
        print(f"  [conf]  {conf.score:.1%} via {conf.mode.value}{flag}")

    tokens_local = response.total_tokens
    tokens_claude = 0
    routed_to = "local"

    # ── Stage 4: Optional override gate ───────────────────
    final_text = response.text

    if conf.is_critical:
        override = ClaudeOverride.from_manifest(manifest)
        if verbose:
            print(f"  [gate]  presenting override request to user...")
        outcome = override.request_override(query, response.text, conf)

        if outcome.get("approved"):
            final_text = outcome.get("claude_response", response.text)
            tokens_claude = outcome.get("tokens_used", 0)
            routed_to = "override_approved"
        else:
            routed_to = "override_rejected"

    # ── Stage 5: Log ──────────────────────────────────────
    total_latency = (time.perf_counter() - t0) * 1000
    record = logger.log(
        query=query, brain_tier=tier, routed_to=routed_to,
        confidence=conf.score, entropy=conf.entropy, mode=conf.mode.value,
        latency_ms=total_latency,
        tokens_local=tokens_local, tokens_claude=tokens_claude,
        threshold=conf.score if conf.is_critical else manifest["confidence"]["threshold"],
    )

    return {
        "routed_to": routed_to,
        "brain_tier": tier,
        "confidence": conf.score,
        "entropy": conf.entropy,
        "mode": conf.mode.value,
        "is_critical": conf.is_critical,
        "answer": final_text,
        "tokens_local": tokens_local,
        "tokens_claude": tokens_claude,
        "latency_ms": round(total_latency, 1),
        "record": asdict(record),
    }


# ──────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────

def cmd_query(args: list[str]) -> int:
    if not args:
        print("Usage: local query 'your prompt here'", file=sys.stderr)
        return 2
    prompt = " ".join(args)
    result = run_pipeline(prompt, verbose=True)
    print("\n" + "=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_ping(_: list[str]) -> int:
    manifest = load_local_manifest()
    gw = OllamaGateway.from_manifest(manifest)
    alive = gw.ping()
    models = gw.list_models()
    print(f"  Ollama:        {'alive' if alive else 'unreachable'}")
    print(f"  Configured:    {gw.model}")
    print(f"  Loaded models: {models}")
    print(f"  Match:         {'YES' if alive else 'NO — pull or check model name'}")
    return 0 if alive else 1


def cmd_stats(args: list[str]) -> int:
    n = int(args[0]) if args else None
    manifest = load_local_manifest()
    logger = LocalDecisionLogger.from_manifest(manifest)
    stats = logger.stats(last_n=n)
    print(json.dumps(asdict(stats), indent=2))
    return 0


def cmd_tail(args: list[str]) -> int:
    n = int(args[0]) if args else 20
    manifest = load_local_manifest()
    logger = LocalDecisionLogger.from_manifest(manifest)
    for record in logger.tail(n):
        print(json.dumps(record))
    return 0


def cmd_config(args: list[str]) -> int:
    manifest = load_local_manifest()
    if not args:
        print(json.dumps(manifest, indent=2, default=str))
        return 0
    print("Note: edit local_manifest.toml directly. CLI write not yet implemented.")
    print(f"Manifest path: {LOCAL_MANIFEST_PATH}")
    return 1


def cmd_test(_: list[str]) -> int:
    """Run 3 representative prompts (one per S0/S1/S2 tier)."""
    test_prompts = [
        ("S0 — trivial", "What is 2+2?"),
        ("S1 — simple", "Briefly explain what a Python dictionary is."),
        ("S2 — moderate", "Compare the trade-offs between BFS and DFS for graph search."),
    ]

    print("\n" + "=" * 60)
    print("  LOCAL SMOKE TEST")
    print("=" * 60)

    for label, prompt in test_prompts:
        print(f"\n  --- {label} ---")
        print(f"  Q: {prompt}")
        result = run_pipeline(prompt, verbose=True)
        if "answer" in result:
            answer = result["answer"][:300]
            print(f"  A: {answer}")
        print(f"  Routed: {result['routed_to']}")
    print("\n  Done. Run `local stats` to see aggregate.")
    return 0


def cmd_refine(args: list[str]) -> int:
    """
    `local refine FILE [--focus 'area']` — run a Toke artifact through Qwen
    for refinement proposals. Skips brain (refinement is local-eligible by
    definition). Confidence-gated; low-confidence proposals trigger override.
    """
    if not args:
        print("Usage: local refine FILE [--focus 'area']", file=sys.stderr)
        return 2

    file_arg = args[0]
    focus = "general improvements, removable cruft, missing safeguards"
    if "--focus" in args:
        idx = args.index("--focus")
        if idx + 1 < len(args):
            focus = args[idx + 1]

    file_path = Path(file_arg).expanduser()
    if not file_path.exists():
        print(f"  File not found: {file_path}", file=sys.stderr)
        return 1

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as e:
        print(f"  Read error: {e}", file=sys.stderr)
        return 1

    prompt = (
        f"You are a precise code reviewer. Analyze this file and propose refinements.\n"
        f"Focus area: {focus}\n\n"
        f"=== {file_path.name} ===\n{content}\n\n"
        "Output (terse, actionable, numbered):\n"
        "1. Top 3 specific improvements (cite section/line if relevant)\n"
        "2. One thing to remove (and why)\n"
        "3. One thing to keep verbatim (and why)\n"
        "No preamble. No fluff."
    )

    manifest = load_local_manifest()
    gw = OllamaGateway.from_manifest(manifest)
    if not gw.ping():
        print(f"  [error] Ollama unreachable", file=sys.stderr)
        return 1

    print(f"\n  [refine] target: {file_path}")
    print(f"  [refine] focus:  {focus}")
    print(f"  [refine] generating with {gw.model} ({len(content)} chars context)...")
    t0 = time.perf_counter()
    response = gw.generate(prompt, max_tokens=600)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  [refine] {response.tokens_generated} tokens in {elapsed:.0f}ms")

    monitor = ConfidenceMonitor.from_manifest(gw, manifest)
    conf = monitor.score_from_response(prompt, response)
    print(f"  [conf]   {conf.score:.1%} via {conf.mode.value}{' [CRITICAL]' if conf.is_critical else ''}")

    final_text = response.text
    routed_to = "local"
    tokens_claude = 0

    if conf.is_critical:
        override = ClaudeOverride.from_manifest(manifest)
        outcome = override.request_override(prompt, response.text, conf)
        if outcome.get("approved"):
            final_text = outcome.get("claude_response", response.text)
            tokens_claude = outcome.get("tokens_used", 0)
            routed_to = "override_approved"
        else:
            routed_to = "override_rejected"

    logger = LocalDecisionLogger.from_manifest(manifest)
    logger.log(
        query=f"refine:{file_path.name}",
        brain_tier="manual_refine",
        routed_to=routed_to,
        confidence=conf.score, entropy=conf.entropy, mode=conf.mode.value,
        latency_ms=elapsed,
        tokens_local=response.total_tokens, tokens_claude=tokens_claude,
    )

    print("\n" + "=" * 60)
    print(f"  REFINEMENT PROPOSAL — {file_path.name}")
    print("=" * 60)
    print(final_text)
    print("=" * 60)
    return 0


def cmd_help(_: list[str]) -> int:
    print(__doc__)
    return 0


# ──────────────────────────────────────────────
# DISPATCH
# ──────────────────────────────────────────────

COMMANDS = {
    "query":  cmd_query,
    "ping":   cmd_ping,
    "stats":  cmd_stats,
    "tail":   cmd_tail,
    "config": cmd_config,
    "test":   cmd_test,
    "refine": cmd_refine,
    "help":   cmd_help,
    "--help": cmd_help,
    "-h":     cmd_help,
}


def main() -> int:
    if len(sys.argv) < 2:
        return cmd_help([])
    cmd = sys.argv[1]
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return cmd_help([])
    return handler(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
