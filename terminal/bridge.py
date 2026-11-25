"""
Qt WebChannel bridge between Python and xterm.js.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebChannel import QWebChannel

    WEBENGINE_AVAILABLE = True

except ImportError:
    QWebEngineView = None
    QWebChannel = None

    WEBENGINE_AVAILABLE = False


class TerminalBridge(QObject):
    """
    Bridge object exposed to JavaScript as:

        channel.objects.bridge
    """

    output_ready = pyqtSignal(str)
    clear_terminal = pyqtSignal()

    set_font_size = pyqtSignal(int)
    set_theme = pyqtSignal(str)

    search_show = pyqtSignal()
    search_hide = pyqtSignal()

    find_next = pyqtSignal(str)
    find_prev = pyqtSignal(str)
    tab_switch_requested = pyqtSignal(int)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

        self.on_input_cb: Optional[Callable[[str], None]] = None
        self.on_resize_cb: Optional[Callable[[int, int], None]] = None

    @pyqtSlot(str)
    def send_input(self, data: str) -> None:
        """
        Called from JavaScript when the user types.
        """
        if self.on_input_cb:
            self.on_input_cb(data)

    @pyqtSlot(int, int)
    def resize_request(self, cols: int, rows: int) -> None:
        """
        Called from JavaScript when xterm.js is resized.
        """
        if self.on_resize_cb:
            self.on_resize_cb(cols, rows)

    @pyqtSlot(int)
    def request_tab_switch(self, delta: int) -> None:
        """
        Called from JavaScript when user presses Shift+Left (-1) or Shift+Right (+1).
        """
        self.tab_switch_requested.emit(int(delta))

    def write_output(self, data: str) -> None:
        """
        Write terminal output to JavaScript.
        """
        self.output_ready.emit(data)
