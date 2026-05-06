#!/usr/bin/env python3
"""
Token Accountant — Toke's namesake meta-agent.
=================================================
Measures actual per-task input/output/cache/thinking tokens from tools.jsonl
+ Anthropic transcript usage objects. Reconciles predicted (Brain tier) vs
actual cost. Surfaces waste patterns: largest context-burners, lowest
cache-hit operations, prompts where actual cost exceeded prediction by >2x.

Design source: Toke/research/division_self_improving_agents_blueprint_2026-05-02.md §5.1
Research brief:  parallel research dispatch 2026-05-02 (3 gaps closed).

Cycle 1 SCOPE (shipped 2026-05-02 morning):
  - Join decisions.jsonl <-> tools.jsonl by decision_id (primary) or
    (session_id, ts) windowed-fallback (legacy data pre-2026-05-02 sentinel).
  - Per-session token receipt (CHAR-byte aggregation only).
  - Skeleton functions for cache-thrash + long-tail spike detection.

Cycle 2 SCOPE (shipped 2026-05-02 evening — THIS FILE):
  - Transcript usage extraction via transcript_loader.py (msg.id-deduped).
  - Per-session $USD receipts joining transcript turns to tools.jsonl rows.
  - Full cache-thrash algorithm via cache_thrash.py (Bayesian-smoothed +
    chunk-hash divergence proposals to proposals/cache_restructure_*.jsonl).
  - Full long-tail spike algorithm via long_tail.py (multi-gate filter +
    spike-cause auto-diagnosis).
  - Predicted-vs-actual reconciliation via reconciliation.py (flags decisions
    whose actual transcript cost exceeded tier prediction by >2x).
  - Edge-case hardening: sentinel staleness mtime check (>1hr ignored),
    atomic sentinel writes, corrupt-line tolerance, stale-sentinel cleanup.

Cycle 3 SCOPE (pending):
  - SessionEnd hook auto-write per-session receipt
  - /toke workbench panel integration (per-division ROI cost vs prediction)
  - Weekly waste report aggregating cache_thrash + long_tail proposals

CLI:
    python token_accountant.py receipt --session <id>          # per-session receipt with $USD
    python token_accountant.py reconcile [--last N]            # join + tier-distribution report
    python token_accountant.py cache-thrash [--window 7]       # full cache hit-rate analysis
    python token_accountant.py long-tail [--window 30]         # full long-tail spike detection
    python token_accountant.py predicted-vs-actual [--last N]  # tier-prediction-vs-cost report
    python token_accountant.py sentinel-gc [--max-age-hr N]    # purge stale decision_id sentinels
    python token_accountant.py weekly                          # weekly waste report (cycle 3)

Sacred Rule alignment:
  Rule 2  — read-only telemetry analysis; never mutates routing data
  Rule 5  — diagnostics-as-features (the receipts ARE the deliverable)
  Rule 6  — synthesis from receipts only, never invents capabilities
  Rule 11 — every load-bearing claim cites a tools.jsonl/decisions.jsonl line
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# Local modules (Cycle 2 additions)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_loader import (  # noqa: E402
    TranscriptTurn, find_transcript, load_session_turns, parse_transcript,
    find_all_transcripts,
)
from cost_model import (  # noqa: E402
    cost_from_turn, cost_breakdown, alias_for_tier,
    tier_predicted_cost_per_call, price_for,
)

# Ensure UTF-8 stdout/stderr on Windows (cp1252 breaks on non-ASCII)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

HOME = Path.home()
TELEMETRY_DIR = HOME / ".claude" / "telemetry" / "brain"
DECISIONS_FILE = TELEMETRY_DIR / "decisions.jsonl"
TOOLS_FILE = TELEMETRY_DIR / "tools.jsonl"
TOKE_ROOT = HOME / "Desktop" / "T1" / "Toke"
TA_DIR = TOKE_ROOT / "automations" / "homer" / "token_accountant"
RECEIPTS_DIR = TA_DIR / "receipts"
REPORTS_DIR = TA_DIR / "reports"
PROPOSALS_DIR = TA_DIR / "proposals"

# Sentinel pattern (paired with Toke/hooks/brain_hook_fast.js cmdHook+cmdTelemetry)
SENTINEL_GLOB = "_active_decision_*.txt"
SENTINEL_MAX_AGE_HR_DEFAULT = 1.0   # decision_id older than this = stale prior-session bleed


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------


@dataclass
class JoinedRow:
    """A tools.jsonl row joined to its driving decisions.jsonl entry."""
    tool_ts: str
    tool_name: str
    skill_name: str | None
    division: str | None
    session_id: str
    decision_id: str | None       # may be None for legacy rows
    join_method: str               # "decision_id" | "windowed" | "unjoined"
    decision_ts: str | None
    decision_tier: str | None
    decision_score: float | None
    decision_prompt_preview: str | None
    input_size: int                # CHAR count of tool input JSON (NOT API tokens)
    output_size: int               # CHAR count of tool output JSON (NOT API tokens)


# -----------------------------------------------------------------------------
# JSONL helpers
# -----------------------------------------------------------------------------


def iter_jsonl(path: Path, *, max_lines: int | None = None) -> Iterator[dict]:
    """Stream a .jsonl file line-by-line. Tolerates malformed lines (skip)."""
    if not path.exists():
        return
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if max_lines is not None and count >= max_lines:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
                count += 1
            except (json.JSONDecodeError, ValueError):
                continue


def parse_iso(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamp, returning None on malformed input."""
    if not ts:
        return None
    try:
        # Handle both `Z` suffix and explicit timezone
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------------------------------
# Join logic (decision_id primary; (session_id, ts) windowed fallback)
# -----------------------------------------------------------------------------


