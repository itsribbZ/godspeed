#!/usr/bin/env python3
"""
Settings.json patch generator (Cycle 3).
==========================================
Diff-tooling for ~/.claude/settings.json — proposes hook chain additions and
removals as unified diffs, with a dry-run-by-default safety contract.

Design:
  - DEFAULT: print the proposed diff. No file writes.
  - --apply: backup + write. Writes a timestamped backup to
    Toke/hooks/settings_backup_<UTC>.json before any change.
  - --validate: parse the current settings.json + print a status report
    listing every event hook chain.

Why a generator and not direct edits:
  - Settings.json is global-impact: every Claude Code session reads it.
  - Bad JSON breaks the harness. Diff-then-apply enforces a review checkpoint.
  - The settings_backup_*.json convention is already Toke standard
    (see toke-init Session Rules). This module makes it cheap.

Sacred Rule alignment:
  Rule 2: NEVER overwrites without --apply + backup
  Rule 4 (only-asked): default is dry-run preview
  Rule 5: backup is itself a diagnostic — kept indefinitely
  Rule 9: --apply is opinionated — backs up automatically
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


HOME = Path.home()
SETTINGS_PATH = HOME / ".claude" / "settings.json"
TOKE_HOOKS = HOME / "Desktop" / "T1" / "Toke" / "hooks"


# -----------------------------------------------------------------------------
# Load + parse
# -----------------------------------------------------------------------------


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dump_settings(s: dict) -> str:
    """Render with stable formatting matching Toke convention (2-space indent)."""
    return json.dumps(s, indent=2, ensure_ascii=False) + "\n"


# -----------------------------------------------------------------------------
# Status report
# -----------------------------------------------------------------------------


def render_status(settings: dict) -> str:
    md = []
    hooks = settings.get("hooks") or {}
    md.append("# Settings.json Hook Status\n")
    md.append(f"**Source:** `{SETTINGS_PATH}`\n")
    if not hooks:
        md.append("\n*No hooks configured.*")
        return "\n".join(md)
    for event, matchers in sorted(hooks.items()):
        md.append(f"## {event}\n")
        if not isinstance(matchers, list):
            md.append(f"  *unexpected shape: {type(matchers).__name__}*")
            continue
        for i, m in enumerate(matchers):
            mat = m.get("matcher", "")
            cmds = m.get("hooks") or []
            md.append(f"- matcher `{mat}` → {len(cmds)} hook(s):")
            for c in cmds:
                cmd = c.get("command", "")
                md.append(f"  - `{cmd}`")
        md.append("")
    return "\n".join(md) + "\n"


# -----------------------------------------------------------------------------
# Patch ops
# -----------------------------------------------------------------------------


@dataclass
class HookAdd:
    event: str        # SessionEnd, UserPromptSubmit, etc.
    matcher: str      # "*" or "**/*" — must match existing matcher to merge
    command: str      # full shell command
    type: str = "command"


def _ensure_event_chain(settings: dict, event: str) -> list:
    """Get-or-create the event's matcher list."""
    settings.setdefault("hooks", {})
    settings["hooks"].setdefault(event, [])
    return settings["hooks"][event]


def apply_add(settings: dict, add: HookAdd) -> dict:
    """Append a hook command to the right matcher block. Creates block if missing.

    Idempotent: if the exact (matcher, command) tuple already exists, no-op.
    """
    new = copy.deepcopy(settings)
    chain = _ensure_event_chain(new, add.event)

    # Find existing matcher block
    existing = next((b for b in chain if b.get("matcher") == add.matcher), None)
    if existing is None:
        existing = {"matcher": add.matcher, "hooks": []}
        chain.append(existing)

    # Idempotent check
    if any(h.get("command") == add.command and h.get("type") == add.type
           for h in existing.get("hooks", [])):
        return new  # already there

    existing.setdefault("hooks", []).append({
        "type": add.type,
        "command": add.command,
    })
    return new


@dataclass
class HookRemove:
    event: str
    command_substring: str   # match by substring


def apply_remove(settings: dict, rm: HookRemove) -> dict:
    """Remove every hook command containing the substring from the event chain."""
    new = copy.deepcopy(settings)
    chain = new.get("hooks", {}).get(rm.event, [])
    for block in chain:
        block["hooks"] = [
            h for h in block.get("hooks", [])
            if rm.command_substring not in (h.get("command") or "")
        ]
    return new


# -----------------------------------------------------------------------------
# Diff render
# -----------------------------------------------------------------------------


def unified_diff(before: dict, after: dict) -> str:
    a = dump_settings(before).splitlines(keepends=True)
    b = dump_settings(after).splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        a, b,
        fromfile="settings.json (current)",
        tofile="settings.json (proposed)",
        n=3,
    ))


# -----------------------------------------------------------------------------
# Backup + write
# -----------------------------------------------------------------------------


def backup_settings(path: Path = SETTINGS_PATH) -> Path:
    """Copy current settings.json to Toke/hooks/settings_backup_<UTC>.json."""
    TOKE_HOOKS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_T%H%M%S")
    dest = TOKE_HOOKS / f"settings_backup_{stamp}.json"
    shutil.copyfile(path, dest)
    return dest


def write_settings(new: dict, *, dest: Path = SETTINGS_PATH) -> None:
    """Atomic write: tmp file + rename. Re-raises on failure."""
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(dump_settings(new), encoding="utf-8")
    tmp.replace(dest)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="settings_patch")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="render hook chain status report")

    p_add = sub.add_parser("add", help="propose a hook addition (dry-run by default)")
    p_add.add_argument("--event", required=True,
                       help="SessionStart | UserPromptSubmit | PostToolUse | "
                            "SessionEnd | PreCompact | SubagentStop | Stop | etc.")
    p_add.add_argument("--matcher", default="**/*",
                       help="matcher glob (default '**/*')")
    p_add.add_argument("--command", required=True,
                       help="shell command (e.g. 'bash ~/.claude/hooks/x.sh')")
    p_add.add_argument("--type", default="command")
    p_add.add_argument("--apply", action="store_true",
                       help="actually write (with backup); default is dry-run")

    p_rm = sub.add_parser("remove", help="propose a hook removal (dry-run by default)")
    p_rm.add_argument("--event", required=True)
    p_rm.add_argument("--command-substring", required=True,
                      help="match commands by substring; ALL matches removed")
    p_rm.add_argument("--apply", action="store_true")

    args = p.parse_args(argv)

    settings = load_settings()

    if args.cmd == "status":
        print(render_status(settings))
        return 0

    if args.cmd == "add":
        new = apply_add(settings, HookAdd(
            event=args.event, matcher=args.matcher,
            command=args.command, type=args.type,
        ))
        diff = unified_diff(settings, new)
        if not diff:
            print("# No-op: hook already present.")
            return 0
        print(diff)
        if args.apply:
            backup = backup_settings()
            write_settings(new)
            print(f"\n# APPLIED. Backup: {backup}")
        else:
            print("\n# DRY-RUN. Re-run with --apply to write (auto-backup first).")
        return 0

    if args.cmd == "remove":
        new = apply_remove(settings, HookRemove(
            event=args.event, command_substring=args.command_substring,
        ))
        diff = unified_diff(settings, new)
        if not diff:
            print("# No-op: no matching hook found.")
            return 0
        print(diff)
        if args.apply:
            backup = backup_settings()
            write_settings(new)
            print(f"\n# APPLIED. Backup: {backup}")
        else:
            print("\n# DRY-RUN. Re-run with --apply to write (auto-backup first).")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
