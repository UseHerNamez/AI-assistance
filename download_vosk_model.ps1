$ErrorActionPreference = "Stop"

$modelName = "vosk-model-small-en-us-0.15"
$modelRoot = Join-Path $HOME ".quest_assistant\models"
$modelPath = Join-Path $modelRoot $modelName
$zipPath = Join-Path $modelRoot "$modelName.zip"
$url = "https://alphacephei.com/vosk/models/$modelName.zip"
# Official zip SHA256 (alphacep / Hugging Face LFS mirror).
$expectedSha256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"

function Test-FileSha256([string]$Path, [string]$Expected) {
    $hash = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $Expected.ToLowerInvariant()) {
        throw "Checksum mismatch for $Path`nExpected: $Expected`nActual:   $hash"
    }
}

if (Test-Path $modelPath) {
  Write-Host "Model already exists:"
  Write-Host $modelPath
  exit 0
}

New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

Write-Host "Downloading Vosk model..."
Invoke-WebRequest -Uri $url -OutFile $zipPath

Write-Host "Verifying download checksum..."
Test-FileSha256 -Path $zipPath -Expected $expectedSha256

Write-Host "Extracting model..."
Expand-Archive -Path $zipPath -DestinationPath $modelRoot -Force
Remove-Item $zipPath

Write-Host "Model ready:"
Write-Host $modelPath
