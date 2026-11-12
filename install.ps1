# Installs the archon-reaper skill for Claude Code by linking it into ~/.claude/skills.
#
#   .\install.ps1            link (a directory junction): edits in this repo take effect immediately
#   .\install.ps1 -Copy      copy the files instead
#   .\install.ps1 -Force     replace an existing archon-reaper skill
#   .\install.ps1 -Uninstall remove the installed skill
param(
    [switch]$Copy,
    [switch]$Force,
    [switch]$Uninstall,
    [string]$SkillsDir = (Join-Path $HOME ".claude\skills")
)
$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "skills\archon-reaper"
$target = Join-Path $SkillsDir "archon-reaper"

function Remove-Installed($path) {
    $item = Get-Item $path -Force
    if ($item.LinkType) { $item.Delete() }          # remove the junction only, never the repo files behind it
    else { Remove-Item $path -Recurse -Force }
}

if ($Uninstall) {
    if (Test-Path $target) { Remove-Installed $target; Write-Host "Removed $target" }
    else { Write-Host "Nothing installed at $target" }
    return
}

if (-not (Test-Path (Join-Path $source "SKILL.md"))) { throw "Cannot find $source\SKILL.md" }
New-Item -ItemType Directory -Force $SkillsDir | Out-Null

if (Test-Path $target) {
    if (-not $Force) { throw "$target already exists. Re-run with -Force to replace it." }
    Remove-Installed $target
}

if ($Copy) {
    Copy-Item $source $target -Recurse
    Write-Host "Copied skill to $target"
} else {
    New-Item -ItemType Junction -Path $target -Target $source | Out-Null
    Write-Host "Linked $target -> $source"
}
Write-Host "Restart Claude Code (or start a new session) so it picks up the skill."
