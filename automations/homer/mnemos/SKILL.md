---
name: mnemos
description: Homer L5 — Three-tier memory store. Core (context-resident, ~5K tokens, auto-compacted) / Recall (SQLite FTS5 searchable) / Archival (cold markdown, back-pointer protocol). Every write and edit requires a valid citation. Zero dark edits. Compaction moves entries between tiers with back-pointers in Core. Zeus and MUSES call Mnemos via the MnemosStore Python API.
model: opus
---

# Mnemos — The Memory of Homer

> Mnemosyne was the Titaness of memory in Greek myth — mother of the nine Muses. In Homer, Mnemos is the three-tier memory store every muse reads from and writes to. She never forgets, and she never lies about her sources.

## Role

Mnemos is Homer's memory layer. Zeus (L2) and MUSES (L3) call Mnemos to:
- Write accumulated learnings (Core or Recall tier)
- Self-edit existing Core entries when clearer sources are found
- Search Recall history for prior runs on similar topics
- Read Archival cold storage via back-pointers surfaced in Core

## The Three Tiers

### Tier 1: CORE (context-resident)
- **Where:** `mnemos/core/core_memory.md` (JSON-lines format, one entry per line)
- **Budget:** ~5,000 tokens (`CORE_TOKEN_BUDGET`)
- **Purpose:** Most-critical patterns. Auto-injected into Zeus dispatch prompts so every muse sees them.
- **Access:** `MnemosStore.write_core()`, `edit_core()`, `read_core()`
- **Compaction:** Triggered when over budget. Lowest-priority entries (LOW confidence + oldest use) move to Archival, back-pointer stays in Core at the same key.

### Tier 2: RECALL (searchable)
- **Where:** `mnemos/recall/recall.db` (SQLite, FTS5 when available, LIKE fallback)
- **Budget:** Unlimited (disk-bound)
- **Purpose:** Full session / conversation history, searchable by keyword.
- **Access:** `MnemosStore.write_recall()`, `search_recall(query, limit)`, `read_by_id(entry_id)`
- **Compaction:** No auto-compaction. Sleep-time Aurora (P3) may prune after measuring access ROI.

