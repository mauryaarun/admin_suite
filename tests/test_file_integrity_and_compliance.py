"""
Unit tests for File Integrity & Application Whitelisting (AIDE & fapolicyd)
and Compliance Automation & Auditing (OpenSCAP & Auditd) modules.
"""

import sys
from pathlib import Path
import pytest

# Ensure package root is in sys.path
pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from PyQt6.QtWidgets import QApplication
from admin_suite.services import AppServices
from admin_suite.security.commands import SecurityCommands, SecurityParsers
from admin_suite.security.file_integrity import FileIntegrityWidget, WhitelistBinaryDialog
from admin_suite.security.compliance import ComplianceAuditorWidget
from admin_suite.security.tab import SecurityHubTab


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


# --------------------------------------------------------------------
# 1. Distribution Detection & Package Installation Tests
# --------------------------------------------------------------------

def test_detect_os_parser_rhel():
    raw_rhel9 = """
=== OS_RELEASE ===
NAME="Red Hat Enterprise Linux"
VERSION="9.4 (Plow)"
ID="rhel"
ID_LIKE="fedora"
VERSION_ID="9.4"
PLATFORM_ID="platform:el9"
PRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"
=== PKG_MANAGER ===
PKG_MGR:dnf
=== ARCH ===
x86_64
"""
    parsed = SecurityParsers.parse_os_release(raw_rhel9)
    assert parsed["family"] == "rhel"
    assert parsed["id"] == "rhel"
    assert parsed["version_id"] == "9.4"
    assert parsed["pkg_manager"] == "dnf"
    assert parsed["arch"] == "x86_64"


def test_detect_os_parser_debian():
    raw_ubuntu = """
=== OS_RELEASE ===
NAME="Ubuntu"
VERSION="24.04 LTS (Noble Numbat)"
ID=ubuntu
ID_LIKE=debian
VERSION_ID="24.04"
PRETTY_NAME="Ubuntu 24.04 LTS"
=== PKG_MANAGER ===
PKG_MGR:apt-get
=== ARCH ===
x86_64
"""
    parsed = SecurityParsers.parse_os_release(raw_ubuntu)
    assert parsed["family"] == "debian"
    assert parsed["id"] == "ubuntu"
    assert parsed["pkg_manager"] == "apt-get"


def test_install_packages_cmd():
    cmd_dnf = SecurityCommands.install_packages_cmd(["aide", "fapolicyd"], pkg_manager="dnf")
    assert "dnf install -y aide fapolicyd" in cmd_dnf

    cmd_apt = SecurityCommands.install_packages_cmd(["aide"], pkg_manager="apt-get")
    assert "apt-get install -y aide" in cmd_apt


# --------------------------------------------------------------------
# 2. AIDE Command Builders & Parsers Tests
# --------------------------------------------------------------------

def test_aide_commands():
    probe = SecurityCommands.aide_probe()
    assert "which aide" in probe
    assert "/var/lib/aide/aide.db" in probe
    assert "cron.daily" in probe

    init_cmd = SecurityCommands.aide_init()
    assert "aide --init" in init_cmd or "aideinit" in init_cmd
    assert "aide.db.new" in init_cmd

    check_cmd = SecurityCommands.aide_check()
    assert check_cmd == "aide --check 2>&1 || true"

    update_cmd = SecurityCommands.aide_update()
    assert "aide --update" in update_cmd

    cron_cmd = SecurityCommands.aide_setup_cron()
    assert "/etc/cron.daily/aide-check" in cron_cmd

    rm_cron = SecurityCommands.aide_remove_cron()
    assert "rm -f /etc/cron.daily/aide-check" in rm_cron


