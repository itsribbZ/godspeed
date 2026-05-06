#!/usr/bin/env python3
"""
Homer — agent_runner.py
========================
Invocation engine for real agents (subagent personas) across Toke divisions.

Each agent is a JSON spec at `automations/director/agents/<name>.json` defining:
- name, division, role, model
- system_prompt (the persona)
- tool_grants (Claude Code tools allowed)
- skill_wrappers (existing skills loaded as context)
- trigger_signals (Director sub-dispatch lexicon)
- success_metrics (per-agent KPIs)

Three invocation modes:
1. **dry-run** — builds the full payload, returns structured mock response.
   No API calls. Validates schema + plumbing.
2. **live** — calls Anthropic SDK directly with persona system_prompt + task.
   Falls back to dry-run on credit errors / missing key. Telemetry captures
   actual tokens.
3. **claude-code** — emits a structured dispatch payload for the calling Claude
   Code session to relay via the Agent tool (subagent_type=general-purpose,
   passing the persona prompt as the agent prompt). Zero direct API cost
   because session-cache amortizes.

Telemetry: every invocation appends one JSONL line to
`~/.claude/telemetry/brain/agent_invocations.jsonl`. Per-agent learnings
land in `agents/_learnings/<name>.md` via shell-append (never bulk-rewrite).

CLI:
    agent_runner.py list                              # all agents
    agent_runner.py info <name>                       # agent details
    agent_runner.py invoke <name> --task "..." [--mode dry-run|live|claude-code]
    agent_runner.py validate                          # schema check all agents
    agent_runner.py telemetry [--agent <name>] [--last N]
    agent_runner.py for-division <division>           # list division agents

Sacred Rule alignment:
- Rule 2: read-only on agent specs; only writes to telemetry + _learnings
- Rule 4: agents are scoped to their charter; no cascading edits
- Rule 5: telemetry is diagnostic — never delete
- Rule 6: persona system_prompts are non-creative declarative roles
- Rule 11: AAA — every agent has measurable success_metrics, validated schema
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

THIS_FILE = Path(__file__).resolve()
HOMER_DIR = THIS_FILE.parent
AUTOMATIONS_DIR = HOMER_DIR.parent
TOKE_DIR = AUTOMATIONS_DIR.parent

# Phase 3i (2026-05-04) — cost / efficiency guard. Sibling module under homer/.
sys.path.insert(0, str(HOMER_DIR))
import cost_guard  # type: ignore  # noqa: E402

DIRECTOR_DIR = AUTOMATIONS_DIR / "director"
AGENTS_DIR = DIRECTOR_DIR / "agents"
AGENTS_MANIFEST = DIRECTOR_DIR / "agents_manifest.json"
LEARNINGS_DIR = AGENTS_DIR / "_learnings"

CLAUDE_HOME = Path.home() / ".claude"
TELEMETRY = CLAUDE_HOME / "telemetry" / "brain" / "agent_invocations.jsonl"

REQUIRED_FIELDS = (
    "name", "division", "role", "model",
    "system_prompt", "tool_grants", "skill_wrappers",
    "trigger_signals", "success_metrics", "_learnings_path", "version",
)
VALID_MODELS = {"haiku", "sonnet", "opus"}
VALID_MODES = {"dry-run", "live", "claude-code"}

# Per Toke/tokens/PRICING_NOTES.md (verified 2026-04-11 against Anthropic docs).
# USD per million tokens — long-context tier matches standard rate per Anthropic
# long-context pricing (1M ctx Opus 4.6 / Sonnet 4.6 = same per-token rate).
MODEL_PRICING = {
    "haiku":  {"input": 1.00, "output": 5.00,  "cache_read": 0.10},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m": 3.75, "cache_write_1h": 6.00},
    "opus":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m": 6.25, "cache_write_1h": 10.00},
}


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
                 cache_ttl: str = "5m") -> dict:
    """
    Compute USD cost for an invocation. Returns four components plus total.
    Unknown model → all zeros (logged but doesn't block telemetry write).

    cache_creation_tokens — tokens written to cache on this call (5m or 1h tier).
    cache_ttl — "5m" or "1h". Phase 3f default is 5m (1.25x base, pays off after 1 read).
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return {
            "input_cost": 0.0, "output_cost": 0.0,
            "cache_read_cost": 0.0, "cache_write_cost": 0.0, "total_cost": 0.0,
        }
    input_cost  = (input_tokens  / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    cache_read_cost = (cache_read_tokens / 1_000_000) * pricing["cache_read"]
    write_rate_key = "cache_write_1h" if cache_ttl == "1h" else "cache_write_5m"
    cache_write_cost = (cache_creation_tokens / 1_000_000) * pricing.get(write_rate_key, pricing["input"] * 1.25)
    return {
        "input_cost":       round(input_cost, 6),
        "output_cost":      round(output_cost, 6),
        "cache_read_cost":  round(cache_read_cost, 6),
        "cache_write_cost": round(cache_write_cost, 6),
        "total_cost":       round(input_cost + output_cost + cache_read_cost + cache_write_cost, 6),
    }


# === Spec loading ==================================================================


@dataclass
class AgentSpec:
    name: str
    division: str
    role: str
    model: str
    version: str
    system_prompt: str
    tool_grants: list[str] = field(default_factory=list)
    skill_wrappers: list[str] = field(default_factory=list)
    trigger_signals: list[dict] = field(default_factory=list)
    success_metrics: dict = field(default_factory=dict)
    learnings_path: str = ""
    anti_signals: list[dict] = field(default_factory=list)
    sacred_rule_overrides: list[str] = field(default_factory=list)
    output_contract: dict = field(default_factory=dict)
    parent_skill: str = ""
    max_thinking_budget: int = 0
    tool_result_truncation_chars: int = 8000
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d

    def trigger_lexicon(self) -> list[str]:
        return [s.get("phrase", "") for s in self.trigger_signals if s.get("phrase")]


def load_agent(name: str) -> AgentSpec:
    """Load agents/<name>.json into AgentSpec. Raises FileNotFoundError if missing."""
    path = AGENTS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Agent spec not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AgentSpec(
        name=raw["name"],
        division=raw["division"],
        role=raw["role"],
        model=raw["model"],
        version=raw.get("version", "1.0"),
        system_prompt=raw["system_prompt"],
        tool_grants=list(raw.get("tool_grants", [])),
        skill_wrappers=list(raw.get("skill_wrappers", [])),
        trigger_signals=list(raw.get("trigger_signals", [])),
        success_metrics=dict(raw.get("success_metrics", {})),
        learnings_path=raw.get("_learnings_path", ""),
        anti_signals=list(raw.get("anti_signals", [])),
        sacred_rule_overrides=list(raw.get("sacred_rule_overrides", [])),
        output_contract=dict(raw.get("output_contract", {})),
        parent_skill=raw.get("parent_skill", ""),
        max_thinking_budget=int(raw.get("max_thinking_budget", 0)),
        tool_result_truncation_chars=int(raw.get("tool_result_truncation_chars", 8000)),
        raw=raw,
    )


def list_agents() -> list[str]:
    """Return list of registered agent names from agents/ directory."""
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.stem for p in AGENTS_DIR.glob("*.json") if not p.stem.startswith("_"))


def list_agents_in_division(division: str) -> list[str]:
    out: list[str] = []
    for name in list_agents():
        try:
            spec = load_agent(name)
            if spec.division == division:
                out.append(name)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            continue
    return out


# === Validation ====================================================================


def validate_agent(name: str) -> tuple[bool, list[str]]:
    """Return (ok, errors) for a single agent."""
    errors: list[str] = []
    try:
        spec_path = AGENTS_DIR / f"{name}.json"
        if not spec_path.exists():
            return False, [f"file missing: {spec_path}"]
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, [f"invalid JSON: {e}"]

    for k in REQUIRED_FIELDS:
        if k not in raw:
            errors.append(f"missing required field: {k}")

    if "model" in raw and raw["model"] not in VALID_MODELS:
        errors.append(f"invalid model: {raw['model']} (must be {VALID_MODELS})")

    if "system_prompt" in raw and len(raw["system_prompt"]) < 200:
        errors.append(f"system_prompt too short ({len(raw['system_prompt'])} chars, need ≥200)")

    if "trigger_signals" in raw:
        if not isinstance(raw["trigger_signals"], list) or len(raw["trigger_signals"]) < 3:
            errors.append(f"need ≥3 trigger_signals (got {len(raw.get('trigger_signals', []))})")
        for s in raw.get("trigger_signals", []):
            if not isinstance(s, dict) or "phrase" not in s or "weight" not in s:
                errors.append(f"trigger_signal malformed: {s}")
                break

    if "success_metrics" in raw and not isinstance(raw["success_metrics"], dict):
        errors.append("success_metrics must be object")

    return len(errors) == 0, errors


def validate_all() -> dict:
    out = {"total": 0, "valid": 0, "invalid": 0, "errors": {}}
    for name in list_agents():
        out["total"] += 1
        ok, errs = validate_agent(name)
        if ok:
            out["valid"] += 1
        else:
            out["invalid"] += 1
            out["errors"][name] = errs
    return out


# === Invocation payload ============================================================


def _hash_task(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]


def build_invocation_payload(spec: AgentSpec, task: str, tier: str | None = None) -> dict:
    """
    Compose the invocation request — system prompt + user task + structured
    metadata. Used by all three invocation modes.

    Phase 3i: tier (Brain S0-S5) + budget_usd (cost ceiling) ride along on the
    payload so invoke_live can enforce mid-flight breach checks and route_full
    can stamp the cost contract on the dispatch envelope. Default tier is
    inferred from spec.model when caller doesn't supply one.
    """
    resolved_tier = (tier or cost_guard.tier_for_model(spec.model)).upper()
    return {
        "agent": spec.name,
        "division": spec.division,
        "model": spec.model,
        "system_prompt": spec.system_prompt,
        "user_task": task,
        "tool_grants": spec.tool_grants,
        "skill_wrappers": spec.skill_wrappers,
        "max_thinking_budget": spec.max_thinking_budget,
        "tool_result_truncation_chars": spec.tool_result_truncation_chars,
        "task_hash": _hash_task(task),
        "tier": resolved_tier,
        "budget_usd": cost_guard.budget_for_tier(resolved_tier),
    }


# === Mode dispatchers ==============================================================


def invoke_dry_run(payload: dict) -> dict:
    """No external call. Validates plumbing + returns mock structured response."""
    return {
        "mode": "dry-run",
        "agent": payload["agent"],
        "model": payload["model"],
        "verdict": "DRY_RUN",
        "response_text": (
            f"[DRY-RUN] Agent '{payload['agent']}' would have processed task "
            f"(hash={payload['task_hash']}, model={payload['model']}). "
            f"System prompt length: {len(payload['system_prompt'])} chars. "
            f"Tool grants: {payload['tool_grants']}. "
            f"Skill wrappers: {payload['skill_wrappers']}."
        ),
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": 0,
        "success": True,
    }


# === Tool execution for LIVE mode (Phase 3e) =======================================
#
# Maps agent.tool_grants → Anthropic API tools=[]. Server-side tools (web_search,
# web_fetch) execute on Anthropic's side. Client-side tools (bash, read, grep,
# glob) execute here. Edit / Write / Agent are HARD-DENIED in LIVE mode to keep
# Sacred Rule #2 (no delete/overwrite without consent) binding even when an agent
# is autonomously looping.

import re as _re
import shlex as _shlex  # noqa: F401 — reserved for future arg-parse

# Forbidden command-name list (checked against first whitespace-separated token).
_BASH_FORBIDDEN_FIRST_WORDS = {
    "rm", "rmdir", "mv", "dd", "mkfs", "format",
    "shutdown", "reboot", "sudo", "halt", "poweroff",
    "kill", "pkill", "killall",
}
# Forbidden embedded patterns (substring match — covers shell-injection vectors).
_BASH_FORBIDDEN_SUBSTRINGS = (
    "| bash", "| sh", "/dev/sda", "/dev/sdb", "/dev/null/",
    ":(){ :|:&",  # fork bomb
)
_BASH_REDIRECT = _re.compile(r"(?<![0-9&])>\s*\S")  # `> file` but allows `2>&1`


def _bash_is_safe(cmd: str) -> tuple[bool, str]:
    cl = cmd.strip().lower()
    if not cl:
        return False, "empty command"
    first = cl.split()[0]
    if first in _BASH_FORBIDDEN_FIRST_WORDS:
        return False, f"forbidden first command '{first}' (Sacred Rule #2)"
    for sub in _BASH_FORBIDDEN_SUBSTRINGS:
        if sub in cl:
            return False, f"forbidden pattern '{sub.strip()}' (Sacred Rule #2)"
    if _BASH_REDIRECT.search(cmd) and "/tmp/" not in cmd:
        return False, "file redirect outside /tmp blocked (Sacred Rule #2)"
    return True, ""


def _build_tools_from_grants(grants: list[str]) -> list[dict]:
    """Map agent tool_grants → Anthropic API tool definitions."""
    out: list[dict] = []
    seen: set[str] = set()
    g = {x.lower() for x in (grants or [])}

    # Server-side (Anthropic-hosted)
    if ("websearch" in g or "web_search" in g) and "web_search" not in seen:
        out.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5})
        seen.add("web_search")
    if ("webfetch" in g or "web_fetch" in g) and "web_fetch" not in seen:
        out.append({"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5})
        seen.add("web_fetch")

    # Client-side (we execute below in _execute_local_tool)
    if "bash" in g and "bash" not in seen:
        out.append({
            "name": "bash",
            "description": (
                "Execute a bash command. READ-ONLY operations only — destructive "
                "patterns (rm, mv, dd, mkfs, shutdown, sudo, redirect outside /tmp, "
                "pipe-to-shell) are blocked at the tool layer per Sacred Rule #2. "
                "30-second timeout per call. Use for `wc`, `stat`, `ls`, `cat`, "
                "`head`, `tail`, `grep`, `find`, `python -c`, `jq`, `git log`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command"}},
                "required": ["command"],
            },
        })
        seen.add("bash")
    if "read" in g and "read" not in seen:
        out.append({
            "name": "read",
            "description": "Read a file from disk. Returns up to 'limit' lines from 'offset'. Use for inspecting source files, JSON, JSONL, markdown.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": 200},
                },
                "required": ["file_path"],
            },
        })
        seen.add("read")
    if "grep" in g and "grep" not in seen:
        out.append({
            "name": "grep",
            "description": "Search file contents for a regex pattern across a directory tree. Returns up to 100 matches as 'path:line: text'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex"},
                    "path": {"type": "string", "default": ".", "description": "Root dir or file"},
                    "glob": {"type": "string", "description": "Optional glob filter, e.g. '*.py'"},
                },
                "required": ["pattern"],
            },
        })
        seen.add("grep")
    if "glob" in g and "glob" not in seen:
        out.append({
            "name": "glob",
            "description": "List files matching a glob pattern. Returns up to 100 paths.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
        })
        seen.add("glob")
    # Edit / Write / Agent are intentionally not added — hard-denied in LIVE mode.

    return out


