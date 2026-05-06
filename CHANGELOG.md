# Changelog

All notable changes to godspeed are tracked here. Versioning follows [SemVer](https://semver.org/).

## [2.4.1] - 2026-05-06
### Fixed
- `plugins/godspeed/automations/homer/homer_integration_test.py` removed from the plugin install path. The test imports `nyx`, which only ships in the Option B (`install.sh`) tree under `toke/automations/homer/sleep/nyx/` — the plugin tree intentionally does not ship the sleep engines (per the v2.3.6 install-path curation rule). Shipping this test in the plugin tree caused `ModuleNotFoundError: No module named 'nyx'` if a user ever tried to run it from a plugin install. The test still ships in the Option B tree where its dependencies actually exist.

### Added
- README "What you get" table — three new rows backed by reproducible commands rather than projections:
  - **Latency** — replaced the stale ~90ms / ~160ms numbers (those were from the pre-`brain_hook_fast.js` bash-subprocess stack) with measured **3–4 ms** cold, median of 5 runs on Windows 11 / Node 22 / v2.4.0. Reproduction command included.
  - **Tests** — **25/25 pass** on the v2.4.0 Homer infrastructure: `test_cost_guard.py` 3/3 + `test_token_accountant.py` 22/22. Reproduction command included.
  - **Footprint** — 1.6 MB plugin install / 2.7 MB install.sh path, 18 vs 16 skills, ~13K LOC Python under `automations/`. Verifiable with `du -sh` + `find -name SKILL.md`.

### Notes
- All "What you get" rows now point at a command the user can run on their own machine to verify the claim. No projection-as-benchmark, no unreproducible internal numbers.

## [2.4.0] - 2026-05-05
### Added
- **Cost Guard (`automations/homer/cost_guard.py`)** — per-tier USD budget table (S0 $0.005 → S5 $5.000), 1.5× soft-cap with mid-flight `BUDGET_EXCEEDED` verdict, post-flight `cost_efficiency.jsonl` receipts. Every live subagent dispatch carries a tier-stamped budget contract; agent aborts gracefully on breach with partial work preserved. CLI: `cost_guard.py budgets | rollup | recent`.
- **Agent Runner (`automations/homer/agent_runner.py`)** — three-mode invocation engine for subagent personas: `dry-run` (default — zero API, schema validation), `live` (Anthropic SDK direct, requires `ANTHROPIC_API_KEY`, cost-guarded), `claude-code` (in-session Agent tool dispatch, zero direct API cost). Telemetry to `agent_invocations.jsonl`.
- **Hook Engineer (`automations/homer/hook_engineer/`)** — QA harness for Claude Code hooks. Probe-shipped checker, side-effect verifier, scaffold generator (Python + Bash for any event), `settings_patch.py` for safe `settings.json` mutation with auto-backup. Catches the class of bug fixed in v2.3.11.
- **Token Accountant (`automations/homer/token_accountant/`)** — msg.id-deduped transcript reader, per-session $USD receipts, cache-thrash detector (Bayesian-smoothed), long-tail spike detector, predicted-vs-actual drift reconciliation. Receipts log to `~/.claude/telemetry/brain/cost_efficiency.jsonl` (shared with cost_guard).
- **Sleep engine refresh** (Option B install.sh path only) — `aurora.py`, `hesper.py`, `nyx.py`, `sleep_cli.py`, `_division.py`, `test_sleep.py`, `run_sleep_nightly.bat` synced from upstream. Engines only — dated proposal/best-practices/audit reports are regenerable per install and intentionally not shipped.
- **Integration tests** — `homer_integration_test.py` + `test_cost_guard.py`. Run via `python -m pytest plugins/godspeed/automations/homer/`.

### Changed
- `plugins/godspeed/skills/godspeed/SKILL.md` — added Cost Guard section (Phase 6.5) documenting tier→budget table, the three guarantees (pre-flight stamp, mid-flight cap, post-flight receipt), and `BUDGET_EXCEEDED` handling protocol. Explicit "free by default" callout above the section.
- `README.md` — added "Free by default" section above "What you get" — documents the two opt-in API surfaces (`brain advise` + `agent_runner --mode live`) and confirms the 5 lifecycle hooks make zero network calls.

### Notes
- **Toke runs at $0 against your Anthropic API account by default.** The classifier is local (Node + regex). Subagents dispatched by Zeus on S3+ tasks use Claude Code's in-session Agent tool, which is part of your existing session — no separate billing channel against your API key. The two opt-in paths (`brain advise` and `agent_runner --mode live`) require an `ANTHROPIC_API_KEY` env var to be set AND an explicit command — they cannot fire silently from a hook.
- `skill_curator/` was evaluated for inclusion but deferred to v2.5.0 — its `_corpus.py` and `_merge_detector.py` modules have hardcoded skill-name lists that need a public-facing redesign rather than a port, and v4.4 "fit in, don't force" mandates against shipping a forced rewrite. v2.5.0 will land it with public-skill-only worked examples.

## [2.3.11] - 2026-05-05
### Fixed
- `install.sh` and `install.ps1` now wire the full **5-event** hook block instead of 3. Previous installs silently missed `PreCompact` (snapshot before context compaction) and `SubagentStop` (capture subagent results), and `SessionEnd` was missing the per-session learning-write hook. A fresh install via either path now matches the plugin's `hooks.json` 1:1 — no degraded observability for `install.sh` users.
- `install.ps1` now emits forward-slash paths inside the printed `settings.json` block. Backslashes inside JSON-quoted strings were silently invalidating the file on Windows; forward slashes work on every platform Claude Code runs on.
- `install.ps1` and `install.sh` now print `bash` as the explicit command for `.sh` hooks. Some PATH configurations were trying to `exec` the shell scripts directly and failing because the shebang resolved to the wrong shell.
- `.github/workflows/validate.yml` was watching `main`; the canonical branch is `master`. Both `push` and `pull_request` triggers updated. CI now actually runs on every push.
- `.github/workflows/validate.yml` `hooks.json` parser updated to handle the top-level `hooks` key wrapper introduced in v2.1.1 (was failing to find any hooks at all when `hooks.json` had the wrapper, but passing because the loop body never executed).

### Added
- `.github/workflows/validate.yml` — new SKILL.md frontmatter validator. Walks `plugins/godspeed/skills/`, checks every `SKILL.md` has frontmatter with at least `name:` and `description:`. Catches the class of bug fixed in this release (devTeam was shipping with no frontmatter at all).
- `plugins/godspeed/skills/devTeam/SKILL.md` — added missing frontmatter block (`name`, `description`, `model`, `effort`). Without frontmatter, Claude Code couldn't auto-load the skill on trigger words; users had to invoke it by name. Now it activates on "devTeam", "deploy devTeam", "fluidity check", "architecture review", and whenever code is written/reviewed/architected.

## [2.3.10] - 2026-04-25
### Fixed
- RELEASE.md scrub regex: removed `your-trading-project` and `your-3d-project` (false-positive scrub targets — these are the substitution OUTPUT for `Quantified` / `Forge3D`, not the leak). Restored the original-name patterns (`Quantified` / `Forge3D`) instead, so the scrub catches re-introductions of the real names. Comment expanded to make the placeholder rule clearer for future maintainers.

## [2.3.9] - 2026-04-25
### Fixed
- Restored RELEASE.md scrub regex literals after a deep history-rewrite pass cosmetically degraded them. The scrub now correctly looks for `Jacob Ribbe` and `Jacob wants` as scrub targets (not as the alias `Ribbz`, which would false-positive on every legitimate author credit).

### Notes
- This release is the first after a `git filter-repo` history rewrite that scrubbed the maintainer's personal email, name, and Windows paths from all commit blobs and commit messages across the entire history. Anyone with a prior clone needs `git fetch --all && git reset --hard origin/master` (or fresh clone) to sync.

## [2.3.8] - 2026-04-25
### Changed
- All narrative references to the maintainer's first name in tracked files (90 occurrences across 31 files — comments, docstrings, README narrative, SKILL.md descriptions, test descriptions) replaced with neutral `the user` / `the user's`. Methodology and behavior unchanged; persona neutralized for public package. Author identity remains as the `Ribbz` alias in `plugin.json`, `marketplace.json`, `LICENSE`, and recent commit history.
- `RELEASE.md` scrub regex hardened: added `\bJacob\b` word-boundary pattern to catch any future standalone first-name leak. The literal `Ribbz` and `the user wants` patterns retained for completeness.

## [2.3.7] - 2026-04-25
### Fixed
- SL-029 in `plugins/godspeed/shared/_shared_learnings.md` — replaced verbatim project-portfolio list with a neutral domain summary. The cognitive-load finding (12+ concurrent projects, primary <10% of session volume) is preserved; the maintainer's specific project names are not. Surfaced by deep pre-push audit (this entry survived prior scrubs because the regex didn't include these particular project names).

## [2.3.6] - 2026-04-24
### Changed
- README: replaced unverifiable "69.0% / 200-prompt held-out eval" claim with honest framing — the eval harness ships, the golden set does not. Maintainer's internal numbers (75.6% / 299-prompt at v2.7) cited as reference, not reproduction target.
- README: cost claims reframed as projections rather than measurements; tied to `brain scan` for user-specific actuals.
- README: repo-layout diagram updated to show actual structure (was missing `plugins/`, `.claude-plugin/`, `CHANGELOG.md`, `RELEASE.md`, `.github/workflows/`).
- README: added "What each install path includes" table — Option A and Option B ship different curated skill sets (11 overlap, 7 unique to A, 5 unique to B).
- `toke/README.md`: same accuracy + cost rewording as root README; added inline `golden_set.json` schema example.
- `toke/README.md`: `brain scan` example output replaced with placeholder — first-time users would have seen $0 against the previous specific-dollar example.

## [2.3.5] - 2026-04-24
### Fixed
- Private project-name leaks scrubbed from `plugins/godspeed/shared/_shared_learnings.md` (3 references in SL-050 / SL-055 metadata + detail)

### Removed
- Hard-coded private-project detection branches in `toke/automations/brain/severity_classifier.py` and `toke/hooks/brain_hook_fast.js`. The shipped versions now only detect `ue5` and `toke` domains. End-users who fork can register their own project detection without shipping the maintainer's private project names.

### Changed
- `RELEASE.md` pre-tag scrub command expanded to catch private project-name patterns in addition to personal-name patterns. Prevents future leaks at release time.

## [2.3.4] - 2026-04-18
### Fixed
- Personal path leaks scrubbed from `toke/` standalone install target (13 files)
- Hook fallback chains now resolve portably: `TOKE_ROOT` → `CLAUDE_PLUGIN_ROOT` → `$HOME/.toke`
- Cross-platform username-prefix stripping in `toke/tokens/*.py` (was hard-coded to maintainer's username)
- `local_ai_guide_gen.py` output path uses `LOCAL_AI_GUIDE_OUT` env var instead of hardcoded path
- LICENSE copyright aligned to public alias (Ribbz)

### Changed
- README: plugin install claim corrected to "18 skills, 3 commands, 5 hooks" (was stale at 16/2/5)
- marketplace.json: skill count corrected to 18 (was stale at 17)
- install.sh + toke/README.md: replaced drifty "65-test"/"68-test" counts with generic "test suite"

### Removed
- `__pycache__/` directories bundled in the filesystem (were untracked but cluttering)

## [2.3.3] - 2026-04-17
### Added
- Generic `init` skill — scans folder, detects project type, reads CLAUDE.md + README + project_status + MEMORY, loads shared protocols grep-first, ecosystem health check, one-screen briefing

## [2.3.2] - 2026-04-17
### Added
- `close-session` v2.3.2: Init→Session Diff, cross-skill auto-promotion, checkpoint verification

## [2.3.1] - 2026-04-17
### Fixed
- Version-check banner now wired into the shipped plugin's `brain_cli`
- Raw-URL version check points at `master` branch, not `main`

## [2.3.0] - 2026-04-17
### Added
- Info Mode (`godspeed info`) — read-only pipeline diagram render
- Version banner at session start

### Removed
- Personal project-specific utility skills

## [2.2.0] - 2026-04-17
### Added
- Full methodology bake-in: pipeline skills (holy-trinity, devTeam, profTeam, professor, blueprint, cycle) + shared infrastructure

## [2.1.2] - 2026-04-17
### Removed
- Personal skills stripped from plugin ship set

### Fixed
- Cross-OS Python detection in install scripts

## [2.1.1] - 2026-04-17
### Fixed
- `hooks.json` events wrapped under top-level `hooks` key (Claude Code schema requirement)

## [2.1.0] - 2026-04-17
### Changed
- Trimmed plugin to operational kernel only (3.0 MB → 835 KB)

## [2.0.0] - 2026-04-16
### Added
- Self-contained engine, one-command install
- Marketplace support via `.claude-plugin/marketplace.json`

## [1.0.0] - Initial release
- godspeed as Claude Code plugin

[2.3.10]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.10
[2.3.9]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.9
[2.3.8]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.8
[2.3.7]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.7
[2.3.6]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.6
[2.3.5]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.5
[2.3.4]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.4
[2.2.0]: https://github.com/itsribbZ/godspeed/releases/tag/v2.2.0
