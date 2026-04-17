#!/usr/bin/env python3
"""
Toke — skill_cost_measure.py

Measures the token cost of every skill in ~/.claude/skills/.
Informs the preload-vs-on-demand decision: which skills are cheap enough to
always load vs which ones should gate behind explicit invocation.

For each SKILL.md the script reports:
  - Total file size (bytes + estimated tokens)
  - Frontmatter size (YAML block between --- markers, if present)
  - Body size (everything after frontmatter)
  - Section count (## headers)
  - model: pin from YAML frontmatter
  - Brain tier (from routing_manifest.toml [skills] if listed)
  - Dollar cost at Opus cache-write ($0.50/MTok) and cache-read ($0.50/MTok) rates

Token estimation: len(text) // 4  (same ratio used across the Toke measurement suite)

Data sources:
  ~/.claude/skills/*/SKILL.md
  Toke/automations/brain/routing_manifest.toml   (tier assignments + pricing)

Modes:
  (default)       Full table — all skills sorted by total tokens desc
  --top N         Limit to top N largest
  --bottom N      Bottom N smallest (complements --top for range view)
  --json          Machine-readable output
  --sort {tokens,name,tier}   Sort order (default: tokens desc)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 guard

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TOKE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = TOKE_ROOT / "automations" / "brain" / "routing_manifest.toml"
SKILLS_DIR = Path(os.path.expanduser("~/.claude/skills"))

# Token estimation: ~4 chars per token (same as brain_classifier.py + this suite)
CHARS_PER_TOKEN = 4

# Pricing at Opus rates ($/MTok) — used for cost estimates.
# Using Opus because skills loaded into context are equivalent to cache-write/read
# cost, and the worst-case model is Opus. Verified 2026-04-11.
OPUS_CACHE_WRITE_PER_MTOK = 0.50   # ephemeral cache write (1.25x of $0.40 input = ~$0.50)
OPUS_CACHE_READ_PER_MTOK  = 0.50   # cache read rate


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
def load_manifest() -> tuple[dict[str, str], dict]:
    """Returns (skill_tier_map, full_manifest_dict)."""
    if not MANIFEST.exists():
        return {}, {}
    with open(MANIFEST, "rb") as f:
        m = tomllib.load(f)
    return m.get("skills", {}), m


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[str, str]:
    """Split SKILL.md text into (frontmatter_text, body_text).

    Handles two layouts:
      1. YAML block:   text starts with '---\\n', ends at second '---\\n'
      2. No YAML:      frontmatter is empty string, body is full text
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return "", ""

    # YAML frontmatter: must start on line 0 with exactly '---'
    if lines[0].rstrip("\r\n") == "---":
        # scan for closing '---'
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r\n") == "---":
                fm_lines = lines[: i + 1]   # includes both '---' delimiters
                body_lines = lines[i + 1:]
                return "".join(fm_lines), "".join(body_lines)
        # Unclosed frontmatter — treat entire file as body (defensive)
        return "", text

    # No YAML frontmatter
    return "", text


def extract_model_pin(frontmatter: str) -> str | None:
    """Extract 'model: <value>' from YAML frontmatter text."""
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("model:"):
            value = stripped[len("model:"):].strip().strip('"').strip("'")
            return value if value else None
    return None


def count_sections(text: str) -> int:
    """Count '## ' header lines in the body (level-2 sections)."""
    return sum(1 for ln in text.splitlines() if ln.startswith("## "))


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def cost_usd(tok: int, rate_per_mtok: float) -> float:
    return (tok / 1_000_000) * rate_per_mtok


# ---------------------------------------------------------------------------
# Per-skill data
# ---------------------------------------------------------------------------
@dataclass
class SkillCost:
    name: str
    skill_md_path: str
    total_bytes: int
    total_tokens: int
    fm_bytes: int
    fm_tokens: int
    body_bytes: int
    body_tokens: int
    sections: int
    model_pin: str | None       # from YAML frontmatter
    brain_tier: str | None      # from manifest [skills]
    cache_write_usd: float      # total_tokens at Opus write rate
    cache_read_usd: float       # total_tokens at Opus read rate

    @property
    def fm_pct(self) -> float:
        return 100.0 * self.fm_tokens / self.total_tokens if self.total_tokens else 0.0


