"""Settings, paths and small shared helpers."""

import copy
import json
import logging
import os
import sys
from pathlib import Path

APP_NAME = "Laugh Clipper"
FROZEN = getattr(sys, "frozen", False)

# Where bundled files (icon, sound model) live: next to the package in dev, inside the exe when built.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / APP_NAME
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "laugh_clipper.log"

log = logging.getLogger(__name__)

DEFAULTS = {
    "running": True,
    # notification
    "monitor": 1,
    "position": "bottom-center",
    "popup_seconds": 8,
    # audio
    "listen_mic": True,
    "mic_device": "",          # "" = Windows default
    "outputs": None,           # output devices to listen to; None = pick the default output on first run
    # clipping
    "hotkey": "f8",
    "auto_clip": False,
    "clip_delay": 10,          # auto-save: seconds to wait after the laughing stops
    "max_wait": 30,            # auto-save: save anyway this long after the laugh started (0 = never)
    "medal_check": True,       # warn (and don't pretend to save) when Medal isn't running
    "confirm_clips": True,     # after the hotkey, check a new clip appears in Medal's folder
    "clips_folder": "",        # Medal's clips folder; "" = find it (X:\Medal\Clips)
    "bookmark_when_recording": False,  # while Medal is recording, press its bookmark key instead of clipping
    "bookmark_hotkey": "",     # Medal's bookmark hotkey; "" = read it from Medal's settings
    "voice_cancel": False,     # saying "don't clip" while the popup is up cancels it (needs the mic)
    "pause_hotkey": "",        # a key to pause/resume detection; "" = none
    "smart_length": False,     # pick the Medal clip hotkey (30/60/120 s…) whose length fits the moment
    "long_laugh": 8,           # smart length: each this-many seconds of laughing moves up one clip length
    # sound cue
    "sound_cue": False,        # play a short chime when the popup appears
    "sound_volume": 50,        # 0-100
    "sound_device": "",        # output to play it on; "" = Windows default
    "sound_name": "chime",     # which sound (keys of sounds.SOUNDS)
    # games
    "game_only": False,        # only listen while a game is the window in front
    "games": [],               # [{"exe": "rocketleague.exe", "name": "Rocket League"}]
    "fullscreen_games": True,  # also count any full-screen app as a game
    "auto_add_games": True,    # add Steam/Epic/Xbox… games to "games" when they're played
    "ignored_games": [],       # exe names removed by hand, so auto-add leaves them out
    # detection
    "triggers": ["laughter"],  # sounds that trigger a clip (keys of engine.SOUNDS)
    "sound_levels": {},        # sound -> its own trigger level; missing = use "threshold"
    "sound_sources": {},       # sound -> sources it listens to ("mic", "out:<device>"); missing = all
    "threshold": 0.30,
    "min_laugh": 1.0,          # seconds of laughing needed to trigger
    "pause_tolerance": 1.5,    # a pause shorter than this still counts as the same laugh
    "cooldown": 20,
    "silence_level": 0.0015,
    # app
    "setup_done": False,       # the first-run setup guide has been seen
    "last_seen_version": "",   # "What's new" was shown for this version ("" = not since it existed)
    "start_minimized": False,
    "check_updates": True,
}
S = copy.deepcopy(DEFAULTS)     # deep, so the dicts and lists in it aren't shared with DEFAULTS


def resource(rel):
    return BASE_DIR / rel


def load():
    """Load settings. Returns False on first run (no settings file anywhere)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = [SETTINGS_PATH,
                  BASE_DIR / "laugh_clipper_settings.json",          # older single-file version
                  Path.cwd() / "laugh_clipper_settings.json"]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("Couldn't read %s: %s", path, e)
            continue
        if "hits_needed" in data and "min_laugh" not in data:
            data["min_laugh"] = data["hits_needed"] * 0.5
        if "setup_done" not in data:
            data["setup_done"] = True      # set up before the guide existed: don't show it to them
        S.update({k: v for k, v in data.items() if k in DEFAULTS})
        S["silence_level"] = min(S["silence_level"], DEFAULTS["silence_level"])
        from .engine import SOUNDS
        triggers = S["triggers"] if isinstance(S["triggers"], list) else []
        S["triggers"] = [k for k in SOUNDS if k in triggers] or list(DEFAULTS["triggers"])
        levels = S["sound_levels"] if isinstance(S["sound_levels"], dict) else {}
        S["sound_levels"] = {k: min(max(float(v), 0.05), 0.9) for k, v in levels.items()
                             if k in SOUNDS and isinstance(v, (int, float)) and not isinstance(v, bool)}
        sources = S["sound_sources"] if isinstance(S["sound_sources"], dict) else {}
        S["sound_sources"] = {k: [s for s in v if isinstance(s, str)] for k, v in sources.items()
                              if k in SOUNDS and isinstance(v, list) and any(isinstance(s, str) for s in v)}
        S["games"] = clean_games(S["games"])
        for key in ("clips_folder", "bookmark_hotkey", "sound_device", "last_seen_version", "pause_hotkey"):
            if not isinstance(S[key], str):
                S[key] = ""
        if not isinstance(S["sound_volume"], (int, float)) or isinstance(S["sound_volume"], bool):
            S["sound_volume"] = DEFAULTS["sound_volume"]
        S["sound_volume"] = int(min(max(S["sound_volume"], 0), 100))
        if not isinstance(S["long_laugh"], (int, float)) or isinstance(S["long_laugh"], bool):
            S["long_laugh"] = DEFAULTS["long_laugh"]
        S["long_laugh"] = int(min(max(S["long_laugh"], 3), 30))
        from .sounds import SOUNDS as CUES
        if S["sound_name"] not in CUES:
            S["sound_name"] = DEFAULTS["sound_name"]
        S["ignored_games"] = [g["exe"] for g in clean_games(S["ignored_games"])]
        if path != SETTINGS_PATH:
            save()
        return True
    return False


def save():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(S, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    except Exception as e:
        log.warning("Couldn't save settings: %s", e)


def clean_games(games):
    """Keep well-formed game entries (bare exe names are accepted too), one per exe."""
    out, seen = [], set()
    for g in games if isinstance(games, list) else []:
        if isinstance(g, str):
            g = {"exe": g}
        if not isinstance(g, dict) or not isinstance(g.get("exe"), str) or not g["exe"].strip():
            continue
        exe = Path(g["exe"].strip()).name.lower()
        if exe not in seen:
            seen.add(exe)
            out.append({"exe": exe, "name": str(g.get("name") or Path(exe).stem)})
    return out


def split_position(pos):
    """'bottom-center' -> ('bottom', 'center'); 'center' -> ('middle', 'center')."""
    if pos == "center":
        return "middle", "center"
    v, _, h = pos.partition("-")
    return v, h or "center"


KEY_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Win", "win": "Win", "esc": "Esc",
              "space": "Space", "enter": "Enter", "tab": "Tab"}


def hotkey_label(hk=None):
    """'ctrl+alt+p' -> 'Ctrl + Alt + P', 'shift+f7' -> 'Shift + F7'."""
    hk = hk or S["hotkey"]
    parts = [p.strip() for p in hk.split("+") if p.strip()]
    return " + ".join(KEY_LABELS.get(p.lower(), p.upper() if len(p) <= 3 else p.capitalize()) for p in parts)
