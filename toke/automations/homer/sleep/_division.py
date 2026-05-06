#!/usr/bin/env python3
"""
Homer L6 — _division.py (shared division-filter utility for parameterized meta-stack)
=====================================================================================
Single source of truth for telemetry-slice access by Director division.

Used by:
- aurora.py    (filter decisions.jsonl by division to tune routing weights)
- hesper.py    (filter _learnings.md corpus by division.primary_skills + support_skills)
- nyx.py       (filter SKILL.md theater audit by division-membership skills)
- oracle.py    (filter Zeus syntheses by division for division-specific rubric scoring)
- check_activation.py (gate per-division meta-stack on traffic threshold)

Design (per blueprint §4.3 hard rule):
- ONE Aurora / Hesper / Oracle / Nyx / Curator parameterized by --division
- NEVER replicate as aurora-research / aurora-ue5 / aurora-toke
- Telemetry-slice is just a filter; no agent fan-out, no sprawl

Cross-join strategy (decisions.jsonl ↔ division):
- decisions.jsonl has no `division` field (Brain UserPromptSubmit hook predates Director)
- director_decisions.jsonl has `matched_division` per prompt_text
- Join on `prompt_text` (exact match) — built as O(N) dict, O(1) lookup
- Falls open: prompts with no Director match → division="unknown" (preserved, not dropped)

Tools.jsonl ↔ division:
- Skill-tool entries have explicit `division` field (PostToolUse hook attaches it)
- Non-Skill entries (Bash, Read, etc.) have no division — best-attributable via decision_id
  cross-join to decisions.jsonl, then to director map

Citations:
- Blueprint §4.3 parameterization rule: Toke/research/division_self_improving_agents_blueprint_2026-05-02.md:181-199
- Blueprint §4.4 threshold-gated activation: same:201-227
- Director schema: Toke/automations/director/divisions_manifest.json
- Cognition essay (single-thread for write-heavy): https://cognition.ai/blog/dont-build-multi-agents
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# === Paths (resolved against ~/.claude and Toke layout) ============================

THIS_FILE = Path(__file__).resolve()
SLEEP_DIR = THIS_FILE.parent
HOMER_DIR = SLEEP_DIR.parent
AUTOMATIONS_DIR = HOMER_DIR.parent
TOKE_DIR = AUTOMATIONS_DIR.parent

DIVISIONS_DIR = AUTOMATIONS_DIR / "director" / "divisions"
DIVISIONS_MANIFEST = AUTOMATIONS_DIR / "director" / "divisions_manifest.json"
SKILL_TO_DIVISION = AUTOMATIONS_DIR / "director" / "skill_to_division.json"

CLAUDE_HOME = Path.home() / ".claude"
DECISIONS_JSONL = CLAUDE_HOME / "telemetry" / "brain" / "decisions.jsonl"
DIRECTOR_JSONL = CLAUDE_HOME / "telemetry" / "brain" / "director_decisions.jsonl"
TOOLS_JSONL = CLAUDE_HOME / "telemetry" / "brain" / "tools.jsonl"
SKILLS_DIR = CLAUDE_HOME / "skills"


# === Spec loading ==================================================================


@dataclass
class DivisionSpec:
    """Loaded division JSON with helpers for skill-set membership."""
    division: str
    name: str
    prefix: str
    mode: str
    tier_floor: str
    primary_skills: list[str] = field(default_factory=list)
    support_skills: list[str] = field(default_factory=list)
    membership_signals: list[dict] = field(default_factory=list)
    boundary: str = ""
    rationale: str = ""
    success_metrics: dict = field(default_factory=dict)
    anti_goals: list[str] = field(default_factory=list)
    activation_policy: dict = field(default_factory=dict)
    calibration_rubric: dict = field(default_factory=dict)
    sacred_rule_overrides: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def all_skills(self) -> list[str]:
        """Union of primary + support, dedup-preserving order."""
        seen: set[str] = set()
        out: list[str] = []
        for s in self.primary_skills + self.support_skills:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out


def load_division_spec(division: str, divisions_dir: Path | None = None) -> DivisionSpec:
    """
    Load divisions/<division>.json into a DivisionSpec.

    Raises FileNotFoundError if the division JSON does not exist.
    """
    divisions_dir = divisions_dir if divisions_dir is not None else DIVISIONS_DIR
    spec_path = divisions_dir / f"{division}.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"Division spec not found: {spec_path}")

    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    return DivisionSpec(
        division=raw.get("division", division),
        name=raw.get("name", division),
        prefix=raw.get("prefix", ""),
        mode=raw.get("mode", "single-thread"),
        tier_floor=raw.get("tier_floor", "S2"),
        primary_skills=list(raw.get("primary_skills", [])),
        support_skills=list(raw.get("support_skills", [])),
        membership_signals=list(raw.get("membership_signals", [])),
        boundary=raw.get("boundary", ""),
        rationale=raw.get("rationale", ""),
        success_metrics=dict(raw.get("success_metrics", {})),
        anti_goals=list(raw.get("anti_goals", [])),
        activation_policy=dict(raw.get("activation_policy", {})),
        calibration_rubric=dict(raw.get("calibration_rubric", {})),
        sacred_rule_overrides=list(raw.get("sacred_rule_overrides", [])),
        raw=raw,
    )


def list_divisions(divisions_dir: Path | None = None) -> list[str]:
    """Return list of division names (filenames without .json) in divisions_dir."""
    divisions_dir = divisions_dir if divisions_dir is not None else DIVISIONS_DIR
    if not divisions_dir.exists():
        return []
    return sorted(p.stem for p in divisions_dir.glob("*.json"))


# === Cross-join: prompt_text → division ============================================


def build_prompt_division_map(
    director_jsonl: Path | None = None,
    last_n: int | None = None,
) -> dict[str, str]:
    """
    Build {prompt_text: matched_division} from director_decisions.jsonl.

    last_n caps to the most recent N entries; None reads all (file is bounded).
    Conflicts (same prompt, different division on different days): last write wins
    — Director's lexicon evolves and the freshest classification is the most
    accurate one.
    """
    director_jsonl = director_jsonl if director_jsonl is not None else DIRECTOR_JSONL
    out: dict[str, str] = {}
    if not director_jsonl.exists():
        return out

    rows: list[dict] = []
    try:
        with open(director_jsonl, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return out

    if last_n is not None:
        rows = rows[-last_n:]

    for r in rows:
        prompt = r.get("prompt_text") or ""
        div = r.get("matched_division") or r.get("division")
        if prompt and div:
            out[prompt] = div
    return out


# === Decisions filter ==============================================================


def iter_decisions_for_division(
    division: str,
    decisions_jsonl: Path | None = None,
    director_jsonl: Path | None = None,
) -> Iterator[dict]:
    """
    Yield every decisions.jsonl row whose prompt_text was Director-classified
    into the given division.

    Rows where Director never classified the prompt are skipped (not yielded as
    "unknown") — this is the strict-attribution mode appropriate for Aurora /
    Oracle, where unattributed rows would smear weights across divisions. For
    inclusive mode, use iter_decisions_with_division().
    """
    decisions_jsonl = decisions_jsonl if decisions_jsonl is not None else DECISIONS_JSONL
    director_jsonl = director_jsonl if director_jsonl is not None else DIRECTOR_JSONL

    prompt_to_div = build_prompt_division_map(director_jsonl)
    if not decisions_jsonl.exists():
        return

    with open(decisions_jsonl, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = d.get("prompt_text") or ""
            mapped = prompt_to_div.get(prompt)
            if mapped == division:
                yield d


def iter_decisions_with_division(
    decisions_jsonl: Path | None = None,
    director_jsonl: Path | None = None,
) -> Iterator[tuple[dict, str | None]]:
    """
    Yield (decision_row, division_or_None) for every decisions.jsonl row.
    division is None if Director never classified that prompt.

    Use this for inclusive analyses (e.g., division coverage diagnostics).
    """
    decisions_jsonl = decisions_jsonl if decisions_jsonl is not None else DECISIONS_JSONL
    director_jsonl = director_jsonl if director_jsonl is not None else DIRECTOR_JSONL

    prompt_to_div = build_prompt_division_map(director_jsonl)
    if not decisions_jsonl.exists():
        return

    with open(decisions_jsonl, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = d.get("prompt_text") or ""
            yield d, prompt_to_div.get(prompt)


# === Tools filter ==================================================================


def iter_tools_for_division(
    division: str,
    tools_jsonl: Path | None = None,
    decisions_jsonl: Path | None = None,
    director_jsonl: Path | None = None,
) -> Iterator[dict]:
    """
    Yield every tools.jsonl row attributable to the division.

    Strategy (in order of preference):
    1. Row has explicit `division` field (Skill-tool entries) → match directly
    2. Row has `decision_id` → look up decision → cross-join to division via
       prompt_text → match
    3. Otherwise → skipped (cannot attribute)
    """
    tools_jsonl = tools_jsonl if tools_jsonl is not None else TOOLS_JSONL
    decisions_jsonl = decisions_jsonl if decisions_jsonl is not None else DECISIONS_JSONL
    director_jsonl = director_jsonl if director_jsonl is not None else DIRECTOR_JSONL

    if not tools_jsonl.exists():
        return

    decision_to_division: dict[str, str] = {}
    if decisions_jsonl.exists():
        prompt_to_div = build_prompt_division_map(director_jsonl)
        try:
            with open(decisions_jsonl, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    decision_id = d.get("decision_id")
                    prompt = d.get("prompt_text") or ""
                    if decision_id and prompt in prompt_to_div:
                        decision_to_division[decision_id] = prompt_to_div[prompt]
        except OSError:
            pass

    with open(tools_jsonl, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            explicit = t.get("division")
            if explicit == division:
                yield t
                continue
            decision_id = t.get("decision_id")
            if decision_id and decision_to_division.get(decision_id) == division:
                yield t


# === Learnings filter ==============================================================


def learnings_paths_for_division(
    spec: DivisionSpec,
    skills_dir: Path | None = None,
) -> list[Path]:
    """
    Return list of _learnings.md paths for skills in spec.all_skills.
    Existence-checked — non-existent skill dirs are dropped silently.
    """
    skills_dir = skills_dir if skills_dir is not None else SKILLS_DIR
    if not skills_dir.exists():
        return []
    out: list[Path] = []
    for skill in spec.all_skills:
        learnings = skills_dir / skill / "_learnings.md"
        if learnings.exists():
            out.append(learnings)
    return out


def skill_md_paths_for_division(
    spec: DivisionSpec,
    skills_dir: Path | None = None,
) -> list[Path]:
    """
    Return list of SKILL.md paths for skills in spec.all_skills.
    Existence-checked.
    """
    skills_dir = skills_dir if skills_dir is not None else SKILLS_DIR
    if not skills_dir.exists():
        return []
    out: list[Path] = []
    for skill in spec.all_skills:
        skill_md = skills_dir / skill / "SKILL.md"
        if skill_md.exists():
            out.append(skill_md)
    return out


# === Activation gate ===============================================================


@dataclass
class ActivationStatus:
    division: str
    decisions_30d: int
    decisions_7d: int
    tools_30d: int
    threshold: int
    activated: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "division": self.division,
            "decisions_30d": self.decisions_30d,
            "decisions_7d": self.decisions_7d,
            "tools_30d": self.tools_30d,
            "threshold": self.threshold,
            "activated": self.activated,
            "reason": self.reason,
        }


def compute_activation_status(
    division: str,
    threshold: int | None = None,
    decisions_jsonl: Path | None = None,
    director_jsonl: Path | None = None,
    tools_jsonl: Path | None = None,
    now_iso: str | None = None,
) -> ActivationStatus:
    """
    Activation gate per blueprint §4.4 — division activates per-division
    meta-stack only when traffic ≥ threshold/30d.

    threshold defaults to spec.activation_policy.decisions_per_30d_threshold
    or 100 if absent.

    now_iso lets callers pin the clock for deterministic tests.
    """
    import datetime as _dt

    if now_iso is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    else:
        now = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    cutoff_30d = now - _dt.timedelta(days=30)
    cutoff_7d = now - _dt.timedelta(days=7)

    if threshold is None:
        try:
            spec = load_division_spec(division)
            threshold = int(spec.activation_policy.get("decisions_per_30d_threshold", 100))
        except FileNotFoundError:
            threshold = 100

    def parse_ts(s: str) -> _dt.datetime | None:
        if not s:
            return None
        try:
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    d30 = 0
    d7 = 0
    for d in iter_decisions_for_division(
        division,
        decisions_jsonl=decisions_jsonl,
        director_jsonl=director_jsonl,
    ):
        ts = parse_ts(d.get("ts", ""))
        if ts is None:
            continue
        if ts >= cutoff_30d:
            d30 += 1
        if ts >= cutoff_7d:
            d7 += 1

    t30 = 0
    for t in iter_tools_for_division(
        division,
        tools_jsonl=tools_jsonl,
        decisions_jsonl=decisions_jsonl,
        director_jsonl=director_jsonl,
    ):
        ts = parse_ts(t.get("ts", ""))
        if ts is None:
            continue
        if ts >= cutoff_30d:
            t30 += 1

    activated = d30 >= threshold
    reason = (
        f"{d30} decisions/30d ≥ {threshold} threshold" if activated
        else f"{d30} decisions/30d < {threshold} threshold (need {threshold - d30} more)"
    )

    return ActivationStatus(
        division=division,
        decisions_30d=d30,
        decisions_7d=d7,
        tools_30d=t30,
        threshold=threshold,
        activated=activated,
        reason=reason,
    )


# === CLI ===========================================================================


def _cli_info(division: str) -> int:
    try:
        spec = load_division_spec(division)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    status = compute_activation_status(division)
    learnings = learnings_paths_for_division(spec)
    skill_mds = skill_md_paths_for_division(spec)

    print(f"Division: {spec.division} ({spec.name})")
    print(f"  prefix: {spec.prefix} | mode: {spec.mode} | tier_floor: {spec.tier_floor}")
    print(f"  primary_skills ({len(spec.primary_skills)}): {', '.join(spec.primary_skills)}")
    print(f"  support_skills ({len(spec.support_skills)}): {', '.join(spec.support_skills)}")
    print(f"  _learnings.md present: {len(learnings)}/{len(spec.all_skills)}")
    print(f"  SKILL.md present:      {len(skill_mds)}/{len(spec.all_skills)}")
    print(f"  ACTIVATION: {'ACTIVE' if status.activated else 'INACTIVE'}")
    print(f"  - decisions 30d: {status.decisions_30d} (threshold {status.threshold})")
    print(f"  - decisions 7d:  {status.decisions_7d}")
    print(f"  - tools 30d:     {status.tools_30d}")
    print(f"  - reason: {status.reason}")
    return 0


def _cli_list() -> int:
    divisions = list_divisions()
    if not divisions:
        print("No divisions found.")
        return 0
    print(f"Divisions ({len(divisions)}):")
    for d in divisions:
        try:
            spec = load_division_spec(d)
            status = compute_activation_status(d)
            mark = "ACTIVE" if status.activated else "INACTIVE"
            print(f"  {d:20s}  mode={spec.mode:13s}  tier_floor={spec.tier_floor}  "
                  f"30d={status.decisions_30d:5d}  {mark}")
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"  {d:20s}  (failed to load)")
    return 0


def _cli_count(division: str) -> int:
    try:
        load_division_spec(division)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    decisions = sum(1 for _ in iter_decisions_for_division(division))
    tools = sum(1 for _ in iter_tools_for_division(division))
    print(f"Division: {division}")
    print(f"  decisions all-time: {decisions}")
    print(f"  tools all-time:     {tools}")
    return 0


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Usage:")
        print("  python _division.py list                    # list all divisions + activation")
        print("  python _division.py info <division>         # full division status")
        print("  python _division.py count <division>        # count decisions+tools all-time")
        return 0

    cmd = argv[1]
    if cmd == "list":
        return _cli_list()
    if cmd == "info" and len(argv) >= 3:
        return _cli_info(argv[2])
    if cmd == "count" and len(argv) >= 3:
        return _cli_count(argv[2])
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
