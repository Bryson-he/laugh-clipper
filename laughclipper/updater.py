"""Checks GitHub Releases for a newer installer, downloads it and hands over to it."""

import hashlib
import json
import logging
import re
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import config
from .version import REPO, VERSION

log = logging.getLogger(__name__)

RETRY_WAITS = (5, 30)       # an automatic check that fails (no network yet at startup…) tries again after these


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

    def fetch_latest(self):
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "LaughClipper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    def _check(self, manual):
        self.state.emit("checking", {"manual": manual})
        try:
            waits = () if manual else RETRY_WAITS
            for attempt in range(len(waits) + 1):
                try:
                    data = self.fetch_latest()
                    break
                except Exception as e:
                    if attempt == len(waits):
                        raise
                    log.info("Update check failed (%s), trying again in %d s", e, waits[attempt])
                    time.sleep(waits[attempt])
            tag = data.get("tag_name", "")
            asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith(".exe")), None)
            if asset and parse_version(tag) > parse_version(VERSION):
                digest = str(asset.get("digest") or "")
                self.latest = {"version": tag.lstrip("vV"), "url": asset["browser_download_url"],
                               "size": asset.get("size", 0), "page": data.get("html_url", ""), "manual": manual,
                               "sha256": digest[7:].lower() if digest.startswith("sha256:") else ""}
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

    @staticmethod
    def verify(path, info):
        """Raises if the downloaded installer isn't the one GitHub published (cut short, or tampered with)."""
        size = path.stat().st_size
        if info.get("size") and size != info["size"]:
            raise ValueError(f"the download is incomplete ({size} of {info['size']} bytes)")
        if info.get("sha256"):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != info["sha256"]:
                raise ValueError("the download doesn't match the published installer")

    def _download(self):
        info = self.latest
        dest = Path(tempfile.gettempdir()) / f"LaughClipper-Setup-{info['version']}.exe"
        part = dest.with_suffix(".part")
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": "LaughClipper"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
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
            self.verify(part, info)
            part.replace(dest)
            self.state.emit("installing", {"path": str(dest), "version": info["version"]})
        except Exception as e:
            log.warning("Update download failed: %s", e)
            part.unlink(missing_ok=True)
            self.state.emit("error", {"manual": True, "message": str(e)})
        finally:
            self.busy = False
