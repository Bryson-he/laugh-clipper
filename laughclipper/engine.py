"""Audio capture + laugh detection (Google's YAMNet sound classifier via MediaPipe)."""

import ctypes
import logging
import math
import sys
import threading
import time
import urllib.request
import warnings
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Signal

from . import config
from .config import S

log = logging.getLogger(__name__)

SR = 48000
HOP = 0.25                 # analyse a 1 s window every 0.25 s
KEEP_RATIO = 0.55          # once laughing, a score above 55% of the trigger keeps it "still laughing"
# sounds you can trigger on: key -> (name, "still ..." text, YAMNet class names)
SOUNDS = {
    "laughter": ("Laughter", "Still laughing",
                 {"Laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle", "Baby laughter"}),
    "cheering": ("Cheering", "Still cheering", {"Cheering", "Whoop", "Applause"}),
    "shouting": ("Shouting", "Still shouting", {"Shout", "Yell", "Bellow", "Children shouting"}),
    "screaming": ("Screaming", "Still screaming", {"Screaming"}),
    "gasp": ("Gasp", "Still going", {"Gasp"}),
}
LABEL_SOUND = {label: key for key, (_, _, labels) in SOUNDS.items() for label in labels}
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"

_SC = None
_SC_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()


def com_init():
    if sys.platform == "win32":
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass


def get_soundcard():
    """Import soundcard once, from a thread with no COM set up yet (it initialises COM itself
    and errors if that's already been done), then join the calling thread to COM."""
    global _SC
    with _SC_LOCK:
        if _SC is None:
            import soundcard
            warnings.filterwarnings("ignore", category=soundcard.SoundcardRuntimeWarning)
            _SC = soundcard
    com_init()
    return _SC


def short_name(device_name):
    """'Voice Chat (Elgato Virtual Audio)' -> 'Voice Chat'"""
    return device_name.split(" (")[0] if " (" in device_name else device_name


def model_path():
    bundled = config.resource("models/yamnet.tflite")
    if bundled.exists():
        return bundled
    path = config.DATA_DIR / "yamnet.tflite"
    with _MODEL_LOCK:
        if not path.exists():
            log.info("Downloading sound model…")
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(MODEL_URL, tmp)
            tmp.replace(path)
    return path


def make_classifier():
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import audio as mp_audio
    opts = mp_audio.AudioClassifierOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=model_path().read_bytes()),
        running_mode=mp_audio.RunningMode.AUDIO_CLIPS,
        max_results=-1,
    )
    return mp_audio.AudioClassifier.create_from_options(opts)


def classify(classifier, samples):
    """Returns ({sound key: score 0-1}, name of the most likely sound)."""
    from mediapipe.tasks.python.components.containers import audio_data as ad
    data = ad.AudioData.create_from_array(samples, SR)
    scores, top, top_score = dict.fromkeys(SOUNDS, 0.0), "", 0.0
    for res in classifier.classify(data):
        for c in res.classifications[0].categories:
            sound = LABEL_SOUND.get(c.category_name)
            if sound:
                scores[sound] = max(scores[sound], c.score)
            if c.score > top_score:
                top, top_score = c.category_name, c.score
    return scores, top


def best_sound(scores):
    """The strongest of the sounds we're triggering on: (key, score)."""
    keys = [k for k in S["triggers"] if k in scores] or ["laughter"]
    k = max(keys, key=lambda k: scores[k])
    return k, scores[k]


