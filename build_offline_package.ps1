# Builds a fully offline Assistance package (for air-gapped Windows machines).
# Run this ONCE on a machine WITH internet. Ship the output folder or zip to the offline PC.
param(
    [switch]$IncludeLLM,
    [switch]$SkipSetupExe,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$PythonVersion = "3.12.10"
$PythonTag = "python-$PythonVersion-embed-amd64"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonTag.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$VoskModelName = "vosk-model-small-en-us-0.15"
$VoskUrl = "https://alphacephei.com/vosk/models/$VoskModelName.zip"
$OllamaSetupUrl = "https://ollama.com/download/OllamaSetup.exe"

$distDir = Join-Path $projectDir "dist"
$offlineDir = Join-Path $distDir "offline"
$offlineStage = Join-Path $distDir ".build\offline-stage"
$assetsDir = Join-Path $offlineStage "offline_assets"
$buildTemp = Join-Path $distDir ".build\offline-temp"

New-Item -ItemType Directory -Force -Path $offlineDir | Out-Null

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Enable-EmbeddedSitePackages([string]$Dir) {
    $pth = Get-ChildItem -Path $Dir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pth) {
        throw "Could not find python*._pth in $Dir"
    }
    $updated = @()
    $siteEnabled = $false
    foreach ($line in Get-Content $pth.FullName) {
        if ($line -match '^\s*#\s*import site') {
            $updated += "import site"
            $siteEnabled = $true
        } else {
            $updated += $line
        }
    }
    if (-not $siteEnabled) {
        $updated += "import site"
    }
    if ($updated -notcontains "Lib\site-packages") {
        $updated += "Lib\site-packages"
    }
    Set-Content -Path $pth.FullName -Value $updated -Encoding ASCII
}

function Get-BuildPython {
    $venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "Python is required on the BUILD machine to prepare the offline package."
}

function Find-InnoCompiler {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ }
    return $candidates | Select-Object -First 1
}

Write-Step "Preparing offline staging folder"
if (Test-Path $offlineStage) {
    Remove-Item $offlineStage -Recurse -Force
}
if (Test-Path $buildTemp) {
    Remove-Item $buildTemp -Recurse -Force
}
Ensure-Directory $offlineStage
Ensure-Directory $assetsDir

$appItems = @(
    "quest_assistant",
    "scripts",
    "requirements.txt",
    "download_vosk_model.ps1",
    "install_local_llm.ps1",
    "install_startup.ps1",
    "run_quest_assistant.ps1",
    "launch_assistance.vbs",
    "Install-Assistance.vbs",
    "README.md",
    "OFFLINE.md"
)
foreach ($item in $appItems) {
    $source = Join-Path $projectDir $item
    if (-not (Test-Path $source)) {
        throw "Missing file: $item"
    }
    Copy-Item -Path $source -Destination (Join-Path $offlineStage $item) -Recurse -Force
}
Get-ChildItem -Path $offlineStage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Step "Building bundled Python runtime with all libraries (this takes a few minutes)"
Ensure-Directory $buildTemp
$embedZip = Join-Path $buildTemp "$PythonTag.zip"
$embedDir = Join-Path $buildTemp "python"
$pythonAssets = Join-Path $assetsDir "python"

if (-not (Test-Path $embedZip)) {
    Write-Host "Downloading embedded Python $PythonVersion"
    Invoke-WebRequest -Uri $EmbedUrl -OutFile $embedZip
}
if (Test-Path $embedDir) {
    Remove-Item $embedDir -Recurse -Force
}
Expand-Archive -Path $embedZip -DestinationPath $embedDir -Force
Enable-EmbeddedSitePackages $embedDir

$pythonExe = Join-Path $embedDir "python.exe"
$pipExe = Join-Path $embedDir "Scripts\pip.exe"
if (-not (Test-Path $pipExe)) {
    $getPipPath = Join-Path $buildTemp "get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPipPath
    & $pythonExe $getPipPath --no-warn-script-location
}

Write-Host "Installing libraries into bundled runtime"
& $pythonExe -m pip install --upgrade pip --no-warn-script-location
& $pythonExe -m pip install -r (Join-Path $projectDir "requirements.txt") --no-warn-script-location

