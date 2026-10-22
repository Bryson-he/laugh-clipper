"""The optional sound that plays when a popup appears (handy in exclusive-fullscreen games, where the
popup itself can't show). Made in code, so there's no sound file to bundle."""

import io
import logging
import sys
import threading
import wave

import numpy as np

log = logging.getLogger(__name__)
SR = 44100
SOUNDS = {                          # setting value -> name shown in the menu
    "chime": "Soft chime",
    "pop": "Bubble pop",
    "marimba": "Marimba",
    "bell": "Bright bell",
}
_cache = {}


def _note(n, start, freq, decay, partials, attack=0.008):
    """One struck note: a sum of sine partials [(multiple, level, decay speed-up)] under a smooth envelope."""
    t = np.arange(n) / SR - start
    on = t >= 0
    t = np.clip(t, 0, None)
    env = np.minimum(t / attack, 1) ** 2 * np.exp(-t / decay)      # eased attack, no click
    tone = sum(a * np.sin(2 * np.pi * freq * m * t) * np.exp(-t * k) for m, a, k in partials)
    return np.where(on, tone * env, 0.0)


def _room(x, wet=0.22):
    """A little room echo so it rings softly instead of stopping dead."""
    y = x.copy()
    for delay, gain in ((0.029, 0.55), (0.041, 0.45), (0.067, 0.32), (0.097, 0.22), (0.131, 0.14)):
        d = int(SR * delay)
        y[d:] += x[:-d] * gain * wet
    return y


def _render(name):
    if name == "pop":
        n = int(SR * 0.32)
        t = np.arange(n) / SR
        freq = 380 + 520 * (1 - np.exp(-t / 0.035))                # slides up quickly, then settles
        phase = 2 * np.pi * np.cumsum(freq) / SR
        env = np.minimum(t / 0.004, 1) ** 2 * np.exp(-t / 0.07)
        x = _room((np.sin(phase) + 0.12 * np.sin(2 * phase)) * env, 0.15)
    elif name == "marimba":
        n = int(SR * 0.9)
        wood = ((1, 1.0, 0.0), (4, 0.18, 18.0), (10, 0.04, 40.0))     # marimba bars ring at 1x, 4x and 10x
        x = sum(_note(n, start, f, 0.22, wood, 0.003) for start, f in ((0.0, 523.25), (0.09, 659.25), (0.18, 783.99)))
        x = _room(x, 0.18)
    elif name == "bell":
        n = int(SR * 0.55)
        bright = ((1, 1.0, 0.0), (2, 0.28, 0.0), (3, 0.08, 0.0))
        x = _note(n, 0.0, 1318.5, 0.13, bright, 0.004) + _note(n, 0.11, 1760.0, 0.13, bright, 0.004)
    else:                                                             # soft chime
        n = int(SR * 1.1)
        soft = ((1, 1.0, 0.0), (2, 0.06, 6.0), (3, 0.015, 12.0))      # nearly pure, overtones fade fast
        x = _note(n, 0.0, 659.25, 0.32, soft, 0.012) + 0.85 * _note(n, 0.13, 987.77, 0.36, soft, 0.012)
        x = _room(x, 0.25)
    fade = min(len(x), int(SR * 0.04))                                # always end in silence
    x[-fade:] *= np.linspace(1, 0, fade)
    return x / np.abs(x).max()


def chime(volume, name="chime"):
    """The sound as WAV bytes. volume: 0-100."""
    volume = int(min(max(volume, 0), 100))
    name = name if name in SOUNDS else "chime"
    if (name, volume) in _cache:
        return _cache[name, volume]
    gain = 0.6 * (volume / 100) ** 2           # perceived loudness follows roughly the square
    pcm = (_render(name) * gain * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    _cache[name, volume] = buf.getvalue()
    return _cache[name, volume]


def samples(volume, name="chime"):
    """The sound as float32 samples (-1..1)."""
    with wave.open(io.BytesIO(chime(volume, name))) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def play_chime(volume, device="", name="chime"):
    """Plays without blocking, on the named output (e.g. 'Game (Elgato Virtual Audio)') or, with no name,
    the Windows default. Falls back to winsound on the default output if that fails."""
    if sys.platform != "win32" or volume <= 0:
        return

    def worker():
        try:
            from .engine import get_soundcard
            sc = get_soundcard()
            spk = next((s for s in sc.all_speakers() if s.name == device), None) if device else None
            spk = spk or sc.default_speaker()
            mono = samples(volume, name)
            spk.play(np.repeat(mono[:, None], max(1, spk.channels), axis=1), samplerate=SR)
        except Exception as e:
            log.warning("Couldn't play the sound on %s (%s %s); using the default output",
                        device or "the default output", type(e).__name__, e)
            try:
                import winsound
                winsound.PlaySound(chime(volume, name), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except Exception as e2:
                log.warning("Couldn't play the sound: %s", e2)
    threading.Thread(target=worker, daemon=True, name="chime").start()
