"""
Remote command execution worker used by SFTP remote search.

This will later be unified with the broader SSH execution service.
"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from admin_suite.ssh.client import ssh_kwargs
from admin_suite.ssh.credentials import SshCredentials
from admin_suite.ssh.hostkeys import create_ssh_client
from admin_suite.ssh.pool import get_ssh_pool


class RemoteExecThread(QThread):
    """
    Run one command remotely or locally.

    host_info format:

        {
            "host": "...",
            "port": 22,
            "user": "...",
            "creds": SshCredentials(...),
            "use_agent": False,
            "strict_host_keys": False,
        }

    If host_info is None or has no host, command runs locally with bash.
    """

    finished_cmd = pyqtSignal(str, int)

    def __init__(
        self,
        host_info: Optional[dict[str, Any]],
        cmd: str,
        timeout: int = 30,
    ):
        super().__init__()

        self.host_info = host_info or {}
        self.cmd = cmd
        self.timeout = timeout

    def run(self) -> None:
        try:
            if not self.host_info or not self.host_info.get("host"):
                r = subprocess.run(
                    ["bash", "-c", self.cmd],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )

                self.finished_cmd.emit(
                    (r.stdout or "") + (r.stderr or ""),
                    r.returncode,
                )

                return

            pool = get_ssh_pool()
            out, err, rc = pool.execute(
                self.host_info,
                self.cmd,
                timeout=self.timeout,
            )
            self.finished_cmd.emit(out + err, rc)

        except Exception as e:
            self.finished_cmd.emit(f"[error] {e}", -1)
