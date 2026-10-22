"""The popup: a Dynamic Island-style black capsule that springs open, counts down,
and morphs into a 'Clip saved' confirmation."""

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import winutil
from .config import split_position
from .engine import SOUNDS
from .theme import T
from .widgets import Spring, clamp

M = 34                      # transparent margin around the capsule for the shadow
W_FULL, H_FULL = 400, 76
W_SAVED, H_SAVED = 236, 60
SEED = 40                   # size it grows from / shrinks back to
EDGE_GAP = 18               # distance from the screen edge

BLUE, BLUE_HOVER = QColor("#0a84ff"), QColor("#3d9bff")
SUB = QColor("#98989d")
SURPRISED = {"shouting", "screaming", "gasp"}   # these get the "O" mouth instead of the laughing one


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


class Toast(QWidget):
    clip_requested = Signal()
    close_requested = Signal()
    finished = Signal()

    def __init__(self, screen, position, mode, source, sound, hotkey, seconds):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)

        # "ask" | "pending" | "saving" | "saved" | "failed" | "nomedal"
        self.mode, self.prev = mode, None
        self.saved_sub = "Sent to Medal"
        self.bookmark = False         # Medal is recording: this laugh will add a bookmark, not a clip
        self.source, self.hotkey = source, hotkey
        self.sound_name, self.still = SOUNDS.get(sound, SOUNDS["laughter"])[:2]
        self.surprised = sound in SURPRISED
        self.total = self.left = max(float(seconds), 0.1)
        self.laughing = False
        self.hover = self.hover_ring = self.closing = False
        self.t = 0.0
        self.close_at = self.end_at = None
        self.pill = self.ring_rect = self.btn_rect = QRectF()

        # placement
        self.av, self.ah = split_position(position)
        self.WW, self.WH = W_FULL + 2 * M, H_FULL + 2 * M
        self.resize(self.WW, self.WH)
        g = screen.availableGeometry()
        x = {"left": g.left() + EDGE_GAP - M, "center": g.center().x() - self.WW // 2,
             "right": g.right() + 1 - EDGE_GAP + M - self.WW}[self.ah]
        y = {"top": g.top() + EDGE_GAP - M, "middle": g.center().y() - self.WH // 2,
             "bottom": g.bottom() + 1 - EDGE_GAP + M - self.WH}[self.av]
        self.move(x, y)
        self.dy = -1 if self.av == "top" else 1 if self.av == "bottom" else 0
        self.dx = 0 if self.dy else (-1 if self.ah == "left" else 1 if self.ah == "right" else 0)

        # springs (response, damping) — tuned to feel like iOS
        self.w = Spring(SEED, 0.56, 0.72)
        self.h = Spring(SEED, 0.5, 0.8)
        self.alpha = Spring(0.0, 0.2, 1.0)
        self.slide = Spring(1.0, 0.5, 0.82)
        self.swap = Spring(1.0, 0.3, 1.0)
        self.scale = Spring(1.0, 0.32, 0.6)
        self.press = Spring(0.0, 0.18, 1.0)
        self.check = Spring(0.0, 0.5, 0.86)
        self.wave = Spring(0.0, 0.15, 1.0)
        self.springs = [self.w, self.h, self.alpha, self.slide, self.swap, self.scale, self.press, self.check, self.wave]

        tw, th = self.size_for(mode)
        self.w.target, self.h.target = tw, th
        self.alpha.target, self.slide.target = 1.0, 0.0
        if mode == "saved":
            self.check.target = 1.0
            self.close_at = 1.6

        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    @staticmethod
    def size_for(mode):
        return (W_SAVED, H_SAVED) if mode in ("saved", "saving", "bookmarked") else (W_FULL, H_FULL)

    def showEvent(self, e):
        super().showEvent(e)
        winutil.no_activate(int(self.winId()))

    # ----- driven by the app -----
    def _switch(self, mode):
        """Morph into another mode: the old content fades out as the new one fades in."""
        self.prev, self.mode = self.mode, mode
        self.swap.snap(0.0)
        self.swap.target = 1.0
        self.w.target, self.h.target = self.size_for(mode)
        self.scale.target = 1.0
        self.press.target = 0.0
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_saving(self):
        """The hotkey went to Medal; waiting for the clip file to appear."""
        if self.mode in ("saving", "saved") or self.closing:
            return
        self._switch("saving")
        self.close_at = None

    def set_saved(self, game=None):
        if self.mode == "saved" or self.closing:
            return
        self._switch("saved")
        self.saved_sub = f"In Medal  ·  {game}" if game else "Sent to Medal"
        self.check.snap(0.0)
        self.check.target = 1.0
        self.close_at = self.t + 1.5

    def set_bookmarked(self):
        """Medal is recording, so we added a bookmark to it instead of saving a clip."""
        if self.mode in ("saved", "bookmarked") or self.closing:
            return
        self._switch("bookmarked")
        self.check.snap(0.0)
        self.check.target = 1.0
        self.close_at = self.t + 1.5

    def set_failed(self, seconds):
        """Medal didn't make a clip file."""
        if self.mode == "failed" or self.closing:
            return
        self._switch("failed")
        self.total = self.left = max(float(seconds), 0.1)

    def set_nomedal(self, seconds):
        """Medal closed before we could clip: say so instead of pretending to save."""
        if self.mode in ("saved", "nomedal") or self.closing:
            return
        self._switch("nomedal")
        self.total = self.left = max(float(seconds), 0.1)

    def set_pending(self, remaining, laughing, total, level):
        self.left = max(0.0, remaining)
        self.total = max(float(total), 0.1)
        self.laughing = laughing
        self.wave.target = clamp(max(level, 0.35)) if laughing else 0.0

    def dismiss(self):
        if self.closing:
            return
        self.closing = True
        self.w.configure(0.36, 0.95)
        self.h.configure(0.36, 0.95)
        self.alpha.configure(0.3, 1.0)
        self.w.target = self.h.target = SEED
        self.alpha.target = 0.0
        self.slide.target = 0.6
        self.end_at = self.t + 0.7

    # ----- animation loop -----
    def tick(self):
        dt = clamp(self.clock.restart() / 1000.0, 0.001, 0.05)
        self.t += dt
        for s in self.springs:
            s.step(dt)
        if not self.closing and not self.hover:
            if self.mode in ("ask", "nomedal", "failed"):
                self.left -= dt
                if self.left <= 0:
                    self.dismiss()
            elif self.mode == "pending" and not self.laughing:
                self.left = max(0.0, self.left - dt)   # smooth between the app's updates
        if self.close_at is not None and self.t >= self.close_at:
            self.close_at = None
            self.dismiss()
        if self.closing and (self.alpha.value < 0.01 or self.t >= self.end_at):
            self.timer.stop()
            self.finished.emit()
            self.close()
            return
        self.update()

    # ----- input -----
    def _over(self, pos):
        return self.pill.contains(pos)

    def _on_ring(self, pos):
        return (self.mode in ("ask", "pending", "nomedal", "failed")
                and self.ring_rect.adjusted(-6, -6, 6, 6).contains(pos))

    def mouseMoveEvent(self, e):
        pos = e.position()
        over = self._over(pos) and not self.closing
        can_clip = self.mode in ("ask", "pending")
        if over != self.hover:
            self.hover = over
            self.scale.target = 1.025 if over and can_clip else 1.0
        self.hover_ring = self._on_ring(pos)
        clickable = over and (can_clip or self.hover_ring)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e):
        self.hover = self.hover_ring = False
        self.scale.target = 1.0
        self.press.target = 0.0

    def mousePressEvent(self, e):
        pos = e.position()
        if self.closing or self.mode == "saved" or not self._over(pos):
            return
        if self._on_ring(pos):
            self.close_requested.emit()
            return
        if self.mode in ("ask", "pending"):
            self.press.target = 1.0

    def mouseReleaseEvent(self, e):
        if self.press.target > 0:
            self.press.target = 0.0
            pos = e.position()
            if self._over(pos) and not self._on_ring(pos) and self.mode in ("ask", "pending"):
                self.clip_requested.emit()

    # ----- painting -----
    def paintEvent(self, _):
        a = clamp(self.alpha.value)
        if a <= 0.003:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        k = self.scale.value * (1 - 0.035 * self.press.value)
        w, h = max(self.w.value, 8) * k, max(self.h.value, 8) * k
        x = {"left": M, "center": (self.WW - w) / 2, "right": self.WW - M - w}[self.ah]
        y = {"top": M, "middle": (self.WH - h) / 2, "bottom": self.WH - M - h}[self.av]
        off = self.slide.value * 18
        r = QRectF(x + self.dx * off, y + self.dy * off, w, h)
        self.pill = r
        rad = min(h / 2, 30)

        p.setOpacity(a)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):                      # soft ambient shadow
            sp = 1.5 + i * 3.2
            p.setBrush(QColor(0, 0, 0, int(26 * (1 - i / 8) ** 2) + 2))
            p.drawRoundedRect(r.adjusted(-sp, -sp + 5, sp, sp + 7), rad + sp, rad + sp)

        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(0, 0, 0, 250))
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawPath(path)
        p.setClipPath(path)

        tw, _ = self.size_for(self.mode)
        reveal = ease_out((self.w.value - SEED - (tw - SEED) * 0.45) / ((tw - SEED) * 0.55))
        center = r.center()
        sw = clamp(self.swap.value)
        if self.prev:
            # old content fades out quickly and drifts up; new content fades in after it, rising into place
            out_t = clamp(sw * 2.4)
            in_t = ease_out((sw - 0.3) / 0.7)
            if out_t < 1:
                self.draw_content(p, self.prev, center + QPointF(0, -5 * out_t), a * (1 - out_t), live=False)
            self.draw_content(p, self.mode, center + QPointF(0, 5 * (1 - in_t)), a * reveal * in_t, live=True)
        else:
            self.draw_content(p, self.mode, center, a * reveal, live=True)

    def draw_content(self, p, mode, center, opacity, live):
        if opacity <= 0.01:
            return
        p.save()
        p.setOpacity(opacity)
        W, H = self.size_for(mode)
        rr = QRectF(center.x() - W / 2, center.y() - H / 2, W, H)
        cy = rr.center().y()
        icon = 34 if mode in ("saved", "saving", "bookmarked") else 44
        pad = (H - icon) / 2
        ic = QRectF(rr.left() + pad, cy - icon / 2, icon, icon)
        tx = ic.right() + 12

        if mode == "bookmarked":
            pop = 0.82 + 0.18 * clamp(self.check.value * 1.4)
            d = icon * pop
            draw_bookmark_icon(p, QRectF(ic.center().x() - d / 2, ic.center().y() - d / 2, d, d), self.check.value)
            self.draw_texts(p, tx, cy, rr.right() - pad - tx, "Bookmarked", "Added to your recording", None)
        elif mode == "saved":
            pop = 0.82 + 0.18 * clamp(self.check.value * 1.4)
            d = icon * pop
            draw_check_icon(p, QRectF(ic.center().x() - d / 2, ic.center().y() - d / 2, d, d), self.check.value)
            self.draw_texts(p, tx, cy, rr.right() - pad - tx, "Clip saved", self.saved_sub, None)
        elif mode == "saving":
            draw_spinner(p, ic, self.t)
            self.draw_texts(p, tx, cy, rr.right() - pad - tx, "Saving clip", "Waiting for Medal…", None)
        elif mode in ("nomedal", "failed"):
            draw_warning_icon(p, ic)
            ring_d = 30
            ring = QRectF(rr.right() - 22 - ring_d, cy - ring_d / 2, ring_d, ring_d)
            if live:
                self.ring_rect = ring
            if mode == "nomedal":
                title, sub = f"{self.sound_name} detected", "Medal isn't running, so no clip was saved"
            else:
                title, sub = "Clip didn't save", "Medal didn't make a clip. Is the hotkey right?"
            self.draw_texts(p, tx, cy, ring.left() - 14 - tx, title, sub, None)
            draw_ring(p, ring, clamp(self.left / self.total), self.hover_ring)
        else:
            draw_laugh_icon(p, ic, self.surprised)
            ring_d = 30
            ring = QRectF(rr.right() - 22 - ring_d, cy - ring_d / 2, ring_d, ring_d)
            btn = QRectF(ring.left() - 10 - 64, cy - 17, 64, 34)
            if live:
                self.ring_rect, self.btn_rect = ring, btn
            if mode == "ask":
                title = f"{self.sound_name} detected"
                sub = f"{self.source}  ·  " + ("Medal is recording" if self.bookmark else f"{self.hotkey} to clip")
                frac = self.left / self.total
            elif self.laughing:
                title, sub = ("Bookmarking" if self.bookmark else "Saving clip"), f"{self.still}  ·  {self.source}"
                frac = 1.0
            else:
                title = "Bookmarking" if self.bookmark else "Saving clip"
                sub = f"In {max(0, math.ceil(self.left))}s  ·  {self.source}"
                frac = self.left / self.total
            title_w = self.draw_texts(p, tx, cy, btn.left() - 12 - tx, title, sub, None)
            if mode == "pending" and self.wave.value > 0.02:
                self.draw_wave(p, tx + title_w + 9, cy - 9)
            # button
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(BLUE_HOVER if (self.hover and not self.hover_ring) else BLUE)
            p.drawRoundedRect(btn, 17, 17)
            p.setPen(QColor("white"))
            p.setFont(T.font(14, 600, display=True))
            p.drawText(btn, Qt.AlignmentFlag.AlignCenter, "Now" if mode == "pending" else "Mark" if self.bookmark else "Clip")
            draw_ring(p, ring, clamp(frac), self.hover_ring)
        p.restore()

    def draw_texts(self, p, x, cy, maxw, title, sub, _):
        tf = T.font(15, 600, display=True)
        sf = T.font(13)
        tfm, sfm = QFontMetricsF(tf), QFontMetricsF(sf)
        title = tfm.elidedText(title, Qt.TextElideMode.ElideRight, maxw)
        sub = sfm.elidedText(sub, Qt.TextElideMode.ElideRight, maxw)
        p.setFont(tf)
        p.setPen(QColor("#f5f5f7"))
        p.drawText(QPointF(x, cy - 3.5), title)
        p.setFont(sf)
        p.setPen(SUB)
        p.drawText(QPointF(x, cy + 15), sub)
        return tfm.horizontalAdvance(title)

    def draw_wave(self, p, x, cy):
        """Little live audio bars next to the title while the laughing continues."""
        lv = self.wave.value
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffd60a"))
        for i in range(4):
            amp = 0.45 + 0.55 * abs(math.sin(self.t * 7.5 + i * 1.9))
            bh = 3 + 11 * lv * amp
            p.drawRoundedRect(QRectF(x + i * 5.5, cy - bh / 2, 3, bh), 1.5, 1.5)


