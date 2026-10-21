"""First-run setup guide: Medal's hotkey, what to listen to, and a quick laugh to set the trigger level."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from . import winutil
from .config import S, hotkey_label
from .engine import short_name
from .theme import T
from .widgets import AA, NO_PEN, AnimDriver, Group, KeyCap, LevelMeter, Row, StatusPill, Switch, blend


class StepDots(QWidget):
    """Page dots; the current one stretches into a pill and slides along with a spring."""

    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.count = count
        self.setFixedSize(count * 16 + 14, 12)       # dots are 6 wide with 10 between; the current one is 20
        self.anim = AnimDriver(self)
        self.pos = self.anim.spring(0.0, 0.42, 0.78)

    def set_step(self, i):
        self.anim.to(self.pos, i)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setPen(NO_PEN)
        cy, x = self.height() / 2, 0.0
        for i in range(self.count):
            near = max(0.0, 1 - abs(self.pos.value - i))            # 1 on the current step, 0 two steps away
            w = 6 + 14 * near
            p.setBrush(blend(T.c("tertiary"), T.c("accent"), near))
            p.drawRoundedRect(QRectF(x, cy - 3, w, 6), 3, 3)
            x += w + 10


def title_label(text):
    t = QLabel(text)
    t.setObjectName("pageTitle")
    t.setWordWrap(True)
    return t


def body_label(text):
    b = QLabel(text)
    b.setObjectName("secondary")
    b.setWordWrap(True)
    b.setStyleSheet("font-size: 13px;")
    return b


class SetupGuide(QWidget):
    done = Signal()
    STEPS = 5

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("root")
        self.setWindowTitle("Set Up Laugh Clipper")
        self.setWindowIcon(ctrl.icon)
        self.setFixedSize(640, 600)
        self.peak = 0.0
        self.out_rows = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack = QStackedWidget()
        for build in (self.page_welcome, self.page_hotkey, self.page_sources, self.page_calibrate, self.page_done):
            page = QWidget()
            page.setObjectName("content")
            pl = QVBoxLayout(page)
            pl.setContentsMargins(40, 34, 40, 10)
            pl.setSpacing(10)
            build(pl)
            pl.addStretch(1)
            self.stack.addWidget(page)
        lay.addWidget(self.stack, 1)

        bar = QWidget()
        bar.setObjectName("content")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(40, 12, 40, 24)
        self.dots = StepDots(self.STEPS)
        bl.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        self.back = QPushButton("Back")
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        bl.addWidget(self.back)
        self.next = QPushButton("Continue")
        self.next.setObjectName("prominent")
        self.next.setDefault(True)
        self.next.clicked.connect(self.next_clicked)
        bl.addWidget(self.next)
        lay.addWidget(bar)

        ctrl.changed.connect(self.on_changed)
        ctrl.devices_changed.connect(lambda *_: self.build_sources())
        T.changed.connect(self.apply_titlebar)
        self.live = QTimer(self)
        self.live.setInterval(60)
        self.live.timeout.connect(self.refresh_live)
        self.live.start()
        self.go(0, animate=False)

    # ---------------------------------------------------------- pages
    def page_welcome(self, pl):
        pl.addSpacing(24)
        icon = QLabel()
        icon.setPixmap(self.ctrl.icon.pixmap(96, 96))
        pl.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        pl.addSpacing(6)
        t = title_label("Welcome to Laugh Clipper")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pl.addWidget(t)
        b = body_label("It listens while you play and saves a Medal clip when someone laughs, so the funniest "
                       "moments are never lost. Setting it up takes about a minute.")
        b.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pl.addWidget(b)
        pl.addSpacing(14)
        g = Group()
        g.add(Row("Hears your mic, game and voice chat", "Each one separately, with its own meter."))
        g.add(Row("Asks, or saves the clip for you", "A small popup that never takes focus from your game."))
        g.add(Row("Checks Medal really saved it", "And keeps a history of every clip."))
        pl.addWidget(g)

    def page_hotkey(self, pl):
        pl.addWidget(title_label("Match Medal's clip hotkey"))
        pl.addWidget(body_label("Laugh Clipper saves a clip by pressing Medal's clip hotkey for you, so it has to "
                                "be the same clip hotkey you've set in Medal's settings."))
        pl.addSpacing(8)
        g = Group()
        self.keycap = KeyCap(hotkey_label())
        self.keycap.clicked.connect(self.record_hotkey)
        g.add(Row("Medal clip hotkey", "Click it, then press the keys.", self.keycap))
        self.medal_pill = StatusPill("Running", "green")
        self.open_medal = QPushButton("Open Medal")
        self.open_medal.clicked.connect(self.ctrl.open_medal)
        self.medal_row = g.add(Row("Medal", "", self.open_medal, self.medal_pill))
        pl.addWidget(g)
        self.update_medal()

    def page_sources(self, pl):
        pl.addWidget(title_label("What should it listen to?"))
        pl.addWidget(body_label("Your mic hears you. Turn on the outputs your game and voice chat play through so "
                                "it hears your friends laughing too. Leave music and browser audio off."))
        pl.addSpacing(8)
        self.src_group = Group()
        pl.addWidget(self.src_group)
        self.build_sources()

    def page_calibrate(self, pl):
        pl.addWidget(title_label("Laugh to set the trigger"))
        pl.addWidget(body_label("Laugh for a few seconds (or play something funny). The bar shows how sure it is "
                                "that it's hearing a laugh. Then use the suggested level, set a little below "
                                "your best score."))
        pl.addSpacing(8)
        g = Group()
        self.cal_meter = LevelMeter(260)
        self.cal_row = g.add(Row("Laugh score", "Waiting for a laugh…", self.cal_meter))
        self.use_level = QPushButton("Use Suggested Level")
        self.use_level.setObjectName("prominent")
        self.use_level.clicked.connect(self.apply_level)
        again = QPushButton("Start Over")
        again.setObjectName("small")
        again.clicked.connect(self.reset_peak)
        self.level_row = g.add(Row("Trigger level", "", again, self.use_level))
        pl.addWidget(g)
        pl.addWidget(body_label("You can fine-tune it any time on the Detection page."))
        self.update_level_row()

    def page_done(self, pl):
        pl.addWidget(title_label("You're all set"))
        pl.addWidget(body_label("Laugh Clipper keeps running in the system tray. Click its icon to open the "
                                "settings, or right-click it to pause."))
        pl.addSpacing(8)
        g = Group()
        auto = Switch(S["auto_clip"])
        auto.toggled.connect(lambda v: self.ctrl.set("auto_clip", v))
        self.auto_sw = auto
        g.add(Row("Save clips automatically", "Off: a popup asks first. On: it saves once the laughing stops.",
                  auto))
        start = Switch(winutil.startup_enabled())
        start.toggled.connect(winutil.set_startup)
        g.add(Row("Start with Windows", None, start))
        game = Switch(S["game_only"])
        game.toggled.connect(lambda v: self.ctrl.set("game_only", v))
        g.add(Row("Only listen while playing", "Pauses when no game is in front, so videos don't set it off.",
                  game))
        pl.addWidget(g)

    # ---------------------------------------------------------- navigation
    def go(self, i, animate=True):
        i = max(0, min(self.STEPS - 1, i))
        self.stack.setCurrentIndex(i)
        self.dots.set_step(i)
        self.back.setVisible(i > 0)
        self.next.setText({0: "Get Started", self.STEPS - 1: "Finish"}.get(i, "Continue"))
        if i == 3:
            self.reset_peak()
        if animate:
            w = self.stack.currentWidget()
            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setDuration(220)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.finished.connect(lambda: w.setGraphicsEffect(None))
            a.start()
            self._fade = a

    def next_clicked(self):
        i = self.stack.currentIndex()
        if i == self.STEPS - 1:
            self.close()
        else:
            self.go(i + 1)

    # ---------------------------------------------------------- hotkey + Medal
    def record_hotkey(self):
        self.keycap.recording = True
        self.keycap.set_label("Press keys…")
        self.ctrl.record_hotkey()

    def update_medal(self):
        ok = self.ctrl.medal_ok
        self.medal_pill.text, self.medal_pill.color_key = ("Running", "green") if ok else ("Not running", "orange")
        self.medal_pill.setFixedWidth(84 if ok else 104)
        self.medal_pill.update()
        self.open_medal.setVisible(not ok)
        self.medal_row.set_sub("Ready to clip." if ok else "Medal needs to be running to save clips.")

    def on_changed(self, key):
        if key == "hotkey":
            self.keycap.recording = False
            self.keycap.set_label(hotkey_label())
        elif key == "medal":
            self.update_medal()
        elif key in ("listen_mic", "outputs"):
            self.build_sources()
        elif key == "threshold":
            self.update_level_row()
        elif key == "auto_clip":
            self.auto_sw.set_silent(S["auto_clip"])

    # ---------------------------------------------------------- sources
    def build_sources(self):
        g = self.src_group
        g.clear()
        self.out_rows = {}
        mic_sw = Switch(S["listen_mic"])
        mic_sw.toggled.connect(lambda v: self.ctrl.set("listen_mic", v))
        self.mic_meter = LevelMeter()
        g.add(Row("Microphone", "Your voice, from the Windows default mic.", self.mic_meter, mic_sw))
        if not self.ctrl.devices:
            g.add(Row("Looking for audio devices…"))
        else:
            outs = S["outputs"] or []
            for n in self.ctrl.devices[1]:
                sw = Switch(n in outs)
                sw.toggled.connect(lambda on, n=n: self.toggle_output(n, on))
                meter = LevelMeter()
                g.add(Row(short_name(n), n if short_name(n) != n else None, meter, sw))
                self.out_rows[n] = meter
        g.update()

    def toggle_output(self, name, on):
        outs = [n for n in (S["outputs"] or []) if n != name] + ([name] if on else [])
        self.ctrl.set("outputs", outs)

    # ---------------------------------------------------------- calibration
    def reset_peak(self):
        self.peak = 0.0
        self.update_level_row()

    def suggested(self):
        return round(min(max(self.peak * 0.75, 0.08), 0.6), 2)

    def update_level_row(self):
        if self.peak >= 0.12:
            self.level_row.set_sub(f"Now {S['threshold']:.2f}. Suggested: {self.suggested():.2f}")
            self.use_level.setText(f"Use {self.suggested():.2f}")
            self.use_level.setEnabled(True)
        else:
            self.level_row.set_sub(f"Now {S['threshold']:.2f}. Laugh to get a suggestion.")
            self.use_level.setText("Use Suggested Level")
            self.use_level.setEnabled(False)

    def apply_level(self):
        self.ctrl.set("threshold", self.suggested())
        self.level_row.set_sub(f"Set to {S['threshold']:.2f}.")

    def raw(self, key):
        eng = self.ctrl.engine
        return eng.sound.get(key, (None, 0.0))[1] if key in eng.threads else None

    def refresh_live(self):
        if not self.isVisible():
            return
        thr = S["threshold"]
        mic = self.raw(("mic", S["mic_device"]))
        if hasattr(self, "mic_meter"):
            self.mic_meter.set(mic, thr)
        for name, meter in self.out_rows.items():
            meter.set(self.raw(("out", name)), thr)
        if self.stack.currentIndex() == 3:
            now = max((self.raw(k) or 0.0 for k in list(self.ctrl.engine.threads)), default=0.0)
            self.cal_meter.set(now, thr)
            if now > self.peak + 0.005:
                self.peak = now
                self.update_level_row()
            self.cal_row.set_sub(f"Best so far: {self.peak:.2f}" if self.peak >= 0.05 else "Waiting for a laugh…")

    # ---------------------------------------------------------- window
    def apply_titlebar(self):
        winutil.style_titlebar(int(self.winId()), T.dark, T.p["window"], T.p["text"])

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_titlebar()

    def closeEvent(self, e):
        self.live.stop()
        self.ctrl.set("setup_done", True)
        self.done.emit()
        super().closeEvent(e)
