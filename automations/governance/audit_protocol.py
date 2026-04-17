#!/usr/bin/env python3
"""
Toke — Unified Governance Audit Protocol
=========================================
Aggregates ALL Toke telemetry into a single auditable event stream.
Maps to OWASP Agentic AI Top 10 (ASI01-ASI10) for risk flagging.

Commands:
    python audit_protocol.py report              Weekly governance report
    python audit_protocol.py events --days 7     Raw unified event stream
    python audit_protocol.py risks               Risk flags only
    python audit_protocol.py sacred-rules        Sacred Rule compliance summary
    python audit_protocol.py --json              Machine-readable output
    python audit_protocol.py --days N            Last N days (default 30)

Data sources:
    ~/.claude/telemetry/brain/decisions.jsonl     Routing decisions
    ~/.claude/telemetry/brain/tools.jsonl         Tool calls (PostToolUse)
    ~/.claude/telemetry/brain/advisor_calls.jsonl Advisor escalations
    Homer VAULT checkpoints                       State snapshots

Dependencies: Python 3.11+ stdlib only.
Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, Phase 2C.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

TELEMETRY_DIR = Path.home() / ".claude" / "telemetry" / "brain"
DECISIONS_LOG = TELEMETRY_DIR / "decisions.jsonl"
TOOLS_LOG = TELEMETRY_DIR / "tools.jsonl"
ADVISOR_LOG = TELEMETRY_DIR / "advisor_calls.jsonl"
VAULT_DIR = Path(__file__).parent.parent / "homer" / "vault" / "state"


# =============================================================================
# Risk detection patterns (OWASP Agentic AI Top 10)
# =============================================================================

# ASI01: Goal hijack / prompt injection
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions?|rules?)", re.I),
    re.compile(r"new\s+instructions?:", re.I),
    re.compile(r"you\s+are\s+now\s+a", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"<\s*/?system", re.I),
    re.compile(r"disregard\s+(the\s+)?(above|previous)", re.I),
    re.compile(r"forget\s+(everything|all|your\s+instructions)", re.I),
]

# ASI02: Tool misuse — destructive commands
DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bgit\s+(reset\s+--hard|push\s+--force|clean\s+-f)", re.I),
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.I),
    re.compile(r"\bDELETE\s+FROM\s+\w+\s*(;|$)", re.I),
    re.compile(r"\bformat\s+[cCdD]:", re.I),
    re.compile(r"\bdel\s+/[sfqSFQ]", re.I),
]

# ASI03: Privilege abuse — paths outside expected scope
SENSITIVE_PATHS = [
    re.compile(r"(/etc/passwd|/etc/shadow|/etc/hosts)", re.I),
    re.compile(r"(\.env|credentials\.json|\.aws/credentials)", re.I),
    re.compile(r"(id_rsa|id_ed25519|\.ssh/)", re.I),
    re.compile(r"(settings\.json|secrets\.)", re.I),
]

# ASI05: Code execution via tool input
SHELL_INJECTION_PATTERNS = [
    re.compile(r"[;&|]\s*(rm|del|format|curl|wget)\b", re.I),
    re.compile(r"\$\(.*\)", re.I),
    re.compile(r"`[^`]+`"),
]


# =============================================================================
# Data loading
# =============================================================================


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    return entries


def _parse_ts(ts_str: str) -> datetime.datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _filter_days(entries: list[dict], days: int) -> list[dict]:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return [e for e in entries if (_parse_ts(e.get("ts", "")) or cutoff) >= cutoff]


# =============================================================================
# Risk flag detection
# =============================================================================


def detect_risks(text: str, context: str = "") -> list[dict[str, str]]:
    """Scan text for risk patterns. Returns list of {owasp_code, flag, match}."""
    flags: list[dict[str, str]] = []
    combined = f"{text} {context}"

    for pattern in INJECTION_PATTERNS:
        m = pattern.search(combined)
        if m:
            flags.append({"owasp": "ASI01", "flag": "goal_hijack", "match": m.group()})

    for pattern in DESTRUCTIVE_PATTERNS:
        m = pattern.search(combined)
        if m:
            flags.append({"owasp": "ASI02", "flag": "tool_misuse", "match": m.group()})

    for pattern in SENSITIVE_PATHS:
        m = pattern.search(combined)
        if m:
            flags.append({"owasp": "ASI03", "flag": "privilege_abuse", "match": m.group()})

    for pattern in SHELL_INJECTION_PATTERNS:
        m = pattern.search(combined)
        if m:
            flags.append({"owasp": "ASI05", "flag": "code_execution", "match": m.group()})

    return flags


# =============================================================================
# Unified event stream
# =============================================================================


def build_unified_events(days: int = 30) -> list[dict[str, Any]]:
    """Merge all telemetry sources into a single chronological event stream."""
    events: list[dict] = []

    # Decisions
    for d in _filter_days(_read_jsonl(DECISIONS_LOG), days):
        result = d.get("result", {})
        prompt_text = ""  # prompt text not stored in decisions.jsonl (privacy)
        risks = detect_risks(str(result))
        events.append({
            "ts": d.get("ts", ""),
            "session_id": d.get("session_id", ""),
            "event_type": "routing_decision",
            "agent": "brain",
            "action": "classify",
            "detail": {
                "tier": result.get("tier", "?") if isinstance(result, dict) else "?",
                "model": result.get("model", "?") if isinstance(result, dict) else "?",
                "confidence": result.get("confidence", 0) if isinstance(result, dict) else 0,
                "guardrails_fired": result.get("guardrails_fired", []) if isinstance(result, dict) else [],
            },
            "human": d.get("human", {}),
            "risk_flags": risks,
            "outcome": "allowed",
        })

    # Tool calls
    for t in _filter_days(_read_jsonl(TOOLS_LOG), days):
        tool_name = t.get("tool_name", "")
        tool_input = str(t.get("tool_input", ""))
        risks = detect_risks(tool_input)

        # Cascade detection: check if this is part of a burst of writes to same file
        events.append({
            "ts": t.get("ts", ""),
            "session_id": t.get("session_id", ""),
            "event_type": "tool_call",
            "agent": "godspeed",
            "action": f"invoke_{tool_name}",
            "detail": {
                "tool_name": tool_name,
                "tool_input_length": len(tool_input),
            },
            "risk_flags": risks,
            "outcome": "allowed",
        })

    # Advisor escalations
    for a in _filter_days(_read_jsonl(ADVISOR_LOG), days):
        events.append({
            "ts": a.get("ts", ""),
            "session_id": a.get("session_id", ""),
            "event_type": "advisor_escalation",
            "agent": "sybil",
            "action": "escalate",
            "detail": {
                "executor_model": a.get("executor_model", ""),
                "advisor_model": a.get("advisor_model", ""),
                "advisor_uses": a.get("advisor_uses", 0),
            },
            "risk_flags": [],
            "outcome": "allowed",
        })

    # Sort chronologically
    events.sort(key=lambda e: e.get("ts", ""))
    return events


# =============================================================================
# Sacred Rule compliance audit
# =============================================================================

SACRED_RULES = {
    1: "Always truthful",
    2: "Never delete/overwrite without consent",
    3: "Never revert confirmed fixes",
    4: "Only change what's asked",
    5: "Debug diagnostics are features",
    6: "Never write lore/creative without greenlight",
    7: "Edit only on existing files",
    8: "Never auto-close session",
    9: "Auto-choose AAA — no options",
    10: "godspeed = immediate invoke",
    11: "AAA quality always",
    12: "Push back on bad ideas",
    13: "Research before implementing",
}


def audit_sacred_rules(events: list[dict]) -> dict[str, Any]:
    """Check events for Sacred Rule violation signals."""
    violations: list[dict] = []
    rule_clear: dict[int, int] = {r: 0 for r in SACRED_RULES}

    for e in events:
        risks = e.get("risk_flags", [])
        for risk in risks:
            if risk.get("flag") == "tool_misuse":
                violations.append({
                    "rule": 2,
                    "event_ts": e.get("ts"),
                    "description": f"Destructive tool pattern detected: {risk.get('match', '?')}",
                    "severity": "HIGH",
                })
            elif risk.get("flag") == "goal_hijack":
                violations.append({
                    "rule": 4,
                    "event_ts": e.get("ts"),
                    "description": f"Potential prompt injection: {risk.get('match', '?')}",
                    "severity": "MEDIUM",
                })

        # Clear rules that have evidence of compliance
        if e.get("event_type") == "routing_decision":
            detail = e.get("detail", {})
            if detail.get("guardrails_fired"):
                rule_clear[11] += 1  # guardrails firing = quality enforcement active
            rule_clear[9] += 1  # every decision = auto-routing active

    compliant_count = sum(1 for r in SACRED_RULES if r not in {v["rule"] for v in violations})
    return {
        "total_rules": len(SACRED_RULES),
        "compliant": compliant_count,
        "violations": violations,
        "compliance_rate": round(compliant_count / len(SACRED_RULES) * 100, 1),
    }


# =============================================================================
# Commands
# =============================================================================


def cmd_report(days: int = 30, as_json: bool = False) -> dict:
    """Weekly governance report."""
    events = build_unified_events(days)
    total = len(events)

    # Count by type
    by_type: dict[str, int] = defaultdict(int)
    for e in events:
        by_type[e["event_type"]] += 1

    # Risk summary
    all_risks: list[dict] = []
    risk_by_owasp: dict[str, int] = defaultdict(int)
    for e in events:
        for r in e.get("risk_flags", []):
            all_risks.append({**r, "ts": e.get("ts"), "event_type": e.get("event_type")})
            risk_by_owasp[r.get("owasp", "?")] += 1

    # Sacred rules
    sacred = audit_sacred_rules(events)

    # Sessions
    sessions = set(e.get("session_id", "") for e in events if e.get("session_id"))

    report = {
        "period_days": days,
        "total_events": total,
        "events_by_type": dict(sorted(by_type.items())),
        "total_sessions": len(sessions),
        "risk_summary": {
            "total_flags": len(all_risks),
            "by_owasp_code": dict(sorted(risk_by_owasp.items())),
            "high_severity": [r for r in all_risks if r.get("flag") == "tool_misuse"],
        },
        "sacred_rule_compliance": sacred,
        "data_sources": {
            "decisions.jsonl": DECISIONS_LOG.exists(),
            "tools.jsonl": TOOLS_LOG.exists(),
            "advisor_calls.jsonl": ADVISOR_LOG.exists(),
        },
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"GOVERNANCE REPORT ({days}-day window)")
        print("=" * 60)
        print(f"Total auditable events: {total}")
        print(f"Sessions covered:       {len(sessions)}")
        print()
        print("Events by type:")
        for etype, count in sorted(by_type.items()):
            print(f"  {etype:<25} {count:>6}")
        print()
        print(f"Risk flags:             {len(all_risks)} total")
        if risk_by_owasp:
            for code, count in sorted(risk_by_owasp.items()):
                print(f"  {code}: {count} events")
        else:
            print("  None detected.")
        print()
        high_risks = report["risk_summary"]["high_severity"]
        if high_risks:
            print(f"HIGH SEVERITY RISKS:    {len(high_risks)}")
            for r in high_risks[:5]:
                print(f"  [{r.get('ts', '?')[:19]}] {r.get('flag')}: {r.get('match')}")
        print()
        print(f"Sacred Rule compliance: {sacred['compliant']}/{sacred['total_rules']}"
              f" ({sacred['compliance_rate']}%)")
        if sacred["violations"]:
            print("VIOLATIONS:")
            for v in sacred["violations"][:5]:
                print(f"  Rule #{v['rule']} [{v['severity']}]: {v['description']}")
        print()
        # Data source health. G13 fix 2026-04-17: advisor_calls.jsonl is NOT a bug
        # when absent — Sybil advisor has simply never been invoked in production.
        # The file is written on first `brain advise` call. Distinguish MISSING
        # (broken) from DORMANT (wired but never triggered, expected for advisor).
        dormant_ok = {"advisor_calls.jsonl"}
        print("Data sources:")
        for src, exists in report["data_sources"].items():
            if exists:
                status = "ACTIVE"
            elif src in dormant_ok:
                status = "DORMANT (expected — fires on first `brain advise`)"
            else:
                status = "MISSING"
            print(f"  {src:<30} {status}")
        print()

    return report


def cmd_events(days: int = 30, as_json: bool = False) -> list[dict]:
    """Raw unified event stream."""
    events = build_unified_events(days)
    if as_json:
        print(json.dumps(events, indent=2))
    else:
        for e in events[-50:]:  # last 50
            flags = " ".join(f"[{r['flag']}]" for r in e.get("risk_flags", []))
            print(
                f"{e.get('ts', '?')[:19]} "
                f"{e.get('event_type', '?'):<25} "
                f"{e.get('action', '?'):<20} "
                f"{flags}"
            )
        if len(events) > 50:
            print(f"\n... showing last 50 of {len(events)} events. Use --json for full dump.")
    return events


def cmd_risks(days: int = 30, as_json: bool = False) -> list[dict]:
    """Risk flags only."""
    events = build_unified_events(days)
    risky = [e for e in events if e.get("risk_flags")]

    if as_json:
        print(json.dumps(risky, indent=2))
    else:
        if not risky:
            print("No risk flags detected in the window.")
        else:
            print(f"RISK FLAGS ({len(risky)} events with flags)")
            print("=" * 60)
            for e in risky:
                for r in e.get("risk_flags", []):
                    print(
                        f"  [{e.get('ts', '?')[:19]}] "
                        f"{r.get('owasp', '?')} {r.get('flag')}: {r.get('match')}"
                    )
    return risky


def cmd_sacred(days: int = 30, as_json: bool = False) -> dict:
    """Sacred Rule compliance summary."""
    events = build_unified_events(days)
    sacred = audit_sacred_rules(events)

    if as_json:
        print(json.dumps(sacred, indent=2))
    else:
        print("SACRED RULE COMPLIANCE")
        print("=" * 60)
        violated_rules = {v["rule"] for v in sacred["violations"]}
        for num, desc in SACRED_RULES.items():
            status = "VIOLATION" if num in violated_rules else "CLEAR"
            print(f"  #{num:>2}: {desc:<45} {status}")
        print()
        print(f"Compliance: {sacred['compliant']}/{sacred['total_rules']} ({sacred['compliance_rate']}%)")
        if sacred["violations"]:
            print(f"\nViolation details:")
            for v in sacred["violations"]:
                print(f"  Rule #{v['rule']} [{v['severity']}] at {v['event_ts'][:19] if v.get('event_ts') else '?'}")
                print(f"    {v['description']}")
    return sacred


# =============================================================================
# CLI dispatch
# =============================================================================


def main() -> int:
    args = sys.argv[1:]

    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    days = 30
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                pass
    args = [a for a in args if a not in ("--days",)]
    # Remove the number after --days
    clean_args: list[str] = []
    skip_next = False
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a == "--days":
            skip_next = True
            continue
        if a == "--json":
            continue
        clean_args.append(a)

    command = clean_args[0] if clean_args else "report"

    commands = {
        "report": lambda: cmd_report(days, as_json),
        "events": lambda: cmd_events(days, as_json),
        "risks": lambda: cmd_risks(days, as_json),
        "sacred-rules": lambda: cmd_sacred(days, as_json),
    }

    if command in commands:
        commands[command]()
        return 0
    elif command in ("help", "--help", "-h"):
        print(__doc__)
        return 0
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Available: {', '.join(commands.keys())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
