#!/usr/bin/env python3
"""
Zeus CLI — The One Command for Phases 4+5
==========================================

Homer's Zeus SKILL.md previously documented Phase 4 (Oracle) and Phase 5 (Mnemos)
as two separate Python CLI calls. That was fragile: the model could skip the
write, bypass the Oracle gate, or run them in the wrong order. The `gate_and_write()`
function enforced the gate in Python code, but nothing in the skill pipeline
actually invoked it.

This CLI collapses Phases 4+5 into one atomic command. Invoke it once per Zeus
synthesis, get back a structured GateResult as JSON, and you're done. If the
Oracle HARD_FAILs, nothing lands in Mnemos. If it PASSes, the Recall write fires
with the Oracle's verdict recorded alongside.

Commands:
    zeus classify TEXT                       Delegate to Brain L1 (phase 0)
    zeus gate-write --topic T --synthesis-file F --citations "a,b"
                                             Atomic Oracle→Mnemos gate (phases 4+5)
    zeus gate-write-stdin --topic T --citations "a,b"
                                             Same but synthesis from stdin
    zeus status                              Health check (dependencies wired?)
    zeus help                                Show this help

Exit codes:
     0   Success — Mnemos write landed (Oracle PASS or SOFT_FAIL)
     1   Oracle HARD_FAIL — nothing written
     2   Mnemos write error (citation, IO, schema)
     3   Input error (bad args, missing file)

JSON output contract (always printed to stdout — stderr is human-readable logs):
    {
      "written": bool,
      "verdict": "PASS" | "SOFT_FAIL" | "HARD_FAIL",
      "entry_id": "recall_..." | null,
      "reason": str,
      "score": float,
      "rule_failures": [str],
      "theater_flags": [str],
      "warning": str
    }

Design principles (match Brain + Mnemos + Oracle):
- Stdlib only
- Windows UTF-8 safe
- No side effects on --help or bad args (exit cleanly, print usage)
- Every successful gate-write prints a line-prefix to stderr for log scraping:
      "[zeus gate-write] verdict=PASS id=recall_... score=0.91"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

# Windows UTF-8 hardening — match the rest of Homer.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_HOMER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_HOMER_DIR / "oracle"))
sys.path.insert(0, str(_HOMER_DIR / "mnemos"))
sys.path.insert(0, str(_HOMER_DIR / "zeus"))

# Import after sys.path wiring. Lazy-importable so `zeus help` works even if deps move.
def _load_pipeline():
    from oracle import Oracle  # type: ignore
    from mnemos import MnemosStore, CitationError, validate_citation  # type: ignore
    from zeus_pipeline import gate_and_write, GateResult  # type: ignore
    return Oracle, MnemosStore, CitationError, gate_and_write, GateResult, validate_citation


BRAIN_CLI = _HOMER_DIR.parent / "brain" / "brain_cli.py"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_citations(raw: str) -> list[str]:
    """Split a comma-delimited citation string. Strips whitespace, drops empties."""
    return [c.strip() for c in raw.split(",") if c.strip()]


def _log(msg: str) -> None:
    print(f"[zeus] {msg}", file=sys.stderr)


def _emit_gate_result(result) -> None:
    """Print a GateResult dataclass as JSON on stdout, human summary on stderr."""
    payload = asdict(result)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _log(
        f"gate-write verdict={result.verdict} "
        f"id={result.entry_id or '-'} score={result.score:.3f}"
    )


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_classify(args: argparse.Namespace) -> int:
    """Delegate to Brain L1 for tier classification. Phase 0 of Zeus."""
    if not args.text:
        _log("error: classify requires TEXT")
        return 3
    prompt = " ".join(args.text)
    if not BRAIN_CLI.exists():
        _log(f"error: brain_cli.py not found at {BRAIN_CLI}")
        return 3
    # Run brain score as a subprocess so Zeus inherits Brain's full output unchanged.
    try:
        proc = subprocess.run(
            [sys.executable, str(BRAIN_CLI), "score", prompt],
            capture_output=True, text=True, encoding="utf-8",
        )
    except OSError as exc:
        _log(f"error: brain_cli invocation failed: {exc}")
        return 3
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def _build_store(args: argparse.Namespace):
    """Construct MnemosStore with optional test-isolation path overrides."""
    _O, MnemosStore, _CE, _GW, _GR, _VC = _load_pipeline()
    kwargs = {}
    if getattr(args, "core_file", None):
        kwargs["core_file"] = Path(args.core_file)
    if getattr(args, "recall_db", None):
        kwargs["recall_db"] = Path(args.recall_db)
    if getattr(args, "archival_dir", None):
        kwargs["archival_dir"] = Path(args.archival_dir)
    return MnemosStore(**kwargs)


def cmd_gate_write(args: argparse.Namespace) -> int:
    """Atomic Oracle → Mnemos gate. Phases 4 + 5 collapsed."""
    Oracle, MnemosStore, CitationError, gate_and_write, _, validate_citation = _load_pipeline()

    # 1. Resolve synthesis text (file OR stdin OR inline via args.synthesis).
    if args.stdin:
        synthesis = sys.stdin.read()
        if not synthesis.strip():
            _log("error: --stdin given but stdin was empty")
            return 3
    elif args.synthesis_file:
        path = Path(args.synthesis_file)
        if not path.exists():
            _log(f"error: synthesis file not found: {path}")
            return 3
        synthesis = path.read_text(encoding="utf-8")
    elif args.synthesis:
        synthesis = args.synthesis
    else:
        _log("error: one of --synthesis / --synthesis-file / --stdin required")
        return 3

    if not synthesis.strip():
        _log("error: synthesis text is empty")
        return 3

    # 2. Parse citations. Fail fast on empty — Mnemos rejects empty lists anyway.
    citations = _parse_citations(args.citations)
    if not citations:
        _log("error: --citations must include at least one non-empty citation")
        return 3

    # 2b. Pre-validate citations so rejections surface with a clean verdict BEFORE
    # the Oracle runs. Otherwise gate_and_write swallows the CitationError and
    # returns verdict="PASS" + written=False, which is confusing to parse.
    bad = [c for c in citations if not validate_citation(c)]
    if bad:
        payload = {
            "written": False,
            "verdict": "MNEMOS_CITATION_REJECTED",
            "entry_id": None,
            "reason": f"Invalid citation(s): {bad}. Accepted formats: file:line | https://... | "
                      f"arxiv:YYYY.NNNN | mnemos:archival_... | decisions:... | session:...",
            "score": 0.0,
            "rule_failures": [],
            "theater_flags": [],
            "warning": "",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _log(f"gate-write blocked: citation rejected — {bad}")
        return 2

    # 3. Optional Oracle context (e.g. baseline output for regression check).
    context = None
    if args.context_json:
        try:
            context = json.loads(args.context_json)
        except json.JSONDecodeError as exc:
            _log(f"error: --context-json not valid JSON: {exc}")
            return 3

    # 4. Run the gate. gate_and_write does: Oracle.score → verdict → Mnemos.write.
    oracle = Oracle()
    store = _build_store(args)

    try:
        result = gate_and_write(
            oracle=oracle,
            store=store,
            synthesis=synthesis,
            topic=args.topic,
            citations=citations,
            context=context,
        )
    except CitationError as exc:
        # Citation rejected BEFORE the gate ran — emit a GateResult-shaped error.
        payload = {
            "written": False,
            "verdict": "MNEMOS_CITATION_REJECTED",
            "entry_id": None,
            "reason": str(exc),
            "score": 0.0,
            "rule_failures": [],
            "theater_flags": [],
            "warning": "",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _log(f"gate-write blocked: citation rejected — {exc}")
        return 2

    _emit_gate_result(result)

    if result.verdict == "HARD_FAIL":
        return 1
    if not result.written:
        # PASS/SOFT_FAIL but write failed downstream (DB locked, schema mismatch…)
        return 2
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """One-screen dependency + wiring health check."""
    Oracle, _MS, _CE, _GW, _GR, _VC = _load_pipeline()
    store = _build_store(args)
    oracle = Oracle()
    health = store.health()
    payload = {
        "oracle_loaded": oracle is not None,
        "mnemos": health,
        "brain_cli_present": BRAIN_CLI.exists(),
        "zeus_pipeline_loaded": True,  # if we got here, gate_and_write imported
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _log("status OK" if payload["brain_cli_present"] else "status WARN — brain_cli missing")
    return 0


# ── Argument parser ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zeus",
        description="Zeus CLI — atomic Oracle→Mnemos gate for Homer orchestration.",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_classify = sub.add_parser("classify", help="Delegate to Brain L1 for tier classification")
    p_classify.add_argument("text", nargs="+", help="Prompt text to classify")
    p_classify.set_defaults(func=cmd_classify)

    p_gate = sub.add_parser("gate-write", help="Atomic Oracle→Mnemos gate (phases 4+5)")
    p_gate.add_argument("--topic", required=True, help="Short topic label for the Recall entry")
    p_gate.add_argument("--citations", required=True,
                        help="Comma-delimited citation list (file:line, URL, session:id, etc.)")
    source = p_gate.add_mutually_exclusive_group(required=False)
    source.add_argument("--synthesis-file", help="Path to synthesis text file")
    source.add_argument("--synthesis", help="Inline synthesis text (use --synthesis-file for large inputs)")
    source.add_argument("--stdin", action="store_true", help="Read synthesis from stdin")
    p_gate.add_argument("--context-json", help="Optional JSON context for Oracle.score()")
    _add_mnemos_path_overrides(p_gate)
    p_gate.set_defaults(func=cmd_gate_write)

    # Convenience alias for shell piping — `echo ... | zeus gate-write-stdin --topic ... --citations ...`
    p_gate_stdin = sub.add_parser("gate-write-stdin",
                                  help="Alias for `gate-write --stdin` (synthesis from stdin)")
    p_gate_stdin.add_argument("--topic", required=True)
    p_gate_stdin.add_argument("--citations", required=True)
    p_gate_stdin.add_argument("--context-json")
    _add_mnemos_path_overrides(p_gate_stdin)
    p_gate_stdin.set_defaults(func=cmd_gate_write, stdin=True,
                              synthesis=None, synthesis_file=None)

    p_status = sub.add_parser("status", help="Health check of Zeus dependencies")
    _add_mnemos_path_overrides(p_status)
    p_status.set_defaults(func=cmd_status)

    return parser


def _add_mnemos_path_overrides(p: argparse.ArgumentParser) -> None:
    """Optional path overrides for test isolation. Default paths point at production."""
    p.add_argument("--core-file", help="Override path to Mnemos core file (test isolation)")
    p.add_argument("--recall-db", help="Override path to Mnemos recall.db (test isolation)")
    p.add_argument("--archival-dir", help="Override path to Mnemos archival dir (test isolation)")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("help", "--help", "-h"):
        _build_parser().print_help()
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Ensure defaults exist on the parsed namespace for gate-write branches.
    for key in ("stdin", "synthesis", "synthesis_file", "context_json"):
        if not hasattr(args, key):
            setattr(args, key, None if key != "stdin" else False)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
