# Creates or refreshes the Assistance desktop shortcut.
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$launcher = Join-Path $projectDir "launch_assistance.vbs"
if (-not (Test-Path $launcher)) {
    throw "Missing launcher: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Assistance.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$shortcut.Arguments = "`"$launcher`""
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description = "Assistance - local voice assistant"
$shortcut.Save()

Write-Host "Desktop shortcut created:"
Write-Host $shortcutPath
