---
name: devTeam
description: DevTeam v4.0 — Adaptive Code Fluidity & Architecture Engine. Calibrated scoring against the 7 Laws of Code Fluidity, domain auto-detection, regression guard, and project-docs reference integration. Holy Tool status. Auto-activates on "devTeam", "deploy devTeam", "fluidity check", "architecture review" — and whenever code is written, reviewed, or architected across multiple modules. Diagnostic engine of the Holy Trinity (devTeam diagnose → profTeam research → implement → verify).
model: opus
effort: high
---

# DevTeam v4.0 — Adaptive Code Fluidity & Architecture Engine

Adaptive code fluidity and architecture engine. Ensures every system achieves Code Fluidity --
large systems with interconnected specificities interlinking cohesively. Domain auto-detection,
calibrated scoring, regression guard, project docs reference integration. Holy Tool status.

## Holy Tool Status

DevTeam is a **Holy Tool** — always available, always trusted. It serves as the diagnostic engine of the **Holy Trinity v4.0** (devTeam diagnose → profTeam research → implement → verify). When the Holy Trinity invokes devTeam, it produces scored gap lists that become profTeam's research targets.

## When to Trigger (AUTO-ACTIVATE)

This skill should be ON STANDBY and auto-activate whenever:
- Building a NEW system or connecting two existing systems
- Writing code that touches multiple modules
- The user says "devTeam", "use devTeam", "deploy devTeam", "fluidity check", "architecture review"
- A code review reveals coupling, god class, or integration issues
- Any time System N+1 is being added to a project
- When refactoring or restructuring existing code
- When the user asks about best practices, patterns, or architecture decisions
- When ANY skill in the ecosystem needs architectural validation

## Domain Auto-Detection (v2.0)

DevTeam is **project-agnostic**. Before scoring, detect the target domain from the working directory and files:

| Domain | Detection Signal | Scoring Mode |
|--------|-----------------|--------------|
| **UE5 Game Code** | .h/.cpp with UPROPERTY/UFUNCTION, .uproject | 7 Laws + Fluidity Checklist + UE5 Patterns |
| **Skill/Tool** | SKILL.md, _learnings.md, .claude/skills/ path | Adapted 7-Dim: Reliability, Learning, Integration, Resilience, Quality, Efficiency, Extensibility |
| **SaaS/Web App** | package.json, Next.js/React, API routes, Supabase | 7 Laws + Web Patterns (OWASP, REST, State Mgmt) |
| **Game Design Doc** | GDD, design docs, no source code target | Adapted 7-Dim: Coherence, Scope, CoreLoop, Differentiation, Interconnection, TechSpec, Motivation |
| **Product/Business** | Market analysis, revenue model, no code target | Adapted 7-Dim: MktValidation, CompGap, Revenue, Build, Distro, Advantage, Scale |
| **Python Tool** | .py files, standalone script/tool | 7 Laws + Python Patterns (typing, error handling, CLI) |
| **Infrastructure** | CI/CD, Docker, configs, deployment | 7 Laws + Ops Patterns (Reliability, Observability, Security) |
| **Unknown** | Cannot auto-detect | Ask user or define dimensions in Phase 0 |

Load domain-specific context, anti-patterns, and scoring dimensions based on detection. If multiple domains apply (e.g., UE5 code + game design), use the primary target's mode.

## The 7 Universal Laws (Always Apply)

These laws were derived from cross-referencing 40+ master programmers. They are NON-NEGOTIABLE:

### LAW 1: Simplicity Is Prerequisite
*Dijkstra, Hickey, Pike, Torvalds, Hotz, Lattner, Kelley*
Simple systems are reliable systems. Complexity is not a feature — it's a cost. If a system feels complex, refactor until it doesn't.

### LAW 2: Design Around Data, Not Abstractions
*Acton, Carmack, Muratori, Blow, Bellard*
The data IS the architecture. Before writing code, define: what data do I have? What data do I need? What transformation connects them?

### LAW 3: Eliminate Edge Cases, Don't Handle Them
*Torvalds*
Restructure the problem so special cases vanish. Many if-branches = wrong abstraction. Find the design where the edge case IS the normal case.

