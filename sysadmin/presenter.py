"""
Beautiful, readable, and presentable formatters and interactive widgets for SysAdmin dashboard output.
Transforms raw terminal output into structured KPI cards, color-coded tables, progress bars, and badges.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.sysadmin.commands import (
    build_journalctl_cmd,
    container_inspect_cmd,
    container_logs_cmd,
    container_op_cmd,
    container_prune_cmd,
    container_rm_cmd,
    container_run_cmd,
    image_prune_cmd,
    image_pull_cmd,
    image_rm_cmd,
    image_search_cmd,
    network_create_cmd,
    network_prune_cmd,
    network_rm_cmd,
    package_refresh_cache_cmd,
    package_update_all_cmd,
    package_update_single_cmd,
    volume_create_cmd,
    volume_prune_cmd,
    volume_rm_cmd,
    dockerfile_build_cmd,
    compose_up_cmd,
    compose_down_cmd,
    compose_restart_cmd,
    compose_logs_cmd,
    compose_ps_cmd,
    compose_build_cmd,
    container_exec_interactive_cmd,
    container_exec_noninteractive_cmd,
)


class SortableTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem with proper numeric, chronological, and weight sorting via UserRole."""

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            v1 = self.data(Qt.ItemDataRole.UserRole)
            v2 = other.data(Qt.ItemDataRole.UserRole)
            if v1 is not None and v2 is not None:
                try:
                    return v1 < v2
                except TypeError:
                    return str(v1) < str(v2)
        return super().__lt__(other)


def _parse_timestamp_for_sort(ts_str: str) -> float:
    """Extract numeric unix timestamp or epoch estimate from log timestamp string."""
    now_year = datetime.datetime.now().year
    # Try ISO format (e.g. 2026-10-07T14:07:20+05:30)
    try:
        clean = ts_str.split("+")[0].split("Z")[0].strip()
        dt = datetime.datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        pass
    # Try Syslog standard format (e.g. Oct 07 14:07:20)
    try:
        parts = ts_str.split()
        if len(parts) >= 3:
            month, day, tm = parts[0], parts[1], parts[2]
            dt = datetime.datetime.strptime(f"{now_year} {month} {day} {tm}", "%Y %b %d %H:%M:%S")
            return dt.timestamp()
    except Exception:
        pass
    return 0.0


def _extract_user_from_log(msg: str, unit: str) -> str:
    """Extract username or UID mentioned in auth, sudo, cron, or systemd log lines."""
    m = re.search(r'\b(?:user|USER)=([a-zA-Z0-9_\-\.]+)', msg)
    if m:
        return m.group(1)
    m = re.search(r'\bsession (?:opened|closed) for user ([a-zA-Z0-9_\-\.]+)', msg)
    if m:
        return m.group(1)
    m = re.search(r'\bfor (?:invalid )?user ([a-zA-Z0-9_\-\.]+)', msg, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'(?:^|\s)sudo:\s+([a-zA-Z0-9_\-\.]+)\s*:', msg)
    if m:
        return m.group(1)
    m = re.search(r'\bAccepted (?:publickey|password) for ([a-zA-Z0-9_\-\.]+)', msg)
    if m:
        return m.group(1)
    m = re.search(r'\bUID\s*=\s*(\d+)', msg)
    if m:
        return f"UID:{m.group(1)}"
    return "-"


def _create_kpi_card(title: str, val: str, color: str, theme: dict[str, str], sub: str = "") -> QFrame:
    frame = QFrame()
    frame.setStyleSheet(
        f"border: 1px solid {theme.get('border', '#444')};"
        f"background: {theme.get('panel2', '#222')};"
        "border-radius: 4px; padding: 4px;"
    )
    l = QVBoxLayout(frame)
    l.setContentsMargins(6, 4, 6, 4)
    l.setSpacing(2)

    t_lbl = QLabel(title)
    t_lbl.setStyleSheet("font-size: 10px; color: #888; text-transform: uppercase;")
    v_lbl = QLabel(val)
    v_lbl.setObjectName("val_lbl")
    v_lbl.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {color};")

    l.addWidget(t_lbl)
    l.addWidget(v_lbl)
    if sub:
        s_lbl = QLabel(sub)
        s_lbl.setObjectName("sub_lbl")
        s_lbl.setStyleSheet("font-size: 10px; color: #aaa;")
        l.addWidget(s_lbl)
    return frame


def _set_card_val(frame: QFrame, val: str, sub: str = ""):
    v = frame.findChild(QLabel, "val_lbl")
    if v:
        v.setText(val)
    if sub:
        s = frame.findChild(QLabel, "sub_lbl")
        if s:
            s.setText(sub)


# ====================================================================
# 1. Overview Widget
# ====================================================================
class SysAdminOverviewWidget(QWidget):
    """Visual KPI and health overview of the system."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Top KPI Cards
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(8)

        self.card_os = _create_kpi_card("Operating System", "Loading...", theme.get("accent", "#3daee9"), theme, "Kernel: -")
        self.card_host = _create_kpi_card("Hostname", "Loading...", theme.get("fg", "#eee"), theme, "Uptime: -")
        self.card_cpu = _create_kpi_card("Processor", "Loading...", "#4fc3f7", theme, "Cores: -")
        self.card_ram = _create_kpi_card("Memory Usage", "Loading...", "#0dbc79", theme, "RAM Used: -")
        self.card_load = _create_kpi_card("System Load", "Loading...", "#f39c12", theme, "1m, 5m, 15m")
        self.card_sec = _create_kpi_card("Security Framework", "Loading...", "#ab47bc", theme, "Status: -")

        kpi_grid.addWidget(self.card_os, 0, 0)
        kpi_grid.addWidget(self.card_host, 0, 1)
        kpi_grid.addWidget(self.card_cpu, 0, 2)
        kpi_grid.addWidget(self.card_ram, 1, 0)
        kpi_grid.addWidget(self.card_load, 1, 1)
        kpi_grid.addWidget(self.card_sec, 1, 2)

        layout.addLayout(kpi_grid)

        # Details Table
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["System Metric / Property", "Detected Value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # 1. OS Release
        os_name = "Linux"
        os_ver = ""
        for line in sections.get("OS RELEASE", []):
            if line.startswith("NAME="):
                os_name = line.split("=", 1)[1].strip('"')
            elif line.startswith("VERSION="):
                os_ver = line.split("=", 1)[1].strip('"')

        # 2. Hostname
        host = "localhost"
        for line in sections.get("HOSTNAME", []):
            if "Static hostname:" in line:
                host = line.split(":", 1)[1].strip()
            elif not line.startswith("Icon") and not line.startswith("Chassis") and line:
                host = line.split()[0]
                break

        # 3. Kernel
        kernel = " ".join(sections.get("KERNEL", ["Unknown"]))

        # 4. Uptime & Load
        uptime_str = "Unknown"
        load_str = ""
        for line in sections.get("UPTIME/LOAD", []):
            if "up" in line:
                m = re.search(r'up\s+(.*?),\s+\d+\s+user', line)
                if m:
                    uptime_str = m.group(1).strip()
                if "load average:" in line:
                    load_str = line.split("load average:", 1)[1].strip()

        # 5. CPU
        cpu_model = "CPU"
        cpu_cores = ""
        for line in sections.get("CPU", []):
            if "Model name:" in line:
                cpu_model = line.split(":", 1)[1].strip()
            elif "model name" in line:
                cpu_model = line.split(":", 1)[1].strip()
            elif "CPU(s):" in line:
                cpu_cores = line.split(":", 1)[1].strip()

        # 6. Memory
        mem_used = "-"
        mem_total = "-"
        for line in sections.get("MEMORY", []):
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 3:
                    mem_total = parts[1]
                    mem_used = parts[2]

        # 7. Security Subsystem
        sec_str = "None"
        for line in sections.get("SECURITY SUBSYSTEM", []):
            if "SELinux status:" in line:
                sec_str = f"SELinux ({line.split(':', 1)[1].strip()})"
            elif "AppArmor:" in line:
                sec_str = line
            elif line:
                sec_str = line
                break

        # Update cards
        _set_card_val(self.card_os, f"{os_name}", f"Version: {os_ver}")
        _set_card_val(self.card_host, host, f"Kernel: {kernel}")
        _set_card_val(self.card_cpu, cpu_model[:32], f"Cores: {cpu_cores or '1'}")
        _set_card_val(self.card_ram, f"{mem_used} / {mem_total}", "Physical Memory")
        _set_card_val(self.card_load, load_str or "-", f"Uptime: {uptime_str}")
        _set_card_val(self.card_sec, sec_str, "Active Linux LSM")

        # Fill table with clean details
        details = [
            ("Operating System", f"{os_name} {os_ver}"),
            ("Hostname", host),
            ("Kernel Architecture", kernel),
            ("System Uptime", uptime_str),
            ("Load Average (1, 5, 15 min)", load_str or "-"),
            ("Processor Model", cpu_model),
            ("Processor Cores", cpu_cores or "-"),
            ("Total Memory", mem_total),
            ("Memory Used", mem_used),
            ("Security Subsystem", sec_str),
            ("Failed Services", " ".join(sections.get("FAILED SERVICES", ["0 failed units"]))),
        ]

        self.table.setRowCount(len(details))
        for row, (k, v) in enumerate(details):
            item_k = QTableWidgetItem(k)
            item_k.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.table.setItem(row, 0, item_k)
            self.table.setItem(row, 1, QTableWidgetItem(v))


# ====================================================================
# 2. Storage Widget
# ====================================================================
class SysAdminStorageWidget(QWidget):
    """Structured disk usage, filesystems, and block devices view."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        lbl = QLabel("💾 Filesystems & Disk Space Usage:")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl)

        self.df_table = QTableWidget(0, 7)
        self.df_table.setHorizontalHeaderLabels(["Filesystem", "Type", "Total Size", "Used", "Avail", "Usage %", "Mount Point"])
        self.df_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.df_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.df_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.df_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.df_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.df_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.df_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.df_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.df_table, 2)

        lbl2 = QLabel("💽 Block Devices & Partitions (lsblk):")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl2)

        self.blk_table = QTableWidget(0, 5)
        self.blk_table.setHorizontalHeaderLabels(["Device Name", "Size", "Type", "FSType", "Mountpoint"])
        self.blk_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.blk_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.blk_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.blk_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.blk_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.blk_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.blk_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # 1. Parse df -hT
        df_lines = sections.get("DISK USAGE (Space)", [])
        rows = []
        for line in df_lines:
            if not line or line.startswith("Filesystem"):
                continue
            parts = line.split()
            if len(parts) >= 7:
                rows.append(parts[:7])

        self.df_table.setRowCount(len(rows))
        for r_idx, r in enumerate(rows):
            for c_idx in range(5):
                self.df_table.setItem(r_idx, c_idx, QTableWidgetItem(r[c_idx]))

            # Usage %
            pct_str = r[5]
            pct_item = QTableWidgetItem(pct_str)
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            try:
                num = int(pct_str.replace("%", ""))
                if num >= 90:
                    pct_item.setForeground(QColor("#ff5252"))
                    pct_item.setFont(QFont("Sans", weight=QFont.Weight.Bold))
                elif num >= 75:
                    pct_item.setForeground(QColor("#ffa000"))
                else:
                    pct_item.setForeground(QColor("#44eb74"))
            except ValueError:
                pass
            self.df_table.setItem(r_idx, 5, pct_item)

            # Mount Point
            self.df_table.setItem(r_idx, 6, QTableWidgetItem(r[6]))

        # 2. Parse lsblk
        blk_lines = sections.get("BLOCK DEVICES", [])
        blk_rows = []
        for line in blk_lines:
            if not line or line.startswith("NAME"):
                continue
            parts = line.split(None, 4)
            if len(parts) >= 3:
                name = parts[0]
                size = parts[1]
                btype = parts[2]
                fstype = parts[3] if len(parts) > 3 else "-"
                mnt = parts[4] if len(parts) > 4 else "-"
                blk_rows.append([name, size, btype, fstype, mnt])

        self.blk_table.setRowCount(len(blk_rows))
        for r_idx, r in enumerate(blk_rows):
            for c_idx, val in enumerate(r):
                self.blk_table.setItem(r_idx, c_idx, QTableWidgetItem(val))


