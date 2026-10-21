# Laugh Clipper

Listens for laughter (your mic and any PC audio you pick) and reminds you to save a
[Medal](https://medal.tv) clip, or saves it for you automatically.

## Install

Download `LaughClipper-Setup-x.y.z.exe` from the [latest release](../../releases/latest) and run it.
It installs just for your Windows user, so admin rights aren't needed. It then:

- adds Laugh Clipper to the Start menu
- runs in the system tray
- can start with Windows
- updates itself: when a new release is out, open the app and click **Update and Restart**

The installer isn't code-signed, so the first time you run it Windows SmartScreen shows
"Windows protected your PC". Click **More info → Run anyway**.

## Releasing a new version

1. Commit your changes.
2. Tag the commit and push the tag:
   ```
   git tag v1.1.0
   git push origin v1.1.0
   ```
3. GitHub Actions builds the exe and installer and publishes them as a release. The installed
   app sees the new version within a few hours, or straight away with **Check for Updates**.

You can also run the workflow manually from the **Actions** tab to get a test build
(download it from the run's artifacts).

## Running from source

```
py -3.12 -m pip install -r requirements.txt
py -3.12 main.py
```

`build_local.bat` builds `dist\LaughClipper\LaughClipper.exe` on your own PC. A local build has
no installer and doesn't auto-update.

## Files

| Path | What it is |
|---|---|
| `laughclipper/engine.py` | Audio capture and laugh detection (YAMNet via MediaPipe) |
| `laughclipper/app.py` | Tray, hotkeys, popups, clipping logic, updates |
| `laughclipper/toast.py` | The animated popup |
| `laughclipper/settings_window.py`, `widgets.py`, `theme.py` | Settings window |
| `laughclipper/updater.py` | Checks GitHub Releases and installs updates |
| `LaughClipper.spec` | PyInstaller build |
| `installer/LaughClipper.iss` | Inno Setup installer |
| `.github/workflows/release.yml` | Builds and publishes releases |

Settings and logs live in `%APPDATA%\Laugh Clipper`.
