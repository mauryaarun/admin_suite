"""
VPN Profiles management for WireGuard and Cisco AnyConnect.
"""

from __future__ import annotations

import configparser
import io
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from admin_suite.core.paths import DATA_DIR, VPN_PROFILES_FILE
from admin_suite.core.utils import read_json, write_json_secure


def find_cisco_binary() -> str:
    """Find installed Cisco AnyConnect / Secure Client binary path."""
    candidates = [
        "/opt/cisco/anyconnect/bin/vpn",
        "/opt/cisco/secureclient/bin/vpn",
        "/usr/local/bin/vpn",
        "/usr/bin/vpn",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    found = shutil.which("vpn")
    if found and os.access(found, os.X_OK):
        return found
    return "/opt/cisco/anyconnect/bin/vpn"


def find_wireguard_binary() -> str:
    """Find wg-quick binary from system repositories."""
    candidates = [
        "/usr/bin/wg-quick",
        "/usr/sbin/wg-quick",
        "/usr/local/bin/wg-quick",
        "/bin/wg-quick",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    found = shutil.which("wg-quick")
    return found or "wg-quick"


def generate_wireguard_keys() -> tuple[str, str]:
    """
    Generate WireGuard private key and public key using standard 'wg' utility.
    Returns (private_key, public_key).
    """
    wg_bin = shutil.which("wg") or "wg"
    try:
        priv = subprocess.check_output([wg_bin, "genkey"], text=True, stderr=subprocess.PIPE).strip()
        proc = subprocess.Popen([wg_bin, "pubkey"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        pub, _ = proc.communicate(input=priv)
        return priv, pub.strip()
    except Exception:
        # Fallback to pure-python base64 key generation if wg binary is not locally present
        import base64
        import os
        random_bytes = os.urandom(32)
        priv = base64.b64encode(random_bytes).decode("ascii")
        return priv, ""


def parse_wireguard_conf_content(text: str) -> dict[str, Any]:
    """Parse a WireGuard .conf file content into structured configuration fields."""
    parsed: dict[str, Any] = {
        "address": "",
        "dns": "",
        "private_key": "",
        "listen_port": "",
        "public_key": "",
        "endpoint": "",
        "allowed_ips": "0.0.0.0/0, ::/0",
        "persistent_keepalive": "25",
        "preshared_key": "",
    }
    
    current_sec = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_sec = line[1:-1].strip().lower()
            continue
        
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip().lower()
            v = v.strip()
            if current_sec == "interface":
                if k == "address":
                    parsed["address"] = v
                elif k == "dns":
                    parsed["dns"] = v
                elif k == "privatekey":
                    parsed["private_key"] = v
                elif k == "listenport":
                    parsed["listen_port"] = v
            elif current_sec == "peer":
                if k == "publickey":
                    parsed["public_key"] = v
                elif k == "endpoint":
                    parsed["endpoint"] = v
                elif k == "allowedips":
                    parsed["allowed_ips"] = v
                elif k == "persistentkeepalive":
                    parsed["persistent_keepalive"] = v
                elif k == "presharedkey":
                    parsed["preshared_key"] = v
    return parsed


def build_wireguard_conf_content(prof: dict[str, Any]) -> str:
    """Build a complete WireGuard .conf text string from profile dictionary."""
    lines = ["[Interface]"]
    if prof.get("address"):
        lines.append(f"Address = {prof['address']}")
    if prof.get("dns"):
        lines.append(f"DNS = {prof['dns']}")
    if prof.get("private_key"):
        lines.append(f"PrivateKey = {prof['private_key']}")
    if prof.get("listen_port"):
        lines.append(f"ListenPort = {prof['listen_port']}")
    
    lines.append("")
    lines.append("[Peer]")
    if prof.get("public_key"):
        lines.append(f"PublicKey = {prof['public_key']}")
    if prof.get("endpoint"):
        lines.append(f"Endpoint = {prof['endpoint']}")
    if prof.get("allowed_ips"):
        lines.append(f"AllowedIPs = {prof['allowed_ips']}")
    if prof.get("persistent_keepalive"):
        lines.append(f"PersistentKeepalive = {prof['persistent_keepalive']}")
    if prof.get("preshared_key"):
        lines.append(f"PresharedKey = {prof['preshared_key']}")
    lines.append("")
    return "\n".join(lines)


class VpnProfileManager:
    """
    Manages persistence and secrets for VPN profiles (WireGuard and Cisco AnyConnect).
    """

    def __init__(self, services):
        self.services = services
        data_dir = getattr(services.paths, "DATA_DIR", DATA_DIR)
        self.wg_configs_dir = data_dir / "wireguard"
        try:
            self.wg_configs_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.wg_configs_dir, 0o700)
        except Exception:
            pass

    def load_profiles(self) -> list[dict[str, Any]]:
        """Load all saved VPN profiles."""
        vpn_file = getattr(self.services.paths, "VPN_PROFILES_FILE", VPN_PROFILES_FILE)
        profs = read_json(vpn_file, [])
        if not isinstance(profs, list):
            profs = []
        
        # If legacy config had vpn_cli / vpn_host and no profiles exist, migrate to a Cisco profile
        if not profs and self.services.config.get("vpn_cli") and self.services.config.get("vpn_host"):
            legacy_id = "cisco-default"
            legacy_prof = {
                "id": legacy_id,
                "name": f"Cisco ({self.services.config.get('vpn_host')})",
                "type": "cisco",
                "active": True,
                "cisco_bin": self.services.config.get("vpn_cli", find_cisco_binary()),
                "cisco_host": self.services.config.get("vpn_host", ""),
                "cisco_group": "",
                "cisco_user": "",
            }
            profs.append(legacy_prof)
            self.save_profiles(profs)
            self.services.config.set("active_vpn_profile_id", legacy_id)
        
        return profs

    def save_profiles(self, profiles: list[dict[str, Any]]) -> None:
        """Save profiles list to JSON."""
        vpn_file = getattr(self.services.paths, "VPN_PROFILES_FILE", VPN_PROFILES_FILE)
        try:
            write_json_secure(vpn_file, profiles)
        except Exception:
            pass

    def get_profile(self, profile_id: str) -> Optional[dict[str, Any]]:
        """Get profile by ID."""
        for p in self.load_profiles():
            if p.get("id") == profile_id:
                return p
        return None

    def get_active_profile(self) -> Optional[dict[str, Any]]:
        """Get currently active VPN profile."""
        profs = self.load_profiles()
        if not profs:
            return None
        active_id = self.services.config.get("active_vpn_profile_id", "")
        if active_id:
            for p in profs:
                if p.get("id") == active_id:
                    return p
        # Fallback to profile marked active, or first profile
        for p in profs:
            if p.get("active"):
                return p
        return profs[0]

    def set_active_profile(self, profile_id: str) -> None:
        """Mark profile as active."""
        profs = self.load_profiles()
        for p in profs:
            p["active"] = (p.get("id") == profile_id)
        self.save_profiles(profs)
        self.services.config.set("active_vpn_profile_id", profile_id)

    def save_or_update_profile(
        self,
        prof: dict[str, Any],
        secrets: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Create or update a VPN profile and persist passwords."""
        profs = self.load_profiles()
        prof_id = prof.get("id") or f"vpn-{uuid.uuid4().hex[:8]}"
        prof["id"] = prof_id

        # Wireguard configuration file generation
        if prof.get("type") == "wireguard":
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", prof.get("name", "wg0")).lower()
            conf_filename = f"{safe_name}.conf"
            conf_path = self.wg_configs_dir / conf_filename
            
            # If using manual config or provided content, write the .conf file
            if prof.get("config_type") == "manual" or prof.get("raw_content"):
                content = prof.get("raw_content") or build_wireguard_conf_content(prof)
                try:
                    conf_path.parent.mkdir(parents=True, exist_ok=True)
                    conf_path.write_text(content, encoding="utf-8")
                    try:
                        os.chmod(conf_path, 0o600)
                    except Exception:
                        pass
                except Exception:
                    pass
                prof["config_path"] = str(conf_path)
                prof["interface"] = safe_name[:15]  # Linux interface name max 15 chars

        # Store secrets if provided
        if secrets:
            for k, v in secrets.items():
                if v is not None:
                    self.services.secrets.set(f"vpn_prof_{prof_id}_{k}", v)

        # Update in list
        replaced = False
        for i, existing in enumerate(profs):
            if existing.get("id") == prof_id:
                profs[i] = prof
                replaced = True
                break
        if not replaced:
            if not profs:
                prof["active"] = True
                self.services.config.set("active_vpn_profile_id", prof_id)
            profs.append(prof)

        self.save_profiles(profs)
        return prof

    def delete_profile(self, profile_id: str) -> None:
        """Delete profile and associated secrets and generated conf file."""
        profs = self.load_profiles()
        target = None
        remaining = []
        for p in profs:
            if p.get("id") == profile_id:
                target = p
            else:
                remaining.append(p)

        if target:
            # Clean secrets
            for key in ("pass", "cert_pass", "privkey"):
                self.services.secrets.delete(f"vpn_prof_{profile_id}_{key}")
            
            # Clean generated conf if managed inside wg_configs_dir
            conf_p = target.get("config_path", "")
            if conf_p and str(self.wg_configs_dir) in conf_p:
                try:
                    p = Path(conf_p)
                    if p.is_file():
                        p.unlink()
                except Exception:
                    pass

        self.save_profiles(remaining)
        if self.services.config.get("active_vpn_profile_id") == profile_id:
            if remaining:
                self.set_active_profile(remaining[0]["id"])
            else:
                self.services.config.set("active_vpn_profile_id", "")

    def get_profile_secret(self, profile_id: str, key: str) -> str:
        """Retrieve stored secret for profile."""
        return self.services.secrets.get(f"vpn_prof_{profile_id}_{key}", "")
