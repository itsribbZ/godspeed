# Toke — Extraction & Portability Guide

> Classifies every Toke component as personal/portable/universal.
> Does NOT make Toke enterprise-ready — documents the path for anyone adapting it.
> Origin: blueprint_katanforoosh_gap_closure_2026-04-12.md, Phase 3C.

---

## Component Matrix

### Universal (use as-is, zero modification)

| Component | Path | What It Does | Lines |
|-----------|------|-------------|-------|
| Brain classifier | `automations/brain/severity_classifier.py` | Pure-function task classifier. JSON in → tier out. | ~300 |
| Brain manifest | `automations/brain/routing_manifest.toml` | Config for tiers, thresholds, weights, guardrails | ~250 |
| Brain tests | `automations/brain/brain_tests.py` | 26 smoke tests, all passing | ~200 |
| VAULT | `automations/homer/vault/vault.py` | Checkpoint store with auto-archive | ~250 |
| Mnemos | `automations/homer/mnemos/mnemos.py` | Three-tier memory (Core/Recall/Archival) with FTS5 | ~530 |
| token_snapshot.py | `tokens/token_snapshot.py` | Per-session token cost breakdown | ~465 |
| tool_breakdown.py | `tokens/tool_breakdown.py` | Per-tool cost/frequency analyzer | ~410 |
| per_turn_breakdown.py | `tokens/per_turn_breakdown.py` | Per-turn token attribution | ~375 |
| interaction_tracker.py | `tokens/interaction_tracker.py` | Human-AI interaction metrics | ~400 |
| prompt_quality.py | `tokens/prompt_quality.py` | Prompt engineering skill assessment | ~280 |
| audit_protocol.py | `automations/governance/audit_protocol.py` | Unified governance audit | ~400 |
| Pipeline stages 0-7 | `pipeline/stage_*.md` | 8-stage pipeline documentation | ~1400 |

**Total universal:** 12 components, ~5,260 lines. Ready to use in any Claude Code environment.

### Portable (minor config changes needed)

| Component | Path | Personal Elements | To Adapt |
|-----------|------|-------------------|----------|
| Brain CLI | `automations/brain/brain_cli.py` | Windows paths, the user's stats-cache | Change STATS_CACHE path |
| Brain hooks | `hooks/brain_advisor.sh` | Bash path to brain_cli.py | Update script path |
| Zeus | `automations/homer/zeus/SKILL.md` | References Sacred Rules, godspeed | Replace rule references |
| MUSES (3) | `automations/homer/muses/*/SKILL.md` | Muse names are cosmetic | Rename if desired |
| Sybil | `automations/homer/sybil/sybil.py` | Sacred Rule 6 hard-coded refusal | Adjust creative content policy |
| Sleep agents (3) | `automations/homer/sleep/*/` | Learning paths assume the user's dirs | Update TELEMETRY_DIR paths |
| Brain learner | `automations/brain/brain_learner.py` | Telemetry paths | Update TELEMETRY_DIR |

**Total portable:** 7 components. Config changes only — no logic rewrite.

### Personal (significant rework for other users)

| Component | Path | Why Personal | Extraction Path |
|-----------|------|-------------|-----------------|
| Godspeed | `~/.claude/skills/godspeed/SKILL.md` | 30+ tools, 12 CHAIN codes, all the user's projects | Extract pipeline router pattern, discard tool roster |
| Oracle | `automations/homer/oracle/oracle.py` | 13 Sacred Rules hardcoded as detection heuristics | Replace rule set with your own governance rules |
| Author | `~/.claude/skills/author/SKILL.md` | 5 KBs, 12 chains, Sworder-specific | Not portable — too domain-specific |

**Total personal:** 3 components. The PATTERNS are portable; the CONTENT is the user's.

---

## What's Portable (the value)

The portable value of Toke is not the specific tools — it's the **patterns**:

1. **Model routing pattern:** Classifier → manifest → hooks → telemetry → learning loop. Works for any Claude Code user who wants cost optimization.

2. **7-layer orchestration pattern:** VAULT → BRAIN → ZEUS → MUSES → SYBIL → MNEMOS → SLEEP → ORACLE. The architecture works regardless of which rules/tools populate it.

3. **Human-side instrumentation pattern:** decisions.jsonl + human{} layer → interaction_tracker → prompt_quality. Works for any user who wants to measure their prompting skill.

4. **Governance audit pattern:** Telemetry aggregation → risk flag detection → Sacred Rule (or any rule) compliance → weekly report. OWASP mapping is universal.

5. **Pipeline analysis methodology:** 8-stage trace of every token from prompt to persistence. The methodology works for any Claude Code environment.

---

## Quick Start (for someone extracting)

1. Copy `automations/brain/` to your project. Update paths in brain_cli.py.
2. Copy `tokens/` tools. They read from `~/.claude/` (Claude Code's standard telemetry).
3. Copy `automations/governance/audit_protocol.py`. Works with any decisions.jsonl.
4. Run `python brain_cli.py scan` to see your cost profile.
5. Run `python tokens/interaction_tracker.py overview` to see your interaction metrics.
6. Run `python tokens/prompt_quality.py report` to see your skill score.
7. Run `python automations/governance/audit_protocol.py report` for governance.

No pip install. No config file. No API keys (unless using `brain advise`).