### LAW 4: Systems Should Not Know Each Other's Internals
*Hickey, Liskov, Pike, Fowler, Metz*
Communicate through data contracts (structs, enums, interfaces), never implementation details. If System A imports System B's internals, there must be a very good reason.

### LAW 5: Code Is Liability, Not Asset
*Hotz, Bellard, Blow, Muratori*
Every line must justify its existence. Refactor until the feature is a 3-line change. "You have never refactored enough."

### LAW 6: Progressive Disclosure of Complexity
*Lattner*
Simple to start, powerful when you dig in. Systems should have a trivial default path and an expert path. Never thrust all complexity at once.

### LAW 7: Make It Work, Make It Right, Make It Fast
*Beck, Knuth, Carmack*
Correctness first. Clarity second. Speed third. Never optimize before profiling. But when profiling reveals a hotspot, optimize ruthlessly.

## The Fluidity Checklist (Run on Every Integration)

Before connecting any two systems, verify:

### Data Contract
- [ ] Data between systems is a clearly defined struct/enum/interface
- [ ] Neither system accesses the other's private state
- [ ] The contract can be described in one sentence

### Independence
- [ ] System A compiles/runs without System B
- [ ] System B can be removed without System A crashing
- [ ] A third System C can hook into the same data flow without modifying A or B

### Extensibility (Seam Pattern)
- [ ] New variants can be added by ONLY adding new files/data
- [ ] No switch/case or if/else chains need modification for new variants
- [ ] Extension points are documented

### Ownership
- [ ] Exactly ONE system owns each piece of mutable state
- [ ] Other systems read (observe), never write (mutate)
- [ ] The owner is documented in comments

### Performance
- [ ] Hot path (ticked every frame / called every request) is free of allocations, casts, string operations
- [ ] Batch operations are used where possible
- [ ] System has been profiled under realistic load

## Calibrated Scoring Engine (v2.0)

### Learning-Driven Weight Adjustment

Before scoring, read `_learnings.md` and compute which Laws have historically caught the most issues for the detected domain. Apply calibrated weights:

1. Count issues caught per Law from all previous reviews in `_learnings.md`
2. Compute: `CalibrationFactor[Law_N] = issues_by_law_N / total_issues`
3. Apply multipliers:
   - Law catches 30%+ of all issues in this domain → weight × 1.5
   - Law catches 0 issues across 3+ reviews → weight × 0.75 (floor: 0.5)
   - Insufficient data (<3 reviews) → weight × 1.0 (default)

Report calibration in every diagnostic output:
```
Calibrated weights: Law 1 (×1.0), Law 2 (×1.5), Law 4 (×1.2), Law 5 (×0.75)...
Data points: [N] previous reviews in this domain
```

### Fluidity Score v2.0

```
FluidityScore = (Σ Dimension_Score_i × Weight_i) / (Σ Weight_i × Max_Score) × 100

Dimensions & Default Weights:
  DataContract    × 0.20
  Independence    × 0.20
  Extensibility   × 0.20
  Ownership       × 0.15
  Performance     × 0.15
  NoAntiPatterns  × 0.10

Each dimension scored 0-100.
Weights may be calibrated from learnings (same mechanism as Laws).
```

**Grade thresholds:**
- 90+: EXCELLENT — Production AAA quality
- 70-89: GOOD — Solid architecture, minor improvements needed
- 50-69: FAIR — Refactoring recommended before shipping
- <50: POOR — Architecture debt, must address before adding more systems

### Regression Guard (v2.0 — CRITICAL)

**Never break what's working.**

**Before ANY changes:**
1. Record all current dimension scores as `baseline_scores`
2. Tag any dimension scoring ≥4/5 (or ≥80 Fluidity) as **PROTECTED**
3. Log protected dimensions in the diagnostic report

**After ANY changes:**
4. Re-score all dimensions
5. Regression check — for each protected dimension:
   - If score dropped by ≥1 point (or ≥10 Fluidity): **REGRESSION DETECTED**
   - Report: `REGRESSION: [dimension] dropped [X] → [Y] after [change description]`
   - Recommend: rollback the specific change, or propose alternative fix
6. Net assessment: implementation is SUCCESSFUL only if:
   - Net score is positive (total improvement > total regression)
   - AND no protected dimension has a regression ≥ severity of the fix

**Escalation:** If a fix improves a weak area but regresses a strong area, present both options to the user:
- Option A: Apply fix, accept regression (explain tradeoff)
- Option B: Find alternative fix that doesn't regress
- Option C: Mark gap as "architectural" — needs redesign, not a patch

## Architecture Patterns (Apply by Situation)

### The Pipeline Pattern
Every game system follows: INPUT → INTENT → VALIDATION → EXECUTION → CONSEQUENCES → FEEDBACK → VISUAL. Each stage transforms data for the next without knowing the next stage's internals.

### Decoupling Strategies for UE5
| Use Case | Pattern | Why |
|----------|---------|-----|
| Cross-system API | UInterface | Actor doesn't know concrete type |
| State change notification | Delegate/Event | Fire-and-forget, N listeners |
| Global service access | UWorldSubsystem | Engine-managed lifetime, no singleton |
| Same-actor communication | Component reference | Tight, justified relationship |
| Gameplay events | Gameplay Tags + Messages | Tag-based routing, zero coupling |

### Decoupling Strategies for Web/SaaS
| Use Case | Pattern | Why |
|----------|---------|-----|
| Cross-component data | React Context / Zustand | Avoid prop drilling |
| API layer | Server Actions / tRPC | Type-safe, colocated |
| Auth/session | Middleware + RLS | Enforce at boundary, not per-query |
| Realtime | Supabase Realtime / WebSocket | Event-driven, not polling |
| File uploads | Signed URLs + Storage | Never proxy through server |

### Data Organization (DOD Hybrid — Game Code)
- **OOP for ownership**: AActor/UActorComponent hierarchy, UObject GC
- **DOD for hot data**: TArray<FTransform>, USTRUCT arrays for batch processing
- **Hot/Cold split**: Frequently ticked data in tight structs. Rarely queried data in UDataAssets
- **Profile with Unreal Insights** — measure cache misses, not assumptions

### State Management
- **Single Source of Truth**: One system OWNS each state. Others observe via delegates/subscriptions
- **Gameplay Tags** (UE5): Extensible identifiers — add new tags without code changes
- **GAS** (UE5): For complex ability interactions, cooldowns, stacking effects, network prediction
- **Server State** (Web): Database is truth. Client state is a cache. Revalidate, don't trust.

## Anti-Pattern Detection v2.0

### Universal Anti-Patterns (All Domains)
| Anti-Pattern | Severity | Smell |
|-------------|----------|-------|
| God Class/Module | CRITICAL | 500+ lines doing multiple concerns |
| Circular Dependencies | CRITICAL | A imports B and B imports A |
| Shotgun Surgery | HIGH | Small feature change requires 10+ file edits |
| Inappropriate Intimacy | HIGH | System directly accesses another's private state |
| Premature Abstraction | MEDIUM | Complex hierarchy before 3 concrete implementations |
| Temporal Coupling | MEDIUM | Systems must initialize in undocumented order |
| Flag Arguments | LOW | Functions with 3+ bool parameters |
| Dead Code | LOW | Unreachable code paths, unused exports |

### UE5-Specific Anti-Patterns
| Anti-Pattern | Severity | Smell |
|-------------|----------|-------|
| Tick Abuse | HIGH | Per-frame logic that could be event-driven |
| Raw Pointer UPROPERTY | MEDIUM | T* instead of TObjectPtr<T> |
| Missing Replication | HIGH | Gameplay state not replicated (multiplayer) |
| Cast Chains | HIGH | Sequential casts to find concrete types |
| Blueprint-Only Logic | MEDIUM | Gameplay logic unreachable from C++ |
| Hardcoded Player Controller | HIGH | Cast to APlayerController (breaks AI) |

### Web/SaaS-Specific Anti-Patterns
| Anti-Pattern | Severity | Smell |
|-------------|----------|-------|
| N+1 Query | CRITICAL | Loop with individual DB queries |
| Prop Drilling | HIGH | Props passed through 3+ component levels |
| Unvalidated Input | CRITICAL | User input used without sanitization |
| Client-Side Auth | CRITICAL | Security checks only in frontend |
| Missing RLS | CRITICAL | Database queries without row-level security |
| Synchronous Waterfall | HIGH | Sequential awaits that could be parallel |
| Stale Cache | MEDIUM | Cached data served without revalidation strategy |

