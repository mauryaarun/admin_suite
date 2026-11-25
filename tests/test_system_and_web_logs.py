"""
Unit tests for System Log Viewer (Security Hub) and Web Server Log Analyzer (Web Manager).
Covers command generators, parsing logic, custom paths, auto-discovery, filtering, and PyQt GUI integration.
"""

import sys
from pathlib import Path
import pytest

# Ensure package root is in sys.path
pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox
from admin_suite.services import AppServices
from admin_suite.security.commands import SecurityCommands, SecurityParsers
from admin_suite.security.system_logs import SystemLogViewerWidget
from admin_suite.security.tab import SecurityHubTab
from admin_suite.web.commands import discover_web_logs_cmd, web_logs_cmd
from admin_suite.web.log_analyzer import WebLogAnalyzerWidget


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
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    import admin_suite.core.paths as paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(paths, "CONFIG_FILE", cfg_dir / "config.json")
    srv = AppServices()
    srv.paths = paths
    srv.config.path = cfg_dir / "config.json"
    return srv


# --------------------------------------------------------------------
# 1. System Log Commands & Parser Tests
# --------------------------------------------------------------------
def test_system_logs_combined_cmd():
    cmd = SecurityCommands.system_logs_combined(lines=350)
    assert "/var/log/messages" in cmd
    assert "/var/log/syslog" in cmd
    assert "journalctl --since '1 day ago' -n 350" in cmd
    assert "=== SYSTEM LOG" in cmd
    assert "=== SYSTEMD JOURNALCTL" in cmd


def test_system_logs_syslog_cmd():
    # Direct path
    cmd = SecurityCommands.system_logs_syslog("/var/log/auth.log", lines=150)
    assert "tail -n 150 /var/log/auth.log" in cmd

    # Messages with Debian fallback
    cmd_msg = SecurityCommands.system_logs_syslog("/var/log/messages", lines=200)
    assert "Note: /var/log/messages not found on this system" in cmd_msg
    assert "tail -n 200 /var/log/syslog" in cmd_msg

    # Syslog with RHEL fallback
    cmd_sys = SecurityCommands.system_logs_syslog("/var/log/syslog", lines=200)
    assert "Note: /var/log/syslog not found on this system" in cmd_sys
    assert "tail -n 200 /var/log/messages" in cmd_sys

    # Grep filter
    cmd_grep = SecurityCommands.system_logs_syslog("/var/log/messages", grep_filter="sudo auth")
    assert "| grep -Ei 'sudo auth'" in cmd_grep


def test_system_logs_journalctl_cmd():
    # Full filter parameters
    cmd = SecurityCommands.system_logs_journalctl(
        since="6 hours ago",
        unit="sshd",
        user="root",
        priority="0..3",
        lines=400,
        grep_filter="Accepted publickey",
    )
    assert "--since '6 hours ago'" in cmd
    assert "-u sshd" in cmd
    assert "-p 0..3" in cmd or "-p '0..3'" in cmd
    assert "-n 400 --no-pager" in cmd
    assert "UID_VAL=$(id -u root" in cmd
    assert "_UID=\"$UID_VAL\"" in cmd
    assert "| grep -Ei 'Accepted publickey'" in cmd

    # Numeric user UID
    cmd_uid = SecurityCommands.system_logs_journalctl(user="1000")
    assert "UID_VAL=$(id -u 1000" in cmd_uid

    # Default parameters (1 day ago)
    cmd_default = SecurityCommands.system_logs_journalctl()
    assert "--since '1 day ago'" in cmd_default
    assert "-n 500 --no-pager" in cmd_default


def test_discover_system_logs_and_parser():
    cmd = SecurityCommands.discover_system_logs()
    assert "/var/log/messages" in cmd
    assert "/var/log/syslog" in cmd
    assert "journalctl" in cmd

    sample_output = (
        "=== SYSLOG_FILES ===\n"
        "FOUND|/var/log/syslog|67M\n"
        "FOUND|/var/log/auth.log|62K\n"
        "FOUND|/var/log/audit/audit.log|12M\n"
        "=== JOURNALCTL_STATUS ===\n"
        "journalctl:available\n"
    )
    parsed = SecurityParsers.parse_discovered_system_logs(sample_output)
    assert parsed["journalctl_available"] is True
    assert len(parsed["files"]) == 3
    assert parsed["files"][0]["path"] == "/var/log/syslog"
    assert parsed["files"][0]["size"] == "67M"