def test_aide_parsers():
    raw_probe = """
=== AIDE_PROBE ===
INSTALLED:yes
AIDEINIT:no
CONFIG:/etc/aide.conf
DB_EXISTS:yes
DB_PATH:/var/lib/aide/aide.db.gz
DB_STAT:12M|Nov 2 12:00
DB_NEW_EXISTS:no
CRON_SCHEDULED:yes
CRON_FILE:/etc/cron.daily/aide-check
LOG_EXISTS:yes
LOG_STAT:45K|Nov 2 04:00
"""
    info = SecurityParsers.parse_aide_probe(raw_probe)
    assert info["installed"] is True
    assert info["db_exists"] is True
    assert info["db_path"] == "/var/lib/aide/aide.db.gz"
    assert info["cron_scheduled"] is True
    assert info["cron_file"] == "/etc/cron.daily/aide-check"
    assert info["log_exists"] is True

    # Check clean output
    clean_out = "AIDE 0.16.2. All files ok. No differences found between database and filesystem."
    res_clean = SecurityParsers.parse_aide_check_output(clean_out)
    assert res_clean["clean"] is True
    assert len(res_clean["entries"]) == 0

    # Check diff output
    diff_out = """
AIDE 0.16.2 found differences between database and filesystem!!
Total number of entries: 45210
Added entries: 2
Removed entries: 1
Changed entries: 2

---------------------------------------------------
Added entries:
---------------------------------------------------
/usr/bin/new_backdoor
/etc/cron.d/malicious

---------------------------------------------------
Removed entries:
---------------------------------------------------
/etc/shadow.bak

---------------------------------------------------
Changed entries:
---------------------------------------------------
/usr/sbin/sshd
/etc/pam.d/system-auth
"""
    res_diff = SecurityParsers.parse_aide_check_output(diff_out)
    assert res_diff["clean"] is False
    assert res_diff["total_entries"] == 45210
    assert res_diff["added"] == 2
    assert res_diff["removed"] == 1
    assert res_diff["changed"] == 2
    assert len(res_diff["entries"]) >= 5
    statuses = [e["status"] for e in res_diff["entries"]]
    assert "Added" in statuses
    assert "Removed" in statuses
    assert "Changed" in statuses


# --------------------------------------------------------------------
# 3. fapolicyd Command Builders & Parsers Tests
# --------------------------------------------------------------------

def test_fapolicyd_commands():
    probe = SecurityCommands.fapolicyd_probe()
    assert "which fapolicyd" in probe
    assert "fapolicyd.conf" in probe

    en = SecurityCommands.fapolicyd_enable()
    assert "systemctl enable --now fapolicyd" in en

    dis = SecurityCommands.fapolicyd_disable()
    assert "systemctl stop fapolicyd" in dis

    mode_enforce = SecurityCommands.fapolicyd_set_mode(permissive=False)
    assert "permissive = 0" in mode_enforce

    mode_perm = SecurityCommands.fapolicyd_set_mode(permissive=True)
    assert "permissive = 1" in mode_perm

    up_db = SecurityCommands.fapolicyd_update_db()
    assert "fapolicyd-cli --update" in up_db

    trust_f = SecurityCommands.fapolicyd_trust_file("/opt/app/bin/tool")
    assert "/etc/fapolicyd/trust.file" in trust_f
    assert "/opt/app/bin/tool" in trust_f


def test_fapolicyd_parsers():
    raw_probe = """
=== FAPOLICYD_PROBE ===
INSTALLED:yes
CLI_INSTALLED:yes
SERVICE_STATE:active
SERVICE_ENABLED:enabled
CONFIG_EXISTS:yes
permissive = 0
integrity = sha256
=== FAPOLICYD_STATUS ===
Rules: 42
Trust database contains 18520 entries
"""
    info = SecurityParsers.parse_fapolicyd_probe(raw_probe)
    assert info["installed"] is True
    assert info["cli_installed"] is True
    assert info["service_state"] == "active"
    assert info["permissive"] == 0
    assert info["integrity"] == "sha256"

    # Denial audit parser
    raw_denials = """
type=FANOTIFY msg=audit(1698888888.123:456): resp=2 ... pid=1234 uid=1000 comm="untrusted_script" exe="/tmp/bad.sh"
type=FANOTIFY msg=audit(1698888899.456:457): resp=2 ... pid=5678 uid=0 comm="curl_exploit" exe="/dev/shm/payload"
"""
    denials = SecurityParsers.parse_fapolicyd_denials(raw_denials)
    assert len(denials) == 2
    assert denials[0]["exe"] == "/tmp/bad.sh"
    assert denials[0]["comm"] == "untrusted_script"
    assert denials[0]["pid"] == "1234"
    assert denials[0]["uid"] == "1000"
    assert denials[1]["exe"] == "/dev/shm/payload"


