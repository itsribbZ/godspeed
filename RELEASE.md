# Release Process

Short, explicit checklist for cutting a new godspeed release. Do every step in order.

## 1. Decide the version bump

Following SemVer:
- **Patch** (2.3.4 → 2.3.5): bug fixes, doc corrections, path scrubs, no behavior change
- **Minor** (2.3.x → 2.4.0): new skill, new command, new hook event, additive pipeline phase
- **Major** (2.x.x → 3.0.0): breaking change to install/hook contract or manifest schema

## 2. Files to bump

Edit these when cutting a release — nothing else carries the version string:

| File | Field | Notes |
|------|-------|-------|
| `plugins/godspeed/.claude-plugin/plugin.json` | `"version"` | The canonical version — this is what Claude Code reads |
| `CHANGELOG.md` | New `## [X.Y.Z] - YYYY-MM-DD` section at top | Document what changed under Added / Changed / Fixed / Removed |

The marketplace manifest (`.claude-plugin/marketplace.json`) pulls from the plugin.json version automatically — do NOT hand-edit version there.

The README doesn't carry a version string — leave it alone unless skill/command/hook counts actually changed.

## 3. Sanity checks before tagging

Run these from the repo root:

```bash
# Verify skill/command/hook counts match claims
echo "skills: $(ls plugins/godspeed/skills/ | wc -l)"
echo "commands: $(ls plugins/godspeed/commands/ | wc -l)"
python -c "import json; print('hooks:', len(json.load(open('plugins/godspeed/hooks/hooks.json'))['hooks']))"

# Verify zero personal leaks AND zero private-project-name leaks (should return nothing).
# NOTE: "your-X" / "Your-X" patterns are intentional public-safe placeholders for
# private project names (e.g. "Your-Automation-Skill" stands in for the real product).
# Do NOT add those to this regex — they are the scrub itself, not the leak.
grep -rn "jbro1\|jribbe04\|Jacob Ribbe\|Jacob wants\|\bJacob\b\|Desktop/T1\|bionics\|Bionics\|sworder\|Sworder\|quantified\|your-trading-project\|forge3d\|your-3d-project\|forge3D\|enigma-init\|enigma_vault\|buddy-init\|ribbz-init\|career-ops\|career_ops\|AnimBPDoctor\|SemperFidelis\|syncscout\|Kashi\|atelier\|Sentinel" plugins/ toke/ README.md install.sh install.ps1 LICENSE 2>/dev/null | grep -v __pycache__

# Verify git state is clean
git status --short
```

If any check fails, fix before tagging.

## 4. Commit, tag, push

```bash
# One commit for the release
git add CHANGELOG.md plugins/godspeed/.claude-plugin/plugin.json <other files you changed>
git commit -m "release(vX.Y.Z): <one-line summary>"

# Tag
git tag -a vX.Y.Z -m "vX.Y.Z — <one-line summary>"

# Push commit and tag
git push origin master
git push origin vX.Y.Z
```

## 5. Verify the ship

```bash
# Confirm tag landed on the remote
git ls-remote --tags origin | grep vX.Y.Z

# Fresh-install test from a clean Claude Code session:
#   /plugin marketplace add itsribbZ/godspeed
#   /plugin install godspeed@itsribbZ-godspeed
#   /godspeed:godspeed-info         → should render
#   /godspeed:godspeed-settings     → should load manifest
#   /godspeed:brain-score "refactor 4 files" → should classify S4
```

## Directory layout reference

```
godspeed-plugin/
├── .claude-plugin/
│   └── marketplace.json          ← marketplace manifest (pulls version from plugin.json)
├── .github/workflows/            ← CI (auto-updates count claims, see workflow README)
├── CHANGELOG.md                  ← version history (EDIT on release)
├── LICENSE                       ← MIT
├── README.md                     ← public-facing pitch + install instructions
├── RELEASE.md                    ← this file
├── install.sh / install.ps1      ← Option B standalone installers
├── plugins/godspeed/             ← THE PLUGIN (Option A — /plugin install target)
│   ├── .claude-plugin/
│   │   └── plugin.json           ← CANONICAL VERSION LIVES HERE
│   ├── automations/              ← Python runtime (Brain classifier, Homer pantheon)
│   ├── commands/                 ← 3 slash commands (brain-score, godspeed-info, godspeed-settings)
│   ├── hooks/                    ← 5 Claude Code lifecycle hooks + hooks.json manifest
│   ├── shared/                   ← shared protocols + learnings + PDF contract
│   └── skills/                   ← 18 skills
└── toke/                         ← STANDALONE ENGINE (Option B install target)
    ├── skills/                   ← 16 skills (installer copies to ~/.claude/skills/)
    ├── commands/                 ← 2 slash commands
    ├── hooks/                    ← lifecycle hooks
    ├── automations/              ← runtime mirror of plugins/godspeed/automations/
    └── tokens/                   ← cost-accounting utilities
```

**Note on the `plugins/` vs `toke/` split:** the plugin is what ships via Claude Code's marketplace; the `toke/` tree is what `install.sh` copies into `~/.claude/skills/` for users who prefer the standalone install. Several skills exist in both trees — when you edit one, check if the other needs the same change.
