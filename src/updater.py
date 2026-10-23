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
"""Update check and hand-off to the elevated updater.

At start (unless checked within the last hour) and then once a day, the
latest GitHub release is compared with this version. A newer one is
offered in a small window; nothing happens unless the user accepts.

Installing needs administrator rights, and the standard edition cannot
operate the User Account Control prompt - for many users the face is the
only input. So accepting does not start the setup program: it starts the
scheduled task \\GazeFace\\Update, which the installer registered to run
installer\\updater\\gazeface_update.ps1 as SYSTEM. That script fetches the
release itself and checks its SHA-256 before running it; nothing from
here reaches it except "go".

The setup program stops GazeFace, so a small detached helper (not a child:
the installer stops GazeFace's whole process tree) waits for the task's
result and starts GazeFace again. On that next start the release notes
are shown.

Only an installed copy (one with the updater next to it) checks; a dev
run, a portable copy and the copies Windows starts at sign-in do not.
"""

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request

from src import paths
from src.i18n import get_language, t

logger = logging.getLogger("Updater")

API = "https://api.github.com/repos/dNv72DgH/GazeFace/releases/latest"
# Testing only: check another URL (file:// works). Changes what is
# OFFERED, never what the elevated task installs.
API_ENV = "GAZEFACE_UPDATE_API"
TASK = r"\GazeFace\Update"

FIRST_DELAY_S = 60
INTERVAL_S = 24 * 3600
STARTUP_MIN_GAP_S = 3600
SNOOZE_S = 24 * 3600
NOTES_MAX_AGE_S = 24 * 3600

LAST_CHECK_KEY = "update_last_check"
NOTIFY_KEY = "update_notify"
SNOOZE_KEY = "update_snooze"
# Written by the installer (GazeFace.iss) when the full edition setup
# failed; removed when it succeeds or the standard edition is installed.
FULL_FAILED_MARK = "full_edition_failed.txt"
FULL_FAILED_SEEN_KEY = "full_edition_failed_seen"

CREATE_NO_WINDOW = 0x08000000


def _updater_dir():
    return paths.install_dir() / "updater"


def _notes_file():
    return paths.user_data_dir() / "update_notes.json"


def eligible() -> bool:
    if os.environ.get(API_ENV):
        return True
    return (getattr(sys, "frozen", False)
            and "--at" not in sys.argv
            and not paths._is_system_account()
            and (_updater_dir() / "gazeface_update.ps1").is_file())


def version_tuple(s):
    s = str(s or "").strip().lstrip("vV")
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        return None


def _current():
    from src.config_manager import VERSION
    return VERSION


def _setting(key, default):
    from src.config_manager import ConfigManager
    return ConfigManager()._read_app_setting(key, default)


def _save_setting(key, value):
    from src.config_manager import ConfigManager
    ConfigManager().write_app_setting(key, value)


# ---------------------------------------------------------------- #
# Checking
# ---------------------------------------------------------------- #

