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
        "echo; echo '== SECURITY SUBSYSTEM =='; "
        "if which sestatus >/dev/null 2>&1; then sestatus; elif which aa-status >/dev/null 2>&1; then aa-status --enabled 2>/dev/null && echo 'AppArmor: enabled' || echo 'AppArmor: disabled'; else echo 'SELinux/AppArmor: none'; fi; "
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
        "echo; echo '== FIREWALL =='; "
        "if sudo -n firewall-cmd --state >/dev/null 2>&1; then "
        "  echo 'Firewalld (active):'; sudo -n firewall-cmd --list-all 2>/dev/null; "
        "elif sudo -n ufw status >/dev/null 2>&1; then "
        "  echo 'UFW (Debian/Ubuntu):'; sudo -n ufw status 2>/dev/null; "
        "elif sudo -n iptables -L -n >/dev/null 2>&1; then "
        "  echo 'iptables:'; sudo -n iptables -L -n 2>/dev/null | head -25; "
        "else "
        "  echo 'no firewall tool or sudo permission'; "
        "fi"
    ),

    "Journal": (
        "echo '== RECENT ERRORS/WARNINGS =='; "
        "journalctl -p warning -n 100 --no-pager -o short-iso 2>/dev/null "
        "|| journalctl -p warning -n 100 --no-pager 2>/dev/null "
        "|| tail -n 100 /var/log/messages 2>/dev/null "
        "|| tail -n 100 /var/log/syslog 2>/dev/null "
        "|| echo 'no journal access' "
    ),

    "Cron": (
        "echo '== SYSTEMD TIMERS =='; systemctl list-timers --all --no-legend --no-pager 2>/dev/null || echo 'none'; "
        "echo; echo '== SYSTEM CRONTAB =='; cat /etc/crontab 2>/dev/null | grep -v '^#' | grep -v '^$'; "
        "echo; echo '== CRON.D =='; for f in /etc/cron.d/*; do [ -f \"$f\" ] && echo \"--- $f\" && grep -v '^#' \"$f\" | grep -v '^$'; done; "
        "echo; echo '== USER CRONTAB =='; crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' || echo 'no user crontab' "
    ),

    "Security": (
        "echo '== SELINUX / APPARMOR STATUS =='; "
        "getenforce 2>/dev/null || (which aa-status >/dev/null 2>&1 && aa-status --enabled 2>/dev/null && echo 'AppArmor enabled') || echo 'Disabled/None'; "
        "echo; echo '== FAILED SSH LOGINS (Last 20) =='; "
        "(journalctl _COMM=sshd --no-pager 2>/dev/null || cat /var/log/secure 2>/dev/null || cat /var/log/auth.log 2>/dev/null) "
        "| grep -i 'failed\\|invalid' | tail -n 20 || echo 'no auth logs accessible'; "
        "echo; echo '== SSH ROOT LOGIN STATUS =='; grep -E '^PermitRootLogin' /etc/ssh/sshd_config 2>/dev/null || echo 'default (usually prohibited)'; "
        "echo; echo '== WORLD-WRITABLE FILES IN /etc =='; find /etc -maxdepth 2 -type f -perm -0002 2>/dev/null | head -10 || echo 'none found' "
    ),

    "Packages": (
        "echo '== UPGRADABLE PACKAGES =='; "
        "if which apt-get >/dev/null 2>&1; then "
        "  apt-get -s -q upgrade 2>/dev/null | awk '/^Inst/ {gsub(/[\\[\\]()]/, \"\", $3); gsub(/[\\[\\]()]/, \"\", $4); print $2 \"|\" $3 \"|\" $4}'; "
        "elif which dnf >/dev/null 2>&1; then "
        "  dnf check-update -q 2>/dev/null | awk 'NF>=2 && !/^[ \\t]*$/ {print $1 \"|-\" \"|\" $2}'; "
        "elif which yum >/dev/null 2>&1; then "
        "  yum check-update -q 2>/dev/null | awk 'NF>=2 && !/^[ \\t]*$/ {print $1 \"|-\" \"|\" $2}'; "
        "elif which pacman >/dev/null 2>&1; then "
        "  pacman -Qu 2>/dev/null | awk '{print $1 \"|\" $2 \"|\" $4}'; "
        "elif which zypper >/dev/null 2>&1; then "
        "  zypper list-updates 2>/dev/null | awk -F'|' 'NR>4 {gsub(/^[ \\t]+|[ \\t]+$/, \"\", $3); gsub(/^[ \\t]+|[ \\t]+$/, \"\", $4); gsub(/^[ \\t]+|[ \\t]+$/, \"\", $5); if($3!=\"\") print $3 \"|\" $4 \"|\" $5}'; "
        "else "
        "  echo 'package manager unsupported'; "
        "fi; "
        "echo; echo '== RECENT PACKAGE HISTORY =='; "
        "if [ -f /var/log/dpkg.log ]; then "
        "  tail -n 25 /var/log/dpkg.log 2>/dev/null | grep -E 'status installed'; "
        "elif which rpm >/dev/null 2>&1; then "
        "  rpm -qa --last 2>/dev/null | head -n 25; "
        "elif [ -f /var/log/dnf.log ]; then "
        "  tail -n 25 /var/log/dnf.log 2>/dev/null; "
        "else "
        "  echo 'package history log unavailable'; "
        "fi"
    ),

    "Containers": (
        "echo '== CONTAINER ENGINES =='; "
        "if which docker >/dev/null 2>&1; then "
        "  d_ver=$(docker --version 2>/dev/null | head -1); "
        "  if docker info >/dev/null 2>&1; then "
        "    echo \"docker|active|${d_ver}\"; "
        "  else "
        "    echo \"docker|daemon_offline|${d_ver}\"; "
        "  fi; "
        "else "
        "  echo 'docker|not_installed|-'; "
        "fi; "
        "if which podman >/dev/null 2>&1; then "
        "  p_ver=$(podman --version 2>/dev/null | head -1); "
        "  if podman info >/dev/null 2>&1; then "
        "    echo \"podman|active|${p_ver}\"; "
        "  else "
        "    echo \"podman|daemon_offline|${p_ver}\"; "
        "  fi; "
        "else "
        "  echo 'podman|not_installed|-'; "
        "fi; "
        "echo; echo '== DOCKER CONTAINERS =='; "
        "docker ps -a --no-trunc --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.CreatedAt}}' 2>/dev/null || docker ps -a 2>/dev/null || true; "
        "echo; echo '== DOCKER STATS =='; "
        "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}' 2>/dev/null || true; "
        "echo; echo '== DOCKER IMAGES =='; "
        "docker images --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedAt}}' 2>/dev/null || docker images 2>/dev/null || true; "
        "echo; echo '== DOCKER VOLUMES =='; "
        "docker volume ls --format '{{.Name}}\t{{.Driver}}\t{{.Scope}}' 2>/dev/null || docker volume ls 2>/dev/null || true; "
        "echo; echo '== DOCKER NETWORKS =='; "
        "docker network ls --format '{{.ID}}\t{{.Name}}\t{{.Driver}}\t{{.Scope}}' 2>/dev/null || docker network ls 2>/dev/null || true; "
        "echo; echo '== PODMAN CONTAINERS =='; "
        "podman ps -a --no-trunc --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.CreatedAt}}' 2>/dev/null || podman ps -a 2>/dev/null || true; "
        "echo; echo '== PODMAN STATS =='; "
        "podman stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}' 2>/dev/null || true; "
        "echo; echo '== PODMAN IMAGES =='; "
        "podman images --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedAt}}' 2>/dev/null || podman images 2>/dev/null || true; "
        "echo; echo '== PODMAN VOLUMES =='; "
        "podman volume ls --format '{{.Name}}\t{{.Driver}}\t{{.Scope}}' 2>/dev/null || podman volume ls 2>/dev/null || true; "
        "echo; echo '== PODMAN NETWORKS =='; "
        "podman network ls --format '{{.ID}}\t{{.Name}}\t{{.Driver}}\t{{.Scope}}' 2>/dev/null || podman network ls 2>/dev/null || true; "
        "echo; echo '== COMPOSE TOOLS =='; "
        "if docker compose version >/dev/null 2>&1; then "
        "  echo \"docker_compose_plugin|$(docker compose version 2>&1 | head -1)\"; "
        "fi; "
        "if which docker-compose >/dev/null 2>&1; then "
        "  echo \"docker_compose_standalone|$(docker-compose --version 2>&1 | head -1)\"; "
        "fi; "
        "if which podman-compose >/dev/null 2>&1; then "
        "  echo \"podman_compose|$(podman-compose --version 2>&1 | head -1)\"; "
        "fi"
    ),

    "Performance": (
        "echo '== VMSTAT (CPU/IO/Sys) =='; vmstat 1 3 2>/dev/null || echo 'vmstat not available'; "
        "echo; echo '== IO STATISTICS =='; iostat -xz 1 2 2>/dev/null || echo 'sysstat/iostat not installed'; "
        "echo; echo '== TOP MEMORY CONSUMERS =='; ps aux --sort=-%mem | head -n 10; "
        "echo; echo '== KERNEL RING BUFFER (ERRORS) =='; dmesg -T --level=err,warn 2>/dev/null | tail -n 15 || dmesg | grep -iE 'error|warn|fail' | tail -n 15 || echo 'dmesg restricted' "
    ),
}


