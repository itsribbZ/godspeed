#!/usr/bin/env python3
"""
Homer L6 — HESPER (sleep-time agent: learning distillation)
===========================================================
Hesper was the Greek personification of the evening star. In Homer, she's
the sleep-time agent who mines all Toke's learning sources and distills
the highest-yield patterns into a best-practices KB.

Absorbs the original Kiln blueprint mission — Kiln was going to be a
standalone distillation engine; Hesper is the same thing as a sleep-time
agent inside Homer.

Sources mined:
- `~/.claude/skills/*/_learnings.md` (all skill learnings)
- `~/.claude/shared/_shared_learnings.md` (promoted cross-skill rules)
- `Toke/research/*.md` (research synthesis documents)
- `Toke/automations/homer/mnemos/archival/*.md` (archived memories)

Output:
- `Toke/automations/homer/sleep/hesper/best_practices/best_practices_YYYY-MM-DD.md`
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HESPER_ROOT = Path(__file__).parent
REPORTS_DIR = HESPER_ROOT / "best_practices"

sys.path.insert(0, str(HESPER_ROOT.parent))
try:
    from _division import (  # type: ignore
        load_division_spec,
        learnings_paths_for_division,
    )
    DIVISION_SUPPORT = True
except ImportError:
    DIVISION_SUPPORT = False
HOMER_ROOT = HESPER_ROOT.parent.parent
TOKE_ROOT = HOMER_ROOT.parent.parent
SKILLS_DIR = Path.home() / ".claude" / "skills"
SHARED_LEARNINGS = Path.home() / ".claude" / "shared" / "_shared_learnings.md"
TOKE_RESEARCH = TOKE_ROOT / "research"
MNEMOS_ARCHIVAL = HOMER_ROOT / "mnemos" / "archival"

# Patterns to extract structured entries
ENTRY_HEADER_PATTERN = re.compile(r"^### (.+?)(?:\s+—\s+(\d{4}-\d{2}-\d{2}))?\s*$")
META_BLOCK_PATTERN = re.compile(r"<!--\s*meta:\s*({.*?})\s*-->", re.DOTALL)
ROI_PATTERN = re.compile(r'"roi_score":\s*(\d+)')
CONFIDENCE_PATTERN = re.compile(r'"confidence":\s*"(HIGH|MEDIUM|LOW)"')
CONFIRMED_PATTERN = re.compile(r'"confirmed_count":\s*(\d+)')
SL_ID_PATTERN = re.compile(r"SL-(\d+)")


@dataclass
class LearningEntry:
    title: str
    date: str
    source_path: str
    skill: str
    roi: int = 0
    confidence: str = "LOW"
    confirmed_count: int = 1
    sl_ids: list[str] = field(default_factory=list)
    body_excerpt: str = ""

    @property
    def score(self) -> int:
        """Composite score: ROI × confidence_rank × confirmed_count."""
        conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(self.confidence, 1)
        return self.roi * conf_rank * max(1, self.confirmed_count)


def parse_learning_file(path: Path, skill_name: str) -> list[LearningEntry]:
    """Parse a _learnings.md file into structured entries."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: list[LearningEntry] = []
    lines = text.splitlines()
    current: LearningEntry | None = None
    body_buffer: list[str] = []

    for line in lines:
        header_match = ENTRY_HEADER_PATTERN.match(line)
        if header_match:
            if current is not None:
                current.body_excerpt = "\n".join(body_buffer[:10])
                entries.append(current)
            body_buffer = []
            title = header_match.group(1).strip()
            date = header_match.group(2) or ""
            current = LearningEntry(
                title=title,
                date=date,
                source_path=str(path),
                skill=skill_name,
            )
            continue

        if current is None:
            continue

        body_buffer.append(line)

        meta_match = META_BLOCK_PATTERN.search(line)
        if meta_match:
            meta_str = meta_match.group(1)
            if roi_m := ROI_PATTERN.search(meta_str):
                current.roi = int(roi_m.group(1))
            if conf_m := CONFIDENCE_PATTERN.search(meta_str):
                current.confidence = conf_m.group(1)
            if cc_m := CONFIRMED_PATTERN.search(meta_str):
                current.confirmed_count = int(cc_m.group(1))

        for sl_m in SL_ID_PATTERN.finditer(line):
            sl_id = f"SL-{sl_m.group(1)}"
            if sl_id not in current.sl_ids:
                current.sl_ids.append(sl_id)

    if current is not None:
        current.body_excerpt = "\n".join(body_buffer[:10])
        entries.append(current)

    return entries


