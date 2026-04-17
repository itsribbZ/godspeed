#!/usr/bin/env python3
"""
Homer L4 — SYBIL
================
Advisor escalation wrapper for Homer / Zeus. When a MUSES worker returns
ROI=0 or Zeus hits an inconclusive research state, Sybil invokes Anthropic's
advisor_20260301 API via brain_cli.py advise.

Sybil is the only Homer component that can make external API calls mid-run.
Cost-capped (max 2 escalations per session). Preconditions enforced.

Contract:
- Import via `from sybil import escalate, SybilState, check_preconditions`
- State file: Toke/automations/homer/sybil/.state/session_<session_id>.json
- Telemetry: ~/.claude/telemetry/brain/advisor_calls.jsonl (Brain owns the file)
- Cost cap: hard max 2 escalations per session_id

Trigger conditions (from Zeus Phase 3 synthesize):
1. Muse returns ROI=0 (empty output or timeout)
2. Zeus Phase 3 reconciliation finds gap in plan coverage
3. Multi-muse disagreement on a factual claim
4. Correction loop detected (2+ user corrections on same problem)

Preconditions (all must pass before escalation fires):
- ANTHROPIC_API_KEY env var set
- brain_cli.py reachable at the expected path
- brain advise subcommand responds to --help
- Task is NOT creative content (Sacred Rule 6)
- Session escalation count < 2
"""

from __future__ import annotations

import datetime
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

# Windows UTF-8 hardening
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HOMER_ROOT = Path(__file__).parent.parent
BRAIN_CLI = HOMER_ROOT.parent / "brain" / "brain_cli.py"
SYBIL_STATE_DIR = Path(__file__).parent / ".state"
ADVISOR_TELEMETRY = Path.home() / ".claude" / "telemetry" / "brain" / "advisor_calls.jsonl"

SESSION_COST_CAP = 2
CREATIVE_CONTENT_KEYWORDS = (
    "write dialogue", "write lore", "character backstory",
    "gdd narrative", "write the quest", "invent names", "story for",
    "write story", "compose song", "write poem", "write verse",
)


@dataclass
class SybilPreconditionCheck:
    """Result of a preconditions check. All must be True for escalate() to proceed."""

    has_api_key: bool = False
    brain_cli_reachable: bool = False
    brain_advise_command_valid: bool = False
    not_creative_content: bool = True
    session_cap_ok: bool = False
    overall_pass: bool = False
    failure_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SybilState:
    """Per-session Sybil state (tracks cost cap)."""

    session_id: str
    escalations_used: int = 0
    escalations_cap: int = SESSION_COST_CAP
    last_escalation_at: str = ""
    escalations_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SybilState":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def is_capped(self) -> bool:
        return self.escalations_used >= self.escalations_cap


def _state_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in session_id)[:60] or "unnamed"
    return SYBIL_STATE_DIR / f"session_{safe}.json"


def load_state(session_id: str) -> SybilState:
    SYBIL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(session_id)
    if not path.exists():
        return SybilState(session_id=session_id)
    try:
        return SybilState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, KeyError):
        return SybilState(session_id=session_id)


def save_state(state: SybilState) -> Path:
    SYBIL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(state.session_id)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return path


