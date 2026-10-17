# GazeFace updater - the elevated half.
#
# Run by the scheduled task \GazeFace\Update as SYSTEM (registered by the
# installer, see register_task.ps1). Any signed-in user may START the task
# but not change it, so this script takes nothing from the user: it asks
# GitHub for the latest release itself, downloads the setup program over
# HTTPS, checks it against the SHA-256 digest GitHub publishes for it, and
# only then runs it silently.
#
# Why a task at all: the standard edition of GazeFace cannot operate the
# User Account Control prompt, and many of its users have no other input.
#
# Progress goes to result.json next to this script, which GazeFace shows:
#   status = running | downloading (done / total bytes) | installing
#            | ok | uptodate | failed (detail)
#
# Everything it writes stays inside this folder (Program Files: only
# administrators can write here), never in a user-writable place a
# standard user could plant a link in.
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.

param(
    # Only for an administrator testing by hand; the task passes nothing.
    [string]$Api = 'https://api.github.com/repos/dNv72DgH/GazeFace/releases/latest'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$here = $PSScriptRoot
$result = Join-Path $here 'result.json'
$logf = Join-Path $here 'update.log'
$setup = Join-Path $here 'download\update_setup.exe'   # not 'GazeFace*': the
                                                      # installer stops those
$uninstKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{CADE7311-DD86-4EA4-AD72-4C7833CBBC9E}_is1'
$assetPrefix = 'https://github.com/dNv72DgH/GazeFace/releases/download/'

function Log([string]$m) {
    Add-Content -LiteralPath $logf -Encoding UTF8 -Value ((Get-Date -Format 's') + ' ' + $m)
}

# Written to a temporary file and renamed, so GazeFace never reads half.
function Result([string]$status, [string]$version, [string]$detail,
                [long]$done = 0, [long]$total = 0) {
    $o = [ordered]@{
        status  = $status
        version = $version
        detail  = $detail
        done    = $done
        total   = $total
        time    = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $tmp = $result + '.tmp'
    [IO.File]::WriteAllText($tmp, ($o | ConvertTo-Json -Compress),
                            (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $tmp -Destination $result -Force
}

function VersionOf([string]$s) {
    $s = $s.Trim().TrimStart('v', 'V')
    if ($s -notmatch '^\d+(\.\d+){0,3}$') { throw "bad version: $s" }
    if ($s -notmatch '\.') { $s += '.0' }
    return [version]$s
}

function Download([string]$url, [string]$path, [long]$size, [string]$ver) {
    $req = [Net.HttpWebRequest]::Create($url)    # follows the redirect
    $req.UserAgent = 'GazeFace-Updater'
    $req.Timeout = 60000
    $req.ReadWriteTimeout = 60000
    $resp = $req.GetResponse()
    $done = [long]0
    try {
        $total = $resp.ContentLength
        if ($total -le 0) { $total = $size }
        $in = $resp.GetResponseStream()
        $out = [IO.File]::Create($path)
        try {
            $buf = New-Object byte[] 262144
            $last = [DateTime]::MinValue
            while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                $out.Write($buf, 0, $n)
                $done += $n
                if (((Get-Date) - $last).TotalMilliseconds -ge 500) {
                    Result 'downloading' $ver '' $done $total
                    $last = Get-Date
                }
            }
            Result 'downloading' $ver '' $done $total
        }
        finally { $out.Close(); $in.Close() }
    }
    finally { $resp.Close() }
    if ($done -ne $size) { throw "download incomplete ($done of $size bytes)" }
}

$target = ''
try {
    Result 'running' '' ''
    $installed = (Get-ItemProperty -LiteralPath $uninstKey).DisplayVersion
    $rel = Invoke-RestMethod -Uri $Api -UseBasicParsing -TimeoutSec 30 -Headers @{
        'User-Agent' = 'GazeFace-Updater'
        'Accept'     = 'application/vnd.github+json'
    }
    $target = ([string]$rel.tag_name).TrimStart('v', 'V')
    if ((VersionOf $target) -le (VersionOf $installed)) {
        Log "up to date ($installed, latest $target)"
        Result 'uptodate' $target ''
        exit 0
    }

    $asset = @($rel.assets | Where-Object {
            $_.name -like 'GazeFace-Setup-*.exe' -and
            ([string]$_.digest).StartsWith('sha256:') -and
            ([string]$_.browser_download_url).StartsWith($assetPrefix)
        }) | Select-Object -First 1
    if ($null -eq $asset) { throw "no usable setup program in $target" }
    $want = ([string]$asset.digest).Substring(7).ToUpperInvariant()

    New-Item -ItemType Directory -Force -Path (Split-Path $setup) | Out-Null
    if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -Force }
    Log "downloading $($asset.browser_download_url)"
    Download $asset.browser_download_url $setup ([long]$asset.size) $target
    $got = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
    if ($got -ne $want) {
        Remove-Item -LiteralPath $setup -Force
        throw "SHA-256 mismatch (got $got, want $want)"
    }

    Log "installing $target over $installed"
    Result 'installing' $target ''
    $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        ('/LOG="' + (Join-Path $here 'setup.log') + '"'))
    Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { throw "setup exit code $($p.ExitCode)" }
    Log "installed $target"
    Result 'ok' $target ''
}
catch {
    $msg = $_.Exception.Message
    try { Log "failed: $msg" } catch {}
    Result 'failed' $target $msg
    exit 1
}
