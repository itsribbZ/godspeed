---
name: zeus
description: Homer L2 — Orchestrator skill. Decomposes a task, plans via extended thinking, delegates to MUSES workers in parallel (Anthropic MARS pattern), synthesizes, hands to Oracle for eval, writes checkpoint to VAULT. Use when a task would benefit from multi-agent decomposition (3+ subtasks, research burst, architecture decisions, multi-file work). Do NOT use for trivial single-tool tasks — route those through Brain L1 directly.
model: opus
---

# Zeus — The Orchestrator (Homer L2)

> Zeus decomposes, delegates, and synthesizes. Zeus never executes leaf work directly — he commands MUSES workers to do that. Zeus is the conductor, not the orchestra.

**Status:** P0 shipped 2026-04-11. MUSES (L3) and SYBIL (L4) are P1-pending — until they exist, Zeus falls back to sequential execution. Document both modes below.

---

## When to invoke Zeus (vs godspeed)

| Signal | Route |
|---|---|
| Single-tool task, no decomposition needed | Brain L1 direct → no orchestration |
| Multi-step, single-domain, no research | godspeed (existing) |
| Multi-step, multi-domain, research-heavy | **Zeus** |
| Task requires 3+ parallel subagents | **Zeus** (MARS pattern) |
| Task needs advisor escalation midway | **Zeus** (SYBIL wired in P1) |
| Task is ambiguous, needs planning | **Zeus** (extended thinking plan phase) |

Zeus is NOT godspeed v5. godspeed remains the generalist orchestrator for the user's daily work. Zeus is the leading-edge pattern for tasks where multi-agent decomposition is clearly the right move. Over time, if Zeus proves out, godspeed may migrate into a thin Zeus wrapper — but that decision waits for P4 evidence, not P0 vibes.

---

## The Zeus Pipeline (7 phases)

```
[-1]  TICK          ← inherit Phase -1 from godspeed (brain godspeed-tick)
 [0]  CLASSIFY      ← delegate to L1 Brain: tier, guardrails, $ estimate
 [1]  PLAN          ← extended-thinking scratchpad: decompose → subtask list
 [2]  DISPATCH      ← spawn N MUSES in parallel (N = 3..5 typical, max 8)
 [3]  SYNTHESIZE    ← wait, reconcile outputs, verify citations
 [4]  EVAL          ← hand to L7 Oracle for sacred-rule + rubric scoring
 [5]  MEMORY        ← write to L5 Mnemos Core/Recall/Archival with citations
 [6]  CHECKPOINT    ← persist state to L0 Vault (phase="done")
```

Every phase writes a VAULT checkpoint update so compaction recovery works at any step. Zeus is phase-resumable.

---

## Phase -1: Tick (inherited from godspeed)

```bash
python ~/Desktop/T1/Toke/automations/brain/brain_cli.py godspeed-tick 33
```

Fires the shared godspeed counter. Zeus and godspeed both count against the same 33-tick auto-scan threshold. No separate Zeus counter — they share telemetry so audit cadence stays coherent.

## Phase 0: Classify (delegate to Brain)

Every Zeus invocation first runs the current task through Brain L1. Brain returns:
- `tier` (S0-S5)
- `model` recommendation
- `guardrails_fired` list
- `confidence`
- `extended_thinking_budget`
- `uncertainty_escalated` flag

If Brain returns S0 or S1 → Zeus SKIPS phases 1-5 and routes the task to a single worker directly. No point decomposing a "list files" prompt into 5 parallel MUSES.

If Brain returns S2+ → proceed to Phase 1.

## Phase 1: Plan (extended thinking)

Zeus uses extended thinking as a controllable scratchpad (per Anthropic MARS). The plan output is a structured decomposition:

```yaml
plan:
  task: <one-sentence restatement>
  subtasks:
    - id: 1
      scope: <what this subtask covers>
      worker_role: <muse_role_name — see roster below>
      tools: [<allowlist>]
      output_format: <markdown | json | plaintext | file_edits>
      boundary: <what this subtask MUST NOT touch>
      success_criterion: <how we know it landed>
  synthesis_strategy: <how Zeus will combine the subtask outputs>
  escalation_triggers:
    - <condition that should fire SYBIL advisor escalation>
```

**Decomposition rules (from Anthropic MARS receipts):**
- 1 subtask → fact lookup, 3-10 tool calls
- 2-4 subtasks → comparison / triage / multi-angle
- 5-8 subtasks → complex research or multi-file refactor
- >8 subtasks → stop, refactor the plan

