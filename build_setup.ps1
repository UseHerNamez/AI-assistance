# Builds Assistance-Setup.exe (one-click installer, no Python required for end users).
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$distDir = Join-Path $projectDir "dist"
$onlineDir = Join-Path $distDir "online"
$stageDir = Join-Path $distDir ".build\online-stage"

New-Item -ItemType Directory -Force -Path $onlineDir | Out-Null

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-InnoCompiler {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    ) | Where-Object { $_ -and (Test-Path $_) }
    return $candidates | Select-Object -First 1
}

Write-Step "Staging installer files"
if (Test-Path $stageDir) {
    Remove-Item $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

$items = @(
    "quest_assistant",
    "scripts",
    "requirements.txt",
    "download_vosk_model.ps1",
    "install_local_llm.ps1",
    "install_startup.ps1",
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

Get-ChildItem -Path $stageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Step "Looking for Inno Setup compiler"
$iscc = Find-InnoCompiler
if (-not $iscc) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Inno Setup not found. Installing via winget..."
        winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
        Start-Sleep -Seconds 5
        $iscc = Find-InnoCompiler
    }
}

if (-not $iscc) {
    throw @"
Inno Setup was not found.

Install Inno Setup 6 from https://jrsoftware.org/isdl.php
Then run this script again:

  .\build_setup.ps1
"@
}

Write-Step "Compiling Assistance-Setup.exe"
& $iscc (Join-Path $projectDir "installer.iss")

$setupExe = Join-Path $onlineDir "Assistance-Setup.exe"
if (-not (Test-Path $setupExe)) {
    throw "Build failed: $setupExe was not created."
}

$sizeMb = [math]::Round((Get-Item $setupExe).Length / 1MB, 2)
Write-Host ""
Write-Host "Online installer created:" -ForegroundColor Green
Write-Host $setupExe
Write-Host ("Size: {0} MB (downloads during install on target PC)" -f $sizeMb)
Write-Host ""
Write-Host "Send dist\online\Assistance-Setup.exe to a PC with internet."
