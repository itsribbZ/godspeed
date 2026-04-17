---
description: Toke workbench — one-screen dashboard covering Brain, Homer, current session tokens, and pipeline progress
argument-hint: "[subcommand]   # status (default) | scan | snapshot | homer | tick | pipeline"
allowed-tools: Bash, Read
---

# /toke — Toke Meta-Automation Workbench

Arguments received: **$ARGUMENTS**

You are running the `/toke` workbench command. Treat the user's argument (if any) as the subcommand. Default is `status`. Run the appropriate commands below in parallel where possible and render ONE compact dashboard at the end — no preamble, no narration.

## Subcommand: `status` (default)

Run these in parallel via the Bash tool, then render the dashboard:

1. `python $TOKE_ROOT/automations/brain/brain_cli.py scan 2>&1 | head -35` — Brain cost + v2 learning state
2. `python $TOKE_ROOT/automations/homer/homer_cli.py status 2>&1` — Homer layer readiness
3. `python $TOKE_ROOT/tokens/token_snapshot.py --current 2>&1` — current session tokens
4. `cat ~/.claude/telemetry/brain/godspeed_count.txt 2>&1` — godspeed tick
5. `ls $TOKE_ROOT/pipeline/ $TOKE_ROOT/tokens/ 2>&1` — charter coverage

Render output in this exact format (ASCII, no emojis unless the user asked):

```
╔═════════════════════════════════════════════════════════════╗
║  TOKE WORKBENCH — <date>                                   ║
╠═════════════════════════════════════════════════════════════╣
║ BRAIN    $<total>/mo | 30d Opus <pct>% | decisions <N>     ║
║ HOMER    <shipped>/<total> layers | VAULT <N> checkpoints  ║
║ SESSION  <turns> turns | <cache_hit>% hit | $<est>         ║
║ PIPELINE <n>/7 stages  | TOKENS <n> files                  ║
║ TICK     <count> | next auto-scan at <next>                 ║
╠═════════════════════════════════════════════════════════════╣
║  v2 LEARNING: <N> decisions | <M> tools | <K> overrides    ║
║  ACHIEVABLE SAVINGS: $<x>/mo (Zone 2)                       ║
║  THEATER (confirmed): 4 kills parked, per-item greenlight   ║
╚═════════════════════════════════════════════════════════════╝
```

Followed by a single line: `Next best move: <one specific next action from project_status.md or obvious gap>`.

## Subcommand: `scan`

Run `python $TOKE_ROOT/automations/brain/brain_cli.py scan` and echo its full output. No additional rendering.

## Subcommand: `snapshot`

Run `python $TOKE_ROOT/tokens/token_snapshot.py --current` and echo its full output. Add `--turns` if the user passed `snapshot turns`.

## Subcommand: `homer`

Run `python $TOKE_ROOT/automations/homer/homer_cli.py status` and echo.

## Subcommand: `tick`

Run `python $TOKE_ROOT/automations/brain/brain_cli.py godspeed-tick 33` and echo.

## Subcommand: `pipeline`

Run `ls $TOKE_ROOT/pipeline/ $TOKE_ROOT/tokens/` and then show a table of which stages (0-6) have a `.md` file in `pipeline/` vs which are empty. Stage titles: 0=Session boot, 1=Prompt arrival, 2=Context assembly, 3=Intent routing, 4=Tool execution, 5=Response generation, 6=Session persistence.

## Rules

- NEVER ask for confirmation — execute the subcommand immediately.
- If a command errors, show the error, continue with the others.
- Keep final output ≤ 40 lines.
- If argument is unrecognized, default to `status` but prepend a one-line note "Unknown subcommand <X>, showing status."
- No emojis unless the user uses them first.
- This command is read-only. Never write files, never edit settings. Use Bash + Read only.
