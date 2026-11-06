"""Controller and wheel buttons, read with Windows' own joystick API (WinMM). It sees Xbox pads (through their
DirectInput side), PlayStation pads, racing wheels and button boxes, and keeps working while a game has focus.
A button is remembered as {"id": "VID_045E&PID_028E", "name": "Xbox 360 controller", "button": "3"}
("up"/"right"/"down"/"left" for the D-pad)."""

import ctypes
import logging
import sys
import threading
import winreg
from ctypes import wintypes as W

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

POLL_MS = 30              # read the buttons ~33 times a second
SCAN_MS = 3000            # look for newly plugged-in controllers this often
JOY_RETURNBUTTONS, JOY_RETURNPOV, JOYCAPS_HASPOV = 0x80, 0x40, 0x10
POV_DIRS = {0: "up", 9000: "right", 18000: "down", 27000: "left"}

# what the numbered buttons are on common pads (WinMM numbers them from 1)
XBOX = {"1": "A", "2": "B", "3": "X", "4": "Y", "5": "LB", "6": "RB", "7": "View", "8": "Menu",
        "9": "Left stick", "10": "Right stick"}
PLAYSTATION = {"1": "Square", "2": "Cross", "3": "Circle", "4": "Triangle", "5": "L1", "6": "R1", "7": "L2",
               "8": "R2", "9": "Create", "10": "Options", "11": "L3", "12": "R3", "13": "PS", "14": "Touchpad"}
VENDOR_LAYOUT = {0x045E: XBOX, 0x054C: PLAYSTATION}        # Microsoft, Sony


class JOYINFOEX(ctypes.Structure):
    _fields_ = [(n, W.DWORD) for n in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos",
                                         "dwVpos", "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1",
                                         "dwReserved2")]


class JOYCAPSW(ctypes.Structure):
    _fields_ = [("wMid", W.WORD), ("wPid", W.WORD), ("szPname", W.WCHAR * 32)] + \
               [(n, W.UINT) for n in ("wXmin", "wXmax", "wYmin", "wYmax", "wZmin", "wZmax", "wNumButtons",
                                      "wPeriodMin", "wPeriodMax", "wRmin", "wRmax", "wUmin", "wUmax", "wVmin",
                                      "wVmax", "wCaps", "wMaxAxes", "wNumAxes", "wMaxButtons")] + \
               [("szRegKey", W.WCHAR * 32), ("szOEMVxD", W.WCHAR * 260)]


def oem_name(device_id):
    """The name Windows keeps for a controller ("DualSense Wireless Controller"), or ""."""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            path = rf"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM\{device_id}"
            with winreg.OpenKey(root, path) as k:
                name = str(winreg.QueryValueEx(k, "OEMName")[0]).strip()
                if name:
                    return name
        except OSError:
            continue
    return ""


def tidy_name(name):
    """'Controller (XBOX 360 For Windows)' -> 'Xbox 360 controller'."""
    low = name.lower()
    if "xbox" in low:
        return "Xbox One controller" if "one" in low else "Xbox 360 controller" if "360" in low else "Xbox controller"
    return name.strip()


def vendor(device_id):
    try:
        return int(device_id[4:8], 16)
    except ValueError:
        return 0


def button_name(info):
    """'X', 'Cross', 'D-pad up' or 'Button 7'."""
    b = str(info.get("button", ""))
    if b in POV_DIRS.values():
        return f"D-pad {b}"
    return VENDOR_LAYOUT.get(vendor(info.get("id", "")), {}).get(b) or f"Button {b}"


def label(info):
    """'X on Xbox 360 controller', for the settings page."""
    if not info:
        return ""
    return f"{button_name(info)} on {info.get('name') or 'controller'}"


def same_button(a, b):
    return bool(a and b) and a.get("id") == b.get("id") and str(a.get("button")) == str(b.get("button"))


