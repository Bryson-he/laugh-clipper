"""Clip confirmation (a new file in Medal's folder) and the clip history."""

import json
import time
from pathlib import Path

from conftest import SENT
from laughclipper import clips
from laughclipper.config import S
from test_clipping import flush

VOICE = ("out", "Voice Chat (Elgato Virtual Audio)")


def medal_saves(folder, game="Rocket League"):
    d = Path(folder) / game
    d.mkdir(exist_ok=True)
    f = d / f"MedalTV{game.replace(' ', '')}{time.strftime('%Y%m%d%H%M%S')}000.mp4"
    f.write_bytes(b"\0" * 1024)
    return f


def test_saved_only_once_the_file_appears(ctrl, qapp):
    ctrl.on_laugh(VOICE, "cheering")
    ctrl.clip()
    assert ctrl.toast.mode == "saving" and ctrl.watcher.active()
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saving"                     # nothing there yet
    f = medal_saves(S["clips_folder"])
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saved" and ctrl.toast.game == "Rocket League"
    flush(qapp)
    assert SENT == ["f8"]
    [entry] = ctrl.history
    assert entry["status"] == "saved" and entry["file"] == str(f)
    assert entry["sound"] == "cheering" and entry["source"] == "Voice Chat" and entry["game"] == "Rocket League"
    assert json.loads(clips.HISTORY_PATH.read_text())[0]["file"] == str(f)


def test_old_clips_dont_count(ctrl):
    medal_saves(S["clips_folder"])                        # a clip from before the hotkey
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.watcher.since = time.time() + 5                  # (creation times can't be faked, so move "now")
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "saving"
    assert clips.new_clips(Path(S["clips_folder"]), time.time() - 30) != []


def test_no_file_means_didnt_save(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    assert ctrl.toast.mode == "failed" and not ctrl.watcher.active()
    assert ctrl.history[-1]["status"] == "failed"


def test_no_folder_falls_back_to_trusting_the_hotkey(ctrl, tmp_path):
    S["clips_folder"] = str(tmp_path / "missing")
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    assert ctrl.toast.mode == "saved" and not ctrl.watcher.active()
    assert ctrl.history[-1]["status"] == "unchecked"


def test_check_can_be_turned_off(ctrl):
    S["confirm_clips"] = False
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    assert ctrl.toast.mode == "saved" and ctrl.history[-1]["status"] == "unchecked"


def test_our_own_hotkey_echo_is_ignored(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.send_hotkey()
    ctrl.on_hotkey_pressed()                 # the echo of our keyboard.send()
    assert ctrl.toast.mode == "saving" and len(ctrl.history) == 0


def test_pressing_medals_key_yourself_is_checked_too(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    assert ctrl.toast.mode == "ask"
    ctrl.on_hotkey_pressed()
    assert ctrl.toast.mode == "saving" and ctrl.watcher.active()
    medal_saves(S["clips_folder"])
    ctrl.watcher._poll()
    assert ctrl.history[-1]["status"] == "saved"


def test_test_popup_isnt_logged(ctrl):
    ctrl.test_toast()
    ctrl.clip()
    assert ctrl.toast.mode == "saved" and ctrl.history == [] and SENT == []


def test_history_is_kept_short_and_survives_restart(ctrl, qapp):
    ctrl.history = [clips.new_entry({}, "saved", "x.mp4") for _ in range(clips.HISTORY_MAX)]
    S["confirm_clips"] = False
    ctrl.on_laugh(VOICE, "gasp")
    ctrl.clip()
    assert len(ctrl.history) == clips.HISTORY_MAX and ctrl.history[-1]["sound"] == "gasp"
    assert clips.load_history()[-1]["sound"] == "gasp"
    ctrl.clear_history()
    assert clips.load_history() == []


def test_broken_history_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "HISTORY_PATH", tmp_path / "history.json")
    (tmp_path / "history.json").write_text("{not json")
    assert clips.load_history() == []
    (tmp_path / "history.json").write_text('[{"time": "2026-10-21T21:00:00"}, 5, "x"]')
    assert len(clips.load_history()) == 1
