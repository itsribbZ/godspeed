# Threat Model — Toke Ecosystem

> Maps OWASP Agentic AI Top 10 (2026) to Toke's attack surface.
> Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, Phase 3B.

---

## ASI-01: Agent Goal Hijack

**OWASP:** Injected instructions redirect agent away from intended task.
**Toke exposure:** Tool results (Read, WebFetch, Bash output) can contain injection attempts. Skill descriptions loaded from disk could be modified. MCP server responses are untrusted.
**Current mitigations:** Sacred Rule #4 (only change what's asked), Oracle theater detection, system prompt injection warnings.
**Residual risk:** LOW — Claude Code's own system prompt warns about injection in tool results. Oracle catches behavioral drift.
**Proposed controls:** `audit_protocol.py` scans all events for injection patterns (6 regex patterns).

## ASI-02: Tool Misuse

**OWASP:** Agent calls destructive tools due to ambiguous instructions.
**Toke exposure:** Godspeed auto-deploys 30+ tools including Bash (arbitrary commands), Edit/Write (file modification), Agent (subagent spawning). Sacred Rule #2 (no delete) is the primary gate.
**Current mitigations:** Sacred Rule #2, godspeed deletion proposal gate (v4.3), PreToolUse permission mode, Oracle rule_2 heuristic.
**Residual risk:** MEDIUM — godspeed operates with wide authority. Permission mode is user-configurable.
**Proposed controls:** `audit_protocol.py` flags destructive command patterns (rm -rf, git reset --hard, DROP TABLE). Weekly governance report surfaces any tool_misuse events.

## ASI-03: Identity & Privilege Abuse

**OWASP:** Agent operates with elevated permissions beyond task scope.
**Toke exposure:** Claude Code runs with the user's full user permissions. No per-task sandboxing. File access is unrestricted.
**Current mitigations:** Working directory convention (stay in project dir). Sacred Rule #4 (scope guard).
**Residual risk:** LOW — single-user system, the user IS the permission boundary.
**Proposed controls:** `audit_protocol.py` flags access to sensitive paths (.env, credentials, .ssh/).

## ASI-04: Supply Chain Vulnerabilities

**OWASP:** Poisoned tool definitions or MCP servers.
**Toke exposure:** MCP servers (context7, figma, circleback) are external. Plugin skills load from disk. `ToolSearch` fetches deferred tool schemas at runtime.
**Current mitigations:** MCP servers configured in settings.json (not auto-discovered). Skills are local files under the user's control.
**Residual risk:** LOW — all MCP servers are curated. No auto-install mechanism.
**Proposed controls:** Nyx (Homer L6) audits skill files for unexpected changes. Audit protocol logs tool schema fetches.

## ASI-05: Unexpected Code Execution

**OWASP:** Shell injection via tool input parameters.
**Toke exposure:** Bash tool executes arbitrary commands. Edit tool modifies files. Commands constructed from user input could be injected.
**Current mitigations:** Claude Code's system prompt prohibits command injection. Sacred Rule #11 (AAA quality includes security).
**Residual risk:** LOW — Claude models are trained against injection. Primary risk is indirect injection from tool results.
**Proposed controls:** `audit_protocol.py` scans for shell injection patterns ($(), backticks, semicolons before destructive commands).

## ASI-06: Memory & Context Poisoning

**OWASP:** RAG / context window poisoning via malicious content.
**Toke exposure:** Mnemos (Homer L5) stores and retrieves memories. Auto-memory system writes to `MEMORY.md`. Stale memories can override current truth. Tool results from web/MCP can inject false context.
**Current mitigations:** Memory verification protocol in CLAUDE.md ("verify before recommending from memory"). Mnemos citation enforcement (zero dark edits). Oracle catches drift.
**Residual risk:** MEDIUM — stale memories ARE a real problem. No automated staleness detection.
**Proposed controls:** Hesper (Homer L6) mines learning files for contradictions. `audit_protocol.py` context_poison flag on suspicious tool results.

