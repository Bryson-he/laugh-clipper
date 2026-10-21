"""Checks GitHub Releases for a newer installer, downloads it and hands over to it."""

import json
import logging
import re
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import config
from .version import REPO, VERSION

log = logging.getLogger(__name__)


def parse_version(v):
    nums = re.findall(r"\d+", v or "")[:3]
    return tuple(int(x) for x in nums + ["0"] * (3 - len(nums)))


class Updater(QObject):
    # state: checking | uptodate | available | downloading | installing | error | unsupported
    state = Signal(str, dict)

    def __init__(self):
        super().__init__()
        self.latest = None
        self.busy = False

    @staticmethod
    def supported():
        return bool(REPO) and config.FROZEN and sys.platform == "win32"

    def check(self, manual=False):
        if not self.supported():
            self.state.emit("unsupported", {"manual": manual})
            return
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._check, args=(manual,), daemon=True, name="update-check").start()

    def _check(self, manual):
        self.state.emit("checking", {"manual": manual})
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "LaughClipper"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            tag = data.get("tag_name", "")
            asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith(".exe")), None)
            if asset and parse_version(tag) > parse_version(VERSION):
                self.latest = {"version": tag.lstrip("vV"), "url": asset["browser_download_url"],
                               "size": asset.get("size", 0), "page": data.get("html_url", ""), "manual": manual}
                self.state.emit("available", dict(self.latest))
            else:
                self.state.emit("uptodate", {"manual": manual})
        except Exception as e:
            log.warning("Update check failed: %s", e)
            self.state.emit("error", {"manual": manual, "message": str(e)})
        finally:
            self.busy = False

    def install(self):
        if not self.latest or self.busy:
            return
        self.busy = True
        threading.Thread(target=self._download, daemon=True, name="update-download").start()

    def _download(self):
        info = self.latest
        try:
            dest = Path(tempfile.gettempdir()) / f"LaughClipper-Setup-{info['version']}.exe"
            req = urllib.request.Request(info["url"], headers={"User-Agent": "LaughClipper"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or info.get("size") or 0)
                done, last = 0, -1
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else 0
                    if pct != last:
                        last = pct
                        self.state.emit("downloading", {"progress": pct})
            self.state.emit("installing", {"path": str(dest), "version": info["version"]})
        except Exception as e:
            log.warning("Update download failed: %s", e)
            self.state.emit("error", {"manual": True, "message": str(e)})
        finally:
            self.busy = False
