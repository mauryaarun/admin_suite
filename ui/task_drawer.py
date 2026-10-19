"""
Task Center UI: Drawer dialog and compact status-bar widget.

Provides a unified status bar indicator and a centralized task control drawer
where administrators can monitor and cancel all background operations.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.task_manager import BackgroundTask, TaskManager


class TaskItemWidget(QFrame):
    """Row widget rendering an individual BackgroundTask."""

    def __init__(self, task: BackgroundTask, manager: TaskManager, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.task = task
        self.manager = manager
        self.theme = theme

        bg = theme.get("panel2", "#2a2e32")
        border = theme.get("border", "#31363b")
        text = theme.get("text", "#eff0f1")
        accent = theme.get("accent", "#3daee9")
        danger = theme.get("danger", "#ff5555")
        ok = theme.get("ok", "#0dbc79")
        sub = theme.get("sub", "#95a5a6")

        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; padding: 4px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header: Category + Title + Status + Cancel
        head = QHBoxLayout()
        cat_lbl = QLabel(f"<b>[{task.category.upper()}]</b>")
        cat_lbl.setStyleSheet(f"color: {accent}; font-size: 11px;")
        head.addWidget(cat_lbl)

        title_lbl = QLabel(task.title)
        title_lbl.setStyleSheet(f"color: {text}; font-weight: bold; font-size: 13px;")
        head.addWidget(title_lbl, 1)

        if task.is_active:
            status_text = "Running..."
            status_color = accent
        elif task.status == "completed":
            status_text = "✓ Completed"
            status_color = ok
        elif task.status == "failed":
            status_text = "✕ Failed"
            status_color = danger
        else:
            status_text = "Cancelled"
            status_color = sub

        self.status_lbl = QLabel(status_text)
        self.status_lbl.setStyleSheet(f"color: {status_color}; font-size: 11px; font-weight: bold;")
        head.addWidget(self.status_lbl)

        if task.is_active and task.cancel_cb:
            cancel_btn = QPushButton("✕ Cancel")
            cancel_btn.setStyleSheet(
                f"background: {danger}; color: white; border-radius: 3px; padding: 2px 8px; font-size: 11px;"
            )
            cancel_btn.clicked.connect(lambda: self.manager.cancel_task(task.id))
            head.addWidget(cancel_btn)

        layout.addLayout(head)

        # Description / details
        if task.description or task.error:
            desc_text = task.error if task.error else task.description
            desc_lbl = QLabel(desc_text)
            desc_lbl.setStyleSheet(f"color: {danger if task.error else sub}; font-size: 11px;")
            desc_lbl.setWordWrap(True)
            layout.addWidget(desc_lbl)

        # Progress bar
        if task.is_active:
            self.bar = QProgressBar()
            self.bar.setFixedHeight(12)
            self.bar.setTextVisible(False)
            if task.progress >= 0:
                self.bar.setRange(0, 100)
                self.bar.setValue(task.progress)
            else:
                self.bar.setRange(0, 0)  # Indeterminate
            layout.addWidget(self.bar)

        # Footer: Elapsed time
        elapsed_str = f"Elapsed: {task.elapsed_seconds:.1f}s"
        time_lbl = QLabel(elapsed_str)
        time_lbl.setStyleSheet(f"color: {sub}; font-size: 10px;")
        layout.addWidget(time_lbl)


class TaskDrawerDialog(QDialog):
    """
    Central background tasks center popup dialog.
    """

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.manager: TaskManager = services.tasks
        self.theme = services.theme.current if hasattr(services, "theme") else {}

        self.setWindowTitle("Background Task Center")
        self.resize(600, 480)

        bg = self.theme.get("win", "#1b1e23")
        self.setStyleSheet(f"QDialog {{ background: {bg}; }}")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        title = QLabel("<b>⏳ Active & Background Operations</b>")
        title.setStyleSheet(f"color: {self.theme.get('accent', '#3daee9')}; font-size: 15px;")
        top.addWidget(title)
        top.addStretch()

        cancel_all_btn = QPushButton("Cancel All Active")
        cancel_all_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        cancel_all_btn.clicked.connect(self.manager.cancel_all)
        top.addWidget(cancel_all_btn)

        clear_btn = QPushButton("Clear Finished")
        clear_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        clear_btn.clicked.connect(self._clear_finished)
        top.addWidget(clear_btn)

        main_layout.addLayout(top)

        # Scroll container
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_widget)
        main_layout.addWidget(self.scroll, 1)

        # Auto-refresh timer while dialog is open
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_list)
        self.timer.start(500)

        self.refresh_list()

    def refresh_list(self) -> None:
        """Repopulate task list."""
        # Clear existing items
        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        tasks = self.manager.get_all_tasks()
        if not tasks:
            empty_lbl = QLabel("No background tasks running or recently executed.")
            empty_lbl.setStyleSheet(f"color: {self.theme.get('sub', '#888')}; padding: 20px; font-size: 13px;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.insertWidget(0, empty_lbl)
            return

        for task in tasks:
            item = TaskItemWidget(task, self.manager, self.theme, self.list_widget)
            self.list_layout.insertWidget(self.list_layout.count() - 1, item)

    def _clear_finished(self) -> None:
        self.manager.clear_finished()
        self.refresh_list()


class TaskStatusBarWidget(QWidget):
    """
    Compact status bar pill showing active task count and progress.
    Clicking it opens the TaskDrawerDialog.
    """

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.manager: TaskManager = services.tasks
        self.theme = services.theme.current if hasattr(services, "theme") else {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.btn = QPushButton("✓ Idle")
        self.btn.setFlat(True)
        self.btn.setStyleSheet(
            f"QPushButton {{ color: {self.theme.get('sub', '#888')}; font-size: 11px; padding: 2px 6px; }} "
            f"QPushButton:hover {{ color: {self.theme.get('accent', '#3daee9')}; }}"
        )
        self.btn.clicked.connect(self.open_drawer)
        layout.addWidget(self.btn)

        self.manager.active_count_changed.connect(self._on_count_changed)
        self.manager.task_updated.connect(self._on_task_updated)
        self._update_text()

    def _on_count_changed(self, count: int) -> None:
        self._update_text()

    def _on_task_updated(self, task: BackgroundTask) -> None:
        self._update_text()

    def _update_text(self) -> None:
        active = self.manager.get_active_tasks()
        accent = self.theme.get("accent", "#3daee9")
        sub = self.theme.get("sub", "#888")

        if not active:
            self.btn.setText("✓ Background: Idle")
            self.btn.setStyleSheet(f"color: {sub}; font-size: 11px; border: none;")
        elif len(active) == 1:
            t = active[0]
            pct = f" ({t.progress}%)" if t.progress >= 0 else ""
            self.btn.setText(f"⏳ {t.title}{pct}")
            self.btn.setStyleSheet(
                f"color: white; background: {accent}; border-radius: 3px; font-size: 11px; padding: 2px 8px; font-weight: bold;"
            )
        else:
            self.btn.setText(f"⏳ {len(active)} Tasks Running...")
            self.btn.setStyleSheet(
                f"color: white; background: {accent}; border-radius: 3px; font-size: 11px; padding: 2px 8px; font-weight: bold;"
            )

    def open_drawer(self) -> None:
        dialog = TaskDrawerDialog(self.services, self)
        dialog.exec()
