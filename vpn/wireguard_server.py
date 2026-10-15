"""
WireGuard VPN Server setup and administration automation.
Uses standard default Linux repository utilities (wireguard, wireguard-tools, iptables, systemd).
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable, Optional


class WireGuardServerManager:
    """
    Automates deployment, configuration, peer management, and diagnostics
    of a WireGuard VPN server on a connected Linux server (remote or local).
    """

    DEFAULT_PORT = 51820
    DEFAULT_SUBNET = "10.8.0.1/24"
    DEFAULT_DNS = "1.1.1.1, 8.8.8.8"

    @staticmethod
    def get_prerequisites_check_script() -> str:
        """Script to check if wireguard tools and firewall are installed."""
        return """
echo "=== WG_CHECK_START ==="
echo "DISTRO: $( (grep -E '^ID=' /etc/os-release || echo 'ID=linux') | cut -d= -f2 | tr -d '\"') "
echo "WG_BIN: $(which wg 2>/dev/null || echo 'NOT_FOUND')"
echo "WG_QUICK_BIN: $(which wg-quick 2>/dev/null || echo 'NOT_FOUND')"
echo "IPTABLES_BIN: $(which iptables 2>/dev/null || echo 'NOT_FOUND')"
echo "IP_FORWARD: $(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo '0')"
echo "DEF_IFACE: $(ip route show default 2>/dev/null | awk '{print $5}' | head -n 1)"
echo "EXT_IP: $(curl -s4 --max-time 2 ifconfig.me 2>/dev/null || ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1)"
echo "WG_ACTIVE: $(systemctl is-active wg-quick@wg0 2>/dev/null || echo 'inactive')"
echo "CONF_EXISTS: $(test -f /etc/wireguard/wg0.conf && echo 'yes' || echo 'no')"
echo "=== WG_CHECK_END ==="
"""

    @staticmethod
    def get_install_packages_script(distro_id: str = "") -> str:
        """Script to install wireguard and utilities from default Linux repositories."""
        d = distro_id.lower().strip()
        if "debian" in d or "ubuntu" in d or "mint" in d or "pop" in d:
            pkg_cmd = "DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard wireguard-tools iptables resolvconf"
        elif "fedora" in d or "centos" in d or "rhel" in d or "alma" in d or "rocky" in d:
            pkg_cmd = "dnf install -y epel-release 2>/dev/null || true; dnf install -y wireguard-tools iptables"
        elif "arch" in d or "manjaro" in d:
            pkg_cmd = "pacman -Sy --noconfirm wireguard-tools iptables"
        elif "alpine" in d:
            pkg_cmd = "apk add wireguard-tools iptables"
        elif "suse" in d or "opensuse" in d:
            pkg_cmd = "zypper install -y wireguard-tools iptables"
        else:
            # Universal fallback attempting apt, dnf, then pacman
            pkg_cmd = "(which apt-get >/dev/null 2>&1 && apt-get update && apt-get install -y wireguard wireguard-tools iptables) || (which dnf >/dev/null 2>&1 && dnf install -y wireguard-tools iptables) || (which pacman >/dev/null 2>&1 && pacman -Sy --noconfirm wireguard-tools iptables)"

        return f"""
echo ">>> Installing WireGuard from system repositories..."
{pkg_cmd}
echo ">>> Installation completed with exit code: $?"
which wg && which wg-quick
"""

    @staticmethod
    def get_deploy_server_script(
        server_ext_ip: str,
        port: int = 51820,
        subnet_cidr: str = "10.8.0.1/24",
        dns: str = "1.1.1.1, 8.8.8.8",
        net_iface: str = "eth0",
        enable_firewall: bool = True,
    ) -> str:
        """
        Generate complete deployment script for WireGuard server on the host.
        Sets up keys, sysctl forwarding, wg0.conf, iptables masquerade, and systemd service.
        """
        port = int(port)
        if not net_iface:
            net_iface = "$(ip route show default | awk '{print $5}' | head -n 1)"

        return f"""