def mine_all_sources(
    skills_dir: Path | None = None,
    division: str | None = None,
) -> list[LearningEntry]:
    """
    Read every learning source Hesper knows about.

    If division is provided, restricts the per-skill scan to skills in
    division.all_skills (primary + support), and skips _shared / research /
    archival sources (those are ecosystem-wide, not division-tagged). This
    keeps division output focused on its own skills' learnings.
    """
    skills_dir = skills_dir if skills_dir is not None else SKILLS_DIR
    all_entries: list[LearningEntry] = []

    if division is not None:
        if not DIVISION_SUPPORT:
            raise RuntimeError(
                "division filter requested but _division.py not importable — check sleep/ layout"
            )
        spec = load_division_spec(division)
        for learnings_path in learnings_paths_for_division(spec, skills_dir=skills_dir):
            all_entries.extend(parse_learning_file(learnings_path, learnings_path.parent.name))
        return all_entries

    # Per-skill learnings (ecosystem-wide)
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            learnings = skill_dir / "_learnings.md"
            if learnings.exists():
                all_entries.extend(parse_learning_file(learnings, skill_dir.name))

    # Shared learnings
    if SHARED_LEARNINGS.exists():
        all_entries.extend(parse_learning_file(SHARED_LEARNINGS, "_shared"))

    # Toke research docs (source 3 of 4)
    if TOKE_RESEARCH.exists():
        for md_file in sorted(TOKE_RESEARCH.glob("*.md")):
            all_entries.extend(parse_learning_file(md_file, f"research/{md_file.stem}"))

    # Mnemos archival entries (source 4 of 4)
    if MNEMOS_ARCHIVAL.exists():
        for md_file in sorted(MNEMOS_ARCHIVAL.glob("archival_*.md")):
            all_entries.extend(parse_learning_file(md_file, f"mnemos_archival/{md_file.stem}"))

    return all_entries


def distill(entries: list[LearningEntry], top_n: int = 20) -> list[LearningEntry]:
    """Rank entries by composite score, return top N."""
    return sorted(entries, key=lambda e: e.score, reverse=True)[:top_n]


def write_best_practices(
    top_entries: list[LearningEntry],
    total_mined: int,
    reports_dir: Path | None = None,
    division: str | None = None,
) -> Path:
    reports_dir = reports_dir if reports_dir is not None else REPORTS_DIR
    if division is not None:
        reports_dir = reports_dir / division
    reports_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    fname = f"best_practices_{division}_{date}.md" if division else f"best_practices_{date}.md"
    path = reports_dir / fname

    title = f"# Hesper Best Practices — {division or 'ecosystem'} — {date}"
    lines = [
        title,
        "",
        f"**Division:** {division or '(ecosystem-wide)'}",
        f"**Sources mined:** {total_mined} learning entries",
        f"**Top-N distilled:** {len(top_entries)}",
        "",
        "Composite score formula: `roi × confidence_rank × max(1, confirmed_count)`",
        "",
        "## Top-Yield Patterns",
        "",
    ]

    for idx, e in enumerate(top_entries, 1):
        lines.append(f"### {idx}. {e.title}")
        lines.append(f"- **Skill:** `{e.skill}`")
        lines.append(f"- **Date:** {e.date or '(undated)'}")
        lines.append(f"- **Score:** {e.score} (ROI={e.roi}, conf={e.confidence}, confirmed={e.confirmed_count})")
        if e.sl_ids:
            lines.append(f"- **SL-IDs referenced:** {', '.join(e.sl_ids)}")
        lines.append(f"- **Source:** `{e.source_path}`")
        if e.body_excerpt.strip():
            excerpt = e.body_excerpt.strip()[:400]
            lines.append(f"- **Excerpt:** {excerpt}")
        lines.append("")

    lines.extend([
        "",
        "## How to use this report",
        "",
        "- These are the highest-yield patterns from Toke's learning pipeline.",
        "- Each entry is citation-backed via its source file:line.",
        "- Feed the top-5 into Zeus's Phase 1 plan prompts as injected context.",
        "- Promote repeat-confirmed patterns to Mnemos Core (manual action — not automated).",
        "- Track score changes over time to see which patterns are compounding.",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_distillation(
    skills_dir: Path | None = None,
    top_n: int = 20,
    reports_dir: Path | None = None,
    division: str | None = None,
) -> dict:
    entries = mine_all_sources(skills_dir=skills_dir, division=division)
    top = distill(entries, top_n=top_n)
    report_path = write_best_practices(
        top, total_mined=len(entries), reports_dir=reports_dir, division=division,
    )
    return {
        "ok": True,
        "timestamp": datetime.datetime.now().isoformat(),
        "division": division,
        "sources_mined": len(entries),
        "top_n": len(top),
        "report_path": str(report_path),
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="hesper",
        description="Homer L6 Hesper — sleep-time learning distillation. Synthesizes from receipts only.",
    )
    parser.add_argument("--top-n", type=int, default=20, help="Number of patterns to distill (default 20)")
    parser.add_argument(
        "--division", default=None,
        help="Filter to skills in this Director division (uses _learnings.md from primary+support skills only)",
    )
    parser.add_argument("top_n_legacy", nargs="?", help="(Legacy positional top_n; use --top-n)")
    args = parser.parse_args(argv[1:])
    top_n = args.top_n
    if args.top_n_legacy and args.top_n_legacy.isdigit():
        top_n = int(args.top_n_legacy)

    result = run_distillation(top_n=top_n, division=args.division)
    print(f"Hesper distillation complete.")
    if result.get("division"):
        print(f"  Division:        {result['division']}")
    print(f"  Sources mined:   {result['sources_mined']}")
    print(f"  Top-N distilled: {result['top_n']}")
    print(f"  Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