class WinMM:
    """The real thing. devices() -> [(index, id, name, has_pov)]; buttons(index, has_pov) -> set or None."""

    def __init__(self):
        self.dll = ctypes.WinDLL("winmm")
        self.dll.joyGetPosEx.argtypes = [W.UINT, ctypes.POINTER(JOYINFOEX)]
        self.dll.joyGetPosEx.restype = W.UINT
        self.dll.joyGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(JOYCAPSW), W.UINT]
        self.dll.joyGetDevCapsW.restype = W.UINT
        self.dll.joyGetNumDevs.restype = W.UINT
        try:
            self.dll.joyConfigChanged.argtypes = [W.DWORD]
            self.config_changed = self.dll.joyConfigChanged
        except AttributeError:
            self.config_changed = None

    def devices(self):
        if self.config_changed:
            self.config_changed(0)          # WinMM only notices controllers plugged in after this
        found = []
        for i in range(min(self.dll.joyGetNumDevs(), 16)):
            if self.buttons(i, False) is None:
                continue
            caps = JOYCAPSW()
            if self.dll.joyGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)):
                continue
            dev_id = f"VID_{caps.wMid:04X}&PID_{caps.wPid:04X}"
            name = tidy_name(oem_name(dev_id) or caps.szPname)
            found.append((i, dev_id, name, bool(caps.wCaps & JOYCAPS_HASPOV)))
        return found

    def buttons(self, index, has_pov):
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(info)
        info.dwFlags = JOY_RETURNBUTTONS | (JOY_RETURNPOV if has_pov else 0)
        if self.dll.joyGetPosEx(index, ctypes.byref(info)):
            return None                         # unplugged, or no such controller
        down = {str(n + 1) for n in range(32) if info.dwButtons & (1 << n)}
        if has_pov and info.dwPOV in POV_DIRS:
            down.add(POV_DIRS[info.dwPOV])
        return down


class Pads(QObject):
    """Watches every controller while active and emits `pressed` with the button's info when one goes down
    (a button held when we start watching doesn't count until it's pressed again)."""
    pressed = Signal(dict)
    _scanned = Signal(list)

    def __init__(self, backend=None):
        super().__init__()
        self.backend = backend
        self.devices = []
        self.last = {}          # index -> set of buttons down
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self.read)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(SCAN_MS)
        self.scan_timer.timeout.connect(self.scan_later)
        self.scanning = False
        self._scanned.connect(self.use_devices)

    def available(self):
        if self.backend is None and sys.platform == "win32":
            try:
                self.backend = WinMM()
            except OSError as e:
                log.warning("Controllers unavailable: %s", e)
                self.backend = False
        return bool(self.backend)

    def set_active(self, on):
        if on and not self.poll.isActive() and self.available():
            self.scan()
            self.poll.start()
            self.scan_timer.start()
        elif not on and self.poll.isActive():
            self.poll.stop()
            self.scan_timer.stop()
            self.last.clear()

    def scan(self):
        """Look for controllers now (50+ ms, so the timer does it on a thread: see scan_later)."""
        self.apply(self.list_devices())

    def scan_later(self):
        if self.scanning:
            return
        self.scanning = True

        def worker():
            try:
                self._scanned.emit(self.list_devices())
            finally:
                self.scanning = False
        threading.Thread(target=worker, daemon=True, name="controllers").start()

    def list_devices(self):
        try:
            return self.backend.devices()
        except Exception as e:
            log.warning("Couldn't list controllers: %s", e)
            return []

    def use_devices(self, devices):
        if self.poll.isActive():                     # not stopped while the scan was running
            self.apply(devices)

    def apply(self, devices):
        if [d[:3] for d in devices] != [d[:3] for d in self.devices]:
            log.info("controllers: %s", ", ".join(d[2] for d in devices) or "none")
        self.devices = devices
        self.last = {i: s for i, s in self.last.items() if any(d[0] == i for d in devices)}

    def read(self):
        for index, dev_id, name, has_pov in self.devices:
            try:
                down = self.backend.buttons(index, has_pov)
            except Exception:
                down = None
            if down is None:
                self.last.pop(index, None)
                continue
            before = self.last.get(index)
            self.last[index] = down
            if before is None:
                continue                         # first look at it: only remember what's held
            for b in sorted(down - before):
                self.pressed.emit({"id": dev_id, "name": name, "button": b})
