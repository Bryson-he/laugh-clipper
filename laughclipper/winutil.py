"""Windows-specific bits. Everything here is a harmless no-op on other platforms."""

import ctypes
import logging
import os
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Laugh Clipper"
LEGACY_VBS = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
              / "Programs" / "Startup" / "Laugh Clipper.vbs")

log = logging.getLogger(__name__)
_mutex = None


def set_app_id(app_id):
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass


def create_mutex(name):
    """Lets the installer see that the app is running."""
    global _mutex
    if IS_WIN:
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)


def release_mutex():
    global _mutex
    if IS_WIN and _mutex:
        ctypes.windll.kernel32.CloseHandle(_mutex)
        _mutex = None


# ---------- start with Windows ----------
def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else exe
    return f'"{exe}" "{Path(sys.argv[0]).resolve()}" --minimized'


def startup_enabled():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        return True
    except OSError:
        return False


def set_startup(on):
    if not IS_WIN:
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as e:
        log.warning("Couldn't change startup setting: %s", e)


def migrate_startup():
    """Swap the old script's Startup-folder shortcut for a registry entry, and keep the path current."""
    had_legacy = False
    try:
        if LEGACY_VBS.exists():
            LEGACY_VBS.unlink()
            had_legacy = True
    except OSError:
        pass
    if had_legacy or startup_enabled():
        set_startup(True)


# ---------- windows ----------
def no_activate(hwnd):
    """Popup never steals focus from the game and has no taskbar button."""
    if not IS_WIN:
        return
    u = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x80, 0x08000000, 0x8
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)


def style_titlebar(hwnd, dark, caption_hex, text_hex):
    """Dark title bar on Windows 10/11, and a title bar colour that matches the window on Windows 11."""
    if not IS_WIN:
        return
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)

        def colorref(h):
            h = h.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return ctypes.c_uint(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref(caption_hex)), 4)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(colorref(text_hex)), 4)
    except Exception:
        pass
