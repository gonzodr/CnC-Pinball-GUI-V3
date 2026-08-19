param(
    [string]$AfterEffectsVersion,
    [switch]$StartAfterEffects
)

$ErrorActionPreference = "Stop"
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$sourceEngine = Join-Path $bridgeRoot "ae\CodexAEBridge.jsx"
$sourcePanel = Join-Path $bridgeRoot "ae\Codex AE Bridge Panel.jsx"
$aePreferencesRoot = Join-Path $env:APPDATA "Adobe\After Effects"

if (-not (Test-Path -LiteralPath $aePreferencesRoot)) {
    throw "No After Effects preferences folder found at $aePreferencesRoot"
}

$versionFolders = Get-ChildItem -LiteralPath $aePreferencesRoot -Directory | Where-Object {
    $_.Name -match '^\d+\.\d+$'
}

if ($AfterEffectsVersion) {
    $selected = $versionFolders | Where-Object Name -EQ $AfterEffectsVersion | Select-Object -First 1
    if (-not $selected) {
        throw "After Effects profile $AfterEffectsVersion was not found."
    }
} else {
    $selected = $versionFolders | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
}

if (-not $selected) {
    throw "No usable After Effects version profile was found."
}

$scriptsFolder = Join-Path $selected.FullName "Scripts"
$panelFolder = Join-Path $selected.FullName "Scripts\ScriptUI Panels"
$queueRoot = Join-Path $env:APPDATA "CodexAEBridge"

New-Item -ItemType Directory -Force -Path $scriptsFolder | Out-Null
New-Item -ItemType Directory -Force -Path $panelFolder | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $queueRoot "inbox") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $queueRoot "outbox") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $queueRoot "previews") | Out-Null

$installedEngine = Join-Path $scriptsFolder "CodexAEBridge.jsx"
$installedPanel = Join-Path $panelFolder "Codex AE Bridge Panel.jsx"
$legacyStartupEngine = Join-Path $scriptsFolder "Startup\CodexAEBridge.jsx"
if (Test-Path -LiteralPath $legacyStartupEngine) {
    Remove-Item -LiteralPath $legacyStartupEngine -Force
}
Copy-Item -LiteralPath $sourceEngine -Destination $installedEngine -Force
Copy-Item -LiteralPath $sourcePanel -Destination $installedPanel -Force

[PSCustomObject]@{
    AfterEffectsProfile = $selected.Name
    EngineScript = $installedEngine
    PanelScript = $installedPanel
    QueueRoot = $queueRoot
}

if ($StartAfterEffects) {
    & (Join-Path $PSScriptRoot "start-bridge.ps1") -BridgeScript $installedEngine
}
