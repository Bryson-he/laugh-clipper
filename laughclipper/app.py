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

from . import clips, config, gamepad, medal, sounds, voice, winutil
from .config import S, hotkey_label
from .engine import SOUNDS, Engine, short_name, sound_level
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


def source_label(key):
    """How the popup and history name a source: "Your mic", or the output's short name ("Voice Chat")."""
    return "Your mic" if key[0] == "mic" else short_name(key[1])


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
    _hotkey_recorded = Signal(str, str)     # which hotkey ("hotkey" / "bookmark_hotkey" / "pause_hotkey"), keys
    _pause_hit = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.icon = QIcon(str(config.resource("assets/icon.png")))
        self.engine = Engine()
        self.engine.laugh.connect(self.on_laugh)
        self.engine.group.connect(self.on_group_laugh)
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
        self.hotkey_handles = {}       # "clip" / "pause" -> keyboard hotkey handle
        self.badges = []
        self.tray_icons = {}         # state -> tray icon, made once
        self.notified_version = None
        self.game = None             # name of the game in front (when "only while playing" is on)
        self.game_seen = 0.0
        self.tuning = False          # our own window is in front, so we listen even without a game
        self.medal_ok = True         # False when Medal isn't running (and we're checking for it)
        self.recording = False       # Medal is recording a session right now
        self.game_admin = False      # the game in front runs as administrator and we don't: keys won't reach it
        self.admin_checked = (0, False)     # (process id, runs as admin) for the last game looked at
        self.admin_warned = set()    # games we've already shown the tray warning for
        self.source_problems = []    # audio sources that have been failing for a while
        self.health_at = time.monotonic()
        self.moment = None           # the laugh the popup is about: sound, source, game, peak score
        self.sent_at = 0.0           # when we last sent the hotkey (it echoes back as a key press)
        self.history = clips.load_history()
        self.watcher = clips.ClipWatcher()
        self.watcher.found.connect(self.on_clip_found)
        self.watcher.missing.connect(self.on_clip_missing)
        self.watcher.late.connect(self.on_clip_late)
        self.failed_entry = None       # the history entry for a clip that didn't seem to save (it may turn up late)

        self._hotkey_hit.connect(self.on_hotkey_pressed)
        self._pause_hit.connect(self.toggle_pause)
        self.voice = voice.VoiceCommands()
        self.voice.heard.connect(self.on_voice)
        self.engine.voice = self.voice
        if S["voice_cancel"] or S["hype_phrases"]:
            self.voice.load()
        self._hotkey_recorded.connect(self.apply_recorded)
        self.pads = gamepad.Pads()
        self.pads.pressed.connect(self.on_pad)
        self.pad_capture = False       # waiting for you to press the button to use
        self.pad_capture_timer = QTimer(self)
        self.pad_capture_timer.setSingleShot(True)
        self.pad_capture_timer.setInterval(10000)
        self.pad_capture_timer.timeout.connect(lambda: self.stop_pad_capture())
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
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(5000)
        self.health_timer.timeout.connect(self.check_health)

        self.setup_tray()
        self.register_hotkey()
        self.check_game()
        self.check_medal()

    def start(self):
        self.engine.refresh_devices()
        self.ticker.start()
        self.game_timer.start()
        self.medal_timer.start()
        self.health_timer.start()
        self.update_timer.start()
        self.update_pads()
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
        if key in ("voice_cancel", "hype_phrases") and value:
            self.voice.load()
        if key == "pad_button":
            self.update_pads()

    # ----- keeping itself working -----
    def check_health(self):
        """Every 5 s: restart audio sources that have hung, notice ones that stay broken, and put the hotkeys
        back after the PC has been asleep (Windows can drop them)."""
        now = time.monotonic()
        slept = now - self.health_at > 30
        self.health_at = now
        if slept:
            log.info("back from sleep: re-registering hotkeys")
            self.register_hotkey()
            self.engine.alive.clear()            # give every source a fresh STALL_SECONDS
            self.check_medal()
        self.engine.restart_stalled()
        problems = self.engine.problems()
        if problems != self.source_problems:
            self.source_problems = problems
            self.update_tray()
            self.changed.emit("sources")

    def health(self):
        """What has to be right for clips to save, as [(key, ok, what's wrong and what to do)].
        ok is None when it can't be told (those don't count as problems)."""
        clip_keys = medal.clip_hotkeys()
        folder = clips.clips_folder()
        lost = ", ".join(source_label(k) for k in self.source_problems)
        checks = [
            ("medal", self.medal_ok, "Medal isn't running, so clips can't be saved."),
            ("hotkey", any(hk == S["hotkey"] for _, hk in clip_keys) if clip_keys else None,
             f"Your clip hotkey ({hotkey_label()}) isn't one of Medal's. Fix it on the Clipping page."),
            ("folder", (folder is not None) if S["confirm_clips"] else None,
             "Medal's clips folder wasn't found, so saved clips can't be checked. Choose it on the Clipping page."),
            ("sources", bool(self.engine.wanted()) or not S["running"],
             "Nothing to listen to. Turn on your mic or some PC audio on the Audio page."),
            ("audio", not lost, f"Can't hear {lost}. Check it's plugged in and not in use by something else."),
            ("admin", not self.game_admin,
             f"{self.game or 'The game'} runs as administrator, which blocks the clip hotkey. Run Laugh Clipper "
             "as administrator too."),
        ]
        last = self.history[-1] if self.history else None
        if last and last.get("status") == "failed":
            checks.append(("last", False, "The last clip didn't save. Use Test Clip to check it works now."))
        return checks

    def test_clip(self):
        """Press Medal's hotkey for real and check the clip turns up (it's saved like any other)."""
        log.info("test clip")
        self.clip_now("Test", "test", "Test clip", force=True)

    def diagnostics(self):
        """A summary for troubleshooting, with nothing personal in it (no folders, no game list)."""
        import platform
        home = str(Path.home())
        lines = [f"Laugh Clipper {VERSION} on Windows {platform.version()}",
                 f"Status: {self.status()}" + (f", game in front: {self.game}" if self.game else ""), "", "Checks:"]
        lines += [f"  {'ok ' if ok else '?  ' if ok is None else 'BAD'} {key}" + ("" if ok is not False else f": {text}")
                  for key, ok, text in self.health()]
        keep = ("auto_clip", "clip_delay", "max_wait", "confirm_clips", "game_only", "triggers", "threshold",
                "sound_levels", "min_laugh", "pause_tolerance", "cooldown", "listen_mic", "group_laughs",
                "voice_cancel", "hype_phrases", "smart_length", "bookmark_when_recording", "pad_mode")
        lines += ["", "Settings:"] + [f"  {k} = {S[k]}" for k in keep]
        lines.append(f"  sources = {[source_label(k) for k in self.engine.wanted()]}")
        kinds = {}
        for e in self.history[-50:]:
            kinds[e.get("status")] = kinds.get(e.get("status"), 0) + 1
        lines += ["", f"Last {min(len(self.history), 50)} clips: {kinds}", "", "Recent problems:"]
        try:
            log_lines = config.LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            log_lines = []
        bad = [ln for ln in log_lines if " WARNING " in ln or " ERROR " in ln][-15:]
        lines += [f"  {ln.replace(home, '~')}" for ln in bad] or ["  none"]
        return "\n".join(lines)

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
        game, tuning, admin = None, False, False
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
                if self.admin_checked[0] != pid:
                    self.admin_checked = (pid, bool(winutil.runs_elevated(pid)))
                admin = self.admin_checked[1]
            elif pid == os.getpid() or self.settings_open():
                # our settings window or setup guide is open (even on another monitor): keep listening,
                # so the meters work while you set things up
                game, tuning = self.game, True
            elif self.game and now - self.game_seen < GAME_GRACE:
                game = self.game                 # quick alt-tab: keep listening for a moment
        gated = bool(S["game_only"] and S["running"] and game is None and not tuning)
        if admin and game not in self.admin_warned:
            self.admin_warned.add(game)
            log.warning("%s runs as administrator: the clip hotkey won't reach Medal", game)
            self.tray.showMessage(f"{game} runs as administrator",
                                  "Windows blocks Laugh Clipper's clip hotkey while it's in front. Run Laugh "
                                  "Clipper as administrator too, or run the game normally.", self.icon, 8000)
        if game != self.game or gated != self.engine.gated or tuning != self.tuning or admin != self.game_admin:
            self.game, self.engine.gated, self.tuning, self.game_admin = game, gated, tuning, admin
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
    def unregister_hotkeys(self):
        try:
            import keyboard
        except Exception:
            return
        for handle in self.hotkey_handles.values():
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self.hotkey_handles = {}

    def register_hotkey(self):
        """Listen for Medal's clip hotkey (to notice you pressing it) and our pause hotkey, if set."""
        try:
            import keyboard
        except Exception as e:
            log.warning("Hotkeys unavailable: %s", e)
            return
        self.unregister_hotkeys()
        for name, hk, hit in (("clip", S["hotkey"], self._hotkey_hit), ("pause", S["pause_hotkey"], self._pause_hit)):
            if not hk:
                continue
            try:
                self.hotkey_handles[name] = keyboard.add_hotkey(hk, hit.emit)
            except Exception as e:
                log.warning("Couldn't register hotkey %s: %s", hk, e)

    def record_hotkey(self, setting="hotkey"):
        """Wait for the next key combination and save it as the clip hotkey, or as `setting`."""
        import keyboard
        self.unregister_hotkeys()

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
            return
        if setting == "pause_hotkey" and hk == S["hotkey"]:
            log.warning("Pause hotkey can't be the same as the clip hotkey (%s)", hk)
            hk = S["pause_hotkey"]          # keep the old one
        S[setting] = ""                     # so the change below always fires, even for the same keys
        self.set(setting, hk)
        self.register_hotkey()              # recording took our hotkeys away; put them back

    def toggle_pause(self):
        """The pause hotkey: pause or resume detection, with a little popup saying which."""
        self.set("running", not S["running"])
        on = S["running"]
        self.show_info("play" if on else "pause", "Detection on" if on else "Detection paused",
                       "Listening for laughs again" if on else f"Press {hotkey_label(S['pause_hotkey'])} to resume")

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

    # ----- controller / wheel button -----
    def update_pads(self):
        """Only read controllers while there's a button to watch for (or one being picked)."""
        self.pads.set_active(bool(S["pad_button"]) or self.pad_capture)

    def capture_pad_button(self):
        """The next controller button pressed becomes the clip button (give up after 10 s)."""
        self.pad_capture = True
        self.update_pads()
        self.pad_capture_timer.start()

    def stop_pad_capture(self, info=None):
        self.pad_capture = False
        self.pad_capture_timer.stop()
        if info:
            log.info("clip button: %s", gamepad.label(info))
            S["pad_button"] = {}                # so the change always fires, even for the same button
            self.set("pad_button", info)
        else:
            self.update_pads()
            self.changed.emit("pad_button")     # the settings page stops saying "Press a button…"

    def on_pad(self, info):
        if self.pad_capture:
            self.stop_pad_capture(info)
            return
        if not gamepad.same_button(info, S["pad_button"]):
            return
        popup = self.toast is not None and self.toast.mode in ("ask", "pending") and not self.toast_is_test
        if popup:
            log.info("clip (controller button)")
            self.clip()
        elif S["pad_mode"] == "always":
            log.info("clip (controller button, no popup)")
            self.clip_now(info.get("name") or "Controller", "button", "Clip button")

    def send_hotkey(self, key=None):
        """Press Medal's clip hotkey (or another of its hotkeys, like bookmark)."""
        self.sent_at = time.monotonic()
        try:
            import keyboard
            keyboard.send(key or S["hotkey"])
        except Exception as e:
            log.warning("Couldn't send hotkey: %s", e)

    # ----- laughs, popups and clipping -----
    def on_laugh(self, key, sound, title=None, label=None):
        now = time.time()
        if self.pending or not S["running"] or now - self.last_prompt < S["cooldown"]:
            return
        self.last_prompt = now
        label = label or source_label(key)
        self.moment = {"key": key, "sound": sound, "source": label, "game": self.game, "auto": S["auto_clip"],
                       "peak": self.engine.sound.get(key, (None, 0.0))[1],
                       "began": now - S["min_laugh"]}      # it had been going this long before it triggered
        if not self.check_medal():
            log.info("not clipping: Medal isn't running")
            self.show_toast("nomedal", label, sound, title=title)
        elif S["auto_clip"] and S["clip_delay"] > 0:
            self.pending = {"start": now, "last": now}
            self.show_toast("pending", label, sound, title=title)
        elif S["auto_clip"]:
            self.show_toast("saving", label, sound, title=title)
            self.clip()
        else:
            self.show_toast("ask", label, sound, title=title)

    def on_group_laugh(self, key, sound, others):
        """Your mic and another source laughing at the same time."""
        keys = sorted({key, *map(tuple, others)}, key=lambda k: k[0] != "mic")     # your mic first
        title = "Group laugh" if sound == "laughter" else f"Group {SOUNDS[sound][0].lower()}"
        names = ["You" if k[0] == "mic" else source_label(k) for k in keys]     # short, so the popup fits it
        self.on_laugh(tuple(key), sound, title, " + ".join(names))

    def on_voice(self, kind, text):
        """The speech model heard "don't clip", "clip that" or a hype phrase."""
        if kind == "cancel":
            self.on_voice_cancel(text)
            return
        popup = self.toast is not None and self.toast.mode in ("ask", "pending") and not self.toast_is_test
        title = f"“{text[:1].upper()}{text[1:]}”"
        if kind == "clip":
            log.info('clip (you said "%s")', text)
            self.clip_now("Your mic", "hype", title)
        elif not popup:                 # a hype phrase during a popup is the same moment
            self.on_laugh(("mic", S["mic_device"]), "hype", title)

    def clip_now(self, label, sound, title=None, force=False):
        """Save a clip straight away ("clip that", or the controller button): the popup's Clip button if one is
        up, otherwise a fresh moment that goes straight to saving."""
        if self.toast is not None and self.toast.mode in ("ask", "pending") and not self.toast_is_test:
            self.clip()
            return
        if not S["running"] and not force:
            return
        now = time.time()
        self.pending = None
        self.last_prompt = now
        self.moment = {"key": None, "sound": sound, "source": label, "game": self.game, "auto": False,
                       "peak": 0.0, "began": now}
        if not self.check_medal():
            log.info("not clipping: Medal isn't running")
            self.show_toast("nomedal", label, sound, title=title)
            return
        self.show_toast("saving", label, sound, title=title)
        self.clip()

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
        self.voice.stop()
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
        self.failed_entry = self.history[-1]

    def on_clip_late(self, path):
        """Medal was slow: the clip we said didn't save has turned up after all. Put the record straight."""
        log.info("clip saved after all (Medal was slow): %s", path)
        entry, self.failed_entry = self.failed_entry, None
        if entry is not None and any(e is entry for e in self.history):
            entry["status"], entry["file"] = "saved", path
            entry["game"] = entry.get("game") or Path(path).parent.name
            clips.save_history(self.history)
            self.history_changed.emit()
        if self.toast and self.toast.mode == "failed":
            self.toast.set_saved(Path(path).parent.name)

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
        self.voice.stop()
        if not self.pending:
            return
        self.pending = None
        self.last_prompt = time.time()
        if self.toast:
            self.toast.dismiss()
        log.info("auto clip cancelled")

    def show_toast(self, mode, label, sound="laughter", test=False, title=None):
        if self.toast is not None:
            old, self.toast = self.toast, None
            try:
                old.close()
            except RuntimeError:
                pass
        seconds = S["clip_delay"] if mode == "pending" else S["popup_seconds"]
        t = Toast(self.screen_for(S["monitor"]), S["position"], mode, label, sound, hotkey_label(), seconds)
        t.bookmark = not test and self.will_bookmark()
        if title:
            t.title = title
        t.clip_requested.connect(self.clip)
        t.not_funny.connect(self.on_not_funny)
        t.close_requested.connect(self.on_toast_close)
        t.finished.connect(lambda t=t: self.on_toast_done(t))
        self.toast, self.toast_is_test = t, test
        t.show()
        self.after_show(mode)

    def after_show(self, mode):
        """A popup just appeared: listen for "don't clip" while it's up, and play the sound if that's on."""
        if mode in ("ask", "pending") and S["voice_cancel"]:
            self.voice.start()
        if mode != "info" and S["sound_cue"]:
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
            self.voice.stop()

    def show_info(self, icon, title, sub):
        """A short popup that says something and goes away by itself (paused, cancelled…)."""
        if self.toast is not None and self.toast.mode in ("ask", "pending", "saving"):
            self.toast.set_info(icon, title, sub)          # turn the current popup into it
            return
        self.show_toast("info", "", test=True)
        self.toast.set_info(icon, title, sub)

    def on_voice_cancel(self, text):
        """Someone said "don't clip" while the popup was up."""
        if not self.toast or self.toast.mode not in ("ask", "pending"):
            return
        self.pending = None
        self.last_prompt = time.time()
        self.moment = None
        log.info('clip cancelled by voice ("%s")', text)
        self.toast.set_info("cancel", "Cancelled", f'You said “{text}”')

    def on_not_funny(self):
        """The 👎 on the popup: skip this one, and be a little stricter with that sound from now on."""
        if not self.toast or self.toast.mode not in ("ask", "pending") or not self.moment:
            return
        self.voice.stop()
        self.pending = None
        self.last_prompt = time.time()
        sound = self.moment["sound"]
        if sound not in SOUNDS:                       # a hype phrase: nothing to tune, just skip it
            self.record("not_funny")
            self.toast.set_info("thumb", "Got it", "Skipped this one")
            return
        old = sound_level(sound)
        new = round(min(0.9, max(old * 1.1, old + 0.02)), 2)
        self.set("sound_levels", {**S["sound_levels"], sound: new})
        log.info("not funny: %s level %.2f -> %.2f", sound, old, new)
        self.record("not_funny")
        self.toast.set_info("thumb", "Got it", f"{SOUNDS[sound][0]} level raised to {new:.2f}")

    def test_toast(self):
        self.pending = None
        self.moment = None
        self.show_toast("ask" if self.check_medal() else "nomedal", "Test", S["triggers"][0], test=True)

    # ----- updates -----
    def check_updates(self, manual=False):
        self.updater.check(manual)

    def on_update_state(self, state, info):
        self.update_state = (state, info)
        if state == "error" and not info.get("manual") and S["check_updates"]:
            QTimer.singleShot(30 * 60 * 1000, lambda: self.check_updates(False))     # offline? look again later
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
        lost = ", ".join(source_label(k) for k in self.source_problems)
        extra = ("\nMedal isn't running, so clips won't save" if not self.medal_ok else
                 f"\nCan't hear: {lost}" if lost else
                 "\nThe game runs as administrator, so clips may not save" if self.game_admin else
                 "\nMedal is recording, so laughs add bookmarks" if self.recording and S["bookmark_when_recording"]
                 else "")
        self.tray.setToolTip(f"Laugh Clipper\n{text}{extra}")
        state = self.status()
        if state != "paused" and (not self.medal_ok or lost or self.game_admin):
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

    def open_window(self, attr, make, on_done=None):
        """Show one of our windows, creating it the first time. Windows with a `done` signal (setup guide,
        calibration, What's new) are thrown away when closed; `on_done` runs after that."""
        w = getattr(self, attr)
        if w is None:
            w = make()
            setattr(self, attr, w)
            if hasattr(w, "done"):
                w.done.connect(lambda: self.window_closed(attr, on_done))
        w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
        w.show()
        w.raise_()
        w.activateWindow()

    def window_closed(self, attr, on_done=None):
        getattr(self, attr).deleteLater()
        setattr(self, attr, None)
        if on_done:
            on_done()

    def show_window(self):
        from .settings_window import SettingsWindow
        self.open_window("window", lambda: SettingsWindow(self))

    def show_setup(self):
        """The setup guide (shown on first run, or from 'Run Setup Again' on the General page)."""
        from .setup_guide import SetupGuide
        self.open_window("setup", lambda: SetupGuide(self), self.on_setup_done)

    def show_calibration(self):
        """Calibrate the trigger level for the game being played (from the Detection page)."""
        from .calibrate import CalibrationWindow
        self.open_window("calibration", lambda: CalibrationWindow(self))

    def show_whats_new(self):
        """The release notes for this version (after an update, or from the General page)."""
        from .whatsnew import WhatsNewWindow
        self.open_window("whats_new", lambda: WhatsNewWindow(self, VERSION))

    def on_setup_done(self):
        if not (self.window and self.window.isVisible()):
            self.tray.showMessage("Laugh Clipper is running", "It's in the system tray. Click the icon to open "
                                  "the settings.", self.icon, 5000)

    def quit(self):
        self.pads.set_active(False)
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