def measure_skill(skill_dir: Path, skill_tier_map: dict[str, str]) -> SkillCost | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    raw = skill_md.read_text(encoding="utf-8", errors="replace")
    fm_text, body_text = parse_frontmatter(raw)

    name = skill_dir.name
    total_b = len(raw.encode("utf-8"))
    fm_b    = len(fm_text.encode("utf-8"))
    body_b  = len(body_text.encode("utf-8"))

    total_t = tokens(raw)
    fm_t    = tokens(fm_text)
    body_t  = tokens(body_text)

    return SkillCost(
        name=name,
        skill_md_path=str(skill_md),
        total_bytes=total_b,
        total_tokens=total_t,
        fm_bytes=fm_b,
        fm_tokens=fm_t,
        body_bytes=body_b,
        body_tokens=body_t,
        sections=count_sections(body_text),
        model_pin=extract_model_pin(fm_text),
        brain_tier=skill_tier_map.get(name),
        cache_write_usd=cost_usd(total_t, OPUS_CACHE_WRITE_PER_MTOK),
        cache_read_usd=cost_usd(total_t, OPUS_CACHE_READ_PER_MTOK),
    )


def scan_all_skills(skill_tier_map: dict[str, str]) -> list[SkillCost]:
    if not SKILLS_DIR.exists():
        return []
    results = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        sc = measure_skill(d, skill_tier_map)
        if sc is not None:
            results.append(sc)
    return results


# ---------------------------------------------------------------------------
# Distribution histogram
# ---------------------------------------------------------------------------
BUCKETS: list[tuple[str, int, int]] = [
    ("<2K",   0,      2_000),
    ("2-5K",  2_000,  5_000),
    ("5-10K", 5_000, 10_000),
    ("10-20K",10_000, 20_000),
    ("20K+",  20_000, 999_999_999),
]


def histogram(skills: list[SkillCost]) -> dict[str, int]:
    counts: dict[str, int] = {label: 0 for label, _, _ in BUCKETS}
    for s in skills:
        for label, lo, hi in BUCKETS:
            if lo <= s.total_tokens < hi:
                counts[label] += 1
                break
    return counts


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_bytes(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f}MB"
    if n >= 1_024:
        return f"{n / 1_024:.1f}KB"
    return f"{n}B"


def fmt_cost(usd: float) -> str:
    if usd < 0.001:
        return f"${usd * 1_000:.3f}m"   # milli-dollars
    return f"${usd:.4f}"


