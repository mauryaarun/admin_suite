"""
Local PTY terminal worker.

Improvements:

- queue-based input
- partial-write safe output
- queued resize requests
- incremental UTF-8 decoding
- EIO / EOF handling
- safer process termination
"""

from __future__ import annotations

import codecs
import errno
import os
import queue
import subprocess
import threading
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


class LocalTerminalWorker(QThread):
    """
    Runs a local shell through a PTY.

    Currently POSIX-only.
    """

    output_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    connection_status = pyqtSignal(str)
    connection_closed = pyqtSignal()

    def __init__(
        self,
        command: str = "bash",
        name: str = "Local",
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: int = 80,
        rows: int = 24,
    ):
        super().__init__()

        self.command = command or "bash"
        self.name = name
        self.cwd = cwd
        self.extra_env = env or {}
        self._cols = max(1, int(cols))
        self._rows = max(1, int(rows))

        self.running = True
        self.process: Optional[subprocess.Popen] = None
        self.master_fd: Optional[int] = None

        self._input_queue: queue.Queue[bytes] = queue.Queue()
        self._resize_queue: queue.Queue[tuple[int, int]] = queue.Queue()
        self._write_buffer = b""
        self._decoder = None
        self._kill_timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------
    # QThread entrypoint
    # ------------------------------------------------------------------

    def run(self) -> None:
        if os.name == "nt":
            self.error_occurred.emit(
                "Local PTY terminal is currently supported on POSIX systems only."
            )
            return

        try:
            import fcntl
            import pty
            import select
            import struct
            import termios
        except Exception as exc:
            self.error_occurred.emit(f"PTY support unavailable: {exc}")
            return

        master_fd = None
        slave_fd = None
        started = False

        try:
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd

            # Apply initial terminal dimensions before starting the shell process.
            try:
                winsize = struct.pack("HHHH", self._rows, self._cols, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env.update(self.extra_env)

            cmd_args = self._build_cmd_args()

            self.process = subprocess.Popen(
                cmd_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=self.cwd,
                close_fds=True,
                start_new_session=True,
            )

            os.close(slave_fd)
            slave_fd = None

            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

            started = True
            self.connection_status.emit("connected")

            drain_attempts = 0

            while self.running:
                exited = self.process.poll() is not None

                self._process_resize_queue(fcntl, termios, struct, master_fd)
                self._fill_write_buffer()

                read_fds = [master_fd]
                write_fds = [master_fd] if self._write_buffer else []

                try:
                    ready_read, ready_write, _ = select.select(
                        read_fds,
                        write_fds,
                        [],
                        0.05,
                    )
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise

                had_activity = False

                if master_fd in ready_write and self._write_buffer:
                    had_activity = True

                    try:
                        written = os.write(master_fd, self._write_buffer)
                        if written > 0:
                            self._write_buffer = self._write_buffer[written:]
                    except BlockingIOError:
                        pass
                    except OSError as exc:
                        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            pass
                        elif exc.errno == errno.EIO:
                            break
                        else:
                            break

                if master_fd in ready_read:
                    had_activity = True

                    try:
                        data = os.read(master_fd, 65536)
                    except BlockingIOError:
                        data = None
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        elif exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            data = None
                        else:
                            break

                    if data:
                        text = self._decoder.decode(data, False)
                        if text:
                            self.output_ready.emit(text)
                    elif data == b"":
                        break

                if (
                    exited
                    and not self._write_buffer
                    and self._input_queue.empty()
                    and not had_activity
                ):
                    drain_attempts += 1
                    if drain_attempts > 20:
                        break
                else:
                    drain_attempts = 0

            if self._decoder is not None:
                try:
                    tail = self._decoder.decode(b"", True)
                    if tail:
                        self.output_ready.emit(tail)
                except Exception:
                    pass

            return_code = self._wait_for_process_exit()

            if return_code is not None:
                self.output_ready.emit(
                    f"\r\n\x1b[90m[Process exited: {return_code}]\x1b[0m\r\n"
                )

            self.connection_closed.emit()

        except Exception as exc:
            self.error_occurred.emit(f"Local process error: {exc}")

            if started:
                self.connection_closed.emit()

        finally:
            self.master_fd = None

            if self._kill_timer is not None:
                try:
                    self._kill_timer.cancel()
                except Exception:
                    pass

            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except Exception:
                    pass

            if master_fd is not None:
                try:
                    os.close(master_fd)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    def _build_cmd_args(self) -> list[str]:
        """
        Build shell command arguments.

        If the command is a known shell executable, run it interactively.
        Otherwise execute it through bash -c.
        """
        cmd = (self.command or "bash").strip()
        if not cmd:
            return ["bash", "-i"]

        parts = cmd.split()
        exe = os.path.basename(parts[0])

        interactive_shells = {
            "bash",
            "dash",
            "sh",
            "zsh",
            "fish",
            "ksh",
            "csh",
            "tcsh",
        }

        if len(parts) == 1 and exe in interactive_shells:
            return [cmd, "-i"]

        if cmd == "bash":
            return ["bash", "-i"]

        return ["bash", "-c", cmd]

    # ------------------------------------------------------------------
    # PTY queue helpers
    # ------------------------------------------------------------------

    def _process_resize_queue(self, fcntl, termios, struct, master_fd: int) -> None:
        while True:
            try:
                cols, rows = self._resize_queue.get_nowait()
            except queue.Empty:
                break

            try:
                winsize = struct.pack(
                    "HHHH",
                    int(rows),
                    int(cols),
                    0,
                    0,
                )
                fcntl.ioctl(
                    master_fd,
                    termios.TIOCSWINSZ,
                    winsize,
                )
            except Exception:
                pass

    def _fill_write_buffer(self) -> None:
        while len(self._write_buffer) < 65536:
            try:
                chunk = self._input_queue.get_nowait()
                self._write_buffer += chunk
            except queue.Empty:
                break

    # ------------------------------------------------------------------
    # Process lifecycle helpers
    # ------------------------------------------------------------------

    def _wait_for_process_exit(self) -> Optional[int]:
        if self.process is None:
            return None

        if self.process.poll() is None:
            self._terminate_process()

        try:
            return self.process.wait(timeout=2)
        except Exception:
            self._force_kill()

            try:
                return self.process.wait(timeout=1)
            except Exception:
                return None

    def _terminate_process(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return

        try:
            import signal

            os.killpg(
                os.getpgid(self.process.pid),
                signal.SIGTERM,
            )
        except Exception:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _force_kill(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return

        try:
            import signal

            os.killpg(
                os.getpgid(self.process.pid),
                signal.SIGKILL,
            )
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_input(self, data: str) -> None:
        """
        Queue user input.
        """
        if not data:
            return

        try:
            self._input_queue.put(data.encode("utf-8", errors="replace"))
        except Exception:
            pass

    def resize_pty(self, cols: int, rows: int) -> None:
        """
        Queue resize request.
        """
        try:
            self._resize_queue.put((int(cols), int(rows)))
        except Exception:
            pass

    def stop(self) -> None:
        """
        Stop local shell.
        """
        self.running = False

        if self.process is not None and self.process.poll() is None:
            self._terminate_process()

            try:
                if self._kill_timer is not None:
                    self._kill_timer.cancel()
            except Exception:
                pass

            self._kill_timer = threading.Timer(1.5, self._force_kill)
            self._kill_timer.daemon = True
            self._kill_timer.start()