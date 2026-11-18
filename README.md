<p align="center"><img src="assets/images/wordmark.png" alt="GazeFace" height="56"></p>

GazeFace is a free, open-source app that lets you control a Windows PC with nothing but a webcam. Move your head to move the pointer, make facial gestures (open your mouth, raise an eyebrow, close an eye, ...) to click or press keys, use your gaze to jump the pointer across the screen, and pick functions from full-screen tile grids by looking at them.

It is built for people who find a mouse and keyboard hard or impossible to use (quadriplegia, spinal cord injury, ALS, muscular dystrophy and more), so that a face alone is enough to work a computer without help.

*日本語: [README.ja.md](README.ja.md)*

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Full edition and standard edition](#full-edition-and-standard-edition)
- [How the full edition signs itself](#how-the-full-edition-signs-itself)
- [Signing it yourself](#signing-it-yourself)
- [Getting started](#getting-started)
- [Feature manual](#feature-manual)
- [FAQ](#faq)
- [Privacy](#privacy)
- [Building from source](#building-from-source)
- [Credits](#credits)
- [License](#license)

## Features

- **Head tracking** — the pointer follows your head, with a separate speed for each direction
- **Facial gestures** — 23 expressions you can bind to left / right / middle click, double click, press-and-hold (drag), and keys or shortcuts
- **Gaze Jump** — moves the pointer to where you are looking in one go, and snaps onto the nearest link or button
- **Gaze move** — look at an on-screen arrow to glide the pointer that way
- **Head-turn wheel mode** — scroll by turning or nodding your head
- **Gaze launcher** — a full-screen icon grid you pick from by looking, to run keys, macros, apps and more
- **Gaze task switcher / gaze clipboard history** — switch windows and paste saved text by gaze
- **Sign-in screen and UAC prompts** (full edition) — registers as a Windows assistive technology so it works before you sign in and on administrator prompts
- **Profiles** — separate settings for different people or situations
- **Auto recovery and updates** — recovers by itself when something goes wrong, and updates with nothing but face control

## Requirements

Minimum:

- Windows 10 / 11 (64-bit)
- CPU: 4 cores (Intel Core i5 8th gen / AMD Ryzen 5 3000 series or better)
- Memory: 8 GB
- Storage: 1 GB free
- GPU: integrated graphics with Direct3D 11 (Intel UHD 620 / AMD Radeon Vega or better)
- Webcam: 720p, 30 fps

Recommended:

- CPU: 6 cores or more
- Memory: 16 GB
- A webcam that works well in your lighting

Face detection runs on the CPU and keeps about one CPU thread busy. The GPU is used only to draw the on-screen overlays.

## Installation

Download `GazeFace-Setup-<version>.exe` from [Releases](../../releases) and run it. A direct link that always points to the latest version: [GazeFace-Setup.exe](https://github.com/dNv72DgH/GazeFace/releases/latest/download/GazeFace-Setup.exe). GazeFace is installed under `C:\Program Files\GazeFace` and can be started from the Start menu. On first launch you are guided through a gaze calibration.

During setup you choose the **full edition** or the **standard edition** (see the next section). With the full edition you can then choose "Start the lite edition on the sign-in screen" and "Start after sign-in".

The installer itself is not code-signed, so Windows SmartScreen may warn you. Choose "More info" → "Run anyway" to continue.

If Windows blocks the installer without offering "Run anyway", Smart App Control is probably on. You can check it under Windows Security → App & browser control → Smart App Control settings. Turning it off lets unsigned apps such as GazeFace run, but it cannot be turned back on without reinstalling Windows. GazeFace cannot be installed in Windows S mode.

### Updates

While it runs, GazeFace checks for a new version once a day and offers it inside its window. Nothing is installed until you choose "Update now"; the update then installs without further prompts and GazeFace restarts by itself. No administrator (UAC) prompt appears, so face control alone is enough: a task registered at setup runs the update after checking the SHA-256 digest of the file downloaded from the GitHub release. To turn the notice off, use Advanced → Updates → "Notify me of updates".

### Uninstalling

Use Settings → Apps. The certificate and private key made for the full edition, the virtual mouse driver and the update task are removed too. Settings and logs (`%LOCALAPPDATA%\GazeFace`) are kept.

## Full edition and standard edition

**Full edition** (selected by default in the installer)

- Works in windows running as administrator (Task Manager, installers, ...).
- Works on User Account Control (UAC) prompts, the sign-in screen and the lock screen (it registers as a Windows assistive technology, and a dedicated lite edition starts on those screens).
- Uses its own virtual mouse driver to bring back the pointer when Windows keeps it hidden after you sign in.
- Lets you do almost everything without help.
- To do this, it creates a certificate valid only on your PC and signs GazeFace with it (next section).

**Standard edition**

- Runs with normal permissions. No certificate is added, nothing is signed, and no driver is installed.
- Cannot operate windows running as administrator, UAC prompts, or the sign-in and lock screens. While one of these is in front, the pointer or clicks stop working, so help from family or a caregiver will be needed.
- Cannot start with Windows (at sign-in / after sign-in).

Advanced → "About GazeFace" shows which edition is running (Full Edition / Standard Edition). Run the installer again to switch editions at any time. Switching to the standard edition removes the full edition's certificate, private key and driver.

### What the virtual mouse driver is for

Right after you sign in, and at some other times, Windows can keep the mouse pointer hidden until a mouse is physically moved. Mouse input that GazeFace sends the normal way (Windows `SendInput`) does not count as "a mouse moved" here, so the pointer stays hidden. Touching the mouse by hand brings it back, but someone who operates the PC with their face alone cannot do that and is left without a visible pointer.

The full edition installs a virtual mouse driver (GazeFace Virtual Mouse) that Windows sees as a real mouse. Only when GazeFace detects that the pointer is being hidden does it send a tiny movement from this virtual mouse, which makes the pointer appear again.

- That is all it is used for. Moving the pointer, clicking, the wheel and everything else go the normal way, not through the virtual mouse.
- Each wake is "move 1 pixel aside and back", so the pointer ends where it was, and it is sent only once while the pointer stays hidden.
- The driver has no way to press a button, so it cannot click.
- It is a user-mode driver (UMDF) and does not run in the core of Windows (the kernel), so a fault in it is unlikely to take Windows down with it. Kernel drivers need an even more expensive certificate and a review by Microsoft; a user-mode driver can be signed with the full edition's certificate.
- Without the driver (for example in the standard edition) nothing happens and everything else works the same.

## How the full edition signs itself

### Why signing is needed

Windows does not let ordinary apps send mouse or keyboard input to windows running with higher privileges (such as an app run as administrator). Only apps holding **UIAccess**, a special permission reserved for assistive technology, can. Windows grants UIAccess only when all of these hold:

1. The executable asks for it (in GazeFace, `GazeFace_UI.exe`).
2. The executable is **digitally signed with a certificate the PC trusts**.
3. It is installed in a protected location such as `Program Files`.

Starting as an assistive technology on the sign-in screen and UAC prompts also needs the signed `GazeFace_UI.exe`, and Windows refuses to install the virtual mouse driver unless it is signed.

### Why it is not distributed already signed

Usually the developer signs the app with a code signing certificate bought from a certificate authority. However:

- A code signing certificate costs tens of thousands of yen (hundreds of US dollars) or more every year, and drivers need an even more expensive kind.
- GazeFace is free and earns nothing.
- The need to control a PC with only the face is certain, but the number of people who need it is expected to be very small, so paying for an expensive certificate every year is not feasible for now.

So the full edition has the installer create **a certificate that is valid only on that PC**, and sign GazeFace on that PC.

### What the installer does

When you choose the full edition, an elevated PowerShell script (`installer/custom/full_edition.ps1`) runs after installation. Its progress is shown in a console window.

1. It creates a self-signed code signing certificate named `CN=GazeFace Local Signing` (valid for 10 years).
2. The private key is stored in the PC's "Local Computer" certificate store as **non-exportable**. Only administrators and SYSTEM can use it.
3. The certificate (its public part) is added to "Trusted Root Certification Authorities" and "Trusted Publishers".
4. It signs `GazeFace_UI.exe` and the virtual mouse driver's catalog file (`driver\gfmouse.cat`), and checks that the signatures are valid.
5. It installs the virtual mouse driver.
6. On later installs (updates) it reuses the same certificate as long as it is usable. It is recreated only when it expires within 30 days or the certificate or key is damaged.

Why the private key stays on the PC: some Windows versions tell desktop apps apart by their signer when managing camera permission. If every update were signed with a new certificate, Windows would treat it as a different app and ask for camera permission again. The camera does not work until that is allowed, and the prompt cannot be answered with face control, so updates must be signed with the same key.

Uninstalling, or switching to the standard edition, removes this certificate everywhere (root, trusted publishers, and the copy holding the private key).

Signing is done only by the published scripts; nothing is downloaded from outside the installer. You can read them in [installer/custom/full_edition.ps1](installer/custom/full_edition.ps1) and [installer/GazeFace.iss](installer/GazeFace.iss).

### Risks

Before choosing the full edition, understand the following.

- **It changes how your PC decides what to trust.** Adding a certificate to "Trusted Root Certification Authorities" means the PC trusts programs signed with it.
- **The private key stays on the PC.** It cannot be exported and only administrators and SYSTEM can use it, but malware that gains administrator rights could use the key to sign itself and pass as a trusted program, or as a driver that installs without a prompt, on this PC. (Note that once administrator rights are taken, the PC is already compromised.)
- **An app holding UIAccess can send input to windows running as administrator.** GazeFace itself runs with that permission.
- **Beware of fake installers.** A tampered installer could use the same mechanism to make your PC trust a malicious program. Always get the installer from the [official releases](../../releases).

Choose the full edition only if you understand these risks and accept them as your own responsibility. If you are uneasy, use the standard edition, or sign it yourself as described next.

## Signing it yourself

If you are uneasy about automatic signing, install the **standard edition** and sign `GazeFace_UI.exe` yourself. You decide the certificate's name and how it is kept, and you can delete the private key after signing so that it does not stay on the PC.

When a signed and trusted `GazeFace_UI.exe` is in `Program Files`, GazeFace uses it automatically from the next start (and Advanced → Startup becomes available).

### Steps

1. Install with the **standard edition** selected.
2. Quit GazeFace (a running file cannot be signed).
3. Right-click "PowerShell" in the Start menu → "Run as administrator", and run:

```powershell
# 1. Create a self-signed code signing certificate (any name except "CN=GazeFace Local Signing")
$name = 'CN=GazeFace Manual Signing'
$cert = New-SelfSignedCertificate -Subject $name -Type CodeSigningCert `
  -CertStoreLocation Cert:\LocalMachine\My -KeyExportPolicy NonExportable `
  -NotAfter (Get-Date).AddYears(10)

# 2. Add the certificate (public part) to this PC's Trusted Root Certification Authorities
$cer = Join-Path $env:TEMP 'gazeface_manual.cer'
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

# 3. Sign GazeFace_UI.exe and check that the status is Valid
$exe = 'C:\Program Files\GazeFace\GazeFace_UI.exe'
Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256
(Get-AuthenticodeSignature $exe).Status
```

4. If you do not want the private key to stay on the PC, run this next (the public part stays in the root store, so the signed `GazeFace_UI.exe` remains trusted):

```powershell
Remove-Item -LiteralPath ('Cert:\LocalMachine\My\' + $cert.Thumbprint) -DeleteKey
Remove-Item $cer
```

5. Start GazeFace. If Advanced → "About GazeFace" shows **Full Edition**, it worked.

Do not name the certificate `CN=GazeFace Local Signing`: the installer removes certificates with that name when it installs or updates the standard edition.

### Notes on signing it yourself

- **You have to sign again after every update.** An update replaces `GazeFace_UI.exe` with an unsigned file, so GazeFace runs as the standard edition until you sign it again (face control keeps working as usual).
- If you deleted the private key, you will make a new certificate next time. The signer then changes, so some Windows versions ask for camera permission again. That prompt cannot be answered with face control, so do it when a helper is around.
- Starting on the sign-in screen and UAC prompts is set under Advanced → Startup once it is signed.
- The virtual mouse driver (which brings the pointer back when it disappears after sign-in) is not installed by signing it yourself. If you need it, run the following in the same PowerShell **before** deleting the private key in step 4:

```powershell
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
Set-AuthenticodeSignature -FilePath 'C:\Program Files\GazeFace\driver\gfmouse.cat' -Certificate $cert -HashAlgorithm SHA256
& 'C:\Program Files\GazeFace\driver\gfmouse_setup.ps1'
```

  If running the script is blocked, first run `Set-ExecutionPolicy -Scope Process Bypass` (it applies only to that PowerShell window).

### Removing a certificate you added yourself

```powershell
$name = 'CN=GazeFace Manual Signing'
foreach ($s in 'Root', 'TrustedPublisher') {
  Get-ChildItem "Cert:\LocalMachine\$s" | Where-Object Subject -eq $name | Remove-Item
}
# Only if you kept the private key (removes it with the certificate)
Get-ChildItem Cert:\LocalMachine\My | Where-Object Subject -eq $name |
  ForEach-Object { Remove-Item -LiteralPath ('Cert:\LocalMachine\My\' + $_.Thumbprint) -DeleteKey }
```

If you installed the driver, remove it with `powershell -ExecutionPolicy Bypass -File "C:\Program Files\GazeFace\driver\gfmouse_setup.ps1" -Remove`.

## Getting started

1. Connect a webcam and place it where it sees your face from the front.
2. Start GazeFace. The first time, a gaze calibration starts: look at each green dot in turn.
3. Turn on **Face control** at the bottom left. The pointer now follows your head, and gestures click.
4. On the Mouse and Keyboard pages, bind gestures to the functions you use and adjust how strongly they respond.
5. Set up the launcher and the other tools under Extensions as needed.

## Feature manual

The menu on the left has Home, Cursor, Mouse, Keyboard, Extensions, Bookmarks and Advanced, plus Gaze calibration and Restart.

### Common panel (left)

- **Camera image and status** — the face detection state (enabled, preparing, searching, disabled, error, recovering, ...).
- **Face control** — turns all face control on or off. While it is off, neither pointer movement nor gestures work.
- **Sub-monitor** — a small always-on-top window that shows the live value of each bound gesture as a bar, so you can check how strongly you are making a gesture while you work.
- **Restart** — restarts the app.

### Home

- **Select a camera** — choose the webcam and check its image.
- **Profiles** — sets of settings for different people or situations, switched with a click. "Manage profiles" adds, renames and deletes them. Bindings, speeds, sensitivities and so on are saved per profile.

### Cursor (head tracking)

- **Move up / down / right / left** — pointer speed for head movement in each direction (0–1000). 0 means the pointer does not move that way. Too high a speed makes the pointer uncontrollable, so change it in small steps.
- **Smooth pointer** — larger values move the pointer more smoothly, at the cost of more delay.
- **Hold trigger delay (ms)** — after the first click, keeping the gesture up for this long turns it into a held press (drag).

### Mouse

For each function you set the gesture, the threshold (a slider; its bar shows the live value), "Gesture options", "Action options", and a bookmark (☆).

Functions you can bind:

- **Left click / Right click / Middle click / Double click**
- **Pause / unpause** — pauses and resumes face control.
- **Gaze Jump** — moves the pointer near where you are looking (below).
- **Gaze move** — glides the pointer toward the arrow you look at (below).
- **Head-turn wheel mode (up/down, left/right) / (left/right, up/down)** — scroll with head movement (below).
- **Gaze launcher / Gaze clipboard history / Gaze task switcher** — open the extensions.
- **Reset cursor to center**
- **Switch focus between monitors** — moves the pointer to another monitor.

#### Gaze Jump

Moves the pointer near where you are looking. Webcam gaze detection has limited accuracy, so it only takes you to the rough area; together with Gaze move or head tracking it gets you there precisely and quickly. A gaze calibration is needed first.

Action options:

- **Dwell time (ms)** — how long after the gesture the jump happens. The pointer is held still meanwhile.
- **Link snap** (on by default) — if a button or link is near where the jump lands, the pointer goes onto it. **Range (px)** sets how far to look. In some apps and sites this does not work or picks the wrong thing.
- **Visual cue** (off by default) — colours the landing animation when the jump snaps onto a button or link.
- **Start Gaze move right after the jump** — opens the Gaze move arrows right after the jump, so you can glide the rest of the way (recommended on).
- **Allow Gaze Jump even when Face control is off**

#### Gaze move

Shows 8 direction arrows and a centre target, and highlights the arrow you look toward. When the display time is up, the pointer moves straight that way (looking at the centre cancels). While it moves, the Gaze move gesture again stops the pointer, and any other gesture immediately runs its own action.

Action options: **Arrow display time (ms)**, **Move speed (px/s)**.

#### Head-turn wheel mode

Instead of moving the pointer, head movement turns the mouse wheel. Where your head is when the mode starts is the origin: while the head is away from it the wheel keeps turning, and the further away, the faster. An icon next to the pointer shows that the mode is on. The same gesture again ends it; any other gesture ends it and immediately runs its own action. Turning face control off also ends it.

- **Up/down, left/right** — nodding scrolls vertically, turning the head scrolls horizontally
- **Left/right, up/down** — turning the head scrolls vertically, nodding scrolls horizontally

Action options (separately for nodding and for turning):

- **Dead stroke (0.1%)** — head movement within this range of the origin does not scroll (0 = no play).
- **Scroll amount** — wheel delta per input (120 = one notch).
- **Rotation speed (per 10 s)** — the slowest rate, just outside the dead stroke.
- **Acceleration by distance** — how much faster it gets further from the origin (0 = always the same rate).
- **Invert / Disable**

#### Gesture options (per gesture)

These belong to the gesture and carry over if you bind it to a different function.

- **Sensitivity (per-gesture)** — a multiplier on top of the overall sensitivity (1.0 = no change).
- **Hold time (ms)** — how long the value must stay above the threshold before it fires. If short noise-like inputs repeat because of the lighting, about 200 ms suppresses them.
- **Response curve** — linear / slow start, fast end (weak gestures barely register, strong ones respond a lot) / fast start, slow end (even a weak gesture responds strongly).
- **Slider upper / lower limit** — for when the slider barely moves, or when the response curve is "fast start, slow end".
- **Freeze pointer on gesture** — holds the pointer still when the value rises quickly, so making the gesture does not drag the pointer off target. With **Freeze sensitivity** and **Ignore blinks / involuntary movements** (a grace time). It releases automatically after 10 s as a safety.
- **Block simultaneous input** — while this gesture is moving, gestures with a lower priority are ignored (**Movement sensitivity**, **Priority**).
- **Disable on strong reaction** — keeps a gesture from misfiring when you laugh suddenly and the like (**Sensitivity**, **Disabled time (s)**).
- **Chattering prevention** — stops a click or other input from being entered several times in a row by accident. After any input, this gesture's clicks and key presses are ignored for the **Interval (ms)** (recommended on).

### Keyboard

"+ Add binding" pairs a gesture with a key. Tick modifiers (Ctrl, Shift, Alt, Win) and press the main key to bind a shortcut such as Ctrl+C. Threshold and gesture options work as on the Mouse page.

### Extensions

Tools that show a full-screen tile grid you pick from by gaze; gestures confirm, go back, exit and so on. The gestures used while a tool is open (**Gestures**) are set separately from the main bindings, choosing from gestures already bound in the main menu.

Common options:

- **Rows / Columns** — grid size.
- **Dwell confirm (ms)** — keep looking at the selected tile to run it without a gesture (0 = by gesture only).
- **Ignore blinks (ms)**
- **Show page buttons**
- **Color settings** — background, highlight and other colours, and background transparency (shared by the three tools).

#### Gaze launcher

Holds the pointer where it is and shows a grid of icons; the item you pick by gaze runs. Items can change with the app in front (**App-specific item**: common items apply to every app, and an app-specific item takes over when that app is in front).

Gestures: **Confirm / Back / Exit / Settings** (Settings opens the editor for the cell you are looking at).

Item types:

- **Key / Mouse / Macro** — key and mouse operations, one per line, run from the top. The buttons on the right and the presets (copy, paste, paste plain, undo, find, save, maximize, minimize, close window, screenshot, wheel, head-turn wheel mode, drag, long press, swipe, ...) add lines.
  - `key ctrl+c` — press keys (+ for together, e.g. `ctrl+shift+s`, `win+up`, `alt+f4`, `enter`, `esc`, `f5`)
  - `text Hello` — type text (pasted through the clipboard, so its content is replaced)
  - `wait 500` — wait this many milliseconds
  - `mouse left / right / middle / double` — click where the pointer is
  - `wheel up 3` — scroll (up / down / left / right, and how many notches)
  - `drag left` — keep the button pressed; the next click lets go
  - `hold left 800` — press and hold in place for this many milliseconds
  - `swipe ↑ medium` — press, move that way, let go, and return to the start (small / medium / large = 10 / 25 / 45% of the screen height, or a number of pixels)
  - `wheelmode vh / hv` — start the head-turn wheel mode
- **Open file** — opens an app, document, folder and so on (arguments optional).
- **Sub-launcher** — opens another page (a nested launcher).
- **Extension** — opens the clipboard history or the task switcher.
- **Gaze calibration / Sub-monitor toggle / Back**

Icons can be automatic, chosen from the library, or taken from an image file. **Manage / reorder** offers multi-select, swap, insert, insert blank and delete. The launcher's options can also **open in a separate window**.

#### Gaze clipboard history

Records copied text; pick an entry by gaze to paste it into the app in front. Entries you use often can be pinned.

- Options: **Record clipboard (start monitoring)**, **Clear history on app start**, **Max entries (0 = unlimited)**, **Clear now**, **Manage / pin** (search, pin / unpin, delete), **Show pinned-list cell**
- Gestures: Confirm (paste) / Forward / Back / Pin / Switch (history ⇔ pinned) / Delete / Exit

#### Gaze task switcher

Shows the open windows as a grid with their icons; switch to one by gaze (Alt+Tab by gaze).

- Options: **Move the pointer to the switched window** (off by default; moves it to the window's centre), **Hide tasks** (leave windows out by process name or window title; titles match exactly, with `*` and `?` wildcards; you can pick from running processes)
- Gestures: Confirm (switch) / Forward / Back / Close (closes the selected window) / Exit

### Bookmarks

Collects only the functions you starred (☆) on the Mouse and Extensions pages, so the settings you adjust most are in one place.

### Gaze calibration

Run it from "Gaze calibration" in the left menu. Sixteen green dots (or eight, see below) appear one after another; look at each with a neutral expression. Each dot stays for 2 seconds and shrinks as it goes. Esc cancels.

With **Advanced → Calibration → Number of points → 8 points**, the run takes about 20 seconds instead of about 36. Every other dot of the 4×4 grid is used, in a checkerboard pattern. In tests on real calibration data its accuracy was about the same as with 16 points, but with fewer dots a single dot you did not look at properly (a blink or a glance away) affects the result more. If the 8-point result seems off, run it again or use 16 points. Recalibrate whenever the gaze seems off, and after moving your seat or the camera.

### Advanced

- **Gesture sensitivity → Overall sensitivity** — a multiplier on every gesture value. Raise it if even strong expressions do not respond; lower it if slight movements cause accidental clicks.
- **Sub-monitor** — opacity and size.
- **Startup** (full edition)
  - **At sign-in (starts on the sign-in screen etc.)** — starts an ultra-light edition on the Windows sign-in screen, meant for typing with the on-screen keyboard. Only mouse operation, Gaze Jump and Gaze move are available, and their animations are not drawn. A band at the top of the screen shows "Face control on / off" or "Face not detected".
  - **After sign-in (starts on the desktop, UAC prompts etc.)** — starts automatically after you sign in. On UAC prompts the lite edition starts so you can answer them.
  - If Windows asks for permission when you change these, choose "Yes".
- **Startup and window** — **Page at startup** (Home / last page used), **Allow multiple instances** (normally not needed; instances fight over the camera and become unstable).
- **Calibration** — **Confirm before running**, **Number of points** (16 points / 8 points, about 20 s).
- **Auto recovery** — when an error occurs, GazeFace tries to restart itself so you are not left unable to operate the PC. If the settings are broken, it restores an earlier backup (made automatically every day) and restarts. While face control is on, if the camera signal is lost or no face is found for the **Threshold (seconds)**, it reopens the camera and rebuilds face detection.
- **Language** — English / Japanese (after a restart).
- **Updates → Notify me of updates**
- **About GazeFace** — version, edition (Full Edition / Standard Edition) and other details.

### Quitting

Closing the window shows a confirmation; choose "Yes" to quit. Closing the window by accident does not quit at once.

## FAQ

**Q. Can I control the PC with my eyes alone?**
A. Gaze is used to bring the pointer to the rough area (Gaze Jump, Gaze move, picking tiles in the grids). Fine positioning is done with head tracking, and clicks with facial gestures.

**Q. Do I need a special camera or an eye tracker?**
A. No. An ordinary webcam (720p, 30 fps or better) is enough.

**Q. My face is not detected.**
A. Place the camera so it sees your face from the front, and avoid a very dark room, backlight or very bright light. The camera cannot be used while another app is using it.

**Q. I cannot operate administrator apps or UAC prompts.**
A. That is a limit of the standard edition. Install the full edition, or sign it yourself.

**Q. After an update, Windows asked for camera permission.**
A. Some Windows versions ask again when the signer changes. The full edition reuses the same certificate, so this normally does not happen; it can happen when you sign the standard edition yourself.

**Q. Does it cost anything?**
A. No. It is free and open source under the Apache License 2.0.

**Q. Is it a medical device?**
A. No. It is not intended for medical use.

## Privacy

- Camera images are processed on your PC only. They are never stored or sent anywhere.
- GazeFace detects facial landmarks (the positions of the eyes, nose, mouth, ...). It does not recognise or identify faces.
- Settings and logs are kept in `%LOCALAPPDATA%\GazeFace`. The sign-in screen's lite edition keeps its settings in `C:\ProgramData\GazeFace\signin`.
- The only network access is checking for and downloading updates (GitHub).

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

I am quadriplegic. Without [Project Gameface](https://github.com/google/project-gameface), I could not have kept working. My heartfelt thanks to Google and everyone who built Project Gameface and released it as open source. GazeFace grew out of that work, in the hope of passing the same chance on to others.

- [Project Gameface](https://github.com/google/project-gameface) (Google LLC) — GazeFace is a modified version of this project
- [MediaPipe Face Landmarker](https://developers.google.com/mediapipe/solutions/vision/face_landmarker) — face landmark and expression detection
- [IBM Plex](https://github.com/IBM/plex) — fonts

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for the modifications and bundled components. The fonts are under the SIL Open Font License 1.1 ([assets/fonts/OFL.txt](assets/fonts/OFL.txt)).
