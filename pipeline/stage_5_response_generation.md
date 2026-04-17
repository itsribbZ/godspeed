# Pipeline Stage 5 — Response Generation

> **Goal:** document what happens when the model decides to stop calling tools and emit text — the final output stage of the pipeline, including formatting, output token economics, and the Stop hook.

**Status:** first-pass research with receipts. Output token data measured from current session (262 turns, 1.75M output tokens, $113 session cost).

---

## 1. What "response generation" actually is

After the tool execution loop (Stage 4) ends — either because the model has enough information or because it hit a blocker — the model emits text instead of (or alongside) tool_use blocks. This text is what the user sees.

Response generation is the ONLY stage where tokens flow exclusively to the output side. Stages 0-4 are input-heavy (context assembly, tool results). Stage 5 is output-heavy (model reasoning, text formatting, status indicators).

**The model decides when to stop the loop.** There is no harness-level iteration counter, no timeout on reasoning, no forced stop. The model emits text when it judges the task is complete, blocked, or needs user input.

---

## 2. Output token economics

From session `8c5879d6` (262 turns, Opus 4.6 — a heavy godspeed session with pipeline doc writes, producing ~4× more output than a typical session like `8470448f` at $46):

| Metric | Value |
|---|---|
| Total output tokens | 1.75M |
| Average per turn | ~6,679 |
| Output cost | $43.75 (at $25/MTok) |
| Share of session cost | **38.6%** of $113.47 total |

Output tokens are the second-largest cost component (after cache reads at ~$29.57). Unlike cache reads, output tokens have NO amortization — every output token costs $25/MTok, period.

### Output cost breakdown by turn type

Not all turns generate equal output:

| Turn type | Typical output | Cost per turn | Frequency |
|---|---|---|---|
| Tool-only (no text) | ~100-500 tokens (tool_use blocks) | $0.003-0.013 | ~40% of turns |
| Short response | ~200-1000 tokens | $0.005-0.025 | ~30% of turns |
| Full explanation/report | ~2000-10000 tokens | $0.05-0.25 | ~20% of turns |
| Large generation (doc write, skill body) | ~10000-50000 tokens | $0.25-1.25 | ~10% of turns |

The top 10% of turns by output size generate ~60% of output cost. These are the doc writes, stage analyses, and reconciliation reports — exactly the high-value deliverables.

---

## 3. The tool-to-text transition

The model's decision to emit text instead of tools follows observable patterns:

### Stop signals (model-internal)
| Signal | What triggers it |
|---|---|
| Task complete | All requested changes made, verified |
| Information gathered | Enough data to answer the question |
| Blocked | Needs user input, missing file, permission denied |
| Error cascade | 3+ failures on same target (Sacred Rule: stop and instrument) |
| Explicit completion | User asked for a status/report, model delivers |

### Mixed turns
The model can emit both tool_use AND text in a single turn. Common pattern:
```
[text: "Found the bug. Fixing now."]
[tool_use: Edit file_path="..." old_string="..." new_string="..."]
[text: "Fixed. The issue was..."]
```

Text before tools = status update. Text after tools = explanation. This is a model-decided formatting choice, not a harness feature.

---

## 4. Output formatting rules

The system prompt contains explicit formatting instructions that shape response generation:

### From the harness system prompt (documented)
| Rule | Effect |
|---|---|
| Length limit: ≤25 words between tool calls | Forces terse tool-interleaved text |
| Final response: ≤100 words unless detail needed | Keeps end-of-turn output compact |
| Github-flavored markdown | Tables, code blocks, headers |
| Monospace rendering (CommonMark) | Formatting must survive monospace display |
| No emojis unless requested | Clean output default |

