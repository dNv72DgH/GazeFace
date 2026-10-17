# Registers (or, with -Remove, deletes) the scheduled task \GazeFace\Update
# that runs gazeface_update.ps1 as SYSTEM. Called by the installer, which
# already runs elevated.
#
# The task has no trigger: GazeFace starts it when the user accepts an
# update. Its security descriptor lets any signed-in user READ and RUN it
# (so no UAC prompt is needed, which the standard edition could not
# operate) but only SYSTEM and administrators change or delete it.
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.

param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$folderName = 'GazeFace'
$taskName = 'Update'

$svc = New-Object -ComObject 'Schedule.Service'
$svc.Connect()
$root = $svc.GetFolder('\')

if ($Remove) {
    try { $f = $svc.GetFolder('\' + $folderName) } catch { exit 0 }
    try { $f.DeleteTask($taskName, 0) } catch {}
    try { $root.DeleteFolder($folderName, 0) } catch {}
    exit 0
}

try { $folder = $svc.GetFolder('\' + $folderName) }
catch { $folder = $root.CreateFolder($folderName) }

$script = Join-Path $PSScriptRoot 'gazeface_update.ps1'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$def = $svc.NewTask(0)
$def.RegistrationInfo.Description = 'Installs a GazeFace update when the user accepts one in GazeFace.'
$def.Principal.UserId = 'S-1-5-18'          # SYSTEM
$def.Principal.LogonType = 5                # TASK_LOGON_SERVICE_ACCOUNT
$def.Principal.RunLevel = 1                 # highest
$def.Settings.Enabled = $true
$def.Settings.AllowDemandStart = $true
$def.Settings.MultipleInstances = 2         # TASK_INSTANCES_IGNORE_NEW
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Settings.ExecutionTimeLimit = 'PT1H'
$def.Settings.Hidden = $false
$act = $def.Actions.Create(0)               # TASK_ACTION_EXEC
$act.Path = $ps
$act.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '"'

# SYSTEM, Administrators: full. Authenticated users: read + run.
$sddl = 'D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;AU)'
# 6 = TASK_CREATE_OR_UPDATE
$folder.RegisterTaskDefinition($taskName, $def, 6, 'S-1-5-18', $null, 5, $sddl) | Out-Null
