"""The "don't clip" voice command: an offline Vosk speech model, restricted to a few phrases, that listens to
your microphone only while a popup is up."""

import json
import logging
import threading

import numpy as np
from PySide6.QtCore import QObject, Signal

from . import config

log = logging.getLogger(__name__)

CANCEL_PHRASES = ("don't clip", "do not clip", "cancel clip")
GRAMMAR = list(CANCEL_PHRASES) + ["[unk]"]      # anything else comes out as [unk]
RATE = 16000                                    # the model's sample rate (the mic is read at 48 kHz)


def model_path():
    return config.resource("models/vosk")


def available():
    return (model_path() / "am").is_dir()


def is_cancel(text):
    return any(p in text for p in CANCEL_PHRASES)


def to_pcm16(chunk48):
    """48 kHz float samples -> 16 kHz 16-bit PCM bytes, for the model."""
    n = len(chunk48) // 3 * 3
    down = chunk48[:n].reshape(-1, 3).mean(axis=1)
    return (np.clip(down, -1, 1) * 32767).astype("<i2").tobytes()


class VoiceCommands(QObject):
    """Call start() when a popup appears and stop() when it's gone; the audio thread for the mic calls
    feed(). `cancel` fires (on the audio thread; Qt queues it to the UI) when someone says "don't clip"."""
    cancel = Signal(str)
    ready = Signal(bool)          # model loaded (True) or couldn't be (False)

    def __init__(self):
        super().__init__()
        self.model = None
        self.loading = False
        self.active = False
        self.generation = 0       # bumped by start(), so the audio thread makes a fresh recognizer
        self._rec = None
        self._rec_generation = -1

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

    def feed(self, chunk48):
        """One 50 ms chunk of mic audio (48 kHz float). Runs on the mic's audio thread."""
        if not self.active or self.model is None:
            return
        try:
            if self._rec is None or self._rec_generation != self.generation:
                import vosk
                self._rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(GRAMMAR))
                self._rec_generation = self.generation
            done = self._rec.AcceptWaveform(to_pcm16(chunk48))
            text = json.loads(self._rec.Result() if done else self._rec.PartialResult()).get(
                "text" if done else "partial", "")
            if is_cancel(text) and self.active:
                self.active = False
                log.info('voice: heard "%s"', text)
                self.cancel.emit(text)
        except Exception as e:
            log.warning("Voice command error: %s %s", type(e).__name__, e)
            self.active = False
