"""Audio capture + laugh detection (Google's YAMNet sound classifier via MediaPipe)."""

import ctypes
import logging
import math
import sys
import threading
import time
import urllib.request
import warnings
from collections import Counter, deque

import numpy as np
from PySide6.QtCore import QObject, Signal

from . import config
from .config import S

log = logging.getLogger(__name__)

SR = 48000
HOP = 0.25                 # analyse a 1 s window every 0.25 s
CHUNK = 0.05               # audio is read in 50 ms pieces, so the loudness meter updates 20 times a second
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


def source_id(key):
    """How a source is named in the `sound_sources` setting: "mic", or "out:<device name>"."""
    return "mic" if key[0] == "mic" else f"out:{key[1]}"


def sound_level(sound):
    """The trigger level for one sound: its own, or the main one from the Detection page."""
    return S["sound_levels"].get(sound, S["threshold"])


def judge(scores, source):
    """Of the sounds we trigger on that listen to this source, which stands out most?
    Returns (sound, its score, strength). Strength is the score divided by that sound's trigger level,
    so 1.0 or more is a hit whatever each level is. sound is None if nothing listens to this source."""
    best = (None, 0.0, 0.0)
    for k in S["triggers"]:
        allowed = S["sound_sources"].get(k)
        if k not in scores or (allowed is not None and source not in allowed):
            continue
        strength = scores[k] / max(sound_level(k), 0.01)
        if best[0] is None or strength > best[2]:
            best = (k, scores[k], strength)
    return best


class TriggerWindow:
    """Fires when ~75% of the last `need` windows were hits, so one quiet breath in the middle of a
    laugh doesn't reset it."""

    def __init__(self):
        self.recent = deque()

    def clear(self):
        self.recent.clear()

    def feed(self, hit, need):
        """hit: the sound heard in this window, or None. Returns the sound heard most when it fires."""
        need = max(1, need)
        self.recent.append(hit)
        while len(self.recent) > need:
            self.recent.popleft()
        hits = [s for s in self.recent if s]
        if len(self.recent) == need and self.recent[-1] and len(hits) >= math.ceil(need * 0.75):
            self.recent.clear()
            return Counter(hits).most_common(1)[0][0]
        return None


class Engine(QObject):
    laugh = Signal(object, str)         # source key ("mic", name) or ("out", name), sound key ("cheering")
    devices = Signal(list, list, str)   # microphones, outputs, default output

    def __init__(self):
        super().__init__()
        self.threads = {}    # key -> stop Event
        self.levels = {}     # key -> latest trigger score, or "loading" / "error"
        self.sound = {}      # key -> (which sound that score is for, its raw score)
        self.heard = {}      # key -> (top sound, volume)
        self.volume = {}     # key -> loudness (RMS) of the last 50 ms, for the meters
        self.scores = {}     # key -> every sound's latest score, e.g. {"laughter": 0.4, "cheering": 0.1}
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
                for d in (self.levels, self.sound, self.heard, self.activity, self.volume, self.scores):
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
                log.warning("[%s] couldn't start: %s %s", label, type(e).__name__, e)
                if not stop.is_set():
                    self.levels[key] = "error"
                stop.wait(3)
                continue

            log.info("listening on: %s", dev.name)
            window = np.zeros(SR, dtype=np.float32)
            chunk_n = int(SR * CHUNK)
            per_hop = round(HOP / CHUNK)
            chunks = 0
            trigger = TriggerWindow()
            source = source_id(key)
            try:
                with dev.recorder(samplerate=SR) as rec:
                    while not stop.is_set():
                        # read in small chunks so the loudness meter moves smoothly; classify every HOP
                        block = rec.record(numframes=chunk_n)
                        mono = (block.mean(axis=1) if block.ndim > 1 else block).astype(np.float32)
                        window = np.concatenate([window[len(mono):], mono])
                        self.volume[key] = float(np.sqrt(np.mean(mono ** 2)))
                        chunks += 1
                        if chunks % per_hop:
                            continue
                        vol = float(np.sqrt(np.mean(window[-chunk_n * per_hop:] ** 2)))

                        if self.gated:
                            self.levels[key] = 0.0
                            self.heard[key] = ("paused", vol)
                            self.scores.pop(key, None)
                            trigger.clear()
                            continue
                        if float(np.sqrt(np.mean(window ** 2))) < S["silence_level"]:
                            self.levels[key] = 0.0
                            self.heard[key] = ("silence", vol)
                            self.scores.pop(key, None)
                            trigger.clear()
                            continue

                        scores, top = classify(classifier, window)
                        if stop.is_set():
                            break
                        sound, score, strength = judge(scores, source)
                        self.levels[key] = strength * S["threshold"]    # meters show every sound against one tick
                        self.sound[key] = (sound, score)
                        self.scores[key] = scores
                        self.heard[key] = (top, vol)
                        if strength >= KEEP_RATIO:
                            self.activity[key] = time.time()
                        heard = trigger.feed(sound if strength >= 1 else None, round(S["min_laugh"] / HOP))
                        if heard:
                            log.info("[%s] %s (score %.2f)", label, heard, score)
                            self.laugh.emit(key, heard)
            except Exception as e:
                log.warning("[%s] audio stopped: %s %s", label, type(e).__name__, e)
                if not stop.is_set():
                    self.levels[key] = "error"
                stop.wait(2)
