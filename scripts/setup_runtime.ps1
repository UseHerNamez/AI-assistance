# Sets up embedded Python + libraries for Assistance (no system Python required).
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,
    [switch]$IncludeLLM,
    [switch]$SkipVoice,
    [switch]$SkipStartup,
    [switch]$Silent
)

$ErrorActionPreference = "Stop"

$PythonVersion = "3.12.10"
$PythonTag = "python-$PythonVersion-embed-amd64"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonTag.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

$RuntimeRoot = Join-Path $InstallDir "runtime"
$RuntimeDir = Join-Path $RuntimeRoot "python"
$LogPath = Join-Path $InstallDir "setup.log"

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogPath -Value $line
    if (-not $Silent) {
        Write-Host $Message
    }
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
    $content = Get-Content $pth.FullName
    $updated = @()
    $siteEnabled = $false
    foreach ($line in $content) {
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

try {
    Set-Location $InstallDir
    Ensure-Directory $RuntimeRoot
    Write-Log "Assistance setup started in $InstallDir"

    $pythonExe = Join-Path $RuntimeDir "python.exe"
    if (-not (Test-Path $pythonExe)) {
        Write-Log "Downloading embedded Python $PythonVersion"
        Ensure-Directory $RuntimeDir
        $zipPath = Join-Path $RuntimeRoot "$PythonTag.zip"
        Invoke-WebRequest -Uri $EmbedUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $RuntimeDir -Force
        Remove-Item $zipPath -Force
        Enable-EmbeddedSitePackages $RuntimeDir
    }

    $pipExe = Join-Path $RuntimeDir "Scripts\pip.exe"
    if (-not (Test-Path $pipExe)) {
        Write-Log "Installing pip"
        $getPipPath = Join-Path $RuntimeRoot "get-pip.py"
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPipPath
        & $pythonExe $getPipPath --no-warn-script-location
        Remove-Item $getPipPath -Force -ErrorAction SilentlyContinue
    }

    Write-Log "Installing Python libraries"
    & $pythonExe -m pip install --upgrade pip --no-warn-script-location
    & $pythonExe -m pip install -r (Join-Path $InstallDir "requirements.txt") --no-warn-script-location

    if (-not $SkipVoice) {
        Write-Log "Downloading speech recognition model"
        & (Join-Path $InstallDir "download_vosk_model.ps1")
    }

    if ($IncludeLLM) {
        Write-Log "Installing local LLM (optional, large download)"
        try {
            & (Join-Path $InstallDir "install_local_llm.ps1")
        } catch {
            Write-Log "Local LLM install skipped or failed: $($_.Exception.Message)"
        }
    }

    if (-not $SkipStartup) {
        Write-Log "Adding to Windows startup"
        & (Join-Path $InstallDir "install_startup.ps1")
    }

    Write-Log "Setup completed successfully"
    exit 0
} catch {
    Write-Log "Setup failed: $($_.Exception.Message)"
    exit 1
}
