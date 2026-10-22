"""The settings window, laid out like macOS System Settings."""

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import clips, medal, sounds, winutil
from .config import DEFAULTS, S, clean_games, hotkey_label
from .engine import SOUNDS, short_name, sound_level
from .theme import T
from .version import VERSION
from .widgets import (Chip, Collapsible, Disclosure, Group, KeyCap, LevelMeter, NavItem, PopupButton,
                      PositionPicker, Row, Segmented, Slider, StatusPill, Switch, WarningBadge, loudness)

PAGES = [
    ("General", "", "#8e8e93"),
    ("Audio", "", "#ff2d55"),
    ("Notification", "", "#ff3b30"),
    ("Clipping", "", "#5e5ce6"),
    ("Detection", "", "#34c759"),
    ("History", "", "#ff9500"),
]


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
        self.switches = {}
        self.sliders = {}
        self.smooth = {}
        self.vsmooth = {}
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
                    self.page_detection, self.page_history]
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
        ctrl.history_changed.connect(self.build_history)
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
        open_medal = QPushButton("Open Medal")
        open_medal.setObjectName("prominent")
        open_medal.clicked.connect(self.ctrl.open_medal)
        self.medal_row = g.add(Row("Medal isn't running", "Clips can't be saved until Medal is open.", open_medal))
        self.medal_row.layout().insertWidget(0, WarningBadge(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.medal_row.setVisible(not self.ctrl.medal_ok)
        self.run_row = g.add(Row("Laugh detection", "", self.switch("running")))
        g = p.section("Games")
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
        g = p.section("Trigger on", footer="Laughter is the most reliable. Shouting and screaming can also "
                                           "pick up loud game audio. Use the arrow to give a sound its own "
                                           "trigger level, or to only listen for it on your mic.")
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
        g = p.section(footer="Watch the live score while you laugh, then set the trigger just below where "
                             "it usually peaks.")
        self.live_meter = LevelMeter(210)
        g.add(Row("Live score", "Grey shows how loud it is. The bar on top is the score for the sounds above, "
                                "green once it passes the trigger level.", self.live_meter))
        self.slider_row(g, "Trigger level", "Lower catches more, higher means fewer false alarms.",
                        "threshold", 0.05, 0.9, 0.01, lambda v: f"{v:.2f}")
        self.slider_row(g, "Length needed", "How long the laugh (or cheer…) has to last before it counts.",
                        "min_laugh", 0.5, 3, 0.25, secs)
        self.slider_row(g, "Pause tolerance",
                        "Breaths and short pauses up to this long still count as the same laugh.",
                        "pause_tolerance", 0.5, 4, 0.25, secs)
        self.slider_row(g, "Wait between prompts", "Stops it asking again straight away.",
                        "cooldown", 0, 120, 5, secs)
        return p.finish()

    def page_history(self):
        p = Page("History")
        clear = QPushButton("Clear")
        clear.setObjectName("small")
        clear.clicked.connect(self.clear_history)
        self.history_group = p.section("Recent clips", header_widget=clear,
                                       footer="Clips that Laugh Clipper saved for you, newest first. Clearing the "
                                              "list doesn't delete any clips from Medal.")
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
        g = self.history_group
        g.clear()
        items = list(reversed(self.ctrl.history))[:100]
        if not items:
            g.add(Row("No clips yet", "Clips Laugh Clipper saves for you will show up here."))
        for it in items:
            name = SOUNDS.get(it.get("sound"), ("Laughter",))[0]
            title = f"{name}  ·  {it['game']}" if it.get("game") else name
            score = it.get("score") or 0
            sub = "  ·  ".join(x for x in (when(it["time"]), it.get("source", ""),
                                           f"score {score:.2f}" if score else "", "auto-saved" if it.get("auto") else "")
                               if x)
            status = it.get("status")
            pill = {"saved": StatusPill("Saved", "green"), "failed": StatusPill("Didn't save", "orange"),
                    "bookmarked": StatusPill("Bookmarked", "accent")}.get(status, StatusPill("Not checked",
                                                                                              "secondary"))
            right = [pill]
            if status == "saved" and it.get("file") and Path(it["file"]).exists():
                show = QPushButton("Show")
                show.setObjectName("small")
                show.setToolTip(it["file"])
                show.clicked.connect(lambda _=False, f=it["file"]: winutil.reveal(f))
                right.append(show)
            g.add(Row(title, sub, *right))
        g.update()

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
        eng = self.ctrl.engine
        lvl = eng.levels.get(key) if key in eng.threads else None
        if isinstance(lvl, float):
            v = max(lvl, self.smooth.get(key, 0.0) * 0.86)
            self.smooth[key] = v
            return v
        return None

    def display_volume(self, key):
        """How loud the source is (0-1), falling back gently so it doesn't flicker."""
        eng = self.ctrl.engine
        info = eng.heard.get(key) if key in eng.threads else None
        v = max(loudness(info[1]) if info else 0.0, self.vsmooth.get(key, 0.0) * 0.8)
        self.vsmooth[key] = v
        return v

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
        live = self.ctrl.engine.threads
        levels = [v for k, v in self.smooth.items() if k in live]
        volumes = [v for k, v in self.vsmooth.items() if k in live]
        self.live_meter.set(max(levels, default=0.0), thr, max(volumes, default=0.0))

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
        state, game = self.ctrl.status(), self.ctrl.game
        n = len(self.ctrl.engine.wanted())
        self.medal_row.setVisible(not self.ctrl.medal_ok)
        if state == "listening":
            self.status.setText(f'<span style="color:{T.css("green")}">●</span> Listening')
            self.run_row.set_sub(f"Listening to {n} source{'s' if n != 1 else ''}." if n else
                                 "Nothing selected. Pick a mic or PC audio in Audio.")
        elif state == "waiting":
            self.status.setText(f'<span style="color:{T.css("orange")}">●</span> Waiting for a game')
            self.run_row.set_sub("Waiting for a game. Detection starts when one is the window in front.")
        else:
            self.status.setText("Paused")
            self.run_row.set_sub("Paused. Turn on to start listening for laughs.")
        if state != "paused" and not self.ctrl.medal_ok:
            self.status.setText(f'<span style="color:{T.css("orange")}">●</span> Medal isn\'t running')
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
        if key in ("running", "listen_mic", "outputs", "game_only", "game", "medal"):
            self.update_status()
        if key == "game_only":
            self.update_game_rows()
        if key == "games":
            self.build_games()
        if key == "auto_clip":
            self.update_auto_rows()
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

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_titlebar()
        self.startup_sw.set_silent(winutil.startup_enabled())
        self.update_medal_keys()          # Medal's hotkeys may have changed while we were hidden

    def closeEvent(self, e):
        e.ignore()
        self.hide()
