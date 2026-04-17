# Blueprint: Katanforoosh Gap Closure

> **Origin:** Comparison of Toke ecosystem vs Kian Katanforoosh's Stanford agentic AI thesis (April 2026).
> **Gaps identified:** Human-AI Interaction (4/10), Enterprise Readiness (2/10), Governance/Safety (5/10).
> **Target:** Close all three gaps to 8/10, 5/10, 8/10 respectively. Steal his best ideas.
> **Date:** 2026-04-12
> **Constraint:** v4.4 fit-in-don't-force — all additions bolt onto existing infrastructure. Zero restructuring.

---

## Executive Summary

Kian Katanforoosh teaches a 5-stage agentic pattern at Stanford: **Intent -> Tools -> Plan -> Execute -> Evaluate**. Toke already implements all 5 stages mechanically (Brain classifier -> godspeed router -> Zeus orchestrator -> tool deployment -> Oracle eval). But Toke instruments only the MACHINE side of the loop. Katanforoosh's core insight — "2026 is the year of the humans" — reveals 3 blind spots where Toke has zero measurement of the HUMAN side.

This blueprint closes those gaps with 8 concrete deliverables across 4 phases. Total new code: ~1,800 lines Python (stdlib only). Total new docs: ~600 lines markdown. Build time: 2 sessions.

---

## Gap Analysis (pre-build scores)

| Gap | Current | Target | Root Cause |
|-----|---------|--------|------------|
| Human-AI Interaction | 4/10 | 8/10 | Zero human-side instrumentation. decisions.jsonl logs what BRAIN decided, not what JACOB did next. |
| Enterprise Readiness | 2/10 | 5/10 | Intentionally personal (by design). Gap exists but is not a flaw — document the extraction path. |
| Governance/Safety | 5/10 | 8/10 | Sacred Rules are informal heuristics. No formal threat model. No unified audit trail. No adversarial testing. |

---

## Stolen Ideas (from Katanforoosh's framework)

### S1. Skill Assessment (from Workera)
Workera measures human skills and tracks progression. Toke should measure the user's **prompt engineering skill** over time: clarity (Brain confidence), efficiency (tokens per completed task), delegation accuracy (override rate decay).

**Deliverable:** `tokens/prompt_quality.py`

### S2. Augmentation Pattern Classification (from "Year of the Humans" thesis)
Katanforoosh classifies human-AI interaction into delegation modes. Every Toke decision should be tagged with its delegation mode: full / supervised / checkpoint / veto.

**Deliverable:** New `human.delegation_mode` field in decisions.jsonl

### S3. Workflow Instrumentation (from Stanford CS230 Lecture 9)
His Intent -> Tools -> Plan -> Execute -> Evaluate loop should be mapped explicitly to Toke's pipeline stages. The missing piece: a feedback loop from Evaluate back to Intent that captures the HUMAN response.

**Deliverable:** `pipeline/stage_7_human_interaction.md`

---

## Phase 1: Schema Extensions (foundation — everything depends on this)

### 1A. Extend decisions.jsonl with human behavioral layer

**File:** `Toke/automations/brain/brain_cli.py` (cmd_hook function)
**Change:** Add `human` dict to every JSONL entry

```json
{
  "ts": "2026-04-12T...",
  "hook": "UserPromptSubmit",
  "session_id": "...",
  "current_model": "opus",
  "result": { "...existing ClassificationResult fields..." },
  "human": {
    "turn_index": 14,
    "turns_since_correction": 3,
    "consecutive_corrections": 0,
    "session_override_count": 1,
    "session_reprompt_count": 2,
    "prompt_token_count": 142,
    "inter_turn_gap_seconds": 47.2,
    "delegation_mode": "supervised"
  }
}
```

**Implementation:**
- Read last N entries from decisions.jsonl for this session_id (already done for context_turns_seen)
- Compute `inter_turn_gap_seconds` = now - last_ts for same session
- Compute `turns_since_correction` by scanning backwards for `correction_detected_in_prompt: true`
- Compute `consecutive_corrections` by counting consecutive True from most recent
- Compute `session_override_count` by counting entries where `current_model != result.model`
- Compute `delegation_mode` from formula:
  ```python
  def delegation_mode(confidence, consecutive_corrections, guardrails_fired):
      if consecutive_corrections >= 2 or guardrails_fired:
          return "veto"
      if confidence < 0.30:
          return "checkpoint"
      if confidence < 0.70:
          return "supervised"
      return "full"
  ```
