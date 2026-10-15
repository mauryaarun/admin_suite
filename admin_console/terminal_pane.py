"""
Live Terminal Output Pane for Admin Console.
Displays raw command execution output, execution timestamps, exit codes,
and provides an interactive ad-hoc server command prompt.
"""

from __future__ import annotations

import datetime
import html
import time
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class TerminalOutputPane(QWidget):
    """
    Terminal output pane for Admin Console.
    Shows raw stdout/stderr of executed commands in real time,
    with interactive execution prompt, history, copy, clear, and search.
    """

    execute_requested = pyqtSignal(str, bool)  # command, use_sudo
    visibility_toggle_requested = pyqtSignal()

    def __init__(self, services, host_name: str = "Localhost", parent=None):
        super().__init__(parent)
        self.services = services
        self.host_name = host_name
        self.theme = self.services.theme.current

        self._history: list[str] = []
        self._history_idx: int = -1
        self._is_collapsed: bool = False
        self._start_time: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 1. Header Toolbar
        header = QFrame()
        header.setStyleSheet(
            f"background: {self.theme.get('panel2', '#1e222b')}; border-radius: 4px; padding: 2px;"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 2, 6, 2)
        h_layout.setSpacing(8)

        self.title_lbl = QLabel(f"📟 Terminal Output — {self.host_name}")
        self.title_lbl.setStyleSheet(f"font-weight: bold; color: {self.theme.get('accent', '#3daee9')}; font-size: 12px;")
        h_layout.addWidget(self.title_lbl)

        self.status_badge = QLabel("● Idle")
        self.status_badge.setStyleSheet(
            "background: #2e7d32; color: #ffffff; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;"
        )
        h_layout.addWidget(self.status_badge)

        h_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter output...")
        self.search_input.setFixedWidth(130)
        self.search_input.setStyleSheet("font-size: 11px; padding: 1px 4px;")
        self.search_input.textChanged.connect(self._filter_output)
        h_layout.addWidget(self.search_input)

        self.autoscroll_chk = QCheckBox("Auto-scroll")
        self.autoscroll_chk.setChecked(True)
        self.autoscroll_chk.setStyleSheet("font-size: 11px;")
        h_layout.addWidget(self.autoscroll_chk)

        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setToolTip("Copy all terminal output to clipboard")
        self.btn_copy.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.btn_copy.clicked.connect(self.copy_all)
        h_layout.addWidget(self.btn_copy)

        self.btn_clear = QPushButton("🧹 Clear")
        self.btn_clear.setToolTip("Clear terminal output")
        self.btn_clear.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.btn_clear.clicked.connect(self.clear_output)
        h_layout.addWidget(self.btn_clear)

        self.btn_toggle = QPushButton("▼")
        self.btn_toggle.setToolTip("Collapse/Expand terminal pane")
        self.btn_toggle.setFixedWidth(24)
        self.btn_toggle.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.btn_toggle.clicked.connect(self._toggle_collapse)
        h_layout.addWidget(self.btn_toggle)

        layout.addWidget(header)

        # 2. Main Terminal Output View
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont("JetBrains Mono, DejaVu Sans Mono, Consolas, monospace", 10))
        self.output_text.setStyleSheet(
            "background-color: #0e1117; color: #dcdfe4; selection-background-color: #264f78; border: 1px solid #282c34;"
        )
        layout.addWidget(self.output_text, 1)

        # 3. Interactive Ad-hoc Command Bar
        self.cmd_bar = QFrame()
        self.cmd_bar.setStyleSheet(f"background: {self.theme.get('panel2', '#1e222b')}; border-radius: 4px;")
        cmd_layout = QHBoxLayout(self.cmd_bar)
        cmd_layout.setContentsMargins(6, 3, 6, 3)
        cmd_layout.setSpacing(6)

        self.prompt_label = QLabel(f"admin@{self.host_name.split()[0].lower()}:~$")
        self.prompt_label.setStyleSheet(f"color: {self.theme.get('accent', '#3daee9')}; font-family: monospace; font-weight: bold;")
        cmd_layout.addWidget(self.prompt_label)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Type a shell command to execute on server (e.g. uname -a, iptables -L -n, df -h)...")
        self.cmd_input.setFont(QFont("JetBrains Mono, Consolas, monospace", 10))
        self.cmd_input.returnPressed.connect(self._on_cmd_enter)
        self.cmd_input.installEventFilter(self)
        cmd_layout.addWidget(self.cmd_input, 1)

        self.sudo_chk = QCheckBox("sudo")
        self.sudo_chk.setChecked(True)
        self.sudo_chk.setToolTip("Elevate command with sudo")
        cmd_layout.addWidget(self.sudo_chk)

        self.btn_run = QPushButton("▶ Run")
        self.btn_run.setStyleSheet("background: #1976d2; color: #fff; font-weight: bold; padding: 2px 10px;")
        self.btn_run.clicked.connect(self._on_cmd_enter)
        cmd_layout.addWidget(self.btn_run)

        layout.addWidget(self.cmd_bar)

        self._print_welcome()

    def _print_welcome(self):
        t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.output_text.append(
            f"<div style='color:#5c6370;'>[{t}] Admin Console Terminal Output Pane initialized. Raw command output from all operations will stream here.</div>"
        )

    def set_host(self, host_name: str):
        self.host_name = host_name
        self.title_lbl.setText(f"📟 Terminal Output — {host_name}")
        clean = host_name.split()[0].lower()
        self.prompt_label.setText(f"admin@{clean}:~$")

    def log_command_start(self, cmd: str, use_sudo: bool = False):
        """Called when a command starts executing."""
        self._start_time = time.perf_counter()
        self._active_count = getattr(self, "_active_count", 0) + 1
        t = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = "sudo " if use_sudo and not cmd.strip().startswith("sudo") else ""
        escaped_cmd = html.escape(f"{prefix}{cmd.strip()}")

        self.status_badge.setText(
            f"● Executing ({self._active_count})..." if self._active_count > 1 else "● Executing..."
        )
        self.status_badge.setStyleSheet(
            "background: #e65100; color: #ffffff; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;"
        )

        header_html = (
            f"<div style='margin-top:6px; color:#61afef; font-weight:bold;'>"
            f"<span style='color:#7f848e;'>[{t}]</span> ⚡ $ {escaped_cmd}"
            f"</div>"
        )
        self.output_text.append(header_html)
        self._maybe_scroll()

    def log_command_output(self, stdout: str, stderr: str = "", rc: int = 0, elapsed: Optional[float] = None):
        """Called when a command finishes."""
        self._active_count = max(0, getattr(self, "_active_count", 1) - 1)
        if elapsed is None:
            elapsed = time.perf_counter() - self._start_time if self._start_time > 0 else 0.0

        if stdout:
            escaped_out = html.escape(stdout.rstrip())
            self.output_text.append(f"<pre style='margin:0; color:#abb2bf; font-family:monospace;'>{escaped_out}</pre>")

        if stderr:
            escaped_err = html.escape(stderr.rstrip())
            self.output_text.append(f"<pre style='margin:0; color:#e06c75; font-family:monospace;'>{escaped_err}</pre>")

        if rc == 0:
            status_color = "#98c379"
            status_text = f"✔ Exited (code 0) in {elapsed:.2f}s"
            if self._active_count == 0:
                self.status_badge.setText("● Idle")
                self.status_badge.setStyleSheet(
                    "background: #2e7d32; color: #ffffff; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;"
                )
            else:
                self.status_badge.setText(f"● Executing ({self._active_count})...")
        else:
            status_color = "#e06c75"
            status_text = f"✘ Failed (code {rc}) in {elapsed:.2f}s"
            if self._active_count == 0:
                self.status_badge.setText(f"● Error ({rc})")
                self.status_badge.setStyleSheet(
                    "background: #c62828; color: #ffffff; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;"
                )
            else:
                self.status_badge.setText(f"● Executing ({self._active_count})...")

        footer_html = f"<div style='color:{status_color}; font-size:10px; margin-bottom:6px;'>[{status_text}]</div>"
        self.output_text.append(footer_html)
        self._maybe_scroll()

    def _maybe_scroll(self):
        if self.autoscroll_chk.isChecked():
            cursor = self.output_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.output_text.setTextCursor(cursor)

    def _on_cmd_enter(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        self._history.append(cmd)
        self._history_idx = len(self._history)
        self.cmd_input.clear()
        use_sudo = self.sudo_chk.isChecked()
        self.execute_requested.emit(cmd, use_sudo)

    def copy_all(self):
        QApplication.clipboard().setText(self.output_text.toPlainText())
        self.services.notifications.push("info", "Terminal", "Terminal output copied to clipboard")

    def clear_output(self):
        self.output_text.clear()
        self._print_welcome()

    def _filter_output(self, text: str):
        # Highlighting matching text in the output view
        if not text:
            return
        cursor = self.output_text.textCursor()
        # Find and move
        self.output_text.find(text)

    def _toggle_collapse(self):
        self._is_collapsed = not self._is_collapsed
        self.output_text.setVisible(not self._is_collapsed)
        self.cmd_bar.setVisible(not self._is_collapsed)
        self.btn_toggle.setText("▲" if self._is_collapsed else "▼")
        self.visibility_toggle_requested.emit()

    def eventFilter(self, obj, event):
        # Handle Up/Down arrow for command history
        if obj == self.cmd_input and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                if self._history and self._history_idx > 0:
                    self._history_idx -= 1
                    self.cmd_input.setText(self._history[self._history_idx])
                return True
            elif key == Qt.Key.Key_Down:
                if self._history and self._history_idx < len(self._history) - 1:
                    self._history_idx += 1
                    self.cmd_input.setText(self._history[self._history_idx])
                elif self._history_idx >= len(self._history) - 1:
                    self._history_idx = len(self._history)
                    self.cmd_input.clear()
                return True
        return super().eventFilter(obj, event)
