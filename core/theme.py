"""
UI and terminal themes.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtGui import QColor, QPalette

UI_THEMES: dict[str, dict[str, str]] = {
    "Breeze Dark": dict(
        win="#1b1e23",
        panel="#232629",
        panel2="#2a2e32",
        text="#eff0f1",
        sub="#95a5a6",
        accent="#3daee9",
        border="#31363b",
        hover="#2d3238",
        ok="#0dbc79",
        warn="#f39c12",
        danger="#ff5555",
        xterm="dark",
    ),
    "Light": dict(
        win="#e8ebef",
        panel="#f6f8fa",
        panel2="#ffffff",
        text="#1f2430",
        sub="#5a6472",
        accent="#0078d4",
        border="#cdd5dd",
        hover="#eaf1f8",
        ok="#0a7d4f",
        warn="#b06e00",
        danger="#c42b1c",
        xterm="light",
    ),
    "Nord": dict(
        win="#2e3440",
        panel="#3b4252",
        panel2="#434c5e",
        text="#eceff4",
        sub="#a8b3c5",
        accent="#88c0d0",
        border="#4c566a",
        hover="#465062",
        ok="#a3be8c",
        warn="#ebcb8b",
        danger="#bf616a",
        xterm="nord",
    ),
    "Dracula": dict(
        win="#282a36",
        panel="#343746",
        panel2="#3c4053",
        text="#f8f8f2",
        sub="#9aa2c0",
        accent="#bd93f9",
        border="#44475a",
        hover="#44475a",
        ok="#50fa7b",
        warn="#f1fa8c",
        danger="#ff5555",
        xterm="dracula",
    ),
}

TERMINAL_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "background": "#1e1e1e",
        "foreground": "#e0e0e0",
        "cursor": "#ffffff",
        "black": "#000000",
        "red": "#cd3131",
        "green": "#0dbc79",
        "yellow": "#e5e510",
        "blue": "#2472c8",
        "magenta": "#bc3fbc",
        "cyan": "#11a8cd",
        "white": "#e5e5e5",
        "brightBlack": "#666666",
        "brightRed": "#f14c4c",
        "brightGreen": "#23d18b",
        "brightYellow": "#f5f543",
        "brightBlue": "#3b8eea",
        "brightMagenta": "#d670d6",
        "brightCyan": "#29b8db",
        "brightWhite": "#ffffff",
    },
    "light": {
        "background": "#ffffff",
        "foreground": "#1f2430",
        "cursor": "#1f2430",
        "black": "#000000",
        "red": "#c42b1c",
        "green": "#0a7d4f",
        "yellow": "#b06e00",
        "blue": "#0078d4",
        "magenta": "#881798",
        "cyan": "#005e6e",
        "white": "#8a8a8a",
        "brightBlack": "#666666",
        "brightRed": "#e43b44",
        "brightGreen": "#0f9d58",
        "brightYellow": "#f4b400",
        "brightBlue": "#4285f4",
        "brightMagenta": "#a142f4",
        "brightCyan": "#24c1e0",
        "brightWhite": "#1f2430",
    },
    "nord": {
        "background": "#2e3440",
        "foreground": "#d8dee9",
        "cursor": "#d8dee9",
        "black": "#3b4252",
        "red": "#bf616a",
        "green": "#a3be8c",
        "yellow": "#ebcb8b",
        "blue": "#81a1c1",
        "magenta": "#b48ead",
        "cyan": "#88c0d0",
        "white": "#e5e9f0",
        "brightBlack": "#4c566a",
        "brightRed": "#bf616a",
        "brightGreen": "#a3be8c",
        "brightYellow": "#ebcb8b",
        "brightBlue": "#81a1c1",
        "brightMagenta": "#b48ead",
        "brightCyan": "#8fbcbb",
        "brightWhite": "#eceff4",
    },
    "dracula": {
        "background": "#282a36",
        "foreground": "#f8f8f2",
        "cursor": "#f8f8f0",
        "black": "#21222c",
        "red": "#ff5555",
        "green": "#50fa7b",
        "yellow": "#f1fa8c",
        "blue": "#bd93f9",
        "magenta": "#ff79c6",
        "cyan": "#8be9fd",
        "white": "#f8f8f2",
        "brightBlack": "#6272a4",
        "brightRed": "#ff6e6e",
        "brightGreen": "#69ff94",
        "brightYellow": "#ffffa5",
        "brightBlue": "#d6acff",
        "brightMagenta": "#ff92df",
        "brightCyan": "#a4ffff",
        "brightWhite": "#ffffff",
    },
}

QSS_TEMPLATE = """
QMainWindow, QDialog { background:$WIN; }
QWidget {
    color:$TEXT;
    font-family:'Segoe UI',Helvetica,Arial,sans-serif;
    font-size:13px;
}
QStatusBar {
    background:$PANEL;
    color:$SUB;
    border-top:1px solid $BORDER;
}
QTabWidget::pane {
    border:1px solid $BORDER;
    background:$PANEL2;
}
QTabBar::tab {
    background:$PANEL;
    border:1px solid $BORDER;
    padding:7px 14px;
    border-top-left-radius:5px;
    border-top-right-radius:5px;
    margin-right:2px;
}
QTabBar::tab:selected {
    background:$PANEL2;
    border-bottom-color:$PANEL2;
    color:$ACCENT;
    font-weight:bold;
}
QTabBar::tab:hover:!selected {
    background:$HOVER;
}
QHeaderView::section {
    background:$PANEL2;
    color:$TEXT;
    border:1px solid $BORDER;
    padding:4px;
    font-weight:bold;
}
QTableView,
QTreeWidget,
QListWidget,
QPlainTextEdit,
QTextEdit,
QLineEdit,
QSpinBox,
QComboBox {
    background:$PANEL;
    border:1px solid $BORDER;
    border-radius:3px;
    selection-background-color:$ACCENT;
    selection-color:white;
}
QTableView {
    gridline-color:$BORDER;
    alternate-background-color:$PANEL2;
}
QTreeWidget::item,
QListWidget::item {
    padding:4px;
    border-radius:3px;
}
QTreeWidget::item:hover,
QListWidget::item:hover {
    background:$HOVER;
}
QTreeWidget::item:selected,
QListWidget::item:selected {
    background:$ACCENT;
    color:white;
}
QPushButton {
    padding:5px 12px;
    border-radius:4px;
    background:$PANEL2;
    border:1px solid $BORDER;
}
QPushButton:hover {
    background:$HOVER;
    border-color:$ACCENT;
}
QPushButton:pressed {
    background:$ACCENT;
    color:white;
}
QPushButton:disabled {
    color:$SUB;
    background:$PANEL;
}
QToolButton {
    padding:5px;
    border-radius:4px;
}
QToolButton:hover {
    background:$HOVER;
}
QSplitter::handle {
    background:$BORDER;
}
QSplitter::handle:horizontal {
    width:2px;
}
QSplitter::handle:vertical {
    height:2px;
}
QMenu {
    background:$PANEL2;
    border:1px solid $BORDER;
    padding:4px;
}
QMenu::item {
    padding:6px 24px;
    border-radius:3px;
}
QMenu::item:selected {
    background:$ACCENT;
    color:white;
}
QScrollBar:vertical {
    background:$PANEL;
    width:10px;
}
QScrollBar:horizontal {
    background:$PANEL;
    height:10px;
}
QScrollBar::handle {
    background:$BORDER;
    border-radius:5px;
    min-height:24px;
}
QScrollBar::handle:hover {
    background:$ACCENT;
}
QScrollBar::add-line,
QScrollBar::sub-line {
    height:0;
    width:0;
}
QProgressBar {
    border:1px solid $BORDER;
    border-radius:4px;
    background:$PANEL;
    text-align:center;
}
QProgressBar::chunk {
    background:$ACCENT;
    border-radius:3px;
}
QComboBox QAbstractItemView {
    background:$PANEL2;
    border:1px solid $BORDER;
}
QToolTip {
    background:$PANEL2;
    color:$TEXT;
    border:1px solid $ACCENT;
    padding:4px;
}
"""


class ThemeManager:
    """
    Applies UI themes and exposes terminal themes.
    """

    def __init__(self):
        self.themes = UI_THEMES
        self.terminal_themes = TERMINAL_THEMES

        self.current_name = "Breeze Dark"
        self.current: dict[str, str] = dict(UI_THEMES[self.current_name])

    def get_theme(self, name: str) -> dict[str, str]:
        """
        Get UI theme dict by name.
        """
        return self.themes.get(name, self.themes["Breeze Dark"])

    def get_terminal_theme(self, name: str) -> dict[str, str]:
        """
        Get terminal theme dict by name.
        """
        return self.terminal_themes.get(name, self.terminal_themes["dark"])

    def apply(self, app: Any, name: str) -> None:
        """
        Apply theme to QApplication.
        """
        theme = self.get_theme(name)

        self.current_name = name
        self.current = theme

        qss = QSS_TEMPLATE

        for key, value in sorted(theme.items(), key=lambda kv: len(kv[0]), reverse=True):
            if key == "xterm":
                continue

            qss = qss.replace("$" + key.upper(), value)

        app.setStyleSheet(qss)

        palette = app.palette()

        palette.setColor(QPalette.ColorRole.Window, QColor(theme["win"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(theme["panel"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(theme["text"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(theme["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(theme["panel2"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(theme["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

        app.setPalette(palette)
