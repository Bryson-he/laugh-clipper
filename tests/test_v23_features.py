"""2.3: a switch per voice phrase, keyboard page changes without animation, the thumbnail as Play, and the
smaller motion fixes."""

import json

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from laughclipper import config, voice, whatsnew, winutil
from laughclipper.config import S
from laughclipper.settings_window import PAGES, SettingsWindow
from laughclipper.widgets import MOTION, AnimDriver, StatsBar, Thumb

TITLES = [t for t, _, _ in PAGES]


@pytest.fixture
def win(ctrl):
    w = SettingsWindow(ctrl)
    yield w
    w.close()
    w.deleteLater()


# ---------------------------------------------------------------- a switch per phrase
def test_switched_off_phrases_leave_the_grammar():
    assert voice.wanted_phrases() == list(voice.CLIP_PHRASES + voice.HYPE_PHRASES)
    S["phrases_off"] = ["oh my god", "clip it"]
    assert voice.wanted_phrases() == ["clip that", "no way", "let's go"]
    g = voice.grammar(False, True)
    assert "oh my god" not in g and "clip it" not in g and "no way" in g and g[-1] == "[unk]"
    assert voice.grammar(True, False) == list(voice.CANCEL_PHRASES) + ["[unk]"]      # "don't clip" is untouched


def test_phrase_setting_is_cleaned():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_PATH.write_text(json.dumps({"phrases_off": ["no way", "banana", 3, "no way"]}), encoding="utf-8")
    config.load()
    assert S["phrases_off"] == ["no way"]
    config.SETTINGS_PATH.write_text(json.dumps({"phrases_off": "no way"}), encoding="utf-8")
    config.load()
    assert S["phrases_off"] == []


def test_phrase_chips_on_the_shortcuts_page(win):
    assert set(win.phrase_chips) == set(voice.CLIP_PHRASES + voice.HYPE_PHRASES)
    assert not win.phrases_row.isEnabled()                       # hype phrases are off to start with
    win.ctrl.set("hype_phrases", True)
    assert win.phrases_row.isEnabled() and "Click one" in win.phrases_row.sub.text()
    win.phrase_chips["oh my god"].click()
    assert S["phrases_off"] == ["oh my god"] and "4 of 5" in win.phrases_row.sub.text()
    win.phrase_chips["oh my god"].click()
    assert S["phrases_off"] == []


def test_turning_off_the_last_phrase_turns_the_feature_off(win):
    win.ctrl.set("hype_phrases", True)
    for phrase in list(win.phrase_chips)[:-1]:
        win.phrase_chips[phrase].click()
    last = list(win.phrase_chips)[-1]
    assert len(S["phrases_off"]) == 4
    win.phrase_chips[last].click()
    assert S["hype_phrases"] is False and len(S["phrases_off"]) == 4 and win.phrase_chips[last].isChecked()


def test_no_phrases_wanted_means_nothing_is_fed(monkeypatch):
    v = voice.VoiceCommands()
    v.model = object()
    S["hype_phrases"], S["phrases_off"] = True, list(voice.CLIP_PHRASES + voice.HYPE_PHRASES)
    import numpy as np
    v.feed(np.zeros(2400, np.float32))                           # would crash on the fake model if it got that far
    assert v._rec is None


# ---------------------------------------------------------------- keyboard page changes don't animate
def test_keys_change_page_without_the_transition(win, qapp):
    win.show()
    win.nav.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert win.stack.currentIndex() == 1 and win.fade.t.value == 1 and win.nav.pos.value == 1
    win.nav.items[3].clicked.emit()                              # a click does animate
    assert win.stack.currentIndex() == 3 and win.fade.t.value == 0
    win.fade.anim.timer.stop()
    win.nav.anim.timer.stop()
    win.fade.t.snap(1.0)
    win.search.setText("pause hot")
    win.open_result(0)
    assert TITLES[win.stack.currentIndex()] == "Shortcuts" and win.fade.t.value == 1


def test_page_fade_still_fades_when_animations_are_off(win, qapp):
    win.show()
    MOTION.reduced = True
    win.nav.items[2].clicked.emit()
    assert win.fade.t.value == 0 and win.fade.rise == 0 and win.stack.currentWidget().y() == 0
    assert win.fade.anim.timer.isActive()                        # the fade plays; nothing moves
    win.fade.anim.timer.stop()


def test_fade_flag_only_spares_fades(qapp):
    from PySide6.QtWidgets import QWidget
    d = AnimDriver(QWidget())
    a, b = d.spring(0.0), d.spring(0.0)
    MOTION.reduced = True
    d.to(a, 1.0)
    assert a.value == 1 and not d.timer.isActive()
    d.to(b, 1.0, fade=True)
    assert b.value == 0 and d.timer.isActive()
    d.timer.stop()


def test_stats_only_count_up_when_a_number_changes(qapp):
    bar = StatsBar()
    bar.show()
    bar.set_items([("3", "Clips")])
    bar.count.snap(1.0)
    bar.anim.timer.stop()
    bar.set_items([("3", "Clips")])                              # same numbers: stays put
    assert bar.count.value == 1 and not bar.anim.timer.isActive()
    bar.hide()
    bar.show()                                                   # coming back to the page: stays put
    assert bar.count.value == 1
    bar.set_items([("4", "Clips")])
    assert bar.count.value == 0 and bar.anim.timer.isActive()
    bar.anim.timer.stop()
    bar.close()


# ---------------------------------------------------------------- the thumbnail is the Play button
def test_thumbnail_plays_the_clip(qapp, monkeypatch):
    monkeypatch.setattr(winutil, "thumbnail", lambda *a: None)
    t = Thumb("C:/clips/a.mp4")
    got = []
    t.clicked.connect(lambda: got.append(1))
    inside = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    t.mouseReleaseEvent(inside)
    t.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert got == [1, 1] and t.focusPolicy() == Qt.FocusPolicy.TabFocus and t.accessibleName() == "Play clip"


# ---------------------------------------------------------------- What's New leads with the main changes
def test_whats_new_folds_the_rest():
    md = "intro\n\n### One\n- a\n\n### Two\n- b\n\n### Three\n- c\n\n### Four\n- d"
    lead, rest = whatsnew.split_notes(md)
    assert lead.endswith("- b") and rest.startswith("### Three") and "Four" in rest
    assert whatsnew.split_notes("### One\n- a") == ("### One\n- a", "")


def test_buttons_press_down():
    from laughclipper.theme import T
    css = T.stylesheet()
    assert "QPushButton:pressed" in css and "padding: 4px 12px 2px 12px" in css


def test_a_switched_off_phrase_is_not_heard_by_the_real_model():
    if not voice.available():
        pytest.skip("speech model not downloaded")
    import time
    from test_v2_features import speech
    v = voice.VoiceCommands()
    v.load()
    for _ in range(200):
        if v.model is not None:
            break
        time.sleep(0.05)
    heard = []
    v.heard.connect(lambda kind, text: heard.append(text))
    S["hype_phrases"], S["phrases_off"] = True, ["no way"]
    for chunk in speech("no_way.wav"):
        v.feed(chunk)
    assert heard == []
    v.generation += 1                      # a fresh listen, as when the next thing is said later
    for chunk in speech("lets_go.wav"):
        v.feed(chunk)
    assert heard == ["let's go"]