def check_preconditions(
    session_id: str,
    task_text: str = "",
    brain_cli: Path | None = None,
) -> SybilPreconditionCheck:
    """Run all preconditions. Returns structured check result."""
    check = SybilPreconditionCheck()
    brain_cli = brain_cli or BRAIN_CLI

    # API key
    if os.environ.get("ANTHROPIC_API_KEY"):
        check.has_api_key = True
    else:
        check.failure_reasons.append("ANTHROPIC_API_KEY not set in env")

    # brain_cli.py reachable
    if brain_cli.exists():
        check.brain_cli_reachable = True
    else:
        check.failure_reasons.append(f"brain_cli.py not found at {brain_cli}")

    # brain advise command valid
    if check.brain_cli_reachable:
        try:
            result = subprocess.run(
                ["python", str(brain_cli), "help"],
                capture_output=True, timeout=10, text=True,
            )
            combined = (result.stdout or "") + (result.stderr or "")
            if "advise" in combined.lower():
                check.brain_advise_command_valid = True
            else:
                check.failure_reasons.append(
                    "brain advise subcommand not present in brain_cli help output"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            check.failure_reasons.append(f"brain help precheck failed: {type(e).__name__}: {e}")

    # Creative content check (Sacred Rule 6)
    task_lower = task_text.lower()
    if any(k in task_lower for k in CREATIVE_CONTENT_KEYWORDS):
        check.not_creative_content = False
        check.failure_reasons.append(
            "task appears to be creative content (Sacred Rule 6 — no advisor for lore/dialogue/GDD narrative)"
        )

    # Session cap
    state = load_state(session_id)
    if not state.is_capped():
        check.session_cap_ok = True
    else:
        check.failure_reasons.append(
            f"session escalation cap hit: {state.escalations_used}/{state.escalations_cap}"
        )

    check.overall_pass = (
        check.has_api_key
        and check.brain_cli_reachable
        and check.brain_advise_command_valid
        and check.not_creative_content
        and check.session_cap_ok
    )
    return check


def _build_advisor_prompt(
    stuck_task: str,
    approaches_tried: list[str],
    blocker: str,
) -> str:
    """Construct the advisor prompt in the canonical format."""
    tried_str = " / ".join(approaches_tried) if approaches_tried else "nothing yet"
    return (
        f"STUCK TASK: {stuck_task}\n"
        f"APPROACHES TRIED: {tried_str}\n"
        f"SPECIFIC BLOCKER: {blocker}\n"
        f"Please provide concrete, actionable guidance to unblock."
    )


def escalate(
    stuck_task: str,
    approaches_tried: list[str],
    blocker: str,
    session_id: str,
    max_uses: int = 2,
    max_tokens: int = 4096,
    dry_run: bool = False,
) -> dict:
    """
    Escalate to the advisor. Returns structured response dict.

    Returns:
        {
            "ok": bool,
            "preconditions": {...},
            "advisor_stdout": str (if ok and not dry_run),
            "prompt_preview": str (if dry_run),
            "escalations_this_session": int,
            "session_cap": int,
            "reason": str,
        }
    """
    check = check_preconditions(session_id=session_id, task_text=stuck_task)
    if not check.overall_pass:
        return {
            "ok": False,
            "preconditions": check.to_dict(),
            "reason": "preconditions failed: " + "; ".join(check.failure_reasons),
        }

    prompt = _build_advisor_prompt(stuck_task, approaches_tried, blocker)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "preconditions": check.to_dict(),
            "prompt_preview": prompt,
            "would_call": f"python {BRAIN_CLI} advise <prompt> --executor sonnet --advisor opus --max-uses {max_uses}",
        }

    try:
        result = subprocess.run(
            [
                "python", str(BRAIN_CLI), "advise", prompt,
                "--executor", "sonnet",
                "--advisor", "opus",
                "--max-uses", str(max_uses),
                "--max-tokens", str(max_tokens),
            ],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "brain advise timeout after 300s"}
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "reason": f"brain advise subprocess failed: {type(e).__name__}: {e}"}

    if result.returncode != 0:
        return {
            "ok": False,
            "reason": f"brain advise exit {result.returncode}",
            "stderr": (result.stderr or "")[-2000:],
        }

    # Successful escalation — update session state
    state = load_state(session_id)
    state.escalations_used += 1
    state.last_escalation_at = datetime.datetime.now().isoformat()
    state.escalations_log.append({
        "at": state.last_escalation_at,
        "task_excerpt": stuck_task[:200],
        "blocker_excerpt": blocker[:200],
    })
    save_state(state)

    return {
        "ok": True,
        "preconditions": check.to_dict(),
        "advisor_stdout": result.stdout,
        "escalations_this_session": state.escalations_used,
        "session_cap": state.escalations_cap,
        "reason": "escalation completed",
    }


# =============================================================================
# CLI entry — for direct testing and homer_cli.py integration
# =============================================================================


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[1]

    if cmd == "check":
        session_id = argv[2] if len(argv) > 2 else f"cli_{secrets.token_hex(4)}"
        task = argv[3] if len(argv) > 3 else "smoke test task"
        result = check_preconditions(session_id=session_id, task_text=task)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.overall_pass else 1

    if cmd == "dry-run":
        session_id = argv[2] if len(argv) > 2 else f"dry_{secrets.token_hex(4)}"
        task = argv[3] if len(argv) > 3 else "stuck task placeholder"
        blocker = argv[4] if len(argv) > 4 else "blocker placeholder"
        result = escalate(
            stuck_task=task,
            approaches_tried=["approach A", "approach B"],
            blocker=blocker,
            session_id=session_id,
            dry_run=True,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if cmd == "state":
        session_id = argv[2] if len(argv) > 2 else ""
        if not session_id:
            print("usage: sybil.py state SESSION_ID", file=sys.stderr)
            return 1
        state = load_state(session_id)
        print(json.dumps(state.to_dict(), indent=2))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
