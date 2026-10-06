"""
Visual Table Designer and Schema Editor for Admin Suite (HeidiSQL Parity).
Allows visual inspection, modification, and addition of columns with DDL generation.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from admin_suite.db.quoting import qident
from admin_suite.db.worker import DbWorker


class TableDesignerDialog(QDialog):
    """Visual Table Designer dialog matching HeidiSQL table editor capabilities."""

    schema_altered = pyqtSignal()

    DATA_TYPES = [
        "VARCHAR",
        "INT",
        "BIGINT",
        "TINYINT",
        "TEXT",
        "LONGTEXT",
        "DATETIME",
        "TIMESTAMP",
        "DATE",
        "TIME",
        "DECIMAL",
        "FLOAT",
        "DOUBLE",
        "BOOLEAN",
        "JSON",
        "BLOB",
    ]

    def __init__(
        self,
        services,
        session_manager,
        cfg: dict[str, Any],
        db_name: str,
        table_name: str,
        initial_columns: list[dict[str, Any]],
        parent=None,
    ):
        super().__init__(parent)
        self.services = services
        self.session_manager = session_manager
        self.cfg = cfg
        self.db_name = db_name
        self.table_name = table_name
        self.initial_columns = initial_columns
        self.backend = cfg.get("backend", "mysql")

        self.setWindowTitle(f"🛠️ Table Designer — {db_name}.{table_name} (HeidiSQL Mode)")
        self.resize(850, 560)

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header
        head_layout = QHBoxLayout()
        quoted_db = qident(self.db_name, self.backend)
        quoted_tbl = qident(self.table_name, self.backend)
        lbl_title = QLabel(f"Table Structure: {quoted_db}.{quoted_tbl}")
        lbl_title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        head_layout.addWidget(lbl_title)
        head_layout.addStretch()

        self.btn_add_col = QPushButton("➕ Add Column")
        self.btn_add_col.clicked.connect(self._add_new_column_row)
        head_layout.addWidget(self.btn_add_col)

        self.btn_del_col = QPushButton("🗑️ Drop Selected Column")
        self.btn_del_col.clicked.connect(self._drop_selected_column)
        head_layout.addWidget(self.btn_del_col)

        self.btn_reset = QPushButton("🔄 Reset")
        self.btn_reset.clicked.connect(self._populate_initial)
        head_layout.addWidget(self.btn_reset)

        layout.addLayout(head_layout)

        # Columns Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Column Name",
            "Data Type",
            "Length / Set",
            "Allow NULL",
            "Default Value",
            "Primary Key",
            "Auto Increment",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        layout.addWidget(self.table, stretch=2)

        # SQL Preview Pane
        layout.addWidget(QLabel("Generated DDL Statement Preview:"))
        self.sql_preview = QTextEdit()
        self.sql_preview.setFont(QFont("Monospace", 10))
        self.sql_preview.setReadOnly(True)
        self.sql_preview.setFixedHeight(90)
        self.sql_preview.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;"
        )
        layout.addWidget(self.sql_preview)

        # Bottom Buttons
        bottom_bar = QHBoxLayout()
        btn_preview = QPushButton("🔍 Generate / Preview SQL")
        btn_preview.clicked.connect(self._update_sql_preview)
        bottom_bar.addWidget(btn_preview)

        bottom_bar.addStretch()

        self.btn_execute = QPushButton("⚡ Execute Changes")
        self.btn_execute.setStyleSheet("background: #2e7d32; color: #fff; font-weight: bold; padding: 6px 16px;")
        self.btn_execute.clicked.connect(self._on_execute)
        bottom_bar.addWidget(self.btn_execute)

        btn_close = QPushButton("Cancel")
        btn_close.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_close)

        layout.addLayout(bottom_bar)

        self._populate_initial()

    def _populate_initial(self):
        self.table.setRowCount(0)
        for col in self.initial_columns:
            # col format e.g. {"field": "id", "type": "int(11)", "null": "NO", "key": "PRI", "default": None, "extra": "auto_increment"}
            name = col.get("field", "")
            raw_type = col.get("type", "varchar(255)")
            nullable = col.get("null", "YES").upper() in ("YES", "TRUE", "1")
            default_val = col.get("default") or ""
            is_pk = "PRI" in col.get("key", "").upper()
            is_ai = "auto_increment" in col.get("extra", "").lower()

            # Parse type & length
            dt_name = "VARCHAR"
            dt_len = ""
            if "(" in raw_type:
                dt_name = raw_type.split("(")[0].upper()
                dt_len = raw_type.split("(")[1].rstrip(")")
            else:
                dt_name = raw_type.upper()

            self._insert_column_row(name, dt_name, dt_len, nullable, default_val, is_pk, is_ai, is_existing=True)

        self._update_sql_preview()

    def _insert_column_row(
        self,
        name: str,
        dt_name: str,
        dt_len: str,
        nullable: bool,
        default_val: str,
        is_pk: bool,
        is_ai: bool,
        is_existing: bool = False,
    ):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0. Name
        name_edit = QLineEdit(name)
        name_edit.textChanged.connect(self._update_sql_preview)
        self.table.setCellWidget(row, 0, name_edit)

        # 1. Type
        type_combo = QComboBox()
        for t in self.DATA_TYPES:
            type_combo.addItem(t)
        found_idx = type_combo.findText(dt_name, Qt.MatchFlag.MatchFixedString)
        if found_idx >= 0:
            type_combo.setCurrentIndex(found_idx)
        else:
            type_combo.addItem(dt_name)
            type_combo.setCurrentText(dt_name)
        type_combo.currentTextChanged.connect(self._update_sql_preview)
        self.table.setCellWidget(row, 1, type_combo)

        # 2. Length
        len_edit = QLineEdit(str(dt_len))
        len_edit.setPlaceholderText("e.g. 255")
        len_edit.textChanged.connect(self._update_sql_preview)
        self.table.setCellWidget(row, 2, len_edit)

        # 3. Nullable
        null_chk = QCheckBox()
        null_chk.setChecked(nullable)
        null_chk.toggled.connect(self._update_sql_preview)
        null_widget = QWidget()
        nl_lay = QHBoxLayout(null_widget)
        nl_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nl_lay.setContentsMargins(0, 0, 0, 0)
        nl_lay.addWidget(null_chk)
        self.table.setCellWidget(row, 3, null_widget)

        # 4. Default
        def_edit = QLineEdit(str(default_val))
        def_edit.setPlaceholderText("NULL")
        def_edit.textChanged.connect(self._update_sql_preview)
        self.table.setCellWidget(row, 4, def_edit)

        # 5. Primary Key
        pk_chk = QCheckBox()
        pk_chk.setChecked(is_pk)
        pk_chk.toggled.connect(self._update_sql_preview)
        pk_widget = QWidget()
        pk_lay = QHBoxLayout(pk_widget)
        pk_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pk_lay.setContentsMargins(0, 0, 0, 0)
        pk_lay.addWidget(pk_chk)
        self.table.setCellWidget(row, 5, pk_widget)

        # 6. Auto Increment
        ai_chk = QCheckBox()
        ai_chk.setChecked(is_ai)
        ai_chk.toggled.connect(self._update_sql_preview)
        ai_widget = QWidget()
        ai_lay = QHBoxLayout(ai_widget)
        ai_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ai_lay.setContentsMargins(0, 0, 0, 0)
        ai_lay.addWidget(ai_chk)
        self.table.setCellWidget(row, 6, ai_widget)

        # Store meta
        item = QTableWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"is_existing": is_existing, "orig_name": name})
        self.table.setItem(row, 0, item)

    def _add_new_column_row(self):
        self._insert_column_row(
            name="new_column",
            dt_name="VARCHAR",
            dt_len="255",
            nullable=True,
            default_val="",
            is_pk=False,
            is_ai=False,
            is_existing=False,
        )
        self.table.scrollToBottom()
        self._update_sql_preview()

    def _drop_selected_column(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "No Column Selected", "Select a column to drop.")
            return

        name_widget = self.table.cellWidget(curr_row, 0)
        col_name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        confirm = QMessageBox.question(
            self,
            "Confirm Drop Column",
            f"Are you sure you want to drop column '{col_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.table.removeRow(curr_row)
            self._update_sql_preview()

    def generate_ddl_queries(self) -> list[str]:
        """Calculates SQL diff and generates ALTER TABLE DDL statements."""
        queries: list[str] = []
        quoted_db = qident(self.db_name, self.backend)
        quoted_tbl = qident(self.table_name, self.backend)
        target = f"{quoted_db}.{quoted_tbl}"

        # Current UI state columns
        current_cols: dict[str, dict[str, Any]] = {}
        for r in range(self.table.rowCount()):
            name_w = self.table.cellWidget(r, 0)
            name = name_w.text().strip() if isinstance(name_w, QLineEdit) else ""
            if not name:
                continue

            type_w = self.table.cellWidget(r, 1)
            dt = type_w.currentText().strip() if isinstance(type_w, QComboBox) else "VARCHAR"

            len_w = self.table.cellWidget(r, 2)
            length = len_w.text().strip() if isinstance(len_w, QLineEdit) else ""

            type_spec = f"{dt}({length})" if length and dt not in ("TEXT", "LONGTEXT", "DATETIME", "TIMESTAMP", "JSON", "DATE") else dt

            null_widget = self.table.cellWidget(r, 3)
            null_chk = null_widget.findChild(QCheckBox) if null_widget else None
            is_null = null_chk.isChecked() if null_chk else True
            null_clause = "NULL" if is_null else "NOT NULL"

            def_w = self.table.cellWidget(r, 4)
            def_val = def_w.text().strip() if isinstance(def_w, QLineEdit) else ""
            def_clause = ""
            if def_val:
                if def_val.upper() in ("NULL", "CURRENT_TIMESTAMP"):
                    def_clause = f"DEFAULT {def_val.upper()}"
                else:
                    def_clause = f"DEFAULT '{def_val}'"

            pk_widget = self.table.cellWidget(r, 5)
            pk_chk = pk_widget.findChild(QCheckBox) if pk_widget else None
            is_pk = pk_chk.isChecked() if pk_chk else False

            ai_widget = self.table.cellWidget(r, 6)
            ai_chk = ai_widget.findChild(QCheckBox) if ai_widget else None
            is_ai = ai_chk.isChecked() if ai_chk else False

            meta_item = self.table.item(r, 0)
            meta = meta_item.data(Qt.ItemDataRole.UserRole) if meta_item else {}

            current_cols[name] = {
                "name": name,
                "type_spec": type_spec,
                "null_clause": null_clause,
                "def_clause": def_clause,
                "is_pk": is_pk,
                "is_ai": is_ai,
                "is_existing": meta.get("is_existing", False),
                "orig_name": meta.get("orig_name", name),
            }

        initial_col_names = {c.get("field"): c for c in self.initial_columns if c.get("field")}

        # 1. Dropped columns
        for orig_name in initial_col_names:
            if not any(info.get("orig_name") == orig_name for info in current_cols.values()):
                queries.append(f"ALTER TABLE {target} DROP COLUMN {qident(orig_name, self.backend)};")

        # 2. Added columns or Modified columns
        for name, info in current_cols.items():
            col_quoted = qident(name, self.backend)
            ai_str = " AUTO_INCREMENT" if info["is_ai"] and self.backend == "mysql" else ""
            col_def = f"{col_quoted} {info['type_spec']} {info['null_clause']} {info['def_clause']}{ai_str}".strip()

            if not info["is_existing"]:
                # New column
                queries.append(f"ALTER TABLE {target} ADD COLUMN {col_def};")
            else:
                # Modified column if name or definition changed
                orig_name = info["orig_name"]
                if orig_name != name:
                    # Rename column
                    queries.append(f"ALTER TABLE {target} RENAME COLUMN {qident(orig_name, self.backend)} TO {col_quoted};")

        return queries

    def _update_sql_preview(self):
        queries = self.generate_ddl_queries()
        if queries:
            self.sql_preview.setPlainText("\n".join(queries))
            self.btn_execute.setEnabled(True)
        else:
            self.sql_preview.setPlainText("-- No schema changes detected.")
            self.btn_execute.setEnabled(False)

    def _on_execute(self):
        queries = self.generate_ddl_queries()
        if not queries:
            return

        confirm = QMessageBox.question(
            self,
            "Confirm DDL Execution",
            f"Are you sure you want to execute the following schema changes?\n\n" + "\n".join(queries[:5]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Execute DDL statements sequentially
        session = self.session_manager.get_session(self.cfg)
        try:
            for q in queries:
                session.execute(q)
            self.services.notifications.push("ok", "Table Altered", f"Applied {len(queries)} schema changes.")
            self.schema_altered.emit()
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "DDL Execution Error", f"Failed to alter table:\n\n{ex}")
