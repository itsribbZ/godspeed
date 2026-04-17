# Pipeline Stage 6 — Session Persistence

> **Goal:** document what persists across sessions, how it gets written, and the cost of maintaining cross-session state. The final pipeline stage — where one session's work becomes the next session's context.

**Status:** first-pass research with receipts. Measured against the Toke project's persistence artifacts (7 memory files, 1,181 lines, 6 transcripts, 22MB total project state).

---

## 1. What "session persistence" actually is

When a Claude Code session ends (or between turns via checkpoints), several mechanisms write state that future sessions will load:

| Mechanism | What persists | Where | Who writes it | Who reads it |
|---|---|---|---|---|
| **Auto-memory** | MEMORY.md index + memory files | `~/.claude/projects/<hash>/memory/` | Model (via Write/Edit) | Session boot (auto-loaded) |
| **Transcripts** | Full session conversation + tool results | `~/.claude/projects/<hash>/<session_id>.jsonl` | Harness (automatic) | `token_snapshot.py`, `tool_breakdown.py` |
| **CLAUDE.md** | Project instructions, rules, config | `cwd/CLAUDE.md` + `~/CLAUDE.md` | Model (via Edit) or user | Session boot (auto-loaded) |
| **Telemetry** | Brain decisions, tool calls, tick counter | `~/.claude/telemetry/brain/` | Hooks (automatic) | Brain CLI, godspeed tick |
| **Skills** | SKILL.md + _learnings.md | `~/.claude/skills/*/` | Model (via Write/Edit) | Session boot (frontmatter) |
| **Shared learnings** | Cross-skill patterns | `~/.claude/shared/` | Model (via Edit) | Skill pre-work (grep-first) |
| **Homer state** | VAULT, Mnemos tiers, Zeus dispatch | `Toke/automations/homer/` | Python scripts | Homer CLI, Zeus |
| **settings.json** | Hooks, permissions, plugins | `~/.claude/settings.json` | Model (via Edit) or user | Session boot (cold-loaded) |
| **SessionEnd hook** | Custom close-session data | Hook-defined | Hook script | Next session's init |

---

## 2. The persistence cost model

Every byte written to persistent state has two costs:

### Write cost (this session)
| Artifact | Typical write | Token cost | When |
|---|---|---|---|
| project_status.md update | ~200-500 lines Edit | ~2-5K output tokens ($0.05-0.13) | End of session |
| Memory file write | ~50-200 lines Write/Edit | ~1-3K output tokens ($0.03-0.08) | On discovery |
| MEMORY.md index update | ~1-5 lines Edit | ~100-500 output tokens ($0.003-0.013) | After memory write |
| Shared learnings append | ~10-30 lines Bash echo | ~0 (shell-append, no model tokens) | End of skill run |
| Telemetry JSONL append | ~1 line per decision/tool call | ~0 (hook-driven, no model tokens) | Per event |

### Read cost (next session)
| Artifact | Boot load | Per-turn cache read | Session lifetime cost (275 turns) |
|---|---|---|---|
| MEMORY.md (8 lines) | ~32 tokens | $0.016/M × 32 × 275 = $0.0001 | Negligible |
| project_status.md (591 lines) | ~8K tokens | $0.50/M × 8K × 275 = $1.10 | **$1.10** |
| Rosetta Stone | ~3K tokens | $0.50/M × 3K × 275 = $0.41 | $0.41 |
| Sacred Rules | ~2K tokens | $0.50/M × 2K × 275 = $0.28 | $0.28 |
| Shared protocols | ~4K tokens | $0.50/M × 4K × 275 = $0.55 | $0.55 |

**project_status.md is the most expensive persistent artifact** — at 591 lines / ~8K tokens, it costs ~$1.10 per 275-turn session just in cache reads. It's loaded at session boot and re-read on every turn for the entire session.

---

## 3. The memory system

Claude Code's auto-memory is file-based:

### Architecture
```
~/.claude/projects/<cwd-hash>/memory/
├── MEMORY.md              # Index file — always loaded (first 200 lines / 25KB)
├── project_status.md      # Current state
├── project_brain.md       # Brain product status
├── project_homer.md       # Homer product status
├── project_homer_roadmap.md
├── project_toke_mission.md
└── project_toke_ship_plan.md
```

### What MEMORY.md costs
- Loaded automatically on every session
- Truncated at 200 lines or 25KB
- Currently 8 lines — well under both limits
- Each pointer line costs ~0.001 per session in cache reads

### What memory files cost
Memory files are NOT automatically loaded — only MEMORY.md is. Individual memory files are loaded on demand when:
- The model decides a memory is relevant (reads it via Read tool)
- The init skill explicitly reads it (toke-init loads project_status.md)
- The model is instructed to "check memory"

