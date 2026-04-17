#!/usr/bin/env python3
"""
Toke — cold_boot_measure.py

Measures the per-component boot tax by comparing turn-1 cache_write across
sessions with different configurations. Run each variant in a separate
Claude Code session, then feed the transcripts here for comparison.

Usage:
  # After running sessions with different configs:
  python cold_boot_measure.py baseline <path_to_baseline_transcript.jsonl>
  python cold_boot_measure.py compare <baseline.jsonl> <variant.jsonl> [--label LABEL]
  python cold_boot_measure.py catalog                    # scan all projects, show turn-1 data

The "catalog" mode is the zero-effort entry: it reads every transcript in
~/.claude/projects/ and reports the turn-1 cache_write for each, sorted by
size. This gives a natural experiment — sessions in different projects with
different skill/MCP configs will show different boot taxes.

Boot tax components (what we're trying to isolate):
  - Core system preamble:  ~3,500 tok (fixed)
  - CLAUDE.md chain:       ~200-600 tok (varies by project)
  - Tool schemas:          ~3,500 tok (fixed)
  - Skill frontmatter:     ~5,000-7,000 tok (varies by skill count)
  - MCP tool names:        ~400 tok (varies by enabled plugins)
  - MCP tool descriptions: ~3,000-5,000 tok (varies)
  - Memory index:          ~80 tok (varies)
  - Environment block:     ~200 tok (fixed)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))


def get_turn1_data(path: Path) -> dict | None:
    """Extract turn-1 usage from a transcript."""
    if not path.exists():
        return None
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
            cc = usage.get("cache_creation", {})
            return {
                "session_id": path.stem,
                "path": str(path),
                "cwd": d.get("cwd", "?"),
                "input_tokens": usage.get("input_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "cache_write_1h": cc.get("ephemeral_1h_input_tokens", 0),
                "cache_write_5m": cc.get("ephemeral_5m_input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "effective_ctx": (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + cc.get("ephemeral_1h_input_tokens", 0)
                    + cc.get("ephemeral_5m_input_tokens", 0)
                ),
                "model": msg.get("model", "?"),
                "timestamp": d.get("timestamp", "?")[:19],
            }
    return None


def cmd_catalog() -> int:
    """Scan all projects, show turn-1 boot tax for every session."""
    entries = []
    for proj_dir in sorted(CLAUDE_PROJECTS.iterdir()):
        if not proj_dir.is_dir():
            continue
        for transcript in sorted(proj_dir.glob("*.jsonl")):
            data = get_turn1_data(transcript)
            if data:
                # Extract project name from dir
                proj_name = proj_dir.name.replace("C--Users-user-", "").replace("-", "/")[:30]
                data["project"] = proj_name
                entries.append(data)

    if not entries:
        print("No transcripts found.", file=sys.stderr)
        return 1

    entries.sort(key=lambda e: e["cache_write_1h"] + e["cache_write_5m"], reverse=True)

    W = 105
    print("=" * W)
    print("  COLD BOOT CATALOG — turn-1 cache_write across all sessions")
    print("=" * W)
    print(f"  Sessions scanned: {len(entries)}")
    print(f"  Largest boot:     {entries[0]['cache_write_1h']:>8,} tok  ({entries[0]['project']})")
    print(f"  Smallest boot:    {entries[-1]['cache_write_1h']:>8,} tok  ({entries[-1]['project']})")
    median = entries[len(entries) // 2]["cache_write_1h"]
    mean = sum(e["cache_write_1h"] for e in entries) // len(entries)
    print(f"  Median boot:      {median:>8,} tok")
    print(f"  Mean boot:        {mean:>8,} tok")
    print()
    print(f"  {'#':>3}  {'session':>10}  {'cache_w1h':>10}  {'cache_r':>8}  {'eff_ctx':>10}  {'project':<32}  date")
    print("  " + "-" * (W - 4))
    for i, e in enumerate(entries, 1):
        print(f"  {i:>3}  {e['session_id'][:10]}  {e['cache_write_1h']:>10,}  {e['cache_read']:>8,}  {e['effective_ctx']:>10,}  {e['project']:<32}  {e['timestamp']}")
    print("=" * W)

    # Component estimate
    print()
    print("  COMPONENT ESTIMATE (from spread between largest and smallest)")
    print("  " + "-" * 60)
    spread = entries[0]["cache_write_1h"] - entries[-1]["cache_write_1h"]
    print(f"  Boot tax range:  {entries[-1]['cache_write_1h']:,} — {entries[0]['cache_write_1h']:,} tok")
    print(f"  Spread:          {spread:,} tok")
    print(f"  Fixed minimum:   ~{entries[-1]['cache_write_1h']:,} tok (core + tools + env)")
    print(f"  Variable:        ~{spread:,} tok (CLAUDE.md + skills + MCP + memory)")
    print()
    print("  To isolate individual components, run sessions with:")
    print("    1. Empty dir, no CLAUDE.md, no plugins  → core baseline")
    print("    2. Same + CLAUDE.md added               → CLAUDE.md cost")
    print("    3. Same + skills enabled                → skills cost")
    print("    4. Same + MCP plugins enabled           → MCP cost")
    print("  Then feed pairs to: cold_boot_measure.py compare <A> <B>")
    return 0


def cmd_compare(baseline_path: str, variant_path: str, label: str = "variant") -> int:
    """Compare turn-1 data between two sessions."""
    base = get_turn1_data(Path(baseline_path))
    var = get_turn1_data(Path(variant_path))
    if not base or not var:
        print("Could not read turn-1 from one or both transcripts.", file=sys.stderr)
        return 1

    print("=" * 70)
    print(f"  BOOT TAX COMPARISON: baseline vs {label}")
    print("=" * 70)
    for key in ["cache_write_1h", "cache_read", "effective_ctx", "input_tokens", "output_tokens"]:
        bv = base[key]
        vv = var[key]
        delta = vv - bv
        sign = "+" if delta >= 0 else ""
        print(f"  {key:<20}  {bv:>10,}  {vv:>10,}  {sign}{delta:>10,}")
    print("=" * 70)
    print(f"  Baseline: {base['session_id'][:10]} ({base['timestamp']})")
    print(f"  {label}:    {var['session_id'][:10]} ({var['timestamp']})")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = sys.argv[1]
    if cmd == "catalog":
        return cmd_catalog()
    elif cmd == "compare":
        if len(sys.argv) < 4:
            print("Usage: cold_boot_measure.py compare <baseline.jsonl> <variant.jsonl> [--label LABEL]")
            return 1
        label = "variant"
        if "--label" in sys.argv:
            idx = sys.argv.index("--label")
            if idx + 1 < len(sys.argv):
                label = sys.argv[idx + 1]
        return cmd_compare(sys.argv[2], sys.argv[3], label)
    elif cmd == "baseline":
        data = get_turn1_data(Path(sys.argv[2])) if len(sys.argv) > 2 else None
        if not data:
            print("Usage: cold_boot_measure.py baseline <transcript.jsonl>")
            return 1
        print(json.dumps(data, indent=2))
        return 0
    else:
        print(f"Unknown command: {cmd}. Use catalog, compare, or baseline.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