if (Test-Path $pythonAssets) {
    Remove-Item $pythonAssets -Recurse -Force
}
Copy-Item -Path $embedDir -Destination $pythonAssets -Recurse -Force

Write-Step "Bundling speech recognition model (~40 MB)"
$voskAssets = Join-Path $assetsDir $VoskModelName
if (-not (Test-Path $voskAssets)) {
    $voskZip = Join-Path $buildTemp "$VoskModelName.zip"
    if (-not (Test-Path $voskZip)) {
        Write-Host "Downloading Vosk model"
        Invoke-WebRequest -Uri $VoskUrl -OutFile $voskZip
    }
    Expand-Archive -Path $voskZip -DestinationPath $assetsDir -Force
}

if ($IncludeLLM) {
    Write-Step "Bundling Ollama + local LLM (large, several GB)"
    $ollamaAssets = Join-Path $assetsDir "ollama"
    Ensure-Directory $ollamaAssets

    $ollamaSetup = Join-Path $ollamaAssets "OllamaSetup.exe"
    if (-not (Test-Path $ollamaSetup)) {
        Write-Host "Downloading Ollama installer"
        Invoke-WebRequest -Uri $OllamaSetupUrl -OutFile $ollamaSetup
    }

    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        $localOllama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $localOllama) {
            $ollamaCmd = Get-Command $localOllama
        }
    }

    $modelName = if ($env:JARVIS_LLM_MODEL) { $env:JARVIS_LLM_MODEL } else { "qwen2.5:3b" }
    $bundledModels = Join-Path $ollamaAssets "models"

    if ($ollamaCmd) {
        Write-Host "Pulling LLM model for bundling: $modelName"
        Start-Process -FilePath $ollamaCmd.Source -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
        Start-Sleep -Seconds 3
        & $ollamaCmd.Source pull $modelName

        $userModels = Join-Path $HOME ".ollama\models"
        if (Test-Path $userModels) {
            if (Test-Path $bundledModels) {
                Remove-Item $bundledModels -Recurse -Force
            }
            Copy-Item -Path $userModels -Destination $bundledModels -Recurse -Force
        } else {
            Write-Warning "Ollama model files were not found after pull. LLM may not work offline."
        }
    } else {
        Write-Warning "Ollama is not installed on this build machine. Install Ollama, pull the model, then rebuild with -IncludeLLM."
    }

    Set-Content -Path (Join-Path $offlineStage "offline.include_llm") -Value "1" -Encoding ASCII
} else {
    Remove-Item (Join-Path $offlineStage "offline.include_llm") -Force -ErrorAction SilentlyContinue
}

Write-Step "Writing offline readme marker"
Set-Content -Path (Join-Path $offlineStage "offline.package") -Value "1" -Encoding ASCII

if (-not $SkipZip) {
    Write-Step "Creating Assistance-Offline.zip"
    $zipPath = Join-Path $offlineDir "Assistance-Offline.zip"
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $offlineStage "*") -DestinationPath $zipPath -CompressionLevel Optimal
    $zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host ("Created: {0} ({1} MB)" -f $zipPath, $zipMb)
}

if (-not $SkipSetupExe) {
    Write-Step "Building Assistance-Offline-Setup.exe"
    $iscc = Find-InnoCompiler
    if (-not $iscc) {
        Write-Warning "Inno Setup not found. Skipping Setup.exe (folder and zip are still usable)."
    } else {
        & $iscc (Join-Path $projectDir "installer_offline.iss")
        $setupExe = Join-Path $offlineDir "Assistance-Offline-Setup.exe"
        if (Test-Path $setupExe) {
            $exeMb = [math]::Round((Get-Item $setupExe).Length / 1MB, 1)
            Write-Host ("Created: {0} ({1} MB)" -f $setupExe, $exeMb)
        }
    }
}

if (Test-Path $buildTemp) {
    Remove-Item $buildTemp -Recurse -Force -ErrorAction SilentlyContinue
}

$folderMb = [math]::Round((Get-ChildItem $offlineStage -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "Offline package ready in dist\offline:" -ForegroundColor Green
Write-Host ("  Assistance-Offline-Setup.exe and/or Assistance-Offline.zip ({0} MB folder staged)" -f $folderMb)
Write-Host ""
Write-Host "Send dist\offline\ to a PC with NO internet."
