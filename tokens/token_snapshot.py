#!/usr/bin/env python3
"""
Toke — token_snapshot.py

Per-session token breakdown with cache economics and cost attribution.
This is the "first thing to build" from PROJECT_BRIEF §3 — the instrument
that shows where tokens actually go for a single Claude Code session.

Reads:  ~/.claude/projects/<project>/<session_id>.jsonl  (Claude Code transcripts)
        Toke/automations/brain/routing_manifest.toml     (pricing — single source)

Modes:
  --current          Auto-detect the most recently modified session transcript
  --session <path>   Explicit path to a transcript .jsonl
  --project <name>   Scan a project directory and aggregate all sessions
  --turns            Print per-turn breakdown (verbose)
  --json             Machine-readable output

Pricing note:
  Uses routing_manifest.toml [models.*] values as the canonical price table.
  Verified against Anthropic docs 2026-04-11: Opus 4.6 = $5/$25/$0.50.
  See tokens/PRICING_NOTES.md for the full table and cache multiplier notes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 guard

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TOKE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = TOKE_ROOT / "automations" / "brain" / "routing_manifest.toml"
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))


# ---------------------------------------------------------------------------
# Pricing (loaded from manifest — single source of truth)
# ---------------------------------------------------------------------------
@dataclass
class Prices:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_5m_factor: float = 1.25  # 5m ephemeral = 1.25x input
    cache_write_1h_factor: float = 2.00  # 1h ephemeral = 2.00x input


def load_prices() -> dict[str, Prices]:
    with open(MANIFEST, "rb") as f:
        m = tomllib.load(f)
    models = m.get("models", {})
    out: dict[str, Prices] = {}
    for alias, cfg in models.items():
        out[alias] = Prices(
            input_per_mtok=float(cfg.get("cost_input_per_mtok", 0)),
            output_per_mtok=float(cfg.get("cost_output_per_mtok", 0)),
            cache_read_per_mtok=float(cfg.get("cost_cache_read_per_mtok", 0)),
        )
    return out


def price_of(model_full_id: str, prices: dict[str, Prices]) -> tuple[str, Prices]:
    """Map a full model id (e.g. 'claude-opus-4-6') to a manifest alias + Prices."""
    fallback = Prices(0, 0, 0)
    mid = model_full_id.lower()
    if "opus" in mid and "1m" in mid:
        return "opus[1m]", prices.get("opus[1m]") or prices.get("opus") or fallback
    if "opus" in mid:
        return "opus", prices.get("opus") or fallback
    if "sonnet" in mid:
        return "sonnet", prices.get("sonnet") or fallback
    if "haiku" in mid:
        return "haiku", prices.get("haiku") or fallback
    return "unknown", fallback


# ---------------------------------------------------------------------------
# Per-turn usage container
# ---------------------------------------------------------------------------
@dataclass
class TurnUsage:
    ts: str
    model: str
    input_tok: int
    cache_read_tok: int
    cache_write_5m_tok: int
    cache_write_1h_tok: int
    output_tok: int
    stop_reason: str | None = None
    tool_uses: int = 0

    @property
    def cache_write_total(self) -> int:
        return self.cache_write_5m_tok + self.cache_write_1h_tok

    @property
    def effective_input(self) -> int:
        return self.input_tok + self.cache_read_tok + self.cache_write_total

    def cost(self, prices: Prices) -> float:
        c = (
            (self.input_tok / 1_000_000) * prices.input_per_mtok
            + (self.output_tok / 1_000_000) * prices.output_per_mtok
            + (self.cache_read_tok / 1_000_000) * prices.cache_read_per_mtok
            + (self.cache_write_5m_tok / 1_000_000)
            * prices.input_per_mtok
            * prices.cache_write_5m_factor
            + (self.cache_write_1h_tok / 1_000_000)
            * prices.input_per_mtok
            * prices.cache_write_1h_factor
        )
        return c


# ---------------------------------------------------------------------------
# Session aggregate
# ---------------------------------------------------------------------------
@dataclass
class SessionSnapshot:
    session_id: str
    path: Path
    cwd: str = ""
    git_branch: str = ""
    first_ts: str = ""
    last_ts: str = ""
    turns: list[TurnUsage] = field(default_factory=list)
    user_messages: int = 0
    tool_results: int = 0

    def per_model(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in self.turns:
            bucket = out.setdefault(
                t.model,
                {
                    "turns": 0,
                    "input": 0,
                    "cache_read": 0,
                    "cache_write_5m": 0,
                    "cache_write_1h": 0,
                    "output": 0,
                    "tool_uses": 0,
                },
            )
            bucket["turns"] += 1
            bucket["input"] += t.input_tok
            bucket["cache_read"] += t.cache_read_tok
            bucket["cache_write_5m"] += t.cache_write_5m_tok
            bucket["cache_write_1h"] += t.cache_write_1h_tok
            bucket["output"] += t.output_tok
            bucket["tool_uses"] += t.tool_uses
        return out

    def total_cost(self, prices: dict[str, Prices]) -> float:
        total = 0.0
        for t in self.turns:
            _, p = price_of(t.model, prices)
            if p:
                total += t.cost(p)
        return total

    def cache_hit_rate(self) -> float:
        read = sum(t.cache_read_tok for t in self.turns)
        written = sum(t.cache_write_total for t in self.turns)
        denom = read + written
        return (read / denom) if denom else 0.0

    def context_growth(self) -> list[int]:
        """Effective prompt size (input + cache_read + cache_write) per turn —
        approximates how much context was loaded into each prompt."""
        return [t.effective_input for t in self.turns]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_transcript(path: Path) -> SessionSnapshot:
    snap = SessionSnapshot(session_id=path.stem, path=path)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if not snap.cwd:
                snap.cwd = d.get("cwd", "")
            if not snap.git_branch:
                snap.git_branch = d.get("gitBranch", "")
            ts = d.get("timestamp", "")
            if ts:
                if not snap.first_ts:
                    snap.first_ts = ts
                snap.last_ts = ts
            if t == "user":
                snap.user_messages += 1
                msg = d.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            snap.tool_results += 1
            elif t == "assistant":
                msg = d.get("message", {})
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage") or {}
                model = msg.get("model", "unknown")
                cache_creation = usage.get("cache_creation") or {}
                cw_5m = int(cache_creation.get("ephemeral_5m_input_tokens", 0))
                cw_1h = int(cache_creation.get("ephemeral_1h_input_tokens", 0))
                # some entries only have the aggregate
                if cw_5m == 0 and cw_1h == 0:
                    total_cw = int(usage.get("cache_creation_input_tokens", 0))
                    cw_5m = total_cw  # attribute to 5m by default

                # count tool uses in the content
                tool_uses = 0
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool_uses += 1

                snap.turns.append(
                    TurnUsage(
                        ts=ts,
                        model=model,
                        input_tok=int(usage.get("input_tokens", 0)),
                        cache_read_tok=int(usage.get("cache_read_input_tokens", 0)),
                        cache_write_5m_tok=cw_5m,
                        cache_write_1h_tok=cw_1h,
                        output_tok=int(usage.get("output_tokens", 0)),
                        stop_reason=msg.get("stop_reason"),
                        tool_uses=tool_uses,
                    )
                )
    return snap


def find_current_session() -> Path | None:
    """Return the most recently modified .jsonl across all projects."""
    if not CLAUDE_PROJECTS.exists():
        return None
    candidates = []
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            candidates.append(f)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_project(name: str) -> Path | None:
    if not CLAUDE_PROJECTS.exists():
        return None
    # case-insensitive contains match
    for proj in CLAUDE_PROJECTS.iterdir():
        if name.lower() in proj.name.lower():
            return proj
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def fmt_num(n: int | float) -> str:
    if isinstance(n, float):
        return f"{n:,.2f}"
    return f"{n:,}"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def render_text(snap: SessionSnapshot, prices: dict[str, Prices], show_turns: bool) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"  TOKEN SNAPSHOT — session {snap.session_id[:8]}")
    lines.append("=" * 72)
    lines.append(f"  Path:       {snap.path}")
    lines.append(f"  CWD:        {snap.cwd}")
    if snap.git_branch:
        lines.append(f"  Branch:     {snap.git_branch}")
    lines.append(f"  Span:       {snap.first_ts}  →  {snap.last_ts}")
    lines.append(f"  Turns:      {len(snap.turns)} assistant | {snap.user_messages} user | {snap.tool_results} tool_results")
    lines.append("")

    per_model = snap.per_model()
    if not per_model:
        lines.append("  (no assistant turns with usage data)")
        return "\n".join(lines)

    total_input = sum(b["input"] for b in per_model.values())
    total_read = sum(b["cache_read"] for b in per_model.values())
    total_write_5m = sum(b["cache_write_5m"] for b in per_model.values())
    total_write_1h = sum(b["cache_write_1h"] for b in per_model.values())
    total_output = sum(b["output"] for b in per_model.values())
    total_cost = snap.total_cost(prices)

    lines.append("  Per-model breakdown")
    lines.append("  " + "-" * 68)
    lines.append(
        f"  {'model':<22} {'turns':>6} {'input':>10} {'cache_r':>10} {'cache_w':>10} {'output':>10}"
    )
    for model_id, b in sorted(per_model.items(), key=lambda kv: -kv[1]["input"] - kv[1]["cache_read"]):
        alias, _ = price_of(model_id, prices)
        cache_w_total = b["cache_write_5m"] + b["cache_write_1h"]
        lines.append(
            f"  {alias:<22} {b['turns']:>6} {fmt_tok(b['input']):>10} "
            f"{fmt_tok(b['cache_read']):>10} {fmt_tok(cache_w_total):>10} "
            f"{fmt_tok(b['output']):>10}"
        )
    lines.append("")
    lines.append("  Totals")
    lines.append("  " + "-" * 68)
    lines.append(f"  Fresh input      {fmt_tok(total_input):>12}  (uncached prompt tokens)")
    lines.append(f"  Cache read       {fmt_tok(total_read):>12}  (warm cache hits)")
    lines.append(f"  Cache write 5m   {fmt_tok(total_write_5m):>12}  (ephemeral 5m writes)")
    lines.append(f"  Cache write 1h   {fmt_tok(total_write_1h):>12}  (ephemeral 1h writes)")
    lines.append(f"  Output           {fmt_tok(total_output):>12}  (generated tokens)")
    lines.append("")
    cache_hit = snap.cache_hit_rate() * 100
    lines.append(f"  Cache hit rate   {cache_hit:>11.1f}%  (read / (read + write))")
    if snap.turns:
        growth = snap.context_growth()
        avg_ctx = sum(growth) / len(growth)
        peak_ctx = max(growth)
        lines.append(f"  Avg effective context  {fmt_tok(int(avg_ctx)):>8}")
        lines.append(f"  Peak effective context {fmt_tok(peak_ctx):>8}")
    lines.append("")
    lines.append(f"  Estimated cost   ${total_cost:>11,.2f}  (manifest pricing verified 2026-04-11)")
    lines.append("")

    if show_turns:
        lines.append("  Per-turn detail (assistant turns only)")
        lines.append("  " + "-" * 68)
        lines.append(
            f"  {'#':>3} {'time':<20} {'model':<14} {'in':>7} {'c_r':>8} {'c_w':>7} {'out':>7} {'tools':>5}"
        )
        for i, t in enumerate(snap.turns):
            alias, _ = price_of(t.model, prices)
            lines.append(
                f"  {i+1:>3} {t.ts[:19]:<20} {alias:<14} "
                f"{fmt_tok(t.input_tok):>7} {fmt_tok(t.cache_read_tok):>8} "
                f"{fmt_tok(t.cache_write_total):>7} {fmt_tok(t.output_tok):>7} "
                f"{t.tool_uses:>5}"
            )
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


def render_json(snap: SessionSnapshot, prices: dict[str, Prices]) -> str:
    per_model = snap.per_model()
    out = {
        "session_id": snap.session_id,
        "path": str(snap.path),
        "cwd": snap.cwd,
        "git_branch": snap.git_branch,
        "first_ts": snap.first_ts,
        "last_ts": snap.last_ts,
        "turns": len(snap.turns),
        "user_messages": snap.user_messages,
        "tool_results": snap.tool_results,
        "per_model": per_model,
        "totals": {
            "input": sum(b["input"] for b in per_model.values()),
            "cache_read": sum(b["cache_read"] for b in per_model.values()),
            "cache_write_5m": sum(b["cache_write_5m"] for b in per_model.values()),
            "cache_write_1h": sum(b["cache_write_1h"] for b in per_model.values()),
            "output": sum(b["output"] for b in per_model.values()),
        },
        "cache_hit_rate": snap.cache_hit_rate(),
        "estimated_cost_usd": round(snap.total_cost(prices), 4),
        "pricing_source": "routing_manifest.toml [models.*]",
        "pricing_verified": "2026-04-11 against Anthropic docs; Opus 4.6 = $5/$25/$0.50",
    }
    return json.dumps(out, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Per-session token breakdown for Claude Code.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--current", action="store_true", help="Most recently modified transcript")
    src.add_argument("--session", type=str, help="Path to a .jsonl transcript")
    src.add_argument("--project", type=str, help="Project name substring (aggregates all sessions)")
    parser.add_argument("--turns", action="store_true", help="Show per-turn detail")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if not MANIFEST.exists():
        print(f"ERROR: manifest not found: {MANIFEST}", file=sys.stderr)
        return 2
    prices = load_prices()

    # resolve source
    if args.session:
        path = Path(args.session)
        if not path.exists():
            print(f"ERROR: session file not found: {path}", file=sys.stderr)
            return 2
        snap = parse_transcript(path)
        print(render_json(snap, prices) if args.json else render_text(snap, prices, args.turns))
        return 0

    if args.project:
        proj_dir = find_project(args.project)
        if not proj_dir:
            print(f"ERROR: project not found: {args.project}", file=sys.stderr)
            return 2
        jsonls = sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not jsonls:
            print(f"ERROR: no .jsonl sessions in {proj_dir}", file=sys.stderr)
            return 2
        if args.json:
            out = []
            for j in jsonls:
                s = parse_transcript(j)
                out.append(json.loads(render_json(s, prices)))
            print(json.dumps(out, indent=2))
        else:
            total_cost = 0.0
            total_turns = 0
            for j in jsonls:
                s = parse_transcript(j)
                print(render_text(s, prices, args.turns))
                total_cost += s.total_cost(prices)
                total_turns += len(s.turns)
            print()
            print(f"  PROJECT TOTAL: {len(jsonls)} sessions | {total_turns} turns | ${total_cost:,.2f}")
        return 0

    # default: --current
    path = find_current_session()
    if not path:
        print("ERROR: no session transcript found", file=sys.stderr)
        return 2
    snap = parse_transcript(path)
    print(render_json(snap, prices) if args.json else render_text(snap, prices, args.turns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
