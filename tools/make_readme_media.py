"""Renders the README's screenshots and popup animations into docs/images.

    .venv\\Scripts\\python -m pip install pillow
    .venv\\Scripts\\python tools\\make_readme_media.py

Everything runs offscreen with made-up devices, games and live scores, and a throwaway settings folder,
so it never touches your real settings, audio or keyboard.
"""

import os
import sys
import tempfile
import threading
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["QT_SCALE_FACTOR"] = "2"                  # render at 2x so it stays sharp on hi-dpi screens
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT))
sys.modules["keyboard"] = types.SimpleNamespace(add_hotkey=lambda *a, **k: 1, remove_hotkey=lambda *a: None,
                                                send=lambda *a: None, unhook_all=lambda: None)

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient, QRegion  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from laughclipper import app as appmod, config, settings_window, theme, toast as toastmod, winutil  # noqa: E402
from laughclipper.config import S  # noqa: E402
from laughclipper.theme import T  # noqa: E402

VERSION = "1.1.0"
MICS = ["Wave:3 Mic (Elgato Wave:3)", "Headset Microphone (Arctis Nova Pro Wireless)"]
SPEAKERS = ["Game (Elgato Virtual Audio)", "Voice Chat (Elgato Virtual Audio)", "Browser (Elgato Virtual Audio)",
            "Music (Elgato Virtual Audio)", "System (Elgato Virtual Audio)"]
GAME = (1, r"D:\SteamLibrary\steamapps\common\rocketleague\Binaries\Win64\RocketLeague.exe", True)

# ---------------------------------------------------------------- a pretend PC
winutil.process_running = lambda exe: True
winutil.foreground_app = lambda: GAME
winutil.exe_description = lambda path: "Rocket League"
winutil.startup_enabled = lambda: True
settings_window.VERSION = VERSION
toastmod.Toast.showEvent = lambda self, e: None

config.load()
S.update({
    "listen_mic": True, "mic_device": "", "outputs": SPEAKERS[:2], "monitor": 1, "position": "bottom-center",
    "hotkey": "f8", "auto_clip": True, "clip_delay": 8, "max_wait": 30, "triggers": ["laughter", "cheering"],
    "threshold": 0.3, "min_laugh": 1.0, "pause_tolerance": 1.5, "game_only": True,
    "games": [{"exe": "rocketleague.exe", "name": "Rocket League"}, {"exe": "valorant.exe", "name": "VALORANT"},
              {"exe": "forzahorizon5.exe", "name": "Forza Horizon 5"}],
})

app = QApplication(sys.argv)
T.init(app)
DPR = app.devicePixelRatio()
ctrl = appmod.Controller(app)
ctrl.update_state = ("uptodate", {})
eng = ctrl.engine
eng._listen = lambda *a: None
LIVE = {("mic", ""): (0.64, "laughter", "Laughter"),
        ("out", SPEAKERS[0]): (0.06, "laughter", "Vehicle"),
        ("out", SPEAKERS[1]): (0.38, "laughter", "Giggle")}
for key, (level, sound, heard) in LIVE.items():
    eng.threads[key] = threading.Event()
    eng.levels[key], eng.sound[key], eng.heard[key] = level, (sound, level), (heard, 0.05)
ctrl.check_game()
S["sound_levels"] = {"cheering": 0.4}
S["sound_sources"] = {"cheering": ["mic", f"out:{SPEAKERS[1]}"]}

# a few clips for the History page ("Show" only appears for files that exist)
CLIPS = Path(os.environ["APPDATA"]) / "Clips" / "Rocket League"
CLIPS.mkdir(parents=True)
now = datetime.now().replace(second=0)
ctrl.history = []
for minutes, sound, source, game, score, auto, status in [
        (2 * 1440 + 200, "laughter", "Your mic", "Minecraft", 0.44, False, "saved"),
        (1440 + 65, "screaming", "Game", "", 0.38, True, "failed"),
        (52, "cheering", "Voice Chat", "Rocket League", 0.71, True, "saved"),
        (3, "laughter", "Voice Chat", "Rocket League", 0.62, True, "saved")]:
    f = CLIPS / f"clip{minutes}.mp4"
    f.write_bytes(b"")
    ctrl.history.append({"time": (now - timedelta(minutes=minutes)).isoformat(timespec="seconds"), "sound": sound,
                         "source": source, "game": game, "score": score, "auto": auto, "status": status,
                         "file": str(f) if status == "saved" else ""})