### Skill/Tool-Specific Anti-Patterns
| Anti-Pattern | Severity | Smell |
|-------------|----------|-------|
| Stale Learnings | MEDIUM | _learnings.md >30 days without update |
| Missing Cross-Skill Propagation | HIGH | Finding applicable to other skills not shared |
| Hardcoded Project Paths | MEDIUM | Skill only works for one specific project |
| No Failure Recovery | HIGH | No documented procedure for known failure modes |
| Context Bloat | MEDIUM | SKILL.md >600 lines with low-density content |

## Master Programmers (Informing the 7 Laws)
Laws derived from: Dijkstra, Knuth, Ritchie, Liskov (classical); Carmack, Sweeney, Acton, Muratori, Gregory, Blow (engine); Hickey, Lattner, Torvalds, Bellard, Hotz, Karpathy, Hoare, Kelley, Pike, Metz, Abramov, Karis, Miyazaki, Fowler (modern).

## Compaction Resilience
For reviews >5 files or >2000 LOC: persist intermediate state to `.claude/skills/devTeam/.state/[target]_[timestamp]/`. Write per-file scores as JSON after each analysis. Read on recovery. Cleanup after final report.

## Failure Recovery Playbook (v4.0 — Holy Tool Resilience)

DevTeam is a Holy Tool that other skills depend on. It must handle its own failures gracefully.

### Known Failure Modes & Recovery

| Failure Mode | Detection | Recovery | Prevention |
|-------------|-----------|----------|------------|
| **_learnings.md corrupted** | Malformed markdown, parse errors when reading | Skip learnings load, use default calibration weights (all ×1.0). Log: "Learnings file corrupted — using default calibration." Fix file after review completes. | Append-only writes. Never read-modify-write. |
| **Domain detection ambiguous** | Multiple domain signals (e.g., .py + SKILL.md) | Use primary target's domain. If still ambiguous, use the domain with MORE files. Last resort: Universal scoring (7 Laws, no domain-specific anti-patterns). | Check primary target file extension first, directory structure second. |
| **State dir orphaned** | `.state/` dir exists from a previous interrupted review | Check timestamp — if >24h old, safe to clean up. If <24h, read state to check for recoverable scores. Log: "Orphaned state dir found: [path]. Age: [X]h. Action: [cleanup/recover]." | Always cleanup in Step 4. Add timestamp to dir name. |
| **Calibration data insufficient** | <3 previous reviews for this domain | Use default weights (all ×1.0). Report: "Insufficient calibration data ([N] reviews). Using default weights." | The incremental checkpoint protocol (v4.0) will accumulate data over time. |
| **Scoring produces invalid output** | Self-validation catches: score >5, total mismatch, missing sections | Self-validation already handles this (v2.1). If validation itself fails: output raw scores with "[UNVALIDATED]" tag. | Run self-validation as final step. |
| **Context compaction mid-review** | Lost track of which files were analyzed | Check state dir for per-file scores. Resume from last persisted file. If no state dir: re-read target files and re-score from scratch. | Use state persistence for all reviews >5 files. |
| **Target files unreadable** | File not found, permission denied, encoding error | Skip unreadable file, note in report: "[file] — SKIPPED: [reason]". Adjust total score proportionally. Never crash the whole review for one bad file. | Verify file existence before parallel read batch. |

### Recovery Priority
1. **Complete the review** — a partial review with some scores is better than no review
2. **Preserve existing data** — never overwrite _learnings.md or state files during recovery
3. **Log the failure** — always append a recovery event to _learnings.md for future prevention
4. **Degrade gracefully** — use defaults (calibration), skip (unreadable files), or tag ([UNVALIDATED]) rather than failing entirely

## Output Self-Validation (v2.1 — Reliability → 5/5)

Before delivering ANY diagnostic report, devTeam validates its own output:

