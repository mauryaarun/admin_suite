"""
UI and terminal themes.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette

from admin_suite.core.fonts import qss_font_family_ui

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
    "one_dark": {
        "background": "#282c34",
        "foreground": "#abb2bf",
        "cursor": "#528bff",
        "black": "#1e2127",
        "red": "#e06c75",
        "green": "#98c379",
        "yellow": "#e5c07b",
        "blue": "#61afef",
        "magenta": "#c678dd",
        "cyan": "#56b6c2",
        "white": "#abb2bf",
        "brightBlack": "#5c6370",
        "brightRed": "#e06c75",
        "brightGreen": "#98c379",
        "brightYellow": "#e5c07b",
        "brightBlue": "#61afef",
        "brightMagenta": "#c678dd",
        "brightCyan": "#56b6c2",
        "brightWhite": "#ffffff",
    },
    "tokyo_night": {
        "background": "#1a1b26",
        "foreground": "#c0caf5",
        "cursor": "#c0caf5",
        "black": "#15161e",
        "red": "#f7768e",
        "green": "#9ece6a",
        "yellow": "#e0af68",
        "blue": "#7aa2f7",
        "magenta": "#bb9af7",
        "cyan": "#7dcfff",
        "white": "#a9b1d6",
        "brightBlack": "#414868",
        "brightRed": "#f7768e",
        "brightGreen": "#9ece6a",
        "brightYellow": "#e0af68",
        "brightBlue": "#7aa2f7",
        "brightMagenta": "#bb9af7",
        "brightCyan": "#7dcfff",
        "brightWhite": "#c0caf5",
    },
    "solarized_dark": {
        "background": "#002b36",
        "foreground": "#839496",
        "cursor": "#93a1a1",
        "black": "#073642",
        "red": "#dc322f",
        "green": "#859900",
        "yellow": "#b58900",
        "blue": "#268bd2",
        "magenta": "#d33682",
        "cyan": "#2aa198",
        "white": "#eee8d5",
        "brightBlack": "#586e75",
        "brightRed": "#cb4b16",
        "brightGreen": "#859900",
        "brightYellow": "#b58900",
        "brightBlue": "#268bd2",
        "brightMagenta": "#6c71c4",
        "brightCyan": "#2aa198",
        "brightWhite": "#fdf6e3",
    },
    "solarized_light": {
        "background": "#fdf6e3",
        "foreground": "#586e75",
        "cursor": "#657b83",
        "black": "#073642",
        "red": "#dc322f",
        "green": "#859900",
        "yellow": "#b58900",
        "blue": "#268bd2",
        "magenta": "#d33682",
        "cyan": "#2aa198",
        "white": "#eee8d5",
        "brightBlack": "#657b83",
        "brightRed": "#cb4b16",
        "brightGreen": "#859900",
        "brightYellow": "#b58900",
        "brightBlue": "#268bd2",
        "brightMagenta": "#6c71c4",
        "brightCyan": "#2aa198",
        "brightWhite": "#002b36",
    },
    "gruvbox_dark": {
        "background": "#282828",
        "foreground": "#ebdbb2",
        "cursor": "#ebdbb2",
        "black": "#282828",
        "red": "#cc241d",
        "green": "#98971a",
        "yellow": "#d79921",
        "blue": "#458588",
        "magenta": "#b16286",
        "cyan": "#689d6a",
        "white": "#a89984",
        "brightBlack": "#928374",
        "brightRed": "#fb4934",
        "brightGreen": "#b8bb26",
        "brightYellow": "#fabd2f",
        "brightBlue": "#83a598",
        "brightMagenta": "#d3869b",
        "brightCyan": "#8ec07c",
        "brightWhite": "#ebdbb2",
    },
    "catppuccin_mocha": {
        "background": "#1e1e2e",
        "foreground": "#cdd6f4",
        "cursor": "#f5e0dc",
        "black": "#45475a",
        "red": "#f38ba8",
        "green": "#a6e3a1",
        "yellow": "#f9e2af",
        "blue": "#89b4fa",
        "magenta": "#f5c2e7",
        "cyan": "#94e2d5",
        "white": "#bac2de",
        "brightBlack": "#585b70",
        "brightRed": "#f38ba8",
        "brightGreen": "#a6e3a1",
        "brightYellow": "#f9e2af",
        "brightBlue": "#89b4fa",
        "brightMagenta": "#f5c2e7",
        "brightCyan": "#94e2d5",
        "brightWhite": "#a6adc8",
    },
    "monokai": {
        "background": "#272822",
        "foreground": "#f8f8f2",
        "cursor": "#f8f8f0",
        "black": "#272822",
        "red": "#f92672",
        "green": "#a6e22e",
        "yellow": "#f4bf75",
        "blue": "#66d9ef",
        "magenta": "#ae81ff",
        "cyan": "#a1efe4",
        "white": "#f8f8f2",
        "brightBlack": "#75715e",
        "brightRed": "#f92672",
        "brightGreen": "#a6e22e",
        "brightYellow": "#f4bf75",
        "brightBlue": "#66d9ef",
        "brightMagenta": "#ae81ff",
        "brightCyan": "#a1efe4",
        "brightWhite": "#f9f8f5",
    },
    "green_phosphor": {
        "background": "#0a140d",
        "foreground": "#7cf29c",
        "cursor": "#7cf29c",
        "black": "#071009",
        "red": "#ff6b6b",
        "green": "#4be37a",
        "yellow": "#d2f56b",
        "blue": "#4ecdc4",
        "magenta": "#95e8a3",
        "cyan": "#5cf2c7",
        "white": "#b6f7c8",
        "brightBlack": "#2f573a",
        "brightRed": "#ff8787",
        "brightGreen": "#80ffaa",
        "brightYellow": "#e5ff8a",
        "brightBlue": "#7ce8df",
        "brightMagenta": "#b5f7c2",
        "brightCyan": "#8affdc",
        "brightWhite": "#e2ffea",
    },
}

TERMINAL_THEME_LABELS: dict[str, str] = {
    "dark": "Midnight Dark",
    "light": "Clean Light",
    "nord": "Nord",
    "dracula": "Dracula",
    "one_dark": "One Dark",
    "tokyo_night": "Tokyo Night",
    "solarized_dark": "Solarized Dark",
    "solarized_light": "Solarized Light",
    "gruvbox_dark": "Gruvbox Dark",
    "catppuccin_mocha": "Catppuccin Mocha",
    "monokai": "Monokai",
    "green_phosphor": "Retro Phosphor",
}


def is_system_dark() -> bool:
    """Detect whether system/desktop environment is currently using a dark theme."""
    app = QGuiApplication.instance()
    if app is not None:
        hints = app.styleHints()
        color_scheme = getattr(hints, "colorScheme", lambda: None)()
        if color_scheme is not None:
            try:
                return color_scheme == Qt.ColorScheme.Dark
            except Exception:
                pass
        win_color = app.palette().color(QPalette.ColorRole.Window)
        return win_color.lightness() < 128
    return False


def get_system_ui_theme() -> dict[str, str]:
    """
    Extract dynamic system theme colors from Qt's native palette and OS appearance hints.
    Automatically supports both Light and Dark OS appearances, matching system fonts and colors.
    """
    app = QGuiApplication.instance()
    is_dark = is_system_dark()

    if app is not None:
        pal = app.palette()
        win = pal.color(QPalette.ColorRole.Window).name()
        panel = pal.color(QPalette.ColorRole.Base).name()
        panel2 = pal.color(QPalette.ColorRole.Button).name()
        text = pal.color(QPalette.ColorRole.WindowText).name()
        accent = pal.color(QPalette.ColorRole.Highlight).name()

        # Resolve placeholder/dim text color
        sub_color = pal.color(QPalette.ColorRole.PlaceholderText) if hasattr(QPalette.ColorRole, "PlaceholderText") else None
        if sub_color and sub_color.isValid():
            sub = sub_color.name()
        else:
            sub = "#95a5a6" if is_dark else "#5a6472"

        # Adaptive borders and hover colors derived from native window/button
        win_qcolor = pal.color(QPalette.ColorRole.Window)
        if is_dark:
            border = win_qcolor.lighter(135).name()
            hover = win_qcolor.lighter(120).name()
            ok = "#0dbc79"
            warn = "#f39c12"
            danger = "#ff5555"
            xterm = "dark"
        else:
            border = win_qcolor.darker(120).name()
            hover = win_qcolor.darker(108).name()
            ok = "#0a7d4f"
            warn = "#b06e00"
            danger = "#c42b1c"
            xterm = "light"

        return dict(
            win=win,
            panel=panel,
            panel2=panel2,
            text=text,
            sub=sub,
            accent=accent,
            border=border,
            hover=hover,
            ok=ok,
            warn=warn,
            danger=danger,
            xterm=xterm,
        )

    return dict(
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
    )


QSS_TEMPLATE = """
QMainWindow, QDialog { background:$WIN; }
QWidget {
    color:$TEXT;
    font-family:$FONT_UI;
    $FONT_SIZE_CSS
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
QPushButton#IconToolBtn {
    padding:4px 6px;
    border-radius:6px;
    background:$PANEL2;
    border:1px solid $BORDER;
}
QPushButton#IconToolBtn:hover {
    background:$HOVER;
    border-color:$ACCENT;
}
QPushButton#IconToolBtn:pressed,
QPushButton#IconToolBtn:checked {
    background:$ACCENT;
    border-color:$ACCENT;
    color:white;
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
QSplitter::handle:hover {
    background:$ACCENT;
}
QSplitter::handle:horizontal {
    width:5px;
}
QSplitter::handle:vertical {
    height:6px;
}
QMenuBar {
    background:$PANEL;
    color:$TEXT;
    border-bottom:1px solid $BORDER;
    padding:2px 4px;
}
QMenuBar::item {
    background:transparent;
    color:$TEXT;
    padding:4px 10px;
    border-radius:4px;
}
QMenuBar::item:selected {
    background:$HOVER;
    color:$ACCENT;
}
QMenuBar::item:pressed {
    background:$ACCENT;
    color:white;
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
    Defaults to native system theme (auto Light/Dark).
    """

    def __init__(self):
        self.themes = {"System": get_system_ui_theme(), **UI_THEMES}
        self.terminal_themes = TERMINAL_THEMES
        self.terminal_theme_labels = TERMINAL_THEME_LABELS

        self.current_name = "System"
        self.current: dict[str, str] = self.get_theme(self.current_name)
        self._connected_scheme_listener = False

    @property
    def name(self) -> str:
        """
        Current theme name.
        """
        return self.current_name

    def get_theme(self, name: str) -> dict[str, str]:
        """
        Get UI theme dict by name. Defaults to dynamic system theme.
        """
        if name == "System" or not name:
            return get_system_ui_theme()
        return self.themes.get(name, get_system_ui_theme())

    def get_terminal_theme(self, name: str) -> dict[str, str]:
        """
        Get terminal theme dict by name.
        """
        return self.terminal_themes.get(name, self.terminal_themes["dark"])

    def terminal_theme_items(self) -> list[tuple[str, str]]:
        """
        Return ordered (theme_key, display_label) pairs for terminal theme pickers.
        """
        return [
            (key, self.terminal_theme_labels.get(key, key.replace("_", " ").title()))
            for key in self.terminal_themes.keys()
        ]

    def apply(self, app: Any, name: str) -> None:
        """
        Apply theme to QApplication. When name is 'System', uses native OS theme,
        colors, and font size.
        """
        if not name:
            name = "System"

        theme = self.get_theme(name)

        self.current_name = name
        self.current = theme

        font_size_css = "font-size:13px;" if name != "System" else ""
        qss = QSS_TEMPLATE.replace("$FONT_UI", qss_font_family_ui())
        qss = qss.replace("$FONT_SIZE_CSS", font_size_css)

        for key, value in sorted(theme.items(), key=lambda kv: len(kv[0]), reverse=True):
            if key == "xterm":
                continue

            qss = qss.replace("$" + key.upper(), value)

        app.setStyleSheet(qss)

        if name != "System":
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
        else:
            # When System theme is applied, listen for live OS light/dark changes
            if hasattr(app, "styleHints") and not self._connected_scheme_listener:
                try:
                    hints = app.styleHints()
                    if hasattr(hints, "colorSchemeChanged"):
                        hints.colorSchemeChanged.connect(lambda _: self.apply(app, "System"))
                        self._connected_scheme_listener = True
                except Exception:
                    pass
