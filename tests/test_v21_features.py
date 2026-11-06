"""Group laughs, hype phrases and the controller/wheel clip button."""

import json

import pytest

from conftest import SENT
from laughclipper import config, gamepad
from laughclipper.config import S
from laughclipper.engine import Engine, TriggerWindow
from test_clipping import flush

MIC = ("mic", "")
VOICE = ("out", "Voice Chat (Elgato Virtual Audio)")
GAME = ("out", "Game (Elgato Virtual Audio)")
PAD = {"id": "VID_045E&PID_028E", "name": "Xbox 360 controller", "button": "3"}


# ---------------------------------------------------------------- group laughs
def test_together_needs_your_mic_and_another_source():
    e = Engine()
    e.threads = {MIC: 1, VOICE: 1, GAME: 1}
    e.near = {VOICE: 100.0}
    assert e.together(MIC, now=100.5) == [VOICE]
    assert e.together(MIC, now=102.0) == []                  # too long ago
    e.near = {GAME: 100.0}
    assert e.together(VOICE, now=100.2) == []                # two outputs could be the same audio twice
    e.near = {MIC: 100.0}
    assert e.together(GAME, now=100.2) == [MIC]
    e.threads = {GAME: 1}
    assert e.together(GAME, now=100.2) == []                 # the mic isn't being listened to any more


def test_group_bar_is_lower_and_shorter():
    """With the default 1 s "length needed", a group laugh needs 2 windows at 75% instead of 4 full hits."""
    need = round(S["min_laugh"] / 2 / 0.25)
    w = TriggerWindow()
    assert w.feed("laughter", need) is None and w.feed("laughter", need) == "laughter"


def test_group_laugh_popup(ctrl):
    ctrl.on_group_laugh(VOICE, "laughter", [MIC])
    t = ctrl.toast
    assert ctrl.toasts == [("ask", "laughter")]
    assert t.title == "Group laugh" and t.label == "You + Voice Chat"
    assert ctrl.moment["source"] == "You + Voice Chat"
    ctrl.toast = None
    ctrl.last_prompt = 0
    ctrl.on_group_laugh(MIC, "cheering", [GAME])
    assert ctrl.toast.title == "Group cheering"


def test_group_laughs_setting_migrates():
    assert config.DEFAULTS["group_laughs"] is False


# ---------------------------------------------------------------- hype phrases
def test_clip_that_saves_straight_away(ctrl, qapp):
    ctrl.on_voice("clip", "clip that")
    flush(qapp)
    assert SENT == ["f8"] and ctrl.toasts[0][0] == "saving"
    assert ctrl.toast.title == "“Clip that”"


def test_clip_that_during_a_popup_presses_clip(ctrl, qapp):
    ctrl.on_laugh(GAME, "laughter")
    ctrl.on_voice("clip", "clip that")
    flush(qapp)
    assert SENT == ["f8"] and len(ctrl.toasts) == 1


def test_hype_phrase_is_a_moment(ctrl, qapp):
    ctrl.on_voice("hype", "no way")
    assert ctrl.toasts == [("ask", "hype")] and ctrl.toast.title == "“No way”"
    assert ctrl.moment["source"] == "Your mic"
    ctrl.on_voice("hype", "let's go")                        # same moment: no second popup
    assert len(ctrl.toasts) == 1
    flush(qapp)
    assert SENT == []


def test_not_funny_on_a_hype_phrase_just_skips(ctrl):
    ctrl.on_voice("hype", "no way")
    ctrl.on_not_funny()
    assert S["sound_levels"] == {} and ctrl.history[-1]["status"] == "not_funny"


def test_hype_phrases_respect_pause(ctrl, qapp):
    S["running"] = False
    ctrl.on_voice("clip", "clip that")
    ctrl.on_voice("hype", "no way")
    flush(qapp)
    assert SENT == [] and ctrl.toasts == []


