"""Smart clip length (pick Medal's 30/60/120 s clip hotkey to fit the moment) and "What's new"."""

import json

import pytest

from conftest import SENT
from laughclipper import app as appmod, config, medal, whatsnew
from laughclipper.config import S
from test_clipping import flush

GAME = ("out", "Game (Elgato Virtual Audio)")
THREE = [{"action": f"clip;length={n}", "device": "keyboard", "inputs": k}
         for n, k in ((30, "SHIFT + F7"), (60, "SHIFT + F8"), (120, "SHIFT + F9"))]


@pytest.fixture
def medal_keys(tmp_path, monkeypatch):
    def use(hotkeys):
        path = tmp_path / "medal.json"
        path.write_text(json.dumps({"recorder": {"triggerHotkeys": hotkeys}}))
        monkeypatch.setattr(medal, "SETTINGS", path)
    use(THREE)
    return use


def laugh_lasting(ctrl, pc, seconds):
    ctrl.on_laugh(GAME, "laughter")
    pc.wait(seconds - S["min_laugh"])


@pytest.mark.parametrize("seconds, key, length", [
    (5, "shift+f7", 30),         # 5 s + 10 s build-up fits in 30 s
    (25, "shift+f8", 60),        # 35 s needs the 60 s clip
    (70, "shift+f9", 120),
    (300, "shift+f9", 120),      # longer than any clip: use the longest
])
def test_picks_the_clip_that_fits(ctrl, pc, qapp, medal_keys, seconds, key, length):
    S["hotkey"], S["smart_length"] = "shift+f7", True
    laugh_lasting(ctrl, pc, seconds)
    ctrl.clip()
    flush(qapp)
    assert SENT == [key] and ctrl.moment["length"] == length


def laughs_for(ctrl, pc, laughing, then_wait=5):
    """A laugh that goes on for `laughing` seconds, then `then_wait` seconds of quiet before the clip."""
    import threading
    ctrl.engine.threads[GAME] = threading.Event()
    ctrl.on_laugh(GAME, "laughter")
    began = ctrl.moment["began"]
    ctrl.engine.activity[GAME] = began + laughing          # the last laugh sound
    pc.now = began + laughing + then_wait


@pytest.mark.parametrize("long_laugh, laughing, length", [
    (8, 5, 30),        # short laugh: your own 30 s hotkey
    (8, 9, 60),        # past 8 s of laughing: one step up
    (8, 17, 120),      # past 16 s: two steps up
    (8, 40, 120),      # can't go past the longest
    (15, 12, 30),      # a higher setting needs a longer laugh
    (3, 4, 60),
])
def test_longer_clip_after_laughing_for(ctrl, pc, qapp, medal_keys, long_laugh, laughing, length):
    S["hotkey"], S["smart_length"], S["long_laugh"] = "shift+f7", True, long_laugh
    laughs_for(ctrl, pc, laughing)
    assert ctrl.clip_key()[1] == length


def test_the_wait_after_laughing_doesnt_count_as_laughing(ctrl, pc, medal_keys):
    S["hotkey"], S["smart_length"], S["long_laugh"] = "shift+f7", True, 8
    laughs_for(ctrl, pc, 4, then_wait=12)                  # 4 s laugh, 12 s quiet: still a short laugh
    assert ctrl.laugh_seconds() == pytest.approx(4)
    assert ctrl.clip_key() == ("shift+f7", 30)             # 16 s + 10 s build-up still fits in 30 s


def test_never_shorter_than_your_own_hotkey(ctrl, pc, medal_keys):
    S["hotkey"], S["smart_length"] = "shift+f8", True     # you chose the 60 s clip
    laughs_for(ctrl, pc, 2)
    assert ctrl.clip_key() == ("shift+f8", 60)


def test_off_by_default_uses_your_hotkey(ctrl, pc, qapp, medal_keys):
    S["hotkey"] = "shift+f7"
    laugh_lasting(ctrl, pc, 70)
    ctrl.clip()
    flush(qapp)
    assert SENT == ["shift+f7"] and ctrl.moment["length"] == 30


def test_needs_several_medal_lengths_and_a_matching_hotkey(ctrl, pc, medal_keys):
    S["smart_length"] = True
    S["hotkey"] = "ctrl+f1"                       # not one of Medal's: we don't know its length
    laugh_lasting(ctrl, pc, 70)
    assert ctrl.clip_key() == ("ctrl+f1", 0)
    medal_keys(THREE[:1])                         # only one clip length in Medal
    S["hotkey"] = "shift+f7"
    assert ctrl.clip_key() == ("shift+f7", 30)


