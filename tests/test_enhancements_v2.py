"""
Comprehensive regression & feature tests for:
1. Sequential Shift+Left / Shift+Right tab switching across all open tabs + split panes.
2. SFTP 5-item path history (local & remote) and live SshTerminalTab replacement.
3. Non-blocking background DB exports with TaskManager & NotificationHub integration.
4. All Database Manager table/query (10 formats) & database (6 formats) export formats.
5. Encrypted & zipped Settings Export / Import backup & restore.
6. MainWindow sidebar title & navbar clock removal to save screen space.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QTreeWidget,
    QWidget,
)

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["admin_suite_test", "-platform", "offscreen"])
    return app


@pytest.fixture
def isolated_services(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    import admin_suite.core.config as cfg_mod
    import admin_suite.core.paths as paths
    import admin_suite.core.settings_backup as sb_mod
    import admin_suite.ui.main_window as mw_mod

    data_dir = tmp_path / "data"
    cfg_dir = tmp_path / "config"
    log_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    cache_dir = tmp_path / "cache"
    for d in (data_dir, cfg_dir, log_dir, state_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    path_overrides = {
        "DATA_DIR": data_dir,
        "CONFIG_DIR": cfg_dir,
        "STATE_DIR": state_dir,
        "CACHE_DIR": cache_dir,
        "XTERM_DIR": cache_dir / "xterm",
        "LOG_DIR": log_dir,
        "CONFIG_FILE": cfg_dir / "config.json",
        "PROFILES_FILE": data_dir / "profiles.json",
        "DB_PROFILES_FILE": data_dir / "db_profiles.json",
        "VPN_PROFILES_FILE": data_dir / "vpn_profiles.json",
        "LAST_SESSION_FILE": data_dir / "last_session.json",
        "RECENT_FILE": data_dir / "recent.json",
        "SNIPPETS_FILE": data_dir / "snippets.json",
        "QUERY_HISTORY_FILE": data_dir / "query_history.json",
        "QUERY_FAVORITES_FILE": data_dir / "query_favorites.json",
        "ANSIBLE_HISTORY_FILE": data_dir / "ansible_history.json",
        "ANSIBLE_COMMAND_SETS_FILE": data_dir / "ansible_command_sets.json",
        "HOST_KEYS_FILE": data_dir / "known_hosts",
    }
    for attr, val in path_overrides.items():
        monkeypatch.setattr(paths, attr, val, raising=False)

    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", path_overrides["CONFIG_FILE"])
    monkeypatch.setattr(mw_mod, "PROFILES_FILE", path_overrides["PROFILES_FILE"])
    monkeypatch.setattr(mw_mod, "DB_PROFILES_FILE", path_overrides["DB_PROFILES_FILE"])
    monkeypatch.setattr(mw_mod, "LAST_SESSION_FILE", path_overrides["LAST_SESSION_FILE"])
    monkeypatch.setattr(mw_mod, "RECENT_FILE", path_overrides["RECENT_FILE"])
    monkeypatch.setattr(sb_mod, "default_paths", paths, raising=False)

    from admin_suite.services import AppServices

    svc = AppServices()
    svc.paths = paths
    svc.config = cfg_mod.ConfigStore(path_overrides["CONFIG_FILE"])

    # In-memory secret store backend to isolate from system keyring daemon.
    secret_store: dict[str, str] = {}
    monkeypatch.setattr(svc.secrets, "get", lambda k, d="": secret_store.get(k, d))
    monkeypatch.setattr(svc.secrets, "set", lambda k, v: secret_store.__setitem__(k, v))
    monkeypatch.setattr(svc.secrets, "delete", lambda k: secret_store.pop(k, None))
    svc._test_secret_dict = secret_store
    return svc


# =============================================================================
# 1. Screen Space Removal (Sidebar Header & Header Clock)
# =============================================================================


def test_main_window_removes_sidebar_header_and_clock(qapp, isolated_services):
    from admin_suite.ui.main_window import MainWindow

    win = MainWindow(isolated_services)
    sidebar_labels = [lbl.text() for lbl in win.sidebar.findChildren(QLabel)]
    assert not any("ADMIN SUITE" in text for text in sidebar_labels)
    assert not hasattr(win, "clock")
    assert not hasattr(win, "_clock_timer")
    win.close()


# =============================================================================
# 2. Sequential Shift+Left / Shift+Right Tab Switching Across All Tab Types
# =============================================================================


def test_sequential_tab_switching_across_all_open_tabs_and_split_terminal(
    qapp, isolated_services, monkeypatch
):
    monkeypatch.setattr("admin_suite.terminal.ssh_tab.SshTerminalWorker", MagicMock())

    from admin_suite.terminal.split_tab import SplitTerminalTab
    from admin_suite.ui.main_window import MainWindow

    win = MainWindow(isolated_services)
    win.show()

    # Open a SplitTerminalTab via open_split_selected pattern.
    split_tab = SplitTerminalTab(isolated_services, "ProdSplit", {"ssh_host": "127.0.0.1"})
    split_tab.tab_switch_requested.connect(win._switch_tab_by_delta)
    for pane in split_tab.terminals:
        win._connect_terminal(pane, connect_tab_switch=False)

    # DB-like tab containing QTreeWidget + QTableView (which previously trapped Shift+Arrow)
    db_tab = QWidget(win)
    tree_w = QTreeWidget(db_tab)
    table_w = QTableView(db_tab)
    non_edit_combo = QComboBox(db_tab)
    non_edit_combo.addItems(["mysql", "postgresql"])

    # SFTP-like tab containing tree & editable PathHistoryBox
    from admin_suite.sftp.file_browser import PathHistoryBox

    sftp_tab = QWidget(win)
    sftp_tree = QTreeWidget(sftp_tab)
    path_combo = PathHistoryBox(parent=sftp_tab)
    text_editor = QPlainTextEdit(sftp_tab)

    idx0 = 0  # Welcome tab already present at index 0
    win.open_db_manager()
    idx_db_mgr = 1  # Database Manager tab opened at index 1
    idx_split = win.tabs.addTab(split_tab, "ProdSplit")
    idx_db_sim = win.tabs.addTab(db_tab, "DB Sim")
    idx_sftp_sim = win.tabs.addTab(sftp_tab, "SFTP Sim")
    assert [idx0, idx_db_mgr, idx_split, idx_db_sim, idx_sftp_sim] == [0, 1, 2, 3, 4]

    win.tabs.setCurrentIndex(0)
    win.tabs.setFocus()
    QApplication.processEvents()

    def _trigger_shift_arrow(cur_widget, key: Qt.Key, delta: int) -> None:
        fw = QApplication.focusWidget() or win.tabs
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.ShiftModifier)
        if cur_widget is split_tab:
            # Terminal widgets handle Shift+Arrow themselves via tab_switch_requested
            # so eventFilter delegates without double-stepping.
            assert win.eventFilter(fw, ev) is False
            pane0 = split_tab.terminals[0]
            if getattr(pane0, "native_view", None) is not None:
                QApplication.sendEvent(pane0.native_view, ev)
            elif getattr(pane0, "bridge", None) is not None:
                pane0.bridge.request_tab_switch(delta)
            else:
                pane0.tab_switch_requested.emit(delta)
        else:
            assert win.eventFilter(fw, ev) is True

    # Step sequentially forward through every single open tab: 0 -> 1 -> 2 -> 3 -> 4 -> 0
    seen_forward = []
    for expected in (1, 2, 3, 4, 0):
        # Put focus on trapping view widgets when on those tabs!
        cur_w = win.tabs.currentWidget()
        if cur_w is db_tab:
            tree_w.setFocus()
        elif cur_w is sftp_tab:
            sftp_tree.setFocus()
        QApplication.processEvents()

        _trigger_shift_arrow(cur_w, Qt.Key.Key_Right, 1)
        seen_forward.append(win.tabs.currentIndex())
        assert win.tabs.currentIndex() == expected
    assert seen_forward == [1, 2, 3, 4, 0]

    # Step sequentially backward with Shift+Left: 0 -> 4 -> 3 -> 2 -> 1 -> 0
    seen_backward = []
    for expected in (4, 3, 2, 1, 0):
        cur_w = win.tabs.currentWidget()
        if cur_w is db_tab:
            table_w.setFocus()
        QApplication.processEvents()
        _trigger_shift_arrow(cur_w, Qt.Key.Key_Left, -1)
        seen_backward.append(win.tabs.currentIndex())
        assert win.tabs.currentIndex() == expected
    assert seen_backward == [4, 3, 2, 1, 0]

    # Non-editable QComboBox also allows Shift+Right tab switch
    win.tabs.setCurrentIndex(idx_db_sim)
    non_edit_combo.setFocus()
    QApplication.processEvents()
    ev_combo = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier
    )
    assert win.eventFilter(non_edit_combo, ev_combo) is True
    assert win.tabs.currentIndex() == idx_sftp_sim

    # SplitTerminalTab child pane emits tab_switch_requested -> steps ONLY by 1 (no +2 jump!)
    win.tabs.setCurrentIndex(idx_split)
    split_tab.terminals[0].tab_switch_requested.emit(1)
    assert win.tabs.currentIndex() == idx_db_sim

    # Even if an oversized delta is passed, _switch_tab_by_delta steps strictly by +-1
    win._switch_tab_by_delta(-5)
    assert win.tabs.currentIndex() == idx_split

    # Editable inputs (PathHistoryBox / QLineEdit / QPlainTextEdit) still keep Shift+Arrow!
    win.tabs.setCurrentIndex(idx_sftp_sim)
    path_combo.setFocus()
    QApplication.processEvents()
    ev_path = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier
    )
    assert win.eventFilter(path_combo, ev_path) is False
    assert win.tabs.currentIndex() == idx_sftp_sim

    text_editor.setFocus()
    QApplication.processEvents()
    ev_txt = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier
    )
    assert win.eventFilter(text_editor, ev_txt) is False
    assert win.tabs.currentIndex() == idx_sftp_sim

    win.close()


# =============================================================================
# 3. SFTP 5-Item Path History + Embedded Working SSH Terminal Tab
# =============================================================================


def test_sftp_five_item_path_history_local_and_remote(qapp, isolated_services, tmp_path):
    from admin_suite.sftp.file_browser import FileBrowserPanel, MAX_PATH_HISTORY

    assert MAX_PATH_HISTORY == 5

    # Local panel: create 7 real directories and visit all 7
    dirs = []
    for i in range(7):
        d = tmp_path / f"folder_{i}"
        d.mkdir()
        dirs.append(str(d))

    local_panel = FileBrowserPanel(isolated_services, "local", mode="local")
    for d in dirs:
        local_panel.navigate_to_path(d)

    # Should keep only the 5 most recently used paths, MRU first
    expected_local = [dirs[6], dirs[5], dirs[4], dirs[3], dirs[2]]
    assert local_panel.path_input.history() == expected_local
    assert [
        local_panel.path_input.itemText(i)
        for i in range(local_panel.path_input.count())
    ] == expected_local
    assert local_panel.path_input.completer().model().stringList() == expected_local

    # Revisiting an older path moves it to the top without duplicating
    local_panel.navigate_to_path(dirs[3])
    assert local_panel.path_input.history() == [
        dirs[3],
        dirs[6],
        dirs[5],
        dirs[4],
        dirs[2],
    ]

    # New local panel reloads persisted 5-item history from services.config
    local_panel_reloaded = FileBrowserPanel(isolated_services, "local", mode="local")
    assert local_panel_reloaded.path_input.history() == [
        dirs[3],
        dirs[6],
        dirs[5],
        dirs[4],
        dirs[2],
    ]

    # Remote panel: per-host 5-item history
    remote_panel = FileBrowserPanel(isolated_services, "remote", mode="remote")
    remote_panel.configure_remote("10.1.2.3", 22, "deploy", None)
    for idx in range(7):
        remote_panel._on_remote_listing([], f"/srv/app_{idx}")

    expected_remote = [
        "/srv/app_6",
        "/srv/app_5",
        "/srv/app_4",
        "/srv/app_3",
        "/srv/app_2",
    ]
    assert remote_panel.path_input.history() == expected_remote

    remote_reloaded = FileBrowserPanel(isolated_services, "remote", mode="remote")
    remote_reloaded.configure_remote("10.1.2.3", 22, "deploy", None)
    assert remote_reloaded.path_input.history() == expected_remote


def test_sftp_replaces_remote_console_with_live_ssh_tab(qapp, isolated_services, monkeypatch):
    monkeypatch.setattr("admin_suite.sftp.tab.RemoteExecThread", MagicMock())
    monkeypatch.setattr("admin_suite.sftp.file_browser.SftpWorker", MagicMock())
    monkeypatch.setattr("admin_suite.terminal.ssh_tab.SshTerminalWorker", MagicMock())

    from admin_suite.sftp.tab import SFTPTab
    from admin_suite.ssh.credentials import SshCredentials
    from admin_suite.terminal.ssh_tab import SshTerminalTab
    from admin_suite.ui.main_window import MainWindow

    win = MainWindow(isolated_services)
    tab = win.add_sftp_tab(
        "WebProd",
        "10.20.30.40",
        2222,
        "ubuntu",
        SshCredentials(password="pw"),
        use_jump=True,
        jump_host="bastion.example.com",
        jump_port=22,
        jump_user="jump",
    )

    tab_titles = [tab.bottom_tabs.tabText(i) for i in range(tab.bottom_tabs.count())]
    assert "Remote Console" not in tab_titles
    assert "SSH Terminal" in tab_titles
    assert not hasattr(tab, "console_input")
    assert isinstance(tab.ssh_terminal, SshTerminalTab)
    assert tab.ssh_terminal.host == "10.20.30.40"
    assert tab.ssh_terminal.port == 2222
    assert tab.ssh_terminal.user == "ubuntu"
    assert tab.ssh_terminal.use_jump is True
    assert tab.ssh_terminal.jump_host == "bastion.example.com"

    # Included in get_all_terminals so terminal themes & broadcast apply to SFTP SSH too
    assert tab.ssh_terminal in win.get_all_terminals()

    win.close()


# =============================================================================
# 4. Non-Blocking Database Exports & Multi-Format Export Suite
# =============================================================================


def _create_sample_sqlite(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, active INTEGER)"
    )
    cur.execute(
        "INSERT INTO users VALUES (1, 'Alice', 'alice@example.com', 1)"
    )
    cur.execute(
        "INSERT INTO users VALUES (2, 'Bob \"B\"', NULL, 0)"
    )
    cur.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total REAL)"
    )
    cur.execute("INSERT INTO orders VALUES (10, 1, 99.5)")
    conn.commit()
    conn.close()


def test_all_table_and_database_export_formats(tmp_path):
    from admin_suite.db.export import (
        DATABASE_EXPORT_FORMATS,
        TABLE_EXPORT_FORMATS,
        export_database_as,
        export_dataset,
    )

    db_path = tmp_path / "sample.db"
    _create_sample_sqlite(db_path)

    headers = ["id", "name", "email", "active"]
    rows = [
        [1, "Alice", "alice@example.com", 1],
        [2, 'Bob "B"', None, 0],
    ]

    # All 10 Table / Query Result formats
    assert len(TABLE_EXPORT_FORMATS) == 10
    for fmt, _label, _flt in TABLE_EXPORT_FORMATS:
        out = tmp_path / f"table_export_{fmt}.{fmt}"
        export_dataset(str(out), fmt, headers, rows, table_name="users", backend="sqlite")
        assert out.exists() and out.stat().st_size > 0

    wb_tbl = openpyxl.load_workbook(str(tmp_path / "table_export_xlsx.xlsx"))
    assert "users" in wb_tbl.sheetnames
    assert wb_tbl["users"]["B2"].value == "Alice"

    sqlite_tbl = sqlite3.connect(str(tmp_path / "table_export_sqlite.sqlite"))
    assert sqlite_tbl.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    sqlite_tbl.close()

    xml_text = (tmp_path / "table_export_xml.xml").read_text(encoding="utf-8")
    assert '<dataset table="users"' in xml_text
    assert '<column name="email" null="true"' in xml_text
    assert "<table>" in (tmp_path / "table_export_html.html").read_text(encoding="utf-8")
    assert "| id | name |" in (tmp_path / "table_export_markdown.markdown").read_text(encoding="utf-8")
    assert len((tmp_path / "table_export_jsonl.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    # All 6 Full Database backup formats
    assert len(DATABASE_EXPORT_FORMATS) == 6
    cfg = {"backend": "sqlite", "sqlite_path": str(db_path)}
    for fmt, _label, suffix, _flt in DATABASE_EXPORT_FORMATS:
        out = tmp_path / f"db_backup{suffix}"
        summary = export_database_as(cfg, "main", str(out), fmt=fmt)
        assert out.exists() and out.stat().st_size > 0
        assert summary

    with gzip.open(str(tmp_path / "db_backup_dump.sql.gz"), "rt", encoding="utf-8") as f:
        sql_text = f.read()
    assert "CREATE TABLE users" in sql_text and "CREATE TABLE orders" in sql_text

    with zipfile.ZipFile(str(tmp_path / "db_backup_csv.zip"), "r") as zf:
        assert "users.csv" in zf.namelist()
        assert "orders.csv" in zf.namelist()

    with zipfile.ZipFile(str(tmp_path / "db_backup_json.zip"), "r") as zf:
        users_json = json.loads(zf.read("users.json").decode("utf-8"))
        assert users_json[0]["name"] == "Alice"

    wb_db = openpyxl.load_workbook(str(tmp_path / "db_backup.xlsx"))
    assert "users" in wb_db.sheetnames and "orders" in wb_db.sheetnames

    snap_conn = sqlite3.connect(str(tmp_path / "db_backup.db"))
    assert snap_conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    snap_conn.close()


def test_db_manager_async_background_export_and_notifications(
    qapp, isolated_services, tmp_path, monkeypatch
):
    from admin_suite.db.manager import DatabaseManagerWidget

    db_path = tmp_path / "async_sample.db"
    _create_sample_sqlite(db_path)

    mgr = DatabaseManagerWidget(isolated_services)
    mgr.active_db_profile = {
        "name": "AsyncSQLite",
        "backend": "sqlite",
        "sqlite_path": str(db_path),
    }

    notifications: list[tuple[str, str, str]] = []
    isolated_services.notifications.pushed.connect(
        lambda item: notifications.append(
            (item.get("level", ""), item.get("title", ""), item.get("msg", ""))
        )
    )

    finished_tasks = []
    isolated_services.tasks.task_finished.connect(finished_tasks.append)

    out_db_zip = tmp_path / "async_db_json.zip"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(out_db_zip), "ZIP Archive (*.zip)"),
    )
    worker = mgr.export_database("main", fmt="json_zip")
    assert worker is not None
    assert worker.wait(5000)
    qapp.processEvents()

    assert out_db_zip.exists()
    assert any(t.status == "completed" and "Database" in t.category for t in finished_tasks)
    assert any(lvl == "ok" and "Database Export" in title for lvl, title, _ in notifications)

    # Async full-table export (xlsx)
    out_tbl_xlsx = tmp_path / "async_users.xlsx"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(out_tbl_xlsx), "Excel (*.xlsx)"),
    )
    tbl_worker = mgr.export_table("main", "users", fmt="xlsx")
    assert tbl_worker is not None
    assert tbl_worker.wait(5000)
    qapp.processEvents()
    assert out_tbl_xlsx.exists()

    mgr.stop_all_workers()


# =============================================================================
# 5. Encrypted & Zipped Settings Export / Import
# =============================================================================


def test_encrypted_settings_backup_export_and_import_roundtrip(
    qapp, isolated_services, tmp_path
):
    from admin_suite.core.settings_backup import (
        export_settings_archive,
        import_settings_archive,
        inspect_settings_archive,
    )
    from admin_suite.core.utils import write_json_secure
    from admin_suite.ui.dialogs import SettingsBackupWorker

    paths = isolated_services.paths

    # Populate settings, profiles, extras, and secrets
    isolated_services.config.set("ui_theme", "Nord")
    isolated_services.config.set("terminal_theme", "nord")
    isolated_services.config.set("terminal_font_size", 15)
    isolated_services.config.save()

    write_json_secure(
        paths.PROFILES_FILE,
        {
            "prod-web": {
                "ssh_host": "web.prod.local",
                "ssh_port": 22,
                "ssh_user": "deploy",
                "auth_method": "Password",
            }
        },
    )
    write_json_secure(
        paths.DB_PROFILES_FILE,
        {
            "analytics": {
                "backend": "postgresql",
                "db_host": "db.prod.local",
                "db_port": "5432",
                "db_user": "analyst",
                "db_name": "metrics",
            }
        },
    )
    write_json_secure(paths.VPN_PROFILES_FILE, {"corp": {"vpn_host": "vpn.corp.local"}})
    write_json_secure(paths.SNIPPETS_FILE, [{"name": "disk", "command": "df -h"}])
    write_json_secure(paths.QUERY_HISTORY_FILE, [{"sql": "SELECT 1;", "db": "metrics"}])
    paths.HOST_KEYS_FILE.write_text("web.prod.local ssh-ed25519 AAAA...\n", encoding="utf-8")

    isolated_services.secrets.set("prof_prod-web", "SshPass123!")
    isolated_services.secrets.set("dbprof_analytics", "DbPass456!")
    isolated_services.secrets.set("ssh_pass", "GlobalSshPass!")

    archive_path = tmp_path / "backup.zip"
    manifest = export_settings_archive(
        archive_path,
        "MasterKey#2026",
        isolated_services,
        iterations=10_000,  # fast KDF iterations in test while still real PBKDF2+Fernet
    )
    assert archive_path.exists()
    assert manifest["counts"]["ssh_profiles"] == 1
    assert manifest["counts"]["db_profiles"] == 1

    # Verify archive contains manifest + encrypted payload (not plaintext secrets/profiles)
    with zipfile.ZipFile(archive_path, "r") as zf:
        assert set(zf.namelist()) == {"manifest.json", "payload.enc"}
        raw_enc = zf.read("payload.enc")
        assert b"SshPass123!" not in raw_enc
        assert b"web.prod.local" not in raw_enc

    inspected = inspect_settings_archive(archive_path)
    assert inspected["format"] == "admin_suite_settings_v1"

    # Wrong password must fail cleanly
    with pytest.raises(ValueError, match="Incorrect password"):
        import_settings_archive(archive_path, "WrongPassword!", isolated_services)

    # Wipe current data/secrets, then restore with correct password
    isolated_services.config.set("ui_theme", "Breeze Dark")
    isolated_services.config.save()
    write_json_secure(paths.PROFILES_FILE, {"temp-other": {"ssh_host": "10.0.0.99"}})
    write_json_secure(paths.DB_PROFILES_FILE, {})
    isolated_services.secrets.delete("prof_prod-web")
    isolated_services.secrets.delete("dbprof_analytics")
    paths.HOST_KEYS_FILE.write_text("", encoding="utf-8")

    # Merge import preserves existing non-conflicting profile + adds restored profile
    res_merge = import_settings_archive(
        archive_path, "MasterKey#2026", isolated_services, merge=True
    )
    assert "ssh_profiles" in res_merge["sections"]
    assert isolated_services.config.get("ui_theme") == "Nord"
    assert isolated_services.config.get("terminal_font_size") == 15
    ssh_profs = json.loads(paths.PROFILES_FILE.read_text(encoding="utf-8"))
    assert "prod-web" in ssh_profs and "temp-other" in ssh_profs
    assert isolated_services.secrets.get("prof_prod-web") == "SshPass123!"
    assert isolated_services.secrets.get("dbprof_analytics") == "DbPass456!"
    assert "web.prod.local" in paths.HOST_KEYS_FILE.read_text(encoding="utf-8")

    # Replace import drops temp-other
    import_settings_archive(archive_path, "MasterKey#2026", isolated_services, merge=False)
    ssh_replaced = json.loads(paths.PROFILES_FILE.read_text(encoding="utf-8"))
    assert list(ssh_replaced.keys()) == ["prod-web"]

    # Background worker check
    worker_out = tmp_path / "worker_backup.zip"
    w = SettingsBackupWorker(
        "export", str(worker_out), "WorkerPass!", isolated_services,
        {"include_config": True, "include_profiles": True, "include_extras": False, "include_secrets": False},
    )
    completed_results = []
    w.completed.connect(completed_results.append)
    w.start()
    assert w.wait(8000)
    qapp.processEvents()
    assert len(completed_results) == 1
    assert worker_out.exists()


def test_split_terminal_pane_context_menu_split_right_and_down(
    qapp, isolated_services, monkeypatch
):
    """Regression test: per-pane Split Right / Split Down must not raise TypeError."""
    monkeypatch.setattr("admin_suite.terminal.ssh_tab.SshTerminalWorker", MagicMock())

    from PyQt6.QtWidgets import QMenu
    from admin_suite.ui.main_window import MainWindow

    win = MainWindow(isolated_services)
    win.profiles["ProdSplit"] = {"ssh_host": "10.0.0.10", "ssh_user": "root"}

    tab = win.open_split_profile("ProdSplit")
    assert len(tab.terminals) == 1

    # Trigger "Split Right" and "Split Down" from the first pane's extended context menu.
    first_pane = tab.terminals[0]
    menu = QMenu()
    tab._extend_pane_context_menu(menu, first_pane)
    actions = {a.text(): a for a in menu.actions() if a.text()}

    assert "Split Right" in actions
    assert "Split Down" in actions

    actions["Split Right"].trigger()
    qapp.processEvents()
    assert len(tab.terminals) == 2

    actions["Split Down"].trigger()
    qapp.processEvents()
    assert len(tab.terminals) == 3

    # Verify newly added panes are connected to MainWindow without double tab switching.
    initial_idx = win.tabs.currentIndex()
    win.open_db_manager()
    win.tabs.setCurrentIndex(initial_idx)
    tab.terminals[1].tab_switch_requested.emit(1)
    qapp.processEvents()
    assert win.tabs.currentIndex() == initial_idx + 1

    win.close()

