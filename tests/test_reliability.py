"""2.1.1: the clip check, settings backup, audio recovery, admin games, updates, and the health card."""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from conftest import SENT
from laughclipper import clips, config, updater
from laughclipper.config import S
from laughclipper.engine import STALL_SECONDS, Engine
from test_clip_check import medal_saves
from test_clipping import flush

VOICE = ("out", "Voice Chat (Elgato Virtual Audio)")
MIC = ("mic", "")


# ---------------------------------------------------------------- clip check
def test_clip_found_when_the_folder_listing_is_stale(ctrl, monkeypatch):
    """On NTFS a game folder's modified time in its parent's listing lags behind new files in it; that made
    saved clips look like they hadn't saved. Fake the lag: scandir entries report an hour-old time."""
    folder = Path(S["clips_folder"])
    (folder / "Rocket League").mkdir()
    real_scandir = os.scandir

    class Stale:
        def __init__(self, e):
            self.e, self.path, self.name = e, e.path, e.name

        def is_dir(self):
            return self.e.is_dir()

        def is_file(self):
            return self.e.is_file()

        def stat(self):
            st = self.e.stat()
            if self.e.is_dir():
                return os.stat_result((st.st_mode, 0, 0, 0, 0, 0, 0, 0, st.st_mtime - 3600, 0))
            return st

    monkeypatch.setattr(clips.os, "scandir", lambda p: [Stale(e) for e in real_scandir(p)])
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    medal_saves(folder)
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saved" and ctrl.history[-1]["status"] == "saved"


