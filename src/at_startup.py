# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Start GazeFace with Windows, the way Windows starts its own
assistive technology (Narrator, On-Screen Keyboard).

A plain autostart entry cannot do what this app needs: it runs after the
desktop appears, and never on the secure desktop, so a user who has only
their face to work with is stuck at the sign-in screen and at every UAC
prompt. Windows has a mechanism for exactly this - registered assistive
technologies ("ATs") that it starts itself:

  HKLM\\...\\Accessibility\\ATs\\gazeface   the registration (admin)
  HKLM\\...\\Accessibility  Configuration  started at sign-in: the sign-in
                                            and lock screen (admin)
  HKCU\\...\\Accessibility  Configuration  started after sign-in: the
                                            desktop, and UAC prompts

Configuration is a comma-separated list of AT names shared with other
tools (this machine lists osk and clickassist), so it is always edited
as a list - our name added or removed, everything else left as it was.

Windows starts only a UIAccess exe on the secure desktop, so the
registration points at GazeFace_UI.exe, which has to be signed and under
Program Files (src/ui_access.py). The copy it starts gets --at on its
command line.

HKLM writes need elevation, which this app does not have: they run in a
short elevated PowerShell (one UAC prompt). The installer registers the
AT already, so "after sign-in" alone never needs one.
"""

import base64
import logging
import os
import shutil
import threading
import winreg

from src.i18n import t

logger = logging.getLogger("ATStartup")

AT_NAME = "gazeface"
AT_FLAG = "--at"
ACC_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Accessibility"
ATS_KEY = ACC_KEY + r"\ATs" + "\\" + AT_NAME

PROFILE = ('<HCIModel><Accommodation type="severe dexterity" />'
           '<Accommodation type="mild dexterity" /></HCIModel>')


# ---------------------------------------------------------------- #
# The Configuration list
# ---------------------------------------------------------------- #

def _items(value):
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def with_item(value, on: bool, name: str = AT_NAME) -> str:
    """`value` with `name` present (on) or absent, order otherwise kept."""
    items = [p for p in _items(value) if p.lower() != name.lower()]
    if on:
        items.append(name)
    return ",".join(items)


def has_item(value, name: str = AT_NAME) -> bool:
    return any(p.lower() == name.lower() for p in _items(value))


def _read(root, path, name):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def before_signin() -> bool:
    """Started on the sign-in / lock screen?"""
    return has_item(_read(winreg.HKEY_LOCAL_MACHINE, ACC_KEY,
                          "Configuration"))


def after_signin() -> bool:
    """Started after sign-in (desktop, UAC prompts)?"""
    return has_item(_read(winreg.HKEY_CURRENT_USER, ACC_KEY,
                          "Configuration"))


def any_enabled() -> bool:
    try:
        return before_signin() or after_signin()
    except Exception:
        return False


def _write_user_config(on: bool, root=winreg.HKEY_CURRENT_USER,
                       path=ACC_KEY) -> None:
    cur = _read(root, path, "Configuration")
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Configuration", 0, winreg.REG_SZ,
                          with_item(cur, on))


# ---------------------------------------------------------------- #
# The registration
# ---------------------------------------------------------------- #

def start_exe() -> str:
    from src import ui_access
    return ui_access.variant_path(True)


def blocker() -> str:
    """Empty if this install can be started by Windows, else why not."""
    from src import ui_access
    return ui_access.blocker()


def registered() -> bool:
    """Is the AT registered, and does it point at THIS install?"""
    exe = _read(winreg.HKEY_LOCAL_MACHINE, ATS_KEY, "StartExe")
    params = _read(winreg.HKEY_LOCAL_MACHINE, ATS_KEY, "StartParams")
    if not exe:
        return False
    return (os.path.normcase(os.path.normpath(str(exe)))
            == os.path.normcase(os.path.normpath(start_exe()))
            and str(params or "").strip() == AT_FLAG)


def _ps_str(s) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(s).replace("'", "''") + "'"


def admin_script(register: bool, machine_config=None) -> str:
    """The PowerShell run elevated: optionally (re)register the AT, and
    optionally add / remove it from the machine-wide Configuration.

    The list edit happens INSIDE the elevated script, on the value as it
    is at that moment, so another tool's entry written in between is not
    lost.
    """
    acc = "HKLM:\\" + ACC_KEY
    lines = ["$ErrorActionPreference = 'Stop'",
             "$acc = %s" % _ps_str(acc)]
    if register:
        exe = start_exe()
        vals = (("ApplicationName", "GazeFace"),
                ("Description", "Face gesture and gaze mouse control"),
                ("ATExe", os.path.basename(exe)),
                ("StartExe", exe),
                ("StartParams", AT_FLAG),
                ("Profile", PROFILE),
                ("SimpleProfile", AT_NAME))
        lines += ["$k = Join-Path $acc %s" % _ps_str("ATs\\" + AT_NAME),
                  "if (-not (Test-Path $k)) { New-Item -Path $k -Force | "
                  "Out-Null }"]
        for n, v in vals:
            lines.append("New-ItemProperty -Path $k -Name %s -Value %s "
                         "-PropertyType String -Force | Out-Null"
                         % (_ps_str(n), _ps_str(v)))
        # Keep running across desktop switches; settings are our own.
        for n in ("TerminateOnDesktopSwitch", "CopySettingsToLockedDesktop"):
            lines.append("New-ItemProperty -Path $k -Name %s -Value 0 "
                         "-PropertyType DWord -Force | Out-Null" % _ps_str(n))
    if machine_config is not None:
        lines += [
            "$cur = (Get-ItemProperty -Path $acc -Name Configuration "
            "-ErrorAction SilentlyContinue).Configuration",
            "$items = @()",
            "if ($cur) { $items = @($cur.Split(',') | ForEach-Object "
            "{ $_.Trim() } | Where-Object { $_ }) }",
            "$items = @($items | Where-Object { $_ -ine %s })"
            % _ps_str(AT_NAME)]
        if machine_config:
            lines.append("$items += %s" % _ps_str(AT_NAME))
        lines.append("New-ItemProperty -Path $acc -Name Configuration "
                     "-Value ($items -join ',') -PropertyType String "
                     "-Force | Out-Null")
    lines.append("exit 0")
    return "\r\n".join(lines)


def _run_elevated(script: str, timeout_s: float = 300.0) -> int:
    """Run `script` in an elevated, hidden PowerShell and wait.

    Returns its exit code; -1 when it could not be started (most often:
    the UAC prompt was declined) or did not finish in time.
    """
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32.ShellExecuteExW.restype = wintypes.BOOL
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    # -EncodedCommand: the script carries quotes and angle brackets (the
    # Profile XML), which no command-line quoting survives intact.
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100      # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = ("-NoProfile -NonInteractive -ExecutionPolicy "
                         "Bypass -WindowStyle Hidden -EncodedCommand " + enc)
    info.nShow = 0                              # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        logger.warning("elevated registry update not started "
                       "(WinError %s)", ctypes.get_last_error())
        return -1
    if not info.hProcess:
        return -1
    try:
        if k32.WaitForSingleObject(info.hProcess,
                                   int(timeout_s * 1000)) != 0:
            logger.error("elevated registry update timed out")
            return -1
        code = wintypes.DWORD(0)
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        k32.CloseHandle(info.hProcess)


# ---------------------------------------------------------------- #
# Settings for the sign-in screen
# ---------------------------------------------------------------- #

def mirror_configs() -> None:
    """Copy this user's settings to where the sign-in screen copy reads.

    The sign-in / lock screen copy runs as SYSTEM, whose own profile
    holds no settings (see paths.user_data_dir). It reads the shared copy
    under ProgramData instead, refreshed from here.
    """
    from src import paths
    src_dir = paths.configs_dir()
    dst_dir = paths.signin_data_dir() / "configs"
    if not (src_dir / "default.json").is_file():
        return
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("launcher_icons"))
        logger.info("settings copied for the sign-in screen: %s", dst_dir)
    except Exception as e:
        logger.error("settings copy for the sign-in screen failed: %s", e)


# ---------------------------------------------------------------- #
# The two switches
# ---------------------------------------------------------------- #

def set_before_signin(on: bool) -> str:
    """Returns "" on success, else a short reason."""
    why = blocker() if on else ""
    if why:
        return why
    if on:
        mirror_configs()
    code = _run_elevated(admin_script(register=on and not registered(),
                                      machine_config=on))
    if code != 0:
        return "cancelled" if code == -1 else t("exit code %d") % code
    return ""


def set_after_signin(on: bool) -> str:
    """Returns "" on success, else a short reason."""
    if on:
        why = blocker()
        if why:
            return why
        if not registered():
            code = _run_elevated(admin_script(register=True))
            if code != 0:
                return "cancelled" if code == -1 else t("exit code %d") % code
    try:
        _write_user_config(on)
    except OSError as e:
        return str(e)
    return ""


def apply_async(which: str, on: bool, done) -> None:
    """Run a switch off the UI thread (the UAC prompt blocks); `done`
    gets the reason string ("" = success) on that worker thread."""
    fn = set_before_signin if which == "before" else set_after_signin

    def work():
        try:
            why = fn(on)
        except Exception as e:
            logger.error("startup option %s=%s failed: %s", which, on, e,
                         exc_info=e)
            why = str(e)
        logger.info("startup option %s=%s -> %s", which, on,
                    why or "ok")
        try:
            from src import desktop_guard
            desktop_guard.set_yield_enabled(any_enabled())
        except Exception:
            pass
        done(why)

    threading.Thread(target=work, name="at-startup", daemon=True).start()
