"""2.2: pages, search, keyboard and screen-reader support, History groups, and the animation plumbing."""

import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from laughclipper import clips, widgets, winutil
from laughclipper.config import S
from laughclipper.settings_window import PAGES, SettingsWindow
from laughclipper.widgets import MOTION, AnimDriver, PositionPicker, Segmented, Slider, StatsBar, Switch

TITLES = [t for t, _, _ in PAGES]
VOICE = ("out", "Voice Chat (Elgato Virtual Audio)")


def press(k):
    return QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)


def key(widget, k):
    widget.keyPressEvent(press(k))


@pytest.fixture
def win(ctrl):
    w = SettingsWindow(ctrl)
    yield w
    w.close()
    w.deleteLater()


# ---------------------------------------------------------------- pages
def test_pages_and_where_things_moved(win):
    assert TITLES == ["General", "Games", "Audio", "Detection", "Clipping", "Shortcuts", "Notification", "History"]
    assert win.stack.count() == len(PAGES)

    def page_of(widget):
        return next(t for i, t in enumerate(TITLES) if win.stack.widget(i).isAncestorOf(widget))
    assert page_of(win.game_row) == "Games" and page_of(win.games_group) == "Games"
    assert page_of(win.pause_row) == page_of(win.voice_row) == page_of(win.pad_row) == "Shortcuts"
    assert page_of(win.hotkey_row) == "Clipping" and page_of(win.status_row) == "General"


def test_sidebar_keyboard_changes_page(win):
    assert win.stack.currentIndex() == 0
    key(win.nav, Qt.Key.Key_Down)
    key(win.nav, Qt.Key.Key_Down)
    assert win.stack.currentIndex() == 2 and win.nav.index == 2
    key(win.nav, Qt.Key.Key_End)
    assert TITLES[win.stack.currentIndex()] == "History"
    key(win.nav, Qt.Key.Key_Down)                              # already at the end
    assert TITLES[win.stack.currentIndex()] == "History"
    key(win.nav, Qt.Key.Key_Home)
    assert win.stack.currentIndex() == 0


def test_highlight_slides_between_items(win):
    win.select(3)
    assert win.nav.pos.target == 3 and win.nav.index == 3
    win.nav.pos.snap(3)
    win.nav._sync()
    assert [round(i.on) for i in win.nav.items] == [0, 0, 0, 1, 0, 0, 0, 0]


# ---------------------------------------------------------------- search
def test_search_finds_a_setting_and_opens_its_page(win):
    win.search.setText("pause hot")
    assert not win.results.isHidden() and win.nav.isHidden()
    assert win.results.entries[0] == ("Pause hotkey", "Shortcuts")
    win.open_result(0)
    assert TITLES[win.stack.currentIndex()] == "Shortcuts"
    assert win.search.text() == "" and win.results.isHidden() and not win.nav.isHidden()


def test_search_ranks_names_first_and_handles_nothing(win):
    win.search.setText("clip")
    names = [t for t, _ in win.results.entries]
    assert names[0].lower().startswith("clip") and len(names) <= 8
    win.search.setText("history")
    assert ("History", "Page") in win.results.entries
    win.search.setText("zzzz")
    assert win.results.entries == [] and win.found == []
    win.open_result(0)                                         # nothing to open: no crash


def test_search_keys(win):
    win.search.setText("hotkey")
    win.eventFilter(win.search, press(Qt.Key.Key_Down))
    assert win.results.current == 1
    win.eventFilter(win.search, press(Qt.Key.Key_Escape))
    assert win.search.text() == ""


# ---------------------------------------------------------------- keyboard + screen readers
def test_buttons_and_controls_only_focus_from_the_keyboard(win):
    for b in win.findChildren(QPushButton):
        assert b.focusPolicy() == Qt.FocusPolicy.TabFocus, b.text()
    for cls in (Switch, Slider, Segmented, PositionPicker):
        for c in win.findChildren(cls):
            assert c.focusPolicy() == Qt.FocusPolicy.TabFocus


