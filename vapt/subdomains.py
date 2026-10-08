"""
Subdomain Reconnaissance & Enumeration Module ("subdomains of this domain").
Combines Certificate Transparency (crt.sh) logs, SSL SAN extraction, and concurrent
DNS dictionary resolution to uncover active subdomains, IP bindings, and HTTP responsiveness.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set


class SubdomainFinder:
    """Discovers and validates subdomains using passive OSINT and DNS resolution."""

    COMMON_SUBDOMAINS = [
        "www", "mail", "api", "admin", "dev", "staging", "app", "portal",
        "vpn", "auth", "login", "test", "cdn", "shop", "status", "beta",
        "docs", "support", "dashboard", "git", "jenkins", "corp", "internal",
        "remote", "server", "webmail", "smtp", "secure", "cpanel", "stage",
        "preview", "monitor", "cloud", "m", "autodiscover", "sso", "id",
    ]

    @classmethod
    def discover(
        cls,
        domain: str,
        ssl_sans: list[str] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        max_workers: int = 15,
    ) -> list[dict[str, Any]]:
        """
        Run subdomain discovery for domain.
        Returns list of dicts: [{subdomain, ip, status_code, source, is_alive}]
        """
        domain = cls._clean_domain(domain)
        candidates: dict[str, set[str]] = {}  # subdomain -> set of sources

        def add_candidate(sub: str, source: str):
            sub_clean = sub.strip().lower().rstrip(".")
            if not sub_clean or "*" in sub_clean:
                return
            # Ensure it is actually a subdomain of target domain
            if sub_clean == domain or sub_clean.endswith(f".{domain}"):
                if sub_clean not in candidates:
                    candidates[sub_clean] = set()
                candidates[sub_clean].add(source)

        # 1. SSL SANs
        if ssl_sans:
            if progress_cb:
                progress_cb("Extracting subdomains from SSL SANs...")
            for san in ssl_sans:
                add_candidate(san, "SSL Certificate SAN")

        # 2. Certificate Transparency via crt.sh
        if progress_cb:
            progress_cb("Querying Certificate Transparency (crt.sh)...")
        crt_names = cls._query_crt_sh(domain)
        for name in crt_names:
            add_candidate(name, "Cert Transparency (crt.sh)")

        # 3. DNS Wordlist candidates
        if progress_cb:
            progress_cb("Adding standard DNS prefix candidates...")
        for prefix in cls.COMMON_SUBDOMAINS:
            add_candidate(f"{prefix}.{domain}", "DNS Dictionary")

        # Also add root domain
        add_candidate(domain, "Root Domain")

        total_candidates = len(candidates)
        if progress_cb:
            progress_cb(f"Resolving {total_candidates} subdomain candidates...")

        # 4. Concurrently resolve IP and check HTTP responsiveness
        results: list[dict[str, Any]] = []

        def check_subdomain(sub: str, sources: set[str]) -> Optional[dict[str, Any]]:
            ip = cls._resolve_ip(sub)
            if not ip:
                # If discovered via CT or SANs, record as inactive / non-resolving
                if "DNS Dictionary" not in sources:
                    return {
                        "subdomain": sub,
                        "ip": "Unresolved",
                        "status_code": "-",
                        "sources": sorted(list(sources)),
                        "is_alive": False,
                    }
                return None

            # Alive / Resolved! Check web port responsiveness
            code = cls._probe_http_status(sub)
            return {
                "subdomain": sub,
                "ip": ip,
                "status_code": code,
                "sources": sorted(list(sources)),
                "is_alive": True,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sub = {
                executor.submit(check_subdomain, sub, sources): sub
                for sub, sources in candidates.items()
            }
            resolved_count = 0
            for future in concurrent.futures.as_completed(future_to_sub):
                resolved_count += 1
                if progress_cb and resolved_count % 5 == 0:
                    progress_cb(f"Resolved {resolved_count}/{total_candidates} subdomains...")
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                except Exception:
                    pass

        # Sort: Alive first, then root domain, then alphabetical
        def sort_key(item: dict[str, Any]):
            is_root = 0 if item["subdomain"] == domain else 1
            alive = 0 if item["is_alive"] else 1
            return (alive, is_root, item["subdomain"])

        results.sort(key=sort_key)
        return results

    @classmethod
    def _clean_domain(cls, domain_or_url: str) -> str:
        s = domain_or_url.strip().lower()
        s = re.sub(r"^https?://", "", s)
        s = s.split("/")[0]
        s = s.split(":")[0]
        return s.rstrip(".")

    @classmethod
    def _query_crt_sh(cls, domain: str) -> list[str]:
        """Fetch certificate log entries from crt.sh API."""
        url = f"https://crt.sh/?q=%.{domain}&output=json"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (AdminSuite-VAPT-Recon/5.0)"},
        )
        names: set[str] = set()
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
                    for entry in data:
                        name_val = entry.get("name_value", "")
                        for line in name_val.split("\n"):
                            clean = line.strip().lower()
                            if clean and not clean.startswith("*"):
                                names.add(clean)
        except Exception:
            pass
        return list(names)

    @classmethod
    def _resolve_ip(cls, hostname: str) -> str:
        """Resolve hostname to IPv4 address."""
        try:
            return socket.gethostbyname(hostname)
        except Exception:
            return ""

    @classmethod
    def _probe_http_status(cls, hostname: str) -> str:
        """Quickly check HTTP or HTTPS status code without downloading body."""
        for scheme in ("https", "http"):
            try:
                url = f"{scheme}://{hostname}"
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)"},
                    method="HEAD",
                )
                with urllib.request.urlopen(req, timeout=3) as response:
                    return f"{response.status} ({scheme.upper()})"
            except urllib.error.HTTPError as ex:
                return f"{ex.code} ({scheme.upper()})"
            except Exception:
                continue
        return "No Web Port"
