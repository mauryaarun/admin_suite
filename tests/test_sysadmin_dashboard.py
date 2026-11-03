"""
Unit tests for SysAdmin Dashboard enhanced GUI, presenter widgets,
and formatted data representation for Users, Services, and Processes.
"""

import sys
from pathlib import Path
import pytest

# Ensure package parent is in sys.path
pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from admin_suite.services import AppServices
from admin_suite.sysadmin.presenter import (
    SysAdminUsersWidget,
    SysAdminServicesWidget,
    SysAdminProcessesWidget,
    UserDetailDialog,
    ServiceLogsDialog,
    ProcessDetailDialog,
    ProcessReniceDialog,
)
from admin_suite.sysadmin.commands import (
    service_action_cmd,
    service_logs_cmd,
    process_kill_cmd,
    process_renice_cmd,
    process_inspect_cmd,
)
from admin_suite.admin_console.console import AdminConsoleTab
from admin_suite.sysadmin.dashboard import SysAdminTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_theme():
    return {
        "bg": "#1e1e1e",
        "fg": "#ffffff",
        "accent": "#3daee9",
        "panel": "#252526",
        "panel2": "#2d2d30",
        "border": "#3f3f46",
        "ok": "#0dbc79",
        "danger": "#ff5555",
        "warn": "#e67e22",
        "sub": "#888888",
    }


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


# ------------------------------------------------------------
# 1. SysAdmin Users Widget Tests
# ------------------------------------------------------------
SAMPLE_USERS_RAW = """=== PASSWD ===
root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
bin:x:2:2:bin:/bin:/usr/sbin/nologin
arun:x:1000:1000:Arun Maurya,,,:/home/arun:/bin/zsh
bob:x:1001:1001:Bob Smith:/home/bob:/bin/bash
systemd-resolve:x:991:991:systemd Resolver:/:/usr/sbin/nologin
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
=== LOGGED_IN ===
arun pts/1 2026-10-31 12:00 (:0)
=== PRIVILEGED ===
sudo:x:27:arun
wheel:x:10:root
"""

def test_users_widget_parsing_and_kpis(qapp, test_theme):
    widget = SysAdminUsersWidget(theme=test_theme)
    widget.update_data(SAMPLE_USERS_RAW)

    # Verify parsed accounts
    assert len(widget._all_users) == 7
    assert widget.table.rowCount() == 7

    # Check KPI cards
    assert widget.card_total.findChild(type(widget.status_combo), "val_lbl") is None  # KPI uses QLabel
    total_val = widget.card_total.findChild(type(widget.card_total.layout().itemAt(1).widget()), "val_lbl").text()
    assert total_val == "7"

    human_val = widget.card_human.findChild(type(widget.card_human.layout().itemAt(1).widget()), "val_lbl").text()
    assert human_val == "2"  # arun (1000) and bob (1001)

    priv_val = widget.card_priv.findChild(type(widget.card_priv.layout().itemAt(1).widget()), "val_lbl").text()
    assert priv_val == "2"  # root (UID 0) and arun (in sudo group)

    online_val = widget.card_online.findChild(type(widget.card_online.layout().itemAt(1).widget()), "val_lbl").text()
    assert online_val == "1"  # arun is logged in


def test_users_widget_filtering_and_sorting(qapp, test_theme):
    widget = SysAdminUsersWidget(theme=test_theme)
    widget.update_data(SAMPLE_USERS_RAW)

    # 1. Filter by text search "arun"
    widget.search_in.setText("arun")
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "arun"
    widget.search_in.clear()

    # 2. Filter by Type "human"
    idx = widget.type_combo.findData("human")
    widget.type_combo.setCurrentIndex(idx)
    assert widget.table.rowCount() == 2  # arun, bob
    widget.type_combo.setCurrentIndex(0)

    # 3. Filter by Status "online"
    idx = widget.status_combo.findData("online")
    widget.status_combo.setCurrentIndex(idx)
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "arun"
    widget.status_combo.setCurrentIndex(0)

    # 4. Numeric sorting verification: UID column (col 1)
    # Root is 0, arun is 1000, bob is 1001, nobody is 65534
    widget.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    first_uid = int(widget.table.item(0, 1).text())
    last_uid = int(widget.table.item(widget.table.rowCount() - 1, 1).text())
    assert first_uid == 0
    assert last_uid == 65534


def test_user_detail_dialog(qapp, test_theme):
    record = {
        "username": "arun",
        "uid": 1000,
        "gid": 1000,
        "gecos": "Arun Maurya,,,",
        "acc_type": "Human",
        "priv": "Yes (sudo)",
        "status": "● Online",
        "home": "/home/arun",
        "shell": "/bin/zsh",
        "raw_line": "arun:x:1000:1000:Arun Maurya,,,:/home/arun:/bin/zsh",
    }
    dlg = UserDetailDialog(record, test_theme)
    assert dlg.windowTitle() == "User Details — arun"
    labels_text = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "Arun Maurya" in labels_text


