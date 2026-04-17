# Pipeline Stage 2 — Context Assembly

> **Goal:** map what actually gets packed into the prompt that flies to the Anthropic API on each turn — the stuff the model sees — using real measurements from a 275-turn Toke session.

**Status:** first-pass research with receipts. Measured against session `8470448f` (275 turns, 43M tokens read from cache across the session's lifetime).

---

## 1. What "context assembly" actually is

After the `UserPromptSubmit` hook fires (Stage 1) and before the API sees anything (Stage 3), Claude Code takes the following artifacts and packs them into a single `messages` payload:

| Artifact | Source | Cacheable? | Who loaded it? |
|---|---|---|---|
| **Core system preamble** | Hard-coded in Claude Code binary | ✅ 1h cache | Built-in, cannot change |
| **CLAUDE.md chain** | `~/CLAUDE.md` + `cwd/CLAUDE.md` + any parents | ✅ 1h cache | Session boot |
| **Tool schemas** — built-in | Hard-coded in Claude Code | ✅ 1h cache | Session boot |
| **Tool schemas** — deferred | `ToolSearch` fetches on demand | ✅ 1h cache when fetched | Per-turn (if fetched) |
| **Tool schemas** — MCP | MCP server handshake at session start | ✅ 1h cache | Session boot |
| **Skill frontmatter (all)** | Scanned from `~/.claude/skills/*/SKILL.md` | ✅ 1h cache | Session boot |
| **Memory index** | `~/.claude/projects/<hash>/memory/MEMORY.md` | ✅ 1h cache | Session boot |
| **Environment block** | `cwd`, `git branch`, `platform`, `date` | ✅ 1h cache | Session boot, refreshed on boundaries |
| **Turn history** — user messages | In-session user turns | ✅ 5m→1h cache rolling | Each user message |
| **Turn history** — assistant messages | In-session assistant turns (incl. tool uses) | ✅ 5m→1h cache rolling | Each assistant message |
| **Turn history** — tool_result blocks | Output of every Read / Bash / Grep / Agent call | ✅ 5m→1h cache rolling | **Every tool result** |
| **Active skill body** | The SKILL.md of any invoked skill (e.g. godspeed) | ✅ 1h cache once loaded | On first `Skill` invocation |
| **Current user prompt** | What the user just typed | ❌ fresh input | This turn |
| **Attachments** (paste-cache) | Any pasted content / attached files | ✅ 1h cache | When attached |

Claude Code's job in Stage 2 is **not to decide** what goes in — most of it is pre-committed by session boot. The per-turn decision is tiny: just the new user prompt + any newly-requested tool schemas. Everything else is cache-replay.

---

## 2. Measured reality — session 8470448f, 275 turns

Across the life of a single Toke session (275 assistant turns):

| Metric | Value | Interpretation |
|---|---|---|
| Fresh input tokens | **6,585** total | Each turn adds ~24 fresh tokens (the new prompt) |
| Cache creation (write) | **1,419,470** | Context grew the 1h cache by this much over 275 turns |
| Cache read | **42,493,860** | The model re-read this much warm context |
| Output tokens | 428,239 | Assistant responses |
| **Write:read ratio** | **1 : 29.9** | Every byte of warm context is read ~30 times |
| Effective context (turn 1) | 30,785 | Boot tax + first user prompt |
| Effective context (last turn) | 295,799 | Context grew ~9.6x over the session |
| Effective context (avg) | 159,709 | Median turn sits near 150K |
| Effective context (peak) | 295,799 | ~30% of the 1M context window |
| Per-turn ctx growth (median) | **+0** | Most turns don't expand context (skill/tool already cached) |
| Per-turn ctx growth (max) | +21,717 | One turn loaded a big file or new skill |

**The #1 insight:** context assembly is overwhelmingly **pay once, read many**. Over 275 turns, the session paid 1.4M in cache writes and got 42.5M in cache reads back. That's a **30× amortization** on the cache write tax.

**The #2 insight:** median growth is 0 tokens per turn. Claude Code isn't constantly re-inflating the context — once a skill or file is loaded, it stays cached for the session, and subsequent turns just pay the cache-read rate (~10% of the base input rate).

---

## 3. The growth events — when context jumps

Out of 275 turns, most are 0-delta replays. The non-zero growth events fall into buckets:

| Bucket | Trigger | Typical delta | Example |
|---|---|---|---|
| New file read | `Read` tool pulls a ~20KB file | +5K-10K tokens | Reading `brain_cli.py` (1092 lines) |
| New skill invoked | First `Skill` call on godspeed, zeus, etc. | +10K-15K tokens | First godspeed invocation |
| New `Bash` output bulk | A large `ls -la` or grep dump | +500-3K tokens | Listing 40 files |
| New `Agent` return | Subagent output lands in tool_result | +5K-30K tokens | Calliope research report (up to 30K) |
| Large paste | User pastes an error log or code block | ++ (varies) | Pasting a 200-line traceback |
| MCP activation | First MCP server tool call | +3K-5K tokens | First `plugin:context7` query |

The biggest single-turn growth in this session was **+21,717 tokens** (turn 23). Measured in session `d38ab304` with `per_turn_breakdown.py`: peak growth was +40,468 tokens (turn 195), caused by 35K output from a Read + Write sequence on the preceding turns — large tool results entering the 1h cache. Growth events consistently trace to tool result bulk, not skill loads.

---

## 4. How context reads are billed — the cache economics

Per Anthropic's verified pricing (2026-04-11):

- **Fresh input** (new prompt text): $5/MTok on Opus 4.6
- **Cache write 1h** (first time content enters warm cache): $10/MTok (2.0× base)
- **Cache write 5m** (5-minute ephemeral, rare in the user's workflow): $6.25/MTok (1.25× base)
- **Cache read** (all subsequent reads of warm content): **$0.50/MTok** (0.1× base — 10% of fresh input price)

For the measured session:
- Fresh input cost: 6,585 × $5/M = **$0.03**
- Cache write cost (1h): 1,419,470 × $10/M = **$14.19**
- Cache read cost: 42,493,860 × $0.50/M = **$21.25**
- Output cost: 428,239 × $25/M = **$10.71**
- **Session total: ~$46.18** (Opus 4.6)

**Key observation:** cache reads ($21) exceed cache writes ($14) in absolute dollars, but per-unit, cache reads are 20× cheaper than writes. The write-heavy cost shows how much content first enters the cache each turn; the read-heavy cost shows how much the model is processing.

If this session had been 1,000 turns instead of 275 (all turns beyond 275 assumed to be cache-hits with zero growth), the additional cost would be ~$0.18 per extra turn read — **context assembly gets cheaper per turn as the session runs longer**. Cache amortization wins.

---

## 5. What Claude Code cannot currently do at Stage 2

Observed limitations of the current Stage 2 assembly logic:

1. **No per-project skill scoping** — every session loads all ~70 registered skills' frontmatter regardless of cwd. A Toke session is paying to cache Sworder skills it will never use.
2. **No turn-level pruning** — old tool results stay in context forever (until compaction). A 5,000-char `Read` output from turn 3 is still re-loaded on turn 250.
3. **No "important vs debris" signal** — Claude cannot mark a tool result as "you won't need this again, drop it from context on the next turn." Tool result bulk is monotonic.
4. **No MCP lazy-load** — MCP server tool schemas are loaded at session boot, not on first invocation. An unused MCP still pays the boot tax.
5. **No cache shard control** — the whole session context is cached as one blob. You can't say "cache the system prompt aggressively but let the last 5 turns stay ephemeral."

Each of these is a potential token-drain lever for a future Toke automation.

---

## 6. The Toke-relevant cost vectors, ranked

Using the measured session as a baseline (~$46 for 275 turns), the biggest per-session savings opportunities:

| # | Lever | Est. savings per session | Effort |
|---|-------|--------------------------|--------|
| 1 | **Skill scoping** — only load skills relevant to cwd | 500-1500 tokens/turn × 275 turns × $0.50/M ≈ $0.35-1.00 | medium — Claude Code change |
| 2 | **Tool result aging** — drop results older than N turns when context > threshold | Varies; could recover 20-40K tokens on long sessions | high — protocol change |
| 3 | **Load heavy skills (godspeed, etc) on demand** | Godspeed = ~14K tokens; only load on actual invocation | medium — cache breakpoint |
| 4 | **Prune deferred tools further** | ~400 tokens / session | low |
| 5 | **Avoid `/fast` mode** | 6× base rate savings (documented trap) | zero — discipline |

Estimated AAA impact if all are applied: **~$1-3 per session**, or **~5-10%** off a typical Toke-session bill.

---

## 7. The "first turn is special" observation

Turn 1 effective context = **30,785 tokens**. That's the boot tax we already measured in `stage_0_session_boot.md`. But it's not a flat cost — it includes:

- Core system preamble: ~3,500 tok (fixed)
- CLAUDE.md chain: ~600 tok (fixed)
- Tool schemas: ~3,500 tok (grows with MCP, skills)
- Skill frontmatter (all 70): ~7,000 tok (dominant)
- Memory index: ~80 tok
- Environment block: ~200 tok
- Tail instructions: ~1,000 tok
- First user prompt: variable
- **Remainder ~14,900 tok** — unaccounted — likely MCP tool schemas (figma, context7, circleback, frontend-design, code-review, feature-dev, etc.) and plugin metadata

MCP schemas are the biggest single bucket you don't usually think about. This is Stage 2's biggest hidden cost — every session pays ~15K tokens to load MCP capability descriptions, many of which the session will never use.

---

## 8. Instrumentation opportunities

Questions Stage 2 leaves open that need a dedicated tool to answer:

- [ ] **Per-MCP-server boot tax** — how many tokens does each MCP server contribute? (Measure by toggling them off one at a time and diffing turn-1 cache write.)
- [ ] **Per-skill boot tax** — frontmatter-only vs full body. How much does *registering* a skill cost vs *loading* it?
- [ ] **Tool result half-life** — what's the median turn lifetime of a tool result before it becomes dead weight?
- [ ] **Growth event attribution** — which specific turn event caused the +21,717 peak? Needs a per-turn delta log.
- [ ] **Cache hit rate over session length** — does the ratio improve with longer sessions, and at what rate?

All of these become trivial to answer once a `tokens/per_turn_breakdown.py` exists. That's the natural next tool in the `tokens/` folder — turn-by-turn attribution of cache writes to the event that caused them.

---

## 9. What this stage teaches us about the broader pipeline

Stage 2 is where the cache economics of Claude Code live. It's the layer that makes long sessions sustainable — without it, every turn would repay the full system prompt + history, and the 30th turn would be ~30× more expensive than the 1st. With cache, the 275th turn costs roughly the same as the 5th.

**The implication for Toke's mission:**
- Brain (Stage 3 — routing) cannot meaningfully cut cost if Stage 2 is already cache-dominated. The main session's bill is driven by cache reads + cache writes, and routing can't rewrite what's already in cache.
- The measurable cost levers all live at cache-write time (what enters the cache) and session-start time (what was pre-loaded). Once the cache is warm, Claude Code is efficient.
- Toke's highest-ROI instrumentation work is therefore:
  1. Measuring **what enters the cache** (pipeline/tokens work — this stage)
  2. Deciding **what should enter the cache** (skill scoping, MCP pruning, deferred tools)
  3. Detecting **when cached content becomes dead weight** (tool result aging)

---

## 10. Status

- ✅ Context assembly inventory mapped against real transcript
- ✅ Per-turn economics measured on 275-turn session ($46 baseline)
- ✅ Cache write:read ratio measured (1 : 29.9)
- ✅ Growth-event buckets identified (file read, skill load, agent return, paste, MCP activation)
- ✅ Hidden MCP cost flagged (~15K tokens turn 1)
- ✅ Per-turn attribution tool built (`tokens/per_turn_breakdown.py`, 2026-04-12)
- ⏳ Cold-boot differential (MCP on vs off) not yet captured
- ⏳ Skill-frontmatter-only vs full-body-load cost not yet measured

Next in the pipeline: **Stage 3 — Intent routing.** Brain's classifier already instruments this, but the upstream question is: once the prompt + context are assembled, how does Claude Code decide which tools, which skills, which subagents to invoke? That's the boundary between Brain (advisory) and Claude Code (authoritative).
