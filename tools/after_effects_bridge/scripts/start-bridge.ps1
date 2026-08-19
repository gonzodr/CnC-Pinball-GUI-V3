param(
    [string]$BridgeScript
)

$ErrorActionPreference = "Stop"

if (-not $BridgeScript) {
    $profilesRoot = Join-Path $env:APPDATA "Adobe\After Effects"
    $installedScripts = Get-ChildItem -LiteralPath $profilesRoot -Directory | Where-Object {
        $_.Name -match '^\d+\.\d+$'
    } | ForEach-Object {
        $candidate = Join-Path $_.FullName "Scripts\CodexAEBridge.jsx"
        if (Test-Path -LiteralPath $candidate) {
            [PSCustomObject]@{ Version = [version]$_.Name; Path = $candidate }
        }
    } | Sort-Object Version -Descending
    $installed = $installedScripts | Select-Object -First 1
    if (-not $installed) {
        throw "No installed Codex AE bridge found. Run install.ps1 first."
    }
    $BridgeScript = $installed.Path
}

if (-not (Test-Path -LiteralPath $BridgeScript)) {
    throw "Bridge script not found: $BridgeScript"
}

$adobeRoots = @(
    "C:\Program Files\Adobe",
    "C:\Program Files (x86)\Adobe"
)
$executables = foreach ($root in $adobeRoots) {
    if (Test-Path -LiteralPath $root) {
        Get-ChildItem -LiteralPath $root -Directory -Filter "Adobe After Effects *" | ForEach-Object {
            Get-ChildItem -LiteralPath (Join-Path $_.FullName "Support Files") -Filter "AfterFX.exe" -File -ErrorAction SilentlyContinue
        }
    }
}
$afterEffects = $executables | Sort-Object {
    try { [version]$_.VersionInfo.ProductVersion } catch { [version]"0.0" }
} -Descending | Select-Object -First 1

if (-not $afterEffects) {
    throw "AfterFX.exe was not found under the standard Adobe install folders."
}

Start-Process -FilePath $afterEffects.FullName -ArgumentList @("-r", ('"' + $BridgeScript + '"'))
[PSCustomObject]@{
    AfterEffects = $afterEffects.FullName
    BridgeScript = $BridgeScript
    Started = $true
}