- `prompt_token_count` = `len(prompt_text) // 4` (rough estimate, same as Brain already does)

**Lines of code:** ~80 in brain_cli.py (new `compute_human_metrics()` function + integration in cmd_hook)
**Dependencies:** None new. Uses existing decisions.jsonl read pattern from brain_learner.py.
**Backwards compatible:** Yes. Old entries without `human` key are ignored by new readers.

### 1B. Extend brain_learner.py with human analytics

**File:** `Toke/automations/brain/brain_learner.py`
**Change:** New `summarize_human_state()` function

**Metrics computed:**
| Metric | Formula | What It Tells You |
|--------|---------|-------------------|
| `override_rate` | `overrides / total_decisions` (per tier) | Is Brain recommending the right model? |
| `reprompt_rate` | `correction_follows / total_decisions` | How often does the first response fail? |
| `abandonment_rate` | `sessions_with_early_correction_then_silence / total_sessions` | How often does the user give up? |
| `trust_calibration` | `corr(correction_detected, 1 - confidence)` | Does Brain's uncertainty match the user's? |
| `avg_delegation_mode` | Distribution of full/supervised/checkpoint/veto | How autonomous is the system? |
| `inter_turn_gap_p50` | Median gap between turns in seconds | Session pacing indicator |
| `inter_turn_gap_p95` | 95th percentile gap | Stall detection threshold |

**Lines of code:** ~120 in brain_learner.py
**Integration:** Called by `brain scan` to add a new "HUMAN METRICS" section to scan output.

---

## Phase 2: New Instruments

### 2A. interaction_tracker.py — Human-Side Analytics

**File:** `Toke/tokens/interaction_tracker.py`
**Purpose:** Standalone CLI tool that mines decisions.jsonl for human behavioral patterns.

**Commands:**
```bash
python interaction_tracker.py overview          # Full human metrics dashboard
python interaction_tracker.py overrides         # Override analysis by tier
python interaction_tracker.py delegation        # Delegation mode distribution
python interaction_tracker.py stalls            # Sessions with >5min gaps
python interaction_tracker.py progression       # Skill progression over time
python interaction_tracker.py --json            # Machine-readable output
python interaction_tracker.py --days 7          # Last N days only
```

**Output example (overview):**
```
HUMAN INTERACTION METRICS (201 decisions, 29 sessions)
======================================================
Override rate:        12.4% (25/201) — Brain recommended != actual model
  S0: 2.1%  S1: 8.3%  S2: 18.7%  S3: 33.3%  S5: 14.3%
Reprompt rate:        1.2% (2/165 post-smoke-test decisions)
Abandonment rate:     3.4% (1/29 sessions)
Trust calibration:    r=0.42 (healthy — corrections correlate with low confidence)
Avg inter-turn gap:   34s (p50) / 287s (p95)
Delegation modes:     full=68% supervised=22% checkpoint=7% veto=3%

SKILL PROGRESSION (30-day window)
==================================
Prompt efficiency:    +8.2% (fewer tokens per completed task)
Correction decay:     -0.3% (correction rate stable)
Tier targeting:       +4.1% (fewer overrides this week vs 30d avg)
```

**Lines of code:** ~350
**Dependencies:** decisions.jsonl (201+ entries). Stdlib only.

### 2B. prompt_quality.py — Prompt Engineering Skill Assessment

**File:** `Toke/tokens/prompt_quality.py`
**Purpose:** Tracks the user's prompt engineering skill over time. Inspired by Workera's skill measurement — but for a solo dev, not enterprise.

**Core metrics (all computed from decisions.jsonl):**

| Metric | Formula | Interpretation |
|--------|---------|---------------|
| Clarity Score | `mean(confidence)` per 7d window | Higher = prompts that Brain classifies with certainty |
| Efficiency Ratio | `target_tier_achieved / prompt_token_count` | Higher = achieving results with fewer words |
| Correction Decay | `reprompt_rate_7d - reprompt_rate_30d` | Negative = improving (fewer corrections lately) |
| Targeting Accuracy | `1 - override_rate_7d` | Higher = prompts land on the right tier |
| Delegation Trust | `pct(delegation_mode == "full")` over time | Rising = system earns more trust |
| Composite Skill | Weighted avg of above 5 metrics | Single 0-100 score |

