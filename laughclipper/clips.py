"""Checks that Medal really saved a clip, and keeps the clip history."""

import json
import logging
import os
import string
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import config
from .config import S

log = logging.getLogger(__name__)

HISTORY_PATH = config.DATA_DIR / "history.json"
HISTORY_MAX = 500
CONFIRM_TIMEOUT = 10        # seconds to wait for Medal's file (it usually appears within half a second)
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm")

_found = (0.0, None)        # (when we looked, folder) so we don't scan every drive on every clip


def clips_folder():
    """Medal's clips folder: the one chosen on the Clipping page, else X:\\Medal\\Clips on any drive."""
    global _found
    if S["clips_folder"]:
        p = Path(S["clips_folder"])
        return p if p.is_dir() else None
    if time.time() - _found[0] < 60:
        return _found[1]
    folder = None
    for root in [f"{d}:\\" for d in string.ascii_uppercase[2:]] + [str(Path.home() / "Videos")]:
        p = Path(root) / "Medal" / "Clips"
        try:
            if p.is_dir():
                folder = p
                break
        except OSError:
            continue
    _found = (time.time(), folder)
    return folder


def new_clips(folder, since):
    """Video files in the folder (or its per-game subfolders) created after `since`."""
    found = []
    try:
        dirs = [folder] + [Path(e.path) for e in os.scandir(folder) if e.is_dir() and e.stat().st_mtime >= since]
        for d in dirs:
            for e in os.scandir(d):
                if e.is_file() and e.name.lower().endswith(VIDEO_EXTS) and e.stat().st_ctime >= since:
                    found.append(Path(e.path))
    except OSError as e:
        log.warning("Couldn't look in %s: %s", folder, e)
    return found


class ClipWatcher(QObject):
    """After the hotkey: polls Medal's folder until a new clip appears, or gives up after a while."""
    found = Signal(str)
    missing = Signal()

    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self.folder = None
        self.since = self.deadline = 0.0

    def active(self):
        return self.timer.isActive()

    def start(self, folder):
        self.folder = Path(folder)
        self.since = time.time() - 1.0
        self.deadline = time.time() + CONFIRM_TIMEOUT
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def _poll(self):
        clips = new_clips(self.folder, self.since)
        if clips:
            self.timer.stop()
            self.found.emit(str(max(clips, key=lambda p: p.stat().st_ctime)))
        elif time.time() > self.deadline:
            self.timer.stop()
            self.missing.emit()


# ---------------------------------------------------------------- history
def load_history():
    try:
        items = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return [i for i in items if isinstance(i, dict) and "time" in i] if isinstance(items, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("Couldn't read the clip history: %s", e)
        return []


def save_history(items):
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-HISTORY_MAX:], indent=1), encoding="utf-8")
        tmp.replace(HISTORY_PATH)
    except Exception as e:
        log.warning("Couldn't save the clip history: %s", e)


def new_entry(moment, status, path=""):
    """One history entry. moment: what the Controller knew about the laugh."""
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "sound": moment.get("sound", "laughter"),
        "source": moment.get("source", ""),
        "game": moment.get("game") or (Path(path).parent.name if path else ""),
        "score": round(float(moment.get("peak", 0.0)), 2),
        "auto": bool(moment.get("auto")),
        "status": status,           # "saved" | "failed" | "unchecked" (no clips folder to check)
        "file": path,
    }