# ---------------------------------------------------------------- helpers
def to_pil(img):
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA",
                            img.bytesPerLine(), 1).copy()


def canvas(w, h):
    img = QImage(int(w * DPR), int(h * DPR), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(DPR)
    img.fill(Qt.GlobalColor.transparent)
    return img


def soft_shadow(p, rect, radius, strength=1.0):
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(12):
        sp = 1 + i * 2.4
        p.setBrush(QColor(0, 0, 0, int(9 * (1 - i / 12) ** 2 * strength) + 1))
        p.drawRoundedRect(rect.adjusted(-sp, -sp + 6, sp, sp + 8), radius + sp, radius + sp)


def framed(shot, pad=36):
    """A window screenshot with rounded corners and a soft shadow, on a transparent background."""
    w, h = shot.width() / DPR, shot.height() / DPR
    img = canvas(w + 2 * pad, h + 2 * pad)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(pad, pad, w, h)
    soft_shadow(p, r, 12)
    path = QPainterPath()
    path.addRoundedRect(r, 12, 12)
    p.setClipPath(path)
    p.drawImage(r, shot)
    p.setClipping(False)
    p.setPen(QColor(0, 0, 0, 40) if not T.dark else QColor(255, 255, 255, 30))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()
    return img


def backdrop(p, r):
    """Game-ish backdrop card the popups float on: deep blue to purple, with a couple of soft glows."""
    path = QPainterPath()
    path.addRoundedRect(r, 22, 22)
    p.save()
    p.setClipPath(path)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#16213e"))
    g.setColorAt(0.55, QColor("#2a1b54"))
    g.setColorAt(1, QColor("#4a1d5e"))
    p.fillRect(r, g)
    for cx, cy, rad, col in ((0.18, 0.2, 0.55, "#2f6bff"), (0.85, 0.85, 0.6, "#ff4fa3"), (0.6, 0.1, 0.35, "#ffd60a")):
        glow = QRadialGradient(QPointF(r.left() + cx * r.width(), r.top() + cy * r.height()), rad * r.width())
        c = QColor(col)
        c.setAlpha(70)
        glow.setColorAt(0, c)
        c.setAlpha(0)
        glow.setColorAt(1, c)
        p.fillRect(r, glow)
    p.restore()


def render_toast(p, t, x, y):
    p.save()
    p.translate(x, y)
    t.render(p, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    p.restore()


def make_toast(mode, source, sound, seconds=8):
    t = toastmod.Toast(app.primaryScreen(), "bottom-center", mode, source, sound, "F8", seconds)
    t.clock.restart = lambda: 16
    return t


def run(t, seconds):
    for _ in range(int(seconds / 0.016)):
        t.tick()


def save_png(img, name):
    to_pil(img).save(OUT / name, optimize=True)
    print("wrote", name)


# ---------------------------------------------------------------- settings window
def set_theme(dark):
    T.dark, T.p = dark, (theme.DARK if dark else theme.LIGHT)
    app.setStyleSheet(T.stylesheet())
    for wd in app.allWidgets():
        wd.update()


def window_shots():
    w = settings_window.SettingsWindow(ctrl)
    w.build_devices(MICS, SPEAKERS, SPEAKERS[0])
    more, panel, _ = w.sound_opts["cheering"]          # show off the per-sound options
    more.setChecked(True)
    more.turn.snap(1.0)
    panel.set_open(True, animate=False)
    w.show()
    shots = {}
    for dark in (True, False):
        set_theme(dark)
        w.update_status()
        for i, (title, _, _) in enumerate(settings_window.PAGES):
            w.select(i, animate=False)
            page = w.stack.widget(i)
            h = max(560, page.widget().sizeHint().height())
            w.resize(900, h)
            for _ in range(3):
                w.refresh_live()
                app.processEvents()
            shot = framed(w.grab().toImage())
            name = f"settings-{title.lower()}-{'dark' if dark else 'light'}.png"
            save_png(shot, name)
            shots[name] = shot
    w.hide()
    return shots


# ---------------------------------------------------------------- popups
CARD_W, CARD_H = 560, 196


def popup_frame(t):
    img = canvas(CARD_W, CARD_H)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    backdrop(p, QRectF(0, 0, CARD_W, CARD_H))
    render_toast(p, t, (CARD_W - t.width()) / 2, (CARD_H - t.height()) / 2 + 8)
    p.end()
    return to_pil(img)


def animate(name, t, script, tail=0.7):
    """Step the popup at ~30 fps; `script(time)` drives it. Stops once the popup has closed."""
    frames, now = [], 0.0
    while now < 20:
        script(now)
        t.tick()
        t.tick()
        now += 0.032
        if not t.timer.isActive():
            break
        frames.append(popup_frame(t))
    blank = popup_frame(make_toast("ask", "", "laughter"))      # an empty card while it waits to loop
    frames += [blank] * int(tail / 0.032)
    frames[0].save(OUT / name, save_all=True, append_images=frames[1:], duration=32, loop=0, quality=88, method=6)
    print("wrote", name, f"({len(frames)} frames, {(OUT / name).stat().st_size // 1024} KB)")


def popup_clip():
    """'Laugh detected' springs open, Clip is pressed, it waits for Medal's file, then shows 'Clip saved'."""
    t = make_toast("ask", "Voice Chat", "laughter")
    state = {}

    def script(now):
        if 1.9 <= now < 2.05:
            t.hover = True
            t.scale.target = 1.025
            t.press.target = 1.0
        elif now >= 2.05 and "clicked" not in state:
            state["clicked"] = True
            t.press.target = 0.0
            t.hover = False
            t.set_saving()
        elif now >= 3.0 and "saved" not in state:
            state["saved"] = True
            t.set_saved("Rocket League")
    animate("popup-clip.webp", t, script)


def popup_autosave():
    """Auto-save: waits while the laughing continues, counts down, then saves on its own."""
    total = 3.0
    t = make_toast("pending", "Game", "cheering", total)
    state = {}

    def script(now):
        if now < 2.4:
            t.set_pending(total, True, total, 0.75)
        elif now < 2.4 + total:
            t.set_pending(total - (now - 2.4), False, total, 0.0)
        elif "saving" not in state:
            state["saving"] = True
            t.set_saving()
        elif now >= 2.4 + total + 0.9 and "saved" not in state:
            state["saved"] = True
            t.set_saved("Rocket League")
    animate("popup-autosave.webp", t, script)


def popup_gallery():
    """Still frames of the different popups, stacked on one card."""
    items = [("ask", "Game", "cheering"), ("ask", "Your mic", "screaming"),
             ("pending", "Voice Chat", "laughter"), ("saved", "Game", "laughter"), ("nomedal", "Game", "laughter")]
    gap, top = 84, 18
    h = top * 2 + gap * (len(items) - 1) + 76 + 36
    img = canvas(CARD_W, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    backdrop(p, QRectF(0, 0, CARD_W, h))
    for i, (mode, source, sound) in enumerate(items):
        t = make_toast(mode, source, sound, 8)
        if mode == "saved":
            t.saved_sub = "In Medal  ·  Rocket League"
            t.check.snap(1.0)
            t.close_at = None
        if mode == "pending":
            t.set_pending(5, True, 8, 0.7)
        run(t, 1.4)
        t.left = t.total * 0.62
        render_toast(p, t, (CARD_W - t.width()) / 2, top + i * gap - 34 + 18)
    p.end()
    save_png(img, "popups.png")


def hero(shots):
    """Dark settings window with a popup floating over its bottom edge."""
    win = shots["settings-detection-dark.png"]
    ww, wh = win.width() / DPR, win.height() / DPR
    img = canvas(ww, wh + 70)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.drawImage(QRectF(0, 0, ww, wh), win)
    t = make_toast("ask", "Voice Chat", "laughter")
    run(t, 1.4)
    t.left = t.total * 0.7
    render_toast(p, t, ww - t.width() - 10, wh + 70 - t.height())
    p.end()
    save_png(img, "hero.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    shots = window_shots()
    set_theme(True)
    hero(shots)
    popup_gallery()
    popup_clip()
    popup_autosave()
