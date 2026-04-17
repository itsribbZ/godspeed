# Pipeline Stage 1 — Prompt Arrival

> **Goal:** map everything that happens between the user hitting Enter and Claude receiving the prompt in context, with real measurements from Toke's live hook pipeline.

**Status:** measured against 165 real prompt captures across 25 sessions (decisions.jsonl, 2026-04-11). **Refresh 2026-04-17:** decision count now 589 across 82 sessions. Override rate 10.5%. Golden_set 66.5% exact / 0.828 weighted (Brain v2.6.2). S1 tier fixed 2026-04-17 via `informational_question_floor` guardrail — see Stage 3 §S4 hole section for resolution details.

---

## 1. The arrival sequence (what happens, in order)

| Step | Event | Who acts | Blocks turn? |
|------|-------|----------|-------------|
| 1 | User types prompt + hits Enter | user | — |
| 2 | Claude Code receives the prompt locally | Claude Code CLI | — |
| 3 | Claude Code fires `UserPromptSubmit` hook(s) from `settings.json` | hook runner | yes — hook must complete before turn |
| 4 | `brain_advisor.sh` wrapper is invoked with hook JSON on stdin | bash | ~10ms overhead |
| 5 | `brain_cli.py hook` reads stdin, parses `{session_id, prompt, current_model}` | python | ~50-80ms cold, ~20-40ms warm |
| 6 | `severity_classifier.classify()` runs | python | <5ms per call (no I/O) |
| 7 | Classifier returns `{tier, model, score, signals, guardrails, confidence, extended_thinking_budget}` | python | — |
| 8 | `brain_cli.py hook` appends one line to `~/.claude/telemetry/brain/decisions.jsonl` | python | 1 write, ~1ms |
| 9 | Advisory printed to stderr (visible to user if mismatch detected) | python | — |
| 10 | Hook exits 0 — Claude Code unblocks the turn | bash | — |
| 11 | Claude Code assembles the prompt into context + tool results + history | Claude Code | local |
| 12 | Prompt dispatched to the Anthropic API | Claude Code | network |
| 13 | First cache-write/cache-read on the assistant side | API | billable |

**Hook budget:** total local latency from step 3 → 10 measured at **231-416ms** (median ~323ms, 5-run microbench 2026-04-12). Cold Python startup (~416ms first run) dominates. The user does not perceive this delay because the hook runs before the API call, but it adds ~300ms serial latency per prompt. Hook is non-blocking for user experience but slower than initially estimated.

---

## 2. What the Brain classifier actually does in <5ms

From `severity_classifier.py` (v2.3):

1. **Signal extraction** — counts prompt tokens, code blocks, file refs, reasoning verbs, multi-step markers, ambiguity markers, tool call hints, context size indicators
2. **Weighted score** — normalized signal values × signal weights (sum ~1.0, per manifest `[weights]`)
3. **Guardrail checks** — 12 hard rules that override the base score upward (`gpqa_hard_reasoning`, `multi_file_refactor`, `ue5_mention_floor`, `architecture_work`, `debug_floor`, `correction_detected`, `long_context`, etc.)
4. **Tier map** — score → {S0..S5} per `[thresholds]` in manifest
5. **Skill override** — if the prompt mentions a registered skill by name (`brain`, `homer`, `sitrep`, etc.), the skill's pinned tier wins
6. **Confidence** — distance of score from nearest tier boundary (0..1). Low confidence = near boundary = `uncertainty_escalated=true`
7. **Extended thinking budget** — S0=0, S1=0, S2=4K, S3=16K, S4=32K, S5=64K (v2.0)
8. **Correction detection** — matches keywords ("wrong", "redo", "stop", etc.) to flag correction prompts for the learner

Output is written to decisions.jsonl as one line of structured JSON:
```json
{
  "ts": "...",
  "hook": "UserPromptSubmit",
  "session_id": "<uuid>",
  "current_model": "sonnet" | "opus" | "",
  "result": {
    "tier": "S3",
    "model": "sonnet",
    "score": 0.45,
    "signals": {...},
    "guardrails_fired": ["debug_floor"],
    "confidence": 0.72,
    "extended_thinking_budget": 16000,
    "correction_detected_in_prompt": false,
    "reasoning": "score=0.45->S3 | top:reasoning=0.50 | guards:debug_floor"
  }
}
```

---

## 3. Measured reality (165 real prompts, 25 sessions)