### From CLAUDE.md chain (the user-specific)
| Rule | Effect |
|---|---|
| Sacred Rule #9: No options — auto-choose AAA | Eliminates "Option A vs B vs C" output patterns |
| Compact, sectioned output | Bullets over paragraphs |
| Lead with answer | No preamble, no "Let me think about..." |
| Status indicator at end | Session state visible at a glance |
| No trailing summaries | User can read the diff — don't narrate what just happened |
| AAA quality (Sacred Rule #13) | Every output ships-grade |

These rules reduce output token waste. "No options" alone likely saves 200-500 tokens per response that would otherwise enumerate trade-offs. "Lead with answer" eliminates ~50-100 tokens of preamble per turn.

---

## 5. The Stop hook

After the model finishes its response (emits text, no more tool calls), the `Stop` hook fires.

**stdin JSON:**
```json
{
  "session_id": "...",
  "hook_event_name": "Stop",
  "stop_hook_active": true
}
```

The Stop hook can:
- Allow normal completion (exit 0)
- Force continuation (exit 0 with `"decision": "block"`) — makes the model continue working
- Log the stop event for telemetry

**the user's setup:** No Stop hook wired. The model decides when to stop unilaterally. This is fine for the current workflow — the user's Sacred Rules already constrain completion behavior (never auto-close, always run close-session, etc.).

**Potential automation:** A Stop hook could enforce reconciliation before session end, or validate that all triaged tasks are accounted for. Currently this is model-discipline (godspeed's reconciliation protocol), not harness-enforced.

---

## 6. Extended thinking and output quality

Brain's classifier assigns extended thinking budgets per tier:

| Tier | Thinking budget | Effect on output |
|---|---|---|
| S0-S2 | 0 | Standard reasoning |
| S3 | 16,000 tokens | Deeper analysis before responding |
| S4 | 32,000 tokens | Architecture-level reasoning |
| S5 | 64,000 tokens | Maximum depth (godspeed sessions) |

Extended thinking tokens are NOT counted in output tokens — they're a separate budget that doesn't appear in transcripts. But they influence output quality: more thinking = more structured, more complete responses.

**the user's sessions are mostly S5 (godspeed + /effort max),** meaning 64K thinking tokens are available. This is the quality amplifier behind AAA-grade outputs.

---

## 7. Context compaction's impact on response quality

When context approaches the window limit (1M tokens), Claude Code triggers compaction — older messages and tool results are cleared. This affects response generation:

| Compaction state | Effect on output |
|---|---|
| Pre-compaction | Full history available, high-quality responses |
| During compaction | Possible quality dip — model loses older context |
| Post-compaction | Model recovers but may miss earlier decisions |

From Stage 2's measurements, the current session peaked at 454.8K tokens (~45% of 1M). Compaction hasn't fired yet, but on the user's 6-15 hour sessions, it's likely.

**The response quality cliff:** When compaction removes earlier tool results, the model may regenerate work it already did, re-read files it already read, or contradict earlier decisions. This is the primary quality risk in long sessions.

Mitigations:
- Incremental checkpoints (shared protocols §5) — write learnings DURING execution
- VAULT L0 — session manifest persists across compaction
- Mnemos Core — critical patterns survive in context-resident memory

---

## 8. Output token optimization opportunities

| Opportunity | Est. savings | Effort |
|---|---|---|
| Enforce ≤100 word responses more strictly | 5-10% of output tokens | Low — CLAUDE.md instruction |
| Eliminate tool result echoing | 2-5% of output tokens | Low — model discipline |
| Compress reconciliation reports | 3-5% per godspeed session | Low — template tightening |
| Reduce between-tool narration | 5-10% of output tokens | Medium — conflicts with "brief updates" requirement |
| Stop hook to validate before large outputs | Variable | Medium — new hook |

Total addressable: ~15-25% of output tokens, or ~$6-11 per session at current rates.

**Trade-off:** Output compression saves tokens but risks violating AAA quality (Sacred Rule #13). The $6-11/session savings must be weighed against output quality. For the user's workflow, quality is non-negotiable — savings should come from eliminating waste, not compressing value.

---

## 9. Status

- ✅ Output token economics measured (1.75M tokens, $43.75, 38.6% of session cost)
- ✅ Tool-to-text transition patterns documented
- ✅ Output formatting rules cataloged (harness + CLAUDE.md)
- ✅ Stop hook behavior documented
- ✅ Extended thinking budget model documented
- ✅ Compaction impact on response quality analyzed
- ✅ Output optimization opportunities identified with trade-offs
- ⏳ Per-turn output token distribution (needs per-turn breakdown tool)
- ✗ Extended thinking token measurement — **HARD WALL.** Extended thinking tokens do not appear in Claude Code transcripts or the API `usage` response object. They are consumed server-side and are not billed separately (included in output token pricing). There is no known way to measure actual thinking token usage per turn. This would require Anthropic to add a `thinking_tokens` field to the API response, which does not exist as of 2026-04-12. The Brain classifier's thinking budgets (S3=16K, S4=32K, S5=64K) are REQUEST caps, not measurements — we don't know how much the model actually uses.
- ⏳ Compaction event correlation with output quality drops

Next in the pipeline: **Stage 6 — Session Persistence.** Memory writes, project_status updates, learning pipeline health, close-session. The final stage.
