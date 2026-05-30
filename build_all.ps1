# Rebuild ALL install packages from the latest source code.
# Run this after any fix or feature change before sending to others.
param(
    [switch]$IncludeLLM,
    [switch]$SkipOffline
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

Write-Host ""
Write-Host "=== Rebuilding Assistance install packages ===" -ForegroundColor Cyan
Write-Host "Source: $projectDir"
Write-Host ""

& (Join-Path $projectDir "build_setup.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipOffline) {
    $offlineArgs = @("-File", (Join-Path $projectDir "build_offline_package.ps1"))
    if ($IncludeLLM) { $offlineArgs += "-IncludeLLM" }
    & powershell -NoProfile -ExecutionPolicy Bypass @offlineArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "=== All packages updated ===" -ForegroundColor Green
Write-Host ""
Write-Host "  dist\online\   -> PCs with internet"
Write-Host "  dist\offline\  -> PCs without internet"
Write-Host ""
Write-Host "Restart your local copy with: .\run_quest_assistant.ps1"