# --------------------------------------------------------------------
# 2. System Log Viewer Widget (PyQt GUI)
# --------------------------------------------------------------------
def test_system_log_viewer_widget_gui(qapp, test_theme, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    executed_cmds = []

    def mock_run_cmd(cmd, callback):
        executed_cmds.append(cmd)
        if "discover_system_logs" in cmd or "SYSLOG_FILES" in cmd:
            callback(
                "=== SYSLOG_FILES ===\nFOUND|/var/log/syslog|50M\n=== JOURNALCTL_STATUS ===\njournalctl:available\n",
                "",
                0,
            )
        else:
            callback(
                "Nov 16 10:00:01 host sshd[1234]: Accepted publickey for user root\n"
                "Nov 16 10:05:02 host systemd[1]: Started Service unit\n"
                "Nov 16 10:10:03 host sudo: pam_unix(sudo:auth): session opened for user admin\n",
                "",
                0,
            )

    widget = SystemLogViewerWidget(run_cmd=mock_run_cmd, theme=test_theme)

    # 1. Initial State
    assert widget.source_combo.currentData() == "combined"
    assert widget.since_combo.currentData() == "1 day ago"
    assert widget.lines_combo.currentText() == "500"
    assert "Combined" in widget.mode_badge.text()
    assert widget.splitter.childrenCollapsible() is False

    # 2. Refresh in Combined mode (Default)
    widget.refresh()
    assert len(executed_cmds) >= 2
    assert any("=== SYSTEM LOG" in c for c in executed_cmds)
    assert "journalctl: available" in widget.journal_badge.text()
    assert "Accepted publickey" in widget.log_view.toPlainText()

    # 3. Switch to Syslog mode
    widget.source_combo.setCurrentIndex(1)  # syslog
    assert widget.source_combo.currentData() == "syslog"
    assert widget.syslog_combo.isEnabled() is True
    widget.refresh()
    assert any("tail -n 500" in c for c in executed_cmds)

    # 4. Switch to journalctl mode with filters
    widget.source_combo.setCurrentIndex(2)  # journalctl
    widget.unit_combo.setCurrentText("sshd")
    widget.user_edit.setText("root")
    widget.priority_combo.setCurrentIndex(1)  # 0..3
    widget.refresh()
    last_cmd = executed_cmds[-1]
    assert "-u 'sshd'" in last_cmd or "-u sshd" in last_cmd
    assert "UID_VAL=$(id -u root" in last_cmd
    assert "-p 0..3" in last_cmd or "-p '0..3'" in last_cmd

    # 5. Client-side text filter
    widget.search_in.setText("sshd")
    filtered_text = widget.log_view.toPlainText()
    assert "sshd[1234]" in filtered_text
    assert "Started Service unit" not in filtered_text

    # Clear filter
    widget.search_in.clear()
    assert "Started Service unit" in widget.log_view.toPlainText()

    # 6. Height resizing
    widget.set_output_height(150)
    sizes = widget.splitter.sizes()
    assert len(sizes) == 2

    # 7. Maximize / Restore
    widget._toggle_maximize()
    assert widget._is_maximized is True
    assert widget.config_panel.isHidden() is True
    assert widget.btn_maximize.text() == "↩ Restore"

    widget._toggle_maximize()
    assert widget._is_maximized is False
    assert widget.config_panel.isHidden() is False
    assert widget.btn_maximize.text() == "⤢ Maximize"

    # 8. Clear view
    widget._clear_view()
    assert widget.log_view.toPlainText() == ""


def test_security_hub_tab_system_logs_tab_integration(qapp, services, monkeypatch):
    monkeypatch.setattr(SecurityHubTab, "refresh_all", lambda self: None)
    hub = SecurityHubTab(services=services, profile_name="Localhost")

    tab_titles = [hub.tabs.tabText(i) for i in range(hub.tabs.count())]
    assert any("System Logs" in t for t in tab_titles)
    assert hasattr(hub, "system_logs_tab")
    assert isinstance(hub.system_logs_tab, SystemLogViewerWidget)


# --------------------------------------------------------------------
# 3. Web Hosting Manager Log Commands & Log Analyzer Tests
# --------------------------------------------------------------------
def test_web_logs_commands():
    # 1. Discovery command
    disc_cmd = discover_web_logs_cmd()
    assert "/var/log/apache2" in disc_cmd
    assert "/var/log/httpd" in disc_cmd
    assert "/var/log/nginx" in disc_cmd

    # 2. Apache auto-fallback (access & error)
    ap_both = web_logs_cmd(server="apache2", lines=120, log_type="both")
    assert "=== ACCESS_LOG ===" in ap_both
    assert "/var/log/apache2/access.log" in ap_both
    assert "/var/log/httpd/access_log" in ap_both
    assert "=== ERROR_LOG ===" in ap_both
    assert "/var/log/apache2/error.log" in ap_both

    # 3. Apache access only
    ap_acc = web_logs_cmd(server="apache2", lines=100, log_type="access")
    assert "=== ACCESS_LOG ===" in ap_acc
    assert "=== ERROR_LOG ===" not in ap_acc

    # 4. Apache error only
    ap_err = web_logs_cmd(server="apache2", lines=100, log_type="error")
    assert "=== ERROR_LOG ===" in ap_err
    assert "=== ACCESS_LOG ===" not in ap_err

    # 5. Custom access path only
    cust_acc = web_logs_cmd(custom_access_path="/var/www/site/access.log", log_type="access")
    assert "/var/www/site/access.log" in cust_acc

    # 6. Custom error path only
    cust_err = web_logs_cmd(custom_error_path="/var/www/site/error.log", log_type="error")
    assert "/var/www/site/error.log" in cust_err

    # 7. Dual custom access and error paths
    cust_both = web_logs_cmd(
        custom_access_path="/var/www/site/access.log",
        custom_error_path="/var/www/site/error.log",
        log_type="both",
    )
    assert "=== ACCESS_LOG ===" in cust_both
    assert "/var/www/site/access.log" in cust_both
    assert "=== ERROR_LOG ===" in cust_both
    assert "/var/www/site/error.log" in cust_both


def test_web_log_analyzer_widget_gui(qapp, services, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    executed_cmds = []

    def mock_exec_fn(cmd, callback):
        executed_cmds.append(cmd)
        if "discover_web_logs_cmd" in cmd or "APACHE_HTTPD_LOGS" in cmd:
            callback(
                "=== APACHE_HTTPD_LOGS ===\n"
                "/var/log/apache2/other_vhosts_access.log.1|1.8K\n"
                "/var/log/apache2/error.log.1|717\n"
                "=== NGINX_LOGS ===\n"
                "/var/log/nginx/access.log|10K\n",
                0,
            )
        else:
            # Combined access and error log stream
            sample_stream = (
                "=== ACCESS_LOG ===\n"
                '192.168.1.50 - - [16/Nov/2026:10:00:00 +0000] "GET /index.html HTTP/1.1" 200 1234 "http://ref.com" "Mozilla/5.0"\n'
                'hp.drdo:80 10.0.0.5 - - [16/Nov/2026:10:01:00 +0000] "POST /api/login HTTP/1.1" 401 56 "-" "Mozilla/5.0"\n'
                '192.168.1.50 - - [16/Nov/2026:10:02:00 +0000] "GET /missing.php HTTP/1.1" 404 89 "-" "Curl/7.88"\n'
                '127.0.0.1 - - [16/Nov/2026:10:03:00 +0000] "GET /crash HTTP/1.1" 500 204 "-" "Bot/1.0"\n'
                "=== ERROR_LOG ===\n"
                "[Sun Nov 16 10:00:00.123456 2026] [mpm_prefork:notice] [pid 2413:tid 2413] AH00163: Apache configured\n"
                "[Sun Nov 16 10:01:00 2026] [error] [client 10.0.0.5:54321] AH01071: Got error 'Primary script unknown'\n"
            )
            callback(sample_stream, 0)

    widget = WebLogAnalyzerWidget(services=services, exec_fn=mock_exec_fn)

    # 1. UI components verification
    assert widget.log_target_combo.count() == 3
    assert widget.tabs.count() == 4
    assert widget.tabs.tabText(0) == "📊 Access Requests"
    assert widget.tabs.tabText(1) == "⚠️ Error Events"
    assert widget.tabs.tabText(2) == "🔝 Top Clients & Paths"
    assert widget.tabs.tabText(3) == "📜 Raw Log Output"

    # 2. Trigger refresh and verify log parsing
    widget.refresh()
    assert len(executed_cmds) >= 2

    # Check access table rows
    assert widget.table.rowCount() == 4
    # Check error table rows
    assert widget.error_table.rowCount() == 2

    # Check metrics cards
    lbl_total = widget.card_total.findChild(QLabel, "val_lbl")
    assert lbl_total.text() == "4"
    lbl_2xx = widget.card_2xx.findChild(QLabel, "val_lbl")
    assert lbl_2xx.text() == "1"
    lbl_4xx = widget.card_4xx.findChild(QLabel, "val_lbl")
    assert lbl_4xx.text() == "2"
    lbl_5xx = widget.card_5xx.findChild(QLabel, "val_lbl")
    assert lbl_5xx.text() == "1"
    lbl_err = widget.card_err.findChild(QLabel, "val_lbl")
    assert lbl_err.text() == "2"

    # Check rankings table
    assert widget.top_ip_table.rowCount() >= 2
    assert widget.top_url_table.rowCount() >= 3

    # 3. Custom path save testing
    widget.custom_access_in.setText("/var/log/custom-site/access.log")
    widget._save_custom_access_path()
    assert "/var/log/custom-site/access.log" in services.config.get("custom_access_log_paths", [])

    widget.custom_error_in.setText("/var/log/custom-site/error.log")
    widget._save_custom_error_path()
    assert "/var/log/custom-site/error.log" in services.config.get("custom_error_log_paths", [])

    # 4. Status filter test
    widget.status_code_filter.setCurrentIndex(1)  # 2xx only
    widget._apply_filter()
    assert widget.table.rowCount() == 1

    # 5. Height resizing and toggle maximize
    widget.set_output_height(300)
    widget._toggle_maximize()
    assert widget._is_maximized is True
    assert widget.control_panel.isHidden() is True
    widget._toggle_maximize()
    assert widget._is_maximized is False
    assert widget.control_panel.isHidden() is False