def test_every_switch_and_slider_has_a_name(win):
    unnamed = [type(c).__name__ for cls in (Switch, Slider, Segmented, PositionPicker)
               for c in win.findChildren(cls) if not c.accessibleName()]
    assert unnamed == []
    assert win.switches["auto_clip"].accessibleName() == "Save clips automatically"
    assert win.copy_diag.accessibleDescription() == "Something not working?"


def test_slider_keys(qapp):
    s = Slider(0, 10, 1, 5)
    got = []
    s.valueChanged.connect(got.append)
    key(s, Qt.Key.Key_Right)
    key(s, Qt.Key.Key_Left)
    key(s, Qt.Key.Key_Left)
    key(s, Qt.Key.Key_End)
    key(s, Qt.Key.Key_End)                                     # already there: no second signal
    key(s, Qt.Key.Key_Home)
    assert got == [6, 5, 4, 10, 0] and s.value() == 0
    key(s, Qt.Key.Key_PageUp)
    assert s.value() == 10


def test_segmented_and_position_keys(qapp):
    seg = Segmented(["a", "b", "c"], 0)
    got = []
    seg.changed.connect(got.append)
    key(seg, Qt.Key.Key_Right)
    key(seg, Qt.Key.Key_Right)
    key(seg, Qt.Key.Key_Right)
    key(seg, Qt.Key.Key_Left)
    assert got == [1, 2, 1]
    pp = PositionPicker("bottom-center")
    moved = []
    pp.changed.connect(moved.append)
    key(pp, Qt.Key.Key_Up)
    key(pp, Qt.Key.Key_Left)
    key(pp, Qt.Key.Key_Down)
    key(pp, Qt.Key.Key_Down)
    assert moved == ["center", "middle-left", "bottom-left"]


# ---------------------------------------------------------------- motion
class Screen:
    def __init__(self, hz):
        self.hz = hz

    def refreshRate(self):
        return self.hz


@pytest.mark.parametrize("hz, ms", [(60, 16), (144, 6), (165, 6), (240, 4), (360, 4), (0, 16), (59.94, 16)])
def test_frames_follow_the_monitor(hz, ms):
    assert MOTION.for_screen(Screen(hz)) == ms
    assert MOTION.for_screen(None) == 16


def test_animations_off_in_windows_means_no_motion(qapp):
    sw = Switch(False)
    sw.setChecked(True)
    assert sw.knob.value < 1 and sw.anim.timer.isActive()      # normally it travels there
    sw.anim.timer.stop()
    MOTION.reduced = True
    sw2 = Switch(False)
    sw2.setChecked(True)
    assert sw2.knob.value == 1 and not sw2.anim.timer.isActive()


def test_reduced_motion_follows_the_windows_setting(monkeypatch):
    monkeypatch.setattr(winutil, "animations_enabled", lambda: False)
    MOTION.checked = -1e9
    assert MOTION.is_reduced() is True
    monkeypatch.setattr(winutil, "animations_enabled", lambda: True)
    assert MOTION.is_reduced() is True                         # asked at most every few seconds
    MOTION.checked = -1e9
    assert MOTION.is_reduced() is False


def test_driver_runs_at_the_screens_rate(qapp, monkeypatch):
    monkeypatch.setattr(MOTION, "interval", lambda w=None: 7)
    sw = Switch(False)
    sw.setChecked(True)
    assert sw.anim.timer.interval() == 7
    sw.anim.timer.stop()


def test_anim_driver_snaps_when_reduced(qapp):
    host = QWidget()
    stepped = []
    d = AnimDriver(host, lambda: stepped.append(1))
    s = d.spring(0.0)
    MOTION.reduced = True
    d.to(s, 5)
    assert s.value == 5 and stepped == [1] and not d.timer.isActive()


