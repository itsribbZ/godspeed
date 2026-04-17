---
name: close-session
description: >
  Session closure and memory persistence v2.0. Captures work completed, decisions made,
  and context for next session. v2.0: structured learning format, cross-skill auto-promotion,
  checkpoint verification, ecosystem health snapshot. Companion to init — init loads, close-session saves.
model: sonnet
effort: medium
---

# Close Session v2.0

Init loads context. Close-session saves it. Together: lossless session loop.

## Trigger

"close session", "end session", "wrap up", "save session", "done for today", "session done"

## Phase 1: Session Audit

Scan the conversation for:

1. **Work Completed** — features built, bugs fixed, code written, files created/modified
2. **Decisions Made** — design choices, architecture decisions, approach changes
3. **Bugs Found/Fixed** — new bugs discovered, existing bugs resolved
4. **Research Done** — professor/profTeam/blueprint outputs, findings
5. **Tools Created/Updated** — new scripts, skill improvements, workflow changes
6. **Unfinished Work** — started but not completed, mid-progress state
7. **Next Steps** — planned next actions, logical continuations
8. **User Feedback** — corrections, preferences, guidance that should persist

## Phase 2: Detect Project

Close-session auto-detects the active project from the current working directory. Claude Code already scopes memory per-project at `~/.claude/projects/<slugified-cwd>/memory/`, so the write target is:

```
~/.claude/projects/$(slug of $PWD)/memory/project_status.md
```