| Metric | Value |
|---|---|
| Total decisions logged | 165 |
| Unique sessions captured | 25 |
| Decisions per session | min=1, median=4, max=52, avg=6.6 |
| Guardrail fire rate | **14.5%** (24/165 prompts trigger at least one guardrail) |
| Correction detection rate | **1.2%** (2/165 — the user rarely sends corrections, or they don't match the keyword set) |
| `current_model` populated | 68/165 = 41% (only sonnet-emitting sessions pass this field; Opus sessions leave it blank) |

### Tier distribution across real prompts
```
S0 (haiku-trivial):   64 (38.8%)  — list, check, trivial lookups
S1 (haiku-light):     51 (30.9%)  — explain short, simple Q&A
S2 (sonnet-medium):   11 ( 6.7%)  — moderate debug, small edits
S3 (sonnet-deep):      4 ( 2.4%)  — multi-step with research
S4 (opus-standard):    0 ( 0.0%)  — **HOLE: never assigned** (see §4)
S5 (opus[1m]-max):    23 (13.9%)  — architecture, multi-file, GPQA-class
```

### Most-fired guardrails
```
gpqa_hard_reasoning   12  (formal proofs, hard reasoning keywords)
multi_file_refactor   11  (3+ file refs in one prompt)
ue5_mention_floor      5  (UE5 project mentions)
ue5_code_work          4  (UE5 + code signal)
architecture_work      3  (architecture keyword)
code_edit_floor        2  (code edit signal above threshold)
debug_floor            1  (debug/error keywords)
creative_game_design   1  (creative content guard)
```

---

## 4. The S4 hole — RESOLVED 2026-04-12

**Update 2026-04-17:** The "needs 500+ decisions" claim below was wrong. gap_audit (2026-04-12) resolved this definitively at 194 decisions — the hole was a threshold design issue, not under-sampling. s2_max was lowered 0.35→0.22, s3_max 0.55→0.32, s4_max 0.80→0.55. Ceiling/floor guardrails added. S4 is now reachable and reliably reached by architecture work prompts (see `brain score "design a distributed caching layer"` → S4). Original analysis preserved below for archaeology:

### Original April 11 analysis (superseded)

**Observation:** zero S4 classifications in 165 real prompts. Tasks jump from S3 (sonnet with 16K thinking) to S5 (opus[1m] with 64K thinking).

**Possible causes:**
1. Threshold gap: S3 ends at 0.55, S4 ends at 0.80. A raw score of 0.56-0.79 SHOULD land in S4. Either the user's prompts never score there, or signal weights compress the mid-high range.
2. Most high-scoring prompts fire a guardrail that pushes them all the way to S5 (e.g., `gpqa_hard_reasoning` is a minimum-score override, not a cap).
3. `skill_override` for heavy skills (`blueprint`, `professor`, `devTeam`) pins straight to S4/S5 — skipping the score ramp.

**Not a bug yet** — need more data. If S4 stays at 0% after 500+ decisions, the threshold needs widening or a guardrail needs capping. *[Superseded: threshold widening was applied 2026-04-12.]*

---

## 5. The hook is advisory — it cannot route the main session

A hard truth measured in this stage: `UserPromptSubmit` hooks in Claude Code are **advisory only**. The hook can:
- Log everything about the prompt ✓
- Print advisories to stderr (visible to the user) ✓
- Modify how the user thinks about the next prompt ✓

The hook CANNOT:
- Force the main session model ✗
- Inject a pre-prompt into the conversation ✗
- Veto the turn ✗
- Change the tier applied to the turn ✗

This is why `brain scan` reports an **ACHIEVABLE $655/mo savings** (Zone 2 subagents, where the env var WORKS) versus a **THEORETICAL $4,717/mo** (would require main-session routing authority, which Claude Code does not expose). The Stage 1 hook is the boundary where that authority ends.

---

## 6. Hot-reload contract (surprise finding from this session)

**Observation:** I wired the `PostToolUse` hook in `settings.json` mid-session (2026-04-11 ~13:29). Expected behavior: dormant until next session restart. Actual behavior: **same** — PostToolUse did not fire on tool calls after the edit, because Claude Code reads `settings.json` at session boot, not hot-reload.

**Contrast:** installing a new skill by dropping `~/.claude/skills/zeus/SKILL.md` mid-session DID hot-reload — the skill appeared in the available-skills list on the very next system reminder. **Skills hot-reload. Hooks do not.**

This is a real contract difference in Claude Code:
- **Skills:** filesystem-watched, hot-reload
- **Hooks:** read once at session start, cold-reload (restart required)
- **settings.json.permissions:** unclear, probably hot-reload

**Implication for Toke:** when instrumenting, skills are lower-friction to iterate on than hooks. Hook changes take effect next session.

---

## 7. Open questions

1. **Actual hook latency distribution** — we believe 30-100ms but haven't timed it. Need a microbench against the hook wrapper or a `time` wrapper around `brain_cli.py hook`.
2. **Is the stderr advisory ever read?** — if the user doesn't see it, the advisory is theater. Worth instrumenting.
3. **Hook failure mode** — if `brain_cli.py` crashed, does Claude Code block the turn or continue? Earlier hook was `set +e` → silent exit 0, so crashes are invisible. Need a test.
4. **decisions.jsonl rotation** — at current rate (~6.6 per session × 10 sessions/day), we'll hit 100K+ decisions in 6 months. Need rotation or compression.
5. **Can the hook mutate the prompt?** — some Claude Code hook docs suggest the prompt stdin can be rewritten back. If true, it would open a path to lightweight prompt preprocessing.

---

## 8. Actionable drains caught at Stage 1

| Drain | Severity | Fix |
|---|---|---|
| S4 never fires — possible threshold gap | measurement needed | 500+ decisions and recheck |
| PostToolUse hook was wired but dormant | contract issue | restart session to activate |
| Advisory-only stderr may never be read | trust issue | add a way to peek: `brain history 5` on demand |
| decisions.jsonl growth unbounded | long-term | rotation at 10MB or 30 days |

---

## 9. Status

- ✅ Arrival sequence mapped step-by-step
- ✅ Classifier internals documented against `severity_classifier.py`
- ✅ 165-decision distribution measured
- ✅ Advisory-only limit characterized (explains the savings gap)
- ✅ Hot-reload contract observed (skills yes, hooks no)
- ✅ Hook latency microbench: **231-416ms** (median ~323ms). 3-4× higher than the 30-100ms estimate. Cold Python startup dominates. Not user-perceptible (hook runs before API call) but adds real serial latency to every turn.
- ⏳ S4 hole investigation (waits for more data)

Next in the pipeline: **Stage 2 — Context assembly.** What files, memories, skills, and tool schemas actually get loaded into the prompt context on this specific turn, and how the decision is made.
