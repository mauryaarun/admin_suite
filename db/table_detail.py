"""
Table detail tab with schema/data/CRUD.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.db.dialogs import RecordDialog
from admin_suite.db.export import export_result_csv
from admin_suite.db.models import SqlResultModel
from admin_suite.db.quoting import placeholder, qident
from admin_suite.db.worker import DbWorker


class TableDetailTab(QWidget):
    """
    Table detail view with schema and CRUD.
    """

    def __init__(
        self,
        services,
        session_manager,
        cfg: dict[str, Any],
        db_name: str,
        table_name: str,
        parent=None,
    ):
        super().__init__(parent)

        self.services = services
        self.session_manager = session_manager
        self.cfg = cfg

        self.db_name = db_name
        self.table_name = table_name

        self.backend = cfg.get("backend", "mysql")

        quoted_db = qident(db_name, self.backend)
        quoted_table = qident(table_name, self.backend)

        self.fqn = f"{quoted_db}.{quoted_table}"

        self._columns_cache: list[dict] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel(f"📋 {self.fqn}")
        header.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;font-size:14px;padding:4px;"
        )

        layout.addWidget(header)

        self.inner_tabs = QTabWidget()

        # Schema tab.
        schema_widget = QWidget()
        schema_layout = QVBoxLayout(schema_widget)

        schema_bar = QHBoxLayout()
        design_btn = QPushButton("🛠️ Design / Alter Table (HeidiSQL Mode)")
        design_btn.clicked.connect(self.open_table_designer)
        schema_bar.addWidget(design_btn)
        schema_bar.addStretch()
        schema_layout.addLayout(schema_bar)

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabels(
            ["Field", "Type", "Null", "Key", "Default", "Extra"]
        )

        self.schema_tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )

        schema_layout.addWidget(self.schema_tree)

        self.inner_tabs.addTab(schema_widget, "📐 Schema")

        # Data tab.
        data_widget = QWidget()
        data_layout = QVBoxLayout(data_widget)

        bar = QHBoxLayout()

        bar.addWidget(QLabel("LIMIT:"))

        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 10000)
        self.limit_input.setValue(
            int(self.services.config.get("db_page_size", 200))
        )

        run_btn = QPushButton("▶ Run SELECT")
        run_btn.clicked.connect(self.load_data)

        export_btn = QPushButton("📤 CSV")
        export_btn.clicked.connect(self.export_csv)

        insert_btn = QPushButton("➕ Insert")
        insert_btn.clicked.connect(self.insert_record)

        update_btn = QPushButton("✏️ Update Selected")
        update_btn.clicked.connect(self.update_record)

        delete_btn = QPushButton("🗑 Delete Selected")
        delete_btn.clicked.connect(self.delete_record)

        bar.addWidget(self.limit_input)
        bar.addStretch()
        bar.addWidget(insert_btn)
        bar.addWidget(update_btn)
        bar.addWidget(delete_btn)
        bar.addWidget(export_btn)
        bar.addWidget(run_btn)

        data_layout.addLayout(bar)

        self.data_table = QTableView()
        self.data_table.setAlternatingRowColors(True)

        self.data_table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )

        self.data_model = SqlResultModel()
        self.data_table.setModel(self.data_model)
        self.data_table.doubleClicked.connect(self.update_record)

        data_layout.addWidget(self.data_table)

        self.row_label = QLabel("")
        self.row_label.setStyleSheet(f"color:{theme['sub']};")

        data_layout.addWidget(self.row_label)

        self.inner_tabs.addTab(data_widget, "📊 Data")

        layout.addWidget(self.inner_tabs, 1)

        self.load_schema()
        self.load_data()

    # ------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------

    def _start_worker(self, worker: DbWorker) -> None:
        worker.error_occurred.connect(
            lambda e: QMessageBox.warning(self, "Database", e)
        )

        worker.start()

        if not hasattr(self, "_workers"):
            self._workers = []

        self._workers = [
            w for w in self._workers if w.isRunning()
        ] + [worker]

    # ------------------------------------------------------------
    # Schema/data loading
    # ------------------------------------------------------------

    def load_schema(self) -> None:
        worker = DbWorker(
            self.session_manager,
            self.cfg,
            mode="fetch_columns",
            db_name=self.db_name,
            table_name=self.table_name,
        )

        worker.columns_loaded.connect(self._populate_schema)

        self._start_worker(worker)

    def _populate_schema(self, columns: list[dict]) -> None:
        self._columns_cache = columns

        self.schema_tree.clear()

        for col in columns:
            self.schema_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        str(col.get("Field", "")),
                        str(col.get("Type", "")),
                        str(col.get("Null", "")),
                        str(col.get("Key", "")),
                        str(col.get("Default", "") or ""),
                        str(col.get("Extra", "")),
                    ]
                )
            )

    def load_data(self) -> None:
        self.row_label.setText("Loading...")

        worker = DbWorker(
            self.session_manager,
            self.cfg,
            mode="run_query",
            query=(
                f"SELECT * FROM {self.fqn} "
                f"LIMIT {self.limit_input.value()};"
            ),
            db_name=self.db_name,
        )

        worker.data_loaded.connect(self._populate_data)

        self._start_worker(worker)

    def _populate_data(
        self,
        headers: list[str],
        rows: list[list[Any]],
    ) -> None:
        self.data_model.set_result(headers, rows)

        self.data_table.resizeColumnsToContents()

        self.row_label.setText(f"{len(rows)} row(s)")

    # ------------------------------------------------------------
    # HeidiSQL Table Designer
    # ------------------------------------------------------------

    def open_table_designer(self) -> None:
        from admin_suite.db.table_designer import TableDesignerDialog

        dlg = TableDesignerDialog(
            services=self.services,
            session_manager=self.session_manager,
            cfg=self.cfg,
            db_name=self.db_name,
            table_name=self.table_name,
            initial_columns=self._columns_cache,
            parent=self,
        )
        dlg.schema_altered.connect(self.load_schema)
        dlg.exec()

    # ------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------

    def _pk_cols(self) -> list[str]:
        return [
            col.get("Field")
            for col in self._columns_cache
            if str(col.get("Key", "")).upper() == "PRI"
        ]

    def _selected_original_row(self) -> Optional[list[Any]]:
        index = self.data_table.currentIndex()

        if not index.isValid():
            return None

        return self.data_model.row_values(index.row())

    def _execute_crud(self, sql: str, params=None) -> None:
        worker = DbWorker(
            self.session_manager,
            self.cfg,
            mode="run_query",
            query=sql,
            params=params or [],
            db_name=self.db_name,
        )

        worker.data_loaded.connect(
            lambda headers, rows: (
                self.services.notifications.push(
                    "ok",
                    "CRUD",
                    "Operation successful",
                ),
                self.load_data(),
            )
        )

        worker.error_occurred.connect(
            lambda e: QMessageBox.critical(self, "CRUD Error", e)
        )

        worker.start()

        if not hasattr(self, "_workers"):
            self._workers = []

        self._workers.append(worker)

    # ------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------

    def insert_record(self) -> None:
        if not self._columns_cache:
            QMessageBox.information(
                self,
                "Insert",
                "Schema not loaded yet.",
            )
            return

        dlg = RecordDialog(self, self._columns_cache, mode="insert")

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.get_values()

        if values is None:
            return

        ph = placeholder(self.backend)

        cols = ", ".join(
            qident(col["Field"], self.backend)
            for col in self._columns_cache
        )

        placeholders = ", ".join([ph] * len(values))

        sql = f"INSERT INTO {self.fqn} ({cols}) VALUES ({placeholders})"

        self._execute_crud(sql, values)

    def update_record(self) -> None:
        original = self._selected_original_row()

        if original is None:
            QMessageBox.information(
                self,
                "Update",
                "Select a row to update.",
            )
            return

        if not self._columns_cache:
            QMessageBox.information(
                self,
                "Update",
                "Schema not loaded yet.",
            )
            return

        pk_cols = self._pk_cols()

        if not pk_cols:
            QMessageBox.warning(
                self,
                "Update",
                "This table has no primary key metadata.\n"
                "Automatic UPDATE is disabled for safety.",
            )
            return

        dlg = RecordDialog(
            self,
            self._columns_cache,
            mode="update",
            current_values=[
                str(value) if value is not None else None
                for value in original
            ],
        )

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.get_values()

        ph = placeholder(self.backend)

        set_parts = []
        set_params = []

        where_parts = []
        where_params = []

        for index, col in enumerate(self._columns_cache):
            field = col.get("Field")

            new_value = values[index] if index < len(values) else None
            old_value = original[index] if index < len(original) else None

            if field in pk_cols:
                if old_value is None:
                    where_parts.append(
                        f"{qident(field, self.backend)} IS NULL"
                    )
                else:
                    where_parts.append(
                        f"{qident(field, self.backend)} = {ph}"
                    )
                    where_params.append(old_value)

            else:
                set_parts.append(
                    f"{qident(field, self.backend)} = {ph}"
                )
                set_params.append(new_value)

        if not set_parts:
            QMessageBox.information(
                self,
                "Update",
                "No non-primary-key columns to update.",
            )
            return

        if not where_parts:
            QMessageBox.warning(
                self,
                "Update",
                "Could not build a safe WHERE clause.",
            )
            return

        sql = (
            f"UPDATE {self.fqn} "
            f"SET {', '.join(set_parts)} "
            f"WHERE {' AND '.join(where_parts)}"
        )

        if self.backend == "mysql":
            sql += " LIMIT 1"

        self._execute_crud(sql, set_params + where_params)

    def delete_record(self) -> None:
        original = self._selected_original_row()

        if original is None:
            QMessageBox.information(
                self,
                "Delete",
                "Select a row to delete.",
            )
            return

        if not self._columns_cache:
            QMessageBox.information(
                self,
                "Delete",
                "Schema not loaded yet.",
            )
            return

        pk_cols = self._pk_cols()

        if not pk_cols:
            QMessageBox.warning(
                self,
                "Delete",
                "This table has no primary key metadata.\n"
                "Automatic DELETE is disabled for safety.",
            )
            return

        row_number = self.data_table.currentIndex().row() + 1

        if QMessageBox.question(
            self,
            "Delete Record",
            f"Delete row {row_number}?\nThis cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return

        ph = placeholder(self.backend)

        where_parts = []
        where_params = []

        for index, col in enumerate(self._columns_cache):
            field = col.get("Field")

            if field not in pk_cols:
                continue

            old_value = original[index] if index < len(original) else None

            if old_value is None:
                where_parts.append(
                    f"{qident(field, self.backend)} IS NULL"
                )
            else:
                where_parts.append(
                    f"{qident(field, self.backend)} = {ph}"
                )
                where_params.append(old_value)

        if not where_parts:
            QMessageBox.warning(
                self,
                "Delete",
                "Could not build a safe WHERE clause.",
            )
            return

        sql = f"DELETE FROM {self.fqn} WHERE {' AND '.join(where_parts)}"

        if self.backend == "mysql":
            sql += " LIMIT 1"

        self._execute_crud(sql, where_params)

    # ------------------------------------------------------------
    # Export
    # ------------------------------------------------------------

    def export_csv(self) -> None:
        if not self.data_model.rowCount():
            QMessageBox.information(self, "Export", "No data.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            f"{self.table_name}.csv",
            "CSV (*.csv)",
        )

        if not path:
            return

        try:
            export_result_csv(
                path,
                self.data_model._headers,
                self.data_model._rows,
            )

            self.services.notifications.push(
                "ok",
                "Export",
                path,
            )

        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