def build_journalctl_cmd(
    limit: int = 100,
    priority: str = "all",
    since: str = "",
    user: str = "",
    unit: str = "",
    grep_text: str = "",
) -> str:
    """Builds a dynamic journalctl query command based on user-selected filters."""
    import re
    import shlex

    args = ["journalctl", f"-n {int(limit)}", "--no-pager"]

    if priority and priority != "all":
        clean_p = re.sub(r'[^a-zA-Z0-9]', '', priority)
        args.append(f"-p {clean_p}")

    if since:
        if since == "-b":
            args.append("-b")
        else:
            args.append(f"--since {shlex.quote(since)}")

    if unit and unit != "all":
        clean_u = re.sub(r'[^a-zA-Z0-9_\-\.@]', '', unit)
        args.append(f"-u {clean_u}")

    if user and user != "all":
        clean_user = re.sub(r'[^a-zA-Z0-9_\-]', '', user)
        if clean_user.isdigit():
            args.append(f"_UID={clean_user}")
        else:
            args.append(f"_UID=$(id -u {clean_user} 2>/dev/null || echo '')")

    if grep_text:
        args.append(f"-g {shlex.quote(grep_text)}")

    # Try short-iso first, fallback to standard output
    jcmd = " ".join(args)
    return (
        f"echo '== RECENT ERRORS/WARNINGS =='; "
        f"{jcmd} -o short-iso 2>/dev/null "
        f"|| {jcmd} 2>/dev/null "
        f"|| tail -n {int(limit)} /var/log/syslog 2>/dev/null "
        f"|| tail -n {int(limit)} /var/log/messages 2>/dev/null "
        f"|| echo 'no journal access'"
    )


def package_update_single_cmd(pkg: str) -> str:
    """Universal shell command to update an individual package across Linux distros."""
    import re
    clean_pkg = re.sub(r'[^a-zA-Z0-9_\-\.\+:]', '', pkg)
    return (
        f"if which apt-get >/dev/null 2>&1; then "
        f"  DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade -y {clean_pkg}; "
        f"elif which dnf >/dev/null 2>&1; then "
        f"  dnf upgrade -y {clean_pkg}; "
        f"elif which yum >/dev/null 2>&1; then "
        f"  yum update -y {clean_pkg}; "
        f"elif which pacman >/dev/null 2>&1; then "
        f"  pacman -S --noconfirm {clean_pkg}; "
        f"elif which zypper >/dev/null 2>&1; then "
        f"  zypper update -y {clean_pkg}; "
        f"else "
        f"  echo 'No supported package manager found' && exit 1; "
        f"fi"
    )


def package_update_all_cmd() -> str:
    """Universal shell command to upgrade all upgradable packages across Linux distros."""
    return (
        "if which apt-get >/dev/null 2>&1; then "
        "  DEBIAN_FRONTEND=noninteractive apt-get upgrade -y; "
        "elif which dnf >/dev/null 2>&1; then "
        "  dnf upgrade -y; "
        "elif which yum >/dev/null 2>&1; then "
        "  yum update -y; "
        "elif which pacman >/dev/null 2>&1; then "
        "  pacman -Syu --noconfirm; "
        "elif which zypper >/dev/null 2>&1; then "
        "  zypper update -y; "
        "else "
        "  echo 'No supported package manager found' && exit 1; "
        "fi"
    )


def package_refresh_cache_cmd() -> str:
    """Universal shell command to refresh package manager repository cache."""
    return (
        "if which apt-get >/dev/null 2>&1; then "
        "  apt-get update -q; "
        "elif which dnf >/dev/null 2>&1; then "
        "  dnf makecache -q; "
        "elif which yum >/dev/null 2>&1; then "
        "  yum makecache -q; "
        "elif which pacman >/dev/null 2>&1; then "
        "  pacman -Sy; "
        "elif which zypper >/dev/null 2>&1; then "
        "  zypper refresh; "
        "fi"
    )


