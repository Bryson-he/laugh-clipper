"""Calibrate for your game: measure how high the game's own sounds score, then how high your laughs score,
and suggest a trigger level between the two. Optional — the sliders on the Detection page still work."""

import numpy as np
from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import winutil
from .config import S
from .engine import SOUNDS, source_id
from .setup_guide import StepDots, body_label, title_label
from .theme import T
from .widgets import AA, NO_PEN, Group, LevelMeter, Row, clamp, loudness, with_alpha

PLAY_SECONDS = 60          # listening to normal play
PLAY_MIN = 20              # can move on after this long
LAUGH_SECONDS = 30


def suggest(noise, laughs, triggers):
    """noise: {sound: [scores heard during normal play]}, laughs: [laughter scores while laughing] (may be
    empty if that step was skipped), triggers: the sounds that are switched on.
    Returns {"threshold", "sound_levels", "floors", "peak", "ok", "note"}."""
    triggers = [t for t in triggers if t in SOUNDS] or ["laughter"]
    # the game's "loud" moments, ignoring the odd one-off spike (it takes a sustained sound to trigger)
    floors = {s: float(np.percentile(noise[s], 97)) if noise.get(s) else 0.0 for s in triggers}
    main = "laughter" if "laughter" in triggers else triggers[0]
    above_game = min(0.9, floors[main] * 1.25 + 0.03)
    peak = max(laughs, default=0.0)
    ok, note = True, ""
    if not laughs:
        level = max(above_game, 0.08)
        note = "You skipped the laugh step, so it's set just above the game's own sounds."
    elif peak * 0.85 > above_game:
        level = max(above_game, peak * 0.6)          # comfortably under your laughs, above the game
        note = "Your laughs stand out clearly from the game."
    else:
        level = (above_game + peak) / 2
        ok = False
        note = ("Your laughs barely stand out from the game's sounds. Try turning off the game audio source on "
                "the Audio page, or give laughter only your mic and voice chat (the arrow on the Detection page).")
    level = round(clamp(level, 0.05, 0.9), 2)
    # other sounds the game makes a lot of (cheering in a sports game, say) get their own, higher level
    sound_levels = {}
    for s in triggers:
        own = round(min(0.9, floors[s] * 1.25 + 0.03), 2)
        if s != main and own > level:
            sound_levels[s] = own
    return {"threshold": level, "sound_levels": sound_levels, "floors": floors, "peak": peak, "ok": ok,
            "note": note, "main": main}


