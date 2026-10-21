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
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import config, winutil
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
    _hotkey_hit = Signal()
    _hotkey_recorded = Signal(str)

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
        self.toast = None
        self.toast_is_test = False
        self.pending = None
        self.last_prompt = 0.0
        self.hotkey_handle = None
        self.badges = []
        self.notified_version = None
        self.game = None             # name of the game in front (when "only while playing" is on)
        self.game_seen = 0.0
        self.medal_ok = True         # False when Medal isn't running (and we're checking for it)

        self._hotkey_hit.connect(self.on_hotkey_pressed)
        self._hotkey_recorded.connect(self.apply_hotkey)
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
        if ok != self.medal_ok:
            self.medal_ok = ok
            log.info("Medal is running" if running else "Medal isn't running")
            self.update_tray()
            self.changed.emit("medal")
        return ok

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
        game = None
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
            if pid == os.getpid():
                game = self.game                 # our own window: leave things as they are
            elif match or (S["fullscreen_games"] and full and path and exe not in winutil.SYSTEM_EXES):
                game = match["name"] if match else winutil.exe_description(path)
                self.game_seen = now
            elif self.game and now - self.game_seen < GAME_GRACE:
                game = self.game                 # quick alt-tab: keep listening for a moment
        gated = bool(S["game_only"] and S["running"] and game is None)
        if game != self.game or gated != self.engine.gated:
            self.game, self.engine.gated = game, gated
            log.info("game in front: %s", game or ("none, waiting" if gated else "not needed"))
            self.update_tray()
            self.changed.emit("game")

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

    def record_hotkey(self):
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
                hk = S["hotkey"]
            self._hotkey_recorded.emit(hk)
        threading.Thread(target=worker, daemon=True, name="record-hotkey").start()

    def apply_hotkey(self, hk):
        S["hotkey"] = hk
        self.save_timer.start()
        self.register_hotkey()
        self.changed.emit("hotkey")

    def on_hotkey_pressed(self):
        """The Medal key was pressed (by you, or by us)."""
        self.pending = None
        self.last_prompt = time.time()
        if self.toast and self.toast.mode not in ("saved", "nomedal") and self.medal_ok:
            self.toast.set_saved()

    def send_hotkey(self):
        try:
            import keyboard
            keyboard.send(S["hotkey"])
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
        if not self.check_medal():
            log.info("not clipping: Medal isn't running")
            self.show_toast("nomedal", label, sound)
        elif S["auto_clip"] and S["clip_delay"] > 0:
            self.pending = {"start": now, "last": now}
            self.show_toast("pending", label, sound)
        elif S["auto_clip"]:
            self.show_toast("saved", label, sound)
            self.clip()
        else:
            self.show_toast("ask", label, sound)

    def on_tick(self):
        if not self.pending:
            return
        now = time.time()
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
        if self.toast:
            self.toast.set_saved()
        if self.toast_is_test:
            log.info("test clip (hotkey not sent)")
            return
        QTimer.singleShot(60, self.send_hotkey)
        log.info("clip saved")

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
        t.clip_requested.connect(self.clip)
        t.close_requested.connect(self.on_toast_close)
        t.finished.connect(lambda t=t: self.on_toast_done(t))
        self.toast, self.toast_is_test = t, test
        t.show()

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
        self.tray.setToolTip(f"Laugh Clipper\n{text}" + ("" if self.medal_ok else
                                                         "\nMedal isn't running, so clips won't save"))

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

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

    if first_run or not ("--minimized" in sys.argv or S["start_minimized"]):
        ctrl.show_window()
    ctrl.start()
    return app.exec()
