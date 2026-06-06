# Rebuild ALL install packages from the latest source code.
# Run this after any fix or feature change before sending to others.
param(
    [switch]$IncludeLLM,
    [switch]$SkipOffline,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

. (Join-Path $projectDir "scripts\release_manifest.ps1")

Write-Host ""
Write-Host "=== Rebuilding Assistance install packages ===" -ForegroundColor Cyan
Write-Host ("Version: {0}" -f $ReleaseVersion)
Write-Host "Source: $projectDir"
Write-Host ""

& (Join-Path $projectDir "build_setup.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $projectDir "package_release.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipOffline) {
    $offlineArgs = @("-File", (Join-Path $projectDir "build_offline_package.ps1"))
    if ($IncludeLLM) { $offlineArgs += "-IncludeLLM" }
    if ($SkipZip) { $offlineArgs += "-SkipZip" }
    & powershell -NoProfile -ExecutionPolicy Bypass @offlineArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "=== All packages updated ===" -ForegroundColor Green
Write-Host ""
Write-Host "  dist\online\Assistance-Setup.exe   -> PCs with internet (recommended)"
Write-Host "  dist\online\Assistance-Setup.zip    -> developers with Python"
Write-Host "  dist\offline\Assistance-Offline\    -> copy whole folder to offline PC"
Write-Host "  dist\offline\Assistance-Offline.zip -> unzip on offline PC"
Write-Host "  dist\offline\Assistance-Offline-Setup.exe -> offline one-click installer"
Write-Host ""
