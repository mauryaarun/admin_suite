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

