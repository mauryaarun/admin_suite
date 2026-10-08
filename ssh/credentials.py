"""
SSH credential model.

Important fix compared with the original code:

- password authentication uses password
- SSH key authentication uses passphrase for encrypted private keys
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SshCredentials:
    """
    Structured SSH credentials.

    password:
        Used for password authentication.

    passphrase:
        Used for encrypted SSH private keys.

    key_path:
        Path to SSH private key.
    """

    password: Optional[str] = None
    passphrase: Optional[str] = None
    key_path: Optional[str] = None
    sudo_password: Optional[str] = None

    @property
    def has_key(self) -> bool:
        return bool(self.key_path)


def profile_creds(data: dict[str, Any]) -> SshCredentials:
    """
    Convert profile dictionary into structured credentials.

    Expected profile fields:
    - auth_method: "Password" or "SSH Key"
    - ssh_pass: password or key passphrase
    - ssh_key_path: path to private key
    - sudo_pass: root/sudo elevation password
    """
    auth = data.get("auth_method", "Password")
    secret = data.get("ssh_pass") or None
    key_path = data.get("ssh_key_path") or None
    sudo_pass = data.get("sudo_pass") or None

    if auth == "SSH Key":
        return SshCredentials(
            password=None,
            passphrase=secret,
            key_path=key_path,
            sudo_password=sudo_pass,
        )

    return SshCredentials(
        password=secret,
        passphrase=None,
        key_path=None,
        sudo_password=sudo_pass,
    )


def profile_creds_tuple(data: dict[str, Any]) -> tuple[str, Optional[str]]:
    """
    Compatibility helper for old code expecting:

        password_or_passphrase, key_path

    New code should use profile_creds() instead.
    """
    creds = profile_creds(data)

    if creds.key_path:
        return creds.passphrase or "", creds.key_path

    return creds.password or "", None
