"""
Unit tests for SFTP remote home directory resolution and Database Manager table double-click fixes.
"""

import os
import sys
import tempfile
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from admin_suite.services import AppServices
from admin_suite.sftp.tab import SFTPTab
from admin_suite.sftp.file_browser import FileBrowserPanel
from admin_suite.sftp.worker import SftpWorker
from admin_suite.db.session import DbSessionManager
from admin_suite.db.worker import DbWorker, _CursorContext
from admin_suite.db.manager import DatabaseManagerWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    import admin_suite.core.paths as paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    srv = AppServices()
    srv.paths = paths
    return srv


@pytest.fixture(autouse=True)
def mock_remote_network(monkeypatch):
    monkeypatch.setattr("admin_suite.sftp.tab.RemoteExecThread", MagicMock())
    monkeypatch.setattr("admin_suite.sftp.file_browser.SftpWorker", MagicMock())
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: None)


# =============================================================================
# 1. SFTP Remote Home Directory Resolution Tests
# =============================================================================

def test_sftp_user_home_initialization(qapp, services):
    """Verify that SFTPTab immediately switches to user's home directory instead of getting stuck."""
    # Test non-root user
    tab = SFTPTab(services, host="192.168.1.100", port=22, user="developer")
    assert tab.remote_panel.current_path == "/home/developer"
    assert tab.remote_panel.path_input.text() == "/home/developer"
    assert "developer" in tab.status_label.text()
    assert "Resolving remote home directory…" not in tab.status_label.text()

    # Test root user
    tab_root = SFTPTab(services, host="192.168.1.100", port=22, user="root")
    assert tab_root.remote_panel.current_path == "/root"
    assert tab_root.remote_panel.path_input.text() == "/root"
    assert "/root" in tab_root.status_label.text()


def test_sftp_go_home_navigates_to_user_home(qapp, services):
    """Verify that clicking Home in remote panel goes to user's home directory, not root /."""
    panel = FileBrowserPanel(services, "remote", mode="remote")
    panel.configure_remote("10.0.0.1", 22, "alice", None)
    assert panel.current_path == "/home/alice"

    # Navigate to somewhere else
    panel.current_path = "/var/log"
    panel.go_home()
    assert panel.current_path == "/home/alice"


def test_sftp_worker_list_dir_home_normalization():
    """Verify that SftpWorker normalize path resolves '.' or '~'."""
    worker = SftpWorker("127.0.0.1", 22, "bob", None)
    mock_sftp = MagicMock()
    mock_sftp.normalize.return_value = "/home/bob"
    mock_sftp.listdir_attr.return_value = []
    worker.sftp = mock_sftp

    entries_out = []
    paths_out = []
    worker.listing_ready.connect(lambda e, p: (entries_out.append(e), paths_out.append(p)))

    worker._list_dir(".")
    assert paths_out == ["/home/bob"]
    mock_sftp.normalize.assert_called_with(".")


# =============================================================================
# 2. Database Session Concurrency & Pool Checkout Tests
# =============================================================================

def test_db_session_manager_concurrency():
    """Verify that concurrent acquires return distinct connections so threads do not collide."""
    mgr = DbSessionManager()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        cfg = {"backend": "sqlite", "sqlite_path": db_path}

        conn1 = mgr.acquire(cfg)
        conn2 = mgr.acquire(cfg)

        # In concurrent calls before release, they must be distinct connections
        assert conn1 is not conn2

        # Releasing conn1 allows it to be checked out again
        mgr.release(cfg, conn1, has_error=False)
        conn3 = mgr.acquire(cfg)
        assert conn3 is conn1

        mgr.release(cfg, conn2, has_error=False)
        mgr.release(cfg, conn3, has_error=False)
        mgr.stop_all()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_cursor_context_sqlite():
    """Verify that _CursorContext works with SQLite without TypeError."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        with _CursorContext(conn) as cur:
            cur.execute("CREATE TABLE test_table (id INT, val TEXT)")
            cur.execute("INSERT INTO test_table VALUES (1, 'hello')")
            conn.commit()

        with _CursorContext(conn) as cur:
            cur.execute("SELECT val FROM test_table WHERE id = 1")
            row = cur.fetchone()
            assert row[0] == "hello"

        conn.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


# =============================================================================
# 3. Database Manager Table Double-Click Tests
# =============================================================================

def test_db_manager_table_dbl_click_and_data_population(qapp, services, monkeypatch):
    """Verify that double clicking a table loads data, schema, and info without throwing errors."""
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: None)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
        cur.execute("INSERT INTO products VALUES (1, 'Widget', 19.99)")
        cur.execute("INSERT INTO products VALUES (2, 'Gadget', 49.99)")
        conn.commit()
        conn.close()

        db_mgr = DatabaseManagerWidget(services)
        db_mgr.active_db_profile = {
            "name": "TestSQLite",
            "backend": "sqlite",
            "sqlite_path": db_path,
        }

        # Create tree item for table
        tbl_item = QTreeWidgetItem(["📋 products"])
        tbl_item.setData(0, 0x0100, ("table", "main", "products"))

        # Trigger double-click
        db_mgr.on_tree_dbl_click(tbl_item)

        assert db_mgr.current_schema == "main"
        assert db_mgr.current_table == "products"
        assert db_mgr.workbench_tabs.currentIndex() == 0
        assert "products" in db_mgr.data_header_label.text()
        assert "products" in db_mgr.schema_header_label.text()
        assert "products" in db_mgr.info_header_label.text()

        # Wait for background workers to complete and process Qt signals
        for w in list(db_mgr._workers):
            w.wait(2000)
        qapp.processEvents()

        # Check Table Data is populated
        assert len(db_mgr._current_data_headers) == 3
        assert len(db_mgr._current_data_rows) == 2
        assert db_mgr._current_data_rows[0][1] == "Widget"

        # Check Table Schema is populated
        assert len(db_mgr._current_table_columns) == 3
        col_names = [c.get("Field") for c in db_mgr._current_table_columns]
        assert "id" in col_names and "name" in col_names

        # Check Object Info is populated
        assert db_mgr._current_info.get("table_name") == "products"
        assert db_mgr._current_info.get("rows") == 2

        db_mgr.session_manager.stop_all()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_table_info_none_rows_handling(qapp, services):
    """Verify that _on_table_info_loaded handles None rows (e.g. from MySQL Views) without crashing."""
    db_mgr = DatabaseManagerWidget(services)
    info = {
        "table_name": "v_users",
        "schema_name": "mydb",
        "engine": "VIEW",
        "row_format": "View",
        "rows": None,  # None rows from VIEW
        "data_length": None,
        "index_length": None,
        "auto_increment": None,
        "collation": "",
        "create_time": "",
        "ddl": "CREATE VIEW v_users AS SELECT * FROM users;",
    }

    # Must not raise TypeError
    db_mgr._on_table_info_loaded(info)
    assert "0" in db_mgr.info_metrics_label.text()
    assert "VIEW" in db_mgr.info_metrics_label.text()