def test_page_change_rises_and_settles(win, qapp):
    win.show()
    win.select(2)
    assert win.fade.t.value == 0 and win.fade.curtain.alpha == 1 and win.stack.currentWidget().y() == win.fade.RISE
    assert win.fade.RISE <= 8 and win.fade.t.stiffness > (2 * 3.14159 / 0.3) ** 2      # quick: under 0.3 s
    win.fade.t.snap(1.0)
    win.fade.step()
    assert win.stack.currentWidget().y() == 0 and win.fade.curtain.isHidden()
    win.select(4, animate=False)
    assert win.fade.t.value == 1 and win.stack.widget(2).y() == 0
    win.fade.anim.timer.stop()


def test_nothing_ticks_while_the_window_is_hidden(win):
    assert not win.live.isActive()
    win.show()
    assert win.live.isActive()
    win.hide()
    assert not win.live.isActive()


def test_stats_count_up(qapp):
    bar = StatsBar()
    bar.set_items([("12", "Clips"), ("0.71", "Best"), ("Rocket League", "Game"), ("–", "x")])
    bar.count.snap(0.5)
    assert [bar.shown(v) for v, _ in bar.items] == ["6", "0.35", "Rocket League", "–"]
    bar.count.snap(1.0)
    assert bar.shown("12") == "12"


def test_new_popup_grows_out_of_the_old_one(qapp):
    from laughclipper.toast import Toast
    screen = qapp.primaryScreen()
    old = Toast(screen, "bottom-center", "saved", "Game", "laughter", "F8", 8)
    old.alpha.snap(1.0)
    old.w.snap(236)
    new = Toast(screen, "bottom-center", "ask", "Game", "laughter", "F8", 8)
    assert new.adopt(old) is True
    assert new.alpha.value == 1 and new.slide.value == 0 and new.w.value == 236 and new.w.target > 236
    old.dismiss()
    assert Toast(screen, "bottom-center", "ask", "Game", "laughter", "F8", 8).adopt(old) is False   # on its way out
    assert Toast(screen, "top-left", "ask", "Game", "laughter", "F8", 8).adopt(new) is False        # somewhere else
    for t in (old, new):
        t.timer.stop()
        t.close()


def test_popup_only_fades_when_animations_are_off(qapp):
    from laughclipper.toast import Toast
    MOTION.reduced = True
    t = Toast(qapp.primaryScreen(), "bottom-center", "ask", "Game", "laughter", "F8", 8)
    t.clock.restart = lambda: 16
    t.tick()
    assert t.w.value == t.w.target and t.slide.value == 0 and 0 < t.alpha.value < 1
    t.timer.stop()
    t.close()


# ---------------------------------------------------------------- history
def entry(days_ago, status, **extra):
    e = clips.new_entry({"sound": "laughter", "game": "Rocket League", "peak": 0.5, **extra}, status)
    e["time"] = (datetime.now() - timedelta(days=days_ago, minutes=1)).isoformat(timespec="seconds")
    return e


def test_history_is_grouped_by_day_and_filtered(ctrl):
    ctrl.history = [entry(3, "saved"), entry(1, "failed"), entry(1, "not_funny"), entry(0, "saved"),
                    entry(0, "bookmarked")]
    w = SettingsWindow(ctrl)
    try:
        heads = [w.history_box.itemAt(i).widget().text() for i in range(w.history_box.count())
                 if isinstance(w.history_box.itemAt(i).widget(), QLabel)]
        assert heads[:2] == ["Today", "Yesterday"] and len(heads) == 3 and len(w.history_rows) == 5
        w.history_filter.choose(1)
        assert len(w.history_rows) == 3                         # saved + bookmarked
        w.history_filter.choose(2)
        assert len(w.history_rows) == 1
        texts = [b.text() for b in w.history_rows[0].findChildren(QPushButton)]
        assert texts == ["Test Clip"] and "Medal didn't make a clip" in w.history_rows[0].sub.text()
        w.history_filter.choose(3)
        assert len(w.history_rows) == 1
        ctrl.history = []
        w.build_history()
        assert w.history_rows == []
    finally:
        w.close()
        w.deleteLater()


