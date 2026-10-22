"""Hand-painted, spring-animated controls in the style of macOS System Settings."""

import math

from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import T

AA = QPainter.RenderHint.Antialiasing
POINTER = Qt.CursorShape.PointingHandCursor
NO_PEN = Qt.PenStyle.NoPen


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def blend(a, b, t):
    t = clamp(t)
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def with_alpha(color, a):
    c = QColor(color)
    c.setAlphaF(clamp(a))
    return c


# ---------------------------------------------------------------- springs
class Spring:
    """Damped spring set up like SwiftUI's .spring(response:dampingFraction:).
    Retargeting mid-flight keeps the current velocity, so interrupted animations stay fluid."""

    def __init__(self, value=0.0, response=0.4, damping=0.85):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.configure(response, damping)

    def configure(self, response, damping):
        self.stiffness = (2 * math.pi / response) ** 2
        self.friction = 4 * math.pi * damping / response

    def step(self, dt):
        n = max(1, int(dt / 0.004) + 1)
        h = dt / n
        for _ in range(n):
            accel = -self.stiffness * (self.value - self.target) - self.friction * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h

    def settled(self):
        return abs(self.value - self.target) < 1e-3 and abs(self.velocity) < 1e-2

    def snap(self, value=None):
        if value is not None:
            self.target = float(value)
        self.value = self.target
        self.velocity = 0.0


class AnimDriver(QObject):
    """Steps a widget's springs at ~60 fps, only while something is moving."""

    def __init__(self, owner, on_step=None):
        super().__init__(owner)
        self.owner = owner
        self.on_step = on_step      # called after each step, for animations that change more than paint
        self.springs = []
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()

    def spring(self, value=0.0, response=0.4, damping=0.85):
        s = Spring(value, response, damping)
        self.springs.append(s)
        return s

    def to(self, spring, target):
        spring.target = float(target)
        if not self.timer.isActive():
            self.clock.start()
            self.timer.start()

    def _tick(self):
        dt = clamp(self.clock.restart() / 1000.0, 0.001, 0.05)
        for s in self.springs:
            s.step(dt)
        if all(s.settled() for s in self.springs):
            for s in self.springs:
                s.snap()
            self.timer.stop()
        if self.on_step:
            self.on_step()
        self.owner.update()


