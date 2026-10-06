"""
SysAdmin dashboard command definitions.
Optimized for performance, robustness, and modern Linux environments.
"""

SYSADMIN_SECTIONS = [
    "Overview",
    "Users",
    "Services",
    "Processes",
    "Storage",
    "Network",
    "Journal",
    "Cron",
    "Security",
    "Packages",
    "Containers",
    "Performance",
]

SYSADMIN_CMDS = {
    "Overview": (
        "echo '== OS RELEASE =='; cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)='; "
        "echo; echo '== HOSTNAME =='; hostnamectl 2>/dev/null || hostname; "
        "echo; echo '== KERNEL =='; uname -r; "
        "echo; echo '== UPTIME/LOAD =='; uptime; "
        "echo; echo '== FAILED SERVICES =='; systemctl --failed --no-legend --no-pager 2>/dev/null || echo 'none/systemd unavailable'; "
        "echo; echo '== MEMORY =='; free -h; "
        "echo; echo '== CPU =='; lscpu 2>/dev/null | grep -E '^(Architecture|CPU\\(s\\)|Model name|Thread)' || cat /proc/cpuinfo | grep -E '^(model name|cpu cores)' | sort -u "
    ),

    "Users": (
        "echo '=== PASSWD ==='; getent passwd 2>/dev/null || cat /etc/passwd; "
        "echo '=== LOGGED_IN ==='; who 2>/dev/null || w -h 2>/dev/null || true; "
        "echo '=== PRIVILEGED ==='; getent group sudo wheel admin 2>/dev/null || grep -E '^(sudo|wheel|admin):' /etc/group 2>/dev/null || true"
    ),

    "Services": (
        "systemctl list-units --type=service --all --no-legend --plain --no-pager 2>/dev/null "
        "|| service --status-all 2>/dev/null"
    ),

    "Processes": (
        "ps aux --sort=-%cpu | head -n 250"
    ),

    "Storage": (
        "echo '== DISK USAGE (Inodes) =='; df -iT 2>/dev/null | grep -vE '^(tmpfs|devtmpfs|none)'; "
        "echo; echo '== DISK USAGE (Space) =='; df -hT 2>/dev/null | grep -vE '^(tmpfs|devtmpfs|none)'; "
        "echo; echo '== BLOCK DEVICES =='; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT 2>/dev/null; "
        "echo; echo '== ZFS POOLS =='; zpool list 2>/dev/null || echo 'no zfs'; "
        "echo; echo '== LVM =='; sudo -n vgs 2>/dev/null; sudo -n lvs 2>/dev/null; "
        "echo; echo '== SWAP =='; swapon --show 2>/dev/null "
    ),

    "Network": (
        "echo '== INTERFACES & IPS =='; ip -brief addr 2>/dev/null || ifconfig -a; "
        "echo; echo '== INTERFACE ERRORS/DROPS =='; ip -s link 2>/dev/null | grep -E '^[0-9]|RX:|TX:' | head -20; "
        "echo; echo '== DEFAULT ROUTE =='; ip route show default 2>/dev/null; "
        "echo; echo '== LISTENING PORTS =='; ss -tunlp 2>/dev/null | head -30; "
        "echo; echo '== DNS =='; cat /etc/resolv.conf 2>/dev/null | grep -v '^#' | grep -v '^$'; "
        "echo; echo '== FIREWALL =='; sudo -n ufw status 2>/dev/null "
        "|| sudo -n firewall-cmd --state 2>/dev/null && sudo -n firewall-cmd --list-all 2>/dev/null "
        "|| sudo -n iptables -L -n 2>/dev/null | head -20 "
        "|| echo 'no firewall tool / no permission' "
    ),

    "Journal": (
        "echo '== RECENT ERRORS/WARNINGS =='; "
        "journalctl -p warning -n 50 --no-pager --no-hostname 2>/dev/null "
        "|| tail -n 50 /var/log/syslog 2>/dev/null "
        "|| tail -n 50 /var/log/messages 2>/dev/null "
        "|| echo 'no journal access' "
    ),

    "Cron": (
        "echo '== SYSTEMD TIMERS =='; systemctl list-timers --all --no-legend --no-pager 2>/dev/null || echo 'none'; "
        "echo; echo '== SYSTEM CRONTAB =='; cat /etc/crontab 2>/dev/null | grep -v '^#' | grep -v '^$'; "
        "echo; echo '== CRON.D =='; for f in /etc/cron.d/*; do [ -f \"$f\" ] && echo \"--- $f\" && grep -v '^#' \"$f\" | grep -v '^$'; done; "
        "echo; echo '== USER CRONTAB =='; crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' || echo 'no user crontab' "
    ),

    "Security": (
        "echo '== FAILED SSH LOGINS (Last 20) =='; "
        "(journalctl _COMM=sshd --no-pager 2>/dev/null || cat /var/log/auth.log 2>/dev/null || cat /var/log/secure 2>/dev/null) "
        "| grep -i 'failed\\|invalid' | tail -n 20 || echo 'no auth logs accessible'; "
        "echo; echo '== SSH ROOT LOGIN STATUS =='; grep -E '^PermitRootLogin' /etc/ssh/sshd_config 2>/dev/null || echo 'default (usually prohibited)'; "
        "echo; echo '== WORLD-WRITABLE FILES IN /etc =='; find /etc -maxdepth 2 -type f -perm -0002 2>/dev/null | head -10 || echo 'none found' "
    ),

    "Packages": (
        "echo '== UPGRADABLE PACKAGES =='; "
        "apt-get -s -q upgrade 2>/dev/null | awk '/^Inst/ {print $2}' "
        "|| dnf check-update -q 2>/dev/null | awk 'NF>1 {print $1}' "
        "|| yum check-update -q 2>/dev/null | awk 'NF>1 {print $1}' "
        "|| echo 'package check unsupported'; "
        "echo; echo '== RECENT PACKAGE HISTORY =='; "
        "tail -n 20 /var/log/dpkg.log 2>/dev/null | grep -E 'status installed' "
        "|| tail -n 20 /var/log/yum.log 2>/dev/null "
        "|| tail -n 20 /var/log/dnf.log 2>/dev/null "
        "|| rpm -qa --last 2>/dev/null | head -n 20 "
        "|| echo 'package log unavailable' "
    ),

    "Containers": (
        "echo '== DOCKER CONTAINERS =='; docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' 2>/dev/null || echo 'docker not installed/accessible'; "
        "echo; echo '== PODMAN CONTAINERS =='; podman ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' 2>/dev/null || echo 'podman not installed'; "
        "echo; echo '== DOCKER RESOURCE USAGE =='; docker stats --no-stream --format 'table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}' 2>/dev/null || true; "
        "echo; echo '== DOCKER IMAGES =='; docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}' 2>/dev/null | head -15 || true "
    ),

    "Performance": (
        "echo '== VMSTAT (CPU/IO/Sys) =='; vmstat 1 3 2>/dev/null || echo 'vmstat not available'; "
        "echo; echo '== IO STATISTICS =='; iostat -xz 1 2 2>/dev/null || echo 'sysstat/iostat not installed'; "
        "echo; echo '== TOP MEMORY CONSUMERS =='; ps aux --sort=-%mem | head -n 10; "
        "echo; echo '== KERNEL RING BUFFER (ERRORS) =='; dmesg -T --level=err,warn 2>/dev/null | tail -n 15 || dmesg | grep -iE 'error|warn|fail' | tail -n 15 || echo 'dmesg restricted' "
    ),
}
