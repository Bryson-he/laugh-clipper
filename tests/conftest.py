"""Shared test setup: offscreen Qt, a throwaway settings folder, and no real keyboard hooks."""

import copy
import os
import sys
import tempfile
import time
import types
from pathlib import Path

# before anything imports laughclipper.config, which reads APPDATA once
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="laughclipper-tests-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SENT = []          # hotkeys "sent" by the app during a test
sys.modules["keyboard"] = types.SimpleNamespace(add_hotkey=lambda *a, **k: 1, remove_hotkey=lambda *a: None,
                                                send=SENT.append, unhook_all=lambda: None)

import pytest  # noqa: E402

from laughclipper import config, winutil  # noqa: E402
from laughclipper.config import S  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_settings():
    """Every test starts from the defaults, with no settings file."""
    config.S.clear()
    config.S.update(copy.deepcopy(config.DEFAULTS))
    config.SETTINGS_PATH.unlink(missing_ok=True)
    SENT.clear()
    yield


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from laughclipper.theme import T
    T.init(app)
    return app


class FakePC:
    """What the Controller sees of Windows: the window in front, whether Medal runs, and the clock."""

    def __init__(self):
        self.front = (2, r"C:\Windows\notepad.exe", False)
        self.medal = True
        self.now = 1000.0

    def wait(self, seconds):
        self.now += seconds


@pytest.fixture
def pc(monkeypatch):
    from laughclipper import app as appmod
    fake = FakePC()
    monkeypatch.setattr(winutil, "foreground_app", lambda: fake.front)
    monkeypatch.setattr(winutil, "process_running", lambda exe: fake.medal)
    monkeypatch.setattr(winutil, "exe_description", lambda path: Path(path).stem)
    # a fake clock for the Controller only (patching time.time itself would fool everything else too)
    monkeypatch.setattr(appmod, "time", types.SimpleNamespace(time=lambda: fake.now, monotonic=time.monotonic))
    return fake


class FakeToast:
    """Stands in for the popup: just remembers which state the app put it in."""

    def __init__(self, mode):
        self.mode = mode
        self.game = None

    def set_saving(self):
        self.mode = "saving"

    def set_saved(self, game=None):
        self.mode, self.game = "saved", game

    def set_failed(self, seconds):
        self.mode = "failed"

    def set_bookmarked(self):
        self.mode = "bookmarked"

    def set_nomedal(self, seconds):
        self.mode = "nomedal"

    def set_pending(self, *a):
        pass

    def dismiss(self):
        self.mode = "closed"


@pytest.fixture
def ctrl(qapp, pc, monkeypatch, tmp_path):
    from laughclipper import app as appmod, clips
    from laughclipper.engine import Engine
    monkeypatch.setattr(Engine, "_listen", lambda *a: None)
    monkeypatch.setattr(clips, "HISTORY_PATH", tmp_path / "history.json")

    def show_toast(self, mode, label, sound="laughter", test=False):
        self.toasts.append((mode, sound))
        self.toast, self.toast_is_test = FakeToast(mode), test
    monkeypatch.setattr(appmod.Controller, "show_toast", show_toast)
    (tmp_path / "Clips").mkdir()
    c = appmod.Controller(qapp)
    S["clips_folder"] = str(tmp_path / "Clips")
    c.toasts = []
    c.save_timer.stop()
    yield c
    c.watcher.stop()
    c.tray.hide()
    # let timers this test started (like the 60 ms hotkey send) fire now, not in the next test
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(150, loop.quit)
    loop.exec()