# ---------------------------------------------------------------- icons
def draw_laugh_icon(p, r, surprised=False):
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor("#ffd60a"))
    g.setColorAt(1, QColor("#ff9f0a"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(r)
    u = r.width() / 40

    def pt(x, y):
        return QPointF(r.left() + x * u, r.top() + y * u)

    ink = QColor(92, 46, 0)
    p.setPen(QPen(ink, 2.7 * u, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if surprised:
        # raised brows, round eyes and an "O" mouth
        for ex in (13.5, 26.5):
            brow = QPainterPath(pt(ex - 3.4, 11.5))
            brow.quadTo(pt(ex, 8.5), pt(ex + 3.4, 11.5))
            p.drawPath(brow)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        for ex in (13.5, 26.5):
            p.drawEllipse(pt(ex, 17), 2.4 * u, 2.9 * u)
        p.drawEllipse(pt(20, 27.5), 5 * u, 6 * u)
        p.setBrush(QColor("#ff6b6b"))
        p.drawEllipse(pt(20, 30.5), 3.2 * u, 2.2 * u)
        return
    for ex in (13.5, 26.5):
        eye = QPainterPath(pt(ex - 3.6, 17.5))
        eye.quadTo(pt(ex, 11.5), pt(ex + 3.6, 17.5))
        p.drawPath(eye)
    mouth = QPainterPath(pt(11, 22))
    mouth.lineTo(pt(29, 22))
    mouth.cubicTo(pt(29, 33.5), pt(11, 33.5), pt(11, 22))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ink)
    p.drawPath(mouth)
    p.save()
    p.setClipPath(mouth, Qt.ClipOperation.IntersectClip)
    p.setBrush(QColor("#ff6b6b"))
    p.drawEllipse(pt(20, 32), 6.5 * u, 4.5 * u)
    p.restore()


def draw_warning_icon(p, r):
    """Orange circle with a white exclamation mark."""
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor("#ffb340"))
    g.setColorAt(1, QColor("#ff8a00"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(r)
    u = r.width() / 40
    c = r.center()
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(c.x() - 2 * u, r.top() + 9 * u, 4 * u, 15 * u), 2 * u, 2 * u)
    p.drawEllipse(QPointF(c.x(), r.top() + 29.5 * u), 2.4 * u, 2.4 * u)


def draw_bookmark_icon(p, r, prog):
    """Blue circle with a white bookmark ribbon that drops in."""
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor("#5e9bff"))
    g.setColorAt(1, QColor("#2f6bff"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(r)
    u = r.width() / 40
    drop = (1 - clamp(prog)) * 4 * u
    x0, y0, w, h = r.left() + 13.5 * u, r.top() + 10 * u - drop, 13 * u, 20 * u
    ribbon = QPainterPath(QPointF(x0, y0 + 2 * u))
    ribbon.quadTo(QPointF(x0, y0), QPointF(x0 + 2 * u, y0))
    ribbon.lineTo(QPointF(x0 + w - 2 * u, y0))
    ribbon.quadTo(QPointF(x0 + w, y0), QPointF(x0 + w, y0 + 2 * u))
    ribbon.lineTo(QPointF(x0 + w, y0 + h))
    ribbon.lineTo(QPointF(x0 + w / 2, y0 + h - 5 * u))
    ribbon.lineTo(QPointF(x0, y0 + h))
    ribbon.closeSubpath()
    p.save()
    p.setOpacity(p.opacity() * clamp(0.3 + prog))
    p.setBrush(QColor("white"))
    p.drawPath(ribbon)
    p.restore()


def draw_spinner(p, r, t):
    """Blue circle with a white arc going round, while we wait for Medal."""
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor("#3d9bff"))
    g.setColorAt(1, QColor("#0a6fe0"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(r)
    inner = r.adjusted(r.width() * 0.26, r.width() * 0.26, -r.width() * 0.26, -r.width() * 0.26)
    w = r.width() * 0.085
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 70), w))
    p.drawEllipse(inner)
    p.setPen(QPen(QColor("white"), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    sweep = 70 + 40 * math.sin(t * 3.1)          # the arc breathes a little as it spins
    p.drawArc(inner, int(-t * 420 * 16) % (360 * 16), int(sweep * 16))


def draw_check_icon(p, r, prog):
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor("#3ddc6a"))
    g.setColorAt(1, QColor("#22a447"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(r)
    u = r.width() / 40
    pts = [QPointF(r.left() + 12 * u, r.top() + 20.5 * u), QPointF(r.left() + 17.5 * u, r.top() + 26 * u),
           QPointF(r.left() + 28.5 * u, r.top() + 14.5 * u)]
    segs = [(pts[0], pts[1]), (pts[1], pts[2])]
    lens = [math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in segs]
    left = clamp(prog) * sum(lens)
    p.setPen(QPen(QColor("white"), 3.4 * u, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath(pts[0])
    for (a, b), ln in zip(segs, lens):
        if left <= 0:
            break
        f = min(1.0, left / ln)
        path.lineTo(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
        left -= ln
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def draw_ring(p, r, frac, hover):
    """Countdown ring that drains clockwise, with a close × in the middle."""
    inner = r.adjusted(2, 2, -2, -2)
    p.setBrush(QColor(255, 255, 255, 30 if hover else 0))
    p.setPen(QPen(QColor(255, 255, 255, 38), 2.6))
    p.drawEllipse(inner)
    if frac > 0.002:
        p.setPen(QPen(QColor(255, 255, 255, 225), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(inner, 90 * 16, int(-360 * 16 * frac))
    c, s = r.center(), r.width() * 0.16
    p.setPen(QPen(QColor(255, 255, 255, 255 if hover else 150), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
    p.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))
