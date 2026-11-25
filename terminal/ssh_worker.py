"""
SSH terminal worker thread.

Improvements compared with the original implementation:

- uses structured SshCredentials
- fixes password vs passphrase handling
- uses Admin Suite host-key handling
- batches terminal output to reduce Qt signal churn
- incremental UTF-8 decoder
- session logging
- jump host support
"""

from __future__ import annotations

import codecs
import datetime
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

import paramiko

from admin_suite.ssh.client import ssh_kwargs
from admin_suite.ssh.credentials import SshCredentials
from admin_suite.ssh.hostkeys import create_ssh_client


class SshTerminalWorker(QThread):
    """
    Interactive SSH terminal worker.
    """

    output_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    connection_status = pyqtSignal(str)
    connection_closed = pyqtSignal()
    latency_ms = pyqtSignal(int)

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        initial_cmd: str = "",
        use_agent: bool = False,
        use_jump: bool = False,
        jump_host: Optional[str] = None,
        jump_port: int = 22,
        jump_user: Optional[str] = None,
        jump_creds: Optional[SshCredentials] = None,
        session_log_path: Optional[str | Path] = None,
        strict_host_keys: bool = False,
        cols: int = 80,
        rows: int = 24,
    ):
        super().__init__()

        self.host = host
        self.user = user

        try:
            self.port = int(port) if port else 22
        except Exception:
            self.port = 22

        self.creds = creds

        self.initial_cmd = initial_cmd or ""
        self.use_agent = bool(use_agent)

        self.use_jump = bool(use_jump)
        self.jump_host = jump_host or None
        self.jump_user = jump_user or None
        self.jump_creds = jump_creds or SshCredentials()

        try:
            self.jump_port = int(jump_port) if jump_port else 22
        except Exception:
            self.jump_port = 22

        self.session_log_path = session_log_path
        self.strict_host_keys = bool(strict_host_keys)
        self._cols = max(1, int(cols))
        self._rows = max(1, int(rows))

        self.running = True

        self.client: Optional[paramiko.SSHClient] = None
        self.channel: Optional[paramiko.Channel] = None
        self.jump_client: Optional[paramiko.SSHClient] = None

        self._lock = threading.Lock()
        self._pending = ""
        self._log_file = None

    def run(self) -> None:
        try:
            self._open_session_log()

            self.connection_status.emit("connecting")

            self.client = create_ssh_client(strict=self.strict_host_keys)

            kw = ssh_kwargs(
                self.host,
                self.port,
                self.user,
                self.creds,
                use_agent=self.use_agent,
            )

            if self.use_jump and self.jump_host:
                self.connection_status.emit("connecting (jump)")

                self.jump_client = create_ssh_client(strict=self.strict_host_keys)

                jkw = ssh_kwargs(
                    self.jump_host,
                    self.jump_port,
                    self.jump_user,
                    self.jump_creds,
                    use_agent=False,
                )

                self.jump_client.connect(**jkw)

                transport = self.jump_client.get_transport()

                sock = transport.open_channel(
                    "direct-tcpip",
                    (self.host, self.port),
                    ("127.0.0.1", 0),
                )

                kw["sock"] = sock

            self.client.connect(**kw)

            self.connection_status.emit("connected")

            with self._lock:
                cols, rows = self._cols, self._rows

            self.channel = self.client.invoke_shell(
                term="xterm-256color",
                width=cols,
                height=rows,
            )

            with self._lock:
                latest_cols, latest_rows = self._cols, self._rows
            if (latest_cols, latest_rows) != (cols, rows):
                try:
                    self.channel.resize_pty(width=latest_cols, height=latest_rows)
                except Exception:
                    pass

            if self.initial_cmd:
                time.sleep(0.35)
                self.channel.sendall((self.initial_cmd + "\n").encode("utf-8"))

            threading.Thread(target=self._latency_loop, daemon=True).start()

            decoder = codecs.getincrementaldecoder("utf-8")("replace")

            while self.running:
                # Send pending user input.
                with self._lock:
                    if self._pending:
                        try:
                            self.channel.sendall(self._pending.encode("utf-8"))

                            if self._log_file:
                                self._log_file.write(self._pending)
                                self._log_file.flush()

                        except Exception:
                            self.running = False
                            break

                        self._pending = ""

                if not self.running:
                    break

                # Read all currently available output.
                try:
                    chunks = []

                    while True:
                        try:
                            if not self.channel.recv_ready():
                                break

                            raw = self.channel.recv(65536)

                            if not raw:
                                break

                            chunks.append(raw)

                        except Exception:
                            break

                    while True:
                        try:
                            if not self.channel.recv_stderr_ready():
                                break

                            raw = self.channel.recv_stderr(65536)

                            if not raw:
                                break

                            chunks.append(raw)

                        except Exception:
                            break

                    if chunks:
                        text = decoder.decode(b"".join(chunks))

                        if text:
                            self.output_ready.emit(text)

                            if self._log_file:
                                self._log_file.write(text)
                                self._log_file.flush()

                    elif self.channel.exit_status_ready():
                        break

                    else:
                        time.sleep(0.01)

                except (EOFError, socket.timeout):
                    break

                except Exception:
                    break

            self.connection_closed.emit()

        except Exception as e:
            if self.running:
                self.error_occurred.emit(f"Connection error: {e}")
                self.connection_status.emit("error")

        finally:
            self._cleanup()

    def _open_session_log(self) -> None:
        if not self.session_log_path:
            return

        try:
            path = Path(self.session_log_path)

            if path.parent:
                path.parent.mkdir(parents=True, exist_ok=True)

            self._log_file = open(path, "a", encoding="utf-8")

            self._log_file.write(
                f"\n=== Session started {datetime.datetime.now().isoformat()} ===\n"
            )

        except Exception:
            self._log_file = None

    def _latency_loop(self) -> None:
        while self.running:
            time.sleep(10)

            try:
                transport = self.client.get_transport() if self.client else None

                if transport and transport.is_active():
                    start = time.perf_counter()

                    transport.global_request(
                        "keepalive@openssh.com",
                        wait=True,
                    )

                    elapsed_ms = int((time.perf_counter() - start) * 1000)

                    self.latency_ms.emit(elapsed_ms)

                else:
                    self.latency_ms.emit(-1)

            except Exception:
                self.latency_ms.emit(-1)

    def write_input(self, data: str) -> None:
        """
        Queue user input.
        """
        with self._lock:
            self._pending += data

    def resize_pty(self, cols: int, rows: int) -> None:
        """
        Resize remote PTY.
        """
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        with self._lock:
            self._cols = cols
            self._rows = rows
            chan = self.channel

        if not chan:
            return

        try:
            chan.resize_pty(
                width=cols,
                height=rows,
            )
        except Exception:
            pass

    def stop(self) -> None:
        """
        Stop worker and clean up.
        """
        self.running = False
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            if self._log_file:
                self._log_file.write(
                    f"\n=== Session ended {datetime.datetime.now().isoformat()} ===\n"
                )
                self._log_file.close()

        except Exception:
            pass

        finally:
            self._log_file = None

        for attr in ("channel", "client", "jump_client"):
            try:
                obj = getattr(self, attr, None)

                if obj:
                    obj.close()

            except Exception:
                pass

            try:
                setattr(self, attr, None)
            except Exception:
                pass
