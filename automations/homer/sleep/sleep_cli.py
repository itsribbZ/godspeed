#!/usr/bin/env python3
"""
Homer L6 — Sleep-time agent unified CLI
========================================
Dispatches Nyx / Hesper / Aurora on-demand or via external cron.

Subcommands:
    sleep_cli.py run nyx       Run Nyx theater audit
    sleep_cli.py run hesper    Run Hesper learning distillation
    sleep_cli.py run aurora    Run Aurora routing weight proposal
    sleep_cli.py run all       Run all three in sequence
    sleep_cli.py status        Show last-run timestamps + latest report paths
    sleep_cli.py help          Show this help
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

SLEEP_ROOT = Path(__file__).parent
NYX_DIR = SLEEP_ROOT / "nyx"
HESPER_DIR = SLEEP_ROOT / "hesper"
AURORA_DIR = SLEEP_ROOT / "aurora"


def _load_nyx():
    sys.path.insert(0, str(NYX_DIR))
    import nyx  # noqa: E402
    return nyx


def _load_hesper():
    sys.path.insert(0, str(HESPER_DIR))
    import hesper  # noqa: E402
    return hesper


def _load_aurora():
    sys.path.insert(0, str(AURORA_DIR))
    import aurora  # noqa: E402
    return aurora


def cmd_run_nyx() -> int:
    nyx = _load_nyx()
    print("=" * 56)
    print("Sleep-time: NYX (theater auditor)")
    print("=" * 56)
    report = nyx.run_audit()
    if not report.get("ok"):
        print(f"  failed: {report.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    path = nyx.write_report(report)
    print(f"  skills audited: {report['skills_audited']}")
    print(f"  PRUNE candidates: {report['prune_candidates_count']}")
    print(f"  INVESTIGATE candidates: {report['investigate_candidates_count']}")
    print(f"  report: {path}")
    return 0


def cmd_run_hesper() -> int:
    hesper = _load_hesper()
    print("=" * 56)
    print("Sleep-time: HESPER (learning distillation)")
    print("=" * 56)
    result = hesper.run_distillation()
    if not result.get("ok"):
        print(f"  failed: {result.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    print(f"  sources mined: {result['sources_mined']}")
    print(f"  top-N distilled: {result['top_n']}")
    print(f"  report: {result['report_path']}")
    return 0


def cmd_run_aurora() -> int:
    aurora = _load_aurora()
    print("=" * 56)
    print("Sleep-time: AURORA (routing weight tuning)")
    print("=" * 56)
    result = aurora.run_tuning()
    if not result.get("ok"):
        print(f"  failed: {result.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    print(f"  decisions analyzed: {result['total_decisions_analyzed']}")
    print(f"  proposals generated: {result['proposals_count']}")
    print(f"  report: {result['report_path']}")
    return 0


def cmd_run_all() -> int:
    print("Running all three sleep-time agents in sequence.")
    print()
    rc = 0
    for fn in (cmd_run_nyx, cmd_run_hesper, cmd_run_aurora):
        r = fn()
        if r != 0:
            rc = r
        print()
    print("Sleep cycle complete.")
    return rc


def _latest_report(dir_path: Path, glob: str) -> dict:
    """Return {'path': str, 'mtime': ISO-str} for the newest matching report."""
    if not dir_path.exists():
        return {"path": None, "mtime": None}
    matches = sorted(dir_path.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        return {"path": None, "mtime": None}
    latest = matches[0]
    mtime_dt = datetime.datetime.fromtimestamp(latest.stat().st_mtime)
    return {"path": str(latest), "mtime": mtime_dt.isoformat()}


def cmd_status() -> int:
    print("=" * 56)
    print("Sleep-time agents — status")
    print("=" * 56)

    nyx_latest = _latest_report(NYX_DIR / "reports", "nyx_audit_*.md")
    hesper_latest = _latest_report(HESPER_DIR / "best_practices", "best_practices_*.md")
    aurora_latest = _latest_report(AURORA_DIR / "proposals", "tuning_*.json")

    print("\nNYX (theater auditor)")
    if nyx_latest["path"]:
        print(f"  last run:  {nyx_latest['mtime']}")
        print(f"  report:    {nyx_latest['path']}")
    else:
        print("  last run:  (never)")
        print("  report:    (none)")

    print("\nHESPER (learning distillation)")
    if hesper_latest["path"]:
        print(f"  last run:  {hesper_latest['mtime']}")
        print(f"  report:    {hesper_latest['path']}")
    else:
        print("  last run:  (never)")
        print("  report:    (none)")

    print("\nAURORA (routing weight tuning)")
    if aurora_latest["path"]:
        print(f"  last run:  {aurora_latest['mtime']}")
        print(f"  report:    {aurora_latest['path']}")
    else:
        print("  last run:  (never)")
        print("  report:    (none)")

    return 0


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd = argv[1]
    if cmd == "run":
        if len(argv) < 3:
            print("usage: sleep_cli.py run {nyx|hesper|aurora|all}", file=sys.stderr)
            return 1
        target = argv[2]
        if target == "nyx":
            return cmd_run_nyx()
        if target == "hesper":
            return cmd_run_hesper()
        if target == "aurora":
            return cmd_run_aurora()
        if target == "all":
            return cmd_run_all()
        print(f"unknown run target: {target}", file=sys.stderr)
        return 1

    if cmd == "status":
        return cmd_status()

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
