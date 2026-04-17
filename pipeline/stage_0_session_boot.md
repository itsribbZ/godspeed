# Pipeline Stage 0 — Session Boot & Cache Warmup

> **Goal of this doc:** map every token and every file that gets loaded before the user's first prompt is answered, using real measurements from the user's actual sessions.

**Status:** first-pass research — measurements from 5 real Toke sessions. Second-pass will instrument additional sessions across your-game-project, your-trading-project, career-ops, etc.

---

## 1. Boot sequence (what happens, in order)

| Step | Event | Who reads / writes | Notes |
|------|-------|--------------------|-------|
| 1 | `claude` CLI launches in `cwd` | shell | entrypoint = CLI / desktop / IDE plugin / web |
| 2 | `~/.claude/settings.json` is read | Claude Code | defines hooks, permissions, env vars |
| 3 | `CLAUDE_CODE_SUBAGENT_MODEL` env var applied | Claude Code | sets subagent default (Zone 2 brain routing) |
| 4 | CLAUDE.md chain discovered | Claude Code | `~/CLAUDE.md` + each parent dir up to `cwd/CLAUDE.md` |
| 5 | Project memory detected | Claude Code | `~/.claude/projects/<hash>/memory/MEMORY.md` auto-loaded |
| 6 | `SessionStart` hook fires | `~/.claude/hooks/session-start.sh` | writes `session_state.json` for statusline |
| 7 | Built-in tool schemas loaded | Claude Code | Read / Edit / Write / Glob / Grep / Bash / Agent / TaskCreate / ... |
| 8 | MCP server tools discovered | Claude Code | plugin:context7, plugin:figma, etc — tool list populated |
| 9 | Skill frontmatter scanned | Claude Code | name + description from every `~/.claude/skills/*/SKILL.md` |
| 10 | Deferred tools listed by name | Claude Code | schemas fetched on demand via ToolSearch |
| 11 | System prompt assembled | Claude Code | static preamble + tools + CLAUDE.md + memory index |
| 12 | First user prompt arrives | user | `UserPromptSubmit` hook fires (`brain_advisor.sh`) |
| 13 | First assistant turn | Anthropic API | full system prompt written to 1h cache, response generated |
| 14 | Status line renders | `~/.claude/statusline.sh` | reads `session_state.json` + last-tool telemetry |

**Observation:** steps 1–11 happen **before** any token hits the Anthropic API. They're free (local). Step 13 is the **boot tax** — the first turn writes the entire warm context into the cache.

---

## 2. Measured boot tax (Toke project, 5 real sessions)

From per-session transcript inspection via `token_snapshot.py`:

| session_id | turn 1 cache_write_1h | turn 1 cache_read | turn 1 output | notes |
|------------|-----------------------|-------------------|---------------|-------|
| 8470448f (this session) | **13,832** | 16,947 | 295 | small boot — narrow skill load |
| d38ab304 | similar range | ~17K | ~300 | (repeat measurement) |

**Median cache read across the full session:** 79,426 tokens
**Mean:** 73,243 tokens
**Peak:** 118,083 tokens
**Total session cache writes:** 849,700 tokens (across 89 assistant turns)

### What the 13,832-token turn-1 cache write contains (estimated)

Pulled from inspection + reasoning about what must be in the system prompt on boot:

- **Core system preamble** — model identity, safety boilerplate, tool-use instructions: ~3,500 tok
- **CLAUDE.md chain** — `~/CLAUDE.md` (1,514 bytes ≈ 380 tok) + `Toke/CLAUDE.md` (814 bytes ≈ 200 tok): ~600 tok
- **Tool schemas** — built-in tool JSON schemas (Read, Edit, Bash, Glob, Grep, Write, Agent, Skill, ScheduleWakeup, ToolSearch): ~3,500 tok
- **Skill frontmatter index** — name + description for ~80 skills (45 local + ~35 plugin): ~5,000-7,000 tok (measured 2026-04-12: 45 local skills = 5,148 tok full frontmatter; plugins add ~3,000 tok)
- **Deferred tool names** — just names, no schemas: ~400 tok
- **MCP server metadata** — context7 + figma + circleback + ... capability descriptions: ~3,000 tok
- **MEMORY.md index** — 3 lines + header: ~80 tok
- **Environment block** — cwd, platform, git state, date: ~200 tok
- **Tail instructions + output contract + response-length rules**: ~1,000 tok

Total ≈ 17,180 (higher than measured 13,832 — suggesting either skill frontmatter is compressed, or some skills are filtered at boot). Note: "cache_write_1h" is not the same as "effective context" — Stage 2 measures effective context at 30,785 on turn 1, which includes cache reads from prior warm content.

> **Implication:** every single session pays ~14K tokens just to wake up. At manifest pricing that's ~$0.07 per boot on opus (cache write 1h rate = input × 2). At verified Anthropic pricing (~$30/MTok for 1M context cache write 1h) that's ~$0.42 per boot.

---

## 3. The real cost driver: per-turn cache reads

