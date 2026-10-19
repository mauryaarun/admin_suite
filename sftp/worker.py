"""
SFTP worker thread.

Improvements compared with the original implementation:

- uses structured SshCredentials
- uses AdminSSHClient host-key handling
- explicit WRITE content support
- no path/content overload
- status log messages
- safer cleanup
"""

from __future__ import annotations

import datetime
import json
import os
import stat
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from admin_suite.ssh.client import ssh_kwargs
from admin_suite.ssh.credentials import SshCredentials
from admin_suite.ssh.hostkeys import create_ssh_client

from admin_suite.sftp.models import SftpAction, SftpTask


class SftpWorker(QThread):
    """
    Executes one SFTP task.
    """

    listing_ready = pyqtSignal(list, str)
    file_content = pyqtSignal(str, str)
    transfer_complete = pyqtSignal(str, bool)
    transfer_progress = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)
    status_update = pyqtSignal(str)

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        use_agent: bool = False,
        strict_host_keys: bool = False,
    ):
        super().__init__()

        self.host = host
        self.user = user

        try:
            self.port = int(port) if port else 22
        except Exception:
            self.port = 22

        self.creds = creds
        self.use_agent = bool(use_agent)
        self.strict_host_keys = bool(strict_host_keys)

        self.client = None
        self.sftp = None

        self.task = SftpTask()

    def set_task(self, task: SftpTask) -> None:
        """
        Assign task before start().
        """
        self.task = task

    def run(self) -> None:
        try:
            self._connect()

            self.status_update.emit(
                f"Connected to {self.user}@{self.host}:{self.port}"
            )

            action = self.task.action

            if action == SftpAction.LIST:
                self._list_dir(self.task.path or "/")

            elif action == SftpAction.DOWNLOAD:
                self.status_update.emit(
                    f"Downloading: {self.task.remote_path} → {self.task.local_path}"
                )

                local_parent = os.path.dirname(
                    os.path.abspath(self.task.local_path)
                )

                if local_parent:
                    os.makedirs(local_parent, exist_ok=True)

                if self.task.recursive:
                    self._download_dir(
                        self.task.remote_path,
                        self.task.local_path,
                    )
                else:
                    self.sftp.get(
                        self.task.remote_path,
                        self.task.local_path,
                        callback=self._progress_callback,
                    )

                self.transfer_complete.emit(
                    os.path.basename(self.task.remote_path),
                    False,
                )

                self.status_update.emit(
                    f"Download complete: {os.path.basename(self.task.remote_path)}"
                )

            elif action == SftpAction.UPLOAD:
                self.status_update.emit(
                    f"Uploading: {self.task.local_path} → {self.task.remote_path}"
                )

                if self.task.recursive:
                    self._upload_dir(
                        self.task.local_path,
                        self.task.remote_path,
                    )
                else:
                    self.sftp.put(
                        self.task.local_path,
                        self.task.remote_path,
                        callback=self._progress_callback,
                    )

                self.transfer_complete.emit(
                    os.path.basename(self.task.local_path),
                    True,
                )

                self.status_update.emit(
                    f"Upload complete: {os.path.basename(self.task.local_path)}"
                )

            elif action == SftpAction.READ:
                self.status_update.emit(f"Reading: {self.task.remote_path}")

                with self.sftp.open(self.task.remote_path, "r") as f:
                    content = f.read().decode("utf-8", errors="replace")

                self.file_content.emit(self.task.remote_path, content)

            elif action == SftpAction.WRITE:
                if self.task.content is None:
                    raise ValueError(
                        "No content provided for remote write operation."
                    )

                self.status_update.emit(f"Writing: {self.task.remote_path}")

                with self.sftp.open(self.task.remote_path, "w") as f:
                    f.write(self.task.content.encode("utf-8"))

                self.transfer_complete.emit(
                    os.path.basename(self.task.remote_path),
                    True,
                )

                self.status_update.emit(
                    f"Write complete: {self.task.remote_path}"
                )

            elif action == SftpAction.MKDIR:
                self.status_update.emit(
                    f"Creating directory: {self.task.remote_path}"
                )

                self.sftp.mkdir(self.task.remote_path)

                self.status_update.emit(
                    f"Directory created: {self.task.remote_path}"
                )

                if self.task.path:
                    self._list_dir(self.task.path)

            elif action == SftpAction.CHMOD:
                self.status_update.emit(
                    f"Changing permissions: {self.task.remote_path} → {oct(self.task.mode)}"
                )

                self.sftp.chmod(self.task.remote_path, self.task.mode)

                if self.task.path:
                    self._list_dir(self.task.path)

            elif action == SftpAction.DELETE:
                self.status_update.emit(f"Deleting: {self.task.remote_path}")

                try:
                    st = self.sftp.stat(self.task.remote_path)

                    if stat.S_ISDIR(st.st_mode):
                        self._rmdir(self.task.remote_path)
                    else:
                        self.sftp.remove(self.task.remote_path)

                except FileNotFoundError:
                    self.status_update.emit(
                        f"Warning: {self.task.remote_path} not found "
                        "(may already be deleted)"
                    )

                self.status_update.emit(f"Deleted: {self.task.remote_path}")

                if self.task.path:
                    self._list_dir(self.task.path)

            elif action == SftpAction.RENAME:
                self.status_update.emit(
                    f"Renaming: {self.task.remote_path} → {self.task.local_path}"
                )

                self.sftp.rename(
                    self.task.remote_path,
                    self.task.local_path,
                )

                if self.task.path:
                    self._list_dir(self.task.path)

            elif action == SftpAction.STAT:
                st = self.sftp.stat(self.task.remote_path)

                self.file_content.emit(
                    self.task.remote_path,
                    json.dumps(
                        {
                            "size": st.st_size,
                            "mode": oct(st.st_mode),
                            "mtime": datetime.datetime.fromtimestamp(
                                st.st_mtime
                            ).isoformat(),
                        }
                    ),
                )

        except PermissionError as e:
            err_msg = f"Permission denied: {e}"
            self.error_occurred.emit(err_msg)
            self.status_update.emit(f"ERROR: {err_msg}")

        except FileNotFoundError as e:
            err_msg = f"File not found: {e}"
            self.error_occurred.emit(err_msg)
            self.status_update.emit(f"ERROR: {err_msg}")

        except TimeoutError as e:
            err_msg = f"Operation timed out: {e}"
            self.error_occurred.emit(err_msg)
            self.status_update.emit(f"ERROR: {err_msg}")

        except IOError as e:
            err_msg = f"IO error: {e}"
            self.error_occurred.emit(err_msg)
            self.status_update.emit(f"ERROR: {err_msg}")

        except Exception as e:
            err_msg = f"SFTP operation failed: {type(e).__name__}: {str(e)}"
            self.error_occurred.emit(err_msg)
            self.status_update.emit(f"ERROR: {err_msg}")

        finally:
            self.cleanup()

    def _connect(self) -> None:
        self.client = create_ssh_client(strict=self.strict_host_keys)

        kw = ssh_kwargs(
            self.host,
            self.port,
            self.user,
            self.creds,
            use_agent=self.use_agent,
        )

        self.client.connect(**kw)

        self.sftp = self.client.open_sftp()

        try:
            self.sftp.get_channel().settimeout(30)
        except Exception:
            pass

    def _list_dir(self, path: str) -> None:
        entries = []
        target_path = path

        if not target_path or target_path in (".", "~"):
            try:
                target_path = self.sftp.normalize(".")
            except Exception:
                user_str = str(self.user or "").strip()
                target_path = "/root" if user_str == "root" else (f"/home/{user_str}" if user_str else "/")

        try:
            for e in self.sftp.listdir_attr(target_path):
                entries.append(
                    {
                        "name": e.filename,
                        "size": e.st_size or 0,
                        "is_dir": stat.S_ISDIR(e.st_mode),
                        "mode": e.st_mode,
                        "mtime": e.st_mtime or 0,
                    }
                )

        except FileNotFoundError:
            # If the guessed directory doesn't exist on remote, try normalizing '.' or root '/'
            fallback = None
            if target_path != "/":
                try:
                    norm = self.sftp.normalize(".")
                    if norm and norm != target_path:
                        fallback = norm
                except Exception:
                    pass
            if not fallback and target_path != "/":
                fallback = "/"

            if fallback:
                try:
                    target_path = fallback
                    for e in self.sftp.listdir_attr(target_path):
                        entries.append(
                            {
                                "name": e.filename,
                                "size": e.st_size or 0,
                                "is_dir": stat.S_ISDIR(e.st_mode),
                                "mode": e.st_mode,
                                "mtime": e.st_mtime or 0,
                            }
                        )
                except Exception as ex2:
                    self.status_update.emit(
                        f"WARNING: List error in {target_path}: {ex2}"
                    )
        except Exception as ex:
            self.status_update.emit(
                f"WARNING: List error in {target_path}: {ex}"
            )

        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        self.listing_ready.emit(entries, target_path)

    def _progress_callback(self, done: int, total: int) -> None:
        self.transfer_progress.emit(done, total)

    def _rmdir(self, path: str) -> None:
        try:
            for e in self.sftp.listdir_attr(path):
                full = path.rstrip("/") + "/" + e.filename

                if stat.S_ISDIR(e.st_mode):
                    self._rmdir(full)
                else:
                    self.sftp.remove(full)

            self.sftp.rmdir(path)

        except Exception as e:
            self.status_update.emit(
                f"WARNING: rmdir partial failure at {path}: {e}"
            )

    def _download_dir(self, remote_dir: str, local_dir: str) -> None:
        os.makedirs(local_dir, exist_ok=True)

        for e in self.sftp.listdir_attr(remote_dir):
            rpath = remote_dir.rstrip("/") + "/" + e.filename
            lpath = os.path.join(local_dir, e.filename)

            if stat.S_ISDIR(e.st_mode):
                self._download_dir(rpath, lpath)
            else:
                try:
                    self.status_update.emit(f"Downloading file: {rpath}")
                    self.sftp.get(rpath, lpath)

                except Exception as e:
                    self.status_update.emit(
                        f"WARNING: Failed to download {rpath}: {e}"
                    )

    def _upload_dir(self, local_dir: str, remote_dir: str) -> None:
        try:
            self.sftp.mkdir(remote_dir)
        except Exception:
            pass

        for item in os.listdir(local_dir):
            lpath = os.path.join(local_dir, item)
            rpath = remote_dir.rstrip("/") + "/" + item

            if os.path.isdir(lpath):
                self._upload_dir(lpath, rpath)
            else:
                try:
                    self.status_update.emit(f"Uploading file: {lpath}")
                    self.sftp.put(lpath, rpath)

                except Exception as e:
                    self.status_update.emit(
                        f"WARNING: Failed to upload {lpath}: {e}"
                    )

    def cleanup(self) -> None:
        try:
            if self.sftp:
                self.sftp.close()
        except Exception:
            pass

        try:
            if self.client:
                self.client.close()
        except Exception:
            pass

        self.sftp = None
        self.client = None
