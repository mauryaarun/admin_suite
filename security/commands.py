"""
Security Subsystem Command Generators and Parsers.
Handles detection, configuration, and analysis for Firewall (UFW/Firewalld),
Fail2ban, Network Exposure, SSH Hardening, and System Security Auditing.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Tuple


class SecurityCommands:
    """Shell command builders for system security operations."""

    # ---------------- Firewall Commands ----------------
    @staticmethod
    def detect_firewall() -> str:
        return (
            "if which ufw >/dev/null 2>&1; then echo 'TYPE:ufw'; "
            "elif which firewall-cmd >/dev/null 2>&1; then echo 'TYPE:firewalld'; "
            "elif which nft >/dev/null 2>&1; then echo 'TYPE:nftables'; "
            "elif which iptables >/dev/null 2>&1; then echo 'TYPE:iptables'; "
            "else echo 'TYPE:none'; fi"
        )

    @staticmethod
    def get_ufw_status() -> str:
        return "ufw status numbered verbose"

    @staticmethod
    def get_firewalld_status() -> str:
        return "firewall-cmd --state && firewall-cmd --list-all"

    @staticmethod
    def ufw_enable() -> str:
        return "ufw --force enable"

    @staticmethod
    def ufw_disable() -> str:
        return "ufw disable"

    @staticmethod
    def ufw_reload() -> str:
        return "ufw reload"

    @staticmethod
    def ufw_add_rule(
        port_or_service: str,
        proto: str = "tcp",
        action: str = "allow",
        from_ip: str = "any",
        comment: str = "",
    ) -> str:
        """
        Build a secure UFW rule insertion command.
        action: 'allow' or 'deny' or 'reject'
        from_ip: 'any' or an IP/CIDR (e.g., '192.168.1.50' or '10.0.0.0/24')
        """
        cmd_parts = ["ufw", action.lower()]
        from_clean = from_ip.strip()

        if from_clean and from_clean.lower() != "any":
            cmd_parts.extend(["from", from_clean, "to", "any"])

        port_clean = port_or_service.strip()
        proto_clean = proto.strip().lower()

        if port_clean:
            if proto_clean and proto_clean != "any":
                cmd_parts.extend(["port", port_clean, "proto", proto_clean])
            else:
                cmd_parts.extend(["port", port_clean])

        if comment:
            clean_comment = re.sub(r'[^a-zA-Z0-9_\-\. ]', '', comment)
            cmd_parts.extend(["comment", shlex.quote(clean_comment)])

        return " ".join(cmd_parts)

    @staticmethod
    def ufw_delete_rule_num(rule_num: int) -> str:
        return f"ufw --force delete {int(rule_num)}"

    # ---------------- Firewalld Commands (RHEL / CentOS / Alma / Rocky / Fedora) ----------------
    @staticmethod
    def firewalld_status(zone: str = "") -> str:
        if zone:
            clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone)
            return f"firewall-cmd --state && echo '=== ACTIVE_ZONES ===' && firewall-cmd --get-active-zones && echo '=== ZONE_DETAILS ===' && firewall-cmd --zone={clean_zone} --list-all"
        return "firewall-cmd --state && echo '=== ACTIVE_ZONES ===' && firewall-cmd --get-active-zones && echo '=== ZONE_DETAILS ===' && firewall-cmd --list-all"

    @staticmethod
    def firewalld_enable() -> str:
        return "systemctl unmask firewalld 2>/dev/null; systemctl enable --now firewalld"

    @staticmethod
    def firewalld_disable() -> str:
        return "systemctl stop firewalld && systemctl disable firewalld"

    @staticmethod
    def firewalld_reload() -> str:
        return "firewall-cmd --reload"

    @staticmethod
    def firewalld_add_port(
        port: str,
        proto: str = "tcp",
        zone: str = "public",
        permanent: bool = True,
    ) -> str:
        clean_port = re.sub(r'[^0-9\-:]', '', port.strip())
        clean_proto = proto.strip().lower()
        if clean_proto not in ("tcp", "udp", "sctp", "dccp"):
            clean_proto = "tcp"
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--add-port={clean_port}/{clean_proto} && firewall-cmd --reload"

    @staticmethod
    def firewalld_remove_port(
        port: str,
        proto: str = "tcp",
        zone: str = "public",
        permanent: bool = True,
    ) -> str:
        clean_port = re.sub(r'[^0-9\-:]', '', port.strip())
        clean_proto = proto.strip().lower()
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--remove-port={clean_port}/{clean_proto} && firewall-cmd --reload"

    @staticmethod
    def firewalld_add_service(
        service: str,
        zone: str = "public",
        permanent: bool = True,
    ) -> str:
        clean_srv = re.sub(r'[^a-zA-Z0-9_\-]', '', service.strip().lower())
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--add-service={clean_srv} && firewall-cmd --reload"

    @staticmethod
    def firewalld_remove_service(
        service: str,
        zone: str = "public",
        permanent: bool = True,
    ) -> str:
        clean_srv = re.sub(r'[^a-zA-Z0-9_\-]', '', service.strip().lower())
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--remove-service={clean_srv} && firewall-cmd --reload"

    @staticmethod
    def firewalld_deny_port(
        port: str,
        proto: str = "tcp",
        zone: str = "public",
    ) -> str:
        """Deny incoming traffic on a port using a rich rule."""
        clean_port = re.sub(r'[^0-9\-:]', '', port.strip())
        clean_proto = proto.strip().lower()
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        rich = f'rule port port="{clean_port}" protocol="{clean_proto}" reject'
        return f"firewall-cmd --zone={clean_zone} --permanent --add-rich-rule='{rich}' && firewall-cmd --reload"

    @staticmethod
    def firewalld_add_rich_rule(rule: str, zone: str = "public", permanent: bool = True) -> str:
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--add-rich-rule={shlex.quote(rule)} && firewall-cmd --reload"

    @staticmethod
    def firewalld_remove_rich_rule(rule: str, zone: str = "public", permanent: bool = True) -> str:
        clean_zone = re.sub(r'[^a-zA-Z0-9_\-]', '', zone.strip()) or "public"
        perm_flag = "--permanent " if permanent else ""
        return f"firewall-cmd --zone={clean_zone} {perm_flag}--remove-rich-rule={shlex.quote(rule)} && firewall-cmd --reload"

    @staticmethod
    def firewalld_get_zones() -> str:
        return "firewall-cmd --get-zones"

    # ---------------- iptables Legacy Commands ----------------
    @staticmethod
    def iptables_status() -> str:
        return "iptables -L -n -v --line-numbers 2>/dev/null || ip6tables -L -n -v --line-numbers 2>/dev/null"

    @staticmethod
    def iptables_drop_port(port: int, proto: str = "tcp") -> str:
        return f"iptables -I INPUT -p {shlex.quote(proto)} --dport {int(port)} -j DROP"

    # ---------------- Active Connections & Remote Traffic ----------------
    @staticmethod
    def list_active_connections() -> str:
        return (
            "echo '=== ESTABLISHED_CONNECTIONS ==='; "
            "if which ss >/dev/null 2>&1; then "
            "  ss -ntu state established; "
            "else "
            "  netstat -ntu 2>/dev/null | grep ESTABLISHED; "
            "fi; "
            "echo '=== TOP_REMOTE_IPS ==='; "
            "if which ss >/dev/null 2>&1; then "
            "  ss -ntu state established 2>/dev/null | awk '{print $5}' | cut -d: -f1 | grep -v 'Local' | grep -v 'Address' | grep -v '^$' | sort | uniq -c | sort -nr | head -25; "
            "else "
            "  netstat -ntu 2>/dev/null | grep ESTABLISHED | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -nr | head -25; "
            "fi"
        )

    # ---------------- Malware, Rootkit & File Integrity Tools ----------------
    @staticmethod
    def malware_tools_probe() -> str:
        return (
            "echo '=== SCANNER_PROBE ==='; "
            "which clamscan >/dev/null 2>&1 && echo 'CLAMAV:yes' || echo 'CLAMAV:no'; "
            "which rkhunter >/dev/null 2>&1 && echo 'RKHUNTER:yes' || echo 'RKHUNTER:no'; "
            "which chkrootkit >/dev/null 2>&1 && echo 'CHKROOTKIT:yes' || echo 'CHKROOTKIT:no'; "
            "which freshclam >/dev/null 2>&1 && echo 'FRESHCLAM:yes' || echo 'FRESHCLAM:no'"
        )

    @staticmethod
    def scan_suid_sgid_files() -> str:
        """Find dangerous SUID/SGID binaries on root filesystem."""
        return (
            "find / -xdev \\( -perm -4000 -o -perm -2000 \\) -type f -exec ls -la {} + 2>/dev/null | head -60"
        )

    @staticmethod
    def scan_world_writable_dirs() -> str:
        """Find world-writable directories that could be exploited for privilege escalation."""
        return (
            "find /etc /var/www /opt /usr/local -maxdepth 3 -type d -perm -0002 2>/dev/null | head -40"
        )

    @staticmethod
    def run_clamscan_quick(target_dir: str = "/var/www") -> str:
        clean_dir = shlex.quote(target_dir.strip() or "/var/www")
        return f"clamscan -r --bell -i --max-filesize=25M --max-scansize=100M {clean_dir} 2>&1"

    @staticmethod
    def run_rkhunter_quick() -> str:
        return "rkhunter --check --sk --rwo 2>&1 || true"

    # ---------------- SSL/TLS Certificate Auditor ----------------
    @staticmethod
    def audit_all_ssl_certs() -> str:
        return (
            "echo '=== SSL_CERTS_AUDIT ==='; "
            "for cert in /etc/letsencrypt/live/*/cert.pem /etc/pki/tls/certs/*.crt /etc/ssl/certs/*.pem /etc/nginx/ssl/*.crt; do "
            "  if [ -f \"$cert\" ]; then "
            "    subj=$(openssl x509 -in \"$cert\" -noout -subject 2>/dev/null | sed 's/subject= //'); "
            "    issuer=$(openssl x509 -in \"$cert\" -noout -issuer 2>/dev/null | sed 's/issuer= //'); "
            "    enddate=$(openssl x509 -in \"$cert\" -noout -enddate 2>/dev/null | cut -d= -f2); "
            "    keysize=$(openssl x509 -in \"$cert\" -noout -text 2>/dev/null | grep -E 'Public Key:|RSA Public-Key:' | head -1 | tr -d ' '); "
            "    datesec=$(date -d \"$enddate\" +%s 2>/dev/null || echo '0'); "
            "    echo \"$cert|$subj|$issuer|$enddate|$keysize|$datesec\"; "
            "  fi; "
            "done"
        )

    # ---------------- Fail2ban Commands ----------------
    @staticmethod
    def fail2ban_status() -> str:
        return "fail2ban-client status"

    @staticmethod
    def fail2ban_jail_status(jail: str) -> str:
        clean_jail = re.sub(r'[^a-zA-Z0-9_\-]', '', jail)
        return f"fail2ban-client status {clean_jail}"

    @staticmethod
    def fail2ban_unban(jail: str, ip: str) -> str:
        clean_jail = re.sub(r'[^a-zA-Z0-9_\-]', '', jail)
        clean_ip = re.sub(r'[^a-fA-F0-9\.:]', '', ip)
        return f"fail2ban-client set {clean_jail} unbanip {clean_ip}"

    @staticmethod
    def fail2ban_ban(jail: str, ip: str) -> str:
        clean_jail = re.sub(r'[^a-zA-Z0-9_\-]', '', jail)
        clean_ip = re.sub(r'[^a-fA-F0-9\.:]', '', ip)
        return f"fail2ban-client set {clean_jail} banip {clean_ip}"

    @staticmethod
    def fail2ban_reload() -> str:
        return "fail2ban-client reload"

    # ---------------- Port Exposure Commands ----------------
    @staticmethod
    def list_listening_sockets() -> str:
        return (
            "if which ss >/dev/null 2>&1; then "
            "  ss -tulpn; "
            "else "
            "  netstat -tulpn 2>/dev/null || netstat -tuln; "
            "fi"
        )

    # ---------------- SSH & Hardening Commands ----------------
    @staticmethod
    def read_sshd_config() -> str:
        return (
            "if [ -f /etc/ssh/sshd_config ]; then "
            "  cat /etc/ssh/sshd_config; "
            "  if [ -d /etc/ssh/sshd_config.d ]; then "
            "    cat /etc/ssh/sshd_config.d/*.conf 2>/dev/null || true; "
            "  fi; "
            "fi"
        )

    @staticmethod
    def system_audit_probe() -> str:
        """Runs a fast multi-check audit script returning structured output."""
        return (
            "echo '=== AUDIT_START ==='; "
            "echo -n 'KERNEL_SYNCOOKIES:'; sysctl -n net.ipv4.tcp_syncookies 2>/dev/null || echo 'unknown'; "
            "echo -n 'KERNEL_IPFORWARD:'; sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 'unknown'; "
            "echo -n 'SHADOW_PERMS:'; stat -c '%a' /etc/shadow 2>/dev/null || stat -f '%Lp' /etc/shadow 2>/dev/null || echo 'unknown'; "
            "echo -n 'PASSWD_PERMS:'; stat -c '%a' /etc/passwd 2>/dev/null || stat -f '%Lp' /etc/passwd 2>/dev/null || echo 'unknown'; "
            "echo -n 'LYNIS_AVAIL:'; which lynis >/dev/null 2>&1 && echo 'yes' || echo 'no'; "
            "echo -n 'FAIL2BAN_SERVICE:'; systemctl is-active fail2ban 2>/dev/null || echo 'unknown'; "
            "echo -n 'UFW_SERVICE:'; systemctl is-active ufw 2>/dev/null || echo 'unknown'; "
            "echo -n 'ROOT_SUDO_USERS:'; getent group sudo wheel 2>/dev/null | cut -d: -f4 | tr ',' ' ' || echo ''; "
            "echo '=== AUDIT_END ==='"
        )

    # ---------------- Distribution & Environment Detection ----------------
    @staticmethod
    def detect_os() -> str:
        return (
            "echo '=== OS_RELEASE ==='; "
            "if [ -f /etc/os-release ]; then "
            "  cat /etc/os-release; "
            "elif [ -f /etc/redhat-release ]; then "
            "  echo 'ID=rhel'; echo 'NAME=\"Red Hat Enterprise Linux\"'; "
            "elif [ -f /etc/debian_version ]; then "
            "  echo 'ID=debian'; echo 'NAME=\"Debian GNU/Linux\"'; "
            "else "
            "  echo 'ID=generic'; echo 'NAME=\"Linux\"'; "
            "fi; "
            "echo '=== PKG_MANAGER ==='; "
            "for pm in dnf yum apt-get pacman zypper; do "
            "  if which $pm >/dev/null 2>&1; then echo \"PKG_MGR:$pm\"; break; fi; "
            "done; "
            "echo '=== ARCH ==='; "
            "uname -m"
        )

    @staticmethod
    def install_packages_cmd(packages: List[str], pkg_manager: str = "auto") -> str:
        pkgs_clean = " ".join(shlex.quote(p) for p in packages if p)
        if pkg_manager in ("dnf", "yum"):
            return f"{pkg_manager} install -y {pkgs_clean}"
        elif pkg_manager in ("apt", "apt-get", "debian"):
            return f"DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs_clean}"
        elif pkg_manager == "zypper":
            return f"zypper install -y {pkgs_clean}"
        elif pkg_manager == "pacman":
            return f"pacman -S --noconfirm {pkgs_clean}"
        else:
            return (
                f"if which dnf >/dev/null 2>&1; then dnf install -y {pkgs_clean}; "
                f"elif which apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs_clean}; "
                f"elif which yum >/dev/null 2>&1; then yum install -y {pkgs_clean}; "
                f"elif which zypper >/dev/null 2>&1; then zypper install -y {pkgs_clean}; "
                f"elif which pacman >/dev/null 2>&1; then pacman -S --noconfirm {pkgs_clean}; "
                f"else echo 'Unknown package manager'; exit 1; fi"
            )

    # ---------------- AIDE (File Integrity Monitoring) ----------------
    @staticmethod
    def aide_probe() -> str:
        return (
            "echo '=== AIDE_PROBE ==='; "
            "which aide >/dev/null 2>&1 && echo 'INSTALLED:yes' || echo 'INSTALLED:no'; "
            "which aideinit >/dev/null 2>&1 && echo 'AIDEINIT:yes' || echo 'AIDEINIT:no'; "
            "if [ -f /etc/aide.conf ]; then echo 'CONFIG:/etc/aide.conf'; "
            "elif [ -f /etc/aide/aide.conf ]; then echo 'CONFIG:/etc/aide/aide.conf'; "
            "else echo 'CONFIG:none'; fi; "
            "db_found='no'; "
            "for db in /var/lib/aide/aide.db.gz /var/lib/aide/aide.db; do "
            "  if [ -f \"$db\" ]; then "
            "    echo 'DB_EXISTS:yes'; "
            "    echo \"DB_PATH:$db\"; "
            "    ls -lh \"$db\" 2>/dev/null | awk '{print \"DB_STAT:\"$5\"|\"$6\" \"$7\" \"$8}'; "
            "    db_found='yes'; break; "
            "  fi; "
            "done; "
            "if [ \"$db_found\" = 'no' ]; then echo 'DB_EXISTS:no'; fi; "
            "if [ -f /var/lib/aide/aide.db.new.gz ] || [ -f /var/lib/aide/aide.db.new ]; then "
            "  echo 'DB_NEW_EXISTS:yes'; "
            "else "
            "  echo 'DB_NEW_EXISTS:no'; "
            "fi; "
            "if [ -f /etc/cron.daily/aide-check ] || [ -f /etc/cron.daily/aide ] || (crontab -l 2>/dev/null | grep -q 'aide'); then "
            "  echo 'CRON_SCHEDULED:yes'; "
            "  if [ -f /etc/cron.daily/aide-check ]; then echo 'CRON_FILE:/etc/cron.daily/aide-check'; "
            "  elif [ -f /etc/cron.daily/aide ]; then echo 'CRON_FILE:/etc/cron.daily/aide'; "
            "  else echo 'CRON_FILE:crontab'; fi; "
            "else "
            "  echo 'CRON_SCHEDULED:no'; "
            "fi; "
            "if [ -f /var/log/aide/aide.log ]; then "
            "  echo 'LOG_EXISTS:yes'; "
            "  ls -lh /var/log/aide/aide.log 2>/dev/null | awk '{print \"LOG_STAT:\"$5\"|\"$6\" \"$7\" \"$8}'; "
            "else "
            "  echo 'LOG_EXISTS:no'; "
            "fi"
        )

    @staticmethod
    def aide_init(family: str = "auto") -> str:
        return (
            "mkdir -p /var/lib/aide /var/log/aide; "
            "if which aideinit >/dev/null 2>&1; then "
            "  aideinit -y -f 2>&1; "
            "elif which aide >/dev/null 2>&1; then "
            "  aide --init 2>&1 && "
            "  ( [ -f /var/lib/aide/aide.db.new.gz ] && cp -f /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz || "
            "    [ -f /var/lib/aide/aide.db.new ] && cp -f /var/lib/aide/aide.db.new /var/lib/aide/aide.db || true ) && "
            "  echo 'AIDE baseline database initialized successfully.'; "
            "else "
            "  echo 'Error: aide binary not found'; exit 1; "
            "fi"
        )

    @staticmethod
    def aide_check() -> str:
        return "aide --check 2>&1 || true"

    @staticmethod
    def aide_update() -> str:
        return (
            "aide --update 2>&1 && "
            "( [ -f /var/lib/aide/aide.db.new.gz ] && cp -f /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz || "
            "  [ -f /var/lib/aide/aide.db.new ] && cp -f /var/lib/aide/aide.db.new /var/lib/aide/aide.db || true ) && "
            "echo 'AIDE baseline database updated successfully.'"
        )

    @staticmethod
    def aide_setup_cron() -> str:
        script = (
            "#!/bin/bash\\n"
            "# Automated AIDE Integrity Check generated by Admin Suite\\n"
            "mkdir -p /var/log/aide\\n"
            "LOGFILE=/var/log/aide/aide.log\\n"
            "echo \\\"=== AIDE Check Run: $(date) ===\\\" >> \\\"$LOGFILE\\\"\\n"
            "/usr/sbin/aide --check >> \\\"$LOGFILE\\\" 2>&1\\n"
        )
        return (
            f"mkdir -p /etc/cron.daily && "
            f"printf '{script}' > /etc/cron.daily/aide-check && "
            f"chmod 755 /etc/cron.daily/aide-check && echo 'Daily AIDE cron check installed at /etc/cron.daily/aide-check'"
        )

    @staticmethod
    def aide_remove_cron() -> str:
        return "rm -f /etc/cron.daily/aide-check && echo 'AIDE daily cron check removed.'"

    @staticmethod
    def aide_read_log(lines: int = 150) -> str:
        return f"tail -n {int(lines)} /var/log/aide/aide.log 2>/dev/null || echo 'No AIDE log file found.'"

    # ---------------- fapolicyd (Application Whitelisting) ----------------
    @staticmethod
    def fapolicyd_probe() -> str:
        return (
            "echo '=== FAPOLICYD_PROBE ==='; "
            "which fapolicyd >/dev/null 2>&1 && echo 'INSTALLED:yes' || echo 'INSTALLED:no'; "
            "which fapolicyd-cli >/dev/null 2>&1 && echo 'CLI_INSTALLED:yes' || echo 'CLI_INSTALLED:no'; "
            "st=$(systemctl is-active fapolicyd 2>/dev/null || echo 'inactive'); echo \"SERVICE_STATE:$st\"; "
            "en=$(systemctl is-enabled fapolicyd 2>/dev/null || echo 'disabled'); echo \"SERVICE_ENABLED:$en\"; "
            "if [ -f /etc/fapolicyd/fapolicyd.conf ]; then "
            "  echo 'CONFIG_EXISTS:yes'; "
            "  grep -E '^(permissive|integrity)=' /etc/fapolicyd/fapolicyd.conf 2>/dev/null || true; "
            "else "
            "  echo 'CONFIG_EXISTS:no'; "
            "fi; "
            "if which fapolicyd-cli >/dev/null 2>&1; then "
            "  echo '=== FAPOLICYD_STATUS ==='; "
            "  fapolicyd-cli --status 2>/dev/null || true; "
            "fi"
        )

    @staticmethod
    def fapolicyd_enable() -> str:
        return "systemctl unmask fapolicyd 2>/dev/null; systemctl enable --now fapolicyd"

    @staticmethod
    def fapolicyd_disable() -> str:
        return "systemctl stop fapolicyd && systemctl disable fapolicyd"

    @staticmethod
    def fapolicyd_set_mode(permissive: bool = False) -> str:
        mode_val = "1" if permissive else "0"
        return (
            f"if [ -f /etc/fapolicyd/fapolicyd.conf ]; then "
            f"  sed -i -E 's/^[#\\s]*permissive\\s*=.*/permissive = {mode_val}/' /etc/fapolicyd/fapolicyd.conf; "
            f"  systemctl restart fapolicyd 2>/dev/null || true; "
            f"  echo 'fapolicyd mode set to permissive={mode_val} and service restarted.'; "
            f"else "
            f"  echo 'fapolicyd.conf not found'; exit 1; "
            f"fi"
        )

    @staticmethod
    def fapolicyd_update_db() -> str:
        return "fapolicyd-cli --update"

    @staticmethod
    def fapolicyd_list_trust() -> str:
        return "fapolicyd-cli --list 2>/dev/null | head -n 250"

    @staticmethod
    def fapolicyd_check_config() -> str:
        return "fapolicyd-cli --check-config 2>&1"

    @staticmethod
    def fapolicyd_trust_file(path: str) -> str:
        clean = shlex.quote(path.strip())
        return (
            f"mkdir -p /etc/fapolicyd && "
            f"echo {clean} >> /etc/fapolicyd/trust.file && "
            f"fapolicyd-cli --update && echo 'Path {path} added to fapolicyd trust database.'"
        )

    @staticmethod
    def fapolicyd_get_denials(lines: int = 50) -> str:
        return (
            f"if which ausearch >/dev/null 2>&1; then "
            f"  ausearch -m FANOTIFY -ts today 2>/dev/null | tail -n {int(lines)}; "
            f"else "
            f"  journalctl -u fapolicyd -n {int(lines)} --no-pager 2>/dev/null; "
            f"fi"
        )

    # ---------------- OpenSCAP (Compliance Automation) ----------------
    @staticmethod
    def openscap_probe() -> str:
        return (
            "echo '=== OPENSCAP_PROBE ==='; "
            "which oscap >/dev/null 2>&1 && echo 'INSTALLED:yes' || echo 'INSTALLED:no'; "
            "echo '=== DATASTREAMS ==='; "
            "for p in /usr/share/xml/scap/ssg/content/*-ds.xml /usr/share/scap-security-guide/*-ds.xml; do "
            "  if [ -f \"$p\" ]; then echo \"DS:$p\"; fi; "
            "done; "
            "echo '=== HTML_DIR ==='; "
            "if [ -d /var/www/html ]; then echo 'HTML_DIR:/var/www/html'; else echo 'HTML_DIR:/tmp'; fi"
        )

    @staticmethod
    def openscap_list_profiles(datastream_path: str) -> str:
        clean_ds = shlex.quote(datastream_path.strip())
        return f"oscap info {clean_ds} 2>/dev/null"

    @staticmethod
    def openscap_eval(
        profile: str,
        datastream_path: str,
        report_path: str = "/var/www/html/rhel-cis-report.html",
        results_path: str = "",
    ) -> str:
        clean_profile = shlex.quote(profile.strip())
        clean_ds = shlex.quote(datastream_path.strip())
        clean_rep = shlex.quote(report_path.strip())
        res_arg = f"--results {shlex.quote(results_path.strip())} " if results_path.strip() else ""
        return (
            f"mkdir -p $(dirname {clean_rep}) && "
            f"oscap xccdf eval --profile {clean_profile} --report {clean_rep} {res_arg}{clean_ds} 2>&1; "
            f"echo \"OSCAP_EXIT:$?\""
        )

    @staticmethod
    def openscap_generate_fix(profile: str, datastream_path: str) -> str:
        clean_profile = shlex.quote(profile.strip())
        clean_ds = shlex.quote(datastream_path.strip())
        return f"oscap xccdf generate fix --profile {clean_profile} {clean_ds} 2>&1"

    # ---------------- Auditd (Auditing & Event Tracking) ----------------
    AUDITD_USER_ACCOUNT_RULES = [
        "-w /etc/passwd -p wa -k user_account_changes",
        "-w /etc/shadow -p wa -k user_account_changes",
        "-w /etc/group -p wa -k user_account_changes",
        "-w /etc/gshadow -p wa -k user_account_changes",
        "-w /etc/security/opasswd -p wa -k user_account_changes",
        "-w /var/log/faillog -p wa -k logins",
        "-w /var/log/lastlog -p wa -k logins",
    ]

    AUDITD_PERMISSION_MOD_RULES = [
        "-a always,exit -F arch=b64 -S chmod,fchmod,fchmodat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b64 -S chown,fchown,lchown,fchownat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b64 -S setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b32 -S chmod,fchmod,fchmodat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b32 -S chown,fchown,lchown,fchownat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
    ]

    AUDITD_SENSITIVE_FILE_RULES = [
        "-w /etc/sudoers -p wa -k sudoers_changes",
        "-w /etc/sudoers.d/ -p wa -k sudoers_changes",
        "-w /etc/ssh/sshd_config -p wa -k sshd_config",
        "-w /etc/ssh/sshd_config.d/ -p wa -k sshd_config",
        "-w /etc/pam.d/ -p wa -k pam_mod",
        "-w /etc/crontab -p wa -k cron_changes",
        "-w /etc/cron.d/ -p wa -k cron_changes",
    ]

    AUDITD_CIS_HARDENING_RULES = [
        "-w /etc/passwd -p wa -k user_account_changes",
        "-w /etc/shadow -p wa -k user_account_changes",
        "-w /etc/group -p wa -k user_account_changes",
        "-w /etc/gshadow -p wa -k user_account_changes",
        "-w /etc/security/opasswd -p wa -k user_account_changes",
        "-w /var/log/faillog -p wa -k logins",
        "-w /var/log/lastlog -p wa -k logins",
        "-a always,exit -F arch=b64 -S chmod,fchmod,fchmodat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b64 -S chown,fchown,lchown,fchownat -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-a always,exit -F arch=b64 -S setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr -F auid>=1000 -F auid!=4294967295 -k perm_mod",
        "-w /etc/sudoers -p wa -k sudoers_changes",
        "-w /etc/sudoers.d/ -p wa -k sudoers_changes",
        "-w /etc/ssh/sshd_config -p wa -k sshd_config",
        "-w /etc/ssh/sshd_config.d/ -p wa -k sshd_config",
        "-w /etc/pam.d/ -p wa -k pam_mod",
        "-w /etc/crontab -p wa -k cron_changes",
        "-w /etc/cron.d/ -p wa -k cron_changes",
        "-a always,exit -F arch=b64 -S init_module,delete_module -k kernel_modules",
        "-a always,exit -F arch=b64 -S adjtimex,settimeofday -k time_change",
        "-w /etc/localtime -p wa -k time_change",
    ]

    @staticmethod
    def auditd_probe() -> str:
        return (
            "echo '=== AUDITD_PROBE ==='; "
            "which auditctl >/dev/null 2>&1 && echo 'INSTALLED:yes' || echo 'INSTALLED:no'; "
            "which ausearch >/dev/null 2>&1 && echo 'AUSEARCH_INSTALLED:yes' || echo 'AUSEARCH_INSTALLED:no'; "
            "which aureport >/dev/null 2>&1 && echo 'AUREPORT_INSTALLED:yes' || echo 'AUREPORT_INSTALLED:no'; "
            "st=$(systemctl is-active auditd 2>/dev/null || (service auditd status 2>/dev/null | grep -q running && echo 'active') || echo 'inactive'); "
            "echo \"SERVICE_STATE:$st\"; "
            "en=$(systemctl is-enabled auditd 2>/dev/null || echo 'disabled'); echo \"SERVICE_ENABLED:$en\"; "
            "echo '=== AUDITCTL_STATUS ==='; "
            "auditctl -s 2>/dev/null || true; "
            "echo '=== RULE_COUNT ==='; "
            "auditctl -l 2>/dev/null | grep -v 'No rules' | wc -l"
        )

    @staticmethod
    def auditd_enable() -> str:
        return "systemctl unmask auditd 2>/dev/null; systemctl enable --now auditd 2>/dev/null || service auditd start 2>/dev/null"

    @staticmethod
    def auditd_disable() -> str:
        return "systemctl stop auditd && systemctl disable auditd 2>/dev/null || service auditd stop 2>/dev/null"

    @staticmethod
    def auditd_reload() -> str:
        return (
            "if which augenrules >/dev/null 2>&1; then "
            "  augenrules --load; "
            "elif [ -f /etc/audit/rules.d/99-admin-suite-security.rules ]; then "
            "  auditctl -R /etc/audit/rules.d/99-admin-suite-security.rules; "
            "elif [ -f /etc/audit/audit.rules ]; then "
            "  auditctl -R /etc/audit/audit.rules; "
            "else "
            "  service auditd restart 2>/dev/null || systemctl restart auditd 2>/dev/null || true; "
            "fi"
        )

    @staticmethod
    def auditd_list_rules() -> str:
        return "auditctl -l 2>/dev/null"

    @staticmethod
    def auditd_deploy_preset_rules(rules: List[str]) -> str:
        rules_str = "\\n".join(rules)
        return (
            "mkdir -p /etc/audit/rules.d && "
            f"printf '{rules_str}\\n' > /etc/audit/rules.d/99-admin-suite-security.rules && "
            "( augenrules --load 2>/dev/null || auditctl -R /etc/audit/rules.d/99-admin-suite-security.rules 2>/dev/null || true ) && "
            "echo 'Audit rules successfully deployed and loaded.'"
        )

    @staticmethod
    def auditd_ausearch(filter_key: str = "", time_range: str = "today", lines: int = 60) -> str:
        key_arg = f"-k {shlex.quote(filter_key)} " if filter_key else ""
        return (
            f"ausearch {key_arg}-ts {shlex.quote(time_range)} --format text 2>/dev/null | tail -n {int(lines)} || "
            f"ausearch {key_arg}-ts {shlex.quote(time_range)} 2>/dev/null | tail -n {int(lines)}"
        )

    @staticmethod
    def auditd_aureport(report_type: str = "summary", time_range: str = "today") -> str:
        flag = "-s"
        if report_type == "auth":
            flag = "-au"
        elif report_type == "account":
            flag = "-m"
        elif report_type == "failed":
            flag = "--failed"
        return f"aureport {flag} -ts {shlex.quote(time_range)} 2>/dev/null"


class SecurityParsers:
    """Parsers for security tool outputs."""

    # ---------------- UFW Parser ----------------
    @classmethod
    def parse_ufw_status(cls, raw: str) -> Dict[str, Any]:
        """
        Parses `ufw status numbered verbose` output.
        Returns:
            {
                "active": bool,
                "raw_status": str,
                "default_incoming": str,
                "default_outgoing": str,
                "rules": [
                    {
                        "num": int,
                        "to": str,
                        "action": str,
                        "from": str,
                        "comment": str,
                        "ipv6": bool
                    }
                ]
            }
        """
        result: Dict[str, Any] = {
            "active": False,
            "raw_status": "inactive",
            "default_incoming": "unknown",
            "default_outgoing": "unknown",
            "rules": [],
        }

        if not raw:
            return result

        lines = [line.strip() for line in raw.splitlines() if line.strip()]

        for line in lines:
            if line.startswith("Status:"):
                st = line.split(":", 1)[1].strip().lower()
                result["raw_status"] = st
                result["active"] = "active" in st
            elif line.startswith("Default:"):
                # E.g.: Default: deny (incoming), allow (outgoing), disabled (routed)
                parts = line.split(":", 1)[1].split(",")
                for p in parts:
                    p = p.strip()
                    if "(incoming)" in p:
                        result["default_incoming"] = p.replace("(incoming)", "").strip()
                    elif "(outgoing)" in p:
                        result["default_outgoing"] = p.replace("(outgoing)", "").strip()

        # Parse numbered rules:
        # [ 1] 22/tcp                     ALLOW IN    Anywhere
        # [ 2] 3306/tcp                   DENY IN     10.0.0.0/8             # Block MySQL
        rule_re = re.compile(
            r"^\[\s*(\d+)\]\s+([^\s]+(?:\s*\([^)]+\))?)\s+(ALLOW(?:\s+IN)?|DENY(?:\s+IN)?|REJECT(?:\s+IN)?|LIMIT(?:\s+IN)?)\s+([^\s#]+(?:\s*\([^)]+\))?)(?:\s+#\s*(.*))?$",
            re.IGNORECASE,
        )

        for line in lines:
            m = rule_re.match(line)
            if m:
                num = int(m.group(1))
                to_target = m.group(2).strip()
                action = m.group(3).strip()
                from_target = m.group(4).strip()
                comment = (m.group(5) or "").strip()
                is_v6 = "(v6)" in to_target or "(v6)" in from_target

                result["rules"].append(
                    {
                        "num": num,
                        "to": to_target,
                        "action": action,
                        "from": from_target,
                        "comment": comment,
                        "ipv6": is_v6,
                    }
                )

        return result

    # ---------------- Fail2ban Parsers ----------------
    @classmethod
    def parse_fail2ban_status(cls, raw: str) -> List[str]:
        """
        Parses `fail2ban-client status`.
        Returns list of active jail names (e.g. ['sshd', 'nginx-http-auth']).
        """
        jails: List[str] = []
        for line in raw.splitlines():
            if "jail list:" in line.lower():
                parts = line.split(":", 1)[1].strip()
                if parts:
                    for j in parts.split(","):
                        j_clean = j.strip()
                        if j_clean:
                            jails.append(j_clean)
        return jails

    @classmethod
    def parse_fail2ban_jail_status(cls, raw: str) -> Dict[str, Any]:
        """
        Parses `fail2ban-client status <jail>`.
        Returns stats and list of currently banned IPs.
        """
        data: Dict[str, Any] = {
            "currently_failed": 0,
            "total_failed": 0,
            "currently_banned": 0,
            "total_banned": 0,
            "banned_ips": [],
            "file_list": "",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if "Currently failed:" in line_s:
                try:
                    data["currently_failed"] = int(line_s.split(":")[-1].strip())
                except ValueError:
                    pass
            elif "Total failed:" in line_s:
                try:
                    data["total_failed"] = int(line_s.split(":")[-1].strip())
                except ValueError:
                    pass
            elif "Currently banned:" in line_s:
                try:
                    data["currently_banned"] = int(line_s.split(":")[-1].strip())
                except ValueError:
                    pass
            elif "Total banned:" in line_s:
                try:
                    data["total_banned"] = int(line_s.split(":")[-1].strip())
                except ValueError:
                    pass
            elif "Banned IP list:" in line_s:
                ip_str = line_s.split(":", 1)[-1].strip()
                if ip_str:
                    data["banned_ips"] = [ip.strip() for ip in ip_str.split() if ip.strip()]
            elif "File list:" in line_s:
                data["file_list"] = line_s.split(":", 1)[-1].strip()

        return data

    # ---------------- Listening Sockets & Risk Classification ----------------
    KNOWN_DATABASE_PORTS = {
        3306: ("MySQL / MariaDB", "high"),
        33060: ("MySQL X Protocol", "high"),
        5432: ("PostgreSQL", "high"),
        6379: ("Redis", "high"),
        27017: ("MongoDB", "high"),
        27018: ("MongoDB Shard", "high"),
        11211: ("Memcached", "high"),
        9200: ("Elasticsearch HTTP", "high"),
        9300: ("Elasticsearch Transport", "high"),
        2375: ("Docker unencrypted daemon", "critical"),
        2379: ("etcd client", "high"),
        2380: ("etcd peer", "high"),
        8500: ("Consul", "medium"),
    }

    KNOWN_MANAGEMENT_PORTS = {
        22: ("SSH Remote Access", "info"),
        21: ("FTP (Plaintext)", "medium"),
        23: ("Telnet (Insecure)", "critical"),
        25: ("SMTP Mail", "info"),
        53: ("DNS Server", "info"),
        80: ("HTTP Web Server", "safe"),
        443: ("HTTPS Web Server", "safe"),
        8080: ("Alternative HTTP / App Server", "medium"),
        8443: ("Alternative HTTPS", "info"),
    }

    @classmethod
    def parse_listening_sockets(cls, raw: str) -> List[Dict[str, Any]]:
        """
        Parses `ss -tulpn` or `netstat -tulpn` into structured records with risk ratings.
        """
        sockets: List[Dict[str, Any]] = []
        if not raw:
            return sockets

        lines = raw.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("Netid") or line_str.startswith("Active Internet"):
                continue

            parts = line_str.split()
            if len(parts) < 4:
                continue

            # ss output: Netid State Recv-Q Send-Q Local-Address:Port Peer-Address:Port Process
            proto = parts[0].lower()
            if proto not in ("tcp", "udp", "raw"):
                continue

            # Find local address field (typically index 4 in ss, index 3 in netstat)
            local_addr_str = ""
            proc_info = ""

            for idx, p in enumerate(parts):
                if ":" in p and any(char.isdigit() for char in p):
                    local_addr_str = p
                    # Process string is usually in the last column
                    if idx + 2 < len(parts):
                        proc_info = " ".join(parts[idx + 2 :])
                    elif idx + 1 < len(parts) and ("users:" in parts[idx + 1] or "/" in parts[idx + 1]):
                        proc_info = parts[idx + 1]
                    break

            if not local_addr_str:
                continue

            # Separate host and port
            if "]:" in local_addr_str:
                # IPv6 e.g. [::]:80
                host_part, port_str = local_addr_str.rsplit(":", 1)
                host_part = host_part.strip("[]")
            else:
                host_part, port_str = local_addr_str.rsplit(":", 1)

            try:
                port = int(port_str)
            except ValueError:
                continue

            # Check bind address
            is_wildcard = host_part in ("0.0.0.0", "::", "*", "")
            is_loopback = host_part in ("127.0.0.1", "::1", "localhost")

            # Extract clean process name and PID
            # E.g. users:(("sshd",pid=1234,fd=3)) or 1234/sshd
            proc_name = ""
            pid = ""
            m_ss = re.search(r'users:\(\("([^"]+)",pid=(\d+)', proc_info)
            if m_ss:
                proc_name = m_ss.group(1)
                pid = m_ss.group(2)
            else:
                m_net = re.search(r'(\d+)/([^\s]+)', proc_info)
                if m_net:
                    pid = m_net.group(1)
                    proc_name = m_net.group(2)

            # Determine Risk Assessment
            risk = "safe"
            risk_reason = "Listening on loopback only"

            if is_loopback:
                risk = "safe"
                risk_reason = "Bound to localhost (127.0.0.1/::1) — inaccessible from external network"
            elif is_wildcard:
                if port in cls.KNOWN_DATABASE_PORTS:
                    service_desc, default_risk = cls.KNOWN_DATABASE_PORTS[port]
                    risk = default_risk
                    risk_reason = f"CRITICAL: {service_desc} is exposed publicly on all network interfaces ({host_part}:{port})!"
                elif port in cls.KNOWN_MANAGEMENT_PORTS:
                    service_desc, default_risk = cls.KNOWN_MANAGEMENT_PORTS[port]
                    risk = default_risk
                    if risk in ("critical", "medium"):
                        risk_reason = f"{service_desc} is exposed publicly"
                    else:
                        risk_reason = f"Standard service: {service_desc}"
                else:
                    risk = "medium"
                    risk_reason = f"Custom or unclassified service on port {port} exposed to all interfaces"
            else:
                # Bound to specific private or public IP
                risk = "info"
                risk_reason = f"Bound to specific address: {host_part}"

            sockets.append(
                {
                    "protocol": proto.upper(),
                    "host": host_part,
                    "port": port,
                    "is_wildcard": is_wildcard,
                    "is_loopback": is_loopback,
                    "process_name": proc_name,
                    "pid": pid,
                    "risk": risk,
                    "risk_reason": risk_reason,
                    "raw_process": proc_info,
                }
            )

        # Sort with highest risk first
        risk_order = {"critical": 0, "high": 1, "medium": 2, "info": 3, "safe": 4}
        sockets.sort(key=lambda s: (risk_order.get(s["risk"], 5), s["port"]))
        return sockets

    # ---------------- SSH Configuration Hardening Parser ----------------
    @classmethod
    def parse_sshd_config(cls, raw: str) -> Dict[str, Any]:
        """
        Parses sshd_config and audits security posture against industry hardening standards.
        Returns check results, hardening score (0-100), and remediation guidance.
        """
        config_map: Dict[str, str] = {}
        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("#"):
                continue
            parts = line_s.split(None, 1)
            if len(parts) == 2:
                key = parts[0].strip().lower()
                val = parts[1].strip()
                # sshd takes first occurrence of directive
                if key not in config_map:
                    config_map[key] = val

        checks: List[Dict[str, Any]] = []
        score_deductions = 0

        # Check 1: PermitRootLogin
        # default is usually 'prohibit-password' in modern debian/ubuntu, 'yes' in older/rhel
        root_val = config_map.get("permitrootlogin", "prohibit-password")
        if root_val.lower() == "no":
            checks.append({
                "rule": "PermitRootLogin",
                "current": root_val,
                "recommended": "no",
                "status": "PASS",
                "severity": "high",
                "description": "Direct root logins disabled completely.",
            })
        elif root_val.lower() in ("prohibit-password", "without-password"):
            checks.append({
                "rule": "PermitRootLogin",
                "current": root_val,
                "recommended": "no",
                "status": "WARN",
                "severity": "medium",
                "description": "Root allowed with SSH keys only. Best practice recommends disabling entirely.",
            })
            score_deductions += 5
        else:
            checks.append({
                "rule": "PermitRootLogin",
                "current": root_val,
                "recommended": "no",
                "status": "FAIL",
                "severity": "critical",
                "description": "Root login enabled with passwords! Major brute-force target.",
            })
            score_deductions += 25

        # Check 2: PasswordAuthentication
        pwd_val = config_map.get("passwordauthentication", "yes")
        if pwd_val.lower() == "no":
            checks.append({
                "rule": "PasswordAuthentication",
                "current": pwd_val,
                "recommended": "no",
                "status": "PASS",
                "severity": "high",
                "description": "Password auth disabled. Requires SSH public key authentication.",
            })
        else:
            checks.append({
                "rule": "PasswordAuthentication",
                "current": pwd_val,
                "recommended": "no",
                "status": "WARN",
                "severity": "high",
                "description": "Password authentication enabled. Vulnerable to dictionary attacks without Fail2ban.",
            })
            score_deductions += 20

        # Check 3: PermitEmptyPasswords
        empty_val = config_map.get("permitemptypasswords", "no")
        if empty_val.lower() == "no":
            checks.append({
                "rule": "PermitEmptyPasswords",
                "current": empty_val,
                "recommended": "no",
                "status": "PASS",
                "severity": "critical",
                "description": "Empty passwords strictly forbidden.",
            })
        else:
            checks.append({
                "rule": "PermitEmptyPasswords",
                "current": empty_val,
                "recommended": "no",
                "status": "FAIL",
                "severity": "critical",
                "description": "Accounts without passwords can log in over SSH!",
            })
            score_deductions += 30

        # Check 4: Port
        port_val = config_map.get("port", "22")
        if port_val != "22":
            checks.append({
                "rule": "Port",
                "current": port_val,
                "recommended": "Custom (> 1024)",
                "status": "PASS",
                "severity": "info",
                "description": f"Running on non-standard port {port_val} to avoid automated botnet noise.",
            })
        else:
            checks.append({
                "rule": "Port",
                "current": port_val,
                "recommended": "Custom (> 1024)",
                "status": "WARN",
                "severity": "low",
                "description": "Standard port 22 receives 99% of automated internet scan sweeps.",
            })
            score_deductions += 5

        # Check 5: MaxAuthTries
        max_tries = config_map.get("maxauthtries", "6")
        try:
            tries_num = int(max_tries)
        except ValueError:
            tries_num = 6
        if tries_num <= 4:
            checks.append({
                "rule": "MaxAuthTries",
                "current": str(tries_num),
                "recommended": "3 or 4",
                "status": "PASS",
                "severity": "medium",
                "description": f"Limited to {tries_num} attempts per session.",
            })
        else:
            checks.append({
                "rule": "MaxAuthTries",
                "current": str(tries_num),
                "recommended": "3 or 4",
                "status": "WARN",
                "severity": "low",
                "description": f"High retry limit ({tries_num}) gives attackers more attempts per connection.",
            })
            score_deductions += 5

        # Check 6: X11Forwarding
        x11_val = config_map.get("x11forwarding", "no")
        if x11_val.lower() == "no":
            checks.append({
                "rule": "X11Forwarding",
                "current": x11_val,
                "recommended": "no",
                "status": "PASS",
                "severity": "low",
                "description": "X11 graphical forwarding disabled.",
            })
        else:
            checks.append({
                "rule": "X11Forwarding",
                "current": x11_val,
                "recommended": "no",
                "status": "WARN",
                "severity": "low",
                "description": "X11 forwarding enabled on headless hosting server.",
            })
            score_deductions += 5

        # Check 7: PubkeyAuthentication
        pubkey_val = config_map.get("pubkeyauthentication", "yes")
        if pubkey_val.lower() == "yes":
            checks.append({
                "rule": "PubkeyAuthentication",
                "current": pubkey_val,
                "recommended": "yes",
                "status": "PASS",
                "severity": "high",
                "description": "Public key authentication enabled.",
            })
        else:
            checks.append({
                "rule": "PubkeyAuthentication",
                "current": pubkey_val,
                "recommended": "yes",
                "status": "FAIL",
                "severity": "high",
                "description": "Public key auth is disabled!",
            })
            score_deductions += 20

        final_score = max(0, 100 - score_deductions)
        return {
            "score": final_score,
            "checks": checks,
            "config_values": config_map,
        }

    # ---------------- System Audit Probe Parser ----------------
    @classmethod
    def parse_system_audit_probe(cls, raw: str) -> Dict[str, Any]:
        """Parses output from `SecurityCommands.system_audit_probe()`."""
        res: Dict[str, Any] = {
            "kernel_syncookies": False,
            "kernel_ipforward": False,
            "shadow_perms": "unknown",
            "passwd_perms": "unknown",
            "lynis_avail": False,
            "fail2ban_active": False,
            "ufw_active": False,
            "sudo_users": [],
        }

        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("KERNEL_SYNCOOKIES:"):
                res["kernel_syncookies"] = line_s.split(":", 1)[1].strip() == "1"
            elif line_s.startswith("KERNEL_IPFORWARD:"):
                res["kernel_ipforward"] = line_s.split(":", 1)[1].strip() == "1"
            elif line_s.startswith("SHADOW_PERMS:"):
                res["shadow_perms"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("PASSWD_PERMS:"):
                res["passwd_perms"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("LYNIS_AVAIL:"):
                res["lynis_avail"] = line_s.split(":", 1)[1].strip().lower() == "yes"
            elif line_s.startswith("FAIL2BAN_SERVICE:"):
                res["fail2ban_active"] = line_s.split(":", 1)[1].strip().lower() == "active"
            elif line_s.startswith("UFW_SERVICE:"):
                res["ufw_active"] = line_s.split(":", 1)[1].strip().lower() == "active"
            elif line_s.startswith("ROOT_SUDO_USERS:"):
                users_str = line_s.split(":", 1)[1].strip()
                if users_str:
                    res["sudo_users"] = [u for u in users_str.split() if u]

        return res

    # ---------------- Firewalld Parser ----------------
    @classmethod
    def parse_firewalld_status(cls, raw: str) -> Dict[str, Any]:
        """
        Parses `firewall-cmd` output into structured firewall records.
        """
        result: Dict[str, Any] = {
            "active": False,
            "raw_state": "inactive",
            "active_zones": [],
            "zone": "public",
            "services": [],
            "ports": [],
            "rich_rules": [],
            "interfaces": [],
            "rules": [],
        }

        if not raw:
            return result

        lines = [line.rstrip() for line in raw.splitlines()]

        has_zone_marker = "=== ZONE_DETAILS ===" in raw
        section = "header"
        curr_zone = "public"

        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue

            if line_s == "running":
                result["active"] = True
                result["raw_state"] = "running"
            elif line_s == "not running":
                result["active"] = False
                result["raw_state"] = "not running"
            elif line_s == "=== ACTIVE_ZONES ===":
                section = "active_zones"
                continue
            elif line_s == "=== ZONE_DETAILS ===":
                section = "zone_details"
                continue

            if section == "active_zones":
                if not line.startswith(" ") and not line.startswith("\t"):
                    result["active_zones"].append(line_s)
            elif section == "zone_details" or not has_zone_marker:
                if "(" in line_s and "active)" in line_s:
                    curr_zone = line_s.split()[0]
                    result["zone"] = curr_zone
                    result["active"] = True
                    result["raw_state"] = "running"
                elif line_s.endswith(":") and not any(line_s.startswith(k) for k in ("services:", "ports:", "interfaces:", "rich rules:")):
                    curr_zone = line_s[:-1]
                    result["zone"] = curr_zone

                if line_s.startswith("interfaces:"):
                    parts = line_s.split(":", 1)[1].strip()
                    if parts:
                        result["interfaces"] = parts.split()
                elif line_s.startswith("services:"):
                    parts = line_s.split(":", 1)[1].strip()
                    if parts:
                        result["services"] = parts.split()
                elif line_s.startswith("ports:"):
                    parts = line_s.split(":", 1)[1].strip()
                    if parts:
                        result["ports"] = parts.split()
                elif line_s.startswith("rich rules:"):
                    pass
                elif "rule " in line_s or line_s.startswith("rule"):
                    result["rich_rules"].append(line_s)

        # Build unified rules table records
        unified_rules: List[Dict[str, Any]] = []
        rule_idx = 1

        for srv in result["services"]:
            unified_rules.append({
                "num": rule_idx,
                "type": "service",
                "to": f"{srv} (service)",
                "action": "ALLOW IN",
                "from": "Anywhere",
                "comment": f"Zone: {result['zone']}",
                "ipv6": False,
                "raw_target": srv,
            })
            rule_idx += 1

        for p in result["ports"]:
            unified_rules.append({
                "num": rule_idx,
                "type": "port",
                "to": p,
                "action": "ALLOW IN",
                "from": "Anywhere",
                "comment": f"Zone: {result['zone']}",
                "ipv6": False,
                "raw_target": p,
            })
            rule_idx += 1

        for r in result["rich_rules"]:
            act = "ALLOW IN" if "accept" in r else ("DENY IN" if "reject" in r or "drop" in r else "RICH")
            unified_rules.append({
                "num": rule_idx,
                "type": "rich",
                "to": r,
                "action": act,
                "from": "Specific Rule",
                "comment": f"Rich Rule ({result['zone']})",
                "ipv6": "ipv6" in r,
                "raw_target": r,
            })
            rule_idx += 1

        result["rules"] = unified_rules
        return result

    # ---------------- Active Connections Parser ----------------
    @classmethod
    def parse_active_connections(cls, raw: str) -> Dict[str, Any]:
        """Parses active established sockets and top remote IPs."""
        sockets = []
        top_ips = []
        section = "sockets"

        for line in raw.splitlines():
            line_s = line.strip()
            if line_s == "=== ESTABLISHED_CONNECTIONS ===":
                section = "sockets"
                continue
            elif line_s == "=== TOP_REMOTE_IPS ===":
                section = "top_ips"
                continue

            if section == "sockets":
                if not line_s or line_s.startswith("Netid") or line_s.startswith("Active"):
                    continue
                parts = line_s.split()
                if len(parts) >= 5:
                    sockets.append({
                        "proto": parts[0],
                        "state": parts[1] if len(parts) > 2 else "ESTAB",
                        "local": parts[3] if len(parts) > 3 else "-",
                        "remote": parts[4] if len(parts) > 4 else "-",
                        "process": " ".join(parts[5:]) if len(parts) > 5 else "-",
                    })
            elif section == "top_ips":
                if line_s and not line_s.startswith("=="):
                    parts = line_s.split(None, 1)
                    if len(parts) == 2:
                        try:
                            count = int(parts[0])
                            ip = parts[1].strip()
                            top_ips.append({"count": count, "ip": ip})
                        except ValueError:
                            pass

        return {"connections": sockets, "top_ips": top_ips}

    # ---------------- SUID Files Parser ----------------
    @classmethod
    def parse_suid_files(cls, raw: str) -> List[Dict[str, Any]]:
        """Parses SUID/SGID audit output."""
        items = []
        KNOWN_HIGH_RISK = {"bash", "sh", "python", "perl", "ruby", "vim", "nano", "nmap", "find", "cp", "mv", "chmod", "chown"}

        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("find:"):
                continue
            parts = line_s.split()
            if len(parts) >= 9:
                perms = parts[0]
                owner = parts[2]
                group = parts[3]
                size = parts[4]
                path = parts[8]
                basename = path.rsplit("/", 1)[-1]
                risk = "high" if basename in KNOWN_HIGH_RISK else "info"
                items.append({
                    "perms": perms,
                    "owner": owner,
                    "group": group,
                    "size": size,
                    "path": path,
                    "name": basename,
                    "risk": risk,
                })
        return items

    # ---------------- SSL Certificates Parser ----------------
    @classmethod
    def parse_ssl_certificates(cls, raw: str) -> List[Dict[str, Any]]:
        """Parses SSL certificate probe output."""
        import time
        now = time.time()
        certs = []

        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("=="):
                continue
            parts = line_s.split("|")
            if len(parts) >= 6:
                path, subj, issuer, enddate, keysize, datesec_str = parts[:6]
                try:
                    datesec = float(datesec_str)
                    days_left = int((datesec - now) / 86400)
                except (ValueError, TypeError):
                    days_left = 999

                if days_left < 0:
                    status = "EXPIRED"
                elif days_left < 15:
                    status = "CRITICAL"
                elif days_left < 30:
                    status = "WARNING"
                else:
                    status = "VALID"

                # Extract domain name from subject or path
                m_cn = re.search(r'CN=([^/,]+)', subj)
                domain = m_cn.group(1) if m_cn else path.split("/")[-2]

                certs.append({
                    "path": path,
                    "domain": domain,
                    "subject": subj,
                    "issuer": issuer,
                    "enddate": enddate,
                    "days_left": days_left,
                    "keysize": keysize,
                    "status": status,
                })

        certs.sort(key=lambda c: c["days_left"])
        return certs

    # ---------------- Distribution Detection Parser ----------------
    @classmethod
    def parse_os_release(cls, raw: str) -> Dict[str, str]:
        """
        Parses `SecurityCommands.detect_os()` output.
        Returns:
            {
                "id": str,
                "id_like": str,
                "name": str,
                "pretty_name": str,
                "version_id": str,
                "family": "rhel" | "debian" | "arch" | "suse" | "generic",
                "pkg_manager": "dnf" | "yum" | "apt-get" | "pacman" | "zypper" | "auto",
                "arch": str,
            }
        """
        result = {
            "id": "unknown",
            "id_like": "",
            "name": "Linux",
            "pretty_name": "Linux Host",
            "version_id": "",
            "family": "generic",
            "pkg_manager": "auto",
            "arch": "x86_64",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("PKG_MGR:"):
                result["pkg_manager"] = line_s.split(":", 1)[1].strip()
            elif "=" in line_s:
                k, v = line_s.split("=", 1)
                v = v.strip().strip('"').strip("'")
                k = k.strip()
                if k == "ID":
                    result["id"] = v.lower()
                elif k == "ID_LIKE":
                    result["id_like"] = v.lower()
                elif k == "NAME":
                    result["name"] = v
                elif k == "PRETTY_NAME":
                    result["pretty_name"] = v
                elif k == "VERSION_ID":
                    result["version_id"] = v
            elif line_s in ("x86_64", "aarch64", "armv7l", "i686", "s390x"):
                result["arch"] = line_s

        combined = f"{result['id']} {result['id_like']}".lower()
        if any(x in combined for x in ("rhel", "centos", "rocky", "almalinux", "fedora", "amzn", "ol")):
            result["family"] = "rhel"
            if result["pkg_manager"] == "auto":
                result["pkg_manager"] = "dnf"
        elif any(x in combined for x in ("debian", "ubuntu", "kali", "linuxmint", "pop")):
            result["family"] = "debian"
            if result["pkg_manager"] == "auto":
                result["pkg_manager"] = "apt-get"
        elif "arch" in combined:
            result["family"] = "arch"
            if result["pkg_manager"] == "auto":
                result["pkg_manager"] = "pacman"
        elif any(x in combined for x in ("suse", "sles")):
            result["family"] = "suse"
            if result["pkg_manager"] == "auto":
                result["pkg_manager"] = "zypper"

        return result

    # ---------------- AIDE Parsers ----------------
    @classmethod
    def parse_aide_probe(cls, raw: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "installed": False,
            "aideinit": False,
            "config": "none",
            "db_exists": False,
            "db_path": "",
            "db_stat": "",
            "db_new_exists": False,
            "cron_scheduled": False,
            "cron_file": "",
            "log_exists": False,
            "log_stat": "",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("INSTALLED:"):
                info["installed"] = "yes" in line_s.lower()
            elif line_s.startswith("AIDEINIT:"):
                info["aideinit"] = "yes" in line_s.lower()
            elif line_s.startswith("CONFIG:"):
                info["config"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("DB_EXISTS:"):
                info["db_exists"] = "yes" in line_s.lower()
            elif line_s.startswith("DB_PATH:"):
                info["db_path"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("DB_STAT:"):
                info["db_stat"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("DB_NEW_EXISTS:"):
                info["db_new_exists"] = "yes" in line_s.lower()
            elif line_s.startswith("CRON_SCHEDULED:"):
                info["cron_scheduled"] = "yes" in line_s.lower()
            elif line_s.startswith("CRON_FILE:"):
                info["cron_file"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("LOG_EXISTS:"):
                info["log_exists"] = "yes" in line_s.lower()
            elif line_s.startswith("LOG_STAT:"):
                info["log_stat"] = line_s.split(":", 1)[1].strip()
        return info

    @classmethod
    def parse_aide_check_output(cls, raw: str) -> Dict[str, Any]:
        """
        Parses `aide --check` output into structured entries and summary statistics.
        """
        data: Dict[str, Any] = {
            "clean": "no differences" in raw.lower() or "all files ok" in raw.lower() or "looks okay" in raw.lower(),
            "total_entries": 0,
            "added": 0,
            "removed": 0,
            "changed": 0,
            "entries": [],
            "raw": raw,
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if "Total number of entries:" in line_s or "Total number of files:" in line_s:
                try:
                    m = re.search(r'\d+', line_s)
                    if m:
                        data["total_entries"] = int(m.group())
                except Exception:
                    pass
            elif "Added entries:" in line_s:
                try:
                    m = re.search(r'\d+', line_s)
                    if m:
                        data["added"] = int(m.group())
                except Exception:
                    pass
            elif "Removed entries:" in line_s:
                try:
                    m = re.search(r'\d+', line_s)
                    if m:
                        data["removed"] = int(m.group())
                except Exception:
                    pass
            elif "Changed entries:" in line_s:
                try:
                    m = re.search(r'\d+', line_s)
                    if m:
                        data["changed"] = int(m.group())
                except Exception:
                    pass

        current_section = ""
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("Added entries:"):
                current_section = "Added"
            elif line_s.startswith("Removed entries:"):
                current_section = "Removed"
            elif line_s.startswith("Changed entries:"):
                current_section = "Changed"
            elif line_s.startswith("Detailed information about changes:"):
                current_section = "Details"
            elif current_section in ("Added", "Removed", "Changed") and line_s.startswith("/"):
                data["entries"].append({
                    "status": current_section,
                    "path": line_s,
                    "details": f"File {current_section.lower()} according to baseline",
                })
            elif current_section == "Details" and (line_s.startswith("File:") or line_s.startswith("Directory:")):
                parts = line_s.split(":", 1)
                p = parts[1].strip() if len(parts) > 1 else ""
                if p and not any(e["path"] == p for e in data["entries"]):
                    data["entries"].append({
                        "status": "Changed",
                        "path": p,
                        "details": "Modified attributes / cryptographic checksum",
                    })

        if data["added"] > 0 or data["removed"] > 0 or data["changed"] > 0:
            data["clean"] = False
        return data

    # ---------------- fapolicyd Parsers ----------------
    @classmethod
    def parse_fapolicyd_probe(cls, raw: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "installed": False,
            "cli_installed": False,
            "service_state": "inactive",
            "service_enabled": "disabled",
            "config_exists": False,
            "permissive": 0,
            "integrity": "none",
            "status_text": "",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("INSTALLED:"):
                info["installed"] = "yes" in line_s.lower()
            elif line_s.startswith("CLI_INSTALLED:"):
                info["cli_installed"] = "yes" in line_s.lower()
            elif line_s.startswith("SERVICE_STATE:"):
                info["service_state"] = line_s.split(":", 1)[1].strip().lower()
            elif line_s.startswith("SERVICE_ENABLED:"):
                info["service_enabled"] = line_s.split(":", 1)[1].strip().lower()
            elif line_s.startswith("CONFIG_EXISTS:"):
                info["config_exists"] = "yes" in line_s.lower()
            elif line_s.startswith("permissive"):
                try:
                    m = re.search(r'\d+', line_s)
                    if m:
                        info["permissive"] = int(m.group())
                except Exception:
                    pass
            elif line_s.startswith("integrity"):
                parts = line_s.split("=", 1)
                if len(parts) > 1:
                    info["integrity"] = parts[1].strip()
            elif any(w in line_s.lower() for w in ("rules", "trust", "database", "daemon")):
                info["status_text"] += line_s + "\n"
        return info

    @classmethod
    def parse_fapolicyd_denials(cls, raw: str) -> List[Dict[str, str]]:
        denials: List[Dict[str, str]] = []
        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("----"):
                continue
            entry: Dict[str, str] = {
                "timestamp": "",
                "exe": "",
                "comm": "",
                "pid": "",
                "uid": "",
                "raw": line_s,
            }
            m_time = re.search(r'msg=audit\((\d+)\.', line_s)
            if m_time:
                import time
                try:
                    entry["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(m_time.group(1))))
                except Exception:
                    entry["timestamp"] = m_time.group(1)
            else:
                m_log_time = re.match(r'^([A-Z][a-z]{2}\s+\d+\s+\d+:\d+:\d+)', line_s)
                if m_log_time:
                    entry["timestamp"] = m_log_time.group(1)

            m_exe = re.search(r'exe="?([^"\s]+)"?', line_s)
            if m_exe:
                entry["exe"] = m_exe.group(1)

            m_comm = re.search(r'comm="?([^"\s]+)"?', line_s)
            if m_comm:
                entry["comm"] = m_comm.group(1)

            m_pid = re.search(r'pid=(\d+)', line_s)
            if m_pid:
                entry["pid"] = m_pid.group(1)

            m_uid = re.search(r'uid=(\d+)', line_s)
            if m_uid:
                entry["uid"] = m_uid.group(1)

            if entry["exe"] or entry["comm"] or "fanotify" in line_s.lower() or "fapolicyd" in line_s.lower():
                denials.append(entry)
        return denials

    # ---------------- OpenSCAP Parsers ----------------
    @classmethod
    def parse_openscap_probe(cls, raw: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "installed": False,
            "datastreams": [],
            "html_dir": "/var/www/html",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("INSTALLED:"):
                info["installed"] = "yes" in line_s.lower()
            elif line_s.startswith("DS:"):
                ds = line_s.split(":", 1)[1].strip()
                if ds and ds not in info["datastreams"]:
                    info["datastreams"].append(ds)
            elif line_s.startswith("HTML_DIR:"):
                info["html_dir"] = line_s.split(":", 1)[1].strip()
        return info

    @classmethod
    def parse_openscap_profiles(cls, raw: str) -> List[Dict[str, str]]:
        profiles: List[Dict[str, str]] = []
        cur_id = ""
        cur_title = ""

        def flush():
            nonlocal cur_id, cur_title
            if cur_id:
                profiles.append({"id": cur_id, "title": cur_title or cur_id.split("content_profile_")[-1]})
                cur_id = ""
                cur_title = ""

        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("Title:"):
                if cur_id and cur_title:
                    flush()
                cur_title = line_s.split(":", 1)[1].strip()
            m = re.search(r'(xccdf_org\.ssgproject\.content_profile_[a-zA-Z0-9_\-]+)', line_s)
            if m:
                if cur_id:
                    flush()
                cur_id = m.group(1)

        flush()
        return profiles

    @classmethod
    def parse_openscap_eval_output(cls, raw: str) -> Dict[str, Any]:
        """
        Parses `oscap xccdf eval` execution output.
        """
        data: Dict[str, Any] = {
            "score": 0.0,
            "passed": 0,
            "failed": 0,
            "notapplicable": 0,
            "error": 0,
            "total_rules": 0,
            "rules": [],
            "report_path": "",
            "raw": raw,
        }
        for line in raw.splitlines():
            line_s = line.strip()
            m_score = re.search(r'Score:\s*([0-9\.]+)\s*%', line_s)
            if m_score:
                try:
                    data["score"] = float(m_score.group(1))
                except Exception:
                    pass

            m_rule = re.search(r'Rule:\s*([^\s]+)\s+Result:\s*([^\s]+)', line_s)
            if m_rule:
                rid = m_rule.group(1)
                res = m_rule.group(2).lower()
                data["rules"].append({"id": rid, "result": res})
                if res == "pass":
                    data["passed"] += 1
                elif res == "fail":
                    data["failed"] += 1
                elif res in ("notapplicable", "na"):
                    data["notapplicable"] += 1
                else:
                    data["error"] += 1
                data["total_rules"] += 1
            elif "OpenSCAP Error:" in line_s or "Error:" in line_s:
                data["error"] += 1

        if data["total_rules"] > 0 and data["score"] == 0.0 and (data["passed"] + data["failed"]) > 0:
            data["score"] = round((data["passed"] / (data["passed"] + data["failed"])) * 100, 1)

        return data

    # ---------------- Auditd Parsers ----------------
    @classmethod
    def parse_auditd_probe(cls, raw: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "installed": False,
            "ausearch_installed": False,
            "aureport_installed": False,
            "service_state": "inactive",
            "service_enabled": "disabled",
            "rules_count": 0,
            "status_text": "",
        }
        for line in raw.splitlines():
            line_s = line.strip()
            if line_s.startswith("INSTALLED:"):
                info["installed"] = "yes" in line_s.lower()
            elif line_s.startswith("AUSEARCH_INSTALLED:"):
                info["ausearch_installed"] = "yes" in line_s.lower()
            elif line_s.startswith("AUREPORT_INSTALLED:"):
                info["aureport_installed"] = "yes" in line_s.lower()
            elif line_s.startswith("SERVICE_STATE:"):
                info["service_state"] = line_s.split(":", 1)[1].strip().lower()
            elif line_s.startswith("SERVICE_ENABLED:"):
                info["service_enabled"] = line_s.split(":", 1)[1].strip().lower()
            elif line_s.isdigit():
                try:
                    info["rules_count"] = int(line_s)
                except Exception:
                    pass
            elif any(w in line_s.lower() for w in ("enabled", "backlog", "pid", "rate_limit")):
                info["status_text"] += line_s + "\n"
        return info

    @classmethod
    def parse_auditd_rules(cls, raw: str) -> List[Dict[str, str]]:
        rules: List[Dict[str, str]] = []
        for line in raw.splitlines():
            line_s = line.strip()
            if not line_s or "no rules" in line_s.lower():
                continue
            rule_entry: Dict[str, str] = {
                "raw": line_s,
                "type": "Watch" if "-w" in line_s else "Syscall",
                "target": "",
                "permissions": "",
                "key": "",
            }
            m_w = re.search(r'-w\s+([^\s]+)', line_s)
            if m_w:
                rule_entry["target"] = m_w.group(1)
            else:
                m_s = re.search(r'-S\s+([^\s]+)', line_s)
                if m_s:
                    rule_entry["target"] = m_s.group(1)
                else:
                    rule_entry["target"] = line_s

            m_p = re.search(r'-p\s+([^\s]+)', line_s)
            if m_p:
                rule_entry["permissions"] = m_p.group(1)

            m_k = re.search(r'-k\s+([^\s]+)', line_s)
            if m_k:
                rule_entry["key"] = m_k.group(1)

            rules.append(rule_entry)
        return rules

    @classmethod
    def parse_ausearch_events(cls, raw: str) -> List[Dict[str, str]]:
        events: List[Dict[str, str]] = []
        for block in raw.split("----"):
            block_s = block.strip()
            if not block_s:
                continue
            ev: Dict[str, str] = {
                "timestamp": "",
                "type": "",
                "key": "",
                "comm": "",
                "exe": "",
                "name": "",
                "success": "unknown",
                "raw": block_s,
            }
            m_time = re.search(r'msg=audit\((\d+)\.', block_s)
            if m_time:
                import time
                try:
                    ev["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(m_time.group(1))))
                except Exception:
                    ev["timestamp"] = m_time.group(1)
            else:
                m_txt_time = re.search(r'time->([^\n]+)', block_s)
                if m_txt_time:
                    ev["timestamp"] = m_txt_time.group(1).strip()

            m_type = re.search(r'type=([A-Z0-9_]+)', block_s)
            if m_type:
                ev["type"] = m_type.group(1)

            m_key = re.search(r'key="?([^"\s]+)"?', block_s)
            if m_key:
                ev["key"] = m_key.group(1)

            m_comm = re.search(r'comm="?([^"\s]+)"?', block_s)
            if m_comm:
                ev["comm"] = m_comm.group(1)

            m_exe = re.search(r'exe="?([^"\s]+)"?', block_s)
            if m_exe:
                ev["exe"] = m_exe.group(1)

            m_name = re.search(r'name="?([^"\s]+)"?', block_s)
            if m_name:
                ev["name"] = m_name.group(1)

            if "success=yes" in block_s or "res=success" in block_s:
                ev["success"] = "yes"
            elif "success=no" in block_s or "res=failed" in block_s:
                ev["success"] = "no"

            if ev["type"] or ev["comm"] or ev["key"] or ev["exe"]:
                events.append(ev)
        return events