# ====================================================================
# 3. Network Widget
# ====================================================================
class SysAdminNetworkWidget(QWidget):
    """Structured network interfaces, routing, and ports view."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Cards for Route & DNS
        meta_row = QHBoxLayout()
        self.card_route = _create_kpi_card("Default Route & Gateway", "-", theme.get("accent", "#3daee9"), theme)
        self.card_dns = _create_kpi_card("DNS Nameservers", "-", "#4fc3f7", theme)
        meta_row.addWidget(self.card_route)
        meta_row.addWidget(self.card_dns)
        layout.addLayout(meta_row)

        lbl = QLabel("🌐 Network Interfaces & IP Addresses:")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl)

        self.if_table = QTableWidget(0, 4)
        self.if_table.setHorizontalHeaderLabels(["Interface", "Status", "IPv4 Addresses", "IPv6 / Details"])
        self.if_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.if_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.if_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.if_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.if_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.if_table, 1)

        lbl2 = QLabel("🔌 Listening Sockets Preview:")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl2)

        self.ports_table = QTableWidget(0, 4)
        self.ports_table.setHorizontalHeaderLabels(["Proto", "Local Address & Port", "Process / PID", "Raw Info"])
        self.ports_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.ports_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.ports_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.ports_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.ports_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.ports_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # Route
        route_lines = sections.get("DEFAULT ROUTE", ["-"])
        _set_card_val(self.card_route, " ".join(route_lines)[:45])

        # DNS
        dns_lines = [l.split()[-1] for l in sections.get("DNS", []) if "nameserver" in l]
        _set_card_val(self.card_dns, ", ".join(dns_lines) or "systemd-resolved")

        # Interfaces
        if_lines = sections.get("INTERFACES & IPS", [])
        if_rows = []
        for line in if_lines:
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                ipv4 = parts[2] if len(parts) > 2 else "-"
                extra = " ".join(parts[3:]) if len(parts) > 3 else "-"
                if_rows.append([name, status, ipv4, extra])

        self.if_table.setRowCount(len(if_rows))
        for r_idx, r in enumerate(if_rows):
            self.if_table.setItem(r_idx, 0, QTableWidgetItem(r[0]))
            st_item = QTableWidgetItem(r[1])
            st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if r[1] == "UP":
                st_item.setForeground(QColor("#44eb74"))
            else:
                st_item.setForeground(QColor("#888"))
            self.if_table.setItem(r_idx, 1, st_item)
            self.if_table.setItem(r_idx, 2, QTableWidgetItem(r[2]))
            self.if_table.setItem(r_idx, 3, QTableWidgetItem(r[3]))

        # Ports
        port_lines = sections.get("LISTENING PORTS", [])
        port_rows = []
        for line in port_lines:
            if not line or line.startswith("Netid"):
                continue
            parts = line.split()
            if len(parts) >= 5:
                proto = parts[0]
                local = parts[4]
                proc = parts[6] if len(parts) > 6 else "-"
                port_rows.append([proto, local, proc, line])

        self.ports_table.setRowCount(len(port_rows))
        for r_idx, r in enumerate(port_rows):
            for c_idx, val in enumerate(r):
                self.ports_table.setItem(r_idx, c_idx, QTableWidgetItem(val))


# ====================================================================
# 4. Journal / Logs Widget
# ====================================================================
class JournalDetailDialog(QDialog):
    """Detailed popup viewer for an individual journal log entry."""

    def __init__(self, record: dict[str, Any], theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log Detail — {record.get('unit', 'System')}")
        self.resize(650, 420)

        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Severity:</b>"), 0, 0)
        sev_lbl = QLabel(record.get("sev", "-"))
        sev_lbl.setStyleSheet(f"color: {record.get('sev_color', '#ffffff')}; font-weight: bold;")
        grid.addWidget(sev_lbl, 0, 1)

        grid.addWidget(QLabel("<b>Timestamp:</b>"), 0, 2)
        grid.addWidget(QLabel(record.get("ts", "-")), 0, 3)

        grid.addWidget(QLabel("<b>Unit / Service:</b>"), 1, 0)
        grid.addWidget(QLabel(record.get("unit", "-")), 1, 1)

        grid.addWidget(QLabel("<b>User:</b>"), 1, 2)
        u_lbl = QLabel(record.get("user", "-"))
        if record.get("user", "-") != "-":
            u_lbl.setStyleSheet(f"color: {theme.get('accent', '#3daee9')}; font-weight: bold;")
        grid.addWidget(u_lbl, 1, 3)

        layout.addLayout(grid)

        layout.addWidget(QLabel("<b>Full Log Message:</b>"))
        msg_edit = QPlainTextEdit(record.get("msg", ""))
        msg_edit.setReadOnly(True)
        msg_edit.setFont(QFont("JetBrains Mono, Consolas", 10))
        layout.addWidget(msg_edit, 1)

        btn_row = QHBoxLayout()
        btn_copy_msg = QPushButton("📋 Copy Message")
        btn_copy_msg.clicked.connect(lambda: QApplication.clipboard().setText(record.get("msg", "")))
        btn_row.addWidget(btn_copy_msg)

        btn_copy_raw = QPushButton("📄 Copy Raw Line")
        btn_copy_raw.clicked.connect(lambda: QApplication.clipboard().setText(record.get("raw", "")))
        btn_row.addWidget(btn_copy_raw)

        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)


class SysAdminJournalWidget(QWidget):
    """Structured journal log viewer with time/user/severity filtering, live querying, and multi-column sorting."""

    def __init__(self, theme: dict[str, str], run_cmd_fn: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.run_cmd_fn = run_cmd_fn
        self._all_rows: list[dict[str, Any]] = []
        self._filtered_rows: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # --- Toolbar 1: Server Query & Advanced Filters ---
        tb_query = QHBoxLayout()
        tb_query.setSpacing(6)

        tb_query.addWidget(QLabel("Time:"))
        self.time_combo = QComboBox()
        self.time_combo.addItem("Default / Recent", "")
        self.time_combo.addItem("Since 15m ago", "15 minutes ago")
        self.time_combo.addItem("Since 1h ago", "1 hour ago")
        self.time_combo.addItem("Since 6h ago", "6 hours ago")
        self.time_combo.addItem("Since 24h ago", "24 hours ago")
        self.time_combo.addItem("Since today", "today")
        self.time_combo.addItem("Since yesterday", "yesterday")
        self.time_combo.addItem("Since boot (-b)", "-b")
        self.time_combo.addItem("Past 7d", "7 days ago")
        self.time_combo.currentIndexChanged.connect(self._apply_filters)
        tb_query.addWidget(self.time_combo)

        tb_query.addWidget(QLabel("Severity:"))
        self.sev_combo = QComboBox()
        self.sev_combo.addItem("All Severities", "all")
        self.sev_combo.addItem("Crit & Error (0-3)", "err")
        self.sev_combo.addItem("Warning & Above (0-4)", "warning")
        self.sev_combo.addItem("Notice & Above (0-5)", "notice")
        self.sev_combo.addItem("Info & Above (0-6)", "info")
        self.sev_combo.currentIndexChanged.connect(self._apply_filters)
        tb_query.addWidget(self.sev_combo)

        self.user_in = QLineEdit()
        self.user_in.setPlaceholderText("👤 Filter User (e.g. root, arun)...")
        self.user_in.setFixedWidth(160)
        self.user_in.textChanged.connect(self._apply_filters)
        tb_query.addWidget(self.user_in)

        self.unit_in = QLineEdit()
        self.unit_in.setPlaceholderText("📦 Unit (e.g. sshd, nginx)...")
        self.unit_in.setFixedWidth(150)
        self.unit_in.textChanged.connect(self._apply_filters)
        tb_query.addWidget(self.unit_in)

        tb_query.addWidget(QLabel("Limit:"))
        self.limit_combo = QComboBox()
        self.limit_combo.addItems(["50", "100", "250", "500", "1000"])
        self.limit_combo.setCurrentText("100")
        tb_query.addWidget(self.limit_combo)

        self.btn_query = QPushButton("⚡ Query Journal")
        self.btn_query.setToolTip("Fetch fresh journal logs from host using the selected filter criteria")
        self.btn_query.setStyleSheet(f"background: {theme.get('accent', '#3daee9')}; color: #ffffff; font-weight: bold; padding: 4px 10px; border-radius: 3px;")
        self.btn_query.clicked.connect(self._run_server_query)
        tb_query.addWidget(self.btn_query)

        self.btn_reset = QPushButton("🧹 Reset")
        self.btn_reset.setToolTip("Reset all filters to default")
        self.btn_reset.clicked.connect(self._reset_filters)
        tb_query.addWidget(self.btn_reset)

        layout.addLayout(tb_query)

        # --- Toolbar 2: Fast Text Search & Sorting ---
        tb_search = QHBoxLayout()
        tb_search.setSpacing(6)

        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("🔍 Fast search in loaded logs (message, unit, PID, user)...")
        self.search_in.textChanged.connect(self._apply_filters)
        tb_search.addWidget(self.search_in, 1)

        tb_search.addWidget(QLabel("Sort by:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("🕒 Timestamp: Newest First", "time_desc")
        self.sort_combo.addItem("🕒 Timestamp: Oldest First", "time_asc")
        self.sort_combo.addItem("⚠️ Severity: Highest First", "sev_high")
        self.sort_combo.addItem("🔤 Unit: A → Z", "unit_asc")
        self.sort_combo.addItem("👤 User: A → Z", "user_asc")
        self.sort_combo.currentIndexChanged.connect(self._apply_sort)
        tb_search.addWidget(self.sort_combo)

        self.count_lbl = QLabel("0 logs")
        self.count_lbl.setStyleSheet(f"color: {theme.get('sub', '#888')}; font-size: 11px;")
        tb_search.addWidget(self.count_lbl)

        layout.addLayout(tb_search)

        # --- Log Table ---
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Severity", "Timestamp", "Unit / Service", "User", "Message"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table, 1)

    def update_data(self, raw: str):
        rows = []
        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("==") or line_s.startswith("--"):
                continue

            line_lower = line_s.lower()
            if any(k in line_lower for k in ("emerg", "alert", "crit", "critical")):
                sev = "CRIT"
                sev_color = "#ff1744"
                sev_weight = 0
            elif any(k in line_lower for k in ("err", "error", "fail", "failed", "fatal")):
                sev = "ERROR"
                sev_color = "#ff5252"
                sev_weight = 1
            elif any(k in line_lower for k in ("warn", "warning")):
                sev = "WARN"
                sev_color = "#ffa000"
                sev_weight = 2
            elif any(k in line_lower for k in ("notice",)):
                sev = "NOTICE"
                sev_color = "#ffeb3b"
                sev_weight = 3
            elif any(k in line_lower for k in ("debug",)):
                sev = "DEBUG"
                sev_color = "#90caf9"
                sev_weight = 5
            else:
                sev = "INFO"
                sev_color = "#00e676"
                sev_weight = 4

            m_iso = re.match(r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*)\s+(.*)$', line_s)
            m_syslog = re.match(r'^([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+(.*)$', line_s)

            if m_iso:
                ts_str = m_iso.group(1)
                rem = m_iso.group(2)
            elif m_syslog:
                ts_str = m_syslog.group(1)
                rem = m_syslog.group(2)
            else:
                parts = line_s.split(None, 3)
                ts_str = " ".join(parts[:3]) if len(parts) >= 3 else "-"
                rem = parts[3] if len(parts) >= 4 else line_s

            ts_epoch = _parse_timestamp_for_sort(ts_str)

            parts_rem = rem.split(None, 1)
            if len(parts_rem) == 2 and not parts_rem[0].endswith(":"):
                rem = parts_rem[1]

            m_unit = re.match(r'^([a-zA-Z0-9_\-\.\@]+(?:\[\d+\])?):\s*(.*)$', rem)
            if m_unit:
                unit = m_unit.group(1)
                msg = m_unit.group(2)
            else:
                parts_u = rem.split(":", 1)
                if len(parts_u) == 2 and len(parts_u[0].split()) <= 2:
                    unit = parts_u[0].strip()
                    msg = parts_u[1].strip()
                else:
                    unit = "-"
                    msg = rem

            user = _extract_user_from_log(msg, unit)

            rows.append({
                "raw": line_s,
                "sev": sev,
                "sev_color": sev_color,
                "sev_weight": sev_weight,
                "ts": ts_str,
                "ts_epoch": ts_epoch,
                "unit": unit,
                "user": user,
                "msg": msg,
            })

        self._all_rows = rows
        self._apply_filters()

    def _apply_filters(self):
        now = time.time()
        time_sel = self.time_combo.currentData() or ""
        sev_sel = self.sev_combo.currentData() or "all"
        user_filter = self.user_in.text().strip().lower()
        unit_filter = self.unit_in.text().strip().lower()
        search_q = self.search_in.text().strip().lower()

        cutoff = 0.0
        if time_sel == "15 minutes ago":
            cutoff = now - 15 * 60
        elif time_sel == "1 hour ago":
            cutoff = now - 3600
        elif time_sel == "6 hours ago":
            cutoff = now - 6 * 3600
        elif time_sel in ("24 hours ago", "today"):
            cutoff = now - 24 * 3600
        elif time_sel == "yesterday":
            cutoff = now - 48 * 3600
        elif time_sel == "7 days ago":
            cutoff = now - 7 * 86400

        filtered = []
        for r in self._all_rows:
            if cutoff > 0 and r["ts_epoch"] > 0 and r["ts_epoch"] < cutoff:
                continue

            if sev_sel == "err" and r["sev_weight"] > 1:
                continue
            elif sev_sel == "warning" and r["sev_weight"] > 2:
                continue
            elif sev_sel == "notice" and r["sev_weight"] > 3:
                continue
            elif sev_sel == "info" and r["sev_weight"] > 4:
                continue

            if user_filter:
                if user_filter not in r["user"].lower() and user_filter not in r["msg"].lower():
                    continue

            if unit_filter:
                if unit_filter not in r["unit"].lower():
                    continue

            if search_q:
                haystack = f"{r['msg']} {r['unit']} {r['user']} {r['ts']} {r['sev']}".lower()
                if search_q not in haystack:
                    continue

            filtered.append(r)

        self._filtered_rows = filtered
        self._apply_sort()

    def _apply_sort(self):
        mode = self.sort_combo.currentData()
        rows = list(self._filtered_rows)

        if mode == "time_desc":
            rows.sort(key=lambda x: x["ts_epoch"], reverse=True)
        elif mode == "time_asc":
            rows.sort(key=lambda x: x["ts_epoch"])
        elif mode == "sev_high":
            rows.sort(key=lambda x: (x["sev_weight"], -x["ts_epoch"]))
        elif mode == "unit_asc":
            rows.sort(key=lambda x: x["unit"].lower())
        elif mode == "user_asc":
            rows.sort(key=lambda x: (0 if x["user"] != "-" else 1, x["user"].lower()))

        self._filtered_rows = rows
        self._render(rows)

    def _render(self, rows: list[dict[str, Any]]):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))

        for idx, r in enumerate(rows):
            it_sev = SortableTableWidgetItem(r["sev"])
            it_sev.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_sev.setForeground(QColor(r["sev_color"]))
            it_sev.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            it_sev.setData(Qt.ItemDataRole.UserRole, r["sev_weight"])
            self.table.setItem(idx, 0, it_sev)

            it_ts = SortableTableWidgetItem(r["ts"])
            it_ts.setData(Qt.ItemDataRole.UserRole, r["ts_epoch"])
            self.table.setItem(idx, 1, it_ts)

            it_unit = SortableTableWidgetItem(r["unit"])
            it_unit.setData(Qt.ItemDataRole.UserRole, r["unit"].lower())
            self.table.setItem(idx, 2, it_unit)

            it_user = SortableTableWidgetItem(r["user"])
            it_user.setData(Qt.ItemDataRole.UserRole, r["user"].lower())
            if r["user"] != "-":
                it_user.setForeground(QColor(self.theme.get("accent", "#3daee9")))
                it_user.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            else:
                it_user.setForeground(QColor(self.theme.get("sub", "#888888")))
            self.table.setItem(idx, 3, it_user)

            it_msg = SortableTableWidgetItem(r["msg"])
            it_msg.setData(Qt.ItemDataRole.UserRole, r["msg"])
            self.table.setItem(idx, 4, it_msg)

        self.table.setSortingEnabled(True)
        self.count_lbl.setText(f"Showing {len(rows)} of {len(self._all_rows)} logs")

    def _run_server_query(self):
        if not self.run_cmd_fn:
            self._apply_filters()
            return

        limit = int(self.limit_combo.currentText())
        since = self.time_combo.currentData() or ""
        sev = self.sev_combo.currentData() or "all"
        user = self.user_in.text().strip()
        unit = self.unit_in.text().strip()
        grep_t = self.search_in.text().strip()

        cmd = build_journalctl_cmd(limit=limit, priority=sev, since=since, user=user, unit=unit, grep_text=grep_t)
        self.btn_query.setEnabled(False)
        self.btn_query.setText("⏳ Fetching...")

        def on_done(out: str, rc: int):
            self.btn_query.setEnabled(True)
            self.btn_query.setText("⚡ Query Journal")
            if out.strip():
                self.update_data(out)
            else:
                QMessageBox.information(self, "Journal", "No logs matching the query were returned.")

        self.run_cmd_fn(cmd, "Query Journal Logs", timeout=60, callback=on_done, refresh_after=False)

    def _reset_filters(self):
        self.time_combo.setCurrentIndex(0)
        self.sev_combo.setCurrentIndex(0)
        self.user_in.clear()
        self.unit_in.clear()
        self.search_in.clear()
        self.sort_combo.setCurrentIndex(0)
        self._apply_filters()

    def _on_row_double_clicked(self, row: int, col: int):
        if 0 <= row < len(self._filtered_rows):
            rec = self._filtered_rows[row]
            dlg = JournalDetailDialog(rec, self.theme, self)
            dlg.exec()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._filtered_rows):
            return
        rec = self._filtered_rows[row]
        menu = QMenu(self)
        det_act = menu.addAction("🔍 View Log Details...")
        copy_msg = menu.addAction("📋 Copy Message")
        copy_line = menu.addAction("📄 Copy Full Line")
        menu.addSeparator()
        user_filter_act = menu.addAction(f"👤 Filter by user '{rec['user']}'") if rec["user"] != "-" else None
        unit_filter_act = menu.addAction(f"📦 Filter by unit '{rec['unit']}'") if rec["unit"] != "-" else None

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == det_act:
            self._on_row_double_clicked(row, 0)
        elif action == copy_msg:
            QApplication.clipboard().setText(rec["msg"])
        elif action == copy_line:
            QApplication.clipboard().setText(rec["raw"])
        elif user_filter_act and action == user_filter_act:
            self.user_in.setText(rec["user"])
        elif unit_filter_act and action == unit_filter_act:
            self.unit_in.setText(rec["unit"].split("[")[0])


# ====================================================================
# 1. Container Logs Dialog
# ====================================================================
class ContainerLogsDialog(QDialog):
    """Monospace log viewer with auto-tail, filtering, and copy."""

    def __init__(
        self,
        theme: dict[str, str],
        engine: str,
        container_id: str,
        container_name: str,
        run_cmd_fn: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.engine = engine
        self.container_id = container_id
        self.container_name = container_name
        self.run_cmd_fn = run_cmd_fn
        self.setWindowTitle(f"📜 Container Logs: {container_name or container_id[:12]} ({engine})")
        self.resize(850, 550)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Top Bar
        top_bar = QHBoxLayout()
        info_lbl = QLabel(
            f"<b>Container:</b> {container_name} "
            f"<span style='color:#888;'>({container_id[:12]})</span> | "
            f"<b>Engine:</b> {engine.upper()}"
        )
        info_lbl.setStyleSheet(f"color: {theme.get('fg', '#ffffff')};")
        top_bar.addWidget(info_lbl)
        top_bar.addStretch()

        top_bar.addWidget(QLabel("Tail:"))
        self.tail_combo = QComboBox()
        self.tail_combo.addItems(["50", "100", "200", "500", "1000"])
        self.tail_combo.setCurrentText("200")
        self.tail_combo.currentTextChanged.connect(self._fetch_logs)
        top_bar.addWidget(self.tail_combo)

        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.clicked.connect(self._fetch_logs)
        top_bar.addWidget(self.btn_refresh)

        self.btn_copy = QPushButton("📋 Copy All")
        self.btn_copy.clicked.connect(self._copy_all)
        top_bar.addWidget(self.btn_copy)
        layout.addLayout(top_bar)

        # Filter row
        filt_bar = QHBoxLayout()
        self.filter_in = QLineEdit()
        self.filter_in.setPlaceholderText("🔍 Filter log lines as you type...")
        self.filter_in.textChanged.connect(self._apply_filter)
        filt_bar.addWidget(self.filter_in, 1)

        self.autoscroll_chk = QCheckBox("Auto-scroll to bottom")
        self.autoscroll_chk.setChecked(True)
        filt_bar.addWidget(self.autoscroll_chk)
        layout.addLayout(filt_bar)

        # Log viewer
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {theme.get('panel', '#181b20')};
                color: #d1d5db;
                border: 1px solid {theme.get('border', '#333')};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.editor, 1)

        self.status_lbl = QLabel("Loading logs...")
        self.status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        self._raw_lines: list[str] = []
        self._fetch_logs()

    def _fetch_logs(self):
        if not self.run_cmd_fn:
            self.editor.setPlainText("Command runner unavailable.")
            return
        tail_val = int(self.tail_combo.currentText() or 200)
        cmd = container_logs_cmd(self.engine, self.container_id, tail=tail_val)
        self.status_lbl.setText("⏳ Fetching logs from host...")

        def on_done(out: str, rc: int):
            self._raw_lines = out.splitlines()
            self._apply_filter()
            self.status_lbl.setText(f"Loaded {len(self._raw_lines)} lines (tail={tail_val})")

        self.run_cmd_fn(cmd, f"Logs: {self.container_name}", timeout=60, callback=on_done, refresh_after=False)

    def _apply_filter(self):
        q = self.filter_in.text().strip().lower()
        if not q:
            text = "\n".join(self._raw_lines)
        else:
            text = "\n".join(line for line in self._raw_lines if q in line.lower())
        self.editor.setPlainText(text)
        if self.autoscroll_chk.isChecked():
            self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().maximum())

    def _copy_all(self):
        QApplication.clipboard().setText(self.editor.toPlainText())
        self.status_lbl.setText("📋 Copied all logs to clipboard!")


# ====================================================================
# 2. Container Inspect Dialog
# ====================================================================
class ContainerInspectDialog(QDialog):
    """Detailed JSON configuration inspector."""

    def __init__(
        self,
        theme: dict[str, str],
        engine: str,
        target_id: str,
        target_name: str,
        run_cmd_fn: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.engine = engine
        self.target_id = target_id
        self.target_name = target_name
        self.run_cmd_fn = run_cmd_fn
        self.setWindowTitle(f"🔍 Inspect: {target_name or target_id[:12]} ({engine})")
        self.resize(800, 520)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(f"<b>Inspect Configuration:</b> {target_name} ({target_id[:12]})"))
        top.addStretch()
        btn_copy = QPushButton("📋 Copy JSON")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.editor.toPlainText()))
        top.addWidget(btn_copy)
        layout.addLayout(top)

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        layout.addWidget(self.editor, 1)

        self._fetch()

    def _fetch(self):
        if not self.run_cmd_fn:
            self.editor.setPlainText("Command runner unavailable.")
            return
        cmd = container_inspect_cmd(self.engine, self.target_id)

        def on_done(out: str, rc: int):
            try:
                data = json.loads(out)
                formatted = json.dumps(data, indent=2)
                self.editor.setPlainText(formatted)
            except Exception:
                self.editor.setPlainText(out)

        self.run_cmd_fn(cmd, f"Inspect: {self.target_name}", timeout=60, callback=on_done, refresh_after=False)


# ====================================================================
# 3. Create & Run Container Dialog
# ====================================================================
class ContainerRunDialog(QDialog):
    """Interactive modal to configure and launch a new container."""

    def __init__(
        self,
        theme: dict[str, str],
        active_engine: str = "docker",
        available_engines: list[str] | None = None,
        prefill_image: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("➕ Create & Run Container")
        self.resize(650, 480)

        engines = available_engines or ["docker", "podman"]
        self.selected_engine = active_engine if active_engine in engines else engines[0]

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Engine selector
        eng_box = QHBoxLayout()
        eng_box.addWidget(QLabel("<b>Container Engine:</b>"))
        self.eng_group = QButtonGroup(self)
        self.rb_docker = QRadioButton("🐳 Docker")
        self.rb_podman = QRadioButton("🦭 Podman")
        self.eng_group.addButton(self.rb_docker)
        self.eng_group.addButton(self.rb_podman)
        if self.selected_engine == "podman":
            self.rb_podman.setChecked(True)
        else:
            self.rb_docker.setChecked(True)
        self.rb_docker.toggled.connect(self._update_preview)
        self.rb_podman.toggled.connect(self._update_preview)
        eng_box.addWidget(self.rb_docker)
        eng_box.addWidget(self.rb_podman)
        eng_box.addStretch()
        layout.addLayout(eng_box)

        # Form layout
        form = QFormLayout()
        form.setSpacing(8)

        # Image input
        self.image_in = QLineEdit()
        self.image_in.setPlaceholderText("e.g. nginx:alpine, redis:7, postgres:16, ubuntu:24.04")
        if prefill_image:
            self.image_in.setText(prefill_image)
        self.image_in.textChanged.connect(self._update_preview)
        form.addRow("Image Name *:", self.image_in)

        # Container Name
        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("e.g. my-web-app (optional)")
        self.name_in.textChanged.connect(self._update_preview)
        form.addRow("Container Name:", self.name_in)

        # Port Mappings
        self.ports_in = QLineEdit()
        self.ports_in.setPlaceholderText("e.g. 8080:80, 8443:443")
        self.ports_in.textChanged.connect(self._update_preview)
        form.addRow("Port Publish (-p):", self.ports_in)

        # Volumes
        self.vols_in = QLineEdit()
        self.vols_in.setPlaceholderText("e.g. /host/data:/data, my_volume:/var/www/html")
        self.vols_in.textChanged.connect(self._update_preview)
        form.addRow("Volume Mounts (-v):", self.vols_in)

        # Envs
        self.envs_in = QLineEdit()
        self.envs_in.setPlaceholderText("e.g. PORT=80, NODE_ENV=production, DB_USER=root")
        self.envs_in.textChanged.connect(self._update_preview)
        form.addRow("Environment (-e):", self.envs_in)

        # Network
        self.net_in = QLineEdit()
        self.net_in.setPlaceholderText("e.g. bridge, host, or custom network name")
        self.net_in.textChanged.connect(self._update_preview)
        form.addRow("Network (--network):", self.net_in)

        # Restart Policy
        self.restart_combo = QComboBox()
        self.restart_combo.addItems(["unless-stopped", "always", "on-failure", "no"])
        self.restart_combo.currentTextChanged.connect(self._update_preview)
        form.addRow("Restart Policy:", self.restart_combo)

        # Detach
        self.detach_chk = QCheckBox("Run in background (detached -d)")
        self.detach_chk.setChecked(True)
        self.detach_chk.toggled.connect(self._update_preview)
        form.addRow("", self.detach_chk)

        # Custom Command
        self.cmd_in = QLineEdit()
        self.cmd_in.setPlaceholderText("e.g. sh -c 'echo hello' (optional override)")
        self.cmd_in.textChanged.connect(self._update_preview)
        form.addRow("Command / Args:", self.cmd_in)

        layout.addLayout(form)

        # Command Preview
        prev_lbl = QLabel("<b>Command Preview:</b>")
        layout.addWidget(prev_lbl)
        self.preview_box = QLineEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setStyleSheet(
            f"background-color:{theme.get('panel', '#222')}; font-family:monospace; color:#3daee9;"
        )
        layout.addWidget(self.preview_box)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self.btn_run = QPushButton("🚀 Launch Container")
        self.btn_run.setStyleSheet(
            f"background-color:{theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold; padding:6px 16px;"
        )
        self.btn_run.clicked.connect(self._on_submit)
        btn_box.addWidget(self.btn_run)
        layout.addLayout(btn_box)

        self._update_preview()

    def _get_engine(self) -> str:
        return "podman" if self.rb_podman.isChecked() else "docker"

    def _update_preview(self):
        img = self.image_in.text().strip() or "<image>"
        cmd = container_run_cmd(
            engine=self._get_engine(),
            image=img,
            name=self.name_in.text().strip(),
            ports=self.ports_in.text().strip(),
            volumes=self.vols_in.text().strip(),
            envs=self.envs_in.text().strip(),
            restart=self.restart_combo.currentText(),
            detach=self.detach_chk.isChecked(),
            network=self.net_in.text().strip(),
            cmd=self.cmd_in.text().strip(),
        )
        self.preview_box.setText(cmd)

    def _on_submit(self):
        img = self.image_in.text().strip()
        if not img:
            QMessageBox.warning(self, "Validation Error", "Please provide a container image name.")
            return
        self.accept()

    def get_run_command(self) -> tuple[str, str, str]:
        """Returns (engine, container_name, full_command)."""
        eng = self._get_engine()
        name = self.name_in.text().strip() or self.image_in.text().strip().split(":")[0]
        cmd = container_run_cmd(
            engine=eng,
            image=self.image_in.text().strip(),
            name=self.name_in.text().strip(),
            ports=self.ports_in.text().strip(),
            volumes=self.vols_in.text().strip(),
            envs=self.envs_in.text().strip(),
            restart=self.restart_combo.currentText(),
            detach=self.detach_chk.isChecked(),
            network=self.net_in.text().strip(),
            cmd=self.cmd_in.text().strip(),
        )
        return eng, name, cmd


# ====================================================================
# 4. Pull Image Dialog
# ====================================================================
class ImagePullDialog(QDialog):
    """Modal to pull images with preset suggestions."""

    def __init__(self, theme: dict[str, str], active_engine: str = "docker", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("📥 Pull Image from Registry")
        self.resize(550, 260)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Engine selector
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Target Engine:</b>"))
        self.eng_combo = QComboBox()
        self.eng_combo.addItems(["docker", "podman"])
        if active_engine in ("docker", "podman"):
            self.eng_combo.setCurrentText(active_engine)
        top.addWidget(self.eng_combo)
        top.addStretch()
        layout.addLayout(top)

        # Image input
        layout.addWidget(QLabel("Image Name & Tag (e.g. <code>nginx:alpine</code>, <code>postgres:16</code>):"))
        self.img_in = QLineEdit()
        self.img_in.setPlaceholderText("e.g. alpine:latest, redis:7-alpine, quay.io/podman/hello:latest")
        layout.addWidget(self.img_in)

        # Quick preset buttons
        layout.addWidget(QLabel("<small>Quick Presets:</small>"))
        preset_box = QHBoxLayout()
        presets = ["nginx:alpine", "redis:alpine", "postgres:16", "alpine:latest", "node:20-alpine", "python:3.12-slim"]
        for p in presets:
            btn = QPushButton(p)
            btn.setStyleSheet("font-size: 11px; padding: 2px 6px;")
            btn.clicked.connect(lambda _, val=p: self.img_in.setText(val))
            preset_box.addWidget(btn)
        layout.addLayout(preset_box)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_pull = QPushButton("📥 Pull Image")
        btn_pull.setStyleSheet(
            f"background-color:{theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold; padding:6px 16px;"
        )
        btn_pull.clicked.connect(self._on_submit)
        btn_box.addWidget(btn_pull)
        layout.addLayout(btn_box)

    def _on_submit(self):
        if not self.img_in.text().strip():
            QMessageBox.warning(self, "Validation Error", "Please enter an image name.")
            return
        self.accept()

    def get_pull_details(self) -> tuple[str, str]:
        return self.eng_combo.currentText(), self.img_in.text().strip()


# ====================================================================
# 5. Search Registry Dialog
# ====================================================================
class ImageSearchDialog(QDialog):
    """Search container registry and pull directly from results."""

    def __init__(
        self,
        theme: dict[str, str],
        active_engine: str = "docker",
        run_cmd_fn: Optional[Callable] = None,
        on_pull_selected: Optional[Callable[[str, str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.active_engine = active_engine
        self.run_cmd_fn = run_cmd_fn
        self.on_pull_selected = on_pull_selected
        self.setWindowTitle("🔍 Search Container Registry")
        self.resize(750, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Search bar
        s_box = QHBoxLayout()
        s_box.addWidget(QLabel("Engine:"))
        self.eng_combo = QComboBox()
        self.eng_combo.addItems(["docker", "podman"])
        self.eng_combo.setCurrentText(active_engine)
        s_box.addWidget(self.eng_combo)

        self.query_in = QLineEdit()
        self.query_in.setPlaceholderText("Search Docker Hub / Registry (e.g. mysql, caddy, redis)...")
        self.query_in.returnPressed.connect(self._do_search)
        s_box.addWidget(self.query_in, 1)

        self.btn_search = QPushButton("🔍 Search")
        self.btn_search.clicked.connect(self._do_search)
        s_box.addWidget(self.btn_search)
        layout.addLayout(s_box)

        # Results table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Description", "Stars", "Official", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        self.status_lbl = QLabel("Enter a search term and press Search")
        self.status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_lbl)

    def _do_search(self):
        q = self.query_in.text().strip()
        if not q:
            return
        if not self.run_cmd_fn:
            self.status_lbl.setText("Command runner unavailable.")
            return

        eng = self.eng_combo.currentText()
        cmd = image_search_cmd(eng, q, limit=25)
        self.status_lbl.setText(f"⏳ Searching registry for '{q}'...")
        self.btn_search.setEnabled(False)

        def on_done(out: str, rc: int):
            self.btn_search.setEnabled(True)
            if rc != 0:
                self.status_lbl.setText(f"Search failed (rc={rc})")
                return

            rows = []
            for line in out.splitlines():
                line_s = line.strip()
                if not line_s or line_s.startswith("NAME") or line_s.startswith("INDEX"):
                    continue
                parts = line_s.split(None, 4)
                if len(parts) >= 2:
                    name = parts[0]
                    desc = parts[1] if len(parts) > 1 else ""
                    stars = parts[2] if len(parts) > 2 else "0"
                    official = parts[3] if len(parts) > 3 else ""
                    rows.append((name, desc, stars, official))

            self.table.setRowCount(len(rows))
            for idx, (name, desc, stars, official) in enumerate(rows):
                self.table.setItem(idx, 0, QTableWidgetItem(name))
                self.table.setItem(idx, 1, QTableWidgetItem(desc))
                self.table.setItem(idx, 2, QTableWidgetItem(stars))
                self.table.setItem(idx, 3, QTableWidgetItem(official))

                btn_p = QPushButton("📥 Pull")
                btn_p.setStyleSheet("padding: 2px 8px; font-weight: bold;")
                btn_p.clicked.connect(lambda _, img=name: self._pull_img(img))
                self.table.setCellWidget(idx, 4, btn_p)

            self.status_lbl.setText(f"Found {len(rows)} matching images")

        self.run_cmd_fn(cmd, f"Search Images: {q}", timeout=60, callback=on_done, refresh_after=False)

    def _pull_img(self, img_name: str):
        if self.on_pull_selected:
            self.on_pull_selected(self.eng_combo.currentText(), img_name)
        self.accept()


# ====================================================================
# 6. Volume Create Dialog
# ====================================================================
class VolumeCreateDialog(QDialog):
    """Modal to create a named volume."""

    def __init__(self, theme: dict[str, str], active_engine: str = "docker", parent=None):
        super().__init__(parent)
        self.setWindowTitle("➕ Create Container Volume")
        self.resize(450, 180)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.eng_combo = QComboBox()
        self.eng_combo.addItems(["docker", "podman"])
        self.eng_combo.setCurrentText(active_engine)
        form.addRow("Engine:", self.eng_combo)

        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("e.g. pg_data, app_storage, web_cache")
        form.addRow("Volume Name *:", self.name_in)

        self.driver_in = QLineEdit()
        self.driver_in.setPlaceholderText("local (default)")
        form.addRow("Driver:", self.driver_in)
        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)
        btn_ok = QPushButton("➕ Create")
        btn_ok.clicked.connect(self._on_submit)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_submit(self):
        if not self.name_in.text().strip():
            QMessageBox.warning(self, "Validation Error", "Please provide a volume name.")
            return
        self.accept()

    def get_details(self) -> tuple[str, str, str]:
        return self.eng_combo.currentText(), self.name_in.text().strip(), self.driver_in.text().strip()


# ====================================================================
# 7. Network Create Dialog
# ====================================================================
class NetworkCreateDialog(QDialog):
    """Modal to create a container network."""

    def __init__(self, theme: dict[str, str], active_engine: str = "docker", parent=None):
        super().__init__(parent)
        self.setWindowTitle("➕ Create Container Network")
        self.resize(450, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.eng_combo = QComboBox()
        self.eng_combo.addItems(["docker", "podman"])
        self.eng_combo.setCurrentText(active_engine)
        form.addRow("Engine:", self.eng_combo)

        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("e.g. frontend_net, backend_net, app_mesh")
        form.addRow("Network Name *:", self.name_in)

        self.driver_combo = QComboBox()
        self.driver_combo.addItems(["bridge", "host", "macvlan", "overlay"])
        form.addRow("Driver:", self.driver_combo)

        self.subnet_in = QLineEdit()
        self.subnet_in.setPlaceholderText("e.g. 172.28.0.0/16 (optional)")
        form.addRow("Subnet (CIDR):", self.subnet_in)
        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)
        btn_ok = QPushButton("➕ Create")
        btn_ok.clicked.connect(self._on_submit)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_submit(self):
        if not self.name_in.text().strip():
            QMessageBox.warning(self, "Validation Error", "Please provide a network name.")
            return
        self.accept()

    def get_details(self) -> tuple[str, str, str, str]:
        return (
            self.eng_combo.currentText(),
            self.name_in.text().strip(),
            self.driver_combo.currentText(),
            self.subnet_in.text().strip(),
        )


# ====================================================================
# 8. Dockerfile Builder Dialog
# ====================================================================
class DockerfileBuildDialog(QDialog):
    """Interactive modal to build container images from a Dockerfile path or interactive editor."""

    TEMPLATES = {
        "🐍 Python (FastAPI / Web)": (
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 8000\n"
            'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
        ),
        "⚡ Node.js (Alpine Express)": (
            "FROM node:20-alpine\n"
            "WORKDIR /app\n"
            "COPY package*.json ./\n"
            "RUN npm ci --only=production\n"
            "COPY . .\n"
            "EXPOSE 3000\n"
            'CMD ["node", "server.js"]\n'
        ),
        "🌐 Nginx Static Web": (
            "FROM nginx:alpine\n"
            "COPY ./html /usr/share/nginx/html\n"
            "EXPOSE 80\n"
            'CMD ["nginx", "-g", "daemon off;"]\n'
        ),
        "🐹 Go (Multi-Stage Minimal)": (
            "# Build stage\n"
            "FROM golang:1.22-alpine AS builder\n"
            "WORKDIR /build\n"
            "COPY . .\n"
            "RUN CGO_ENABLED=0 GOOS=linux go build -o app .\n\n"
            "# Final minimal stage\n"
            "FROM alpine:latest\n"
            "WORKDIR /app\n"
            "COPY --from=builder /build/app .\n"
            "EXPOSE 8080\n"
            'CMD ["./app"]\n'
        ),
        "☕ Java Spring Boot (Temurin 21)": (
            "FROM eclipse-temurin:21-jre-alpine\n"
            "WORKDIR /app\n"
            "COPY target/*.jar app.jar\n"
            "EXPOSE 8080\n"
            'ENTRYPOINT ["java", "-jar", "app.jar"]\n'
        ),
        "🦀 Rust (Alpine Multi-Stage)": (
            "FROM rust:1.77-alpine AS builder\n"
            "WORKDIR /app\n"
            "COPY . .\n"
            "RUN cargo build --release\n\n"
            "FROM alpine:latest\n"
            "COPY --from=builder /app/target/release/app /usr/local/bin/app\n"
            'CMD ["app"]\n'
        ),
    }

    def __init__(
        self,
        theme: dict[str, str],
        active_engine: str = "docker",
        available_engines: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("🔨 Build Image from Dockerfile")
        self.resize(720, 560)

        engines = available_engines or ["docker", "podman"]
        self.selected_engine = active_engine if active_engine in engines else engines[0]

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Engine selector
        eng_box = QHBoxLayout()
        eng_box.addWidget(QLabel("<b>Container Engine:</b>"))
        self.eng_combo = QComboBox()
        self.eng_combo.addItems(["docker", "podman"])
        self.eng_combo.setCurrentText(self.selected_engine)
        self.eng_combo.currentTextChanged.connect(self._update_preview)
        eng_box.addWidget(self.eng_combo)
        eng_box.addStretch()
        layout.addLayout(eng_box)

        # Tabs for Source: Path vs In-app Editor
        self.src_tabs = QTabWidget()

        # Tab 1: Path
        path_w = QWidget()
        p_lay = QVBoxLayout(path_w)
        p_form = QFormLayout()
        self.dockerfile_path_in = QLineEdit("Dockerfile")
        self.dockerfile_path_in.setPlaceholderText("e.g. Dockerfile or /path/to/Dockerfile")
        self.dockerfile_path_in.textChanged.connect(self._update_preview)
        p_form.addRow("Dockerfile Path *:", self.dockerfile_path_in)
        p_lay.addLayout(p_form)
        p_lay.addStretch()
        self.src_tabs.addTab(path_w, "📁 Existing File Path")

        # Tab 2: In-app Editor
        edit_w = QWidget()
        e_lay = QVBoxLayout(edit_w)
        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("Template Preset:"))
        self.template_combo = QComboBox()
        self.template_combo.addItem("-- Select Template --")
        for t_name in self.TEMPLATES:
            self.template_combo.addItem(t_name)
        self.template_combo.currentTextChanged.connect(self._apply_template)
        t_row.addWidget(self.template_combo, 1)
        e_lay.addLayout(t_row)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        self.editor.setPlaceholderText("# Paste or type Dockerfile instructions here...\nFROM alpine:latest\nCMD [\"echo\", \"hello\"]")
        self.editor.setStyleSheet(f"background-color: {theme.get('panel', '#181b20')}; color: #d1d5db;")
        e_lay.addWidget(self.editor, 1)
        self.src_tabs.addTab(edit_w, "📝 Write / Paste Dockerfile")

        self.src_tabs.currentChanged.connect(self._update_preview)
        layout.addWidget(self.src_tabs, 1)

        # Form fields: Tag, Context, Target, Build Args
        form = QFormLayout()
        form.setSpacing(6)

        self.tag_in = QLineEdit()
        self.tag_in.setPlaceholderText("e.g. my-app:latest (required)")
        self.tag_in.textChanged.connect(self._update_preview)
        form.addRow("Image Tag (-t) *:", self.tag_in)

        self.context_in = QLineEdit(".")
        self.context_in.setPlaceholderText("e.g. . or /path/to/project_dir")
        self.context_in.textChanged.connect(self._update_preview)
        form.addRow("Build Context Dir:", self.context_in)

        self.target_in = QLineEdit()
        self.target_in.setPlaceholderText("e.g. builder, runner (optional multi-stage)")
        self.target_in.textChanged.connect(self._update_preview)
        form.addRow("Target Stage (--target):", self.target_in)

        self.args_in = QLineEdit()
        self.args_in.setPlaceholderText("e.g. APP_ENV=production, VERSION=1.0")
        self.args_in.textChanged.connect(self._update_preview)
        form.addRow("Build Args (--build-arg):", self.args_in)

        # Options checkboxes
        opts_box = QHBoxLayout()
        self.no_cache_chk = QCheckBox("No Cache (--no-cache)")
        self.no_cache_chk.toggled.connect(self._update_preview)
        opts_box.addWidget(self.no_cache_chk)

        self.pull_chk = QCheckBox("Always pull latest base images (--pull)")
        self.pull_chk.toggled.connect(self._update_preview)
        opts_box.addWidget(self.pull_chk)
        opts_box.addStretch()
        form.addRow("Options:", opts_box)

        layout.addLayout(form)

        # Command preview
        layout.addWidget(QLabel("<b>Command Preview:</b>"))
        self.preview_box = QLineEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setStyleSheet(f"background-color:{theme.get('panel', '#222')}; font-family:monospace; color:#3daee9;")
        layout.addWidget(self.preview_box)

        # Bottom buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self.btn_build = QPushButton("🔨 Build Image")
        self.btn_build.setStyleSheet(f"background-color:{theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold; padding:6px 16px;")
        self.btn_build.clicked.connect(self._on_submit)
        btn_box.addWidget(self.btn_build)
        layout.addLayout(btn_box)

        self._update_preview()

    def _apply_template(self, t_name: str):
        if t_name in self.TEMPLATES:
            self.editor.setPlainText(self.TEMPLATES[t_name])
            if not self.tag_in.text():
                short = t_name.split()[1].lower().replace(".", "").replace("/", "")
                self.tag_in.setText(f"{short}-app:latest")
            self._update_preview()

    def _update_preview(self):
        eng = self.eng_combo.currentText()
        tag = self.tag_in.text().strip() or "<image:tag>"
        ctx = self.context_in.text().strip() or "."
        df = self.dockerfile_path_in.text().strip() or "Dockerfile"
        if self.src_tabs.currentIndex() == 1:
            df = "Dockerfile"

        cmd = dockerfile_build_cmd(
            engine=eng,
            tag=tag,
            dockerfile=df,
            context_dir=ctx,
            build_args=self.args_in.text().strip(),
            target=self.target_in.text().strip(),
            no_cache=self.no_cache_chk.isChecked(),
            pull=self.pull_chk.isChecked(),
        )
        self.preview_box.setText(cmd)

    def _on_submit(self):
        if not self.tag_in.text().strip():
            QMessageBox.warning(self, "Validation Error", "Please provide a target image tag (e.g. my-app:latest).")
            return
        if self.src_tabs.currentIndex() == 1 and not self.editor.toPlainText().strip():
            QMessageBox.warning(self, "Validation Error", "Please provide Dockerfile content in the editor.")
            return
        self.accept()

    def get_build_command(self) -> tuple[str, str, str, Optional[str]]:
        """Returns (engine, tag, command, optional_editor_content)."""
        eng = self.eng_combo.currentText()
        tag = self.tag_in.text().strip()
        editor_content = self.editor.toPlainText().strip() if self.src_tabs.currentIndex() == 1 else None
        cmd = self.preview_box.text()
        return eng, tag, cmd, editor_content


# ====================================================================
# 9. Container Console Dialog (Quick Exec)
# ====================================================================
class ContainerConsoleDialog(QDialog):
    """In-app interactive console to execute commands inside a running container."""

    def __init__(
        self,
        theme: dict[str, str],
        engine: str,
        container_id: str,
        container_name: str,
        run_cmd_fn: Optional[Callable] = None,
        on_open_full_terminal: Optional[Callable[[str, str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.engine = engine
        self.container_id = container_id
        self.container_name = container_name
        self.run_cmd_fn = run_cmd_fn
        self.on_open_full_terminal = on_open_full_terminal
        self.setWindowTitle(f"💻 Console: {container_name or container_id[:12]} ({engine})")
        self.resize(800, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header bar
        top_bar = QHBoxLayout()
        info_lbl = QLabel(
            f"<b>Container:</b> {container_name} "
            f"<span style='color:#888;'>({container_id[:12]})</span> | "
            f"<b>Engine:</b> {engine.upper()}"
        )
        info_lbl.setStyleSheet(f"color: {theme.get('fg', '#ffffff')};")
        top_bar.addWidget(info_lbl)
        top_bar.addStretch()

        top_bar.addWidget(QLabel("User:"))
        self.user_in = QLineEdit("root")
        self.user_in.setFixedWidth(70)
        top_bar.addWidget(self.user_in)

        if on_open_full_terminal:
            btn_term = QPushButton("💻 Open in Terminal Tab")
            btn_term.setToolTip("Open full interactive PTY session in a new terminal tab")
            btn_term.setStyleSheet(f"background-color: {theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold; padding:2px 8px;")
            btn_term.clicked.connect(self._launch_terminal_tab)
            top_bar.addWidget(btn_term)

        layout.addLayout(top_bar)

        # Quick command shortcuts
        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("<small>Quick Actions:</small>"))
        shortcuts = ["uname -a", "ps aux", "df -h", "ip addr", "env", "top -b -n 1", "cat /etc/os-release"]
        for sc in shortcuts:
            btn_sc = QPushButton(sc)
            btn_sc.setStyleSheet("font-size: 11px; padding: 2px 5px;")
            btn_sc.clicked.connect(lambda _, cmd=sc: self._run_command(cmd))
            q_row.addWidget(btn_sc)
        q_row.addStretch()
        layout.addLayout(q_row)

        # Output console
        self.console_out = QPlainTextEdit()
        self.console_out.setReadOnly(True)
        self.console_out.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        self.console_out.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {theme.get('panel', '#181b20')};
                color: #d1d5db;
                border: 1px solid {theme.get('border', '#333')};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.console_out, 1)

        # Command input bar
        cmd_box = QHBoxLayout()
        cmd_box.addWidget(QLabel("<b>$</b>"))
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Enter command to execute inside container (e.g. ls -la, cat /etc/hosts)...")
        self.cmd_input.returnPressed.connect(self._on_enter_command)
        cmd_box.addWidget(self.cmd_input, 1)

        self.btn_run = QPushButton("⚡ Execute")
        self.btn_run.clicked.connect(self._on_enter_command)
        cmd_box.addWidget(self.btn_run)

        btn_clear = QPushButton("🧹 Clear")
        btn_clear.clicked.connect(self.console_out.clear)
        cmd_box.addWidget(btn_clear)
        layout.addLayout(cmd_box)

        # Status
        self.status_lbl = QLabel("Ready. Type a command or click a quick action above.")
        self.status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_lbl)

    def _on_enter_command(self):
        c = self.cmd_input.text().strip()
        if c:
            self._run_command(c)
            self.cmd_input.clear()

    def _run_command(self, cmd_str: str):
        if not self.run_cmd_fn:
            self.console_out.appendPlainText("Command runner unavailable.")
            return

        usr = self.user_in.text().strip()
        exec_cmd = container_exec_noninteractive_cmd(self.engine, self.container_id, cmd_str, user=usr)
        self.console_out.appendPlainText(f"\n$ {cmd_str}\n" + "─" * 40)
        self.status_lbl.setText(f"Executing: {cmd_str}...")

        def on_done(out: str, rc: int):
            self.console_out.appendPlainText(out.strip() if out.strip() else "[Command produced no output]")
            self.console_out.verticalScrollBar().setValue(self.console_out.verticalScrollBar().maximum())
            self.status_lbl.setText(f"Exit code: {rc}")

        self.run_cmd_fn(exec_cmd, f"Exec in {self.container_name}: {cmd_str}", timeout=60, callback=on_done, refresh_after=False)

    def _launch_terminal_tab(self):
        if self.on_open_full_terminal:
            self.on_open_full_terminal(self.container_id, self.container_name)
            self.accept()


# ====================================================================
# 10. Container SSH Connect Dialog
# ====================================================================
class ContainerSSHDialog(QDialog):
    """Dialog to connect SSH into a container or launch an SSH terminal."""

    def __init__(
        self,
        theme: dict[str, str],
        engine: str,
        container_id: str,
        container_name: str,
        ports_str: str = "",
        on_connect_ssh: Optional[Callable[[str, int, str], None]] = None,
        on_fallback_shell: Optional[Callable[[str, str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.engine = engine
        self.container_id = container_id
        self.container_name = container_name
        self.on_connect_ssh = on_connect_ssh
        self.on_fallback_shell = on_fallback_shell
        self.setWindowTitle(f"🔑 SSH Connect: {container_name or container_id[:12]}")
        self.resize(500, 280)

        # Extract mapped port for 22 if present
        detected_port = 22
        m = re.search(r':(\d+)->22(?:/tcp)?', ports_str)
        if m:
            detected_port = int(m.group(1))

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info_box = QFrame()
        info_box.setStyleSheet(f"background-color:{theme.get('panel', '#222')}; border-radius:4px; padding:6px;")
        i_lay = QVBoxLayout(info_box)
        i_lay.addWidget(QLabel(f"<b>Container:</b> {container_name} ({container_id[:12]})"))
        if m:
            i_lay.addWidget(QLabel(f"✅ <b>SSH Port Detected:</b> Host port <code>{detected_port}</code> is mapped to container port 22."))
        else:
            i_lay.addWidget(QLabel("ℹ️ Port 22 mapping was not detected. Enter custom port or use Direct Shell."))
        layout.addWidget(info_box)

        form = QFormLayout()
        form.setSpacing(8)

        self.host_in = QLineEdit("127.0.0.1")
        form.addRow("Target Host / IP:", self.host_in)

        self.port_in = QLineEdit(str(detected_port))
        form.addRow("SSH Port:", self.port_in)

        self.user_in = QLineEdit("root")
        form.addRow("SSH Username:", self.user_in)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        if on_fallback_shell:
            btn_sh = QPushButton("💻 Direct Console / Shell")
            btn_sh.setToolTip("Connect to container shell via exec without SSH")
            btn_sh.clicked.connect(self._fallback)
            btn_box.addWidget(btn_sh)

        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_connect = QPushButton("🔑 Connect SSH Terminal")
        btn_connect.setStyleSheet(f"background-color:{theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold; padding:6px 14px;")
        btn_connect.clicked.connect(self._connect)
        btn_box.addWidget(btn_connect)

        layout.addLayout(btn_box)

    def _connect(self):
        host = self.host_in.text().strip() or "127.0.0.1"
        try:
            port = int(self.port_in.text().strip() or 22)
        except ValueError:
            port = 22
        user = self.user_in.text().strip() or "root"
        if self.on_connect_ssh:
            self.on_connect_ssh(host, port, user)
        self.accept()

    def _fallback(self):
        if self.on_fallback_shell:
            self.on_fallback_shell(self.container_id, self.container_name)
        self.accept()


# ====================================================================
# 11. SysAdmin Containers Widget (Complete Manager with Compose & Console)
# ====================================================================
class SysAdminContainersWidget(QWidget):
    """Full-featured Docker & Podman Container Manager.
    Features:
    - Multi-engine detection (Docker, Podman, and Compose tools)
    - Full container lifecycle (start/stop/restart/delete)
    - Console & Interactive Shell (spawns in terminal tab or in-app console)
    - SSH connection detection and launcher
    - Dockerfile image builder with templates
    - docker-compose & podman-compose project manager with stack templates
    - Images, Volumes, and Networks management
    """

    COMPOSE_TEMPLATES = {
        "🌐 LEMP Stack (Nginx + PHP + MariaDB)": (
            "version: '3.8'\n\n"
            "services:\n"
            "  web:\n"
            "    image: nginx:alpine\n"
            "    restart: unless-stopped\n"
            '    ports:\n      - "80:80"\n'
            "    volumes:\n      - ./html:/usr/share/nginx/html:ro\n"
            "    depends_on:\n      - db\n\n"
            "  db:\n"
            "    image: mariadb:10.11\n"
            "    restart: unless-stopped\n"
            "    environment:\n"
            "      MYSQL_ROOT_PASSWORD: secret_root_pass\n"
            "      MYSQL_DATABASE: app_db\n"
            "      MYSQL_USER: app_user\n"
            "      MYSQL_PASSWORD: app_password\n"
            "    volumes:\n      - db_data:/var/lib/mysql\n\n"
            "volumes:\n  db_data:\n"
        ),
        "🐍 Python FastAPI + PostgreSQL + Redis": (
            "version: '3.8'\n\n"
            "services:\n"
            "  api:\n"
            "    image: python:3.12-slim\n"
            '    command: sh -c "pip install fastapi uvicorn redis psycopg2-binary && uvicorn main:app --host 0.0.0.0 --port 8000"\n'
            '    ports:\n      - "8000:8000"\n'
            "    environment:\n"
            "      DATABASE_URL: postgresql://postgres:postgres@db:5432/app\n"
            "      REDIS_URL: redis://redis:6379/0\n"
            "    depends_on:\n      - db\n      - redis\n\n"
            "  db:\n"
            "    image: postgres:16-alpine\n"
            "    restart: unless-stopped\n"
            "    environment:\n"
            "      POSTGRES_PASSWORD: postgres\n"
            "      POSTGRES_DB: app\n"
            "    volumes:\n      - pg_data:/var/lib/postgresql/data\n\n"
            "  redis:\n"
            "    image: redis:7-alpine\n"
            "    restart: unless-stopped\n\n"
            "volumes:\n  pg_data:\n"
        ),
        "⚡ Node.js Express + MongoDB": (
            "version: '3.8'\n\n"
            "services:\n"
            "  app:\n"
            "    image: node:20-alpine\n"
            "    working_dir: /app\n"
            '    ports:\n      - "3000:3000"\n'
            "    environment:\n"
            "      MONGO_URI: mongodb://mongo:27017/myapp\n"
            "    depends_on:\n      - mongo\n\n"
            "  mongo:\n"
            "    image: mongo:7.0\n"
            "    restart: unless-stopped\n"
            "    volumes:\n      - mongo_data:/data/db\n\n"
            "volumes:\n  mongo_data:\n"
        ),
        "🛡️ Prometheus + Grafana Monitoring": (
            "version: '3.8'\n\n"
            "services:\n"
            "  prometheus:\n"
            "    image: prom/prometheus:latest\n"
            '    ports:\n      - "9090:9090"\n'
            "    volumes:\n      - prom_data:/prometheus\n\n"
            "  grafana:\n"
            "    image: grafana/grafana:latest\n"
            '    ports:\n      - "3000:3000"\n'
            "    environment:\n"
            "      GF_SECURITY_ADMIN_PASSWORD: admin\n"
            "    volumes:\n      - grafana_data:/var/lib/grafana\n"
            "    depends_on:\n      - prometheus\n\n"
            "volumes:\n  prom_data:\n  grafana_data:\n"
        ),
        "💾 Redis Cache": (
            "version: '3.8'\n\n"
            "services:\n"
            "  redis:\n"
            "    image: redis:7-alpine\n"
            "    restart: unless-stopped\n"
            '    ports:\n      - "6379:6379"\n'
            "    volumes:\n      - redis_data:/data\n\n"
            "volumes:\n  redis_data:\n"
        ),
    }

    def __init__(self, theme: dict[str, str], run_cmd_fn: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.run_cmd_fn = run_cmd_fn

        self._engines: dict[str, dict[str, str]] = {
            "docker": {"status": "not_detected", "version": ""},
            "podman": {"status": "not_detected", "version": ""},
        }
        self._compose_tools: dict[str, str] = {}
        self._all_containers: list[dict[str, Any]] = []
        self._stats: dict[str, dict[str, str]] = {}
        self._all_images: list[dict[str, Any]] = []
        self._all_volumes: list[dict[str, Any]] = []
        self._all_networks: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # -------------------------------------------------------------
        # 1. Header Bar: Engine detection card, KPIs, and Main Actions
        # -------------------------------------------------------------
        header_frame = QFrame()
        header_frame.setStyleSheet(
            f"background-color: {theme.get('panel', '#181b20')}; "
            f"border: 1px solid {theme.get('border', '#2c313a')}; "
            "border-radius: 6px; padding: 4px 8px;"
        )
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(6, 4, 6, 4)
        h_layout.setSpacing(10)

        # Engine Status Info
        self.engine_status_lbl = QLabel("🐳 <b>Containers Engine:</b> Detecting...")
        self.engine_status_lbl.setStyleSheet(f"color: {theme.get('fg', '#fff')}; font-size: 13px;")
        h_layout.addWidget(self.engine_status_lbl)

        # Engine Selector Dropdown
        self.engine_filter_combo = QComboBox()
        self.engine_filter_combo.addItems(["All Engines", "🐳 Docker", "🦭 Podman"])
        self.engine_filter_combo.currentIndexChanged.connect(self._apply_all_filters)
        h_layout.addWidget(self.engine_filter_combo)

        h_layout.addStretch()

        # KPI Badges
        self.kpi_running = QLabel("🟢 0 Running")
        self.kpi_running.setStyleSheet(
            "color: #44eb74; font-weight: bold; background: rgba(68,235,116,0.1); padding: 3px 8px; border-radius: 4px;"
        )
        h_layout.addWidget(self.kpi_running)

        self.kpi_stopped = QLabel("⚪ 0 Stopped")
        self.kpi_stopped.setStyleSheet(
            "color: #888888; font-weight: bold; background: rgba(136,136,136,0.1); padding: 3px 8px; border-radius: 4px;"
        )
        h_layout.addWidget(self.kpi_stopped)

        self.kpi_images = QLabel("📦 0 Images")
        self.kpi_images.setStyleSheet(
            "color: #3daee9; font-weight: bold; background: rgba(61,174,233,0.1); padding: 3px 8px; border-radius: 4px;"
        )
        h_layout.addWidget(self.kpi_images)

        self.kpi_volumes = QLabel("💾 0 Vols")
        self.kpi_volumes.setStyleSheet(
            "color: #e5c07b; font-weight: bold; background: rgba(229,192,123,0.1); padding: 3px 8px; border-radius: 4px;"
        )
        h_layout.addWidget(self.kpi_volumes)

        self.kpi_networks = QLabel("🌐 0 Nets")
        self.kpi_networks.setStyleSheet(
            "color: #c678dd; font-weight: bold; background: rgba(198,120,221,0.1); padding: 3px 8px; border-radius: 4px;"
        )
        h_layout.addWidget(self.kpi_networks)

        # Quick Actions
        btn_df = QPushButton("🔨 Build Dockerfile")
        btn_df.setToolTip("Build image from Dockerfile or Containerfile")
        btn_df.clicked.connect(self._open_dockerfile_build_dialog)
        h_layout.addWidget(btn_df)

        btn_run = QPushButton("➕ Run Container")
        btn_run.setToolTip("Create and run a new container")
        btn_run.setStyleSheet(
            f"background-color: {theme.get('accent', '#3daee9')}; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px;"
        )
        btn_run.clicked.connect(lambda: self._open_run_dialog())
        h_layout.addWidget(btn_run)

        btn_pull = QPushButton("📥 Pull Image")
        btn_pull.setToolTip("Pull an image from container registry")
        btn_pull.clicked.connect(self._open_pull_dialog)
        h_layout.addWidget(btn_pull)

        layout.addWidget(header_frame)

        # -------------------------------------------------------------
        # 2. Offline / Help Banner (hidden when engines active)
        # -------------------------------------------------------------
        self.help_banner = QFrame()
        self.help_banner.setStyleSheet(
            "background-color: #3b2d18; border: 1px solid #7a5c20; border-radius: 5px; padding: 6px;"
        )
        help_lay = QHBoxLayout(self.help_banner)
        help_lay.setContentsMargins(6, 4, 6, 4)
        self.help_msg = QLabel("⚠️ <b>No active container engine detected or daemon is offline.</b>")
        self.help_msg.setStyleSheet("color: #f6c177;")
        help_lay.addWidget(self.help_msg)
        help_lay.addStretch()
        btn_help = QPushButton("💡 Show Setup Help")
        btn_help.setStyleSheet("background: #5c441b; color: #fff; font-weight: bold; padding: 2px 8px;")
        btn_help.clicked.connect(self._show_setup_help)
        help_lay.addWidget(btn_help)
        layout.addWidget(self.help_banner)
        self.help_banner.setVisible(False)

        # -------------------------------------------------------------
        # 3. Tabs: Containers, Images, Volumes, Networks, Compose
        # -------------------------------------------------------------
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Tab 1: Containers
        self.tab_containers = QWidget()
        self._init_containers_tab()
        self.tabs.addTab(self.tab_containers, "🐳 Containers (0)")

        # Tab 2: Images
        self.tab_images = QWidget()
        self._init_images_tab()
        self.tabs.addTab(self.tab_images, "📦 Images (0)")

        # Tab 3: Volumes
        self.tab_volumes = QWidget()
        self._init_volumes_tab()
        self.tabs.addTab(self.tab_volumes, "💾 Volumes (0)")

        # Tab 4: Networks
        self.tab_networks = QWidget()
        self._init_networks_tab()
        self.tabs.addTab(self.tab_networks, "🌐 Networks (0)")

        # Tab 5: Compose
        self.tab_compose = QWidget()
        self._init_compose_tab()
        self.tabs.addTab(self.tab_compose, "🐙 Compose")

    # -----------------------------------------------------------------
    # Tab Initializers
    # -----------------------------------------------------------------
    def _init_containers_tab(self):
        c_lay = QVBoxLayout(self.tab_containers)
        c_lay.setContentsMargins(2, 6, 2, 2)
        c_lay.setSpacing(6)

        # Filter bar
        fb = QHBoxLayout()
        self.c_search = QLineEdit()
        self.c_search.setPlaceholderText("🔍 Filter containers by name, image, port, ID...")
        self.c_search.textChanged.connect(self._render_containers)
        fb.addWidget(self.c_search, 1)

        self.c_state_combo = QComboBox()
        self.c_state_combo.addItems(["All States", "🟢 Running Only", "⚪ Stopped Only", "🟡 Paused / Other"])
        self.c_state_combo.currentIndexChanged.connect(self._render_containers)
        fb.addWidget(self.c_state_combo)

        btn_start_all = QPushButton("▶ Start Stopped")
        btn_start_all.clicked.connect(self._start_all_stopped)
        fb.addWidget(btn_start_all)

        btn_stop_all = QPushButton("⏹ Stop Running")
        btn_stop_all.clicked.connect(self._stop_all_running)
        fb.addWidget(btn_stop_all)

        btn_prune_c = QPushButton("🧹 Prune Stopped")
        btn_prune_c.clicked.connect(self._prune_containers)
        fb.addWidget(btn_prune_c)

        c_lay.addLayout(fb)

        # Table
        self.c_table = QTableWidget(0, 9)
        self.c_table.setHorizontalHeaderLabels([
            "State", "Container Name", "Image", "Container ID", "Port Mappings", "CPU %", "Memory", "Engine", "Actions"
        ])
        self.c_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.c_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.c_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.c_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self.c_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.c_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.c_table.customContextMenuRequested.connect(self._show_container_context_menu)
        self.c_table.cellDoubleClicked.connect(self._on_container_double_clicked)
        c_lay.addWidget(self.c_table, 1)

    def _init_images_tab(self):
        img_lay = QVBoxLayout(self.tab_images)
        img_lay.setContentsMargins(2, 6, 2, 2)
        img_lay.setSpacing(6)

        # Filter bar
        fb = QHBoxLayout()
        self.img_search = QLineEdit()
        self.img_search.setPlaceholderText("🔍 Filter images by repository, tag, or ID...")
        self.img_search.textChanged.connect(self._render_images)
        fb.addWidget(self.img_search, 1)

        btn_df = QPushButton("🔨 Build Dockerfile")
        btn_df.clicked.connect(self._open_dockerfile_build_dialog)
        fb.addWidget(btn_df)

        btn_pull = QPushButton("📥 Pull Image")
        btn_pull.clicked.connect(self._open_pull_dialog)
        fb.addWidget(btn_pull)

        btn_search = QPushButton("🔍 Search Registry")
        btn_search.clicked.connect(self._open_search_dialog)
        fb.addWidget(btn_search)

        btn_prune = QPushButton("🧹 Prune Images")
        btn_prune.clicked.connect(self._prune_images)
        fb.addWidget(btn_prune)

        img_lay.addLayout(fb)

        # Table
        self.img_table = QTableWidget(0, 7)
        self.img_table.setHorizontalHeaderLabels([
            "Repository", "Tag", "Image ID", "Size", "Created", "Engine", "Actions"
        ])
        self.img_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.img_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.img_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.img_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.img_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.img_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.img_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.img_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.img_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.img_table.customContextMenuRequested.connect(self._show_image_context_menu)
        self.img_table.cellDoubleClicked.connect(self._on_image_double_clicked)
        img_lay.addWidget(self.img_table, 1)

    def _init_volumes_tab(self):
        v_lay = QVBoxLayout(self.tab_volumes)
        v_lay.setContentsMargins(2, 6, 2, 2)
        v_lay.setSpacing(6)

        # Filter bar
        fb = QHBoxLayout()
        self.vol_search = QLineEdit()
        self.vol_search.setPlaceholderText("🔍 Filter volumes by name or driver...")
        self.vol_search.textChanged.connect(self._render_volumes)
        fb.addWidget(self.vol_search, 1)

        btn_create = QPushButton("➕ Create Volume")
        btn_create.clicked.connect(self._open_create_volume)
        fb.addWidget(btn_create)

        btn_prune = QPushButton("🧹 Prune Volumes")
        btn_prune.clicked.connect(self._prune_volumes)
        fb.addWidget(btn_prune)

        v_lay.addLayout(fb)

        # Table
        self.vol_table = QTableWidget(0, 5)
        self.vol_table.setHorizontalHeaderLabels(["Volume Name", "Driver", "Scope", "Engine", "Actions"])
        self.vol_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.vol_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.vol_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.vol_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.vol_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.vol_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.vol_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.vol_table.customContextMenuRequested.connect(self._show_volume_context_menu)
        v_lay.addWidget(self.vol_table, 1)

    def _init_networks_tab(self):
        n_lay = QVBoxLayout(self.tab_networks)
        n_lay.setContentsMargins(2, 6, 2, 2)
        n_lay.setSpacing(6)

        # Filter bar
        fb = QHBoxLayout()
        self.net_search = QLineEdit()
        self.net_search.setPlaceholderText("🔍 Filter networks by name or driver...")
        self.net_search.textChanged.connect(self._render_networks)
        fb.addWidget(self.net_search, 1)

        btn_create = QPushButton("➕ Create Network")
        btn_create.clicked.connect(self._open_create_network)
        fb.addWidget(btn_create)

        btn_prune = QPushButton("🧹 Prune Networks")
        btn_prune.clicked.connect(self._prune_networks)
        fb.addWidget(btn_prune)

        n_lay.addLayout(fb)

        # Table
        self.net_table = QTableWidget(0, 6)
        self.net_table.setHorizontalHeaderLabels(["Network ID", "Name", "Driver", "Scope", "Engine", "Actions"])
        self.net_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.net_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.net_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.net_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.net_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.net_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.net_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.net_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.net_table.customContextMenuRequested.connect(self._show_network_context_menu)
        n_lay.addWidget(self.net_table, 1)

    def _init_compose_tab(self):
        comp_lay = QVBoxLayout(self.tab_compose)
        comp_lay.setContentsMargins(2, 6, 2, 2)
        comp_lay.setSpacing(6)

        # Top Project Config bar
        top_box = QHBoxLayout()

        top_box.addWidget(QLabel("<b>Compose Tool:</b>"))
        self.compose_tool_combo = QComboBox()
        self.compose_tool_combo.addItems(["docker compose", "docker-compose", "podman-compose"])
        top_box.addWidget(self.compose_tool_combo)

        top_box.addWidget(QLabel("Project Dir:"))
        self.compose_dir_in = QLineEdit(".")
        self.compose_dir_in.setPlaceholderText("Directory with docker-compose.yml")
        top_box.addWidget(self.compose_dir_in, 1)

        top_box.addWidget(QLabel("Project Name:"))
        self.compose_proj_in = QLineEdit()
        self.compose_proj_in.setPlaceholderText("Optional (-p)")
        top_box.addWidget(self.compose_proj_in)

        top_box.addWidget(QLabel("Template:"))
        self.compose_template_combo = QComboBox()
        self.compose_template_combo.addItem("-- Select Template --")
        for t_name in self.COMPOSE_TEMPLATES:
            self.compose_template_combo.addItem(t_name)
        self.compose_template_combo.currentTextChanged.connect(self._on_compose_template_selected)
        top_box.addWidget(self.compose_template_combo, 1)

        comp_lay.addLayout(top_box)

        # Splitter: Left editor, Right controls + output
        split = QSplitter(Qt.Orientation.Horizontal)

        # Left: Editor
        left_w = QWidget()
        l_lay = QVBoxLayout(left_w)
        l_lay.setContentsMargins(0, 0, 0, 0)
        l_lay.addWidget(QLabel("<b>docker-compose.yml Configuration:</b>"))

        self.compose_editor = QPlainTextEdit()
        self.compose_editor.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        self.compose_editor.setPlaceholderText("# Write or paste your docker-compose.yml here, or select a template above...")
        self.compose_editor.setStyleSheet(
            f"background-color: {self.theme.get('panel', '#181b20')}; color: #d1d5db; border: 1px solid {self.theme.get('border', '#333')}; border-radius: 4px;"
        )
        l_lay.addWidget(self.compose_editor, 1)

        # Editor buttons
        ed_btns = QHBoxLayout()
        btn_save = QPushButton("💾 Save to File...")
        btn_save.clicked.connect(self._save_compose_file)
        ed_btns.addWidget(btn_save)
        ed_btns.addStretch()
        l_lay.addLayout(ed_btns)

        split.addWidget(left_w)

        # Right: Operations & Output
        right_w = QWidget()
        r_lay = QVBoxLayout(right_w)
        r_lay.setContentsMargins(0, 0, 0, 0)

        # Compose actions toolbar
        ops_grid = QGridLayout()
        ops_grid.setSpacing(4)

        btn_up = QPushButton("🚀 Compose Up (-d)")
        btn_up.setStyleSheet(f"background-color: {self.theme.get('accent', '#3daee9')}; color: white; font-weight: bold; padding: 4px 8px;")
        btn_up.clicked.connect(self._compose_up)
        ops_grid.addWidget(btn_up, 0, 0)

        btn_down = QPushButton("⏹ Compose Down")
        btn_down.clicked.connect(self._compose_down)
        ops_grid.addWidget(btn_down, 0, 1)

        btn_restart = QPushButton("🔄 Restart Stack")
        btn_restart.clicked.connect(self._compose_restart)
        ops_grid.addWidget(btn_restart, 0, 2)

        btn_logs = QPushButton("📜 View Logs")
        btn_logs.clicked.connect(self._compose_logs)
        ops_grid.addWidget(btn_logs, 1, 0)

        btn_ps = QPushButton("🔍 Services (ps)")
        btn_ps.clicked.connect(self._compose_ps)
        ops_grid.addWidget(btn_ps, 1, 1)

        btn_build = QPushButton("🔨 Build Services")
        btn_build.clicked.connect(self._compose_build)
        ops_grid.addWidget(btn_build, 1, 2)

        btn_vdown = QPushButton("🧹 Down with Volumes (-v)")
        btn_vdown.setStyleSheet("color: #ff5555;")
        btn_vdown.clicked.connect(lambda: self._compose_down(remove_volumes=True))
        ops_grid.addWidget(btn_vdown, 2, 0, 1, 3)

        r_lay.addLayout(ops_grid)

        r_lay.addWidget(QLabel("<b>Compose Execution Output:</b>"))
        self.compose_output = QPlainTextEdit()
        self.compose_output.setReadOnly(True)
        self.compose_output.setFont(QFont("JetBrains Mono, Consolas, Courier", 10))
        self.compose_output.setStyleSheet(
            f"background-color: {self.theme.get('panel', '#181b20')}; color: #d1d5db; border: 1px solid {self.theme.get('border', '#333')}; border-radius: 4px;"
        )
        r_lay.addWidget(self.compose_output, 1)

        split.addWidget(right_w)
        split.setSizes([450, 450])
        comp_lay.addWidget(split, 1)

    # -----------------------------------------------------------------
    # Data Parsing
    # -----------------------------------------------------------------
    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = ""
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # Fallback if no section headers found
        if not sections:
            self._parse_legacy(raw)
            return

        # 1. Parse Engines
        d_status = "not_installed"
        d_ver = ""
        p_status = "not_installed"
        p_ver = ""
        for line in sections.get("CONTAINER ENGINES", []):
            if line.startswith("docker|"):
                p = line.split("|", 2)
                d_status = p[1] if len(p) > 1 else "unknown"
                d_ver = p[2] if len(p) > 2 else ""
            elif line.startswith("podman|"):
                p = line.split("|", 2)
                p_status = p[1] if len(p) > 1 else "unknown"
                p_ver = p[2] if len(p) > 2 else ""

        self._engines["docker"] = {"status": d_status, "version": d_ver}
        self._engines["podman"] = {"status": p_status, "version": p_ver}

        # Update Engine label
        active_parts = []
        if d_status == "active":
            active_parts.append("🐳 <b>Docker:</b> Active")
        elif d_status == "daemon_offline":
            active_parts.append("🐳 <b>Docker:</b> Daemon Offline / Permission Denied")

        if p_status == "active":
            active_parts.append("🦭 <b>Podman:</b> Active")
        elif p_status == "daemon_offline":
            active_parts.append("🦭 <b>Podman:</b> Inactive")

        # Parse Compose Tools
        self._compose_tools.clear()
        for line in sections.get("COMPOSE TOOLS", []):
            if "docker_compose_plugin|" in line:
                self._compose_tools["docker_compose_plugin"] = line.split("|", 1)[1]
            elif "docker_compose_standalone|" in line:
                self._compose_tools["docker_compose_standalone"] = line.split("|", 1)[1]
            elif "podman_compose|" in line:
                self._compose_tools["podman_compose"] = line.split("|", 1)[1]

        if "docker_compose_plugin" in self._compose_tools:
            active_parts.append("🐙 <b>Docker Compose:</b> Active")
        elif "podman_compose" in self._compose_tools:
            active_parts.append("🐙 <b>Podman Compose:</b> Active")

        if active_parts:
            self.engine_status_lbl.setText(" | ".join(active_parts))
            self.help_banner.setVisible(False)
        else:
            self.engine_status_lbl.setText("⚠️ <b>No Active Container Engine</b>")
            self.help_banner.setVisible(True)

        # 2. Parse Stats
        self._stats.clear()
        for eng in ("DOCKER", "PODMAN"):
            for line in sections.get(f"{eng} STATS", []):
                if not line or line.startswith("NAME") or line.startswith("CONTAINER"):
                    continue
                parts = line.split("|") if "|" in line else line.split("\t")
                if len(parts) >= 3:
                    name = parts[0].strip()
                    cpu = parts[1].strip()
                    mem = parts[2].strip()
                    net = parts[3].strip() if len(parts) > 3 else "-"
                    self._stats[name] = {"cpu": cpu, "mem": mem, "net": net}

        # 3. Parse Containers
        self._all_containers.clear()
        for eng, eng_name in (("DOCKER", "docker"), ("PODMAN", "podman")):
            for line in sections.get(f"{eng} CONTAINERS", []):
                if not line or line.startswith("==") or "NAMES" in line or "CONTAINER ID" in line:
                    continue
                parts = line.split("\t") if "\t" in line else line.split("|")
                if len(parts) >= 2:
                    cid = parts[0].strip()
                    name = parts[1].strip() if len(parts) > 1 else cid[:12]
                    image = parts[2].strip() if len(parts) > 2 else "-"
                    status = parts[3].strip() if len(parts) > 3 else "-"
                    ports = parts[4].strip() if len(parts) > 4 else "-"
                    created = parts[5].strip() if len(parts) > 5 else "-"

                    state = "other"
                    if "Up" in status:
                        state = "running"
                    elif "Exited" in status or "Created" in status:
                        state = "stopped"
                    elif "Paused" in status:
                        state = "paused"

                    stat_entry = self._stats.get(name, self._stats.get(cid, {}))
                    cpu = stat_entry.get("cpu", "-")
                    mem = stat_entry.get("mem", "-")

                    self._all_containers.append({
                        "id": cid,
                        "name": name,
                        "image": image,
                        "status": status,
                        "state": state,
                        "ports": ports,
                        "created": created,
                        "engine": eng_name,
                        "cpu": cpu,
                        "mem": mem,
                    })

        # 4. Parse Images
        self._all_images.clear()
        for eng, eng_name in (("DOCKER", "docker"), ("PODMAN", "podman")):
            for line in sections.get(f"{eng} IMAGES", []):
                if not line or line.startswith("==") or line.startswith("REPOSITORY"):
                    continue
                parts = line.split("\t") if "\t" in line else line.split("|")
                if len(parts) >= 3:
                    repo = parts[0].strip()
                    tag = parts[1].strip() if len(parts) > 1 else "latest"
                    img_id = parts[2].strip() if len(parts) > 2 else "-"
                    size = parts[3].strip() if len(parts) > 3 else "-"
                    created = parts[4].strip() if len(parts) > 4 else "-"
                    self._all_images.append({
                        "repo": repo,
                        "tag": tag,
                        "id": img_id,
                        "size": size,
                        "created": created,
                        "engine": eng_name,
                    })

        # 5. Parse Volumes
        self._all_volumes.clear()
        for eng, eng_name in (("DOCKER", "docker"), ("PODMAN", "podman")):
            for line in sections.get(f"{eng} VOLUMES", []):
                if not line or line.startswith("==") or line.startswith("DRIVER") or line.startswith("VOLUME NAME"):
                    continue
                parts = line.split("\t") if "\t" in line else line.split("|")
                if len(parts) >= 1 and parts[0].strip():
                    vname = parts[0].strip()
                    driver = parts[1].strip() if len(parts) > 1 else "local"
                    scope = parts[2].strip() if len(parts) > 2 else "local"
                    self._all_volumes.append({
                        "name": vname,
                        "driver": driver,
                        "scope": scope,
                        "engine": eng_name,
                    })

        # 6. Parse Networks
        self._all_networks.clear()
        for eng, eng_name in (("DOCKER", "docker"), ("PODMAN", "podman")):
            for line in sections.get(f"{eng} NETWORKS", []):
                if not line or line.startswith("==") or line.startswith("NETWORK ID") or line.startswith("NAME"):
                    continue
                parts = line.split("\t") if "\t" in line else line.split("|")
                if len(parts) >= 2:
                    nid = parts[0].strip()
                    nname = parts[1].strip()
                    driver = parts[2].strip() if len(parts) > 2 else "bridge"
                    scope = parts[3].strip() if len(parts) > 3 else "local"
                    self._all_networks.append({
                        "id": nid,
                        "name": nname,
                        "driver": driver,
                        "scope": scope,
                        "engine": eng_name,
                    })

        # Update KPIs
        n_running = sum(1 for c in self._all_containers if c["state"] == "running")
        n_stopped = sum(1 for c in self._all_containers if c["state"] == "stopped")
        self.kpi_running.setText(f"🟢 {n_running} Running")
        self.kpi_stopped.setText(f"⚪ {n_stopped} Stopped")
        self.kpi_images.setText(f"📦 {len(self._all_images)} Images")
        self.kpi_volumes.setText(f"💾 {len(self._all_volumes)} Vols")
        self.kpi_networks.setText(f"🌐 {len(self._all_networks)} Nets")

        self.tabs.setTabText(0, f"🐳 Containers ({len(self._all_containers)})")
        self.tabs.setTabText(1, f"📦 Images ({len(self._all_images)})")
        self.tabs.setTabText(2, f"💾 Volumes ({len(self._all_volumes)})")
        self.tabs.setTabText(3, f"🌐 Networks ({len(self._all_networks)})")

        self._apply_all_filters()

    def _parse_legacy(self, raw: str):
        """Fallback parser for legacy formatted text."""
        self._all_containers.clear()
        engine = "docker"
        for line in raw.splitlines():
            line_s = line.strip()
            if "PODMAN CONTAINERS" in line_s:
                engine = "podman"
                continue
            if not line_s or line_s.startswith("==") or "NAMES" in line_s:
                continue
            parts = line_s.split("\t") if "\t" in line_s else line_s.split(None, 2)
            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                ports = parts[2] if len(parts) > 2 else "-"
                state = "running" if "Up" in status else "stopped"
                self._all_containers.append({
                    "id": name[:12],
                    "name": name,
                    "image": "-",
                    "status": status,
                    "state": state,
                    "ports": ports,
                    "created": "-",
                    "engine": engine,
                    "cpu": "-",
                    "mem": "-",
                })
        self._apply_all_filters()

    # -----------------------------------------------------------------
    # Rendering & Filtering
    # -----------------------------------------------------------------
    def _apply_all_filters(self):
        self._render_containers()
        self._render_images()
        self._render_volumes()
        self._render_networks()

    def _get_target_engine(self) -> Optional[str]:
        idx = self.engine_filter_combo.currentIndex()
        if idx == 1:
            return "docker"
        elif idx == 2:
            return "podman"
        return None

    def _render_containers(self):
        eng_target = self._get_target_engine()
        state_idx = self.c_state_combo.currentIndex()
        q = self.c_search.text().strip().lower()

        filtered: list[dict[str, Any]] = []
        for c in self._all_containers:
            if eng_target and c["engine"] != eng_target:
                continue
            if state_idx == 1 and c["state"] != "running":
                continue
            elif state_idx == 2 and c["state"] != "stopped":
                continue
            elif state_idx == 3 and c["state"] in ("running", "stopped"):
                continue

            if q:
                haystack = f"{c['name']} {c['image']} {c['id']} {c['ports']} {c['status']}".lower()
                if q not in haystack:
                    continue
            filtered.append(c)

        self.c_table.setRowCount(len(filtered))
        for idx, c in enumerate(filtered):
            # 0: State
            st_text = "🟢 Up" if c["state"] == "running" else ("⚪ Exited" if c["state"] == "stopped" else "🟡 Paused")
            it_st = QTableWidgetItem(f"{st_text} ({c['status'][:20]})")
            it_st.setForeground(QColor("#44eb74" if c["state"] == "running" else ("#888" if c["state"] == "stopped" else "#e5c07b")))
            self.c_table.setItem(idx, 0, it_st)

            # 1: Name
            it_name = QTableWidgetItem(c["name"])
            it_name.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.c_table.setItem(idx, 1, it_name)

            # 2: Image
            it_img = QTableWidgetItem(c["image"])
            it_img.setForeground(QColor("#3daee9"))
            self.c_table.setItem(idx, 2, it_img)

            # 3: ID
            it_id = QTableWidgetItem(c["id"][:12])
            it_id.setFont(QFont("Monospace", 9))
            self.c_table.setItem(idx, 3, it_id)

            # 4: Ports
            self.c_table.setItem(idx, 4, QTableWidgetItem(c["ports"]))

            # 5: CPU %
            self.c_table.setItem(idx, 5, QTableWidgetItem(c.get("cpu", "-")))

            # 6: Memory
            self.c_table.setItem(idx, 6, QTableWidgetItem(c.get("mem", "-")))

            # 7: Engine
            it_eng = QTableWidgetItem("🐳 Docker" if c["engine"] == "docker" else "🦭 Podman")
            self.c_table.setItem(idx, 7, it_eng)

            # 8: Actions Widget
            act_w = QWidget()
            act_lay = QHBoxLayout(act_w)
            act_lay.setContentsMargins(2, 2, 2, 2)
            act_lay.setSpacing(4)

            cid = c["id"]
            cname = c["name"]
            ceng = c["engine"]
            cports = c["ports"]

            if c["state"] == "running":
                btn_stop = QPushButton("⏹ Stop")
                btn_stop.setStyleSheet("padding: 2px 6px; font-size: 11px;")
                btn_stop.clicked.connect(lambda _, e=ceng, i=cid, n=cname: self._stop_container(e, i, n))
                act_lay.addWidget(btn_stop)

                btn_restart = QPushButton("🔄 Restart")
                btn_restart.setStyleSheet("padding: 2px 6px; font-size: 11px;")
                btn_restart.clicked.connect(lambda _, e=ceng, i=cid, n=cname: self._restart_container(e, i, n))
                act_lay.addWidget(btn_restart)
            else:
                btn_start = QPushButton("▶ Start")
                btn_start.setStyleSheet("padding: 2px 6px; font-size: 11px; font-weight: bold; color: #44eb74;")
                btn_start.clicked.connect(lambda _, e=ceng, i=cid, n=cname: self._start_container(e, i, n))
                act_lay.addWidget(btn_start)

                btn_del = QPushButton("🗑️ Delete")
                btn_del.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #ff5555;")
                btn_del.clicked.connect(lambda _, e=ceng, i=cid, n=cname: self._delete_container(e, i, n))
                act_lay.addWidget(btn_del)

            # Console Button
            btn_console = QPushButton("💻 Console")
            btn_console.setToolTip("Open interactive console / shell in container")
            btn_console.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #3daee9;")
            btn_console.clicked.connect(lambda _, i=cid, n=cname: self._open_container_terminal(i, n))
            act_lay.addWidget(btn_console)

            # SSH Button
            btn_ssh = QPushButton("🔑 SSH")
            btn_ssh.setToolTip("Connect to container via SSH")
            btn_ssh.setStyleSheet("padding: 2px 5px; font-size: 11px;")
            btn_ssh.clicked.connect(lambda _, e=ceng, i=cid, n=cname, p=cports: self._open_container_ssh(e, i, n, p))
            act_lay.addWidget(btn_ssh)

            btn_logs = QPushButton("📜 Logs")
            btn_logs.setStyleSheet("padding: 2px 6px; font-size: 11px;")
            btn_logs.clicked.connect(lambda _, e=ceng, i=cid, n=cname: self._show_logs(e, i, n))
            act_lay.addWidget(btn_logs)

            self.c_table.setCellWidget(idx, 8, act_w)

    def _render_images(self):
        eng_target = self._get_target_engine()
        q = self.img_search.text().strip().lower()

        filtered: list[dict[str, Any]] = []
        for img in self._all_images:
            if eng_target and img["engine"] != eng_target:
                continue
            if q:
                haystack = f"{img['repo']} {img['tag']} {img['id']}".lower()
                if q not in haystack:
                    continue
            filtered.append(img)

        self.img_table.setRowCount(len(filtered))
        for idx, img in enumerate(filtered):
            # 0: Repo
            it_repo = QTableWidgetItem(img["repo"])
            it_repo.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.img_table.setItem(idx, 0, it_repo)

            # 1: Tag
            it_tag = QTableWidgetItem(img["tag"])
            it_tag.setForeground(QColor("#e5c07b"))
            self.img_table.setItem(idx, 1, it_tag)

            # 2: ID
            it_id = QTableWidgetItem(img["id"][:12])
            it_id.setFont(QFont("Monospace", 9))
            self.img_table.setItem(idx, 2, it_id)

            # 3: Size
            self.img_table.setItem(idx, 3, QTableWidgetItem(img["size"]))

            # 4: Created
            self.img_table.setItem(idx, 4, QTableWidgetItem(img["created"]))

            # 5: Engine
            it_eng = QTableWidgetItem("🐳 Docker" if img["engine"] == "docker" else "🦭 Podman")
            self.img_table.setItem(idx, 5, it_eng)

            # 6: Actions
            act_w = QWidget()
            act_lay = QHBoxLayout(act_w)
            act_lay.setContentsMargins(2, 2, 2, 2)
            act_lay.setSpacing(4)

            full_img = f"{img['repo']}:{img['tag']}"
            i_id = img["id"]
            i_eng = img["engine"]

            btn_run = QPushButton("🚀 Run")
            btn_run.setStyleSheet("padding: 2px 6px; font-size: 11px;")
            btn_run.clicked.connect(lambda _, im=full_img: self._open_run_dialog(prefill_image=im))
            act_lay.addWidget(btn_run)

            btn_rm = QPushButton("🗑️ Remove")
            btn_rm.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #ff5555;")
            btn_rm.clicked.connect(lambda _, e=i_eng, i=i_id, n=full_img: self._delete_image(e, i, n))
            act_lay.addWidget(btn_rm)

            self.img_table.setCellWidget(idx, 6, act_w)

    def _render_volumes(self):
        eng_target = self._get_target_engine()
        q = self.vol_search.text().strip().lower()

        filtered: list[dict[str, Any]] = []
        for v in self._all_volumes:
            if eng_target and v["engine"] != eng_target:
                continue
            if q and q not in f"{v['name']} {v['driver']}".lower():
                continue
            filtered.append(v)

        self.vol_table.setRowCount(len(filtered))
        for idx, v in enumerate(filtered):
            it_name = QTableWidgetItem(v["name"])
            it_name.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.vol_table.setItem(idx, 0, it_name)

            self.vol_table.setItem(idx, 1, QTableWidgetItem(v["driver"]))
            self.vol_table.setItem(idx, 2, QTableWidgetItem(v["scope"]))

            it_eng = QTableWidgetItem("🐳 Docker" if v["engine"] == "docker" else "🦭 Podman")
            self.vol_table.setItem(idx, 3, it_eng)

            # Action
            btn_del = QPushButton("🗑️ Delete")
            btn_del.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #ff5555;")
            btn_del.clicked.connect(lambda _, e=v["engine"], n=v["name"]: self._delete_volume(e, n))
            self.vol_table.setCellWidget(idx, 4, btn_del)

    def _render_networks(self):
        eng_target = self._get_target_engine()
        q = self.net_search.text().strip().lower()

        filtered: list[dict[str, Any]] = []
        for net in self._all_networks:
            if eng_target and net["engine"] != eng_target:
                continue
            if q and q not in f"{net['name']} {net['driver']} {net['id']}".lower():
                continue
            filtered.append(net)

        self.net_table.setRowCount(len(filtered))
        for idx, net in enumerate(filtered):
            it_id = QTableWidgetItem(net["id"][:12])
            it_id.setFont(QFont("Monospace", 9))
            self.net_table.setItem(idx, 0, it_id)

            it_name = QTableWidgetItem(net["name"])
            it_name.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.net_table.setItem(idx, 1, it_name)

            self.net_table.setItem(idx, 2, QTableWidgetItem(net["driver"]))
            self.net_table.setItem(idx, 3, QTableWidgetItem(net["scope"]))

            it_eng = QTableWidgetItem("🐳 Docker" if net["engine"] == "docker" else "🦭 Podman")
            self.net_table.setItem(idx, 4, it_eng)

            # Action
            btn_del = QPushButton("🗑️ Delete")
            btn_del.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #ff5555;")
            btn_del.clicked.connect(lambda _, e=net["engine"], n=net["name"]: self._delete_network(e, n))
            self.net_table.setCellWidget(idx, 5, btn_del)

    # -----------------------------------------------------------------
    # Container Operations
    # -----------------------------------------------------------------
    def _start_container(self, engine: str, cid: str, name: str):
        if not self.run_cmd_fn:
            return
        cmd = container_op_cmd(engine, "start", cid)
        self.run_cmd_fn(cmd, f"Start Container: {name}", timeout=60, refresh_after=True)

    def _stop_container(self, engine: str, cid: str, name: str):
        if not self.run_cmd_fn:
            return
        cmd = container_op_cmd(engine, "stop", cid)
        self.run_cmd_fn(cmd, f"Stop Container: {name}", timeout=60, refresh_after=True)

    def _restart_container(self, engine: str, cid: str, name: str):
        if not self.run_cmd_fn:
            return
        cmd = container_op_cmd(engine, "restart", cid)
        self.run_cmd_fn(cmd, f"Restart Container: {name}", timeout=60, refresh_after=True)

    def _delete_container(self, engine: str, cid: str, name: str, confirm: bool = True):
        if not self.run_cmd_fn:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Container Deletion",
                f"Are you sure you want to forcibly remove container '{name}' ({cid[:12]})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = container_rm_cmd(engine, cid, force=True)
        self.run_cmd_fn(cmd, f"Delete Container: {name}", timeout=60, refresh_after=True)

    def _start_all_stopped(self, confirm: bool = True):
        stopped = [c for c in self._all_containers if c["state"] == "stopped"]
        if not stopped:
            QMessageBox.information(self, "Start Containers", "No stopped containers found.")
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Start All Stopped Containers",
                f"Start all {len(stopped)} stopped container(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        for c in stopped:
            cmd = container_op_cmd(c["engine"], "start", c["id"])
            self.run_cmd_fn(cmd, f"Start Container: {c['name']}", timeout=60, refresh_after=False)
        self._refresh_data()

    def _stop_all_running(self, confirm: bool = True):
        running = [c for c in self._all_containers if c["state"] == "running"]
        if not running:
            QMessageBox.information(self, "Stop Containers", "No running containers found.")
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Stop All Running Containers",
                f"Stop all {len(running)} running container(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        for c in running:
            cmd = container_op_cmd(c["engine"], "stop", c["id"])
            self.run_cmd_fn(cmd, f"Stop Container: {c['name']}", timeout=60, refresh_after=False)
        self._refresh_data()

    def _prune_containers(self, confirm: bool = True):
        eng = self._get_target_engine() or "docker"
        if confirm:
            ans = QMessageBox.question(
                self,
                "Prune Stopped Containers",
                f"Prune all stopped {eng.capitalize()} containers?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = container_prune_cmd(eng)
        self.run_cmd_fn(cmd, f"Prune Stopped {eng.capitalize()} Containers", timeout=60, refresh_after=True)

    def _show_logs(self, engine: str, cid: str, name: str):
        dlg = ContainerLogsDialog(self.theme, engine, cid, name, run_cmd_fn=self.run_cmd_fn, parent=self)
        dlg.exec()

    def _inspect_container(self, engine: str, cid: str, name: str):
        dlg = ContainerInspectDialog(self.theme, engine, cid, name, run_cmd_fn=self.run_cmd_fn, parent=self)
        dlg.exec()

    # -----------------------------------------------------------------
    # Terminal, Console & SSH
    # -----------------------------------------------------------------
    def _open_container_terminal(self, cid: str, cname: str, shell: str = "/bin/sh", user: str = ""):
        """Launches interactive container shell in MainWindow terminal tab or in-app console."""
        c_entry = next((c for c in self._all_containers if c["id"] == cid or c["name"] == cname), None)
        engine = c_entry["engine"] if c_entry else "docker"

        exec_cmd = container_exec_interactive_cmd(engine, cid, shell=shell, user=user)

        w = self.window()
        parent_tab = self.parent()
        while parent_tab and not hasattr(parent_tab, "_is_remote"):
            parent_tab = parent_tab.parent()

        is_remote = parent_tab._is_remote() if parent_tab else False
        profile = getattr(parent_tab, "profile", None) if parent_tab else None

        if is_remote and profile and hasattr(w, "add_terminal_tab"):
            from admin_suite.ssh.credentials import SshCredentials
            creds = SshCredentials(password=profile.get("ssh_pass") or None, key_path=profile.get("ssh_key") or None)
            w.add_terminal_tab(
                name=f"{cname} ({engine})",
                host=profile.get("ssh_host", ""),
                port=int(profile.get("ssh_port", 22) or 22),
                user=profile.get("ssh_user", ""),
                creds=creds,
                initial_cmd=exec_cmd,
            )
        elif hasattr(w, "add_local_command_tab"):
            w.add_local_command_tab(command=exec_cmd, name=f"{cname} Shell")
        else:
            self._show_console_dialog(cid, cname)

    def _show_console_dialog(self, cid: str, cname: str):
        """Opens in-app quick command runner console for container."""
        c_entry = next((c for c in self._all_containers if c["id"] == cid or c["name"] == cname), None)
        engine = c_entry["engine"] if c_entry else "docker"
        dlg = ContainerConsoleDialog(
            self.theme,
            engine=engine,
            container_id=cid,
            container_name=cname,
            run_cmd_fn=self.run_cmd_fn,
            on_open_full_terminal=lambda i, n: self._open_container_terminal(i, n),
            parent=self,
        )
        dlg.exec()

    def _open_container_ssh(self, engine: str, cid: str, cname: str, ports_str: str):
        """Opens SSH connect dialog for container."""
        w = self.window()
        parent_tab = self.parent()
        while parent_tab and not hasattr(parent_tab, "_is_remote"):
            parent_tab = parent_tab.parent()

        def connect_ssh(host: str, port: int, user: str):
            if hasattr(w, "add_terminal_tab"):
                from admin_suite.ssh.credentials import SshCredentials
                creds = SshCredentials()
                w.add_terminal_tab(
                    name=f"SSH: {cname}:{port}",
                    host=host,
                    port=port,
                    user=user,
                    creds=creds,
                )
            elif hasattr(w, "add_local_command_tab"):
                w.add_local_command_tab(command=f"ssh -p {port} {user}@{host}", name=f"SSH: {cname}")

        dlg = ContainerSSHDialog(
            self.theme,
            engine=engine,
            container_id=cid,
            container_name=cname,
            ports_str=ports_str,
            on_connect_ssh=connect_ssh,
            on_fallback_shell=lambda i, n: self._open_container_terminal(i, n),
            parent=self,
        )
        dlg.exec()

    # -----------------------------------------------------------------
    # Dockerfile Builder Operations
    # -----------------------------------------------------------------
    def _open_dockerfile_build_dialog(self):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"] or ["docker", "podman"]
        act = avail[0] if avail else "docker"
        dlg = DockerfileBuildDialog(
            self.theme,
            active_engine=act,
            available_engines=avail,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            eng, tag, cmd, content = dlg.get_build_command()
            if content and self.run_cmd_fn:
                # If content was typed in editor, pipe via stdin: echo '<content>' | <engine> build -t <tag> -
                escaped = content.replace("'", "'\\''")
                full_cmd = f"echo '{escaped}' | {eng} build -t {tag} -"
            else:
                full_cmd = cmd

            if self.run_cmd_fn:
                self.run_cmd_fn(full_cmd, f"Build Dockerfile: {tag}", timeout=600, refresh_after=True)

    # -----------------------------------------------------------------
    # Compose Operations
    # -----------------------------------------------------------------
    def _on_compose_template_selected(self, t_name: str):
        if t_name in self.COMPOSE_TEMPLATES:
            self.compose_editor.setPlainText(self.COMPOSE_TEMPLATES[t_name])
            if not self.compose_proj_in.text():
                short = t_name.split()[1].lower().replace(".", "").replace("/", "")
                self.compose_proj_in.setText(f"{short}-stack")

    def _save_compose_file(self):
        content = self.compose_editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Save Compose File", "Compose editor is empty.")
            return

        pdir = self.compose_dir_in.text().strip() or "."
        fname = "docker-compose.yml"
        target_path = os.path.join(pdir, fname) if pdir != "." else fname

        # If on local host, we can save directly or via shell echo
        if self.run_cmd_fn:
            escaped = content.replace("'", "'\\''")
            cmd = f"mkdir -p '{pdir}' && echo '{escaped}' > '{target_path}' && echo 'Wrote {target_path} successfully'"
            self.run_cmd_fn(cmd, f"Save {target_path}", timeout=30, refresh_after=False)
            self.compose_output.appendPlainText(f"\n[Saved compose file to {target_path}]")

    def _run_compose_op(self, op_cmd: str, title: str):
        if not self.run_cmd_fn:
            self.compose_output.setPlainText("Command runner unavailable.")
            return

        self.compose_output.appendPlainText(f"\n$ {op_cmd}\n" + "─" * 40)

        def on_done(out: str, rc: int):
            self.compose_output.appendPlainText(out.strip() if out.strip() else f"[{title} completed successfully]")
            self.compose_output.verticalScrollBar().setValue(self.compose_output.verticalScrollBar().maximum())

        self.run_cmd_fn(op_cmd, title, timeout=300, callback=on_done, refresh_after=True)

    def _get_compose_params(self) -> tuple[str, str, str, str]:
        tool = self.compose_tool_combo.currentText()
        pdir = self.compose_dir_in.text().strip()
        pname = self.compose_proj_in.text().strip()
        fpath = os.path.join(pdir, "docker-compose.yml") if pdir and pdir != "." else "docker-compose.yml"
        return tool, fpath, pdir, pname

    def _compose_up(self):
        tool, fpath, pdir, pname = self._get_compose_params()
        cmd = compose_up_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname, detach=True)
        self._run_compose_op(cmd, "Compose Up")

    def _compose_down(self, remove_volumes: bool = False):
        tool, fpath, pdir, pname = self._get_compose_params()
        if remove_volumes:
            ans = QMessageBox.question(
                self,
                "Confirm Compose Down with Volumes",
                "Are you sure you want to stop and remove all services AND their volumes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = compose_down_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname, remove_volumes=remove_volumes)
        self._run_compose_op(cmd, "Compose Down" + (" (with volumes)" if remove_volumes else ""))

    def _compose_restart(self):
        tool, fpath, pdir, pname = self._get_compose_params()
        cmd = compose_restart_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname)
        self._run_compose_op(cmd, "Compose Restart")

    def _compose_logs(self):
        tool, fpath, pdir, pname = self._get_compose_params()
        cmd = compose_logs_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname, tail=200)
        self._run_compose_op(cmd, "Compose Logs")

    def _compose_ps(self):
        tool, fpath, pdir, pname = self._get_compose_params()
        cmd = compose_ps_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname)
        self._run_compose_op(cmd, "Compose Ps")

    def _compose_build(self):
        tool, fpath, pdir, pname = self._get_compose_params()
        cmd = compose_build_cmd(compose_tool=tool, file_path=fpath, project_dir=pdir, project_name=pname)
        self._run_compose_op(cmd, "Compose Build")

    # -----------------------------------------------------------------
    # Image Operations
    # -----------------------------------------------------------------
    def _open_run_dialog(self, prefill_image: str = ""):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"] or ["docker", "podman"]
        act = avail[0] if avail else "docker"
        dlg = ContainerRunDialog(
            self.theme,
            active_engine=act,
            available_engines=avail,
            prefill_image=prefill_image,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            eng, name, cmd = dlg.get_run_command()
            if self.run_cmd_fn:
                self.run_cmd_fn(cmd, f"Run Container: {name}", timeout=180, refresh_after=True)

    def _open_pull_dialog(self):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"]
        act = avail[0] if avail else "docker"
        dlg = ImagePullDialog(self.theme, active_engine=act, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            eng, img = dlg.get_pull_details()
            cmd = image_pull_cmd(eng, img)
            if self.run_cmd_fn:
                self.run_cmd_fn(cmd, f"Pull Image: {img}", timeout=300, refresh_after=True)

    def _open_search_dialog(self):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"]
        act = avail[0] if avail else "docker"

        def on_pull(eng: str, img: str):
            cmd = image_pull_cmd(eng, img)
            if self.run_cmd_fn:
                self.run_cmd_fn(cmd, f"Pull Image: {img}", timeout=300, refresh_after=True)

        dlg = ImageSearchDialog(
            self.theme,
            active_engine=act,
            run_cmd_fn=self.run_cmd_fn,
            on_pull_selected=on_pull,
            parent=self,
        )
        dlg.exec()

    def _delete_image(self, engine: str, img_id: str, repo_tag: str, confirm: bool = True):
        if not self.run_cmd_fn:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Image Deletion",
                f"Remove image '{repo_tag}' ({img_id[:12]}) from host?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = image_rm_cmd(engine, img_id, force=False)
        self.run_cmd_fn(cmd, f"Remove Image: {repo_tag}", timeout=60, refresh_after=True)

    def _prune_images(self, confirm: bool = True):
        eng = self._get_target_engine() or "docker"
        if confirm:
            ans = QMessageBox.question(
                self,
                "Prune Unused Images",
                f"Prune all unused/dangling {eng.capitalize()} images?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = image_prune_cmd(eng)
        self.run_cmd_fn(cmd, f"Prune Images ({eng})", timeout=120, refresh_after=True)

    # -----------------------------------------------------------------
    # Volume Operations
    # -----------------------------------------------------------------
    def _open_create_volume(self):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"]
        act = avail[0] if avail else "docker"
        dlg = VolumeCreateDialog(self.theme, active_engine=act, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            eng, name, drv = dlg.get_details()
            cmd = volume_create_cmd(eng, name, driver=drv)
            if self.run_cmd_fn:
                self.run_cmd_fn(cmd, f"Create Volume: {name}", timeout=60, refresh_after=True)

    def _delete_volume(self, engine: str, vol_name: str, confirm: bool = True):
        if not self.run_cmd_fn:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Volume Deletion",
                f"Delete volume '{vol_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = volume_rm_cmd(engine, vol_name, force=False)
        self.run_cmd_fn(cmd, f"Delete Volume: {vol_name}", timeout=60, refresh_after=True)

    def _prune_volumes(self, confirm: bool = True):
        eng = self._get_target_engine() or "docker"
        if confirm:
            ans = QMessageBox.question(
                self,
                "Prune Unused Volumes",
                f"Prune all unused {eng.capitalize()} volumes? This will remove all volumes not attached to at least one container.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = volume_prune_cmd(eng)
        self.run_cmd_fn(cmd, f"Prune Volumes ({eng})", timeout=60, refresh_after=True)

    # -----------------------------------------------------------------
    # Network Operations
    # -----------------------------------------------------------------
    def _open_create_network(self):
        avail = [e for e, info in self._engines.items() if info["status"] == "active"]
        act = avail[0] if avail else "docker"
        dlg = NetworkCreateDialog(self.theme, active_engine=act, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            eng, name, drv, sub = dlg.get_details()
            cmd = network_create_cmd(eng, name, driver=drv, subnet=sub)
            if self.run_cmd_fn:
                self.run_cmd_fn(cmd, f"Create Network: {name}", timeout=60, refresh_after=True)

    def _delete_network(self, engine: str, net_name: str, confirm: bool = True):
        if not self.run_cmd_fn:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Network Deletion",
                f"Delete network '{net_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = network_rm_cmd(engine, net_name)
        self.run_cmd_fn(cmd, f"Delete Network: {net_name}", timeout=60, refresh_after=True)

    def _prune_networks(self, confirm: bool = True):
        eng = self._get_target_engine() or "docker"
        if confirm:
            ans = QMessageBox.question(
                self,
                "Prune Unused Networks",
                f"Prune all unused {eng.capitalize()} networks?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        cmd = network_prune_cmd(eng)
        self.run_cmd_fn(cmd, f"Prune Networks ({eng})", timeout=60, refresh_after=True)

    # -----------------------------------------------------------------
    # Context Menus & Helpers
    # -----------------------------------------------------------------
    def _show_container_context_menu(self, pos):
        item = self.c_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        it_name = self.c_table.item(row, 1)
        it_id = self.c_table.item(row, 3)
        it_ports = self.c_table.item(row, 4)
        it_eng = self.c_table.item(row, 7)
        it_st = self.c_table.item(row, 0)
        if not it_name or not it_id:
            return

        name = it_name.text()
        cid = it_id.text()
        ports_str = it_ports.text() if it_ports else ""
        eng = "podman" if "Podman" in (it_eng.text() if it_eng else "") else "docker"
        is_running = "Up" in (it_st.text() if it_st else "")

        menu = QMenu(self)
        if is_running:
            act_stop = menu.addAction("⏹ Stop Container")
            act_restart = menu.addAction("🔄 Restart Container")
            act_start = None
        else:
            act_start = menu.addAction("▶ Start Container")
            act_stop = None
            act_restart = None

        menu.addSeparator()
        act_term = menu.addAction("💻 Open Interactive Shell in Terminal Tab...")
        act_console = menu.addAction("💻 Quick Container Console Dialog...")
        act_ssh = menu.addAction("🔑 Connect via SSH...")
        menu.addSeparator()
        act_logs = menu.addAction("📜 View Logs...")
        act_inspect = menu.addAction("🔍 Inspect Configuration (JSON)...")
        menu.addSeparator()
        act_copy_id = menu.addAction("📋 Copy Container ID")
        act_copy_name = menu.addAction("📋 Copy Container Name")
        menu.addSeparator()
        act_delete = menu.addAction("🗑️ Delete Container (Force)")

        choice = menu.exec(self.c_table.mapToGlobal(pos))
        if act_start and choice == act_start:
            self._start_container(eng, cid, name)
        elif act_stop and choice == act_stop:
            self._stop_container(eng, cid, name)
        elif act_restart and choice == act_restart:
            self._restart_container(eng, cid, name)
        elif choice == act_term:
            self._open_container_terminal(cid, name)
        elif choice == act_console:
            self._show_console_dialog(cid, name)
        elif choice == act_ssh:
            self._open_container_ssh(eng, cid, name, ports_str)
        elif choice == act_logs:
            self._show_logs(eng, cid, name)
        elif choice == act_inspect:
            self._inspect_container(eng, cid, name)
        elif choice == act_copy_id:
            QApplication.clipboard().setText(cid)
        elif choice == act_copy_name:
            QApplication.clipboard().setText(name)
        elif choice == act_delete:
            self._delete_container(eng, cid, name)

    def _on_container_double_clicked(self, row: int, col: int):
        it_name = self.c_table.item(row, 1)
        it_id = self.c_table.item(row, 3)
        it_eng = self.c_table.item(row, 7)
        if it_name and it_id:
            eng = "podman" if "Podman" in (it_eng.text() if it_eng else "") else "docker"
            self._show_logs(eng, it_id.text(), it_name.text())

    def _show_image_context_menu(self, pos):
        item = self.img_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        it_repo = self.img_table.item(row, 0)
        it_tag = self.img_table.item(row, 1)
        it_id = self.img_table.item(row, 2)
        it_eng = self.img_table.item(row, 5)
        if not it_repo or not it_id:
            return

        repo = it_repo.text()
        tag = it_tag.text() if it_tag else "latest"
        full_tag = f"{repo}:{tag}"
        img_id = it_id.text()
        eng = "podman" if "Podman" in (it_eng.text() if it_eng else "") else "docker"

        menu = QMenu(self)
        act_run = menu.addAction(f"🚀 Run Container from {full_tag}...")
        menu.addSeparator()
        act_copy_id = menu.addAction("📋 Copy Image ID")
        act_copy_tag = menu.addAction("📋 Copy Image Tag")
        menu.addSeparator()
        act_rm = menu.addAction("🗑️ Remove Image")

        choice = menu.exec(self.img_table.mapToGlobal(pos))
        if choice == act_run:
            self._open_run_dialog(prefill_image=full_tag)
        elif choice == act_copy_id:
            QApplication.clipboard().setText(img_id)
        elif choice == act_copy_tag:
            QApplication.clipboard().setText(full_tag)
        elif choice == act_rm:
            self._delete_image(eng, img_id, full_tag)

    def _on_image_double_clicked(self, row: int, col: int):
        it_repo = self.img_table.item(row, 0)
        it_tag = self.img_table.item(row, 1)
        if it_repo and it_tag:
            self._open_run_dialog(prefill_image=f"{it_repo.text()}:{it_tag.text()}")

    def _show_volume_context_menu(self, pos):
        item = self.vol_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        it_name = self.vol_table.item(row, 0)
        it_eng = self.vol_table.item(row, 3)
        if not it_name:
            return
        name = it_name.text()
        eng = "podman" if "Podman" in (it_eng.text() if it_eng else "") else "docker"

        menu = QMenu(self)
        act_copy = menu.addAction("📋 Copy Volume Name")
        act_rm = menu.addAction("🗑️ Delete Volume")
        choice = menu.exec(self.vol_table.mapToGlobal(pos))
        if choice == act_copy:
            QApplication.clipboard().setText(name)
        elif choice == act_rm:
            self._delete_volume(eng, name)

    def _show_network_context_menu(self, pos):
        item = self.net_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        it_name = self.net_table.item(row, 1)
        it_id = self.net_table.item(row, 0)
        it_eng = self.net_table.item(row, 4)
        if not it_name:
            return
        name = it_name.text()
        nid = it_id.text() if it_id else ""
        eng = "podman" if "Podman" in (it_eng.text() if it_eng else "") else "docker"

        menu = QMenu(self)
        act_inspect = menu.addAction("🔍 Inspect Network...")
        act_copy = menu.addAction("📋 Copy Network Name")
        act_rm = menu.addAction("🗑️ Delete Network")
        choice = menu.exec(self.net_table.mapToGlobal(pos))
        if choice == act_inspect:
            dlg = ContainerInspectDialog(self.theme, eng, nid or name, name, run_cmd_fn=self.run_cmd_fn, parent=self)
            dlg.exec()
        elif choice == act_copy:
            QApplication.clipboard().setText(name)
        elif choice == act_rm:
            self._delete_network(eng, name)

    def _refresh_data(self):
        """Triggers section refresh."""
        p = self.parent()
        while p:
            if hasattr(p, "_refresh_current"):
                p._refresh_current()
                break
            p = p.parent()

    def _show_setup_help(self):
        msg = (
            "<h3>Container Engine Setup Guide</h3>"
            "<p>Neither Docker nor Podman is currently running or accessible without elevated permissions.</p>"
            "<b>To start or install Docker:</b>"
            "<pre style='background:#222; padding:6px; color:#3daee9;'>"
            "# Debian / Ubuntu:\n"
            "sudo apt-get update && sudo apt-get install -y docker.io\n"
            "sudo systemctl enable --now docker\n"
            "sudo usermod -aG docker $USER\n\n"
            "# RHEL / CentOS / Rocky / Fedora:\n"
            "sudo dnf install -y docker-ce || sudo dnf install -y podman-docker\n"
            "sudo systemctl enable --now docker"
            "</pre>"
            "<b>To install or use rootless Podman (daemon-less):</b>"
            "<pre style='background:#222; padding:6px; color:#3daee9;'>"
            "# Debian / Ubuntu:\n"
            "sudo apt-get install -y podman\n\n"
            "# RHEL / Fedora / CentOS:\n"
            "sudo dnf install -y podman\n\n"
            "# Start user service if needed:\n"
            "systemctl --user enable --now podman.socket"
            "</pre>"
            "<p><i>Note: If Docker requires root privileges, make sure the <b>'Sudo'</b> checkbox in the toolbar is enabled!</i></p>"
        )
        QMessageBox.information(self, "Container Engines Setup", msg)



# ====================================================================
# 6. Cron & Scheduled Tasks Widget
# ====================================================================
class SysAdminCronWidget(QWidget):
    """Structured systemd timers and crontab jobs view."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        lbl = QLabel("⏰ Active Systemd Timers:")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl)

        self.timers_table = QTableWidget(0, 5)
        self.timers_table.setHorizontalHeaderLabels(["Next Trigger", "Time Left", "Last Trigger", "Timer Unit", "Activates Service"])
        self.timers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.timers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.timers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.timers_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.timers_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.timers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.timers_table, 1)

        lbl2 = QLabel("📜 Crontab Jobs (/etc/crontab, /etc/cron.d, user crontab):")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl2)

        self.cron_table = QTableWidget(0, 3)
        self.cron_table.setHorizontalHeaderLabels(["Schedule", "Command / Task", "Source File"])
        self.cron_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.cron_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cron_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.cron_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.cron_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # 1. Timers
        t_lines = sections.get("SYSTEMD TIMERS", [])
        t_rows = []
        for line in t_lines:
            if not line or line.startswith("NEXT"):
                continue
            parts = line.split(None, 4)
            if len(parts) >= 5:
                t_rows.append(parts)

        self.timers_table.setRowCount(len(t_rows))
        for r_idx, r in enumerate(t_rows):
            for c_idx, val in enumerate(r):
                self.timers_table.setItem(r_idx, c_idx, QTableWidgetItem(val))

        # 2. Crontabs
        c_rows = []
        for src, lines in [("System Crontab", sections.get("SYSTEM CRONTAB", [])),
                           ("Cron.d", sections.get("CRON.D", [])),
                           ("User Crontab", sections.get("USER CRONTAB", []))]:
            for line in lines:
                if not line or line.startswith("#") or line.startswith("---") or line.startswith("SHELL") or line.startswith("PATH"):
                    continue
                parts = line.split(None, 5)
                if len(parts) >= 6:
                    schedule = " ".join(parts[:5])
                    cmd = parts[5]
                    c_rows.append([schedule, cmd, src])

        self.cron_table.setRowCount(len(c_rows))
        for r_idx, r in enumerate(c_rows):
            for c_idx, val in enumerate(r):
                self.cron_table.setItem(r_idx, c_idx, QTableWidgetItem(val))