**Boundary discipline is non-negotiable.** MARS research: "Without detailed task descriptions, agents duplicate work, leave gaps, or fail to find necessary information." Every subtask spec must include an explicit boundary that prevents overlap with its siblings.

## Phase 2: Dispatch (parallel MUSES — P1 pending)

**P0 behavior (until MUSES exists):** Zeus runs subtasks sequentially in the main session using direct tool calls. This is the degraded fallback — functional but slow. Zeus still writes checkpoints between subtasks so at least compaction recovery works.

**P1 behavior (when MUSES ships):** Zeus spawns N `Agent` tool calls in a single message — true parallel. Each Agent invocation is scoped per the Phase 1 plan. Sonnet via `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` env var (Zone 2 routing, free).

**Expertise pre-load (identity persistence):** Before dispatching any muse, Zeus reads that muse's `expertise.json` from `homer/muses/<muse>/expertise.json`. Include relevant context in the dispatch prompt:
- For Calliope: inject `high_roi_sources` and `high_roi_queries` as "PROVEN SOURCES" block
- For Clio: inject `mapped_codebases` and `known_patterns` as "PRIOR MAP" block
- For Urania: inject `proven_queries` and `metric_cache` as "KNOWN BASELINES" block
Only inject entries relevant to the current subtask topic. Don't dump the entire expertise file.

Shipped muse roster (P1 — installed at `~/.claude/skills/`):

| Muse | Role | Tool allowlist | Output format |
|---|---|---|---|
| **Calliope** | Epic research / deep synthesis | WebSearch, WebFetch, Read, Grep | structured markdown w/ citations |
| **Clio** | Historical / existing-code exploration | Glob, Grep, Read, Bash | file:line map |
| **Urania** | Measurement / telemetry / brain integration | Bash, Read | numeric report w/ receipts |

Zeus typically picks 2-3 muses per task. For subtasks outside these 3 roles, Zeus handles them inline (sequential fallback) or dispatches existing Toke tools (devTeam, profTeam, debug, etc.) via Agent calls.

Future muse expansion (build only when a real Zeus run identifies the gap):
- **Euterpe** — build/verify/test orchestration (currently handled by Zeus inline + verify skill)
- **Melpomene** — failure-mode analysis (currently handled by debug skill)
- **Polyhymnia** — sacred-rule compliance checking (currently handled by Oracle inline)

Erato (creative), Terpsichore (reconciliation), and Thalia (lookups) are removed from the roster — their roles are covered by existing Toke skills or Zeus inline behavior.

## Phase 3: Synthesize

Zeus waits for all MUSES to return. Synthesis follows Anthropic's CitationAgent pattern:
1. Collect each muse's output verbatim
2. Cross-check claims for conflicts (two muses disagree → flag)
3. Verify citations exist and match their referenced files (no hallucinated line numbers)
4. Compose the final output with source attribution
5. Write synthesis-phase checkpoint to VAULT

**If any muse returns ROI=0** (empty, timeout, crashed) → mark that subtask blocked, fire Phase 4 escalation trigger.

**Expertise update (identity persistence):** After scoring each muse's output (ROI 1-5), Zeus updates that muse's `expertise.json`:
- **ROI ≥ 4:** Add successful queries/sources/patterns to the muse's `high_roi_*` arrays
- **ROI ≤ 1:** Add failed queries/sources to the muse's `low_roi_*` arrays
- **Always:** Increment `dispatches`, update `last_updated` to current ISO date
- **Calliope:** Add new T1-T3 sources to `high_roi_sources`, update `domain_expertise` topic counts
- **Clio:** Update `mapped_codebases` with paths + dates, add new `known_patterns`
- **Urania:** Add working commands to `proven_queries`, snapshot metrics to `metric_cache`
Use Edit tool (not Write) on the JSON files — Sacred Rule #7.

## Phase 4: Eval (Oracle — LIVE)

Zeus scores the synthesized output via Oracle. Run this Bash command after Phase 3 synthesis:

```bash
python ~/Desktop/T1/Toke/automations/homer/oracle/oracle.py score "<synthesis output file or text>"
```

Oracle returns a JSON `ScoreReport` with `verdict` (PASS / SOFT_FAIL / HARD_FAIL), per-rule checks, rubric score, and theater flags.