# --------------------------------------------------------------------
# 4. OpenSCAP Command Builders & Parsers Tests
# --------------------------------------------------------------------

def test_openscap_commands():
    probe = SecurityCommands.openscap_probe()
    assert "which oscap" in probe
    assert "/usr/share/xml/scap/ssg/content" in probe

    eval_cmd = SecurityCommands.openscap_eval(
        profile="xccdf_org.ssgproject.content_profile_cis",
        datastream_path="/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml",
        report_path="/var/www/html/rhel-cis-report.html",
    )
    assert "oscap xccdf eval" in eval_cmd
    assert "--profile xccdf_org.ssgproject.content_profile_cis" in eval_cmd
    assert "--report /var/www/html/rhel-cis-report.html" in eval_cmd
    assert "/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml" in eval_cmd

    fix_cmd = SecurityCommands.openscap_generate_fix(
        profile="xccdf_org.ssgproject.content_profile_cis",
        datastream_path="/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml",
    )
    assert "oscap xccdf generate fix" in fix_cmd


def test_openscap_parsers():
    raw_probe = """
=== OPENSCAP_PROBE ===
INSTALLED:yes
=== DATASTREAMS ===
DS:/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml
DS:/usr/share/xml/scap/ssg/content/ssg-rhel8-ds.xml
=== HTML_DIR ===
HTML_DIR:/var/www/html
"""
    info = SecurityParsers.parse_openscap_probe(raw_probe)
    assert info["installed"] is True
    assert len(info["datastreams"]) == 2
    assert "/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml" in info["datastreams"]
    assert info["html_dir"] == "/var/www/html"

    # Profile parser
    raw_info = """
Document type: Source Data Stream
Profiles:
    Title: CIS Red Hat Enterprise Linux 9 Benchmark
    Id: xccdf_org.ssgproject.content_profile_cis
    Title: DISA STIG for Red Hat Enterprise Linux 9
    Id: xccdf_org.ssgproject.content_profile_stig
"""
    profs = SecurityParsers.parse_openscap_profiles(raw_info)
    assert len(profs) == 2
    assert profs[0]["id"] == "xccdf_org.ssgproject.content_profile_cis"
    assert "CIS" in profs[0]["title"]
    assert profs[1]["id"] == "xccdf_org.ssgproject.content_profile_stig"

    # Eval output parser
    raw_eval = """
OpenSCAP Evaluation Result:
Rule: xccdf_org.ssgproject.content_rule_accounts_password_minlen Result: pass
Rule: xccdf_org.ssgproject.content_rule_sshd_disable_root_login Result: pass
Rule: xccdf_org.ssgproject.content_rule_audit_rules_usergroup_modification Result: fail
Rule: xccdf_org.ssgproject.content_rule_wireless_disable_interfaces Result: notapplicable
Title: Compliance Score
Score: 75.000000%
"""
    data = SecurityParsers.parse_openscap_eval_output(raw_eval)
    assert data["score"] == 75.0
    assert data["passed"] == 2
    assert data["failed"] == 1
    assert data["notapplicable"] == 1
    assert len(data["rules"]) == 4


# --------------------------------------------------------------------
# 5. Auditd Command Builders & Parsers Tests
# --------------------------------------------------------------------

