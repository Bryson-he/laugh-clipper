"""The first-run setup guide and the tray icon states."""

import threading

from laughclipper.config import S

MIC = ("mic", "")


def test_guide_walks_through_and_sets_the_level(ctrl, qapp):
    ctrl.show_setup()
    g = ctrl.setup
    assert g.stack.currentIndex() == 0 and not g.back.isVisible() and g.next.text() == "Get Started"
    for _ in range(3):
        g.next.click()
    assert g.stack.currentIndex() == 3
    assert not g.use_level.isEnabled()                 # no laugh heard yet

    ctrl.engine.threads[MIC] = threading.Event()
    ctrl.engine.sound[MIC] = ("laughter", 0.6)          # someone laughs
    g.refresh_live()
    assert g.peak == 0.6 and g.use_level.isEnabled() and g.use_level.text() == "Use 0.45"
    g.use_level.click()
    assert S["threshold"] == 0.45
    ctrl.engine.sound[MIC] = ("laughter", 0.1)          # quieter afterwards: the best score is kept
    g.refresh_live()
    assert g.peak == 0.6

    g.next.click()
    assert g.next.text() == "Finish"
    g.next.click()
    assert S["setup_done"] is True and ctrl.setup is None


def test_suggestion_stays_sensible(ctrl):
    ctrl.show_setup()
    g = ctrl.setup
    g.peak = 0.95
    assert g.suggested() == 0.6
    g.peak = 0.13
    assert g.suggested() == 0.1
    g.close()


def test_closing_early_still_counts_as_done(ctrl):
    ctrl.show_setup()
    ctrl.setup.close()
    assert S["setup_done"] is True


def test_tray_icon_follows_the_state(ctrl, pc):
    assert ctrl.tray.icon().cacheKey() == ctrl.tray_icon("listening").cacheKey()
    pc.medal = False
    ctrl.check_medal()
    assert ctrl.tray.icon().cacheKey() == ctrl.tray_icon("warning").cacheKey()
    ctrl.set("running", False)
    assert ctrl.tray.icon().cacheKey() == ctrl.tray_icon("paused").cacheKey()
    pc.medal = True
    ctrl.check_medal()
    ctrl.set("running", True)
    assert ctrl.tray.icon().cacheKey() == ctrl.tray_icon("listening").cacheKey()
