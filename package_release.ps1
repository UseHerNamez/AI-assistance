# Builds a small distributable zip. Heavy assets are downloaded during install.ps1.
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$distDir = Join-Path $projectDir "dist"
$onlineDir = Join-Path $distDir "online"
$stageDir = Join-Path $distDir ".build\online-zip-stage"
$zipPath = Join-Path $onlineDir "Assistance-Setup.zip"

New-Item -ItemType Directory -Force -Path $onlineDir | Out-Null

if (Test-Path $stageDir) {
    Remove-Item $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

$items = @(
    "quest_assistant",
    "requirements.txt",
    "install.ps1",
    "install_startup.ps1",
    "install_local_llm.ps1",
    "download_vosk_model.ps1",
    "run_quest_assistant.ps1",
    "launch_assistance.vbs",
    "README.md",
    "INSTALL.md"
)

foreach ($item in $items) {
    $source = Join-Path $projectDir $item
    if (-not (Test-Path $source)) {
        throw "Missing release file: $item"
    }
    Copy-Item -Path $source -Destination (Join-Path $stageDir $item) -Recurse -Force
}

# Drop Python cache from the staged copy.
Get-ChildItem -Path $stageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
New-Item -ItemType Directory -Force -Path $distDir | Out-Null
Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $zipPath -Force

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Release zip created:" -ForegroundColor Green
Write-Host $zipPath
Write-Host ("Size: {0} MB" -f $sizeMb)
Write-Host ""
Write-Host "For most users, prefer dist\online\Assistance-Setup.exe instead."
Write-Host "This zip is for developers who already have Python."
