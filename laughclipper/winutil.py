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

GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER = -20, 0x80, 4
GWL_STYLE, WS_CAPTION = -16, 0x00C00000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYSTEM_EXES = {"explorer.exe", "applicationframehost.exe", "textinputhost.exe", "systemsettings.exe",
               "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe"}
# where launchers install games (lower-case path pieces), and exes in those folders that aren't games
GAME_FOLDERS = ("\\steamapps\\common\\", "\\epic games\\", "\\xboxgames\\", "\\riot games\\", "\\ea games\\",
                "\\ubisoft game launcher\\games\\", "\\gog galaxy\\games\\", "\\gog games\\")
NOT_GAME_FOLDERS = ("\\riot client\\", "\\epic games\\launcher\\", "\\wallpaper_engine\\")
NOT_GAME_WORDS = ("launcher", "crash", "report", "helper", "setup", "unins", "redist", "installer", "updater",
                  "anticheat")

if IS_WIN:
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE = wintypes.HANDLE(-1).value

    # our own handles with proper 64-bit types, so we don't change ctypes.windll for anyone else
    _user32 = ctypes.WinDLL("user32")
    _kernel32 = ctypes.WinDLL("kernel32")
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    _user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                      ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


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
    # only the installed app keeps the entry pointing at itself; a run from source or a local build
    # mustn't take over the startup entry of the copy that's installed
    if had_legacy or (startup_enabled() and getattr(sys, "frozen", False) and "\\Programs\\" in sys.executable):
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


def foreground_app():
    """(process id, exe path, covers its whole monitor) for the window in front."""
    if not IS_WIN:
        return 0, "", False
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", False
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value, process_path(pid.value), _is_fullscreen(hwnd)


def running_apps():
    """Apps with a visible window, other than us and Windows itself: [(exe path, window title)]."""
    if not IS_WIN:
        return []
    found = {}
    me = os.getpid()

    @WNDENUMPROC
    def visit(hwnd, _):
        if (_user32.IsWindowVisible(hwnd) and not _user32.GetWindow(hwnd, GW_OWNER)
                and not _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            n = _user32.GetWindowTextLengthW(hwnd)
            if n:
                title = ctypes.create_unicode_buffer(n + 1)
                _user32.GetWindowTextW(hwnd, title, n + 1)
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                path = process_path(pid.value) if pid.value != me else ""
                if path and Path(path).name.lower() not in SYSTEM_EXES:
                    found.setdefault(path, title.value)
        return True
    _user32.EnumWindows(visit, 0)
    return sorted(found.items(), key=lambda kv: exe_description(kv[0]).lower())


def is_library_game(path):
    """True for an exe installed in a game launcher's library (Steam, Epic, Xbox…), but not the launchers."""
    p = path.lower().replace("/", "\\")
    if any(f in p for f in NOT_GAME_FOLDERS) or any(w in Path(p).stem for w in NOT_GAME_WORDS):
        return False
    return any(folder in p for folder in GAME_FOLDERS)


def process_running(exe):
    """Is a process with this exe name (e.g. 'medal.exe') running? None if we can't tell."""
    if not IS_WIN:
        return None
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe:
                return True
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        _kernel32.CloseHandle(snap)


def open_file(path):
    """Open a file with its default app (a clip in the video player)."""
    if IS_WIN:
        os.startfile(os.path.normpath(path))


def reveal(path):
    """Open Explorer with the file selected."""
    if IS_WIN:
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def medal_path():
    """Medal's launcher (it installs per user with Squirrel), or "" if it isn't installed there."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Medal"
    for p in (base / "Medal.exe", base / "current" / "Medal.exe"):
        if p.exists():
            return str(p)
    return ""


def process_path(pid):
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = wintypes.DWORD(len(buf))
        return buf.value if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        _kernel32.CloseHandle(h)


_descriptions = {}


def exe_description(path):
    """The exe's friendly name from its version info ('Rocket League'), else its file name."""
    if path in _descriptions:
        return _descriptions[path]
    name = Path(path).stem
    if IS_WIN and path:
        try:
            ver = ctypes.WinDLL("version")
            size = ver.GetFileVersionInfoSizeW(path, None)
            if size:
                data = ctypes.create_string_buffer(size)
                if ver.GetFileVersionInfoW(path, 0, size, data):
                    ptr, n = ctypes.c_void_p(), wintypes.UINT()
                    langs = []
                    if ver.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(n)):
                        arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
                        langs.append(f"{arr[0]:04x}{arr[1]:04x}")
                    for lang in langs + ["040904b0", "040904e4"]:
                        key = rf"\StringFileInfo\{lang}\FileDescription"
                        if ver.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(n)) and n.value > 1:
                            desc = ctypes.wstring_at(ptr, n.value).split("\x00")[0].strip()
                            if desc:
                                name = desc
                                break
        except Exception:
            pass
    _descriptions[path] = name
    return name


