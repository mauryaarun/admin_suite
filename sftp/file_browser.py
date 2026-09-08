"""
File browser panel for local and remote SFTP views.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from typing import Any, Optional

from PyQt6.QtCore import QByteArray, QMimeData, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from admin_suite.sftp.models import SftpAction, SftpTask
from admin_suite.sftp.worker import SftpWorker

# Custom role for numeric-aware sorting
_ROLE_SORT = Qt.ItemDataRole.UserRole + 1

# File-type icon map
_EXT_ICONS: dict[str, str] = {
    "py": "🐍", "sh": "🐚", "bash": "🐚", "zsh": "🐚",
    "conf": "⚙️", "cfg": "⚙️", "ini": "⚙️", "env": "⚙️",
    "log": "📜", "txt": "📄", "md": "📝", "rst": "📝",
    "json": "🧾", "yaml": "🧾", "yml": "🧾", "xml": "🧾", "toml": "🧾",
    "csv": "📊", "tsv": "📊",
    "gz": "🗜️", "tar": "🗜️", "zip": "🗜️", "bz2": "🗜️",
    "xz": "🗜️", "7z": "🗜️", "rar": "🗜️",
    "jpg": "🖼️", "jpeg": "🖼️", "png": "🖼️", "gif": "🖼️",
    "svg": "🖼️", "bmp": "🖼️", "ico": "🖼️",
    "mp3": "🎵", "wav": "🎵", "flac": "🎵", "ogg": "🎵",
    "mp4": "🎬", "mkv": "🎬", "avi": "🎬", "mov": "🎬",
    "c": "🅒", "h": "🅗", "cpp": "➕", "hpp": "➕", "go": "🐹",
    "js": "🟨", "ts": "🟦", "html": "🌐", "css": "🎨",
    "sql": "🗄️", "db": "🗄️", "sqlite": "🗄️",
    "key": "🔑", "pem": "🔑", "crt": "🔑", "cer": "🔑",
    "pdf": "📕", "doc": "📘", "docx": "📘", "xls": "📗", "xlsx": "📗",
}

def _icon_for(name: str, is_dir: bool) -> str:
    if is_dir:
        return "📁"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_ICONS.get(ext, "📄")

class _SortableItem(QTreeWidgetItem):
    """Tree item that sorts by a stored sort-key instead of display text."""
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        widget = self.treeWidget()
        col = widget.sortColumn() if widget is not None else 0
        a = self.data(col, _ROLE_SORT)
        b = other.data(col, _ROLE_SORT)
        if a is None or b is None:
            return super().__lt__(other)
        try:
            return a < b
        except TypeError:
            return super().__lt__(other)

class FileBrowserPanel(QWidget):
    """Local or remote file browser panel."""
    file_action = pyqtSignal(str, str, str)

    def __init__(
        self, services, panel_id: str, mode: str = "local",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.services = services
        self.panel_id = panel_id
        self.mode = mode
        
        if mode == "local":
            default_local = self.services.config.get("sftp_default_local", "")
            if default_local and os.path.isdir(default_local):
                self.current_path = default_local
            else:
                self.current_path = os.path.expanduser("~")
        else:
            self.current_path = "/"
            
        self.sftp_worker: Optional[SftpWorker] = None
        self.host_info: dict[str, Any] = {}
        
        # View state
        self._entries: list[dict[str, Any]] = []
        self._tag: str = mode
        self._filter_text: str = ""
        self._show_hidden: bool = False
        
        self.setAcceptDrops(True)
        self._drag_start_pos = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        
        # ---- path bar ----
        pb = QHBoxLayout()
        up = QPushButton("⬆")
        up.setFixedWidth(30)
        up.setToolTip("Go up one level")
        up.clicked.connect(self.go_up)
        
        home = QPushButton("🏠")
        home.setFixedWidth(30)
        home.setToolTip("Go to home / root")
        home.clicked.connect(self.go_home)
        
        rf = QPushButton("🔄")
        rf.setFixedWidth(30)
        rf.setToolTip("Refresh (F5)")
        rf.clicked.connect(self.refresh)
        
        self.path_input = QLineEdit(self.current_path)
        self.path_input.returnPressed.connect(
            lambda: self.navigate_to_path(self.path_input.text())
        )
        
        pb.addWidget(up)
        pb.addWidget(home)
        pb.addWidget(rf)
        pb.addWidget(self.path_input, 1)
        layout.addLayout(pb)
        
        # ---- filter / hidden row ----
        fb = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("🔎 Filter current folder…")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._on_filter_changed)
        
        self.show_hidden = QCheckBox("Hidden")
        self.show_hidden.setToolTip("Show dot-files")
        self.show_hidden.toggled.connect(self._on_hidden_toggled)
        
        fb.addWidget(self.filter_input, 1)
        fb.addWidget(self.show_hidden)
        layout.addLayout(fb)
        
        # ---- tree ----
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.itemDoubleClicked.connect(self.on_dbl_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_ctx_menu)
        self.tree.setDragEnabled(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.tree, 1)
        
        theme = self.services.theme.current
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        layout.addWidget(self.status_label)
        
        # ---- keyboard shortcuts ----
        del_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        del_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        del_sc.activated.connect(self._delete_selected)
        
        rf_sc = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        rf_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        rf_sc.activated.connect(self.refresh)
        
        # F2 for rename
        ren_sc = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        ren_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        ren_sc.activated.connect(self._rename_current)
        
        if mode == "local":
            self.refresh()

    def configure_remote(
        self, host: str, port: int, user: str, creds, *,
        use_agent: bool = False, strict_host_keys: bool = False,
    ) -> None:
        self.host_info = {
            "host": host, "port": port, "user": user, "creds": creds,
            "use_agent": use_agent, "strict_host_keys": strict_host_keys,
        }

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.navigate_to_path(self.current_path)

    def go_up(self) -> None:
        if self.mode == "local":
            parent = os.path.dirname(self.current_path.rstrip("/"))
            if not parent:
                parent = os.path.expanduser("~")
            self.navigate_to_path(parent)
        else:
            self.navigate_to_path(self._remote_parent(self.current_path))

    def go_home(self) -> None:
        if self.mode == "local":
            self.navigate_to_path(os.path.expanduser("~"))
        else:
            self.navigate_to_path("/")

    def navigate_to_path(self, path: str) -> None:
        self.current_path = path
        self.path_input.setText(path)
        
        self.filter_input.blockSignals(True)
        self.filter_input.clear()
        self.filter_input.blockSignals(False)
        self._filter_text = ""
        
        if self.mode == "local":
            self._list_local(path)
        else:
            self._list_remote(path)

    def _remote_parent(self, path: str) -> str:
        if path == "/":
            return "/"
        p = path.rstrip("/")
        if "/" not in p:
            return "/"
        parent = p.rsplit("/", 1)[0]
        return parent or "/"

    def _remote_full(self, name: str) -> str:
        if self.current_path == "/":
            return "/" + name
        return self.current_path.rstrip("/") + "/" + name

    # ------------------------------------------------------------------
    # Filter / hidden
    # ------------------------------------------------------------------
    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self._render()

    def _on_hidden_toggled(self, checked: bool) -> None:
        self._show_hidden = checked
        self._render()

    # ------------------------------------------------------------------
    # Listing / rendering
    # ------------------------------------------------------------------
    def _fill_tree(self, entries: list[dict[str, Any]], tag: str) -> None:
        self._entries = entries
        self._tag = tag
        self._render()

    def _render(self) -> None:
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        visible = 0
        for e in self._entries:
            name = e["name"]
            if not self._show_hidden and name.startswith("."):
                continue
            if self._filter_text and self._filter_text not in name.lower():
                continue
                
            item = _SortableItem()
            item.setText(0, f"{_icon_for(name, e['is_dir'])} {name}")
            item.setText(1, "<DIR>" if e["is_dir"] else self._fmt_size(e["size"]))
            
            mtime = e.get("mtime")
            if mtime:
                item.setText(2, datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"))
            else:
                item.setText(2, "")
                
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            item.setData(0, _ROLE_SORT, name.lower())
            item.setData(1, _ROLE_SORT, -1 if e["is_dir"] else float(e["size"]))
            item.setData(2, _ROLE_SORT, float(mtime) if mtime else 0.0)
            self.tree.addTopLevelItem(item)
            visible += 1
            
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.status_label.setText(f"{visible}/{len(self._entries)} items ({self._tag})")

    def _list_local(self, path: str) -> None:
        try:
            entries = []
            for name in os.listdir(path):
                full = os.path.join(path, name)
                try:
                    st = os.stat(full)
                    entries.append({
                        "name": name, "size": st.st_size,
                        "is_dir": os.path.isdir(full), "mtime": st.st_mtime,
                        "mode": st.st_mode,
                    })
                except Exception:
                    pass
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            self._fill_tree(entries, "local")
        except Exception as ex:
            self.status_label.setText(f"Error: {ex}")

    def _list_remote(self, path: str) -> None:
        if not self.host_info.get("host"):
            self.status_label.setText("Remote host not configured")
            return
            
        self.status_label.setText("Loading…")
        self.tree.clear()
        
        if self.sftp_worker and self.sftp_worker.isRunning():
            self.sftp_worker.wait(1500)
            
        h = self.host_info
        self.sftp_worker = SftpWorker(
            h["host"], h.get("port", 22), h.get("user", ""), h.get("creds"),
            use_agent=h.get("use_agent", False),
            strict_host_keys=h.get("strict_host_keys", False),
        )
        self.sftp_worker.listing_ready.connect(self._on_remote_listing)
        self.sftp_worker.error_occurred.connect(
            lambda e: self.status_label.setText(f"Error: {e}")
        )
        task = SftpTask(action=SftpAction.LIST, path=path)
        self.sftp_worker.set_task(task)
        self.sftp_worker.start()

    def _on_remote_listing(self, entries: list[dict[str, Any]], path: str) -> None:
        self.current_path = path
        self.path_input.setText(path)
        self._fill_tree(entries, "remote")

    @staticmethod
    def _fmt_size(size: float) -> str:
        size = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def on_dbl_click(self, item: QTreeWidgetItem, column: int) -> None:
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if not e:
            return
        if e["is_dir"]:
            if self.mode == "local":
                self.navigate_to_path(os.path.join(self.current_path, e["name"]))
            else:
                self.navigate_to_path(self._remote_full(e["name"]))
        else:
            if self.mode == "local":
                self.file_action.emit("upload", os.path.join(self.current_path, e["name"]), self.panel_id)
            else:
                self.file_action.emit("download", self._remote_full(e["name"]), self.panel_id)

    def show_ctx_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        
        # Respect multi-selection
        selected_items = self.tree.selectedItems()
        if item and item not in selected_items:
            if not selected_items:
                selected_items = [item]
                
        if selected_items:
            first_e = selected_items[0].data(0, Qt.ItemDataRole.UserRole)
            first_full_local = os.path.join(self.current_path, first_e["name"]) if first_e else ""
            first_full_remote = self._remote_full(first_e["name"]) if first_e else ""
            
            if self.mode == "local":
                def _upload_all():
                    for sel_item in selected_items:
                        e = sel_item.data(0, Qt.ItemDataRole.UserRole)
                        if not e: continue
                        full = os.path.join(self.current_path, e["name"])
                        action = "upload-dir" if e["is_dir"] else "upload"
                        self.file_action.emit(action, full, self.panel_id)
                menu.addAction(f"📤 Upload Selected ({len(selected_items)})").triggered.connect(_upload_all)
                
                if len(selected_items) == 1:
                    menu.addAction("✏️ Rename (F2)").triggered.connect(lambda: self._rename_item(first_e))
                    menu.addAction("📂 Open Containing Folder").triggered.connect(
                        lambda: self._open_containing_folder(first_full_local)
                    )
            else:
                def _download_all():
                    for sel_item in selected_items:
                        e = sel_item.data(0, Qt.ItemDataRole.UserRole)
                        if not e: continue
                        full = self._remote_full(e["name"])
                        action = "download-dir" if e["is_dir"] else "download"
                        self.file_action.emit(action, full, self.panel_id)
                menu.addAction(f"📥 Download Selected ({len(selected_items)})").triggered.connect(_download_all)
                
                if len(selected_items) == 1 and first_e:
                    if not first_e["is_dir"]:
                        menu.addAction("✏️ Open in Editor").triggered.connect(
                            lambda: self.file_action.emit("edit", first_full_remote, self.panel_id)
                        )
                    menu.addAction("🔐 Permissions (chmod)").triggered.connect(
                        lambda: self.file_action.emit(
                            "chmod",
                            json.dumps({"path": first_full_remote, "mode": first_e.get("mode", 0o644)}),
                            self.panel_id,
                        )
                    )
                    menu.addAction("✏️ Rename (F2)").triggered.connect(lambda: self._rename_item(first_e))
                    
                menu.addSeparator()
                menu.addAction("🗑 Delete Selected…").triggered.connect(self._delete_selected)
                
            menu.addSeparator()
            menu.addAction("📋 Copy Path").triggered.connect(
                lambda: QApplication.clipboard().setText(
                    first_full_local if self.mode == "local" else first_full_remote
                )
            )
            
        menu.addAction("🔄 Refresh (F5)").triggered.connect(self.refresh)
        menu.addAction("📁 New Folder").triggered.connect(
            lambda: self.file_action.emit("mkdir", self.current_path, self.panel_id)
        )
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Helper Actions
    # ------------------------------------------------------------------
    def _rename_current(self) -> None:
        item = self.tree.currentItem()
        if item:
            e = item.data(0, Qt.ItemDataRole.UserRole)
            if e:
                self._rename_item(e)

    def _rename_item(self, e: dict) -> None:
        old_name = e["name"]
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if ok and new_name and new_name != old_name:
            if self.mode == "local":
                old_path = os.path.join(self.current_path, old_name)
                new_path = os.path.join(self.current_path, new_name)
                try:
                    os.rename(old_path, new_path)
                    self.refresh()
                except Exception as ex:
                    QMessageBox.critical(self, "Rename Error", str(ex))
            else:
                old_path = self._remote_full(old_name)
                new_path = self._remote_full(new_name)
                self.file_action.emit(
                    "rename",
                    json.dumps({"old": old_path, "new": new_path}),
                    self.panel_id,
                )

    def _open_containing_folder(self, path: str) -> None:
        folder = os.path.dirname(path) if not os.path.isdir(path) else path
        try:
            if sys.platform == "win32":
                subprocess.Popen(f'explorer /select,"{path}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as ex:
            QMessageBox.critical(self, "Open Folder", str(ex))

    # ------------------------------------------------------------------
    # Destructive actions
    # ------------------------------------------------------------------
    def _delete_selected(self) -> None:
        sels = self.get_selected_files()
        if not sels:
            return
            
        if self.mode == "local":
            names = ", ".join(s["name"] for s in sels[:5])
            more = f" (+{len(sels) - 5} more)" if len(sels) > 5 else ""
            if QMessageBox.question(
                self, "Delete", f"Delete {len(sels)} local item(s)?\n{names}{more}\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            for s in sels:
                try:
                    if s["is_dir"]:
                        import shutil
                        shutil.rmtree(s["path"])
                    else:
                        os.remove(s["path"])
                except Exception as ex:
                    QMessageBox.critical(self, "Delete Error", str(ex))
            self.refresh()
            return

        # Remote delete
        names = ", ".join(s["name"] for s in sels[:5])
        more = f" (+{len(sels) - 5} more)" if len(sels) > 5 else ""
        if QMessageBox.question(
            self, "Delete", f"Delete {len(sels)} item(s)?\n{names}{more}\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        for s in sels:
            self.file_action.emit("delete", s["path"], self.panel_id)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def get_selected_file(self) -> Optional[dict[str, Any]]:
        item = self.tree.currentItem()
        if not item: return None
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if not e: return None
        if self.mode == "local":
            return {"path": os.path.join(self.current_path, e["name"]), "name": e["name"], "is_dir": e["is_dir"]}
        return {"path": self._remote_full(e["name"]), "name": e["name"], "is_dir": e["is_dir"]}

    def get_selected_files(self) -> list[dict[str, Any]]:
        items = self.tree.selectedItems()
        if not items:
            item = self.tree.currentItem()
            items = [item] if item else []
        selected = []
        for item in items:
            e = item.data(0, Qt.ItemDataRole.UserRole)
            if not e: continue
            if self.mode == "local":
                selected.append({"path": os.path.join(self.current_path, e["name"]), "name": e["name"], "is_dir": e["is_dir"]})
            else:
                selected.append({"path": self._remote_full(e["name"]), "name": e["name"], "is_dir": e["is_dir"]})
        return selected

    # ------------------------------------------------------------------
    # Drag and drop
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start_pos:
            distance = (e.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance > 10:
                selected = self.get_selected_files()
                if selected:
                    md = QMimeData()
                    if self.mode == "local":
                        md.setUrls([QUrl.fromLocalFile(x["path"]) for x in selected])
                    else:
                        payload = json.dumps({
                            "panel_id": self.panel_id,
                            "files": [{"path": x["path"], "name": x["name"]} for x in selected],
                        })
                        md.setData("application/x-admin-suite-remote-files", QByteArray(payload.encode()))
                    drag = QDrag(self)
                    drag.setMimeData(md)
                    drag.exec(Qt.DropAction.CopyAction)
                self._drag_start_pos = None
        super().mouseMoveEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat("application/x-admin-suite-remote-files"):
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if self.mode == "remote" and md.hasUrls():
            urls = [u.toLocalFile() for u in md.urls()]
            urls = [p for p in urls if p and os.path.exists(p)]
            if not urls: return
            if len(urls) > 5 and not self._confirm_bulk(f"Upload {len(urls)} items to remote?"):
                return
            for lp in urls:
                if os.path.isdir(lp):
                    self.file_action.emit("upload-dir", lp, self.panel_id)
                else:
                    self.file_action.emit("upload", lp, self.panel_id)
            e.acceptProposedAction()
        elif self.mode == "local" and md.hasFormat("application/x-admin-suite-remote-files"):
            raw = json.loads(bytes(md.data("application/x-admin-suite-remote-files")).decode())
            files = raw.get("files", []) if isinstance(raw, dict) else raw
            if not files: return
            if len(files) > 5 and not self._confirm_bulk(f"Download {len(files)} items to local?"):
                return
            for it in files:
                self.file_action.emit("download-to", json.dumps({
                    "remote": it["path"],
                    "local": os.path.join(self.current_path, it["name"]),
                }), self.panel_id)
            e.acceptProposedAction()

    def _confirm_bulk(self, message: str) -> bool:
        return QMessageBox.question(
            self, "Confirm transfer", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes