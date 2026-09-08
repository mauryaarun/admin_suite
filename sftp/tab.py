"""
Main SFTP tab with local/remote panels, transfer queue, and status log.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shlex
import subprocess
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from admin_suite.sftp.dialogs import ChmodDialog
from admin_suite.sftp.editor import RemoteEditorTab
from admin_suite.sftp.exec_worker import RemoteExecThread
from admin_suite.sftp.file_browser import FileBrowserPanel
from admin_suite.sftp.models import SftpAction, SftpTask
from admin_suite.sftp.rsync import RsyncDialog
from admin_suite.sftp.search import RemoteSearchDialog
from admin_suite.sftp.worker import SftpWorker

from admin_suite.terminal import SshTerminalTab



class SFTPTab(QWidget):
    """SFTP browser tab."""

    def __init__(
        self, services, main_window=None, *,
        host: str = "", port: int = 22, user: str = "", creds=None,
        name: str = "", use_agent: bool = False, strict_host_keys: Optional[bool] = None,
    ):
        super().__init__(main_window)
        self.services = services
        self.main_window = main_window
        self.host = host
        self.user = user
        try:
            self.port = int(port) if port else 22
        except Exception:
            self.port = 22
        self.creds = creds
        self.name = name
        self.use_agent = bool(use_agent)

        if strict_host_keys is None:
            strict_host_keys = bool(self.services.config.get("ssh_strict_host_keys", False))
        self.strict_host_keys = bool(strict_host_keys)

        self.host_info = {
            "host": self.host, "port": self.port, "user": self.user,
            "creds": self.creds, "use_agent": self.use_agent,
            "strict_host_keys": self.strict_host_keys,
        }

        # Transfer queue state
        self._queue: list[SftpTask] = []
        self._active_transfer: Optional[SftpWorker] = None
        self._active_task: Optional[SftpTask] = None
        self._xfer_start: float = 0.0

        # Short-lived op workers
        self._op_workers: list[SftpWorker] = []
        self._probe: Optional[RemoteExecThread] = None

        # Remote console state
        self._console_cwd: str = ""
        self._console_home: str = ""
        self._home_probe: Optional[RemoteExecThread] = None
        self._console_worker: Optional[RemoteExecThread] = None
        self._terminal_process: Optional[subprocess.Popen] = None

        # Tasks pane state
        self._tasks_visible: bool = True

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- header ----
        head = QHBoxLayout()
        title = QLabel(f"🔗 SFTP — {user}@{host}:{self.port}")
        title.setStyleSheet(f"color:{theme['accent']};font-weight:bold;padding:4px;")
        head.addWidget(title)

        self.conn_pill = QLabel("● idle")
        self.conn_pill.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        head.addWidget(self.conn_pill)
        head.addStretch()

        test_btn = QPushButton("🩺 Test")
        test_btn.setToolTip("Test SSH connectivity")
        test_btn.clicked.connect(self.test_connection)
        head.addWidget(test_btn)
        layout.addLayout(head)

        # ---- action bar ----
        ab = QHBoxLayout()
        up = QPushButton("⬆ Upload Selected")
        up.clicked.connect(self.upload_selected)
        dn = QPushButton("⬇ Download Selected")
        dn.clicked.connect(self.download_selected)
        srch = QPushButton("🔎 Remote Search…")
        srch.clicked.connect(self.remote_search)
        rsync_btn = QPushButton("🔄 Rsync Folder")
        rsync_btn.clicked.connect(self.open_rsync)

        self.tasks_toggle = QPushButton("▼ Tasks")
        self.tasks_toggle.setToolTip("Show/hide the transfer tasks queue")
        self.tasks_toggle.setCheckable(True)
        self.tasks_toggle.setChecked(True)
        self.tasks_toggle.clicked.connect(self._toggle_tasks)
        self.tasks_toggle.setStyleSheet(
            f"padding:4px 10px;color:{theme['sub']};font-size:11px;"
        )

        hint = QLabel("Tip: drag files/dirs between panels")
        hint.setStyleSheet(f"color:{theme['sub']};font-size:11px;")

        ab.addWidget(up)
        ab.addWidget(dn)
        ab.addWidget(srch)
        ab.addWidget(rsync_btn)
        ab.addStretch()
        ab.addWidget(self.tasks_toggle)
        ab.addWidget(hint)
        layout.addLayout(ab)

        # ---- main vertical splitter ----
        self.main_vsplitter = QSplitter(Qt.Orientation.Vertical)

        # Horizontal panels
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.local_panel = FileBrowserPanel(self.services, "local", mode="local")
        self.local_panel.file_action.connect(self.on_file_action)

        self.remote_panel = FileBrowserPanel(self.services, "remote", mode="remote")
        self.remote_panel.configure_remote(
            self.host, self.port, self.user, self.creds,
            use_agent=self.use_agent, strict_host_keys=self.strict_host_keys,
        )
        self.remote_panel.file_action.connect(self.on_file_action)

        h_splitter.addWidget(self.local_panel)
        h_splitter.addWidget(self.remote_panel)
        h_splitter.setSizes([600, 600])

        # Tasks container
        self.tasks_container = QWidget()
        tasks_lay = QVBoxLayout(self.tasks_container)
        tasks_lay.setContentsMargins(0, 0, 0, 0)
        tasks_lay.setSpacing(2)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setColumnCount(3)
        self.queue_tree.setHeaderLabels(["Task", "Status", "Type"])
        self.queue_tree.setMaximumHeight(140)
        self.queue_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        cancel_btn = QPushButton("⛔ Cancel Current")
        cancel_btn.clicked.connect(self.cancel_current)
        cancel_row.addWidget(cancel_btn)

        tasks_lay.addWidget(self.queue_tree)
        tasks_lay.addLayout(cancel_row)
        self.tasks_container.setVisible(True)

        # Bottom tabs: log + console
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setMinimumHeight(120)

        # Tab 1: Operation Log
        self.sftp_log = QPlainTextEdit()
        self.sftp_log.setReadOnly(True)
        self.sftp_log.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.sftp_log.setPlaceholderText("SFTP operation log…")
        self.sftp_log.setStyleSheet(
            f"background:{theme['panel']};border:1px solid {theme['border']};"
        )
        self.bottom_tabs.addTab(self.sftp_log, "📜 Operation Log")


        self._open_remote_terminal





        # Tab 2: Remote Console (External Terminal Launcher)

        console_widget = QWidget()
        console_lay = QVBoxLayout(console_widget)
        console_lay.setContentsMargins(10, 10, 10, 10)

        console_info = QLabel(
            "Launch a full interactive SSH terminal session to the remote host.\n"
            "This opens an external terminal window with complete shell access."
        )
        console_info.setStyleSheet(f"color:{theme['text']};font-size:12px;margin-bottom:10px;")
        console_info.setWordWrap(True)
        console_lay.addWidget(console_info)



        term_btn_row = QHBoxLayout()
        
        self.launch_term_btn = QPushButton("🖥️ Launch SSH Terminal")
        self.launch_term_btn.setStyleSheet(
            f"background:{theme['accent']};color:white;padding:10px 20px;"
            f"font-size:13px;font-weight:bold;border-radius:4px;"
        )
        #self.launch_term_btn.clicked.connect(self._launch_external_terminal)
        self.launch_term_btn.clicked.connect(self._open_remote_terminal)
        term_btn_row.addWidget(self.launch_term_btn)

        self.term_status = QLabel("Ready to launch")
        self.term_status.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        term_btn_row.addWidget(self.term_status)
        term_btn_row.addStretch()

        console_lay.addLayout(term_btn_row)

        # Quick command execution (fallback)
        console_lay.addSpacing(15)
        quick_label = QLabel("Or execute a quick command:")
        quick_label.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        console_lay.addWidget(quick_label)

        cmd_row = QHBoxLayout()
        self.console_prompt = QLabel(f"{self.user}@{self.host}:~$ ")
        self.console_prompt.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;"
            f"font-family:'JetBrains Mono, Consolas';"
        )
        cmd_row.addWidget(self.console_prompt)

        self.console_input = QLineEdit()
        self.console_input.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.console_input.setPlaceholderText("Enter command and press Enter…")
        self.console_input.returnPressed.connect(self._run_console_cmd)
        cmd_row.addWidget(self.console_input, 1)

        console_lay.addLayout(cmd_row)

        self.console_out = QPlainTextEdit()
        self.console_out.setReadOnly(True)
        self.console_out.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.console_out.setMaximumHeight(100)
        self.console_out.setStyleSheet(
            f"background:{theme['panel']};color:{theme['text']};"
            f"border:1px solid {theme['border']};"
        )
        self.console_out.setPlaceholderText("Quick command output will appear here...")
        console_lay.addWidget(self.console_out)

        self.bottom_tabs.addTab(console_widget, "💻 Send Remote Commands")

        # Assemble main vertical splitter
        self.main_vsplitter.addWidget(h_splitter)
        self.main_vsplitter.addWidget(self.tasks_container)
        self.main_vsplitter.addWidget(self.bottom_tabs)
        self.main_vsplitter.setStretchFactor(0, 4)
        self.main_vsplitter.setStretchFactor(1, 1)
        self.main_vsplitter.setStretchFactor(2, 1)
        self.main_vsplitter.setSizes([600, 150, 220])

        layout.addWidget(self.main_vsplitter, 1)

        # ---- progress + speed ----
        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        prog_row.addWidget(self.progress, 1)

        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        prog_row.addWidget(self.speed_label)
        layout.addLayout(prog_row)

        # ---- status ----
        self.status_label = QLabel("Resolving remote home directory…")
        self.status_label.setStyleSheet(f"color:{theme['sub']};")
        layout.addWidget(self.status_label)

        self._resolve_remote_home()
        self._render_queue()

    # ------------------------------------------------------------
    # External Terminal Launch
    # ------------------------------------------------------------

    def _connect_terminal(self, terminal) -> None:
        try:
            terminal.input_sent.disconnect(self._on_terminal_input)
        except Exception:
            pass

        terminal.input_sent.connect(self._on_terminal_input)


    def _on_terminal_input(self, data: str) -> None:
        if not self.broadcast_enabled:
            return

        sender = self.sender()

        for terminal in self.get_all_terminals():
            if terminal is not sender and hasattr(terminal, "inject_input"):
                terminal.inject_input(data)


    def _open_remote_terminal(self) -> None:
        try:

            tab = SshTerminalTab(
                self.services,
                host=self.host,
                port=self.port,
                user=self.user,
                creds=self.creds,
                initial_cmd="/bin/date",
                name=self.name,
                use_jump="",
                jump_host="",
                jump_port="",
                jump_user="",
                jump_creds="",
                use_agent="",
                profile_name="",
            )

            self._connect_terminal(tab)

            index = self.bottom_tabs.addTab(tab, f"🐚 Remote Terminal")
            self.bottom_tabs.setCurrentIndex(index)
        except Exception as e:
            QMessageBox.critical(
                self, "Launch Failed",
                f"Failed to launch remote terminal:\n{e}"
            )
            self.term_status.setText(f"❌ Launch failed: {e}")

    def _launch_external_terminal(self) -> None:
        """Launch an external terminal with SSH connection."""
        system = platform.system()
        
        # Build SSH command
        ssh_cmd = ["ssh"]
        if self.port != 22:
            ssh_cmd.extend(["-p", str(self.port)])
        ssh_cmd.append(f"{self.user}@{self.host}")

        try:
            if system == "Linux":
                # Try common terminal emulators in order of preference
                terminals = [
                    ["gnome-terminal", "--"],
                    ["konsole", "--noclose", "-e"],
                    ["xfce4-terminal", "--hold", "-e"],
                    ["xterm", "-e"],
                    ["mate-terminal", "--"],
                    ["terminator", "-e"],
                ]
                
                launched = False
                for term_cmd in terminals:
                    try:
                        full_cmd = term_cmd + ssh_cmd
                        subprocess.Popen(full_cmd)
                        launched = True
                        self.term_status.setText(f"✅ Launched {term_cmd[0]}")
                        self._log(f"External terminal launched: {term_cmd[0]}")
                        break
                    except FileNotFoundError:
                        continue
                
                if not launched:
                    QMessageBox.warning(
                        self, "Terminal Not Found",
                        "Could not find a supported terminal emulator.\n"
                        "Please install gnome-terminal, konsole, xterm, or another terminal."
                    )
                    
            elif system == "Darwin":  # macOS
                # Use Terminal.app or iTerm2
                try:
                    # Try iTerm2 first
                    subprocess.Popen([
                        "open", "-a", "iTerm",
                        f"ssh://{self.user}@{self.host}:{self.port}"
                    ])
                    self.term_status.setText("✅ Launched iTerm")
                    self._log("External terminal launched: iTerm")
                except Exception:
                    # Fall back to Terminal.app
                    subprocess.Popen([
                        "open", "-a", "Terminal",
                        f"ssh://{self.user}@{self.host}:{self.port}"
                    ])
                    self.term_status.setText("✅ Launched Terminal.app")
                    self._log("External terminal launched: Terminal.app")
                    
            elif system == "Windows":
                # Use Windows Terminal, PowerShell, or cmd
                try:
                    # Try Windows Terminal first
                    subprocess.Popen([
                        "wt", "ssh", f"{self.user}@{self.host}",
                        "-p", str(self.port)
                    ])
                    self.term_status.setText("✅ Launched Windows Terminal")
                    self._log("External terminal launched: Windows Terminal")
                except FileNotFoundError:
                    # Fall back to cmd
                    subprocess.Popen([
                        "cmd", "/c", "start", "cmd", "/k",
                        " ".join(ssh_cmd)
                    ])
                    self.term_status.setText("✅ Launched cmd")
                    self._log("External terminal launched: cmd")
            else:
                QMessageBox.warning(
                    self, "Unsupported Platform",
                    f"External terminal launch not supported on {system}.\n"
                    "Please use the quick command execution below."
                )
                
        except Exception as e:
            QMessageBox.critical(
                self, "Launch Failed",
                f"Failed to launch external terminal:\n{e}"
            )
            self.term_status.setText(f"❌ Launch failed: {e}")

    # ------------------------------------------------------------
    # Remote home directory resolution
    # ------------------------------------------------------------
    def _resolve_remote_home(self) -> None:
        self.status_label.setText("Resolving remote home directory…")
        self._home_probe = RemoteExecThread(
            self.host_info, "printf '%s' \"$HOME\"", timeout=10
        )
        self._home_probe.finished_cmd.connect(self._home_probe_done)
        self._home_probe.start()

    def _home_probe_done(self, out: str, rc: int) -> None:
        home = out.strip()
        if rc == 0 and home and home.startswith("/"):
            self._console_home = home
            self.remote_panel.current_path = home
            self.remote_panel.path_input.setText(home)
            self.status_label.setText(f"Remote home: {home}")
        else:
            self._console_home = "/"
            self.status_label.setText("Could not resolve home — using /")
        self._console_cwd = self.remote_panel.current_path
        self._update_console_prompt()
        self.remote_panel.refresh()

    # ------------------------------------------------------------
    # Tasks pane toggle
    # ------------------------------------------------------------
    def _toggle_tasks(self) -> None:
        self._tasks_visible = self.tasks_toggle.isChecked()
        self.tasks_container.setVisible(self._tasks_visible)
        self.tasks_toggle.setText("▼ Tasks" if self._tasks_visible else "▶ Tasks")
        if self._tasks_visible:
            sizes = self.main_vsplitter.sizes()
            total = sum(sizes)
            if total > 0:
                tasks_h = 150
                remaining = total - tasks_h
                panels_h = int(remaining * 0.75)
                bottom_h = remaining - panels_h
                self.main_vsplitter.setSizes([panels_h, tasks_h, bottom_h])
        else:
            sizes = self.main_vsplitter.sizes()
            total = sum(sizes)
            if total > 0:
                panels_h = int(total * 0.75)
                bottom_h = total - panels_h
                self.main_vsplitter.setSizes([panels_h, 0, bottom_h])

    # ------------------------------------------------------------
    # Connection health
    # ------------------------------------------------------------
    def test_connection(self) -> None:
        theme = self.services.theme.current
        self.conn_pill.setText("● testing…")
        self.conn_pill.setStyleSheet(f"color:{theme['warn']};font-size:11px;")
        self._probe = RemoteExecThread(self.host_info, "echo ok", timeout=10)
        self._probe.finished_cmd.connect(self._probe_done)
        self._probe.start()

    def _probe_done(self, out: str, rc: int) -> None:
        theme = self.services.theme.current
        if rc == 0 and "ok" in out:
            self.conn_pill.setText("● connected")
            self.conn_pill.setStyleSheet(f"color:{theme['ok']};font-size:11px;")
        else:
            self.conn_pill.setText("● unreachable")
            self.conn_pill.setStyleSheet(f"color:{theme['error']};font-size:11px;")

    # ------------------------------------------------------------
    # Dialogs / hooks
    # ------------------------------------------------------------
    def open_rsync(self) -> None:
        dlg = RsyncDialog(
            self, self.services, self.host_info,
            local_path=self.local_panel.current_path,
            remote_path=self.remote_panel.current_path,
        )
        dlg.exec()

    def remote_search(self) -> None:
        RemoteSearchDialog(self, self.host_info).exec()

    def open_remote_editor(self, remote_path: str) -> None:
        self.on_file_action("edit", remote_path, "remote")

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------
    def _log(self, msg: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.sftp_log.appendPlainText(f"[{ts}] {msg}")
        doc = self.sftp_log.document()
        if doc.blockCount() > 2000:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(500):
                cursor.movePosition(
                    QTextCursor.MoveOperation.Down,
                    QTextCursor.MoveMode.KeepAnchor,
                )
            cursor.removeSelectedText()
            cursor.deleteChar()

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    @staticmethod
    def _remote_join(path: str, name: str) -> str:
        if path == "/":
            return "/" + name
        return path.rstrip("/") + "/" + name

    @staticmethod
    def _fmt(n: float) -> str:
        n = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}TB"

    def _release_worker(self, worker: SftpWorker) -> None:
        try:
            self._op_workers.remove(worker)
        except ValueError:
            pass

    def _run_op_worker(self, worker: SftpWorker) -> None:
        worker.finished.connect(lambda w=worker: self._release_worker(w))
        self._op_workers.append(worker)
        worker.start()

    def _make_worker(self) -> SftpWorker:
        return SftpWorker(
            self.host, self.port, self.user, self.creds,
            use_agent=self.use_agent, strict_host_keys=self.strict_host_keys,
        )

    # ------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------
    def upload_selected(self) -> None:
        sels = self.local_panel.get_selected_files()
        if not sels:
            self.services.notifications.push(
                "info", "Upload",
                "Select one or more files/folders in the local panel.",
            )
            return
        for sel in sels:
            task = SftpTask(
                action=SftpAction.UPLOAD, local_path=sel["path"],
                remote_path=self._remote_join(self.remote_panel.current_path, sel["name"]),
                recursive=bool(sel["is_dir"]),
            )
            self._enqueue(task)
        self.status_label.setText(f"Queued {len(sels)} item(s) for upload")

    def download_selected(self) -> None:
        sels = self.remote_panel.get_selected_files()
        if not sels:
            self.services.notifications.push(
                "info", "Download",
                "Select one or more files/folders in the remote panel.",
            )
            return
        for sel in sels:
            task = SftpTask(
                action=SftpAction.DOWNLOAD, remote_path=sel["path"],
                local_path=os.path.join(self.local_panel.current_path, sel["name"]),
                recursive=bool(sel["is_dir"]),
            )
            self._enqueue(task)
        self.status_label.setText(f"Queued {len(sels)} item(s) for download")

    def _enqueue(self, task: SftpTask) -> None:
        self._queue.append(task)
        source = task.local_path or task.remote_path
        self._log(f"Queued: {task.action.value} {os.path.basename(source)}")
        self._render_queue()
        if self._active_transfer is None:
            self._next_transfer()

    def _render_queue(self) -> None:
        self.queue_tree.clear()
        if self._active_task is not None:
            src = self._active_task.local_path or self._active_task.remote_path
            self.queue_tree.addTopLevelItem(QTreeWidgetItem(
                [os.path.basename(src), "▶ running", self._active_task.action.value]
            ))
        for t in self._queue:
            src = t.local_path or t.remote_path
            self.queue_tree.addTopLevelItem(QTreeWidgetItem(
                [os.path.basename(src), "… queued", t.action.value]
            ))

    def cancel_current(self) -> None:
        if self._active_transfer is None:
            return
        try:
            self._active_transfer.requestInterruption()
        except Exception:
            pass
        self._log("⛔ Cancel requested for current transfer")
        self._active_transfer = None
        self._active_task = None
        self.progress.setVisible(False)
        self.speed_label.setText("")
        self._render_queue()
        QTimer.singleShot(100, self._next_transfer)

    def _next_transfer(self) -> None:
        if not self._queue:
            self._active_transfer = None
            self._active_task = None
            self.progress.setVisible(False)
            self.speed_label.setText("")
            self._render_queue()
            return

        task = self._queue.pop(0)
        self._active_task = task
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._xfer_start = datetime.datetime.now().timestamp()
        source = task.local_path or task.remote_path
        self.status_label.setText(
            f"{task.action.value.capitalize()}ing {os.path.basename(source)}…"
        )
        self._log(
            f"Starting: {task.action.value} {os.path.basename(source)} → "
            f"{task.remote_path or task.local_path}"
        )
        self._render_queue()

        worker = self._make_worker()
        worker.set_task(task)
        worker.transfer_progress.connect(self._progress)
        worker.transfer_complete.connect(lambda fn, uploaded, t=task: self._done(fn, t))
        worker.error_occurred.connect(self._err)
        worker.status_update.connect(self._log)
        self._active_transfer = worker
        worker.start()

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
            elapsed = max(datetime.datetime.now().timestamp() - self._xfer_start, 0.001)
            speed = done / elapsed
            self.speed_label.setText(
                f"{self._fmt(done)}/{self._fmt(total)} · {self._fmt(speed)}/s"
            )

    def _done(self, filename: str, task: SftpTask) -> None:
        self.status_label.setText(f"✅ {filename} transferred")
        self._log(f"✅ Complete: {filename} ({task.action.value})")
        self.services.notifications.push(
            "ok", "Transfer complete", f"{filename} ({task.action.value})"
        )
        if task.action == SftpAction.UPLOAD:
            self.remote_panel.refresh()
        elif task.action == SftpAction.DOWNLOAD:
            self.local_panel.refresh()

        self._active_transfer = None
        self._active_task = None
        QTimer.singleShot(150, self._next_transfer)

    def _err(self, err: str) -> None:
        self.status_label.setText(f"❌ {err}")
        self._log(f"❌ ERROR: {err}")
        self.services.notifications.push("error", "Transfer failed", err)

        self._active_transfer = None
        self._active_task = None
        QTimer.singleShot(150, self._next_transfer)

    # ------------------------------------------------------------
    # Remote Console (Quick Command Execution)
    # ------------------------------------------------------------
    def _fmt_cwd(self, path: str) -> str:
        if not path:
            return "/"
        if self._console_home and path == self._console_home:
            return "~"
        if self._console_home and path.startswith(self._console_home + "/"):
            return "~" + path[len(self._console_home):]
        return path

    def _update_console_prompt(self) -> None:
        cwd_display = self._fmt_cwd(self._console_cwd) if self._console_cwd else "~"
        self.console_prompt.setText(f"{self.user}@{self.host}:{cwd_display}$ ")

    def _run_console_cmd(self) -> None:
        cmd = self.console_input.text().strip()
        if not cmd:
            return
        self.console_input.clear()

        if not self._console_cwd:
            self._console_cwd = self.remote_panel.current_path or "/"

        cwd_display = self._fmt_cwd(self._console_cwd)
        self.console_out.appendPlainText(f"{self.user}@{self.host}:{cwd_display}$ {cmd}")
        self.console_input.setEnabled(False)

        marker = "___ADMINSUITE_CWD___"
        wrapped = (
            f"cd {shlex.quote(self._console_cwd)} 2>/dev/null; "
            f"{cmd}; "
            f"__ec=$?; "
            f"printf '\\n{marker}\\n'; "
            f"pwd; "
            f"exit $__ec"
        )

        self._console_worker = RemoteExecThread(self.host_info, wrapped, timeout=120)
        self._console_worker.finished_cmd.connect(self._console_done)
        self._console_worker.start()

    def _console_done(self, out: str, rc: int) -> None:
        self.console_input.setEnabled(True)
        self.console_input.setFocus()

        marker = "___ADMINSUITE_CWD___"
        actual_out = out

        if marker in out:
            before, after = out.rsplit(marker, 1)
            actual_out = before.rstrip("\n")
            lines = [ln for ln in after.splitlines() if ln.strip()]
            if lines:
                new_cwd = lines[-1].strip()
                if new_cwd and new_cwd.startswith("/"):
                    self._console_cwd = new_cwd

        if actual_out.strip():
            self.console_out.appendPlainText(actual_out)

        if rc != 0:
            self.console_out.appendPlainText(f"[Process exited with code {rc}]")

        self._update_console_prompt()
        self.console_out.appendPlainText("")
        self.console_out.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------
    def on_file_action(self, action: str, path: str, panel_id: str) -> None:
        try:
            if action == "edit":
                tab = RemoteEditorTab(self.services, self.host_info, path, parent=self.main_window)
                if self.main_window and hasattr(self.main_window, "tabs"):
                    idx = self.main_window.tabs.addTab(tab, f"✏️ {os.path.basename(path)}")
                    self.main_window.tabs.setCurrentIndex(idx)
                else:
                    tab.show()

            elif action == "chmod":
                try:
                    data = json.loads(path)
                    actual_mode = data.get("mode", 0o644)
                    chmod_path = data["path"]
                except (json.JSONDecodeError, TypeError):
                    actual_mode = 0o644
                    chmod_path = path
                dlg = ChmodDialog(self, actual_mode)
                if dlg.exec():
                    worker = self._make_worker()
                    task = SftpTask(
                        action=SftpAction.CHMOD, path=self.remote_panel.current_path,
                        remote_path=chmod_path, mode=dlg.result_mode,
                    )
                    worker.set_task(task)
                    worker.listing_ready.connect(lambda ents, p: self.remote_panel._fill_tree(ents, "remote"))
                    worker.error_occurred.connect(lambda e: (
                        self._log(f"❌ chmod error: {e}"),
                        self.services.notifications.push("error", "chmod", e),
                    ))
                    worker.status_update.connect(self._log)
                    self._run_op_worker(worker)

            elif action == "delete":
                worker = self._make_worker()
                task = SftpTask(
                    action=SftpAction.DELETE, path=self.remote_panel.current_path,
                    remote_path=path,
                )
                worker.set_task(task)
                worker.listing_ready.connect(lambda ents, p: self.remote_panel._fill_tree(ents, "remote"))
                worker.error_occurred.connect(lambda e: (
                    self._log(f"❌ delete error: {e}"),
                    self.services.notifications.push("error", "Delete", e),
                ))
                worker.status_update.connect(self._log)
                self._run_op_worker(worker)

            elif action == "rename":
                try:
                    data = json.loads(path)
                    old_path = data["old"]
                    new_path = data["new"]
                except (json.JSONDecodeError, TypeError, KeyError):
                    return
                worker = self._make_worker()
                task = SftpTask(
                    action=SftpAction.RENAME, path=self.remote_panel.current_path,
                    remote_path=old_path, local_path=new_path,
                )
                worker.set_task(task)
                worker.listing_ready.connect(lambda ents, p: self.remote_panel._fill_tree(ents, "remote"))
                worker.error_occurred.connect(lambda e: (
                    self._log(f"❌ rename error: {e}"),
                    self.services.notifications.push("error", "Rename", e),
                ))
                worker.status_update.connect(self._log)
                self._run_op_worker(worker)

            elif action == "mkdir":
                name, ok = QInputDialog.getText(self, "New Folder", "Name:")
                if not (ok and name):
                    return
                if panel_id == "local":
                    new_path = os.path.join(path, name)
                    try:
                        os.makedirs(new_path, exist_ok=True)
                        self.local_panel.refresh()
                        self._log(f"✅ Created local folder: {new_path}")
                    except Exception as e:
                        self._log(f"❌ local mkdir error: {e}")
                        QMessageBox.critical(self, "mkdir", str(e))
                else:
                    remote = self._remote_join(path, name)
                    worker = self._make_worker()
                    task = SftpTask(
                        action=SftpAction.MKDIR, path=path, remote_path=remote,
                    )
                    worker.set_task(task)
                    worker.listing_ready.connect(lambda ents, p: self.remote_panel._fill_tree(ents, "remote"))
                    worker.error_occurred.connect(lambda e: (
                        self._log(f"❌ mkdir error: {e}"),
                        self.services.notifications.push("error", "mkdir", e),
                    ))
                    worker.status_update.connect(self._log)
                    self._run_op_worker(worker)

            elif action == "upload":
                self._enqueue(SftpTask(
                    action=SftpAction.UPLOAD, local_path=path,
                    remote_path=self._remote_join(self.remote_panel.current_path, os.path.basename(path)),
                    recursive=False,
                ))
            elif action == "upload-dir":
                self._enqueue(SftpTask(
                    action=SftpAction.UPLOAD, local_path=path,
                    remote_path=self._remote_join(self.remote_panel.current_path, os.path.basename(path)),
                    recursive=True,
                ))
            elif action == "download":
                self._enqueue(SftpTask(
                    action=SftpAction.DOWNLOAD, remote_path=path,
                    local_path=os.path.join(self.local_panel.current_path, os.path.basename(path)),
                    recursive=False,
                ))
            elif action == "download-dir":
                self._enqueue(SftpTask(
                    action=SftpAction.DOWNLOAD, remote_path=path,
                    local_path=os.path.join(self.local_panel.current_path, os.path.basename(path)),
                    recursive=True,
                ))
            elif action == "download-to":
                spec = json.loads(path)
                self._enqueue(SftpTask(
                    action=SftpAction.DOWNLOAD, remote_path=spec["remote"],
                    local_path=spec["local"], recursive=False,
                ))
        except Exception as e:
            self._log(f"❌ Action '{action}' failed: {e}")
            self.services.notifications.push("error", "SFTP Error", f"Operation failed: {e}")

    # ------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------
    def closeEvent(self, event):
        try:
            if self._active_transfer is not None and self._active_transfer.isRunning():
                self._active_transfer.wait(1500)
        except Exception:
            pass
        for worker in list(self._op_workers):
            try:
                if worker.isRunning():
                    worker.wait(500)
            except Exception:
                pass
        for panel in (self.local_panel, self.remote_panel):
            worker = getattr(panel, "sftp_worker", None)
            if worker is not None:
                try:
                    if worker.isRunning():
                        worker.wait(1000)
                except Exception:
                    pass
        event.accept()
