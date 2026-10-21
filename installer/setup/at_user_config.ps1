# Add (-On 1) or remove (-On 0) GazeFace in THIS user's list of assistive
# technologies Windows starts after sign-in (HKCU ...\Accessibility,
# Configuration), keeping every other entry in its order. Same edit as
# src\at_startup.py _write_user_config().
# Run by the installer as the original, non-elevated user, so HKCU is the
# signed-in user's and not the elevated account's.
# ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.

param(
    [int]$On = 1
)

$ErrorActionPreference = 'Stop'

$k = 'HKCU:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Accessibility'
$cur = (Get-ItemProperty -Path $k -Name Configuration -ErrorAction SilentlyContinue).Configuration
if (-not $cur -and -not $On) { exit 0 }

$items = @()
if ($cur) { $items = @($cur.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
$items = @($items | Where-Object { $_ -ine 'gazeface' })
if ($On) { $items += 'gazeface' }

if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
New-ItemProperty -Path $k -Name Configuration -Value ($items -join ',') -PropertyType String -Force | Out-Null
exit 0
