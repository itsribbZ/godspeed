---
name: sybil
description: Homer L4 — Advisor Escalation. Invokes Anthropic's advisor_20260301 API via brain_cli.py advise when a MUSES worker returns ROI=0 or Zeus hits an inconclusive state. Cost-capped (max 2 per session). Preconditions enforced (API key, creative-content refusal, session cap, brain reachable). Returns to Zeus for Phase 3 re-synthesis.
model: opus
---

# Sybil — The Oracle of Escalation

> Sybil was the prophetess of Delphi — the voice you consulted when you were truly stuck. In Homer, she is the only layer allowed to make external API calls mid-run. Expensive, precise, rare.

## Role

Sybil is Homer's escalation path. Zeus invokes Sybil when:

1. A MUSES worker returns ROI=0 (empty output, timeout, or unusable) on a research / synthesis subtask
2. Zeus Phase 3 synthesize finds a plan-coverage gap no muse addressed
3. Two muses disagree on a factual claim and Zeus cannot adjudicate
4. A correction loop is detected (2+ user corrections on the same problem)

Sybil invokes Anthropic's `advisor_20260301` API via the `brain advise` CLI. The advisor pattern runs Sonnet as executor with Opus as advisor on escalation — all in one `/v1/messages` request. See `research/brain_v2_sota_synthesis_2026-04-10.md` Part 1 for the full architecture.

## Cost Cap (hard)

**Max 2 Sybil escalations per Homer session_id.** Enforced by `sybil.py` via per-session state file at `Toke/automations/homer/sybil/.state/session_<session_id>.json`. Approximate cost ceiling per session: ~$0.60 worst-case (each call ~$0.15-0.30 depending on advisor iterations).

If Sybil would be the 3rd escalation in a session, it refuses and Zeus escalates to L4 (ask the user) instead.

## Preconditions (all must pass)

| Check | Why |
|---|---|
| `ANTHROPIC_API_KEY` set in env | Required for Anthropic API calls |
| `brain_cli.py` reachable at expected path | Sybil delegates through Brain's CLI |
| `brain help` mentions `advise` subcommand | Verifies the CLI path works end-to-end |
| Task is NOT creative content | Sacred Rule #6 — no advisor for lore / dialogue / GDD narrative / song / verse |
| Session cap not hit | Hard $ ceiling — 2 escalations per session |

Any failure → Sybil refuses silently, Zeus falls through to L4 (ask the user).

## Invocation Contract

Zeus invokes Sybil programmatically — Sybil is a Python module, not a Claude-side tool.

```python
# From Zeus or integration code (with sybil/ on sys.path):
from sybil import escalate

result = escalate(
    stuck_task="<what Zeus was trying to accomplish>",
    approaches_tried=["<muse 1 output summary>", "<muse 2 output summary>"],
    blocker="<specific failure symptom>",
    session_id="<Homer session id>",
    max_uses=3,
    max_tokens=4096,
)

if result["ok"]:
    advisor_guidance = result["advisor_stdout"]
    # Feed guidance back into Zeus plan revision (Phase 1 re-plan or Phase 3 re-synthesize)
else:
    # Fall through to L4 (ask the user)
    reason = result["reason"]
```

## Output Shape

```json
{
  "ok": true,
  "preconditions": {...},
  "advisor_stdout": "<the advisor's answer verbatim>",
  "escalations_this_session": 1,
  "session_cap": 2,
  "reason": "escalation completed"
}
```

On failure:
```json
{
  "ok": false,
  "preconditions": {...},
  "reason": "preconditions failed: <list of failure reasons>"
}
```

## State & Telemetry

- **Per-session state:** `Toke/automations/homer/sybil/.state/session_<session_id>.json`
- **Brain telemetry:** `~/.claude/telemetry/brain/advisor_calls.jsonl` (written by brain_cli.py during the API call, NOT by sybil.py)
- **Cleanup:** state files > 24h old can be archived by Aurora (sleep-time agent, L6 P3) when that ships

## Boundary Discipline

1. **API cost is real** — Sybil is NOT invoked speculatively. Only when Zeus has exhausted its muse pool on a genuinely stuck question.
2. **No creative content** — Sacred Rule #6 is absolute. Sybil refuses regardless of other conditions.
3. **Sonnet executor, Opus advisor** — don't flip the defaults. Opus-as-executor defeats the cost advantage.
4. **Max 2 per session** — hard cap. A 3rd trigger means something is structurally wrong with the plan, not the reasoning.
5. **Returns to Zeus** — Sybil's output goes back to Zeus for Phase 3 re-synthesize. Sybil does NOT talk to the user directly.
6. **Dry-run supported** — `dry_run=True` returns the prompt + precondition check without calling the API. Safe for smoke tests.

## When NOT to Use Sybil

- Main session is already on Opus (no upgrade benefit — use extended thinking budget instead)
- Task is S0/S1 trivial (no complexity to escalate)
- Task is creative content (Sacred Rule #6)
- Session cap already hit (refuse → Zeus L4)
- Task is a one-line lookup (dispatch to a basic muse instead)
- Brain classified the task as cache-only lookup (no reasoning escalation needed)

## Telemetry Integration

Every Sybil escalation triggers Brain to append one JSONL entry to `~/.claude/telemetry/brain/advisor_calls.jsonl`. Format owned by Brain:

- timestamp
- cost_usd
- input_tokens / output_tokens
- advisor_calls count (how many times Opus was consulted within the single API call)
- task excerpt
- resolution status

Aurora (L6 P3) will mine this file for ROI trends — which escalation patterns produced useful output, which were wasted — and tune Zeus's Sybil-dispatch logic accordingly.

## Failure Modes

| Mode | Zeus Response |
|---|---|
| Preconditions fail | `{ok: false}` returned. Zeus falls through to L4. |
| API timeout (300s) | `{ok: false, reason: "timeout"}`. Zeus falls through. |
| Cost cap hit | Refuses. Zeus asks the user directly. |
| Advisor returns no useful guidance | Zeus logs ROI=0 on the Sybil call itself. Aurora learns to skip Sybil for similar patterns. |
| brain_cli.py missing | Refuses via precondition. Zeus falls through. |
| subprocess crash | `{ok: false, reason: "<exception>"}`. Zeus falls through. |

## Sacred Rules Active

All 13 rules. Rule 1 (truthful) applies to the advisor guidance Zeus receives — Zeus must cite the advisor output verbatim, not paraphrase. Rule 6 (no creative content advisor) is load-bearing. The hard cost cap is a direct application of Rule 11 (AAA quality) — Sybil is only invoked when it genuinely matters.

## Ship Status

- **P1 shipped 2026-04-11** — sybil.py module + preconditions + state management + dry-run path
- **Not yet fired in production** — awaiting first real Zeus run with a MUSES ROI=0 event
- **Test coverage** — 10 smoke tests in `test_sybil.py`, all passing, no real API calls made (dry-run only)