**On-demand loading is the correct pattern.** A memory file that costs 8K tokens to read (like project_status.md) only costs cache-read rate for the turns after it's loaded, not the entire session. If loaded at turn 1, the cost is the same as boot-loaded. If loaded at turn 50 (on demand), it saves 50 × 8K × $0.50/M = $0.20.

### Memory write discipline

| When | What gets written | Who triggers |
|---|---|---|
| User says "remember X" | New memory file + MEMORY.md pointer | User-explicit |
| Session discovers durable fact | New/updated memory file | Model-initiated |
| close-session runs | project_status.md update | close-session skill |
| Init finds stale memory | Memory update/removal | Init skill |

**The stale memory problem:** Memories are written at a point in time but read in future sessions. A memory claiming "tool_breakdown.py doesn't exist yet" is harmful once it does exist. The auto-memory instructions say "verify memory is still correct by reading the current state of files."

**Cost of stale memories:** A stale memory costs the same cache-read tokens as a correct memory, plus the output tokens of verifying it, plus the confusion if it contradicts reality. Total: ~$0.05-0.20 per stale memory per session.

---

## 4. The close-session skill

the user's workflow always ends with explicit "close session." The close-session skill:

1. Captures work completed, decisions made, context for next session
2. Updates project_status.md with structured session entry
3. Checks if any session insight is durable enough for Rosetta Stone update (Sacred Rule #12)
4. Runs learning pipeline health check
5. Writes learning entries for any skills used

### What close-session preserves
| Artifact | Survives compaction? | Survives session end? | Survives project switch? |
|---|---|---|---|
| project_status.md | ✅ (on disk) | ✅ | ❌ (project-specific) |
| Memory files | ✅ (on disk) | ✅ | ❌ (project-specific) |
| MEMORY.md | ✅ (on disk) | ✅ | ❌ (project-specific) |
| Rosetta Stone | ✅ (on disk) | ✅ | ✅ (home-level) |
| Shared learnings | ✅ (on disk) | ✅ | ✅ (global) |
| Transcript .jsonl | ✅ (on disk) | ✅ | ❌ (project-specific) |
| Telemetry JSONL | ✅ (on disk) | ✅ | ✅ (global) |
| In-context knowledge | ❌ | ❌ | ❌ |

The critical gap: **in-context knowledge dies at session end.** Everything the model learned during the session that wasn't explicitly written to a file is lost. This is why incremental checkpoints (shared protocols §5) and close-session are load-bearing — they're the bridge between session-local knowledge and cross-session persistence.

---

## 5. The learning pipeline

Skills write learnings via two mechanisms:

### Shell-append (SL-079 — the fix that unblocked 5 skills)
```bash
echo "### [DATE] finding" >> ~/.claude/skills/[skill]/_learnings.md
```
Fires at Phase 0, before any Edit call. Survives context compaction because it's a disk write, not a context entry. This is the fix that activated devTeam, blueprint, bionics, marketbot, and brain's learning pipelines.

### Model-driven Edit (legacy, fragile)
```
Edit(_learnings.md, old_string="## Entries", new_string="## Entries\n### new entry")
```
Fails if context compaction removes the earlier Read of the file. Shell-append replaced this for checkpoint writes.

### Learning pipeline health metrics
| Metric | Healthy | Warning | Broken |
|---|---|---|---|
| _learnings.md entries per 10 invocations | ≥5 | 2-4 | <2 |
| Shared learnings SL-ID growth | Monotonic | Gaps | Stale (>30 days) |
| Freshness ratio | >0.5 | 0.3-0.5 | <0.3 |
| Checkpoint dirs orphaned >24h | 0 | 1-2 | 3+ |

---

## 6. Transcript economics

Every session generates a `.jsonl` transcript that persists on disk:

| Metric | Toke project |
|---|---|
| Transcripts | 6 sessions |
| Total disk | 22MB |
| Avg per session | ~3.7MB |
| Largest | ~10MB (275-turn godspeed session) |

Transcripts are NOT loaded into context — they're on-disk artifacts for `token_snapshot.py` and `tool_breakdown.py` analysis. They cost zero tokens per session but serve as the ground-truth data source for Toke's measurement tools.

### What transcripts contain
- Every user message
- Every assistant message (tool_use blocks + text)
- Every tool_result
- Token counts per turn (input, cache_read, cache_write, output)
- Model identifier per turn
- Timestamps

### What transcripts DON'T contain
- Extended thinking tokens (not in transcripts)
- Hook execution results (hooks are external, not in the API payload)
- Permission prompts/responses
- Compaction events (no explicit marker in transcripts)

---

## 7. The telemetry layer

Brain's telemetry persists across sessions:

| File | Contents | Growth rate | Size |
|---|---|---|---|
| `decisions.jsonl` | One line per UserPromptSubmit | ~10-20/session | 120KB (180 entries) |
| `tools.jsonl` | One line per PostToolUse | ~50-200/session (when activated) | 177B (1 entry — dormant) |
| `godspeed_count.txt` | Single integer | +1 per godspeed invocation | 2B |

**decisions.jsonl is the most valuable telemetry artifact.** It's the only instrument that captures prompt characteristics BEFORE the model processes them. Every future learning (Brain tuning, S4 threshold adjustment, correction detection) depends on this data.

**tools.jsonl will become the most voluminous** once the PostToolUse matcher fix activates. At ~50-200 tool calls per session × 29 sessions/week, it'll grow by ~5-20KB/day.

---

## 8. What Claude Code cannot currently do at Stage 6

| Limitation | Impact | Potential automation |
|---|---|---|
| No automatic project_status update | Relies on close-session skill (model-dependent) | SessionEnd hook could auto-append |
| No memory garbage collection | Stale memories accumulate, cost cache reads | Periodic memory audit (could auto-run in init) |
| No transcript pruning | Transcripts grow indefinitely on disk | Archival script for >30-day transcripts |
| No compaction event logging | Can't measure when/why compaction fires | Would need harness-level instrumentation |
| ~~No cross-session context carryover~~ | **RESOLVED 2026-04-12d.** VAULT v2 (SQLite, WAL-mode) shipped. `automations/homer/vault/vault_db.py` — 4 tables, 5 primitives (@checkpoint, durable_sleep, signals, timers, replay), 88/88 tests green. Homer 8/8 layers LIVE. | — |
| No session cost reporting at end | User doesn't see per-session cost | SessionEnd hook could run `token_snapshot.py --current` |

---

## 9. The full persistence stack, ranked by impact

| Rank | Mechanism | Cross-session value | Cost to maintain |
|---|---|---|---|
| 1 | **project_status.md** | High — captures decisions, progress, next-up | $1.10/session cache reads |
| 2 | **decisions.jsonl** | High — enables Brain learning pipeline | Zero (hook-driven) |
| 3 | **Shared learnings** | High — cross-skill pattern library | ~$0.55/session if loaded |
| 4 | **CLAUDE.md** | High — behavioral rules | ~$0.08/session (small file) |
| 5 | **Rosetta Stone** | High — user profile continuity | ~$0.41/session |
| 6 | **Transcripts** | Medium — measurement data source | Zero tokens (disk only) |
| 7 | **Skill _learnings.md** | Medium — per-skill calibration | On-demand (not auto-loaded) |
| 8 | **MEMORY.md** | Low — just pointers | ~$0.001/session |
| 9 | **tools.jsonl** | Medium (when active) — tool cost tracking | Zero (hook-driven) |
| 10 | **godspeed_count.txt** | Low — tick counter | Zero (script-driven) |

---

## 10. Status

- ✅ All persistence mechanisms documented (9 artifact types)
- ✅ Cost model per artifact (write cost + read cost per session)
- ✅ project_status.md identified as costliest persistent artifact ($1.10/session)
- ✅ Memory system architecture mapped
- ✅ Learning pipeline health metrics defined
- ✅ Transcript economics measured (22MB, 6 sessions, zero-token-cost data source)
- ✅ Telemetry layer documented
- ✅ Stale memory cost model ($0.05-0.20 per stale entry)
- ⏳ Memory garbage collection automation
- ⏳ Compaction event logging (needs harness instrumentation)
- ⏳ Session cost auto-reporting at SessionEnd

---

## 11. Pipeline complete

With Stage 6, the full 7-stage pipeline is documented:

| Stage | Title | Status | Key finding |
|---|---|---|---|
| 0 | Session Boot | ✅ | Boot tax = 13,832 tokens turn 1, 73K avg cache read/turn |
| 1 | Prompt Arrival | ✅ | S0 38.8%, S4 0% hole, skills hot-reload / hooks cold-reload |
| 2 | Context Assembly | ✅ | Write:read ratio 1:29.9, 30× cache amortization |
| 3 | Intent Routing | ✅ | All routing model-decided, Brain advisory only |
| 4 | Tool Execution | ✅ | Read = 58.8% result bulk, Edit = 5.5% error rate |
| 5 | Response Generation | ✅ | Output = 38.6% of session cost, $43.75/session |
| 6 | Session Persistence | ✅ | project_status.md = $1.10/session, stale memories $0.05-0.20 each |

The pipeline map is the foundation for Toke's optimization work. Every cost lever, every automation opportunity, every measurement gap is now cataloged with receipts.