### Required Sections Checklist
Every diagnostic report MUST contain:
- [ ] Diagnostic header (target name, domain, calibration data)
- [ ] Dimension scores table (all 7 dimensions, 0-5 each)
- [ ] Total score with grade
- [ ] Protected dimensions list (or "none" if all <4)
- [ ] Top gaps ranked by weighted impact (or "none" if score ≥30)
- [ ] Anti-patterns found (or "none detected")
- [ ] Strengths to preserve (at least 1 — every system has something working)

### Score Validity Checks
- Each dimension score is 0-5 integer (no fractions, no >5, no <0)
- Total equals sum of individual scores
- Grade matches threshold: A=30-35, B=25-29, C=18-24, D=12-17, F=<12
- Calibrated weights are reported and sum to reasonable range (not all ×0.5 or all ×1.5)
- If previous scores exist: delta is computed correctly

### If validation fails:
- Fix the output before delivering
- Log: "Self-validation caught: [issue]. Corrected before delivery."
- Append to `_learnings.md` as a calibration note

## Priority Scanning (v2.1 — Efficiency → 5/5)

DevTeam does NOT review all files equally. It prioritizes by impact:

### Priority Tiers
1. **CRITICAL (scan first)**: Files changed since last review
   - If git repo: `git diff --name-only [last_review_commit]` or `git diff --name-only HEAD~5`
   - If no git: scan by modification date
2. **HIGH**: Spine files / core modules (known architectural load-bearing files)
   - Detect spine files dynamically from Domain Auto-Detection + project init context
   - UE5 projects: character base classes, game mode, core subsystems (from project init)
   - SaaS projects: middleware, API routes, auth, database schema (from package.json analysis)
   - Skills: SKILL.md, _learnings.md
   - Python tools: main entry point, core classes, CLI interface
   - Trading engines: live trader, signal pipeline, risk manager
3. **MEDIUM**: Files with known architectural debt from previous reviews
4. **LOW**: Stable files with no recent changes and no known debt

### Change-Aware Review (v2.1)
When reviewing a target that was reviewed before:
1. Load previous review from `_learnings.md` (score entry + anti-patterns found)
2. Identify what changed since then (git diff or file modification dates)
3. **Focus the review on changed areas** — don't re-analyze unchanged code in detail
4. **Verify previous anti-patterns**: Are the flagged patterns still present?
5. **Quick-scan unchanged areas**: Verify scores haven't drifted, but don't deep-analyze
6. Report: "Change-aware review: [N] files changed, [M] stable. Focused on changes."

### When to do full scan instead:
- First review of this target (no previous data)
- User explicitly requests full review
- >50% of files changed since last review
- Previous review is >30 days old

## How to Apply This Skill

When this skill activates, follow this process:

### Step 0.0: Session Marker (PRE-EXECUTION — shell-append, compaction-resistant)

**CRITICAL per SL-046/SL-062**: Fire this Bash command IMMEDIATELY on invocation, BEFORE any reads, diagnosis, or Edit calls. It guarantees a durable session marker even if context compaction eats everything else. This is the fix for the broken-pipeline problem (devTeam had 3 learnings despite 125+ invocations).

```bash
SKILL_DIR="$HOME/.claude/skills/devTeam"
TS="$(date +%Y%m%d_%H%M%S)"
DATE="$(date +%Y-%m-%d)"
printf '\n### Session Marker: devTeam — %s\n<!-- meta: { "run_id": "devteam_%s", "domain": "pending", "confidence": "PENDING", "confirmed_count": 0, "roi_score": null, "staleness_check": "%s" } -->\n\n**Phase**: Pre-execution\n**Status**: Session started — target TBD\n**Action**: Full entry written after Phase 4 validation; this marker persists even if run is interrupted\n' "$DATE" "$TS" "$DATE" >> "$SKILL_DIR/_learnings.md"
```

This is a ONE-SHOT Bash tool call. Not an Edit. Not queued. The shell writes directly to disk outside Claude's context. Future invocations can detect interrupted runs via `grep "### Session Marker" _learnings.md | tail -5`.

**Do NOT skip this step.** The entire v4.0 checkpoint protocol depends on this shell-append landing before anything else. Per the 2026-04-10 toolset audit, this is the single fix that converts devTeam from "broken pipeline" to "compounding learning."