# ------------------------------------------------------------
# 2. SysAdmin Services Widget Tests
# ------------------------------------------------------------
SAMPLE_SYSTEMD_SERVICES = """cron.service loaded active running Regular background program processing daemon
dbus.service loaded active running D-Bus System Message Bus
ssh.service loaded active running OpenBSD Secure Shell server
apache2.service loaded failed failed The Apache HTTP Server
cups.service loaded inactive dead CUPS Scheduler
docker.socket loaded active listening Docker Socket for the API
basic.target loaded active active Basic System
"""

SAMPLE_SYSV_SERVICES = """ [ + ]  cron
 [ - ]  apache2
 [ ? ]  alsa-utils
"""

def test_services_widget_systemd_parsing(qapp, test_theme):
    executed_commands = []
    def mock_run_cmd(cmd, title="", callback=None, **kwargs):
        executed_commands.append((cmd, title))
        if callback:
            callback("OK", 0)

    widget = SysAdminServicesWidget(theme=test_theme, run_cmd_fn=mock_run_cmd)
    widget.update_data(SAMPLE_SYSTEMD_SERVICES)

    assert len(widget._all_services) == 7
    assert widget.table.rowCount() == 7

    # Check KPI metrics
    tot = widget.card_total.findChild(type(widget.card_total.layout().itemAt(1).widget()), "val_lbl").text()
    act = widget.card_active.findChild(type(widget.card_active.layout().itemAt(1).widget()), "val_lbl").text()
    fld = widget.card_failed.findChild(type(widget.card_failed.layout().itemAt(1).widget()), "val_lbl").text()
    inact = widget.card_inactive.findChild(type(widget.card_inactive.layout().itemAt(1).widget()), "val_lbl").text()
    init_lbl = widget.card_init.findChild(type(widget.card_init.layout().itemAt(1).widget()), "val_lbl").text()

    assert tot == "7"
    assert act == "5"  # cron, dbus, ssh, docker.socket, basic.target
    assert fld == "1"  # apache2
    assert inact == "1"  # cups
    assert init_lbl == "systemd"


def test_services_widget_sysv_fallback(qapp, test_theme):
    widget = SysAdminServicesWidget(theme=test_theme)
    widget.update_data(SAMPLE_SYSV_SERVICES)

    assert len(widget._all_services) == 3
    assert widget.table.rowCount() == 3

    init_lbl = widget.card_init.findChild(type(widget.card_init.layout().itemAt(1).widget()), "val_lbl").text()
    assert init_lbl == "SysV init"

    # Status of cron should be active
    assert widget._all_services[0]["unit"] == "cron"
    assert widget._all_services[0]["active"] == "active"
    assert widget._all_services[1]["unit"] == "apache2"
    assert widget._all_services[1]["active"] == "inactive"


def test_services_widget_filters_and_actions(qapp, test_theme, monkeypatch):
    executed_commands = []
    def mock_run_cmd(cmd, title="", callback=None, **kwargs):
        executed_commands.append((cmd, title))
        if callback:
            callback("OK", 0)

    widget = SysAdminServicesWidget(theme=test_theme, run_cmd_fn=mock_run_cmd)
    widget.update_data(SAMPLE_SYSTEMD_SERVICES)

    # Filter search by "ssh"
    widget.search_in.setText("ssh")
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "ssh.service"
    widget.search_in.clear()

    # Filter by Failed state
    idx = widget.state_combo.findData("failed")
    widget.state_combo.setCurrentIndex(idx)
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "apache2.service"
    widget.state_combo.setCurrentIndex(0)

    # Test service action (restart)
    widget.table.selectRow(0)
    selected_svc = widget.table.item(0, 0).text()
    widget._service_action("restart")
    assert len(executed_commands) == 1
    cmd, title = executed_commands[0]
    assert "restart" in cmd
    assert selected_svc in cmd


# ------------------------------------------------------------
# 3. SysAdmin Processes Widget Tests
# ------------------------------------------------------------
SAMPLE_PROCESSES_RAW = """USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.1  0.2 168340 13456 ?        Ss   Oct20   0:05 /sbin/init splash
arun        1234 35.5  4.2 2450100 350000 pts/1  Sl+  10:00   1:23 python3 -m admin_suite
arun        1235  0.0  0.0      0      0 ?        Z    10:01   0:00 [defunct_proc] <defunct>
www-data    2048  1.2 12.5 500000 800000 ?       S    09:00   0:15 /usr/sbin/apache2 -k start
"""