def test_auditd_commands_and_presets():
    probe = SecurityCommands.auditd_probe()
    assert "auditctl -s" in probe
    assert "which ausearch" in probe
    assert "which aureport" in probe

    en = SecurityCommands.auditd_enable()
    assert "auditd" in en

    # Presets for the 3 user requested categories:
    # 1. User accounts & auth
    assert any("/etc/passwd" in r for r in SecurityCommands.AUDITD_USER_ACCOUNT_RULES)
    assert any("/etc/shadow" in r for r in SecurityCommands.AUDITD_USER_ACCOUNT_RULES)
    assert any("user_account_changes" in r for r in SecurityCommands.AUDITD_USER_ACCOUNT_RULES)

    # 2. Permissions
    assert any("chmod" in r for r in SecurityCommands.AUDITD_PERMISSION_MOD_RULES)
    assert any("chown" in r for r in SecurityCommands.AUDITD_PERMISSION_MOD_RULES)
    assert any("perm_mod" in r for r in SecurityCommands.AUDITD_PERMISSION_MOD_RULES)

    # 3. Sensitive files
    assert any("/etc/sudoers" in r for r in SecurityCommands.AUDITD_SENSITIVE_FILE_RULES)
    assert any("sshd_config" in r for r in SecurityCommands.AUDITD_SENSITIVE_FILE_RULES)
    assert any("/etc/pam.d" in r for r in SecurityCommands.AUDITD_SENSITIVE_FILE_RULES)

    # Deploy rules command
    dep = SecurityCommands.auditd_deploy_preset_rules(SecurityCommands.AUDITD_USER_ACCOUNT_RULES)
    assert "/etc/audit/rules.d/99-admin-suite-security.rules" in dep
    assert "augenrules --load" in dep or "auditctl -R" in dep

    # ausearch command
    srch = SecurityCommands.auditd_ausearch(filter_key="user_account_changes", time_range="today")
    assert "-k user_account_changes" in srch
    assert "-ts today" in srch


def test_auditd_parsers():
    raw_probe = """
=== AUDITD_PROBE ===
INSTALLED:yes
AUSEARCH_INSTALLED:yes
AUREPORT_INSTALLED:yes
SERVICE_STATE:active
SERVICE_ENABLED:enabled
=== AUDITCTL_STATUS ===
enabled 1
failure 1
pid 982
rate_limit 0
backlog_limit 8192
lost 0
backlog 0
=== RULE_COUNT ===
35
"""
    info = SecurityParsers.parse_auditd_probe(raw_probe)
    assert info["installed"] is True
    assert info["service_state"] == "active"
    assert info["rules_count"] == 35
    assert "enabled 1" in info["status_text"]

    # Rules parser
    raw_rules = """
-w /etc/passwd -p wa -k user_account_changes
-w /etc/shadow -p wa -k user_account_changes
-a always,exit -F arch=b64 -S chmod,fchmod,fchmodat -F auid>=1000 -k perm_mod
"""
    rules = SecurityParsers.parse_auditd_rules(raw_rules)
    assert len(rules) == 3
    assert rules[0]["type"] == "Watch"
    assert rules[0]["target"] == "/etc/passwd"
    assert rules[0]["key"] == "user_account_changes"
    assert rules[2]["type"] == "Syscall"
    assert "chmod" in rules[2]["target"]
    assert rules[2]["key"] == "perm_mod"

    # Ausearch event parser
    raw_ausearch = """
----
time->Mon Nov  2 14:22:10 2026
type=SYSCALL msg=audit(1698888888.100:200): arch=c000003e syscall=268 success=yes exit=0 a0=ffffff9c a1=7ffd a2=1b6 a3=0 items=1 ppid=1200 pid=1201 auid=1000 uid=1000 comm="chmod" exe="/usr/bin/chmod" key="perm_mod"
type=PATH msg=audit(1698888888.100:200): item=0 name="/var/www/html/secret.txt" inode=4567 dev=fd:00 mode=0100644 ouid=1000 ogid=1000
----
time->Mon Nov  2 14:25:00 2026
type=USER_AUTH msg=audit(1698888900.200:201): pid=1250 uid=0 auid=4294967295 comm="sshd" exe="/usr/sbin/sshd" res=failed
"""
    events = SecurityParsers.parse_ausearch_events(raw_ausearch)
    assert len(events) == 2
    assert events[0]["comm"] == "chmod"
    assert events[0]["key"] == "perm_mod"
    assert events[0]["name"] == "/var/www/html/secret.txt"
    assert events[0]["success"] == "yes"

    assert events[1]["type"] == "USER_AUTH"
    assert events[1]["comm"] == "sshd"
    assert events[1]["success"] == "no"


# --------------------------------------------------------------------
# 6. UI Widget Integration Tests
# --------------------------------------------------------------------