# ---------------------------------------------------------------- controls
class Switch(QAbstractButton):
    """macOS toggle: accent track, white knob that squishes while pressed."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(POINTER)
        self.setFixedSize(42, 24)
        self.anim = AnimDriver(self)
        self.knob = self.anim.spring(1.0 if checked else 0.0, 0.32, 0.68)
        self.squish = self.anim.spring(0.0, 0.22, 1.0)
        self.toggled.connect(lambda on: self.anim.to(self.knob, 1.0 if on else 0.0))

    def set_silent(self, on):
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self.knob.snap(1.0 if on else 0.0)
        self.update()

    def mousePressEvent(self, e):
        self.anim.to(self.squish, 1.0)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.anim.to(self.squish, 0.0)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if not self.isEnabled():
            p.setOpacity(0.4)
        w, h = self.width(), self.height()
        t = self.knob.value
        p.setPen(NO_PEN)
        p.setBrush(blend(T.c("track"), T.c("accent"), t))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        d = h - 4
        kw = d + 5 * self.squish.value
        x = clamp(2 + t * (w - 4 - kw), 1, w - 1 - kw)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRoundedRect(QRectF(x, 2.7, kw, d), d / 2, d / 2)
        p.setBrush(T.c("knob"))
        p.drawRoundedRect(QRectF(x, 2, kw, d), d / 2, d / 2)


class Slider(QWidget):
    valueChanged = Signal(float)
    R = 10

    def __init__(self, lo, hi, step, value, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)
        self._value = self._snap(value)
        self.setFixedHeight(26)
        self.setMinimumWidth(120)
        self.setCursor(POINTER)
        self.anim = AnimDriver(self)
        self.grow = self.anim.spring(0.0, 0.25, 0.75)
        self.pos = self.anim.spring(self._frac(), 0.2, 1.0)

    def _snap(self, v):
        v = round((float(v) - self.lo) / self.step) * self.step + self.lo
        return round(clamp(v, self.lo, self.hi), 4)

    def _frac(self):
        return (self._value - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0.0

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = self._snap(v)
        self.pos.snap(self._frac())
        self.update()

    def _from_x(self, x, animate):
        span = self.width() - 2 * self.R
        f = clamp((x - self.R) / span) if span > 0 else 0.0
        v = self._snap(self.lo + f * (self.hi - self.lo))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
        if animate:
            self.anim.to(self.pos, self._frac())
        else:
            self.pos.snap(self._frac())
            self.update()

    def mousePressEvent(self, e):
        self.anim.to(self.grow, 1.0)
        self._from_x(e.position().x(), True)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._from_x(e.position().x(), False)

    def mouseReleaseEvent(self, e):
        self.anim.to(self.grow, 0.0)

    def wheelEvent(self, e):
        e.ignore()  # let the page scroll instead

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if not self.isEnabled():
            p.setOpacity(0.4)
        w, cy = self.width(), self.height() / 2
        x = self.R + clamp(self.pos.value) * (w - 2 * self.R)
        p.setPen(NO_PEN)
        p.setBrush(T.c("track"))
        p.drawRoundedRect(QRectF(2, cy - 2, w - 4, 4), 2, 2)
        p.setBrush(T.c("accent"))
        p.drawRoundedRect(QRectF(2, cy - 2, max(4, x - 2), 4), 2, 2)
        r = 9 + 1.5 * self.grow.value
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(QPointF(x, cy + 1.2), r + 1, r + 1)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QPointF(x, cy + 0.5), r + 0.3, r + 0.3)
        p.setBrush(T.c("knob"))
        p.drawEllipse(QPointF(x, cy), r, r)


class Segmented(QWidget):
    """macOS segmented control with a sliding thumb."""
    changed = Signal(int)

    def __init__(self, items, index=0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setCursor(POINTER)
        self.anim = AnimDriver(self)
        self.thumb = self.anim.spring(0, 0.36, 0.8)
        self.items, self.index, self.seg_w = [], 0, 64
        self.set_items(items, index)

    def set_items(self, items, index):
        self.items = list(items) or ["1"]
        self.index = int(clamp(index, 0, len(self.items) - 1))
        self.thumb.snap(self.index)
        fm = QFontMetricsF(T.font(12, 600))
        self.seg_w = max(56, max(fm.horizontalAdvance(t) for t in self.items) + 30)
        self.setFixedWidth(int(self.seg_w * len(self.items) + 4))
        self.update()

    def mousePressEvent(self, e):
        i = int(clamp((e.position().x() - 2) // self.seg_w, 0, len(self.items) - 1))
        if i != self.index:
            self.index = i
            self.anim.to(self.thumb, i)
            self.changed.emit(i)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        h = self.height()
        p.setPen(NO_PEN)
        p.setBrush(T.c("control"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), 8, 8)
        tr = QRectF(2 + self.thumb.value * self.seg_w, 2, self.seg_w, h - 4)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawRoundedRect(tr.translated(0, 0.8), 6.5, 6.5)
        p.setBrush(T.c("thumb"))
        p.drawRoundedRect(tr, 6.5, 6.5)
        for i, text in enumerate(self.items):
            sel = abs(self.thumb.value - i) < 0.5
            p.setFont(T.font(12, 600 if sel else 500))
            p.setPen(T.c("text") if sel else T.c("secondary"))
            p.drawText(QRectF(2 + i * self.seg_w, 0, self.seg_w, h), Qt.AlignmentFlag.AlignCenter, text)


class PopupButton(QComboBox):
    """macOS pop-up button: rounded field with a blue up/down chevron box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(POINTER)
        self.setFixedHeight(28)
        self.setMinimumWidth(240)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(22)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.c("group_border"), 1))
        p.setBrush(T.c("control_hover") if self.underMouse() else T.c("control"))
        p.drawRoundedRect(r, 7, 7)
        box = QRectF(r.right() - 22, r.top() + 4, 18, r.height() - 8)
        p.setPen(NO_PEN)
        p.setBrush(T.c("accent"))
        p.drawRoundedRect(box, 4.5, 4.5)
        p.setPen(QPen(QColor("white"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy = box.center().x(), box.center().y()
        p.drawPolyline(QPolygonF([QPointF(cx - 3, cy - 1.6), QPointF(cx, cy - 4.4), QPointF(cx + 3, cy - 1.6)]))
        p.drawPolyline(QPolygonF([QPointF(cx - 3, cy + 1.6), QPointF(cx, cy + 4.4), QPointF(cx + 3, cy + 1.6)]))
        p.setFont(T.font(13))
        p.setPen(T.c("text"))
        tr = QRectF(r.left() + 10, r.top(), r.width() - 40, r.height())
        text = QFontMetricsF(p.font()).elidedText(self.currentText(), Qt.TextElideMode.ElideRight, tr.width())
        p.drawText(tr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class KeyCap(QAbstractButton):
    """Shows the hotkey as a physical key. Click to record a new one."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setCursor(POINTER)
        self.setFixedHeight(30)
        self.recording = False
        self.set_label(text)

    def set_label(self, text):
        self.setText(text)
        fm = QFontMetricsF(T.font(13, 600))
        self.setFixedWidth(int(max(46, fm.horizontalAdvance(text) + 30)))
        self.update()

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        w, h = self.width(), self.height()
        p.setPen(NO_PEN)
        p.setBrush(T.c("keycap_shadow"))
        p.drawRoundedRect(QRectF(0.5, 2.5, w - 1, h - 3), 7, 7)
        down = self.isDown()
        cap = QRectF(0.5, 0.5 + (1.5 if down else 0), w - 1, h - 3)
        p.setBrush(T.c("control_hover") if self.underMouse() else T.c("control"))
        if self.recording:
            p.setPen(QPen(T.c("accent"), 1.5))
        p.drawRoundedRect(cap, 7, 7)
        p.setFont(T.font(13, 600))
        p.setPen(T.c("accent") if self.recording else T.c("text"))
        p.drawText(cap, Qt.AlignmentFlag.AlignCenter, self.text())


def loudness(rms):
    """RMS volume -> 0..1 on a dB scale (-60 dB is empty, -6 dB is full), so quiet sound still shows."""
    if not rms or rms <= 0:
        return 0.0
    return clamp((20 * math.log10(rms) + 60) / 54)


class LevelMeter(QWidget):
    """Thin capsule meter with a tick at the trigger level. A soft fill behind shows how loud the sound is;
    the bar on top is the laugh (or cheer…) score, and turns green above the tick."""

    def __init__(self, width=90, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, 16)
        self.level = None
        self.volume = 0.0
        self.threshold = 0.3

    def set(self, level, threshold, volume=0.0):
        volume = round(volume or 0.0, 3)
        if level != self.level or threshold != self.threshold or volume != self.volume:
            self.level, self.threshold, self.volume = level, threshold, volume
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        w, cy = self.width(), self.height() / 2
        p.setPen(NO_PEN)
        p.setBrush(T.c("meter_bg"))
        p.drawRoundedRect(QRectF(0, cy - 2.5, w, 5), 2.5, 2.5)
        if self.volume > 0.01:
            p.setBrush(with_alpha(T.c("meter_idle"), 0.45))
            p.drawRoundedRect(QRectF(0, cy - 2.5, max(5, w * self.volume), 5), 2.5, 2.5)
        lvl = self.level
        if isinstance(lvl, float) and lvl > 0.004:
            p.setBrush(T.c("green") if lvl >= self.threshold else T.c("accent"))
            p.drawRoundedRect(QRectF(0, cy - 2.5, max(5, w * min(lvl, 1.0)), 5), 2.5, 2.5)
        x = w * self.threshold
        p.setBrush(with_alpha(T.c("text"), 0.55))
        p.drawRoundedRect(QRectF(x - 1, cy - 6, 2, 12), 1, 1)


class Disclosure(QAbstractButton):
    """Round chevron button that turns to point down when its section is open."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(POINTER)
        self.setFixedSize(28, 28)
        self.anim = AnimDriver(self)
        self.turn = self.anim.spring(0.0, 0.32, 0.82)
        self.toggled.connect(lambda on: self.anim.to(self.turn, 1.0 if on else 0.0))

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if not self.isEnabled():
            p.setOpacity(0.35)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.underMouse() and self.isEnabled():
            p.setPen(NO_PEN)
            p.setBrush(T.c("hover"))
            p.drawEllipse(r)
        p.translate(r.center())
        p.rotate(90 * self.turn.value)
        p.setPen(QPen(T.c("secondary"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(-2.2, -4.5), QPointF(2.6, 0), QPointF(-2.2, 4.5)]))


class Chip(QAbstractButton):
    """Toggle capsule: filled with the accent colour when on."""

    def __init__(self, text, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(text)
        self.setCursor(POINTER)
        fm = QFontMetricsF(T.font(12, 600))
        self.setFixedSize(int(fm.horizontalAdvance(text) + 28), 26)
        self.anim = AnimDriver(self)
        self.on = self.anim.spring(1.0 if checked else 0.0, 0.25, 0.9)
        self.press = self.anim.spring(0.0, 0.18, 1.0)
        self.toggled.connect(lambda on: self.anim.to(self.on, 1.0 if on else 0.0))

    def set_silent(self, on):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.on.snap(1.0 if on else 0.0)
        self.update()

    def mousePressEvent(self, e):
        self.anim.to(self.press, 1.0)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.anim.to(self.press, 0.0)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if not self.isEnabled():
            p.setOpacity(0.4)
        t = clamp(self.on.value)
        k = 1 - 0.05 * self.press.value
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = QRectF(r.center().x() - r.width() * k / 2, r.center().y() - r.height() * k / 2, r.width() * k,
                   r.height() * k)
        p.setPen(NO_PEN)
        p.setBrush(blend(T.c("control"), T.c("accent"), t))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(T.font(12, 600))
        p.setPen(blend(T.c("text"), QColor("white"), t))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class Collapsible(QWidget):
    """Holds a widget and springs open/closed to its natural height."""

    def __init__(self, content, parent=None):
        super().__init__(parent)
        self.content = content
        content.setParent(self)      # not in a layout: it keeps its full height and gets clipped while opening
        self.anim = AnimDriver(self, self._resize)
        self.open = self.anim.spring(0.0, 0.38, 0.92)
        self.setFixedHeight(0)
        self.hide()

    def set_open(self, on, animate=True):
        if on:
            self.show()
        if animate:
            self.anim.to(self.open, 1.0 if on else 0.0)
        else:
            self.open.snap(1.0 if on else 0.0)
            self._resize()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.content.setGeometry(0, 0, self.width(), self.content.sizeHint().height())

    def refresh(self):
        """Call after the content changes size."""
        self._resize()

    def _resize(self):
        f = clamp(self.open.value, 0.0, 1.2)
        self.setFixedHeight(max(0, round(self.content.sizeHint().height() * f)))
        self.content.setGeometry(0, 0, self.width(), self.content.sizeHint().height())
        if self.open.target == 0 and not self.anim.timer.isActive():
            self.hide()
        if self.parentWidget():
            self.parentWidget().update()


class StatusPill(QWidget):
    """Small tinted capsule with a dot and a word, like 'Saved' or "Didn't save"."""

    def __init__(self, text, color_key, parent=None):
        super().__init__(parent)
        self.text, self.color_key = text, color_key
        fm = QFontMetricsF(T.font(11, 600))
        self.setFixedSize(int(fm.horizontalAdvance(text) + 30), 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        c = T.c(self.color_key)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(NO_PEN)
        p.setBrush(with_alpha(c, 0.16))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(c)
        p.drawEllipse(QPointF(r.left() + 11, r.center().y()), 3.2, 3.2)
        p.setFont(T.font(11, 600))
        p.setPen(c if self.color_key != "secondary" else T.c("text"))
        p.drawText(r.adjusted(19, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)


class WarningBadge(QWidget):
    """Small orange circle with an exclamation mark, for rows that need attention."""

    def __init__(self, size=22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        u = r.width() / 22
        p.setPen(NO_PEN)
        p.setBrush(T.c("orange"))
        p.drawEllipse(r)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(r.center().x() - 1.2 * u, r.top() + 4.6 * u, 2.4 * u, 8.6 * u), 1.2 * u, 1.2 * u)
        p.drawEllipse(QPointF(r.center().x(), r.top() + 16.4 * u), 1.45 * u, 1.45 * u)


class PositionPicker(QWidget):
    """A little monitor; click where the notification should go."""
    changed = Signal(str)
    GRID = [["top-left", "top-center", "top-right"],
            ["middle-left", "center", "middle-right"],
            ["bottom-left", "bottom-center", "bottom-right"]]

    def __init__(self, position, parent=None):
        super().__init__(parent)
        self.setFixedSize(236, 156)
        self.setCursor(POINTER)
        self.setMouseTracking(True)
        self.position = position
        self.hover = None
        r, c = self._rc(position)
        self.anim = AnimDriver(self)
        self.sx = self.anim.spring(c, 0.42, 0.76)
        self.sy = self.anim.spring(r, 0.42, 0.76)

    def _rc(self, pos):
        for r, row in enumerate(self.GRID):
            if pos in row:
                return r, row.index(pos)
        return 2, 1

    def screen(self):
        return QRectF(18, 12, 200, 114)

    def spot(self, r, c):
        s = self.screen()
        return QPointF(s.left() + 34 + c * (s.width() - 68) / 2, s.top() + 16 + r * (s.height() - 32) / 2)

    def set_position(self, pos):
        self.position = pos
        r, c = self._rc(pos)
        self.anim.to(self.sx, c)
        self.anim.to(self.sy, r)

    def _cell(self, pt):
        best = min(((r, c) for r in range(3) for c in range(3)),
                   key=lambda rc: (self.spot(*rc) - pt).manhattanLength())
        return best

    def mouseMoveEvent(self, e):
        cell = self._cell(e.position())
        if cell != self.hover:
            self.hover = cell
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        r, c = self._cell(e.position())
        pos = self.GRID[r][c]
        if pos != self.position:
            self.set_position(pos)
            self.changed.emit(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        s = self.screen()
        bezel = s.adjusted(-6, -6, 6, 6)
        # stand
        p.setPen(NO_PEN)
        p.setBrush(T.c("tertiary"))
        cx = bezel.center().x()
        stand = QPainterPath()
        stand.moveTo(cx - 18, bezel.bottom())
        stand.lineTo(cx + 18, bezel.bottom())
        stand.lineTo(cx + 24, bezel.bottom() + 14)
        stand.lineTo(cx - 24, bezel.bottom() + 14)
        stand.closeSubpath()
        p.drawPath(stand)
        p.drawRoundedRect(QRectF(cx - 34, bezel.bottom() + 13, 68, 4), 2, 2)
        # bezel + wallpaper
        p.setBrush(T.c("bezel"))
        p.drawRoundedRect(bezel, 11, 11)
        g = QLinearGradient(s.topLeft(), s.bottomRight())
        g.setColorAt(0, T.c("wall1"))
        g.setColorAt(1, T.c("wall2"))
        p.setBrush(g)
        p.drawRoundedRect(s, 5, 5)
        # targets
        for r in range(3):
            for c in range(3):
                hot = self.hover == (r, c)
                p.setBrush(QColor(255, 255, 255, 210 if hot else 110))
                p.drawEllipse(self.spot(r, c), 3.2 if hot else 2.6, 3.2 if hot else 2.6)
        # mini notification
        pt = self.spot(self.sy.value, self.sx.value)
        pill = QRectF(pt.x() - 26, pt.y() - 7.5, 52, 15)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(pill.translated(0, 1.5), 7.5, 7.5)
        p.setBrush(QColor(0, 0, 0, 235))
        p.drawRoundedRect(pill, 7.5, 7.5)
        p.setBrush(QColor("#ffd60a"))
        p.drawEllipse(QPointF(pill.left() + 7.5, pill.center().y()), 4.2, 4.2)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawRoundedRect(QRectF(pill.left() + 15, pill.center().y() - 3.2, 22, 2.6), 1.3, 1.3)
        p.setBrush(QColor(255, 255, 255, 90))
        p.drawRoundedRect(QRectF(pill.left() + 15, pill.center().y() + 0.9, 15, 2.2), 1.1, 1.1)
        p.setBrush(QColor("#0a84ff"))
        p.drawRoundedRect(QRectF(pill.right() - 12, pill.center().y() - 3, 8, 6), 3, 3)


# ---------------------------------------------------------------- layout pieces
class Group(QWidget):
    """Inset-grouped list section: rounded card with hairline separators between rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.rows = []

    def add(self, row):
        self.lay.addWidget(row)
        self.rows.append(row)
        return row

    def clear(self):
        for r in self.rows:
            self.lay.removeWidget(r)
            r.hide()
            r.deleteLater()
        self.rows = []

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.c("group_border"), 1))
        p.setBrush(T.c("group"))
        p.drawRoundedRect(r, 11, 11)
        visible = [w for w in self.rows if not w.isHidden()]
        p.setPen(QPen(T.c("separator"), 1))
        for w in visible[:-1]:
            y = w.geometry().y() + w.geometry().height() - 0.5
            p.drawLine(QPointF(16, y), QPointF(self.width() - 1, y))


class Row(QWidget):
    def __init__(self, title, subtitle=None, *right):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 9, 14, 9)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        self.title = QLabel(title)
        col.addWidget(self.title)
        self.sub = QLabel(subtitle or "")
        self.sub.setObjectName("secondary")
        self.sub.setWordWrap(True)
        self.sub.setVisible(bool(subtitle))
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        for w in right:
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumHeight(46)

    def set_sub(self, text):
        text = text or ""
        if self.sub.text() != text:
            self.sub.setText(text)
            self.sub.setVisible(bool(text))


def draw_tile(p, rect, color, glyph, letter):
    """Coloured rounded-square icon, like the ones in System Settings."""
    c = QColor(color)
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0, c.lighter(118))
    g.setColorAt(1, c)
    p.setPen(NO_PEN)
    p.setBrush(g)
    p.drawRoundedRect(rect, rect.width() * 0.26, rect.width() * 0.26)
    p.setPen(QColor("white"))
    if T.glyphs:
        f = T.font(rect.height() * 0.56)
        f.setFamily(T.glyphs)
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    else:
        p.setFont(T.font(rect.height() * 0.55, 700))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)


class NavItem(QWidget):
    clicked = Signal()

    def __init__(self, text, glyph, color, parent=None):
        super().__init__(parent)
        self.text, self.glyph, self.color = text, glyph, color
        self.selected = self.hover = False
        self.setFixedHeight(34)
        self.setCursor(POINTER)

    def set_selected(self, on):
        self.selected = on
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(self.rect()).adjusted(10, 1, -10, -1)
        p.setPen(NO_PEN)
        if self.selected:
            p.setBrush(T.c("accent"))
            p.drawRoundedRect(r, 8, 8)
        elif self.hover:
            p.setBrush(T.c("hover"))
            p.drawRoundedRect(r, 8, 8)
        tile = QRectF(r.left() + 6, r.center().y() - 11, 22, 22)
        draw_tile(p, tile, self.color, self.glyph, self.text[0])
        p.setFont(T.font(13, 500))
        p.setPen(QColor("white") if self.selected else T.c("text"))
        p.drawText(QRectF(tile.right() + 10, r.top(), r.width(), r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