**Output:**
```
PROMPT ENGINEERING SKILL REPORT — 2026-04-12
=============================================
Composite skill score: 72/100 (+3 from last week)

  Clarity:           78/100  (avg confidence 0.78)
  Efficiency:        65/100  (142 tokens/task avg, down from 168)
  Correction decay:  81/100  (reprompt rate falling)
  Targeting:         88/100  (override rate 12% → 8%)
  Delegation trust:  54/100  (full delegation 68%, up from 61%)

Trend: IMPROVING — all 5 dimensions positive over 30d window.
Weakest area: Delegation trust (system still in checkpoint/veto 32% of the time).
```

**Lines of code:** ~280
**Dependencies:** decisions.jsonl with human{} layer (Phase 1A must ship first).

### 2C. audit_protocol.py — Unified Governance Audit

**File:** `Toke/automations/governance/audit_protocol.py`
**Purpose:** Aggregates ALL Toke telemetry into a single auditable event stream. Weekly governance report.

**Data sources aggregated:**
| Source | Path | Events |
|--------|------|--------|
| decisions.jsonl | `~/.claude/telemetry/brain/decisions.jsonl` | Routing decisions |
| tools.jsonl | `~/.claude/telemetry/brain/tools.jsonl` | Tool calls (PostToolUse) |
| advisor_calls.jsonl | `~/.claude/telemetry/brain/advisor_calls.jsonl` | Advisor escalations |
| godspeed_count.txt | `~/.claude/telemetry/brain/godspeed_count.txt` | Godspeed tick count |
| VAULT checkpoints | `Toke/automations/homer/vault/state/` | Homer state snapshots |

**Audit event schema (unified):**
```json
{
  "schema_version": "1.0",
  "ts": "2026-04-12T14:23:01.442Z",
  "session_id": "abc123",
  "event_type": "routing_decision|tool_call|advisor_escalation|checkpoint|sacred_rule_check",
  "agent": "brain|zeus|godspeed|oracle",
  "action": "classify|invoke_tool|escalate|checkpoint|score",
  "detail": { "...event-specific fields..." },
  "risk_flags": [],
  "outcome": "allowed|blocked|warned"
}
```

**Risk flag detection (maps to OWASP Agentic Top 10):**
| OWASP Code | Flag | Detection |
|------------|------|-----------|
| ASI01 | `goal_hijack` | Prompt contains injection patterns ("ignore previous", "new instructions") |
| ASI02 | `tool_misuse` | Destructive tool call (rm, git reset --hard, DROP TABLE) |
| ASI03 | `privilege_abuse` | Tool call targets path outside CWD |
| ASI05 | `code_execution` | Shell injection patterns in tool input |
| ASI06 | `context_poison` | Tool result contains injection attempt |
| ASI08 | `cascade_risk` | 3+ tool calls in sequence all targeting same critical file |
| ASI10 | `rogue_agent` | Agent spawns subagent with model override |

**Commands:**
```bash
python audit_protocol.py report              # Weekly governance report
python audit_protocol.py events --days 7     # Raw unified event stream
python audit_protocol.py risks               # Risk flags only
python audit_protocol.py sacred-rules        # Sacred Rule compliance summary
python audit_protocol.py --json              # Machine-readable
```

**Lines of code:** ~400
**Dependencies:** All telemetry sources. Stdlib only.

---

## Phase 3: Documentation

### 3A. stage_7_human_interaction.md — The Missing Pipeline Stage

**File:** `Toke/pipeline/stage_7_human_interaction.md`
**Purpose:** Completes the pipeline by documenting the human feedback loop — from "Claude responds" back to "the user decides what to do next."

**Sections:**
1. What Stage 7 is — the human decision point after every Claude response
2. Delegation mode distribution — measured from decisions.jsonl
3. Override patterns — when and why the user disagrees with Brain
4. Session stall detection — gaps > 5min indicate confusion or context switch
5. Correction patterns — what triggers re-prompts, how to reduce them
6. Skill progression — is the human-agent loop getting tighter over time?
7. Integration with Stages 0-6 — feedback arrows back to Intent (Stage 1) and Routing (Stage 3)
8. Kian Katanforoosh mapping — explicit cross-reference to his 5-stage framework

