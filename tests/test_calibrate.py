"""Calibrating for a game: the suggested level, and the calibration window's flow."""

import threading

import pytest

from laughclipper.calibrate import suggest
from laughclipper.config import S

GAME = ("out", "Game (Elgato Virtual Audio)")
MIC = ("mic", "")


def test_laughs_that_stand_out():
    r = suggest({"laughter": [0.05] * 50 + [0.12] * 5}, [0.2, 0.55, 0.7], ["laughter"])
    assert r["ok"] and r["floors"]["laughter"] == pytest.approx(0.12, abs=0.01)
    assert r["floors"]["laughter"] < r["threshold"] < 0.7 * 0.85
    assert r["threshold"] == pytest.approx(0.42)                 # 60% of the best laugh
    assert r["sound_levels"] == {}


def test_skipping_the_laugh_step():
    r = suggest({"laughter": [0.2] * 40}, [], ["laughter"])
    assert r["ok"] and r["threshold"] == pytest.approx(0.28) and "skipped" in r["note"]


def test_laughs_that_dont_stand_out():
    r = suggest({"laughter": [0.4] * 40}, [0.45], ["laughter"])
    assert not r["ok"] and 0.4 < r["threshold"] < 0.55 and "barely" in r["note"]


def test_a_noisy_other_sound_gets_its_own_level():
    noise = {"laughter": [0.05] * 40, "cheering": [0.6] * 40, "gasp": [0.01] * 40}
    r = suggest(noise, [0.6], ["laughter", "cheering", "gasp"])
    assert r["sound_levels"] == {"cheering": 0.78}               # cheering game, gasps are quiet


def test_one_spike_doesnt_count():
    r = suggest({"laughter": [0.03] * 99 + [0.9]}, [0.6], ["laughter"])
    assert r["floors"]["laughter"] < 0.1


def test_silent_game_has_a_sensible_floor():
    r = suggest({}, [], ["laughter"])
    assert r["threshold"] == 0.08


@pytest.fixture
def cal(ctrl, monkeypatch):
    ctrl.engine.threads.update({GAME: threading.Event(), MIC: threading.Event()})
    ctrl.show_calibration()
    yield ctrl.calibration
    if ctrl.calibration:
        ctrl.calibration.close()


def hear(ctrl, game, mic, times=10):
    ctrl.engine.scores[GAME], ctrl.engine.scores[MIC] = game, mic


def test_calibration_flow(ctrl, cal):
    S["triggers"] = ["laughter", "cheering"]
    assert cal.stack.currentIndex() == 0
    cal.next.click()                                            # Start
    assert cal.stack.currentIndex() == 1 and not cal.next.isEnabled()   # needs 20 s of play first
    hear(ctrl, {"laughter": 0.1, "cheering": 0.5}, {"laughter": 0.02, "cheering": 0.0})
    for _ in range(30):
        cal.sample()
    assert max(cal.noise["laughter"]) == 0.1 and max(cal.noise["cheering"]) == 0.5
    cal.go(2)
    hear(ctrl, {"laughter": 0.05, "cheering": 0.1}, {"laughter": 0.7, "cheering": 0.0})
    cal.sample()
    assert cal.next.isEnabled()
    cal.next.click()                                            # to the result
    r = cal.result
    assert cal.stack.currentIndex() == 3 and r["ok"] and r["threshold"] == pytest.approx(0.42)
    assert r["sound_levels"] == {"cheering": 0.66}
    cal.next.click()                                            # Apply
    assert S["threshold"] == pytest.approx(0.42) and S["sound_levels"] == {"cheering": 0.66}
    assert ctrl.calibration is None


def test_sounds_only_count_from_their_own_sources(ctrl, cal):
    S["sound_sources"] = {"laughter": ["mic"]}                  # game audio can't trigger laughter here
    cal.go(1)
    hear(ctrl, {"laughter": 0.8}, {"laughter": 0.05})
    cal.sample()
    assert max(cal.noise["laughter"]) == 0.05


def test_cancel_changes_nothing(ctrl, cal):
    before = (S["threshold"], dict(S["sound_levels"]))
    cal.go(1)
    hear(ctrl, {"laughter": 0.6}, {"laughter": 0.6})
    cal.sample()
    cal.go(3)
    cal.cancel.click()
    assert (S["threshold"], S["sound_levels"]) == before and ctrl.calibration is None


def test_listens_while_calibrating_without_a_game(ctrl, cal, pc):
    pc.front = (12, r"C:\Discord\Discord.exe", False)
    ctrl.set("game_only", True)
    assert not ctrl.engine.gated and ctrl.tuning               # the calibration window counts as "open"
