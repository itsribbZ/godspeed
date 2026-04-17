---
name: Katanforoosh Agentic AI Reference
description: Kian Katanforoosh's Stanford agentic AI thesis, how Toke compares, and the gap closure work shipped 2026-04-12
type: reference
originSessionId: 833a9001-6a1e-4747-b25f-32bcaa81b0ba
---
# Kian Katanforoosh — Agentic AI Reference

## Who he is
- CEO & Founder of Workera (AI agent platform for skill measurement)
- Stanford AI Adjunct Lecturer — co-created CS230 (Deep Learning) with Andrew Ng
- Walter J. Gores Award winner (Stanford's highest teaching honor)
- WEF Technology Pioneer 2025, spoke at Davos 2026
- Twitter: @kiankatan

## His core 2026 thesis
- **"2026 will be the year of the humans"** — not the agents
- **"AI has not worked as autonomously as we thought"** — hype overshot
- Focus shifting from AI replacing → AI augmenting human workflows
- MCP reducing friction for connecting agents to real systems
- Companies should hire for AI governance, transparency, safety roles

## His 5-stage agentic pattern (CS230 Lecture 9 + Stanford Online course)
```
INTENT → TOOLS → PLAN → EXECUTE → EVALUATE
```
Maps to Toke: Brain classifier → godspeed router → Zeus → tool deployment → Oracle

## Stanford courses
- CS230: Deep Learning (with Andrew Ng) — Autumn 2025 lectures on YouTube
- Agentic AI in Action (Stanford Online) — tool calling, API integration, multiagent orchestration
- CS329A (Self-Improving AI Agents) taught by others at Stanford, not Kian directly

## Toke vs Katanforoosh comparison (2026-04-12)
Toke was ahead on: cost awareness (10/10), self-improvement (8/10), measurability (9/10), multi-agent orchestration (9/10).
Toke was behind on: human-AI interaction (4/10), enterprise readiness (2/10), governance (5/10).

## Gap closure shipped (2026-04-12, 8/11 deliverables)
- **Human-AI gap 4→8/10:** interaction_tracker.py, prompt_quality.py, human{} layer in decisions.jsonl, Stage 7 pipeline doc
- **Enterprise gap 2→5/10:** extraction_guide.md (12 universal / 7 portable / 3 personal components)
- **Governance gap 5→8/10:** audit_protocol.py (OWASP ASI01-10), threat_model.md, Sacred Rule compliance (13/13)
- **Remaining (Phase 4):** audit_protocol wiring into brain scan, adversarial eval test cases, Oracle routing-decision scoring

## Key findings from real data (210 decisions, 32 sessions)
- Override rate: 29% overall, S1=56.7% (structural — Brain says Haiku, /effort max forces Opus)
- Trust calibration: HEALTHY (confidence 0.15 on corrections vs 0.55 normal)
- Prompt skill: 74/100 (proficient). Weakest: delegation trust (51%). Strongest: efficiency (100%)
- Sacred Rules: 13/13 CLEAR across 852 auditable events
- Delegation modes: full=51%, supervised=18%, checkpoint=17%, veto=15%

## Where to find his content
- Twitter: https://x.com/kiankatan
- Stanford Online: Agentic AI in Action course
- CS230 Autumn 2025 YouTube playlist
- CXOTalk episode 837
- WEF Radio Davos + Meet The Leader podcasts
- Workera blog: workera.ai/blog
