"""App controller: tray icon, settings, hotkeys, popups and updates."""

import logging
import os
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import clips, config, medal, sounds, winutil
from .config import S, hotkey_label
from .engine import Engine, short_name
from .theme import T
from .toast import Toast
from .updater import Updater
from .version import VERSION

log = logging.getLogger("laughclipper")
SERVER_NAME = "LaughClipper-single-instance"
MUTEX_NAME = "LaughClipperRunning"
GAME_GRACE = 5               # keep listening this many seconds after you alt-tab out of a game
PRE_ROLL = 10                # seconds before a laugh worth keeping in the clip (whatever was funny)


def badged(pix, state):
    """Tray icon variant: 'paused' = faded and grey; 'waiting' / 'warning' = orange dot bottom-right."""
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(pix.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    w = pix.width() / pix.devicePixelRatio()
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if state == "paused":
        gray = pix.toImage().convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied)
        alpha = pix.toImage().convertToFormat(QImage.Format.Format_Alpha8)
        gray.setAlphaChannel(alpha)
        p.setOpacity(0.55)
        p.drawImage(QRectF(0, 0, w, w), gray)
    else:
        p.drawPixmap(0, 0, pix)
        d = max(5.0, w * 0.42)
        r = QRectF(w - d, w - d, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))                        # dark ring so it shows on any taskbar
        p.drawEllipse(r)
        p.setBrush(QColor("#ff9f0a"))
        p.drawEllipse(r.adjusted(d * 0.14, d * 0.14, -d * 0.14, -d * 0.14))
    p.end()
    return out


def sorted_screens():
    primary = QGuiApplication.primaryScreen()
    screens = list(QGuiApplication.screens())
    screens.sort(key=lambda s: (s is not primary, s.geometry().x(), s.geometry().y()))
    return screens


class DisplayBadge(QWidget):
    """Big number shown on each display by 'Identify'."""

    def __init__(self, number, screen, primary):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.number, self.primary = number, primary
        g = screen.geometry()
        self.setGeometry(g.center().x() - 110, g.center().y() - 110, 220, 220)
        self.setWindowOpacity(0.0)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(220)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        QTimer.singleShot(2200, self.fade_out)

    def showEvent(self, e):
        super().showEvent(e)
        winutil.no_activate(int(self.winId()))
        self.fade.start()

    def fade_out(self):
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.finished.connect(self.close)
        self.fade.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 215))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(4, 4, -4, -4), 44, 44)
        p.setPen(QColor("white"))
        p.setFont(T.font(112, 700, display=True))
        p.drawText(QRectF(0, -10, 220, 220), Qt.AlignmentFlag.AlignCenter, str(self.number))
        if self.primary:
            p.setFont(T.font(14, 600))
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(0, 160, 220, 30), Qt.AlignmentFlag.AlignCenter, "Main display")