class ProgressBar(QWidget):
    """Thin accent capsule that fills up."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.value = 0.0

    def set(self, v):
        v = clamp(v)
        if abs(v - self.value) > 0.001:
            self.value = v
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setPen(NO_PEN)
        r = QRectF(self.rect())
        p.setBrush(T.c("meter_bg"))
        p.drawRoundedRect(r, 4, 4)
        if self.value > 0.01:
            p.setBrush(T.c("accent"))
            p.drawRoundedRect(QRectF(0, 0, max(8, r.width() * self.value), r.height()), 4, 4)


class ResultBar(QWidget):
    """The result at a glance: how high the game scored (grey), your best laugh (green) and the suggested
    trigger level (the tick), on one 0-1 scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.floor = self.peak = 0.0
        self.level = 0.3

    def set(self, floor, peak, level):
        self.floor, self.peak, self.level = floor, peak, level
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        w = self.width() - 2
        top, h = 22, 10
        p.setPen(NO_PEN)
        p.setBrush(T.c("meter_bg"))
        p.drawRoundedRect(QRectF(1, top, w, h), 5, 5)
        p.setBrush(with_alpha(T.c("meter_idle"), 0.7))
        p.drawRoundedRect(QRectF(1, top, max(h, w * clamp(self.floor)), h), 5, 5)
        if self.peak > 0:
            p.setBrush(T.c("green"))
            p.drawEllipse(QRectF(1 + w * clamp(self.peak) - 5, top, 10, 10))
        x = 1 + w * clamp(self.level)
        p.setBrush(T.c("accent"))
        p.drawRoundedRect(QRectF(x - 1.5, top - 6, 3, h + 12), 1.5, 1.5)
        p.setFont(T.font(11, 600))
        p.setPen(T.c("accent"))
        label = f"Trigger {self.level:.2f}"
        p.drawText(QRectF(x - 60, 0, 120, 16), Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(T.font(11))
        p.setPen(T.c("secondary"))
        p.drawText(QRectF(1, top + h + 6, w / 2, 16), Qt.AlignmentFlag.AlignLeft,
                   f"Game sounds up to {self.floor:.2f}")
        if self.peak > 0:
            p.setPen(T.c("green"))
            p.drawText(QRectF(1 + w / 2, top + h + 6, w / 2, 16), Qt.AlignmentFlag.AlignRight,
                       f"Your laughs up to {self.peak:.2f}")


class CalibrationWindow(QWidget):
    done = Signal()

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("root")
        self.setWindowTitle("Calibrate Laugh Clipper")
        self.setWindowIcon(ctrl.icon)
        self.setFixedSize(600, 520)
        self.noise = {}
        self.laughs = []
        self.result = None
        self.main = "laughter"      # the sound the main trigger level is for
        self.clock = QElapsedTimer()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack = QStackedWidget()
        for build in (self.page_intro, self.page_play, self.page_laugh, self.page_result):
            page = QWidget()
            page.setObjectName("content")
            pl = QVBoxLayout(page)
            pl.setContentsMargins(36, 30, 36, 10)
            pl.setSpacing(10)
            build(pl)
            pl.addStretch(1)
            self.stack.addWidget(page)
        lay.addWidget(self.stack, 1)

        bar = QWidget()
        bar.setObjectName("content")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(36, 12, 36, 22)
        self.dots = StepDots(4)
        bl.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.close)
        bl.addWidget(self.cancel)
        self.skip = QPushButton("Skip")
        self.skip.clicked.connect(lambda: self.go(3))
        bl.addWidget(self.skip)
        self.next = QPushButton("Start")
        self.next.setObjectName("prominent")
        self.next.clicked.connect(self.next_clicked)
        bl.addWidget(self.next)
        lay.addWidget(bar)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.sample)
        T.changed.connect(self.apply_titlebar)
        self.go(0)

    # ---------------------------------------------------------- pages
    def page_intro(self, pl):
        pl.addWidget(title_label("Calibrate for your game"))
        pl.addWidget(body_label("Every game sounds different. This listens to yours for a minute, then to you "
                                "laughing, and suggests a trigger level that catches your laughs but not the "
                                "game. Nothing changes until you apply it, and you can still adjust it by hand."))
        pl.addSpacing(6)
        g = Group()
        g.add(Row("1.  Play normally", "About a minute of your game, with the sound as you'd usually have it. "
                                      "Try not to laugh yet."))
        g.add(Row("2.  Laugh", "A few real laughs, or have a friend laugh in voice chat. You can skip this if "
                               "you're on your own."))
        g.add(Row("3.  Apply", "See the result and use the suggested level."))
        pl.addWidget(g)
        pl.addWidget(body_label("Tip: start your game first. This window can stay open on another monitor."))

    def page_play(self, pl):
        pl.addWidget(title_label("Play normally"))
        pl.addWidget(body_label("Keep playing with the sound up. Laugh Clipper is measuring how much the game's "
                                "own sounds (explosions, music, crowds) look like the sounds you trigger on."))
        pl.addSpacing(6)
        g = Group()
        self.play_meter = LevelMeter(240)
        self.play_row = g.add(Row("Game sounds", "Listening…", self.play_meter))
        self.play_progress = ProgressBar()
        self.play_progress.setFixedWidth(240)
        self.play_time = g.add(Row("Time", "", self.play_progress))
        pl.addWidget(g)

    def page_laugh(self, pl):
        pl.addWidget(title_label("Now laugh"))
        pl.addWidget(body_label("Laugh for real a few times (or get someone in voice chat to). Keep the game "
                                "going if you can, so it hears your laughs over it."))
        pl.addSpacing(6)
        g = Group()
        self.laugh_meter = LevelMeter(240)
        self.laugh_row = g.add(Row("Laugh score", "Waiting for a laugh…", self.laugh_meter))
        self.laugh_progress = ProgressBar()
        self.laugh_progress.setFixedWidth(240)
        g.add(Row("Time", None, self.laugh_progress))
        pl.addWidget(g)

    def page_result(self, pl):
        pl.addWidget(title_label("Your result"))
        self.result_note = body_label("")
        pl.addWidget(self.result_note)
        pl.addSpacing(6)
        g = Group()
        self.result_bar = ResultBar()
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(16, 12, 16, 10)
        hl.addWidget(self.result_bar)
        g.add(holder)
        self.result_rows = g
        pl.addWidget(g)
        self.extra = body_label("")
        pl.addWidget(self.extra)

    # ---------------------------------------------------------- steps
    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.dots.set_step(i)
        self.skip.setVisible(i == 2)
        self.next.setText({0: "Start", 1: "Next", 2: "Next", 3: "Apply"}[i])
        self.cancel.setText("Close" if i == 3 else "Cancel")
        if i in (1, 2):
            self.clock.start()
            self.timer.start()
            if i == 1:
                self.noise = {s: [] for s in S["triggers"]}
                self.main = "laughter" if "laughter" in self.noise else next(iter(self.noise), "laughter")
            else:
                self.laughs = []
        else:
            self.timer.stop()
        if i == 3:
            self.show_result()
        self.update_buttons()

    def next_clicked(self):
        i = self.stack.currentIndex()
        if i == 3:
            self.apply()
        else:
            self.go(i + 1)

    def update_buttons(self):
        i = self.stack.currentIndex()
        secs = self.clock.elapsed() / 1000 if self.clock.isValid() else 0
        if i == 1:
            self.next.setEnabled(secs >= PLAY_MIN)
        elif i == 2:
            self.next.setEnabled(bool(self.laughs) and max(self.laughs) >= 0.1)
        else:
            self.next.setEnabled(True)

    # ---------------------------------------------------------- measuring
    def scores_now(self):
        """{sound: highest score right now across the sources that sound listens to}"""
        eng = self.ctrl.engine
        out = {}
        for key in list(eng.threads):
            scores = eng.scores.get(key)
            if not scores:
                continue
            for s, v in scores.items():
                allowed = S["sound_sources"].get(s)
                if allowed is None or source_id(key) in allowed:
                    out[s] = max(out.get(s, 0.0), v)
        return out

    def sample(self):
        eng = self.ctrl.engine
        now = self.scores_now()
        secs = self.clock.elapsed() / 1000
        vol = max((loudness(eng.volume.get(k)) for k in list(eng.threads)), default=0.0)
        if self.stack.currentIndex() == 1:
            for s in self.noise:
                self.noise[s].append(now.get(s, 0.0))
            heard = self.noise.get(self.main, [])
            self.play_meter.set(now.get(self.main, 0.0), S["threshold"], vol)
            self.play_row.set_sub(f"Highest so far: {max(heard, default=0):.2f}" if heard else "Listening…")
            self.play_progress.set(secs / PLAY_SECONDS)
            left = max(0, int(PLAY_SECONDS - secs))
            self.play_time.set_sub(f"{left} s left" + ("" if secs >= PLAY_MIN else
                                                       f"  ·  Next in {int(PLAY_MIN - secs)} s"))
            if secs >= PLAY_SECONDS:
                self.go(2)
                return
        else:
            v = now.get(self.main, 0.0)
            self.laughs.append(v)
            self.laugh_meter.set(v, S["threshold"], vol)
            best = max(self.laughs)
            self.laugh_row.set_sub(f"Best so far: {best:.2f}" if best >= 0.05 else "Waiting for a laugh…")
            self.laugh_progress.set(secs / LAUGH_SECONDS)
            if secs >= LAUGH_SECONDS:
                self.go(3)
                return
        self.update_buttons()

    def show_result(self):
        laughs = self.laughs if self.laughs and max(self.laughs) >= 0.1 else []
        r = self.result = suggest(self.noise, laughs, S["triggers"])
        self.result_bar.set(r["floors"][r["main"]], r["peak"], r["threshold"])
        self.result_note.setText(f"Suggested trigger level: {r['threshold']:.2f} (now {S['threshold']:.2f}). "
                                 + r["note"])
        extra = [f"{SOUNDS[s][0]} gets its own level of {v:.2f}, because the game makes a lot of it."
                 for s, v in r["sound_levels"].items()]
        self.extra.setText("  ".join(extra))
        self.extra.setVisible(bool(extra))

    def apply(self):
        r = self.result
        levels = {k: v for k, v in S["sound_levels"].items() if k not in S["triggers"]}
        levels.update(r["sound_levels"])
        self.ctrl.set("threshold", r["threshold"])
        self.ctrl.set("sound_levels", levels)
        self.ctrl.tray.showMessage("Calibrated", f"Trigger level set to {r['threshold']:.2f}.", self.ctrl.icon, 4000)
        self.close()

    # ---------------------------------------------------------- window
    def apply_titlebar(self):
        winutil.style_titlebar(int(self.winId()), T.dark, T.p["window"], T.p["text"])

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_titlebar()

    def closeEvent(self, e):
        self.timer.stop()
        self.done.emit()
        super().closeEvent(e)
