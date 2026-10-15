"""
Unit tests for VPN configuration, Admin Console, and Database Manager enhancements.
"""

import os
import sys
import pytest

from pathlib import Path

# Ensure package root is in sys.path
pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from admin_suite.services import AppServices
from admin_suite.vpn.profiles import (
    VpnProfileManager,
    build_wireguard_conf_content,
    parse_wireguard_conf_content,
)
from admin_suite.vpn.wireguard_server import WireGuardServerManager
from admin_suite.db.table_view import SmartSortFilterProxyModel, CellInspectorDialog
from admin_suite.db.models import SqlResultModel
from PyQt6.QtCore import QThread, pyqtSignal


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
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
    monkeypatch.setattr(paths, "VPN_PROFILES_FILE", tmp_path / "data" / "vpn_profiles.json")
    srv = AppServices()
    srv.paths = paths
    return srv


# ------------------------------------------------------------
# 1. VPN Profile & WireGuard Server Tests
# ------------------------------------------------------------

def test_wireguard_conf_parse_and_build():
    sample_conf = """
[Interface]
Address = 10.8.0.2/24
DNS = 1.1.1.1, 8.8.8.8
PrivateKey = aaaaaaaa=
ListenPort = 51820

[Peer]
PublicKey = bbbbbbbb=
Endpoint = 198.51.100.1:51820
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
    parsed = parse_wireguard_conf_content(sample_conf)
    assert parsed["address"] == "10.8.0.2/24"
    assert parsed["dns"] == "1.1.1.1, 8.8.8.8"
    assert parsed["private_key"] == "aaaaaaaa="
    assert parsed["public_key"] == "bbbbbbbb="
    assert parsed["endpoint"] == "198.51.100.1:51820"
    assert parsed["allowed_ips"] == "0.0.0.0/0, ::/0"
    assert parsed["persistent_keepalive"] == "25"

    rebuilt = build_wireguard_conf_content(parsed)
    assert "Address = 10.8.0.2/24" in rebuilt
    assert "PublicKey = bbbbbbbb=" in rebuilt
    assert "Endpoint = 198.51.100.1:51820" in rebuilt


def test_vpn_profile_manager_lifecycle(services):
    mgr = VpnProfileManager(services)
    initial_count = len(mgr.load_profiles())

    # Add WireGuard profile
    wg_prof = {
        "name": "HQ-Office-WG",
        "type": "wireguard",
        "config_type": "manual",
        "address": "10.8.0.5/24",
        "private_key": "privkey123=",
        "public_key": "pubkey123=",
        "endpoint": "vpn.hq.com:51820",
        "dns": "1.1.1.1",
        "allowed_ips": "0.0.0.0/0",
    }
    saved_wg = mgr.save_or_update_profile(wg_prof)
    assert saved_wg.get("id") is not None
    assert Path(saved_wg["config_path"]).exists()

    # Add Cisco profile
    cisco_prof = {
        "name": "Datacenter Cisco",
        "type": "cisco",
        "cisco_bin": "/opt/cisco/anyconnect/bin/vpn",
        "cisco_host": "vpn.dc.example.com",
        "cisco_user": "admin",
    }
    saved_cisco = mgr.save_or_update_profile(cisco_prof, secrets={"pass": "SecretPass123!"})
    assert saved_cisco.get("id") is not None
    assert mgr.get_profile_secret(saved_cisco["id"], "pass") == "SecretPass123!"

    profs = mgr.load_profiles()
    assert len(profs) == initial_count + 2

    # Set active profile
    mgr.set_active_profile(saved_cisco["id"])
    active = mgr.get_active_profile()
    assert active["id"] == saved_cisco["id"]

    # Delete profile
    mgr.delete_profile(saved_wg["id"])
    remaining = mgr.load_profiles()
    assert len(remaining) == initial_count + 1
    assert any(p["id"] == saved_cisco["id"] for p in remaining)


def test_wireguard_server_scripts():
    prereq_script = WireGuardServerManager.get_prerequisites_check_script()
    assert "WG_CHECK_START" in prereq_script
    assert "which wg" in prereq_script

    install_script = WireGuardServerManager.get_install_packages_script("ubuntu")
    assert "apt-get install -y wireguard wireguard-tools iptables" in install_script

    deploy_script = WireGuardServerManager.get_deploy_server_script(
        server_ext_ip="203.0.113.10",
        port=51820,
        subnet_cidr="10.8.0.1/24",
        dns="1.1.1.1, 8.8.8.8",
        net_iface="eth0",
    )
    assert "wg genkey" in deploy_script
    assert "net.ipv4.ip_forward=1" in deploy_script
    assert "systemctl enable --now wg-quick@wg0" in deploy_script

    peer_script = WireGuardServerManager.get_add_peer_script(
        peer_name="laptop-arun",
        client_ip="10.8.0.2",
        client_pubkey="clientPubkeyABC123=",
    )
    assert "laptop-arun" in peer_script
    assert "PublicKey = clientPubkeyABC123=" in peer_script
    assert "AllowedIPs = 10.8.0.2/32" in peer_script

    raw_show_output = """interface: wg0
  public key: serverPub123=
  listening port: 51820