(Claude Code does the slugification automatically when it loads `MEMORY.md` at conversation start — you don't need to compute it manually.)

If the session spanned multiple directories, persist to each of their memory dirs. If no `project_status.md` exists yet, create one with the frontmatter template in Phase 3a below.

## Phase 3: Core Persistence (ALWAYS — parallel writes)

### 3a. Update Project Status

Read existing status file first. **Append** new session entry:

```
## Session: [First Task] — [DATE]
**Completed**:
- [bullet list of completed work]
**In-Progress**:
- [bullet list with current state]
**Decisions**:
- [design/architecture decisions made]
**Next Session**:
- [priority-ordered next steps]
```

**NEVER overwrite previous sessions.** Append only.

### 3b. Create/Update Memory Files

For each piece of information worth persisting beyond this session:

- **New bugs** → update or create `project_bug_list.md`
- **User feedback/corrections** → create `feedback_[topic].md` with Why + How to apply
- **New tools/references** → create `reference_[topic].md`
- **Architecture decisions** → update relevant project memory
- **Research findings** → create `project_[topic].md`
- **Lore/design content** → create `project_[topic].md`

Standard frontmatter format:
```markdown
---
name: [descriptive name]
description: [one-line — specific enough to judge relevance later]
type: [user|feedback|project|reference]
---
[content]
```

### 3c. Update MEMORY.md Index

Add any NEW memory files to the index. One line each, under correct section heading. Don't duplicate existing entries.

## Phase 4: Conditional Persistence

Only run these when the session warrants it. Skip for brief/conversation-only sessions.

### 4a. Bible Sync — IF session changed facts the Bible documents

Trigger: session modified architecture, systems, counts, performance numbers, known issues, or source structure that the Bible describes.

Projects often maintain a long-form "bible" document (architecture, systems, counts, known issues). If the session changed load-bearing facts that belong in such a doc:

- **If the bible is a markdown file in the repo** → edit directly. Surgical edits only. Update the `Last Updated` date.
- **If the bible is a PDF or external doc** → list specific changes under a `BIBLE UPDATES NEEDED` section in the session status entry. The user will apply them externally.
- **If no bible exists** → skip.

**Update when:** Factual corrections, new permanent systems, removed systems, updated counts/lists, source structure changes, performance numbers, known issues resolved.

**Do NOT update with:** Session progress, temporary state, WIP notes, phase status (that's project_status.md), speculative plans.

**Skip entirely if:** Session was pure research, debugging without resolution, or conversation-only.

### 4b. Sacred-Rules / Personal-Protocols Update — IF durable behavioral insights were reached

Check if the session produced insights that change how we work long-term (not task status). If yes, surface them as a proposal to append to the user's personal rules doc (e.g. `~/CLAUDE.md`, a project-root `CLAUDE.md`, or whatever memory file holds sacred rules for this user's workflow).

**Update when:** New sacred rules, model routing decisions, prompt optimization insights, workflow shifts.
**Do NOT update with:** Session progress, project status, temporary decisions.

### 4c. Skill Learnings — IF skills were invoked AND new insights gained

#### Structured Format (v2.0 — per shared protocols §9)

Append to each invoked skill's `_learnings.md` using the structured format:
```markdown
### [ENTRY_TYPE]: [Topic] — [YYYY-MM-DD]
<!-- meta: { "run_id": "[skill]_[topic]_[date]", "domain": "[domain]", "confidence": "[HIGH/MEDIUM/LOW]", "confirmed_count": 1, "roi_score": [1-5], "staleness_check": "[YYYY-MM-DD]" } -->

**Finding**: [One-sentence summary]
**Evidence**: [What proved it]
**Applies to**: [skill1, skill2, ALL]
**Action**: [What to DO differently]
```

Only write if something genuinely new was learned. Don't force entries for every session.

#### Cross-Skill Auto-Promotion (v2.0 — per shared protocols §10)

After writing skill-local learnings:
1. **Scan `_shared_learnings.md`** for similar findings
2. **If match found**: Increment `confirmed_count` on the shared entry
3. **If cross-applicable** (applies_to is not just one skill): Check if the same pattern exists in another skill's `_learnings.md`
   - If found in 2+ skill-local learnings → **auto-promote to shared** with next SL-ID
   - grep max existing SL-ID first (per SL-043): `grep -oE 'SL-[0-9]+' ~/.claude/shared/_shared_learnings.md | sed 's/SL-//' | sort -n | tail -1`
4. **Report**: "Cross-skill promotion: [finding] → SL-[NNN]" or "No cross-skill learnings this session"

#### Checkpoint Verification (v2.1 — 2026-04-10 audit expansion)

For each of these skills invoked during the session, verify the `_learnings.md` pipeline fired:

**Holy Tools** (original v2.0 coverage):
- devTeam, profTeam, holy-trinity, godspeed

**Previously-broken-pipeline skills** (added 2026-04-10 per toolset audit — shell-append Phase 0 fix must be verified every session until confirmed stable):
- blueprint, bionics, marketbot, brain, debug, cycle

**Protocol**:
1. **Check each invoked skill's `_learnings.md`** — did a session marker AND incremental checkpoints get written during the session?
2. **If YES + marker present**: shell-append fired correctly. Consolidate marker into a clean run entry.
3. **If marker present but no further checkpoints**: Phase 0 fired but mid-execution writes didn't. Flag: "⚠ Partial checkpoint: marker present but phase checkpoints missing for [skill]."
4. **If NO marker at all**: The Phase 0 shell-append protocol didn't fire. This is a v4.1 regression — the fix from 2026-04-10 broke. Write a manual recovery entry and flag: "🚨 SHELL-APPEND REGRESSION: [skill] Phase 0 did not write session marker. Root cause investigation needed."
5. This verification catches silent pipeline failures that SL-044 identified AND confirms the SL-062 shell-append fix is still holding.

**Expanded baseline**: any invoked skill with <5 total learning entries after 10+ known invocations is flagged as "PIPELINE LOW — may need v4.1 upgrade."

### 4d. Toke Systems Snapshot — IF Brain or Homer were active this session

For Toke and any session where Brain/Homer were actively used (not just background routing):

**Brain snapshot** (append to session status entry):
```bash
DECISIONS=$(wc -l < ~/.claude/telemetry/brain/decisions.jsonl 2>/dev/null || echo 0)
TOOLS=$(wc -l < ~/.claude/telemetry/brain/tools.jsonl 2>/dev/null || echo 0)
TICK=$(cat ~/.claude/telemetry/brain/godspeed_count.txt 2>/dev/null || echo 0)
echo "Brain: ${DECISIONS} decisions, ${TOOLS} tool calls, godspeed tick ${TICK}"
```

**Homer snapshot** (if Homer layers were invoked):
```bash
python $TOKE_ROOT/automations/homer/homer_cli.py status 2>/dev/null | head -5
```

Include in the session status entry so init can show current system health.

### 4e. Git Snapshot — IF in a git repo with uncommitted work

```bash
git log --oneline -5
git status
git diff --stat
```

Include in the session status entry so init can show "Where You Left Off" accurately.

### 4f. Session Log Update — IF project uses session_log.json

Projects that use a session log JSON (e.g. an append-only `session_log.json` tracking per-session deliverables): update the active entry with the session's actual summary, deliverables, learnings, and set its status to "complete".

## Phase 5: Session Summary

```
═══════════════════════════════════════
  SESSION CLOSED — [DATE]
═══════════════════════════════════════

COMPLETED:
  - [items]

IN-PROGRESS:
  - [items with current state]

SAVED TO MEMORY:
  - [files created/updated]

SKILL LEARNINGS:
  - [skills with new entries / checkpoint verification results]
  - Cross-skill promotions: [SL-NNN list or none]

BIBLE: [Updated / No changes needed / Updates flagged]

TOKE SYSTEMS: [if active]
  Brain: [N] decisions, [N] tool calls, tick [N]
  Homer: [status summary or "not invoked"]

ECOSYSTEM HEALTH:
  Pipeline: devTeam=[N] profTeam=[N] trinity=[N] godspeed=[N]
  Checkpoint verification: [all fired / ⚠ manual recovery for: list]

NEXT SESSION:
  - [priority-ordered list]

STATUS: Session persisted ✓
═══════════════════════════════════════
```

## Phase 6: Validate

After all writes, verify:
1. New memory files exist with correct frontmatter
2. MEMORY.md includes all new entries
3. Status file has the new session entry
4. No orphaned references

## Rules

1. **NEVER delete or overwrite existing memory** — append or create new
2. **NEVER save ephemeral info** — no debug logs, no temp state, no code that's in the repo
3. **DO save context that would be lost** — decisions, reasoning, mid-progress state, preferences
4. **Convert relative dates to absolute** — "tomorrow" → specific date
5. **Keep entries concise** — summaries over transcripts
6. **Deduplicate** — check existing memories before creating new ones
7. **Flag critical next-session context** — if something is CRITICAL for pickup, emphasize it in status

## Init Integration

| Close-Session Writes | Init Reads |
|---------------------|------------|
| project_status.md (session entry) | "Where You Left Off" section |
| Bible updates (factual edits or flags) | Architecture, systems, rules |
| feedback_*.md (user preferences) | Behavioral rules |
| project_*.md (project state) | Project context |
| reference_*.md (tools/resources) | Tool/resource lookups |
| MEMORY.md (index updates) | Always loaded at conversation start |
| _learnings.md (skill insights) | Read before skill execution |
| Brain telemetry snapshot | Brain scan / decision count |
| Homer VAULT state | Homer layer health |
| Git snapshot in status | Recent work areas |
| session_log.json (if applicable) | Session history and continuity |

## Protocols

Follow `~/.claude/shared/_shared_protocols.md` for: post-invocation learning (§4), incremental checkpoints (§5), session safety (§6), learning pipeline health (§7), structured learning format (§9), cross-skill auto-promotion (§10), staleness detection (§11).
