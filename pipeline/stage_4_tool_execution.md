# Pipeline Stage 4 — Tool Execution Loop

> **Goal:** document what happens AFTER the model decides to call a tool (Stage 3) — the full PreToolUse → execute → PostToolUse → result injection lifecycle, with real measurements of tool cost, frequency, and failure patterns.

**Status:** first-pass research with receipts. Tool frequency measured across 6 Toke sessions (1,339 turns, 806 tool calls). Lifecycle details from Claude Code docs via claude-code-guide agent.

---

## 1. What "tool execution" actually is

After the model emits a `tool_use` block (Stage 3), Claude Code runs a deterministic pipeline to actually execute the tool and feed the result back. Unlike Stage 3 (model-decided), Stage 4 is **harness-controlled** — the model has no say in how tools execute, only which tools to call and with what parameters.

```
Model emits tool_use block(s)
    ↓
┌─ For each tool_use: ────────────────────────────────────────┐
│                                                             │
│  [4a] PreToolUse hook fires (can block, modify, allow)      │
│       ↓                                                     │
│  [4b] Permission rules evaluated (deny > ask > allow)       │
│       ↓                                                     │
│  [4c] Tool executes (subprocess, filesystem, or API call)   │
│       ↓                                                     │
│  [4d] PostToolUse or PostToolUseFailure hook fires           │
│       ↓                                                     │
│  [4e] Result injected into context as tool_result block      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
    ↓
Model sees all tool_results, decides next action
    ↓
Loop until model emits text (no more tool calls)
```

---

## 2. Phase 4a — PreToolUse hook

Fires BEFORE permission check. This is the first external gate after the model decides.

**stdin JSON:**
```json
{
  "session_id": "...",
  "transcript_path": "/path/to/session.jsonl",
  "cwd": "/...",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "npm test" },
  "tool_use_id": "toolu_01..."
}
```

**Response options:**
| Exit code | Decision | Effect |
|---|---|---|
| 0 + `"allow"` | Allow | Tool executes (still subject to permission rules) |
| 0 + `"deny"` | Deny | Tool blocked, error returned to model |
| 0 + `"ask"` | Prompt user | Pauses for manual approval |
| 2 (blocking) | Block | Tool prevented before rule evaluation |
| 1 (non-blocking) | Continue | Error logged, execution proceeds |

**Input modification:** PreToolUse can return `updatedInput` to modify tool parameters before execution:
```json
{
  "hookSpecificOutput": {
    "permissionDecision": "allow",
    "updatedInput": { "command": "npm test --coverage" }
  }
}
```

**Precedence when multiple hooks conflict:** `deny > defer > ask > allow`. A hook allowing does NOT bypass deny rules — deny rules still win.

**the user's setup:** No PreToolUse hooks wired. The PostToolUse hook (brain_tools_hook.sh) fires after, not before.

---

## 3. Phase 4b — Permission evaluation

After PreToolUse hooks pass, permission rules evaluate:

| Mode | Behavior |
|---|---|
| `dontAsk` (the user's mode) | Only tools matching `permissions.allow` patterns execute. Everything else auto-denied. No prompts. |
| `default` | Unknown tools prompt the user for per-session approval |
| `allowEdits` | Read/Edit/Write auto-allowed, others prompt |

the user's `settings.json` has 159 explicit `permissions.allow` entries covering Bash patterns, WebFetch domains, Read paths, and MCP tools. Any tool call not matching a pattern in `dontAsk` mode is silently denied.

**Implication:** `dontAsk` mode means tool execution is gated by a static allowlist. New tool patterns (e.g., a new MCP server) require manual `permissions.allow` additions before they work.

---

## 4. Phase 4c — Tool execution

Each tool type has different execution characteristics:

### Execution models

| Tool | Execution model | Process type | Persistence |
|---|---|---|---|
| **Bash** | Subprocess | New shell per call | CWD persists, env does NOT |
| **Read** | In-process filesystem | Direct I/O | N/A |
| **Edit** | In-process filesystem | Read-modify-write | N/A |
| **Write** | In-process filesystem | Direct write | N/A |
| **Grep** | Ripgrep engine | Likely in-process (undisclosed) | N/A |
| **Glob** | Filesystem pattern match | In-process | N/A |
| **Agent** | API call (new context) | Separate agentic loop | Fresh context window |
| **Skill** | Skill body injection | In-process context swap | Skill body enters cache |
| **ToolSearch** | Schema fetch | In-process | Schema cached 1h |
| **WebFetch** | Network call | Undisclosed | N/A |
| **WebSearch** | Network call | Undisclosed | N/A |

### Bash specifics
- Each call spawns a new shell process
- CWD persists between calls (within project boundaries)
- Shell state (variables, aliases) does NOT persist
- Default timeout: 120,000ms (2 min), configurable up to 600,000ms (10 min)
- Non-zero exit code → error returned to model, execution continues

### Agent specifics
- Spawns a completely fresh context window
- Does NOT inherit parent conversation history
- Gets only: its prompt + cwd + env details
- Returns a summary to the parent as `tool_result`
- The full subagent transcript does NOT enter parent context
- Cannot spawn nested subagents
- CWD changes in subagent don't affect parent
- Model inherits `CLAUDE_CODE_SUBAGENT_MODEL` env var (Sonnet in the user's setup)

### Sandboxing (Bash only)
- OS-level sandboxing for Bash subprocesses
- Filesystem access restricted to `filesystem.allowRead`/`denyRead` paths
- Network restricted to `allowedDomains`
- Does NOT apply to Read/Edit/Write/Grep/Glob (built-in file tools bypass sandbox)
- `autoAllowBashIfSandboxed: true` (default) auto-approves sandboxed Bash without prompts

---

## 5. Phase 4d — PostToolUse / PostToolUseFailure

### PostToolUse (success path)
Fires after tool executes successfully. stdin includes `tool_response`.

Can block further execution:
```json
{ "decision": "block", "reason": "tests must pass" }
```

**the user's setup:** `brain_tools_hook.sh` wired here. Logs tool_name, model, input/output size to `tools.jsonl`. Matcher fixed in settings.json (empty string `""` replaces `"**/*"` — see Stage 3 §12). Cold-reload required; activates next session.

### PostToolUseFailure
Fires when tool fails. stdin includes `error` string and `is_interrupt` boolean.

**Cannot block** — tool already failed. Can inject `additionalContext` into the model's view of the failure. Exit 2 has no effect.

---

## 6. Phase 4e — Result injection

Tool results return to the model as `tool_result` content blocks in the standard Anthropic API `messages` array. Each result:
- Carries the `tool_use_id` linking it to the original `tool_use` block
- Enters the session's context and stays until compaction
- Gets cached after first injection (5m → 1h rolling)
- Is NEVER truncated before injection (no harness-level size limits documented)

**The compaction trap:** Tool results are the first thing cleared during context compaction, but large results can cause "thrashing" — context fills to limit, compaction clears old results, new tool calls immediately refill. On long sessions, this is the dominant cost pattern.

---

## 7. Parallel execution

When the model emits multiple `tool_use` blocks in one turn:
- Each fires its own PreToolUse hook independently
- Hooks run in parallel
- Actual tool execution parallelism is undisclosed (likely parallel for independent tools)
- No ordering guarantees between parallel tool calls
- All results return to the model in a single turn

---

## 8. Measured reality — tool frequency and cost

### Aggregate across 6 Toke sessions (1,339 turns, 806 tool calls)

| Tool | Calls | Avg result | Total bulk | Errors | Bulk share |
|---|---|---|---|---|---|
| **Read** | 138 | 4,600 chars | 630.6K chars | 6 (4.3%) | **58.8%** |
| **Bash** | 283 | 759 chars | 214.8K chars | 14 (4.9%) | 20.0% |
| **Agent** | 13 | 9,300 chars | 120.9K chars | 0 | 11.3% |
| WebFetch | 7 | 4,600 chars | 32.0K chars | 0 | 3.0% |
| WebSearch | 8 | 3,200 chars | 25.9K chars | 0 | 2.4% |
| Grep | 13 | 1,700 chars | 22.1K chars | 0 | 2.1% |
| **Edit** | 127 | 106 chars | 13.5K chars | 7 (5.5%) | 1.3% |
| Write | 64 | 97 chars | 6.3K chars | 0 | 0.6% |
| TaskCreate | 50 | 71 chars | 3.6K chars | 0 | 0.3% |
| TaskUpdate | 82 | 22 chars | 1.8K chars | 0 | 0.2% |
| ToolSearch | 6 | 130 chars | 782 chars | 0 | 0.1% |
| Skill | 15 | 29 chars | 438 chars | 0 | 0.0% |

**Total result bulk:** 1.07M chars (~298K tokens)

### Cost structure insights

1. **Read is the bulk king (58.8%).** 138 calls averaging 4.6K chars each. Every file read enters context and stays there. On a 275-turn session at $0.50/MTok cache-read rate, each Read result gets re-read ~30× (Stage 2's 1:29.9 write:read ratio). A single 4.6K-char Read costs ~$0.02 in cache writes but ~$0.17 in cumulative cache reads over the session.

2. **Bash is the frequency king (283 calls).** Low per-call cost (759 chars avg) but high volume. Bash calls are cheap individually but they accumulate. 14 errors (4.9%) — mostly non-zero exits that the model recovers from.

3. **Agent is the per-call heavyweight (9.3K avg).** Only 13 calls but each injects a full research report into context. Agent results are the biggest single-turn context growth events. Zero errors — subagents either complete or timeout, never partial.

4. **Edit is cheap and numerous (127 calls, 106 chars avg).** Edit results are tiny — just a confirmation message. But the 5.5% error rate (7/127) is the highest of any tool. These are mostly "old_string not found" mismatches.

5. **TaskCreate/TaskUpdate are noise (132 calls, 5.4K total).** Context cost is negligible but they add visual clutter to transcripts.

### Error patterns

| Tool | Error rate | Common failure | Model recovery |
|---|---|---|---|
| Edit | 5.5% (7/127) | `old_string` not unique or not found | Re-reads file, retries with larger context |
| Bash | 4.9% (14/283) | Non-zero exit, command not found | Adjusts command, retries |
| Read | 4.3% (6/138) | File not found, encoding issues | Tries alternate path |
| Agent | 0% (0/13) | — | — |
| Write | 0% (0/64) | — | — |
| Grep | 0% (0/13) | — | — |

**Edit has the highest error rate.** The primary failure mode is `old_string` not matching due to indentation differences, line number mismatches from prior edits, or non-unique strings. The model's recovery pattern: Read the file again, then retry Edit with the correct string.

---

## 9. The tool execution loop as a cost function

Each iteration of the loop has a measurable cost:

| Cost component | Per iteration | Notes |
|---|---|---|
| Cache write (tool result) | result_chars × $10/MTok ÷ 4 | One-time write to 1h cache |
| Cache reads (subsequent turns) | result_chars × $0.50/MTok ÷ 4 × remaining_turns | Amortized over session |
| Output tokens (model reasoning) | ~500-2000 tokens × $25/MTok | Model deciding next action |
| Hook execution | ~0ms-50ms wall time | Free in token terms |

**The loop's cost is dominated by cache reads, not writes.** A tool result costs ~2× base rate to cache-write once, then ~0.1× base rate per turn for the rest of the session. On a 275-turn session, a 4.6K-char Read result costs:
- Cache write: 4,600 / 4 × $10/M = $0.012
- Cache reads (remaining ~270 turns): 4,600 / 4 × $0.50/M × 270 = $0.155
- **Total lifetime cost of one Read call: ~$0.17**

An Agent result (9.3K avg) costs ~$0.34 lifetime. But Agent runs on Sonnet (Zone 2), which means the SUBAGENT's processing is at Sonnet rates, not Opus. The result entering Opus's context is the only Opus-rate cost.

---

## 10. What Claude Code cannot currently do at Stage 4

| Limitation | Impact | Potential automation |
|---|---|---|
| No result size limits | Large Read/Agent results inflate context permanently | PreToolUse hook could truncate known-large files |
| No result aging | Old tool results stay until compaction | Could mark results as "ephemeral" for aggressive compaction |
| No retry limits | Model can retry a failed Edit indefinitely | PostToolUseFailure hook could enforce max retries |
| No cost feedback | Model doesn't know a Read costs $0.17 lifetime | Could inject cost estimate via PostToolUse additionalContext |
| No tool result dedup | Reading the same file twice creates two context entries | PreToolUse hook could detect and short-circuit |
| No Edit validation | Edit fails at 5.5% — highest error rate | PreToolUse hook could pre-validate old_string existence |

---

## 11. Instrumentation opportunities

With PostToolUse telemetry activating next session (matcher fix from Stage 3):

- [ ] **Per-session tool cost attribution** — which tool calls contributed most to the session bill?
- [ ] **Tool call chains** — detect repeated patterns (Grep → Read → Edit)
- [ ] **Edit failure prediction** — can PreToolUse pre-validate old_string to reduce the 5.5% error rate?
- [ ] **Result size alerting** — flag when a single tool result exceeds a threshold (e.g., >20K chars)
- [ ] **Agent ROI** — compare Agent cost (subagent Sonnet + result in Opus context) vs doing the work in main context
- [ ] **Compaction correlation** — do large tool results correlate with earlier compaction events?

---

## 12. Status

- ✅ Full execution lifecycle documented (PreToolUse → permission → execute → PostToolUse → result injection)
- ✅ Tool frequency measured across 6 sessions (806 calls, 1.07M chars bulk)
- ✅ Cost structure analyzed (Read = 58.8% bulk, Agent = 9.3K/call, Edit = 5.5% error rate)
- ✅ Tool lifetime cost model built ($0.17 per Read, $0.34 per Agent on a 275-turn session)
- ✅ Error patterns documented with model recovery strategies
- ✅ Parallel execution behavior documented
- ✅ Sandboxing and permission system mapped
- ⏳ Live PostToolUse telemetry (blocked on matcher fix — next session)
- ✅ Tool call chain analysis: `per_turn_breakdown.py chains` — Read→Edit (1.9%), Edit→Bash (2.3%), Bash→Bash (10.3%)
- ✅ Per-session cost attribution: `per_turn_breakdown.py cost` + `summary` modes

Next in the pipeline: **Stage 5 — Response Generation.** How does the model format its output? What's the text vs tool_use decision at the end of the loop? How do status indicators, formatting rules, and output compression work?
