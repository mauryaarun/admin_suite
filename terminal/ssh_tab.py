"""
SSH terminal tab.

Improvements:

- starts only after terminal readiness
- exponential reconnect backoff with jitter
- manual disconnect support
- reconnect-on-close optional behavior
- SSH context menu actions
"""

from __future__ import annotations

import datetime
import random
import re
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from admin_suite.ssh.credentials import SshCredentials
from admin_suite.terminal.base_tab import TerminalBaseTab
from admin_suite.terminal.bridge import WEBENGINE_AVAILABLE
from admin_suite.terminal.ssh_worker import SshTerminalWorker


class SshTerminalTab(TerminalBaseTab):
    """
    SSH terminal tab using xterm.js and Paramiko.
    """

    def __init__(
        self,
        services,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        initial_cmd: str = "",
        name: str = "",
        use_jump: bool = False,
        jump_host: Optional[str] = None,
        jump_port: int = 22,
        jump_user: Optional[str] = None,
        jump_creds: Optional[SshCredentials] = None,
        use_agent: bool = False,
        profile_name: Optional[str] = None,
        strict_host_keys: Optional[bool] = None,
        profile_data: Optional[dict] = None,
    ):
        self.host = host
        self.user = user

        try:
            self.port = int(port) if port else 22
        except Exception:
            self.port = 22

        self.creds = creds
        self.initial_cmd = initial_cmd or ""

        self.use_jump = bool(use_jump)
        self.jump_host = jump_host
        self.jump_port = jump_port
        self.jump_user = jump_user
        self.jump_creds = jump_creds
        self.use_agent = bool(use_agent)

        display_name = name or f"{user}@{host}"
        self.profile_name = profile_name or display_name
        self.profile_data = profile_data or {}

        session_log_path = None

        if services.config.get("session_logging", True):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r"[^\w\-]", "_", display_name)
            session_log_path = str(
                services.paths.LOG_DIR / f"{safe}_{ts}.log"
            )

        if strict_host_keys is None:
            strict_host_keys = bool(
                services.config.get("ssh_strict_host_keys", False)
            )

        self.strict_host_keys = bool(strict_host_keys)

        super().__init__(
            services,
            name=display_name,
            show_reconnect=True,
            session_log_path=session_log_path,
        )

        self.ssh_worker: Optional[SshTerminalWorker] = None

        self._network_dialogs = []
        self.profile_data = {}

        self._manual_disconnect = False
        self._reconnect_attempts = 0

        try:
            max_reconnect = int(
                services.config.get("ssh_auto_reconnect_attempts", 3)
            )
        except Exception:
            max_reconnect = 3

        self._max_reconnect = (
            max_reconnect
            if services.config.get("auto_reconnect", True)
            else 0
        )

        self._reconnect_on_closed = bool(
            services.config.get("ssh_reconnect_on_closed", False)
        )

        self.terminal_ready.connect(self._on_terminal_ready)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _on_terminal_ready(self) -> None:
        if not self._manual_disconnect:
            self.start_ssh()

    def start_ssh(self) -> None:
        """
        Start or restart SSH connection.
        """
        if not WEBENGINE_AVAILABLE:
            return

        if self._manual_disconnect:
            return

        if self.ssh_worker is not None:
            self.ssh_worker.stop()
            self.ssh_worker.wait(1500)

        self.set_status("● Connecting...", self.theme.get("warn", "#ff0"))

        self.ssh_worker = SshTerminalWorker(
            self.host,
            self.port,
            self.user,
            self.creds,
            initial_cmd=self.initial_cmd,
            use_agent=self.use_agent,
            use_jump=self.use_jump,
            jump_host=self.jump_host,
            jump_port=self.jump_port,
            jump_user=self.jump_user,
            jump_creds=self.jump_creds,
            session_log_path=self.session_log_path,
            strict_host_keys=self.strict_host_keys,
        )

        self.ssh_worker.output_ready.connect(self._on_output)
        self.ssh_worker.error_occurred.connect(self._on_error)
        self.ssh_worker.connection_status.connect(self._on_status)
        self.ssh_worker.connection_closed.connect(self._on_closed)
        self.ssh_worker.latency_ms.connect(self._on_latency)

        self.ssh_worker.start()

    # ------------------------------------------------------------------
    # Terminal hooks
    # ------------------------------------------------------------------

    def handle_input(self, data: str) -> None:
        """
        Forward typed input to SSH worker.
        """
        if self.ssh_worker:
            self.ssh_worker.write_input(data)

    def handle_resize(self, cols: int, rows: int) -> None:
        """
        Forward resize events to SSH worker.
        """
        if self.ssh_worker:
            self.ssh_worker.resize_pty(cols, rows)

    # ------------------------------------------------------------------
    # Worker signals
    # ------------------------------------------------------------------

    def _on_output(self, data: str) -> None:
        self.write_output(data)

    def _on_latency(self, ms: int) -> None:
        self.set_latency(ms)

    def _on_status(self, status: str) -> None:
        if status == "connected":
            self.set_status("● Connected", self.theme.get("ok", "#0f0"))
            self._reconnect_attempts = 0

            self.services.notifications.push(
                "ok",
                "SSH Connected",
                f"{self.user}@{self.host}",
            )
            self.services.audit(
                "SSH",
                f"SSH session established: {self.name} ({self.user}@{self.host}:{self.port})",
            )

            QTimer.singleShot(250, self.force_focus)

        elif status == "error":
            self.set_status("● Error", self.theme.get("danger", "#f00"))

        else:
            self.set_status("● Connecting...", self.theme.get("warn", "#ff0"))

    def _on_error(self, error: str) -> None:
        self.write_output(f"\r\n\x1b[31m[ERROR] {error}\x1b[0m\r\n")
        self.set_status("● Error", self.theme.get("danger", "#f00"))

        self.services.notifications.push(
            "error",
            "SSH Error",
            f"{self.name}: {error}",
        )
        self.services.emit_log(
            "SSH",
            f"SSH error on {self.name}: {error}",
            "ERROR",
        )

        if self._manual_disconnect:
            return

        if self._reconnect_attempts < self._max_reconnect:
            self._reconnect_attempts += 1

            delay = min(
                1000 * (2 ** max(0, self._reconnect_attempts - 1)),
                30000,
            )
            delay += random.randint(0, 500)

            self.set_status(
                f"● Retrying in {delay // 1000}s",
                self.theme.get("warn", "#ff0"),
            )

            self.services.emit_log(
                "SSH",
                "Auto-reconnect "
                f"{self._reconnect_attempts}/{self._max_reconnect} "
                f"for {self.name} in {delay}ms",
                "WARN",
            )

            QTimer.singleShot(delay, self._auto_reconnect)

    def _on_closed(self) -> None:
        self.write_output("\r\n\x1b[33m[Connection closed]\x1b[0m\r\n")
        self.services.emit_log("SSH", f"SSH session closed: {self.name}", "INFO")

        if self._manual_disconnect:
            self.set_status("● Disconnected", self.theme.get("sub", "#888"))
            return

        if (
            self._reconnect_on_closed
            and self._reconnect_attempts < self._max_reconnect
        ):
            self._reconnect_attempts += 1

            delay = min(
                1000 * (2 ** max(0, self._reconnect_attempts - 1)),
                30000,
            )
            delay += random.randint(0, 500)

            self.set_status(
                f"● Reconnecting in {delay // 1000}s",
                self.theme.get("warn", "#ff0"),
            )

            QTimer.singleShot(delay, self._auto_reconnect)
        else:
            self.set_status("● Disconnected", self.theme.get("sub", "#888"))

    def _auto_reconnect(self) -> None:
        if self._manual_disconnect:
            return

        self.start_ssh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def reconnect(self) -> None:
        """
        Manual reconnect.
        """
        self._manual_disconnect = False
        self._reconnect_attempts = 0
        self.start_ssh()

    def disconnect_ssh(self) -> None:
        """
        Manual disconnect.
        """
        self._manual_disconnect = True

        if self.ssh_worker:
            self.ssh_worker.stop()
            self.ssh_worker.wait(1500)
            self.ssh_worker = None

        self.set_status("● Disconnected", self.theme.get("sub", "#888"))

    def send_command(self, command: str) -> None:
        """
        Send a command line to the remote shell.
        """
        self.handle_input(command + "\n")

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def extend_context_menu(self, menu) -> None:

        self._add_menu_action(menu, "Networking Tools…", self.open_networking_tools)
        menu.addSeparator()

        self._add_menu_action(menu, "Reconnect", self.reconnect)
        self._add_menu_action(menu, "Disconnect", self.disconnect_ssh)

        menu.addSeparator()

        self._add_menu_action(menu, "Copy SSH URI", self.copy_ssh_uri)
        self._add_menu_action(menu, "Copy SSH Command", self.copy_ssh_command)



    def open_networking_tools(self) -> None:
        from admin_suite.ui.networking_dialog import NetworkingToolsDialog

        dialog = NetworkingToolsDialog(
            services=self.services,
            host=self.host,
            port=self.port,
            username=self.user,
            creds=self.creds,
            parent=self,
            profile_data=self.profile_data,
        )

        self._network_dialogs.append(dialog)

        dialog.destroyed.connect(
            lambda *_: self._network_dialogs.remove(dialog)
            if dialog in self._network_dialogs
            else None
        )

        dialog.show()
        
    def copy_ssh_uri(self) -> None:
        uri = f"ssh://{self.user}@{self.host}:{self.port}"
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(uri)

    def copy_ssh_command(self) -> None:
        cmd = f"ssh -p {self.port} {self.user}@{self.host}"
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(cmd)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """
        Stop SSH worker.
        """
        if self.ssh_worker:
            self.ssh_worker.stop()
            self.ssh_worker.wait(1500)
            self.ssh_worker = None

        super().cleanup()