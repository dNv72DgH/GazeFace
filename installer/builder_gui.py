"""GazeFace installer builder.

Edits the user-written parts of the installer and builds it:
  - custom\\texts.iss        option descriptions and finish notes (ja / en)
  - custom\\full_edition.ps1 script run elevated after a full edition install
then runs build_installer.ps1.

    python installer\\builder_gui.py
"""
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEXTS = HERE / "custom" / "texts.iss"
SCRIPT = HERE / "custom" / "full_edition.ps1"
BUILD = HERE / "build_installer.ps1"
OUTPUT = HERE / "Output"
POWERSHELL = "powershell.exe"
NO_WINDOW = subprocess.CREATE_NO_WINDOW

LANGS = [("ja", "日本語"), ("en", "English")]
DESC_FIELDS = [
    ("FullDesc", "完全版の説明（選択肢の下に表示）"),
    ("StandardDesc", "通常版の説明（選択肢の下に表示）"),
    ("LogonDesc", "「サインイン画面で軽量版を起動する」の説明（完全版のみ）"),
    ("AfterSigninDesc", "「サインイン後に起動する」の説明（完全版のみ・空欄なら表示なし）"),
]
NOTE_FIELDS = [
    ("DoneFull", "完全版の注意書き"),
    ("DoneStandard", "通常版の注意書き"),
]

TEXTS_HEADER = """\
; 自分で書くテキスト（GazeFace.iss から #include）。
; builder_gui.py が書き出す。手で編集する場合:
; %n = 改行。1項目1行で書く（行の途中で改行しない）。
; DoneFull / DoneStandard を空にすると、Inno Setup 標準の完了メッセージになる。

[CustomMessages]
"""

# Parse errors of a .ps1, one per line, without running it.
PARSE_CHECK = (
    "$e = $null; "
    "[void][System.Management.Automation.Language.Parser]::ParseFile("
    "$env:GF_SCRIPT, [ref]$null, [ref]$e); "
    "$e | ForEach-Object { 'L{0}:{1} {2}' -f "
    "$_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.Message }"
)


# ---- files ----

def load_texts():
    """{(lang, key): text} from texts.iss, %n turned into newlines."""
    values = {}
    if TEXTS.exists():
        for line in TEXTS.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^(en|ja)\.(\w+)=(.*)$", line)
            if m:
                values[(m.group(1), m.group(2))] = m.group(3).replace("%n", "\n")
    return values


def save_texts(values):
    lines = [TEXTS_HEADER]
    for fields, title in ((DESC_FIELDS, "; 版選択ページ・起動の設定ページ：各選択肢の下の説明"),
                          (NOTE_FIELDS, "; 完了ページの注意書き（版ごと）")):
        lines.append(title + "\n")
        for key, _ in fields:
            for lang, _ in reversed(LANGS):  # en first, as in GazeFace.iss
                text = values.get((lang, key), "").strip().replace("\r\n", "\n")
                lines.append(f"{lang}.{key}={text.replace(chr(10), '%n')}\n")
        lines.append("\n")
    TEXTS.write_text("".join(lines).rstrip("\n") + "\n", encoding="utf-8-sig")


def load_script():
    return SCRIPT.read_text(encoding="utf-8-sig") if SCRIPT.exists() else ""


def save_script(text):
    # BOM: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.
    with open(SCRIPT, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text.rstrip("\n") + "\n")


def check_script():
    """Parse errors of the saved script; empty when it parses."""
    env = dict(os.environ, GF_SCRIPT=str(SCRIPT))
    r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECK],
                       capture_output=True, encoding="oem", errors="replace",
                       env=env, creationflags=NO_WINDOW)
    return (r.stdout + r.stderr).strip()


def dist_folders():
    """Folders in the repo root that hold a built GazeFace."""
    return sorted(p.name for p in ROOT.iterdir()
                  if (p / "GazeFace" / "GazeFace.exe").exists())


