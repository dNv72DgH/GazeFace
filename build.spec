# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import mediapipe
import customtkinter
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

mp_init = Path(mediapipe.__file__)
mp_modules =  Path(mp_init.parent,"modules")

ctk_init = Path(customtkinter.__file__)
ctk_modules =  Path(ctk_init.parent,"modules")

# Newer mediapipe (>=0.10.30) ships a `mediapipe.tasks.c` submodule that
# loads native bindings dynamically. PyInstaller doesn't follow the
# importlib.resources.files() call inside, so we have to declare these
# explicitly.
mp_hidden = collect_submodules('mediapipe')
mp_extra_datas = collect_data_files('mediapipe', include_py_files=True)

# Heavy transitive deps that mediapipe declares but the FaceLandmarker
# tasks API never imports at runtime. Excluding them keeps only what's
# needed to launch (verified: the model builds + detects with these
# blocked). jax/jaxlib alone are ~220 MB; scipy ~75 MB.
RUNTIME_EXCLUDES = [
    'jax', 'jaxlib', 'scipy', 'pandas', 'IPython',
    'IPython.lib', 'jupyter', 'notebook', 'pytest', 'nbconvert',
]

# Qt Quick (PySide6) gaze overlay. The overlay runs as a sub-mode of this
# same exe (run_app.py --qt-overlay). PyInstaller's PySide6 hooks pull in
# the Qt libs / platform + qml plugins; we just have to name the QML
# modules we actually use and ship the .qml file as data.
PYSIDE_HIDDEN = [
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickControls2',
    'PySide6.QtNetwork', 'PySide6.QtOpenGL',
]



app = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=[],
    datas=[(mp_modules.as_posix(), 'mediapipe/modules'),
                    ('assets','assets'),
                    # Shown from Advanced > About GazeFace.
                    ('LICENSE', '.'), ('NOTICE', '.'),
                    # Factory defaults only. NOT the repo's configs/ tree:
                    # that is the build machine's own live data (tweaked
                    # profiles, the launcher icon cache keyed to apps
                    # installed here, a recorded clipboard) and none of it
                    # belongs in a build. Settings live in
                    # %LOCALAPPDATA%\GazeFace (see src/paths.py); the name
                    # differs from `configs` so copying a build over an
                    # install can never overwrite the user's real profiles.
                    # paths.ensure_user_data() seeds from an existing
                    # install's configs/ first, and only falls back here.
                    ('configs_template','configs_template'),
                    ('src/gui/qt_overlay/overlay.qml', 'src/gui/qt_overlay'),
                    (ctk_init.parent.as_posix(), 'customtkinter')] + mp_extra_datas,
    # comtypes' generated UI Automation wrapper (src/click_snap.py): a
    # frozen app cannot generate it at run time, so it ships pre-built.
    hiddenimports=mp_hidden + PYSIDE_HIDDEN + [
        'comtypes', 'comtypes.client', 'comtypes.gen',
        'comtypes.gen.UIAutomationClient',
        'comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0',
        'comtypes.gen.stdole',
        'comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=RUNTIME_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz_app = PYZ(app.pure, app.zipped_data, cipher=block_cipher)

exe_app = EXE(
    pyz_app,
    app.scripts,
    [],
    exclude_binaries=True,
    name='GazeFace',
    icon='assets/images/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Flatten layout so assets/ and configs/ sit next to run_app.exe,
    # matching the pre-PyInstaller-6 layout the app's relative paths expect.
    contents_directory='.',
)


# --- UIAccess variant (GazeFace_UI.exe) -------------------------------
# Byte-for-byte the same program as GazeFace.exe; the ONLY difference is
# one bit in the embedded manifest. UIAccess is granted by the loader at
# process creation and cannot be acquired or dropped afterwards, so an
# in-app "use UIAccess" setting can only be implemented as "launch the
# other exe" (see src/ui_access.py and src/restart.py).
#
# Built from the same Analysis, so it costs no extra collected binaries —
# just a second small launcher stub in the same folder.
#
# Windows will REFUSE to start this exe unless it is Authenticode-signed
# AND installed under Program Files. That is why the ordinary exe remains
# the default and the fallback.
exe_app_uiaccess = EXE(
    pyz_app,
    app.scripts,
    [],
    exclude_binaries=True,
    name='GazeFace_UI',
    icon='assets/images/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_uiaccess=True,
    contents_directory='.',
)


# --- crash sentinel (GazeFace_Sub.exe) --------------------------------
# A tiny, separate exe that only watches the main app's pid and relaunches
# it after a hard native crash (see src/sentinel.py). Shipped under its own
# name so it does not appear in Task Manager as a second "GazeFace.exe"
# (which reads like malware). It imports none of the GUI / camera /
# MediaPipe stack, so its Analysis stays small; the shared binaries it does
# need are already collected by the main app in the same folder.
sub = Analysis(
    ['sentinel_main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=RUNTIME_EXCLUDES + PYSIDE_HIDDEN + [
        'customtkinter', 'cv2', 'mediapipe', 'PIL', 'matplotlib',
        'tkinter', 'PySide6',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz_sub = PYZ(sub.pure, sub.zipped_data, cipher=block_cipher)

exe_sub = EXE(
    pyz_sub,
    sub.scripts,
    [],
    exclude_binaries=True,
    name='GazeFace_Sub',
    icon='assets/images/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='.',
)


coll = COLLECT(
    exe_app,
    exe_app_uiaccess,
    exe_sub,
    app.binaries,
    app.zipfiles,
    app.datas,
    sub.binaries,
    sub.zipfiles,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GazeFace',
)
