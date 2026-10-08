"""
Master VAPT Audit Orchestration Engine.
Coordinates HTTP fetching, WHOIS intelligence, DNS records, SSL/TLS handshake,
Security Headers scoring, Tech Stack fingerprinting, Subdomain discovery, and Exposure assessment.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from admin_suite.vapt.whois_lookup import WhoisLookup
from admin_suite.vapt.dns_sec import DnsAuditor
from admin_suite.vapt.ssl_tls import SslTlsAuditor
from admin_suite.vapt.security_headers import SecurityHeadersAuditor
from admin_suite.vapt.tech_stack import TechStackDetector
from admin_suite.vapt.subdomains import SubdomainFinder
from admin_suite.vapt.web_exposure import WebExposureAuditor


class VaptEngine:
    """Master workflow engine for comprehensive web application and domain security audits."""

    @classmethod
    def run_audit(
        cls,
        target_input: str,
        options: Optional[dict[str, bool]] = None,
        progress_cb: Optional[Callable[[str, int], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """
        Execute full VAPT audit against the target domain or URL.
        progress_cb: callback(message: str, percentage: int)
        is_cancelled: callback() -> bool to abort scan early.
        """
        start_time = time.time()
        options = options or {}
        include_whois = options.get("whois", True)
        include_dns = options.get("dns", True)
        include_ssl = options.get("ssl", True)
        include_headers = options.get("headers", True)
        include_tech = options.get("tech", True)
        include_subdomains = options.get("subdomains", True)
        include_exposure = options.get("exposure", True)

        def emit(msg: str, pct: int):
            if progress_cb:
                progress_cb(msg, pct)

        def check_abort():
            if is_cancelled and is_cancelled():
                raise InterruptedError("Audit cancelled by user.")

        # 1. Parse and normalize target input
        emit("Normalizing target URL & hostname...", 5)
        parsed = cls._parse_target(target_input)
        target_url = parsed["url"]
        domain = parsed["domain"]
        host = parsed["host"]
        port = parsed["port"]
        is_https = parsed["is_https"]

        result: dict[str, Any] = {
            "target_input": target_input,
            "target_url": target_url,
            "domain": domain,
            "host": host,
            "port": port,
            "is_https": is_https,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "http_response": {},
            "whois": {},
            "dns": {},
            "ssl": {},
            "security_headers": {},
            "tech_stack": [],
            "subdomains": [],
            "web_exposure": {},
            "cookies": {},
            "all_findings": [],
            "score": 0,
            "grade": "F",
            "duration": 0.0,
        }

        # 2. HTTP Initial Request
        check_abort()
        emit(f"Connecting to {target_url}...", 10)
        http_data = cls._fetch_target(target_url)
        result["http_response"] = http_data

        headers = http_data.get("headers", {})
        html_body = http_data.get("body", "")
        cookies = http_data.get("cookies", [])

        # 3. WHOIS Domain Intelligence
        if include_whois:
            check_abort()
            emit(f"Querying WHOIS intelligence for {domain}...", 20)
            try:
                result["whois"] = WhoisLookup.query(domain)
            except Exception as ex:
                result["whois"] = {"error": str(ex), "raw": ""}

        # 4. DNS Records & Anti-Spoofing
        if include_dns:
            check_abort()
            emit(f"Resolving DNS & email security (SPF/DMARC) for {domain}...", 35)
            try:
                result["dns"] = DnsAuditor.audit(domain)
                for f in result["dns"].get("risks", []):
                    result["all_findings"].append(f)
            except Exception as ex:
                result["dns"] = {"error": str(ex)}

        # 5. SSL / TLS Certificate Handshake
        ssl_sans = []
        if include_ssl and is_https:
            check_abort()
            emit(f"Performing TLS handshake on {host}:{port}...", 50)
            try:
                result["ssl"] = SslTlsAuditor.audit(host, port)
                ssl_sans = result["ssl"].get("sans", [])
                for f in result["ssl"].get("findings", []):
                    result["all_findings"].append(f)
            except Exception as ex:
                result["ssl"] = {"error": str(ex), "has_ssl": False}

        # 6. HTTP Security Headers
        if include_headers:
            check_abort()
            emit("Auditing HTTP security headers...", 65)
            try:
                result["security_headers"] = SecurityHeadersAuditor.audit(headers, is_https=is_https)
                for f in result["security_headers"].get("findings", []):
                    result["all_findings"].append(f)
            except Exception as ex:
                result["security_headers"] = {"error": str(ex), "score": 0, "grade": "F"}

        # 7. Application & Tech Stack Fingerprinting
        if include_tech:
            check_abort()
            emit("Detecting technology stack & frameworks...", 75)
            try:
                result["tech_stack"] = TechStackDetector.detect(headers, html_body, cookies)
            except Exception as ex:
                result["tech_stack"] = []

        # 8. Web Exposure & Cookies
        if include_exposure:
            check_abort()
            emit("Auditing cookies, robots.txt, CORS & exposures...", 85)
            try:
                result["web_exposure"] = WebExposureAuditor.audit(target_url)
                for f in result["web_exposure"].get("findings", []):
                    result["all_findings"].append(f)

                result["cookies"] = WebExposureAuditor.audit_cookies(cookies, is_https=is_https)
                for f in result["cookies"].get("findings", []):
                    result["all_findings"].append(f)
            except Exception as ex:
                result["web_exposure"] = {"error": str(ex)}

        # 9. Subdomain Reconnaissance
        if include_subdomains:
            check_abort()
            emit("Running subdomain enumeration...", 90)
            try:
                def sub_cb(msg: str):
                    emit(msg, 92)
                result["subdomains"] = SubdomainFinder.discover(domain, ssl_sans=ssl_sans, progress_cb=sub_cb)
            except Exception as ex:
                result["subdomains"] = []

        # 10. Posture Scoring & Grade
        emit("Computing overall security score...", 98)
        cls._compute_overall_score(result)

        result["duration"] = round(time.time() - start_time, 2)
        emit(f"Audit completed in {result['duration']}s!", 100)
        return result

    @classmethod
    def _parse_target(cls, raw: str) -> dict[str, Any]:
        """Normalize URL or domain input."""
        cleaned = raw.strip()
        if not cleaned.startswith(("http://", "https://")):
            # Default to https
            url = f"https://{cleaned}"
        else:
            url = cleaned

        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc
        host = netloc.split(":")[0]
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        is_https = parsed.scheme == "https"

        # Extract root domain (e.g. sub.example.com -> example.com)
        parts = host.split(".")
        if len(parts) > 2 and not (parts[-1].isdigit() and parts[0].isdigit()):
            # Handle co.uk, com.au, etc.
            if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "net", "edu") and len(parts[-1]) <= 3:
                domain = ".".join(parts[-3:])
            else:
                domain = ".".join(parts[-2:])
        else:
            domain = host

        return {
            "url": f"{parsed.scheme}://{netloc}{parsed.path or '/'}",
            "domain": domain,
            "host": host,
            "port": port,
            "is_https": is_https,
        }

    @classmethod
    def _fetch_target(cls, url: str) -> dict[str, Any]:
        """Fetch target HTTP response, headers, cookies, and body snippet."""
        t0 = time.time()
        headers: dict[str, str] = {}
        cookies: list[str] = []
        body = ""
        status_code = 0
        final_url = url
        error = None

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.status
                final_url = resp.geturl()
                for k, v in resp.headers.items():
                    headers[k] = v
                cookies = resp.headers.get_all("Set-Cookie") or []
                raw_bytes = resp.read(500000)  # max 500KB
                body = raw_bytes.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as ex:
            status_code = ex.code
            final_url = ex.geturl()
            for k, v in ex.headers.items():
                headers[k] = v
            cookies = ex.headers.get_all("Set-Cookie") or []
            try:
                body = ex.read(100000).decode("utf-8", errors="replace")
            except Exception:
                pass
        except Exception as ex:
            error = str(ex)

        latency_ms = int((time.time() - t0) * 1000)
        return {
            "status_code": status_code,
            "final_url": final_url,
            "headers": headers,
            "cookies": cookies,
            "body": body,
            "latency_ms": latency_ms,
            "error": error,
        }

    @classmethod
    def _compute_overall_score(cls, result: dict[str, Any]) -> None:
        """Compute consolidated posture score and letter grade."""
        score = 100

        # Deductions by finding severity
        severity_penalties = {
            "CRITICAL": 25,
            "HIGH": 15,
            "MEDIUM": 8,
            "LOW": 3,
            "INFO": 0,
        }

        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
        deductions = 0

        for f in result["all_findings"]:
            sev = f.get("severity", "LOW").upper()
            if sev not in counts:
                sev = "LOW"
            counts[sev] += 1
            deductions += severity_penalties[sev]

        # Consider headers score
        sec_headers = result.get("security_headers", {})
        h_score = sec_headers.get("score")
        if h_score is not None:
            # 30% weight to headers score
            headers_deficit = (100 - h_score) * 0.3
            deductions += headers_deficit

        score = max(5, int(100 - (deductions * 0.7)))

        # If HTTP response failed completely:
        if not result["http_response"].get("status_code") and result["http_response"].get("error"):
            score = 0

        result["score"] = score
        result["finding_counts"] = counts

        if score >= 90:
            result["grade"] = "A"
        elif score >= 75:
            result["grade"] = "B"
        elif score >= 60:
            result["grade"] = "C"
        elif score >= 45:
            result["grade"] = "D"
        else:
            result["grade"] = "F"
