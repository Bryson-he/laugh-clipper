# PyInstaller build file:  pyinstaller LaughClipper.spec --noconfirm
from PyInstaller.utils.hooks import collect_all

datas = [
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
    ("models/yamnet.tflite", "models"),
    ("models/vosk", "models/vosk"),           # speech model for the "don't clip" voice command
    ("release-notes", "release-notes"),      # for "What's new" after an update
]
binaries = []
hiddenimports = ["keyboard"]

# mediapipe and numpy ship native libraries + data files; soundcard reads its .h definitions at runtime
# vosk ships its own DLLs (libvosk + the MinGW runtime) next to the module
for pkg in ("numpy", "mediapipe", "soundcard", "vosk"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LaughClipper",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LaughClipper")
