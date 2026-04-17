# Skills Index — All 36 Installed Skills

**Master reference for every skill/tool Claude Code has access to.**
Use this file when you need to find, reference, or modify any skill.

---

## Where Skills Live

All skills are stored at: `~\.claude\skills\<skill-name>\`

Each skill folder contains:
- `SKILL.md` — the main instructions file Claude reads when the skill fires
- `_learnings.md` — persistent learnings written back by the skill
- Optional: helper scripts, JSON configs, prompts, sub-templates

**To modify a skill** → edit `~\.claude\skills\<name>\SKILL.md` directly.

---

## Tier 0 — Context Pre-Load (runs FIRST on Sworder tasks)

| Skill | Path | Trigger | Purpose |
|---|---|---|---|
| **author** | `.claude\skills\author\` | Sworder KB work | Master prefix-coded routing for Bible/UE/Blend/GDD/Roadmap. Returns 12 logical chains in dependency order. |

---

## Tier 1 — Holy Tools (highest authority)

| Skill | Path | Trigger | Purpose |
|---|---|---|---|
| **godspeed** | `.claude\skills\godspeed\` | "godspeed" | Max execution mode. Auto-routes to the right tool for every task. |
| **holy-trinity** | `.claude\skills\holy-trinity\` | Complex research+impl | Diagnose → Research → Implement → Verify loop |
| **devTeam** | `.claude\skills\devTeam\` | Code architecture review | Calibrated scoring with 7 Laws, regression guard |
| **profTeam** | `.claude\skills\profTeam\` | Deep parallel research | Multi-agent research engine with confidence matrix |

---

## Tier 2 — Production Tools (frequent use)

| Skill | Path | Trigger | Purpose |
|---|---|---|---|
| **bionics** | `.claude\skills\bionics\` | UE5 automation | Prompt → Doctor → Blueprint → Execute pipeline |
| **ue-knowledge** | `.claude\skills\ue-knowledge\` | UE5 API lookup | 46K+ lines, 9 zones of engine reference |
| **blueprint** | `.claude\skills\blueprint\` | Implementation plan | Actionable blueprint from existing codebase |
| **cycle** | `.claude\skills\cycle\` | Refine a blueprint | 3-pass iterative refinement |
| **professor** | `.claude\skills\professor\` | Deep topic research | Single-topic deep dive with PDF output |
| **player1** | `.claude\skills\player1\` | Gameplay QA | Veteran gamer fun/feel/balance review |
| **marketbot** | `.claude\skills\marketbot\` | Marketing content | Research-backed marketing generator |
| **scanner** | `.claude\skills\scanner\` | PDF reading | Content extraction + search in PDFs |
| **blend-master** | `.claude\skills\blend-master\` | Blender work | Visual feedback loop for 3D asset creation |

---

## Tier 2.5 — Operations Tools (cross-cutting)

| Skill | Path | Trigger | Purpose |
|---|---|---|---|
| **brain** | `.claude\skills\brain\` | Cost/routing/advisor | Model routing classifier + advisor API wrapper |
| **debug** | `.claude\skills\debug\` | Bug/error/crash | Localize → context → logger → fix → persist |
| **verify** | `.claude\skills\verify\` | Build verification | Auto-detects project type, runs appropriate checks |
| **sitrep** | `.claude\skills\sitrep\` | Cross-project status | Birds-eye view of every project |
| **pulse** | `.claude\skills\pulse\` | your-trading-project watchdog | Checks if trading engine is actually executing |
| **simplify** | `.claude\skills\simplify\` | Code quality review | Reuse + quality + efficiency pass |

---

## Tier 3 — Utility Tools

| Skill | Path | Trigger | Purpose |
|---|---|---|---|
| **find** | `.claude\skills\find\` | Sworder search | File/Bible/GDD search router |
| **finder** | `.claude\skills\finder\` | Editor guides | Step-by-step UE5 Editor walkthroughs |
| **organizer** | `.claude\skills\organizer\` | Folder structure | Professional file/folder organization |
| **reference** | `.claude\skills\reference\` | Link tracking | Living reference tracker |
| **analyst** | `.claude\skills\analyst\` | Session analysis | Developer psychology + session review |
| **knowledge-router** | `.claude\skills\knowledge-router\` | KB routing | (Haiku-pinned router) |
| **close-session** | `.claude\skills\close-session\` | End of session | Memory persistence + v2.0 structured learnings |

---

## Init Skills (load project context)

| Skill | Path | Trigger | Project |
|---|---|---|---|
| **init** | `.claude\skills\init\` | "init" | Universal router — auto-detects CWD |
| **sworder-init** | `.claude\skills\sworder-init\` | "sworder init" | your-game-project UE5 project |
| **quantified-init** | `.claude\skills\quantified-init\` | "quantified init" | your-trading-project Trading Engine |
| **forge3d-init** | `.claude\skills\forge3d-init\` | "forge3d init" | your-3d-project C++/Vulkan sculpting |
| **buddy-init** | `.claude\skills\buddy-init\` | "buddy init" | Buddy AI learning track |
| **enigma-init** | `.claude\skills\enigma-init\` | "enigma init" | Enigma quantum security track |
| **ribbz-init** | `.claude\skills\ribbz-init\` | "ribbz init" | Ribbz branding project |
| **career-ops-init** | `.claude\skills\career-ops-init\` | "career-ops init" | AI job search pipeline |
| **toke-init** | `.claude\skills\toke-init\` | "toke init" | Toke meta-automation project |
| **ue-knowledge-init** | `.claude\skills\ue-knowledge-init\` | "ue-knowledge init" | UE5 engine bible session |

---

## Key Absolute Paths

```
Skills root:          ~\.claude\skills\
Shared protocols:     ~\.claude\shared\
Memory (Desktop):     ~\.claude\projects\C--Users-user-Desktop\memory\
Memory (home):        ~\.claude\projects\C--Users-user\memory\
Telemetry:            ~\.claude\telemetry\
Brain automations:    ~\Desktop\T1\Toke\automations\brain\
Godspeed counter:     ~\.claude\telemetry\brain\godspeed_count.txt
```

---

## How to Modify a Skill (The Golden Path)

1. **Find it**: look up the skill name above → path is `.claude\skills\<name>\`
2. **Read it first**: `Read ~\.claude\skills\<name>\SKILL.md`
3. **Edit it**: use Edit tool (Sacred Rule #7 — NEVER Write over an existing SKILL.md)
4. **Test it**: fire the trigger phrase to confirm the change took effect
5. **Document it**: skill will write back to `_learnings.md` on next fire

---

## Recent Heavy-Activity Skills (edited within the last 48 hours)

As of 2026-04-11:
- **godspeed** (v4.2+ — Phase -1 tick, advisor escalation, v4.3 deletion proposals, v4.4 fit-don't-force)
- **author** (v1.0 — 12 logical chains, drift-proof validator)
- **bionics** (v0.5 — 150 Python + 22 C++ tools, AnimGraph automation)
- **brain** (v2.1 — shared protocols, subagent env var routing, advisor_20260301)
- **close-session** (v2.0 — structured learning format, ecosystem health snapshot)
- **devTeam** (Calibration Echo v4.0, shell-append Phase 0)
- **blueprint** (shell-append Phase 0 fix)
- **marketbot** (shell-append Phase 0 fix)

---

## Related Documentation

- `~\.claude\shared\_shared_protocols.md` — v2.1 shared protocols all skills inherit
- `~\CLAUDE.md` — Sacred Rules + behavioral rules + key paths
- `~\.claude\projects\C--Users-user-Desktop\memory\MEMORY.md` — memory index
- `Desktop\ROSETTA_STONE.md` — full user profile
- `Desktop\T1\Toke\PROJECT_BRIEF.md` — Toke meta-automation project context

---

**Maintained by**: godspeed + close-session. Update this file when a new skill is added, renamed, or deleted from `.claude\skills\`.
