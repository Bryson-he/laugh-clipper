"""macOS-style colour palettes that follow the Windows light/dark setting."""

from PySide6.QtCore import QObject, Qt, Signal, qVersion
from PySide6.QtGui import QColor, QFont, QFontDatabase

DARK = {
    "window": "#1e1e1e", "sidebar": "#262628", "group": "#2c2c2e", "group_border": (255, 255, 255, 14),
    "separator": "#38383a", "text": "#f5f5f7", "secondary": "#98989d", "tertiary": "#636366",
    "control": "#3a3a3c", "control_hover": "#444446", "control_press": "#4f4f52",
    "accent": "#0a84ff", "accent_hover": "#3395ff", "green": "#30d158", "red": "#ff453a", "orange": "#ff9f0a",
    "track": "#48484a", "thumb": "#636366", "knob": "#ffffff", "keycap_shadow": "#141416",
    "hover": (255, 255, 255, 16), "menu": "#2c2c2e", "scroll": (255, 255, 255, 55),
    "meter_bg": (255, 255, 255, 28), "meter_idle": "#8e8e93",
    "wall1": "#2b4fd6", "wall2": "#9b3fc6", "bezel": "#0b0b0d",
}
LIGHT = {
    "window": "#f2f2f7", "sidebar": "#e8e8ed", "group": "#ffffff", "group_border": (0, 0, 0, 14),
    "separator": "#e3e3e8", "text": "#1d1d1f", "secondary": "#6e6e73", "tertiary": "#aeaeb2",
    "control": "#e8e8ed", "control_hover": "#dedee3", "control_press": "#d1d1d6",
    "accent": "#007aff", "accent_hover": "#1a88ff", "green": "#34c759", "red": "#ff3b30", "orange": "#ff9500",
    "track": "#e3e3e8", "thumb": "#ffffff", "knob": "#ffffff", "keycap_shadow": "#c4c4ca",
    "hover": (0, 0, 0, 12), "menu": "#ffffff", "scroll": (0, 0, 0, 60),
    "meter_bg": (0, 0, 0, 20), "meter_idle": "#aeaeb2",
    "wall1": "#4f86ff", "wall2": "#b56be0", "bezel": "#1d1d1f",
}
WEIGHTS = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold, 700: QFont.Weight.Bold}


def _first(options, available, fallback):
    return next((f for f in options if f in available), fallback)


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.dark = True
        self.p = DARK
        self.family = self.display = "Segoe UI"
        self.glyphs = None

    def init(self, app):
        fams = set(QFontDatabase.families())
        # Segoe UI Variable only renders its weights properly with Qt's DirectWrite backend (6.8+)
        modern = tuple(int(x) for x in qVersion().split(".")[:2]) >= (6, 8)
        text = (["Segoe UI Variable Text"] if modern else []) + ["Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue"]
        disp = (["Segoe UI Variable Display"] if modern else []) + ["Segoe UI", "SF Pro Display", "Inter"]
        self.family = _first(text, fams, app.font().family())
        self.display = _first(disp, fams, self.family)
        self.glyphs = _first(["Segoe Fluent Icons", "Segoe MDL2 Assets"], fams, None)
        try:
            app.styleHints().colorSchemeChanged.connect(lambda *_: self.refresh(app))
        except AttributeError:
            pass
        self.refresh(app)

    def refresh(self, app):
        try:
            self.dark = app.styleHints().colorScheme() != Qt.ColorScheme.Light
        except AttributeError:
            self.dark = True
        self.p = DARK if self.dark else LIGHT
        app.setFont(self.font(13))
        app.setStyleSheet(self.stylesheet())
        self.changed.emit()
        for w in app.allWidgets():
            w.update()

    def c(self, key):
        v = self.p[key]
        return QColor(*v) if isinstance(v, tuple) else QColor(v)

    def css(self, key):
        c = self.c(key)
        return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"

    def font(self, px, weight=400, display=False):
        f = QFont(self.display if display else self.family)
        f.setPixelSize(max(1, int(round(px))))
        f.setWeight(WEIGHTS.get(weight, QFont.Weight.Normal))
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return f

    def stylesheet(self):
        c = self.css
        return f"""
        QWidget {{ color: {c('text')}; font-family: "{self.family}"; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel:disabled {{ color: {c('tertiary')}; }}
        #root, #content {{ background: {c('window')}; }}
        #sidebar {{ background: {c('sidebar')}; border-right: 1px solid {c('separator')}; }}
        QLabel#pageTitle {{ font-family: "{self.display}"; font-size: 24px; font-weight: 700; }}
        QLabel#appName {{ font-family: "{self.display}"; font-size: 15px; font-weight: 600; }}
        QLabel#secondary, QLabel#footer, QLabel#value {{ color: {c('secondary')}; font-size: 12px; }}
        QLabel#secondary:disabled {{ color: {c('tertiary')}; }}
        QLabel#header {{ color: {c('secondary')}; font-size: 12px; font-weight: 600; }}
        QPushButton {{ background: {c('control')}; color: {c('text')}; border: none; border-radius: 7px;
                       padding: 5px 14px; min-height: 18px; }}
        QPushButton:hover {{ background: {c('control_hover')}; }}
        QPushButton:pressed {{ background: {c('control_press')}; }}
        QPushButton:disabled {{ color: {c('tertiary')}; }}
        QPushButton#prominent {{ background: {c('accent')}; color: white; font-weight: 600; }}
        QPushButton#prominent:hover {{ background: {c('accent_hover')}; }}
        QPushButton#prominent:disabled {{ background: {c('control')}; color: {c('tertiary')}; }}
        QPushButton#small {{ padding: 2px 10px; font-size: 12px; min-height: 16px; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {c('scroll')}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QComboBox QAbstractItemView {{ background: {c('menu')}; color: {c('text')}; border: 1px solid {c('separator')};
                                       padding: 4px; outline: 0; selection-background-color: {c('accent')};
                                       selection-color: white; }}
        QMenu {{ background: {c('menu')}; border: 1px solid {c('separator')}; border-radius: 8px; padding: 5px; }}
        QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {c('accent')}; color: white; }}
        QMenu::separator {{ height: 1px; background: {c('separator')}; margin: 4px 8px; }}
        QToolTip {{ background: {c('menu')}; color: {c('text')}; border: 1px solid {c('separator')}; padding: 4px 8px; }}
        """


T = Theme()
