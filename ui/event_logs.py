"""
Event and Audit Logs widget for Admin Suite.
Provides structured logging, filtering by level and component, real-time search,
and export capabilities for system diagnostics and audit compliance.
"""

from __future__ import annotations

import csv
import datetime
import html
import io
import json
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


LEVEL_COLORS = {
    "AUDIT": "#c084fc",    # Vibrant purple
    "ERROR": "#f87171",    # Bright red
    "WARN": "#fbbf24",     # Amber yellow
    "SUCCESS": "#4ade80",  # Emerald green
    "INFO": "#38bdf8",     # Cyan / Sky blue
}


class EventLogsWidget(QWidget):
    """
    Enhanced event and audit logging console for Admin Suite.
    """

    def __init__(self, services, main_window=None, parent=None):
        super().__init__(parent)
        self.services = services
        self.main_window = main_window

        self._events: list[dict[str, Any]] = []
        self._counts = {"TOTAL": 0, "AUDIT": 0, "ERROR": 0, "WARN": 0, "SUCCESS": 0, "INFO": 0}

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # --------------------------------------------------------
        # Header / Controls Toolbar
        # --------------------------------------------------------
        toolbar_frame = QFrame()
        toolbar_frame.setStyleSheet(
            f"background:{theme.get('panel2', '#222')};"
            f"border:1px solid {theme.get('border', '#333')};"
            "border-radius:6px;padding:4px;"
        )
        tb_layout = QVBoxLayout(toolbar_frame)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(4)

        # Row 1: Filters
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        self.level_combo = QComboBox()
        self.level_combo.addItems([
            "All Levels",
            "🛡️ AUDIT",
            "❌ ERROR",
            "⚠️ WARN",
            "✅ SUCCESS",
            "ℹ️ INFO",
        ])
        self.level_combo.currentIndexChanged.connect(self._apply_filter)

        self.source_combo = QComboBox()
        self.source_combo.addItems([
            "All Sources",
            "SSH",
            "DB",
            "SFTP",
            "VPN",
            "SYSADMIN",
            "WEB",
            "SECURITY",
            "ANSIBLE",
            "AI",
            "SYSTEM",
        ])
        self.source_combo.currentIndexChanged.connect(self._apply_filter)

        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("🔍 Filter messages...")
        self.search_in.setClearButtonEnabled(True)
        self.search_in.textChanged.connect(self._apply_filter)

        row1.addWidget(self.level_combo, 0)
        row1.addWidget(self.source_combo, 0)
        row1.addWidget(self.search_in, 1)
        tb_layout.addLayout(row1)

        # Row 2: Action Buttons
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.autoscroll_chk = QCheckBox("Auto-scroll")
        self.autoscroll_chk.setChecked(True)

        self.copy_btn = QPushButton("📋 Copy")
        self.copy_btn.setToolTip("Copy filtered logs to clipboard")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)

        self.export_btn = QPushButton("💾 Export ▾")
        self.export_btn.setToolTip("Export logs to file for auditing")
        self._init_export_menu()

        self.clear_btn = QPushButton("🗑 Clear")
        self.clear_btn.setToolTip("Clear log view")
        self.clear_btn.clicked.connect(self._clear_logs)

        row2.addWidget(self.autoscroll_chk)
        row2.addStretch()
        row2.addWidget(self.copy_btn)
        row2.addWidget(self.export_btn)
        row2.addWidget(self.clear_btn)
        tb_layout.addLayout(row2)

        layout.addWidget(toolbar_frame)

        # --------------------------------------------------------
        # Console Text Display
        # --------------------------------------------------------
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("JetBrains Mono, Consolas, Courier New", 9))
        self.console.setStyleSheet(
            f"background:{theme.get('win', '#181818')};"
            f"color:{theme.get('text', '#d4d4d4')};"
            f"border:1px solid {theme.get('border', '#333')};"
            "border-radius:4px;padding:6px;line-height:140%;"
        )
        layout.addWidget(self.console, 1)

        # --------------------------------------------------------
        # Status / Summary Footer
        # --------------------------------------------------------
        self.status_bar = QLabel("Events: 0 · Audits: 0 · Errors: 0 · Warnings: 0")
        self.status_bar.setStyleSheet(f"color:{theme.get('sub', '#888')};font-size:11px;padding:2px 4px;")
        layout.addWidget(self.status_bar)

        # Connect to pipeline events
        self.services.events.event_emitted.connect(self.append_event)

        # Load initial history from pipeline
        for record in self.services.events.get_history():
            self.append_event(record, update_view=False)
        self._rebuild_display()

    # ------------------------------------------------------------
    # Export Menu
    # ------------------------------------------------------------
    def _init_export_menu(self) -> None:
        menu = QMenu(self)
        txt_act = menu.addAction("📄 Export as Text (.txt)...")
        json_act = menu.addAction("📦 Export as JSON (.json)...")
        csv_act = menu.addAction("📊 Export as CSV (.csv)...")

        txt_act.triggered.connect(self._export_txt)
        json_act.triggered.connect(self._export_json)
        csv_act.triggered.connect(self._export_csv)

        self.export_btn.setMenu(menu)

    # ------------------------------------------------------------
    # Event Ingestion
    # ------------------------------------------------------------
    def append_event(self, event: dict[str, Any], update_view: bool = True) -> None:
        self._events.append(event)

        lvl = event.get("level", "INFO")
        self._counts["TOTAL"] += 1
        if lvl in self._counts:
            self._counts[lvl] += 1

        self._update_status_bar()

        if update_view:
            if self._matches_filter(event):
                self._append_html_line(self._format_event_html(event))

    def append_raw_line(self, line: str) -> None:
        """Compatibility method for raw log lines directly added to widget."""
        now = datetime.datetime.now()
        event = {
            "timestamp": now.strftime("%H:%M:%S"),
            "iso_time": now.isoformat(),
            "level": "INFO",
            "source": "SYSTEM",
            "message": str(line),
            "details": None,
        }
        self.append_event(event)

    def _format_event_html(self, event: dict[str, Any]) -> str:
        ts = event.get("timestamp", "")
        lvl = event.get("level", "INFO")
        src = event.get("source", "SYSTEM")
        raw_msg = str(event.get("message", ""))
        msg_escaped = html.escape(raw_msg)

        color = LEVEL_COLORS.get(lvl, "#38bdf8")
        bg_style = ""
        if lvl == "AUDIT":
            bg_style = "background-color:rgba(192,132,252,0.15);padding:1px 4px;border-radius:3px;"
        elif lvl == "ERROR":
            bg_style = "background-color:rgba(248,113,113,0.15);padding:1px 4px;border-radius:3px;"
        elif lvl == "WARN":
            bg_style = "background-color:rgba(251,191,36,0.12);padding:1px 4px;border-radius:3px;"

        return (
            f"<div style='margin-bottom:2px;'>"
            f"<span style='color:#71717a;'>[{ts}]</span> "
            f"<span style='color:{color};font-weight:bold;{bg_style}'>[{lvl}]</span> "
            f"<span style='color:#94a3b8;'>[{src}]</span> "
            f"<span>{msg_escaped}</span>"
            f"</div>"
        )

    def _append_html_line(self, html_text: str) -> None:
        self.console.append(html_text)
        if self.autoscroll_chk.isChecked():
            self.console.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------
    # Filtering & Search
    # ------------------------------------------------------------
    def _matches_filter(self, event: dict[str, Any]) -> bool:
        lvl_sel = self.level_combo.currentText().split()[-1].upper()
        if lvl_sel != "LEVELS" and event.get("level", "") != lvl_sel:
            return False

        src_sel = self.source_combo.currentText().upper()
        if src_sel != "ALL SOURCES" and event.get("source", "") != src_sel:
            return False

        q = self.search_in.text().strip().lower()
        if q:
            msg = str(event.get("message", "")).lower()
            src = str(event.get("source", "")).lower()
            lvl = str(event.get("level", "")).lower()
            if q not in msg and q not in src and q not in lvl:
                return False

        return True

    def _apply_filter(self) -> None:
        self._rebuild_display()

    def _rebuild_display(self) -> None:
        self.console.clear()
        matching = [e for e in self._events if self._matches_filter(e)]
        if not matching:
            return

        html_blocks = [self._format_event_html(e) for e in matching]
        self.console.setHtml("".join(html_blocks))

        if self.autoscroll_chk.isChecked():
            self.console.moveCursor(QTextCursor.MoveOperation.End)

    def _update_status_bar(self) -> None:
        self.status_bar.setText(
            f"Total: {self._counts['TOTAL']} · "
            f"Audits: {self._counts['AUDIT']} · "
            f"Errors: {self._counts['ERROR']} · "
            f"Warnings: {self._counts['WARN']}"
        )

    # ------------------------------------------------------------
    # Actions: Copy, Export, Clear
    # ------------------------------------------------------------
    def _clear_logs(self) -> None:
        self.console.clear()

    def _copy_to_clipboard(self) -> None:
        matching = [e for e in self._events if self._matches_filter(e)]
        lines = [
            f"[{e.get('timestamp')}] [{e.get('level')}] [{e.get('source')}] {e.get('message')}"
            for e in matching
        ]
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        if hasattr(self.services, "notifications"):
            self.services.notifications.push("ok", "Logs Copied", f"{len(lines)} lines copied to clipboard.")

    def _export_txt(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs as Text",
            f"event_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        matching = [e for e in self._events if self._matches_filter(e)]
        lines = [
            f"[{e.get('timestamp')}] [{e.get('level')}] [{e.get('source')}] {e.get('message')}"
            for e in matching
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.services.notifications.push("ok", "Export Complete", f"Saved {len(matching)} records to {path}")
        self.services.audit("SYSTEM", f"Exported {len(matching)} log events to {path}")

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs as JSON",
            f"event_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        matching = [e for e in self._events if self._matches_filter(e)]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(matching, f, indent=2)
        self.services.notifications.push("ok", "Export Complete", f"Saved {len(matching)} JSON records to {path}")
        self.services.audit("SYSTEM", f"Exported {len(matching)} JSON log events to {path}")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs as CSV",
            f"event_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        matching = [e for e in self._events if self._matches_filter(e)]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["timestamp", "iso_time", "level", "source", "message", "details"],
            )
            writer.writeheader()
            for row in matching:
                writer.writerow(row)
        self.services.notifications.push("ok", "Export Complete", f"Saved {len(matching)} CSV records to {path}")
        self.services.audit("SYSTEM", f"Exported {len(matching)} CSV log events to {path}")
