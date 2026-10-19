"""
Docking Workspace & Multi-Pane Layout Manager for Admin Suite.

Features:
- Detachable floating windows for multi-monitor setups (pop-out any tab to second monitor)
- Safe re-docking back into the main tab bar upon close or button click
- Side-by-side (Horizontal) and Top-and-Bottom (Vertical) multi-pane splitting
- Workspace perspective switching (Server Lens, Terminal Ops, DB Workbench)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class DetachedTabWindow(QMainWindow):
    """
    Independent floating top-level window hosting a detached tab.
    Allows moving terminals, DB consoles, or dashboards to a second monitor.
    """

    redock_requested = pyqtSignal(object)  # Emits self

    def __init__(
        self,
        widget: QWidget,
        title: str,
        original_tabs: QTabWidget,
        original_index: int,
        theme: dict[str, str],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.detached_widget = widget
        self.original_title = title
        self.original_tabs = original_tabs
        self.original_index = original_index
        self.theme = theme

        self.setWindowTitle(f"Admin Suite — {title}")
        self.resize(1000, 700)

        win_bg = theme.get("win", "#1b1e23")
        panel_bg = theme.get("panel", "#232629")
        accent = theme.get("accent", "#3daee9")
        border = theme.get("border", "#31363b")

        self.setStyleSheet(f"QMainWindow {{ background: {win_bg}; }}")

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top Bar with Re-dock button
        top_bar = QFrame()
        top_bar.setFixedHeight(38)
        top_bar.setStyleSheet(
            f"background: {panel_bg}; border-bottom: 1px solid {border};"
        )
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(8, 2, 8, 2)

        title_lbl = QLabel(f"🗔 <b>{title}</b> (Detached Window)")
        title_lbl.setStyleSheet(f"color: {accent}; font-size: 12px;")
        tb_layout.addWidget(title_lbl)
        tb_layout.addStretch()

        redock_btn = QPushButton("📌 Re-dock to Main Window")
        redock_btn.setStyleSheet(
            f"background: {accent}; color: white; border-radius: 3px; padding: 3px 10px; font-weight: bold; font-size: 11px;"
        )
        redock_btn.clicked.connect(self.request_redock)
        tb_layout.addWidget(redock_btn)

        layout.addWidget(top_bar)
        layout.addWidget(self.detached_widget, 1)

    def request_redock(self) -> None:
        """Trigger re-docking and close window."""
        self.redock_requested.emit(self)
        self.close()

    def closeEvent(self, event):
        """When user closes the window, automatically re-dock if not already re-docked."""
        if self.detached_widget is not None and self.original_tabs is not None:
            # Re-dock back
            idx = min(self.original_index, self.original_tabs.count())
            self.original_tabs.insertTab(idx, self.detached_widget, self.original_title)
            self.original_tabs.setCurrentWidget(self.detached_widget)
            self.detached_widget = None
        event.accept()


# Compatibility alias
DetachableTabWindow = DetachedTabWindow


class WorkspaceManager:
    """
    Manages tab detachment, split views, and workspace layouts.
    """

    def __init__(self, main_window: Any):
        self.main_window = main_window
        self.detached_windows: list[DetachedTabWindow] = []

    def detach_tab(self, tabs: QTabWidget, index: int) -> Optional[DetachedTabWindow]:
        """Detach a tab into an independent floating window."""
        if index < 0 or index >= tabs.count():
            return None

        widget = tabs.widget(index)
        if not widget:
            return None

        # Check if tab is permanent (e.g. Welcome screen)
        if hasattr(self.main_window, "_is_permanent_tab") and self.main_window._is_permanent_tab(widget):
            return None

        title = tabs.tabText(index)
        tabs.removeTab(index)

        theme = getattr(self.main_window.services, "theme", None)
        theme_dict = theme.current if theme else {}

        win = DetachedTabWindow(
            widget=widget,
            title=title,
            original_tabs=tabs,
            original_index=index,
            theme=theme_dict,
            parent=None,
        )
        win.redock_requested.connect(self._on_redock)
        self.detached_windows.append(win)
        win.show()
        return win

    def _on_redock(self, win: DetachedTabWindow) -> None:
        """Handle re-dock request from floating window."""
        if win in self.detached_windows:
            self.detached_windows.remove(win)

    def close_all_detached(self) -> None:
        """Safely re-dock and close all detached windows on application exit."""
        for win in list(self.detached_windows):
            try:
                win.close()
            except Exception:
                pass
        self.detached_windows.clear()