peer: clientPubABC=
  endpoint: 198.51.100.50:41234
  allowed ips: 10.8.0.2/32
  latest handshake: 1 minute, 12 seconds ago
  transfer: 12.5 MiB received, 45.2 MiB sent
"""
    status = WireGuardServerManager.parse_server_status(raw_show_output)
    assert status["active"] is True
    assert status["interface"] == "wg0"
    assert status["listening_port"] == "51820"
    assert len(status["peers"]) == 1
    assert status["peers"][0]["public_key"] == "clientPubABC="
    assert status["peers"][0]["allowed_ips"] == "10.8.0.2/32"
    assert "12.5 MiB" in status["peers"][0]["transfer_rx"]


def test_vpn_config_dialog_server_combo_dict_profiles(qapp, services, tmp_path, monkeypatch):
    """Test that VpnConfigDialog initializes correctly when PROFILES_FILE is a dictionary."""
    from admin_suite.core.utils import write_json_secure
    from admin_suite.vpn.dialog import VpnConfigDialog
    import admin_suite.core.paths as paths

    prof_file = tmp_path / "data" / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", prof_file)

    # Standard format saved by AdminSuite (dict mapping name to profile dict)
    profiles_dict = {
        "prod-server": {
            "name": "prod-server",
            "ssh_host": "192.168.1.50",
            "ssh_user": "ubuntu",
            "ssh_port": "22",
        },
        "db-server": {
            "name": "db-server",
            "ssh_host": "10.0.0.5",
            "ssh_user": "root",
            "ssh_port": "2222",
        },
        "localhost": {
            "name": "localhost",
            "is_local": True,
        },
    }
    write_json_secure(prof_file, profiles_dict)

    dlg = VpnConfigDialog(services)
    assert dlg.server_combo.count() == 3
    assert "Localhost" in dlg.server_combo.itemText(0)
    assert "prod-server (192.168.1.50)" in dlg.server_combo.itemText(1)
    assert "db-server (10.0.0.5)" in dlg.server_combo.itemText(2)
    assert dlg.server_combo.itemData(1)["name"] == "prod-server"
    assert dlg.server_combo.itemData(1)["ssh_host"] == "192.168.1.50"
    dlg.close()


def test_vpn_config_dialog_server_combo_list_profiles(qapp, services, tmp_path, monkeypatch):
    """Test that VpnConfigDialog handles legacy or list format profiles gracefully."""
    from admin_suite.core.utils import write_json_secure
    from admin_suite.vpn.dialog import VpnConfigDialog
    import admin_suite.core.paths as paths

    prof_file = tmp_path / "data" / "profiles.json"
    monkeypatch.setattr(paths, "PROFILES_FILE", prof_file)

    profiles_list = [
        {"name": "legacy-box", "ssh_host": "172.16.0.10"},
        "plain-name-only",
    ]
    write_json_secure(prof_file, profiles_list)

    dlg = VpnConfigDialog(services)
    assert dlg.server_combo.count() == 3
    assert "Localhost" in dlg.server_combo.itemText(0)
    assert "legacy-box (172.16.0.10)" in dlg.server_combo.itemText(1)
    assert "plain-name-only" in dlg.server_combo.itemText(2)
    dlg.close()


# ------------------------------------------------------------
# 2. Database Table View (Sorting & Filtering) Tests
# ------------------------------------------------------------

def test_smart_sort_filter_proxy_model_sorting(qapp):
    headers = ["id", "username", "age", "status"]
    rows = [
        [10, "bob", 30, "active"],
        [2, "alice", 25, "active"],
        [1, "charlie", 40, "inactive"],
        [20, "david", None, "active"],
    ]

    src = SqlResultModel(headers, rows)
    proxy = SmartSortFilterProxyModel()
    proxy.setSourceModel(src)

    # Sort by 'id' ascending
    proxy.sort(0, pytest.importorskip("PyQt6.QtCore").Qt.SortOrder.AscendingOrder)
    assert proxy.data(proxy.index(0, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "1"
    assert proxy.data(proxy.index(1, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "2"
    assert proxy.data(proxy.index(2, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "10"
    assert proxy.data(proxy.index(3, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "20"

    # Sort by 'id' descending
    proxy.sort(0, pytest.importorskip("PyQt6.QtCore").Qt.SortOrder.DescendingOrder)
    assert proxy.data(proxy.index(0, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "20"
    assert proxy.data(proxy.index(3, 0), pytest.importorskip("PyQt6.QtCore").Qt.ItemDataRole.DisplayRole) == "1"


def test_smart_sort_filter_proxy_model_filters(qapp):
    headers = ["id", "name", "score", "active"]
    rows = [
        [1, "Alice Smith", 95, "yes"],
        [2, "Bob Jones", 72, "no"],
        [3, "Charlie Brown", 88, "yes"],
        [4, "David Clark", None, "no"],
    ]

    src = SqlResultModel(headers, rows)
    proxy = SmartSortFilterProxyModel()
    proxy.setSourceModel(src)

    assert proxy.rowCount() == 4

    # Substring filter on name
    proxy.set_column_filter(1, "Smith")
    assert proxy.rowCount() == 1
    proxy.clear_column_filters()

    # Greater than filter on score
    proxy.set_column_filter(2, "> 80")
    assert proxy.rowCount() == 2  # Alice (95) and Charlie (88)
    proxy.clear_column_filters()

    # NULL check filter
    proxy.set_column_filter(2, "NULL")
    assert proxy.rowCount() == 1  # David
    proxy.clear_column_filters()

    # NOT NULL check filter
    proxy.set_column_filter(2, "!NULL")
    assert proxy.rowCount() == 3
    proxy.clear_column_filters()

    # Exact match filter
    proxy.set_column_filter(3, "=no")
    assert proxy.rowCount() == 2
    proxy.clear_column_filters()

    # Exclusion filter
    proxy.set_column_filter(3, "!no")
    assert proxy.rowCount() == 2


def test_enhanced_table_data_widget_integration(qapp, services):
    from admin_suite.db.table_view import EnhancedTableDataWidget
    widget = EnhancedTableDataWidget(services)

    headers = ["id", "email", "status"]
    rows = [
        [1, "admin@example.com", "active"],
        [2, "user@test.org", "pending"],
        [3, "root@example.com", "active"],
    ]
    widget.set_result(headers, rows)
    assert widget.proxy_model.rowCount() == 3
    assert widget.source_model.rowCount() == 3

    # Filter column
    widget._filter_edits[1].setText("example.com")
    assert widget.proxy_model.rowCount() == 2

    # Push to SQL WHERE
    collected_sql = []
    widget.where_clause_generated.connect(lambda sql: collected_sql.append(sql))
    widget._push_filters_to_sql_where()
    assert len(collected_sql) == 1
    assert "`email` LIKE '%example.com%'" in collected_sql[0]


# ------------------------------------------------------------
# 3. Admin Console & Terminal Pane Tests
# ------------------------------------------------------------

def test_terminal_output_pane(qapp, services):
    from admin_suite.admin_console.terminal_pane import TerminalOutputPane
    pane = TerminalOutputPane(services, host_name="TestServer")
    assert "TestServer" in pane.title_lbl.text()

    # Log command start
    pane.log_command_start("uptime", use_sudo=False)
    assert "● Executing..." in pane.status_badge.text()
    assert "uptime" in pane.output_text.toPlainText()

    # Log command finish
    pane.log_command_output(" 10:00:00 up 12 days, 1 user\n", "", rc=0)
    assert "● Idle" in pane.status_badge.text()
    assert "up 12 days" in pane.output_text.toPlainText()


def test_admin_console_tab_creation(qapp, services):
    from admin_suite.admin_console.console import AdminConsoleTab
    console = AdminConsoleTab(
        services,
        profile_name="Localhost",
        profile={"is_local": True},
        initial_category="system",
        auto_refresh=False,
    )
    assert console.master_tabs.count() == 5  # System, Security, Network, Web, VAPT
    assert console.terminal_pane is not None
def test_admin_console_refresh_all(qapp, services):
    from admin_suite.admin_console.console import AdminConsoleTab
    console = AdminConsoleTab(
        services,
        profile_name="Localhost",
        profile={"is_local": True},
        initial_category="system",
        auto_refresh=False,
    )
    commands_run = []
    def mock_exec(cmd, cb=None, use_sudo=False):
        commands_run.append(cmd)
        if cb:
            cb("test output", "", 0)
    console.execute_command = mock_exec
    console.auto_refresh = True
    # Verify _refresh_sysadmin_section handles string command properly
    console._refresh_sysadmin_section("Overview")
    # Verify refresh_all executes without exceptions
    console.refresh_all()
    assert len(commands_run) > 0


def test_notification_hub_push_signatures():
    from admin_suite.core.notifications import NotificationHub
    hub = NotificationHub()

    # 1. Canonical 3 positional arguments
    hub.push("ok", "Title 1", "Msg 1")
    assert hub.items[0]["level"] == "ok"
    assert hub.items[0]["title"] == "Title 1"
    assert hub.items[0]["msg"] == "Msg 1"

    # 2. 1 positional + level kwarg (the exact case from vapt/tab.py)
    hub.push("VAPT Audit Complete: example.com", level="success")
    assert hub.items[0]["level"] == "ok"  # normalized from success
    assert hub.items[0]["title"] == "VAPT Audit Complete: example.com"

    # 3. 2 positional + level kwarg
    hub.push("Backup", "Completed successfully", level="warning")
    assert hub.items[0]["level"] == "warn"  # normalized from warning
    assert hub.items[0]["title"] == "Backup"
    assert hub.items[0]["msg"] == "Completed successfully"

    # 4. All kwargs
    hub.push(level="error", title="Fatal", message="Something broke")
    assert hub.items[0]["level"] == "error"
    assert hub.items[0]["title"] == "Fatal"
    assert hub.items[0]["msg"] == "Something broke"

def test_sudo_password_alias_resolution(services):
    # Setting password on 'localhost' should make it accessible across all localhost aliases
    services.set_sudo_password("localhost", "my_secret_pw", persist=False)
    assert services.get_sudo_password("localhost") == "my_secret_pw"
    assert services.get_sudo_password("Localhost") == "my_secret_pw"
    assert services.get_sudo_password("Local") == "my_secret_pw"

    # Clearing password on 'Local' should clear all localhost aliases
    services.clear_sudo_password("Local")
    assert services.get_sudo_password("localhost") is None
    assert services.get_sudo_password("Localhost") is None
    assert services.get_sudo_password("Local") is None


class _MockConsoleWorker(QThread):
    finished_cmd = pyqtSignal(str, str, int)

    def run(self):
        self.finished_cmd.emit("ok", "", 0)


def test_admin_console_sudo_localhost_resolution(qapp, services):
    from admin_suite.admin_console.console import AdminConsoleTab
    # Provide sudo_pass in profile dict
    console = AdminConsoleTab(
        services,
        profile_name="localhost",
        profile={"name": "localhost", "is_local": True, "sudo_pass": "pass123"},
        initial_category="system",
        auto_refresh=False,
    )
    assert console._get_sudo_password() == "pass123"
    assert services.get_sudo_password("localhost") == "pass123"
    assert services.get_sudo_password("Local") == "pass123"

    # Test adhoc command wrapping with sudo
    executed_cmds = []
    def mock_worker_run(cmd, timeout=60, sudo_password=None, parent=None):
        executed_cmds.append((cmd, sudo_password))
        return _MockConsoleWorker(parent)

    # Patch _ConsoleLocalExecThread
    console.auto_refresh = True
    import admin_suite.admin_console.console as c_mod
    orig_thread = c_mod._ConsoleLocalExecThread
    c_mod._ConsoleLocalExecThread = mock_worker_run
    try:
        console.execute_command("echo hello", use_sudo=True)
        assert len(executed_cmds) == 1
        cmd, pw = executed_cmds[0]
        assert "sudo -S -p '' bash -c" in cmd
        assert "echo hello" in cmd
        assert pw == "pass123"
    finally:
        c_mod._ConsoleLocalExecThread = orig_thread


def test_console_local_exec_thread_no_hang(qapp):
    from admin_suite.admin_console.console import _ConsoleLocalExecThread
    # Command with sudo_password=None must use DEVNULL and exit cleanly (not hang)
    worker = _ConsoleLocalExecThread("echo local_test", timeout=5, sudo_password=None)
    results = []
    worker.finished_cmd.connect(lambda out, err, rc: results.append((out, err, rc)))
    worker.run()
    assert len(results) == 1
    out, err, rc = results[0]
    assert "local_test" in out
    assert rc == 0



