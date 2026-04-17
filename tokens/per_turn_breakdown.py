#!/usr/bin/env python3
"""
Toke — per_turn_breakdown.py v2.0

AAA per-turn token + cost attribution for Claude Code session transcripts.
The definitive instrument for understanding where tokens go and what they cost.

Analyses:
  summary     One-screen session overview (default)
  growth      Top N turns by context growth with classification
  cost        Per-turn cost attribution with cumulative tracking
  output      Output token hotspot analysis
  compaction  Compaction event detection and impact
  tools       Per-tool lifetime cost attribution
  full        All turns, all columns
  chains      Repeated tool call sequence detection (bigrams + trigrams)

Modes:
  --current            Most recently modified transcript
  --session <path>     Explicit transcript file
  --project <name>     Aggregate all sessions in a project
  --top <N>            Limit ranked views (default 15)
  --json               Machine-readable output

Pricing: reads routing_manifest.toml (single source of truth).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOKE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = TOKE_ROOT / "automations" / "brain" / "routing_manifest.toml"
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))

# ── Pricing ─────────────────────────────────────────────────────────────────

def load_manifest_prices() -> dict:
    try:
        with open(MANIFEST, "rb") as f:
            m = tomllib.load(f)
        return m.get("models", {})
    except Exception:
        return {}

def price_for_model(model_id: str, manifest: dict) -> tuple[float, float, float]:
    """Returns (input_$/MTok, output_$/MTok, cache_read_$/MTok) for a model."""
    mid = model_id.lower()
    alias = "opus[1m]" if ("opus" in mid and "1m" in mid) else \
            "opus" if "opus" in mid else \
            "sonnet" if "sonnet" in mid else \
            "haiku" if "haiku" in mid else "unknown"
    cfg = manifest.get(alias, {})
    return (
        float(cfg.get("cost_input_per_mtok", 5.0)),
        float(cfg.get("cost_output_per_mtok", 25.0)),
        float(cfg.get("cost_cache_read_per_mtok", 0.50)),
    )


# ── Turn data ───────────────────────────────────────────────────────────────

@dataclass
class Turn:
    num: int
    ts: str
    model: str
    input_tok: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output_tok: int
    effective_ctx: int
    ctx_delta: int
    tools: list[str]
    cost: float = 0.0
    cumulative_cost: float = 0.0

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_read + self.cache_write
        return (self.cache_read / total) if total else 0.0

    def classify_growth(self) -> str:
        if self.num == 1:
            return "BOOT"
        if self.ctx_delta < -5000:
            return "COMPACT"
        if self.ctx_delta < 0:
            return "shrink"
        if self.ctx_delta == 0:
            return "flat"
        if self.ctx_delta < 1000:
            return "small"
        if self.ctx_delta < 5000:
            return "medium"
        if self.ctx_delta < 15000:
            return "large"
        return "SPIKE"


# ── Parser ──────────────────────────────────────────────────────────────────

def parse_transcript(path: Path, manifest: dict) -> list[Turn]:
    turns: list[Turn] = []
    turn_num = 0
    prev_ctx = 0
    cumulative = 0.0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message", {})
            usage = msg.get("usage", {})
            if not usage:
                continue

            turn_num += 1
            inp = usage.get("input_tokens", 0)
            cr = usage.get("cache_read_input_tokens", 0)
            cc = usage.get("cache_creation", {})
            cw5 = cc.get("ephemeral_5m_input_tokens", 0)
            cw1 = cc.get("ephemeral_1h_input_tokens", 0)
            out = usage.get("output_tokens", 0)
            eff = inp + cr + cw5 + cw1
            delta = eff - prev_ctx

            tool_names = [b.get("name", "?") for b in msg.get("content", [])
                          if isinstance(b, dict) and b.get("type") == "tool_use"]

            model_id = msg.get("model", d.get("model", ""))
            inp_rate, out_rate, cr_rate = price_for_model(model_id, manifest)
            turn_cost = (
                (inp / 1e6) * inp_rate
                + (out / 1e6) * out_rate
                + (cr / 1e6) * cr_rate
                + (cw5 / 1e6) * inp_rate * 1.25
                + (cw1 / 1e6) * inp_rate * 2.0
            )
            cumulative += turn_cost

            turns.append(Turn(
                num=turn_num, ts=d.get("timestamp", ""), model=model_id,
                input_tok=inp, cache_read=cr, cache_write_5m=cw5,
                cache_write_1h=cw1, output_tok=out, effective_ctx=eff,
                ctx_delta=delta, tools=tool_names, cost=turn_cost,
                cumulative_cost=cumulative,
            ))
            prev_ctx = eff
    return turns


# ── Finders ─────────────────────────────────────────────────────────────────

def find_current_transcript() -> Path | None:
    if not CLAUDE_PROJECTS.exists():
        return None
    best, best_mt = None, 0.0
    try:
        for proj in CLAUDE_PROJECTS.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    mt = f.stat().st_mtime
                except (PermissionError, OSError):
                    continue
                if mt > best_mt:
                    best_mt, best = mt, f
    except PermissionError:
        pass
    return best

def find_project_transcripts(name: str) -> list[Path]:
    out = []
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir() or name.lower() not in proj.name.lower():
            continue
        out.extend(sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime))
    return out


# ── Spark bar ───────────────────────────────────────────────────────────────

def spark(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = min(width, int(round(value / max_val * width)))
    return "|" * filled + " " * (width - filled)


# ── Analysis: summary ───────────────────────────────────────────────────────

def analysis_summary(turns: list[Turn], sid: str, path: Path) -> None:
    n = len(turns)
    total_out = sum(t.output_tok for t in turns)
    total_cw = sum(t.cache_write for t in turns)
    total_cr = sum(t.cache_read for t in turns)
    total_cost = sum(t.cost for t in turns)
    peak_ctx = max(t.effective_ctx for t in turns)
    peak_turn = max(turns, key=lambda t: t.effective_ctx)
    peak_growth_t = max(turns, key=lambda t: t.ctx_delta)
    compactions = [t for t in turns if t.ctx_delta < -5000]
    spikes = [t for t in turns if t.ctx_delta >= 15000 and t.num > 1]
    hit_rate = total_cr / (total_cr + total_cw) if (total_cr + total_cw) else 0
    mp = load_manifest_prices()
    cost_input = sum((t.input_tok / 1e6) * price_for_model(t.model, mp)[0] for t in turns)
    cost_output = sum((t.output_tok / 1e6) * price_for_model(t.model, mp)[1] for t in turns)
    cost_cache_r = sum((t.cache_read / 1e6) * price_for_model(t.model, mp)[2] for t in turns)
    cost_cache_w = sum(
        (t.cache_write_5m / 1e6) * price_for_model(t.model, mp)[0] * 1.25
        + (t.cache_write_1h / 1e6) * price_for_model(t.model, mp)[0] * 2.0
        for t in turns
    )

    # Tool frequency
    tool_counts: Counter = Counter()
    for t in turns:
        for tl in t.tools:
            tool_counts[tl] += 1
    top_tools = tool_counts.most_common(8)

    # Output hotspots
    out_sorted = sorted(turns, key=lambda t: t.output_tok, reverse=True)
    top_out = out_sorted[:3]

    # Time span
    first_ts = turns[0].ts[:19] if turns else "?"
    last_ts = turns[-1].ts[:19] if turns else "?"

    W = 80
    print("=" * W)
    print(f"  PER-TURN BREAKDOWN v2.0 — {sid[:8]}")
    print("=" * W)
    print(f"  Transcript:  {path.name}")
    print(f"  Span:        {first_ts}  ..  {last_ts}")
    print(f"  Turns:       {n} assistant")
    print()
    print(f"  TOKENS")
    print(f"  {'Cache read:':<20} {total_cr:>12,}    {'Cache write:':<16} {total_cw:>12,}")
    print(f"  {'Output:':<20} {total_out:>12,}    {'Cache hit rate:':<16} {hit_rate:>11.1%}")
    print(f"  {'Peak context:':<20} {peak_ctx:>12,}    {'at turn:':<16} {peak_turn.num:>12}")
    print()
    print(f"  COST BREAKDOWN                          ${total_cost:>8.2f} total")
    print(f"  {'-'*56}")
    bars_max = max(cost_cache_r, cost_cache_w, cost_output, cost_input, 0.01)
    print(f"  Cache read   ${cost_cache_r:>7.2f}  {spark(cost_cache_r, bars_max, 30)}")
    print(f"  Cache write  ${cost_cache_w:>7.2f}  {spark(cost_cache_w, bars_max, 30)}")
    print(f"  Output       ${cost_output:>7.2f}  {spark(cost_output, bars_max, 30)}")
    print(f"  Fresh input  ${cost_input:>7.2f}  {spark(cost_input, bars_max, 30)}")
    print()
    # Build preceding-turn lookup for growth attribution
    prev_tools_map: dict[int, list[str]] = {}
    for i, t in enumerate(turns):
        if i > 0:
            prev_tools_map[t.num] = turns[i - 1].tools

    def caused_by(t: Turn) -> str:
        pt = prev_tools_map.get(t.num, [])
        return ", ".join(pt[:3]) if pt else "(boot)" if t.num == 1 else "(prev output)"

    print(f"  GROWTH EVENTS (caused by preceding turn's tools)")
    print(f"  Peak growth:   +{peak_growth_t.ctx_delta:>8,} tok  turn {peak_growth_t.num:<5}  << {caused_by(peak_growth_t)}")
    print(f"  Spikes (>15K): {len(spikes):<5} Compactions (<-5K): {len(compactions)}")
    if spikes:
        for s in spikes[:3]:
            print(f"    turn {s.num:>4}  +{s.ctx_delta:>8,}  << {caused_by(s)}")
    if compactions:
        for c in compactions[:3]:
            print(f"    turn {c.num:>4}  {c.ctx_delta:>9,}  COMPACTION")
    print()
    print(f"  OUTPUT HOTSPOTS (top 3 by output tokens)")
    for t in top_out:
        print(f"    turn {t.num:>4}  {t.output_tok:>8,} tok  ${t.output_tok/1e6*price_for_model(t.model, mp)[1]:>5.2f}  {', '.join(t.tools[:3]) or '(text)'}")
    print()
    if top_tools:
        print(f"  TOOL FREQUENCY (top 8)")
        max_tc = top_tools[0][1] if top_tools else 1
        for name, cnt in top_tools:
            print(f"    {name:<18} {cnt:>4}  {spark(cnt, max_tc, 25)}")
    print("=" * W)


# ── Analysis: growth ────────────────────────────────────────────────────────

def analysis_growth(turns: list[Turn], top_n: int) -> None:
    # Build preceding-turn lookup: growth on turn N is caused by tools on turn N-1
    prev_tools: dict[int, list[str]] = {}
    for i, t in enumerate(turns):
        if i > 0:
            prev_tools[t.num] = turns[i - 1].tools
        else:
            prev_tools[t.num] = []

    ranked = sorted(turns, key=lambda t: t.ctx_delta, reverse=True)[:top_n]
    max_delta = ranked[0].ctx_delta if ranked else 1
    print(f"  Context growth is caused by the PRECEDING turn's tool results entering cache.")
    print(f"  'caused by' shows tools from the turn that generated the new context.")
    print()
    print(f"  {'#':>3}  {'turn':>5}  {'delta':>10}  {'eff_ctx':>10}  {'$cost':>7}  {'class':>8}  {'bar':<22}  caused by (prev turn)")
    print("  " + "-" * 105)
    for i, t in enumerate(ranked, 1):
        sign = "+" if t.ctx_delta >= 0 else ""
        cls = t.classify_growth()
        bar = spark(max(t.ctx_delta, 0), max(max_delta, 1), 20)
        pt = prev_tools.get(t.num, [])
        caused = ", ".join(pt[:4]) if pt else "(boot/prompt)" if t.num == 1 else "(prev text only)"
        print(f"  {i:>3}  {t.num:>5}  {sign}{t.ctx_delta:>9,}  {t.effective_ctx:>10,}  ${t.cost:>5.2f}  {cls:>8}  {bar}  {caused}")


# ── Analysis: cost ──────────────────────────────────────────────────────────

def analysis_cost(turns: list[Turn], top_n: int) -> None:
    ranked = sorted(turns, key=lambda t: t.cost, reverse=True)[:top_n]
    max_cost = ranked[0].cost if ranked else 0.01
    print(f"  {'#':>3}  {'turn':>5}  {'$cost':>8}  {'$cumul':>8}  {'output':>8}  {'cache_w':>8}  {'bar':<22}  tools")
    print("  " + "-" * 100)
    for i, t in enumerate(ranked, 1):
        bar = spark(t.cost, max_cost, 20)
        tools = ", ".join(t.tools[:4]) or "(text)"
        print(f"  {i:>3}  {t.num:>5}  ${t.cost:>6.3f}  ${t.cumulative_cost:>6.2f}  {t.output_tok:>8,}  {t.cache_write:>8,}  {bar}  {tools}")


# ── Analysis: output ────────────────────────────────────────────────────────

def analysis_output(turns: list[Turn], top_n: int) -> None:
    ranked = sorted(turns, key=lambda t: t.output_tok, reverse=True)[:top_n]
    max_out = ranked[0].output_tok if ranked else 1
    total_out = sum(t.output_tok for t in turns)
    print(f"  Total output: {total_out:,} tokens  |  Top {top_n} account for {sum(t.output_tok for t in ranked)/max(total_out,1):.0%}")
    print()
    print(f"  {'#':>3}  {'turn':>5}  {'output':>8}  {'$out':>7}  {'share':>6}  {'bar':<22}  tools")
    print("  " + "-" * 88)
    for i, t in enumerate(ranked, 1):
        bar = spark(t.output_tok, max_out, 20)
        share = t.output_tok / max(total_out, 1)
        tools = ", ".join(t.tools[:4]) or "(text)"
        print(f"  {i:>3}  {t.num:>5}  {t.output_tok:>8,}  ${t.output_tok/1e6*price_for_model(t.model, mp)[1]:>5.2f}  {share:>5.1%}  {bar}  {tools}")


# ── Analysis: compaction ────────────────────────────────────────────────────

def analysis_compaction(turns: list[Turn]) -> None:
    events = [t for t in turns if t.ctx_delta < -1000]
    if not events:
        print("  No compaction events detected (no turns with ctx_delta < -1000).")
        print("  Session stayed within context window limits.")
        return
    print(f"  {len(events)} compaction event(s) detected")
    print()
    print(f"  {'turn':>5}  {'delta':>10}  {'before':>10}  {'after':>10}  {'freed':>8}  {'freed%':>7}")
    print("  " + "-" * 65)
    for t in events:
        before = t.effective_ctx - t.ctx_delta
        freed = abs(t.ctx_delta)
        pct = freed / max(before, 1)
        print(f"  {t.num:>5}  {t.ctx_delta:>10,}  {before:>10,}  {t.effective_ctx:>10,}  {freed:>8,}  {pct:>6.1%}")


# ── Analysis: tools ─────────────────────────────────────────────────────────

def analysis_tools(turns: list[Turn], top_n: int) -> None:
    tool_data: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "output": 0, "cw": 0})
    for t in turns:
        if t.tools:
            per_tool_cost = t.cost / max(len(t.tools), 1)
            per_tool_out = t.output_tok // max(len(t.tools), 1)
            per_tool_cw = t.cache_write // max(len(t.tools), 1)
            for name in t.tools:
                d = tool_data[name]
                d["calls"] += 1
                d["cost"] += per_tool_cost
                d["output"] += per_tool_out
                d["cw"] += per_tool_cw
    ranked = sorted(tool_data.items(), key=lambda kv: kv[1]["cost"], reverse=True)[:top_n]
    max_cost = ranked[0][1]["cost"] if ranked else 0.01
    total_cost = sum(v["cost"] for _, v in ranked)
    print(f"  Estimated per-tool cost attribution (proportional split on shared turns)")
    print()
    print(f"  {'tool':<18}  {'calls':>5}  {'$cost':>8}  {'share':>6}  {'output':>8}  {'cache_w':>8}  {'bar':<22}")
    print("  " + "-" * 90)
    for name, d in ranked:
        bar = spark(d["cost"], max_cost, 20)
        share = d["cost"] / max(total_cost, 0.01)
        print(f"  {name:<18}  {d['calls']:>5}  ${d['cost']:>6.3f}  {share:>5.1%}  {d['output']:>8,}  {d['cw']:>8,}  {bar}")


# ── Analysis: chains ────────────────────────────────────────────────────────

def analysis_chains(turns: list[Turn], top_n: int) -> None:
    """Detect repeated tool call sequences (bigrams and trigrams) across consecutive turns."""
    # Build the tool sequence: one representative tool per turn (first tool), skip empty turns
    seq: list[tuple[int, str]] = []  # (turn_num, first_tool)
    for t in turns:
        if t.tools:
            seq.append((t.num, t.tools[0]))

    if len(seq) < 2:
        print("  Not enough tool-using turns to build chains.")
        return

    # Collect bigrams and trigrams with example turn ranges
    bigrams:  Counter = Counter()
    trigrams: Counter = Counter()
    bigram_examples:  dict[str, list[str]] = defaultdict(list)
    trigram_examples: dict[str, list[str]] = defaultdict(list)

    for i in range(len(seq) - 1):
        # Only count if turns are consecutive (no large gaps — still allow any consecutive tool turns)
        b_key = f"{seq[i][1]} -> {seq[i+1][1]}"
        bigrams[b_key] += 1
        span = f"{seq[i][0]}-{seq[i+1][0]}"
        if len(bigram_examples[b_key]) < 6:
            bigram_examples[b_key].append(span)

    for i in range(len(seq) - 2):
        t_key = f"{seq[i][1]} -> {seq[i+1][1]} -> {seq[i+2][1]}"
        trigrams[t_key] += 1
        span = f"{seq[i][0]}-{seq[i+2][0]}"
        if len(trigram_examples[t_key]) < 6:
            trigram_examples[t_key].append(span)

    # Merge and rank all chains
    all_chains: list[tuple[str, int, list[str]]] = []
    for key, cnt in bigrams.items():
        all_chains.append((key, cnt, bigram_examples[key]))
    for key, cnt in trigrams.items():
        all_chains.append((key, cnt, trigram_examples[key]))

    all_chains.sort(key=lambda x: x[1], reverse=True)
    ranked = all_chains[:top_n]

    if not ranked:
        print("  No repeated chains found.")
        return

    total_chains = sum(cnt for _, cnt, _ in all_chains)
    max_cnt = ranked[0][1]

    # Column widths
    chain_w = max(len(c) for c, _, _ in ranked)
    chain_w = max(chain_w, 30)

    print(f"  Chains: bigrams + trigrams across consecutive tool-using turns")
    print(f"  First tool per turn used as representative. {len(seq)} tool-using turns total.")
    print()
    print(f"  {'chain':<{chain_w}}  {'count':>6}  {'share':>6}  example turns")
    print("  " + "-" * (chain_w + 30))
    for chain, cnt, examples in ranked:
        share = cnt / max(total_chains, 1)
        ex_str = "[" + ", ".join(examples[:5])
        if cnt > len(examples):
            ex_str += ", ..."
        ex_str += "]"
        print(f"  {chain:<{chain_w}}  {cnt:>6}  {share:>5.1%}  {ex_str}")


# ── Analysis: full ──────────────────────────────────────────────────────────

def analysis_full(turns: list[Turn]) -> None:
    print(f"  {'turn':>5}  {'delta':>9}  {'eff_ctx':>9}  {'cache_r':>8}  {'cache_w':>8}  {'output':>8}  {'$cost':>7}  {'$cum':>7}  {'class':>8}  tools")
    print("  " + "-" * 110)
    for t in turns:
        sign = "+" if t.ctx_delta >= 0 else ""
        cls = t.classify_growth()
        tools = ", ".join(t.tools[:3])
        if len(t.tools) > 3:
            tools += f" +{len(t.tools)-3}"
        print(f"  {t.num:>5}  {sign}{t.ctx_delta:>8,}  {t.effective_ctx:>9,}  {t.cache_read:>8,}  {t.cache_write:>8,}  {t.output_tok:>8,}  ${t.cost:>5.2f}  ${t.cumulative_cost:>5.0f}  {cls:>8}  {tools}")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Toke per-turn breakdown v2.0 — AAA session token + cost attribution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--current", action="store_true", help="Most recent transcript")
    grp.add_argument("--session", type=str, help="Path to a .jsonl transcript")
    grp.add_argument("--project", type=str, help="Project name substring (aggregates)")
    parser.add_argument("analysis", nargs="?", default="summary",
                        choices=["summary", "growth", "cost", "output", "compaction", "tools", "full", "chains"],
                        help="Analysis type (default: summary)")
    parser.add_argument("--top", type=int, default=15, help="Limit ranked views (default 15)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    manifest = load_manifest_prices()

    paths: list[Path] = []
    if args.project:
        paths = find_project_transcripts(args.project)
        if not paths:
            print(f"No transcripts found for project '{args.project}'.", file=sys.stderr)
            return 1
    else:
        p = Path(args.session) if args.session else find_current_transcript()
        if not p or not p.exists():
            print("No transcript found.", file=sys.stderr)
            return 1
        paths = [p]

    all_turns: list[Turn] = []
    for p in paths:
        all_turns.extend(parse_transcript(p, manifest))

    if not all_turns:
        print("No assistant turns found.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([{
            "turn": t.num, "ts": t.ts, "ctx_delta": t.ctx_delta,
            "effective_ctx": t.effective_ctx, "cache_write": t.cache_write,
            "cache_read": t.cache_read, "output": t.output_tok,
            "cost": round(t.cost, 4), "cumulative_cost": round(t.cumulative_cost, 2),
            "growth_class": t.classify_growth(), "tools": t.tools,
        } for t in all_turns], indent=2))
        return 0

    sid = paths[0].stem if len(paths) == 1 else f"AGGREGATE ({len(paths)} sessions)"
    path_display = paths[0] if len(paths) == 1 else Path(f"<{len(paths)} files>")

    W = 80
    if args.analysis == "summary":
        analysis_summary(all_turns, sid, path_display)
    else:
        print("=" * W)
        print(f"  {args.analysis.upper()} — {sid[:8] if len(paths)==1 else sid}")
        print("=" * W)
        if args.analysis == "growth":
            analysis_growth(all_turns, args.top)
        elif args.analysis == "cost":
            analysis_cost(all_turns, args.top)
        elif args.analysis == "output":
            analysis_output(all_turns, args.top)
        elif args.analysis == "compaction":
            analysis_compaction(all_turns)
        elif args.analysis == "tools":
            analysis_tools(all_turns, args.top)
        elif args.analysis == "full":
            analysis_full(all_turns)
        elif args.analysis == "chains":
            analysis_chains(all_turns, args.top)
        print("=" * W)

    return 0


if __name__ == "__main__":
    sys.exit(main())
