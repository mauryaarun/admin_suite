"""
External File Editor Integration for SFTP with File System Watcher (FileZilla Parity).
Allows opening remote files in system default external editor (VS Code, Sublime, etc.)
and automatically re-uploading modified files when saved.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from admin_suite.sftp.models import SftpAction, SftpTask
from admin_suite.sftp.worker import SftpWorker


class ExternalEditorManager(QObject):
    """
    Manages external file editing for SFTP sessions.
    Watches downloaded files for local changes and auto-uploads them back to the server.
    """

    file_uploaded = pyqtSignal(str, str)  # remote_path, local_path

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_local_file_changed)

        # local_path -> {"remote_path": ..., "host_info": ..., "last_mtime": ...}
        self._tracked: Dict[str, Dict[str, Any]] = {}
        self._workers: list[SftpWorker] = []
        self._debounce_timers: Dict[str, QTimer] = {}

    def edit_remote_file(self, remote_path: str, host_info: dict[str, Any]) -> None:
        """Downloads remote file to cache and opens in system editor."""
        host = host_info.get("host", "localhost")
        clean_rel = remote_path.lstrip("/").replace("/", "_")
        cache_dir = Path.home() / ".cache" / "admin_suite" / "sftp_edit" / host
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(cache_dir / clean_rel)

        worker = SftpWorker(
            host_info.get("host", ""),
            host_info.get("port", 22),
            host_info.get("user", ""),
            host_info.get("creds"),
            use_agent=host_info.get("use_agent", False),
            strict_host_keys=host_info.get("strict_host_keys", False),
        )
        task = SftpTask(action=SftpAction.READ, remote_path=remote_path)
        worker.set_task(task)

        def _on_loaded(path: str, content: str):
            try:
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(content)

                # Set tracking metadata
                mtime = os.path.getmtime(local_path)
                self._tracked[local_path] = {
                    "remote_path": remote_path,
                    "host_info": host_info,
                    "last_mtime": mtime,
                }

                # Watch file
                self.watcher.addPath(local_path)

                # Launch external editor via QDesktopServices / xdg-open
                opened = QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))
                if not opened:
                    try:
                        subprocess.Popen(["xdg-open", local_path])
                    except Exception as ex:
                        self.services.notifications.push(
                            "warn", "External Editor", f"Opened file at {local_path}: {ex}"
                        )

                self.services.notifications.push(
                    "ok",
                    "External Editor",
                    f"Opened {os.path.basename(remote_path)} in system editor. Saving will sync back to server.",
                )
            except Exception as ex:
                self.services.notifications.push("error", "External Editor Error", str(ex))

        worker.file_content.connect(_on_loaded)
        worker.error_occurred.connect(
            lambda err: self.services.notifications.push("error", "SFTP Download Failed", err)
        )
        self._workers.append(worker)
        worker.start()

    def _on_local_file_changed(self, local_path: str):
        """Called by QFileSystemWatcher when an external editor saves changes."""
        if local_path not in self._tracked:
            return

        # Debounce multiple rapid file events (some editors write 2-3 times per save)
        if local_path in self._debounce_timers:
            self._debounce_timers[local_path].stop()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(400)
        timer.timeout.connect(lambda: self._sync_file_to_remote(local_path))
        self._debounce_timers[local_path] = timer
        timer.start()

    def _sync_file_to_remote(self, local_path: str):
        if local_path not in self._tracked:
            return

        # Re-add path to watcher (Linux inotify can drop watch on file replace)
        if os.path.exists(local_path):
            self.watcher.addPath(local_path)
        else:
            return

        try:
            curr_mtime = os.path.getmtime(local_path)
            meta = self._tracked[local_path]
            if curr_mtime <= meta.get("last_mtime", 0):
                return

            meta["last_mtime"] = curr_mtime

            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read()

            remote_path = meta["remote_path"]
            host_info = meta["host_info"]

            saver = SftpWorker(
                host_info.get("host", ""),
                host_info.get("port", 22),
                host_info.get("user", ""),
                host_info.get("creds"),
                use_agent=host_info.get("use_agent", False),
                strict_host_keys=host_info.get("strict_host_keys", False),
            )
            task = SftpTask(action=SftpAction.WRITE, remote_path=remote_path, content=content)
            saver.set_task(task)

            saver.transfer_complete.connect(
                lambda n, up: (
                    self.services.notifications.push(
                        "ok", "File Synced", f"Auto-uploaded {os.path.basename(remote_path)} to server."
                    ),
                    self.file_uploaded.emit(remote_path, local_path),
                )
            )
            saver.error_occurred.connect(
                lambda err: self.services.notifications.push("error", "Auto-Sync Failed", err)
            )
            self._workers.append(saver)
            saver.start()

        except Exception as ex:
            self.services.notifications.push("error", "Sync Error", str(ex))