### Step 0: Domain Detection & Calibration
1. **Detect domain** from target files/directory (see Domain Auto-Detection table)
2. **Load scoring dimensions** for detected domain
3. **Read `_learnings.md`** — load previous reviews, compute calibrated weights
4. **Read previous scores** for this target (if any) — establish baseline for trend tracking
5. **Read `.claude/shared/_shared_learnings.md`** — apply cross-skill context
6. **Initialize state persistence** if target is large (>5 files or >2000 LOC)

### Step 1: Priority Scan
1. **Classify files** into priority tiers (Critical → High → Medium → Low)
2. **If change-aware**: Load previous review, identify deltas
3. **Plan scan order**: Critical first, Low last (may skip if time-constrained)

### Step 2: Analyze (per file, by priority)

**PARALLEL FILE READ (v3.0)**: Load ALL Critical + High priority files in a **SINGLE message** using multiple simultaneous Read tool calls before analysis begins. Do not read files one-at-a-time sequentially. One message = all priority files loaded simultaneously. This enables cross-file pattern detection from the start and cuts setup time. Medium + Low priority files can be batched in a second parallel message if needed.

**PROJECT DOCS CROSS-REFERENCE (v3.0)**: If the project maintains canonical documentation — read it BEFORE scoring. Per SL-004: project docs typically provide 90%+ of architectural context. The 7 Laws in this skill are distilled from the same sources that typically inform project docs — cross-reference to catch project-specific patterns and tuning values.

**SOURCE CITATION (v3.0)**: When making claims about external patterns, best practices, or architectural standards during analysis — cite the source tier: `[Law N]` (internal law), `[Docs Ch.N]` (project docs), `[T1]`/`[T2]`/`[T3]` (external source). Claims without a source tier are marked `[OPINION]` and carry less weight in gap scoring. This ensures the diagnostic report is fully auditable.

4. **Run the Fluidity Checklist** on system connections
5. **Compute Complexity Metrics** per file:
   - CC: Target <15, Warning 25+
   - CBO: Target ≤9 per class
   - Lines per Function: Target <50, Warning >100
   - Lines per Class/Module: Target <300, Warning >500
   - Depth of Inheritance/Nesting: Target ≤4, Warning >6
6. **Persist intermediate state** if large review (compaction resilience)

### Step 3: Detect & Score
7. **Check for anti-patterns** — domain-specific table, priority-ordered
8. **Apply the appropriate pattern** — Pipeline, interface, delegate, subsystem, or tag
9. **Verify the Seam Test** — Can System N+1 be added by ONLY adding new files?
10. **Generate Fluidity Score v2.1** — Calibrated weights

### Step 4: Validate, Protect & Track
11. **Self-validate output** — run the Required Sections Checklist + Score Validity Checks
12. **Regression Guard** — snapshot baseline, tag protected dimensions
13. **Score Trend Entry** — append to `_learnings.md`
14. **Cleanup state dir** if compaction resilience was used

## Automated Analysis Output

When devTeam runs an architecture review, output these sections:

### Diagnostic Header (v4.0)
```
DEVTEAM v4.0 — DIAGNOSTIC REPORT
═════════════════════════════════
Target: [system/file/module name]
Domain: [auto-detected domain]
Calibration: [N] previous reviews | Weights: Law 1 (×W), Law 2 (×W)...
Previous Score: [X/35 or N/A if first review]
```

### Fluidity Report Card
Use `pdf.heatmap_table()` if generating a PDF, or markdown table if inline:
```
| Module | CC Avg | CBO | LOC | Depth | Score | Grade | Delta |
|--------|--------|-----|-----|-------|-------|-------|-------|
| [mod]  | X      | Y   | Z   | D     | S     | G     | +/-N  |
```

### Laws Scorecard
```
| Law | Score | Weight | Weighted | Issues Found |
|-----|-------|--------|----------|--------------|
| 1   | X/5   | ×W     | X.X      | [count]      |
...
| TOTAL | X/35 | — | X.X/35 | [total] |
```

### Protected Dimensions
```
PROTECTED (score ≥4/5 — must not regress):
  [dimension] — [current score]
  ...
```

### Dependency Graph
Use `pdf.dependency_graph()` to render module relationships visually.

### Integration Checklist Results
For each system connection, show pass/fail on each checklist item with rationale.

## Score Trend Tracking (v2.0)