set -e
echo ">>> Deploying WireGuard Server (Interface: wg0, Port: {port}, Subnet: {subnet_cidr})..."

# 1. Create secure config directory
mkdir -p /etc/wireguard
chmod 700 /etc/wireguard

# 2. Generate Server Key Pair if not already present
if [ ! -f /etc/wireguard/server_private.key ]; then
    echo ">>> Generating server WireGuard cryptographic keys..."
    wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
    chmod 600 /etc/wireguard/server_private.key
fi

SERVER_PRIVKEY=$(cat /etc/wireguard/server_private.key)
SERVER_PUBKEY=$(cat /etc/wireguard/server_public.key)

# 3. Detect outgoing network interface if needed
IFACE="{net_iface}"
if [ -z "$IFACE" ] || [ "$IFACE" = "auto" ]; then
    IFACE=$(ip route show default 2>/dev/null | awk '{{print $5}}' | head -n 1)
    [ -z "$IFACE" ] && IFACE="eth0"
fi
echo ">>> Using NAT outgoing interface: $IFACE"

# 4. Enable IPv4 packet forwarding in Linux kernel
echo ">>> Enabling IPv4 forwarding..."
sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wireguard.conf

# 5. Extract existing peers if wg0.conf already exists
EXISTING_PEERS=""
if [ -f /etc/wireguard/wg0.conf ]; then
    echo ">>> Preserving existing peers from current wg0.conf..."
    EXISTING_PEERS=$(awk '/^\\[Peer\\]/{{p=1}} p{{print}}' /etc/wireguard/wg0.conf)
fi

# 6. Write /etc/wireguard/wg0.conf
cat <<'EOF' > /etc/wireguard/wg0.conf
[Interface]
Address = {subnet_cidr}
ListenPort = {port}
PrivateKey = SERVER_PRIVKEY_PLACEHOLDER
SaveConfig = false

# NAT & Routing via iptables
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o IFACE_PLACEHOLDER -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o IFACE_PLACEHOLDER -j MASQUERADE

EOF

# Replace placeholders
sed -i "s|SERVER_PRIVKEY_PLACEHOLDER|$SERVER_PRIVKEY|g" /etc/wireguard/wg0.conf
sed -i "s|IFACE_PLACEHOLDER|$IFACE|g" /etc/wireguard/wg0.conf

# Re-append preserved peers if any
if [ -n "$EXISTING_PEERS" ]; then
    echo "" >> /etc/wireguard/wg0.conf
    echo "$EXISTING_PEERS" >> /etc/wireguard/wg0.conf
fi
chmod 600 /etc/wireguard/wg0.conf

# 7. Configure Firewall rules
if which ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    echo ">>> Opening port {port}/udp in UFW firewall..."
    ufw allow {port}/udp comment 'WireGuard VPN' || true
fi
if which firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld >/dev/null 2>&1; then
    echo ">>> Opening port {port}/udp in Firewalld..."
    firewall-cmd --add-port={port}/udp --permanent || true
    firewall-cmd --reload || true
fi

# 8. Start and enable systemd service
echo ">>> Starting and enabling wg-quick@wg0 systemd service..."
systemctl daemon-reload
systemctl enable --now wg-quick@wg0
systemctl restart wg-quick@wg0

echo "=== SERVER_DEPLOY_SUCCESS ==="
echo "SERVER_PUBKEY: $SERVER_PUBKEY"
echo "SERVER_PORT: {port}"
echo "SERVER_IFACE: wg0"
echo "STATUS: $(systemctl is-active wg-quick@wg0)"
"""

    @staticmethod
    def get_add_peer_script(
        peer_name: str,
        client_ip: str,
        client_pubkey: str,
        preshared_key: str = "",
    ) -> str:
        """
        Script to add a client peer to server wg0.conf and live WireGuard interface.
        """
        psk_line = f"PresharedKey = {preshared_key}\n" if preshared_key else ""
        return f"""