**Decision logic:**
- `verdict == "PASS"` → proceed to Phase 5
- `verdict == "SOFT_FAIL"` → proceed but flag to the user in the reconciliation report
- `verdict == "HARD_FAIL"` → return to Phase 1 for re-plan with Oracle's `sacred_rule_checks` attached as constraint context

Oracle scores against 10 text-detectable Sacred Rules (Rules 10 godspeed-trigger, 11 ask-don't-guess, and 12 rosetta-update are structural/runtime checks not detectable from output text).

## Phase 5: Memory (Mnemos — LIVE)

Zeus writes the distilled synthesis to Mnemos. Run these after Phase 4 passes:

**Write the key finding to Core (context-resident, ~5K budget):**
```bash
python ~/Desktop/T1/Toke/automations/homer/mnemos/mnemos.py write-core "<distilled pattern>" "<file:line citation>" HIGH
```

**Write the full run narrative to Recall (searchable):**
```bash
python ~/Desktop/T1/Toke/automations/homer/mnemos/mnemos.py write-recall "Zeus run: <topic>" "<full synthesis text>" "<citation1>,<citation2>"
```

Citation is REQUIRED on both writes. `CitationError` fires if citation is invalid or missing. Accepted formats: `file:line`, `https://...`, `arxiv:YYYY.NNNN`, `mnemos:archival_id`, `decisions:id`, `session:id`.

Every Core write must include a file:line citation. Zero dark edits (Beat SOTA commitment #3).

## Phase 6: Checkpoint (VAULT)

Zeus writes the final checkpoint with `phase="done"` via `VaultStore.update()`. Every earlier phase also wrote checkpoints, so the VAULT history captures the full Zeus run for time-travel debugging.

---

## Escalation Ladder (Zeus adaptation of godspeed v3.0)

| Level | Action | When |
|---|---|---|
| L1 | Narrow scope within the current muse | First muse failure |
| L2 | Instrument — spawn a second muse with diagnostic role | Second failure |
| L3 | Research — dispatch Calliope with broader scope | L2 inconclusive |
| **L3.5** | **SYBIL advisor** — call `advisor_20260301` via `brain advise` | **L3 inconclusive or muse returns ROI=0** (P1 wiring) |
| L4 | Ask the user | Advisor exhausted or creative decision needed |
| L5 | Flag blocker | Requires external action |

Max 2 SYBIL escalations per Zeus session (hard cost cap, same as godspeed v4.1).

---

## Stacking (Zeus + existing Toke tools)

- **Zeus + Brain** — always. Brain is Phase 0, non-negotiable.
- **Zeus + Author** — when any Sworder KB load is needed, Zeus dispatches Clio with Author's chain output as Clio's load order.
- **Zeus + godspeed** — godspeed can invoke Zeus as a tier-2.5 sub-tool when a task escalates past godspeed's default depth. godspeed does NOT become Zeus; Zeus is a capability godspeed can reach for.
- **Zeus + bionics / devTeam / profTeam** — these remain top-level tools. Zeus can dispatch them as muse executors when a subtask needs their specific capability.

## Non-Goals for Zeus P0

- ❌ NO rewriting godspeed SKILL.md
- ❌ NO deletions of existing content
- ❌ NO touching `settings.json`
- ❌ NO new hooks
- ❌ NO new env vars
- ❌ NO modifying Brain
- ❌ Pure addition — zero cascade

## Sacred Rules Active in Zeus

All 13 rules apply. Zeus is an orchestrator — it delegates execution but is accountable for the collected output. Rule #11 AAA Quality applies to the synthesized result, not each muse output individually.

## Phase -1 Counter Integration

Zeus fires the SAME `brain godspeed-tick` counter as godspeed. There is one counter per the user, shared across all orchestrators. Auto-scan at tick 33 / 66 / 99 audits Zeus + godspeed + any future orchestrator in one pass.

## Success Criteria for Zeus P0 (today)

- [x] SKILL.md shipped
- [x] VAULT L0 built and tested (smoke tests green)
- [x] Homer CLI operational (`homer init` / `status` / `test`)
- [x] First VAULT checkpoint written
- [ ] First full Zeus run on a real task — deferred to P1 because MUSES don't exist yet (degraded sequential fallback available but untested)

**P0 is scaffolding.** The leading-edge multi-agent behavior lands in P1 when MUSES + SYBIL ship. Today's deliverable is the frame everyone else hangs on: VAULT checkpointing, Zeus skill file, Homer CLI.