def build_decision_index(
    decisions_path: Path = DECISIONS_FILE,
    *,
    last_n: int | None = None,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Return (by_decision_id, by_session_chronological).

    by_decision_id: O(1) primary key lookup
    by_session_chronological: list of decisions per session, sorted by ts ASC
                              for windowed-fallback lookup
    """
    by_id: dict[str, dict] = {}
    by_session: dict[str, list[dict]] = defaultdict(list)

    decisions = list(iter_jsonl(decisions_path))
    if last_n:
        decisions = decisions[-last_n:]

    for d in decisions:
        did = d.get("decision_id")
        if did:
            by_id[did] = d
        sid = d.get("session_id", "")
        if sid:
            by_session[sid].append(d)

    # Sort each session's decisions by ts ASC for windowed lookup
    for sid in by_session:
        by_session[sid].sort(key=lambda x: x.get("ts", ""))

    return by_id, by_session


def find_active_decision(
    by_session: dict[str, list[dict]],
    session_id: str,
    tool_ts: str,
) -> dict | None:
    """Windowed fallback: latest decision in this session with ts <= tool_ts.

    Per research brief Gap 1: "Find the latest decision in the session whose
    ts <= tool_ts. That's the prompt that triggered this tool fire."
    """
    candidates = by_session.get(session_id, [])
    if not candidates:
        return None
    tool_dt = parse_iso(tool_ts)
    if tool_dt is None:
        return None
    best = None
    for d in candidates:
        d_dt = parse_iso(d.get("ts", ""))
        if d_dt is None:
            continue
        if d_dt > tool_dt:
            break  # decisions are sorted ASC; we passed the tool ts
        best = d
    return best


def join_tools_to_decisions(
    *,
    decisions_path: Path = DECISIONS_FILE,
    tools_path: Path = TOOLS_FILE,
    last_n_tools: int | None = None,
    session_id: str | None = None,
) -> Iterator[JoinedRow]:
    """Stream JoinedRow per tools.jsonl entry.

    Filters:
        last_n_tools: only the most-recent N entries (default: all)
        session_id:   only entries matching this session_id (default: all)

    Strategy:
        1) decision_id direct lookup (primary, O(1))
        2) windowed (session_id, ts) fallback (for legacy pre-sentinel rows)
        3) unjoined (no decisions in this session at all)
    """
    by_id, by_session = build_decision_index(decisions_path)

    tools = list(iter_jsonl(tools_path))
    if last_n_tools:
        tools = tools[-last_n_tools:]
    if session_id:
        tools = [t for t in tools if t.get("session_id") == session_id]

    for t in tools:
        tid = t.get("decision_id")
        sid = t.get("session_id", "")
        tts = t.get("ts", "")

        decision = None
        method = "unjoined"
        if tid and tid in by_id:
            decision = by_id[tid]
            method = "decision_id"
        else:
            decision = find_active_decision(by_session, sid, tts)
            if decision is not None:
                method = "windowed"

        result = (decision or {}).get("result") or {}
        yield JoinedRow(
            tool_ts=tts,
            tool_name=t.get("tool_name", ""),
            skill_name=t.get("skill_name"),
            division=t.get("division"),
            session_id=sid,
            decision_id=tid,
            join_method=method,
            decision_ts=(decision or {}).get("ts"),
            decision_tier=result.get("tier"),
            decision_score=result.get("score"),
            decision_prompt_preview=(decision or {}).get("prompt_text"),
            input_size=int(t.get("input_size", 0)),
            output_size=int(t.get("output_size", 0)),
        )


# -----------------------------------------------------------------------------
# Receipt builder (per-session)
# -----------------------------------------------------------------------------


def build_session_receipt(session_id: str, *, write_to_disk: bool = True) -> str:
    """Render a markdown receipt for one session.

    Cycle 1 scope: join + counts + char-byte-size aggregation. Token-cost
    reconciliation lands in Cycle 2 (requires transcript JSONL extraction).
    """
    rows = list(join_tools_to_decisions(session_id=session_id))
    if not rows:
        return f"# Token Receipt — {session_id}\n\nNo tool fires found for this session.\n"

    # Aggregations
    n_tools = len(rows)
    by_method = defaultdict(int)
    by_tool = defaultdict(int)
    by_skill = defaultdict(int)
    by_division = defaultdict(int)
    by_tier = defaultdict(int)
    total_in = 0
    total_out = 0
    decision_set: set[str] = set()
    first_ts = rows[0].tool_ts
    last_ts = rows[-1].tool_ts

    for r in rows:
        by_method[r.join_method] += 1
        by_tool[r.tool_name] += 1
        if r.skill_name:
            by_skill[r.skill_name] += 1
        if r.division:
            by_division[r.division] += 1
        if r.decision_tier:
            by_tier[r.decision_tier] += 1
        total_in += r.input_size
        total_out += r.output_size
        if r.decision_id:
            decision_set.add(r.decision_id)

    join_pct = (by_method["decision_id"] / n_tools * 100) if n_tools else 0
    win_pct = (by_method["windowed"] / n_tools * 100) if n_tools else 0
    unj_pct = (by_method["unjoined"] / n_tools * 100) if n_tools else 0

    md = []
    md.append(f"# Token Accountant Receipt — {session_id}\n")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    md.append(f"**Window:** {first_ts} → {last_ts}\n")
    md.append("")
    md.append("## Join Quality (decision_id <-> tool fire)\n")
    md.append(f"- Total tool fires: **{n_tools}**")
    md.append(f"- Distinct decisions covered: **{len(decision_set)}** (decision_id-keyed)")
    md.append(f"- Join methods: decision_id={by_method['decision_id']} ({join_pct:.0f}%), "
              f"windowed={by_method['windowed']} ({win_pct:.0f}%), "
              f"unjoined={by_method['unjoined']} ({unj_pct:.0f}%)")
    md.append("")
    md.append("## Tool Frequency\n")
    for tool, n in sorted(by_tool.items(), key=lambda x: -x[1])[:10]:
        md.append(f"- `{tool}` — {n} fires")
    md.append("")
    if by_skill:
        md.append("## Skill Fires\n")
        for skill, n in sorted(by_skill.items(), key=lambda x: -x[1]):
            md.append(f"- `{skill}` — {n}")
        md.append("")
    if by_division:
        md.append("## Division Attribution\n")
        for div, n in sorted(by_division.items(), key=lambda x: -x[1]):
            md.append(f"- `{div}` — {n}")
        md.append("")
    if by_tier:
        md.append("## Brain Tier Distribution (driving decisions)\n")
        for tier in ("S0", "S1", "S2", "S3", "S4", "S5"):
            if tier in by_tier:
                md.append(f"- `{tier}` — {by_tier[tier]} tool fires")
        md.append("")
    md.append("## Char-Byte Sizes (tools.jsonl)\n")
    md.append(f"- Total input chars: **{total_in:,}**")
    md.append(f"- Total output chars: **{total_out:,}**")
    md.append(f"- Implicit ratio: 1:{(total_out/total_in if total_in else 0):.2f}")
    md.append("")

    # --- Transcript-derived API token + $USD section (Cycle 2) ---
    transcript_section = _render_transcript_section(session_id)
    md.append(transcript_section)

    text = "\n".join(md) + "\n"
    if write_to_disk:
        RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = RECEIPTS_DIR / f"session_{session_id[:8]}_{date_part}.md"
        out.write_text(text, encoding="utf-8")
        print(f"Receipt written: {out}", file=sys.stderr)
    return text


# -----------------------------------------------------------------------------
# Reconciliation (last-N decisions)
# -----------------------------------------------------------------------------


def reconcile_last_n(last_n: int = 50) -> str:
    """Show join health + tier distribution across the last N decisions."""
    by_id, by_session = build_decision_index(last_n=last_n)
    sessions = list(by_session.keys())
    n_decisions = sum(len(v) for v in by_session.values())
    n_with_did = sum(1 for did in by_id if did)

    # Pull tools that touch any of these decisions' sessions
    rows = []
    for sid in sessions:
        rows.extend(list(join_tools_to_decisions(session_id=sid)))

    n_tools = len(rows)
    n_joined_did = sum(1 for r in rows if r.join_method == "decision_id")
    n_joined_win = sum(1 for r in rows if r.join_method == "windowed")
    n_unjoined = sum(1 for r in rows if r.join_method == "unjoined")

    md = []
    md.append(f"# Reconciliation — last {last_n} decisions\n")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    md.append(f"- Decisions analyzed: {n_decisions}")
    md.append(f"- Decisions with decision_id (post-2026-05-02 sentinel): {n_with_did} "
              f"({n_with_did/n_decisions*100 if n_decisions else 0:.0f}%)")
    md.append(f"- Sessions covered: {len(sessions)}")
    md.append(f"- Tool fires across these sessions: {n_tools}")
    md.append(f"  - Joined via decision_id: {n_joined_did} "
              f"({n_joined_did/n_tools*100 if n_tools else 0:.0f}%)")
    md.append(f"  - Joined via windowed-fallback: {n_joined_win} "
              f"({n_joined_win/n_tools*100 if n_tools else 0:.0f}%)")
    md.append(f"  - Unjoined: {n_unjoined} "
              f"({n_unjoined/n_tools*100 if n_tools else 0:.0f}%)")
    md.append("")
    md.append("## Per-Session Summary\n")
    for sid in sessions[-10:]:  # last 10 sessions
        srows = [r for r in rows if r.session_id == sid]
        if not srows:
            continue
        n = len(srows)
        tiers = defaultdict(int)
        for r in srows:
            if r.decision_tier:
                tiers[r.decision_tier] += 1
        tier_str = " ".join(f"{t}={tiers[t]}" for t in ("S0", "S1", "S2", "S3", "S4", "S5") if t in tiers)
        md.append(f"- `{sid[:8]}` — {n} tools | tiers: {tier_str or '(none)'}")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# Transcript section (per-receipt $USD)
# -----------------------------------------------------------------------------


def _render_transcript_section(session_id: str) -> str:
    """Render the transcript-derived API-token + $USD section for a receipt.

    Returns markdown lines (newline-joined). Empty section if no transcript
    found — receipts still ship with the join-proof half (Cycle 1 behavior).
    """
    turns = load_session_turns(session_id)
    if not turns:
        return (
            "## Transcript $USD (Cycle 2)\n\n"
            "*No transcript found for this session — only tools.jsonl half of receipt.*\n"
        )

    # Aggregations (msg.id-deduped via transcript_loader)
    total_in = sum(t.input_tokens for t in turns)
    total_cr = sum(t.cache_read for t in turns)
    total_cw5 = sum(t.cache_create_5m for t in turns)
    total_cw1 = sum(t.cache_create_1h for t in turns)
    total_out = sum(t.output_tokens for t in turns)
    total_thinking = sum(t.thinking_chars for t in turns)
    total_cost = sum(cost_from_turn(t) for t in turns)
    n_turns = len(turns)
    models_seen = sorted({t.model for t in turns if t.model})

    # Per-skill aggregation (turn-level — same logic as skill_cost_attribution.py
    # but with msg.id dedupe so it doesn't 2-4x overcount).
    skill_cost: dict[str, float] = defaultdict(float)
    skill_turns: dict[str, int] = defaultdict(int)
    for t in turns:
        c = cost_from_turn(t)
        if t.skills:
            share = c / len(t.skills)
            for sk in t.skills:
                skill_cost[sk] += share
                skill_turns[sk] += 1
        else:
            skill_cost["(no skill)"] += c
            skill_turns["(no skill)"] += 1

    # Cache hit rate (transcript-truth)
    denom_input = total_in + total_cr + total_cw5 + total_cw1
    hit_rate = (total_cr / denom_input) if denom_input else 0.0
    write_5m_share = (total_cw5 / (total_cw5 + total_cw1)) if (total_cw5 + total_cw1) else 0.0

    md = []
    md.append("## Transcript $USD (Cycle 2 — msg.id-deduped)\n")
    md.append(f"- Distinct API messages: **{n_turns}** (de-duplicated by msg.id)")
    md.append(f"- Models seen: {', '.join(models_seen) or '(none)'}")
    md.append(f"- **Total cost: ${total_cost:.4f}**")
    md.append("")
    md.append("### Token totals\n")
    md.append(f"- Input (fresh):       {total_in:>10,} tok")
    md.append(f"- Cache read:          {total_cr:>10,} tok")
    md.append(f"- Cache write 5m:      {total_cw5:>10,} tok")
    md.append(f"- Cache write 1h:      {total_cw1:>10,} tok")
    md.append(f"- Output:              {total_out:>10,} tok")
    md.append(f"- Thinking text size:  {total_thinking:>10,} chars (informational)")
    md.append(f"- **Cache hit rate: {hit_rate*100:.1f}%** "
              f"(read / (fresh+read+writes))")
    md.append(f"- Cache write 5m share: {write_5m_share*100:.1f}% "
              f"(rest is 1h cache, billed 1.6x more)")
    md.append("")
    if skill_cost:
        md.append("### Per-skill $USD attribution\n")
        for sk, c in sorted(skill_cost.items(), key=lambda x: -x[1])[:15]:
            n = skill_turns[sk]
            avg = c / n if n else 0.0
            md.append(f"- `{sk}` — ${c:.4f} ({n} turns, avg ${avg:.4f}/turn)")
        md.append("")
    md.append("### Methodology\n")
    md.append("- Source: `~/.claude/projects/<cwd>/<session_id>.jsonl` "
              "(via `transcript_loader.parse_transcript` — msg.id dedupe).")
    md.append("- Pricing: `automations/brain/routing_manifest.toml` "
              "[models.*] (via `cost_model.price_for`).")
    md.append("- Cache-write split: 5m × 1.25, 1h × 2.00 of input rate "
              "(per PRICING_NOTES.md).")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# Sentinel hygiene (edge-case hardening)
# -----------------------------------------------------------------------------


def sentinel_age_hours(path: Path) -> float:
    """Age of a sentinel file in hours (used to gate stale-cross-session reads)."""
    try:
        mt = path.stat().st_mtime
    except (OSError, ValueError):
        return float("inf")
    return (datetime.now().timestamp() - mt) / 3600.0


def gc_stale_sentinels(*, max_age_hr: float = 24.0,
                      dry_run: bool = False) -> tuple[int, list[Path]]:
    """Remove stale `_active_decision_<sid>.txt` sentinels.

    Default cutoff is 24h — covers overnight sessions. The 1-hr threshold for
    *reads* (SENTINEL_MAX_AGE_HR_DEFAULT) is enforced inside brain_hook_fast.js
    when the hook decides whether to attach a decision_id to a tool fire.
    """
    if not TELEMETRY_DIR.exists():
        return 0, []
    removed = []
    for p in TELEMETRY_DIR.glob(SENTINEL_GLOB):
        if sentinel_age_hours(p) > max_age_hr:
            removed.append(p)
            if not dry_run:
                try:
                    p.unlink()
                except OSError:
                    pass
    return len(removed), removed


# -----------------------------------------------------------------------------
# Cache-thrash + Long-tail dispatch (live algorithms in their own modules)
# -----------------------------------------------------------------------------


def cache_thrash_report(window_days: int = 7,
                       *, write_proposals: bool = True) -> str:
    """Full cache-thrash analysis. Wraps `cache_thrash.run()`.

    Per research brief Gap 2:
      - Per-(skill, model) hit rate over rolling window
      - Bayesian smoothing (alpha=8, beta=2 -> 80% prior, prevents first-fire
        skill from showing 0% hit on a single cache_creation turn)
      - Status thresholds: thrash <0.50, warn <0.60, ok >=0.60
      - Below-min: reads+creates==0 AND fresh>0
      - Insufficient samples: fires<5 (separate flag, not lumped into thrash)
      - Dynamic-prefix divergence: chunk-hash MD5 over 256-tok blocks of
        consecutive turns; first divergent chunk = the cache-invalidator
    """
    from cache_thrash import run as run_cache_thrash
    return run_cache_thrash(window_days=window_days, write_proposals=write_proposals)


def long_tail_report(window_days: int = 30) -> str:
    """Full long-tail spike detection. Wraps `long_tail.run()`.

    Per research brief Gap 3:
      - Per-(skill, model) cost-per-fire over the window
      - n>=30 minimum (binomial-CI floor); below = "insufficient" flag
      - Multi-gate filter: ratio (p95 >= 10*p50)
                          AND absolute (p95 >= $0.50)
                          AND spread (p95 - p50 >= $0.10)
      - Spike-cause auto-diagnosis (4 known + indeterminate)
      - Sorted by spike-cost-contribution = (p95 - p50) * fire_count
    """
    from long_tail import run as run_long_tail
    return run_long_tail(window_days=window_days)


def predicted_vs_actual_report(last_n: int = 100) -> str:
    """Reconciliation: decision.tier prediction vs transcript actual cost.

    Wraps `reconciliation.run()`. Flags decisions whose actual cost > 2x the
    tier-predicted cost (using a conservative 30K context / 90% cache /
    500-output baseline — see `cost_model.tier_predicted_cost_per_call`).
    """
    from reconciliation import run as run_recon
    return run_recon(last_n=last_n)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="token_accountant")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_receipt = sub.add_parser("receipt", help="per-session token receipt")
    p_receipt.add_argument("--session", required=True)
    p_receipt.add_argument("--no-write", action="store_true",
                           help="print to stdout instead of writing to receipts/")

    p_recon = sub.add_parser("reconcile", help="join health + tier distribution")
    p_recon.add_argument("--last", type=int, default=50)

    p_thrash = sub.add_parser("cache-thrash", help="cache hit rate analysis + restructure proposals")
    p_thrash.add_argument("--window", type=int, default=7)
    p_thrash.add_argument("--no-proposals", action="store_true",
                          help="skip writing proposals/cache_restructure_*.jsonl")

    p_lt = sub.add_parser("long-tail", help="long-tail $USD spike detection")
    p_lt.add_argument("--window", type=int, default=30)

    p_pva = sub.add_parser("predicted-vs-actual", help="tier-prediction vs transcript-actual cost")
    p_pva.add_argument("--last", type=int, default=100)

    p_gc = sub.add_parser("sentinel-gc", help="purge stale decision_id sentinel files")
    p_gc.add_argument("--max-age-hr", type=float, default=24.0)
    p_gc.add_argument("--dry-run", action="store_true")

    sub.add_parser("weekly", help="weekly waste report (Cycle 3)")

    args = p.parse_args(argv)

    if args.cmd == "receipt":
        text = build_session_receipt(args.session, write_to_disk=not args.no_write)
        if args.no_write:
            print(text)
    elif args.cmd == "reconcile":
        print(reconcile_last_n(args.last))
    elif args.cmd == "cache-thrash":
        print(cache_thrash_report(args.window, write_proposals=not args.no_proposals))
    elif args.cmd == "long-tail":
        print(long_tail_report(args.window))
    elif args.cmd == "predicted-vs-actual":
        print(predicted_vs_actual_report(args.last))
    elif args.cmd == "sentinel-gc":
        n, removed = gc_stale_sentinels(max_age_hr=args.max_age_hr, dry_run=args.dry_run)
        verb = "would-remove" if args.dry_run else "removed"
        print(f"sentinel-gc: {verb} {n} stale sentinels (>{args.max_age_hr}h)")
        for p in removed:
            print(f"  {verb}: {p.name}")
    elif args.cmd == "weekly":
        print("# Weekly Waste Report — SKELETON (Cycle 3)\n\nNot yet implemented.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
