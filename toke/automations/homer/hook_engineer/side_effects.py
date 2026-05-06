#!/usr/bin/env python3
"""
Side-effect manifest verification (Cycle 3).
=============================================
Hooks are valuable for what they WRITE — a hook that exits 0 but never wrote
the expected log line is silently broken. The harness needs to verify the
side effects, not just the exit code.

This module provides:
  - Declarative manifest: per-hook tuples of (path, must_grow_by_at_least_lines,
    must_contain_substring_after_run).
  - Verification utility that snapshots before, runs, snapshots after,
    asserts the delta.
  - Battery of side-effect probes against the shipped hooks.

Sacred Rule alignment:
  Rule 5: side-effect logs ARE diagnostics. Verification ensures they keep
          producing — silent breakage is the failure mode this catches.
  Rule 11: every manifest entry cites the file path AND the canonical
          substring expected to appear.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_harness import (  # noqa: E402
    HookResult, ProbeOutcome, AssertionFailure, invoke_hook,
    mock_SessionEnd, mock_UserPromptSubmit, mock_SubagentStop,
    EVENT_BUDGETS_MS,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


HOME = Path.home()
TELEMETRY = HOME / ".claude" / "telemetry" / "brain"
TOKE_HOOKS = HOME / "Desktop" / "T1" / "Toke" / "hooks"


# -----------------------------------------------------------------------------
# Manifest
# -----------------------------------------------------------------------------


@dataclass
class SideEffect:
    """One observable file change a hook should produce when invoked."""
    path: Path
    must_grow_lines: int = 0           # min new lines after run
    must_contain: str | None = None    # substring that must appear in tail

    def snapshot(self) -> tuple[int, int]:
        """Return (size_bytes, line_count) for current state. Returns (0,0) if missing."""
        try:
            data = self.path.read_text(encoding="utf-8", errors="replace")
            return (len(data), data.count("\n"))
        except (FileNotFoundError, OSError):
            return (0, 0)


@dataclass
class HookManifest:
    """Declares the side effects a hook should produce.

    `event` is the hook event name (drives default budget + payload factory).
    """
    label: str
    cmd: str
    event: str
    side_effects: list[SideEffect] = field(default_factory=list)


# Canonical manifest for shipped Toke hooks. Add new hooks here as they ship.
SHIPPED_MANIFESTS: list[HookManifest] = [
    HookManifest(
        label="brain_advisor.sh",
        cmd=f"bash {TOKE_HOOKS / 'brain_advisor.sh'}",
        event="UserPromptSubmit",
        side_effects=[
            SideEffect(
                path=TELEMETRY / "decisions.jsonl",
                must_grow_lines=1,
                must_contain="UserPromptSubmit",
            ),
        ],
    ),
    HookManifest(
        label="token_accountant_receipt.sh",
        cmd=f"bash {TOKE_HOOKS / 'token_accountant_receipt.sh'}",
        event="SessionEnd",
        side_effects=[
            SideEffect(
                path=TELEMETRY / "token_accountant_receipt.log",
                must_grow_lines=1,
                must_contain="run:",
            ),
        ],
    ),
    HookManifest(
        label="subagent_capture.sh",
        cmd=f"bash {TOKE_HOOKS / 'subagent_capture.sh'}",
        event="SubagentStop",
        side_effects=[
            SideEffect(
                path=TELEMETRY / "subagent_completions.jsonl",
                must_grow_lines=1,
                must_contain="SubagentStop",
            ),
        ],
    ),
    HookManifest(
        label="session_cost_report.sh",
        cmd=f"bash {TOKE_HOOKS / 'session_cost_report.sh'}",
        event="SessionEnd",
        side_effects=[
            # session_cost_report writes to session_costs.log when transcript
            # exists. With our mock session_id (random UUID), there's no
            # transcript so it may legitimately not write. We don't assert
            # growth here — just document the path for visibility.
            SideEffect(
                path=TELEMETRY / "session_costs.log",
                must_grow_lines=0,
            ),
        ],
    ),
    HookManifest(
        label="toke_session_learn.sh",
        cmd=f"bash {TOKE_HOOKS / 'toke_session_learn.sh'}",
        event="SessionEnd",
        side_effects=[
            # toke_session_learn writes a Mnemos breadcrumb. Always-fires.
            SideEffect(
                path=TELEMETRY / "toke_session_learn.log",
                must_grow_lines=1,
            ),
        ],
    ),
]


# -----------------------------------------------------------------------------
# Payload generation per event
# -----------------------------------------------------------------------------


def _payload_for(event: str) -> dict:
    """Synthetic payload appropriate for the manifest's declared event."""
    sid = str(uuid.uuid4())
    if event == "UserPromptSubmit":
        return mock_UserPromptSubmit(session_id=sid, prompt="hook-engineer side-effect probe")
    if event == "SubagentStop":
        return mock_SubagentStop(session_id=sid)
    if event == "SessionEnd":
        return mock_SessionEnd(session_id=sid)
    # Default — generic envelope with session_id
    return {"session_id": sid}