After every review, append a score entry to `_learnings.md`:

```markdown
### Score: [Target] — [Date] — Domain: [domain]
| Dimension | Score | Previous | Delta |
|-----------|-------|----------|-------|
| [dim]     | X/5   | Y/5      | +/-Z  |
Overall: X/35 | Previous: Y/35 | Delta: +/-Z
Calibration: Law weights [list]
Protected: [dimensions ≥4/5]
Regressions: [none / details]
```

When reviewing a target that has previous score entries, ALWAYS:
1. Load all previous scores for this target
2. Report the trend: improving / stable / declining
3. Highlight dimensions that have consistently improved or consistently stagnated
4. Adjust recommendations based on what's already been tried

## Project Context
Project-agnostic. Context loaded dynamically via Domain Auto-Detection:
- **UE5 projects**: Spine files, project docs, module structure detected from source tree
- **SaaS projects**: Stack detected from package.json, framework configs
- **Skills**: Detected from SKILL.md presence in .claude/skills/
- **Other**: Adapts based on file types and directory structure
Specific paths come from project init skills, NOT hardcoded here.

## Auto-Update Protocol

### Structured Learning Entry Format
```markdown
## Review: [Context] — [Date] — Domain: [domain]

### Laws Triggered
| Law | Issues Found | Severity | Module | Calibration Accurate? |
|-----|-------------|----------|--------|-----------------------|
| Law N | [desc] | CRIT/HIGH/MED/LOW | [module] | Yes/No (adjust to ×W) |

### Anti-Patterns Found
| Pattern | Count | Where | Fix Applied | Domain-Specific? |
|---------|-------|-------|-------------|------------------|
| [pattern] | N | [location] | Yes/No | Yes/No |

### Score Entry
| Dimension | Score | Previous | Delta |
|-----------|-------|----------|-------|
| [dim] | X/5 | Y/5 | +/-Z |
Overall: X/35 | Previous: Y/35 | Delta: +/-Z

### Regression Guard Results
Protected dimensions: [list]
Regressions detected: [none / details]
Net score change: +/-Z

### Cross-Skill Observations
- [observations about other skills' output quality, if applicable]
```

### What DevTeam Tracks (Cumulative)
- Which Laws catch the most issues per domain (calibration data)
- Which anti-patterns appear most frequently per domain (priority rankings)
- Fluidity score trends per target over time
- Architecture debt resolution rate — is debt growing or shrinking?
- Which Fluidity Checklist items are most commonly failed per domain
- Which patterns (Pipeline, Interface, Delegate, etc.) are most effective per use case
- Regression frequency — how often do fixes break protected dimensions?
- Calibration accuracy — are the learned weights producing better diagnoses?

## Structured Output Contract (v4.0 — Parseable Interface)

DevTeam's diagnostic report is consumed by profTeam, Cycle, and Holy Trinity. To ensure reliable integration, every report includes a machine-readable summary block at the end:

```markdown
<!-- devteam_output: {
  "target": "[name]",
  "domain": "[detected]",
  "total_score": [int],
  "grade": "[A-F]",
  "dimensions": [
    {"name": "[dim]", "score": [int], "status": "[PROTECTED|GAP|OK]"}
  ],
  "protected": ["[dim names]"],
  "top_gaps": [
    {"description": "[gap]", "severity": "[CRIT|HIGH|MED|LOW]", "weighted_impact": [float]}
  ],
  "anti_patterns": [{"name": "[pattern]", "severity": "[level]", "location": "[where]"}],
  "research_targets": ["[specific question for profTeam]"],
  "calibration_accuracy": [float or null]
} -->
```

This comment block is invisible in markdown rendering but parseable by any downstream skill. It enables:
- profTeam to extract research targets programmatically
- Holy Trinity to compare scores across passes without parsing tables
- Cycle to feed gap descriptions directly to Blueprint
- Automated regression detection across runs

**Backward compatibility**: The human-readable markdown tables remain the primary output. The JSON comment block is ADDED at the end, never replaces the tables.

