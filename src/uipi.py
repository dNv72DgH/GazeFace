"""Who owns the foreground window, and are we allowed to drive it.

Windows refuses synthetic input from a process whose integrity level is
below that of the process owning the FOREGROUND window. That restriction
covers SetCursorPos as well as SendInput, which is why the symptom is not
just "clicks are ignored" but "head tracking freezes too" while such a
window has focus — the app looks broken rather than restricted.

Nothing here changes behaviour. It answers, at the moment it happens,
whether the block was a permission problem or something else, because the
two need completely different fixes:

  * higher-integrity foreground -> no amount of SendInput will get
    through without the UIAccess flag (see src/ui_access.py).
  * same or lower integrity   -> permissions are not the problem and the
    cause is elsewhere.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("UIPI")

_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25
_TOKEN_UIACCESS = 26
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Prototypes are REQUIRED here, not tidiness: ctypes defaults a function's
# restype to c_int, so a 64-bit HANDLE from OpenProcess comes back
# truncated and every later call using it fails with OverflowError.
#
# They go on PRIVATE handles, not ctypes.windll.*: that loader is shared
# process-wide, so a prototype set through it rewrites the signature for
# every other module too. Doing that to user32.GetCursorPos elsewhere in
# this app broke an unrelated caller that passes its own POINT type
# (src/extensions/launcher.py). ctypes.WinDLL(name) builds an unshared
# loader, so these declarations stay local to this module.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_a32 = ctypes.WinDLL("advapi32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_a32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.HANDLE)]
_a32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                     ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
_a32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_a32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_a32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
_a32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]


def _il_name(rid):
    if rid >= 0x4000:
        return "System"
    if rid >= 0x3000:
        return "High"
    if rid >= 0x2000:
        return "Medium"
    if rid >= 0x1000:
        return "Low"
    return "Untrusted"


def integrity_level(pid):
    """(rid, name) for a pid, or (None, reason)."""
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        # Being unable to open it is itself informative: it means that
        # process sits above us.
        return None, "access denied (higher integrity)"
    tok = wintypes.HANDLE()
    try:
        if not _a32.OpenProcessToken(h, _TOKEN_QUERY, ctypes.byref(tok)):
            return None, "token denied"
        need = wintypes.DWORD()
        _a32.GetTokenInformation(tok, _TOKEN_INTEGRITY_LEVEL, None, 0,
                                 ctypes.byref(need))
        buf = ctypes.create_string_buffer(need.value)
        if not _a32.GetTokenInformation(tok, _TOKEN_INTEGRITY_LEVEL, buf,
                                        need.value, ctypes.byref(need)):
            return None, "query failed"
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        cnt = _a32.GetSidSubAuthorityCount(sid)[0]
        rid = _a32.GetSidSubAuthority(sid, cnt - 1)[0]
        return int(rid), _il_name(int(rid))
    except Exception as e:
        return None, "error: %s" % e
    finally:
        if tok:
            _k32.CloseHandle(tok)
        _k32.CloseHandle(h)


def has_uiaccess(pid=None) -> bool:
    """Does this process hold the UIAccess flag?

    The flag is granted by the loader from the exe's manifest and cannot
    be turned on or off afterwards, so this is the only honest way to
    answer "is the privileged build actually running privileged" — the
    exe can be named GazeFace_UI.exe, carry the manifest, and still be
    running WITHOUT the flag if Windows declined to grant it.
    """
    import os as _os
    target = _os.getpid() if pid is None else int(pid)
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, target)
    if not h:
        return False
    tok = wintypes.HANDLE()
    try:
        if not _a32.OpenProcessToken(h, _TOKEN_QUERY, ctypes.byref(tok)):
            return False
        val = wintypes.DWORD()
        got = wintypes.DWORD()
        if not _a32.GetTokenInformation(tok, _TOKEN_UIACCESS,
                                        ctypes.byref(val),
                                        ctypes.sizeof(val),
                                        ctypes.byref(got)):
            return False
        return bool(val.value)
    except Exception:
        return False
    finally:
        if tok:
            _k32.CloseHandle(tok)
        _k32.CloseHandle(h)


def foreground_info():
    """(pid, exe name, title, integrity name) of the foreground window."""
    u = ctypes.windll.user32
    try:
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None, "", "", "?"
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(hwnd, buf, 200)
        name = ""
        try:
            import psutil
            name = psutil.Process(pid.value).name()
        except Exception:
            pass
        _rid, il = integrity_level(pid.value)
        return int(pid.value), name, buf.value, il
    except Exception as e:
        return None, "", "", "error: %s" % e


def describe_block():
    """One line explaining who is blocking us, for the log."""
    try:
        import os
        _rid, ours = integrity_level(os.getpid())
        pid, name, title, theirs = foreground_info()
        return ("foreground=%s(pid=%s) integrity=%s ; ours=%s ; title=%r"
                % (name or "?", pid, theirs, ours, title[:60]))
    except Exception as e:
        return "describe_block failed: %s" % e
