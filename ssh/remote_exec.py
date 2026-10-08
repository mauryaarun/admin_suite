from __future__ import annotations

import subprocess
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from admin_suite.ssh.client import ssh_kwargs
from admin_suite.ssh.credentials import SshCredentials, profile_creds
from admin_suite.ssh.hostkeys import create_ssh_client


class RemoteExecThread(QThread):
    """
    Execute one command remotely or locally with optional sudo password piping.
    Emits both finished_cmd(out+err, rc) and finished_output(out, err, rc).
    """

    finished_cmd = pyqtSignal(str, int)
    finished_output = pyqtSignal(str, str, int)

    def __init__(
        self,
        profile: Optional[dict[str, Any]] = None,
        cmd: str = "",
        timeout: Optional[float] = None,
        sudo_password: Optional[str] = None,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self.cmd = cmd
        self.timeout = timeout
        self.sudo_password = sudo_password
        if self.sudo_password is None and profile:
            self.sudo_password = profile.get("sudo_pass") or None

    def run(self) -> None:
        try:
            is_local = (
                not self.profile
                or bool(self.profile.get("is_local"))
                or (
                    self.profile.get("ssh_host") in ("localhost", "127.0.0.1", "::1")
                    and bool(self.profile.get("use_local_exec", True))
                )
                or (
                    self.profile.get("host") in ("localhost", "127.0.0.1", "::1")
                    and bool(self.profile.get("use_local_exec", True))
                )
            )

            if is_local:
                p = subprocess.Popen(
                    ["bash", "-c", self.cmd],
                    stdin=subprocess.PIPE if self.sudo_password is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                out, err = p.communicate(
                    input=(self.sudo_password + "\n") if self.sudo_password else None,
                    timeout=self.timeout,
                )
                combined = out + (("\n" + err) if err else "")
                self.finished_cmd.emit(combined, p.returncode)
                self.finished_output.emit(out, err or "", p.returncode)
                return

            # Extract connection parameters from remote profile
            host = self.profile.get("ssh_host") or self.profile.get("host", "localhost")
            port = int(self.profile.get("ssh_port", 22) or 22)
            user = self.profile.get("ssh_user") or self.profile.get("user", "")

            # Extract credentials
            creds = profile_creds(self.profile)

            # Build SSH kwargs with connection parameters
            kw = ssh_kwargs(
                host=host,
                port=port,
                user=user,
                creds=creds,
            )

            # Create and connect SSH client
            client = create_ssh_client()
            client.connect(**kw)

            # Execute command
            stdin, stdout, stderr = client.exec_command(
                self.cmd,
                timeout=self.timeout,
            )

            if self.sudo_password:
                try:
                    stdin.write(self.sudo_password + "\n")
                    stdin.flush()
                except Exception:
                    pass

            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            rc = stdout.channel.recv_exit_status()

            client.close()
            combined = out + (("\n" + err) if err else "")
            self.finished_cmd.emit(combined, rc)
            self.finished_output.emit(out, err, rc)

        except subprocess.TimeoutExpired:
            self.finished_cmd.emit("[timeout]\n", 124)
            self.finished_output.emit("", "[timeout]\n", 124)
        except Exception as e:
            self.finished_cmd.emit(f"[error] {e}\n", -1)
            self.finished_output.emit("", f"[error] {e}\n", -1)