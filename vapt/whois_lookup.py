"""
WHOIS Domain Lookup & Registration Intelligence Module.
Performs socket-based WHOIS queries with fallback to system whois CLI.
Parses registrar, registration dates, expiration dates, nameservers, and registrant details.
"""

from __future__ import annotations

import re
import socket
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional


class WhoisLookup:
    """Socket-based WHOIS client with robust parsing and CLI fallback."""

    DEFAULT_IANA_SERVER = "whois.iana.org"
    TIMEOUT = 8

    @classmethod
    def query(cls, domain: str) -> dict[str, Any]:
        """
        Query WHOIS information for the given domain.
        Returns parsed dictionary and full raw response.
        """
        # Clean domain
        domain = cls._clean_domain(domain)
        if not domain:
            return {"error": "Invalid domain name", "raw": ""}

        raw_data = ""
        # 1. Try socket direct query
        try:
            raw_data = cls._socket_query(domain)
        except Exception:
            raw_data = ""

        # 2. If socket failed or yielded empty, try CLI whois
        if not raw_data or len(raw_data.strip()) < 50:
            try:
                cli_res = subprocess.run(
                    ["whois", domain],
                    capture_output=True,
                    text=True,
                    timeout=cls.TIMEOUT,
                )
                if cli_res.stdout:
                    raw_data = cli_res.stdout
            except Exception:
                pass

        if not raw_data:
            return {
                "domain": domain,
                "error": "WHOIS lookup timed out or service unavailable",
                "raw": "No WHOIS response received.",
            }

        parsed = cls._parse_whois(raw_data, domain)
        parsed["raw"] = raw_data
        parsed["domain"] = domain
        return parsed

    @classmethod
    def _clean_domain(cls, domain_or_url: str) -> str:
        s = domain_or_url.strip().lower()
        s = re.sub(r"^https?://", "", s)
        s = s.split("/")[0]
        s = s.split(":")[0]
        # Remove trailing dot if present
        s = s.rstrip(".")
        return s

    @classmethod
    def _query_server(cls, server: str, query_str: str) -> str:
        """Send query to a specific WHOIS server over TCP port 43."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(cls.TIMEOUT)
            s.connect((server, 43))
            s.sendall(f"{query_str}\r\n".encode("utf-8"))
            response = b""
            while True:
                data = s.recv(4096)
                if not data:
                    break
                response += data
                if len(response) > 250000:  # limit to 250KB
                    break
        try:
            return response.decode("utf-8", errors="replace")
        except Exception:
            return response.decode("latin-1", errors="replace")

    @classmethod
    def _socket_query(cls, domain: str) -> str:
        """Query IANA to find the authoritative WHOIS server, then query it."""
        # Query IANA first
        iana_resp = cls._query_server(cls.DEFAULT_IANA_SERVER, domain)
        
        # Look for refer / whois server in IANA response
        whois_server = None
        for line in iana_resp.splitlines():
            line_str = line.strip()
            if line_str.lower().startswith("whois:") or line_str.lower().startswith("refer:"):
                parts = line_str.split(":", 1)
                if len(parts) > 1 and parts[1].strip():
                    whois_server = parts[1].strip()
                    break

        if not whois_server:
            # Fallback to common TLD whois servers
            tld = domain.split(".")[-1]
            whois_server = f"whois.nic.{tld}"

        # Query authoritative server
        auth_resp = cls._query_server(whois_server, domain)

        # Check if the registry response points to a registrar WHOIS server
        registrar_server = None
        for line in auth_resp.splitlines():
            line_str = line.strip()
            if re.match(r"^registrar whois server:\s*(.+)$", line_str, re.IGNORECASE):
                match = re.search(r"^registrar whois server:\s*(.+)$", line_str, re.IGNORECASE)
                if match:
                    registrar_server = match.group(1).strip()
                    break

        if registrar_server:
            try:
                reg_resp = cls._query_server(registrar_server, domain)
                if len(reg_resp.strip()) > 50:
                    return f"{auth_resp}\n\n--- REGISTRAR WHOIS DETAILS ---\n\n{reg_resp}"
            except Exception:
                pass

        return auth_resp

    @classmethod
    def _parse_whois(cls, text: str, domain: str) -> dict[str, Any]:
        """Parse common fields from WHOIS response text."""
        result: dict[str, Any] = {
            "registrar": "Unknown",
            "registrar_url": "",
            "registrar_iana_id": "",
            "creation_date": "",
            "expiration_date": "",
            "updated_date": "",
            "days_until_expiry": None,
            "name_servers": [],
            "status": [],
            "registrant_org": "",
            "registrant_country": "",
            "dnssec": "unsigned",
            "is_registered": True,
        }

        # Check if domain is available / unregistered
        not_found_patterns = [
            r"no match for",
            r"not found",
            r"no data found",
            r"domain not found",
            r"status:\s*available",
            r"the queried object does not exist",
        ]
        lower_text = text.lower()
        for pat in not_found_patterns:
            if re.search(pat, lower_text):
                result["is_registered"] = False
                result["status"].append("AVAILABLE / UNREGISTERED")
                break

        # Regex extractors
        def extract(patterns: list[str]) -> str:
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if m:
                    return m.group(1).strip()
            return ""

        result["registrar"] = extract([
            r"^Registrar:\s*(.+)$",
            r"^Registrar Name:\s*(.+)$",
            r"^Sponsoring Registrar:\s*(.+)$",
            r"^registrar-name:\s*(.+)$",
        ]) or "Unknown"

        result["registrar_url"] = extract([
            r"^Registrar URL:\s*(.+)$",
            r"^Registrar Web:\s*(.+)$",
        ])

        result["registrar_iana_id"] = extract([
            r"^Registrar IANA ID:\s*(.+)$",
            r"^Sponsoring Registrar IANA ID:\s*(.+)$",
        ])

        result["creation_date"] = extract([
            r"^Creation Date:\s*(.+)$",
            r"^Created:\s*(.+)$",
            r"^Created on:\s*(.+)$",
            r"^Registration Time:\s*(.+)$",
            r"^created:\s*(.+)$",
        ])

        result["expiration_date"] = extract([
            r"^Registry Expiry Date:\s*(.+)$",
            r"^Registrar Registration Expiration Date:\s*(.+)$",
            r"^Expiration Date:\s*(.+)$",
            r"^Expires on:\s*(.+)$",
            r"^paid-till:\s*(.+)$",
            r"^expires:\s*(.+)$",
        ])

        result["updated_date"] = extract([
            r"^Updated Date:\s*(.+)$",
            r"^Last Updated On:\s*(.+)$",
            r"^Last Updated:\s*(.+)$",
            r"^changed:\s*(.+)$",
        ])

        result["registrant_org"] = extract([
            r"^Registrant Organization:\s*(.+)$",
            r"^Registrant Org:\s*(.+)$",
            r"^registrant-organization:\s*(.+)$",
            r"^org:\s*(.+)$",
        ]) or "Redacted for Privacy / Unknown"

        result["registrant_country"] = extract([
            r"^Registrant Country:\s*(.+)$",
            r"^registrant-country:\s*(.+)$",
            r"^country:\s*(.+)$",
        ]) or "Unknown"

        # DNSSEC
        dnssec = extract([
            r"^DNSSEC:\s*(.+)$",
            r"^dnssec:\s*(.+)$",
        ])
        if dnssec:
            result["dnssec"] = dnssec

        # Name servers
        ns_matches = re.findall(
            r"^(?:Name Server|nserver|nameserver|name-server):\s*([^\s]+)",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        if ns_matches:
            cleaned_ns = sorted(list({ns.strip().lower().rstrip(".") for ns in ns_matches if ns.strip()}))
            result["name_servers"] = cleaned_ns

        # Status
        status_matches = re.findall(
            r"^Domain Status:\s*([^\s]+)",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        if status_matches:
            cleaned_st = sorted(list({st.strip() for st in status_matches if st.strip()}))
            result["status"] = cleaned_st

        # Calculate days until expiry
        if result["expiration_date"]:
            result["days_until_expiry"] = cls._calc_days_left(result["expiration_date"])

        return result

    @classmethod
    def _calc_days_left(cls, date_str: str) -> Optional[int]:
        # Try parsing ISO or common date formats
        cleaned = re.sub(r"([0-9]{4}-[0-9]{2}-[0-9]{2})[T ]([0-9]{2}:[0-9]{2}:[0-9]{2}).*", r"\1 \2", date_str)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        date_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d-%b-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%d.%m.%Y",
        ]
        for fmt in date_formats:
            try:
                dt = datetime.strptime(cleaned[:19], fmt)
                delta = dt - datetime.utcnow()
                return delta.days
            except Exception:
                continue
        return None
