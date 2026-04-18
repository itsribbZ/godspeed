# Changelog

All notable changes to godspeed are tracked here. Versioning follows [SemVer](https://semver.org/).

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

[2.3.4]: https://github.com/itsribbZ/godspeed/releases/tag/v2.3.4
[2.2.0]: https://github.com/itsribbZ/godspeed/releases/tag/v2.2.0
