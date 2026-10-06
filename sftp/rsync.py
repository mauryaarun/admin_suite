"""
Rsync folder sync utility for the SFTP module.

Fixes over the original:
  * `def init(...)` corrected to `def __init__(...)` (dialog never built before).

Enhancements:
  * exclude patterns, bandwidth limit, compress / partial / per-file progress
  * live overall progress bar (driven by --info=progress2)
  * coloured output (errors red, success green, progress blue)
  * save / apply / delete named presets (persisted via services.config)
  * password is masked in the echoed command
"""
from __future__ import annotations

import re
import shlex
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class RsyncWorker(QThread):
    """Runs the rsync subprocess and streams stdout/stderr line by line."""

    output_ready = pyqtSignal(str)
    finished_signal = pyqtSignal(int)

    def __init__(self, cmd, cwd=None, env=None):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.cwd,
                env=self.env,
                bufsize=1,
            )
            for line in self.proc.stdout:
                self.output_ready.emit(line.rstrip("\n"))
            self.proc.wait()
            self.finished_signal.emit(self.proc.returncode)
        except Exception as e:
            self.output_ready.emit(f"[ERROR] {e}")
            self.finished_signal.emit(-1)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class RsyncDialog(QDialog):
    """Configure and run an rsync sync against the configured remote host."""

    _PRESET_KEY = "rsync_presets"

    def __init__(
        self,
        parent,
        services,
        host_info,
        local_path: str = "",
        remote_path: str = "",
    ):
        super().__init__(parent)
        self.services = services
        self.host_info = host_info
        self.worker = None

        self.setWindowTitle("Rsync Folder Sync")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ---- presets ----
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        preset_row.addWidget(self.preset_combo, 1)
        save_btn = QPushButton("💾 Save")
        save_btn.clicked.connect(self.save_preset)
        apply_btn = QPushButton("📂 Apply")
        apply_btn.clicked.connect(self.apply_preset)
        del_btn = QPushButton("🗑 Delete")
        del_btn.clicked.connect(self.delete_preset)
        for b in (save_btn, apply_btn, del_btn):
            preset_row.addWidget(b)
        layout.addLayout(preset_row)

        # ---- direction ----
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Direction:"))
        self.direction = QComboBox()
        self.direction.addItems(
            ["Upload (Local → Remote)", "Download (Remote → Local)"]
        )
        dir_layout.addWidget(self.direction, 1)
        layout.addLayout(dir_layout)

        # ---- paths ----
        local_layout = QHBoxLayout()
        local_layout.addWidget(QLabel("Local Path:"))
        self.local_path = QLineEdit(local_path)
        self.local_path.setPlaceholderText("/path/to/local/folder/")
        local_layout.addWidget(self.local_path, 1)
        browse_local = QPushButton("Browse…")
        browse_local.clicked.connect(self.browse_local)
        local_layout.addWidget(browse_local)
        layout.addLayout(local_layout)

        remote_layout = QHBoxLayout()
        remote_layout.addWidget(QLabel("Remote Path:"))
        self.remote_path = QLineEdit(remote_path)
        self.remote_path.setPlaceholderText("/path/to/remote/folder/")
        remote_layout.addWidget(self.remote_path, 1)
        layout.addLayout(remote_layout)

        # ---- option checkboxes ----
        opts = QGridLayout()
        self.dry_run = QCheckBox("Dry run (--dry-run)")
        self.delete_extra = QCheckBox("Delete extraneous (--delete)")
        self.compress = QCheckBox("Compress (-z)")
        self.compress.setChecked(True)
        self.partial = QCheckBox("Resume partial (--partial)")
        self.partial.setChecked(True)
        self.per_file = QCheckBox("Per-file progress (--progress)")
        self.per_file.setChecked(True)
        opts.addWidget(self.dry_run, 0, 0)
        opts.addWidget(self.delete_extra, 0, 1)
        opts.addWidget(self.compress, 1, 0)
        opts.addWidget(self.partial, 1, 1)
        opts.addWidget(self.per_file, 2, 0)
        layout.addLayout(opts)

        # ---- exclude + bandwidth ----
        adv = QHBoxLayout()
        adv.addWidget(QLabel("Exclude:"))
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("comma-separated, e.g. *.log,*.tmp,.git")
        adv.addWidget(self.exclude, 1)
        adv.addWidget(QLabel("BW limit (KB/s):"))
        self.bwlimit = QLineEdit()
        self.bwlimit.setFixedWidth(90)
        adv.addWidget(self.bwlimit)
        layout.addLayout(adv)

        # ---- progress + output ----
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

        # ---- buttons ----
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("▶ Start Sync")
        self.run_btn.setStyleSheet(
            "background:#3daee9;color:white;font-weight:bold;padding:6px;"
        )
        self.run_btn.clicked.connect(self.start_sync)
        self.stop_btn = QPushButton("⛔ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_sync)
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.stop_btn)
        layout.addLayout(btn_layout)

        self._refresh_presets()

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------
    def _load_presets(self) -> dict:
        try:
            return self.services.config.get(self._PRESET_KEY, {}) or {}
        except Exception:
            return {}

    def _persist_presets(self, presets: dict) -> None:
        try:
            if hasattr(self.services.config, "set"):
                self.services.config.set(self._PRESET_KEY, presets)
            else:
                self.services.config[self._PRESET_KEY] = presets
        except Exception:
            pass

    def _refresh_presets(self) -> None:
        self.preset_combo.clear()
        for name in sorted(self._load_presets()):
            self.preset_combo.addItem(name)

    def _collect_state(self) -> dict:
        return {
            "direction": self.direction.currentIndex(),
            "local": self.local_path.text(),
            "remote": self.remote_path.text(),
            "dry_run": self.dry_run.isChecked(),
            "delete": self.delete_extra.isChecked(),
            "compress": self.compress.isChecked(),
            "partial": self.partial.isChecked(),
            "per_file": self.per_file.isChecked(),
            "exclude": self.exclude.text(),
            "bwlimit": self.bwlimit.text(),
        }

    def _apply_state(self, s: dict) -> None:
        self.direction.setCurrentIndex(int(s.get("direction", 0)))
        self.local_path.setText(s.get("local", ""))
        self.remote_path.setText(s.get("remote", ""))
        self.dry_run.setChecked(bool(s.get("dry_run", False)))
        self.delete_extra.setChecked(bool(s.get("delete", False)))
        self.compress.setChecked(bool(s.get("compress", True)))
        self.partial.setChecked(bool(s.get("partial", True)))
        self.per_file.setChecked(bool(s.get("per_file", True)))
        self.exclude.setText(s.get("exclude", ""))
        self.bwlimit.setText(s.get("bwlimit", ""))

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:"
        )
        if not ok or not name.strip():
            return
        presets = self._load_presets()
        presets[name.strip()] = self._collect_state()
        self._persist_presets(presets)
        self._refresh_presets()
        self.preset_combo.setCurrentText(name.strip())

    def apply_preset(self) -> None:
        name = self.preset_combo.currentText()
        presets = self._load_presets()
        if name in presets:
            self._apply_state(presets[name])

    def delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        presets = self._load_presets()
        if name in presets:
            del presets[name]
            self._persist_presets(presets)
            self._refresh_presets()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Local Folder")
        if path:
            self.local_path.setText(path)

    @staticmethod
    def _display_cmd(cmd: list[str]) -> str:
        """Return the command as a string with passwords masked."""
        out = list(cmd)
        for i in range(len(out) - 2):
            if out[i] == "sshpass" and out[i + 1] == "-p":
                out[i + 2] = "****"
        if out and out[0] == "sshpass" and "-e" in out:
            return "SSHPASS=**** " + " ".join(out)
        return " ".join(out)

    def build_command(self):
        h = self.host_info
        host = h.get("host", "")
        port = h.get("port", 22)
        user = h.get("user", "")
        creds = h.get("creds")
        local = self.local_path.text().strip()
        remote = self.remote_path.text().strip()

        if not local or not remote:
            return None, "Local and Remote paths are required."

        # Trailing slashes => sync directory contents.
        if not local.endswith("/"):
            local += "/"
        if not remote.endswith("/"):
            remote += "/"

        ssh_cmd = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no"]
        key_path = getattr(creds, "key_path", None) if creds else None
        if key_path:
            key_path = os.path.expanduser(key_path)
            if os.path.exists(key_path):
                ssh_cmd.extend(["-i", key_path])

        ssh_cmd_str = " ".join(shlex.quote(c) for c in ssh_cmd)

        cmd = ["rsync", "-a", "-v", "--info=progress2", "-e", ssh_cmd_str]
        if self.compress.isChecked():
            cmd.append("-z")
        if self.per_file.isChecked():
            cmd.append("--progress")
        if self.dry_run.isChecked():
            cmd.append("--dry-run")
        if self.delete_extra.isChecked():
            cmd.append("--delete")
        if self.partial.isChecked():
            cmd.append("--partial")

        bw = self.bwlimit.text().strip()
        if bw.isdigit():
            cmd.extend(["--bwlimit", bw])

        for pat in (p.strip() for p in self.exclude.text().split(",")):
            if pat:
                cmd.extend(["--exclude", pat])

        # Password / passphrase handling
        self._sync_env = None
        password = getattr(creds, "password", None) if creds else None
        passphrase = getattr(creds, "passphrase", None) if creds else None
        secret_to_pass = password or (passphrase if key_path else None)

        if secret_to_pass and not (key_path and not passphrase):
            import shutil
            if shutil.which("sshpass"):
                self._sync_env = os.environ.copy()
                self._sync_env["SSHPASS"] = secret_to_pass
                cmd = ["sshpass", "-e"] + cmd
            else:
                return None, (
                    "Authentication requires 'sshpass' to be installed on your local Linux system.\n\n"
                    "Please install it via:\n  sudo apt install sshpass\n\n"
                    "or configure passwordless SSH key authentication."
                )

        remote_target = f"{user}@{host}:{remote}"
        if self.direction.currentIndex() == 0:  # Upload
            cmd.extend([local, remote_target])
        else:  # Download
            cmd.extend([remote_target, local])
        return cmd, None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------
    def start_sync(self) -> None:
        cmd, err = self.build_command()
        if err:
            QMessageBox.warning(self, "Validation", err)
            return
        self.output.clear()
        self.progress.setValue(0)
        self._append_line(f"$ {self._display_cmd(cmd)}")
        self._append_line("")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = RsyncWorker(cmd, env=getattr(self, "_sync_env", None))
        self.worker.output_ready.connect(self._append_line)
        self.worker.finished_signal.connect(self.sync_finished)
        self.worker.start()

    def stop_sync(self) -> None:
        if self.worker:
            self.worker.stop()
            self._append_line("[STOPPED BY USER]")

    def sync_finished(self, rc: int) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if rc == 0:
            self.progress.setValue(100)
            self._append_line("✅ Sync completed successfully.")
        else:
            self._append_line(f"❌ Sync finished with exit code {rc}.")

    # ------------------------------------------------------------------
    # Output rendering
    # ------------------------------------------------------------------
    _ERR_TOKENS = ("error", "failed", "denied", "permission", "no such")

    def _append_line(self, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        low = text.lower()
        if any(tok in low for tok in self._ERR_TOKENS):
            fmt.setForeground(QColor("#e06c75"))
        elif text.startswith("✅") or "completed" in low:
            fmt.setForeground(QColor("#98c379"))
        elif text.startswith("❌") or text.startswith("[STOPPED"):
            fmt.setForeground(QColor("#e06c75"))
        elif "%" in text:
            fmt.setForeground(QColor("#61afef"))
        cursor.insertText(text + "\n", fmt)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

        m = re.search(r"(\d{1,3})%", text)
        if m:
            try:
                self.progress.setValue(min(int(m.group(1)), 100))
            except ValueError:
                pass