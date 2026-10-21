<p align="center"><img src="assets/images/wordmark.png" alt="GazeFace" height="56"></p>

GazeFace lets you control a Windows PC with nothing but a webcam. Move your head to move the pointer, make facial gestures (open your mouth, raise an eyebrow, close an eye, ...) to click or press keys, and use your gaze to jump the pointer across the screen.

It is built for people who find a mouse and keyboard hard or impossible to use, so that a face alone is enough to work a computer.

*日本語: [README.ja.md](README.ja.md)*

## Features

- **Head tracking** — the pointer follows your head movement
- **Facial gestures** — left / right / middle click, double click, press-and-hold (drag), and keys or shortcuts, each bound to a gesture of your choice
- **Gaze Jump** — moves the pointer to where you are looking; can snap onto the nearest link or button
- **Gaze move** — look at an on-screen arrow to glide the pointer that way
- **Gaze launcher / task switcher / clipboard history** — full-screen tile grids you pick from by looking
- **Sign-in screen and UAC prompts** — registers as a Windows assistive technology and starts an ultra-light mode that works before you sign in and on administrator prompts
- **Profiles** — separate settings for different people or situations

## Requirements

- Windows 10 / 11 (64-bit)
- A webcam

## Installation

Download `GazeFace-Setup-<version>.exe` from [Releases](../../releases) and run it. GazeFace is installed under `C:\Program Files\GazeFace` and can be started from the Start menu. On first launch you are guided through a gaze calibration.

The installer is not code-signed yet, so Windows SmartScreen may warn you. Choose "More info" → "Run anyway" to continue.

If Windows blocks the installer without offering "Run anyway", Smart App Control is probably on. You can check it under Windows Security → App & browser control → Smart App Control settings. Turning it off lets unsigned apps such as GazeFace run, but it cannot be turned back on without reinstalling Windows. GazeFace cannot be installed in Windows S mode.

While it runs, GazeFace checks for a new version once a day and offers it inside its window. Nothing is installed until you choose "Update now"; the update then installs without further prompts and GazeFace restarts by itself.

To uninstall, use Settings → Apps.

## Privacy

- Camera images are processed on your PC only. They are never stored or sent anywhere.
- GazeFace detects facial landmarks (the positions of the eyes, nose, mouth, ...). It does not recognise or identify faces.
- Settings and logs are kept in `%LOCALAPPDATA%\GazeFace`.

## Building from source

Use Python 3.12 (64-bit).

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\python run_app.py
```

Build the distributable with PyInstaller:

```bash
.venv\Scripts\pyinstaller build.spec --noconfirm
```

On first launch the settings are seeded from `configs_template`.

## Credits

- [Project Gameface](https://github.com/google/project-gameface) (Google LLC) — GazeFace is a modified version of this project
- [MediaPipe Face Landmarker](https://developers.google.com/mediapipe/solutions/vision/face_landmarker) — face landmark and expression detection
- [IBM Plex](https://github.com/IBM/plex) — fonts

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for the modifications and bundled components. The fonts are under the SIL Open Font License 1.1 ([assets/fonts/OFL.txt](assets/fonts/OFL.txt)).
