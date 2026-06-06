# Builds a small distributable zip. Heavy assets are downloaded during install.ps1.

$ErrorActionPreference = "Stop"



$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Set-Location $projectDir



. (Join-Path $projectDir "scripts\release_manifest.ps1")



$distDir = Join-Path $projectDir "dist"

$onlineDir = Join-Path $distDir "online"

$stageDir = Join-Path $distDir ".build\online-zip-stage"

$zipPath = Join-Path $onlineDir "Assistance-Setup.zip"



New-Item -ItemType Directory -Force -Path $onlineDir | Out-Null



Write-Host ""

Write-Host "==> Staging developer zip (v$ReleaseVersion)" -ForegroundColor Cyan

Copy-ReleaseStage -ProjectDir $projectDir -StageDir $stageDir -Items (Get-ReleaseDeveloperZipItems) -WriteBuildInfo



if (Test-Path $zipPath) {

    Remove-Item $zipPath -Force

}

Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $zipPath -Force



$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)

Write-Host ""

Write-Host "Release zip created:" -ForegroundColor Green

Write-Host $zipPath

Write-Host ("Version: {0}" -f $ReleaseVersion)

Write-Host ("Size: {0} MB" -f $sizeMb)

Write-Host ""

Write-Host "For most users, prefer dist\online\Assistance-Setup.exe instead."

Write-Host "This zip is for developers who already have Python. Run .\install.ps1 after unzip."