class Controller(QObject):
    changed = Signal(str)
    devices_changed = Signal(list, list, str)
    update_changed = Signal(str, dict)
    history_changed = Signal()
    _hotkey_hit = Signal()
    _hotkey_recorded = Signal(str, str)     # which hotkey ("hotkey" / "bookmark_hotkey"), keys

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.icon = QIcon(str(config.resource("assets/icon.png")))
        self.engine = Engine()
        self.engine.laugh.connect(self.on_laugh)
        self.engine.devices.connect(self.on_devices)
        self.devices = None
        self.updater = Updater()
        self.updater.state.connect(self.on_update_state)
        self.update_state = ("idle", {})
        self.window = None
        self.setup = None
        self.calibration = None
        self.whats_new = None
        self.toast = None
        self.toast_is_test = False
        self.pending = None
        self.last_prompt = 0.0
        self.hotkey_handle = None
        self.badges = []
        self.tray_icons = {}         # state -> tray icon, made once
        self.notified_version = None
        self.game = None             # name of the game in front (when "only while playing" is on)
        self.game_seen = 0.0
        self.tuning = False          # our own window is in front, so we listen even without a game
        self.medal_ok = True         # False when Medal isn't running (and we're checking for it)
        self.recording = False       # Medal is recording a session right now
        self.moment = None           # the laugh the popup is about: sound, source, game, peak score
        self.sent_at = 0.0           # when we last sent the hotkey (it echoes back as a key press)
        self.history = clips.load_history()
        self.watcher = clips.ClipWatcher()
        self.watcher.found.connect(self.on_clip_found)
        self.watcher.missing.connect(self.on_clip_missing)

        self._hotkey_hit.connect(self.on_hotkey_pressed)
        self._hotkey_recorded.connect(self.apply_recorded)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(config.save)
        self.ticker = QTimer(self)
        self.ticker.setInterval(50)
        self.ticker.timeout.connect(self.on_tick)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(6 * 3600 * 1000)
        self.update_timer.timeout.connect(lambda: S["check_updates"] and self.check_updates(False))
        self.game_timer = QTimer(self)
        self.game_timer.setInterval(1000)
        self.game_timer.timeout.connect(self.check_game)
        self.medal_timer = QTimer(self)
        self.medal_timer.setInterval(5000)
        self.medal_timer.timeout.connect(self.check_medal)

        self.setup_tray()
        self.register_hotkey()
        self.check_game()
        self.check_medal()

    def start(self):
        self.engine.refresh_devices()
        self.ticker.start()
        self.game_timer.start()
        self.medal_timer.start()
        self.update_timer.start()
        if S["check_updates"]:
            QTimer.singleShot(5000, lambda: self.check_updates(False))

    # ----- settings -----
    def set(self, key, value):
        if S.get(key) == value:
            return
        S[key] = value
        self.save_timer.start()
        if key in ("running", "listen_mic", "mic_device", "outputs"):
            self.engine.sync()
        if key == "running":
            self.update_tray()
            if not value:
                self.cancel_pending()
        self.changed.emit(key)
        if key in ("running", "game_only", "games", "fullscreen_games", "auto_add_games"):
            self.check_game()
        if key == "medal_check":
            self.check_medal()

    # ----- Medal -----
    def check_medal(self):
        """Returns False when Medal isn't running, so a clip can't be saved."""
        running = winutil.process_running("medal.exe")
        ok = running is not False or not S["medal_check"]      # None = couldn't tell, so don't warn
        recording = bool(running) and medal.is_recording(self.medal_root())
        if ok != self.medal_ok or recording != self.recording:
            if ok != self.medal_ok:
                log.info("Medal is running" if running else "Medal isn't running")
            if recording != self.recording:
                log.info("Medal started recording" if recording else "Medal stopped recording")
            self.medal_ok, self.recording = ok, recording
            self.update_tray()
            self.changed.emit("medal")
        return ok

    def medal_root(self):
        """Medal's own folder (C:\\Medal), the parent of its clips folder."""
        folder = clips.clips_folder()
        return folder.parent if folder else None

    def bookmark_key(self):
        return S["bookmark_hotkey"] or medal.bookmark_hotkey()

    def will_bookmark(self):
        """Medal is recording and we're set to add a bookmark instead of saving a clip."""
        return bool(S["bookmark_when_recording"] and self.medal_ok and self.bookmark_key()
                    and medal.is_recording(self.medal_root()))

    def open_medal(self):
        path = winutil.medal_path()
        if not path:
            self.tray.showMessage("Laugh Clipper", "Couldn't find Medal. Start it from the Start menu.",
                                  self.icon, 5000)
            return
        try:
            os.startfile(path)
        except OSError as e:
            log.warning("Couldn't start Medal: %s", e)
        QTimer.singleShot(4000, self.check_medal)

    # ----- games -----
    def check_game(self):
        """When 'only while playing' is on, pause detection unless a game is the window in front."""
        game, tuning = None, False
        if S["game_only"] and S["running"]:
            now = time.time()
            pid, path, full = winutil.foreground_app()
            exe = Path(path).name.lower()
            match = next((g for g in S["games"] if g["exe"] == exe), None)
            if (not match and S["auto_add_games"] and exe not in S["ignored_games"]
                    and winutil.is_library_game(path)):
                match = {"exe": exe, "name": winutil.exe_description(path)}
                log.info("auto-added game: %s (%s)", match["name"], path)
                S["games"] = S["games"] + [match]    # not self.set(): that would call back into here
                self.save_timer.start()
                self.changed.emit("games")
            if match or (S["fullscreen_games"] and full and path and exe not in winutil.SYSTEM_EXES):
                game = match["name"] if match else winutil.exe_description(path)
                self.game_seen = now
            elif pid == os.getpid() or self.settings_open():
                # our settings window or setup guide is open (even on another monitor): keep listening,
                # so the meters work while you set things up
                game, tuning = self.game, True
            elif self.game and now - self.game_seen < GAME_GRACE:
                game = self.game                 # quick alt-tab: keep listening for a moment
        gated = bool(S["game_only"] and S["running"] and game is None and not tuning)
        if game != self.game or gated != self.engine.gated or tuning != self.tuning:
            self.game, self.engine.gated, self.tuning = game, gated, tuning
            log.info("game in front: %s", game or ("none, waiting" if gated else
                                                   "none, but settings are open" if tuning else "not needed"))
            self.update_tray()
            self.changed.emit("game")

    def settings_open(self):
        """The settings window, setup guide or calibration is on screen (open and not minimized)."""
        return any(w is not None and w.isVisible() and not w.isMinimized()
                   for w in (self.window, self.setup, self.calibration))

    def status(self):
        """'paused', 'waiting' (for a game) or 'listening'."""
        if not S["running"]:
            return "paused"
        return "waiting" if self.engine.gated else "listening"

    def on_devices(self, mics, spks, default):
        if S["outputs"] is None:
            S["outputs"] = [default] if default else []
            config.save()
        self.devices = (mics, spks, default)
        self.engine.sync()
        self.devices_changed.emit(mics, spks, default)

    # ----- displays -----
    def display_names(self):
        primary = QGuiApplication.primaryScreen()
        return [f"{i}  ·  Main" if s is primary else str(i) for i, s in enumerate(sorted_screens(), 1)]

    def screen_for(self, n):
        screens = sorted_screens()
        return screens[min(max(n, 1), len(screens)) - 1]

    def identify_displays(self):
        for b in self.badges:
            try:
                b.close()
            except RuntimeError:
                pass
        primary = QGuiApplication.primaryScreen()
        self.badges = [DisplayBadge(i, s, s is primary) for i, s in enumerate(sorted_screens(), 1)]
        for b in self.badges:
            b.show()

    # ----- hotkey -----
    def register_hotkey(self):
        try:
            import keyboard
        except Exception as e:
            log.warning("Hotkeys unavailable: %s", e)
            return
        if self.hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except Exception:
                pass
            self.hotkey_handle = None
        try:
            self.hotkey_handle = keyboard.add_hotkey(S["hotkey"], self._hotkey_hit.emit)
        except Exception as e:
            log.warning("Couldn't register hotkey %s: %s", S["hotkey"], e)

    def record_hotkey(self, setting="hotkey"):
        """Wait for the next key combination and save it as the clip hotkey, or as `setting`."""
        import keyboard
        if self.hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except Exception:
                pass
            self.hotkey_handle = None

        def worker():
            try:
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:
                hk = S[setting]
            self._hotkey_recorded.emit(setting, hk)
        threading.Thread(target=worker, daemon=True, name="record-hotkey").start()

    def apply_recorded(self, setting, hk):
        if setting == "hotkey":
            self.apply_hotkey(hk)
        else:
            self.register_hotkey()          # recording took our clip hotkey away; put it back
            S[setting] = ""                 # so the change below always fires, even for the same keys
            self.set(setting, hk)

    def apply_hotkey(self, hk):
        S["hotkey"] = hk
        self.save_timer.start()
        self.register_hotkey()
        self.changed.emit("hotkey")

    def on_hotkey_pressed(self):
        """The Medal key was pressed: by us (it echoes back), or by you while a popup is showing."""
        self.pending = None
        self.last_prompt = time.time()
        if time.monotonic() - self.sent_at < 1.5:
            return                                   # just our own keyboard.send() coming back
        if self.toast and self.toast.mode in ("ask", "pending") and self.medal_ok and not self.toast_is_test:
            log.info("clip (you pressed the hotkey)")
            self.after_clip()

    def send_hotkey(self, key=None):
        """Press Medal's clip hotkey (or another of its hotkeys, like bookmark)."""
        self.sent_at = time.monotonic()
        try:
            import keyboard
            keyboard.send(key or S["hotkey"])
        except Exception as e:
            log.warning("Couldn't send hotkey: %s", e)

    # ----- laughs, popups and clipping -----
    def on_laugh(self, key, sound):
        now = time.time()
        if self.pending or not S["running"] or now - self.last_prompt < S["cooldown"]:
            return
        self.last_prompt = now
        kind, name = key
        label = "Your mic" if kind == "mic" else short_name(name)
        self.moment = {"key": key, "sound": sound, "source": label, "game": self.game, "auto": S["auto_clip"],
                       "peak": self.engine.sound.get(key, (None, 0.0))[1],
                       "began": now - S["min_laugh"]}      # it had been going this long before it triggered
        if not self.check_medal():
            log.info("not clipping: Medal isn't running")
            self.show_toast("nomedal", label, sound)
        elif S["auto_clip"] and S["clip_delay"] > 0:
            self.pending = {"start": now, "last": now}
            self.show_toast("pending", label, sound)
        elif S["auto_clip"]:
            self.show_toast("saving", label, sound)
            self.clip()
        else:
            self.show_toast("ask", label, sound)

    def on_tick(self):
        if not self.pending:
            return
        now = time.time()
        if self.moment:
            self.moment["peak"] = max(self.moment["peak"], self.engine.sound.get(self.moment["key"], (None, 0.0))[1])
        laughing = self.engine.laughing(now)
        if laughing:
            # count the wait from the last laugh sound, not from when the pause tolerance ran out
            self.pending["last"] = max(self.pending["last"], self.engine.last_activity())
        remaining = S["clip_delay"] - (now - self.pending["last"])
        if S["max_wait"] > 0 and now - self.pending["start"] >= S["max_wait"]:
            remaining = 0
        if self.toast:
            self.toast.set_pending(remaining, laughing and remaining > 0, S["clip_delay"], self.engine.max_level())
        if remaining <= 0:
            self.clip()

    def clip(self):
        self.pending = None
        self.last_prompt = time.time()
        if not self.check_medal():
            if self.toast:
                self.toast.set_nomedal(S["popup_seconds"])
            log.info("not clipping: Medal isn't running")
            return
        if self.toast_is_test:
            if self.toast:
                self.toast.set_saved()
            log.info("test clip (hotkey not sent)")
            return
        if self.will_bookmark():
            key = self.bookmark_key()
            QTimer.singleShot(60, lambda: self.send_hotkey(key))
            log.info("Medal is recording: bookmark (%s) sent instead of a clip", key)
            if self.toast:
                self.toast.set_bookmarked()
            self.record("bookmarked")
            return
        key, length = self.clip_key()
        if self.moment is not None:
            self.moment["length"] = length
        QTimer.singleShot(60, lambda: self.send_hotkey(key))
        log.info("clip: hotkey %s sent%s", key, f" ({length} s clip)" if length else "")
        self.after_clip()

    def moment_seconds(self):
        """How long the moment has been going: from the start of the laugh until now."""
        began = (self.moment or {}).get("began")
        return max(0.0, time.time() - began) if began else 0.0

    def laugh_seconds(self):
        """How long the laughing itself went on: from when it started to the last laugh sound (not the wait
        after it). At least `min_laugh`, since that's what it took to trigger."""
        began = (self.moment or {}).get("began")
        if not began:
            return 0.0
        last = max(self.engine.last_activity(), began + S["min_laugh"])
        return max(0.0, min(time.time(), last) - began)

    def clip_key(self):
        """(hotkey to press, its clip length in seconds or 0 if unknown). With smart length on, your own clip
        hotkey for short laughs, one of Medal's longer ones for every `long_laugh` seconds of laughing, and
        always long enough to cover the whole moment plus a little build-up before it."""
        clip_keys = medal.clip_hotkeys()
        base = next((i for i, (n, hk) in enumerate(clip_keys) if hk == S["hotkey"]), None)
        if not (S["smart_length"] and base is not None and len(clip_keys) > 1):
            return S["hotkey"], clip_keys[base][0] if base is not None else 0
        steps = int(self.laugh_seconds() // max(S["long_laugh"], 1))
        needed = self.moment_seconds() + PRE_ROLL
        fits = next((i for i, (n, _) in enumerate(clip_keys) if n >= needed), len(clip_keys) - 1)
        n, hk = clip_keys[min(len(clip_keys) - 1, max(base + steps, fits))]
        return hk, n

    def after_clip(self):
        """The hotkey went to Medal: check a new clip file turns up, then log it in the history."""
        folder = clips.clips_folder() if S["confirm_clips"] else None
        self.watcher.stop()
        if folder:
            if self.toast:
                self.toast.set_saving()
            self.watcher.start(folder)
        else:
            if self.toast:
                self.toast.set_saved()
            self.record("unchecked")

    def on_clip_found(self, path):
        log.info("clip saved: %s", path)
        if self.toast:
            self.toast.set_saved(Path(path).parent.name, (self.moment or {}).get("length"))
        self.record("saved", path)

    def on_clip_missing(self):
        log.warning("no new clip appeared in Medal's folder")
        if self.toast:
            self.toast.set_failed(S["popup_seconds"])
        else:
            self.tray.showMessage("Clip didn't save", "Medal didn't make a clip. Check the hotkey on the Clipping "
                                  "page matches Medal's.", self.icon, 6000)
        self.record("failed")

    def record(self, status, path=""):
        self.history.append(clips.new_entry(self.moment or {}, status, path))
        self.history = self.history[-clips.HISTORY_MAX:]
        clips.save_history(self.history)
        self.moment = None
        self.history_changed.emit()

    def clear_history(self):
        self.history = []
        clips.save_history(self.history)
        self.history_changed.emit()

    def cancel_pending(self):
        if not self.pending:
            return
        self.pending = None
        self.last_prompt = time.time()
        if self.toast:
            self.toast.dismiss()
        log.info("auto clip cancelled")

    def show_toast(self, mode, label, sound="laughter", test=False):
        if self.toast is not None:
            old, self.toast = self.toast, None
            try:
                old.close()
            except RuntimeError:
                pass
        seconds = S["clip_delay"] if mode == "pending" else S["popup_seconds"]
        t = Toast(self.screen_for(S["monitor"]), S["position"], mode, label, sound, hotkey_label(), seconds)
        t.bookmark = not test and self.will_bookmark()
        t.clip_requested.connect(self.clip)
        t.close_requested.connect(self.on_toast_close)
        t.finished.connect(lambda t=t: self.on_toast_done(t))
        self.toast, self.toast_is_test = t, test
        t.show()
        if S["sound_cue"]:
            sounds.play_chime(S["sound_volume"], S["sound_device"], S["sound_name"])

    def on_toast_close(self):
        if self.pending:
            self.cancel_pending()
        elif self.toast:
            self.toast.dismiss()

    def on_toast_done(self, t):
        if self.toast is t:
            self.toast = None
            self.toast_is_test = False

    def test_toast(self):
        self.pending = None
        self.moment = None
        self.show_toast("ask" if self.check_medal() else "nomedal", "Test", S["triggers"][0], test=True)

    # ----- updates -----
    def check_updates(self, manual=False):
        self.updater.check(manual)

    def on_update_state(self, state, info):
        self.update_state = (state, info)
        if state == "available" and info["version"] != self.notified_version:
            self.notified_version = info["version"]
            self.tray.showMessage("Laugh Clipper", f"Version {info['version']} is ready to install. "
                                  "Open Laugh Clipper to update.", self.icon, 6000)
        if state == "installing":
            winutil.release_mutex()
            try:
                subprocess.Popen([info["path"], "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                  "/CLOSEAPPLICATIONS"], close_fds=True)
                QTimer.singleShot(300, self.quit)
            except OSError as e:
                log.warning("Couldn't start installer: %s", e)
                winutil.create_mutex(MUTEX_NAME)
                state, info = "error", {"manual": True, "message": str(e)}
                self.update_state = (state, info)
        self.update_changed.emit(state, info)

    # ----- tray / window -----
    def setup_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("Laugh Clipper")
        menu = QMenu()
        self.act_status = menu.addAction("Listening")
        self.act_status.setEnabled(False)
        self.act_medal = menu.addAction("Medal isn't running · Open Medal", self.open_medal)
        menu.addAction("Open Laugh Clipper", self.show_window)
        menu.addSeparator()
        self.act_run = menu.addAction("Pause Detection", lambda: self.set("running", not S["running"]))
        menu.addAction("Show Test Notification", self.test_toast)
        menu.addSeparator()
        menu.addAction("Quit Laugh Clipper", self.quit)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.update_tray()
        self.tray.show()

    def update_tray(self):
        self.act_run.setText("Pause Detection" if S["running"] else "Resume Detection")
        text = {"paused": "Paused", "waiting": "Waiting for a game",
                "listening": f"Listening  ·  {self.game}" if self.game else "Listening"}[self.status()]
        self.act_status.setText(text)
        self.act_medal.setVisible(not self.medal_ok)
        extra = ("\nMedal isn't running, so clips won't save" if not self.medal_ok else
                 "\nMedal is recording, so laughs add bookmarks" if self.recording and S["bookmark_when_recording"]
                 else "")
        self.tray.setToolTip(f"Laugh Clipper\n{text}{extra}")
        state = self.status()
        if state != "paused" and not self.medal_ok:
            state = "warning"
        self.tray.setIcon(self.tray_icon(state))

    def tray_icon(self, state):
        """The app icon as it is when listening, greyed out when paused, and with an orange dot when
        it's waiting for a game or Medal isn't running."""
        if state not in self.tray_icons:
            if state == "listening":
                icon = self.icon
            else:
                icon = QIcon()
                for size in (16, 20, 24, 32, 40, 48, 64):
                    icon.addPixmap(badged(self.icon.pixmap(size, size), state))
            self.tray_icons[state] = icon
        return self.tray_icons[state]

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_setup(self):
        """The setup guide (shown on first run, or from 'Run Setup Again' on the General page)."""
        if self.setup is None:
            from .setup_guide import SetupGuide
            self.setup = SetupGuide(self)
            self.setup.done.connect(self.on_setup_done)
        self.setup.show()
        self.setup.raise_()
        self.setup.activateWindow()

    def show_calibration(self):
        """Calibrate the trigger level for the game being played (from the Detection page)."""
        if self.calibration is None:
            from .calibrate import CalibrationWindow
            self.calibration = CalibrationWindow(self)
            self.calibration.done.connect(self.on_calibration_done)
        self.calibration.show()
        self.calibration.raise_()
        self.calibration.activateWindow()

    def show_whats_new(self):
        """The release notes for this version (after an update, or from the General page)."""
        if self.whats_new is None:
            from .whatsnew import WhatsNewWindow
            self.whats_new = WhatsNewWindow(self, VERSION)
            self.whats_new.done.connect(self.on_whats_new_done)
        self.whats_new.show()
        self.whats_new.raise_()
        self.whats_new.activateWindow()

    def on_whats_new_done(self):
        self.whats_new.deleteLater()
        self.whats_new = None

    def on_calibration_done(self):
        self.calibration.deleteLater()
        self.calibration = None

    def on_setup_done(self):
        self.setup.deleteLater()
        self.setup = None
        if not (self.window and self.window.isVisible()):
            self.tray.showMessage("Laugh Clipper is running", "It's in the system tray. Click the icon to open "
                                  "the settings.", self.icon, 5000)

    def show_window(self):
        if self.window is None:
            from .settings_window import SettingsWindow
            self.window = SettingsWindow(self)
        w = self.window
        w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
        w.show()
        w.raise_()
        w.activateWindow()

    def quit(self):
        self.engine.stop_all()
        config.save()
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        self.tray.hide()
        self.app.quit()


def setup_logging():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(config.LOG_PATH, maxBytes=512_000, backupCount=1, encoding="utf-8")]
    if sys.stdout:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    sys.excepthook = lambda *exc: log.error("Unhandled error", exc_info=exc)
    threading.excepthook = lambda a: log.error("Error in thread %s", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def whats_new_due(first_run, version):
    """Show "What's new" once after an update to `version`. A new install gets the setup guide instead."""
    from .whatsnew import notes
    due = not first_run and S["last_seen_version"] != version and bool(notes(version))
    if S["last_seen_version"] != version:
        S["last_seen_version"] = version
        config.save()
    return due


def run():
    winutil.set_app_id("LaughClipper.App")
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(config.resource("assets/icon.png"))))

    # already running? just bring that one to the front
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0

    setup_logging()
    log.info("Laugh Clipper %s starting", VERSION)
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)
    winutil.create_mutex(MUTEX_NAME)

    first_run = not config.load()
    winutil.migrate_startup()
    T.init(app)
    ctrl = Controller(app)

    def on_connection():
        conn = server.nextPendingConnection()
        conn.readyRead.connect(ctrl.show_window)
        conn.disconnected.connect(conn.deleteLater)
    server.newConnection.connect(on_connection)

    if whats_new_due(first_run, VERSION):
        QTimer.singleShot(1500, ctrl.show_whats_new)
    if first_run or not S["setup_done"]:
        ctrl.show_setup()
    elif not ("--minimized" in sys.argv or S["start_minimized"]):
        ctrl.show_window()
    ctrl.start()
    return app.exec()
