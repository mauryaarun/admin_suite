"""
Base terminal tab.

This provides:

- toolbar
- status label
- latency label
- xterm.js WebEngine view
- WebChannel bridge
- search toggle
- clear
- font resizing
- shared terminal HTML loading
- terminal readiness handling
- output buffering
- context menus
- copy/paste/select-all
- save output
- shortcut support
"""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.icons import get_icon, get_swatch_icon, style_icon_button

from admin_suite.terminal.assets import (
    ensure_xterm_assets,
    shared_terminal_html_path,
)

from admin_suite.terminal.bridge import (
    WEBENGINE_AVAILABLE,
    TerminalBridge,
)
from admin_suite.terminal.native_terminal import NativeTerminalWidget


if WEBENGINE_AVAILABLE:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    class TerminalWebView(QWebEngineView):
        """
        QWebEngineView subclass that emits a context-menu requested signal.
        """

        context_menu_requested = pyqtSignal(object)

        def contextMenuEvent(self, event):
            try:
                global_pos = event.globalPosition().toPoint()
            except AttributeError:
                global_pos = event.globalPos()

            self.context_menu_requested.emit(global_pos)

else:
    TerminalWebView = None


def force_web_focus(view) -> None:
    """
    Force focus onto the actual WebEngine render widget and xterm.js terminal.
    """
    if not WEBENGINE_AVAILABLE or view is None:
        return

    try:
        proxy = view.focusProxy()
        if proxy:
            proxy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            proxy.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
            proxy.setFocus()

        view.setFocus()

        page = view.page()
        if page is not None:
            page.runJavaScript(
                """
                if (window.term) {
                    term.focus();
                }
                """
            )
    except Exception:
        pass


