# Builds the GazeFace standard-edition installer (INSTALLER_SPEC.md, 2-A).
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Dist dist182
# No elevation needed. ASCII only: Windows PowerShell 5.1 reads a BOM-less
# .ps1 as ANSI.
param(
    [Parameter(Mandatory = $true)][string]$Dist
)
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { throw "ISCC.exe not found: $iscc" }

$distDir = Join-Path $root $Dist
foreach ($exe in 'GazeFace.exe', 'GazeFace_UI.exe', 'GazeFace_Sub.exe') {
    $p = Join-Path $distDir "GazeFace\$exe"
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$cm = Get-Content (Join-Path $root 'src\config_manager.py') -Raw
if ($cm -notmatch 'VERSION\s*=\s*"([^"]+)"') { throw 'VERSION not found in src\config_manager.py' }
$version = $Matches[1]

Write-Host "GazeFace $version from $distDir (standard edition)"
& $iscc "/DAppVersion=$version" "/DDistDir=$distDir" (Join-Path $here 'GazeFace.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }
Write-Host ("Output: " + (Join-Path $here "Output\GazeFace-Setup-$version.exe"))
