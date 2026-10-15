"""
VPN connection service supporting WireGuard (system repository utilities)
and Cisco AnyConnect (preinstalled binary) with profile management.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from admin_suite.vpn.profiles import (
    VpnProfileManager,
    find_cisco_binary,
    find_wireguard_binary,
)


class VpnService(QObject):
    """
    VPN connection service supporting WireGuard and Cisco AnyConnect.
    """

    result = pyqtSignal(dict)
    profile_changed = pyqtSignal(dict)

    def __init__(self, services):
        super().__init__()

        self.services = services
        self.profiles = VpnProfileManager(services)

        self.connected = False
        self.busy = False
        self.status_checking = False
        self.pending_toggle = False
        self.active_profile_cache: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------

    def _log(self, message: str) -> None:
        self.services.emit_log("vpn", message)

    # ------------------------------------------------------------
    # Active Profile
    # ------------------------------------------------------------

    def get_active_profile(self) -> Optional[dict[str, Any]]:
        prof = self.profiles.get_active_profile()
        self.active_profile_cache = prof
        return prof

    def set_active_profile(self, profile_id: str) -> None:
        self.profiles.set_active_profile(profile_id)
        prof = self.profiles.get_profile(profile_id)
        self.active_profile_cache = prof
        if prof:
            self.profile_changed.emit(prof)
        self.check_status()

    # ------------------------------------------------------------
    # CLI execution
    # ------------------------------------------------------------

    def _run_cmd(
        self,
        cmd: list[str],
        timeout: int = 15,
        input_text: Optional[str] = None,
        use_sudo: bool = False,
    ) -> tuple[int, str, str]:
        """Run CLI command safely with optional sudo."""
        exec_cmd = list(cmd)
        
        # If running as non-root and sudo is required (e.g. for wg-quick)
        sudo_pass = None
        if use_sudo and os.name != "nt" and os.geteuid() != 0:
            sudo_pass = self.services.get_sudo_password("Local") or self.services.get_sudo_password("localhost") or ""
            if sudo_pass:
                exec_cmd = ["sudo", "-S", "-p", ""] + exec_cmd
                input_text = (sudo_pass + "\n" + (input_text or ""))
            else:
                exec_cmd = ["sudo", "-n"] + exec_cmd

        stdin_mode = subprocess.PIPE if input_text is not None else subprocess.DEVNULL

        popen_kwargs: dict[str, Any] = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=stdin_mode,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(exec_cmd, **popen_kwargs)
            try:
                out, err = proc.communicate(input=input_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
                try:
                    proc.kill()
                except Exception:
                    pass
                return 124, "", f"Command timed out after {timeout}s: {' '.join(exec_cmd)}"

            return proc.returncode if proc.returncode is not None else -1, out or "", err or ""
        except FileNotFoundError:
            return 127, "", f"Command not found: {exec_cmd[0]}"
        except Exception as e:
            return 1, "", str(e)

    # ------------------------------------------------------------
    # State parsing
    # ------------------------------------------------------------

    def parse_state(self, output: str, rc: Optional[int] = None) -> Optional[bool]:
        """Parse status output for connected/disconnected state."""
        txt = (output or "").lower()

        # Wireguard checks
        if "interface:" in txt or "peer:" in txt:
            return True
        if "latest handshake:" in txt:
            return True
        if "wg0: <up" in txt or "state up" in txt:
            return True

        # Cisco checks
        if re.search(r"state\s*:\s*connected", txt):
            return True
        if re.search(r"state\s*:\s*disconnected", txt):
            return False
        if re.search(r"\bnot connected\b", txt):
            return False
        if re.search(r"\bdisconnected\b", txt):
            return False

        if rc == 0 and re.search(r"\bconnected\b", txt) and not re.search(r"disconnect", txt):
            return True

        return None

    # ------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------

    def toggle(self) -> None:
        """Toggle VPN connection on active profile."""
        if self.busy:
            self._log("VPN toggle ignored: another VPN operation is running.")
            return

        if self.status_checking:
            self.pending_toggle = True
            self._log("Waiting for VPN status check before toggling.")
            return

        prof = self.get_active_profile()
        if not prof:
            self._log("VPN toggle ignored: No VPN profile configured.")
            self.result.emit({
                "op": "toggle_status",
                "state": False,
                "rc": 1,
                "out": "",
                "err": "No VPN profile configured. Open VPN Configuration to create one.",
            })
            return

        self.busy = True

        def work():
            state = self._check_profile_state_sync(prof)
            self.busy = False

            self.result.emit({
                "op": "toggle_status",
                "state": state,
                "profile": prof,
                "rc": 0 if state is not None else 1,
                "out": f"Active profile: {prof.get('name')}",
                "err": "",
            })

        threading.Thread(target=work, daemon=True).start()

    def connect_vpn(self, target_prof: Optional[dict[str, Any]] = None) -> None:
        """Connect active or specified VPN profile."""
        if self.busy:
            self._log("VPN connect ignored: another operation is in progress.")
            return

        prof = target_prof or self.get_active_profile()
        if not prof:
            self.result.emit({
                "op": "connect",
                "error": "No VPN profile configured. Please add a WireGuard or Cisco profile.",
            })
            return

        self.busy = True

        def work():
            ptype = prof.get("type", "wireguard")
            rc, out, err = -1, "", ""
            state = False

            try:
                if ptype == "wireguard":
                    rc, out, err = self._connect_wireguard_sync(prof)
                else:
                    rc, out, err = self._connect_cisco_sync(prof)

                # Check state after connect attempt
                state = self._check_profile_state_sync(prof)
                if state is None and rc == 0:
                    state = True

            except Exception as ex:
                err = str(ex)
            finally:
                self.busy = False
                self.connected = bool(state)
                self.result.emit({
                    "op": "connect",
                    "profile": prof,
                    "rc": rc,
                    "out": out,
                    "err": err,
                    "state": state,
                })

        threading.Thread(target=work, daemon=True).start()

    def disconnect_vpn(self, target_prof: Optional[dict[str, Any]] = None) -> None:
        """Disconnect active or specified VPN profile."""
        if self.busy:
            self._log("VPN disconnect ignored: another operation is in progress.")
            return

        prof = target_prof or self.get_active_profile()
        if not prof:
            self.result.emit({
                "op": "disconnect",
                "error": "No VPN profile configured.",
            })
            return

        self.busy = True

        def work():
            ptype = prof.get("type", "wireguard")
            rc, out, err = -1, "", ""
            state = False

            try:
                if ptype == "wireguard":
                    rc, out, err = self._disconnect_wireguard_sync(prof)
                else:
                    rc, out, err = self._disconnect_cisco_sync(prof)

                state = self._check_profile_state_sync(prof)
                if state is None and rc == 0:
                    state = False

            except Exception as ex:
                err = str(ex)
            finally:
                self.busy = False
                self.connected = bool(state)
                self.result.emit({
                    "op": "disconnect",
                    "profile": prof,
                    "rc": rc,
                    "out": out,
                    "err": err,
                    "state": state,
                })

        threading.Thread(target=work, daemon=True).start()

    def check_status(self) -> None:
        """Background status poll for active VPN profile."""
        if self.busy or self.status_checking:
            return

        prof = self.get_active_profile()
        if not prof:
            return

        self.status_checking = True

        def work():
            state = self._check_profile_state_sync(prof)
            self.status_checking = False

            pending = self.pending_toggle
            self.pending_toggle = False

            self.connected = bool(state)
            self.result.emit({
                "op": "poll_status",
                "profile": prof,
                "state": state,
                "rc": 0 if state is not None else 1,
                "out": f"Profile: {prof.get('name')}",
                "err": "",
                "pending_toggle": pending,
            })

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------
    # Internal sync runners
    # ------------------------------------------------------------

    def _check_profile_state_sync(self, prof: dict[str, Any]) -> Optional[bool]:
        """Check live state of given profile."""
        ptype = prof.get("type", "wireguard")
        if ptype == "wireguard":
            iface = prof.get("interface", "wg0")
            conf = prof.get("config_path", "")
            if not iface and conf:
                iface = os.path.splitext(os.path.basename(conf))[0]
            
            # Check via wg show
            rc, out, err = self._run_cmd(["wg", "show", iface], timeout=5, use_sudo=True)
            if rc == 0 and ("interface:" in out or "peer:" in out):
                return True
            
            # Check via ip link
            rc_ip, out_ip, _ = self._run_cmd(["ip", "link", "show", iface], timeout=3)
            if rc_ip == 0 and "state up" in out_ip.lower():
                return True
            return False

        else:
            # Cisco status check
            cli = prof.get("cisco_bin") or find_cisco_binary()
            rc, out, err = self._run_cmd([cli, "status"], timeout=6)
            return self.parse_state(out + "\n" + err, rc)

    def _connect_wireguard_sync(self, prof: dict[str, Any]) -> tuple[int, str, str]:
        """Connect WireGuard client using standard repo wg-quick utility."""
        wg_bin = find_wireguard_binary()
        conf_path = prof.get("config_path", "")
        if not conf_path or not os.path.exists(conf_path):
            # Try to build and save config if manual
            self.profiles.save_or_update_profile(prof)
            conf_path = prof.get("config_path", "")

        if not conf_path or not os.path.exists(conf_path):
            return 1, "", f"WireGuard configuration file not found: {conf_path}"

        self._log(f"Starting WireGuard VPN with: {wg_bin} up {conf_path}")
        return self._run_cmd([wg_bin, "up", conf_path], timeout=25, use_sudo=True)

    def _disconnect_wireguard_sync(self, prof: dict[str, Any]) -> tuple[int, str, str]:
        """Disconnect WireGuard client using wg-quick down."""
        wg_bin = find_wireguard_binary()
        conf_path = prof.get("config_path", "")
        iface = prof.get("interface", "wg0")
        target = conf_path if (conf_path and os.path.exists(conf_path)) else iface

        self._log(f"Stopping WireGuard VPN: {wg_bin} down {target}")
        return self._run_cmd([wg_bin, "down", target], timeout=25, use_sudo=True)

    def _connect_cisco_sync(self, prof: dict[str, Any]) -> tuple[int, str, str]:
        """Connect Cisco AnyConnect using preinstalled binary."""
        cli = prof.get("cisco_bin") or find_cisco_binary()
        host = prof.get("cisco_host", "")
        user = prof.get("cisco_user", "")
        group = prof.get("cisco_group", "")
        
        prof_id = prof.get("id", "")
        vpn_pass = self.profiles.get_profile_secret(prof_id, "pass") or self.services.secrets.get("vpn_pass", "")
        cert_pass = self.profiles.get_profile_secret(prof_id, "cert_pass") or self.services.secrets.get("vpn_cert_pass", "")

        if not host:
            return 1, "", "Cisco VPN host is not configured in profile."

        # Cisco AnyConnect interactive stdin sequence
        inputs = ""
        if group:
            inputs += f"{group}\n"
        if user:
            inputs += f"{user}\n"
        if cert_pass:
            inputs += f"{cert_pass}\n"
        if vpn_pass:
            inputs += f"{vpn_pass}\n"
        inputs += "y\n"  # accept certificate warning if prompted

        self._log(f"Connecting Cisco VPN to {host} via {cli}")
        return self._run_cmd([cli, "-s", "connect", host], timeout=60, input_text=inputs)

    def _disconnect_cisco_sync(self, prof: dict[str, Any]) -> tuple[int, str, str]:
        """Disconnect Cisco AnyConnect using preinstalled binary."""
        cli = prof.get("cisco_bin") or find_cisco_binary()
        self._log(f"Disconnecting Cisco VPN via {cli}")
        return self._run_cmd([cli, "-s", "disconnect"], timeout=25)
