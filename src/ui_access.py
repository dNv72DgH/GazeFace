"""Which build to run: the ordinary one, or the UIAccess one.

What UIAccess buys
------------------
Windows refuses synthetic input — SendInput AND SetCursorPos — from a
process whose integrity level is below that of the process owning the
FOREGROUND window. The user experiences that as "head tracking was taken
away": the pointer simply stops following, and stays stopped for as long
as that window has focus. A process holding the UIAccess flag is exempt
from that check up to High integrity.

What it does NOT buy
--------------------
UIAccess does not change how the input is produced, so events still
arrive marked LLMHF_INJECTED. Applications that refuse injected input on
principle keep refusing it.

It also does not reach the SYSTEM-integrity desktop: UAC prompts and the
secure desktop stay out of bounds. Measured, not assumed.

Why it needs a second exe
-------------------------
UIAccess comes from a flag in the exe's embedded manifest, read by the
loader at process creation. There is no API to acquire or drop it at
runtime, so "turn it on" can only mean "run a different exe". Hence
GazeFace_UI.exe: same code, same everything, one manifest bit different.

Windows grants the flag only if ALL of these hold, and refuses to start
the process at all when they do not:

  * the manifest requests it,
  * the exe is Authenticode-signed by a certificate chaining to a root
    the machine trusts, and
  * the exe lives in a secure location — under Program Files or Windows.

A user-profile install can therefore never run the UIAccess build. That
is checked here up front so the setting can explain itself instead of
failing silently at the next restart.

When it CAN run, it is used: there is nothing to switch on. The build is
chosen app-wide (not per profile - a profile switch must not change what
Windows lets the app do), from the "ui_access" key of default.json, which
defaults to on and exists only so it can be turned off by hand.
"""

import ctypes
import logging
import os
import sys
from ctypes import wintypes

logger = logging.getLogger("UIAccess")

CONFIG_KEY = "ui_access"
NORMAL_EXE = "GazeFace.exe"
UIACCESS_EXE = "GazeFace_UI.exe"