def container_op_cmd(engine: str, op: str, container_id: str) -> str:
    """Builds container control command (start, stop, restart, pause, unpause)."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    clean_op = op.strip().lower()
    if clean_op not in ("start", "stop", "restart", "pause", "unpause"):
        clean_op = "restart"
    return f"{eng} {clean_op} {shlex.quote(container_id.strip())}"


def container_rm_cmd(engine: str, container_id: str, force: bool = True) -> str:
    """Builds container removal command."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    flag = "-f " if force else ""
    return f"{eng} rm {flag}{shlex.quote(container_id.strip())}"


def container_logs_cmd(engine: str, container_id: str, tail: int = 200, timestamps: bool = False) -> str:
    """Builds container log extraction command."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    ts_flag = "-t " if timestamps else ""
    return f"{eng} logs --tail {int(tail)} {ts_flag}{shlex.quote(container_id.strip())}"


def container_inspect_cmd(engine: str, target: str) -> str:
    """Builds container or image inspect command."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} inspect {shlex.quote(target.strip())}"


def container_run_cmd(
    engine: str,
    image: str,
    name: str = "",
    ports: list[str] | str = "",
    volumes: list[str] | str = "",
    envs: list[str] | str = "",
    restart: str = "unless-stopped",
    detach: bool = True,
    network: str = "",
    cmd: str = "",
) -> str:
    """Builds a container run command for Docker or Podman."""
    import re
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    parts = [eng, "run"]
    if detach:
        parts.append("-d")
    if name and name.strip():
        parts.extend(["--name", shlex.quote(name.strip())])
    if restart and restart.strip() != "no":
        parts.extend(["--restart", shlex.quote(restart.strip())])
    if network and network.strip():
        parts.extend(["--network", shlex.quote(network.strip())])

    # Ports (e.g. "8080:80, 8443:443" or list)
    if isinstance(ports, str):
        p_list = [p.strip() for p in re.split(r'[,;\n]', ports) if p.strip()]
    else:
        p_list = [str(p).strip() for p in ports if str(p).strip()]
    for p in p_list:
        parts.extend(["-p", shlex.quote(p)])

    # Volumes (e.g. "/host/dir:/app:ro" or list)
    if isinstance(volumes, str):
        v_list = [v.strip() for v in re.split(r'[,;\n]', volumes) if v.strip()]
    else:
        v_list = [str(v).strip() for v in volumes if str(v).strip()]
    for v in v_list:
        parts.extend(["-v", shlex.quote(v)])

    # Envs (e.g. "ENV=production" or list)
    if isinstance(envs, str):
        e_list = [e.strip() for e in re.split(r'[,;\n]', envs) if e.strip()]
    else:
        e_list = [str(e).strip() for e in envs if str(e).strip()]
    for e in e_list:
        parts.extend(["-e", shlex.quote(e)])

    parts.append(shlex.quote(image.strip()))
    if cmd and cmd.strip():
        parts.append(cmd.strip())

    return " ".join(parts)


def container_prune_cmd(engine: str) -> str:
    """Builds command to prune stopped containers."""
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} container prune -f"


def image_pull_cmd(engine: str, image: str) -> str:
    """Builds command to pull an image from container registry."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} pull {shlex.quote(image.strip())}"


def image_search_cmd(engine: str, term: str, limit: int = 25) -> str:
    """Builds command to search container registry."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} search --limit {int(limit)} {shlex.quote(term.strip())}"


def image_rm_cmd(engine: str, image_id: str, force: bool = False) -> str:
    """Builds command to remove container image."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    flag = "-f " if force else ""
    return f"{eng} rmi {flag}{shlex.quote(image_id.strip())}"


def image_prune_cmd(engine: str) -> str:
    """Builds command to prune unused container images."""
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} image prune -f"


def volume_create_cmd(engine: str, name: str, driver: str = "") -> str:
    """Builds command to create a container volume."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    dr_flag = f"--driver {shlex.quote(driver.strip())} " if driver and driver.strip() else ""
    return f"{eng} volume create {dr_flag}{shlex.quote(name.strip())}"


def volume_rm_cmd(engine: str, name: str, force: bool = False) -> str:
    """Builds command to remove a container volume."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    flag = "-f " if force else ""
    return f"{eng} volume rm {flag}{shlex.quote(name.strip())}"


def volume_prune_cmd(engine: str) -> str:
    """Builds command to prune unused container volumes."""
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} volume prune -f"


def network_create_cmd(engine: str, name: str, driver: str = "bridge", subnet: str = "") -> str:
    """Builds command to create a container network."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    dr_val = driver.strip() if driver and driver.strip() else "bridge"
    sub_flag = f"--subnet {shlex.quote(subnet.strip())} " if subnet and subnet.strip() else ""
    return f"{eng} network create --driver {shlex.quote(dr_val)} {sub_flag}{shlex.quote(name.strip())}"