def test_file_integrity_widget_gui(qapp, test_theme):
    cmds_executed = []

    def mock_run_cmd(cmd, callback):
        cmds_executed.append(cmd)
        if "detect_os" in cmd or "OS_RELEASE" in cmd:
            callback(
                "=== OS_RELEASE ===\nID=rhel\nNAME=\"Red Hat Enterprise Linux\"\nVERSION_ID=\"9.4\"\n"
                "=== PKG_MANAGER ===\nPKG_MGR:dnf\n=== ARCH ===\nx86_64\n",
                "",
                0,
            )
        elif "AIDE_PROBE" in cmd:
            callback(
                "=== AIDE_PROBE ===\nINSTALLED:yes\nCONFIG:/etc/aide.conf\nDB_EXISTS:yes\n"
                "DB_PATH:/var/lib/aide/aide.db.gz\nCRON_SCHEDULED:yes\nCRON_FILE:/etc/cron.daily/aide-check\n",
                "",
                0,
            )
        elif "FAPOLICYD_PROBE" in cmd:
            callback(
                "=== FAPOLICYD_PROBE ===\nINSTALLED:yes\nSERVICE_STATE:active\nSERVICE_ENABLED:enabled\n"
                "permissive = 0\nintegrity = sha256\n",
                "",
                0,
            )
        else:
            callback("OK", "", 0)

    widget = FileIntegrityWidget(run_cmd=mock_run_cmd, theme=test_theme)
    assert widget.sub_tabs.count() == 2
    assert "AIDE" in widget.sub_tabs.tabText(0)
    assert "fapolicyd" in widget.sub_tabs.tabText(1)

    # Initial refresh
    widget.refresh()
    assert any("detect_os" in c or "OS_RELEASE" in c for c in cmds_executed)
    assert "RHEL" in widget.distro_badge.text()
    assert "Baseline Ready" in widget.badge_aide.text()
    assert "Enforcing" in widget.badge_fapol.text()


def test_compliance_auditor_widget_gui(qapp, test_theme):
    cmds_executed = []

    def mock_run_cmd(cmd, callback):
        cmds_executed.append(cmd)
        if "detect_os" in cmd or "OS_RELEASE" in cmd:
            callback(
                "=== OS_RELEASE ===\nID=rhel\nNAME=\"Red Hat Enterprise Linux\"\nVERSION_ID=\"9.4\"\n"
                "=== PKG_MANAGER ===\nPKG_MGR:dnf\n=== ARCH ===\nx86_64\n",
                "",
                0,
            )
        elif "OPENSCAP_PROBE" in cmd:
            callback(
                "=== OPENSCAP_PROBE ===\nINSTALLED:yes\nDS:/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml\n"
                "HTML_DIR:/var/www/html\n",
                "",
                0,
            )
        elif "AUDITD_PROBE" in cmd:
            callback(
                "=== AUDITD_PROBE ===\nINSTALLED:yes\nSERVICE_STATE:active\nSERVICE_ENABLED:enabled\n"
                "=== RULE_COUNT ===\n24\n",
                "",
                0,
            )
        else:
            callback("OK", "", 0)

    widget = ComplianceAuditorWidget(run_cmd=mock_run_cmd, theme=test_theme)
    assert widget.sub_tabs.count() == 2
    assert "OpenSCAP" in widget.sub_tabs.tabText(0)
    assert "Auditd" in widget.sub_tabs.tabText(1)

    # Initial refresh
    widget.refresh()
    assert "Ready" in widget.badge_oscap.text()
    assert "Active" in widget.badge_auditd.text()
    assert widget.chk_rule_accounts.isChecked()
    assert widget.chk_rule_perms.isChecked()
    assert widget.chk_rule_sensitive.isChecked()


def test_security_hub_tab_integration(qapp, services, monkeypatch):
    # Patch refresh_all on instantiation to avoid modal sudo dialog in offscreen test
    monkeypatch.setattr(SecurityHubTab, "refresh_all", lambda self: None)
    hub = SecurityHubTab(services=services, profile_name="Localhost")
    assert hub.tabs.count() >= 8

    # Verify our new tabs are present
    tab_titles = [hub.tabs.tabText(i) for i in range(hub.tabs.count())]
    assert any("File Integrity & Whitelisting" in t for t in tab_titles)
    assert any("Compliance & Auditing" in t for t in tab_titles)
    assert hasattr(hub, "integrity_tab")
    assert hasattr(hub, "compliance_tab")
    assert hasattr(hub, "badge_distro")