class TerminalBaseTab(QWidget):
    """
    Base widget for terminal tabs.
    """

    input_sent = pyqtSignal(str)
    terminal_ready = pyqtSignal()
    terminal_crashed = pyqtSignal()
    tab_switch_requested = pyqtSignal(int)
    terminal_theme_apply_all_requested = pyqtSignal(str)

    def __init__(
        self,
        services,
        name: str = "Terminal",
        *,
        show_reconnect: bool = False,
        session_log_path: Optional[str] = None,
    ):
        super().__init__()

        self.services = services
        self.name = name
        self.session_log_path = session_log_path

        self.theme = self.services.theme.current
        self.search_visible = False

        self._terminal_ready = False
        self._ready_poll_attempts = 0
        self._term_cols = 80
        self._term_rows = 24
        self._pending_output = deque(maxlen=100_000)
        self._output_history = deque(maxlen=100_000)
        self._context_menu_extensions: list[Callable[[QMenu], None]] = []
        self._view_cleaned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_toolbar(layout, show_reconnect)
        self._setup_shortcuts()

        use_native = (
            not WEBENGINE_AVAILABLE
            or not ensure_xterm_assets(self.services.emit_log)
            or bool(self.services.config.get("terminal_engine", "") == "native")
        )

        if use_native:
            self.native_view = NativeTerminalWidget(parent=self)
            self.native_view.send_input.connect(self._on_web_input)
            self.native_view.resized.connect(self._on_web_resize)
            self.native_view.tab_switch_requested.connect(self.tab_switch_requested.emit)
            self.native_view.context_menu_requested.connect(self._show_context_menu)
            layout.addWidget(self.native_view, 1)
            self.apply_terminal_theme()
            self._terminal_ready = True
            return

        self.view = TerminalWebView()
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.view.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.view.installEventFilter(self)
        self.view.context_menu_requested.connect(self._show_context_menu)

        self.channel = QWebChannel(self.view.page())
        self.bridge = TerminalBridge()

        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.bridge.on_input_cb = self._on_web_input
        self.bridge.on_resize_cb = self._on_web_resize
        self.bridge.tab_switch_requested.connect(self.tab_switch_requested.emit)

        self.view.loadFinished.connect(self._on_page_loaded)

        # Optional crash handling, if supported by this Qt build.
        try:
            page = self.view.page()
            terminated_signal = getattr(page, "renderProcessTerminated", None)
            if terminated_signal is not None:
                terminated_signal.connect(self._on_render_process_terminated)
        except Exception:
            pass

        html_path = shared_terminal_html_path()
        self.view.load(QUrl.fromLocalFile(str(html_path)))

        layout.addWidget(self.view, 1)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self, parent_layout: QVBoxLayout, show_reconnect: bool) -> None:
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet(
            f"background:{self.theme.get('panel', '#111')};"
            f"border-bottom:1px solid {self.theme.get('border', '#333')};"
        )
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        toolbar.customContextMenuRequested.connect(self._show_toolbar_context_menu)

        h = QHBoxLayout(toolbar)
        h.setContentsMargins(8, 3, 8, 3)
        h.setSpacing(6)

        self.toolbar = toolbar
        self.toolbar_layout = h

        self.status_label = QLabel("● Idle")
        self.status_label.setStyleSheet(
            f"color:{self.theme.get('sub', '#888')};font-size:11px;font-weight:600;"
        )
        h.addWidget(self.status_label)

        self.latency_label = QLabel("-- ms")
        self.latency_label.setToolTip("Session round-trip latency")
        self.latency_label.setStyleSheet(
            f"color:{self.theme.get('sub', '#888')};font-size:11px;"
        )
        h.addWidget(self.latency_label)

        if self.session_log_path:
            log_label = QLabel(os.path.basename(self.session_log_path))
            log_label.setToolTip(f"Session log: {self.session_log_path}")
            log_label.setStyleSheet(
                f"color:{self.theme.get('sub', '#888')};font-size:10px;padding:1px 6px;"
                f"border:1px solid {self.theme.get('border', '#333')};border-radius:3px;"
            )
            h.addWidget(log_label)

        h.addStretch()

        icon_color = self.theme.get("text", "#eff0f1")

        if show_reconnect:
            self.reconnect_btn = QPushButton()
            self.reconnect_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            style_icon_button(
                self.reconnect_btn,
                "refresh",
                tooltip="Reconnect SSH session",
                color=icon_color,
                min_width=34,
                min_height=28,
            )
            self.reconnect_btn.clicked.connect(self.reconnect)
            h.addWidget(self.reconnect_btn)

        self.search_btn = QPushButton()
        self.search_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.search_btn,
            "search",
            tooltip="Search terminal (Ctrl+Shift+F)",
            color=icon_color,
            min_width=34,
            min_height=28,
        )
        self.search_btn.clicked.connect(self.toggle_search)
        h.addWidget(self.search_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.clear_btn,
            "clear",
            tooltip="Clear terminal (Ctrl+Shift+K)",
            color=icon_color,
            min_width=34,
            min_height=28,
        )
        self.clear_btn.clicked.connect(self.clear_terminal)
        h.addWidget(self.clear_btn)

        self.theme_btn = QPushButton()
        self.theme_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.theme_btn,
            "palette",
            tooltip="Terminal color theme",
            color=icon_color,
            min_width=34,
            min_height=28,
        )
        self.theme_btn.clicked.connect(self._show_theme_menu_from_button)
        h.addWidget(self.theme_btn)

        self.font_inc_btn = QPushButton()
        self.font_inc_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.font_inc_btn,
            "font_plus",
            tooltip="Increase font size (Ctrl+Shift+=)",
            color=icon_color,
            min_width=34,
            min_height=28,
        )
        self.font_inc_btn.clicked.connect(lambda: self.change_font_size(1))
        h.addWidget(self.font_inc_btn)

        self.font_dec_btn = QPushButton()
        self.font_dec_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.font_dec_btn,
            "font_minus",
            tooltip="Decrease font size (Ctrl+-)",
            color=icon_color,
            min_width=34,
            min_height=28,
        )
        self.font_dec_btn.clicked.connect(lambda: self.change_font_size(-1))
        h.addWidget(self.font_dec_btn)

        parent_layout.addWidget(toolbar)

    def _setup_shortcuts(self) -> None:
        """
        Terminal keyboard shortcuts.

        These use WidgetWithChildrenShortcut so they do not hijack global
        application shortcuts unnecessarily.
        """
        shortcuts = (
            ("Ctrl+Shift+C", self.copy_selection),
            ("Ctrl+Shift+V", self.paste_clipboard),
            ("Ctrl+Shift+A", self.select_all_terminal),
            ("Ctrl+Shift+F", self.toggle_search),
            ("Ctrl+Shift+K", self.clear_terminal),
            ("Ctrl+Shift+=", lambda: self.change_font_size(1)),
            ("Ctrl+-", lambda: self.change_font_size(-1)),
            ("Ctrl+0", self.reset_font_size),
        )

        for sequence, slot in shortcuts:
            try:
                shortcut = QShortcut(QKeySequence(sequence), self)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(slot)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Page / terminal readiness
    # ------------------------------------------------------------------

    def _on_page_loaded(self, ok: bool = True) -> None:
        if not WEBENGINE_AVAILABLE or not hasattr(self, "bridge"):
            return

        self._terminal_ready = False
        self._ready_poll_attempts = 0

        if not ok:
            self.set_status("● Page load failed", self.theme.get("danger", "#f00"))
            return

        self.apply_terminal_theme()
        QTimer.singleShot(120, self.force_focus)
        self._poll_terminal_ready()

    def _poll_terminal_ready(self) -> None:
        """
        Poll until xterm.js exists.

        This avoids relying only on fixed QTimer delays.
        """
        if getattr(self, "_view_cleaned", False):
            return

        if not WEBENGINE_AVAILABLE or not hasattr(self, "view"):
            return

        if self._terminal_ready:
            return

        # After ~10 seconds, fall back to ready state instead of blocking forever.
        if self._ready_poll_attempts > 80:
            self._mark_terminal_ready()
            return

        self._ready_poll_attempts += 1

        def _ready_callback(ready) -> None:
            if bool(ready):
                self._mark_terminal_ready()
            else:
                QTimer.singleShot(120, self._poll_terminal_ready)

        try:
            self.view.page().runJavaScript(
                "Boolean(window.term && typeof window.term.write === 'function');",
                _ready_callback,
            )
        except Exception:
            QTimer.singleShot(150, self._poll_terminal_ready)

    def _mark_terminal_ready(self) -> None:
        if getattr(self, "_view_cleaned", False):
            return

        already_ready = self._terminal_ready
        self._terminal_ready = True

        self.apply_terminal_theme()
        self._flush_pending_output()

        if not already_ready:
            self.terminal_ready.emit()

        QTimer.singleShot(50, self.force_focus)

    def _on_render_process_terminated(self, *args) -> None:
        self._terminal_ready = False
        self._ready_poll_attempts = 0

        self.set_status("● Renderer crashed", self.theme.get("danger", "#f00"))
        self.terminal_crashed.emit()

        QTimer.singleShot(250, self._reload_view)

    def _reload_view(self) -> None:
        if WEBENGINE_AVAILABLE and hasattr(self, "view") and not self._view_cleaned:
            self.view.reload()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_terminal_theme(self, theme_name: Optional[str] = None) -> None:
        """
        Apply font size and terminal theme from current configuration.
        """
        try:
            font_size = int(self.services.config.get("terminal_font_size", 13))
        except Exception:
            font_size = 13

        active_name = theme_name or self.services.config.get(
            "terminal_theme",
            self.theme.get("xterm", "dark"),
        )
        self._current_terminal_theme_name = active_name

        try:
            terminal_theme = self.services.theme.get_terminal_theme(active_name)
        except Exception:
            terminal_theme = {}

        if hasattr(self, "native_view") and self.native_view is not None:
            self.native_view.set_font_size(font_size)
            self.native_view.set_theme(terminal_theme)

        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            if hasattr(self.bridge, "set_font_size"):
                self.bridge.set_font_size.emit(font_size)

            if hasattr(self.bridge, "set_theme"):
                self.bridge.set_theme.emit(json.dumps(terminal_theme))

        if WEBENGINE_AVAILABLE and hasattr(self, "view") and not self._view_cleaned:
            bg_hex = terminal_theme.get("background")
            if bg_hex:
                try:
                    self.view.page().setBackgroundColor(QColor(bg_hex))
                except Exception:
                    pass

    def set_terminal_theme(self, theme_name: str, save_config: bool = True) -> None:
        """
        Change the terminal color scheme dynamically and optionally persist to config.
        """
        if save_config:
            self.services.config.set("terminal_theme", theme_name)
            self.services.config.save()

        self.apply_terminal_theme(theme_name=theme_name)

    def _apply_current_theme_to_all(self) -> None:
        active_name = getattr(self, "_current_terminal_theme_name", None) or self.services.config.get(
            "terminal_theme",
            self.theme.get("xterm", "dark"),
        )
        self.services.config.set("terminal_theme", active_name)
        self.services.config.save()
        self.terminal_theme_apply_all_requested.emit(active_name)

    def _populate_terminal_theme_menu(self, parent_menu: QMenu) -> QMenu:
        """
        Populate a Terminal Theme submenu with color swatches and checkable entries.
        """
        theme_menu = parent_menu.addMenu("Terminal Theme")
        theme_menu.setIcon(
            get_icon("palette", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        )

        active_name = getattr(self, "_current_terminal_theme_name", None) or self.services.config.get(
            "terminal_theme",
            self.theme.get("xterm", "dark"),
        )

        items = (
            self.services.theme.terminal_theme_items()
            if hasattr(self.services.theme, "terminal_theme_items")
            else [(k, k.replace("_", " ").title()) for k in self.services.theme.terminal_themes]
        )

        group = QActionGroup(theme_menu)
        group.setExclusive(True)

        for key, label in items:
            t_dict = self.services.theme.get_terminal_theme(key)
            swatch = get_swatch_icon(
                t_dict.get("background", "#111111"),
                t_dict.get("foreground", "#eeeeee"),
                t_dict.get("cyan") or t_dict.get("blue") or "#3daee9",
                size=16,
            )
            act = QAction(swatch, label, theme_menu)
            act.setCheckable(True)
            act.setChecked(key == active_name)
            act.setData(key)
            act.triggered.connect(
                lambda _checked=False, name=key: self.set_terminal_theme(name, save_config=True)
            )
            group.addAction(act)
            theme_menu.addAction(act)

        theme_menu.addSeparator()
        apply_all_act = self._add_menu_action(
            theme_menu,
            "Apply Theme to All Open Terminals",
            self._apply_current_theme_to_all,
            icon_name="layers",
        )
        apply_all_act.setToolTip("Apply the active terminal theme to all open terminal tabs")
        return theme_menu

    def _show_theme_menu_from_button(self) -> None:
        if not hasattr(self, "theme_btn"):
            return
        menu = QMenu(self)
        self._populate_terminal_theme_menu(menu)
        # If opened directly from the palette button, lift its actions to top level for 1-click choice.
        submenus = [a.menu() for a in menu.actions() if a.menu() is not None]
        if submenus:
            sub = submenus[0]
            sub.exec(self.theme_btn.mapToGlobal(QPoint(0, self.theme_btn.height())))
            return
        menu.exec(self.theme_btn.mapToGlobal(QPoint(0, self.theme_btn.height())))

    # ------------------------------------------------------------------
    # Focus
    # ------------------------------------------------------------------

    def force_focus(self) -> None:
        """
        Focus terminal view.
        """
        if hasattr(self, "native_view") and self.native_view is not None:
            self.native_view.setFocus()
            return
        force_web_focus(getattr(self, "view", None))

    def eventFilter(self, obj, event):
        if WEBENGINE_AVAILABLE and obj is getattr(self, "view", None):
            event_type = event.type()

            if event_type in (
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
            ):
                proxy = self.view.focusProxy()
                if proxy is not None and proxy is not obj:
                    QApplication.sendEvent(proxy, event)
                    return True

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def write_output(self, data: str) -> None:
        """
        Write output to xterm.js or native terminal.

        Output is buffered until the terminal is ready.
        """
        if not data:
            return

        self._output_history.append(data)

        if hasattr(self, "native_view") and self.native_view is not None:
            self.native_view.write(data)
            return

        if not self._terminal_ready:
            self._pending_output.append(data)
            return

        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            try:
                self.bridge.write_output(data)
            except Exception:
                pass

    def _flush_pending_output(self) -> None:
        if not WEBENGINE_AVAILABLE or not hasattr(self, "bridge"):
            return

        while self._pending_output:
            data = self._pending_output.popleft()
            try:
                self.bridge.write_output(data)
            except Exception:
                break

    def clear_terminal(self) -> None:
        """
        Clear xterm.js or native terminal content.
        """
        self._pending_output.clear()

        if hasattr(self, "native_view") and self.native_view is not None:
            self.native_view.clear()
            return

        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            clear_signal = getattr(self.bridge, "clear_terminal", None)
            if clear_signal is not None:
                clear_signal.emit()
            else:
                self._run_terminal_js("term.clear();")

    def clear_scrollback(self) -> None:
        """
        Clear scrollback buffer.
        """
        self._run_terminal_js("term.clear();")

    def reset_terminal(self) -> None:
        """
        Reset terminal state.
        """
        self._run_terminal_js("term.reset();")

    def select_all_terminal(self) -> None:
        """
        Select all terminal text.
        """
        self._run_terminal_js("term.selectAll();")

    def save_output_as(self) -> None:
        """
        Save buffered terminal output to a file.
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Terminal Output",
            "",
            "Text Files (*.txt);;Log Files (*.log);;All Files (*)",
        )

        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8", errors="replace") as fh:
                fh.write("".join(self._output_history))
        except Exception as exc:
            self.services.notifications.push(
                "error",
                "Save Output Failed",
                str(exc),
            )

    # ------------------------------------------------------------------
    # Search / font
    # ------------------------------------------------------------------

    def toggle_search(self) -> None:
        """
        Toggle terminal search bar.
        """
        if not WEBENGINE_AVAILABLE or not hasattr(self, "bridge"):
            return

        self.search_visible = not self.search_visible

        if self.search_visible:
            signal = getattr(self.bridge, "search_show", None)
        else:
            signal = getattr(self.bridge, "search_hide", None)

        if signal is not None:
            signal.emit()

    def change_font_size(self, delta: int) -> None:
        """
        Change terminal font size.
        """
        try:
            current = int(self.services.config.get("terminal_font_size", 13))
        except Exception:
            current = 13

        new_size = current + delta

        if 8 <= new_size <= 32:
            self.services.config.set("terminal_font_size", new_size)
            self.services.config.save()

            if hasattr(self, "native_view") and self.native_view is not None:
                self.native_view.set_font_size(new_size)

            if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
                if hasattr(self.bridge, "set_font_size"):
                    self.bridge.set_font_size.emit(new_size)

    def reset_font_size(self) -> None:
        """
        Reset terminal font size to default.
        """
        try:
            default_size = int(
                self.services.config.get("terminal_font_size_default", 13)
            )
        except Exception:
            default_size = 13

        self.services.config.set("terminal_font_size", default_size)
        self.services.config.save()

        if hasattr(self, "native_view") and self.native_view is not None:
            self.native_view.set_font_size(default_size)

        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            if hasattr(self.bridge, "set_font_size"):
                self.bridge.set_font_size.emit(default_size)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_status(self, text: str, color: Optional[str] = None) -> None:
        """
        Set status label.
        """
        if not hasattr(self, "status_label"):
            return

        if color is None:
            color = self.theme.get("sub", "#888")

        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:600;"
        )

    def set_latency(self, ms: int) -> None:
        """
        Set latency label.
        """
        if not hasattr(self, "latency_label"):
            return

        if ms < 0:
            self.latency_label.setText("Offline")
            self.latency_label.setStyleSheet(
                f"color:{self.theme.get('danger', '#f00')};font-size:11px;"
            )
            return

        self.latency_label.setText(f"{ms} ms")

        if ms < 150:
            color = self.theme.get("ok", "#0f0")
        elif ms < 400:
            color = self.theme.get("warn", "#ff0")
        else:
            color = self.theme.get("danger", "#f00")

        self.latency_label.setStyleSheet(
            f"color:{color};font-size:11px;"
        )

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _on_web_input(self, data: str) -> None:
        """
        Input received from xterm.js.
        """
        self.handle_input(data)
        self.input_sent.emit(data)

    def _on_web_resize(self, cols: int, rows: int) -> None:
        """
        Resize request from xterm.js or native terminal.
        """
        if cols > 0 and rows > 0:
            self._term_cols = int(cols)
            self._term_rows = int(rows)
        self.handle_resize(cols, rows)

    def handle_input(self, data: str) -> None:
        """
        Implemented by subclasses.
        """
        pass

    def handle_resize(self, cols: int, rows: int) -> None:
        """
        Implemented by subclasses.
        """
        pass

    def inject_input(self, data: str) -> None:
        """
        Used by broadcast mode.

        This writes input without emitting input_sent again.
        """
        self.handle_input(data)

    def send_text(self, text: str) -> None:
        """
        Send raw text to terminal.
        """
        self.handle_input(text)

    def reconnect(self) -> None:
        """
        Implemented by reconnectable terminal tabs.
        """
        pass

    # ------------------------------------------------------------------
    # Context menu helpers
    # ------------------------------------------------------------------

    def add_context_menu_extension(self, callback: Callable[[QMenu], None]) -> None:
        """
        Add an external context-menu builder.

        callback signature:
            callback(menu: QMenu) -> None
        """
        self._context_menu_extensions.append(callback)

    def extend_context_menu(self, menu: QMenu) -> None:
        """
        Subclasses can override this to add menu entries.
        """
        pass

    def _add_menu_action(
        self,
        menu: QMenu,
        text: str,
        slot=None,
        enabled: bool = True,
        icon_name: Optional[str] = None,
    ):
        action = menu.addAction(text)
        if icon_name:
            action.setIcon(
                get_icon(icon_name, color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
            )
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def build_context_menu(self) -> QMenu:
        """
        Construct and return the terminal right-click context menu.
        Works for both xterm.js WebEngine and NativeTerminalWidget backends.
        """
        menu = QMenu(self)
        icon_color = self.services.theme.current.get("text", "#e2e8f0")

        self._add_menu_action(menu, "Copy", self.copy_selection, icon_name="copy")
        self._add_menu_action(menu, "Paste", self.paste_clipboard, icon_name="file_text")
        self._add_menu_action(menu, "Select All", self.select_all_terminal, icon_name="check")

        menu.addSeparator()

        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            self._add_menu_action(menu, "Search", self.toggle_search, icon_name="search")
        self._add_menu_action(menu, "Clear", self.clear_terminal, icon_name="clear")
        if WEBENGINE_AVAILABLE and hasattr(self, "bridge"):
            self._add_menu_action(menu, "Clear Scrollback", self.clear_scrollback, icon_name="trash")
            self._add_menu_action(menu, "Reset Terminal", self.reset_terminal, icon_name="refresh")

        menu.addSeparator()

        # Terminal Theme submenu right from the terminal right-click menu.
        self._populate_terminal_theme_menu(menu)

        font_menu = menu.addMenu("Font Size")
        font_menu.setIcon(get_icon("sliders", color=icon_color, size=16))

        self._add_menu_action(
            font_menu,
            "Increase",
            lambda: self.change_font_size(1),
            icon_name="font_plus",
        )
        self._add_menu_action(
            font_menu,
            "Decrease",
            lambda: self.change_font_size(-1),
            icon_name="font_minus",
        )
        self._add_menu_action(
            font_menu,
            "Reset",
            self.reset_font_size,
            icon_name="refresh",
        )

        menu.addSeparator()

        self._add_menu_action(
            menu,
            "Save Output As…",
            self.save_output_as,
            enabled=bool(self._output_history),
            icon_name="save",
        )

        if self.session_log_path:
            self._add_menu_action(menu, "Open Log Location", self.open_log_location, icon_name="folder_open")
            self._add_menu_action(menu, "Copy Log Path", self.copy_log_path, icon_name="copy")

        menu.addSeparator()

        # Subclass hook.
        self.extend_context_menu(menu)

        # External hook, useful for SplitTerminalTab.
        for callback in self._context_menu_extensions:
            try:
                callback(menu)
            except Exception:
                pass

        return menu

    def _show_context_menu(self, global_pos) -> None:
        """
        Show terminal context menu.
        """
        has_native = hasattr(self, "native_view") and self.native_view is not None
        has_web = WEBENGINE_AVAILABLE and hasattr(self, "bridge")
        if not has_native and not has_web:
            return

        menu = self.build_context_menu()
        menu.exec(global_pos)

    def _show_toolbar_context_menu(self, pos) -> None:
        """
        Toolbar context menu.
        """
        if not hasattr(self, "toolbar"):
            return

        menu = QMenu(self)

        self._add_menu_action(menu, "Copy Status", self.copy_status, icon_name="info")
        self._add_menu_action(menu, "Copy Latency", self.copy_latency, icon_name="clock")

        if self.session_log_path:
            menu.addSeparator()
            self._add_menu_action(menu, "Open Log Location", self.open_log_location, icon_name="folder_open")
            self._add_menu_action(menu, "Copy Log Path", self.copy_log_path, icon_name="copy")

        menu.addSeparator()

        self._add_menu_action(menu, "Toggle Search", self.toggle_search, icon_name="search")
        self._add_menu_action(menu, "Clear Terminal", self.clear_terminal, icon_name="clear")
        self._populate_terminal_theme_menu(menu)

        menu.addSeparator()

        self._add_menu_action(
            menu,
            "Increase Font",
            lambda: self.change_font_size(1),
            icon_name="font_plus",
        )
        self._add_menu_action(
            menu,
            "Decrease Font",
            lambda: self.change_font_size(-1),
            icon_name="font_minus",
        )
        self._add_menu_action(menu, "Reset Font", self.reset_font_size, icon_name="refresh")

        menu.exec(self.toolbar.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Clipboard / JS helpers
    # ------------------------------------------------------------------

    def _run_terminal_js(self, expression: str) -> None:
        """
        Run JavaScript only if terminal exists.
        """
        if not WEBENGINE_AVAILABLE or not hasattr(self, "view"):
            return

        js = f"""
        if (window.term) {{
            {expression}
        }}
        """

        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass

    def copy_selection(self) -> None:
        """
        Copy selected terminal text to clipboard.
        """
        if hasattr(self, "native_view") and self.native_view is not None:
            sel = self.native_view.get_selected_text()
            if sel:
                clipboard = QApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(sel)
            return

        if not WEBENGINE_AVAILABLE or not hasattr(self, "view"):
            return

        def _copy_callback(text):
            if text:
                clipboard = QApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(str(text))

        try:
            self.view.page().runJavaScript(
                """
                window.term ? window.term.getSelection() : "";
                """,
                _copy_callback,
            )
        except Exception:
            pass

    def paste_clipboard(self) -> None:
        """
        Paste clipboard text into terminal.
        """
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        text = clipboard.text()
        if not text:
            return

        if hasattr(self, "native_view") and self.native_view is not None:
            self.handle_input(text)
            return

        if not WEBENGINE_AVAILABLE or not hasattr(self, "view"):
            return

        payload = json.dumps(text)

        js = f"""
        if (window.term) {{
            const text = {payload};

            if (typeof term.paste === "function") {{
                term.paste(text);
            }} else if (
                term._core &&
                term._core.coreService &&
                typeof term._core.coreService.triggerDataEvent === "function"
            ) {{
                term._core.coreService.triggerDataEvent(text);
            }} else if (typeof term.write === "function") {{
                term.write(text);
            }}
        }}
        """

        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass

    def copy_status(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and hasattr(self, "status_label"):
            clipboard.setText(self.status_label.text())

    def copy_latency(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and hasattr(self, "latency_label"):
            clipboard.setText(self.latency_label.text())

    def copy_log_path(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and self.session_log_path:
            clipboard.setText(self.session_log_path)

    def open_log_location(self) -> None:
        """
        Open the folder containing the session log.
        """
        if not self.session_log_path:
            return

        log_dir = os.path.dirname(self.session_log_path)
        if not log_dir:
            log_dir = "."

        QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """
        Cleanup base resources.

        Subclasses should stop workers and then call:
            super().cleanup()
        """
        self._cleanup_view()

    def _cleanup_view(self) -> None:
        if getattr(self, "_view_cleaned", False):
            return

        self._view_cleaned = True

        if not WEBENGINE_AVAILABLE or not hasattr(self, "view"):
            return

        try:
            self.view.loadFinished.disconnect(self._on_page_loaded)
        except Exception:
            pass

        try:
            self.view.stop()
        except Exception:
            pass

        try:
            self.view.deleteLater()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self.cleanup()
        except Exception:
            pass

        event.accept()