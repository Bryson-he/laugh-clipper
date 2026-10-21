"""Laugh -> popup -> clip, cooldown, and never pretending to save when Medal is closed."""

from conftest import SENT
from laughclipper.config import S

GAME = ("out", "Game (Elgato Virtual Audio)")


def flush(qapp):
    """The hotkey is sent 60 ms after the click; let that timer run."""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(120, loop.quit)
    loop.exec()


def test_ask_mode_then_clip(ctrl, qapp):
    ctrl.on_laugh(GAME, "cheering")
    assert ctrl.toasts == [("ask", "cheering")] and ctrl.pending is None
    ctrl.clip()
    flush(qapp)
    assert SENT == ["f8"]


def test_cooldown(ctrl, pc):
    ctrl.on_laugh(GAME, "laughter")
    pc.wait(S["cooldown"] - 1)
    ctrl.on_laugh(GAME, "laughter")
    assert len(ctrl.toasts) == 1
    pc.wait(2)
    ctrl.on_laugh(GAME, "laughter")
    assert len(ctrl.toasts) == 2


def test_auto_save_waits_for_the_laughing_to_stop(ctrl, pc, qapp):
    S["auto_clip"], S["clip_delay"], S["max_wait"] = True, 5, 30
    ctrl.on_laugh(GAME, "laughter")
    assert ctrl.toasts == [("pending", "laughter")] and ctrl.pending
    ctrl.engine.threads[GAME] = object()
    for _ in range(4):                     # still laughing: the countdown keeps restarting
        pc.wait(3)
        ctrl.engine.activity[GAME] = pc.now
        ctrl.on_tick()
    assert ctrl.pending and SENT == []
    pc.wait(5.1)
    ctrl.on_tick()
    flush(qapp)
    assert ctrl.pending is None and SENT == ["f8"]


def test_medal_closed_means_no_hotkey(ctrl, pc, qapp):
    pc.medal = False
    ctrl.on_laugh(GAME, "laughter")
    assert ctrl.toasts == [("nomedal", "laughter")]
    ctrl.clip()
    flush(qapp)
    assert SENT == []


def test_medal_closing_mid_countdown(ctrl, pc, qapp):
    S["auto_clip"], S["clip_delay"] = True, 5
    ctrl.on_laugh(GAME, "laughter")
    pc.medal = False
    pc.wait(6)
    ctrl.on_tick()
    flush(qapp)
    assert ctrl.pending is None and SENT == []


def test_medal_check_can_be_turned_off(ctrl, pc):
    pc.medal = False
    ctrl.set("medal_check", False)
    ctrl.on_laugh(GAME, "laughter")
    assert ctrl.toasts == [("ask", "laughter")]