def _is_fullscreen(hwnd):
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):   # the desktop and taskbar
        return False
    # a maximized window also covers a monitor with no (or an auto-hidden) taskbar, but it keeps its title bar
    if _user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION == WS_CAPTION:
        return False
    r = wintypes.RECT()
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not (_user32.GetWindowRect(hwnd, ctypes.byref(r))
            and _user32.GetMonitorInfoW(_user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


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


def is_admin():
    """This copy of Laugh Clipper is running as administrator."""
    if not IS_WIN:
        return False
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except OSError:
        return False


def runs_elevated(pid):
    """True when that process runs as administrator and we don't: Windows then stops our key presses
    reaching it (and Medal's hotkey with them). None when it can't be told."""
    if not IS_WIN or not pid or is_admin():
        return False
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    proc = k32.OpenProcess(0x1000, False, pid)                 # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(proc, 0x0008, ctypes.byref(token)):       # TOKEN_QUERY
            return True if ctypes.get_last_error() == 5 else None                 # access denied: it's above us
        try:
            elevated, size = wintypes.DWORD(), wintypes.DWORD()
            ok = advapi.GetTokenInformation(token, 20, ctypes.byref(elevated), 4, ctypes.byref(size))  # TokenElevation
            return bool(elevated.value) if ok else None
        finally:
            k32.CloseHandle(token)
    finally:
        k32.CloseHandle(proc)


def animations_enabled():
    """Windows' Settings → Accessibility → Visual effects → Animation effects. True when it can't be told."""
    if not IS_WIN:
        return True
    on = wintypes.BOOL(True)
    try:
        user32 = ctypes.WinDLL("user32")
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        if not user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):     # SPI_GETCLIENTAREAANIMATION
            return True
    except OSError:
        return True
    return bool(on.value)


def thumbnail(path, width, height):
    """The thumbnail Windows Explorer would show for a file, as (width, height, BGRA bytes), or None.
    Slow the first time for a video (Windows decodes a frame), so call it off the UI thread."""
    if not IS_WIN:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", ctypes.c_void_p)]

    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    gdi32, user32 = ctypes.WinDLL("gdi32"), ctypes.WinDLL("user32")
    ole32.CoInitializeEx(None, 0)
    # IShellItemImageFactory {bcc18b79-ba16-442f-80c4-8a59c30c463b}
    iid = GUID(0xbcc18b79, 0xba16, 0x442f, (ctypes.c_ubyte * 8)(0x80, 0xc4, 0x8a, 0x59, 0xc3, 0x0c, 0x46, 0x3b))
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                    ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    factory = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(iid), ctypes.byref(factory)) or not factory:
        return None
    vtable = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HBITMAP))(vtable[3])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    bitmap = wintypes.HBITMAP()
    try:
        if get_image(factory, SIZE(width, height), 0x8, ctypes.byref(bitmap)) or not bitmap:    # SIIGBF_THUMBNAILONLY
            return None
    finally:
        release(factory)
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    try:
        bm = BITMAP()
        if not gdi32.GetObjectW(bitmap, ctypes.sizeof(bm), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)   # top-down
        buf = ctypes.create_string_buffer(w * h * 4)
        dc = user32.GetDC(None)
        try:
            if not gdi32.GetDIBits(dc, bitmap, 0, h, buf, ctypes.byref(header), 0):
                return None
        finally:
            user32.ReleaseDC(None, dc)
        return w, h, buf.raw
    finally:
        gdi32.DeleteObject(bitmap)


def mica_supported():
    """Windows 11 22H2 or later, where a window can ask for the Mica backdrop (the wallpaper-tinted material
    behind Windows 11's own Settings sidebar)."""
    return IS_WIN and sys.getwindowsversion().build >= 22621


def apply_mica(hwnd, dark):
    """Ask Windows to draw Mica behind the window. It shows wherever the window paints nothing, so the window
    needs a translucent background. False if Windows said no."""
    if not mica_supported():
        return False

    class MARGINS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_int) for n in ("left", "right", "top", "bottom")]
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
        dwm.DwmSetWindowAttribute.restype = dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        if dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1))):
            return False
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1 if dark else 0)), 4)    # dark material
        return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(2)), 4) == 0        # backdrop: Mica
    except OSError:
        return False