def test_slow_medal_is_put_right_afterwards(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "failed" and ctrl.history[-1]["status"] == "failed"
    assert ctrl.watcher.active()                           # still looking, in case Medal was only slow
    f = medal_saves(S["clips_folder"])
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saved" and not ctrl.watcher.active()
    assert ctrl.history[-1]["status"] == "saved" and ctrl.history[-1]["file"] == str(f)
    assert json.loads(clips.HISTORY_PATH.read_text())[-1]["status"] == "saved"


def test_gives_up_for_good_eventually(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    assert not ctrl.watcher.active() and ctrl.history[-1]["status"] == "failed"


def test_late_clip_doesnt_touch_a_newer_entry(ctrl, pc):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    ctrl.clear_history()
    ctrl.on_clip_late(str(Path(S["clips_folder"]) / "x.mp4"))
    assert ctrl.history == []


# ---------------------------------------------------------------- settings backup
def test_good_settings_are_backed_up_and_restored():
    S["threshold"] = 0.42
    config.save()
    assert config.load()
    assert config.BACKUP_PATH.exists()
    config.SETTINGS_PATH.write_text('{"threshold": 0.4', encoding="utf-8")       # cut off mid-write
    S["threshold"] = 0.3
    assert config.load() is True                           # not treated as a first run
    assert S["threshold"] == 0.42
    assert config.BAD_PATH.exists()                        # the damaged file is kept
    assert json.loads(config.SETTINGS_PATH.read_text())["threshold"] == 0.42


@pytest.mark.parametrize("junk", ["", "[]", "null", "\x00\x00\x00"])
def test_junk_settings_without_a_backup_is_a_first_run(junk):
    config.SETTINGS_PATH.write_text(junk, encoding="utf-8")
    assert config.load() is False
    assert S["threshold"] == config.DEFAULTS["threshold"]


# ---------------------------------------------------------------- audio
def test_stalled_source_is_restarted(monkeypatch):
    started = []
    monkeypatch.setattr(Engine, "_listen", lambda self, key, stop: started.append(key))
    e = Engine()
    S["outputs"], S["listen_mic"] = [VOICE[1]], True
    e.sync()
    time.sleep(0.05)
    assert sorted(started) == sorted([MIC, VOICE])
    old_stop = e.threads[VOICE]
    e.alive = {MIC: time.monotonic(), VOICE: time.monotonic() - STALL_SECONDS - 1}
    assert e.restart_stalled() == [VOICE]
    time.sleep(0.05)
    assert old_stop.is_set() and e.threads[VOICE] is not old_stop and started.count(VOICE) == 2
    assert e.levels[VOICE] == "error"
    assert e.restart_stalled() == []
    e.stop_all()


def test_problems_only_after_a_while():
    e = Engine()
    e.threads = {MIC: object(), VOICE: object()}
    e.broken = {MIC: time.time() - 60, VOICE: time.time() - 2, ("out", "gone"): time.time() - 60}
    assert e.problems() == [MIC]


def test_broken_source_shows_in_tray_and_health(ctrl):
    ctrl.engine.threads = {MIC: object()}
    ctrl.engine.broken = {MIC: time.time() - 60}
    seen = []
    ctrl.changed.connect(seen.append)
    ctrl.check_health()
    assert ctrl.source_problems == [MIC] and "sources" in seen
    assert "Can't hear: Your mic" in ctrl.tray.toolTip()
    assert dict((k, ok) for k, ok, _ in ctrl.health())["audio"] is False
    ctrl.engine.broken = {}
    ctrl.check_health()
    assert ctrl.source_problems == [] and "Can't hear" not in ctrl.tray.toolTip()
    ctrl.engine.threads = {}


def test_hotkeys_come_back_after_sleep(ctrl, monkeypatch):
    calls = []
    monkeypatch.setattr(ctrl, "register_hotkey", lambda: calls.append(1))
    ctrl.check_health()
    assert calls == []
    ctrl.health_at = time.monotonic() - 120                 # the timer didn't run for two minutes: asleep
    ctrl.check_health()
    assert calls == [1]


# ---------------------------------------------------------------- admin games
def test_admin_game_warns_once(ctrl, pc, monkeypatch):
    from laughclipper import winutil
    S["game_only"] = True
    S["games"] = [{"exe": "game.exe", "name": "Some Game"}]
    monkeypatch.setattr(winutil, "runs_elevated", lambda pid: True)
    shown = []
    monkeypatch.setattr(ctrl.tray, "showMessage", lambda *a: shown.append(a[0]))
    pc.front = (4242, r"C:\Games\game.exe", True)
    ctrl.check_game()
    ctrl.check_game()
    assert ctrl.game_admin and shown == ["Some Game runs as administrator"]
    assert dict((k, ok) for k, ok, _ in ctrl.health())["admin"] is False
    monkeypatch.setattr(winutil, "runs_elevated", lambda pid: False)
    pc.front = (4243, r"C:\Games\game.exe", True)           # restarted normally
    ctrl.check_game()
    assert not ctrl.game_admin


# ---------------------------------------------------------------- updates
RELEASE = {"tag_name": "v99.0.0", "html_url": "",
           "assets": [{"name": "LaughClipper-Setup-99.0.0.exe", "browser_download_url": "http://x/y.exe",
                       "size": 5, "digest": "sha256:" + hashlib.sha256(b"hello").hexdigest()}]}


def states(u):
    got = []
    u.state.connect(lambda s, i: got.append((s, i)))
    return got


def test_automatic_check_retries(monkeypatch):
    u = updater.Updater()
    got = states(u)
    tries = []

    def fetch():
        tries.append(1)
        if len(tries) < 3:
            raise OSError("getaddrinfo failed")
        return RELEASE
    monkeypatch.setattr(u, "fetch_latest", fetch)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    u._check(False)
    assert len(tries) == 3 and got[-1][0] == "available"
    assert got[-1][1]["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_manual_check_fails_straight_away(monkeypatch):
    u = updater.Updater()
    got = states(u)
    monkeypatch.setattr(u, "fetch_latest", lambda: (_ for _ in ()).throw(OSError("offline")))
    u._check(True)
    assert got[-1][0] == "error" and got[-1][1]["manual"] is True


def test_download_is_verified(tmp_path):
    good = tmp_path / "a.exe"
    good.write_bytes(b"hello")
    info = {"size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}
    updater.Updater.verify(good, info)
    with pytest.raises(ValueError, match="incomplete"):
        updater.Updater.verify(good, {**info, "size": 9})
    good.write_bytes(b"hellp")
    with pytest.raises(ValueError, match="doesn't match"):
        updater.Updater.verify(good, info)
    updater.Updater.verify(good, {"size": 0, "sha256": ""})    # an old release with nothing to check against


def test_bad_download_is_thrown_away(monkeypatch, tmp_path):
    import io
    u = updater.Updater()
    got = states(u)
    u.latest = {"version": "99.0.0", "url": "http://x/y.exe", "size": 5, "sha256": "0" * 64}

    class Resp(io.BytesIO):
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout: Resp(b"hello"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    u._download()
    assert got[-1][0] == "error" and list(tmp_path.iterdir()) == []
    u.latest["sha256"] = hashlib.sha256(b"hello").hexdigest()
    u._download()
    assert got[-1][0] == "installing" and [p.name for p in tmp_path.iterdir()] == ["LaughClipper-Setup-99.0.0.exe"]


# ---------------------------------------------------------------- health card, test clip, diagnostics
def test_health_all_good_and_test_clip(ctrl, qapp):
    S["running"] = False                                    # Test Clip works even while paused
    assert all(ok is not False for _, ok, _ in ctrl.health())
    ctrl.test_clip()
    flush(qapp)
    assert SENT == ["f8"] and ctrl.toasts == [("saving", "test")]
    medal_saves(S["clips_folder"])
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saved" and ctrl.history[-1]["sound"] == "test"


def test_health_lists_what_is_wrong(ctrl, pc, tmp_path):
    pc.medal = False
    ctrl.check_medal()
    S["clips_folder"] = str(tmp_path / "nope")
    S["listen_mic"], S["outputs"] = False, []
    bad = {k for k, ok, _ in ctrl.health() if ok is False}
    assert bad == {"medal", "folder", "sources"}


def test_health_card_on_the_general_page(ctrl, pc):
    from laughclipper.settings_window import SettingsWindow
    w = SettingsWindow(ctrl)
    try:
        assert w.health_row.title.text() == "Everything's working" and w.health_bad == []
        S["listen_mic"], S["outputs"] = False, []
        ctrl.changed.emit("outputs")
        assert w.health_row.title.text() == "1 thing needs attention" and w.health_bad == ["sources"]
        w.copy_diagnostics()
        from PySide6.QtGui import QGuiApplication
        text = QGuiApplication.clipboard().text()
        assert "BAD sources" in text and "Laugh Clipper" in text
    finally:
        w.close()
        w.deleteLater()


def test_diagnostics_has_nothing_personal(ctrl):
    S["games"] = [{"exe": "secretgame.exe", "name": "Secret Game"}]
    config.LOG_PATH.write_text(f"2026-01-01 00:00:00,000 WARNING x: couldn't open {Path.home()}\\file\n",
                               encoding="utf-8")
    text = ctrl.diagnostics()
    assert "Secret Game" not in text and str(Path.home()) not in text and S["clips_folder"] not in text
    assert "~\\file" in text
