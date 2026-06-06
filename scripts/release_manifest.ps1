# Shared file list for online/offline install packages.
# Dot-source from build_*.ps1 scripts in the repo root.

$script:ReleaseVersion = "0.2.0"

function Get-ReleaseCoreItems {
    return @(
        "quest_assistant",
        "scripts",
        "requirements.txt",
        "requirements-whisper.txt",
        "download_vosk_model.ps1",
        "install_local_llm.ps1",
        "install_startup.ps1",
        "run_quest_assistant.ps1",
        "launch_assistance.vbs",
        "Install-Assistance.vbs"
    )
}

function Get-ReleaseOnlineItems {
    return (Get-ReleaseCoreItems) + @(
        "README.md",
        "INSTALL.md"
    )
}

function Get-ReleaseOfflineItems {
    return (Get-ReleaseCoreItems) + @(
        "README.md",
        "OFFLINE.md"
    )
}

function Get-ReleaseDeveloperZipItems {
    return (Get-ReleaseCoreItems) + @(
        "install.ps1",
        "README.md",
        "INSTALL.md",
        "OFFLINE.md"
    )
}

function Copy-ReleaseStage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectDir,
        [Parameter(Mandatory = $true)]
        [string]$StageDir,
        [Parameter(Mandatory = $true)]
        [string[]]$Items,
        [switch]$WriteBuildInfo
    )

    if (Test-Path $StageDir) {
        Remove-Item $StageDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

    foreach ($item in $Items) {
        $source = Join-Path $ProjectDir $item
        if (-not (Test-Path $source)) {
            throw "Missing release file: $item"
        }
        Copy-Item -Path $source -Destination (Join-Path $StageDir $item) -Recurse -Force
    }

    Get-ChildItem -Path $StageDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    Get-ChildItem -Path $StageDir -Recurse -Include "*.pyc" -File | Remove-Item -Force -ErrorAction SilentlyContinue

    if ($WriteBuildInfo) {
        $info = @(
            "Assistance release $script:ReleaseVersion",
            "Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
            "Source: $ProjectDir"
        ) -join "`n"
        Set-Content -Path (Join-Path $StageDir "BUILD_INFO.txt") -Value $info -Encoding UTF8
    }
}
