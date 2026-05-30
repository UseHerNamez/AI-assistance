# Restarts the local dev copy of Assistance after code changes.
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $projectDir

function Stop-AssistanceProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*quest_assistant*' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    $lockPath = Join-Path $env:USERPROFILE ".quest_assistant\assistance.lock"
    if (Test-Path $lockPath) {
        $lines = Get-Content $lockPath -ErrorAction SilentlyContinue
        if ($lines -and $lines[0] -match '^\d+$') {
            Stop-Process -Id ([int]$lines[0]) -Force -ErrorAction SilentlyContinue
        }
    }
}

Stop-AssistanceProcesses
Start-Sleep -Seconds 1
Stop-AssistanceProcesses
Start-Sleep -Seconds 1

$pythonw = Join-Path $projectDir "runtime\python\pythonw.exe"
$venvPythonw = Join-Path $projectDir ".venv\Scripts\pythonw.exe"
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
$pythoncore = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Python\pythoncore-*\pythonw.exe") -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (Test-Path $pythonw) {
    $exe = $pythonw
} elseif (Test-Path $venvPythonw) {
    $exe = $venvPythonw
} elseif ($pythoncore) {
    $exe = $pythoncore.FullName
} elseif (Test-Path $venvPython) {
    $exe = $venvPython
} else {
    $exe = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $exe) {
        $exe = (Get-Command python).Source
    }
}

$env:JARVIS_TTS_BACKEND = "edge"
$env:JARVIS_EDGE_VOICE = "en-GB-RyanNeural"

Start-Process -FilePath $exe -ArgumentList "-m", "quest_assistant" -WorkingDirectory $projectDir -WindowStyle Hidden
Write-Host "Assistance restarted from $projectDir using $exe (edge voice)"