def _run_bash(cmd: str, timeout: int = 30):
    """
    Run a bash command, preferring real bash (Git Bash on Windows / system bash on POSIX)
    so `~`, glob, and POSIX paths expand correctly. Falls back to shell=True only if
    no bash binary is found. Critical: subprocess(shell=True) on Windows uses cmd.exe,
    which does NOT expand `~/.claude/...` and breaks every agent that uses POSIX paths.
    """
    import subprocess as _sp
    for bash_bin in ("bash", "/usr/bin/bash", "C:/Program Files/Git/bin/bash.exe"):
        try:
            return _sp.run([bash_bin, "-c", cmd], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
    # Last resort — POSIX systems where direct shell=True is fine
    return _sp.run(cmd, shell=True, capture_output=True, text=True,
                   timeout=timeout, encoding="utf-8", errors="replace")


def _execute_local_tool(name: str, params: dict) -> tuple[str, bool]:
    """Run a client-side tool locally. Returns (result_text, is_error)."""
    import subprocess as _sp
    try:
        n = (name or "").lower()
        if n == "bash":
            cmd = (params or {}).get("command", "")
            ok, why = _bash_is_safe(cmd)
            if not ok:
                return f"REFUSED: {why}\ncommand was: {cmd[:200]}", True
            res = _run_bash(cmd, timeout=30)
            stdout = (res.stdout or "")[:6000]
            stderr = (res.stderr or "")[:1000]
            tail = f"\n[STDERR truncated]\n{stderr}" if stderr else ""
            return f"exit_code={res.returncode}\n{stdout}{tail}", res.returncode != 0
        if n == "read":
            from pathlib import Path as _P
            path = _P((params or {}).get("file_path", "")).expanduser()
            off = int((params or {}).get("offset", 0))
            lim = int((params or {}).get("limit", 200))
            if not path.exists():
                return f"FILE_NOT_FOUND: {path}", True
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
            sliced = lines[off:off + lim]
            numbered = "\n".join(f"{off + i + 1:>5d}\t{l}" for i, l in enumerate(sliced))
            head = f"file: {path}\nlines {off + 1}-{off + len(sliced)} of {len(lines)}\n"
            return (head + numbered)[:8000], False
        if n == "grep":
            from pathlib import Path as _P
            pattern = (params or {}).get("pattern", "")
            path = _P((params or {}).get("path", ".")).expanduser()
            glob_pat = (params or {}).get("glob", "") or ""
            try:
                rx = _re.compile(pattern)
            except _re.error as e:
                return f"REGEX_ERROR: {e}", True
            hits: list[str] = []
            files_iter = path.rglob(glob_pat) if glob_pat else (path.rglob("*") if path.is_dir() else [path])
            for p in files_iter:
                if not p.is_file():
                    continue
                try:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
                        if rx.search(line):
                            hits.append(f"{p}:{i}: {line[:200]}")
                            if len(hits) >= 100:
                                break
                except (OSError, UnicodeDecodeError):
                    continue
                if len(hits) >= 100:
                    break
            return ("\n".join(hits) if hits else "(no matches)")[:8000], False
        if n == "glob":
            from pathlib import Path as _P
            pattern = (params or {}).get("pattern", "")
            path = _P((params or {}).get("path", ".")).expanduser()
            try:
                matches = sorted(str(p) for p in path.rglob(pattern))[:100]
            except OSError as e:
                return f"GLOB_ERROR: {e}", True
            return ("\n".join(matches) if matches else "(no matches)"), False
        if n in ("edit", "write", "agent"):
            return f"DENIED: tool '{n}' is not permitted in LIVE mode (Sacred Rule #2 — never delete/overwrite without consent). Use claude-code dispatch mode for write operations.", True
        return f"UNKNOWN_TOOL: {n}", True
    except _sp.TimeoutExpired:
        return f"TIMEOUT: tool '{name}' exceeded 30s", True
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}", True


