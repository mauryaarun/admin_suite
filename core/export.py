"""
Export Subsystem for Admin Suite.
Provides unified exporting of reports, logs, tables, and text excerpts to CSV, JSON,
Markdown, and Plain Text files, as well as clipboard management.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableView,
    QTextEdit,
    QWidget,
)


class ReportExporter:
    """Utility class for saving reports and data tables to files."""

    @staticmethod
    def export_text_file(
        parent: QWidget,
        content: str,
        default_filename: str = "report.txt",
        title: str = "Export Report",
        filter_str: str = "Text Files (*.txt);;Markdown (*.md);;Log Files (*.log);;All Files (*)",
    ) -> Optional[str]:
        """Prompts user for save location and writes text content to file."""
        if not content.strip():
            QMessageBox.information(parent, "Export", "Nothing to export (content is empty).")
            return None

        filepath, _ = QFileDialog.getSaveFileName(
            parent,
            title,
            default_filename,
            filter_str,
        )
        if not filepath:
            return None

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(
                parent,
                "Export Successful",
                f"Successfully exported report to:\n{os.path.basename(filepath)}",
            )
            return filepath
        except Exception as ex:
            QMessageBox.critical(parent, "Export Error", f"Failed to save file:\n{ex}")
            return None

    @staticmethod
    def export_table_csv(
        parent: QWidget,
        table: QTableWidget | QTableView,
        default_filename: str = "table_export.csv",
        title: str = "Export to CSV",
    ) -> Optional[str]:
        """Exports QTableWidget or QTableView contents to a standard CSV file."""
        model = table.model() if hasattr(table, "model") else None
        row_count = model.rowCount() if model else table.rowCount()
        col_count = model.columnCount() if model else table.columnCount()

        if row_count == 0:
            QMessageBox.information(parent, "Export", "The table contains no data to export.")
            return None

        filepath, _ = QFileDialog.getSaveFileName(
            parent,
            title,
            default_filename,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not filepath:
            return None

        try:
            # Extract headers
            headers: list[str] = []
            for col in range(col_count):
                if model:
                    h_val = model.headerData(col, Qt.Orientation.Horizontal)
                    headers.append(str(h_val) if h_val is not None else f"Column_{col+1}")
                elif isinstance(table, QTableWidget):
                    h_item = table.horizontalHeaderItem(col)
                    headers.append(h_item.text() if h_item else f"Column_{col+1}")
                else:
                    headers.append(f"Column_{col+1}")

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

                for row in range(row_count):
                    row_data: list[str] = []
                    for col in range(col_count):
                        if model:
                            idx = model.index(row, col)
                            val = model.data(idx, Qt.ItemDataRole.DisplayRole)
                            row_data.append(str(val).strip() if val is not None else "")
                        elif isinstance(table, QTableWidget):
                            item = table.item(row, col)
                            row_data.append(item.text().strip() if item else "")
                        else:
                            row_data.append("")
                    writer.writerow(row_data)

            QMessageBox.information(
                parent,
                "Export Successful",
                f"Successfully exported {row_count} row(s) to:\n{os.path.basename(filepath)}",
            )
            return filepath
        except Exception as ex:
            QMessageBox.critical(parent, "Export Error", f"Failed to save CSV file:\n{ex}")
            return None

    @staticmethod
    def export_table_json(
        parent: QWidget,
        table: QTableWidget | QTableView,
        default_filename: str = "table_export.json",
        title: str = "Export to JSON",
    ) -> Optional[str]:
        """Exports QTableWidget or QTableView contents to a JSON file."""
        model = table.model() if hasattr(table, "model") else None
        row_count = model.rowCount() if model else table.rowCount()
        col_count = model.columnCount() if model else table.columnCount()

        if row_count == 0:
            QMessageBox.information(parent, "Export", "The table contains no data to export.")
            return None

        filepath, _ = QFileDialog.getSaveFileName(
            parent,
            title,
            default_filename,
            "JSON Files (*.json);;All Files (*)",
        )
        if not filepath:
            return None

        try:
            headers: list[str] = []
            for col in range(col_count):
                if model:
                    h_val = model.headerData(col, Qt.Orientation.Horizontal)
                    headers.append(str(h_val) if h_val is not None else f"column_{col+1}")
                elif isinstance(table, QTableWidget):
                    h_item = table.horizontalHeaderItem(col)
                    headers.append(h_item.text() if h_item else f"column_{col+1}")
                else:
                    headers.append(f"column_{col+1}")

            data: list[dict[str, str]] = []
            for row in range(row_count):
                row_obj: dict[str, str] = {}
                for col in range(col_count):
                    h_key = headers[col]
                    if model:
                        idx = model.index(row, col)
                        val = model.data(idx, Qt.ItemDataRole.DisplayRole)
                        row_obj[h_key] = str(val).strip() if val is not None else ""
                    elif isinstance(table, QTableWidget):
                        item = table.item(row, col)
                        row_obj[h_key] = item.text().strip() if item else ""
                    else:
                        row_obj[h_key] = ""
                data.append(row_obj)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            QMessageBox.information(
                parent,
                "Export Successful",
                f"Successfully exported {row_count} row(s) to JSON:\n{os.path.basename(filepath)}",
            )
            return filepath
        except Exception as ex:
            QMessageBox.critical(parent, "Export Error", f"Failed to save JSON file:\n{ex}")
            return None

    @staticmethod
    def export_excerpt_dialog(parent: QWidget, selected_text: str, default_name: str = "excerpt.txt") -> None:
        """Saves selected text or excerpt to a user-chosen file."""
        if not selected_text.strip():
            QMessageBox.information(parent, "Export Excerpt", "No text is currently selected.")
            return

        ReportExporter.export_text_file(
            parent=parent,
            content=selected_text,
            default_filename=default_name,
            title="Export Selected Excerpt",
            filter_str="Text Files (*.txt);;Log Excerpt (*.log);;Markdown (*.md);;All Files (*)",
        )

    @staticmethod
    def attach_export_context_menu(widget: QPlainTextEdit | QTextEdit) -> None:
        """Attaches right-click menu actions for copying and saving selected excerpts."""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _show_menu(pos):
            menu = widget.createStandardContextMenu()
            menu.addSeparator()

            selected = widget.textCursor().selectedText().replace("\u2029", "\n")

            action_copy_sel = QAction("📋 Copy Selected Excerpt", widget)
            action_copy_sel.setEnabled(bool(selected))
            action_copy_sel.triggered.connect(
                lambda: QApplication.clipboard().setText(selected)
            )
            menu.addAction(action_copy_sel)

            action_save_sel = QAction("💾 Export Selection to File...", widget)
            action_save_sel.setEnabled(bool(selected))
            action_save_sel.triggered.connect(
                lambda: ReportExporter.export_excerpt_dialog(widget, selected, "excerpt.txt")
            )
            menu.addAction(action_save_sel)

            action_save_all = QAction("📤 Export Entire Output to File...", widget)
            full_text = widget.toPlainText()
            action_save_all.setEnabled(bool(full_text))
            action_save_all.triggered.connect(
                lambda: ReportExporter.export_text_file(
                    widget, full_text, "full_output.txt", "Export All Text"
                )
            )
            menu.addAction(action_save_all)

            menu.exec(widget.mapToGlobal(pos))

        widget.customContextMenuRequested.connect(_show_menu)