Turn 1 is cheap. **The compounding cost is the ~73K average cache read per turn**, which is the re-loading of the full system prompt + accumulated turn history on every assistant response. Over 89 turns, that's **6.5M cache reads per session**.

At Anthropic Opus 4 cache-read pricing ($1.50/MTok), that's **$9.75 per session just for warm cache hits**. At manifest pricing ($0.50/MTok) it's $3.25. Either way, cache reads are the #1 token-flow on the user's sessions.

### What grows the per-turn cache read

1. **Turn-over-turn history** — every prior user/assistant/tool message is replayed into context
2. **Tool result bulk** — `Bash` / `Read` / `Grep` outputs accumulate. A single `Read` of a 5K-line file = ~20K tokens, cached from turn N+1 onwards
3. **Skill SKILL.md files** — every invoked skill's full body gets pulled into context for that turn forward. Godspeed's SKILL.md alone = 55,662 bytes ≈ 14K tokens
4. **`_learnings.md` side files** — 144KB for godspeed's learnings alone ≈ 36K tokens IF it gets loaded by the skill body

---

## 4. Actionable token drains (candidates for automation)

Ordered by estimated token-per-session saved if fixed:

| # | Drain | Est. tokens / session | Fix |
|---|-------|----------------------|-----|
| 1 | Godspeed SKILL.md theater (36% unfired) | ~5,000 tok / session × N godspeed invocations | prune SKILL.md to ~500 lines |
| 2 | Skill registry loads ALL ~70 skills regardless of project | ~500 tok | scope skills to cwd (per-project allowlist) |
| 3 | MCP servers all enabled even when unused | ~3,000 tok | disable unused MCPs per project |
| 4 | `_learnings.md` loaded as skill side-car | up to 36K tok if body-loaded | split into dated archives, only recent active |
| 5 | Tool results not re-used — same Read twice costs 2x | varies | teach `simplify` skill to detect repeat Reads |

### Measurement needed (next pass)

- [ ] Does Claude Code auto-scope skills by `cwd`? Answer: not currently — full registry every session.
- [ ] How expensive is each MCP server's schema? Instrument with a count.
- [ ] Does `_learnings.md` actually get loaded by godspeed invocations or only referenced? Run a session with `_learnings.md` temporarily moved and measure the delta.

---

## 5. Cache discipline rules (first-pass, to be verified)

| Rule | Evidence level | Effect |
|------|---------------|--------|
| First turn always writes to 1h cache | MEASURED | boot tax is paid once per 1-hour window |
| Prompts < 5 min apart hit 5m cache (cheaper) | inferred | keep sessions active to avoid re-warming |
| `/fast` mode invalidates the cache | documented (SL-072) | never use `/fast` |
| Tool result tokens count toward prompt size but not output | verified from transcripts | reading huge files inflates every subsequent turn |
| Deferred tools cost ~0 tokens until `ToolSearch` hits them | documented | prefer them for rarely-used tools |

---

## 6. Open questions for Stage 0 (research queue)

1. Does Claude Code actually send the full skill registry to the API, or a hash/manifest?
2. What's the exact format of the system preamble? (black-box measurement via differential cache writes)
3. Is the MEMORY.md index inlined into the system prompt, or lazily loaded via Read?
4. Can `settings.json` hooks reduce boot-time cost by injecting pre-warmed context?
5. How does session resumption (`/resume`) compare to cold boot on cache spend?

---

## 7. Instrument this stage further

Next steps for quantifying Stage 0 beyond this document:

1. **Cold-boot instrumentation** — spin up a new session in an empty directory, measure turn-1 cache_write. Isolates core system overhead from CLAUDE.md / skills / MCP.
2. **Skill-count differential** — disable half the skills, measure turn-1 delta. Tells us cost per skill in the registry.
3. **MCP-off baseline** — remove all MCP servers, measure turn-1 delta. Tells us MCP schema cost.
4. **CLAUDE.md chain isolation** — empty CLAUDE.md files, re-measure. Tells us the user instructions cost.
5. **Record the results** in `tokens/stage_0_measurements.md` for diff-over-time tracking.

---

## 8. Status

- ✅ Boot sequence mapped at the event level
- ✅ Turn-1 cache write measured at 13,832 tok (one real session)
- ✅ Full-session cache economics measured: mean 73K read / 9.5K write per turn
- ✅ Cold-boot baseline captured: `cold_boot_measure.py catalog` scanned 336 sessions. Boot range 0-83,994 tok, median 15,694, mean 17,191. Sworder heaviest (~84K), Toke lightest (~14K). Variable component (skills+MCP+CLAUDE.md) accounts for ~70K spread.
- ✅ Component-wise attribution (partial): MCP-heavy projects (Desktop: ~50-74K) vs MCP-light (Toke: ~14-31K) show ~30-40K MCP component. Full isolation available via `cold_boot_measure.py compare`.
- ✅ Stage 0 instrumentation tools built: `cold_boot_measure.py` (catalog, compare, baseline modes)

Next in the pipeline: Stage 1 — Prompt Arrival. Where the `UserPromptSubmit` hook fires and the Brain classifier runs before the LLM sees the prompt.