### Tier 3: ARCHIVAL (cold)
- **Where:** `mnemos/archival/archival_*.md` (one markdown file per entry)
- **Budget:** Unlimited
- **Purpose:** Entries trimmed out of Core during compaction. Never auto-deleted (Sacred Rule #2).
- **Access:** `MnemosStore.read_archival(archival_id)` for cold-read by id. Look up via Core's back-pointer.

## Citation Requirement (Beat SOTA commitment #3)

**Every Core write, every Core edit, every Recall write requires a valid citation.** Zero dark edits. This is Homer's accountability-beats-Letta commitment made operational.

### Accepted formats:
| Format | Example |
|---|---|
| `file:line` | `Toke/research/brain_synthesis.md:42` |
| `file:line-line` | `brain_cli.py:100-150` |
| `https://...` | `https://anthropic.com/engineering/multi-agent-research-system` |
| `arxiv:YYYY.NNNN` | `arxiv:2603.18897` |
| `mnemos:archival_<id>` | `mnemos:archival_xyz_20260411` |
| `decisions:<session_id>` | `decisions:abc-123-def` |
| `session:<session_id>` | `session:d38ab304-a6ec-494f` |

### Rejected:
- Empty string / whitespace-only
- `"around line 50"` — vague location
- `"see somewhere in the codebase"` — no file
- `"I think I read it somewhere"` — no source
- Anything not matching the regex patterns

Writes with invalid citations raise `CitationError` immediately — no silent write, no fallback, no "close enough."

## Self-Edit Contract

Every `edit_core()` call requires BOTH a new citation AND a reason:

```python
store.edit_core(
    key="core_pattern_xyz",
    new_content="updated pattern content",
    new_citation="brain_cli.py:205",    # REQUIRED — must pass validate_citation
    reason="found more specific source in Brain v2.3 classifier",  # REQUIRED — non-empty
)
```

- Empty reason → `ValueError`
- Empty or invalid citation → `CitationError`
- Missing key → `KeyError`

This is the load-bearing self-edit discipline. Letta's MemGPT allows agents to self-edit freely; Mnemos forces every edit to carry a traceable source AND a justification. When Aurora audits Core history in P3, every edit will have a "why."

## Python API (for Zeus / MUSES)

```python
from mnemos import MnemosStore, CitationError

store = MnemosStore()

# Write to Core (~5K token budget, auto-compacted)
entry = store.write_core(
    pattern="Orchestrator-worker pattern gives +90.2% over single-agent Opus",
    citation="https://www.anthropic.com/engineering/multi-agent-research-system",
    confidence="HIGH",
)

# Write to Recall (unlimited, FTS5-searchable)
rid = store.write_recall(
    topic="Homer P1 ship session",
    content="Shipped VAULT + Zeus + 3 MUSES + Sybil. 25/25 tests green.",
    citations=["project_homer.md:50-80", "session:2026-04-11-homer-p1"],
)

# Self-edit a Core entry (citation + reason required)
store.edit_core(
    key=entry.key,
    new_content="MARS pattern gives +90.2% per Anthropic's research eval",
    new_citation="https://www.anthropic.com/engineering/multi-agent-research-system",
    reason="tightened the claim to 'per Anthropic eval' for clarity",
)

# Search Recall
results = store.search_recall("orchestrator worker", limit=5)

# Compact Core if over budget (fires automatically, safe to call unconditionally)
compaction = store.compact_if_over_budget()
# {"compacted": bool, "result": {...}, "new_status": {...}}

# Health check
health = store.health()
# {"core": {budget...}, "recall_count": N, "archival_count": M, "fts_available": bool}
```

## Boundary Discipline

1. **Citations are gatekeepers** — no write without a valid citation. `require_citation()` runs on every entry point.
2. **Self-edits are justified** — every edit carries a reason. Reasons are persisted (future: to Recall for Aurora audit).
3. **Compaction never silently deletes** — trimmed Core entries always land in Archival with a back-pointer in Core.
4. **Budget is a floor, not a ceiling** — exceeding triggers compaction, which is a normal operation, not an error.
5. **Dark-edit zero** — any modification that doesn't include a fresh citation + reason is rejected, even if "obviously correct."
6. **Corrupt entries skipped, never fail reads** — reader resilience, following VAULT's discipline.

## When Zeus Dispatches Mnemos Calls

Zeus invokes Mnemos during these pipeline phases:

- **Phase 1 (plan)** — `search_recall()` for prior runs that might inform the current plan
- **Phase 3 (synthesize)** — if muses disagree, consult Core for previously-verified patterns at the same key
- **Phase 5 (memory write)** — after synthesis, write the distilled output to Core (citation from muse source) and full run narrative to Recall

Muses do NOT write directly to Mnemos. Their outputs flow up to Zeus, and Zeus is the only layer with write authority to Mnemos. This keeps the accountability chain single-threaded.

## Failure Modes

| Mode | Response |
|---|---|
| `CitationError` on write | Zeus rejects the muse output, re-dispatches with stricter citation requirement |
| `ValueError` on edit (empty reason) | Zeus regenerates the edit with a justification |
| `KeyError` on edit (missing key) | Zeus checks if the key was compacted to Archival, reads back-pointer, continues |
| Budget exceeded | Auto-compaction fires, back-pointers created, operation continues |
| FTS5 not available | Falls back to LIKE-based search automatically (no caller action needed) |
| Recall DB corruption | (future P3) Fresh DB created; old DB preserved with `.corrupt` suffix |
| Core file corruption | Corrupt JSON lines silently skipped; partial read continues |

## Sleep-Time Agent Integration (P3 preview)

When Sleep-time agents (L6) ship, Aurora will:
- Read Recall access counts to identify highest-value entries for Core promotion
- Read Core compaction history to identify patterns that should be retained longer
- Suggest Core token budget adjustments based on usage patterns
- Archive stale Recall entries not accessed in N days (with back-pointers)

Hesper will:
- Mine all three Mnemos tiers for cross-tier learnings
- Distill into Toke's best-practices KB (absorbing the original Kiln blueprint mission)

Nyx will:
- Audit Core self-edit history for quality (edits without strong reasons get flagged)
- Flag citation patterns that point at missing/moved files

## Sacred Rules Active

All 13 rules. Rule 1 (truthful) is enforced at the citation level — untraceable claims are rejected at write time. Rule 2 (no delete) is enforced by compaction protocol — trimmed entries always go to Archival, never deleted. Rule 11 (AAA quality) means every citation is validated, every self-edit is justified, every archival entry has a back-pointer.

## Ship Status

- **P2 shipped 2026-04-11** — mnemos.py + SKILL.md + test_mnemos.py
- **Three tiers live:** Core + Recall (SQLite FTS5) + Archival
- **Citation enforcement active** on all write paths
- **Test coverage:** 35 smoke tests across citation validation / Core ops / Recall ops / Archival ops / Facade
- **Not yet integrated with Zeus** — Zeus's Phase 5 memory write still uses the fallback `_learnings.md` entry path. Zeus↔Mnemos wire-up comes with Oracle (L7) in P3, since both are "post-synthesis" layers.