**Key insight this stage documents:** The pipeline is not linear (0→6). It's a loop: Stage 6 (persistence) feeds into Stage 7 (human decision), which feeds back to Stage 1 (next prompt). The human IS a pipeline stage, not an external observer.

**Lines:** ~200

### 3B. threat_model.md — Formal Threat Enumeration

**File:** `Toke/automations/governance/threat_model.md`
**Purpose:** Maps OWASP Agentic AI Top 10 to Toke's specific attack surface.

**Structure per threat:**
```
## ASI-XX: [Threat Name]

**OWASP description:** [one line]
**Toke exposure:** [how this manifests in the user's ecosystem]
**Current mitigations:** [what already exists — Sacred Rules, Oracle, hooks]
**Residual risk:** LOW / MEDIUM / HIGH
**Proposed controls:** [what this blueprint adds]
```

**Toke-specific threats (beyond OWASP):**
| Threat | Description | Current Mitigation |
|--------|-------------|-------------------|
| Sacred Rule drift | Oracle heuristics miss new violation patterns | Nyx sleep-time audit |
| Theater accumulation | Skills/docs grow impressive but non-functional content | Oracle + Nyx theater detection |
| Cost spiral | Opus-heavy sessions compound without visibility | Brain scan + token_snapshot.py |
| Memory poisoning | Stale memories override current codebase truth | Memory verification protocol in CLAUDE.md |
| Hook bypass | settings.json edit disables safety hooks | audit_protocol.py detects missing hooks |

**Lines:** ~300

### 3C. extraction_guide.md — Portability Matrix

**File:** `Toke/automations/portability/extraction_guide.md`
**Purpose:** Classifies every Toke component as personal/portable/universal. Does NOT make Toke enterprise-ready — documents the path for anyone who wants to adapt it.

**Component matrix:**

| Component | Category | Personal Elements | Portable As-Is |
|-----------|----------|-------------------|----------------|
| Brain classifier | **Universal** | Tier thresholds tuned to the user's usage | Classifier + manifest + CLI |
| Brain hooks | **Portable** | Hook paths assume Windows/bash | Hook pattern + schema |
| Homer VAULT | **Universal** | None | Checkpoint store |
| Homer Zeus | **Portable** | Sacred Rules references | Orchestrator-worker pattern |
| Homer MUSES | **Portable** | Muse names (cosmetic) | Parallel subagent pattern |
| Homer Oracle | **Personal** | 13 Sacred Rules hardcoded | Theater detection algorithm |
| Homer Mnemos | **Universal** | None | Three-tier memory store |
| Homer Sleep agents | **Portable** | Learning paths assume the user's dirs | Agent patterns |
| Godspeed | **Personal** | 30+ tools, all the user's | Pipeline router pattern |
| Token tools | **Universal** | None | All 3 scripts |
| Pipeline docs | **Universal** | Session-specific measurements | Stage structure + methodology |
| interaction_tracker | **Universal** | None | Human metrics framework |
| prompt_quality | **Universal** | Skill thresholds | Skill assessment framework |
| audit_protocol | **Universal** | Risk flag thresholds | Audit aggregation pattern |

**Universal (use as-is):** 6 components
**Portable (minor config):** 4 components
**Personal (significant rework):** 3 components

**Lines:** ~200

---

## Phase 4: Integration + Wiring

### 4A. Wire interaction_tracker into brain scan

**File:** `Toke/automations/brain/brain_cli.py`
**Change:** Import `interaction_tracker` functions. Add "HUMAN METRICS" section to `cmd_scan` output.

### 4B. Wire audit_protocol into brain scan

**File:** `Toke/automations/brain/brain_cli.py`
**Change:** Add "GOVERNANCE" section to `cmd_scan` output with risk flag count and compliance summary.

### 4C. Add adversarial test cases to eval_prompts.json