def _secure_location(path) -> bool:
    """Is `path` somewhere Windows will accept a UIAccess exe from?

    Program Files (either bitness) and the Windows directory. Comparison
    is case-insensitive and normalised because the roots come from the
    environment and the path from sys.executable.
    """
    try:
        p = os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return False
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                "SystemRoot"):
        v = os.environ.get(var)
        if v:
            roots.append(os.path.normcase(os.path.abspath(v)))
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def install_dir() -> str:
    """Directory the running exe lives in (or the repo root when frozen
    is False, so the setting still reports sensibly in a dev run)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    try:
        from src import paths
        return str(paths.install_dir())
    except Exception:
        return os.path.abspath(".")


def variant_path(uiaccess: bool) -> str:
    return os.path.join(install_dir(),
                        UIACCESS_EXE if uiaccess else NORMAL_EXE)


def active() -> bool:
    """Is THIS process actually running with the flag?

    Asks the token, not the exe name: the privileged build can be
    launched and still not be granted the flag.
    """
    try:
        from src import uipi
        return bool(uipi.has_uiaccess())
    except Exception:
        return False


def wanted() -> bool:
    try:
        from src.config_manager import ConfigManager
        return bool(ConfigManager()._read_app_setting(CONFIG_KEY, True))
    except Exception:
        return True


def blocker() -> str:
    """Empty string if the UIAccess build could run here, else the reason.

    Deliberately returns the FIRST unmet requirement rather than a
    generic failure, because each one has a different fix and the user
    otherwise has no way to tell which applies.
    """
    # Worded through t(): the reason is shown on the Advanced page.
    from src.i18n import t
    if not getattr(sys, "frozen", False):
        return t("not a packaged build (dev run)")
    exe = variant_path(True)
    if not os.path.isfile(exe):
        return t("%s is missing from this build") % UIACCESS_EXE
    if not _secure_location(install_dir()):
        return (t("installed in %s - it has to be under Program Files")
                % install_dir())
    if not _is_signed(exe):
        return t("%s is not signed") % UIACCESS_EXE
    if not _is_trusted(exe):
        return t("%s is not signed by a trusted certificate") % UIACCESS_EXE
    return ""


def _is_signed(exe) -> bool:
    """Does the exe carry an Authenticode signature at all?

    Presence only — whether the chain is trusted is Windows' call at
    launch, and duplicating that judgement here would just be a second
    opinion that can disagree with the one that matters.
    """
    try:
        with open(exe, "rb") as f:
            head = f.read(0x400)
        if head[:2] != b"MZ":
            return False
        import struct
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        if pe + 0x18 > len(head):
            return False
        magic = struct.unpack_from("<H", head, pe + 0x18)[0]
        # Security directory is entry 4 of the data directories, which
        # start at 0x60 into the optional header for PE32 and 0x70 for
        # PE32+.
        base = pe + 0x18 + (0x70 if magic == 0x20B else 0x60) + 4 * 8
        if base + 8 > len(head):
            return False
        rva, size = struct.unpack_from("<II", head, base)
        return bool(rva and size)
    except Exception as e:
        logger.debug("signature probe failed: %s", e)
        return False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p)]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p)]


# WINTRUST_ACTION_GENERIC_VERIFY_V2
_VERIFY_V2 = _GUID(0x00AAC56B, 0xCD44, 0x11D0,
                   (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0,
                                        0x4F, 0xC2, 0x95, 0xEE))


def _is_trusted(exe) -> bool:
    """Does Windows trust the exe's signature (WinVerifyTrust)?

    The presence check above is not enough to switch on: an exe signed
    by a certificate this machine does not trust is refused at launch,
    and a start-up that switched to it would fail every time. Offline
    only (no revocation check, cached URLs only) - this runs at start-up.
    """
    try:
        wt = ctypes.WinDLL("wintrust")
        wt.WinVerifyTrust.restype = ctypes.c_long
        wt.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(_GUID),
                                      ctypes.POINTER(_WINTRUST_DATA)]
        fi = _WINTRUST_FILE_INFO(ctypes.sizeof(_WINTRUST_FILE_INFO),
                                 str(exe), None, None)
        wd = _WINTRUST_DATA()
        wd.cbStruct = ctypes.sizeof(wd)
        wd.dwUIChoice = 2                 # WTD_UI_NONE
        wd.fdwRevocationChecks = 0        # WTD_REVOKE_NONE
        wd.dwUnionChoice = 1              # WTD_CHOICE_FILE
        wd.pFile = ctypes.pointer(fi)
        wd.dwStateAction = 1              # WTD_STATEACTION_VERIFY
        wd.dwProvFlags = 0x1000           # WTD_CACHE_ONLY_URL_RETRIEVAL
        guid = _GUID(_VERIFY_V2.Data1, _VERIFY_V2.Data2, _VERIFY_V2.Data3,
                     _VERIFY_V2.Data4)
        hwnd = wintypes.HWND(-1)          # INVALID_HANDLE_VALUE: no UI
        rc = wt.WinVerifyTrust(hwnd, ctypes.byref(guid), ctypes.byref(wd))
        wd.dwStateAction = 2              # WTD_STATEACTION_CLOSE
        wt.WinVerifyTrust(hwnd, ctypes.byref(guid), ctypes.byref(wd))
        if rc != 0:
            logger.info("%s signature not trusted (0x%08X)",
                        os.path.basename(str(exe)), rc & 0xFFFFFFFF)
        return rc == 0
    except Exception as e:
        logger.debug("trust check failed: %s", e)
        return False


def target_exe():
    """The exe this app SHOULD be running as, or None if it already is.

    None means "no switch needed" — including every case where the
    setting asks for UIAccess but this machine cannot provide it. The
    caller must not treat that as a reason to do nothing about the
    setting; call blocker() to say why.
    """
    want = wanted()
    if want and blocker():
        return None
    if want == active():
        return None
    path = variant_path(want)
    if not os.path.isfile(path):
        return None
    return path


def apply_switch(reason: str) -> bool:
    """Restart into the exe the setting asks for. True if we are going.

    Deliberately NOT routed through restart.request_restart(): that path
    exists for recovery and carries a budget, and exceeding the budget
    during start-up rolls the user's config back to a backup. A switch
    the user asked for must never be able to destroy their settings, and
    it is not a symptom of anything, so it should not count against the
    recovery budget either.
    """
    target = target_exe()
    if not target:
        return False
    logger.warning("UIAccess switch: restarting as %s (%s)",
                   os.path.basename(target), reason)
    try:
        from src.task_killer import TaskKiller
        TaskKiller().exit(restart=True)
        return True
    except Exception as e:
        logger.error("UIAccess switch failed: %s", e, exc_info=e)
        return False


def describe() -> str:
    """One line for the log and the settings page."""
    want = wanted()
    if not want:
        return "off (standard build)"
    why = blocker()
    if why:
        return "requested but unavailable: %s" % why
    return "on and active" if active() else "requested, pending restart"