def fetch_latest():
    url = os.environ.get(API_ENV) or API
    req = urllib.request.Request(url, headers={
        "User-Agent": "GazeFace", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def newer_release(rel, current):
    """{version, notes} if `rel` is newer than `current` and carries a
    setup program the elevated updater will accept, else None."""
    want = version_tuple(rel.get("tag_name"))
    have = version_tuple(current)
    if want is None or have is None or want <= have:
        return None
    for a in rel.get("assets") or []:
        name = str(a.get("name") or "")
        if (name.startswith("GazeFace-Setup-") and name.endswith(".exe")
                and str(a.get("digest") or "").startswith("sha256:")):
            return {"version": str(rel["tag_name"]).lstrip("vV"),
                    "notes": str(rel.get("body") or "")}
    return None


def _snoozed(version):
    s = _setting(SNOOZE_KEY, {}) or {}
    return (isinstance(s, dict) and s.get("version") == version
            and time.time() < float(s.get("until", 0)))


def notify_enabled() -> bool:
    """Advanced settings > Updates > "Notify me of updates" (app-wide,
    on by default). Read at every check, so switching it applies at once."""
    try:
        return bool(_setting(NOTIFY_KEY, True))
    except Exception:
        return True


def _check_once(tk_root):
    if not notify_enabled():
        logger.info("update check skipped: notifications are off")
        return
    try:
        rel = fetch_latest()
    except Exception as e:
        logger.info("update check failed: %s", e)
        return
    finally:
        try:
            _save_setting(LAST_CHECK_KEY, int(time.time()))
        except Exception:
            pass
    offer = newer_release(rel, _current())
    if offer is None:
        logger.info("up to date (%s, latest %s)", _current(),
                    rel.get("tag_name"))
        return
    if _snoozed(offer["version"]):
        logger.info("update %s snoozed", offer["version"])
        return
    logger.info("update %s available", offer["version"])
    tk_root.after(0, lambda: UpdateBanner.show_offer(tk_root, offer))


def _loop(tk_root):
    try:
        last = float(_setting(LAST_CHECK_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0
    gap = time.time() - last
    # Every start the user makes checks. Self-restarts (auto recovery,
    # restart.py passes --wait-pid) can come often, so those skip the
    # start check when one ran within the hour (not when testing).
    self_restart = "--wait-pid" in sys.argv
    delay = (FIRST_DELAY_S
             if (not self_restart or gap >= STARTUP_MIN_GAP_S
                 or os.environ.get(API_ENV))
             else max(FIRST_DELAY_S, INTERVAL_S - gap))
    logger.info("next update check in %d s", delay)
    while True:
        time.sleep(delay)
        _check_once(tk_root)
        delay = INTERVAL_S


def _full_edition_notice(tk_root):
    """The installer leaves FULL_FAILED_MARK in the install folder when
    the full edition setup failed - silently so in an automatic update,
    after which GazeFace just runs as the standard edition. Said once per
    failure (the marker's time), in the banner."""
    mark = paths.install_dir() / FULL_FAILED_MARK
    try:
        stamp = int(mark.stat().st_mtime)
    except OSError:
        return
    try:
        if int(_setting(FULL_FAILED_SEEN_KEY, 0) or 0) == stamp:
            return
        _save_setting(FULL_FAILED_SEEN_KEY, stamp)
        detail = mark.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        logger.debug("full edition marker: %s", e)
        detail = ""
    logger.error("full edition setup failed: %s", detail.strip()[:300])
    UpdateBanner._get(tk_root).failed(
        t("The full edition could not be set up, so GazeFace runs as the "
          "standard edition. Run the installer again to retry."),
        detail.strip().splitlines()[-1] if detail.strip() else "")


def start(tk_root):
    """Call once from the main window (Tk thread)."""
    try:
        tk_root.after(1500, lambda: _after_update(tk_root))
        tk_root.after(2500, lambda: _full_edition_notice(tk_root))
    except Exception as e:
        logger.error("post-update notice: %s", e)
    if not eligible():
        logger.info("update check off (not an installed copy)")
        return
    threading.Thread(target=_loop, args=(tk_root,), name="UpdateCheck",
                     daemon=True).start()


# ---------------------------------------------------------------- #
# Starting the update
# ---------------------------------------------------------------- #

def _ps_quote(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _result_stamp() -> int:
    """result.json's last write as a Windows FILETIME (0 if none). A
    result counts only once the file has changed since the task was
    started: its own time field has whole-second resolution, so a result
    of an earlier try in the same second would pass for a fresh one."""
    try:
        ns = os.stat(_updater_dir() / "result.json").st_mtime_ns
    except OSError:
        return 0
    return ns // 100 + 116444736000000000


def _spawn_relauncher(stamp):
    """A hidden PowerShell that waits for the task's result and starts
    GazeFace again. Started through a second PowerShell that exits at
    once, so it is nobody's child when the installer stops GazeFace's
    process tree. Gives up after 2 minutes if the task never starts, and
    starts nothing while a GazeFace of this install is running (the
    update failed before the setup program stopped it). Copies run from
    other folders are ignored: the setup program does not stop them.

    While GazeFace is closed for the install there is nothing of it on
    screen, which looked like it had simply vanished. So from the moment
    the install begins this helper shows a small window with a running
    bar ("updating, GazeFace starts again by itself") until GazeFace is
    back. It needs no input and closes itself."""
    app = paths.install_dir()
    script = r"""
$r = %s; $exe = %s; $dir = %s; $t0 = [int64]%d
$title = %s; $busy = %s; $starting = %s; $failed = %s
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$form = $null; $label = $null
function Show-Busy([string]$text) {
  if (-not $script:form) {
    $f = New-Object System.Windows.Forms.Form
    $f.Text = $title; $f.FormBorderStyle = 'FixedDialog'; $f.ControlBox = $false
    $f.StartPosition = 'CenterScreen'; $f.ClientSize = New-Object System.Drawing.Size(460, 120)
    $f.TopMost = $true; $f.ShowInTaskbar = $true
    $f.Font = New-Object System.Drawing.Font('Segoe UI', 11)
    $l = New-Object System.Windows.Forms.Label
    $l.SetBounds(20, 16, 420, 52)
    $f.Controls.Add($l)
    $p = New-Object System.Windows.Forms.ProgressBar
    $p.Style = 'Marquee'; $p.MarqueeAnimationSpeed = 30; $p.SetBounds(20, 78, 420, 20)
    $f.Controls.Add($p)
    $f.Show()
    $script:form = $f; $script:label = $l
  }
  $script:label.Text = $text
  [System.Windows.Forms.Application]::DoEvents()
}
# GazeFace from THIS install only (either exe): a copy run from another
# folder is not stopped by the setup program and must not count. The path
# comes from QueryFullProcessImageName with limited-query rights: .Path is
# empty for the UIAccess build when asked from a normal process.
Add-Type -Namespace GfUpd -Name Img -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern System.IntPtr OpenProcess(int access, bool inherit, int pid);
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool QueryFullProcessImageNameW(System.IntPtr h, int flags, System.Text.StringBuilder name, ref int size);
[DllImport("kernel32.dll")]
public static extern bool CloseHandle(System.IntPtr h);
'@
function Get-ImagePath([int]$procId) {
  $h = [GfUpd.Img]::OpenProcess(0x1000, $false, $procId)
  if ($h -eq [IntPtr]::Zero) { return '' }
  try {
    $sb = New-Object System.Text.StringBuilder 1024; $n = 1024
    if ([GfUpd.Img]::QueryFullProcessImageNameW($h, 0, $sb, [ref]$n)) { return $sb.ToString() }
    return ''
  } finally { [void][GfUpd.Img]::CloseHandle($h) }
}
function Get-Ours {
  Get-Process -Name GazeFace, GazeFace_UI -ErrorAction SilentlyContinue |
    Where-Object { (Get-ImagePath $_.Id).StartsWith($dir + '\', [StringComparison]::OrdinalIgnoreCase) }
}
function Wait-A-Bit([int]$ms) {
  for ($i = 0; $i -lt ($ms / 100); $i++) {
    if ($script:form) { [System.Windows.Forms.Application]::DoEvents() }
    Start-Sleep -Milliseconds 100
  }
}
$deadline = (Get-Date).AddSeconds(120); $seen = $false; $j = $null
while ($true) {
  Wait-A-Bit 1000
  $j = $null
  try {
    if ((Get-Item -LiteralPath $r -ErrorAction Stop).LastWriteTimeUtc.ToFileTimeUtc() -gt $t0) {
      $j = Get-Content -LiteralPath $r -Raw | ConvertFrom-Json
    }
  } catch {}
  if ($j) {
    if (@('ok', 'uptodate', 'failed') -contains $j.status) { break }
    if (-not $seen) { $seen = $true; $deadline = (Get-Date).AddMinutes(30) }
    # The setup program stops GazeFace once it starts installing.
    if ($j.status -eq 'installing' -and -not (Get-Ours)) {
      Show-Busy $busy
    }
  }
  if ((Get-Date) -gt $deadline) { if ($seen) { break } else { exit } }
}
if (-not (Get-Ours)) {
  if ($j -and $j.status -eq 'failed') { $text = $failed } else { $text = $starting }
  Show-Busy $text
  Wait-A-Bit 2000
  Start-Process -FilePath $exe -WorkingDirectory $dir
  # Keep the window up until GazeFace has a window of its own (max 60 s).
  for ($i = 0; $i -lt 60; $i++) {
    Wait-A-Bit 1000
    $g = Get-Ours | Where-Object { $_.MainWindowHandle -ne 0 }
    if ($g) { break }
  }
}
if ($form) { $form.Close() }
""" % (_ps_quote(_updater_dir() / "result.json"),
       _ps_quote(app / "GazeFace.exe"), _ps_quote(app), int(stamp),
       _ps_quote(t("GazeFace update")),
       _ps_quote(t("Updating GazeFace... It starts again by itself when "
                   "the update is done.")),
       _ps_quote(t("Starting GazeFace...")),
       _ps_quote(t("The update failed. Starting GazeFace...")))
    inner = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    outer = ("Start-Process -WindowStyle Hidden -FilePath powershell.exe "
             "-ArgumentList '-NoProfile','-NonInteractive','-ExecutionPolicy',"
             "'Bypass','-EncodedCommand','%s'" % inner)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         outer],
        creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def begin_update(offer):
    """Hand the update to the elevated task. Returns (start time,
    result.json stamp at the start), or None if it did not start."""
    started = int(time.time()) - 1
    stamp = _result_stamp()
    try:
        _notes_file().parent.mkdir(parents=True, exist_ok=True)
        with open(_notes_file(), "w", encoding="utf-8") as f:
            json.dump({"version": offer["version"], "notes": offer["notes"],
                       "from": _current(), "started": started}, f,
                      ensure_ascii=False)
    except Exception as e:
        logger.error("saving release notes failed: %s", e)
    try:
        _spawn_relauncher(stamp)
    except Exception as e:
        logger.error("relauncher failed: %s", e)
    try:
        rc = subprocess.run(["schtasks.exe", "/Run", "/TN", TASK],
                            creationflags=CREATE_NO_WINDOW,
                            capture_output=True, timeout=30).returncode
    except Exception as e:
        logger.error("starting the update task failed: %s", e)
        rc = -1
    if rc != 0:
        logger.error("update task did not start (rc %s)", rc)
        _drop_notes()
        return None
    logger.info("update %s handed to the update task", offer["version"])
    return started, stamp


def _drop_notes():
    try:
        _notes_file().unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug("notes file: %s", e)


# ---------------------------------------------------------------- #
# After the update
# ---------------------------------------------------------------- #

def notes_for_language(body: str, lang: str) -> str:
    """The release notes carry English, then '---' and a Japanese part
    under '## 日本語'. Plain text for the chosen language."""
    body = body.replace("\r\n", "\n")
    marker = "## 日本語"
    if lang == "ja" and marker in body:
        text = body.split(marker, 1)[1]
    else:
        text = body.split("\n---\n", 1)[0]
    lines = []
    for line in text.strip().split("\n"):
        line = line.lstrip("#").strip() if line.startswith("#") else line
        lines.append(line.replace("**", "").replace("`", ""))
    return "\n".join(lines).strip()


def _read_result():
    try:
        with open(_updater_dir() / "result.json", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _after_update(tk_root):
    try:
        with open(_notes_file(), encoding="utf-8") as f:
            pending = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.debug("notes file unreadable: %s", e)
        _drop_notes()
        return
    want = version_tuple(pending.get("version"))
    if want is not None and want == version_tuple(_current()):
        _drop_notes()
        logger.info("updated to %s", _current())
        show_release_notes(_current(),
                           notes_for_language(pending.get("notes", ""),
                                              get_language()))
        return
    res = _read_result() or {}
    started = float(pending.get("started", 0) or 0)
    if (res.get("status") == "failed"
            and float(res.get("time", 0) or 0) >= started):
        _drop_notes()
        logger.error("update failed: %s", res.get("detail"))
        UpdateBanner.show_failed(tk_root, res.get("detail"))
    elif time.time() - started > NOTES_MAX_AGE_S:
        _drop_notes()


# ---------------------------------------------------------------- #
# In-app UI: a banner along the bottom of the main window, never a window
# of its own - an update must not get in the way of other apps. Not a
# native messagebox either: that would block the Tk loop and with it face
# control.
# ---------------------------------------------------------------- #

class UpdateBanner:

    POLL_MS = 1000
    START_TIMEOUT_S = 120
    _current = None

    @classmethod
    def _get(cls, tk_root):
        b = cls._current
        if b is None or not b._alive():
            b = cls._current = cls(tk_root)
        return b

    @classmethod
    def show_offer(cls, tk_root, offer):
        cls._get(tk_root).offer(offer)

    @classmethod
    def show_failed(cls, tk_root, detail=""):
        cls._get(tk_root).failed(t("The update failed."), detail)

    def __init__(self, tk_root):
        import customtkinter
        self.root = tk_root
        self._offer = None
        self._started = 0
        self._stamp = 0
        f = customtkinter.CTkFrame(tk_root, fg_color="white",
                                   border_color="#E7E2DB", border_width=1,
                                   corner_radius=12)
        self.frame = f
        f.grid_columnconfigure(0, weight=1)
        self.msg = customtkinter.CTkLabel(f, text="", anchor="w",
                                          justify="left", wraplength=640)
        self.msg.grid(row=0, column=0, sticky="w", padx=(18, 12),
                      pady=(12, 4))
        self.bar = customtkinter.CTkProgressBar(f, width=620)
        self.btns = customtkinter.CTkFrame(f, fg_color="transparent")
        self.btns.grid(row=0, column=1, rowspan=2, padx=(0, 14), pady=10)
        f.place(relx=0.5, rely=1.0, y=-12, anchor="s", relwidth=0.95)
        f.lift()

    def _alive(self):
        try:
            return bool(self.frame.winfo_exists())
        except Exception:
            return False

    def _buttons(self, spec):
        import customtkinter
        for w in self.btns.winfo_children():
            w.destroy()
        # An empty CTkFrame falls back to its default 200 px size.
        if spec:
            self.btns.grid()
        else:
            self.btns.grid_remove()
        for i, (text, cmd) in enumerate(spec):
            customtkinter.CTkButton(self.btns, text=text, width=140,
                                    height=36, command=cmd).pack(
                side="left", padx=(0 if i == 0 else 8, 0))

    def _bar(self, fraction=None):
        """None hides the bar, a negative value runs it without an end."""
        if fraction is None:
            self.bar.stop()
            self.bar.grid_remove()
            return
        self.bar.grid(row=1, column=0, sticky="we", padx=(18, 12),
                      pady=(0, 14))
        if fraction < 0:
            if self.bar.cget("mode") != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start()
        else:
            if self.bar.cget("mode") != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar.set(max(0.0, min(1.0, fraction)))

    def _say(self, text):
        self.msg.configure(text=text)
        self.frame.lift()

    # -- states ------------------------------------------------------ #

    def offer(self, offer):
        self._offer = offer
        self._bar(None)
        self._say(t("GazeFace %s is available.") % offer["version"] + "\n"
                  + t("While it updates, GazeFace closes for a moment and "
                      "then starts again by itself."))
        self._buttons([(t("Update now"), self._accept),
                       (t("Later"), self._later)])

    def failed(self, text, detail=""):
        self._bar(None)
        detail = str(detail or "").strip()
        self._say(text + ("\n" + detail[:160] if detail else ""))
        self._buttons([(t("Close"), self.close)])

    def close(self):
        if UpdateBanner._current is self:
            UpdateBanner._current = None
        try:
            self.bar.stop()
            self.frame.destroy()
        except Exception:
            pass

    # -- actions ----------------------------------------------------- #

    def _later(self):
        try:
            _save_setting(SNOOZE_KEY, {"version": self._offer["version"],
                                       "until": int(time.time() + SNOOZE_S)})
        except Exception as e:
            logger.error("snooze: %s", e)
        self.close()

    def _accept(self):
        self._buttons([])
        self._say(t("Preparing the update..."))
        self._bar(-1)
        offer = self._offer

        def work():
            handed = begin_update(offer)
            self.root.after(0, lambda: self._handed_over(handed))
        threading.Thread(target=work, name="UpdateStart", daemon=True).start()

    def _handed_over(self, handed):
        if not handed:
            self.failed(t("The update could not be started."))
            return
        self._started, self._stamp = handed
        self._poll()

    def _poll(self):
        if not self._alive():
            return
        res = _read_result() if _result_stamp() > self._stamp else None
        if res:
            st = res.get("status")
            if st == "downloading":
                total = float(res.get("total") or 0)
                frac = float(res.get("done") or 0) / total if total else 0.0
                self._bar(frac)
                self._say(t("Downloading the update... %d%%")
                          % int(frac * 100))
            elif st == "installing":
                self._bar(-1)
                self._say(t("Installing the update... GazeFace closes for "
                            "a moment and starts again by itself."))
            elif st == "running":
                self._bar(-1)
                self._say(t("Preparing the update..."))
            elif st == "uptodate":
                _drop_notes()
                self.failed(t("No newer version was found."))
                return
            elif st == "failed":
                _drop_notes()
                logger.error("update failed: %s", res.get("detail"))
                self.failed(t("The update failed."), res.get("detail"))
                return
            elif st == "ok":
                # Installed, yet this copy was not stopped: the next start
                # runs the new version and shows its notes.
                self._bar(None)
                self._say(t("The update is installed. Restart GazeFace "
                            "to use it."))
                self._buttons([(t("Close"), self.close)])
                return
        elif time.time() - self._started > self.START_TIMEOUT_S:
            _drop_notes()
            self.failed(t("The update could not be started."))
            return
        self.root.after(self.POLL_MS, self._poll)


def show_release_notes(version, text):
    """The notes of the version just installed, as an in-app panel."""
    import customtkinter
    from src.gui.panel_host import panel_host
    host = panel_host()
    if host is None:
        return
    body = host.push(t("What's new in GazeFace %s") % version)
    box = customtkinter.CTkTextbox(body, wrap="word", height=380)
    box.pack(fill="both", expand=True, padx=24, pady=(16, 10))
    box.insert("1.0", text or t("GazeFace has been updated to %s.")
               % version)
    box.configure(state="disabled")
    customtkinter.CTkButton(body, text=t("OK"), width=140, height=36,
                            command=lambda: host.pop(body)).pack(
        padx=24, pady=(0, 16), anchor="w")