def test_failed_clip_remembers_an_admin_game(ctrl):
    ctrl.on_laugh(VOICE, "laughter")
    ctrl.clip()
    ctrl.game_admin = True
    ctrl.watcher.deadline = time.time() - 1
    ctrl.watcher._poll()
    assert ctrl.history[-1]["status"] == "failed" and ctrl.history[-1]["why"] == "admin"
    w = SettingsWindow(ctrl)
    try:
        assert "administrator" in w.history_rows[0].sub.text()
    finally:
        w.close()
        w.deleteLater()


def test_thumbnails_load_once_in_the_background(monkeypatch, qapp):
    calls = []

    def fake(path, w, h):
        calls.append(path)
        return (2, 1, bytes(8)) if "good" in path else None
    monkeypatch.setattr(winutil, "thumbnail", fake)
    loader = widgets.ThumbLoader()
    loader.request("good.mp4")
    loader.request("good.mp4")
    loader.request("bad.mp4")
    loader.thread.join(2)
    assert sorted(calls) == ["bad.mp4", "good.mp4"]
    assert loader.get("good.mp4").width() == 2 and loader.get("bad.mp4") is None and "bad.mp4" in loader.images
    loader.request("good.mp4")
    assert len(calls) == 2


def test_settings_still_use_the_same_keys(win):
    """Moving rows between pages mustn't change what gets saved."""
    assert S["pause_hotkey"] == "" and {"voice_cancel", "hype_phrases", "game_only"} <= set(win.switches)


# ---------------------------------------------------------------- 2.3: status card, pinned live score, timing
def test_status_card_follows_the_app(ctrl, pc):
    w = SettingsWindow(ctrl)
    try:
        assert w.status_row.title.text() == "Listening for laughs"
        S["game_only"] = True
        pc.front = (1, r"C:\Windows\explorer.exe", False)
        pc.wait(10)
        ctrl.check_game()
        assert w.status_row.title.text() == "Waiting for a game"
        pc.medal = False
        ctrl.check_medal()
        assert "Medal isn't running" in w.status_row.sub.text() and not w.medal_row.isHidden()
        assert not w.test_btn.isEnabled()
    finally:
        w.close()
        w.deleteLater()


def test_live_score_is_pinned_above_the_detection_settings(win):
    i = TITLES.index("Detection")
    holder, scroll = win.stack.widget(i), win.scroller(i)
    assert scroll is not holder and holder.isAncestorOf(win.live_meter) and not scroll.isAncestorOf(win.live_meter)
    assert scroll.isAncestorOf(win.sliders["threshold"])
    assert win.scroller(0) is win.stack.widget(0)
    assert win.live_text({}) == "Starting…" or "Nothing to listen to" in win.live_text({})
    S["running"] = False
    assert win.live_text({}) == "Paused"


def test_timing_is_folded_away_and_search_opens_it(win):
    assert win.timing_panel.isHidden() and "Length needed 1 s" in win.timing_row.sub.text()
    win.ctrl.set("cooldown", 45)
    assert "wait between prompts 45 s" in win.timing_row.sub.text()
    win.search.setText("pause tolerance")
    assert win.results.entries[0] == ("Pause tolerance", "Detection")
    win.open_result(0)
    assert win.timing_panel.opener.isChecked() and not win.timing_panel.isHidden()


def test_setup_guide_shows_the_real_popup(ctrl, qapp):
    ctrl.show_setup()
    g = ctrl.setup
    assert g.welcome_stage.toast.mode == "ask" and g.done_stage.toast.mode == "saved"
    assert not g.welcome_stage.toast.timer.isActive()            # a still picture until the page plays it
    g.welcome_stage.play()
    t = g.welcome_stage.toast
    assert t.timer.isActive() and t.close_at is None
    g.welcome_stage.hold(t)
    assert not t.timer.isActive()
    assert not t.isVisible()                                     # the Toast itself never goes on screen
    g.close()
