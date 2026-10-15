"""
Linux Network Administration & Diagnostics components for Admin Console.
Includes Interfaces, Routing & DNS, Active Sockets (ss), Diagnostics (Ping/Traceroute/DNS/Port),
and integrated WireGuard Server Management.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.vpn.wireguard_server import WireGuardServerManager
from admin_suite.vpn.dialog import VpnConfigDialog


class NetworkAdminWidget(QWidget):
    """
    Comprehensive Linux Network Administration Workbench.
    """

    def __init__(self, run_cmd_fn: Callable[[str, Callable[[str, str, int], None]], None], theme: dict[str, Any], parent=None):
        super().__init__(parent)
        self.run_cmd = run_cmd_fn
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.subtabs = QTabWidget()
        layout.addWidget(self.subtabs)

        # Subtab 1: Network Interfaces
        self.tab_ifaces = self._build_ifaces_tab()
        self.subtabs.addTab(self.tab_ifaces, "🔌 Interfaces & IPs")

        # Subtab 2: Routing & DNS
        self.tab_routes = self._build_routes_tab()
        self.subtabs.addTab(self.tab_routes, "🗺️ Routing & DNS")

        # Subtab 3: Active Sockets & Connections
        self.tab_sockets = self._build_sockets_tab()
        self.subtabs.addTab(self.tab_sockets, "🌐 Sockets & Connections (ss)")

        # Subtab 4: Diagnostics (Ping, Trace, DNS, Port Probe)
        self.tab_diag = self._build_diagnostics_tab()
        self.subtabs.addTab(self.tab_diag, "🩺 Network Diagnostics")

        # Subtab 5: WireGuard VPN Server
        self.tab_wg = self._build_wireguard_tab()
        self.subtabs.addTab(self.tab_wg, "🛡️ WireGuard Server Manager")

    # ------------------------------------------------------------
    # 1. Interfaces & IPs
    # ------------------------------------------------------------
    def _build_ifaces_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        bar = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Refresh Interfaces")
        btn_refresh.clicked.connect(self.refresh_interfaces)
        bar.addWidget(btn_refresh)
        bar.addStretch()
        layout.addLayout(bar)

        self.iface_table = QTableWidget(0, 5)
        self.iface_table.setHorizontalHeaderLabels(["Interface", "State", "IPv4 Addresses", "IPv6 / MAC", "MTU / Flags"])
        self.iface_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.iface_table)

        self.iface_raw = QPlainTextEdit()
        self.iface_raw.setReadOnly(True)
        self.iface_raw.setFont(QFont("Monospace", 9))
        self.iface_raw.setMaximumHeight(150)
        layout.addWidget(self.iface_raw)

        return widget

    def refresh_interfaces(self):
        cmd = "ip -br addr show && echo '--- DETAIL ---' && ip -s link show"
        def on_done(out, err, rc):
            self.iface_raw.setPlainText(out or err)
            # Parse ip -br addr show
            lines = out.split("--- DETAIL ---")[0].strip().splitlines()
            self.iface_table.setRowCount(0)
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    iface = parts[0]
                    state = parts[1]
                    ips = ", ".join(parts[2:]) if len(parts) > 2 else "None"
                    row = self.iface_table.rowCount()
                    self.iface_table.insertRow(row)
                    self.iface_table.setItem(row, 0, QTableWidgetItem(iface))
                    self.iface_table.setItem(row, 1, QTableWidgetItem(state))
                    self.iface_table.setItem(row, 2, QTableWidgetItem(ips))
                    self.iface_table.setItem(row, 3, QTableWidgetItem("-"))
                    self.iface_table.setItem(row, 4, QTableWidgetItem("-"))
        self.run_cmd(cmd, on_done)

    # ------------------------------------------------------------
    # 2. Routing & DNS
    # ------------------------------------------------------------
    def _build_routes_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        bar = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Refresh Routes & DNS")
        btn_refresh.clicked.connect(self.refresh_routes)
        bar.addWidget(btn_refresh)
        bar.addStretch()
        layout.addLayout(bar)

        self.routes_raw = QPlainTextEdit()
        self.routes_raw.setReadOnly(True)
        self.routes_raw.setFont(QFont("Monospace", 10))
        layout.addWidget(self.routes_raw)

        return widget

    def refresh_routes(self):
        cmd = "echo '=== IP ROUTING TABLE ===' && ip route show && echo '' && echo '=== /etc/resolv.conf ===' && cat /etc/resolv.conf 2>/dev/null && echo '' && echo '=== DEFAULT GATEWAY PING ===' && (GW=$(ip route show default | awk '{print $3}' | head -n 1); [ -n \"$GW\" ] && ping -c 2 -W 2 $GW || echo 'No default gateway found')"
        def on_done(out, err, rc):
            self.routes_raw.setPlainText(out or err)
        self.run_cmd(cmd, on_done)

    # ------------------------------------------------------------
    # 3. Active Sockets (ss)
    # ------------------------------------------------------------
    def _build_sockets_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        bar = QHBoxLayout()
        self.sock_filter_combo = QComboBox()
        self.sock_filter_combo.addItems(["All Listening (TCP & UDP)", "All Established Connections", "All TCP & UDP Sockets"])
        bar.addWidget(QLabel("Socket Filter:"))
        bar.addWidget(self.sock_filter_combo)

        btn_refresh = QPushButton("🔄 Refresh Sockets")
        btn_refresh.clicked.connect(self.refresh_sockets)
        bar.addWidget(btn_refresh)
        bar.addStretch()
        layout.addLayout(bar)

        self.sockets_table = QTableWidget(0, 5)
        self.sockets_table.setHorizontalHeaderLabels(["Proto", "State", "Local Address:Port", "Peer Address:Port", "Process / PID"])
        self.sockets_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.sockets_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.sockets_table)

        return widget

    def refresh_sockets(self):
        mode = self.sock_filter_combo.currentIndex()
        if mode == 0:
            flag = "-tulnp"
        elif mode == 1:
            flag = "-tunp state established"
        else:
            flag = "-tunap"

        cmd = f"ss {flag}"
        def on_done(out, err, rc):
            lines = out.strip().splitlines()
            self.sockets_table.setRowCount(0)
            for line in lines[1:]:
                parts = line.split(maxsplit=5)
                if len(parts) >= 5:
                    row = self.sockets_table.rowCount()
                    self.sockets_table.insertRow(row)
                    self.sockets_table.setItem(row, 0, QTableWidgetItem(parts[0]))
                    self.sockets_table.setItem(row, 1, QTableWidgetItem(parts[1]))
                    self.sockets_table.setItem(row, 2, QTableWidgetItem(parts[3] if len(parts) > 3 else parts[2]))
                    self.sockets_table.setItem(row, 3, QTableWidgetItem(parts[4] if len(parts) > 4 else "*:*"))
                    proc_info = parts[5] if len(parts) > 5 else "-"
                    self.sockets_table.setItem(row, 4, QTableWidgetItem(proc_info))
        self.run_cmd(cmd, on_done)

    # ------------------------------------------------------------
    # 4. Diagnostics Tool
    # ------------------------------------------------------------
    def _build_diagnostics_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        f_bar = QHBoxLayout()
        self.diag_tool_combo = QComboBox()
        self.diag_tool_combo.addItems(["Ping", "Traceroute", "DNS Lookup (dig/nslookup)", "TCP Port Probe (nc/curl)"])
        f_bar.addWidget(QLabel("Tool:"))
        f_bar.addWidget(self.diag_tool_combo)

        self.diag_target_edit = QLineEdit("8.8.8.8")
        self.diag_target_edit.setPlaceholderText("Hostname, IP, or domain")
        f_bar.addWidget(QLabel("Target:"))
        f_bar.addWidget(self.diag_target_edit, 1)

        self.diag_port_spin = QSpinBox()
        self.diag_port_spin.setRange(1, 65535)
        self.diag_port_spin.setValue(443)
        self.diag_port_spin.setToolTip("Port for TCP Probe")
        f_bar.addWidget(QLabel("Port:"))
        f_bar.addWidget(self.diag_port_spin)

        btn_run = QPushButton("▶ Run Diagnostic")
        btn_run.setStyleSheet("background:#1976d2; color:#fff; font-weight:bold; padding:4px 10px;")
        btn_run.clicked.connect(self._run_diagnostic)
        f_bar.addWidget(btn_run)
        layout.addLayout(f_bar)

        self.diag_output = QPlainTextEdit()
        self.diag_output.setReadOnly(True)
        self.diag_output.setFont(QFont("Monospace", 10))
        layout.addWidget(self.diag_output)

        return widget

    def _run_diagnostic(self):
        target = self.diag_target_edit.text().strip()
        if not target:
            return
        tool_idx = self.diag_tool_combo.currentIndex()
        q_target = shlex.quote(target)

        if tool_idx == 0:
            cmd = f"ping -c 4 {q_target}"
        elif tool_idx == 1:
            cmd = f"(which traceroute >/dev/null && traceroute -n {q_target}) || (which tracepath >/dev/null && tracepath {q_target}) || ping -c 4 {q_target}"
        elif tool_idx == 2:
            cmd = f"(which dig >/dev/null && dig {q_target} +noall +answer) || (which nslookup >/dev/null && nslookup {q_target}) || getent hosts {q_target}"
        else:
            port = self.diag_port_spin.value()
            cmd = f"(which nc >/dev/null && nc -zvw 3 {q_target} {port}) || (curl -v telnet://{q_target}:{port} --max-time 3 2>&1)"

        self.diag_output.setPlainText(f">>> Running: {cmd}...\n")
        def on_done(out, err, rc):
            self.diag_output.appendPlainText((out or "") + (f"\n[stderr]\n{err}" if err else ""))
        self.run_cmd(cmd, on_done)

    # ------------------------------------------------------------
    # 5. WireGuard Server Setup Tab
    # ------------------------------------------------------------
    def _build_wireguard_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top_row = QHBoxLayout()
        lbl_info = QLabel("<b>WireGuard VPN Server:</b> Configure, manage, and deploy a secure WireGuard server on this machine.")
        lbl_info.setStyleSheet(f"color: {self.theme.get('accent', '#3daee9')};")
        top_row.addWidget(lbl_info)
        top_row.addStretch()

        btn_open_full_mgr = QPushButton("🔒 Open Full VPN Manager & Profiles Dialog")
        btn_open_full_mgr.setStyleSheet("font-weight:bold; padding:4px 8px;")
        def open_dialog():
            parent_window = self.window()
            services = getattr(parent_window, "services", None)
            if services:
                dlg = VpnConfigDialog(services, parent=parent_window, initial_tab=1)
                dlg.exec()
        btn_open_full_mgr.clicked.connect(open_dialog)
        top_row.addWidget(btn_open_full_mgr)
        layout.addLayout(top_row)

        action_row = QHBoxLayout()
        btn_status = QPushButton("🔍 Check WireGuard Status (wg show)")
        btn_status.clicked.connect(self._check_wg_status)
        btn_install = QPushButton("📦 Install Packages (wireguard, iptables)")
        btn_install.clicked.connect(self._install_wg)
        btn_start_wg = QPushButton("▶ Start Service")
        btn_start_wg.clicked.connect(lambda: self.run_cmd("systemctl start wg-quick@wg0", lambda o, e, rc: self.wg_log.appendPlainText(o or e)))
        btn_stop_wg = QPushButton("⏹ Stop Service")
        btn_stop_wg.clicked.connect(lambda: self.run_cmd("systemctl stop wg-quick@wg0", lambda o, e, rc: self.wg_log.appendPlainText(o or e)))

        action_row.addWidget(btn_status)
        action_row.addWidget(btn_install)
        action_row.addWidget(btn_start_wg)
        action_row.addWidget(btn_stop_wg)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.wg_log = QPlainTextEdit()
        self.wg_log.setReadOnly(True)
        self.wg_log.setFont(QFont("Monospace", 9))
        layout.addWidget(self.wg_log)

        return widget

    def _check_wg_status(self):
        cmd = WireGuardServerManager.get_prerequisites_check_script() + "\nwg show wg0 2>/dev/null || true"
        def on_done(out, err, rc):
            self.wg_log.setPlainText(out or err)
        self.run_cmd(cmd, on_done)

    def _install_wg(self):
        cmd = WireGuardServerManager.get_install_packages_script()
        def on_done(out, err, rc):
            self.wg_log.appendPlainText(out or err)
        self.run_cmd(cmd, on_done)
