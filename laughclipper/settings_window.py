"""The settings window, laid out like macOS System Settings."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import winutil
from .config import DEFAULTS, S, hotkey_label
from .engine import short_name
from .theme import T
from .version import VERSION
from .widgets import (Group, KeyCap, LevelMeter, NavItem, PopupButton, PositionPicker, Row, Segmented, Slider,
                      Switch)

PAGES = [
    ("General", "", "#8e8e93"),
    ("Audio", "", "#ff2d55"),
    ("Notification", "", "#ff3b30"),
    ("Clipping", "", "#5e5ce6"),
    ("Detection", "", "#34c759"),
]


def secs(v):
    v = float(v)
    return f"{v:g} s"


class Page(QScrollArea):
    def __init__(self, title):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("content")
        self.setWidget(inner)
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(30, 24, 30, 30)
        self.lay.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        self.lay.addWidget(t)
        self.lay.addSpacing(12)

    def section(self, header=None, footer=None, header_widget=None):
        if header or header_widget:
            hb = QHBoxLayout()
            hb.setContentsMargins(8, 6, 2, 2)
            if header:
                lbl = QLabel(header)
                lbl.setObjectName("header")
                hb.addWidget(lbl)
            hb.addStretch(1)
            if header_widget:
                hb.addWidget(header_widget)
            self.lay.addLayout(hb)
        g = Group()
        self.lay.addWidget(g)
        if footer:
            f = QLabel(footer)
            f.setObjectName("footer")
            f.setWordWrap(True)
            f.setContentsMargins(8, 4, 8, 10)
            self.lay.addWidget(f)
        else:
            self.lay.addSpacing(14)
        return g

    def finish(self):
        self.lay.addStretch(1)
        return self


class SettingsWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("root")
        self.setWindowTitle("Laugh Clipper")
        self.setWindowIcon(ctrl.icon)
        self.resize(900, 640)
        self.setMinimumSize(800, 560)
        self.switches = {}
        self.sliders = {}
        self.smooth = {}
        self.out_rows = {}
        self.mic_names = []
        self.upd_mode = "check"

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 16)
        sl.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(20, 0, 12, 16)
        head.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(ctrl.icon.pixmap(40, 40))
        head.addWidget(icon)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        name = QLabel("Laugh Clipper")
        name.setObjectName("appName")
        self.status = QLabel()
        self.status.setObjectName("secondary")
        tv.addWidget(name)
        tv.addWidget(self.status)
        head.addLayout(tv, 1)
        sl.addLayout(head)

        self.stack = QStackedWidget()
        builders = [self.page_general, self.page_audio, self.page_notification, self.page_clipping,
                    self.page_detection]
        self.nav = []
        for i, ((title, glyph, color), build) in enumerate(zip(PAGES, builders)):
            item = NavItem(title, glyph, color)
            item.clicked.connect(lambda i=i: self.select(i))
            sl.addWidget(item)
            self.nav.append(item)
            self.stack.addWidget(build())
        sl.addStretch(1)
        ver = QLabel(f"Version {VERSION}")
        ver.setObjectName("secondary")
        ver.setContentsMargins(22, 0, 0, 0)
        sl.addWidget(ver)

        outer.addWidget(side)
        outer.addWidget(self.stack, 1)
        self.select(0, animate=False)
        self.update_status()

        ctrl.changed.connect(self.on_setting_changed)
        ctrl.devices_changed.connect(self.build_devices)
        ctrl.update_changed.connect(self.on_update)
        T.changed.connect(self.apply_titlebar)
        QGuiApplication.instance().screenAdded.connect(lambda *_: self.refresh_displays())
        QGuiApplication.instance().screenRemoved.connect(lambda *_: self.refresh_displays())
        if ctrl.devices:
            self.build_devices(*ctrl.devices)
        self.on_update(*ctrl.update_state)

        self.live = QTimer(self)
        self.live.setInterval(60)
        self.live.timeout.connect(self.refresh_live)
        self.live.start()

    # ---------------------------------------------------------- helpers
    def switch(self, key):
        sw = Switch(S[key])
        sw.toggled.connect(lambda v: self.ctrl.set(key, v))
        self.switches[key] = sw
        return sw

    def slider_row(self, group, title, sub, key, lo, hi, step, fmt):
        val = QLabel(fmt(S[key]))
        val.setObjectName("value")
        val.setMinimumWidth(58)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sl = Slider(lo, hi, step, S[key])
        sl.setFixedWidth(210)
        is_int = isinstance(DEFAULTS[key], int)

        def changed(v):
            v = int(round(v)) if is_int else round(v, 2)
            val.setText(fmt(v))
            self.ctrl.set(key, v)
        sl.valueChanged.connect(changed)
        self.sliders[key] = sl
        return group.add(Row(title, sub, sl, val))

    def select(self, i, animate=True):
        for j, n in enumerate(self.nav):
            n.set_selected(j == i)
        if animate and self.stack.currentIndex() == i:
            return
        self.stack.setCurrentIndex(i)
        if animate:
            w = self.stack.currentWidget()
            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setDuration(200)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.finished.connect(lambda: w.setGraphicsEffect(None))
            a.start()
            self._page_anim = a

    # ---------------------------------------------------------- pages
    def page_general(self):
        p = Page("General")
        g = p.section()
        self.run_row = g.add(Row("Laugh detection", "", self.switch("running")))
        g = p.section(footer="When the window is closed, Laugh Clipper keeps running in the system tray.")
        self.startup_sw = Switch(winutil.startup_enabled())
        self.startup_sw.toggled.connect(winutil.set_startup)
        g.add(Row("Start with Windows", None, self.startup_sw))
        g.add(Row("Start in the tray", "Don't open this window when Laugh Clipper starts.",
                  self.switch("start_minimized")))
        g = p.section("Updates")
        self.upd_btn = QPushButton("Check for Updates")
        self.upd_btn.clicked.connect(self.update_clicked)
        self.upd_row = g.add(Row("Laugh Clipper", f"Version {VERSION}", self.upd_btn))
        g.add(Row("Check for updates automatically", None, self.switch("check_updates")))
        return p.finish()

    def page_audio(self):
        p = Page("Audio")
        g = p.section("Microphone")
        self.mic_meter = LevelMeter()
        self.mic_row = g.add(Row("Listen to my microphone", "", self.mic_meter, self.switch("listen_mic")))
        self.mic_popup = PopupButton()
        self.mic_popup.addItem("Windows default")
        self.mic_popup.activated.connect(self.mic_selected)
        g.add(Row("Input device", None, self.mic_popup))
        refresh = QPushButton("Refresh")
        refresh.setObjectName("small")
        refresh.clicked.connect(self.ctrl.engine.refresh_devices)
        self.out_group = p.section(
            "PC audio", header_widget=refresh,
            footer="With Elgato Wave Link, each channel shows up as its own device. Turn on only the ones "
                   "you want, like Game and Voice Chat, and leave Browser, Music and the Monitor/Stream "
                   "mixes off.")
        self.out_group.add(Row("Looking for audio devices…"))
        return p.finish()

    def page_notification(self):
        p = Page("Notification")
        g = p.section()
        self.seg = Segmented(self.ctrl.display_names(), S["monitor"] - 1)
        self.seg.changed.connect(lambda i: self.ctrl.set("monitor", i + 1))
        ident = QPushButton("Identify")
        ident.clicked.connect(self.ctrl.identify_displays)
        g.add(Row("Display", None, self.seg, ident))
        self.picker = PositionPicker(S["position"])
        self.picker.changed.connect(lambda s: self.ctrl.set("position", s))
        g.add(Row("Position", "Click where the notification should appear.", self.picker))
        self.slider_row(g, "Show for", "How long the question stays up before it goes away.",
                        "popup_seconds", 2, 30, 1, secs)
        test = QPushButton("Show Test Notification")
        test.setObjectName("prominent")
        test.clicked.connect(self.ctrl.test_toast)
        g.add(Row("Preview", None, test))
        return p.finish()

    def page_clipping(self):
        p = Page("Clipping")
        g = p.section(footer="Medal saves the last few seconds when the hotkey is pressed. Keep Medal's clip "
                             "length longer than the wait plus your longest laugh, or the start of the "
                             "moment gets cut off.")
        self.keycap = KeyCap(hotkey_label())
        self.keycap.clicked.connect(self.record_hotkey)
        g.add(Row("Medal clip hotkey", "Click to change. Must match the clip hotkey in Medal.", self.keycap))
        g.add(Row("Save clips automatically", "Saves the clip for you instead of asking.",
                  self.switch("auto_clip")))
        self.delay_row = self.slider_row(g, "Wait after laughing stops",
                                         "Keeps the whole laugh in the clip.", "clip_delay", 0, 30, 1,
                                         lambda v: "Instantly" if v == 0 else secs(v))
        self.maxwait_row = self.slider_row(g, "Save anyway after", "In case the laughing just won't stop.",
                                           "max_wait", 0, 120, 5, lambda v: "Never" if v == 0 else secs(v))
        self.update_auto_rows()
        return p.finish()

    def page_detection(self):
        p = Page("Detection")
        g = p.section(footer="Watch the live score while you laugh, then set the trigger just below where "
                             "it usually peaks.")
        self.live_meter = LevelMeter(210)
        g.add(Row("Live laugh score", "The highest score across everything you're listening to.",
                  self.live_meter))
        self.slider_row(g, "Trigger level", "Lower catches more laughs, higher means fewer false alarms.",
                        "threshold", 0.05, 0.9, 0.01, lambda v: f"{v:.2f}")
        self.slider_row(g, "Laugh length needed", "How long someone has to laugh before it counts.",
                        "min_laugh", 0.5, 3, 0.25, secs)
        self.slider_row(g, "Pause tolerance",
                        "Breaths and short pauses up to this long still count as the same laugh.",
                        "pause_tolerance", 0.5, 4, 0.25, secs)
        self.slider_row(g, "Wait between prompts", "Stops it asking again straight away.",
                        "cooldown", 0, 120, 5, secs)
        return p.finish()

    # ---------------------------------------------------------- audio devices
    def build_devices(self, mics, spks, default):
        self.mic_names = list(mics)
        items = ["Windows default"] + list(mics)
        cur = S["mic_device"]
        if cur and cur not in mics:
            items.append(f"{cur} (not connected)")
            self.mic_names.append(cur)
        self.mic_popup.blockSignals(True)
        self.mic_popup.clear()
        self.mic_popup.addItems(items)
        self.mic_popup.setCurrentIndex(self.mic_names.index(cur) + 1 if cur else 0)
        self.mic_popup.blockSignals(False)

        self.out_group.clear()
        self.out_rows = {}
        outs = S["outputs"] or []
        names = list(spks) + [n for n in outs if n not in spks]
        if not names:
            self.out_group.add(Row("No output devices found", "Click Refresh after plugging something in."))
        for n in names:
            meter = LevelMeter()
            sw = Switch(n in outs)
            sw.toggled.connect(lambda on, n=n: self.toggle_output(n, on))
            row = Row(short_name(n), None, meter, sw)
            self.out_group.add(row)
            self.out_rows[n] = (row, meter, n in spks)
        self.out_group.update()

    def mic_selected(self, i):
        self.ctrl.set("mic_device", "" if i <= 0 else self.mic_names[i - 1])

    def toggle_output(self, name, on):
        outs = list(S["outputs"] or [])
        if on and name not in outs:
            outs.append(name)
        elif not on and name in outs:
            outs.remove(name)
        self.ctrl.set("outputs", outs)

    def hearing(self, key):
        eng = self.ctrl.engine
        if key not in eng.threads:
            return None
        lvl = eng.levels.get(key)
        if lvl == "loading":
            return "Loading the sound model…"
        if lvl == "error":
            return "Can't open this device"
        info = eng.heard.get(key)
        if not info:
            return "Starting…"
        top = info[0]
        if top == "silence":
            return "Silent"
        text = f"Hearing: {top}"
        if isinstance(lvl, float) and lvl >= 0.05:
            text += f"  ·  laugh {lvl:.2f}"
        return text

    def display_level(self, key):
        eng = self.ctrl.engine
        lvl = eng.levels.get(key) if key in eng.threads else None
        if isinstance(lvl, float):
            v = max(lvl, self.smooth.get(key, 0.0) * 0.86)
            self.smooth[key] = v
            return v
        return None

    def refresh_live(self):
        if not self.isVisible():
            return
        thr = S["threshold"]
        mic = ("mic", S["mic_device"])
        self.mic_meter.set(self.display_level(mic), thr)
        self.mic_row.set_sub(self.hearing(mic) or "Off")
        for name, (row, meter, connected) in self.out_rows.items():
            key = ("out", name)
            meter.set(self.display_level(key), thr)
            h = self.hearing(key)
            row.set_sub(h if h else (name if connected else "Not connected"))
        levels = [v for k, v in self.smooth.items() if k in self.ctrl.engine.threads]
        self.live_meter.set(max(levels, default=0.0), thr)

    # ---------------------------------------------------------- misc
    def refresh_displays(self):
        self.seg.set_items(self.ctrl.display_names(), S["monitor"] - 1)

    def record_hotkey(self):
        self.keycap.recording = True
        self.keycap.set_label("Press keys…")
        self.ctrl.record_hotkey()

    def update_auto_rows(self):
        for row in (self.delay_row, self.maxwait_row):
            row.setEnabled(S["auto_clip"])

    def update_status(self):
        running = S["running"]
        green = T.css("green")
        n = len(self.ctrl.engine.wanted())
        if running:
            self.status.setText(f'<span style="color:{green}">●</span> Listening')
            self.run_row.set_sub(f"Listening to {n} source{'s' if n != 1 else ''}." if n else
                                 "Nothing selected. Pick a mic or PC audio in Audio.")
        else:
            self.status.setText("Paused")
            self.run_row.set_sub("Paused. Turn on to start listening for laughs.")

    def on_setting_changed(self, key):
        if key in self.switches:
            self.switches[key].set_silent(S[key])
        if key in ("running", "listen_mic", "outputs"):
            self.update_status()
        if key == "auto_clip":
            self.update_auto_rows()
        if key == "hotkey":
            self.keycap.recording = False
            self.keycap.set_label(hotkey_label())

    def update_clicked(self):
        if self.upd_mode == "available":
            self.ctrl.updater.install()
        else:
            self.ctrl.check_updates(manual=True)

    def on_update(self, state, info):
        btn, row = self.upd_btn, self.upd_row

        def prominent(on):
            btn.setObjectName("prominent" if on else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        btn.setEnabled(True)
        self.upd_mode = "check"
        prominent(False)
        if state == "checking":
            btn.setEnabled(False)
            btn.setText("Checking…")
        elif state == "available":
            self.upd_mode = "available"
            row.title.setText(f"Version {info['version']} is available")
            row.set_sub(f"You have version {VERSION}.")
            btn.setText("Update and Restart")
            prominent(True)
        elif state == "downloading":
            btn.setEnabled(False)
            btn.setText(f"Downloading… {info.get('progress', 0)}%")
        elif state == "installing":
            btn.setEnabled(False)
            btn.setText("Restarting…")
        else:
            row.title.setText("Laugh Clipper")
            btn.setText("Check for Updates")
            row.set_sub({
                "uptodate": f"Version {VERSION}. You're up to date.",
                "error": f"Version {VERSION}. Couldn't check for updates.",
                "unsupported": f"Version {VERSION}. Updates work in the installed app.",
            }.get(state, f"Version {VERSION}"))

    def apply_titlebar(self):
        winutil.style_titlebar(int(self.winId()), T.dark, T.p["window"], T.p["text"])

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_titlebar()
        self.startup_sw.set_silent(winutil.startup_enabled())

    def closeEvent(self, e):
        e.ignore()
        self.hide()
