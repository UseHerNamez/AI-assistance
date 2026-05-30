$ErrorActionPreference = "Stop"

$modelName = "vosk-model-small-en-us-0.15"
$modelRoot = Join-Path $HOME ".quest_assistant\models"
$modelPath = Join-Path $modelRoot $modelName
$zipPath = Join-Path $modelRoot "$modelName.zip"
$url = "https://alphacephei.com/vosk/models/$modelName.zip"

if (Test-Path $modelPath) {
  Write-Host "Model already exists:"
  Write-Host $modelPath
  exit 0
}

New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

Write-Host "Downloading Vosk model..."
Invoke-WebRequest -Uri $url -OutFile $zipPath

Write-Host "Extracting model..."
Expand-Archive -Path $zipPath -DestinationPath $modelRoot -Force
Remove-Item $zipPath

Write-Host "Model ready:"
Write-Host $modelPath

