"""
MySQL server status and information tab.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from admin_suite.core.fonts import mono_font
from admin_suite.core.icons import get_icon, style_icon_button
from admin_suite.db.worker import DbWorker


class MySQLStatusTab(QWidget):
    """
    MySQL status dashboard.
    """

    def __init__(self, services, db_manager, parent=None):
        super().__init__(parent)

        self.services = services
        self.db_manager = db_manager
        self.session_manager = db_manager.session_manager

        self._workers = []
        self._active_workers = 0

        self._status_map = {}
        self._var_map = {}

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        bar = QHBoxLayout()

        title = QLabel("MySQL Server Status & Information")
        title.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;font-size:15px;padding:4px;"
        )

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{theme['sub']};")

        self.refresh_btn = QPushButton()
        style_icon_button(self.refresh_btn, "refresh", "Refresh MySQL status metrics", icon_only=True)
        self.refresh_btn.clicked.connect(self.refresh)

        bar.addWidget(title)
        bar.addStretch()
        bar.addWidget(self.status_label)
        bar.addWidget(self.refresh_btn)

        layout.addLayout(bar)

        self.tabs = QTabWidget()

        # Overview.
        self.overview_tree = QTreeWidget()
        self.overview_tree.setColumnCount(2)
        self.overview_tree.setHeaderLabels(["Item", "Value"])

        self.overview_tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        self.overview_tree.header().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )

        self.tabs.addTab(self.overview_tree, get_icon("info", size=16), "Overview")

        # Global status.
        self.status_table = QTableView()
        self.status_table.setAlternatingRowColors(True)

        self.status_model = QStandardItemModel()
        self.status_table.setModel(self.status_model)

        self.tabs.addTab(self.status_table, get_icon("activity", size=16), "Global Status")

        # Processlist.
        self.process_table = QTableView()
        self.process_table.setAlternatingRowColors(True)

        self.process_model = QStandardItemModel()
        self.process_table.setModel(self.process_model)

        self.tabs.addTab(self.process_table, get_icon("list", size=16), "Processlist")

        # Variables.
        self.variables_table = QTableView()
        self.variables_table.setAlternatingRowColors(True)

        self.variables_model = QStandardItemModel()
        self.variables_table.setModel(self.variables_model)

        self.tabs.addTab(self.variables_table, get_icon("settings", size=16), "Variables")

        # InnoDB.
        self.innodb_text = QPlainTextEdit()
        self.innodb_text.setReadOnly(True)
        self.innodb_text.setFont(mono_font(10))

        self.tabs.addTab(self.innodb_text, get_icon("database", size=16), "InnoDB")

        # Replication.
        self.repl_text = QPlainTextEdit()
        self.repl_text.setReadOnly(True)
        self.repl_text.setFont(mono_font(10))

        self.tabs.addTab(self.repl_text, get_icon("sync", size=16), "Replication")

        layout.addWidget(self.tabs, 1)

        self.refresh()

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _cfg(self):
        return self.db_manager._build_cfg()

    @staticmethod
    def _safe_int(value, default=0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def _safe_float(value, default=0.0) -> float:
        try:
            return float(str(value).strip())
        except Exception:
            return default

    @classmethod
    def _human_bytes(cls, value) -> str:
        n = cls._safe_float(value)

        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(n) < 1024:
                return f"{n:.1f} {unit}"

            n /= 1024

        return f"{n:.1f} PB"

    @classmethod
    def _human_duration(cls, seconds) -> str:
        seconds = cls._safe_int(seconds)

        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []

        if days:
            parts.append(f"{days}d")

        if hours or days:
            parts.append(f"{hours}h")

        if minutes or hours or days:
            parts.append(f"{minutes}m")

        parts.append(f"{secs}s")

        return " ".join(parts)

    # ------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------

    def refresh(self) -> None:
        cfg = self._cfg()

        if cfg.get("backend", "") != "mysql":
            self.status_label.setText("MySQL backend not active")
            return

        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Refreshing...")

        self._workers = []
        self._active_workers = 5

        self._status_map = {}
        self._var_map = {}

        self.overview_tree.clear()

        self.status_model.clear()
        self.process_model.clear()
        self.variables_model.clear()

        self.innodb_text.clear()
        self.repl_text.clear()

        self._start_worker("SHOW GLOBAL STATUS", self._status_loaded)
        self._start_worker("SHOW GLOBAL VARIABLES", self._variables_loaded)
        self._start_worker(
            "SHOW FULL PROCESSLIST",
            self._process_loaded,
            self._process_error,
        )
        self._start_worker(
            "SHOW ENGINE INNODB STATUS",
            self._innodb_loaded,
            self._innodb_error,
        )
        self._start_worker(
            "SHOW SLAVE STATUS",
            self._replication_loaded,
            self._replication_error,
        )

    def _start_worker(self, query, success_cb, error_cb=None) -> None:
        worker = DbWorker(
            self.session_manager,
            self._cfg(),
            mode="run_query",
            query=query,
        )

        worker.data_loaded.connect(success_cb)

        if error_cb:
            worker.error_occurred.connect(error_cb)
        else:
            worker.error_occurred.connect(
                lambda e, q=query: self._generic_error(q, e)
            )

        worker.finished.connect(lambda w=worker: self._worker_finished(w))

        worker.start()

        self._workers.append(worker)

    def _worker_finished(self, worker) -> None:
        try:
            self._workers.remove(worker)
        except Exception:
            pass

        self._active_workers -= 1

        if self._active_workers <= 0:
            self.refresh_btn.setEnabled(True)
            self.status_label.setText("Ready")

    def _generic_error(self, query, err) -> None:
        self.status_label.setText("Error")

        self.services.notifications.push(
            "error",
            "MySQL Status",
            str(err)[:200],
        )

        self.services.emit_log(
            "database",
            f"MySQL status query failed: {query}: {err}",
        )

    # ------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------

    def _fill_table(self, model, headers, rows) -> None:
        model.clear()

        if headers:
            model.setHorizontalHeaderLabels([str(h) for h in headers])

        for row in rows or []:
            items = []

            for value in row:
                items.append(
                    QStandardItem("" if value is None else str(value))
                )

            model.appendRow(items)

    def _status_loaded(self, headers, rows) -> None:
        self._fill_table(self.status_model, headers, rows)

        self._status_map = {}

        for row in rows:
            if len(row) >= 2:
                self._status_map[str(row[0])] = row[1]

        self._maybe_fill_overview()

    def _variables_loaded(self, headers, rows) -> None:
        self._fill_table(self.variables_model, headers, rows)

        self._var_map = {}

        for row in rows:
            if len(row) >= 2:
                self._var_map[str(row[0])] = row[1]

        self._maybe_fill_overview()

    def _process_loaded(self, headers, rows) -> None:
        self._fill_table(self.process_model, headers, rows)
        self.process_table.resizeColumnsToContents()

    def _process_error(self, err) -> None:
        self.process_model.clear()
        self.process_model.setHorizontalHeaderLabels(["Error"])
        self.process_model.appendRow([QStandardItem(str(err))])

    def _innodb_loaded(self, headers, rows) -> None:
        if rows and len(rows[0]) >= 3:
            self.innodb_text.setPlainText(str(rows[0][2]))
        elif rows:
            self.innodb_text.setPlainText(str(rows[0][-1]))
        else:
            self.innodb_text.setPlainText("No InnoDB status returned.")

    def _innodb_error(self, err) -> None:
        self.innodb_text.setPlainText(
            f"InnoDB status unavailable:\n{err}"
        )

    def _replication_loaded(self, headers, rows) -> None:
        if not rows:
            self.repl_text.setPlainText("No replication status returned.")
            return

        row = rows[0]
        out = []

        for i, header in enumerate(headers):
            value = row[i] if i < len(row) else ""
            out.append(f"{header}: {value}")

        self.repl_text.setPlainText("\n".join(out))

    def _replication_error(self, err) -> None:
        self.repl_text.setPlainText(
            "Replication status unavailable.\n"
            "This is normal if this server is not configured as a replica/slave.\n"
            f"Details: {err}"
        )

    # ------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------

    def _maybe_fill_overview(self) -> None:
        if not self._status_map or not self._var_map:
            return

        self.overview_tree.clear()

        uptime = self._safe_int(self._status_map.get("Uptime", 0))
        questions = self._safe_int(self._status_map.get("Questions", 0))

        qps = questions / uptime if uptime else 0

        innodb_read_requests = self._safe_int(
            self._status_map.get("Innodb_buffer_pool_read_requests", 0)
        )

        innodb_reads = self._safe_int(
            self._status_map.get("Innodb_buffer_pool_reads", 0)
        )

        hit_ratio = "-"

        if innodb_read_requests > 0:
            hit_ratio = (
                f"{((1 - (innodb_reads / innodb_read_requests)) * 100):.2f}%"
            )

        self._add_overview_section(
            "Server",
            [
                ("Version", self._var_map.get("version", "-")),
                ("Version Comment", self._var_map.get("version_comment", "-")),
                ("Hostname", self._var_map.get("hostname", "-")),
                ("Port", self._var_map.get("port", "-")),
                ("Data Directory", self._var_map.get("datadir", "-")),
                ("Socket", self._var_map.get("socket", "-")),
                ("Uptime", self._human_duration(uptime)),
                ("Max Connections", self._var_map.get("max_connections", "-")),
            ],
        )

        self._add_overview_section(
            "Connections / Threads",
            [
                ("Threads Connected", self._status_map.get("Threads_connected", "-")),
                ("Threads Running", self._status_map.get("Threads_running", "-")),
                ("Threads Created", self._status_map.get("Threads_created", "-")),
                ("Total Connections", self._status_map.get("Connections", "-")),
                ("Aborted Clients", self._status_map.get("Aborted_clients", "-")),
                ("Aborted Connects", self._status_map.get("Aborted_connects", "-")),
                ("Thread Cache Size", self._var_map.get("thread_cache_size", "-")),
            ],
        )

        self._add_overview_section(
            "Queries",
            [
                ("Questions", questions),
                ("Avg QPS", f"{qps:.2f}"),
                ("Slow Queries", self._status_map.get("Slow_queries", "-")),
                ("Com_select", self._status_map.get("Com_select", "-")),
                ("Com_insert", self._status_map.get("Com_insert", "-")),
                ("Com_update", self._status_map.get("Com_update", "-")),
                ("Com_delete", self._status_map.get("Com_delete", "-")),
                ("Com_commit", self._status_map.get("Com_commit", "-")),
                ("Com_rollback", self._status_map.get("Com_rollback", "-")),
            ],
        )

        self._add_overview_section(
            "Traffic",
            [
                (
                    "Bytes Received",
                    self._human_bytes(self._status_map.get("Bytes_received", 0)),
                ),
                (
                    "Bytes Sent",
                    self._human_bytes(self._status_map.get("Bytes_sent", 0)),
                ),
            ],
        )

        self._add_overview_section(
            "InnoDB",
            [
                ("Buffer Pool Read Requests", innodb_read_requests),
                ("Buffer Pool Reads", innodb_reads),
                ("Buffer Pool Hit Ratio", hit_ratio),
                (
                    "Buffer Pool Size",
                    self._var_map.get("innodb_buffer_pool_size", "-"),
                ),
                (
                    "Log File Size",
                    self._var_map.get("innodb_log_file_size", "-"),
                ),
                (
                    "Flush Log At Trx Commit",
                    self._var_map.get("innodb_flush_log_at_trx_commit", "-"),
                ),
            ],
        )

        self._add_overview_section(
            "Tables / Temp",
            [
                ("Open Tables", self._status_map.get("Open_tables", "-")),
                ("Opened Tables", self._status_map.get("Opened_tables", "-")),
                (
                    "Table Open Cache",
                    self._var_map.get("table_open_cache", "-"),
                ),
                (
                    "Created Temp Tables",
                    self._status_map.get("Created_tmp_tables", "-"),
                ),
                (
                    "Created Temp Disk Tables",
                    self._status_map.get("Created_tmp_disk_tables", "-"),
                ),
            ],
        )

    def _add_overview_section(self, title, pairs) -> None:
        theme = self.services.theme.current

        top = QTreeWidgetItem([title, ""])
        top.setForeground(0, QColor(theme["accent"]))

        font = top.font(0)
        font.setBold(True)
        top.setFont(0, font)

        for key, value in pairs:
            top.addChild(QTreeWidgetItem([str(key), str(value)]))

        self.overview_tree.addTopLevelItem(top)
        top.setExpanded(True)
