# Builds the GazeFace installer (full / standard edition chosen at install).
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Dist dist182 [-Driver <driver\gfmouse>]
# -Driver: the virtual mouse driver source folder, after its build.ps1
# (default: driver\gfmouse in this tree). Its UNSIGNED build output is
# packaged with a catalog made here (WDK Inf2Cat); the catalog is signed
# on the user's PC by custom\full_edition.ps1, so it must cover the
# unsigned dll.
# No elevation needed. ASCII only: Windows PowerShell 5.1 reads a BOM-less
# .ps1 as ANSI.
param(
    [Parameter(Mandatory = $true)][string]$Dist,
    [string]$Driver = ''
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

# ---- virtual mouse driver package: gfmouse.inf + dll + unsigned catalog ----
if (-not $Driver) { $Driver = Join-Path $root 'driver\gfmouse' }
$dll = Join-Path $Driver 'build\x64\gfmouse.dll'
$inf = Join-Path $Driver 'gfmouse.inf'
foreach ($p in $dll, $inf) {
    if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p (run driver\gfmouse\build.ps1, or pass -Driver)" }
}
if ((Get-AuthenticodeSignature -LiteralPath $dll).Status -ne 'NotSigned') {
    throw "$dll is signed; the package needs the unsigned build output"
}
$inf2cat = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Recurse -Filter Inf2Cat.exe -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $inf2cat) { throw 'Inf2Cat.exe not found (WDK)' }

$pkg = Join-Path $here 'Output\driver'
if (Test-Path -LiteralPath $pkg) { Remove-Item -LiteralPath $pkg -Recurse -Force }
New-Item -ItemType Directory -Path $pkg | Out-Null
Copy-Item -LiteralPath $dll, $inf -Destination $pkg
& $inf2cat.FullName "/driver:$pkg" '/os:10_X64' '/uselocaltime' | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Inf2Cat failed ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath (Join-Path $pkg 'gfmouse.cat'))) { throw 'Inf2Cat made no gfmouse.cat' }

Write-Host "GazeFace $version from $distDir, driver from $Driver"
& $iscc "/DAppVersion=$version" "/DDistDir=$distDir" "/DDriverPkg=$pkg" (Join-Path $here 'GazeFace.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }
Write-Host ("Output: " + (Join-Path $here "Output\GazeFace-Setup-$version.exe"))