class Engine(QObject):
    laugh = Signal(object, str)         # source key ("mic", name) or ("out", name), sound key ("cheering")
    devices = Signal(list, list, str)   # microphones, outputs, default output

    def __init__(self):
        super().__init__()
        self.threads = {}    # key -> stop Event
        self.levels = {}     # key -> latest trigger score, or "loading" / "error"
        self.sound = {}      # key -> which sound that score is for
        self.heard = {}      # key -> (top sound, volume)
        self.activity = {}   # key -> last time the score was above the keep-alive level
        self.gated = False   # True while waiting for a game: keep recording, but don't classify

    # ----- sources -----
    def wanted(self):
        if not S["running"]:
            return set()
        keys = {("out", n) for n in (S["outputs"] or [])}
        if S["listen_mic"]:
            keys.add(("mic", S["mic_device"]))
        return keys

    def sync(self):
        wanted = self.wanted()
        for key in list(self.threads):
            if key not in wanted:
                self.threads.pop(key).set()
                for d in (self.levels, self.sound, self.heard, self.activity):
                    d.pop(key, None)
        for key in wanted - set(self.threads):
            stop = threading.Event()
            self.threads[key] = stop
            threading.Thread(target=self._listen, args=(key, stop), daemon=True,
                             name=f"listen-{key[0]}-{short_name(key[1])}").start()

    def stop_all(self):
        for stop in self.threads.values():
            stop.set()
        self.threads.clear()

    def refresh_devices(self):
        def worker():
            try:
                sc = get_soundcard()
                mics = [m.name for m in sc.all_microphones()]
                spks = [s.name for s in sc.all_speakers()]
                try:
                    default = sc.default_speaker().name
                except Exception:
                    default = ""
            except Exception as e:
                log.warning("Couldn't list audio devices: %s", e)
                mics, spks, default = [], [], ""
            self.devices.emit(mics, spks, default)
        threading.Thread(target=worker, daemon=True, name="devices").start()

    # ----- state for the UI -----
    def last_activity(self):
        """When anything we're listening to last sounded like laughing."""
        return max((t for k, t in list(self.activity.items()) if k in self.threads), default=0.0)

    def laughing(self, now=None):
        """True while anything is laughing, allowing short pauses (pause tolerance)."""
        return (now or time.time()) - self.last_activity() <= S["pause_tolerance"]

    def max_level(self):
        vals = [v for k, v in list(self.levels.items()) if k in self.threads and isinstance(v, float)]
        return max(vals, default=0.0)

    # ----- worker -----
    def _listen(self, key, stop):
        kind, name = key
        label = short_name(name) or "mic"
        classifier = None
        while not stop.is_set():
            try:
                sc = get_soundcard()
                if classifier is None:
                    self.levels[key] = "loading"
                    classifier = make_classifier()
                if kind == "mic":
                    dev = (next((m for m in sc.all_microphones() if m.name == name), None)
                           if name else sc.default_microphone())
                else:
                    spk = next((s for s in sc.all_speakers() if s.name == name), None)
                    dev = sc.get_microphone(id=spk.id, include_loopback=True) if spk else None
                if dev is None:
                    raise RuntimeError("device not found")
            except Exception as e:
                log.warning("[%s] couldn't start: %s", label, e)
                if not stop.is_set():
                    self.levels[key] = "error"
                stop.wait(3)
                continue

            log.info("listening on: %s", dev.name)
            window = np.zeros(SR, dtype=np.float32)
            hop_n = int(SR * HOP)
            recent = deque()
            try:
                with dev.recorder(samplerate=SR) as rec:
                    while not stop.is_set():
                        block = rec.record(numframes=hop_n)
                        mono = (block.mean(axis=1) if block.ndim > 1 else block).astype(np.float32)
                        window = np.concatenate([window[len(mono):], mono])
                        vol = float(np.sqrt(np.mean(mono ** 2)))
                        need = max(1, round(S["min_laugh"] / HOP))

                        if self.gated:
                            self.levels[key] = 0.0
                            self.heard[key] = ("paused", vol)
                            recent.clear()
                            continue
                        if float(np.sqrt(np.mean(window ** 2))) < S["silence_level"]:
                            self.levels[key] = 0.0
                            self.heard[key] = ("silence", vol)
                            recent.clear()
                            continue

                        scores, top = classify(classifier, window)
                        if stop.is_set():
                            break
                        sound, score = best_sound(scores)
                        self.levels[key] = score
                        self.sound[key] = sound
                        self.heard[key] = (top, vol)
                        thr = S["threshold"]
                        if score >= thr * KEEP_RATIO:
                            self.activity[key] = time.time()

                        # trigger when ~75% of the last `min_laugh` seconds were laughing (or cheering…),
                        # so one quiet breath in the middle doesn't reset it
                        recent.append(sound if score >= thr else None)
                        while len(recent) > need:
                            recent.popleft()
                        hits = [s for s in recent if s]
                        if len(recent) == need and recent[-1] and len(hits) >= math.ceil(need * 0.75):
                            heard = max(set(hits), key=hits.count)
                            log.info("[%s] %s (score %.2f)", label, heard, score)
                            self.laugh.emit(key, heard)
                            recent.clear()
            except Exception as e:
                log.warning("[%s] audio stopped: %s", label, e)
                if not stop.is_set():
                    self.levels[key] = "error"
                stop.wait(2)
