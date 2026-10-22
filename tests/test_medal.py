"""Reading Medal's hotkeys, spotting a recording, and bookmarking instead of clipping."""

import json

import pytest

from conftest import SENT
from laughclipper import medal
from laughclipper.config import S
from test_clipping import flush

GAME = ("out", "Game (Elgato Virtual Audio)")
MEDAL_HOTKEYS = [
    {"action": "clip;length=60", "type": "short_press", "device": "keyboard", "inputs": "SHIFT + F8"},
    {"action": "clip;length=30", "type": "short_press", "device": "keyboard", "inputs": "SHIFT + F7"},
    {"action": "segment_toggle", "type": "short_press", "device": "keyboard", "inputs": "ALT + F7"},
    {"action": "bookmark", "type": "short_press", "device": "keyboard", "inputs": "F8"},
    {"action": "clip;length=120", "type": "short_press", "device": "mouse", "inputs": "MOUSE4"},
    {"action": "callback;event=dismiss", "type": "short_press", "device": "keyboard", "inputs": "CONTROL + SHIFT + d"},
]


@pytest.fixture
def medal_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"recorder": {"triggerHotkeys": MEDAL_HOTKEYS}, "user": {"token": "secret"}}))
    monkeypatch.setattr(medal, "SETTINGS", path)
    return path


@pytest.mark.parametrize("medal_keys, ours", [
    ("SHIFT + F7", "shift+f7"), ("ALT + SHIFT + d", "alt+shift+d"), ("CONTROL + F12", "ctrl+f12"),
    ("F8", "f8"), (" ctrl+ a ", "ctrl+a"),
])
def test_hotkey_format(medal_keys, ours):
    assert medal.to_keyboard(medal_keys) == ours


def test_reads_medals_hotkeys(medal_settings):
    keys = medal.hotkeys()
    assert keys["bookmark"] == "f8" and keys["segment_toggle"] == "alt+f7"
    assert medal.clip_hotkeys(keys) == [(30, "shift+f7"), (60, "shift+f8")]     # the mouse one is skipped
    assert medal.bookmark_hotkey(keys) == "f8"
    assert "secret" not in json.dumps(keys)


def test_missing_or_broken_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(medal, "SETTINGS", tmp_path / "nope.json")
    assert medal.hotkeys() == {} and medal.clip_hotkeys() == [] and medal.bookmark_hotkey() == ""
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setattr(medal, "SETTINGS", tmp_path / "bad.json")
    assert medal.hotkeys() == {}


def test_recording_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(medal, "RECORDING_MIN_AGE", 0)
    assert medal.is_recording(tmp_path) is False             # no Temp folder
    (tmp_path / "Temp").mkdir()
    (tmp_path / "Temp" / "MedalTVRocketLeague20261022163843575.mp4.cicap").write_bytes(b"")
    assert medal.is_recording(tmp_path) is False             # only .icap counts
    (tmp_path / "Temp" / "MedalTVRocketLeague20261022163843575.mp4.icap").write_bytes(b"")
    assert medal.is_recording(tmp_path) is True
    monkeypatch.setattr(medal, "RECORDING_MIN_AGE", 60)
    assert medal.is_recording(tmp_path) is False             # just appeared: not long enough yet
    assert medal.is_recording(None) is False


def recording(monkeypatch, on=True):
    monkeypatch.setattr(medal, "is_recording", lambda root: on)


def test_bookmarks_while_recording(ctrl, qapp, monkeypatch):
    S["bookmark_when_recording"], S["bookmark_hotkey"] = True, "f8"
    S["hotkey"] = "shift+f7"
    recording(monkeypatch)
    ctrl.on_laugh(GAME, "laughter")
    ctrl.clip()
    flush(qapp)
    assert ctrl.toast.mode == "bookmarked" and SENT == ["f8"]
    assert not ctrl.watcher.active() and ctrl.history[-1]["status"] == "bookmarked"