def test_processes_widget_parsing_and_kpis(qapp, test_theme):
    widget = SysAdminProcessesWidget(theme=test_theme)
    widget.update_data(SAMPLE_PROCESSES_RAW)

    assert len(widget._all_processes) == 4
    assert widget.table.rowCount() == 4

    # Top CPU should be python3 (35.5%)
    top_cpu = widget.card_top_cpu.findChild(type(widget.card_top_cpu.layout().itemAt(1).widget()), "val_lbl").text()
    assert "python3" in top_cpu
    assert "35.5%" in top_cpu

    # Top Mem should be apache2 (12.5%)
    top_mem = widget.card_top_mem.findChild(type(widget.card_top_mem.layout().itemAt(1).widget()), "val_lbl").text()
    assert "apache2" in top_mem
    assert "12.5%" in top_mem

    # Zombie count should be 1
    zombies = widget.card_zombies.findChild(type(widget.card_zombies.layout().itemAt(1).widget()), "val_lbl").text()
    assert zombies == "1"


def test_processes_widget_filters_and_actions(qapp, test_theme, monkeypatch):
    executed = []
    def mock_run_cmd(cmd, title="", callback=None, **kwargs):
        executed.append((cmd, title))
        if callback:
            callback("OK", 0)

    widget = SysAdminProcessesWidget(theme=test_theme, run_cmd_fn=mock_run_cmd)
    widget.update_data(SAMPLE_PROCESSES_RAW)

    # Filter by user "www-data"
    idx = widget.user_combo.findText("www-data")
    assert idx >= 0
    widget.user_combo.setCurrentIndex(idx)
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "www-data"
    widget.user_combo.setCurrentIndex(0)

    # Filter by zombies
    idx = widget.thresh_combo.findData("zombies")
    widget.thresh_combo.setCurrentIndex(idx)
    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 7).text() == "Z"
    widget.thresh_combo.setCurrentIndex(0)

    # Test sorting numeric PID
    widget.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert int(widget.table.item(0, 1).text()) == 1
    assert int(widget.table.item(widget.table.rowCount() - 1, 1).text()) == 2048


def test_process_renice_dialog(qapp, test_theme):
    dlg = ProcessReniceDialog(pid=1234, user="arun", cmd_name="python3", theme=test_theme)
    dlg.spin.setValue(10)
    assert dlg.get_priority() == 10
    assert dlg.slider.value() == 10


# ------------------------------------------------------------
# 4. Integration Tests in AdminConsoleTab & SysAdminTab
# ------------------------------------------------------------
def test_admin_console_tab_formatted_sysadmin_views(qapp, services):
    console = AdminConsoleTab(
        services,
        profile_name="Localhost",
        profile={"is_local": True},
        initial_category="system",
        auto_refresh=False,
    )

    # Verify that sys_views contains formatted presenter widgets for Users, Services, Processes
    assert "Users" in console.sys_views
    assert isinstance(console.sys_views["Users"], SysAdminUsersWidget)

    assert "Services" in console.sys_views
    assert isinstance(console.sys_views["Services"], SysAdminServicesWidget)

    assert "Processes" in console.sys_views
    assert isinstance(console.sys_views["Processes"], SysAdminProcessesWidget)

    # Verify updating data populates formatted presenter widgets
    console.sys_views["Users"].update_data(SAMPLE_USERS_RAW)
    assert console.sys_views["Users"].table.rowCount() == 7

    console.sys_views["Services"].update_data(SAMPLE_SYSTEMD_SERVICES)
    assert console.sys_views["Services"].table.rowCount() == 7

    console.sys_views["Processes"].update_data(SAMPLE_PROCESSES_RAW)
    assert console.sys_views["Processes"].table.rowCount() == 4

    console.stop_all_workers()


def test_sysadmin_tab_formatted_views(qapp, services):
    services.set_sudo_password("Localhost", "testpass")
    tab = SysAdminTab(
        services,
        profile_name="Localhost",
        profile={"is_local": True},
    )

    # Verify presenter views exist for Users, Services, Processes
    assert "Users" in tab.presenter_views
    assert isinstance(tab.presenter_views["Users"], SysAdminUsersWidget)

    assert "Services" in tab.presenter_views
    assert isinstance(tab.presenter_views["Services"], SysAdminServicesWidget)

    assert "Processes" in tab.presenter_views
    assert isinstance(tab.presenter_views["Processes"], SysAdminProcessesWidget)

    # Verify rendering updates data cleanly
    tab._render("Users", SAMPLE_USERS_RAW, 0)
    assert tab.presenter_views["Users"].table.rowCount() == 7

    tab._render("Services", SAMPLE_SYSTEMD_SERVICES, 0)
    assert tab.presenter_views["Services"].table.rowCount() == 7

    tab._render("Processes", SAMPLE_PROCESSES_RAW, 0)
    assert tab.presenter_views["Processes"].table.rowCount() == 4
