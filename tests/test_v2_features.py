"""2.0 features: "don't clip" by voice, the pause hotkey, "Not funny", and History stats."""

import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from conftest import SENT
from laughclipper import clips, voice
from laughclipper.config import S
from test_clipping import flush

GAME = ("out", "Game (Elgato Virtual Audio)")
DATA = Path(__file__).parent / "data"


def speech(name):
    """A text-to-speech clip as 48 kHz float audio in 50 ms chunks, like the mic thread would see it."""
    with wave.open(str(DATA / name)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    x48 = np.interp(np.arange(len(x) * 3) / 3, np.arange(len(x)), x).astype(np.float32)
    x48 = np.concatenate([np.zeros(24000, np.float32), x48, np.zeros(48000, np.float32)])
    return [x48[i:i + 2400] for i in range(0, len(x48), 2400)]


# ---------------------------------------------------------------- voice
def test_cancel_phrases():
    assert voice.is_cancel("don't clip") and voice.is_cancel("do not clip [unk]") and voice.is_cancel("cancel clip")
    assert not voice.is_cancel("clip [unk]") and not voice.is_cancel("[unk]") and not voice.is_cancel("")


def test_resampling():
    pcm = voice.to_pcm16(np.full(2400, 0.5, np.float32))
    assert len(pcm) == 800 * 2                               # 50 ms at 16 kHz, 16-bit


@pytest.fixture(scope="module")
def listener():
    if not voice.available():
        pytest.skip("speech model not downloaded")
    v = voice.VoiceCommands()
    done = threading.Event()
    v.ready.connect(lambda ok: done.set())
    v.load()
    for _ in range(200):                                     # the ready signal needs no event loop: poll
        if v.model is not None:
            break
        time.sleep(0.05)
    assert v.model is not None
    return v


@pytest.mark.parametrize("clip, cancels", [("dont_clip.wav", True), ("clip_that.wav", False)])
def test_hears_dont_clip(listener, clip, cancels):
    heard = []
    listener.cancel.connect(heard.append)
    try:
        listener.start()
        for chunk in speech(clip):
            listener.feed(chunk)
    finally:
        listener.cancel.disconnect(heard.append)
        listener.stop()
    assert bool(heard) is cancels


def test_only_listens_while_a_popup_is_up(listener):
    heard = []
    listener.cancel.connect(heard.append)
    try:
        listener.stop()
        for chunk in speech("dont_clip.wav"):
            listener.feed(chunk)
    finally:
        listener.cancel.disconnect(heard.append)
    assert heard == []


def test_voice_cancels_the_popup(ctrl, pc, qapp):
    S["auto_clip"], S["clip_delay"], S["voice_cancel"] = True, 5, True
    ctrl.on_laugh(GAME, "laughter")
    assert ctrl.toast.mode == "pending" and ctrl.voice.active
    ctrl.on_voice_cancel("don't clip")
    assert ctrl.pending is None and ctrl.toast.mode == "info"
    pc.wait(10)
    ctrl.on_tick()
    flush(qapp)
    assert SENT == [] and ctrl.history == []                  # nothing was clipped


def test_too_late_once_the_clip_is_sent(ctrl, qapp):
    S["voice_cancel"] = True
    ctrl.on_laugh(GAME, "laughter")
    ctrl.clip()
    assert not ctrl.voice.active                              # stops listening once it's sent
    ctrl.on_voice_cancel("don't clip")
    assert ctrl.toast.mode == "saving"


def test_off_means_not_listening(ctrl):
    ctrl.on_laugh(GAME, "laughter")
    assert not ctrl.voice.active


# ---------------------------------------------------------------- pause hotkey
def test_pause_hotkey_toggles(ctrl):
    S["pause_hotkey"] = "ctrl+alt+p"
    ctrl.toggle_pause()
    assert S["running"] is False and ctrl.toast.mode == "info"
    ctrl.toggle_pause()
    assert S["running"] is True


def test_pause_hotkey_cant_be_the_clip_hotkey(ctrl):
    S["hotkey"], S["pause_hotkey"] = "shift+f7", "ctrl+alt+p"
    ctrl.apply_recorded("pause_hotkey", "shift+f7")
    assert S["pause_hotkey"] == "ctrl+alt+p"
    ctrl.apply_recorded("pause_hotkey", "f9")
    assert S["pause_hotkey"] == "f9"


# ---------------------------------------------------------------- not funny
def test_not_funny_skips_it_and_learns(ctrl, pc, qapp):
    S["threshold"], S["auto_clip"], S["clip_delay"] = 0.3, True, 5
    ctrl.on_laugh(GAME, "cheering")
    ctrl.on_not_funny()
    assert S["sound_levels"]["cheering"] == pytest.approx(0.33)
    assert ctrl.pending is None and ctrl.toast.mode == "info"
    assert ctrl.history[-1]["status"] == "not_funny" and ctrl.history[-1]["sound"] == "cheering"
    pc.wait(10)
    ctrl.on_tick()
    flush(qapp)
    assert SENT == []


def test_not_funny_keeps_raising_but_not_forever(ctrl, pc):
    S["sound_levels"] = {"laughter": 0.88}
    S["cooldown"] = 0
    ctrl.on_laugh(GAME, "laughter")
    ctrl.on_not_funny()
    assert S["sound_levels"]["laughter"] == 0.9


# ---------------------------------------------------------------- history stats
def test_stats():
    now = datetime(2026, 10, 26, 12, 0)

    def item(days, status="saved", game="Rocket League", score=0.5):
        return {"time": (now - timedelta(days=days)).isoformat(), "status": status, "game": game, "score": score}
    items = [item(1), item(2, game="Minecraft", score=0.8), item(3), item(20, status="bookmarked"),
             item(1, status="failed", score=0.95), item(0, status="not_funny"), item(0, status="not_funny")]
    st = clips.stats(items, now)
    assert st == {"clips": 4, "week": 3, "top_game": "Rocket League", "best": 0.8, "not_funny": 2}
    assert clips.stats([]) == {"clips": 0, "week": 0, "top_game": "", "best": 0.0, "not_funny": 0}


def test_history_page_shows_stats_and_play(ctrl, tmp_path):
    from laughclipper.settings_window import SettingsWindow
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"")
    ctrl.history = [clips.new_entry({"sound": "laughter", "game": "Rocket League", "peak": 0.6}, "saved", str(f)),
                    clips.new_entry({"sound": "gasp"}, "not_funny")]
    w = SettingsWindow(ctrl)
    assert w.stats_bar.items[0] == ("1", "Clips saved") and w.stats_bar.items[4] == ("1", "Not funny")
    rows = w.history_group.rows
    texts = [b.text() for b in rows[1].findChildren(type(w.upd_btn))]
    assert "Play" in texts and "Show" in texts
