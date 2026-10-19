"""
Unit tests for Phase 2 enhancements:
- Global Background Task Manager & Cancellation
- Task Center Status Bar & Drawer Widget
- Docking Workspace & Detachable Floating Windows
- Host Console Tab Reuse (Server Lens Consolidation)
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QTabWidget, QWidget

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from admin_suite.services import AppServices
from admin_suite.core.task_manager import BackgroundTask, TaskManager
from admin_suite.ui.task_drawer import TaskItemWidget, TaskStatusBarWidget
from admin_suite.ui.workspace import DetachableTabWindow, WorkspaceManager


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


# =============================================================================
# 1. Background Task Manager Tests
# =============================================================================

def test_task_manager_lifecycle(qapp):
    mgr = TaskManager()
    cancelled = []

    # Start task
    task = mgr.start_task(
        title="Syncing Remote Logs",
        description="Syncing /var/log over rsync",
        category="SFTP",
        cancel_cb=lambda: cancelled.append(True),
        progress=10,
    )
    assert task.is_active
    assert task.progress == 10
    assert len(mgr.get_active_tasks()) == 1

    # Update task
    mgr.update_task(task.id, progress=50, description="Halfway done")
    assert task.progress == 50
    assert task.description == "Halfway done"

    # Finish task
    mgr.finish_task(task.id)
    assert not task.is_active
    assert task.status == "completed"
    assert task.progress == 100
    assert len(mgr.get_active_tasks()) == 0

    # Test error finishing
    fail_task = mgr.start_task("VAPT Scan")
    mgr.finish_task(fail_task.id, error="Connection timeout")
    assert fail_task.status == "failed"
    assert fail_task.error == "Connection timeout"

    # Test cancellation
    cancel_task = mgr.start_task(
        "Long DB Query",
        cancel_cb=lambda: cancelled.append("query_cancelled"),
    )
    mgr.cancel_task(cancel_task.id)
    assert cancel_task.status == "cancelled"
    assert "query_cancelled" in cancelled

    # Clear finished
    mgr.clear_finished()
    assert len(mgr.get_all_tasks()) == 0


def test_task_status_bar_widget(qapp, services):
    widget = TaskStatusBarWidget(services)
    assert "Idle" in widget.btn.text()

    # Start a task
    t = services.tasks.start_task("Downloading Database Dump", progress=25)
    assert "Downloading Database Dump (25%)" in widget.btn.text()

    # Update task
    services.tasks.update_task(t.id, progress=80)
    assert "80%" in widget.btn.text()

    # Complete task
    services.tasks.finish_task(t.id)
    assert "Idle" in widget.btn.text()


# =============================================================================
# 2. Docking Workspace & Detachable Windows Tests
# =============================================================================

def test_workspace_tab_detachment_and_redock(qapp, services):
    tabs = QTabWidget()
    tab1 = QLabel("Terminal Content")
    tab2 = QLabel("SFTP Content")
    tabs.addTab(tab1, "🐚 Terminal")
    tabs.addTab(tab2, "📁 SFTP")

    mock_main = MagicMock()
    mock_main.services = services
    mock_main._is_permanent_tab = lambda w: False

    mgr = WorkspaceManager(mock_main)

    # Detach tab 0
    detached_win = mgr.detach_tab(tabs, 0)
    assert detached_win is not None
    assert tabs.count() == 1
    assert tabs.tabText(0) == "📁 SFTP"
    assert len(mgr.detached_windows) == 1

    # Re-dock
    detached_win.request_redock()
    assert tabs.count() == 2
    assert "Terminal" in tabs.tabText(0) or "Terminal" in tabs.tabText(1)
    assert len(mgr.detached_windows) == 0


def test_workspace_close_all_detached(qapp, services):
    tabs = QTabWidget()
    tab1 = QLabel("Content 1")
    tabs.addTab(tab1, "Tab 1")

    mock_main = MagicMock()
    mock_main.services = services
    mock_main._is_permanent_tab = lambda w: False

    mgr = WorkspaceManager(mock_main)
    detached_win = mgr.detach_tab(tabs, 0)
    assert len(mgr.detached_windows) == 1

    mgr.close_all_detached()
    assert len(mgr.detached_windows) == 0


def test_admin_console_tab_reuse(qapp, services):
    from admin_suite.ui.main_window import MainWindow
    win = MainWindow(services)
    initial_tabs = win.tabs.count()

    t1 = win.open_admin_console("web01", initial_category="web", auto_refresh=False)
    assert win.tabs.count() == initial_tabs + 1

    # Opening again for same host switches focus without adding new tab
    t2 = win.open_admin_console("web01", initial_category="security", auto_refresh=False)
    assert win.tabs.count() == initial_tabs + 1
    assert t1 is t2

    win.close()

