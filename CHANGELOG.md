# Changelog

All notable changes to godspeed are tracked here. Versioning follows [SemVer](https://semver.org/).

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

[2.3.9]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.9
[2.3.8]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.8
[2.3.7]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.7
[2.3.6]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.6
[2.3.5]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.5
[2.3.4]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.4
[2.2.0]: https://github.com/itsribbZ/godspeed/releases/tag/v2.2.0