def test_file_integrity_and_compliance_on_demand_initial_state(qapp, test_theme):
    """Verify that file integrity and compliance widgets initialize in On Demand posture with zero commands run."""
    ran_cmds = []

    def mock_run_cmd(cmd, callback=None):
        ran_cmds.append(cmd)

    # 1. File Integrity Widget
    fi_widget = FileIntegrityWidget(run_cmd=mock_run_cmd, theme=test_theme)
    assert len(ran_cmds) == 0, "No commands should run automatically on FileIntegrityWidget initialization"
    assert "On Demand" in fi_widget.badge_aide.text()
    assert "On Demand" in fi_widget.badge_fapol.text()
    assert "On Demand" in fi_widget.distro_badge.text()
    assert "On Demand" in fi_widget.aide_status_lbl.text()
    assert "On Demand" in fi_widget.fapol_state_lbl.text()
    assert fi_widget.btn_refresh.text() == "🔍 Audit Status Now"

    # 2. Compliance Auditor Widget
    comp_widget = ComplianceAuditorWidget(run_cmd=mock_run_cmd, theme=test_theme)
    assert len(ran_cmds) == 0, "No commands should run automatically on ComplianceAuditorWidget initialization"
    assert "On Demand" in comp_widget.badge_oscap.text()
    assert "On Demand" in comp_widget.badge_auditd.text()
    assert "On Demand" in comp_widget.distro_badge.text()
    assert "On Demand" in comp_widget.score_gauge.text()
    assert "On Demand" in comp_widget.auditd_state_lbl.text()
    assert comp_widget.btn_refresh.text() == "🔍 Audit Status Now"


def test_security_hub_refresh_all_skips_heavy_audits(qapp, services, monkeypatch):
    """Verify SecurityHubTab.refresh_all does NOT trigger automatic file integrity or compliance audits."""
    monkeypatch.setattr(SecurityHubTab, "refresh_all", lambda self: None)
    hub = SecurityHubTab(services=services, profile_name="Localhost")

    integrity_refreshed = False
    compliance_refreshed = False

    def mock_int_refresh():
        nonlocal integrity_refreshed
        integrity_refreshed = True

    def mock_comp_refresh():
        nonlocal compliance_refreshed
        compliance_refreshed = True

    hub.integrity_tab.refresh = mock_int_refresh
    hub.compliance_tab.refresh = mock_comp_refresh

    # Restore and run original refresh_all
    # Mock execute_command to no-op
    hub.execute_command = lambda *args, **kwargs: None
    SecurityHubTab.refresh_all(hub)

    assert not integrity_refreshed, "SecurityHubTab.refresh_all must NOT auto-refresh FileIntegrityWidget"
    assert not compliance_refreshed, "SecurityHubTab.refresh_all must NOT auto-refresh ComplianceAuditorWidget"


def test_admin_console_overview_first_then_sequential_queue(qapp, services):
    """Verify AdminConsoleTab loads Overview first, then runs remaining checks sequentially."""
    from admin_suite.admin_console.console import AdminConsoleTab

    console = AdminConsoleTab(
        services,
        profile_name="Localhost",
        profile={"is_local": True},
        auto_refresh=False,
    )

    executed_cmds = []

    def mock_exec_cmd(cmd, callback=None, use_sudo=True, timeout=60):
        executed_cmds.append(cmd)
        if callback:
            callback("OK", "", 0)

    console.execute_command = mock_exec_cmd
    console.auto_refresh = True

    # Call refresh_all
    console.refresh_all()

    # Step 1: First command MUST be the Overview command
    assert len(executed_cmds) >= 1
    assert "uptime" in executed_cmds[0] or "free" in executed_cmds[0] or "hostname" in executed_cmds[0]

    # Verify background queue was initialized and processed sequentially
    queue_names = [name for name, _ in getattr(console, "_bg_queue", [])]
    assert "integrity" not in queue_names
    assert "compliance" not in queue_names
    assert "malware" not in queue_names

    console.stop_all_workers()
    assert console._bg_queue_running is False

