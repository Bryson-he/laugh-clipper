"""Meters show loudness, the app listens while its windows are open, and the optional chime."""

import io
import wave

import numpy as np
import pytest

from laughclipper import sounds
from laughclipper.config import S
from laughclipper.widgets import loudness

DISCORD = (12, r"C:\Discord\Discord.exe", False)


@pytest.mark.parametrize("rms, expected", [(0, 0.0), (None, 0.0), (0.001, 0.0), (0.5, 1.0), (1.0, 1.0)])
def test_loudness_ends(rms, expected):
    assert loudness(rms) == pytest.approx(expected, abs=0.01)


def test_loudness_shows_quiet_sound():
    assert 0.2 < loudness(0.005) < loudness(0.05) < 1.0        # quiet Wave Link mic still shows something


class OpenWindow:
    def __init__(self, visible=True, minimized=False):
        self.visible, self.minimized = visible, minimized

    def isVisible(self):
        return self.visible

    def isMinimized(self):
        return self.minimized


def test_listens_while_settings_are_open_on_another_monitor(ctrl, pc):
    pc.front = DISCORD
    ctrl.set("game_only", True)
    assert ctrl.engine.gated
    ctrl.window = OpenWindow()                 # open, but Discord has focus
    ctrl.check_game()
    assert not ctrl.engine.gated and ctrl.tuning
    ctrl.window.minimized = True
    ctrl.check_game()
    assert ctrl.engine.gated
    ctrl.window = None


def test_a_game_still_wins_over_the_open_window(ctrl, pc):
    S["games"] = [{"exe": "rocketleague.exe", "name": "Rocket League"}]
    pc.front = (11, r"F:\games\RocketLeague.exe", False)
    ctrl.window = OpenWindow()
    ctrl.set("game_only", True)
    assert ctrl.game == "Rocket League" and not ctrl.tuning
    ctrl.window = None


def samples(data):
    w = wave.open(io.BytesIO(data))
    return np.frombuffer(w.readframes(w.getnframes()), "<i2"), w.getframerate()


def test_chime_volume():
    quiet, sr = samples(sounds.chime(25))
    loud, _ = samples(sounds.chime(100))
    silent, _ = samples(sounds.chime(0))
    assert sr == sounds.SR and 0.4 < len(loud) / sr < 1.5
    assert silent.max() == 0 and 0 < abs(quiet).max() < abs(loud).max() < 32767


def brightness(x):
    spec = np.abs(np.fft.rfft(x))
    return (spec * np.fft.rfftfreq(len(x), 1 / sounds.SR)).sum() / spec.sum()


@pytest.mark.parametrize("name", list(sounds.SOUNDS))
def test_every_sound_is_clean(name):
    x = sounds.samples(100, name)
    assert 0.2 < len(x) / sounds.SR < 1.5
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01            # starts and ends in silence: no clicks
    assert 0.3 < np.abs(x).max() <= 0.61


def test_default_sound_is_soft():
    assert brightness(sounds.samples(100, "chime")) < brightness(sounds.samples(100, "bell")) / 2
    assert sounds.chime(50, "nonsense") == sounds.chime(50, "chime")


def test_chime_off_plays_nothing(monkeypatch):
    started = []
    monkeypatch.setattr(sounds.threading, "Thread", lambda *a, **k: started.append(1))
    sounds.play_chime(0)
    assert started == []
