"""Opening the settings window or the setup guide mustn't flash any stray little windows."""

from PySide6.QtWidgets import QWidget


def stray_windows_while(monkeypatch, qapp, build):
    """Build and show a window; return the names of any *other* widgets that got shown on their own."""
    shown = []
    real = QWidget.setVisible

    def watch(self, visible):
        if visible and self.parentWidget() is None:
            shown.append(type(self).__name__)
        return real(self, visible)
    monkeypatch.setattr(QWidget, "setVisible", watch)
    w = build()
    w.show()
    qapp.processEvents()
    w.hide()
    return [name for name in shown if name != type(w).__name__]


def test_settings_window_opens_cleanly(ctrl, qapp, monkeypatch):
    from laughclipper.settings_window import SettingsWindow
    assert stray_windows_while(monkeypatch, qapp, lambda: SettingsWindow(ctrl)) == []


def test_setup_guide_opens_cleanly(ctrl, qapp, monkeypatch):
    from laughclipper.setup_guide import SetupGuide
    assert stray_windows_while(monkeypatch, qapp, lambda: SetupGuide(ctrl)) == []