# ---------------------------------------------------------------- controller
class FakePads:
    """Stands in for WinMM: one Xbox pad whose held buttons the test sets."""

    def __init__(self):
        self.down = set()
        self.plugged = True

    def devices(self):
        return [(0, PAD["id"], PAD["name"], True)] if self.plugged else []

    def buttons(self, index, has_pov):
        return set(self.down) if self.plugged else None


@pytest.fixture
def pad(ctrl):
    fake = FakePads()
    ctrl.pads.backend = fake
    yield fake
    ctrl.pads.set_active(False)


def press(ctrl, fake, button):
    ctrl.pads.read()
    fake.down.add(button)
    ctrl.pads.read()
    fake.down.discard(button)
    ctrl.pads.read()


def test_buttons_fire_once_per_press(ctrl, pad):
    got = []
    ctrl.pads.pressed.connect(got.append)
    pad.down = {"1"}                                           # held before we start: doesn't count
    ctrl.pads.set_active(True)
    ctrl.pads.read()
    ctrl.pads.read()
    assert got == []
    pad.down = {"1", "up"}
    ctrl.pads.read()
    ctrl.pads.read()
    assert got == [{**PAD, "button": "up"}]


def test_only_reads_controllers_when_needed(ctrl, pad):
    ctrl.update_pads()
    assert not ctrl.pads.poll.isActive()
    ctrl.set("pad_button", PAD)
    assert ctrl.pads.poll.isActive()
    ctrl.set("pad_button", {})
    assert not ctrl.pads.poll.isActive()


def test_assign_a_button(ctrl, pad):
    ctrl.capture_pad_button()
    assert ctrl.pads.poll.isActive()
    press(ctrl, pad, "3")
    assert S["pad_button"] == PAD and not ctrl.pad_capture


def test_assign_gives_up_after_a_while(ctrl, pad):
    ctrl.capture_pad_button()
    ctrl.pad_capture_timer.timeout.emit()
    assert not ctrl.pad_capture and not ctrl.pads.poll.isActive() and S["pad_button"] == {}


def test_button_clips_while_a_popup_shows(ctrl, pad, qapp):
    ctrl.set("pad_button", PAD)
    press(ctrl, pad, "3")                                      # no popup: nothing (default mode)
    flush(qapp)
    assert SENT == [] and ctrl.toasts == []
    ctrl.on_laugh(GAME, "laughter")
    press(ctrl, pad, "2")                                      # some other button: nothing
    flush(qapp)
    assert SENT == []
    press(ctrl, pad, "3")
    flush(qapp)
    assert SENT == ["f8"]


def test_button_any_time(ctrl, pad, qapp):
    ctrl.set("pad_button", PAD)
    ctrl.set("pad_mode", "always")
    press(ctrl, pad, "3")
    flush(qapp)
    assert SENT == ["f8"] and ctrl.toasts == [("saving", "button")]
    assert ctrl.moment["source"] == "Xbox 360 controller"


def test_unplugged_controller_is_skipped(ctrl, pad):
    ctrl.set("pad_button", PAD)
    pad.plugged = False
    ctrl.pads.read()
    assert ctrl.pads.last == {}


def test_button_names():
    assert gamepad.label(PAD) == "X on Xbox 360 controller"
    assert gamepad.button_name({"id": "VID_054C&PID_0CE6", "button": "2"}) == "Cross"
    assert gamepad.button_name({"id": "VID_046D&PID_C266", "button": "7"}) == "Button 7"
    assert gamepad.button_name({"id": "VID_046D&PID_C266", "button": "left"}) == "D-pad left"
    assert gamepad.tidy_name("Controller (XBOX 360 For Windows)") == "Xbox 360 controller"
    assert gamepad.same_button(PAD, {**PAD, "name": "renamed"}) and not gamepad.same_button(PAD, {})


@pytest.mark.parametrize("saved, expect", [
    ({"id": "VID_045E&PID_028E", "button": 3}, {"id": "VID_045E&PID_028E", "name": "", "button": "3"}),
    ({"button": "3"}, {}),
    ("A", {}),
    ({"id": "x", "button": True}, {}),
])
def test_pad_button_setting_is_cleaned(saved, expect):
    config.SETTINGS_PATH.write_text(json.dumps({"pad_button": saved, "pad_mode": "sometimes"}), encoding="utf-8")
    config.load()
    assert S["pad_button"] == expect and S["pad_mode"] == "popup"


