# Installs Assistance from bundled offline assets (no internet required).
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,
    [switch]$IncludeLLM,
    [switch]$SkipVoice,
    [switch]$SkipStartup,
    [switch]$Silent
)

$ErrorActionPreference = "Stop"

$AssetsDir = Join-Path $InstallDir "offline_assets"
$RuntimeDir = Join-Path $InstallDir "runtime\python"
$BundledPython = Join-Path $AssetsDir "python"
$BundledVosk = Join-Path $AssetsDir "vosk-model-small-en-us-0.15"
$LogPath = Join-Path $InstallDir "setup.log"

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogPath -Value $line
    if (-not $Silent) {
        Write-Host $Message
    }
}

function Copy-Tree([string]$Source, [string]$Destination) {
    if (-not (Test-Path $Source)) {
        throw "Missing bundled asset: $Source"
    }
    if (Test-Path $Destination) {
        Remove-Item $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Destination -Parent) | Out-Null
    Copy-Item -Path $Source -Destination $Destination -Recurse -Force
}

try {
    Set-Location $InstallDir
    Write-Log "Assistance offline setup started in $InstallDir"

    if (-not (Test-Path $BundledPython)) {
        throw "Bundled Python runtime was not found at $BundledPython"
    }

    Write-Log "Installing bundled Python runtime"
    Copy-Tree $BundledPython $RuntimeDir

    if (-not $SkipVoice) {
        if (-not (Test-Path $BundledVosk)) {
            throw "Bundled Vosk model was not found at $BundledVosk"
        }
        $voiceDestRoot = Join-Path $HOME ".quest_assistant\models"
        $voiceDest = Join-Path $voiceDestRoot "vosk-model-small-en-us-0.15"
        Write-Log "Installing bundled speech model"
        New-Item -ItemType Directory -Force -Path $voiceDestRoot | Out-Null
        Copy-Tree $BundledVosk $voiceDest
    }

    if ($IncludeLLM) {
        $ollamaSetup = Join-Path $AssetsDir "ollama\OllamaSetup.exe"
        $bundledModels = Join-Path $AssetsDir "ollama\models"
        if (Test-Path $ollamaSetup) {
            Write-Log "Installing bundled Ollama"
            $ollamaArgs = "/SP- /VERYSILENT /NORESTART"
            $proc = Start-Process -FilePath $ollamaSetup -ArgumentList $ollamaArgs -Wait -PassThru
            if ($proc.ExitCode -ne 0) {
                Write-Log "Ollama installer exit code: $($proc.ExitCode)"
            }
            Start-Sleep -Seconds 3
        } else {
            Write-Log "Bundled Ollama installer not found; skipping Ollama install"
        }

        if (Test-Path $bundledModels) {
            $userOllama = Join-Path $HOME ".ollama"
            $userModels = Join-Path $userOllama "models"
            Write-Log "Copying bundled LLM model files"
            New-Item -ItemType Directory -Force -Path $userModels | Out-Null
            Copy-Item -Path (Join-Path $bundledModels "*") -Destination $userModels -Recurse -Force
        } else {
            Write-Log "Bundled LLM model files not found; skipping model copy"
        }
    }

    Set-Content -Path (Join-Path $InstallDir "offline.mode") -Value "1" -Encoding ASCII
    [Environment]::SetEnvironmentVariable("JARVIS_TTS_BACKEND", "sapi", "User")

    if (-not $SkipStartup) {
        Write-Log "Adding to Windows startup"
        & (Join-Path $InstallDir "install_startup.ps1")
    }

    Write-Log "Offline setup completed successfully"
    exit 0
} catch {
    Write-Log "Offline setup failed: $($_.Exception.Message)"
    exit 1
}