### Fluidify Protocol (When Called by Cycle)
When DevTeam is invoked alongside Cycle, it serves as the **quality gate** between iterations:
- After each Cycle pass, DevTeam scores the output
- Identifies the TOP 3 weakest points for the next Cycle to address
- Professor targets research specifically at DevTeam's identified gaps
- This creates a directed improvement loop: DevTeam finds weakness → Professor fills gap → Blueprint integrates → DevTeam re-scores

## Incremental Learning Checkpoint Protocol (v4.0 — CRITICAL FIX)

**Root cause of broken pipeline**: DevTeam had 2 learnings despite 125+ invocations because writes only happened at end-of-session — context compaction ate them every time. v4.0 mandates IMMEDIATE writes at each milestone.

### Mandatory Write Points

After EACH of these milestones, IMMEDIATELY append to `_learnings.md` — do NOT queue for later:

| Milestone | What to Write | Format |
|-----------|--------------|--------|
| **After Domain Detection** | Domain detected, calibration weights loaded, data points count | `### Checkpoint: [Target] — [Date] — Domain Detection\n- Domain: [X]\n- Calibration: [weights]\n- Data points: [N] previous reviews` |
| **After Scoring** | All 7 dimension scores, total, grade, protected dimensions | Score entry table (see Auto-Update Protocol format above) |
| **After Anti-Pattern Scan** | Anti-patterns found with severity, location, domain-specific flag | Anti-pattern table (see format above) |
| **After Regression Check** | Regression status, protected dims maintained/violated | `Regression: [none/details]. Protected: [list] — [maintained/violated]` |
| **After Calibration Echo** | Whether calibration weights predicted the right gaps (see below) | Calibration accuracy entry |

### Write Mechanics
```
# ALWAYS use append-only writes. NEVER read-modify-write.
# Each checkpoint is a self-contained markdown block.
# If context compaction hits mid-review, checkpoints already on disk survive.
```

### Minimum Viable Learning
Even if a review is interrupted or abbreviated, the scoring checkpoint alone has high value — it feeds calibration weights for future reviews. A review that writes ONLY its scores is infinitely more valuable than one that writes nothing.

## Calibration Echo (v4.0 — Self-Improving Accuracy)

After every review, compute whether the calibration weights correctly predicted which dimensions would have the largest gaps:

1. **Before scoring**: Record which Laws have the highest calibration weights (predicted most impactful)
2. **After scoring**: Record which Laws ACTUALLY had the largest gaps
3. **Compare**: 
   - `prediction_accuracy = overlap(predicted_top_3, actual_top_3) / 3`
   - If accuracy < 0.33: weights are **MISCALIBRATED** — log adjustment, shift weights toward actual top gaps
   - If accuracy 0.33-0.66: weights are **DEVELOPING** — maintain current weights, flag for review after 3 more data points. No adjustment yet — insufficient signal to distinguish noise from systematic error.
   - If accuracy ≥ 0.67: weights are **WELL-CALIBRATED** — log confirmation, no changes needed
4. **Write to _learnings.md immediately**:
   ```
   ### Calibration Echo: [Target] — [Date]
   - Predicted top gaps: Law [N], Law [M], Law [K]
   - Actual top gaps: Law [X], Law [Y], Law [Z]
   - Accuracy: [X]/3 ([percentage])
   - Adjustment: [none / increase Law X weight / decrease Law N weight]
   ```

Over time, calibration echoes accumulate into a feedback loop: weights that consistently miss get downweighted, weights that consistently hit get upweighted. This is how devTeam gets SMARTER with every invocation.

## Cross-Invocation Learning Aggregation (v4.0)

After every 5th checkpoint entry in `_learnings.md`, auto-compute aggregates:

1. **Most common anti-patterns per domain** (top 3 by frequency)
2. **Most impactful Laws per domain** (top 3 by gap size)
3. **Calibration accuracy trend** (improving / stable / degrading)
4. **Protected dimension stability** (which dims consistently score ≥4)

Write aggregates as a `### Aggregate: [Domain] — [Date]` entry. These aggregates are the PRIMARY input for calibration weight adjustment.

## Protocols

Follow `${CLAUDE_PLUGIN_ROOT}/shared/_shared_protocols.md` for: pre-work loading, source tiers, parallel execution, post-invocation learning, incremental checkpoints, session safety, structured learning format, cross-skill auto-promotion, staleness detection.
