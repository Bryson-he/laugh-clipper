"""Trigger logic: which sound counts, per-sound levels and sources, and the 75% rule."""

import numpy as np
import pytest

from laughclipper import config, engine
from laughclipper.config import S
from laughclipper.engine import TriggerWindow, judge, source_id

MIC, GAME = "mic", "out:Game (Elgato Virtual Audio)"


def scores(**kw):
    s = dict.fromkeys(engine.SOUNDS, 0.0)
    s.update(kw)
    return s


def test_source_ids():
    assert source_id(("mic", "")) == "mic"
    assert source_id(("mic", "Wave:3")) == "mic"
    assert source_id(("out", "Game (Elgato Virtual Audio)")) == GAME


def test_only_selected_sounds_count():
    S["threshold"] = 0.3
    sound, score, strength = judge(scores(laughter=0.1, shouting=0.9), MIC)
    assert sound == "laughter" and score == 0.1 and strength < 1
    S["triggers"] = ["laughter", "shouting"]
    sound, score, strength = judge(scores(laughter=0.1, shouting=0.9), MIC)
    assert sound == "shouting" and strength == pytest.approx(3.0)


def test_per_sound_level_decides_the_winner():
    S["threshold"] = 0.3
    S["triggers"] = ["laughter", "shouting"]
    S["sound_levels"] = {"shouting": 0.8}          # shouting needs a much higher score
    sound, score, strength = judge(scores(laughter=0.33, shouting=0.6), MIC)
    assert sound == "laughter" and strength == pytest.approx(1.1)
    sound, _, strength = judge(scores(laughter=0.0, shouting=0.6), MIC)
    assert sound == "shouting" and strength == pytest.approx(0.75)   # 0.6 is under its 0.8 level


def test_per_sound_sources():
    S["triggers"] = ["laughter", "shouting"]
    S["sound_sources"] = {"shouting": [MIC]}       # shouting only from the mic, never from game audio
    assert judge(scores(laughter=0.0, shouting=0.9), GAME)[0] == "laughter"
    assert judge(scores(laughter=0.0, shouting=0.9), MIC)[0] == "shouting"
    S["sound_sources"] = {"laughter": [MIC], "shouting": [MIC]}
    assert judge(scores(laughter=0.9, shouting=0.9), GAME) == (None, 0.0, 0.0)


def test_trigger_needs_enough_of_the_window():
    t = TriggerWindow()
    assert [t.feed("laughter", 4) for _ in range(3)] == [None, None, None]
    assert t.feed("laughter", 4) == "laughter"
    assert t.recent == type(t.recent)()           # starts over after firing


def test_one_breath_does_not_reset_it():
    t = TriggerWindow()
    assert [t.feed(h, 4) for h in ("laughter", "laughter", None, "laughter")] == [None, None, None, "laughter"]


def test_it_waits_for_the_laugh_to_be_going():
    t = TriggerWindow()
    assert [t.feed(h, 4) for h in ("laughter", "laughter", "laughter", None)] == [None] * 4   # last one silent
    assert [t.feed(h, 4) for h in (None, None, "laughter", "laughter")] == [None] * 4         # only 2 of 4


def test_reports_the_sound_heard_most():
    t = TriggerWindow()
    assert [t.feed(h, 4) for h in ("laughter", "cheering", "cheering", "cheering")][-1] == "cheering"


def test_short_min_laugh_still_needs_one_hit():
    t = TriggerWindow()
    assert t.feed(None, 0) is None
    assert t.feed("gasp", 0) == "gasp"


@pytest.mark.skipif(not config.resource("models/yamnet.tflite").exists(), reason="model not downloaded")
def test_classifier_on_noise():
    c = engine.make_classifier()
    rng = np.random.default_rng(1)
    result, top = engine.classify(c, (rng.standard_normal(engine.SR) * 0.05).astype(np.float32))
    assert set(result) == set(engine.SOUNDS)
    assert all(0.0 <= v <= 1.0 for v in result.values())
    assert result["laughter"] < 0.2 and top
