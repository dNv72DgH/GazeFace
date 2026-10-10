# Install a GazeFace build into C:\Program Files\GazeFace with the
# UIAccess variant signed.
#
# Usage (elevated):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_uiaccess.ps1 -Dist dist126
#
# GazeFace is started once the install succeeds; -NoLaunch skips that.
#
# Why elevated: writing to Program Files, and the test-signing key is only
# usable from an elevated token on this machine (signtool reports
# "After Private Key filter, 0 certs" otherwise).
#
# Why UIAccess needs all this: GazeFace_UI.exe carries uiAccess="true" in
# its manifest, and Windows only honours that for an Authenticode-signed
# exe (chaining to a trusted root) installed under Program Files. Without
# it, input to elevated / sandboxed windows is dropped (UIPI).
#
# ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 as
# the ANSI codepage, so any non-ASCII byte breaks the parser.
#
# Safe by construction:
#   * anything running FROM the install folder is only stopped after a
#     y/N prompt; otherwise the script refuses to continue
#   * aborts before copying if the signature does not verify as Valid
#   * robocopy /E (never /MIR): files that exist only in the install
#     folder are left alone

param(
    [Parameter(Mandatory = $true)][string]$Dist,
    # Thumbprint of YOUR code-signing certificate (CurrentUser\My). The
    # UIAccess exe must be signed by a certificate this machine trusts.
    [string]$Thumbprint = $env:GAZEFACE_SIGN_THUMBPRINT,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'

$proj  = Split-Path -Parent $PSScriptRoot
$src   = Join-Path $proj (Join-Path $Dist 'GazeFace')
$dst   = 'C:\Program Files\GazeFace'
$thumb = $Thumbprint
$exes  = @('GazeFace.exe', 'GazeFace_UI.exe', 'GazeFace_Sub.exe')

function Done($code) {
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
    exit $code
}

try {
    if (-not $thumb) {
        throw "No signing certificate: pass -Thumbprint or set GAZEFACE_SIGN_THUMBPRINT"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $src 'GazeFace_UI.exe'))) {
        throw "Build not found: $src"
    }

    # 1. nothing may be running from the destination
    $running = Get-Process | Where-Object {
        $_.Path -and $_.Path.StartsWith($dst + '\', [StringComparison]::OrdinalIgnoreCase)
    }
    if ($running) {
        Write-Host "Running from $dst :"
        $running | Select-Object Id, ProcessName, Path | Format-Table -AutoSize
        $ans = Read-Host "Stop them now and continue? (y/N)"
        if ($ans -notmatch '^(y|yes)$') {
            throw "Close GazeFace first."
        }
        # The crash sentinel first: it restarts the app the moment the
        # main process disappears, so stopping the app first just brings
        # it straight back.
        $ordered = @($running | Where-Object { $_.ProcessName -like '*_sub' }) +
                   @($running | Where-Object { $_.ProcessName -notlike '*_sub' })
        foreach ($p in $ordered) {
            Write-Host "Stopping $($p.ProcessName) ($($p.Id))"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
        # Wait for them to be gone (and for anything respawned to show up).
        $deadline = (Get-Date).AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 500
            $left = Get-Process | Where-Object {
                $_.Path -and $_.Path.StartsWith($dst + '\', [StringComparison]::OrdinalIgnoreCase)
            }
        } while ($left -and (Get-Date) -lt $deadline)
        if ($left) {
            $left | Select-Object Id, ProcessName, Path | Format-Table -AutoSize
            throw "Still running after 10 s - close GazeFace manually."
        }
    }

    # 2. sign the UIAccess variant IN THE BUILD FOLDER, and verify
    $signtool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "signtool.exe not found (Windows SDK)" }

    $ui = Join-Path $src 'GazeFace_UI.exe'
    Write-Host "Signing $ui"
    & $signtool.FullName sign /fd SHA256 /sha1 $thumb /s My $ui
    if ($LASTEXITCODE -ne 0) { throw "signtool failed ($LASTEXITCODE)" }
    $sig = Get-AuthenticodeSignature -LiteralPath $ui
    if ($sig.Status -ne 'Valid') {
        throw "Signature is '$($sig.Status)', not Valid - not installing."
    }
    Write-Host "Signature: Valid ($($sig.SignerCertificate.Subject))"

    # 3. case-correct existing exe names. robocopy overwrites content but
    #    keeps the destination's old spelling (gazeface.exe stays lower
    #    case), so rename through a temporary name first.
    if (Test-Path -LiteralPath $dst) {
        foreach ($name in $exes) {
            $hit = Get-ChildItem -LiteralPath $dst -File |
                Where-Object { $_.Name -ieq $name -and $_.Name -cne $name }
            foreach ($f in $hit) {
                $tmp = $f.FullName + '.renaming'
                Move-Item -LiteralPath $f.FullName -Destination $tmp
                Move-Item -LiteralPath $tmp -Destination (Join-Path $dst $name)
                Write-Host "Renamed $($f.Name) -> $name"
            }
        }
    }

    # 4. copy
    Write-Host "Copying $src -> $dst"
    robocopy $src $dst /E /R:1 /W:1 /NFL /NDL /NJH /NP | Out-Host
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

    # 5. verify what actually landed
    foreach ($name in $exes) {
        $f = Get-ChildItem -LiteralPath $dst -File | Where-Object { $_.Name -ieq $name }
        if (-not $f) { throw "Missing after copy: $name" }
        if ($f.Name -cne $name) { throw "Name case not applied: $($f.Name)" }
    }
    $isig = Get-AuthenticodeSignature -LiteralPath (Join-Path $dst 'GazeFace_UI.exe')
    if ($isig.Status -ne 'Valid') { throw "Installed GazeFace_UI.exe signature: $($isig.Status)" }

    # 6. register GazeFace as an assistive technology, so Windows can
    #    start it at sign-in / after sign-in (Advanced settings >
    #    Startup; see src/at_startup.py). Registration alone starts
    #    nothing: that is decided by the Configuration lists the app
    #    edits. Rewritten every install so it always points here.
    $at = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Accessibility\ATs\gazeface'
    if (-not (Test-Path $at)) { New-Item -Path $at -Force | Out-Null }
    $atVals = [ordered]@{
        ApplicationName = 'GazeFace'
        Description     = 'Face gesture and gaze mouse control'
        ATExe           = 'GazeFace_UI.exe'
        StartExe        = (Join-Path $dst 'GazeFace_UI.exe')
        StartParams     = '--at'
        Profile         = '<HCIModel><Accommodation type="severe dexterity" /><Accommodation type="mild dexterity" /></HCIModel>'
        SimpleProfile   = 'gazeface'
    }
    foreach ($n in $atVals.Keys) {
        New-ItemProperty -Path $at -Name $n -Value $atVals[$n] -PropertyType String -Force | Out-Null
    }
    foreach ($n in 'TerminateOnDesktopSwitch', 'CopySettingsToLockedDesktop') {
        New-ItemProperty -Path $at -Name $n -Value 0 -PropertyType DWord -Force | Out-Null
    }
    Write-Host "Registered assistive technology 'gazeface'"

    Write-Host ""
    Write-Host "Installed $Dist to $dst"
    Get-ChildItem -LiteralPath $dst -Filter *.exe | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

    # 7. start it - NOT elevated. This script runs as administrator, and
    #    anything it starts directly inherits that token (the app would
    #    then run elevated, with its own UIPI behaviour). explorer.exe
    #    hands the launch to the user's desktop shell, which starts it at
    #    normal integrity. The standard exe is started either way; it
    #    switches itself to GazeFace_UI.exe when the setting asks for it.
    if (-not $NoLaunch) {
        $main = Join-Path $dst 'GazeFace.exe'
        Write-Host "Starting $main"
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $main + '"')
    }
    Done 0
}
catch {
    Write-Host ""
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Done 1
}
