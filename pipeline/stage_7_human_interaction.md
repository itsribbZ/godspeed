# Pipeline Stage 7 — Human Interaction

> **Goal:** Document the human feedback loop — from "Claude responds" to "the user decides what to do next." Closes the pipeline loop (Stage 6 → Stage 7 → Stage 1).
> **Status:** First-pass with live data from 210 decisions across 32 sessions.
> **Origin:** Katanforoosh gap closure — "2026 is the year of the humans." Human-AI Interaction gap 4/10 → 8/10.

---

## 1. What Stage 7 is

After Stage 6 (session persistence) completes a turn, the pipeline doesn't end — it loops. the user reads the response, forms a judgment, and either:

1. **Continues** — next prompt follows naturally (no correction)
2. **Corrects** — next prompt fixes what Claude got wrong
3. **Overrides** — uses a different model/approach than recommended
4. **Stalls** — pauses > 5 minutes (confusion, context switch, or break)
5. **Abandons** — session ends after early frustration

Stage 7 is the measurement of WHICH of these happens and WHY. It's the only pipeline stage that instruments the human, not the machine.

---

## 2. Delegation modes (measured)

Every Brain decision is now classified into a delegation mode based on classifier confidence and correction history:

| Mode | Definition | Measured Distribution |
|------|-----------|----------------------|
| **Full** | confidence >= 0.70, no corrections | 51.0% |
| **Supervised** | 0.30 <= confidence < 0.70 | 17.6% |
| **Checkpoint** | confidence < 0.30 | 16.7% |
| **Veto** | 2+ consecutive corrections or guardrails fired | 14.8% |

**Interpretation:** Majority full delegation (51%) indicates the system is trusted for most tasks. The 14.8% veto rate is driven by S5 (Opus) tasks where guardrails fire.

**By tier:**
- S0: 80% full, 20% supervised — trivial tasks, high trust
- S1: 48% full, 27% checkpoint — mid-tier uncertainty
- S2: 78% checkpoint — Brain's S2 classification has low confidence
- S5: 100% veto — all S5 decisions fire guardrails

**Source:** `python tokens/interaction_tracker.py delegation`

---

## 3. Override patterns (measured)

Overall override rate: **29.0%** (61/210 decisions).

| Tier | Override Rate | What's Happening |
|------|-------------|-----------------|
| S0 | 13.4% | Brain says Haiku, the user runs Sonnet. Acceptable — some S0 prompts need more than Haiku. |
| S1 | **56.7%** | Brain says Haiku, the user runs Sonnet. HIGH — S1 threshold may be too low. |
| S2 | 0.0% | Perfect alignment. Brain says Sonnet, the user runs Sonnet. |
| S3 | 0.0% | Perfect alignment. |
| S5 | **50.0%** | Brain says Opus[1M], the user sometimes runs Sonnet. Likely subagent sessions. |

**Root cause for S1:** Brain classifies S1 tasks as Haiku-appropriate, but the user's `/effort max` forces Opus/Sonnet. The override is structural, not behavioral — Brain's S1 recommendation is correct, but the harness can't route down.

**Actionable:** S1 override rate will drop when Zone 2 (subagent routing) handles more S1 work via Sonnet. No Brain threshold change needed.

**Source:** `python tokens/interaction_tracker.py overrides`

---

## 4. Correction patterns (measured)

Reprompt rate: **1.9%** (4/210 decisions). Healthy — Claude's first response is accepted 98% of the time.

Trust calibration: **HEALTHY** — avg confidence on correction turns (0.150) is much lower than normal turns (0.555). This means Brain's uncertainty accurately predicts when the user will disagree.

**Implication:** Brain knows when it's unsure, and the user corrects exactly in those moments. The classifier is honest.

---

## 5. Session stall detection

Stall events (gaps > 5 minutes between turns): **detected but sparse.**

Stalls are natural in long sessions (6-15 hours) — the user takes breaks, switches to UE5 editor, checks your-trading-project. A stall is only concerning if it's followed by a correction (indicating confusion, not a break).

**Metric:** `stalls_with_correction / total_stalls` = confusion rate.

**Source:** `python tokens/interaction_tracker.py stalls`

---

## 6. Skill progression (measured)

Composite prompt engineering score: **74/100** (Proficient).

| Dimension | Score | Interpretation |
|-----------|-------|---------------|
| Clarity | 55/100 | Brain confidence averaging 0.55 — room to improve |
| Efficiency | 100/100 | Very concise prompts |
| Correction | 90/100 | 1.9% reprompt rate — excellent |
| Targeting | 71/100 | 29% override rate — structural, not behavioral |
| Delegation | 51/100 | Only 51% full delegation — system hasn't fully earned trust |

**Trend:** STABLE (7d matches 30d — not enough time series data yet).

**Weakest area:** Delegation trust. The system is in checkpoint/veto mode 32% of the time. As the human{} layer accumulates data and Brain thresholds calibrate, this should improve.

**Source:** `python tokens/prompt_quality.py report`

---

## 7. Integration with Stages 0-6

Stage 7 closes the pipeline loop:

```
Stage 0 (boot) → Stage 1 (prompt) → Stage 2 (context) → Stage 3 (routing)
    ↑                                                              ↓
    │                                                    Stage 4 (tools)
    │                                                              ↓
    │                                                   Stage 5 (response)
    │                                                              ↓
    │                                                   Stage 6 (persist)
    │                                                              ↓
    └──────────────── Stage 7 (HUMAN) ─────────────────────────────┘
```

**Feedback arrows:**
- Stage 7 → Stage 1: the user's next prompt is shaped by the quality of the last response
- Stage 7 → Stage 3: Override events feed back into Brain's learning pipeline (decisions.jsonl)
- Stage 7 → Stage 6: Correction patterns inform which memories are useful vs stale

---

## 8. Katanforoosh framework mapping

| Kian's Stage | Toke Pipeline Stage | Human Component |
|-------------|--------------------|-----------------| 
| Intent | Stage 1 (prompt) + Stage 7 (human decision) | Human forms intent → types prompt |
| Tools | Stage 3 (routing) + Stage 4 (execution) | Brain recommends → human can override |
| Plan | Zeus L2 (orchestration) | Human reviews plan via godspeed triage output |
| Execute | Stage 4 (tool calls) | Human monitors via permission gates |
| Evaluate | Stage 5 (response) + Stage 7 (human judgment) | Human reads response → continues/corrects/abandons |

**Key insight from Katanforoosh:** The pipeline is human-centric, not machine-centric. The machine stages (0-6) exist to serve the human stage (7). Optimizing Stages 0-6 is wasted if Stage 7 — the human's experience — degrades.

---

## 9. Instrumentation tools

| Tool | Command | What It Measures |
|------|---------|-----------------|
| interaction_tracker.py | `overview` | Full human metrics dashboard |
| interaction_tracker.py | `overrides` | Per-tier override analysis |
| interaction_tracker.py | `delegation` | Delegation mode distribution |
| interaction_tracker.py | `stalls` | Session stall detection |
| interaction_tracker.py | `progression` | Skill trend over time |
| prompt_quality.py | `report` | Composite skill score (0-100) |
| prompt_quality.py | `trend` | 7d vs 30d progression |
| brain_cli.py | `scan` | HUMAN METRICS section in brain scan |
| audit_protocol.py | `sacred-rules` | Sacred Rule compliance check |
