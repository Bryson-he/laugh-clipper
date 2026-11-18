"""The settings window, laid out like macOS System Settings."""

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import clips, config, gamepad, medal, sounds, winutil
from .config import DEFAULTS, S, clean_games, hotkey_label
from .engine import SOUNDS, short_name, sound_level, sound_names
from .theme import T
from .version import VERSION
from .widgets import (Chip, Collapsible, Disclosure, Group, KeyCap, LevelMeter, NavList, PageFade, PopupButton,
                      PositionPicker, ResultList, Row, RowList, Segmented, Slider, StatsBar, StatusPill, Switch,
                      Thumb, WarningBadge, clamp, loudness)

PAGES = [                       # (title, Segoe Fluent icon, tile colour); Ctrl+1…8 jump to them
    ("General", "\ue713", "#8e8e93"),
    ("Games", "\ue7fc", "#af52de"),
    ("Audio", "\ue720", "#ff2d55"),
    ("Detection", "\ue9d9", "#34c759"),
    ("Clipping", "\ue714", "#5e5ce6"),
    ("Shortcuts", "\ue765", "#0a84ff"),
    ("Notification", "\uea8f", "#ff3b30"),
    ("History", "\ue81c", "#ff9500"),
]
HISTORY_FILTERS = ["All", "Saved", "Didn't save", "Not funny"]
KEPT = ("saved", "unchecked", "bookmarked")       # history statuses that count as a clip you have


PAD_MODES = ["While a popup shows", "Any time"]    # the controller button's "Works" choice (pad_mode)

SOUND_HINTS = {
    "laughter": "Laughs, giggles, chuckles and snickers.",
    "cheering": "Cheers, whoops and applause.",
    "shouting": "Shouts, yells and bellows.",
    "screaming": "Screams of fright or excitement.",
    "gasp": "A sharp breath in, like after a close call.",
}


def key_text(hk):
    """A hotkey for display ('shift+f7' -> 'Shift + F7'), or 'Not set'."""
    return hotkey_label(hk) if hk else "Not set"


def when(iso):
    """'2026-10-21T21:09:18' -> 'Today, 9:09 PM' / 'Yesterday, 8:02 PM' / 'Mon 19 Oct, 9:52 PM'."""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    clock = f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"
    days = (date.today() - t.date()).days
    day = "Today" if days == 0 else "Yesterday" if days == 1 else f"{t:%a} {t.day} {t:%b}"
    return f"{day}, {clock}"


def clock(iso):
    """'2026-10-21T21:09:18' -> '9:09 PM'."""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def day_name(iso):
    """'Today', 'Yesterday' or 'Wednesday 4 November' for a history entry's time."""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "Earlier"
    days = (date.today() - t.date()).days
    return "Today" if days == 0 else "Yesterday" if days == 1 else f"{t:%A} {t.day} {t:%B}"


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


class SoundOptions(QWidget):
    """Under a 'Trigger on' row: that sound's own trigger level, and which audio it listens to."""
    resized = Signal()

    def __init__(self, ctrl, key):
        super().__init__()
        self.ctrl, self.key = ctrl, key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 0, 14, 12)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.level_label = QLabel()
        self.level_label.setObjectName("secondary")
        row.addWidget(self.level_label, 1)
        self.reset = QPushButton("Reset")
        self.reset.setObjectName("small")
        self.reset.setToolTip("Use the main trigger level again")
        self.reset.clicked.connect(self.reset_level)
        row.addWidget(self.reset)
        self.slider = Slider(0.05, 0.9, 0.01, sound_level(key))
        self.slider.setFixedWidth(210)
        self.slider.setAccessibleName(f"{SOUNDS[key][0]} trigger level")
        self.slider.valueChanged.connect(self.level_moved)
        row.addWidget(self.slider)
        self.value = QLabel()
        self.value.setObjectName("value")
        self.value.setMinimumWidth(58)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel("Listen to")
        lbl.setObjectName("secondary")
        lbl.setFixedWidth(70)
        row.addWidget(lbl)
        self.chip_box = QHBoxLayout()
        self.chip_box.setSpacing(6)
        row.addLayout(self.chip_box)
        row.addStretch(1)
        lay.addLayout(row)
        self.chips = {}
        self.chip_ids = []

    def sources(self):
        """The sources being listened to: [(id, label)]."""
        out = [("mic", "Mic")] if S["listen_mic"] else []
        return out + [(f"out:{n}", short_name(n)) for n in (S["outputs"] or [])]

    def refresh(self):
        own = self.key in S["sound_levels"]
        level = sound_level(self.key)
        self.slider.blockSignals(True)
        self.slider.setValue(level)
        self.slider.blockSignals(False)
        self.value.setText(f"{level:.2f}")
        self.level_label.setText("Trigger level" if own else "Trigger level  ·  same as the main one")
        self.reset.setVisible(own)

        srcs = self.sources()
        if [i for i, _ in srcs] != self.chip_ids:
            for chip in self.chips.values():
                self.chip_box.removeWidget(chip)
                chip.deleteLater()
            self.chips = {}
            for sid, label in srcs:
                chip = Chip(label)
                chip.toggled.connect(lambda on, sid=sid: self.toggle_source(sid, on))
                self.chip_box.addWidget(chip)
                self.chips[sid] = chip
            self.chip_ids = [i for i, _ in srcs]
            if not srcs:
                self.chip_ids = []
            self.resized.emit()
        allowed = S["sound_sources"].get(self.key)
        for sid, chip in self.chips.items():
            chip.set_silent(allowed is None or sid in allowed)

    def level_moved(self, v):
        self.ctrl.set("sound_levels", {**S["sound_levels"], self.key: round(v, 2)})

    def reset_level(self):
        self.ctrl.set("sound_levels", {k: v for k, v in S["sound_levels"].items() if k != self.key})

    def toggle_source(self, sid, on):
        chosen = [i for i, chip in self.chips.items() if chip.isChecked()]
        if not chosen:                          # it has to listen to something
            self.chips[sid].set_silent(True)
            return
        others = {k: v for k, v in S["sound_sources"].items() if k != self.key}
        if len(chosen) < len(self.chips):
            others[self.key] = chosen
        self.ctrl.set("sound_sources", others)


class SettingsWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("root")
        self.setWindowTitle("Laugh Clipper")
        self.setWindowIcon(ctrl.icon)
        self.resize(900, 640)
        self.setMinimumSize(800, 560)
        # Windows 11: the sidebar is see-through to Mica, like Windows' own Settings. Has to be decided before
        # the window exists; apply_titlebar() turns it off again if Windows won't do it.
        self.mica = winutil.mica_supported() and QGuiApplication.platformName() == "windows"
        if self.mica:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setProperty("mica", True)
        self.built = False              # set once every page exists
        self.switches = {}
        self.sliders = {}
        self.out_rows = {}
        self.mic_names = []
        self.upd_mode = "check"

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setProperty("mica", self.mica)
        self.side = side
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 16)
        sl.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(20, 0, 12, 12)
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

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search settings")
        self.search.textChanged.connect(self.run_search)
        self.search.installEventFilter(self)
        box = QHBoxLayout()
        box.setContentsMargins(12, 0, 12, 10)
        box.addWidget(self.search)
        sl.addLayout(box)

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")         # opaque, so only the sidebar shows the backdrop
        builders = [self.page_general, self.page_games, self.page_audio, self.page_detection, self.page_clipping,
                    self.page_shortcuts, self.page_notification, self.page_history]
        for build in builders:
            self.stack.addWidget(build())
        self.nav = NavList(PAGES)
        self.nav.chosen.connect(self.select)
        sl.addWidget(self.nav)
        self.results = ResultList()
        self.results.opened.connect(self.open_result)
        self.results.hide()
        sl.addWidget(self.results)
        self.found = []                 # what the results list is showing: [(page index, Row or None)]
        sl.addStretch(1)
        ver = QLabel(f"Version {VERSION}")
        ver.setObjectName("secondary")
        ver.setContentsMargins(22, 0, 0, 0)
        sl.addWidget(ver)

        outer.addWidget(side)
        outer.addWidget(self.stack, 1)
        self.fade = PageFade(self.stack)
        self.built = True
        self.select(0, animate=False)
        self.update_status()

        for n in range(len(PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{n + 1}"), self, lambda n=n: self.select(n, animate=False))
        QShortcut(QKeySequence.StandardKey.Find, self, self.focus_search)
        QApplication.instance().focusChanged.connect(self.follow_focus)
        self.tab_only()

        ctrl.changed.connect(self.on_setting_changed)
        ctrl.devices_changed.connect(self.build_devices)
        ctrl.update_changed.connect(self.on_update)
        ctrl.history_changed.connect(self.build_history)
        ctrl.history_changed.connect(self.build_health)
        T.changed.connect(self.apply_titlebar)
        QGuiApplication.instance().screenAdded.connect(lambda *_: self.refresh_displays())
        QGuiApplication.instance().screenRemoved.connect(lambda *_: self.refresh_displays())
        if ctrl.devices:
            self.build_devices(*ctrl.devices)
        self.on_update(*ctrl.update_state)

        self.live = QTimer(self)
        self.live.setInterval(40)
        self.live.timeout.connect(self.refresh_live)      # started in showEvent: nothing ticks while hidden

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
        """Show page i. From a click it rises a little and fades in (widgets.PageFade); from the keyboard
        (Ctrl+1…8, the arrow keys, search) it's simply there, since nobody presses a key to watch a transition."""
        i = int(clamp(i, 0, self.stack.count() - 1))
        self.nav.set_index(i, animate)
        if self.stack.currentIndex() == i and animate:
            return
        self.stack.setCurrentIndex(i)
        self.fade.start(animate and self.isVisible())

    def tab_only(self):
        """Buttons take keyboard focus from Tab but not from a click, so the focus ring only shows when
        you're using the keyboard. Called again after rows are rebuilt."""
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def follow_focus(self, _old, new):
        """Tabbing to a control further down the page scrolls it into view."""
        page = self.scroller(self.stack.currentIndex()) if self.stack.count() else None
        if new is not None and page is not None and page.isAncestorOf(new):
            page.ensureWidgetVisible(new, 20, 70)

    # ---------------------------------------------------------- search
    def focus_search(self):
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def search_index(self):
        """Everything you can search for: each page, and each setting's row (not the clips in History)."""
        entries = []
        for i, (title, _, _) in enumerate(PAGES):
            entries.append((title, "", title, i, None))
            if title == "History":
                continue
            for row in self.stack.widget(i).findChildren(Row):
                if row.title.text() and not row.isHidden():
                    entries.append((row.title.text(), row.sub.text(), title, i, row))
        return entries

    def run_search(self, text):
        words = text.lower().split()
        self.nav.setVisible(not words)
        self.results.setVisible(bool(words))
        if not words:
            self.found = []
            return
        hits = []
        for title, detail, page, i, row in self.search_index():
            name = title.lower()
            if all(w in f"{name} {detail.lower()} {page.lower()}" for w in words):
                # best first: the name starts with it, then the name has it, then only the description does
                rank = 0 if name.startswith(words[0]) else 1 if all(w in name for w in words) else 2
                hits.append((rank, len(hits), title, page, i, row))
        hits.sort(key=lambda h: h[:2])
        hits = hits[:8]
        self.found = [(i, row) for _, _, _, _, i, row in hits]
        self.results.set_entries([(title, page if row is not None else "Page") for _, _, title, page, _, row in hits])

    def open_result(self, n):
        if not 0 <= n < len(self.found):
            return
        i, row = self.found[n]
        self.search.clear()
        self.select(i, animate=False)       # you asked for it by name: just be there
        if row is not None:
            page = self.scroller(i)
            parent = row.parentWidget()
            while parent is not None and parent is not page:       # a row folded away: open what holds it
                if isinstance(parent, Collapsible) and getattr(parent, "opener", None) is not None:
                    parent.opener.setChecked(True)
                parent = parent.parentWidget()

            def show():
                try:
                    page.ensureWidgetVisible(row, 20, 120)
                    row.flash()
                except RuntimeError:
                    pass                    # the row was rebuilt in the meantime
            QTimer.singleShot(60, show)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == e.Type.KeyPress:
            k = e.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up) and not self.results.isHidden():
                self.results.step(1 if k == Qt.Key.Key_Down else -1)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self.results.isHidden():
                self.open_result(self.results.current)
                return True
            if k == Qt.Key.Key_Escape and self.search.text():
                self.search.clear()
                return True
        return super().eventFilter(obj, e)

    # ---------------------------------------------------------- pages
    def page_general(self):
        p = Page("General")
        # one card that says what the app is doing right now; anything in the way is listed under it
        g = self.health_group = p.section()
        self.test_btn = QPushButton("Test Clip")
        self.test_btn.setToolTip("Presses Medal's clip hotkey for real and checks the clip turns up")
        self.test_btn.clicked.connect(self.ctrl.test_clip)
        self.status_row = g.add(Row("", "", self.test_btn, self.switch("running")))
        self.status_row.title.setObjectName("statusTitle")
        self.status_row.setMinimumHeight(76)
        self.status_row.layout().setContentsMargins(16, 12, 14, 12)
        self.status_row.layout().setSpacing(14)
        self.status_icon = QLabel(self.status_row)
        self.status_icon.setFixedSize(44, 44)
        self.status_row.layout().insertWidget(0, self.status_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switches["running"].setAccessibleName("Laugh detection")
        open_medal = QPushButton("Open Medal")
        open_medal.setObjectName("prominent")
        open_medal.clicked.connect(self.ctrl.open_medal)
        self.medal_row = g.add(Row("Medal isn't running", "Clips can't be saved until Medal is open.", open_medal))
        self.medal_row.layout().insertWidget(0, WarningBadge(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.medal_row.setVisible(not self.ctrl.medal_ok)
        self.problem_rows = []
        self.health_bad = []
        self.build_health()
        g = p.section(footer="When the window is closed, Laugh Clipper keeps running in the system tray.")
        self.startup_sw = Switch(winutil.startup_enabled())
        self.startup_sw.toggled.connect(winutil.set_startup)
        g.add(Row("Start with Windows", None, self.startup_sw))
        g.add(Row("Start in the tray", "Don't open this window when Laugh Clipper starts.",
                  self.switch("start_minimized")))
        setup = QPushButton("Run Setup Again")
        setup.clicked.connect(self.ctrl.show_setup)
        g.add(Row("Setup guide", "Hotkey, audio and a quick laugh to set the trigger level.", setup))
        g = p.section("Updates")
        self.upd_btn = QPushButton("Check for Updates")
        self.upd_btn.clicked.connect(self.update_clicked)
        whats_new = QPushButton("What's New")
        whats_new.clicked.connect(self.ctrl.show_whats_new)
        from .whatsnew import notes
        whats_new.setVisible(bool(notes(VERSION)))
        self.upd_row = g.add(Row("Laugh Clipper", f"Version {VERSION}", whats_new, self.upd_btn))
        g.add(Row("Check for updates automatically", None, self.switch("check_updates")))
        g = p.section("Troubleshooting")
        open_log = QPushButton("Open Log")
        open_log.setObjectName("small")
        open_log.clicked.connect(lambda: winutil.open_file(str(config.LOG_PATH)))
        self.copy_diag = QPushButton("Copy Diagnostics")
        self.copy_diag.clicked.connect(self.copy_diagnostics)
        g.add(Row("Something not working?", "Copies a summary of your setup and recent problems. No folders or "
                                            "game names.", open_log, self.copy_diag))
        return p.finish()

    def build_health(self):
        """Under the status row: a row for each thing that would stop a clip saving (Medal has its own)."""
        g = self.health_group
        for row in self.problem_rows:
            g.remove(row)
        self.problem_rows = []
        bad = [(key, text) for key, ok, text in self.ctrl.health() if ok is False]
        self.health_bad = [key for key, _ in bad]
        for key, text in bad:
            if key == "medal":
                continue                       # its row has the Open Medal button
            row = g.add(Row(text))
            row.title.setWordWrap(True)
            row.layout().insertWidget(0, WarningBadge(), 0, Qt.AlignmentFlag.AlignVCenter)
            self.problem_rows.append(row)
        self.test_btn.setEnabled(self.ctrl.medal_ok)
        self.tab_only()
        if self.built:
            self.update_status()

    def copy_diagnostics(self):
        QGuiApplication.clipboard().setText(self.ctrl.diagnostics())
        self.copy_diag.setText("Copied")
        QTimer.singleShot(1600, lambda: self.copy_diag.setText("Copy Diagnostics"))

    def page_games(self):
        p = Page("Games")
        g = p.section()
        self.game_row = g.add(Row("Only listen while playing", "", self.switch("game_only")))
        self.fullscreen_row = g.add(Row("Count any full-screen app as a game",
                                        "Catches most games without adding them. Full-screen videos count too.",
                                        self.switch("fullscreen_games")))
        self.autoadd_row = g.add(Row("Add games automatically",
                                     "Adds Steam, Epic, Xbox, Riot, EA, Ubisoft and GOG games when you play them.",
                                     self.switch("auto_add_games")))
        self.add_game_btn = QPushButton("Add Game…")
        self.add_game_btn.setObjectName("small")
        self.add_game_btn.clicked.connect(self.show_game_menu)
        self.games_group = p.section("Your games", header_widget=self.add_game_btn,
                                     footer="Detection pauses when none of these is the window in front, so "
                                            "videos and music between games don't set it off. It keeps "
                                            "listening for a few seconds after you alt-tab.")
        self.build_games()
        return p.finish()

    def page_shortcuts(self):
        p = Page("Shortcuts")
        g = p.section("Keyboard")
        self.pause_keycap = KeyCap("")
        self.pause_keycap.clicked.connect(self.record_pause_key)
        self.pause_clear = QPushButton("Remove")
        self.pause_clear.setObjectName("small")
        self.pause_clear.clicked.connect(lambda: self.ctrl.set("pause_hotkey", ""))
        self.pause_row = g.add(Row("Pause hotkey", "", self.pause_clear, self.pause_keycap))
        self.update_pause_row()

        g = p.section("Voice commands", footer="Uses a small speech model on your PC, so nothing you say "
                                               "leaves it. Hype phrases need to be said on their own, not in "
                                               "the middle of a sentence.")
        self.voice_row = g.add(Row("Cancel with your voice", "", self.switch("voice_cancel")))
        self.hype_row = g.add(Row("Clip with hype phrases", "", self.switch("hype_phrases")))
        from . import voice
        self.phrase_chips = {}
        chips = QWidget()
        cl = QHBoxLayout(chips)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        for phrase in voice.CLIP_PHRASES + voice.HYPE_PHRASES:
            chip = Chip(f"\u201c{phrase}\u201d", phrase not in S["phrases_off"])
            chip.setAccessibleName(f"Listen for {phrase}")
            chip.toggled.connect(lambda on, phrase=phrase: self.toggle_phrase(phrase, on))
            cl.addWidget(chip)
            self.phrase_chips[phrase] = chip
        self.phrases_row = g.add(Row("Phrases", "", chips))
        self.update_voice_row()

        g = p.section("Controller or wheel", footer="Works with Xbox and PlayStation controllers, racing wheels "
                                                    "and button boxes, even while the game is in front. Pick a "
                                                    "button the game doesn't use much.")
        self.pad_keycap = KeyCap("")
        self.pad_keycap.clicked.connect(self.capture_pad)
        self.pad_clear = QPushButton("Remove")
        self.pad_clear.setObjectName("small")
        self.pad_clear.clicked.connect(lambda: self.ctrl.set("pad_button", {}))
        self.pad_row = g.add(Row("Clip button", "", self.pad_clear, self.pad_keycap))
        self.pad_mode = Segmented(PAD_MODES, 0 if S["pad_mode"] == "popup" else 1)
        self.pad_mode.changed.connect(lambda i: self.ctrl.set("pad_mode", ("popup", "always")[i]))
        self.pad_mode_row = g.add(Row("Works", None, self.pad_mode))
        self.update_pad_rows()
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

        g = p.section("Sound", footer="Handy in exclusive-fullscreen games, where Windows won't let the "
                                      "notification show. With Wave Link, pick a channel you can hear, like "
                                      "Game, if your System channel is turned down.")
        g.add(Row("Play a sound with the notification", None, self.switch("sound_cue")))
        play = QPushButton("Play")
        play.setObjectName("small")
        play.clicked.connect(lambda: self.preview_sound())
        self.volume_row = self.slider_row(g, "Volume", None, "sound_volume", 0, 100, 5,
                                          lambda v: "Off" if v == 0 else f"{v:g}%")
        self.volume_row.layout().addWidget(play, 0, Qt.AlignmentFlag.AlignVCenter)
        self.sound_pick = PopupButton()
        self.sound_pick.addItems(list(sounds.SOUNDS.values()))
        self.sound_pick.setCurrentIndex(list(sounds.SOUNDS).index(S["sound_name"]))
        self.sound_pick.activated.connect(self.sound_chosen)
        self.sound_pick_row = g.add(Row("Sound", None, self.sound_pick))
        self.sound_out = PopupButton()
        self.sound_out.activated.connect(self.sound_device_chosen)
        self.sound_out_names = []
        self.sound_out_row = g.add(Row("Play on", None, self.sound_out))
        self.fill_sound_outputs()
        self.update_sound_rows()
        return p.finish()

    def fill_sound_outputs(self, speakers=None):
        names = list(speakers if speakers is not None else (self.ctrl.devices[1] if self.ctrl.devices else []))
        cur = S["sound_device"]
        if cur and cur not in names:
            names.append(cur)
        self.sound_out_names = names
        self.sound_out.blockSignals(True)
        self.sound_out.clear()
        self.sound_out.addItem("Windows default")
        self.sound_out.addItems([short_name(n) if n in (speakers or names) else f"{short_name(n)} (not connected)"
                                 for n in names])
        self.sound_out.setCurrentIndex(names.index(cur) + 1 if cur else 0)
        self.sound_out.blockSignals(False)

    def preview_sound(self):
        sounds.play_chime(S["sound_volume"], S["sound_device"], S["sound_name"])

    def sound_chosen(self, i):
        self.ctrl.set("sound_name", list(sounds.SOUNDS)[i])
        self.preview_sound()

    def sound_device_chosen(self, i):
        self.ctrl.set("sound_device", "" if i <= 0 else self.sound_out_names[i - 1])
        self.preview_sound()     # let them hear where it goes

    def update_sound_rows(self):
        for row in (self.sound_pick_row, self.volume_row, self.sound_out_row):
            row.setEnabled(S["sound_cue"])

    def page_clipping(self):
        p = Page("Clipping")
        g = p.section(footer="Medal saves the last few seconds when the hotkey is pressed. Keep Medal's clip "
                             "length longer than the wait plus your longest laugh, or the start of the "
                             "moment gets cut off.")
        self.keycap = KeyCap(hotkey_label())
        self.keycap.clicked.connect(self.record_hotkey)
        self.medal_keys = PopupButton()
        self.medal_keys.setMinimumWidth(200)
        self.medal_keys.setMinimumContentsLength(14)
        self.medal_keys.activated.connect(self.medal_key_chosen)
        self.use_medal_key = QPushButton("Use Medal's")
        self.use_medal_key.clicked.connect(lambda: self.ctrl.apply_hotkey(self.medal_clip_keys[0][1]))
        self.hotkey_row = g.add(Row("Medal clip hotkey", "", self.medal_keys, self.use_medal_key, self.keycap))
        g.add(Row("Save clips automatically", "Saves the clip for you instead of asking.",
                  self.switch("auto_clip")))
        g.add(Row("Warn when Medal isn't running", "Tells you the clip won't save instead of pretending it did.",
                  self.switch("medal_check")))
        self.delay_row = self.slider_row(g, "Wait after laughing stops",
                                         "Keeps the whole laugh in the clip.", "clip_delay", 0, 30, 1,
                                         lambda v: "Instantly" if v == 0 else secs(v))
        self.maxwait_row = self.slider_row(g, "Save anyway after", "In case the laughing just won't stop.",
                                           "max_wait", 0, 120, 5, lambda v: "Never" if v == 0 else secs(v))
        self.update_auto_rows()
        self.smart_row = g.add(Row("Pick the clip length to fit the moment",
                                   "Uses Medal's longer clip hotkeys for long laughs, so the start isn't cut off.",
                                   self.switch("smart_length")))
        self.long_row = self.slider_row(g, "Use a longer clip after laughing for",
                                        "Each time the laughing goes on this long, it moves up to Medal's next "
                                        "clip length.", "long_laugh", 3, 30, 1, secs)
        self.length_warn = g.add(Row("The start of a moment could get cut off", ""))
        self.length_warn.layout().insertWidget(0, WarningBadge(), 0, Qt.AlignmentFlag.AlignVCenter)

        g = p.section("Clip check", footer="After the hotkey, Laugh Clipper looks for the new clip in Medal's "
                                           "folder, so the popup only says “Clip saved” when it really was.")
        g.add(Row("Check that Medal saved the clip", None, self.switch("confirm_clips")))
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_clips_folder)
        self.auto_folder_btn = QPushButton("Find It")
        self.auto_folder_btn.setObjectName("small")
        self.auto_folder_btn.setToolTip("Find Medal's clips folder automatically")
        self.auto_folder_btn.clicked.connect(lambda: self.ctrl.set("clips_folder", ""))
        self.folder_row = g.add(Row("Medal's clips folder", "", self.auto_folder_btn, choose))
        self.update_folder_row()

        self.rec_pill = StatusPill("Not recording", "secondary")
        g = p.section("While Medal is recording",
                      footer="Medal can record a whole session (its start/stop recording hotkey). While it does, "
                             "a laugh can add a bookmark to that recording instead of saving a separate clip.")
        self.bookmark_row = g.add(Row("Add a bookmark instead of a clip", "", self.rec_pill,
                                      self.switch("bookmark_when_recording")))
        self.bm_keycap = KeyCap("")
        self.bm_keycap.clicked.connect(self.record_bookmark_key)
        self.use_medal_bm = QPushButton("Use Medal's")
        self.use_medal_bm.clicked.connect(lambda: self.ctrl.set("bookmark_hotkey", ""))
        self.bm_key_row = g.add(Row("Medal bookmark hotkey", "", self.use_medal_bm, self.bm_keycap))
        self.update_medal_keys()
        return p.finish()

    def update_medal_keys(self):
        """Compare our hotkeys with the ones set in Medal (everyone's are different), and offer Medal's."""
        keys = medal.hotkeys()
        clip_keys = medal.clip_hotkeys(keys)
        self.medal_clip_keys = clip_keys
        many = len(clip_keys) > 1
        self.medal_keys.blockSignals(True)
        self.medal_keys.clear()
        if many:
            self.medal_keys.addItem("Use Medal's…")
            for length, hk in clip_keys:
                self.medal_keys.addItem(f"{length} s clip  ·  {key_text(hk)}" if length else key_text(hk))
        self.medal_keys.setCurrentIndex(0)
        self.medal_keys.blockSignals(False)
        self.medal_keys.setVisible(many)
        match = next((length for length, hk in clip_keys if hk == S["hotkey"]), None)
        self.use_medal_key.setVisible(len(clip_keys) == 1 and match is None)
        if not clip_keys:
            self.hotkey_row.set_sub("Click, then press the clip hotkey you set in Medal.")
        elif match is not None:
            self.hotkey_row.set_sub(f"Matches Medal's {match} s clip hotkey." if match else
                                    "Matches Medal's clip hotkey.")
        elif S["hotkey"] == medal.bookmark_hotkey(keys):
            self.hotkey_row.set_sub("That's Medal's bookmark hotkey, not its clip hotkey. Use Medal's clip "
                                    "hotkey instead.")
        else:
            self.hotkey_row.set_sub(f"Medal's clip hotkey is {key_text(clip_keys[0][1])}." if not many else
                                    "Medal doesn't use this hotkey for clips. Pick one of Medal's from the menu.")

        from_medal = medal.bookmark_hotkey(keys)
        bm = S["bookmark_hotkey"] or from_medal
        if not self.bm_keycap.recording:
            self.bm_keycap.set_label(key_text(bm))
        self.use_medal_bm.setVisible(bool(S["bookmark_hotkey"] and from_medal and S["bookmark_hotkey"] != from_medal))
        if not bm:
            self.bm_key_row.set_sub("Not found in Medal's settings. Click, then press the bookmark hotkey you "
                                    "set in Medal.")
        elif S["bookmark_hotkey"]:
            self.bm_key_row.set_sub("Set by you. Click to change it.")
        else:
            self.bm_key_row.set_sub("Read from Medal's settings. Click to change it.")
        self.bookmark_row.set_sub(f"While Medal is recording, presses its bookmark hotkey ({key_text(bm)}) "
                                  "instead of the clip hotkey." if bm else
                                  "Set Medal's bookmark hotkey below first.")
        self.update_rec_pill()
        self.update_clip_length()

    def record_pause_key(self):
        self.pause_keycap.recording = True
        self.pause_keycap.set_label("Press keys…")
        self.ctrl.record_hotkey("pause_hotkey")

    def update_pause_row(self):
        hk = S["pause_hotkey"]
        if not self.pause_keycap.recording:
            self.pause_keycap.set_label(hotkey_label(hk) if hk else "Not set")
        self.pause_clear.setVisible(bool(hk))
        self.pause_row.set_sub(f"Press {hotkey_label(hk)} in a game to pause or resume listening." if hk else
                               "Click to set a key that pauses or resumes listening, even from inside a game.")

    def update_voice_row(self):
        from . import voice
        mic_off = " Turn on your microphone on the Audio page first." if not S["listen_mic"] else ""
        if not voice.available():
            text = hype = "The speech model isn't installed with this copy of Laugh Clipper."
        else:
            text = ("Say \u201cdon\u2019t clip\u201d or \u201ccancel clip\u201d while the popup is up and it "
                    "won\u2019t save." + mic_off)
            hype = ("\u201cClip that\u201d and \u201cclip it\u201d save a clip straight away. The others count "
                    "as a moment, like a laugh." + mic_off)
        self.voice_row.set_sub(text)
        self.hype_row.set_sub(hype)
        for row in (self.voice_row, self.hype_row):
            row.setEnabled(voice.available())
        for phrase, chip in self.phrase_chips.items():
            chip.set_silent(phrase not in S["phrases_off"])
        on = len(voice.wanted_phrases())
        self.phrases_row.set_sub("Click one to turn it off." if on == len(self.phrase_chips) else
                                 f"Listening for {on} of {len(self.phrase_chips)}.")
        self.phrases_row.setEnabled(voice.available() and S["hype_phrases"])

    def toggle_phrase(self, phrase, on):
        from . import voice
        off = [p for p in S["phrases_off"] if p != phrase] + ([] if on else [phrase])
        if len(off) == len(self.phrase_chips):          # the last one: turn the whole thing off instead
            self.phrase_chips[phrase].set_silent(True)
            self.ctrl.set("hype_phrases", False)
            return
        self.ctrl.set("phrases_off", [p for p in voice.CLIP_PHRASES + voice.HYPE_PHRASES if p in off])

    def capture_pad(self):
        self.ctrl.capture_pad_button()
        self.update_pad_rows()

    def update_pad_rows(self):
        info = S["pad_button"]
        if self.ctrl.pad_capture:
            self.pad_keycap.set_label("Press a button\u2026")
            self.pad_row.set_sub("Press the button on your controller or wheel that should save clips.")
        elif info:
            self.pad_keycap.set_label(gamepad.button_name(info))
            self.pad_row.set_sub(f"{gamepad.button_name(info)} on your {info.get('name') or 'controller'} "
                                 "saves the clip. Click to change it.")
        else:
            self.pad_keycap.set_label("Not set")
            self.pad_row.set_sub("Click, then press a button on your controller or wheel.")
        self.pad_clear.setVisible(bool(info) and not self.ctrl.pad_capture)
        self.pad_mode_row.setEnabled(bool(info))

    def record_bookmark_key(self):
        self.bm_keycap.recording = True
        self.bm_keycap.set_label("Press keys…")
        self.ctrl.record_hotkey("bookmark_hotkey")

    def update_rec_pill(self):
        rec = self.ctrl.recording
        self.rec_pill.text, self.rec_pill.color_key = ("Recording", "red") if rec else ("Not recording", "secondary")
        self.rec_pill.setFixedWidth(88 if rec else 108)
        self.rec_pill.update()

    def medal_key_chosen(self, i):
        if 1 <= i <= len(self.medal_clip_keys):
            self.ctrl.apply_hotkey(self.medal_clip_keys[i - 1][1])
        self.medal_keys.setCurrentIndex(0)

    def page_detection(self):
        p = Page("Detection")
        g = p.section("Trigger on", footer="Laughter is the most reliable; shouting and screaming can also "
                                           "pick up loud game audio. A sound's arrow gives it its own level and "
                                           "sources.")
        self.trigger_sw, self.sound_opts = {}, {}
        for key, (name, _, _) in SOUNDS.items():
            sw = Switch(key in S["triggers"])
            sw.toggled.connect(lambda on, key=key: self.toggle_trigger(key, on))
            self.trigger_sw[key] = sw
            more = Disclosure()
            more.setToolTip(f"{name} options")
            g.add(Row(name, SOUND_HINTS.get(key), more, sw))
            opts = SoundOptions(self.ctrl, key)
            panel = g.add(Collapsible(opts))
            more.toggled.connect(lambda on, panel=panel: panel.set_open(on))
            opts.resized.connect(panel.refresh)
            self.sound_opts[key] = (more, panel, opts)
        self.update_sound_opts()
        g = p.section()
        g.add(Row("Group laughs", "Triggers sooner when you and your friends laugh together. Needs your mic and "
                                  "some PC audio on.", self.switch("group_laughs")))
        g = p.section(footer="Watch the live score above while you laugh, then set the trigger level just below "
                             "where it usually peaks.")
        calibrate = QPushButton("Calibrate…")
        calibrate.clicked.connect(self.ctrl.show_calibration)
        g.add(Row("Calibrate for your game", "Listens to your game, then to you laughing, and suggests a level.",
                  calibrate))
        self.slider_row(g, "Trigger level", "Lower catches more, higher means fewer false alarms.",
                        "threshold", 0.05, 0.9, 0.01, lambda v: f"{v:.2f}")
        # the three most people never touch sit behind a disclosure
        more = Disclosure()
        more.setAccessibleName("More timing")
        self.timing_row = g.add(Row("More timing", "", more))
        timing = RowList()
        self.slider_row(timing, "Length needed", "How long a laugh has to last before it counts.",
                        "min_laugh", 0.5, 3, 0.25, secs)
        self.slider_row(timing, "Pause tolerance", "Pauses up to this long still count as the same laugh.",
                        "pause_tolerance", 0.5, 4, 0.25, secs)
        self.slider_row(timing, "Wait between prompts", "Stops it asking again straight away.",
                        "cooldown", 0, 120, 5, secs)
        panel = g.add(Collapsible(timing))
        panel.opener = more
        more.toggled.connect(panel.set_open)
        self.timing_panel = panel
        self.update_timing_row()
        p.finish()

        # the live score stays put above the scrolling settings, so it's in view whatever you're changing
        holder = QWidget()
        holder.setObjectName("content")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("content")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(30, 16, 30, 0)
        pinned = Group()
        self.live_meter = LevelMeter(300)
        self.live_meter.setToolTip("Grey shows how loud it is. The bar on top is the score for the sounds you "
                                   "trigger on, green once it passes the trigger level. The thin line holds your "
                                   "recent peak.")
        self.live_meter.setAccessibleName("Live score")
        self.live_row = pinned.add(Row("Live score", "", self.live_meter))
        bl.addWidget(pinned)
        hl.addWidget(bar)
        hl.addWidget(p, 1)
        holder.scroll_area = p      # (not .scroll: that's a QWidget method)
        return holder

    def update_timing_row(self):
        self.timing_row.set_sub(f"Length needed {secs(S['min_laugh'])}  ·  pause tolerance "
                                f"{secs(S['pause_tolerance'])}  ·  wait between prompts {secs(S['cooldown'])}")

    def scroller(self, i):
        """The scrolling part of page i (Detection keeps its live score outside it)."""
        page = self.stack.widget(i)
        return getattr(page, "scroll_area", page)

    def page_history(self):
        p = Page("History")
        g = p.section()
        self.stats_bar = StatsBar()
        g.add(self.stats_bar)
        self.history_filter = Segmented(HISTORY_FILTERS, 0)
        self.history_filter.setAccessibleName("Show")
        self.history_filter.changed.connect(lambda _: self.build_history())
        clear = QPushButton("Clear")
        clear.setObjectName("small")
        clear.clicked.connect(self.clear_history)
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 6)
        bar.addWidget(self.history_filter)
        bar.addStretch(1)
        bar.addWidget(clear)
        p.lay.addLayout(bar)
        self.history_box = QVBoxLayout()            # a heading and a group per day, rebuilt by build_history
        self.history_box.setSpacing(6)
        p.lay.addLayout(self.history_box)
        self.history_rows = []
        foot = QLabel("Clips that Laugh Clipper saved for you, newest first. Clearing the list doesn't delete any "
                      "clips from Medal.")
        foot.setObjectName("footer")
        foot.setWordWrap(True)
        foot.setContentsMargins(8, 4, 8, 10)
        p.lay.addWidget(foot)
        self.build_history()
        return p.finish()

    # ---------------------------------------------------------- clips + history
    def update_folder_row(self):
        folder = clips.clips_folder()
        chosen = bool(S["clips_folder"])
        if folder:
            self.folder_row.set_sub(str(folder) + ("" if chosen else "  ·  found automatically"))
        elif chosen:
            self.folder_row.set_sub(f"{S['clips_folder']} doesn't exist. Choose another folder.")
        else:
            self.folder_row.set_sub("Couldn't find it. Choose the folder Medal saves clips to.")
        self.auto_folder_btn.setVisible(chosen)
        self.folder_row.setEnabled(S["confirm_clips"])

    def choose_clips_folder(self):
        start = str(clips.clips_folder() or "")
        path = QFileDialog.getExistingDirectory(self, "Medal's Clips Folder", start)
        if path:
            self.ctrl.set("clips_folder", str(Path(path)))

    def clear_history(self):
        if not self.ctrl.history:
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Clear History",
                          "Clear the list of clips? Your clips stay in Medal.",
                          QMessageBox.StandardButton.Cancel, self)
        clear = box.addButton("Clear", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is clear:
            self.ctrl.clear_history()

    def build_history(self):
        st = clips.stats(self.ctrl.history)
        self.stats_bar.set_items([
            (str(st["clips"]), "Clips saved"),
            (str(st["week"]), "This week"),
            (st["top_game"] or "–", "Most clipped game"),
            (f"{st['best']:.2f}" if st["best"] else "–", "Best laugh score"),
            (str(st["not_funny"]), "Not funny"),
        ])
        while self.history_box.count():             # throw the old day groups away
            w = self.history_box.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.history_rows = []
        show = self.history_filter.index
        items = [it for it in reversed(self.ctrl.history)
                 if show == 0 or (show == 1 and it.get("status") in KEPT)
                 or (show == 2 and it.get("status") == "failed") or (show == 3 and it.get("status") == "not_funny")]
        items = items[:100]
        if not items:
            g = Group()
            g.add(Row("No clips yet", "Clips Laugh Clipper saves for you will show up here.") if show == 0 else
                  Row(f"Nothing under \u201c{HISTORY_FILTERS[show]}\u201d"))
            self.history_box.addWidget(g)
        day, g = None, None
        for it in items:
            if day_name(it["time"]) != day:
                day = day_name(it["time"])
                head = QLabel(day)
                head.setObjectName("header")
                head.setContentsMargins(8, 8, 2, 0)
                self.history_box.addWidget(head)
                g = Group()
                self.history_box.addWidget(g)
            row = g.add(self.history_row(it))
            self.history_rows.append(row)
        self.tab_only()
        newest = self.ctrl.history[-1].get("time") if self.ctrl.history else None
        if self.history_rows and self.isVisible() and newest != getattr(self, "history_newest", newest) \
                and items and items[0].get("time") == newest:
            self.history_rows[0].flash()            # a clip just arrived while you were looking
        self.history_newest = newest

    def history_row(self, it):
        name = sound_names(it.get("sound"))[0]
        title = f"{name}  ·  {it['game']}" if it.get("game") else name
        score = it.get("score") or 0
        status = it.get("status")
        sub = "  ·  ".join(x for x in (clock(it["time"]), it.get("source", ""),
                                       f"{it['length']} s clip" if it.get("length") else "",
                                       f"score {score:.2f}" if score else "",
                                       "auto-saved" if it.get("auto") else "")
                           if x)
        pill = {"saved": StatusPill("Saved", "green"), "failed": StatusPill("Didn't save", "orange"),
                "bookmarked": StatusPill("Bookmarked", "accent"),
                "not_funny": StatusPill("Not funny", "secondary")}.get(status, StatusPill("Not checked", "secondary"))
        right = [pill]
        has_file = status == "saved" and it.get("file") and Path(it["file"]).exists()
        if has_file:
            show = QPushButton("Show in Folder")
            show.setObjectName("small")
            show.setToolTip(it["file"])
            show.clicked.connect(lambda _=False, f=it["file"]: winutil.reveal(f))
            right.append(show)
        elif status == "failed":
            sub += ("\nThe game was running as administrator, which blocks the clip hotkey."
                    if it.get("why") == "admin" else
                    "\nMedal didn't make a clip. Check the hotkey on the Clipping page matches Medal's.")
            test = QPushButton("Test Clip")
            test.setObjectName("small")
            test.setToolTip("Presses Medal's clip hotkey for real and checks the clip turns up")
            test.clicked.connect(self.ctrl.test_clip)
            right.append(test)
        row = Row(title, sub, *right)
        if has_file:                                # the thumbnail is the Play button
            thumb = Thumb(it["file"], row)
            thumb.clicked.connect(lambda f=it["file"]: winutil.open_file(f))
            row.layout().insertWidget(0, thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    # ---------------------------------------------------------- games
    def build_games(self):
        self.games_group.clear()
        if not S["games"]:
            self.games_group.add(Row("No games added", "Play a game, or click Add Game… while it's running."))
        for game in S["games"]:
            rm = QPushButton("Remove")
            rm.setObjectName("small")
            rm.clicked.connect(lambda _=False, exe=game["exe"]: self.remove_game(exe))
            self.games_group.add(Row(game["name"], game["exe"], rm))
        self.games_group.update()
        self.update_game_rows()
        self.tab_only()

    def show_game_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        added = {g["exe"] for g in S["games"]}
        apps = winutil.running_apps()
        for path, title in apps:
            exe = Path(path).name.lower()
            name = winutil.exe_description(path)
            act = menu.addAction(f"{name}  ·  added" if exe in added else name)
            act.setToolTip(title)
            act.setEnabled(exe not in added)
            act.triggered.connect(lambda _=False, path=path: self.add_game(path))
        if not apps:
            menu.addAction("No open apps found").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Browse…", self.browse_game)
        menu.exec(self.add_game_btn.mapToGlobal(self.add_game_btn.rect().bottomLeft()))

    def browse_game(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Game", "", "Programs (*.exe)")
        if path:
            self.add_game(path)

    def add_game(self, path):
        game = {"exe": Path(path).name, "name": winutil.exe_description(path)}
        self.ctrl.set("ignored_games", [e for e in S["ignored_games"] if e != Path(path).name.lower()])
        self.ctrl.set("games", clean_games(S["games"] + [game]))

    def remove_game(self, exe):
        if exe not in S["ignored_games"]:      # so auto-add doesn't put it straight back
            self.ctrl.set("ignored_games", S["ignored_games"] + [exe])
        self.ctrl.set("games", [g for g in S["games"] if g["exe"] != exe])

    def update_game_rows(self):
        on = S["game_only"]
        self.fullscreen_row.setEnabled(on)
        self.autoadd_row.setEnabled(on)
        self.games_group.setEnabled(on)
        self.add_game_btn.setEnabled(on)

    # ---------------------------------------------------------- audio devices
    def build_devices(self, mics, spks, default):
        self.fill_sound_outputs(spks)
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

    def update_sound_opts(self):
        for key, (more, panel, opts) in self.sound_opts.items():
            on = key in S["triggers"]
            more.setEnabled(on)
            if not on and more.isChecked():
                more.setChecked(False)
            opts.refresh()

    def toggle_trigger(self, key, on):
        triggers = [k for k in SOUNDS if (k == key and on) or (k != key and k in S["triggers"])]
        if not triggers:                       # keep at least one sound on
            self.trigger_sw[key].set_silent(True)
            return
        self.ctrl.set("triggers", triggers)

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
        if top == "paused":
            return "Waiting for a game"
        text = f"Hearing: {top}"
        sound, score = eng.sound.get(key, (None, 0.0))
        if sound and score >= 0.05:
            text += f"  ·  {SOUNDS[sound][0].lower()} {score:.2f}"
        return text

    def display_level(self, key):
        """The score for the meter (it does its own smoothing), or None when this source is off."""
        eng = self.ctrl.engine
        lvl = eng.levels.get(key) if key in eng.threads else None
        return lvl if isinstance(lvl, float) else None

    def display_volume(self, key):
        """How loud the source is right now (0-1)."""
        eng = self.ctrl.engine
        return loudness(eng.volume.get(key)) if key in eng.threads else 0.0

    def refresh_live(self):
        if not self.isVisible():
            return
        thr = S["threshold"]
        mic = ("mic", S["mic_device"])
        self.mic_meter.set(self.display_level(mic), thr, self.display_volume(mic))
        self.mic_row.set_sub(self.hearing(mic) or "Off")
        for name, (row, meter, connected) in self.out_rows.items():
            key = ("out", name)
            meter.set(self.display_level(key), thr, self.display_volume(key))
            h = self.hearing(key)
            row.set_sub(h if h else (name if connected else "Not connected"))
        live = list(self.ctrl.engine.threads)
        levels = {k: v for k, v in ((k, self.display_level(k)) for k in live) if v is not None}
        self.live_meter.set(max(levels.values(), default=0.0), thr,
                            max((self.display_volume(k) for k in live), default=0.0))
        self.live_row.set_sub(self.live_text(levels))

    def live_text(self, levels):
        """What the pinned live score says under its name: the source scoring highest, and what it hears."""
        eng = self.ctrl.engine
        if not S["running"]:
            return "Paused"
        if not eng.threads:
            return "Nothing to listen to. Turn on a source on the Audio page."
        if not levels:
            return self.hearing(next(iter(eng.threads))) or "Starting…"
        key = max(levels, key=levels.get)
        who = "Your mic" if key[0] == "mic" else short_name(key[1])
        return f"{self.hearing(key)}  ·  {who}"

    # ---------------------------------------------------------- misc
    def refresh_displays(self):
        self.seg.set_items(self.ctrl.display_names(), S["monitor"] - 1)

    def record_hotkey(self):
        self.keycap.recording = True
        self.keycap.set_label("Press keys…")
        self.ctrl.record_hotkey()

    def update_clip_length(self):
        """Smart length works when Medal has several clip lengths and ours is one of them; otherwise its rows
        are greyed out and say why. Also warn when a moment can last longer than the clip Medal saves."""
        from .app import PRE_ROLL
        clip_keys = getattr(self, "medal_clip_keys", [])
        current = next((n for n, hk in clip_keys if hk == S["hotkey"]), 0)
        usable = len(clip_keys) > 1 and current > 0
        if usable:
            why = "Uses Medal's longer clip hotkeys for long laughs, so the start isn't cut off."
        elif len(clip_keys) == 1:
            why = ("Medal only has one clip length set up. Add more clip hotkeys in Medal's settings "
                   "(like 60 s and 120 s) to use this.")
        elif clip_keys:
            why = "Set your clip hotkey to one of Medal's (above) to use this."
        else:
            why = "Needs Medal's clip hotkeys, which couldn't be read. Set up clip hotkeys in Medal to use this."
        self.smart_row.set_sub(why)
        self.smart_row.setEnabled(usable)
        self.long_row.setEnabled(usable and S["smart_length"])
        if not current:
            self.length_warn.setVisible(False)
            return
        longest = max(n for n, _ in clip_keys) if S["smart_length"] and len(clip_keys) > 1 else current
        if S["auto_clip"] and S["clip_delay"] > 0 and S["max_wait"] == 0:
            self.length_warn.set_sub("“Save anyway after” is set to Never, so a long laugh can run past Medal's "
                                     f"{longest} s clip. Set a time there.")
            self.length_warn.setVisible(True)
            return
        wait = S["max_wait"] if S["auto_clip"] and S["clip_delay"] > 0 else S["popup_seconds"]
        worst = round(PRE_ROLL + S["min_laugh"] + wait)
        first = "Lower “Save anyway after”" if S["auto_clip"] and S["clip_delay"] > 0 else "Click Clip sooner"
        if len(clip_keys) > 1 and not S["smart_length"]:
            fixes = f"{first}, or turn on “Pick the clip length to fit the moment”."
        elif len(clip_keys) > 1:
            fixes = f"{first}, or add a longer clip hotkey in Medal."
        else:
            fixes = f"{first}, or set a longer clip length in Medal."
        self.length_warn.set_sub(f"A moment can last about {worst} s, counting {PRE_ROLL} s before the laugh, "
                                 f"but Medal's clip is {longest} s. {fixes}")
        self.length_warn.setVisible(worst > longest)

    def update_auto_rows(self):
        for row in (self.delay_row, self.maxwait_row):
            row.setEnabled(S["auto_clip"])

    def update_status(self):
        state, game = self.ctrl.status(), self.ctrl.game
        n = len(self.ctrl.engine.wanted())
        self.medal_row.setVisible(not self.ctrl.medal_ok)
        medal = "Medal ready" if self.ctrl.medal_ok else "Medal isn't running"
        sources = f"{n} source{'s' if n != 1 else ''}"
        if state == "listening":
            self.status.setText(f'<span style="color:{T.css("green")}">●</span> Listening')
            title = "Listening for laughs"
            sub = ("  ·  ".join(x for x in (game, sources, medal) if x) if n else
                   "Nothing to listen to. Turn on your mic or some PC audio on the Audio page.")
        elif state == "waiting":
            self.status.setText(f'<span style="color:{T.css("orange")}">●</span> Waiting for a game')
            title, sub = "Waiting for a game", f"It starts listening when a game is in front  ·  {medal}"
        else:
            self.status.setText("Paused")
            title, sub = "Paused", "Turn it on to start listening for laughs."
        if state != "paused" and not self.ctrl.medal_ok:
            self.status.setText(f'<span style="color:{T.css("orange")}">●</span> Medal isn\'t running')
        extra = len(self.health_bad) - ("medal" in self.health_bad)
        if state != "paused" and extra > 0:
            sub += f"  ·  {extra} thing{'s' if extra != 1 else ''} to fix below"
        if self.isVisible() and self.status_row.title.text() not in ("", title):
            self.status_row.flash()                 # listening <-> waiting <-> paused: show that it changed
        self.status_row.title.setText(title)
        self.status_row.set_sub(sub)
        icon = self.ctrl.icon if state == "listening" and not self.health_bad else self.ctrl.tray_icon(
            "paused" if state == "paused" else "warning" if self.health_bad else "waiting")
        self.status_icon.setPixmap(icon.pixmap(44, 44))
        if not S["game_only"]:
            self.game_row.set_sub("Off, so it listens all the time.")
        elif game:
            self.game_row.set_sub(f"{game} is in front, so it's listening.")
        elif self.ctrl.tuning:
            self.game_row.set_sub("No game in front, but it listens while this window is open so you can "
                                  "test it.")
        else:
            self.game_row.set_sub("No game in front, so detection is waiting.")

    def on_setting_changed(self, key):
        if key in self.switches:
            self.switches[key].set_silent(S[key])
        if key in ("medal", "hotkey", "clips_folder", "confirm_clips", "sources", "game", "running", "listen_mic",
                   "outputs"):
            self.build_health()
        if key in ("running", "listen_mic", "outputs", "game_only", "game", "medal"):
            self.update_status()
        if key == "game_only":
            self.update_game_rows()
        if key == "games":
            self.build_games()
        if key == "auto_clip":
            self.update_auto_rows()
        if key in ("hotkey", "smart_length", "clip_delay", "max_wait", "min_laugh", "auto_clip", "popup_seconds"):
            self.update_clip_length()
        if key == "triggers":
            for k, sw in self.trigger_sw.items():
                sw.set_silent(k in S["triggers"])
        if key in ("triggers", "threshold", "sound_levels", "sound_sources", "listen_mic", "outputs"):
            self.update_sound_opts()
        if key in ("clips_folder", "confirm_clips"):
            self.update_folder_row()
        if key == "hotkey":
            self.keycap.recording = False
            self.keycap.set_label(hotkey_label())
            self.update_medal_keys()
        if key == "pause_hotkey":
            self.pause_keycap.recording = False
            self.update_pause_row()
        if key in ("listen_mic", "voice_cancel", "hype_phrases", "phrases_off"):
            self.update_voice_row()
        if key == "pad_button":
            self.update_pad_rows()
        if key in ("min_laugh", "pause_tolerance", "cooldown"):
            self.update_timing_row()
        if key == "pad_mode":
            self.pad_mode.set_items(PAD_MODES, 0 if S["pad_mode"] == "popup" else 1)
        if key in ("bookmark_hotkey", "bookmark_when_recording"):
            self.bm_keycap.recording = False
            self.update_medal_keys()
        if key == "medal":
            self.update_rec_pill()
        if key == "sound_cue":
            self.update_sound_rows()

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
        if self.mica and not winutil.apply_mica(int(self.winId()), T.dark):
            self.mica = False                       # Windows said no: paint the sidebar ourselves again
            for w in (self, self.side):
                w.setProperty("mica", False)
                w.style().unpolish(w)
                w.style().polish(w)

    def showEvent(self, e):
        super().showEvent(e)
        self.live.start()
        self.setFocus(Qt.FocusReason.OtherFocusReason)      # nothing looks focused until Tab is pressed
        self.apply_titlebar()
        self.startup_sw.set_silent(winutil.startup_enabled())
        self.update_medal_keys()          # Medal's hotkeys may have changed while we were hidden

    def hideEvent(self, e):
        super().hideEvent(e)
        self.live.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fade.place()

    def closeEvent(self, e):
        e.ignore()
        self.hide()
