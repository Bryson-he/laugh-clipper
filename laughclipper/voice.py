"""Voice commands: an offline Vosk speech model, restricted to a few phrases, listening to your microphone.
"Don't clip" only counts while a popup is up; hype phrases ("clip that", "no way"…) are listened for all the
time when they're turned on (but not while waiting for a game)."""

import json
import logging
import threading

import numpy as np
from PySide6.QtCore import QObject, Signal

from . import config
from .config import S

log = logging.getLogger(__name__)

CANCEL_PHRASES = ("don't clip", "do not clip", "cancel clip")
CLIP_PHRASES = ("clip that", "clip it")                  # save a clip straight away
HYPE_PHRASES = ("no way", "oh my god", "let's go")       # count as a moment, like a laugh
RATE = 16000                                    # the model's sample rate (the mic is read at 48 kHz)
MIN_CONF = 0.75     # hype phrases: how sure the model must be of every word. A restricted grammar makes it
                    # squeeze ordinary chatter into the nearest phrase, and those guesses come out less sure.


def model_path():
    return config.resource("models/vosk")


def available():
    return (model_path() / "am").is_dir()


def is_cancel(text):
    return any(p in text for p in CANCEL_PHRASES)


def phrase_kind(text):
    """"cancel", "clip", "hype" or None for what the model heard."""
    if is_cancel(text):
        return "cancel"
    if any(p in text for p in CLIP_PHRASES):
        return "clip"
    # hype phrases only count shouted on their own: in a sentence ("no wait, come back") the restricted grammar
    # turns near misses into them ("no way [unk]")
    rest = text
    for p in HYPE_PHRASES:
        rest = rest.replace(p, "")
    if rest.strip() == "" and rest != text:
        return "hype"
    return None


def grammar(cancel, hype):
    """The phrases to listen for; anything else comes out as [unk]."""
    phrases = (list(CANCEL_PHRASES) if cancel else []) + (list(CLIP_PHRASES + HYPE_PHRASES) if hype else [])
    return phrases + ["[unk]"]


def confidence(result):
    """The least sure word of a final result (0 when there are no words)."""
    words = result.get("result") or []
    return min((float(w.get("conf", 0)) for w in words), default=0.0)


def to_pcm16(chunk48):
    """48 kHz float samples -> 16 kHz 16-bit PCM bytes, for the model."""
    n = len(chunk48) // 3 * 3
    down = chunk48[:n].reshape(-1, 3).mean(axis=1)
    return (np.clip(down, -1, 1) * 32767).astype("<i2").tobytes()


class VoiceCommands(QObject):
    """Call start() when a popup appears and stop() when it's gone; the audio thread for the mic calls
    feed(). `heard` fires (on the audio thread; Qt queues it to the UI) with ("cancel" / "clip" / "hype", text)."""
    heard = Signal(str, str)
    ready = Signal(bool)          # model loaded (True) or couldn't be (False)

    def __init__(self):
        super().__init__()
        self.model = None
        self.loading = False
        self.active = False       # a popup is up, so "don't clip" counts
        self.generation = 0       # bumped by start(), so the audio thread makes a fresh recognizer
        self._rec = None
        self._rec_key = None      # (generation, grammar) the recognizer was made for

    def load(self):
        """Load the model in the background (about a second). Safe to call again."""
        if self.model is not None or self.loading or not available():
            return
        self.loading = True

        def worker():
            try:
                import vosk
                vosk.SetLogLevel(-1)
                self.model = vosk.Model(str(model_path()))
                log.info("voice commands ready")
                self.ready.emit(True)
            except Exception as e:
                log.warning("Couldn't load the speech model: %s %s", type(e).__name__, e)
                self.ready.emit(False)
            finally:
                self.loading = False
        threading.Thread(target=worker, daemon=True, name="voice-model").start()

    def start(self):
        self.generation += 1
        self.active = True

    def stop(self):
        self.active = False

    def feed(self, chunk48, gated=False):
        """One 50 ms chunk of mic audio (48 kHz float). Runs on the mic's audio thread.
        gated: waiting for a game, so only "don't clip" (for a popup that's already up) counts."""
        cancel = self.active and S["voice_cancel"]
        hype = S["hype_phrases"] and not gated
        if not (cancel or hype) or self.model is None:
            return
        try:
            words = grammar(cancel, hype)
            key = (self.generation, tuple(words))
            if self._rec is None or self._rec_key != key:
                import vosk
                self._rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(words))
                self._rec.SetWords(True)
                self._rec_key = key
            done = self._rec.AcceptWaveform(to_pcm16(chunk48))
            result = json.loads(self._rec.Result() if done else self._rec.PartialResult())
            text = result.get("text" if done else "partial", "")
            kind = phrase_kind(text)
            if kind == "cancel" and cancel:
                # partial results are fine here: the grammar is tiny while a popup is up, and it should be quick
                self.active = False
                log.info('voice: heard "%s"', text)
                self.heard.emit(kind, text)
            elif kind in ("clip", "hype") and hype and done:
                conf = confidence(result)
                if conf >= MIN_CONF:
                    log.info('voice: heard "%s" (%.2f)', text, conf)
                    self.heard.emit(kind, text)
                else:
                    log.info('voice: ignored "%s" (only %.2f sure)', text, conf)
        except Exception as e:
            log.warning("Voice command error: %s %s", type(e).__name__, e)
            self.active = False
            self._rec = None