**File:** `Toke/automations/brain/eval/eval_prompts.json`
**Change:** Add 10 adversarial prompts:
- 3 prompt injection attempts (ASI01)
- 2 tool misuse triggers (ASI02)
- 2 Sacred Rule violation attempts
- 2 context poisoning patterns (ASI06)
- 1 privilege escalation attempt (ASI03)

### 4D. Extend Oracle with routing-decision scoring

**File:** `Toke/automations/homer/oracle/oracle.py`
**Change:** New `score_routing_decision(classification_result, actual_outcome)` method.
- Takes a Brain classification + what actually happened
- Scores: did the recommended tier match what was needed?
- Feeds into audit_protocol as a governance event

---

## Build Sequence

| Phase | Deliverable | Depends On | Lines | Gap Closed |
|-------|-------------|------------|-------|------------|
| **1A** | decisions.jsonl human layer | Nothing | ~80 | Gap 1 foundation |
| **1B** | brain_learner.py human analytics | 1A | ~120 | Gap 1 metrics |
| **2A** | interaction_tracker.py | 1A | ~350 | Gap 1 (4→7/10) |
| **2B** | prompt_quality.py | 1A, 2A | ~280 | Steal: skill assessment |
| **2C** | audit_protocol.py | Nothing | ~400 | Gap 3 (5→7/10) |
| **3A** | stage_7_human_interaction.md | 2A data | ~200 | Gap 1 (7→8/10) |
| **3B** | threat_model.md | 2C framework | ~300 | Gap 3 (7→8/10) |
| **3C** | extraction_guide.md | Nothing | ~200 | Gap 2 (2→5/10) |
| **4A** | brain scan integration | 2A, 2C | ~40 | Wiring |
| **4B** | eval adversarial cases | 2C | ~50 | Gap 3 hardening |
| **4C** | Oracle routing scoring | 2C | ~60 | Gap 3 depth |

**Total new code:** ~1,900 lines Python (stdlib only)
**Total new docs:** ~700 lines markdown
**Critical path:** 1A → 2A → 2B → 3A (human-side chain)
**Parallel track:** 2C → 3B → 4B (governance chain)

---

## Post-Build Score Projections

| Gap | Before | After | Delta | How |
|-----|--------|-------|-------|-----|
| Human-AI Interaction | 4/10 | **8/10** | +4 | Stage 7 + interaction_tracker + prompt_quality + delegation modes |
| Enterprise Readiness | 2/10 | **5/10** | +3 | Extraction guide + component matrix (13 components classified) |
| Governance/Safety | 5/10 | **8/10** | +3 | OWASP threat model + audit_protocol + adversarial tests + Oracle routing scoring |

**Composite Toke score vs Katanforoosh:** 8.1/10 (up from 6.4/10)
- Pipeline completeness: 9→10 (Stage 7 completes the loop)
- Cost awareness: 10 (unchanged — already ahead)
- Self-improvement: 8→9 (skill progression tracking adds human-side self-improvement)
- Multi-agent orchestration: 9 (unchanged)
- Human-agent interaction: 4→8 (primary gap closed)
- Enterprise readiness: 2→5 (documented, not forced)
- Governance/safety: 5→8 (formal threat model + unified audit)
- Measurability: 9→10 (human-side metrics close the last blind spot)

---

## Design Principles (v4.4 compliance)

1. **Additive only.** Every deliverable is a NEW file. Zero modifications to existing pipeline stages, oracle.py schema, or classifier logic.
2. **Stdlib only.** All Python code uses only Python 3.11+ stdlib. No pip installs.
3. **decisions.jsonl is THE data source.** New tools mine existing data. Only Phase 1A extends the schema (additive — new `human` key, old entries still valid).
4. **Sacred Rule compliant.** No file deletions. No creative content. No cascading changes. Edit-only for existing files.
5. **Backwards compatible.** All new tools handle decisions.jsonl entries that lack the `human` key (pre-Phase-1A entries).

---

## What We're NOT Building

- Enterprise multi-user support (Toke is personal — extraction_guide documents the path, doesn't walk it)
- External API integrations for governance (everything is local, stdlib, JSONL)
- Real-time monitoring dashboard (CLI tools are sufficient for solo dev)
- ML-based threat detection (heuristic risk flags are more robust in sparse-signal regime)
- Formal certification or compliance documentation (this is a personal tool, not a product)
