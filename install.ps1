# Toke installer — Windows PowerShell 5.1+ / PowerShell Core 7+
#
# What this does:
#   1. Sets TOKE_ROOT to the absolute path of this checkout
#   2. Copies all skills from .\skills\ into $HOME\.claude\skills\
#   3. Copies all slash commands from .\commands\ into $HOME\.claude\commands\
#   4. Syncs the Brain routing manifest (TOML -> JSON)
#   5. Runs the full test suite to verify the install
#   6. Prints the settings.json snippet you need to paste to wire hooks
#
# Usage:
#   .\install.ps1              # install + verify + print hook snippet
#   .\install.ps1 -Force       # overwrite existing skills/commands (backup first)
#   .\install.ps1 -SkipTests   # skip the test verification at the end

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'

function Log   { param([string]$msg) Write-Host "[toke] $msg" -ForegroundColor Cyan }
function Ok    { param([string]$msg) Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn2 { param([string]$msg) Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Die   { param([string]$msg) Write-Host "  XX  $msg" -ForegroundColor Red; exit 1 }

# ── Pre-flight ───────────────────────────────────────────────────────────────
$TokeRoot    = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ClaudeHome  = if ($env:CLAUDE_HOME) { $env:CLAUDE_HOME } else { Join-Path $HOME ".claude" }
$SkillsDir   = Join-Path $ClaudeHome "skills"
$CommandsDir = Join-Path $ClaudeHome "commands"

Log "Toke installer (PowerShell)"
Log "TOKE_ROOT   = $TokeRoot"
Log "CLAUDE_HOME = $ClaudeHome"

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $Python) { Die "Python 3.10+ is required but not found on PATH" }
Log ("python      = " + (& $Python.Source --version 2>&1))

if (-not (Test-Path (Join-Path $TokeRoot "skills")))   { Die "No skills\ dir in $TokeRoot. Run from the repo root." }
if (-not (Test-Path (Join-Path $TokeRoot "commands"))) { Die "No commands\ dir in $TokeRoot." }

New-Item -ItemType Directory -Force -Path $SkillsDir, $CommandsDir | Out-Null

# ── Copy skills ──────────────────────────────────────────────────────────────
Log "Installing skills into $SkillsDir\ ..."
$installed = 0; $skipped = 0; $overwrote = 0
Get-ChildItem -Directory (Join-Path $TokeRoot "skills") | ForEach-Object {
    $src  = $_.FullName
    $name = $_.Name
    $dest = Join-Path $SkillsDir $name
    if (Test-Path $dest) {
        if ($Force) {
            $backup = "$dest.bak." + [int](Get-Date -UFormat %s)
            Move-Item -LiteralPath $dest -Destination $backup
            Copy-Item -LiteralPath $src -Destination $dest -Recurse
            $overwrote++
            Ok "overwrote $name (backup at $backup)"
        } else {
            $skipped++
            Warn2 "skipped $name — already installed (use -Force to overwrite)"
        }
    } else {
        Copy-Item -LiteralPath $src -Destination $dest -Recurse
        $installed++
        Ok "installed $name"
    }
}
Log "Skills: $installed new, $overwrote overwritten, $skipped skipped"

# ── Copy slash commands ──────────────────────────────────────────────────────
Log "Installing slash commands into $CommandsDir\ ..."
Get-ChildItem -File -Filter "*.md" (Join-Path $TokeRoot "commands") | ForEach-Object {
    $src  = $_.FullName
    $dest = Join-Path $CommandsDir $_.Name
    if ((Test-Path $dest) -and (-not $Force)) {
        Warn2 ("skipped " + $_.Name + " — already exists (use -Force to overwrite)")
    } else {
        Copy-Item -LiteralPath $src -Destination $dest -Force
        $slash = "/" + $_.BaseName
        Ok "installed $slash"
    }
}

# ── Sync Brain routing manifest ──────────────────────────────────────────────
Log "Syncing Brain routing manifest (TOML -> JSON) ..."
$manifest = Join-Path $TokeRoot "automations\brain\manifest_to_json.py"
if (Test-Path $manifest) {
    try {
        Push-Location $TokeRoot
        & $Python.Source $manifest | Out-Null
        Pop-Location
        Ok "manifest synced"
    } catch {
        Warn2 "manifest sync failed (non-blocking)"
    }
}

# ── Run tests ────────────────────────────────────────────────────────────────
if (-not $SkipTests) {
    Log "Running test suite (this takes ~30 seconds) ..."

    Write-Host "  [Brain]"
    Push-Location $TokeRoot
    try { & $Python.Source "automations\brain\brain_tests.py" 2>&1 | Select-Object -Last 3 } catch { Warn2 "Brain tests reported issues" }
    Pop-Location

    Write-Host "  [Mnemos]"
    Push-Location (Join-Path $TokeRoot "automations\homer\mnemos")
    try { & $Python.Source "test_mnemos.py" 2>&1 | Select-Object -Last 2 } catch { Warn2 "Mnemos tests reported issues" }
    Pop-Location

    Write-Host "  [Homer integration]"
    Push-Location (Join-Path $TokeRoot "automations\homer")
    try { & $Python.Source "homer_integration_test.py" 2>&1 | Select-Object -Last 2 } catch { Warn2 "Homer integration reported issues" }
    Pop-Location
}

# ── Final instructions ──────────────────────────────────────────────────────
@"

Toke installed.

Next steps:

1. Persist TOKE_ROOT in your PowerShell profile (run once):

     [Environment]::SetEnvironmentVariable('TOKE_ROOT', '$TokeRoot', 'User')

   (Close and reopen your shell for the new env var to take effect.)

2. (Recommended) Route subagents to Sonnet by default:

     [Environment]::SetEnvironmentVariable('CLAUDE_CODE_SUBAGENT_MODEL', 'sonnet', 'User')

3. Wire the hooks into Claude Code. Add this block to $ClaudeHome\settings.json:

     {
       "hooks": {
         "UserPromptSubmit": [
           { "command": "`$env:TOKE_ROOT\hooks\brain_advisor.sh" }
         ],
         "PostToolUse": [
           { "command": "`$env:TOKE_ROOT\hooks\brain_tools_hook.sh" }
         ],
         "SessionEnd": [
           { "command": "`$env:TOKE_ROOT\hooks\session_cost_report.sh" }
         ]
       }
     }

4. Start a new Claude Code session and try it:

     /brain-score "refactor my distributed cache across 4 files"
     # -> should classify as S4 (Opus)

     godspeed
     # -> activates full pipeline for the rest of the turn

5. (Optional) Nightly sleep agents — see README for schtasks / cron setup.

Verify any time with:  .\install.ps1 -SkipTests
"@ | Write-Host