# ====================================================================
# 7. Security Widget
# ====================================================================
class SysAdminSecurityWidget(QWidget):
    """Structured security posture, failed logins, and file permission alerts."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        kpi_row = QHBoxLayout()
        self.card_lsm = _create_kpi_card("SELinux / AppArmor", "-", theme.get("accent", "#3daee9"), theme)
        self.card_root = _create_kpi_card("SSH Root Login", "-", "#4fc3f7", theme)
        self.card_fails = _create_kpi_card("Recent Failed Logins", "0", "#ff5252", theme)
        kpi_row.addWidget(self.card_lsm)
        kpi_row.addWidget(self.card_root)
        kpi_row.addWidget(self.card_fails)
        layout.addLayout(kpi_row)

        lbl = QLabel("🚨 Recent Failed SSH Authentication Attempts:")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl)

        self.fails_table = QTableWidget(0, 4)
        self.fails_table.setHorizontalHeaderLabels(["Timestamp", "Target User", "Remote IP", "Details"])
        self.fails_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.fails_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.fails_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.fails_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.fails_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.fails_table, 1)

        lbl2 = QLabel("⚠️ World-Writable Files in /etc:")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl2)

        self.ww_table = QTableWidget(0, 2)
        self.ww_table.setHorizontalHeaderLabels(["File Path", "Risk Level"])
        self.ww_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ww_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.ww_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.ww_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # LSM
        lsm_str = " ".join(sections.get("SELINUX / APPARMOR STATUS", ["-"]))
        _set_card_val(self.card_lsm, lsm_str[:30])

        # Root
        root_str = " ".join(sections.get("SSH ROOT LOGIN STATUS", ["default"]))
        _set_card_val(self.card_root, root_str[:30])

        # Failed logins
        fail_lines = sections.get("FAILED SSH LOGINS (Last 20)", [])
        _set_card_val(self.card_fails, str(len(fail_lines)))

        f_rows = []
        for line in fail_lines:
            if not line or "no auth logs" in line:
                continue
            parts = line.split()
            ts = " ".join(parts[:3]) if len(parts) >= 3 else "-"
            user = "-"
            ip = "-"
            m_user = re.search(r'for\s+(invalid user\s+)?(\S+)\s+from\s+(\S+)', line)
            if m_user:
                user = m_user.group(2)
                ip = m_user.group(3)
            f_rows.append([ts, user, ip, line])

        self.fails_table.setRowCount(len(f_rows))
        for r_idx, r in enumerate(f_rows):
            for c_idx, val in enumerate(r):
                self.fails_table.setItem(r_idx, c_idx, QTableWidgetItem(val))

        # World writable
        ww_lines = sections.get("WORLD-WRITABLE FILES IN /etc", [])
        self.ww_table.setRowCount(len(ww_lines))
        for r_idx, f in enumerate(ww_lines):
            self.ww_table.setItem(r_idx, 0, QTableWidgetItem(f))
            item_r = QTableWidgetItem("⚠️ Caution")
            item_r.setForeground(QColor("#ff5252"))
            self.ww_table.setItem(r_idx, 1, item_r)


# ====================================================================
# 8. Packages Widget
# ====================================================================
class SysAdminPackagesWidget(QWidget):
    """Structured upgradable packages, single/bulk upgrade manager, and package history view."""

    def __init__(self, theme: dict[str, str], run_cmd_fn: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.run_cmd_fn = run_cmd_fn
        self._upgradable_pkgs: list[dict[str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Header bar: KPI + Bulk update buttons + status
        head_row = QHBoxLayout()
        head_row.setSpacing(8)

        self.card_upgrades = _create_kpi_card("Upgradable Packages", "0", theme.get("accent", "#3daee9"), theme, "Pending Updates")
        head_row.addWidget(self.card_upgrades)

        # Bulk update button
        self.btn_update_all = QPushButton("🚀 Update All Packages")
        self.btn_update_all.setToolTip("Upgrade all pending packages on system in non-interactive mode")
        self.btn_update_all.setStyleSheet("background: #1b5e20; color: #4caf50; font-weight: bold; font-size: 13px; padding: 8px 14px; border-radius: 4px;")
        self.btn_update_all.clicked.connect(self._update_all_packages)
        head_row.addWidget(self.btn_update_all)

        # Refresh repositories cache button
        self.btn_refresh = QPushButton("🔄 Refresh Repositories")
        self.btn_refresh.setToolTip("Fetch fresh package index metadata (apt update / dnf makecache)")
        self.btn_refresh.setStyleSheet("padding: 8px 12px; border-radius: 4px;")
        self.btn_refresh.clicked.connect(self._refresh_package_cache)
        head_row.addWidget(self.btn_refresh)

        head_row.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 12px; padding: 4px 8px; border-radius: 3px;")
        head_row.addWidget(self.status_lbl)

        layout.addLayout(head_row)

        # Table header & filter
        sub_head = QHBoxLayout()
        lbl = QLabel("📦 Packages with Available Updates:")
        lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        sub_head.addWidget(lbl)

        sub_head.addStretch()

        self.pkg_search = QLineEdit()
        self.pkg_search.setPlaceholderText("🔍 Filter packages by name or version...")
        self.pkg_search.setFixedWidth(280)
        self.pkg_search.textChanged.connect(self._filter_packages)
        sub_head.addWidget(self.pkg_search)

        layout.addLayout(sub_head)

        # Table of upgradable packages
        self.up_table = QTableWidget(0, 5)
        self.up_table.setHorizontalHeaderLabels(["Package Name", "Current Version", "New Version", "Status", "Action"])
        self.up_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.up_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.up_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.up_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.up_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.up_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.up_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.up_table.setAlternatingRowColors(True)
        layout.addWidget(self.up_table, 1)

        # History section
        lbl2 = QLabel("📜 Recent Package Installations / History:")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')}; margin-top: 4px;")
        layout.addWidget(lbl2)

        self.hist_table = QTableWidget(0, 2)
        self.hist_table.setHorizontalHeaderLabels(["Transaction Entry", "Details"])
        self.hist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.hist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hist_table.setAlternatingRowColors(True)
        layout.addWidget(self.hist_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # Upgrades parsing
        up_lines = [l for l in sections.get("UPGRADABLE PACKAGES", []) if l and not l.startswith("package manager")]
        pkgs = []
        for line in up_lines:
            if "|" in line:
                parts = line.split("|")
                name = parts[0].strip()
                cur_v = parts[1].strip() if len(parts) > 1 else "-"
                new_v = parts[2].strip() if len(parts) > 2 else "-"
            else:
                parts = line.split()
                name = parts[0].strip()
                cur_v = parts[1].strip() if len(parts) > 1 else "-"
                new_v = parts[2].strip() if len(parts) > 2 else "-"
            if name:
                pkgs.append({"name": name, "cur_v": cur_v, "new_v": new_v})

        self._upgradable_pkgs = pkgs
        _set_card_val(self.card_upgrades, str(len(pkgs)), "Pending Updates")

        # Enable/disable bulk update button
        self.btn_update_all.setEnabled(len(pkgs) > 0)
        if len(pkgs) == 0:
            self.btn_update_all.setText("✅ System Up to Date")
            self.btn_update_all.setStyleSheet("background: #2a3b2c; color: #81c784; font-weight: bold; border-radius: 4px; padding: 8px 14px;")
        else:
            self.btn_update_all.setText(f"🚀 Update All ({len(pkgs)}) Packages")
            self.btn_update_all.setStyleSheet("background: #1b5e20; color: #4caf50; font-weight: bold; border-radius: 4px; padding: 8px 14px;")

        self._render_packages_table(pkgs)

        # History parsing
        hist_lines = sections.get("RECENT PACKAGE HISTORY", [])
        self.hist_table.setRowCount(len(hist_lines))
        for r_idx, h in enumerate(hist_lines):
            parts = h.split(None, 2)
            ts = parts[0] if len(parts) > 0 else "-"
            det = h
            self.hist_table.setItem(r_idx, 0, QTableWidgetItem(ts))
            self.hist_table.setItem(r_idx, 1, QTableWidgetItem(det))

    def _render_packages_table(self, pkgs: list[dict[str, str]]):
        self.up_table.setRowCount(len(pkgs))
        for r_idx, pkg in enumerate(pkgs):
            it_name = QTableWidgetItem(pkg["name"])
            it_name.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.up_table.setItem(r_idx, 0, it_name)

            it_cur = QTableWidgetItem(pkg["cur_v"])
            it_cur.setForeground(QColor(self.theme.get("sub", "#888888")))
            self.up_table.setItem(r_idx, 1, it_cur)

            it_new = QTableWidgetItem(pkg["new_v"])
            it_new.setForeground(QColor(self.theme.get("ok", "#0dbc79")))
            it_new.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.up_table.setItem(r_idx, 2, it_new)

            it_st = QTableWidgetItem("Update Available")
            it_st.setForeground(QColor("#f39c12"))
            it_st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.up_table.setItem(r_idx, 3, it_st)

            # Update button for single package
            btn = QPushButton("⬆️ Update")
            btn.setToolTip(f"Upgrade package '{pkg['name']}' to latest version")
            btn.setStyleSheet(f"background: {self.theme.get('accent', '#3daee9')}; color: #ffffff; font-weight: bold; padding: 3px 8px; border-radius: 3px;")
            btn.clicked.connect(lambda _, p=pkg["name"]: self._update_single_package(p))
            self.up_table.setCellWidget(r_idx, 4, btn)

    def _filter_packages(self, text: str):
        q = text.strip().lower()
        for row in range(self.up_table.rowCount()):
            item_name = self.up_table.item(row, 0)
            item_cur = self.up_table.item(row, 1)
            item_new = self.up_table.item(row, 2)
            name_t = item_name.text().lower() if item_name else ""
            cur_t = item_cur.text().lower() if item_cur else ""
            new_t = item_new.text().lower() if item_new else ""
            match = not q or (q in name_t or q in cur_t or q in new_t)
            self.up_table.setRowHidden(row, not match)

    def _update_single_package(self, pkg_name: str, confirm: bool = True):
        if not self.run_cmd_fn:
            QMessageBox.information(self, "Update Package", "Command runner is not available.")
            return

        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Package Update",
                f"Upgrade package '{pkg_name}' to the latest available version on target host?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        cmd = package_update_single_cmd(pkg_name)
        self.status_lbl.setText(f"⏳ Upgrading {pkg_name}...")
        self.status_lbl.setStyleSheet("color: #3daee9; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        def on_done(out: str, rc: int):
            if rc == 0:
                self.status_lbl.setText(f"✅ Package {pkg_name} upgraded successfully!")
                self.status_lbl.setStyleSheet("color: #0dbc79; font-weight: bold; padding: 4px 8px; border-radius: 3px;")
            else:
                self.status_lbl.setText(f"❌ Failed to upgrade {pkg_name} (rc={rc})")
                self.status_lbl.setStyleSheet("color: #ff5555; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        self.run_cmd_fn(cmd, f"Upgrade Package: {pkg_name}", timeout=300, callback=on_done, refresh_after=True)

    def _update_all_packages(self, confirm: bool = True):
        if not self.run_cmd_fn:
            QMessageBox.information(self, "Update All Packages", "Command runner is not available.")
            return
        count = len(self._upgradable_pkgs)
        if count == 0:
            QMessageBox.information(self, "Update All Packages", "No upgradable packages found.")
            return

        if confirm:
            ans = QMessageBox.question(
                self,
                "Confirm Bulk Upgrade",
                f"Are you sure you want to upgrade ALL {count} pending packages on target host?\n\n"
                "This will execute a non-interactive system package upgrade.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        cmd = package_update_all_cmd()
        self.status_lbl.setText(f"⏳ Upgrading all {count} packages...")
        self.status_lbl.setStyleSheet("color: #3daee9; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        def on_done(out: str, rc: int):
            if rc == 0:
                self.status_lbl.setText("✅ System upgrade completed successfully!")
                self.status_lbl.setStyleSheet("color: #0dbc79; font-weight: bold; padding: 4px 8px; border-radius: 3px;")
            else:
                self.status_lbl.setText(f"❌ System upgrade failed (rc={rc})")
                self.status_lbl.setStyleSheet("color: #ff5555; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        self.run_cmd_fn(cmd, "Upgrade All Packages", timeout=600, callback=on_done, refresh_after=True)

    def _refresh_package_cache(self):
        if not self.run_cmd_fn:
            QMessageBox.information(self, "Refresh Repositories", "Command runner is not available.")
            return
        cmd = package_refresh_cache_cmd()
        self.status_lbl.setText("⏳ Updating repository package index...")
        self.status_lbl.setStyleSheet("color: #3daee9; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        def on_done(out: str, rc: int):
            if rc == 0:
                self.status_lbl.setText("✅ Package cache refreshed!")
                self.status_lbl.setStyleSheet("color: #0dbc79; font-weight: bold; padding: 4px 8px; border-radius: 3px;")
            else:
                self.status_lbl.setText(f"⚠️ Cache refresh warning (rc={rc})")
                self.status_lbl.setStyleSheet("color: #f39c12; font-weight: bold; padding: 4px 8px; border-radius: 3px;")

        self.run_cmd_fn(cmd, "Refresh Package Repositories", timeout=120, callback=on_done, refresh_after=True)


# ====================================================================
# 9. Performance Widget
# ====================================================================
class SysAdminPerformanceWidget(QWidget):
    """Structured CPU, memory, IO, and kernel performance metrics view."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        lbl = QLabel("🔥 Top CPU & Memory Consuming Processes:")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl)

        self.proc_table = QTableWidget(0, 6)
        self.proc_table.setHorizontalHeaderLabels(["User", "PID", "%CPU", "%MEM", "Time", "Command"])
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.proc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.proc_table, 1)

        lbl2 = QLabel("⚠️ Kernel Ring Buffer (dmesg) Recent Alerts & Warnings:")
        lbl2.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(lbl2)

        self.dmesg_table = QTableWidget(0, 2)
        self.dmesg_table.setHorizontalHeaderLabels(["Level", "Kernel Message"])
        self.dmesg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.dmesg_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.dmesg_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.dmesg_table, 1)

    def update_data(self, raw: str):
        sections: dict[str, list[str]] = {}
        curr = "HEADER"
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("==") and line_s.endswith("=="):
                curr = line_s.strip("= ")
                sections[curr] = []
            elif curr in sections:
                sections[curr].append(line_s)

        # Processes
        proc_lines = sections.get("TOP MEMORY CONSUMERS", [])
        p_rows = []
        for line in proc_lines:
            if not line or line.startswith("USER"):
                continue
            parts = line.split(None, 10)
            if len(parts) >= 11:
                u = parts[0]
                pid = parts[1]
                cpu = parts[2]
                mem = parts[3]
                time = parts[9]
                cmd = parts[10]
                p_rows.append([u, pid, cpu, mem, time, cmd])

        self.proc_table.setRowCount(len(p_rows))
        for r_idx, r in enumerate(p_rows):
            for c_idx, val in enumerate(r):
                self.proc_table.setItem(r_idx, c_idx, QTableWidgetItem(val))

        # dmesg
        dmesg_lines = sections.get("KERNEL RING BUFFER (ERRORS)", [])
        d_rows = []
        for line in dmesg_lines:
            if not line or "restricted" in line:
                continue
            lvl = "WARN"
            if "err" in line.lower() or "fail" in line.lower():
                lvl = "ERROR"
            d_rows.append([lvl, line])

        self.dmesg_table.setRowCount(len(d_rows))
        for r_idx, (lvl, msg) in enumerate(d_rows):
            item_l = QTableWidgetItem(lvl)
            item_l.setForeground(QColor("#ff5252" if lvl == "ERROR" else "#ffa000"))
            item_l.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            self.dmesg_table.setItem(r_idx, 0, item_l)
            self.dmesg_table.setItem(r_idx, 1, QTableWidgetItem(msg))

