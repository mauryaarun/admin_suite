"""
Web Application Exposure & Security Misconfiguration Auditor Module.
Audits robots.txt, security.txt, cookie flags (Secure, HttpOnly, SameSite),
CORS policy, HTTP->HTTPS redirects, and exposed sensitive files.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class WebExposureAuditor:
    """Evaluates web application endpoint exposure, cookies, and CORS configurations."""

    TIMEOUT = 6

    @classmethod
    def audit(cls, base_url: str) -> dict[str, Any]:
        """
        Audit web application endpoints and security posture.
        """
        parsed = urllib.parse.urlparse(base_url)
        scheme = parsed.scheme or "https"
        host = parsed.netloc or parsed.path
        if not host:
            return {"error": "Invalid target URL"}

        root_url = f"{scheme}://{host}"

        result: dict[str, Any] = {
            "root_url": root_url,
            "robots_txt": cls._check_robots_txt(root_url),
            "security_txt": cls._check_security_txt(root_url),
            "sitemap_xml": cls._check_sitemap(root_url),
            "http_redirect": cls._check_http_redirect(host),
            "cors_check": cls._check_cors(root_url),
            "sensitive_files": cls._check_sensitive_endpoints(root_url),
            "findings": [],
        }

        # Consolidate findings
        for f in result["robots_txt"].get("findings", []):
            result["findings"].append(f)
        for f in result["cors_check"].get("findings", []):
            result["findings"].append(f)
        for f in result["sensitive_files"].get("findings", []):
            result["findings"].append(f)
        if not result["http_redirect"].get("redirects_to_https") and scheme == "https":
            result["findings"].append({
                "severity": "MEDIUM",
                "issue": "Insecure HTTP Does Not Redirect to HTTPS",
                "detail": f"http://{host} does not enforce automatic redirection to secure HTTPS.",
                "remediation": "Configure 301 Permanent Redirect on port 80 to redirect all traffic to HTTPS.",
            })

        return result

    @classmethod
    def audit_cookies(cls, set_cookie_headers: list[str], is_https: bool = True) -> dict[str, Any]:
        """
        Audit Set-Cookie headers for missing security flags.
        """
        cookie_records = []
        findings = []

        for raw_cookie in set_cookie_headers:
            parts = [p.strip() for p in raw_cookie.split(";")]
            if not parts:
                continue

            name_val = parts[0].split("=", 1)
            cookie_name = name_val[0]
            cookie_val = name_val[1] if len(name_val) > 1 else ""

            attributes = {p.lower().split("=")[0]: (p.split("=")[1] if "=" in p else True) for p in parts[1:]}

            has_secure = "secure" in attributes
            has_httponly = "httponly" in attributes
            samesite = attributes.get("samesite")

            # Check flaws
            flaws = []
            if is_https and not has_secure:
                flaws.append("Missing 'Secure' flag (transmitted over plaintext)")
                findings.append({
                    "severity": "HIGH",
                    "issue": f"Cookie '{cookie_name}' Missing 'Secure' Flag",
                    "detail": "Cookie can be intercepted over unencrypted HTTP connections.",
                    "remediation": f"Set 'Secure' attribute on cookie '{cookie_name}'.",
                })

            if not has_httponly:
                flaws.append("Missing 'HttpOnly' flag (accessible via JavaScript)")
                findings.append({
                    "severity": "MEDIUM",
                    "issue": f"Cookie '{cookie_name}' Missing 'HttpOnly' Flag",
                    "detail": "Cookie can be stolen via Cross-Site Scripting (XSS) document.cookie access.",
                    "remediation": f"Add 'HttpOnly' attribute to cookie '{cookie_name}'.",
                })

            if not samesite:
                flaws.append("Missing 'SameSite' attribute")
                findings.append({
                    "severity": "LOW",
                    "issue": f"Cookie '{cookie_name}' Missing 'SameSite' Attribute",
                    "detail": "Missing SameSite attribute increases exposure to Cross-Site Request Forgery (CSRF).",
                    "remediation": f"Add 'SameSite=Lax' or 'SameSite=Strict' to cookie '{cookie_name}'.",
                })
            elif str(samesite).lower() == "none" and not has_secure:
                flaws.append("SameSite=None without Secure (rejected by browsers)")

            cookie_records.append({
                "name": cookie_name,
                "has_secure": has_secure,
                "has_httponly": has_httponly,
                "samesite": str(samesite) if samesite else "None",
                "flaws": flaws,
                "status": "PASS" if not flaws else ("WARN" if len(flaws) == 1 else "FAIL"),
            })

        return {
            "cookies": cookie_records,
            "findings": findings,
            "total_cookies": len(cookie_records),
            "vulnerable_cookies": sum(1 for c in cookie_records if c["flaws"]),
        }

    @classmethod
    def _check_robots_txt(cls, root_url: str) -> dict[str, Any]:
        """Fetch robots.txt and look for interesting disallowed paths."""
        url = f"{root_url}/robots.txt"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)"})
        disallowed = []
        sensitive_disallowed = []
        findings = []

        try:
            with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
                if resp.status == 200:
                    content = resp.read().decode("utf-8", errors="replace")
                    for line in content.splitlines():
                        line_str = line.strip()
                        if line_str.lower().startswith("disallow:"):
                            parts = line_str.split(":", 1)
                            if len(parts) > 1:
                                path = parts[1].strip()
                                if path:
                                    disallowed.append(path)
                                    if any(s in path.lower() for s in ["admin", "api", "backup", "secret", "private", "db", "dump", "config", "internal"]):
                                        sensitive_disallowed.append(path)

                    if sensitive_disallowed:
                        findings.append({
                            "severity": "LOW",
                            "issue": "Sensitive Paths Disclosed in robots.txt",
                            "detail": f"robots.txt reveals potential hidden directories: {', '.join(sensitive_disallowed[:5])}",
                            "remediation": "Do not rely on robots.txt for security. Use proper authentication and authorization controls.",
                        })

                    return {
                        "present": True,
                        "disallowed_count": len(disallowed),
                        "disallowed_paths": disallowed[:30],
                        "sensitive_paths": sensitive_disallowed,
                        "findings": findings,
                    }
        except Exception:
            pass

        return {"present": False, "disallowed_count": 0, "disallowed_paths": [], "sensitive_paths": [], "findings": []}

    @classmethod
    def _check_security_txt(cls, root_url: str) -> dict[str, Any]:
        """Check for RFC 9116 security.txt vulnerability disclosure policy."""
        for path in ("/.well-known/security.txt", "/security.txt"):
            url = f"{root_url}{path}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)"})
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        content = resp.read(2048).decode("utf-8", errors="replace")
                        if "contact:" in content.lower():
                            return {"present": True, "path": path, "content_snippet": content[:300]}
            except Exception:
                continue
        return {"present": False, "path": "", "content_snippet": ""}

    @classmethod
    def _check_sitemap(cls, root_url: str) -> dict[str, Any]:
        """Check for sitemap.xml."""
        url = f"{root_url}/sitemap.xml"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)"})
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    return {"present": True, "url": url}
        except Exception:
            pass
        return {"present": False, "url": ""}

    @classmethod
    def _check_http_redirect(cls, host: str) -> dict[str, Any]:
        """Verify if plaintext HTTP upgrades to HTTPS."""
        url = f"http://{host}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)"})
        try:
            # Custom opener to prevent following redirects automatically
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = urllib.request.build_opener(NoRedirect)
            resp = opener.open(req, timeout=4)
            # If 200 returned on plain http without redirect:
            return {"redirects_to_https": False, "status_code": resp.status}
        except urllib.error.HTTPError as ex:
            if ex.code in (301, 302, 307, 308):
                location = ex.headers.get("Location", "")
                if location.lower().startswith("https://"):
                    return {"redirects_to_https": True, "status_code": ex.code, "location": location}
            return {"redirects_to_https": False, "status_code": ex.code}
        except Exception:
            return {"redirects_to_https": False, "status_code": "Timeout/Error"}

    @classmethod
    def _check_cors(cls, root_url: str) -> dict[str, Any]:
        """Probe CORS policy with an arbitrary untrusted Origin."""
        req = urllib.request.Request(
            root_url,
            headers={
                "User-Agent": "Mozilla/5.0 (AdminSuite-Audit/5.0)",
                "Origin": "https://untrusted-attacker.example.com",
            },
        )
        findings = []
        cors_headers = {}
        try:
            with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
                for k, v in resp.headers.items():
                    if k.lower().startswith("access-control-"):
                        cors_headers[k] = v

                allow_origin = resp.headers.get("Access-Control-Allow-Origin", "").strip()
                allow_creds = resp.headers.get("Access-Control-Allow-Credentials", "").strip().lower()

                if allow_origin == "https://untrusted-attacker.example.com" and allow_creds == "true":
                    findings.append({
                        "severity": "CRITICAL",
                        "issue": "Dangerous Reflective CORS with Credentials",
                        "detail": "Application trusts arbitrary attacker Origin and permits credentialed requests (cookies/auth headers).",
                        "remediation": "Do not dynamically reflect Origin headers when credentials mode is enabled. Validate against strict whitelist.",
                    })
                elif allow_origin == "*":
                    # Wildcard is fine for public APIs, but noted
                    cors_headers["note"] = "Wildcard '*' allowed (public resource)"

        except Exception:
            pass

        return {"headers": cors_headers, "findings": findings}

    @classmethod
    def _check_sensitive_endpoints(cls, root_url: str) -> dict[str, Any]:
        """
        Passive non-destructive probe for common critical configuration leakages.
        Tests: .git/HEAD, .env, phpinfo.php, server-status.
        """
        probes = [
            ("/.git/HEAD", "ref: refs/", "CRITICAL", "Exposed .git Repository", "Source code and git commit history publicly exposed."),
            ("/.env", "DB_", "CRITICAL", "Exposed .env Configuration File", "Environment file potentially disclosing database/API credentials."),
            ("/phpinfo.php", "PHP Version", "HIGH", "Exposed phpinfo() Diagnostic Page", "Full PHP environment, extensions, and server paths disclosed."),
            ("/server-status", "Apache Server Status", "MEDIUM", "Exposed Apache server-status", "Server traffic, connected IPs, and client requests disclosed."),
        ]

        tested_items = []
        findings = []

        for path, signature, severity, issue_name, issue_desc in probes:
            target_url = f"{root_url}{path}"
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "Mozilla/5.0 (AdminSuite-SecurityProbe/5.0)"},
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        body_snip = resp.read(512).decode("utf-8", errors="replace")
                        if signature in body_snip:
                            findings.append({
                                "severity": severity,
                                "issue": issue_name,
                                "detail": f"Endpoint '{path}' returned 200 OK and matched signature: {issue_desc}",
                                "remediation": f"Immediately restrict access or delete '{path}' on the web server.",
                            })
                            tested_items.append({"path": path, "status": "EXPOSED (200 OK)", "severity": severity})
                            continue
                    tested_items.append({"path": path, "status": f"Safe ({resp.status})", "severity": "NONE"})
            except urllib.error.HTTPError as ex:
                tested_items.append({"path": path, "status": f"Protected ({ex.code})", "severity": "NONE"})
            except Exception:
                tested_items.append({"path": path, "status": "Not Reachable", "severity": "NONE"})

        return {"items": tested_items, "findings": findings}
