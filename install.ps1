# Installs Assistance (Jarvis) on this Windows machine.
# Heavy downloads (Vosk speech model, optional Ollama LLM) happen here, not in the zip.
param(
    [switch]$IncludeLLM,
    [switch]$SkipVoice,
    [switch]$SkipStartup,
    [switch]$SkipDesktopShortcut
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-PythonCommand {
    $candidates = @(
        (Get-Command python -ErrorAction SilentlyContinue),
        (Get-Command py -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($cmd in $candidates) {
        if ($cmd.Name -eq "py") {
            return @("py", "-3")
        }
        return @($cmd.Source)
    }
    return $null
}

Write-Step "Checking Python"
$pythonArgs = Get-PythonCommand
if (-not $pythonArgs) {
    throw "Python 3.10+ is required. Install from https://www.python.org/downloads/ and enable 'Add Python to PATH', then run install.ps1 again."
}

$versionText = & $pythonArgs[0] @($pythonArgs[1..($pythonArgs.Length - 1)] + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"))
$parts = $versionText.Trim().Split(".")
$major = [int]$parts[0]
$minor = [int]$parts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    throw "Python 3.10 or newer is required. Found $versionText"
}
Write-Host "Using Python $versionText"

Write-Step "Creating virtual environment"
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $pythonArgs[0] @($pythonArgs[1..($pythonArgs.Length - 1)] + @("-m", "venv", ".venv"))
}
if (-not (Test-Path $venvPython)) {
    throw "Failed to create virtual environment at .venv"
}

Write-Step "Installing Python libraries"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectDir "requirements.txt")

if (-not $SkipVoice) {
    Write-Step "Downloading speech recognition model (~40 MB)"
    & (Join-Path $projectDir "download_vosk_model.ps1")
} else {
    Write-Host "Skipped Vosk model download (-SkipVoice)."
}

if ($IncludeLLM) {
    Write-Step "Installing local LLM (Ollama + model, may take several minutes)"
    & (Join-Path $projectDir "install_local_llm.ps1")
} else {
    Write-Host ""
    Write-Host "Skipped local LLM install. Jarvis will still work with the built-in parser."
    Write-Host "To add the LLM later, run: .\install_local_llm.ps1"
}

if (-not $SkipDesktopShortcut) {
    Write-Step "Creating desktop shortcut"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $launcher = Join-Path $projectDir "launch_assistance.vbs"
    $shortcutPath = Join-Path $desktop "Assistance.lnk"

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
    $shortcut.Arguments = "`"$launcher`""
    $shortcut.WorkingDirectory = $projectDir
    $shortcut.Description = "Assistance - local voice assistant"
    $shortcut.Save()
    Write-Host $shortcutPath
}

if (-not $SkipStartup) {
    Write-Step "Adding to Windows startup"
    & (Join-Path $projectDir "install_startup.ps1")
}

Write-Step "Installation complete"
Write-Host "Run Assistance with:"
Write-Host "  .\run_quest_assistant.ps1"
Write-Host ""
Write-Host "Or double-click the desktop shortcut (Assistance)."