## ASI-07: Insecure Inter-Agent Communication

**OWASP:** Agent-to-subagent trust chains lack validation.
**Toke exposure:** Zeus dispatches MUSES via Agent tool. Subagents inherit Sonnet model via env var. Subagent results are trusted by parent.
**Current mitigations:** Oracle scores Zeus synthesis outputs. Subagent results are read-only (MUSES don't write files). Sybil has precondition gates.
**Residual risk:** LOW — subagents are scoped (read-only research). Write authority stays with main session.
**Proposed controls:** Audit protocol logs all Agent tool calls. Subagent model override events flagged as ASI-10 (rogue_agent).

## ASI-08: Cascading Failures

**OWASP:** One bad tool call cascades to multi-step damage.
**Toke exposure:** Godspeed executes P0 tasks in sequence, P1-P3 in parallel. A bad P0 fix could cascade into P1 work built on wrong assumptions.
**Current mitigations:** Godspeed reconciliation protocol (zero missed tasks). Verify skill after every batch. Escalation ladder (3 failures → stop and instrument).
**Residual risk:** LOW — escalation ladder limits cascade depth. Human veto at L4.
**Proposed controls:** Audit protocol tracks burst patterns (3+ tool calls to same file = cascade_risk flag).

## ASI-09: Human-Agent Trust Exploitation

**OWASP:** User tricked into approving unsafe actions.
**Toke exposure:** Permission mode approval dialogs. Godspeed's "no options, auto-choose" philosophy (Sacred Rule #9) means less human review per action.
**Current mitigations:** Deletion proposal gate (explicit per-item greenlight). Sacred Rule #2 as hard stop on destructive actions. Human metrics track delegation modes.
**Residual risk:** MEDIUM — long overnight sessions may reduce vigilance. High-speed godspeed execution may outpace review.
**Proposed controls:** `interaction_tracker.py` stall detection. Human metrics in brain scan surface delegation mode drift.

## ASI-10: Rogue Agents

**OWASP:** Agent spawns autonomous subagents beyond authorization.
**Toke exposure:** Zeus can spawn MUSES. Godspeed spawns Agent calls. Subagent model can be overridden per-call.
**Current mitigations:** Brain Zone 2 routing (all subagents default Sonnet). Zeus session cap on advisor calls (max 2). Sacred Rule #8 (no auto-close — prevents runaway sessions).
**Residual risk:** LOW — subagent authority is read-only. No subagent can push to git, delete files, or modify settings.
**Proposed controls:** Audit protocol flags Agent calls with model override as rogue_agent.

---

## Toke-Specific Threats (beyond OWASP)

| Threat | Risk | Mitigation | Monitor |
|--------|------|------------|---------|
| Sacred Rule drift | MEDIUM | Oracle heuristics + Nyx audit | `audit_protocol.py sacred-rules` |
| Theater accumulation | MEDIUM | Nyx + Oracle theater detection | Nyx dated reports |
| Cost spiral | LOW | Brain scan + token_snapshot.py | `brain scan` weekly |
| Memory poisoning | MEDIUM | Mnemos citation enforcement | Hesper learning distillation |
| Hook bypass | LOW | Audit protocol detects missing hooks | `audit_protocol.py report` data sources section |
| Stale memory override | MEDIUM | Memory verification protocol | `interaction_tracker.py` override analysis |

---

## Residual Risk Summary

| Level | Count | Threats |
|-------|-------|---------|
| HIGH | 0 | - |
| MEDIUM | 4 | ASI-02 (tool misuse), ASI-06 (context poison), ASI-09 (trust exploitation), theater/stale memory |
| LOW | 9 | All others |

**Overall posture: ACCEPTABLE for personal-use tool. Would need MEDIUM→LOW reduction before any multi-user deployment.**