set -e
echo ">>> Adding Peer '{peer_name}' (IP: {client_ip}) to WireGuard..."

# Append peer to /etc/wireguard/wg0.conf
cat <<'EOF' >> /etc/wireguard/wg0.conf

# Client: {peer_name}
[Peer]
PublicKey = {client_pubkey}
{psk_line}AllowedIPs = {client_ip}/32
EOF

# Live apply to running WireGuard interface if active
if systemctl is-active wg-quick@wg0 >/dev/null 2>&1; then
    wg set wg0 peer {shlex.quote(client_pubkey)} allowed-ips {shlex.quote(client_ip + '/32')}
    echo ">>> Applied peer live to wg0"
fi

echo "=== PEER_ADDED_SUCCESS ==="
"""

    @staticmethod
    def get_remove_peer_script(client_pubkey: str) -> str:
        """Script to remove a peer from server wg0 and wg0.conf."""
        return f"""
set -e
echo ">>> Removing peer {client_pubkey}..."

# Remove from live interface
if systemctl is-active wg-quick@wg0 >/dev/null 2>&1; then
    wg set wg0 peer {shlex.quote(client_pubkey)} remove || true
fi

# Remove peer block from /etc/wireguard/wg0.conf
python3 -c "
import sys
conf = '/etc/wireguard/wg0.conf'
target_pub = sys.argv[1].strip()
try:
    with open(conf, 'r') as f:
        content = f.read()
    
    sections = content.split('[Peer]')
    new_sections = [sections[0]]
    for sec in sections[1:]:
        if target_pub not in sec:
            new_sections.append('[Peer]' + sec)
    with open(conf, 'w') as f:
        f.write(''.join(new_sections).strip() + '\\n')
    print('Peer removed from configuration file.')
except Exception as ex:
    print('Failed to update conf:', ex)
" {shlex.quote(client_pubkey)}

echo "=== PEER_REMOVED_SUCCESS ==="
"""

    @staticmethod
    def parse_server_status(raw_output: str) -> dict[str, Any]:
        """Parse 'wg show' and status output into structured metrics and peers."""
        status = {
            "active": False,
            "interface": "wg0",
            "public_key": "",
            "listening_port": "",
            "peers": [],
        }

        if "interface:" in raw_output.lower() or "peer:" in raw_output.lower():
            status["active"] = True

        current_peer: Optional[dict[str, Any]] = None

        for line in raw_output.splitlines():
            sline = line.strip()
            if not sline:
                continue

            if sline.startswith("interface:"):
                status["interface"] = sline.partition(":")[2].strip()
            elif sline.startswith("public key:") and not current_peer:
                status["public_key"] = sline.partition(":")[2].strip()
            elif sline.startswith("listening port:"):
                status["listening_port"] = sline.partition(":")[2].strip()
            elif sline.startswith("peer:"):
                if current_peer:
                    status["peers"].append(current_peer)
                current_peer = {
                    "public_key": sline.partition(":")[2].strip(),
                    "endpoint": "",
                    "allowed_ips": "",
                    "latest_handshake": "Never",
                    "transfer_rx": "0 B",
                    "transfer_tx": "0 B",
                }
            elif current_peer:
                if sline.startswith("endpoint:"):
                    current_peer["endpoint"] = sline.partition(":")[2].strip()
                elif sline.startswith("allowed ips:"):
                    current_peer["allowed_ips"] = sline.partition(":")[2].strip()
                elif sline.startswith("latest handshake:"):
                    current_peer["latest_handshake"] = sline.partition(":")[2].strip()
                elif sline.startswith("transfer:"):
                    trans = sline.partition(":")[2].strip()
                    parts = trans.split("received,")
                    if len(parts) == 2:
                        current_peer["transfer_rx"] = parts[0].strip()
                        current_peer["transfer_tx"] = parts[1].replace("sent", "").strip()
                    else:
                        current_peer["transfer_rx"] = trans

        if current_peer:
            status["peers"].append(current_peer)

        return status
