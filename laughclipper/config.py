"""Settings, paths and small shared helpers."""

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
    # games
    "game_only": False,        # only listen while a game is the window in front
    "games": [],               # [{"exe": "rocketleague.exe", "name": "Rocket League"}]
    "fullscreen_games": True,  # also count any full-screen app as a game
    "auto_add_games": True,    # add Steam/Epic/Xbox… games to "games" when they're played
    "ignored_games": [],       # exe names removed by hand, so auto-add leaves them out
    # detection
    "triggers": ["laughter"],  # sounds that trigger a clip (keys of engine.SOUNDS)
    "threshold": 0.30,
    "min_laugh": 1.0,          # seconds of laughing needed to trigger
    "pause_tolerance": 1.5,    # a pause shorter than this still counts as the same laugh
    "cooldown": 20,
    "silence_level": 0.0015,
    # app
    "start_minimized": False,
    "check_updates": True,
}
S = dict(DEFAULTS)


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
        S.update({k: v for k, v in data.items() if k in DEFAULTS})
        S["silence_level"] = min(S["silence_level"], DEFAULTS["silence_level"])
        from .engine import SOUNDS
        triggers = S["triggers"] if isinstance(S["triggers"], list) else []
        S["triggers"] = [k for k in SOUNDS if k in triggers] or list(DEFAULTS["triggers"])
        S["games"] = clean_games(S["games"])
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


def hotkey_label(hk=None):
    hk = hk or S["hotkey"]
    return " + ".join(p.upper() if len(p) <= 3 else p.capitalize() for p in hk.split("+"))
