"""2.2: cheaper audio capture, resting while waiting for a game, and the detection double-check."""

import time

import numpy as np
import pytest

from laughclipper import config, engine
from laughclipper.config import S
from laughclipper.engine import Engine

MIC = ("mic", "")
VOICE = ("out", "Voice Chat (Elgato Virtual Audio)")
HAS_MODEL = config.resource("models/yamnet.tflite").exists()


# ---------------------------------------------------------------- polling
class FakeRecorder:
    """The parts of soundcard's _Recorder the polling patch touches."""
    samplerate, channelmap = 48000, [0, 1]

    def __init__(self, available):
        self.available = list(available)        # what each "anything yet?" answers
        self._idle_start_time = None
        self.asked = self.period_asked = 0

    def _capture_available_frames(self):
        self.asked += 1
        return self.available.pop(0) if self.available else 0

    @property
    def deviceperiod(self):
        self.period_asked += 1
        return 0.01, 0.003


def patched(monkeypatch):
    """engine._patch_polling applied to a stand-in for soundcard's module."""
    class Recorder:
        def _record_chunk(self):
            return "real audio"

    class Module:
        _Recorder = Recorder
    monkeypatch.setattr(engine.time, "sleep", lambda s: sleeps.append(s))
    sleeps = []
    engine._patch_polling(Module)
    return Recorder._record_chunk, sleeps


def test_polls_every_10_ms_not_every_millisecond(monkeypatch):
    record_chunk, sleeps = patched(monkeypatch)
    rec = FakeRecorder([0, 0, 0, 480, 480])
    assert record_chunk(rec) == "real audio"
    assert sleeps == [engine.POLL, engine.POLL] and engine.POLL >= 0.01
    assert rec.period_asked == 1                              # asked once, then remembered
    rec.available = [0, 0, 480, 480]
    record_chunk(rec)
    assert rec.period_asked == 1


def test_silent_device_still_gets_zeros_at_the_right_rate(monkeypatch):
    record_chunk, _ = patched(monkeypatch)
    rec = FakeRecorder([])
    clock = [0]

    def now():
        clock[0] += 15_000_000                                # each look at the clock is 15 ms later
        return clock[0]
    monkeypatch.setattr(engine.time, "perf_counter_ns", now)
    out = record_chunk(rec)
    assert isinstance(out, np.ndarray) and not out.any()
    seconds = len(out) / 2 / 48000
    assert 0.04 < seconds < 0.08                              # as long as the wait was, no more
    assert engine.POLL * 4 <= 0.04                            # slower would make soundcard invent silence


# ---------------------------------------------------------------- resting while waiting for a game
def test_sources_rest_while_waiting_for_a_game():
    e = Engine()
    assert not e.resting(MIC)
    e.gated = True
    assert e.resting(MIC) and e.resting(VOICE)

    class Voice:
        active = True
    e.voice = Voice()
    assert not e.resting(MIC) and e.resting(VOICE)            # the mic stays for "don't clip" while a popup is up


def test_no_stream_is_opened_while_resting(monkeypatch):
    opened = []
    monkeypatch.setattr(engine, "get_soundcard", lambda: opened.append(1))
    monkeypatch.setattr(engine, "make_classifier", lambda: opened.append(2))
    e = Engine()
    e.gated = True
    S["listen_mic"], S["outputs"] = True, []
    e.sync()
    time.sleep(0.4)
    assert opened == [] and e.heard[MIC][0] == "paused" and e.levels[MIC] == 0.0
    assert e.restart_stalled() == []                          # resting isn't the same as hung
    e.stop_all()


# ---------------------------------------------------------------- detection
def test_downmix():
    stereo = np.array([[0.2, 0.4], [1.0, -1.0]], np.float32)
    assert engine.downmix(stereo).tolist() == pytest.approx([0.3, 0.0])
    surround = np.zeros((1, 8), np.float32)
    surround[0, :2] = 0.3                                     # game audio in front left/right of a 7.1 device
    assert engine.downmix(surround)[0] == pytest.approx(0.3)  # not 0.075
    assert engine.downmix(np.ones((1, 6), np.float32))[0] == 1.0          # clipped, never above full scale
    assert engine.downmix(np.array([0.5], np.float32)).tolist() == [0.5]


@pytest.mark.skipif(not HAS_MODEL, reason="model not downloaded")
def test_every_sound_name_exists_in_the_model():
    """A typo in SOUNDS would silently never match."""
    from mediapipe.tasks.python.components.containers import audio_data as ad
    c = engine.make_classifier()
    results = c.classify(ad.AudioData.create_from_array(np.zeros(engine.SR, np.float32), engine.SR))
    names = {cat.category_name for cat in results[0].classifications[0].categories}
    assert [label for label in engine.LABEL_SOUND if label not in names] == []
    assert len(results) == 2                                  # the real window + a padded 25 ms tail we ignore


@pytest.mark.skipif(not HAS_MODEL, reason="model not downloaded")
def test_quiet_audio_scores_like_loud_audio():
    """Wave Link's mic is quiet; the model has to cope without us boosting it."""
    import wave
    from pathlib import Path
    with wave.open(str(Path(__file__).parent / "data" / "no_wait_come_back.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    x48 = np.interp(np.arange(len(x) * 3) / 3, np.arange(len(x)), x).astype(np.float32)[4800:4800 + engine.SR]
    c = engine.make_classifier()
    assert engine.classify(c, x48)[1] == "Speech"
    assert engine.classify(c, x48 * 0.03)[1] == "Speech"      # rms ~0.003, just above the silence cutoff

