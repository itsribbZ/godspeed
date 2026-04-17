#!/usr/bin/env python3
"""
Toke — Memory ROI Trending Tool
================================
Scans all ~/.claude/projects/*/memory/ directories.
For each memory file: measures size (token cost), extracts metadata,
computes staleness, estimates retrieval value, and outputs ROI scores.

Identifies GC candidates (low ROI + stale) and high-value memories.

CLI:
    python memory_roi_trend.py                    # full scan, table output
    python memory_roi_trend.py --project toke     # filter to project substring
    python memory_roi_trend.py --gc               # show GC candidates only
    python memory_roi_trend.py --json             # JSON output for piping
    python memory_roi_trend.py --summary          # one-line-per-project summary

Origin: Built 2026-04-12 for Toke Frontier #4 (Memory file ROI trending).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

MEMORY_ROOT = Path.home() / ".claude" / "projects"
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MemoryFileInfo:
    project: str
    filename: str
    path: str
    size_bytes: int
    est_tokens: int
    mem_type: str         # user, feedback, project, reference, index, unknown
    name: str
    description: str
    days_stale: float     # days since last modified
    in_index: bool        # referenced in MEMORY.md
    roi_score: float      # 0.0 (GC candidate) to 1.0 (high value)
    roi_reason: str       # human-readable explanation


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML-like frontmatter from markdown files."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    result = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


# ---------------------------------------------------------------------------
# Index parser
# ---------------------------------------------------------------------------


def parse_memory_index(index_path: Path) -> set[str]:
    """Extract filenames referenced in MEMORY.md index."""
    if not index_path.exists():
        return set()
    text = index_path.read_text(encoding="utf-8", errors="replace")
    # Match markdown links like [Title](filename.md)
    refs = set()
    for m in re.finditer(r"\[.*?\]\(([^)]+\.md)\)", text):
        refs.add(m.group(1).strip())
    # Also match bare filenames
    for m in re.finditer(r"\b([\w_-]+\.md)\b", text):
        refs.add(m.group(1).strip())
    refs.discard("MEMORY.md")
    return refs


# ---------------------------------------------------------------------------
# ROI scoring
# ---------------------------------------------------------------------------


# Type multipliers: higher = more likely to be valuable over time
TYPE_VALUE = {
    "feedback": 1.0,     # behavioral rules persist longest
    "user": 0.9,         # user profile is highly stable
    "reference": 0.7,    # external pointers, moderately stable
    "project": 0.5,      # project state decays fast
    "index": 0.3,        # MEMORY.md index itself, low standalone value
    "unknown": 0.3,      # no frontmatter = probably less maintained
}

# Staleness decay: ROI drops as days since last modification increase
STALE_HALF_LIFE_DAYS = 60.0  # ROI halves every 60 days of no updates


def compute_roi(
    mem_type: str,
    size_bytes: int,
    days_stale: float,
    in_index: bool,
) -> tuple[float, str]:
    """Compute ROI score (0-1) and reason string.

    ROI = type_value * freshness * index_bonus * size_efficiency
    """
    reasons = []

    # Type value
    type_val = TYPE_VALUE.get(mem_type, 0.3)
    reasons.append(f"type={mem_type}({type_val:.1f})")

    # Freshness: exponential decay
    freshness = 2.0 ** (-days_stale / STALE_HALF_LIFE_DAYS)
    if days_stale > 90:
        reasons.append(f"stale({int(days_stale)}d)")
    elif days_stale < 7:
        reasons.append("fresh")

    # Index bonus: if not in MEMORY.md, it might be orphaned
    index_mult = 1.0 if in_index else 0.6
    if not in_index:
        reasons.append("orphaned")

    # Size efficiency: smaller files have better ROI (less context cost)
    # Diminishing penalty: 1.0 for <1KB, down to 0.5 for 10KB+
    est_tokens = max(size_bytes // 4, 1)
    if est_tokens > 5000:
        size_eff = 0.5
        reasons.append(f"large({est_tokens}tok)")
    elif est_tokens > 2000:
        size_eff = 0.7
    else:
        size_eff = 1.0

    score = type_val * freshness * index_mult * size_eff
    score = round(min(max(score, 0.0), 1.0), 3)

    return score, " | ".join(reasons)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_project(project_dir: Path) -> list[MemoryFileInfo]:
    """Scan a single project's memory directory."""
    memory_dir = project_dir / "memory"
    if not memory_dir.is_dir():
        return []

    project_name = project_dir.name
    # Simplify project name: remove C--Users-user- prefix
    short_name = re.sub(r"^C--Users-\w+-", "", project_name)
    if not short_name:
        short_name = "home"

    # Parse index
    index_refs = parse_memory_index(memory_dir / "MEMORY.md")

    results = []
    for md_file in sorted(memory_dir.glob("*.md")):
        filename = md_file.name
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        size_bytes = len(text.encode("utf-8", errors="replace"))
        est_tokens = max(size_bytes // 4, 1)

        # Parse frontmatter
        fm = parse_frontmatter(text)
        mem_type = fm.get("type", "index" if filename == "MEMORY.md" else "unknown")
        name = fm.get("name", filename)
        description = fm.get("description", "")

        # Staleness
        try:
            mtime = md_file.stat().st_mtime
            last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
            days_stale = (NOW - last_modified).total_seconds() / 86400
        except OSError:
            days_stale = 999.0

        # Index membership
        in_index = filename in index_refs or filename == "MEMORY.md"

        # ROI
        roi_score, roi_reason = compute_roi(mem_type, size_bytes, days_stale, in_index)

        results.append(MemoryFileInfo(
            project=short_name,
            filename=filename,
            path=str(md_file),
            size_bytes=size_bytes,
            est_tokens=est_tokens,
            mem_type=mem_type,
            name=name,
            description=description[:80],
            days_stale=round(days_stale, 1),
            in_index=in_index,
            roi_score=roi_score,
            roi_reason=roi_reason,
        ))

    return results


def scan_all(project_filter: str | None = None) -> list[MemoryFileInfo]:
    """Scan all project memory directories."""
    all_results = []
    for project_dir in sorted(MEMORY_ROOT.iterdir()):
        if not project_dir.is_dir():
            continue
        if project_filter and project_filter.lower() not in project_dir.name.lower():
            continue
        all_results.extend(scan_project(project_dir))
    return all_results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def print_table(results: list[MemoryFileInfo], gc_only: bool = False) -> None:
    """Print human-readable table sorted by ROI."""
    if gc_only:
        results = [r for r in results if r.roi_score < 0.25]

    results.sort(key=lambda r: r.roi_score)

    # Summary stats
    total_files = len(results)
    total_bytes = sum(r.size_bytes for r in results)
    total_tokens = sum(r.est_tokens for r in results)
    gc_count = sum(1 for r in results if r.roi_score < 0.25)
    high_count = sum(1 for r in results if r.roi_score >= 0.7)

    print(f"{'=' * 100}")
    print(f"  MEMORY ROI TRENDING — {NOW.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 100}")
    print(f"  Files: {total_files} | Bytes: {total_bytes:,} | Est tokens: {total_tokens:,}")
    print(f"  High ROI (>=0.7): {high_count} | GC candidates (<0.25): {gc_count}")
    print(f"{'=' * 100}")
    print()

    # Table header
    hdr = f"{'ROI':>5}  {'Type':<10}  {'Stale':>6}  {'Tok':>6}  {'Idx':>3}  {'Project':<25}  {'File':<35}  {'Reason'}"
    print(hdr)
    print("-" * len(hdr))

    for r in results:
        idx_mark = "Y" if r.in_index else "N"
        stale_str = f"{int(r.days_stale)}d"
        print(
            f"{r.roi_score:5.3f}  {r.mem_type:<10}  {stale_str:>6}  {r.est_tokens:>6}  {idx_mark:>3}  "
            f"{r.project:<25}  {r.filename:<35}  {r.roi_reason}"
        )

    # GC summary
    if not gc_only:
        print()
        gc_items = [r for r in results if r.roi_score < 0.25]
        if gc_items:
            gc_bytes = sum(r.size_bytes for r in gc_items)
            gc_tokens = sum(r.est_tokens for r in gc_items)
            print(f"  GC CANDIDATES ({len(gc_items)} files, {gc_bytes:,} bytes, ~{gc_tokens:,} tokens):")
            for r in gc_items:
                print(f"    {r.roi_score:.3f}  {r.project}/{r.filename} — {r.roi_reason}")


def print_summary(results: list[MemoryFileInfo]) -> None:
    """One-line-per-project summary."""
    by_project: dict[str, list[MemoryFileInfo]] = {}
    for r in results:
        by_project.setdefault(r.project, []).append(r)

    print(f"{'Project':<30}  {'Files':>5}  {'Bytes':>8}  {'Tokens':>8}  {'AvgROI':>7}  {'GC':>3}")
    print("-" * 80)
    for proj in sorted(by_project, key=lambda p: sum(r.size_bytes for r in by_project[p]), reverse=True):
        items = by_project[proj]
        total_bytes = sum(r.size_bytes for r in items)
        total_tokens = sum(r.est_tokens for r in items)
        avg_roi = sum(r.roi_score for r in items) / len(items) if items else 0
        gc_count = sum(1 for r in items if r.roi_score < 0.25)
        print(f"{proj:<30}  {len(items):>5}  {total_bytes:>8,}  {total_tokens:>8,}  {avg_roi:>7.3f}  {gc_count:>3}")


def print_json(results: list[MemoryFileInfo]) -> None:
    """JSON output for piping."""
    print(json.dumps([asdict(r) for r in results], indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Toke Memory ROI Trending")
    parser.add_argument("--project", "-p", help="Filter to project name substring")
    parser.add_argument("--gc", action="store_true", help="Show GC candidates only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--summary", "-s", action="store_true", help="Project summary")
    args = parser.parse_args()

    results = scan_all(args.project)

    if not results:
        print("No memory files found.")
        return 0

    if args.json:
        print_json(results)
    elif args.summary:
        print_summary(results)
    elif args.gc:
        print_table(results, gc_only=True)
    else:
        print_table(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
