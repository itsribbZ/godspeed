#!/usr/bin/env python3
"""
Homer — Unified CLI Workbench
==============================
Subcommands:
    homer init                    Bootstrap Homer: create state dir + first checkpoint
    homer status                  Show layer readiness + vault health + latest checkpoint
    homer effort                  Show current effort level (1-5 or auto)
    homer effort <1-5>            Set session-level effort override
    homer effort reset             Clear override, return to Brain auto-routing
    homer checkpoint list         List all checkpoints newest-first
    homer checkpoint latest       Dump latest checkpoint as JSON
    homer checkpoint read ID      Dump a specific checkpoint as JSON
    homer checkpoint archive      Archive stale checkpoints (>24h)
    homer vault health            VAULT diagnostic snapshot
    homer vault run TOPIC         Create a test checkpoint with given topic
    homer test                    Run all VAULT smoke tests
    homer help                    Show this help

All subcommands are stdlib-only. No external dependencies. Matches Brain v2.3 discipline.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

# Windows UTF-8 hardening
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HOMER_ROOT = Path(__file__).parent
VAULT_DIR = HOMER_ROOT / "vault"
ZEUS_DIR = HOMER_ROOT / "zeus"
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
EFFORT_STATE_FILE = Path.home() / ".claude" / "telemetry" / "brain" / "effort_override.txt"

sys.path.insert(0, str(VAULT_DIR))
from vault import VaultStore, STATE_DIR, ARCHIVE_DIR  # noqa: E402
from vault_db import VaultDB, replay as vault_replay, migrate_json_to_sqlite  # noqa: E402


def install_zeus_skill() -> tuple[bool, str]:
    """Copy Zeus SKILL.md into ~/.claude/skills/zeus/ so Claude Code can discover it.
    Returns (did_install, message). Pure add — never overwrites non-identical content."""
    src = ZEUS_DIR / "SKILL.md"
    if not src.exists():
        return False, f"source missing: {src}"
    dst_dir = CLAUDE_SKILLS_DIR / "zeus"
    dst = dst_dir / "SKILL.md"
    dst_dir.mkdir(parents=True, exist_ok=True)
    src_bytes = src.read_bytes()
    if dst.exists():
        if dst.read_bytes() == src_bytes:
            return False, f"already up-to-date: {dst}"
        # Non-identical — don't clobber silently. Write .new and report.
        side = dst_dir / "SKILL.md.new"
        side.write_bytes(src_bytes)
        return False, f"DIFFERS from installed — wrote {side} for review (Sacred Rule #2)"
    dst.write_bytes(src_bytes)
    return True, f"installed: {dst}"


# =============================================================================
# Layer readiness detection
# =============================================================================

LAYER_STATUS = {
    "L0 VAULT": {
        "path": VAULT_DIR / "vault.py",
        "expected_tag": "P0 SHIPPED",
        "phase": "P0",
    },
    "L1 BRAIN": {
        "path": HOMER_ROOT.parent / "brain" / "brain_cli.py",
        "expected_tag": "pre-existing",
        "phase": "v2.3",
    },
    "L2 ZEUS": {
        "path": ZEUS_DIR / "SKILL.md",
        "expected_tag": "P0 SHIPPED",
        "phase": "P0",
    },
    "L3 MUSES (3/3)": {
        "path": HOMER_ROOT / "muses",
        "expected_tag": "P1 SHIPPED",
        "phase": "P1",
    },
    "L4 SYBIL": {
        "path": HOMER_ROOT / "sybil" / "sybil.py",
        "expected_tag": "P1 SHIPPED",
        "phase": "P1",
    },
    "L5 MNEMOS": {
        "path": HOMER_ROOT / "mnemos" / "mnemos.py",
        "expected_tag": "P2 SHIPPED",
        "phase": "P2",
    },
    "L6 SLEEP-TIME": {
        "path": HOMER_ROOT / "sleep" / "sleep_cli.py",
        "expected_tag": "P3 SHIPPED",
        "phase": "P3",
    },
    "L7 ORACLE": {
        "path": HOMER_ROOT / "oracle" / "oracle.py",
        "expected_tag": "P3 SHIPPED",
        "phase": "P3",
    },
}


def check_layer(name: str, spec: dict) -> tuple[str, str]:
    """Return (status_emoji, detail_string) for a single layer."""
    path: Path = spec["path"]
    phase: str = spec["phase"]
    if path.exists():
        if path.is_file():
            size = path.stat().st_size
            return "SHIPPED", f"{phase} - {size:,} bytes"
        elif any(path.iterdir()):
            return "SHIPPED", f"{phase} - directory populated"
        else:
            return "PENDING", f"{phase} - dir exists, empty"
    return "PENDING", phase


# =============================================================================
# Subcommands
# =============================================================================


def cmd_init(args: list[str]) -> int:
    """Bootstrap Homer — create state dir + write first checkpoint + install Zeus skill."""
    print("=" * 60)
    print("Homer init")
    print("=" * 60)

    # Ensure directories exist
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  state dir:   {STATE_DIR}")
    print(f"  archive dir: {ARCHIVE_DIR}")

    # Install Zeus skill into Claude Code's discoverable skill dir
    installed, msg = install_zeus_skill()
    prefix = "  [Zeus]       installed:" if installed else "  [Zeus]       "
    print(f"{prefix} {msg}")

    # Write the bootstrap checkpoint
    store = VaultStore()
    session_id = args[0] if args else f"bootstrap_{secrets.token_hex(4)}"
    cp = store.create(
        topic="homer_bootstrap",
        session_id=session_id,
        phase="init",
        tasks=[
            {"id": 1, "priority": "P0", "status": "done", "description": "VAULT built + smoke tests green"},
            {"id": 2, "priority": "P0", "status": "done", "description": "Zeus SKILL.md shipped"},
            {"id": 3, "priority": "P0", "status": "done", "description": "Homer CLI operational"},
            {"id": 4, "priority": "P1", "status": "pending", "description": "MUSES worker pool (Calliope..Urania)"},
            {"id": 5, "priority": "P1", "status": "pending", "description": "SYBIL advisor escalation wiring"},
            {"id": 6, "priority": "P2", "status": "pending", "description": "MNEMOS three-tier memory"},
            {"id": 7, "priority": "P3", "status": "pending", "description": "ORACLE eval harness"},
            {"id": 8, "priority": "P3", "status": "pending", "description": "Sleep-time agents (Nyx / Hesper / Aurora)"},
        ],
        notes=(
            "Homer P0 bootstrap checkpoint. VAULT L0 + Zeus L2 shipped. "
            "All P1-P3 layers pending. No existing Toke files modified during P0. "
            "Theater kills from 2026-04-11 godspeed audit are parked pending per-item approval."
        ),
    )

    print(f"\n  First checkpoint written:")
    print(f"    id:      {cp.checkpoint_id}")
    print(f"    session: {cp.session_id}")
    print(f"    phase:   {cp.phase}")
    print(f"    path:    {STATE_DIR / (cp.checkpoint_id + '.json')}")

    # G12 fix 2026-04-17: Auto-migrate v1 JSON checkpoints to v2 SQLite on init.
    # Before this, v1/v2 coexisted without a sunset plan — `homer init` wrote v1,
    # migration was manual via `homer vault migrate`. Now init guarantees v2 is
    # caught up every bootstrap. Failures are logged but non-fatal (v1 is still
    # authoritative source; migration is additive enrichment).
    try:
        db = VaultDB()
        migrated = migrate_json_to_sqlite(STATE_DIR, db, ARCHIVE_DIR)
        health = db.health_report()
        if migrated > 0:
            print(f"\n  [VAULT v2]   auto-migrated {migrated} JSON checkpoint(s) to SQLite")
        else:
            print(f"\n  [VAULT v2]   already in sync ({health.get('workflow_count', 0)} workflows)")
    except Exception as exc:  # noqa: BLE001 — migration is non-fatal enrichment
        print(f"\n  [VAULT v2]   migration skipped ({type(exc).__name__}): {exc}")

    print("\nHomer is LIVE. Run 'homer status' to see layer readiness.")
    return 0


def cmd_status(args: list[str]) -> int:
    """Show Homer layer readiness + vault health."""
    print("=" * 60)
    print("Homer status")
    print("=" * 60)

    # Layer readiness table
    print("\nLAYER READINESS")
    print("-" * 60)
    shipped = 0
    total = len(LAYER_STATUS)
    for name, spec in LAYER_STATUS.items():
        status, detail = check_layer(name, spec)
        mark = "[SHIPPED]" if status == "SHIPPED" else "[PENDING]"
        print(f"  {mark}  {name:<15} {detail}")
        if status == "SHIPPED":
            shipped += 1
    print("-" * 60)
    print(f"  {shipped}/{total} layers shipped")

    # VAULT v1 health (JSON)
    print("\nVAULT v1 (JSON)")
    print("-" * 60)
    store = VaultStore()
    h = store.health_report()
    print(f"  live checkpoints:     {h['live_count']}")
    print(f"  archived checkpoints: {h['archived_count']}")
    print(f"  stale candidates:     {h['stale_candidates']}")
    print(f"  schema version:       {h['schema_version']}")

    # VAULT v2 health (SQLite)
    print("\nVAULT v2 (SQLite)")
    print("-" * 60)
    db = VaultDB()
    h2 = db.health_report()
    print(f"  workflows:            {h2['workflows']}")
    print(f"  steps:                {h2['steps']} ({h2['steps_done']} done, {h2['steps_failed']} failed)")
    print(f"  signals:              {h2['signals']}")
    print(f"  timers:               {h2['timers']}")
    print(f"  phases:               {h2['phase_counts'] or '(none)'}")
    print(f"  schema:               {h2['schema']}")

    if h2["latest_id"]:
        print(f"\n  Latest workflow:")
        print(f"    id:      {h2['latest_id']}")
        print(f"    topic:   {h2['latest_topic']}")
        print(f"    phase:   {h2['latest_phase']}")
    elif h["latest_id"]:
        print(f"\n  Latest checkpoint (v1):")
        print(f"    id:      {h['latest_id']}")
        print(f"    topic:   {h['latest_topic']}")
        print(f"    phase:   {h['latest_phase']}")
    else:
        print("\n  No checkpoints yet. Run 'homer init' to bootstrap.")

    return 0


def cmd_checkpoint(args: list[str]) -> int:
    """Checkpoint subcommands: list / latest / read / archive"""
    if not args:
        print("usage: homer checkpoint <list|latest|read|archive> [args]", file=sys.stderr)
        return 1

    sub = args[0]
    store = VaultStore()

    if sub == "list":
        include_archive = "--all" in args
        lst = store.list_all(include_archive=include_archive)
        if not lst:
            print("No checkpoints. Run 'homer init' to bootstrap.")
            return 0
        print(f"Homer checkpoints ({len(lst)} total{' incl. archive' if include_archive else ''})")
        print("-" * 72)
        for cp in lst:
            print(f"  {cp.checkpoint_id}")
            print(f"    phase={cp.phase}  topic={cp.topic}")
            print(f"    created={cp.created_at}  session={cp.session_id}")
        return 0

    if sub == "latest":
        cp = store.latest()
        if cp is None:
            print("No checkpoints.")
            return 0
        print(json.dumps(cp.to_dict(), indent=2))
        return 0

    if sub == "read":
        if len(args) < 2:
            print("usage: homer checkpoint read <checkpoint_id>", file=sys.stderr)
            return 1
        cp = store.read(args[1])
        if cp is None:
            print(f"Not found: {args[1]}", file=sys.stderr)
            return 1
        print(json.dumps(cp.to_dict(), indent=2))
        return 0

    if sub == "archive":
        n = store.archive_stale()
        print(f"Archived {n} stale checkpoint(s).")
        return 0

    print(f"unknown checkpoint subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_vault(args: list[str]) -> int:
    """Vault subcommands: health / run / v2 / query / migrate / replay"""
    if not args:
        print("usage: homer vault <health|run|v2|query|migrate|replay> [args]", file=sys.stderr)
        return 1

    sub = args[0]

    # --- v1 commands (JSON-backed) ---
    if sub == "health":
        store = VaultStore()
        v1 = store.health_report()
        db = VaultDB()
        v2 = db.health_report()
        print("VAULT v1 (JSON):")
        print(json.dumps(v1, indent=2))
        print("\nVAULT v2 (SQLite):")
        print(json.dumps(v2, indent=2))
        return 0

    if sub == "run":
        topic = args[1] if len(args) > 1 else "adhoc_run"
        session = args[2] if len(args) > 2 else f"run_{secrets.token_hex(4)}"
        store = VaultStore()
        cp = store.create(topic=topic, session_id=session)
        print(f"Created (v1 JSON): {cp.checkpoint_id}")
        return 0

    # --- v2 commands (SQLite-backed) ---
    if sub == "v2":
        db = VaultDB()
        print(json.dumps(db.health_report(), indent=2))
        return 0

    if sub == "query":
        db = VaultDB()
        # homer vault query [--phase PHASE] [--limit N]
        phase = None
        limit = 20
        i = 1
        while i < len(args):
            if args[i] == "--phase" and i + 1 < len(args):
                phase = args[i + 1]
                i += 2
            elif args[i] == "--limit" and i + 1 < len(args):
                limit = int(args[i + 1])
                i += 2
            else:
                i += 1
        workflows = db.list_workflows(phase=phase, limit=limit)
        if not workflows:
            print("No workflows found.")
            return 0
        print(f"{'ID':<55} {'Phase':<8} {'Topic':<30} {'Created'}")
        print("-" * 110)
        for wf in workflows:
            print(f"{wf['id']:<55} {wf['phase']:<8} {(wf['topic'] or '')[:30]:<30} {wf['created_at'][:19]}")
        print(f"\n{len(workflows)} workflow(s)")
        return 0

    if sub == "migrate":
        db = VaultDB()
        count = migrate_json_to_sqlite(STATE_DIR, db, ARCHIVE_DIR)
        print(f"Migrated {count} JSON checkpoints to SQLite")
        print(json.dumps(db.health_report(), indent=2))
        return 0

    if sub == "replay":
        wf_id = args[1] if len(args) > 1 else ""
        if not wf_id:
            print("usage: homer vault replay <workflow_id>", file=sys.stderr)
            return 1
        db = VaultDB()
        state = vault_replay(db, wf_id)
        print(json.dumps(state, indent=2, default=str))
        return 0

    print(f"unknown vault subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_test(args: list[str]) -> int:
    """Run all VAULT smoke tests (v1 + v2)."""
    sys.path.insert(0, str(VAULT_DIR))
    from test_vault import run_all as run_v1  # noqa: E402
    from test_vault_db import run_all as run_v2  # noqa: E402

    print("--- VAULT v1 tests ---")
    v1_exit = run_v1()  # returns int (0 = pass, 1 = fail)
    print()
    print("--- VAULT v2 tests ---")
    v2_pass, v2_fail = run_v2()  # returns (pass_count, fail_count)
    print()
    v2_exit = 1 if v2_fail > 0 else 0
    total_exit = max(v1_exit, v2_exit)
    print(f"COMBINED: v1={'PASS' if v1_exit == 0 else 'FAIL'} | v2={v2_pass} passed, {v2_fail} failed")
    return total_exit


def _load_effort_map() -> dict:
    """Load the [effort_map] section from the Brain routing manifest."""
    manifest_path = HOMER_ROOT / "brain" / "routing_manifest.toml"
    if not manifest_path.exists():
        manifest_path = HOMER_ROOT.parent / "brain" / "routing_manifest.toml"
    if not manifest_path.exists():
        # Fall back to brain dir at same level as homer
        manifest_path = HOMER_ROOT.parent / "brain" / "routing_manifest.toml"
    if not manifest_path.exists():
        return {}
    import tomllib
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    return manifest.get("effort_map", {})


def _read_effort() -> int | None:
    """Read current effort override. Returns 1-5 or None."""
    if not EFFORT_STATE_FILE.exists():
        return None
    try:
        val = EFFORT_STATE_FILE.read_text().strip()
        level = int(val)
        return level if 1 <= level <= 5 else None
    except (ValueError, OSError):
        return None


def cmd_effort(args: list[str]) -> int:
    """Set, query, or clear manual effort level.

    homer effort              Show current effort level
    homer effort <1-5>        Set session-level effort override
    homer effort reset         Clear override, return to Brain auto-routing
    """
    if not args:
        level = _read_effort()
        if level:
            effort_map = _load_effort_map()
            desc = effort_map.get(str(level), {}).get("description", "")
            layers = effort_map.get(str(level), {}).get("layers", [])
            print(f"Effort: {level} — {desc}")
            print(f"Layers: {', '.join(layers)}")
        else:
            print("Effort: auto (Brain routing)")
        return 0

    if args[0] == "reset":
        if EFFORT_STATE_FILE.exists():
            EFFORT_STATE_FILE.unlink()
            print("Effort override cleared. Brain auto-routing restored.")
        else:
            print("No effort override was set.")
        return 0

    try:
        level = int(args[0])
    except ValueError:
        print(f"Invalid effort level: {args[0]} (expected 1-5 or 'reset')", file=sys.stderr)
        return 2

    if level < 1 or level > 5:
        print(f"Effort level must be 1-5, got {level}", file=sys.stderr)
        return 2

    EFFORT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EFFORT_STATE_FILE.write_text(str(level))

    effort_map = _load_effort_map()
    cfg = effort_map.get(str(level), {})
    desc = cfg.get("description", "")
    layers = cfg.get("layers", [])
    model = cfg.get("model", "?")
    muse_count = cfg.get("muse_count", 0)
    oracle = cfg.get("oracle_mode", "off")
    thinking = cfg.get("extended_thinking_budget", 0)

    print(f"Effort set to {level}: {desc}")
    print(f"  Model: {model} | MUSES: {muse_count} | Oracle: {oracle} | Thinking: {thinking}")
    print(f"  Layers: {', '.join(layers)}")
    print("Active for this session. 'homer effort reset' to clear.")
    return 0


def cmd_help(args: list[str]) -> int:
    print(__doc__)
    return 0


COMMANDS = {
    "init": cmd_init,
    "status": cmd_status,
    "checkpoint": cmd_checkpoint,
    "vault": cmd_vault,
    "effort": cmd_effort,
    "test": cmd_test,
    "help": cmd_help,
    "-h": cmd_help,
    "--help": cmd_help,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return cmd_help([])
    cmd = argv[1]
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(f"run 'homer help' to see all commands", file=sys.stderr)
        return 1
    return COMMANDS[cmd](argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