def invoke_live(payload: dict, max_iterations: int = 12) -> dict:
    """
    Live Anthropic API call with tool-use loop (Phase 3e).
    Server-side tools (web_search, web_fetch) execute on Anthropic side.
    Client-side tools (bash, read, grep, glob) execute here.
    Edit / Write / Agent are hard-denied per Sacred Rule #2.

    Degrades to dry-run on credit error / missing key. Token usage and cost
    accumulate across all loop iterations.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        out = invoke_dry_run(payload)
        out["mode"] = "live-fallback-dry"
        out["fallback_reason"] = "ANTHROPIC_API_KEY not set"
        return out

    try:
        import anthropic  # type: ignore
    except ImportError:
        out = invoke_dry_run(payload)
        out["mode"] = "live-fallback-dry"
        out["fallback_reason"] = "anthropic SDK not importable"
        return out

    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-7",
    }
    model_id = model_map.get(payload["model"], "claude-sonnet-4-6")
    tools = _build_tools_from_grants(payload.get("tool_grants", []))

    # Phase 3f — prompt caching on persona system prompt (4-5K tokens, well above
    # 1024 cache minimum). 5m TTL is the default — pays off after 1 cache hit
    # (1.25x write × 1 + 0.1x read × 1 < 2.25x; vs 1x×2 = 2.0x without cache, but
    # that ignores the read savings on 2nd call). Real win: agent persona is
    # identical across invocations of the same agent, so 2nd+ call within 5min
    # pays only 0.1x for the system prompt.
    cached_system: list[dict] = [{
        "type": "text",
        "text": payload["system_prompt"],
        "cache_control": {"type": "ephemeral"},
    }]

    messages: list[dict] = [{"role": "user", "content": payload["user_task"]}]
    total_input = 0
    total_output = 0
    cache_read = 0
    cache_creation = 0
    tool_calls_made = 0
    iterations = 0
    final_text = ""
    last_stop_reason = "(none)"

    # Phase 3i — running-cost guard. Bypass when budget_usd is 0/missing
    # (back-compat for callers that build payloads without going through
    # build_invocation_payload). Verdict is set only on actual breach.
    budget_usd = float(payload.get("budget_usd", 0.0) or 0.0)
    breach_verdict: str | None = None
    running_cost = 0.0

    started = time.time()
    try:
        client = anthropic.Anthropic(api_key=api_key)
        while iterations < max_iterations:
            iterations += 1
            kwargs = {
                "model": model_id,
                "max_tokens": 4096,
                "system": cached_system,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            response = client.messages.create(**kwargs)
            if response.usage:
                total_input    += int(getattr(response.usage, "input_tokens", 0) or 0)
                total_output   += int(getattr(response.usage, "output_tokens", 0) or 0)
                cache_read     += int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
                cache_creation += int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
            last_stop_reason = response.stop_reason or "(none)"

            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            if text_blocks:
                final_text = "\n".join(text_blocks)

            # Phase 3i mid-flight breach check — recompute running cost after
            # each iteration's token accumulation. Abort BEFORE sending the next
            # tool_use round-trip so we cap at known spend, not projected spend.
            if budget_usd > 0:
                cost_now = compute_cost(
                    payload["model"], total_input, total_output,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                )
                running_cost = cost_now["total_cost"]
                if cost_guard.is_breach(running_cost, budget_usd):
                    breach_verdict = "BUDGET_EXCEEDED"
                    last_stop_reason = "budget_exceeded"
                    break

            tool_uses = [b for b in response.content
                         if hasattr(b, "type") and b.type == "tool_use"]
            if not tool_uses or response.stop_reason != "tool_use":
                break

            # Capture assistant turn (must include tool_use blocks for the API)
            messages.append({"role": "assistant", "content": response.content})
            # Phase 3g — per-agent tool result truncation cap. Default 8000 chars,
            # raise via agent JSON `tool_result_truncation_chars` for codebase-mapping
            # agents (e.g. clio_archaeologist) where 8000 is too tight.
            trunc_chars = int(payload.get("tool_result_truncation_chars", 8000) or 8000)
            tool_results: list[dict] = []
            for tu in tool_uses:
                tool_calls_made += 1
                result_text, is_error = _execute_local_tool(tu.name, dict(tu.input or {}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_text[:trunc_chars],
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        duration_ms = int((time.time() - started) * 1000)
        return {
            "mode": "live",
            "agent": payload["agent"],
            "model": payload["model"],
            "verdict": breach_verdict or "OK",
            "response_text": final_text,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "duration_ms": duration_ms,
            "tool_calls_made": tool_calls_made,
            "iterations": iterations,
            "stop_reason": last_stop_reason,
            "success": breach_verdict is None,
            "budget_usd": budget_usd,
            "tier": payload.get("tier"),
            "breach": breach_verdict is not None,
        }
    except Exception as e:
        out = invoke_dry_run(payload)
        out["mode"] = "live-fallback-dry"
        out["fallback_reason"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["iterations"] = iterations
        out["tool_calls_made"] = tool_calls_made
        return out


def invoke_claude_code(payload: dict) -> dict:
    """
    Emit a dispatch envelope the calling Claude Code session can relay via the
    Agent tool. Zero direct API cost — session cache amortizes.

    Returns: dispatch metadata + agent prompt the caller should pass through.
    """
    handoff_prompt = (
        f"# Persona\n{payload['system_prompt']}\n\n"
        f"# Skill Wrappers (load as context if relevant)\n"
        f"{', '.join(payload['skill_wrappers']) or '(none)'}\n\n"
        f"# Tool Grants\n{', '.join(payload['tool_grants']) or '(default)'}\n\n"
        f"# Task\n{payload['user_task']}\n"
    )
    return {
        "mode": "claude-code",
        "agent": payload["agent"],
        "model": payload["model"],
        "verdict": "DISPATCHED",
        "response_text": "[CLAUDE-CODE] Caller should invoke Agent tool with this prompt.",
        "dispatch_prompt": handoff_prompt,
        "subagent_type": "general-purpose",
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": 0,
        "success": True,
        "tier": payload.get("tier"),
        "budget_usd": float(payload.get("budget_usd", 0.0) or 0.0),
    }


# === Telemetry =====================================================================


def write_telemetry(spec: AgentSpec, payload: dict, result: dict, session_id: str = "") -> None:
    TELEMETRY.parent.mkdir(parents=True, exist_ok=True)
    in_tok        = int(result.get("input_tokens", 0) or 0)
    out_tok       = int(result.get("output_tokens", 0) or 0)
    cache_read    = int(result.get("cache_read_tokens", 0) or 0)
    cache_create  = int(result.get("cache_creation_tokens", 0) or 0)
    cost          = compute_cost(spec.model, in_tok, out_tok, cache_read, cache_create, cache_ttl="5m")
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent": spec.name,
        "division": spec.division,
        "mode": result.get("mode", "?"),
        "task_hash": payload.get("task_hash", ""),
        "session_id": session_id,
        "model": spec.model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "input_cost": cost["input_cost"],
        "output_cost": cost["output_cost"],
        "cache_read_cost": cost["cache_read_cost"],
        "cache_write_cost": cost["cache_write_cost"],
        "total_cost": cost["total_cost"],
        "duration_ms": int(result.get("duration_ms", 0) or 0),
        "tool_calls_made": int(result.get("tool_calls_made", 0) or 0),
        "iterations": int(result.get("iterations", 0) or 0),
        "stop_reason": result.get("stop_reason", ""),
        "success": bool(result.get("success", False)),
        "verdict": result.get("verdict", "UNKNOWN"),
        "fallback_reason": result.get("fallback_reason", ""),
    }
    with open(TELEMETRY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def telemetry_tail(agent: str | None = None, last_n: int = 20) -> list[dict]:
    if not TELEMETRY.exists():
        return []
    rows: list[dict] = []
    with open(TELEMETRY, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if agent is not None and d.get("agent") != agent:
                continue
            rows.append(d)
    return rows[-last_n:]


def telemetry_rollup(agent: str | None = None) -> dict:
    """Aggregate stats: fire count, success rate, p50 duration, last-fire, total cost."""
    rows = telemetry_tail(agent=agent, last_n=10000)
    if not rows:
        return {"agent": agent, "fire_count": 0, "success_count": 0, "success_rate": 0.0,
                "last_fire": None, "total_cost_usd": 0.0, "live_fire_count": 0,
                "input_tokens_total": 0, "output_tokens_total": 0,
                "avg_cost_per_live_fire": 0.0, "p50_duration_ms": 0}
    fire = len(rows)
    succ = sum(1 for r in rows if r.get("success"))
    live_rows = [r for r in rows if r.get("mode") == "live"]
    live_fire = len(live_rows)
    durations = sorted(r.get("duration_ms", 0) for r in rows if r.get("duration_ms"))
    p50 = durations[len(durations) // 2] if durations else 0
    # Lazy cost backfill — old entries with tokens but no cost field still attribute correctly
    def _row_cost(r: dict) -> float:
        c = float(r.get("total_cost", 0) or 0)
        if c == 0.0:
            in_t  = int(r.get("input_tokens", 0) or 0)
            out_t = int(r.get("output_tokens", 0) or 0)
            cache = int(r.get("cache_read_tokens", 0) or 0)
            if in_t > 0 or out_t > 0:
                c = compute_cost(r.get("model", "?"), in_t, out_t, cache)["total_cost"]
        return c
    total_cost = sum(_row_cost(r) for r in rows)
    in_tok_total       = sum(int(r.get("input_tokens", 0) or 0) for r in rows)
    out_tok_total      = sum(int(r.get("output_tokens", 0) or 0) for r in rows)
    cache_read_total   = sum(int(r.get("cache_read_tokens", 0) or 0) for r in rows)
    cache_create_total = sum(int(r.get("cache_creation_tokens", 0) or 0) for r in rows)
    avg_live = (total_cost / live_fire) if live_fire else 0.0
    # Phase 3g — iteration histogram for cost-of-cap awareness. Bins live-mode runs.
    # Cap at 12 (current default max_iterations); 12 in the histogram = "hit cap, may need bump".
    iter_bins: dict[int, int] = {}
    for r in live_rows:
        n = int(r.get("iterations", 0) or 0)
        if n > 0:
            iter_bins[n] = iter_bins.get(n, 0) + 1
    iter_histogram = sorted(iter_bins.items())  # [(iter_count, fire_count), ...]
    iter_cap_hits = iter_bins.get(12, 0)  # convention: 12 = current default cap
    # Phase 3f cache hit rate — fraction of total input-side tokens served from cache.
    # Denominator is what the input WOULD have cost without caching: read + create + uncached input.
    denom = cache_read_total + cache_create_total + in_tok_total
    cache_hit_rate = (cache_read_total / denom) if denom > 0 else 0.0
    return {
        "agent": agent,
        "fire_count": fire,
        "live_fire_count": live_fire,
        "success_count": succ,
        "success_rate": round(succ / max(1, fire), 3),
        "p50_duration_ms": p50,
        "last_fire": rows[-1].get("ts"),
        "last_verdict": rows[-1].get("verdict"),
        "total_cost_usd": round(total_cost, 4),
        "input_tokens_total": in_tok_total,
        "output_tokens_total": out_tok_total,
        "cache_read_tokens_total": cache_read_total,
        "cache_creation_tokens_total": cache_create_total,
        "cache_hit_rate": round(cache_hit_rate, 4),
        "avg_cost_per_live_fire": round(avg_live, 4),
        "iter_histogram": iter_histogram,
        "iter_cap_hits": iter_cap_hits,
    }


def cost_rollup_by_division() -> dict:
    """
    Aggregate cost across all agents grouped by division.
    Computes cost lazily from (model, tokens) when total_cost field is absent
    or zero — handles historical entries that predate the cost-field write path.
    """
    rows = telemetry_tail(agent=None, last_n=100000)
    by_div: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    for r in rows:
        div = r.get("division", "?")
        ag  = r.get("agent", "?")
        in_tok  = int(r.get("input_tokens", 0) or 0)
        out_tok = int(r.get("output_tokens", 0) or 0)
        cache_tok = int(r.get("cache_read_tokens", 0) or 0)
        cost = float(r.get("total_cost", 0) or 0)
        if cost == 0.0 and (in_tok > 0 or out_tok > 0):
            cost = compute_cost(r.get("model", "?"), in_tok, out_tok, cache_tok)["total_cost"]
        is_live = r.get("mode") == "live"
        for bucket, key in ((by_div, div), (by_agent, ag)):
            if key not in bucket:
                bucket[key] = {"fires": 0, "live_fires": 0, "input_tokens": 0,
                               "output_tokens": 0, "total_cost_usd": 0.0}
            bucket[key]["fires"] += 1
            bucket[key]["live_fires"] += 1 if is_live else 0
            bucket[key]["input_tokens"] += in_tok
            bucket[key]["output_tokens"] += out_tok
            bucket[key]["total_cost_usd"] += cost
    for b in (by_div, by_agent):
        for k, v in b.items():
            v["total_cost_usd"] = round(v["total_cost_usd"], 4)
    return {
        "by_division": by_div,
        "by_agent": by_agent,
        "grand_total_cost_usd": round(sum(d["total_cost_usd"] for d in by_div.values()), 4),
        "grand_total_live_fires": sum(d["live_fires"] for d in by_div.values()),
    }


# === Per-agent learnings (shell-append) ============================================


def append_learning(spec: AgentSpec, entry_text: str, citation: str = "") -> Path | None:
    """
    Shell-append a learning entry to per-agent _learnings.md.
    Schema mirrors Toke's existing _learnings.md format.
    """
    if not spec.learnings_path:
        return None
    path = (AGENTS_DIR / spec.learnings_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    block = (
        f"\n### {entry_text.splitlines()[0][:80]} — {date}\n"
        f"<!-- meta: {{\"roi_score\": 0, \"confidence\": \"LOW\", \"confirmed_count\": 1}} -->\n\n"
        f"{entry_text}\n\n"
        f"**Citation:** {citation or '(none)'}\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return path


# Verdicts that count as PASS for auto-_learnings (Phase 3e — auto-write on success)
PASS_VERDICTS = {
    "PASS", "OK",
    "MEASUREMENT_COMPLETE", "PARTIAL_MEASUREMENT",  # urania
    "MAP_COMPLETE", "PARTIAL_MAP",                  # clio
    "SYNTHESIS_READY",                              # calliope
    "PAPERS_FOUND", "DEEP_READ_COMPLETE",           # paper_scout
    "BLUEPRINT_READY",                              # ue5_blueprinter
    "DISPATCHED",                                   # claude-code dispatch envelope built
}


def _maybe_auto_append_learning(spec: AgentSpec, payload: dict, result: dict) -> Path | None:
    """
    Auto-append a terse PASS marker to per-agent _learnings.md when:
    - mode == 'live' (real generation only — skip dry-run and claude-code envelopes)
    - verdict in PASS_VERDICTS
    - tokens > 0 (defends against degraded fallback that copied verdict shape)

    Per project_status.md side finding #6 — wires the dormant append_learning() path
    so Aurora/Hesper can mine per-agent ROI signal without manual writes.
    """
    if result.get("mode") != "live":
        return None
    if result.get("verdict") not in PASS_VERDICTS:
        return None
    in_tok  = int(result.get("input_tokens", 0) or 0)
    out_tok = int(result.get("output_tokens", 0) or 0)
    if in_tok == 0 and out_tok == 0:
        return None
    cost = compute_cost(spec.model, in_tok, out_tok, int(result.get("cache_read_tokens", 0) or 0))
    iters = int(result.get("iterations", 1) or 1)
    tools_used = int(result.get("tool_calls_made", 0) or 0)
    duration_s = int(result.get("duration_ms", 0) or 0) / 1000.0
    entry = (
        f"LIVE PASS — verdict={result.get('verdict')} "
        f"in/out={in_tok}/{out_tok} tok | "
        f"${cost['total_cost']:.4f} | "
        f"{iters} iter | {tools_used} tool_calls | {duration_s:.1f}s"
    )
    citation = (
        f"agent_invocations.jsonl mode=live "
        f"task_hash={payload.get('task_hash','')[:12]}"
    )
    return append_learning(spec, entry, citation=citation)


# === Main invocation entrypoint ====================================================


def invoke(name: str, task: str, mode: str = "dry-run", session_id: str = "",
           tier: str | None = None) -> dict:
    spec = load_agent(name)
    payload = build_invocation_payload(spec, task, tier=tier)

    if mode == "dry-run":
        result = invoke_dry_run(payload)
    elif mode == "live":
        result = invoke_live(payload)
    elif mode == "claude-code":
        result = invoke_claude_code(payload)
    else:
        raise ValueError(f"Invalid mode: {mode} (must be {VALID_MODES})")

    write_telemetry(spec, payload, result, session_id=session_id)
    _maybe_auto_append_learning(spec, payload, result)

    # Phase 3i — post-flight cost-efficiency receipt. Only `live` mode produces
    # real spend; dry-run + claude-code modes still write a row (actual=0) so
    # the rollup tracks per-mode invocation share.
    if mode == "live":
        actual = compute_cost(
            payload["model"],
            int(result.get("input_tokens", 0) or 0),
            int(result.get("output_tokens", 0) or 0),
            cache_read_tokens=int(result.get("cache_read_tokens", 0) or 0),
            cache_creation_tokens=int(result.get("cache_creation_tokens", 0) or 0),
        )["total_cost"]
        chr_rate = cost_guard.cache_hit_rate(
            int(result.get("input_tokens", 0) or 0),
            int(result.get("cache_read_tokens", 0) or 0),
            int(result.get("cache_creation_tokens", 0) or 0),
        )
    else:
        actual = 0.0
        chr_rate = None
    receipt = cost_guard.build_receipt(
        agent=spec.name,
        tier=payload.get("tier", "S2"),
        actual_cost_usd=actual,
        iterations=int(result.get("iterations", 0) or 0),
        cache_hit_rate=chr_rate,
        verdict=result.get("verdict", "UNKNOWN"),
        session_id=session_id,
        notes=[mode],
    )
    cost_guard.write_receipt(receipt)
    return result


# === CLI ===========================================================================


def _cli_list() -> int:
    agents = list_agents()
    if not agents:
        print("No agents registered.")
        return 0
    print(f"Agents ({len(agents)}):")
    for name in agents:
        try:
            spec = load_agent(name)
            roll = telemetry_rollup(agent=name)
            print(f"  {name:24s} division={spec.division:18s} model={spec.model:6s} "
                  f"fires={roll['fire_count']:4d} success={roll['success_rate']}")
        except Exception as e:
            print(f"  {name:24s} (load error: {e})")
    return 0


def _cli_info(name: str) -> int:
    try:
        spec = load_agent(name)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Agent: {spec.name}")
    print(f"  division:        {spec.division}")
    print(f"  role:            {spec.role}")
    print(f"  model:           {spec.model}")
    print(f"  parent_skill:    {spec.parent_skill or '(none)'}")
    print(f"  tool_grants:     {spec.tool_grants}")
    print(f"  skill_wrappers:  {spec.skill_wrappers}")
    print(f"  trigger_signals: {len(spec.trigger_signals)} entries — {spec.trigger_lexicon()[:5]}")
    print(f"  system_prompt:   {len(spec.system_prompt)} chars")
    print(f"  success_metrics: {list(spec.success_metrics.keys())}")
    roll = telemetry_rollup(agent=name)
    print(f"  fires:           {roll['fire_count']} (success_rate={roll['success_rate']})")
    print(f"  last_fire:       {roll['last_fire'] or '(never)'}")
    return 0


def _cli_validate() -> int:
    out = validate_all()
    print(f"Agent validation: {out['valid']}/{out['total']} valid")
    if out["invalid"] > 0:
        for name, errs in out["errors"].items():
            print(f"  [INVALID] {name}:")
            for e in errs:
                print(f"    - {e}")
        return 1
    print("ALL GREEN")
    return 0


def _cli_invoke(name: str, task: str, mode: str, session_id: str, json_out: bool) -> int:
    try:
        result = invoke(name, task, mode=mode, session_id=session_id)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Agent: {result['agent']}  mode: {result['mode']}  verdict: {result['verdict']}")
        if "fallback_reason" in result and result["fallback_reason"]:
            print(f"  fallback_reason: {result['fallback_reason']}")
        print(f"  tokens in/out: {result.get('input_tokens', 0)}/{result.get('output_tokens', 0)}")
        print(f"  duration_ms:   {result.get('duration_ms', 0)}")
        print(f"  response head: {(result.get('response_text') or '')[:300]}")
    return 0 if result.get("success") else 3


def _cli_telemetry(agent: str | None, last_n: int) -> int:
    rows = telemetry_tail(agent=agent, last_n=last_n)
    if not rows:
        print("No telemetry yet.")
        return 0
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    return 0


def _cli_for_division(division: str) -> int:
    agents = list_agents_in_division(division)
    if not agents:
        print(f"No agents registered for division: {division}")
        return 0
    print(f"Agents in division '{division}' ({len(agents)}):")
    for name in agents:
        spec = load_agent(name)
        roll = telemetry_rollup(agent=name)
        print(f"  {name:24s} model={spec.model:6s} fires={roll['fire_count']:4d}")
    return 0


def _cli_costs(json_out: bool, by: str) -> int:
    """Print per-agent or per-division cost rollup from agent_invocations.jsonl."""
    rollup = cost_rollup_by_division()
    if json_out:
        print(json.dumps(rollup, indent=2, ensure_ascii=False))
        return 0
    print(f"=== Agent Cost Rollup (USD) — verified pricing 2026-04-11 ===")
    print(f"Grand total: ${rollup['grand_total_cost_usd']:.4f}  "
          f"({rollup['grand_total_live_fires']} live fires)\n")
    if by == "division":
        print(f"{'Division':20s}  {'Fires':>6s}  {'Live':>4s}  {'In Tok':>10s}  {'Out Tok':>10s}  {'$ Cost':>10s}")
        for div, d in sorted(rollup["by_division"].items()):
            print(f"{div:20s}  {d['fires']:>6d}  {d['live_fires']:>4d}  "
                  f"{d['input_tokens']:>10d}  {d['output_tokens']:>10d}  "
                  f"${d['total_cost_usd']:>9.4f}")
    else:
        print(f"{'Agent':24s}  {'Fires':>6s}  {'Live':>4s}  {'In Tok':>10s}  {'Out Tok':>10s}  {'$ Cost':>10s}")
        for ag, d in sorted(rollup["by_agent"].items()):
            print(f"{ag:24s}  {d['fires']:>6d}  {d['live_fires']:>4d}  "
                  f"{d['input_tokens']:>10d}  {d['output_tokens']:>10d}  "
                  f"${d['total_cost_usd']:>9.4f}")
    return 0


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent_runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_info = sub.add_parser("info")
    p_info.add_argument("name")

    sub.add_parser("validate")

    p_inv = sub.add_parser("invoke")
    p_inv.add_argument("name")
    p_inv.add_argument("--task", required=True)
    p_inv.add_argument("--mode", choices=sorted(VALID_MODES), default="dry-run")
    p_inv.add_argument("--session-id", default="")
    p_inv.add_argument("--json", action="store_true")

    p_tel = sub.add_parser("telemetry")
    p_tel.add_argument("--agent", default=None)
    p_tel.add_argument("--last", type=int, default=20)

    p_div = sub.add_parser("for-division")
    p_div.add_argument("division")

    p_cost = sub.add_parser("costs", help="USD cost rollup from agent_invocations.jsonl")
    p_cost.add_argument("--by", choices=["agent", "division"], default="agent")
    p_cost.add_argument("--json", action="store_true")

    args = parser.parse_args(argv[1:])

    if args.cmd == "list":
        return _cli_list()
    if args.cmd == "info":
        return _cli_info(args.name)
    if args.cmd == "validate":
        return _cli_validate()
    if args.cmd == "invoke":
        return _cli_invoke(args.name, args.task, args.mode, args.session_id, args.json)
    if args.cmd == "telemetry":
        return _cli_telemetry(args.agent, args.last)
    if args.cmd == "costs":
        return _cli_costs(args.json, args.by)
    if args.cmd == "for-division":
        return _cli_for_division(args.division)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
