"""
Encrypted, zipped settings export and import for Admin Suite.

Packages global configuration, SSH profiles, database profiles, VPN profiles,
snippets, query/Ansible favorites & history, SSH known_hosts, and optional
stored credentials inside a compressed ZIP encrypted with PBKDF2-HMAC-SHA256 +
Fernet (AES-128-CBC + HMAC-SHA256).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import admin_suite.core.paths as default_paths
from admin_suite.core.utils import write_json_secure

BACKUP_FORMAT = "admin_suite_settings_v1"
DEFAULT_KDF_ITERATIONS = 600_000

_DICT_JSON_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("config", "CONFIG_FILE", "config.json"),
    ("ssh_profiles", "PROFILES_FILE", "profiles.json"),
    ("db_profiles", "DB_PROFILES_FILE", "db_profiles.json"),
    ("vpn_profiles", "VPN_PROFILES_FILE", "vpn_profiles.json"),
    ("last_session", "LAST_SESSION_FILE", "last_session.json"),
)

_LIST_JSON_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("snippets", "SNIPPETS_FILE", "snippets.json"),
    ("query_favorites", "QUERY_FAVORITES_FILE", "query_favorites.json"),
    ("query_history", "QUERY_HISTORY_FILE", "query_history.json"),
    ("ansible_command_sets", "ANSIBLE_COMMAND_SETS_FILE", "ansible_command_sets.json"),
    ("ansible_history", "ANSIBLE_HISTORY_FILE", "ansible_history.json"),
    ("recent", "RECENT_FILE", "recent.json"),
)

_TEXT_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("known_hosts", "HOST_KEYS_FILE", "known_hosts"),
)

_GLOBAL_SECRET_KEYS: tuple[str, ...] = (
    "ssh_pass",
    "db_pass",
    "vpn_cert_pass",
    "vpn_pass",
)


def _paths_mod(services: Any):
    return getattr(services, "paths", None) or default_paths


def _derive_fernet_key(password: str, salt: bytes, iterations: int = DEFAULT_KDF_ITERATIONS) -> bytes:
    if not password:
        raise ValueError("Encryption password must not be empty.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _read_json_file(path: Path) -> Any:
    if not path or not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _collect_secret_keys(services: Any, ssh_profiles: dict[str, Any], db_profiles: dict[str, Any]) -> dict[str, str]:
    secrets_store = getattr(services, "secrets", None)
    if secrets_store is None:
        return {}

    keys: list[str] = list(_GLOBAL_SECRET_KEYS)
    for name in ssh_profiles.keys():
        keys.extend([f"prof_{name}", f"prof_jump_{name}", f"prof_sudo_{name}"])
    for name in db_profiles.keys():
        keys.extend([f"dbprof_{name}", f"dbprof_ssh_{name}"])

    out: dict[str, str] = {}
    for key in keys:
        val = secrets_store.get(key, "")
        if val:
            out[key] = val
    return out


def export_settings_archive(
    output_path: str | Path,
    password: str,
    services: Any,
    *,
    include_config: bool = True,
    include_profiles: bool = True,
    include_extras: bool = True,
    include_secrets: bool = True,
    iterations: int = DEFAULT_KDF_ITERATIONS,
) -> dict[str, Any]:
    """
    Create an encrypted ZIP backup of Admin Suite settings and profiles.

    Returns the archive manifest metadata dict.
    """
    if not password:
        raise ValueError("Please provide an encryption password.")

    paths = _paths_mod(services)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    inner_buf = io.BytesIO()
    included_sections: list[str] = []
    counts: dict[str, int] = {}

    ssh_profiles_data: dict[str, Any] = {}
    db_profiles_data: dict[str, Any] = {}

    with zipfile.ZipFile(inner_buf, "w", compression=zipfile.ZIP_DEFLATED) as inner_zf:
        if include_config:
            cfg_data = dict(getattr(getattr(services, "config", None), "data", {}) or {})
            if not cfg_data:
                cfg_file = Path(getattr(paths, "CONFIG_FILE", default_paths.CONFIG_FILE))
                disk_cfg = _read_json_file(cfg_file)
                if isinstance(disk_cfg, dict):
                    cfg_data = disk_cfg
            inner_zf.writestr("config.json", json.dumps(cfg_data, indent=2))
            included_sections.append("config")
            counts["config_keys"] = len(cfg_data)

        if include_profiles:
            ssh_file = Path(getattr(paths, "PROFILES_FILE", default_paths.PROFILES_FILE))
            raw_ssh = _read_json_file(ssh_file)
            if isinstance(raw_ssh, dict):
                ssh_profiles_data = {
                    k: {sk: sv for sk, sv in v.items() if sk not in ("ssh_pass", "jump_pass", "sudo_pass")}
                    if isinstance(v, dict) else v
                    for k, v in raw_ssh.items()
                }
            inner_zf.writestr("profiles.json", json.dumps(ssh_profiles_data, indent=2))
            included_sections.append("ssh_profiles")
            counts["ssh_profiles"] = len(ssh_profiles_data)

            db_file = Path(getattr(paths, "DB_PROFILES_FILE", default_paths.DB_PROFILES_FILE))
            raw_db = _read_json_file(db_file)
            if isinstance(raw_db, dict):
                db_profiles_data = {
                    k: {sk: sv for sk, sv in v.items() if sk not in ("db_pass", "ssh_pass")}
                    if isinstance(v, dict) else v
                    for k, v in raw_db.items()
                }
            inner_zf.writestr("db_profiles.json", json.dumps(db_profiles_data, indent=2))
            included_sections.append("db_profiles")
            counts["db_profiles"] = len(db_profiles_data)

            vpn_file = Path(getattr(paths, "VPN_PROFILES_FILE", default_paths.VPN_PROFILES_FILE))
            raw_vpn = _read_json_file(vpn_file)
            if isinstance(raw_vpn, dict):
                inner_zf.writestr("vpn_profiles.json", json.dumps(raw_vpn, indent=2))
                included_sections.append("vpn_profiles")
                counts["vpn_profiles"] = len(raw_vpn)

        if include_extras:
            for section, attr, filename in _LIST_JSON_ITEMS:
                p = Path(getattr(paths, attr, getattr(default_paths, attr)))
                data = _read_json_file(p)
                if data is not None:
                    inner_zf.writestr(filename, json.dumps(data, indent=2))
                    included_sections.append(section)
                    counts[section] = len(data) if isinstance(data, (list, dict)) else 1

            last_sess_p = Path(getattr(paths, "LAST_SESSION_FILE", default_paths.LAST_SESSION_FILE))
            last_sess = _read_json_file(last_sess_p)
            if isinstance(last_sess, dict):
                inner_zf.writestr("last_session.json", json.dumps(last_sess, indent=2))
                included_sections.append("last_session")

            for section, attr, filename in _TEXT_ITEMS:
                p = Path(getattr(paths, attr, getattr(default_paths, attr)))
                if p.exists():
                    try:
                        inner_zf.writestr(filename, p.read_text(encoding="utf-8", errors="replace"))
                        included_sections.append(section)
                    except Exception:
                        pass

        if include_secrets:
            secrets_map = _collect_secret_keys(services, ssh_profiles_data, db_profiles_data)
            inner_zf.writestr("secrets.json", json.dumps(secrets_map, indent=2))
            included_sections.append("secrets")
            counts["secrets"] = len(secrets_map)

    salt = os.urandom(16)
    key = _derive_fernet_key(password, salt, iterations=iterations)
    encrypted_payload = Fernet(key).encrypt(inner_buf.getvalue())

    manifest = {
        "format": BACKUP_FORMAT,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": iterations,
        "cipher": "Fernet-AES128-CBC-HMAC-SHA256",
        "salt": base64.b64encode(salt).decode("ascii"),
        "payload_sha256": hashlib.sha256(encrypted_payload).hexdigest(),
        "sections": included_sections,
        "counts": counts,
    }

    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED) as outer_zf:
        outer_zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        outer_zf.writestr("payload.enc", encrypted_payload)

    try:
        os.chmod(out_file, 0o600)
    except Exception:
        pass

    return manifest


def inspect_settings_archive(archive_path: str | Path) -> dict[str, Any]:
    """Read unencrypted manifest metadata from a settings archive."""
    src = Path(archive_path)
    if not src.exists():
        raise FileNotFoundError(f"Backup archive not found: {src}")

    with zipfile.ZipFile(src, "r") as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names or "payload.enc" not in names:
            raise ValueError("Invalid Admin Suite settings archive (missing manifest or payload).")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError(f"Unsupported backup format: {manifest.get('format')}")
    return manifest


def import_settings_archive(
    archive_path: str | Path,
    password: str,
    services: Any,
    *,
    merge: bool = True,
    restore_secrets: bool = True,
) -> dict[str, Any]:
    """
    Decrypt and restore settings, profiles, and secrets from a backup archive.

    Args:
        archive_path: Path to the encrypted ZIP archive.
        password: Password used when creating the archive.
        services: Active AppServices instance.
        merge: If True, merge dictionaries/lists with existing entries; if False, replace them.
        restore_secrets: Whether to write stored passwords back into SecretStore.
    """
    if not password:
        raise ValueError("Please enter the backup decryption password.")

    src = Path(archive_path)
    with zipfile.ZipFile(src, "r") as outer_zf:
        names = set(outer_zf.namelist())
        if "manifest.json" not in names or "payload.enc" not in names:
            raise ValueError("Invalid Admin Suite settings archive.")
        manifest = json.loads(outer_zf.read("manifest.json").decode("utf-8"))
        encrypted_payload = outer_zf.read("payload.enc")

    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError(f"Unsupported backup format: {manifest.get('format')}")

    expected_sha = manifest.get("payload_sha256")
    if expected_sha and hashlib.sha256(encrypted_payload).hexdigest() != expected_sha:
        raise ValueError("Archive integrity check failed (payload checksum mismatch).")

    try:
        salt = base64.b64decode(manifest["salt"])
        iterations = int(manifest.get("iterations", DEFAULT_KDF_ITERATIONS))
        key = _derive_fernet_key(password, salt, iterations=iterations)
        inner_zip_bytes = Fernet(key).decrypt(encrypted_payload)
    except InvalidToken as exc:
        raise ValueError("Incorrect password or corrupted encrypted archive.") from exc

    paths = _paths_mod(services)
    restored: dict[str, Any] = {"sections": [], "counts": {}}

    with zipfile.ZipFile(io.BytesIO(inner_zip_bytes), "r") as inner_zf:
        inner_names = set(inner_zf.namelist())

        for member in inner_names:
            if ".." in Path(member).parts or Path(member).is_absolute():
                raise ValueError(f"Unsafe path in archive: {member}")

        # 1. Global configuration
        if "config.json" in inner_names:
            incoming_cfg = json.loads(inner_zf.read("config.json").decode("utf-8"))
            if isinstance(incoming_cfg, dict):
                cfg_store = getattr(services, "config", None)
                if cfg_store is not None:
                    base = dict(cfg_store.data) if merge else dict(cfg_store.data)
                    if not merge:
                        cfg_store.data.clear()
                    cfg_store.data.update(incoming_cfg)
                    cfg_store.save()
                else:
                    cfg_path = Path(getattr(paths, "CONFIG_FILE", default_paths.CONFIG_FILE))
                    write_json_secure(cfg_path, incoming_cfg)
                restored["sections"].append("config")
                restored["counts"]["config_keys"] = len(incoming_cfg)

        # 2. Dictionary JSON files (SSH profiles, DB profiles, VPN profiles, last_session)
        for section, attr, filename in _DICT_JSON_ITEMS:
            if section == "config" or filename not in inner_names:
                continue
            incoming = json.loads(inner_zf.read(filename).decode("utf-8"))
            if not isinstance(incoming, dict):
                continue
            dest = Path(getattr(paths, attr, getattr(default_paths, attr)))
            existing = _read_json_file(dest) if merge else {}
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(incoming)
            write_json_secure(dest, merged)
            restored["sections"].append(section)
            restored["counts"][section] = len(incoming)

        # 3. List JSON files (snippets, favorites, history, recent)
        for section, attr, filename in _LIST_JSON_ITEMS:
            if filename not in inner_names:
                continue
            incoming = json.loads(inner_zf.read(filename).decode("utf-8"))
            dest = Path(getattr(paths, attr, getattr(default_paths, attr)))
            if merge and isinstance(incoming, list):
                existing = _read_json_file(dest)
                if isinstance(existing, list):
                    combined = list(existing)
                    for item in incoming:
                        if item not in combined:
                            combined.append(item)
                    incoming = combined
            write_json_secure(dest, incoming)
            restored["sections"].append(section)
            restored["counts"][section] = len(incoming) if isinstance(incoming, (list, dict)) else 1

        # 4. SSH known_hosts
        for section, attr, filename in _TEXT_ITEMS:
            if filename not in inner_names:
                continue
            incoming_txt = inner_zf.read(filename).decode("utf-8", errors="replace")
            dest = Path(getattr(paths, attr, getattr(default_paths, attr)))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if merge and dest.exists():
                existing_lines = [ln for ln in dest.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                seen = set(existing_lines)
                for ln in incoming_txt.splitlines():
                    if ln.strip() and ln not in seen:
                        existing_lines.append(ln)
                        seen.add(ln)
                final_txt = "\n".join(existing_lines) + ("\n" if existing_lines else "")
            else:
                final_txt = incoming_txt
            dest.write_text(final_txt, encoding="utf-8")
            try:
                os.chmod(dest, 0o600)
            except Exception:
                pass
            restored["sections"].append(section)

        # 5. Stored credentials
        if restore_secrets and "secrets.json" in inner_names:
            secrets_map = json.loads(inner_zf.read("secrets.json").decode("utf-8"))
            secrets_store = getattr(services, "secrets", None)
            if isinstance(secrets_map, dict) and secrets_store is not None:
                for skey, sval in secrets_map.items():
                    if sval:
                        secrets_store.set(str(skey), str(sval))
                restored["sections"].append("secrets")
                restored["counts"]["secrets"] = len(secrets_map)

    return restored
