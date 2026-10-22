"""What we can read from Medal itself: its hotkeys, and whether it's recording right now."""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

SETTINGS = Path(os.environ.get("APPDATA", "")) / "Medal" / "store" / "settings.json"
RECORDING_MIN_AGE = 2.0     # an .icap marker has to stay this long to count (ignores any brief one)

KEY_NAMES = {"CONTROL": "ctrl", "CTRL": "ctrl", "ALT": "alt", "SHIFT": "shift", "WIN": "windows",
             "META": "windows", "CMD": "windows", "ESCAPE": "esc", "RETURN": "enter"}


def to_keyboard(inputs):
    """Medal's 'SHIFT + F7' -> 'shift+f7' (the format the keyboard module uses)."""
    parts = [p.strip() for p in str(inputs).split("+") if p.strip()]
    return "+".join(KEY_NAMES.get(p.upper(), p.lower()) for p in parts)


def hotkeys():
    """Medal's keyboard hotkeys as {action: hotkey}, e.g. {"clip;length=30": "shift+f7", "bookmark": "f8"}.
    Only the hotkey list is used; nothing else in Medal's settings is read or kept."""
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        items = data.get("recorder", {}).get("triggerHotkeys", [])
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Couldn't read Medal's hotkeys: %s", type(e).__name__)
        return {}
    out = {}
    for h in items if isinstance(items, list) else []:
        if isinstance(h, dict) and h.get("device", "keyboard") == "keyboard" and h.get("action") and h.get("inputs"):
            out.setdefault(str(h["action"]), to_keyboard(h["inputs"]))
    return out


def clip_hotkeys(keys=None):
    """[(clip length in seconds, hotkey)], shortest first."""
    keys = hotkeys() if keys is None else keys
    clips = []
    for action, hk in keys.items():
        if action.startswith("clip"):
            length = next((p.split("=", 1)[1] for p in action.split(";") if p.startswith("length=")), "0")
            clips.append((int(length) if length.isdigit() else 0, hk))
    return sorted(clips)


def bookmark_hotkey(keys=None):
    keys = hotkeys() if keys is None else keys
    return keys.get("bookmark", "")


def is_recording(medal_root):
    """Medal keeps <name>.mp4.icap / .cicap marker files in its Temp folder while a recording runs, and
    deletes them when it stops."""
    if not medal_root:
        return False
    try:
        now = time.time()
        for e in os.scandir(Path(medal_root) / "Temp"):
            if e.name.endswith(".icap") and now - e.stat().st_ctime >= RECORDING_MIN_AGE:
                return True
    except OSError:
        pass
    return False
