[CmdletBinding()]
param(
    [string]$SourceRoot
)

$ErrorActionPreference = 'Stop'

# Creates only the current user's desktop shortcut. It does not install
# dependencies, launch CapsWriter, register startup, or change system settings.
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $sourceRoot = Split-Path -Parent $PSScriptRoot
} else {
    $sourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
}
$runScript = Join-Path $sourceRoot 'run.bat'
if (-not (Test-Path -LiteralPath $runScript -PathType Leaf)) {
    throw "Source startup script was not found: $runScript"
}

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'CapsWriter 离线版（源码）.lnk'

$iconPath = Join-Path $sourceRoot 'assets\manager-tray-blue.ico'
if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "Project icon was not found: $iconPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $env:ComSpec
$shortcut.Arguments = "/c `"$runScript`""
$shortcut.WorkingDirectory = $sourceRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Start CapsWriter Offline source (local voice input manager)'
$shortcut.Save()

Write-Host "Desktop shortcut created or updated: $shortcutPath"
Write-Host 'The shortcut starts the source version only; run.bat still prepares dependencies and models on first launch.'