def test_length_reaches_the_popup_and_history(ctrl, pc, medal_keys, tmp_path):
    from test_clip_check import medal_saves
    S["hotkey"], S["smart_length"] = "shift+f7", True
    laugh_lasting(ctrl, pc, 25)
    ctrl.clip()
    medal_saves(S["clips_folder"])
    ctrl.watcher._poll()
    assert ctrl.toast.length == 60 and ctrl.history[-1]["length"] == 60


def window(ctrl):
    from laughclipper.settings_window import SettingsWindow
    w = SettingsWindow(ctrl)
    w.update_medal_keys()
    return w


def test_warns_when_a_moment_outlasts_the_clip(ctrl, medal_keys):
    S["hotkey"], S["auto_clip"], S["clip_delay"], S["max_wait"] = "shift+f7", True, 5, 30
    w = window(ctrl)
    assert w.smart_row.isEnabled()                          # Medal has 3 lengths and ours is one of them
    assert not w.length_warn.isHidden()                     # 10 s + 1 s + 30 s > 30 s
    assert "turn on" in w.length_warn.sub.text()
    ctrl.set("smart_length", True)
    assert w.length_warn.isHidden()                         # up to 120 s now
    ctrl.set("max_wait", 0)
    assert not w.length_warn.isHidden() and "Never" in w.length_warn.sub.text()


def test_greyed_out_with_only_one_clip_length(ctrl, medal_keys):
    medal_keys(THREE[:1])
    S["hotkey"] = "shift+f7"
    w = window(ctrl)
    assert not w.smart_row.isHidden() and not w.smart_row.isEnabled() and not w.long_row.isEnabled()
    assert "only has one clip length" in w.smart_row.sub.text() and "60 s and 120 s" in w.smart_row.sub.text()


def test_greyed_out_until_the_hotkey_is_one_of_medals(ctrl, medal_keys):
    S["hotkey"] = "ctrl+f1"
    w = window(ctrl)
    assert not w.smart_row.isEnabled() and "one of Medal's" in w.smart_row.sub.text()
    w.medal_key_chosen(1)                                   # pick Medal's 30 s hotkey from the menu
    assert w.smart_row.isEnabled() and not w.long_row.isEnabled()      # slider waits for the switch
    ctrl.set("smart_length", True)
    assert w.long_row.isEnabled()


def test_no_warning_when_it_fits(ctrl, medal_keys):
    S["hotkey"], S["auto_clip"], S["clip_delay"], S["max_wait"] = "shift+f7", True, 5, 10
    w = window(ctrl)
    assert w.length_warn.isHidden()                         # 10 + 1 + 10 = 21 s < 30 s


def test_whats_new_notes(tmp_path, monkeypatch):
    (tmp_path / "release-notes").mkdir()
    (tmp_path / "release-notes" / "v9.9.9.md").write_text("## What's new\n\n### Thing\n- x\n\n## Fixes\n- y\n")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    text = whatsnew.notes("9.9.9")
    assert text.startswith("### Thing") and "### Fixes" in text and "What's new" not in text
    assert whatsnew.notes("0.0.1") == ""


def test_notes_become_readable_html():
    out = whatsnew.to_html("### New\n- A **bold** move with `code`,\n  wrapping on.\n- Two & <three>\n\nThanks.")
    assert "<b>bold</b>" in out and "<code>code</code>" in out and "wrapping on." in out
    assert out.count("<li") == 2 and "&amp; &lt;three&gt;" in out and "<p style='margin: 8px 0;'>Thanks.</p>" in out


def test_whats_new_shows_once_after_an_update(tmp_path, monkeypatch):
    (tmp_path / "release-notes").mkdir()
    (tmp_path / "release-notes" / "v2.0.0.md").write_text("- new")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert appmod.whats_new_due(True, "2.0.0") is False and S["last_seen_version"] == "2.0.0"   # new install
    S["last_seen_version"] = "1.9.0"
    assert appmod.whats_new_due(False, "2.0.0") is True                                         # updated
    assert appmod.whats_new_due(False, "2.0.0") is False                                        # only once
    S["last_seen_version"] = ""
    assert appmod.whats_new_due(False, "2.1.0") is False      # no notes for it (dev builds): stay quiet


def test_whats_new_window_opens_cleanly(ctrl, qapp, monkeypatch):
    from test_windows import stray_windows_while
    from laughclipper.whatsnew import WhatsNewWindow
    assert stray_windows_while(monkeypatch, qapp, lambda: WhatsNewWindow(ctrl, "1.4.0")) == []
