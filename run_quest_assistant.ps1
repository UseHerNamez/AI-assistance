$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$runtimePython = Join-Path $projectDir "runtime\python\python.exe"
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (Test-Path $runtimePython) {
  $python = $runtimePython
} elseif (Test-Path $venvPython) {
  $python = $venvPython
} else {
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $python) {
    throw "Python was not found. Run Assistance-Setup.exe or .\install.ps1 first."
  }
}

& $python -m quest_assistant