def tier_model_label(sc: SkillCost) -> str:
    """Single display column: Brain tier if known, else model pin, else '—'."""
    parts = []
    if sc.brain_tier:
        parts.append(sc.brain_tier)
    if sc.model_pin:
        parts.append(sc.model_pin)
    return "/".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_text(
    skills: list[SkillCost],
    top_n: int | None,
    bottom_n: int | None,
) -> str:
    lines: list[str] = []

    # ---- header ----
    total_t  = sum(s.total_tokens for s in skills)
    total_fm = sum(s.fm_tokens for s in skills)
    total_wr = sum(s.cache_write_usd for s in skills)
    total_rd = sum(s.cache_read_usd for s in skills)

    lines.append("=" * 80)
    lines.append("  SKILL COST MEASUREMENT — ~/.claude/skills/")
    lines.append("=" * 80)
    lines.append(f"  Skills scanned:      {len(skills)}")
    lines.append(f"  Total tokens (all):  {fmt_tok(total_t)}  ({total_t:,} est tokens)")
    lines.append(f"  Total frontmatter:   {fmt_tok(total_fm)}  ({total_fm:,} est tokens)")
    lines.append(f"  If ALL skills loaded:")
    lines.append(f"    Cache-write cost:  {fmt_cost(total_wr)}  (Opus $0.50/MTok)")
    lines.append(f"    Cache-read cost:   {fmt_cost(total_rd)}  (Opus $0.50/MTok)")
    lines.append(f"  Token estimate:      len(text) // {CHARS_PER_TOKEN} chars/token")
    lines.append(f"  Pricing basis:       Opus cache-write + cache-read $0.50/MTok  (verified 2026-04-11)")
    lines.append("")

    # ---- histogram ----
    hist = histogram(skills)
    lines.append("  Distribution by skill size")
    lines.append("  " + "-" * 52)
    for label, count in hist.items():
        bar = "#" * count
        lines.append(f"  {label:>6}  {count:>3}  {bar}")
    lines.append("")

    # ---- main table ----
    sorted_skills = sorted(skills, key=lambda s: -s.total_tokens)

    sections: list[tuple[str, list[SkillCost]]] = []
    if top_n is not None or bottom_n is not None:
        if top_n is not None:
            sections.append((f"Top {top_n} largest", sorted_skills[:top_n]))
        if bottom_n is not None:
            sections.append((f"Bottom {bottom_n} smallest", sorted_skills[-bottom_n:][::-1]))
    else:
        # default: show top 10 + bottom 10 then full table
        sections.append(("Top 10 largest", sorted_skills[:10]))
        sections.append(("Bottom 10 smallest", sorted_skills[-10:][::-1]))
        sections.append(("Full roster", sorted_skills))

    col_hdr = (
        f"  {'skill':<22} {'total':>7} {'fm':>6} {'body':>7} "
        f"{'§§':>3} {'tier/pin':<12} {'wr_cost':>9} {'rd_cost':>9}"
    )

    for section_label, skill_list in sections:
        lines.append(f"  {section_label}")
        lines.append("  " + "-" * 76)
        lines.append(col_hdr)
        for s in skill_list:
            lines.append(
                f"  {s.name:<22} {fmt_tok(s.total_tokens):>7} {fmt_tok(s.fm_tokens):>6} "
                f"{fmt_tok(s.body_tokens):>7} {s.sections:>3} "
                f"{tier_model_label(s):<12} {fmt_cost(s.cache_write_usd):>9} "
                f"{fmt_cost(s.cache_read_usd):>9}"
            )
        lines.append("")

    # ---- aggregate by Brain tier ----
    tier_groups: dict[str, list[SkillCost]] = {}
    unassigned: list[SkillCost] = []
    for s in skills:
        if s.brain_tier:
            tier_groups.setdefault(s.brain_tier, []).append(s)
        else:
            unassigned.append(s)

    lines.append("  By Brain tier (manifest assignment)")
    lines.append("  " + "-" * 52)
    lines.append(f"  {'tier':<8} {'count':>5} {'total_tokens':>14} {'wr_cost':>10}")
    for tier in sorted(tier_groups.keys()):
        group = tier_groups[tier]
        g_tok = sum(s.total_tokens for s in group)
        g_wr  = sum(s.cache_write_usd for s in group)
        lines.append(f"  {tier:<8} {len(group):>5} {fmt_tok(g_tok):>14} {fmt_cost(g_wr):>10}")
    if unassigned:
        u_tok = sum(s.total_tokens for s in unassigned)
        u_wr  = sum(s.cache_write_usd for s in unassigned)
        lines.append(f"  {'(none)':<8} {len(unassigned):>5} {fmt_tok(u_tok):>14} {fmt_cost(u_wr):>10}")
    lines.append("")

    # ---- preload recommendation ----
    PRELOAD_THRESHOLD = 5_000   # tokens — skills under this are cheap enough to always load
    cheap  = [s for s in skills if s.total_tokens <= PRELOAD_THRESHOLD]
    costly = [s for s in skills if s.total_tokens  > PRELOAD_THRESHOLD]
    lines.append(f"  Preload vs on-demand split  (threshold: {fmt_tok(PRELOAD_THRESHOLD)} tokens)")
    lines.append("  " + "-" * 52)
    cheap_tok = sum(s.total_tokens for s in cheap)
    cheap_wr  = sum(s.cache_write_usd for s in cheap)
    lines.append(f"  Preload-safe  (<= {fmt_tok(PRELOAD_THRESHOLD)}):  {len(cheap):>3} skills  "
                 f"{fmt_tok(cheap_tok):>8} tokens  {fmt_cost(cheap_wr):>9}/load")
    costly_tok = sum(s.total_tokens for s in costly)
    costly_wr  = sum(s.cache_write_usd for s in costly)
    lines.append(f"  On-demand-only (> {fmt_tok(PRELOAD_THRESHOLD)}):  {len(costly):>3} skills  "
                 f"{fmt_tok(costly_tok):>8} tokens  {fmt_cost(costly_wr):>9}/load")
    if cheap:
        lines.append(f"  Preload-safe skills: {', '.join(s.name for s in sorted(cheap, key=lambda x: x.name))}")
    lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def render_json(skills: list[SkillCost]) -> str:
    total_t   = sum(s.total_tokens for s in skills)
    total_fm  = sum(s.fm_tokens for s in skills)
    total_wr  = sum(s.cache_write_usd for s in skills)
    total_rd  = sum(s.cache_read_usd for s in skills)
    hist      = histogram(skills)

    skill_list = []
    for s in sorted(skills, key=lambda x: -x.total_tokens):
        d = asdict(s)
        d["fm_pct"] = round(s.fm_pct, 1)
        d["tier_label"] = tier_model_label(s)
        skill_list.append(d)

    return json.dumps(
        {
            "skills_dir": str(SKILLS_DIR),
            "manifest": str(MANIFEST),
            "skills_scanned": len(skills),
            "token_estimate_method": f"len(text) // {CHARS_PER_TOKEN}",
            "pricing": {
                "opus_cache_write_per_mtok": OPUS_CACHE_WRITE_PER_MTOK,
                "opus_cache_read_per_mtok": OPUS_CACHE_READ_PER_MTOK,
                "note": "Opus rates used as worst-case; verified 2026-04-11",
            },
            "totals": {
                "total_tokens": total_t,
                "total_fm_tokens": total_fm,
                "all_skills_cache_write_usd": round(total_wr, 6),
                "all_skills_cache_read_usd": round(total_rd, 6),
            },
            "distribution": hist,
            "skills": skill_list,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure token cost of every skill in ~/.claude/skills/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  skill_cost_measure.py                  Full report\n"
            "  skill_cost_measure.py --top 10         Top 10 heaviest\n"
            "  skill_cost_measure.py --bottom 10      Bottom 10 lightest\n"
            "  skill_cost_measure.py --json           Machine-readable\n"
            "  skill_cost_measure.py --top 5 --json   Top 5 as JSON\n"
        ),
    )
    parser.add_argument("--top",    type=int, default=None, help="Show top N largest skills")
    parser.add_argument("--bottom", type=int, default=None, help="Show bottom N smallest skills")
    parser.add_argument("--json",   action="store_true",   help="Machine-readable JSON output")
    args = parser.parse_args()

    if not MANIFEST.exists():
        print(f"WARNING: manifest not found: {MANIFEST} — tier data will be blank", file=sys.stderr)

    if not SKILLS_DIR.exists():
        print(f"ERROR: skills directory not found: {SKILLS_DIR}", file=sys.stderr)
        return 2

    skill_tier_map, _ = load_manifest()
    skills = scan_all_skills(skill_tier_map)

    if not skills:
        print("ERROR: no SKILL.md files found", file=sys.stderr)
        return 2

    # sort by tokens desc (default); --top/--bottom operate on this ordering
    skills.sort(key=lambda s: -s.total_tokens)

    if args.json:
        subset = skills
        if args.top is not None:
            subset = skills[: args.top]
        elif args.bottom is not None:
            subset = skills[-args.bottom :][::-1]
        print(render_json(subset))
        return 0

    print(render_text(skills, top_n=args.top, bottom_n=args.bottom))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