# -----------------------------------------------------------------------------
# Verification
# -----------------------------------------------------------------------------


@dataclass
class SideEffectProbe:
    """Outcome of one manifest verification."""
    label: str
    cmd: str
    event: str
    result: HookResult
    pre_snapshots: dict[str, tuple[int, int]]  # path -> (bytes, lines)
    post_snapshots: dict[str, tuple[int, int]]
    failures: list[AssertionFailure]

    @property
    def passed(self) -> bool:
        return not self.failures and self.result.exit_code == 0


def verify_manifest(manifest: HookManifest, *, timeout: float = 5.0) -> SideEffectProbe:
    """Snapshot → invoke → snapshot → diff."""
    pre = {str(se.path): se.snapshot() for se in manifest.side_effects}
    payload = _payload_for(manifest.event)
    result = invoke_hook(manifest.cmd, payload, timeout=timeout, label=manifest.label)
    # Brief settle — some hooks (e.g. token_accountant_receipt.sh) background
    # the actual write. Wait up to 3s for the file to grow.
    deadline = time.time() + 3.0
    failures: list[AssertionFailure] = []
    post = {}
    for se in manifest.side_effects:
        target_lines = pre[str(se.path)][1] + se.must_grow_lines
        while time.time() < deadline:
            cur = se.snapshot()
            if cur[1] >= target_lines:
                break
            time.sleep(0.1)
        post[str(se.path)] = se.snapshot()
    # Assert: each side-effect file grew by at least the expected line count
    # AND (if specified) contains the required substring in tail
    for se in manifest.side_effects:
        pre_lines = pre[str(se.path)][1]
        post_lines = post[str(se.path)][1]
        delta = post_lines - pre_lines
        if delta < se.must_grow_lines:
            failures.append(AssertionFailure(
                f"side_effect[{se.path.name}].lines_grew",
                f">= {se.must_grow_lines}", str(delta),
            ))
        if se.must_contain and post_lines > 0:
            try:
                tail = se.path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                tail = ""
            if se.must_contain not in tail:
                failures.append(AssertionFailure(
                    f"side_effect[{se.path.name}].contains",
                    se.must_contain, "<not in last 4KB>",
                ))
    return SideEffectProbe(
        label=manifest.label, cmd=manifest.cmd, event=manifest.event,
        result=result, pre_snapshots=pre, post_snapshots=post, failures=failures,
    )


def run_all_manifests() -> list[SideEffectProbe]:
    return [verify_manifest(m) for m in SHIPPED_MANIFESTS]


def render_side_effect_report(probes: list[SideEffectProbe]) -> str:
    n_pass = sum(1 for p in probes if p.passed)
    n_fail = len(probes) - n_pass

    md = []
    md.append("# Hook-Engineer Side-Effect Verification Report\n")
    md.append(f"**Probes:** {len(probes)} | **Pass:** {n_pass} | **Fail:** {n_fail}\n")
    md.append("")
    md.append("## Per-Hook Outcomes\n")
    md.append("| label | event | exit | latency_ms | side-effects |")
    md.append("|---|---|---:|---:|---|")
    for p in probes:
        emoji = "✓" if p.passed else "✗"
        se_summary = []
        for path_str, post in p.post_snapshots.items():
            pre = p.pre_snapshots[path_str]
            delta = post[1] - pre[1]
            name = Path(path_str).name
            se_summary.append(f"{name}+{delta}L")
        md.append(f"| {emoji} `{p.label}` | {p.event} | {p.result.exit_code} | "
                  f"{p.result.duration_ms:.1f} | {', '.join(se_summary) or '(none declared)'} |")
    md.append("")
    if any(not p.passed for p in probes):
        md.append("## Failures\n")
        for p in probes:
            if p.passed:
                continue
            md.append(f"### `{p.label}`\n")
            for f in p.failures:
                md.append(f"- **{f.name}**: expected `{f.expected}`, got `{f.actual}`")
            md.append("")
    md.append("## Methodology\n")
    md.append("- Pre-snapshot every declared side-effect file (bytes + line count)")
    md.append("- Invoke hook with synthetic event payload")
    md.append("- Wait up to 3s for backgrounded writes (matches token_accountant_receipt.sh pattern)")
    md.append("- Post-snapshot + assert line growth >= manifest minimum")
    md.append("- If `must_contain` declared, scan last 4KB of file for substring")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="side_effects")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="run all manifests + render report")
    sub.add_parser("list", help="list declared manifests")
    args = p.parse_args(argv)

    if args.cmd == "list":
        for m in SHIPPED_MANIFESTS:
            print(f"{m.label} ({m.event})")
            for se in m.side_effects:
                print(f"  -> {se.path}  (grow>={se.must_grow_lines}"
                      f"{', contains=' + repr(se.must_contain) if se.must_contain else ''})")
        return 0

    if args.cmd == "verify":
        probes = run_all_manifests()
        print(render_side_effect_report(probes))
        return 0 if all(p.passed for p in probes) else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