def default_driver_dir():
    """driver\\gfmouse in this tree, else in the sibling GazeFace source tree."""
    for d in (ROOT / "driver" / "gfmouse", ROOT.parent / "GazeFace" / "driver" / "gfmouse"):
        if (d / "gfmouse.inf").exists():
            return str(d)
    return ""


def app_version():
    try:
        text = (ROOT / "src" / "config_manager.py").read_text(encoding="utf-8")
    except OSError:
        return "?"
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "?"


# ---- window ----

class Builder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GazeFace インストーラー作成")
        self.geometry("960x700")
        self.minsize(720, 520)
        self.boxes = {}  # (lang, key) -> Text
        self.log_queue = queue.Queue()
        self.building = False

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        nb.add(self.text_tab(nb, DESC_FIELDS, 3,
                             "インストーラーの版選択ページと起動の設定ページで、"
                             "各選択肢の下に表示される説明。"),
               text="説明文")
        nb.add(self.text_tab(nb, NOTE_FIELDS, 8,
                             "インストール完了ページに表示される注意書き。"
                             "空欄なら Inno Setup 標準の完了メッセージ。"),
               text="注意書き")
        nb.add(self.script_tab(nb), text="完全版スクリプト")
        nb.add(self.build_tab(nb), text="ビルド")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=8)
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left")
        ttk.Button(bar, text="保存 (Ctrl+S)", command=self.save).pack(side="right")

        self.bind_all("<Control-s>", lambda e: self.save())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.load()
        self.after(100, self.drain_log)

    def text_tab(self, parent, fields, height, hint):
        tab = ttk.Frame(parent, padding=10)
        ttk.Label(tab, text=hint, wraplength=880).pack(anchor="w", pady=(0, 8))
        for key, label in fields:
            group = ttk.LabelFrame(tab, text=label, padding=6)
            group.pack(fill="both", expand=True, pady=4)
            for col, (lang, lang_label) in enumerate(LANGS):
                group.columnconfigure(col, weight=1)
                ttk.Label(group, text=lang_label).grid(row=0, column=col, sticky="w")
                box = ScrolledText(group, height=height, wrap="word", undo=True,
                                   font=("Yu Gothic UI", 10))
                box.grid(row=1, column=col, sticky="nsew", padx=(0, 6) if col == 0 else 0)
                self.boxes[(lang, key)] = box
            group.rowconfigure(1, weight=1)
        return tab

    def script_tab(self, parent):
        tab = ttk.Frame(parent, padding=10)
        ttk.Label(tab, wraplength=880, text=(
            "完全版のインストール後、管理者権限で実行される PowerShell スクリプト"
            "（コンソール ウィンドウ表示あり）。引数: $AppDir = インストール先、"
            "$Language = ja / en。exit 0 で成功、それ以外はインストーラーがエラー表示。"
        )).pack(anchor="w", pady=(0, 8))
        self.script_box = ScrolledText(tab, wrap="none", undo=True, font=("Consolas", 10))
        self.script_box.pack(fill="both", expand=True)
        ttk.Button(tab, text="構文チェック", command=self.on_check).pack(anchor="e", pady=(6, 0))
        return tab

    def build_tab(self, parent):
        tab = ttk.Frame(parent, padding=10)
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="dist フォルダ:").pack(side="left")
        self.dist = ttk.Combobox(row, state="readonly", width=24)
        self.dist.pack(side="left", padx=6)
        ttk.Button(row, text="更新", command=self.refresh_dist).pack(side="left")
        self.version_label = ttk.Label(row, text="")
        self.version_label.pack(side="left", padx=16)
        self.open_button = ttk.Button(row, text="出力フォルダを開く",
                                      command=lambda: os.startfile(OUTPUT))
        self.open_button.pack(side="right")
        self.build_button = ttk.Button(row, text="保存してビルド", command=self.on_build)
        self.build_button.pack(side="right", padx=6)
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="ドライバー フォルダ:").pack(side="left")
        self.driver = tk.StringVar(value=default_driver_dir())
        ttk.Entry(row2, textvariable=self.driver).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="参照", command=self.browse_driver).pack(side="left")
        self.log = ScrolledText(tab, wrap="none", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        self.refresh_dist()
        return tab

    # ---- load / save ----

    def current(self):
        texts = {k: b.get("1.0", "end-1c") for k, b in self.boxes.items()}
        return texts, self.script_box.get("1.0", "end-1c")

    def load(self):
        values = load_texts()
        for k, box in self.boxes.items():
            box.delete("1.0", "end")
            box.insert("1.0", values.get(k, ""))
            box.edit_reset()
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", load_script())
        self.script_box.edit_reset()
        self.saved = self.current()

    def save(self):
        texts, script = self.current()
        try:
            save_texts(texts)
            save_script(script)
        except OSError as e:
            messagebox.showerror("保存失敗", str(e))
            return False
        self.saved = self.current()
        self.status.config(text="保存しました")
        self.after(3000, lambda: self.status.config(text=""))
        return True

    def on_close(self):
        if self.building:
            messagebox.showinfo("ビルド中", "ビルドが終わるまでお待ちください。")
            return
        if self.current() != self.saved:
            answer = messagebox.askyesnocancel("未保存", "変更を保存しますか？")
            if answer is None or (answer and not self.save()):
                return
        self.destroy()

    # ---- script check ----

    def on_check(self):
        if not self.save():
            return
        errors = check_script()
        if errors:
            messagebox.showerror("構文エラー", errors)
        else:
            messagebox.showinfo("構文チェック", "エラーはありません。")

    # ---- build ----

    def refresh_dist(self):
        names = dist_folders()
        self.dist["values"] = names
        if names and self.dist.get() not in names:
            self.dist.set(names[-1])
        version = app_version()
        self.version_label.config(text=f"バージョン {version} → GazeFace-Setup-{version}.exe")

    def browse_driver(self):
        path = filedialog.askdirectory(title="driver\\gfmouse フォルダを選択",
                                       initialdir=self.driver.get() or str(ROOT))
        if path:
            self.driver.set(str(Path(path)))

    def on_build(self):
        dist = self.dist.get()
        if not dist:
            messagebox.showerror("ビルド", "dist フォルダが見つかりません（GazeFace\\GazeFace.exe を含むフォルダ）。")
            return
        driver = self.driver.get().strip()
        if not (Path(driver) / "gfmouse.inf").exists():
            messagebox.showerror("ビルド", "ドライバー フォルダ（gfmouse.inf を含む driver\\gfmouse）を指定してください。")
            return
        if not self.save():
            return
        errors = check_script()
        if errors:
            messagebox.showerror("構文エラー", "完全版スクリプトを直してください。\n\n" + errors)
            return
        self.building = True
        self.build_button.config(state="disabled")
        self.set_log("")
        threading.Thread(target=self.run_build, args=(dist, driver), daemon=True).start()

    def run_build(self, dist, driver):
        cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(BUILD), "-Dist", dist, "-Driver", driver]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 encoding="oem", errors="replace", creationflags=NO_WINDOW)
            for line in p.stdout:
                self.log_queue.put(line)
            code = p.wait()
        except OSError as e:
            self.log_queue.put(f"{e}\n")
            code = -1
        self.log_queue.put(("done", code))

    def drain_log(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple):
                    self.build_done(item[1])
                else:
                    self.append_log(item)
        except queue.Empty:
            pass
        self.after(100, self.drain_log)

    def build_done(self, code):
        self.building = False
        self.build_button.config(state="normal")
        if code == 0:
            self.append_log("\n=== ビルド成功 ===\n")
            messagebox.showinfo("ビルド", "インストーラーを作成しました。")
        else:
            self.append_log(f"\n=== ビルド失敗 (code {code}) ===\n")
            messagebox.showerror("ビルド", "ビルドに失敗しました。ログを確認してください。")

    def set_log(self, text):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.config(state="disabled")

    def append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")


if __name__ == "__main__":
    Builder().mainloop()
