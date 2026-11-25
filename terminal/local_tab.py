"""
Local terminal tab.

Improvements:

- starts only after terminal readiness
- restart shell action
- kill confirmation
- context menu integration
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QPushButton

from admin_suite.terminal.base_tab import TerminalBaseTab
from admin_suite.terminal.bridge import WEBENGINE_AVAILABLE
from admin_suite.terminal.local_worker import LocalTerminalWorker


class LocalTerminalTab(TerminalBaseTab):
    """
    Local PTY terminal tab.
    """

    def __init__(
        self,
        services,
        command: str = "bash",
        name: str = "Local",
    ):
        self.command = command
        self.proc_worker: Optional[LocalTerminalWorker] = None
        self._manual_stop = False

        super().__init__(
            services,
            name=name,
            show_reconnect=False,
        )

        from admin_suite.core.icons import style_icon_button

        self.kill_btn = QPushButton()
        self.kill_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_icon_button(
            self.kill_btn,
            "kill",
            tooltip="Kill local process",
            color=self.theme.get("danger", "#ff5555"),
            min_width=34,
            min_height=28,
        )
        self.kill_btn.clicked.connect(self.confirm_kill_process)

        if hasattr(self, "toolbar_layout"):
            self.toolbar_layout.addWidget(self.kill_btn)

        self.terminal_ready.connect(self._on_terminal_ready)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _on_terminal_ready(self) -> None:
        if not self._manual_stop:
            self.start_process()

    def start_process(self) -> None:
        """
        Start local PTY process.
        """
        if not WEBENGINE_AVAILABLE:
            return

        self._manual_stop = False

        if self.proc_worker is not None:
            self.proc_worker.stop()
            self.proc_worker.wait(1000)

        self.set_status("● Starting...", self.theme.get("warn", "#ff0"))

        self.proc_worker = LocalTerminalWorker(
            command=self.command,
            name=self.name,
            cols=self._term_cols,
            rows=self._term_rows,
        )

        self.proc_worker.output_ready.connect(self._on_output)
        self.proc_worker.error_occurred.connect(self._on_error)
        self.proc_worker.connection_status.connect(self._on_status)
        self.proc_worker.connection_closed.connect(self._on_closed)

        self.proc_worker.start()

    # ------------------------------------------------------------------
    # Terminal hooks
    # ------------------------------------------------------------------

    def handle_input(self, data: str) -> None:
        """
        Forward typed input to local worker.
        """
        if self.proc_worker:
            self.proc_worker.write_input(data)

    def handle_resize(self, cols: int, rows: int) -> None:
        """
        Forward resize events to local PTY.
        """
        if self.proc_worker:
            self.proc_worker.resize_pty(cols, rows)

    # ------------------------------------------------------------------
    # Worker signals
    # ------------------------------------------------------------------

    def _on_output(self, data: str) -> None:
        self.write_output(data)

    def _on_status(self, status: str) -> None:
        if status == "connected":
            self.set_status("● Running", self.theme.get("ok", "#0f0"))
        else:
            self.set_status("● Idle", self.theme.get("sub", "#888"))

    def _on_error(self, error: str) -> None:
        self.write_output(f"\r\n\x1b[31m[ERROR] {error}\x1b[0m\r\n")
        self.set_status("● Error", self.theme.get("danger", "#f00"))

        self.services.notifications.push(
            "error",
            "Local Terminal",
            error,
        )

    def _on_closed(self) -> None:
        self.write_output("\r\n\x1b[33m[Local shell closed]\x1b[0m\r\n")
        self.set_status("● Exited", self.theme.get("sub", "#888"))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def restart_process(self) -> None:
        """
        Restart local shell.
        """
        self._manual_stop = False
        self.start_process()

    def kill_process(self) -> None:
        """
        Kill local process.
        """
        self._manual_stop = True

        if self.proc_worker:
            self.proc_worker.stop()

        self.set_status("● Killed", self.theme.get("danger", "#f00"))

    def confirm_kill_process(self) -> None:
        """
        Ask before killing local process.
        """
        result = QMessageBox.question(
            self,
            "Kill Process",
            "Kill the local terminal process?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if result == QMessageBox.StandardButton.Yes:
            self.kill_process()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def extend_context_menu(self, menu) -> None:
        self._add_menu_action(menu, "Restart Shell", self.restart_process)
        self._add_menu_action(menu, "Kill Process", self.confirm_kill_process)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """
        Stop local worker.
        """
        if self.proc_worker:
            self.proc_worker.stop()
            self.proc_worker.wait(1000)
            self.proc_worker = None

        super().cleanup()