def test_clips_as_usual_when_not_recording_or_turned_off(ctrl, qapp, monkeypatch):
    S["bookmark_hotkey"], S["hotkey"] = "f8", "shift+f7"
    recording(monkeypatch)
    ctrl.on_laugh(GAME, "laughter")               # recording, but the option is off
    ctrl.clip()
    flush(qapp)
    assert SENT == ["shift+f7"] and ctrl.toast.mode == "saving"
    S["bookmark_when_recording"] = True
    recording(monkeypatch, False)                 # option on, but not recording
    assert not ctrl.will_bookmark()


def test_uses_medals_bookmark_key_when_none_set(ctrl, medal_settings, monkeypatch):
    S["bookmark_when_recording"] = True
    recording(monkeypatch)
    assert ctrl.bookmark_key() == "f8" and ctrl.will_bookmark()
    medal_settings.write_text(json.dumps({"recorder": {"triggerHotkeys": []}}))
    assert ctrl.bookmark_key() == "" and not ctrl.will_bookmark()    # no bookmark key: clip instead


def settings_with(path, hotkeys):
    path.write_text(json.dumps({"recorder": {"triggerHotkeys": hotkeys}}))


def shown(widget):
    """Not hidden on purpose (pages that aren't showing hide everything on them, so isVisible won't do)."""
    return not widget.isHidden()


def window(ctrl):
    from laughclipper.settings_window import SettingsWindow
    w = SettingsWindow(ctrl)
    w.update_medal_keys()
    return w


def test_one_medal_clip_key_gets_a_button(ctrl, medal_settings):
    settings_with(medal_settings, [{"action": "clip", "device": "keyboard", "inputs": "CONTROL + F2"}])
    S["hotkey"] = "f8"
    w = window(ctrl)
    assert shown(w.use_medal_key) and not shown(w.medal_keys)
    assert "Ctrl + F2" in w.hotkey_row.sub.text()
    w.use_medal_key.click()
    assert S["hotkey"] == "ctrl+f2" and not shown(w.use_medal_key)
    assert w.hotkey_row.sub.text() == "Matches Medal's clip hotkey."


def test_several_medal_clip_keys_get_a_menu(ctrl, medal_settings):
    S["hotkey"] = "f8"
    w = window(ctrl)
    assert shown(w.medal_keys) and w.medal_keys.count() == 3          # "Use Medal's…" + 30 s + 60 s
    assert "bookmark hotkey" in w.hotkey_row.sub.text()                      # F8 is Medal's bookmark key
    w.medal_key_chosen(2)
    assert S["hotkey"] == "shift+f8" and "60 s" in w.hotkey_row.sub.text()


def test_no_medal_settings_asks_you_to_press_keys(ctrl, tmp_path, monkeypatch):
    monkeypatch.setattr(medal, "SETTINGS", tmp_path / "none.json")
    w = window(ctrl)
    assert not shown(w.medal_keys) and not shown(w.use_medal_key)
    assert "press the clip hotkey" in w.hotkey_row.sub.text()
    assert w.bm_keycap.text() == "Not set" and "Not found" in w.bm_key_row.sub.text()


def test_bookmark_key_can_be_set_by_hand(ctrl, medal_settings):
    w = window(ctrl)
    assert w.bm_keycap.text() == "F8" and "Read from Medal" in w.bm_key_row.sub.text()
    ctrl.apply_recorded("bookmark_hotkey", "ctrl+b")          # what pressing Ctrl+B in the recorder does
    assert S["bookmark_hotkey"] == "ctrl+b" and ctrl.bookmark_key() == "ctrl+b"
    assert w.bm_keycap.text() == "Ctrl + B" and shown(w.use_medal_bm)
    w.use_medal_bm.click()
    assert S["bookmark_hotkey"] == "" and ctrl.bookmark_key() == "f8"


def test_recording_state_is_tracked(ctrl, pc, monkeypatch):
    recording(monkeypatch)
    ctrl.check_medal()
    assert ctrl.recording
    S["bookmark_when_recording"] = True
    ctrl.update_tray()
    assert "laughs add bookmarks" in ctrl.tray.toolTip()
    recording(monkeypatch, False)
    ctrl.check_medal()
    assert not ctrl.recording
