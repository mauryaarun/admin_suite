"""
Split terminal tab.

Improvements:

- per-pane context menus
- close pane
- close other panes
- close last pane
- broadcast input
- active pane tracking
- nested splitter support
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from admin_suite.ssh.credentials import profile_creds
from admin_suite.terminal.ssh_tab import SshTerminalTab


class SplitTerminalTab(QWidget):
    """
    A tab containing multiple SSH terminal panes for one profile.
    """

    def __init__(
        self,
        services,
        profile_name: str,
        profile_data: dict,
    ):
        super().__init__()

        self.services = services
        self.profile_name = profile_name
        self.profile_data = profile_data

        self.terminals = []
        self._parent_splitter = {}
        self._active_terminal = None

        self.broadcast_enabled = False
        self._updating_broadcast = False

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(
            f"background:{theme.get('panel', '#111')};"
            f"border-bottom:1px solid {theme.get('border', '#333')};"
        )
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._show_bar_context_menu)

        bh = QHBoxLayout(bar)
        bh.setContentsMargins(6, 2, 6, 2)

        self.bar = bar

        label = QLabel(f"⧉ Split session — {profile_name}")
        label.setStyleSheet(
            f"color:{theme.get('accent', '#4af')};font-weight:bold;font-size:12px;"
        )
        bh.addWidget(label)

        bh.addStretch()

        button_style = self._button_style(theme)

        add_h = QPushButton("◫ Split Right")
        add_h.setStyleSheet(button_style)
        add_h.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_h.clicked.connect(lambda: self.add_pane(Qt.Orientation.Horizontal))
        bh.addWidget(add_h)

        add_v = QPushButton("⬓ Split Down")
        add_v.setStyleSheet(button_style)
        add_v.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_v.clicked.connect(lambda: self.add_pane(Qt.Orientation.Vertical))
        bh.addWidget(add_v)

        self.broadcast_btn = QPushButton("📣 Broadcast: Off")
        self.broadcast_btn.setStyleSheet(button_style)
        self.broadcast_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.broadcast_btn.clicked.connect(self._toggle_broadcast)
        bh.addWidget(self.broadcast_btn)

        layout.addWidget(bar)

        self.root_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.root_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.root_splitter, 1)

        self.add_pane(Qt.Orientation.Horizontal)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _button_style(self, theme: dict) -> str:
        return (
            "QPushButton{"
            f"background:{theme.get('panel2', '#222')};"
            "border:none;"
            "padding:3px 10px;"
            "border-radius:3px;"
            "font-size:11px;"
            "}"
            "QPushButton:hover{"
            f"background:{theme.get('hover', '#333')};"
            "}"
        )

    def _add_menu_action(self, menu: QMenu, text: str, slot=None, enabled: bool = True):
        action = menu.addAction(text)
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def _make_term(self):
        data = self.profile_data or {}
        is_local = (
            bool(data.get("is_local"))
            or self.profile_name.lower() == "localhost"
            or (
                data.get("ssh_host") in ("127.0.0.1", "localhost")
                and bool(data.get("use_local_exec", True))
            )
        )
        if is_local:
            import os
            from admin_suite.terminal.local_tab import LocalTerminalTab

            terminal = LocalTerminalTab(
                self.services,
                command=data.get("initial_cmd") or os.environ.get("SHELL", "/bin/bash"),
                name=self.profile_name,
            )
            terminal.add_context_menu_extension(
                lambda menu, t=terminal: self._extend_pane_context_menu(menu, t)
            )
            terminal.input_sent.connect(
                lambda data, t=terminal: self._on_pane_input(data, t)
            )
            return terminal

        creds = profile_creds(data)

        terminal = SshTerminalTab(
            self.services,
            host=data.get("ssh_host", ""),
            port=data.get("ssh_port", 22),
            user=data.get("ssh_user", ""),
            creds=creds,
            initial_cmd=data.get("initial_cmd", ""),
            name=self.profile_name,
            use_jump=data.get("use_jump", False),
            jump_host=data.get("jump_host"),
            jump_port=data.get("jump_port", 22),
            jump_user=data.get("jump_user"),
            jump_creds=None,
            use_agent=data.get("use_agent", False),
            profile_name=self.profile_name,
            profile_data=self.profile_data,
        )

        terminal.add_context_menu_extension(
            lambda menu, t=terminal: self._extend_pane_context_menu(menu, t)
        )

        terminal.input_sent.connect(
            lambda data, t=terminal: self._on_pane_input(data, t)
        )

        return terminal

    def _on_pane_input(self, data: str, source) -> None:
        self._active_terminal = source

        if not self.broadcast_enabled:
            return

        if self._updating_broadcast:
            return

        self._updating_broadcast = True

        try:
            for terminal in self.terminals:
                if terminal is not source:
                    terminal.inject_input(data)
        finally:
            self._updating_broadcast = False

    def _focus_terminal(self, terminal) -> None:
        self._active_terminal = terminal

        try:
            terminal.setFocus()
            terminal.force_focus()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Pane management
    # ------------------------------------------------------------------

    def add_pane(self, orientation, target=None) -> None:
        """
        Add another terminal pane.

        If target is provided, split relative to that terminal.
        Otherwise split relative to active or last terminal.
        """
        terminal = self._make_term()

        if not self.terminals:
            self.root_splitter.addWidget(terminal)
            self._parent_splitter[terminal] = self.root_splitter

        else:
            target = target or self._active_terminal or self.terminals[-1]
            parent = self._parent_splitter.get(target, self.root_splitter)

            if parent.orientation() == orientation:
                index = parent.indexOf(target)
                if index < 0:
                    index = parent.count()

                parent.insertWidget(index + 1, terminal)
                self._parent_splitter[terminal] = parent

            else:
                nested = QSplitter(orientation)
                nested.setChildrenCollapsible(False)

                index = parent.indexOf(target)
                if index < 0:
                    index = parent.count()

                parent.insertWidget(index, nested)

                nested.addWidget(target)
                nested.addWidget(terminal)

                self._parent_splitter[target] = nested
                self._parent_splitter[terminal] = nested

        self.terminals.append(terminal)
        self._active_terminal = terminal

    def close_pane(self, terminal) -> None:
        if len(self.terminals) <= 1:
            return

        self._remove_terminal(terminal, allow_last=False)

    def close_last_pane(self) -> None:
        if len(self.terminals) <= 1:
            return

        self._remove_terminal(self.terminals[-1], allow_last=False)

    def close_other_panes(self, target=None) -> None:
        target = target or self._active_terminal

        if target is None:
            return

        if target not in self.terminals:
            return

        for terminal in list(self.terminals):
            if terminal is not target:
                self._remove_terminal(terminal, allow_last=True)

        self._active_terminal = target

    def close_all_panes(self) -> None:
        for terminal in list(self.terminals):
            self._remove_terminal(terminal, allow_last=True)

    def _remove_terminal(self, terminal, allow_last: bool = False) -> None:
        if terminal not in self.terminals:
            return

        if not allow_last and len(self.terminals) <= 1:
            return

        parent = self._parent_splitter.get(terminal)

        if parent is not None:
            try:
                parent.removeWidget(terminal)
            except Exception:
                pass

        self.terminals.remove(terminal)
        self._parent_splitter.pop(terminal, None)

        try:
            terminal.cleanup()
        except Exception:
            pass

        try:
            terminal.setParent(None)
            terminal.deleteLater()
        except Exception:
            pass

        if parent is not None and parent is not self.root_splitter:
            self._collapse_splitter_if_needed(parent)

        if self._active_terminal is terminal:
            self._active_terminal = self.terminals[-1] if self.terminals else None

    def _collapse_splitter_if_needed(self, splitter: QSplitter) -> None:
        if splitter is None or splitter is self.root_splitter:
            return

        if splitter.count() == 0:
            grand = splitter.parentWidget()

            if isinstance(grand, QSplitter):
                index = grand.indexOf(splitter)
                if index >= 0:
                    grand.removeWidget(splitter)

            splitter.deleteLater()

            if isinstance(grand, QSplitter) and grand is not self.root_splitter:
                self._collapse_splitter_if_needed(grand)

            return

        if splitter.count() == 1:
            child = splitter.widget(0)
            grand = splitter.parentWidget()

            if isinstance(grand, QSplitter):
                index = grand.indexOf(splitter)

                if index >= 0:
                    grand.insertWidget(index, child)

                    if not isinstance(child, QSplitter):
                        self._parent_splitter[child] = grand

                    splitter.deleteLater()

                    if grand is not self.root_splitter:
                        self._collapse_splitter_if_needed(grand)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    def _toggle_broadcast(self, *args) -> None:
        self.broadcast_enabled = not self.broadcast_enabled
        self._update_broadcast_ui()

    def _update_broadcast_ui(self) -> None:
        if self.broadcast_enabled:
            self.broadcast_btn.setText("📣 Broadcast: On")
        else:
            self.broadcast_btn.setText("📣 Broadcast: Off")

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _show_bar_context_menu(self, pos) -> None:
        menu = QMenu(self)

        self._add_menu_action(
            menu,
            "Split Right",
            lambda: self.add_pane(Qt.Orientation.Horizontal),
        )

        self._add_menu_action(
            menu,
            "Split Down",
            lambda: self.add_pane(Qt.Orientation.Vertical),
        )

        menu.addSeparator()

        self._add_menu_action(
            menu,
            "Close Last Pane",
            self.close_last_pane,
            enabled=len(self.terminals) > 1,
        )

        self._add_menu_action(
            menu,
            "Close Other Panes",
            lambda: self.close_other_panes(),
            enabled=len(self.terminals) > 1,
        )

        self._add_menu_action(
            menu,
            "Close All Panes",
            self.close_all_panes,
            enabled=bool(self.terminals),
        )

        menu.addSeparator()

        broadcast_text = (
            "Disable Broadcast Input"
            if self.broadcast_enabled
            else "Enable Broadcast Input"
        )

        self._add_menu_action(menu, broadcast_text, self._toggle_broadcast)

        menu.exec(self.bar.mapToGlobal(pos))

    def _extend_pane_context_menu(self, menu: QMenu, terminal) -> None:
        menu.addSeparator()

        self._add_menu_action(
            menu,
            "Split Right",
            lambda: self.add_pane(Qt.Orientation.Horizontal, terminal),
        )

        self._add_menu_action(
            menu,
            "Split Down",
            lambda: self.add_pane(Qt.Orientation.Vertical, terminal),
        )

        self._add_menu_action(
            menu,
            "Focus Pane",
            lambda: self._focus_terminal(terminal),
        )

        self._add_menu_action(
            menu,
            "Close Pane",
            lambda: self.close_pane(terminal),
            enabled=len(self.terminals) > 1,
        )

        self._add_menu_action(
            menu,
            "Close Other Panes",
            lambda: self.close_other_panes(terminal),
            enabled=len(self.terminals) > 1,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all_terminals(self):
        """
        Return all terminal panes.
        """
        return list(self.terminals)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        for terminal in list(self.terminals):
            try:
                terminal.cleanup()
            except Exception:
                pass

            try:
                terminal.setParent(None)
                terminal.deleteLater()
            except Exception:
                pass

        self.terminals.clear()
        self._parent_splitter.clear()
        self._active_terminal = None

        event.accept()