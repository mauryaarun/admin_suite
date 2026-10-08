"""
SSL / TLS Certificate & Cryptographic Security Auditor Module.
Performs socket TLS handshakes, verifies certificate validity, extracts SANs, and checks ciphers.
"""

from __future__ import annotations

import re
import socket
import ssl
from datetime import datetime
from typing import Any, Dict, List, Optional


class SslTlsAuditor:
    """Probes remote endpoint SSL/TLS certificates and protocol configurations."""

    TIMEOUT = 6

    @classmethod
    def audit(cls, domain: str, port: int = 443) -> dict[str, Any]:
        """Audit SSL/TLS certificate of target domain."""
        domain = cls._clean_domain(domain)
        result: dict[str, Any] = {
            "domain": domain,
            "port": port,
            "has_ssl": False,
            "issuer": {},
            "subject": {},
            "valid_from": "",
            "valid_to": "",
            "days_left": None,
            "sans": [],
            "protocol": "",
            "cipher": "",
            "cipher_bits": 0,
            "is_expired": False,
            "is_expiring_soon": False,
            "is_self_signed": False,
            "grade": "F",
            "findings": [],
            "error": None,
        }

        # Attempt TLS connection
        context = ssl.create_default_context()
        try:
            with socket.create_connection((domain, port), timeout=cls.TIMEOUT) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    cipher_info = ssock.cipher()
                    protocol = ssock.version()

                    result["has_ssl"] = True
                    result["protocol"] = protocol or "Unknown"
                    if cipher_info:
                        result["cipher"] = cipher_info[0]
                        result["cipher_bits"] = cipher_info[2]

                    # Parse Cert
                    cls._parse_cert_dict(cert, result)
        except ssl.SSLCertVerificationError as ex:
            # Self-signed or expired or invalid name
            result["has_ssl"] = True
            result["is_self_signed"] = True
            result["findings"].append({
                "severity": "CRITICAL",
                "issue": "SSL Certificate Verification Failed",
                "detail": str(ex),
                "remediation": "Replace self-signed or invalid certificate with a valid certificate from a trusted CA.",
            })
            # Try connecting without verification to read cert details
            cls._probe_unverified(domain, port, result)
        except Exception as ex:
            result["error"] = str(ex)
            result["findings"].append({
                "severity": "CRITICAL",
                "issue": "TLS Connection Failed",
                "detail": f"Could not establish HTTPS/TLS connection on port {port}: {ex}",
                "remediation": "Ensure web server is listening on port 443 with SSL/TLS configured.",
            })
            return result

        # Security evaluation & Grade calculation
        cls._evaluate_posture(result)
        return result

    @classmethod
    def _clean_domain(cls, domain_or_url: str) -> str:
        s = domain_or_url.strip().lower()
        s = re.sub(r"^https?://", "", s)
        s = s.split("/")[0]
        s = s.split(":")[0]
        return s.rstrip(".")

    @classmethod
    def _parse_cert_dict(cls, cert: dict[str, Any], result: dict[str, Any]) -> None:
        if not cert:
            return

        # Subject
        sub_dict = {}
        for rdn in cert.get("subject", ()):
            for key, val in rdn:
                sub_dict[key] = val
        result["subject"] = {
            "commonName": sub_dict.get("commonName", ""),
            "organizationName": sub_dict.get("organizationName", ""),
            "countryName": sub_dict.get("countryName", ""),
        }

        # Issuer
        iss_dict = {}
        for rdn in cert.get("issuer", ()):
            for key, val in rdn:
                iss_dict[key] = val
        result["issuer"] = {
            "commonName": iss_dict.get("commonName", ""),
            "organizationName": iss_dict.get("organizationName", ""),
            "countryName": iss_dict.get("countryName", ""),
        }

        # Check self-signed
        if result["subject"].get("commonName") and result["subject"].get("commonName") == result["issuer"].get("commonName"):
            result["is_self_signed"] = True

        # Valid dates
        not_before = cert.get("notBefore")
        not_after = cert.get("notAfter")
        if not_before:
            result["valid_from"] = not_before
        if not_after:
            result["valid_to"] = not_after
            try:
                # SSL date format: 'May 10 12:00:00 2026 GMT'
                dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                days_left = (dt - datetime.utcnow()).days
                result["days_left"] = days_left
                result["is_expired"] = days_left < 0
                result["is_expiring_soon"] = 0 <= days_left <= 30
            except Exception:
                pass

        # Subject Alternative Names (SANs)
        sans = []
        for typ, val in cert.get("subjectAltName", ()):
            if typ.lower() == "dns":
                sans.append(val)
        result["sans"] = sorted(list(set(sans)))

    @classmethod
    def _probe_unverified(cls, domain: str, port: int, result: dict[str, Any]) -> None:
        """Fetch certificate in unverified mode if standard handshake fails."""
        try:
            ctx = ssl._create_unverified_context()
            with socket.create_connection((domain, port), timeout=cls.TIMEOUT) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert(binary_form=False)
                    if cert:
                        cls._parse_cert_dict(cert, result)
                    if not result.get("protocol"):
                        result["protocol"] = ssock.version() or "TLS"
                    if not result.get("cipher"):
                        c = ssock.cipher()
                        if c:
                            result["cipher"] = c[0]
                            result["cipher_bits"] = c[2]
        except Exception:
            pass

    @classmethod
    def _evaluate_posture(cls, res: dict[str, Any]) -> None:
        """Score SSL and produce findings."""
        if not res["has_ssl"]:
            res["grade"] = "F"
            return

        score = 100
        findings = res["findings"]

        if res["is_self_signed"]:
            score -= 40

        if res.get("is_expired"):
            score -= 50
            findings.append({
                "severity": "CRITICAL",
                "issue": "Certificate Has Expired",
                "detail": f"SSL certificate expired on {res.get('valid_to')}.",
                "remediation": "Renew the certificate immediately (e.g. via certbot or CA dashboard).",
            })
        elif res.get("is_expiring_soon"):
            score -= 15
            days = res.get("days_left", 0)
            findings.append({
                "severity": "HIGH",
                "issue": f"Certificate Expiring Soon ({days} days remaining)",
                "detail": f"Certificate expires on {res.get('valid_to')}. Auto-renewal may have stalled.",
                "remediation": "Trigger automated renewal or schedule manual certificate replacement.",
            })

        proto = res.get("protocol", "")
        if proto == "TLSv1.3":
            # Best
            pass
        elif proto == "TLSv1.2":
            score -= 5
        elif proto in ("TLSv1.0", "TLSv1.1", "SSLv3", "SSLv2"):
            score -= 40
            findings.append({
                "severity": "CRITICAL",
                "issue": f"Deprecated Insecure TLS Version Negotiated ({proto})",
                "detail": "TLS 1.0 and 1.1 are cryptographically broken and prohibited by PCI-DSS.",
                "remediation": "Disable TLS 1.0 and 1.1 in web server configuration; enforce TLS 1.2 and TLS 1.3.",
            })

        bits = res.get("cipher_bits", 0)
        if bits and bits < 128:
            score -= 30
            findings.append({
                "severity": "HIGH",
                "issue": f"Weak Cipher Key Length ({bits} bits)",
                "detail": "Ciphers under 128 bits do not provide adequate modern cryptographic protection.",
                "remediation": "Configure modern cipher suites with AES-128-GCM, AES-256-GCM, or CHACHA20-POLY1305.",
            })

        score = max(0, min(100, score))
        if score >= 90:
            res["grade"] = "A"
        elif score >= 75:
            res["grade"] = "B"
        elif score >= 60:
            res["grade"] = "C"
        elif score >= 40:
            res["grade"] = "D"
        else:
            res["grade"] = "F"
