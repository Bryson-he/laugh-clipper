"""'What's new': the release notes for the version you're running, shown once after an update and from the
General page. The notes are bundled with the app (release-notes/vX.Y.Z.md), so this works offline."""

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import config, winutil
from .theme import T


def notes(version):
    """The release notes for this version as Markdown, or "" if there aren't any (dev builds)."""
    try:
        text = config.resource(f"release-notes/v{version}.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    text = re.sub(r"^## What's new\s*\n", "", text.strip())          # the window has its own title
    return re.sub(r"^## ", "### ", text, flags=re.M)                  # so the other headings sit under it


def to_html(markdown):
    """Our release notes are simple Markdown (### headings, - bullets, **bold**, `code`); turn them into
    HTML with a bit of breathing room, which QLabel's own Markdown rendering doesn't give."""
    out, items, para = [], [], []

    def inline(text):
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", text)
        return re.sub(r"`(.+?)`", lambda m: f"<code>{m.group(1)}</code>", text)

    def flush():
        if items:
            out.append("<ul style='margin: 0 0 4px -18px;'>" + "".join(
                f"<li style='margin-bottom: 6px;'>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        if para:
            out.append(f"<p style='margin: 8px 0;'>{inline(' '.join(para))}</p>")
            para.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            out.append(f"<p style='font-size: 15px; font-weight: 600; margin: 16px 0 6px 0;'>"
                       f"{inline(stripped.lstrip('#').strip())}</p>")
        elif stripped.startswith("- "):
            if para:
                flush()
            items.append(stripped[2:])
        elif stripped and items and line.startswith(" "):
            items[-1] += " " + stripped                  # a bullet that wraps onto the next line
        elif stripped:
            if items:
                flush()
            para.append(stripped)
        else:
            flush()
    flush()
    return "".join(out)


class WhatsNewWindow(QWidget):
    done = Signal()

    def __init__(self, ctrl, version):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(f"What's New in Laugh Clipper {version}")
        self.setWindowIcon(ctrl.icon)
        self.resize(560, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("content")
        il = QVBoxLayout(inner)
        il.setContentsMargins(34, 28, 34, 16)
        il.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        icon = QLabel(inner)
        icon.setPixmap(ctrl.icon.pixmap(56, 56))
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("What's new", inner)
        t.setObjectName("pageTitle")
        sub = QLabel(f"Laugh Clipper {version}", inner)
        sub.setObjectName("secondary")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        il.addLayout(head)
        il.addSpacing(6)
        body = QLabel(inner)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        body.setOpenExternalLinks(True)
        body.setText(to_html(notes(version)))
        il.addWidget(body)
        il.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        bar = QWidget()
        bar.setObjectName("content")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(34, 10, 34, 20)
        bl.addStretch(1)
        ok = QPushButton("Got It")
        ok.setObjectName("prominent")
        ok.setDefault(True)
        ok.clicked.connect(self.close)
        bl.addWidget(ok)
        lay.addWidget(bar)
        T.changed.connect(self.apply_titlebar)

    def apply_titlebar(self):
        winutil.style_titlebar(int(self.winId()), T.dark, T.p["window"], T.p["text"])

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_titlebar()

    def closeEvent(self, e):
        self.done.emit()
        super().closeEvent(e)