def network_rm_cmd(engine: str, name: str) -> str:
    """Builds command to remove a container network."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} network rm {shlex.quote(name.strip())}"


def network_prune_cmd(engine: str) -> str:
    """Builds command to prune unused container networks."""
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    return f"{eng} network prune -f"


def dockerfile_build_cmd(
    engine: str,
    tag: str,
    dockerfile: str = "Dockerfile",
    context_dir: str = ".",
    build_args: Optional[list[str] | str] = None,
    target: str = "",
    no_cache: bool = False,
    pull: bool = False,
) -> str:
    """Builds a container image using Dockerfile or Containerfile."""
    import re
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    parts = [eng, "build", "-t", shlex.quote(tag.strip()), "-f", shlex.quote(dockerfile.strip())]
    if target and target.strip():
        parts.extend(["--target", shlex.quote(target.strip())])
    if no_cache:
        parts.append("--no-cache")
    if pull:
        parts.append("--pull")

    if build_args:
        if isinstance(build_args, str):
            b_list = [b.strip() for b in re.split(r'[,;\n]', build_args) if b.strip()]
        else:
            b_list = [str(b).strip() for b in build_args if str(b).strip()]
        for b in b_list:
            parts.extend(["--build-arg", shlex.quote(b)])

    parts.append(shlex.quote(context_dir.strip() or "."))
    return " ".join(parts)


def compose_up_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
    detach: bool = True,
    build: bool = False,
) -> str:
    """Builds compose up command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.append("up")
    if detach:
        parts.append("-d")
    if build:
        parts.append("--build")
    return " ".join(parts)


def compose_down_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
    remove_volumes: bool = False,
) -> str:
    """Builds compose down command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.append("down")
    if remove_volumes:
        parts.append("-v")
    return " ".join(parts)


def compose_restart_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
    service: str = "",
) -> str:
    """Builds compose restart command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.append("restart")
    if service and service.strip():
        parts.append(shlex.quote(service.strip()))
    return " ".join(parts)


def compose_logs_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
    tail: int = 200,
    service: str = "",
) -> str:
    """Builds compose logs command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.extend(["logs", "--tail", str(int(tail))])
    if service and service.strip():
        parts.append(shlex.quote(service.strip()))
    return " ".join(parts)


def compose_ps_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
) -> str:
    """Builds compose ps command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.append("ps")
    return " ".join(parts)


def compose_build_cmd(
    compose_tool: str = "docker compose",
    file_path: str = "",
    project_dir: str = "",
    project_name: str = "",
    no_cache: bool = False,
) -> str:
    """Builds compose build command."""
    import shlex
    tool = compose_tool.strip() if compose_tool.strip() else "docker compose"
    parts = [tool]
    if file_path and file_path.strip():
        parts.extend(["-f", shlex.quote(file_path.strip())])
    if project_dir and project_dir.strip():
        parts.extend(["--project-directory", shlex.quote(project_dir.strip())])
    if project_name and project_name.strip():
        parts.extend(["-p", shlex.quote(project_name.strip())])
    parts.append("build")
    if no_cache:
        parts.append("--no-cache")
    return " ".join(parts)


def container_exec_interactive_cmd(
    engine: str,
    container_id: str,
    shell: str = "/bin/sh",
    user: str = "",
    workdir: str = "",
) -> str:
    """Builds interactive exec command for launching a container terminal session."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    parts = [eng, "exec", "-it"]
    if user and user.strip():
        parts.extend(["-u", shlex.quote(user.strip())])
    if workdir and workdir.strip():
        parts.extend(["-w", shlex.quote(workdir.strip())])
    parts.extend([shlex.quote(container_id.strip()), shell.strip() or "/bin/sh"])
    return " ".join(parts)


def container_exec_noninteractive_cmd(
    engine: str,
    container_id: str,
    command: str,
    user: str = "",
    workdir: str = "",
) -> str:
    """Builds non-interactive exec command to run a command inside a container and return output."""
    import shlex
    eng = "podman" if engine.strip().lower() == "podman" else "docker"
    parts = [eng, "exec"]
    if user and user.strip():
        parts.extend(["-u", shlex.quote(user.strip())])
    if workdir and workdir.strip():
        parts.extend(["-w", shlex.quote(workdir.strip())])
    parts.append(shlex.quote(container_id.strip()))
    parts.extend(["sh", "-c", shlex.quote(command.strip())])
    return " ".join(parts)



