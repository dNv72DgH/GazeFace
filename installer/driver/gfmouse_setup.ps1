# Install or remove the GazeFace virtual mouse (Root\GazeFaceMouse) on an
# end-user PC. Needs no WDK: the device node is created through SetupAPI,
# the same calls "devcon install" makes.
#
#   Install: called by custom\full_edition.ps1 AFTER it has signed
#            gfmouse.cat with the per-PC certificate and put that
#            certificate in LocalMachine\Root and TrustedPublisher, so
#            Windows installs the package without a prompt.
#   Remove:  -Remove, from the uninstaller ([UninstallRun] in GazeFace.iss).
#            Best effort; never fails the uninstall.
#
# Install errors are thrown, not returned as exit codes, so a caller that
# runs this with & catches them in its own try/catch.
# ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.

param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$hwid = 'Root\GazeFaceMouse'
$inf  = Join-Path $PSScriptRoot 'gfmouse.inf'
$cat  = Join-Path $PSScriptRoot 'gfmouse.cat'

# Callers wrap this in @(): PowerShell unrolls a one-element array into the
# bare CimInstance, which has no .Count.
function Get-OurDevices {
    @(Get-CimInstance Win32_PnPEntity | Where-Object {
        $_.HardwareID -and ($_.HardwareID -contains $hwid)
    })
}

if ($Remove) {
    $ErrorActionPreference = 'Continue'
    # Device nodes first, then every gfmouse.inf package in the driver store
    # (Get-WindowsDriver instead of parsing pnputil output, which is localized).
    foreach ($d in Get-OurDevices) {
        Write-Host "Removing device $($d.PNPDeviceID)"
        pnputil /remove-device $d.PNPDeviceID | Out-Host
    }
    try {
        $packages = @(Get-WindowsDriver -Online | Where-Object {
            (Split-Path -Leaf $_.OriginalFileName) -eq 'gfmouse.inf'
        })
    } catch {
        Write-Host "Driver store query failed: $($_.Exception.Message)"
        $packages = @()
    }
    foreach ($p in $packages) {
        Write-Host "Deleting driver package $($p.Driver)"
        pnputil /delete-driver $p.Driver /uninstall /force | Out-Host
    }
    exit 0
}

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class GfMouseSetup
{
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVINFO_DATA
    {
        public int cbSize;
        public Guid ClassGuid;
        public int DevInst;
        public IntPtr Reserved;
    }

    const int DICD_GENERATE_ID = 0x1;
    const int SPDRP_HARDWAREID = 0x1;
    const int DIF_REMOVE = 0x5;
    const int DIF_REGISTERDEVICE = 0x19;
    const int INSTALLFLAG_FORCE = 0x1;
    // Fail instead of showing a trust prompt nobody may be able to answer.
    const int INSTALLFLAG_NONINTERACTIVE = 0x4;
    static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiGetINFClassW(string infName, out Guid classGuid,
        StringBuilder className, int classNameSize, out int requiredSize);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid classGuid, IntPtr hwndParent);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiCreateDeviceInfoW(IntPtr set, string deviceName,
        ref Guid classGuid, string description, IntPtr hwndParent, int flags,
        ref SP_DEVINFO_DATA data);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr set,
        ref SP_DEVINFO_DATA data, int property, byte[] buffer, int size);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiCallClassInstaller(int function, IntPtr set,
        ref SP_DEVINFO_DATA data);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);

    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr hwndParent,
        string hardwareId, string fullInfPath, int flags, out bool rebootRequired);

    static Win32Exception Fail(string what)
    {
        int e = Marshal.GetLastWin32Error();
        return new Win32Exception(e, string.Format("{0} failed: {1} (0x{2:X8})",
            what, new Win32Exception(e).Message, e));
    }

    // When create is true, registers a new root-enumerated device node with
    // hardwareId first ("devcon install"); then installs inf on every device
    // with that ID ("devcon update"). A new node is removed again if the
    // driver install fails. Returns true when Windows needs a reboot.
    public static bool Install(string inf, string hardwareId, bool create)
    {
        IntPtr set = INVALID_HANDLE_VALUE;
        SP_DEVINFO_DATA dev = new SP_DEVINFO_DATA();
        bool orphan = false;
        try
        {
            if (create)
            {
                Guid cls;
                int required;
                StringBuilder name = new StringBuilder(64);
                if (!SetupDiGetINFClassW(inf, out cls, name, name.Capacity, out required))
                    throw Fail("SetupDiGetINFClass");
                set = SetupDiCreateDeviceInfoList(ref cls, IntPtr.Zero);
                if (set == INVALID_HANDLE_VALUE)
                    throw Fail("SetupDiCreateDeviceInfoList");
                dev.cbSize = Marshal.SizeOf(typeof(SP_DEVINFO_DATA));
                if (!SetupDiCreateDeviceInfoW(set, name.ToString(), ref cls, null,
                                              IntPtr.Zero, DICD_GENERATE_ID, ref dev))
                    throw Fail("SetupDiCreateDeviceInfo");
                byte[] ids = Encoding.Unicode.GetBytes(hardwareId + "\0\0");  // REG_MULTI_SZ
                if (!SetupDiSetDeviceRegistryPropertyW(set, ref dev, SPDRP_HARDWAREID,
                                                       ids, ids.Length))
                    throw Fail("SetupDiSetDeviceRegistryProperty");
                if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, set, ref dev))
                    throw Fail("DIF_REGISTERDEVICE");
                orphan = true;
            }
            bool reboot;
            if (!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero, hardwareId, inf,
                    INSTALLFLAG_FORCE | INSTALLFLAG_NONINTERACTIVE, out reboot))
                throw Fail("UpdateDriverForPlugAndPlayDevices");
            orphan = false;
            return reboot;
        }
        finally
        {
            if (orphan)
                SetupDiCallClassInstaller(DIF_REMOVE, set, ref dev);
            if (set != INVALID_HANDLE_VALUE)
                SetupDiDestroyDeviceInfoList(set);
        }
    }
}
'@

if (-not (Test-Path -LiteralPath $inf)) { throw "gfmouse.inf not found: $inf" }
$sig = Get-AuthenticodeSignature -LiteralPath $cat
if ($sig.Status -ne 'Valid') { throw "gfmouse.cat signature: $($sig.Status)" }

$create = @(Get-OurDevices).Count -eq 0
if ($create) { Write-Host "Creating $hwid" } else { Write-Host "Updating $hwid" }
$reboot = [GfMouseSetup]::Install($inf, $hwid, $create)
if ($reboot) {
    Write-Host 'Virtual mouse driver installed. Restart Windows to start using it.'
    return
}

# The device should come up within a few seconds.
for ($i = 0; $i -lt 10; $i++) {
    $devs = @(Get-OurDevices)
    if ($devs.Count -gt 0 -and -not ($devs | Where-Object ConfigManagerErrorCode -ne 0)) {
        Write-Host 'Virtual mouse driver installed.'
        return
    }
    Start-Sleep -Seconds 1
}
$codes = ($devs | ForEach-Object { $_.ConfigManagerErrorCode }) -join ', '
throw "Virtual mouse device is not working (problem code: $codes)"
