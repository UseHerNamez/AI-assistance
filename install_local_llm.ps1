$ErrorActionPreference = "Stop"

$model = if ($env:JARVIS_LLM_MODEL) { $env:JARVIS_LLM_MODEL } else { "qwen2.5:3b" }

function Get-OllamaPath {
  $cmd = Get-Command ollama -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $candidate) {
    return $candidate
  }

  return $null
}

$ollama = Get-OllamaPath
if (-not $ollama) {
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $winget) {
    throw "Ollama is not installed and winget was not found. Install Ollama from https://ollama.com/download/windows, then run this script again."
  }

  Write-Host "Installing Ollama..."
  winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
  $ollama = Get-OllamaPath
}

if (-not $ollama) {
  throw "Ollama was not found after installation. Restart PowerShell or install Ollama manually from https://ollama.com/download/windows."
}

Write-Host "Starting Ollama..."
Start-Process -FilePath $ollama -WindowStyle Hidden -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Write-Host "Pulling local model: $model"
& $ollama pull $model

Write-Host "Local LLM is ready:"
Write-Host $model
& $ollama list

