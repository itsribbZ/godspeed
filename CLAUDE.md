# Toke — Project Context

Project home for **Toke** — the meta-automation project. Everything here is about automating the pipeline from the user's prompt → Claude's execution, and understanding/optimizing every token spent along the way.

## What Toke Is

A laboratory for studying and automating Claude Code itself. The deliverables are hooks, skills, scripts, analyses, and documentation that reduce friction, cut token waste, and make the human→Claude loop as fast and accurate as possible.

## Scope

- **Pipeline analysis** — trace every stage from user prompt arrival to final output
- **Token accounting** — where tokens go, what they cost, what's avoidable
- **Automation layers** — hooks, skills, slash commands, MCP, subagents, status line
- **Context optimization** — what loads automatically vs on-demand, cache discipline
- **Execution routing** — how the right tools/agents get picked for the right job

## Working Style

- Go deep, not wide. One stage of the pipeline at a time.
- Every claim backed by receipts: actual file contents, actual token counts, actual behavior.
- Prefer measurement over speculation. If we don't know, we instrument.
- Outputs land in `pipeline/`, `tokens/`, `automations/`, `hooks/`, `research/` — organized by domain.

## Inherits

All rules and behavioral protocols from `~/CLAUDE.md` (the home CLAUDE.md). Sacred Rules apply. No duplication here.

## Entry Point

Run `toke init` (or `init` from this dir) to load full session context.