def test_settings_page_rows(ctrl, pad):
    from laughclipper.settings_window import SettingsWindow
    w = SettingsWindow(ctrl)
    try:
        assert w.pad_keycap.text() == "Not set" and w.pad_clear.isHidden()
        w.capture_pad()
        assert w.pad_keycap.text() == "Press a button…"
        press(ctrl, pad, "3")
        assert w.pad_keycap.text() == "X" and not w.pad_clear.isHidden()
        assert "Xbox 360 controller" in w.pad_row.sub.text()
        ctrl.set("pad_mode", "always")
        assert w.pad_mode.index == 1
        assert not w.hype_row.isHidden() and "group_laughs" in w.switches
    finally:
        w.close()
        w.deleteLater()


# ---------------------------------------------------------------- group laughs, end to end
class FakeRecorder:
    def __init__(self, stop):
        self.stop = stop

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def record(self, numframes):
        import time
        import numpy as np
        time.sleep(0.002)
        return np.full((numframes, 1), 0.1, np.float32)       # loud enough not to count as silence


class FakeDevice:
    name = "fake"

    def recorder(self, samplerate):
        return FakeRecorder(None)


class FakeSoundcard:
    def all_microphones(self):
        return [type("M", (FakeDevice,), {"name": ""})()]

    def default_microphone(self):
        return FakeDevice()

    def all_speakers(self):
        return [type("S", (), {"name": VOICE[1], "id": 1})()]

    def get_microphone(self, id, include_loopback):
        return FakeDevice()


def run_engine(monkeypatch, qapp, score, seconds=1.5):
    """Both sources hear laughter at `score` (the trigger level is 0.30); returns what the engine emitted."""
    import threading
    import time
    from laughclipper import engine
    monkeypatch.setattr(engine, "get_soundcard", lambda: FakeSoundcard())
    monkeypatch.setattr(engine, "make_classifier", lambda: object())
    monkeypatch.setattr(engine, "classify", lambda c, w: ({**dict.fromkeys(engine.SOUNDS, 0.0), "laughter": score},
                                                          "Laughter"))
    e = Engine()
    got, lock = [], threading.Lock()

    def note(*a):
        with lock:
            got.append(a)
    e.laugh.connect(lambda k, s: note("laugh", k, s))
    e.group.connect(lambda k, s, o: note("group", k, s, o))
    S["outputs"], S["mic_device"] = [VOICE[1]], ""
    e.sync()
    end = time.monotonic() + seconds
    while time.monotonic() < end:                      # the signals are queued to this thread: let them in
        qapp.processEvents()
        time.sleep(0.01)
    e.stop_all()
    time.sleep(0.1)
    qapp.processEvents()
    return got


def test_engine_group_laugh_fires_where_neither_alone_would(monkeypatch, qapp):
    S["group_laughs"] = True
    got = run_engine(monkeypatch, qapp, 0.25)                       # strength 0.83: not a hit on its own
    assert any(g[0] == "group" for g in got) and not any(g[0] == "laugh" for g in got)
    first = next(g for g in got if g[0] == "group")
    assert {first[1][0], first[3][0][0]} == {"mic", "out"}


def test_engine_group_laughs_off(monkeypatch, qapp):
    assert run_engine(monkeypatch, qapp, 0.25) == []


def test_engine_one_source_alone_is_not_a_group(monkeypatch, qapp):
    S["group_laughs"], S["listen_mic"] = True, False
    assert run_engine(monkeypatch, qapp, 0.25) == []


def test_engine_harness_still_hears_a_normal_laugh(monkeypatch, qapp):
    """So the "nothing fired" tests above mean something: a full-strength laugh does fire."""
    got = run_engine(monkeypatch, qapp, 0.35)
    assert any(g[0] == "laugh" for g in got)
