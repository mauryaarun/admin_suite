"""
Database manager widget.
Modern database administration workbench inspired by HeidiSQL, DBeaver, and TablePlus.
"""

from __future__ import annotations

import csv
import json
import os
import time
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTableView,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.core.paths import (
    QUERY_FAVORITES_FILE,
    QUERY_HISTORY_FILE,
)
from admin_suite.core.utils import (
    read_json,
    write_json_secure,
)
from admin_suite.db.backends import BACKENDS
from admin_suite.db.cache import SchemaCache
from admin_suite.db.completer import SqlCompleter
from admin_suite.db.dialogs import RecordDialog
from admin_suite.db.export import (
    export_database,
    export_result_csv,
    export_result_json,
    export_table_sql,
)
from admin_suite.db.highlighter import SQLHighlighter, SQL_KEYWORDS
from admin_suite.db.models import SqlResultModel
from admin_suite.db.quoting import placeholder, qident, sql_literal
from admin_suite.db.session import DbSessionManager
from admin_suite.db.table_detail import TableDetailTab
from admin_suite.db.worker import DbWorker


def _format_bytes(size: Any) -> str:
    """Format byte size into human readable string."""
    try:
        val = float(size or 0)
    except Exception:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(val) < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


class DatabaseManagerWidget(QWidget):
    """
    Main database manager UI.
    Features a modern tabbed workbench:
      - 📊 Table Data (Browse, Filter, Edit, Export)
      - 📐 Table Schema (Columns, HeidiSQL Designer, Export)
      - ⚡ SQL Query Console (Execute, Explain, Favorites, Export)
      - ℹ️ Object Info & DDL (Database/Table properties, Table summaries, full DDL)
    """

    def __init__(self, services, parent=None):
        super().__init__(parent)

        self.services = services
        self.main_window = parent

        self.session_manager = DbSessionManager()

        self.active_db_profile: Optional[dict[str, Any]] = None

        self.current_schema: Optional[str] = None
        self.current_table: Optional[str] = None

        self.schema_cache = SchemaCache(
            ttl_seconds=int(
                self.services.config.get("db_schema_cache_ttl", 300)
            )
        )

        self.query_history = read_json(QUERY_HISTORY_FILE, [])
        self.query_favs = read_json(QUERY_FAVORITES_FILE, [])

        self._known_words = list(SQL_KEYWORDS)
        self._workers = []

        # State for SQL Console
        self._last_headers: list[str] = []
        self._last_rows: list[list[Any]] = []

        # State for Table Data tab
        self._current_data_headers: list[str] = []
        self._current_data_rows: list[list[Any]] = []

        # State for Table Schema tab
        self._current_table_columns: list[dict] = []

        # State for Object Info tab
        self._current_info: dict[str, Any] = {}

        theme = self.services.theme.current
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --------------------------------------------------------
        # Top toolbar
        # --------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 6, 6, 6)
        toolbar.setSpacing(6)

        self.connect_btn = QPushButton("🔌 Connect Database")
        self.connect_btn.clicked.connect(self.load_schemas)

        self.test_btn = QPushButton("🔌 Test Only")
        self.test_btn.clicked.connect(self.test_connection)

        self.refresh_all_btn = QPushButton("🔄 Refresh")
        self.refresh_all_btn.clicked.connect(self.load_schemas)

        self.hist_btn = QPushButton("🕒 History")
        self.hist_btn.clicked.connect(self.show_history)

        self.fav_btn = QPushButton("⭐ Favorites")
        self.fav_btn.clicked.connect(self.show_favorites)

        self.conn_status = QLabel("● Idle")
        self.conn_status.setStyleSheet(
            f"color:{theme['sub']};font-weight:bold;padding:0 8px;"
        )

        for button in (
            self.connect_btn,
            self.test_btn,
            self.refresh_all_btn,
            self.hist_btn,
            self.fav_btn,
        ):
            toolbar.addWidget(button)

        toolbar.addStretch()
        toolbar.addWidget(self.conn_status)

        layout.addLayout(toolbar)

        # --------------------------------------------------------
        # Main Splitter: Left (Tree) | Right (Workbench Tabs)
        # --------------------------------------------------------
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel: Tree with search filter and action bar
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)

        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("🔍 Filter schemas / tables...")
        self.tree_filter.textChanged.connect(self._filter_tree)
        left_layout.addWidget(self.tree_filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self.on_tree_expanded)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        self.tree.itemDoubleClicked.connect(self.on_tree_dbl_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_menu)
        left_layout.addWidget(self.tree, 1)

        tree_bar = QHBoxLayout()
        tree_bar.setContentsMargins(0, 0, 0, 0)
        tree_bar.setSpacing(4)

        create_db_btn = QPushButton("➕ DB")
        create_db_btn.setToolTip("Create new database")
        create_db_btn.clicked.connect(self.create_database_dialog)

        create_tbl_btn = QPushButton("➕ Table")
        create_tbl_btn.setToolTip("Create new table in selected database")
        create_tbl_btn.clicked.connect(
            lambda: self.create_table_dialog(self.current_schema or "")
        )

        tree_bar.addWidget(create_db_btn)
        tree_bar.addWidget(create_tbl_btn)
        tree_bar.addStretch()
        left_layout.addLayout(tree_bar)

        self.main_splitter.addWidget(left_panel)

        # Right panel: Workbench Tabs
        self.workbench_tabs = QTabWidget()
        self.workbench_tabs.setDocumentMode(True)

        self._build_table_data_tab()
        self._build_table_schema_tab()
        self._build_sql_query_tab()
        self._build_object_info_tab()

        self.main_splitter.addWidget(self.workbench_tabs)
        self.main_splitter.setSizes([280, 960])

        layout.addWidget(self.main_splitter, 1)

        QShortcut(QKeySequence("F5"), self).activated.connect(self.execute_query)

        # Initial export button states
        self._update_export_buttons_state()

    # ============================================================
    # Workbench Tab 0: Table Data
    # ============================================================

    def _build_table_data_tab(self) -> None:
        self.data_tab = QWidget()
        layout = QVBoxLayout(self.data_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header & Control Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        self.data_header_label = QLabel("📋 Select a table from tree to browse data")
        self.data_header_label.setStyleSheet(
            f"color:{self._theme['accent']};font-weight:bold;font-size:13px;"
        )
        top_bar.addWidget(self.data_header_label)
        top_bar.addStretch()

        layout.addLayout(top_bar)

        # Filter & Action Toolbar
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 0, 0, 0)
        action_bar.setSpacing(6)

        action_bar.addWidget(QLabel("LIMIT:"))
        self.data_limit_spin = QSpinBox()
        self.data_limit_spin.setRange(1, 100000)
        self.data_limit_spin.setValue(int(self.services.config.get("db_page_size", 200)))
        self.data_limit_spin.setFixedWidth(85)
        action_bar.addWidget(self.data_limit_spin)

        self.data_filter_input = QLineEdit()
        self.data_filter_input.setPlaceholderText("Filter WHERE clause (e.g. status='active' AND id > 10)")
        self.data_filter_input.returnPressed.connect(self.load_table_data)
        action_bar.addWidget(self.data_filter_input, 1)

        self.data_filter_btn = QPushButton("🔍 Filter")
        self.data_filter_btn.clicked.connect(self.load_table_data)
        action_bar.addWidget(self.data_filter_btn)

        self.data_refresh_btn = QPushButton("🔄 Refresh")
        self.data_refresh_btn.clicked.connect(self.load_table_data)
        action_bar.addWidget(self.data_refresh_btn)

        # CRUD actions
        self.data_insert_btn = QPushButton("➕ Insert")
        self.data_insert_btn.setToolTip("Insert new record")
        self.data_insert_btn.clicked.connect(self.insert_data_row)
        action_bar.addWidget(self.data_insert_btn)

        self.data_update_btn = QPushButton("✏️ Edit")
        self.data_update_btn.setToolTip("Edit selected record")
        self.data_update_btn.clicked.connect(self.edit_selected_data_row)
        action_bar.addWidget(self.data_update_btn)

        self.data_delete_btn = QPushButton("🗑 Delete")
        self.data_delete_btn.setToolTip("Delete selected record")
        self.data_delete_btn.clicked.connect(self.delete_selected_data_row)
        action_bar.addWidget(self.data_delete_btn)

        # Prominent dynamic export button
        self.data_export_btn = QPushButton("📤 Export Data ▾")
        self.data_export_btn.setToolTip("Export table data to CSV, JSON, or SQL INSERT")
        self.data_export_btn.clicked.connect(self.show_data_export_menu)
        action_bar.addWidget(self.data_export_btn)

        layout.addLayout(action_bar)

        # Table Grid
        self.data_table = QTableView()
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.data_model = SqlResultModel()
        self.data_table.setModel(self.data_model)
        self.data_table.doubleClicked.connect(self.edit_selected_data_row)
        ReportExporter.attach_export_context_menu(self.data_table)
        layout.addWidget(self.data_table, 1)

        # Bottom query bar & status
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 0, 0, 0)
        bottom_bar.setSpacing(6)

        self.data_status_label = QLabel("0 rows")
        self.data_status_label.setStyleSheet(f"color:{self._theme['sub']};font-weight:bold;")
        bottom_bar.addWidget(self.data_status_label)

        self.data_sql_preview = QLineEdit()
        self.data_sql_preview.setReadOnly(True)
        self.data_sql_preview.setPlaceholderText("Active query preview...")
        self.data_sql_preview.setStyleSheet(
            f"background:{self._theme['panel2']};color:{self._theme['text']};font-family:monospace;font-size:11px;"
        )
        bottom_bar.addWidget(self.data_sql_preview, 1)

        self.data_open_sql_btn = QPushButton("⚡ Open in Console")
        self.data_open_sql_btn.setToolTip("Send this query to the SQL Console tab")
        self.data_open_sql_btn.clicked.connect(self._transfer_data_query_to_console)
        bottom_bar.addWidget(self.data_open_sql_btn)

        layout.addLayout(bottom_bar)

        self.workbench_tabs.addTab(self.data_tab, "📊 Table Data")

    # ============================================================
    # Workbench Tab 1: Table Schema
    # ============================================================

    def _build_table_schema_tab(self) -> None:
        self.schema_tab = QWidget()
        layout = QVBoxLayout(self.schema_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)

        self.schema_header_label = QLabel("📐 Select a table from tree to view schema")
        self.schema_header_label.setStyleSheet(
            f"color:{self._theme['accent']};font-weight:bold;font-size:13px;"
        )
        bar.addWidget(self.schema_header_label)
        bar.addStretch()

        self.alter_table_btn = QPushButton("🛠️ Design / Alter Table (HeidiSQL Mode)")
        self.alter_table_btn.setToolTip("Open full visual Table Designer (columns, keys, indexes)")
        self.alter_table_btn.clicked.connect(self.open_table_designer)
        bar.addWidget(self.alter_table_btn)

        self.schema_export_btn = QPushButton("📤 Export Schema ▾")
        self.schema_export_btn.setToolTip("Export column definitions or CREATE TABLE DDL")
        self.schema_export_btn.clicked.connect(self.show_schema_export_menu)
        bar.addWidget(self.schema_export_btn)

        self.schema_refresh_btn = QPushButton("🔄 Refresh")
        self.schema_refresh_btn.clicked.connect(self.load_table_schema)
        bar.addWidget(self.schema_refresh_btn)

        layout.addLayout(bar)

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabels(
            ["Field", "Type", "Null", "Key", "Default", "Extra"]
        )
        self.schema_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        ReportExporter.attach_export_context_menu(self.schema_tree)
        layout.addWidget(self.schema_tree, 1)

        self.schema_status_label = QLabel("")
        self.schema_status_label.setStyleSheet(f"color:{self._theme['sub']};padding:2px;")
        layout.addWidget(self.schema_status_label)

        self.workbench_tabs.addTab(self.schema_tab, "📐 Table Schema")

    # ============================================================
    # Workbench Tab 2: SQL Query Console
    # ============================================================

    def _build_sql_query_tab(self) -> None:
        self.query_tab = QWidget()
        layout = QVBoxLayout(self.query_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Toolbar
        query_buttons = QHBoxLayout()
        query_buttons.setContentsMargins(0, 0, 0, 0)
        query_buttons.setSpacing(6)

        self.exec_btn = QPushButton("▶ Execute (F5)")
        self.exec_btn.setStyleSheet(
            f"background:{self._theme['accent']};color:white;font-weight:bold;padding:4px 14px;"
        )
        self.exec_btn.clicked.connect(self.execute_query)

        self.explain_btn = QPushButton("📊 EXPLAIN")
        self.explain_btn.clicked.connect(self.explain_query)

        self.star_btn = QPushButton("⭐ Save Favorite")
        self.star_btn.clicked.connect(self.save_favorite)

        self.clear_btn = QPushButton("🧹 Clear")
        self.clear_btn.clicked.connect(lambda: self.query_edit.clear())

        # Prominent dynamic export button
        self.query_export_btn = QPushButton("📤 Export Results ▾")
        self.query_export_btn.setToolTip("Export query results to CSV, JSON, or Markdown")
        self.query_export_btn.clicked.connect(self.show_query_export_menu)

        query_buttons.addWidget(self.exec_btn)
        query_buttons.addWidget(self.explain_btn)
        query_buttons.addWidget(self.star_btn)
        query_buttons.addWidget(self.clear_btn)
        query_buttons.addStretch()
        query_buttons.addWidget(self.query_export_btn)

        layout.addLayout(query_buttons)

        # Vertical Splitter: Editor / Results Table
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.query_edit = QTextEdit()
        self.query_edit.setFont(QFont("JetBrains Mono, Consolas", 11))
        self.query_edit.setPlaceholderText(
            "-- Write SQL here. Press F5 or ▶ Execute to run.\n"
            "-- Double-click a table in the tree to browse its data and schema.\n"
            "-- Autocompletion active for keywords, schemas, and columns."
        )

        self._highlighter = SQLHighlighter(self.query_edit.document())
        self._completer = SqlCompleter(self.query_edit)
        self.query_edit.textChanged.connect(self._completer.maybe_complete)

        splitter.addWidget(self.query_edit)

        results_widget = QWidget()
        res_layout = QVBoxLayout(results_widget)
        res_layout.setContentsMargins(0, 0, 0, 0)
        res_layout.setSpacing(4)

        self.results_table = QTableView()
        self.results_table.setAlternatingRowColors(True)
        self.result_model = SqlResultModel()
        self.results_table.setModel(self.result_model)
        ReportExporter.attach_export_context_menu(self.results_table)
        res_layout.addWidget(self.results_table, 1)

        self.row_count_label = QLabel("No query executed yet.")
        self.row_count_label.setStyleSheet(f"color:{self._theme['sub']};padding:2px;")
        res_layout.addWidget(self.row_count_label)

        splitter.addWidget(results_widget)
        splitter.setSizes([200, 380])

        layout.addWidget(splitter, 1)

        self.workbench_tabs.addTab(self.query_tab, "⚡ SQL Console")

    # ============================================================
    # Workbench Tab 3: Object Info & DDL
    # ============================================================

    def _build_object_info_tab(self) -> None:
        self.info_tab = QWidget()
        layout = QVBoxLayout(self.info_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Top Header
        self.info_header_label = QLabel("ℹ️ Select a database or table to view information")
        self.info_header_label.setStyleSheet(
            f"color:{self._theme['accent']};font-weight:bold;font-size:13px;"
        )
        layout.addWidget(self.info_header_label)

        # Properties Grid Container
        self.info_props_widget = QWidget()
        self.info_props_layout = QVBoxLayout(self.info_props_widget)
        self.info_props_layout.setContentsMargins(0, 0, 0, 0)
        self.info_props_layout.setSpacing(4)

        self.info_metrics_label = QLabel("No object selected.")
        self.info_metrics_label.setStyleSheet(
            f"background:{self._theme['panel2']};padding:10px;border-radius:4px;border:1px solid {self._theme['border']};line-height:1.4;"
        )
        self.info_props_layout.addWidget(self.info_metrics_label)

        layout.addWidget(self.info_props_widget)

        # Bottom Split Area: Database tables summary OR Table DDL
        self.info_detail_splitter = QSplitter(Qt.Orientation.Vertical)

        # Database view: Tables summary table
        self.db_summary_widget = QWidget()
        db_sum_layout = QVBoxLayout(self.db_summary_widget)
        db_sum_layout.setContentsMargins(0, 0, 0, 0)
        db_sum_layout.setSpacing(4)

        db_bar = QHBoxLayout()
        db_bar.addWidget(QLabel("Tables Summary:"))
        self.db_tables_filter = QLineEdit()
        self.db_tables_filter.setPlaceholderText("Filter tables...")
        self.db_tables_filter.textChanged.connect(self._filter_db_summary_tables)
        db_bar.addWidget(self.db_tables_filter, 1)

        self.export_db_dump_btn = QPushButton("📤 Export Database Dump (SQL)")
        self.export_db_dump_btn.clicked.connect(lambda: self.export_database(self.current_schema or ""))
        db_bar.addWidget(self.export_db_dump_btn)

        db_sum_layout.addLayout(db_bar)

        self.db_summary_table = QTableWidget()
        self.db_summary_table.setColumnCount(6)
        self.db_summary_table.setHorizontalHeaderLabels(
            ["Table Name", "Engine", "Rows", "Data Size", "Index Size", "Collation"]
        )
        self.db_summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.db_summary_table.setAlternatingRowColors(True)
        self.db_summary_table.itemDoubleClicked.connect(self._on_summary_table_dbl_click)
        ReportExporter.attach_export_context_menu(self.db_summary_table)
        db_sum_layout.addWidget(self.db_summary_table, 1)

        self.info_detail_splitter.addWidget(self.db_summary_widget)

        # Table view: DDL Viewer
        self.table_ddl_widget = QWidget()
        ddl_layout = QVBoxLayout(self.table_ddl_widget)
        ddl_layout.setContentsMargins(0, 0, 0, 0)
        ddl_layout.setSpacing(4)

        ddl_bar = QHBoxLayout()
        ddl_bar.addWidget(QLabel("CREATE TABLE Definition (DDL):"))
        ddl_bar.addStretch()

        self.copy_ddl_btn = QPushButton("📋 Copy DDL")
        self.copy_ddl_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self.table_ddl_edit.toPlainText())
        )
        ddl_bar.addWidget(self.copy_ddl_btn)

        self.export_ddl_btn = QPushButton("💾 Export DDL (*.sql)")
        self.export_ddl_btn.clicked.connect(self._export_table_ddl)
        ddl_bar.addWidget(self.export_ddl_btn)

        ddl_layout.addLayout(ddl_bar)

        self.table_ddl_edit = QPlainTextEdit()
        self.table_ddl_edit.setReadOnly(True)
        self.table_ddl_edit.setFont(QFont("JetBrains Mono, Consolas", 10))
        self._ddl_highlighter = SQLHighlighter(self.table_ddl_edit.document())
        ddl_layout.addWidget(self.table_ddl_edit, 1)

        self.info_detail_splitter.addWidget(self.table_ddl_widget)

        layout.addWidget(self.info_detail_splitter, 1)

        # Hide detail widgets initially until selection
        self.db_summary_widget.hide()
        self.table_ddl_widget.hide()

        self.workbench_tabs.addTab(self.info_tab, "ℹ️ Object Info & DDL")

    # ============================================================
    # Tree Filtering & Navigation
    # ============================================================

    def _filter_tree(self, text: str) -> None:
        pattern = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            db_item = self.tree.topLevelItem(i)
            db_match = pattern in db_item.text(0).lower()
            any_child_match = False
            for j in range(db_item.childCount()):
                child = db_item.child(j)
                child_match = pattern in child.text(0).lower()
                child.setHidden(bool(pattern and not child_match and not db_match))
                if child_match:
                    any_child_match = True
            db_item.setHidden(bool(pattern and not db_match and not any_child_match))
            if pattern and any_child_match:
                db_item.setExpanded(True)

    def _filter_db_summary_tables(self, text: str) -> None:
        pattern = text.strip().lower()
        for r in range(self.db_summary_table.rowCount()):
            item = self.db_summary_table.item(r, 0)
            if item:
                match = pattern in item.text().lower()
                self.db_summary_table.setRowHidden(r, bool(pattern and not match))

    def _on_summary_table_dbl_click(self, item: QTableWidgetItem) -> None:
        row = item.row()
        tbl_item = self.db_summary_table.item(row, 0)
        if tbl_item and self.current_schema:
            tbl_name = tbl_item.text().strip()
            self.current_table = tbl_name
            self.load_table_data(self.current_schema, tbl_name)
            self.load_table_schema(self.current_schema, tbl_name)
            self.load_table_info(self.current_schema, tbl_name)
            self.workbench_tabs.setCurrentIndex(0)

    # ============================================================
    # Tree Item Selection Handlers
    # ============================================================

    def on_tree_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if items:
            self.on_tree_item_clicked(items[0], 0)

    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int = 0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data[0] == "schema":
            db_name = data[1]
            self.current_schema = db_name
            self.current_table = None

            self.info_header_label.setText(f"🗄 Database: {db_name}")
            self.load_db_info(db_name)

        elif data[0] == "table":
            db_name = data[1]
            table_name = data[2]
            self.current_schema = db_name
            self.current_table = table_name

            fqn = f"{self._qident(db_name)}.{self._qident(table_name)}"
            self.data_header_label.setText(f"📋 {fqn}")
            self.schema_header_label.setText(f"📐 {fqn}")
            self.info_header_label.setText(f"📋 Table: {fqn}")

            limit = self.data_limit_spin.value()
            self.data_sql_preview.setText(f"SELECT * FROM {fqn} LIMIT {limit};")

            # Load schema into Schema tab
            self.load_table_schema(db_name, table_name)
            # Load metadata and DDL into Info tab
            self.load_table_info(db_name, table_name)

    def on_tree_dbl_click(self, item: QTreeWidgetItem, column: int = 0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data[0] == "table":
            db_name = data[1]
            table_name = data[2]
            self.current_schema = db_name
            self.current_table = table_name

            fqn = f"{self._qident(db_name)}.{self._qident(table_name)}"
            limit = self.data_limit_spin.value()

            # Pre-fill query editor
            query = f"SELECT * FROM {fqn} LIMIT {limit};"
            self.query_edit.setPlainText(query)

            # Load Schema, Info, and Data
            self.load_table_schema(db_name, table_name)
            self.load_table_info(db_name, table_name)
            self.load_table_data(db_name, table_name)

            # Switch to Table Data tab
            self.workbench_tabs.setCurrentIndex(0)

        elif data[0] == "schema":
            db_name = data[1]
            self.current_schema = db_name
            self.current_table = None

            item.setExpanded(not item.isExpanded())
            self.load_db_info(db_name)
            self.workbench_tabs.setCurrentIndex(3)

    # ============================================================
    # Loading Data / Schema / Info
    # ============================================================

    def load_table_data(self, db_name: Optional[str] = None, table_name: Optional[str] = None) -> None:
        db = db_name or self.current_schema
        table = table_name or self.current_table
        if not db or not table:
            return

        fqn = f"{self._qident(db)}.{self._qident(table)}"
        limit = self.data_limit_spin.value()
        filter_clause = self.data_filter_input.text().strip()

        if filter_clause:
            if not filter_clause.upper().startswith("WHERE"):
                filter_clause = "WHERE " + filter_clause
            query = f"SELECT * FROM {fqn} {filter_clause} LIMIT {limit};"
        else:
            query = f"SELECT * FROM {fqn} LIMIT {limit};"

        self.data_sql_preview.setText(query)
        self.data_status_label.setText("Loading data...")

        started_at = time.perf_counter()

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=query,
            db_name=db,
        )

        def on_loaded(headers, rows):
            elapsed = time.perf_counter() - started_at
            self._on_table_data_loaded(headers, rows, elapsed)

        worker.data_loaded.connect(on_loaded)
        self._start_worker(worker)

    def _on_table_data_loaded(self, headers: list[str], rows: list[list[Any]], elapsed: float) -> None:
        self._current_data_headers = headers or []
        self._current_data_rows = rows or []

        self.data_model.set_result(self._current_data_headers, self._current_data_rows)
        self.data_table.resizeColumnsToContents()
        self.data_status_label.setText(
            f"{len(rows)} row(s), {len(headers)} col(s) in {elapsed * 1000:.0f} ms"
        )
        self._update_export_buttons_state()

    def load_table_schema(self, db_name: Optional[str] = None, table_name: Optional[str] = None) -> None:
        db = db_name or self.current_schema
        table = table_name or self.current_table
        if not db or not table:
            return

        cache_key = ("columns", db, table)
        cached = self.schema_cache.get(cache_key)
        if cached is not None:
            self._on_table_schema_loaded(cached)
            return

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="fetch_columns",
            db_name=db,
            table_name=table,
        )

        def cols_loaded(cols):
            self.schema_cache.set(("columns", db, table), cols)
            self._on_table_schema_loaded(cols)

        worker.columns_loaded.connect(cols_loaded)
        self._start_worker(worker)

    def _on_table_schema_loaded(self, columns: list[dict]) -> None:
        self._current_table_columns = columns or []
        self.schema_tree.clear()

        pk_cols = []
        for col in self._current_table_columns:
            field = str(col.get("Field", ""))
            ctype = str(col.get("Type", ""))
            cnull = str(col.get("Null", ""))
            ckey = str(col.get("Key", ""))
            cdef = str(col.get("Default", "") or "")
            cextra = str(col.get("Extra", ""))

            if ckey.upper() == "PRI":
                pk_cols.append(field)

            item = QTreeWidgetItem([field, ctype, cnull, ckey, cdef, cextra])
            self.schema_tree.addTopLevelItem(item)

        pk_text = f" · Primary Key: {', '.join(pk_cols)}" if pk_cols else ""
        self.schema_status_label.setText(
            f"{len(self._current_table_columns)} column(s){pk_text}"
        )
        self._update_export_buttons_state()

    def load_table_info(self, db_name: Optional[str] = None, table_name: Optional[str] = None) -> None:
        db = db_name or self.current_schema
        table = table_name or self.current_table
        if not db or not table:
            return

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="fetch_table_info",
            db_name=db,
            table_name=table,
        )
        worker.table_info_loaded.connect(self._on_table_info_loaded)
        self._start_worker(worker)

    def _on_table_info_loaded(self, info: dict[str, Any]) -> None:
        self._current_info = info
        self.db_summary_widget.hide()
        self.table_ddl_widget.show()

        engine = info.get("engine", "Unknown")
        rows = info.get("rows", 0)
        data_len = _format_bytes(info.get("data_length", 0))
        idx_len = _format_bytes(info.get("index_length", 0))
        auto_inc = info.get("auto_increment") or "N/A"
        collation = info.get("collation") or "Default"
        row_fmt = info.get("row_format") or "Standard"
        created = info.get("create_time") or "N/A"

        metrics_html = (
            f"<b>Table:</b> {info.get('table_name')} &nbsp; | &nbsp; "
            f"<b>Database:</b> {info.get('schema_name')} &nbsp; | &nbsp; "
            f"<b>Engine:</b> {engine} &nbsp; | &nbsp; "
            f"<b>Row Format:</b> {row_fmt}<br>"
            f"<b>Est. Rows:</b> {rows:,} &nbsp; | &nbsp; "
            f"<b>Data Size:</b> {data_len} &nbsp; | &nbsp; "
            f"<b>Index Size:</b> {idx_len} &nbsp; | &nbsp; "
            f"<b>Auto-Inc:</b> {auto_inc} &nbsp; | &nbsp; "
            f"<b>Collation:</b> {collation} &nbsp; | &nbsp; "
            f"<b>Created:</b> {created}"
        )
        self.info_metrics_label.setText(metrics_html)

        ddl = info.get("ddl", "")
        self.table_ddl_edit.setPlainText(ddl)

    def load_db_info(self, db_name: Optional[str] = None) -> None:
        db = db_name or self.current_schema
        if not db:
            return

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="fetch_db_info",
            db_name=db,
        )
        worker.db_info_loaded.connect(self._on_db_info_loaded)
        self._start_worker(worker)

    def _on_db_info_loaded(self, info: dict[str, Any]) -> None:
        self._current_info = info
        self.table_ddl_widget.hide()
        self.db_summary_widget.show()

        backend = info.get("backend", "Database")
        charset = info.get("charset", "utf8mb4")
        collation = info.get("collation", "utf8mb4_unicode_ci")
        tbl_cnt = info.get("table_count", 0)
        total_data = _format_bytes(info.get("total_data_bytes", 0))
        total_idx = _format_bytes(info.get("total_index_bytes", 0))

        metrics_html = (
            f"<b>Database:</b> {info.get('schema_name')} &nbsp; | &nbsp; "
            f"<b>Backend:</b> {backend} &nbsp; | &nbsp; "
            f"<b>Charset:</b> {charset} &nbsp; | &nbsp; "
            f"<b>Collation:</b> {collation}<br>"
            f"<b>Total Tables:</b> {tbl_cnt} &nbsp; | &nbsp; "
            f"<b>Total Data Size:</b> {total_data} &nbsp; | &nbsp; "
            f"<b>Total Index Size:</b> {total_idx}"
        )
        self.info_metrics_label.setText(metrics_html)

        # Populate tables summary grid
        tables = info.get("tables", [])
        self.db_summary_table.setRowCount(len(tables))

        for r, tbl in enumerate(tables):
            name_item = QTableWidgetItem(tbl.get("name", ""))
            engine_item = QTableWidgetItem(tbl.get("engine", "InnoDB"))
            rows_item = QTableWidgetItem(f"{tbl.get('rows', 0):,}")
            data_item = QTableWidgetItem(_format_bytes(tbl.get("data_length", 0)))
            idx_item = QTableWidgetItem(_format_bytes(tbl.get("index_length", 0)))
            coll_item = QTableWidgetItem(tbl.get("collation", ""))

            for item in (name_item, engine_item, rows_item, data_item, idx_item, coll_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self.db_summary_table.setItem(r, 0, name_item)
            self.db_summary_table.setItem(r, 1, engine_item)
            self.db_summary_table.setItem(r, 2, rows_item)
            self.db_summary_table.setItem(r, 3, data_item)
            self.db_summary_table.setItem(r, 4, idx_item)
            self.db_summary_table.setItem(r, 5, coll_item)

        self.db_summary_table.resizeColumnsToContents()

    # ============================================================
    # Dynamic Export Button State & Menus
    # ============================================================

    def _update_export_buttons_state(self) -> None:
        """Update visual state of export buttons depending on populated data."""
        # Query results
        has_query_rows = bool(self._last_rows)
        self.query_export_btn.setEnabled(has_query_rows)
        if has_query_rows:
            self.query_export_btn.setStyleSheet(
                f"background:{self._theme['ok']};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
            )
        else:
            self.query_export_btn.setStyleSheet("")

        # Data tab
        has_data_rows = bool(self._current_data_rows)
        self.data_export_btn.setEnabled(has_data_rows)
        if has_data_rows:
            self.data_export_btn.setStyleSheet(
                f"background:{self._theme['ok']};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
            )
        else:
            self.data_export_btn.setStyleSheet("")

        # Schema tab
        has_schema = bool(self._current_table_columns)
        self.schema_export_btn.setEnabled(has_schema)
        if has_schema:
            self.schema_export_btn.setStyleSheet(
                f"background:{self._theme['accent']};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
            )
        else:
            self.schema_export_btn.setStyleSheet("")

    def show_query_export_menu(self) -> None:
        if not self._last_rows or not self._last_headers:
            QMessageBox.information(self, "Export", "No query results to export.")
            return

        menu = QMenu(self)
        menu.addAction("📄 Export Results (CSV)").triggered.connect(
            lambda: self.export_result("csv")
        )
        menu.addAction("📋 Export Results (JSON)").triggered.connect(
            lambda: self.export_result("json")
        )
        menu.addAction("📝 Export Results (Markdown Table)").triggered.connect(
            lambda: self._export_query_markdown()
        )
        menu.addSeparator()
        menu.addAction("📋 Copy to Clipboard (TSV)").triggered.connect(
            lambda: self._copy_results_to_clipboard()
        )
        menu.exec(self.query_export_btn.mapToGlobal(self.query_export_btn.rect().bottomLeft()))

    def show_data_export_menu(self) -> None:
        if not self._current_data_rows or not self._current_data_headers:
            QMessageBox.information(self, "Export", "No table data loaded to export.")
            return

        tbl_name = self.current_table or "table"
        menu = QMenu(self)
        menu.addAction("📄 Export Current Rows (CSV)").triggered.connect(
            lambda: self._export_data_tab_csv()
        )
        menu.addAction("📋 Export Current Rows (JSON)").triggered.connect(
            lambda: self._export_data_tab_json()
        )
        menu.addAction("💾 Export SQL INSERT Statements (*.sql)").triggered.connect(
            lambda: self.export_table_sql(self.current_schema or "", tbl_name)
        )
        menu.addSeparator()
        menu.addAction("📋 Copy to Clipboard (TSV)").triggered.connect(
            lambda: self._copy_data_to_clipboard()
        )
        menu.exec(self.data_export_btn.mapToGlobal(self.data_export_btn.rect().bottomLeft()))

    def show_schema_export_menu(self) -> None:
        if not self._current_table_columns:
            QMessageBox.information(self, "Export", "No schema columns loaded to export.")
            return

        tbl_name = self.current_table or "table"
        menu = QMenu(self)
        menu.addAction("📄 Export Columns (CSV)").triggered.connect(
            lambda: self._export_schema_csv()
        )
        menu.addAction("📋 Export Columns (JSON)").triggered.connect(
            lambda: self._export_schema_json()
        )
        menu.addAction("💾 Export CREATE TABLE DDL (*.sql)").triggered.connect(
            self._export_table_ddl
        )
        menu.exec(self.schema_export_btn.mapToGlobal(self.schema_export_btn.rect().bottomLeft()))

    def _export_data_tab_csv(self) -> None:
        tbl = self.current_table or "data"
        path, _ = QFileDialog.getSaveFileName(self, "Export Table Data CSV", f"{tbl}.csv", "CSV (*.csv)")
        if path:
            export_result_csv(path, self._current_data_headers, self._current_data_rows)
            self.services.notifications.push("ok", "Export", f"{len(self._current_data_rows)} rows → {path}")

    def _export_data_tab_json(self) -> None:
        tbl = self.current_table or "data"
        path, _ = QFileDialog.getSaveFileName(self, "Export Table Data JSON", f"{tbl}.json", "JSON (*.json)")
        if path:
            export_result_json(path, self._current_data_headers, self._current_data_rows)
            self.services.notifications.push("ok", "Export", f"{len(self._current_data_rows)} rows → {path}")

    def _export_schema_csv(self) -> None:
        tbl = self.current_table or "schema"
        path, _ = QFileDialog.getSaveFileName(self, "Export Schema CSV", f"{tbl}_schema.csv", "CSV (*.csv)")
        if not path:
            return
        headers = ["Field", "Type", "Null", "Key", "Default", "Extra"]
        rows = [
            [
                col.get("Field", ""),
                col.get("Type", ""),
                col.get("Null", ""),
                col.get("Key", ""),
                col.get("Default", ""),
                col.get("Extra", ""),
            ]
            for col in self._current_table_columns
        ]
        export_result_csv(path, headers, rows)
        self.services.notifications.push("ok", "Export", f"Schema exported to {path}")

    def _export_schema_json(self) -> None:
        tbl = self.current_table or "schema"
        path, _ = QFileDialog.getSaveFileName(self, "Export Schema JSON", f"{tbl}_schema.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._current_table_columns, f, indent=2, default=str)
        self.services.notifications.push("ok", "Export", f"Schema JSON exported to {path}")

    def _export_table_ddl(self) -> None:
        ddl = self.table_ddl_edit.toPlainText().strip()
        if not ddl:
            QMessageBox.information(self, "DDL", "No DDL available to export.")
            return
        tbl = self.current_table or "table"
        path, _ = QFileDialog.getSaveFileName(self, "Export DDL", f"{tbl}_ddl.sql", "SQL Files (*.sql)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(ddl + "\n")
        self.services.notifications.push("ok", "Export", f"DDL exported to {path}")

    def _export_query_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Markdown", "results.md", "Markdown (*.md)")
        if not path:
            return
        lines = ["| " + " | ".join(self._last_headers) + " |"]
        lines.append("| " + " | ".join(["---"] * len(self._last_headers)) + " |")
        for r in self._last_rows[:1000]:
            lines.append("| " + " | ".join(str(v or "").replace("|", "\\|") for v in r) + " |")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.services.notifications.push("ok", "Export", f"Markdown exported to {path}")

    def _copy_results_to_clipboard(self) -> None:
        lines = ["\t".join(self._last_headers)]
        for r in self._last_rows:
            lines.append("\t".join(str(v or "") for v in r))
        QApplication.clipboard().setText("\n".join(lines))
        self.services.notifications.push("ok", "Clipboard", "Copied results to clipboard as TSV")

    def _copy_data_to_clipboard(self) -> None:
        lines = ["\t".join(self._current_data_headers)]
        for r in self._current_data_rows:
            lines.append("\t".join(str(v or "") for v in r))
        QApplication.clipboard().setText("\n".join(lines))
        self.services.notifications.push("ok", "Clipboard", "Copied table data to clipboard as TSV")

    def _transfer_data_query_to_console(self) -> None:
        query = self.data_sql_preview.text().strip()
        if query:
            self.query_edit.setPlainText(query)
            self.workbench_tabs.setCurrentIndex(2)

    # ============================================================
    # HeidiSQL Designer & CRUD Operations
    # ============================================================

    def open_table_designer(self) -> None:
        if not self.current_schema or not self.current_table:
            QMessageBox.information(self, "Designer", "Please select a table from the tree first.")
            return

        from admin_suite.db.table_designer import TableDesignerDialog

        dlg = TableDesignerDialog(
            services=self.services,
            session_manager=self.session_manager,
            cfg=self._build_cfg(),
            db_name=self.current_schema,
            table_name=self.current_table,
            initial_columns=self._current_table_columns,
            parent=self,
        )
        dlg.schema_altered.connect(lambda: (
            self.load_table_schema(self.current_schema, self.current_table),
            self.load_table_info(self.current_schema, self.current_table),
            self.load_table_data(self.current_schema, self.current_table),
        ))
        dlg.exec()

    def insert_data_row(self) -> None:
        if not self.current_schema or not self.current_table:
            QMessageBox.information(self, "Insert", "Please select a table first.")
            return

        if not self._current_table_columns:
            self.load_table_schema()

        cols = self._current_table_columns or [{"Field": h, "Type": "varchar", "Null": "YES"} for h in self._current_data_headers]
        dlg = RecordDialog(self, cols, mode="insert")

        if dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.get_values()
            fields = [self._qident(c.get("Field", "")) for c in cols]
            backend = self._backend_name()

            ph = ", ".join(placeholder(backend, i + 1) for i in range(len(values)))
            cols_str = ", ".join(fields)
            fqn = f"{self._qident(self.current_schema)}.{self._qident(self.current_table)}"
            sql = f"INSERT INTO {fqn} ({cols_str}) VALUES ({ph});"

            worker = DbWorker(
                self.session_manager,
                self._build_cfg(),
                mode="run_query",
                query=sql,
                params=values,
                db_name=self.current_schema,
            )
            worker.finished.connect(self.load_table_data)
            self._start_worker(worker)

    def edit_selected_data_row(self) -> None:
        selected_indexes = self.data_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Edit", "Please select a row to edit.")
            return

        row_idx = selected_indexes[0].row()
        if row_idx >= len(self._current_data_rows):
            return

        current_values = self._current_data_rows[row_idx]
        cols = self._current_table_columns or [{"Field": h, "Type": "varchar", "Null": "YES"} for h in self._current_data_headers]

        dlg = RecordDialog(self, cols, mode="update", current_values=current_values)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_values = dlg.get_values()
        backend = self._backend_name()
        fqn = f"{self._qident(self.current_schema)}.{self._qident(self.current_table)}"

        # Identify Primary Keys
        pk_cols = [c.get("Field") for c in cols if str(c.get("Key", "")).upper() == "PRI"]
        set_clauses = []
        params = []

        for i, col in enumerate(cols):
            col_name = col.get("Field", "")
            set_clauses.append(f"{self._qident(col_name)} = {placeholder(backend, len(params) + 1)}")
            params.append(new_values[i])

        where_clauses = []
        if pk_cols:
            for pk in pk_cols:
                pk_idx = next((idx for idx, c in enumerate(cols) if c.get("Field") == pk), -1)
                if pk_idx >= 0:
                    where_clauses.append(f"{self._qident(pk)} = {placeholder(backend, len(params) + 1)}")
                    params.append(current_values[pk_idx])
        else:
            for i, col in enumerate(cols):
                val = current_values[i]
                if val is None:
                    where_clauses.append(f"{self._qident(col.get('Field'))} IS NULL")
                else:
                    where_clauses.append(f"{self._qident(col.get('Field'))} = {placeholder(backend, len(params) + 1)}")
                    params.append(val)

        sql = f"UPDATE {fqn} SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)};"

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=sql,
            params=params,
            db_name=self.current_schema,
        )
        worker.finished.connect(self.load_table_data)
        self._start_worker(worker)

    def delete_selected_data_row(self) -> None:
        selected_indexes = self.data_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Delete", "Please select a row to delete.")
            return

        if QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this row? This cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return

        row_idx = selected_indexes[0].row()
        if row_idx >= len(self._current_data_rows):
            return

        current_values = self._current_data_rows[row_idx]
        cols = self._current_table_columns or [{"Field": h, "Type": "varchar", "Null": "YES"} for h in self._current_data_headers]
        backend = self._backend_name()
        fqn = f"{self._qident(self.current_schema)}.{self._qident(self.current_table)}"

        pk_cols = [c.get("Field") for c in cols if str(c.get("Key", "")).upper() == "PRI"]
        where_clauses = []
        params = []

        if pk_cols:
            for pk in pk_cols:
                pk_idx = next((idx for idx, c in enumerate(cols) if c.get("Field") == pk), -1)
                if pk_idx >= 0:
                    where_clauses.append(f"{self._qident(pk)} = {placeholder(backend, len(params) + 1)}")
                    params.append(current_values[pk_idx])
        else:
            for i, col in enumerate(cols):
                val = current_values[i]
                if val is None:
                    where_clauses.append(f"{self._qident(col.get('Field'))} IS NULL")
                else:
                    where_clauses.append(f"{self._qident(col.get('Field'))} = {placeholder(backend, len(params) + 1)}")
                    params.append(val)

        sql = f"DELETE FROM {fqn} WHERE {' AND '.join(where_clauses)};"

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=sql,
            params=params,
            db_name=self.current_schema,
        )
        worker.finished.connect(self.load_table_data)
        self._start_worker(worker)

    # ============================================================
    # Config / Profile Helpers
    # ============================================================

    def _backend_name(self) -> str:
        return self._build_cfg().get("backend", "mysql")

    def _qident(self, ident: str) -> str:
        return qident(ident, self._backend_name())

    def set_active_profile(self, profile: Optional[dict[str, Any]]) -> None:
        self.active_db_profile = profile
        if profile:
            self.conn_status.setText(f"● Profile: {profile.get('name', 'custom')}")

    def _build_cfg(self) -> dict[str, Any]:
        if self.active_db_profile:
            cfg = dict(self.active_db_profile)
            cfg.setdefault(
                "use_tunnel",
                self.active_db_profile.get("use_tunnel", False),
            )
            return cfg

        return {
            "backend": self.services.config.get("db_backend", "mysql"),
            "ssh_host": self.services.config.get("ssh_host", ""),
            "ssh_user": self.services.config.get("ssh_user", ""),
            "ssh_port": self.services.config.get("ssh_port", "22"),
            "ssh_pass": self.services.secrets.get("ssh_pass", ""),
            "ssh_key_path": "",
            "db_host": self.services.config.get("db_host", "127.0.0.1"),
            "db_port": self.services.config.get("db_port", "3306"),
            "db_user": self.services.config.get("db_user", ""),
            "db_pass": self.services.secrets.get("db_pass", ""),
            "db_name": self.services.config.get("db_name", ""),
            "sqlite_path": self.services.config.get("sqlite_path", ""),
            "use_tunnel": self.services.config.get("db_use_tunnel", True),
        }

    def _start_worker(self, worker: DbWorker) -> None:
        worker.error_occurred.connect(self.on_db_error)
        worker.start()
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]

    # ============================================================
    # Schema Tree Population
    # ============================================================

    def test_connection(self) -> None:
        self.conn_status.setText("● Testing...")
        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="fetch_schemas",
        )
        worker.schemas_loaded.connect(
            lambda schemas: (
                self.conn_status.setText("● Reachable ✅"),
                self.services.notifications.push(
                    "ok", "Database", "Connection OK"
                ),
            )
        )
        self._start_worker(worker)

    def load_schemas(self) -> None:
        self.conn_status.setText("● Loading...")
        self.schema_cache.invalidate()

        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem(["Loading schemas..."]))

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="fetch_schemas",
        )
        worker.schemas_loaded.connect(self.populate_schemas)
        self._start_worker(worker)

    def populate_schemas(self, schemas: list[str]) -> None:
        self.tree.clear()
        skip = ["information_schema", "performance_schema", "sys"]
        backend = self._backend_name()

        for db in schemas:
            if backend == "mysql" and db in skip:
                continue

            item = QTreeWidgetItem([f"🗄 {db}"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("schema", db))
            item.addChild(QTreeWidgetItem(["Loading..."]))
            self.tree.addTopLevelItem(item)

        self.conn_status.setText(f"● Connected ({backend}) ✅")
        self.services.notifications.push(
            "ok", "Database", f"{len(schemas)} schema(s) loaded"
        )

    def on_tree_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data[0] == "schema":
            db_name = data[1]
            cache_key = ("tables", db_name)
            cached = self.schema_cache.get(cache_key)

            if cached is not None:
                item.takeChildren()
                self.populate_tables(item, cached, db_name)
                return

            item.takeChildren()
            worker = DbWorker(
                self.session_manager,
                self._build_cfg(),
                mode="fetch_tables",
                db_name=db_name,
            )

            def tables_loaded(tables, it=item, db=db_name):
                self.schema_cache.set(("tables", db), tables)
                self.populate_tables(it, tables, db)

            worker.tables_loaded.connect(tables_loaded)
            self._start_worker(worker)

        elif data[0] == "table":
            db_name = data[1]
            table_name = data[2]

            cache_key = ("columns", db_name, table_name)
            cached = self.schema_cache.get(cache_key)

            if cached is not None:
                item.takeChildren()
                self.populate_columns(item, cached)
                return

            item.takeChildren()
            worker = DbWorker(
                self.session_manager,
                self._build_cfg(),
                mode="fetch_columns",
                db_name=db_name,
                table_name=table_name,
            )

            def columns_loaded(cols, it=item, db=db_name, table=table_name):
                self.schema_cache.set(("columns", db, table), cols)
                self.populate_columns(it, cols)

            worker.columns_loaded.connect(columns_loaded)
            self._start_worker(worker)

    def populate_tables(
        self,
        parent_item: QTreeWidgetItem,
        tables: list[str],
        db_name: str,
    ) -> None:
        self._known_words += tables
        self._completer.set_words(self._known_words)

        for table in tables:
            item = QTreeWidgetItem([f"📋 {table}"])
            item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                ("table", db_name, table),
            )
            item.addChild(QTreeWidgetItem(["Loading..."]))
            parent_item.addChild(item)

    def populate_columns(
        self,
        parent_item: QTreeWidgetItem,
        columns: list[dict],
    ) -> None:
        for col in columns:
            field = col.get("Field", "")
            self._known_words.append(field)
            parent_item.addChild(
                QTreeWidgetItem([f"🔢 {field} ({col.get('Type', '')})"])
            )
        self._completer.set_words(self._known_words)

    def show_tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)

        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            menu.addAction("🔄 Refresh All").triggered.connect(self.load_schemas)
            menu.addSeparator()
            menu.addAction("🆕 Create Database").triggered.connect(
                self.create_database_dialog
            )
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        backend = self._backend_name()

        if data[0] == "schema":
            db = data[1]
            self.current_schema = db
            self.current_table = None

            menu.addAction("📋 Copy Database Name").triggered.connect(
                lambda: QApplication.clipboard().setText(db)
            )
            menu.addSeparator()
            menu.addAction("ℹ️ View Database Info").triggered.connect(
                lambda: (
                    self.load_db_info(db),
                    self.workbench_tabs.setCurrentIndex(3),
                )
            )
            menu.addAction("📤 Export Database (SQL Dump)").triggered.connect(
                lambda: self.export_database(db)
            )
            menu.addSeparator()
            menu.addAction("🆕 Create Table in this DB").triggered.connect(
                lambda: self.create_table_dialog(db)
            )

            if backend == "mysql":
                menu.addAction("🗑 DROP Database").triggered.connect(
                    lambda: self._confirm(
                        f"DROP DATABASE {self._qident(db)};",
                        f"Drop database '{db}'?",
                    )
                )

        elif data[0] == "table":
            db = data[1]
            table = data[2]
            self.current_schema = db
            self.current_table = table
            fqn = f"{self._qident(db)}.{self._qident(table)}"

            menu.addAction("📊 Browse Table Data").triggered.connect(
                lambda: (
                    self.load_table_data(db, table),
                    self.workbench_tabs.setCurrentIndex(0),
                )
            )
            menu.addAction("📐 View Schema").triggered.connect(
                lambda: (
                    self.load_table_schema(db, table),
                    self.workbench_tabs.setCurrentIndex(1),
                )
            )
            menu.addAction("🛠️ Design / Alter Table (HeidiSQL Mode)").triggered.connect(
                self.open_table_designer
            )
            menu.addAction("ℹ️ View Table Info & DDL").triggered.connect(
                lambda: (
                    self.load_table_info(db, table),
                    self.workbench_tabs.setCurrentIndex(3),
                )
            )
            menu.addSeparator()
            menu.addAction("▶ SELECT * (100 rows)").triggered.connect(
                lambda: (
                    self.query_edit.setPlainText(f"SELECT * FROM {fqn} LIMIT 100;"),
                    self.execute_query(),
                    self.workbench_tabs.setCurrentIndex(2),
                )
            )
            menu.addAction("🔢 COUNT(*)").triggered.connect(
                lambda: (
                    self.query_edit.setPlainText(f"SELECT COUNT(*) AS total FROM {fqn};"),
                    self.execute_query(),
                    self.workbench_tabs.setCurrentIndex(2),
                )
            )
            menu.addAction("📊 EXPLAIN SELECT").triggered.connect(
                lambda: (
                    self.query_edit.setPlainText(f"EXPLAIN SELECT * FROM {fqn} LIMIT 10;"),
                    self.execute_query(),
                    self.workbench_tabs.setCurrentIndex(2),
                )
            )
            menu.addSeparator()
            menu.addAction("📤 Export Table (CSV)").triggered.connect(
                lambda: self.export_table_csv(db, table)
            )
            menu.addAction("📤 Export Table (SQL INSERT)").triggered.connect(
                lambda: self.export_table_sql(db, table)
            )
            menu.addSeparator()
            menu.addAction("📋 Open Table in Isolated Tab").triggered.connect(
                lambda: self._open_table_detail(db, table)
            )
            menu.addSeparator()
            menu.addAction("⚠ TRUNCATE Table").triggered.connect(
                lambda: self._confirm(f"TRUNCATE TABLE {fqn};", f"Truncate {table}?")
            )
            menu.addAction("🗑 DROP Table").triggered.connect(
                lambda: self._confirm(f"DROP TABLE {fqn};", f"Drop {table}?")
            )
            menu.addSeparator()
            menu.addAction("📋 Copy Table Name").triggered.connect(
                lambda: QApplication.clipboard().setText(fqn)
            )

        menu.addSeparator()
        menu.addAction("🔄 Refresh All").triggered.connect(self.load_schemas)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ============================================================
    # Query Execution & SQL Console
    # ============================================================

    def explain_query(self) -> None:
        query = self.query_edit.toPlainText().strip()
        if query and not query.upper().startswith("EXPLAIN"):
            self.query_edit.setPlainText("EXPLAIN " + query)
            self.execute_query()

    def execute_query(self) -> None:
        query = self.query_edit.toPlainText().strip()
        if not query:
            return

        self.query_history = [x for x in self.query_history if x != query]
        self.query_history.insert(0, query)
        self.query_history = self.query_history[:100]
        write_json_secure(QUERY_HISTORY_FILE, self.query_history)

        self.exec_btn.setEnabled(False)
        self.row_count_label.setText("Executing query...")
        started_at = time.perf_counter()

        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=query,
            db_name=self.current_schema or "",
        )

        worker.data_loaded.connect(
            lambda headers, rows: self.display_data(
                headers,
                rows,
                time.perf_counter() - started_at,
            )
        )
        worker.finished.connect(lambda: self.exec_btn.setEnabled(True))
        self._start_worker(worker)

    def display_data(
        self,
        headers: list[str],
        rows: list[list[Any]],
        elapsed: float = 0.0,
    ) -> None:
        self._last_headers = headers or []
        self._last_rows = rows or []

        if not headers:
            self.result_model.clear()
            self.row_count_label.setText(
                f"Query OK ({elapsed * 1000:.0f} ms) — no result set."
            )
            self.services.notifications.push(
                "ok", "Query", f"Executed in {elapsed * 1000:.0f} ms"
            )
            self._update_export_buttons_state()
            return

        self.result_model.set_result(headers, rows)
        self.results_table.resizeColumnsToContents()
        self.row_count_label.setText(
            f"{len(rows)} row(s), {len(headers)} column(s) — {elapsed * 1000:.0f} ms"
        )
        self._update_export_buttons_state()

    # ============================================================
    # Export Helpers
    # ============================================================

    def export_result(self, fmt: str) -> None:
        if not self._last_headers or not self._last_rows:
            QMessageBox.information(self, "Export", "No data. Run a SELECT first.")
            return

        if fmt == "csv":
            path, _ = QFileDialog.getSaveFileName(
                self, "Export CSV", "results.csv", "CSV Files (*.csv)"
            )
            if not path:
                return
            export_result_csv(path, self._last_headers, self._last_rows)

        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export JSON", "results.json", "JSON Files (*.json)"
            )
            if not path:
                return
            export_result_json(path, self._last_headers, self._last_rows)

        self.services.notifications.push(
            "ok", "Export", f"{len(self._last_rows)} rows → {path}"
        )

    def export_table_csv(self, db_name: str, table_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Table CSV", f"{table_name}.csv", "CSV (*.csv)"
        )
        if not path:
            return

        fqn = f"{self._qident(db_name)}.{self._qident(table_name)}"
        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=f"SELECT * FROM {fqn} LIMIT 10000;",
            db_name=db_name,
        )

        def save(headers, rows):
            try:
                export_result_csv(path, headers, rows)
                self.services.notifications.push(
                    "ok", "Export", f"{len(rows)} rows → {path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

        worker.data_loaded.connect(save)
        worker.error_occurred.connect(self.on_db_error)
        worker.start()
        self._workers.append(worker)

    def export_table_sql(self, db_name: str, table_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Table SQL", f"{table_name}_data.sql", "SQL Files (*.sql)"
        )
        if not path:
            return

        backend = self._backend_name()
        fqn = f"{self._qident(db_name)}.{self._qident(table_name)}"
        worker = DbWorker(
            self.session_manager,
            self._build_cfg(),
            mode="run_query",
            query=f"SELECT * FROM {fqn} LIMIT 10000;",
            db_name=db_name,
        )

        def save(headers, rows):
            try:
                export_table_sql(path, fqn, headers, rows, backend, self._qident)
                self.services.notifications.push(
                    "ok", "Export", f"{len(rows)} INSERT statements → {path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

        worker.data_loaded.connect(save)
        worker.error_occurred.connect(self.on_db_error)
        worker.start()
        self._workers.append(worker)

    def export_database(self, db_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Database", f"{db_name}_dump.sql", "SQL Files (*.sql)"
        )
        if not path:
            return

        try:
            export_database(self._build_cfg(), db_name, path)
            self.services.notifications.push(
                "ok", "Export", f"Database '{db_name}' exported to {path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    # ============================================================
    # Dialogs & Confirmation
    # ============================================================

    def create_database_dialog(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Create Database", "Database name:"
        )
        if not ok or not name:
            return

        backend = self._backend_name()
        quoted = self._qident(name)

        if backend == "mysql":
            sql = f"CREATE DATABASE {quoted} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        else:
            sql = f"CREATE DATABASE {quoted};"

        if QMessageBox.question(
            self, "Create Database", f"Execute:\n{sql}"
        ) == QMessageBox.StandardButton.Yes:
            self.query_edit.setPlainText(sql)
            self.execute_query()
            self.workbench_tabs.setCurrentIndex(2)

    def create_table_dialog(self, db_name: str) -> None:
        from admin_suite.db.dialogs import CreateTableDialog

        dlg = CreateTableDialog(
            self,
            db_name or self.current_schema or "",
            self._backend_name(),
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            sql = dlg.get_sql()
            if sql:
                self.query_edit.setPlainText(sql)
                self.execute_query()
                self.workbench_tabs.setCurrentIndex(2)

    def _confirm(self, sql: str, message: str) -> None:
        if QMessageBox.question(
            self,
            "Confirm destructive operation",
            message + "\nThis cannot be undone. Execute?",
        ) == QMessageBox.StandardButton.Yes:
            self.query_edit.setPlainText(sql)
            self.execute_query()
            self.workbench_tabs.setCurrentIndex(2)

    def _open_table_detail(self, db_name: str, table_name: str) -> None:
        main_window = self.main_window
        if main_window and hasattr(main_window, "tabs"):
            title = f"📋 {db_name}.{table_name}"
            for i in range(main_window.tabs.count()):
                if main_window.tabs.tabText(i) == title:
                    main_window.tabs.setCurrentIndex(i)
                    return

            detail = TableDetailTab(
                self.services,
                self.session_manager,
                self._build_cfg(),
                db_name,
                table_name,
                main_window,
            )
            idx = main_window.tabs.addTab(detail, title)
            main_window.tabs.setCurrentIndex(idx)

    # ============================================================
    # History & Favorites
    # ============================================================

    def show_history(self) -> None:
        from PyQt6.QtWidgets import QListWidget

        dlg = QDialog(self)
        dlg.setWindowTitle("Query History")
        dlg.resize(640, 420)
        layout = QVBoxLayout(dlg)

        lw = QListWidget()
        lw.setFont(QFont("JetBrains Mono, Consolas", 10))

        for query in self.query_history:
            lw.addItem(query)

        layout.addWidget(lw)

        row = QHBoxLayout()
        use = QPushButton("Use Query")
        use.clicked.connect(
            lambda: (
                self.query_edit.setPlainText(lw.currentItem().text())
                if lw.currentItem() else None,
                dlg.accept(),
            )
        )

        clear = QPushButton("Clear History")
        clear.clicked.connect(
            lambda: (
                self.query_history.clear(),
                lw.clear(),
                write_json_secure(QUERY_HISTORY_FILE, []),
            )
        )

        close = QPushButton("Close")
        close.clicked.connect(dlg.reject)

        row.addWidget(use)
        row.addWidget(clear)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)

        lw.itemDoubleClicked.connect(
            lambda item: (
                self.query_edit.setPlainText(item.text()),
                dlg.accept(),
            )
        )
        dlg.exec()

    def save_favorite(self) -> None:
        query = self.query_edit.toPlainText().strip()
        if not query:
            return

        name, ok = QInputDialog.getText(self, "Favorite", "Name:")
        if ok and name:
            self.query_favs.insert(0, {"name": name, "sql": query})
            write_json_secure(QUERY_FAVORITES_FILE, self.query_favs[:100])
            self.services.notifications.push("ok", "Favorite saved", name)

    def show_favorites(self) -> None:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem

        dlg = QDialog(self)
        dlg.setWindowTitle("Query Favorites")
        dlg.resize(640, 420)
        layout = QVBoxLayout(dlg)

        lw = QListWidget()
        for fav in self.query_favs:
            item = QListWidgetItem(f"⭐ {fav['name']}")
            item.setData(Qt.ItemDataRole.UserRole, fav)
            lw.addItem(item)

        layout.addWidget(lw)

        row = QHBoxLayout()
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(
            lambda: (
                self.query_edit.setPlainText(
                    lw.currentItem().data(Qt.ItemDataRole.UserRole)["sql"]
                )
                if lw.currentItem() else None,
                dlg.accept(),
            )
        )

        delete_btn = QPushButton("Delete")
        def delete_favorite():
            item = lw.currentItem()
            if item:
                fav = item.data(Qt.ItemDataRole.UserRole)
                if fav in self.query_favs:
                    self.query_favs.remove(fav)
                write_json_secure(QUERY_FAVORITES_FILE, self.query_favs)
                lw.takeItem(lw.row(item))

        delete_btn.clicked.connect(delete_favorite)

        close = QPushButton("Close")
        close.clicked.connect(dlg.reject)

        row.addWidget(load_btn)
        row.addWidget(delete_btn)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)

        lw.itemDoubleClicked.connect(
            lambda item: (
                self.query_edit.setPlainText(
                    item.data(Qt.ItemDataRole.UserRole)["sql"]
                ),
                dlg.accept(),
            )
        )
        dlg.exec()

    # ============================================================
    # Errors
    # ============================================================

    def on_db_error(self, err: str) -> None:
        self.row_count_label.setText(f"Error: {err}")
        self.conn_status.setText("● Error ❌")
        self.services.notifications.push("error", "Database", str(err)[:200])
        QMessageBox.critical(self, "Database Error", str(err))
