#!/usr/bin/env python3
"""
Homer L6 — NYX (sleep-time agent: theater auditor)
==================================================
Nyx was the primordial goddess of the night in Greek myth. In Homer, she runs
while the user sleeps — auditing all `~/.claude/skills/*/SKILL.md` files for
theater (dead infrastructure with no fire evidence). Produces a dated report.

Pattern: reuses Oracle's `detect_theater()` against every skill SKILL.md.
Automates yesterday's manual godspeed audit on the whole skill ecosystem.

Run cadence: on-demand (`sleep run nyx`) or nightly via cron.
Output: `Toke/automations/homer/sleep/nyx/reports/nyx_audit_YYYY-MM-DD.md`
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

NYX_ROOT = Path(__file__).parent
REPORTS_DIR = NYX_ROOT / "reports"
HOMER_ROOT = NYX_ROOT.parent.parent
ORACLE_PATH = (HOMER_ROOT / "oracle").resolve()
SKILLS_DIR = Path.home() / ".claude" / "skills"

sys.path.insert(0, str(ORACLE_PATH))
from oracle import Oracle  # noqa: E402

sys.path.insert(0, str(NYX_ROOT.parent))
try:
    from _division import (  # type: ignore
        load_division_spec,
        skill_md_paths_for_division,
    )
    DIVISION_SUPPORT = True
except ImportError:
    DIVISION_SUPPORT = False


@dataclass
class SkillAuditEntry:
    skill_name: str
    skill_md_path: str
    size_bytes: int
    line_count: int
    theater_ratio: float
    theater_recommendation: str
    suspect_sections: list[dict] = field(default_factory=list)
    learnings_entry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "skill_md_path": self.skill_md_path,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
            "theater_ratio": self.theater_ratio,
            "theater_recommendation": self.theater_recommendation,
            "suspect_sections": self.suspect_sections,
            "learnings_entry_count": self.learnings_entry_count,
        }


def count_learnings(skill_dir: Path) -> int:
    learnings = skill_dir / "_learnings.md"
    if not learnings.exists():
        return 0
    try:
        text = learnings.read_text(encoding="utf-8", errors="replace")
        return sum(1 for line in text.splitlines() if line.startswith("### "))
    except OSError:
        return 0


def audit_single_skill(skill_md: Path, oracle: Oracle) -> SkillAuditEntry:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    theater = oracle.detect_theater(text)
    return SkillAuditEntry(
        skill_name=skill_md.parent.name,
        skill_md_path=str(skill_md),
        size_bytes=len(text.encode("utf-8")),
        line_count=len(text.splitlines()),
        theater_ratio=theater.theater_ratio,
        theater_recommendation=theater.recommendation,
        suspect_sections=theater.suspect_sections,
        learnings_entry_count=count_learnings(skill_md.parent),
    )


def run_audit(
    skills_dir: Path | None = None,
    oracle: Oracle | None = None,
    division: str | None = None,
) -> dict:
    """
    Audit every SKILL.md in ~/.claude/skills/. Returns structured report.

    If division is provided, restricts audit to SKILL.md files for skills in
    division.all_skills (primary + support). Same theater detection logic;
    smaller corpus.
    """
    skills_dir = skills_dir if skills_dir is not None else SKILLS_DIR
    oracle = oracle or Oracle()

    entries: list[SkillAuditEntry] = []
    if not skills_dir.exists():
        return {"ok": False, "reason": f"skills dir not found: {skills_dir}", "entries": []}

    if division is not None:
        if not DIVISION_SUPPORT:
            return {"ok": False, "reason": "division filter requested but _division.py not importable", "entries": []}
        spec = load_division_spec(division)
        skill_mds = skill_md_paths_for_division(spec, skills_dir=skills_dir)
        for skill_md in skill_mds:
            try:
                entries.append(audit_single_skill(skill_md, oracle))
            except OSError:
                continue
    else:
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                entries.append(audit_single_skill(skill_md, oracle))
            except OSError:
                continue

    # Summarize
    total_bytes = sum(e.size_bytes for e in entries)
    prune_candidates = [e for e in entries if e.theater_recommendation == "PRUNE"]
    investigate_candidates = [e for e in entries if e.theater_recommendation == "INVESTIGATE"]

    return {
        "ok": True,
        "timestamp": datetime.datetime.now().isoformat(),
        "division": division,
        "skills_audited": len(entries),
        "total_bytes": total_bytes,
        "prune_candidates_count": len(prune_candidates),
        "investigate_candidates_count": len(investigate_candidates),
        "entries": [e.to_dict() for e in entries],
        "prune_candidates": [e.to_dict() for e in prune_candidates],
        "investigate_candidates": [e.to_dict() for e in investigate_candidates],
    }


def write_report(report: dict, reports_dir: Path | None = None) -> Path:
    """Write a human-readable markdown report."""
    reports_dir = reports_dir if reports_dir is not None else REPORTS_DIR
    division = report.get("division")
    if division is not None:
        reports_dir = reports_dir / division
    reports_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    fname = f"nyx_audit_{division}_{date}.md" if division else f"nyx_audit_{date}.md"
    path = reports_dir / fname

    title = f"# Nyx Theater Audit — {division or 'ecosystem'} — {date}"
    lines = [
        title,
        "",
        f"**Skills audited:** {report['skills_audited']}",
        f"**Total bytes:** {report['total_bytes']:,}",
        f"**PRUNE candidates:** {report['prune_candidates_count']}",
        f"**INVESTIGATE candidates:** {report['investigate_candidates_count']}",
        "",
        "## Top theater ratios (worst first)",
        "",
        "| Skill | Lines | Bytes | Theater Ratio | Verdict | Learnings Entries |",
        "|---|---|---|---|---|---|",
    ]

    sorted_entries = sorted(report["entries"], key=lambda e: e["theater_ratio"], reverse=True)
    for e in sorted_entries[:25]:
        lines.append(
            f"| {e['skill_name']} | {e['line_count']} | {e['size_bytes']:,} | "
            f"{e['theater_ratio']:.3f} | {e['theater_recommendation']} | "
            f"{e['learnings_entry_count']} |"
        )

    if report["prune_candidates_count"] > 0:
        lines.extend(["", "## PRUNE candidates (deletion proposals — requires the user greenlight)", ""])
        for e in report["prune_candidates"]:
            lines.append(f"### {e['skill_name']}")
            lines.append(f"- Path: `{e['skill_md_path']}`")
            lines.append(f"- Ratio: {e['theater_ratio']:.3f}")
            lines.append(f"- Suspect sections: {len(e['suspect_sections'])}")
            for s in e["suspect_sections"][:5]:
                lines.append(f"  - `{s['header']}` (lines {s['line_start']}-{s['line_end']}, flags: {','.join(s['flags'])})")
            lines.append("")

    lines.extend([
        "",
        "## Next steps",
        "",
        "- Review PRUNE candidates and confirm per-item via Sacred Rule #2 deletion proposal protocol.",
        "- INVESTIGATE candidates may have legitimate reasons for version tags / NEW labels — manual review.",
        "- Run Nyx nightly via cron to track theater drift over time.",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="nyx",
        description="Homer L6 Nyx — sleep-time SKILL.md theater auditor. PROPOSE-only deletion candidates.",
    )
    parser.add_argument(
        "--division", default=None,
        help="Filter to SKILL.md files for skills in this Director division (primary+support).",
    )
    args = parser.parse_args(argv[1:])

    report = run_audit(division=args.division)
    if not report["ok"]:
        print(f"Nyx audit failed: {report.get('reason')}", file=sys.stderr)
        return 1

    path = write_report(report)
    print(f"Nyx audit complete.")
    if report.get("division"):
        print(f"  Division: {report['division']}")
    print(f"  Skills audited: {report['skills_audited']}")
    print(f"  PRUNE candidates: {report['prune_candidates_count']}")
    print(f"  INVESTIGATE candidates: {report['investigate_candidates_count']}")
    print(f"  Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
