"""
HTTP Security Headers Auditor Module.
Deeply inspects HTTP response headers, grades compliance with OWASP guidelines,
and provides precise Nginx/Apache hardening remediation snippets.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class SecurityHeadersAuditor:
    """OWASP HTTP Security Headers compliance auditor."""

    HEADER_SPECS = [
        {
            "name": "Strict-Transport-Security",
            "weight": 25,
            "required": True,
            "desc": "Enforces secure HTTPS connections and prevents SSL stripping attacks.",
            "recommended": "max-age=31536000; includeSubDomains; preload",
        },
        {
            "name": "Content-Security-Policy",
            "weight": 25,
            "required": True,
            "desc": "Restricts sources of executable scripts, stylesheets, and resources to mitigate XSS and injection attacks.",
            "recommended": "default-src 'self'; script-src 'self'; object-src 'none';",
        },
        {
            "name": "X-Frame-Options",
            "weight": 15,
            "required": True,
            "desc": "Prevents Clickjacking by controlling whether the site can be embedded in an <iframe>.",
            "recommended": "DENY or SAMEORIGIN",
        },
        {
            "name": "X-Content-Type-Options",
            "weight": 10,
            "required": True,
            "desc": "Prevents MIME-type confusion attacks and forced executable script interpretation.",
            "recommended": "nosniff",
        },
        {
            "name": "Referrer-Policy",
            "weight": 10,
            "required": True,
            "desc": "Controls amount of referrer information sent in request headers to external destinations.",
            "recommended": "strict-origin-when-cross-origin",
        },
        {
            "name": "Permissions-Policy",
            "weight": 8,
            "required": False,
            "desc": "Restricts browser features like camera, microphone, geolocation, and USB.",
            "recommended": "camera=(), microphone=(), geolocation=()",
        },
        {
            "name": "Cross-Origin-Opener-Policy",
            "weight": 4,
            "required": False,
            "desc": "Isolates browsing context to protect against cross-origin attacks (Spectre).",
            "recommended": "same-origin",
        },
        {
            "name": "Cross-Origin-Resource-Policy",
            "weight": 3,
            "required": False,
            "desc": "Prevents other origins from reading the resource.",
            "recommended": "same-origin",
        },
    ]

    LEAK_HEADERS = [
        ("Server", "Web server software and version disclosure."),
        ("X-Powered-By", "Backend technology and language runtime disclosure."),
        ("X-AspNet-Version", "ASP.NET framework version disclosure."),
        ("X-Generator", "CMS or static site generator disclosure."),
        ("X-Runtime", "Application response timing disclosure."),
    ]

    @classmethod
    def audit(cls, headers: dict[str, str], is_https: bool = True) -> dict[str, Any]:
        """
        Evaluate headers dictionary (case-insensitive).
        Returns scores, letter grade, itemized findings, and remediation configs.
        """
        # Normalize header keys to lowercase
        norm_headers = {k.lower(): v.strip() for k, v in headers.items()}

        raw_score = 0
        max_score = sum(spec["weight"] for spec in cls.HEADER_SPECS)
        items: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []

        # 1. Evaluate standard security headers
        for spec in cls.HEADER_SPECS:
            hname = spec["name"]
            key = hname.lower()
            val = norm_headers.get(key)
            weight = spec["weight"]

            if not val:
                status = "FAIL"
                earned = 0
                detail = f"Header '{hname}' is missing."
                findings.append({
                    "severity": "HIGH" if weight >= 15 else "MEDIUM",
                    "issue": f"Missing {hname}",
                    "detail": detail,
                    "remediation": f"Configure '{hname}: {spec['recommended']}' on your web server or reverse proxy.",
                })
            else:
                status, earned, detail = cls._evaluate_header_value(hname, val, weight, is_https)
                if status == "WARN":
                    findings.append({
                        "severity": "LOW",
                        "issue": f"Weak or Suboptimal {hname}",
                        "detail": detail,
                        "remediation": f"Improve '{hname}' configuration: {spec['recommended']}",
                    })
                elif status == "FAIL":
                    findings.append({
                        "severity": "MEDIUM",
                        "issue": f"Invalid or Dangerous {hname}",
                        "detail": detail,
                        "remediation": f"Replace '{hname}' value with recommended: {spec['recommended']}",
                    })

            raw_score += earned
            items.append({
                "header": hname,
                "status": status,
                "value": val or "Not Configured",
                "earned": earned,
                "weight": weight,
                "recommended": spec["recommended"],
                "detail": detail,
            })

        # 2. Check Information Leakage Headers
        leaks: list[dict[str, Any]] = []
        for leak_name, leak_desc in cls.LEAK_HEADERS:
            leak_key = leak_name.lower()
            val = norm_headers.get(leak_key)
            if val:
                # Deduct points if version or detailed info is exposed
                penalty = 5
                has_version = bool(re.search(r"[0-9]+\.[0-9]+", val))
                if has_version:
                    penalty = 10
                    findings.append({
                        "severity": "LOW",
                        "issue": f"Version Disclosure in '{leak_name}' ({val})",
                        "detail": f"{leak_desc} Attackers can use this to target version-specific CVEs.",
                        "remediation": f"Remove the '{leak_name}' header or set server_tokens off in Nginx / ServerSignature Off in Apache.",
                    })
                raw_score = max(0, raw_score - penalty)
                leaks.append({
                    "header": leak_name,
                    "value": val,
                    "has_version": has_version,
                    "description": leak_desc,
                })

        # Score percentage
        score_pct = int(round((raw_score / max_score) * 100))
        score_pct = max(0, min(100, score_pct))

        # Letter Grade
        if score_pct >= 95:
            grade = "A+"
        elif score_pct >= 85:
            grade = "A"
        elif score_pct >= 70:
            grade = "B"
        elif score_pct >= 55:
            grade = "C"
        elif score_pct >= 40:
            grade = "D"
        else:
            grade = "F"

        # Remediation configs
        nginx_snippet = cls._generate_nginx_config(items, leaks)
        apache_snippet = cls._generate_apache_config(items, leaks)

        return {
            "score": score_pct,
            "grade": grade,
            "items": items,
            "leaks": leaks,
            "findings": findings,
            "nginx_config": nginx_snippet,
            "apache_config": apache_snippet,
        }

    @classmethod
    def _evaluate_header_value(cls, name: str, val: str, weight: int, is_https: bool) -> tuple[str, int, str]:
        val_lower = val.lower()

        if name == "Strict-Transport-Security":
            if not is_https:
                return "WARN", int(weight * 0.5), "HSTS served over plaintext HTTP (ignored by browsers)."
            m = re.search(r"max-age=([0-9]+)", val_lower)
            if not m:
                return "FAIL", 0, "Missing max-age directive."
            age = int(m.group(1))
            if age < 10886400:  # < 18 weeks
                return "WARN", int(weight * 0.6), f"max-age ({age}s) is shorter than recommended (minimum 10886400s / 18 weeks)."
            has_sub = "includesubdomains" in val_lower
            has_preload = "preload" in val_lower
            if has_sub and has_preload and age >= 31536000:
                return "PASS", weight, "Excellent HSTS: 1+ year max-age with includeSubDomains and preload."
            if has_sub:
                return "PASS", weight, "Strong HSTS with includeSubDomains."
            return "PASS", int(weight * 0.85), "Valid HSTS (consider adding includeSubDomains)."

        if name == "Content-Security-Policy":
            flaws = []
            if "'unsafe-inline'" in val_lower:
                flaws.append("Allows 'unsafe-inline' script execution")
            if "'unsafe-eval'" in val_lower:
                flaws.append("Allows 'unsafe-eval' execution")
            if "http:" in val_lower:
                flaws.append("Allows insecure http: resources")
            if "default-src *" in val_lower:
                flaws.append("Wildcard '*' in default-src")

            if flaws:
                return "WARN", int(weight * 0.6), "CSP configured but weakened: " + ", ".join(flaws)
            return "PASS", weight, "Robust Content-Security-Policy in place."

        if name == "X-Frame-Options":
            if val_lower in ("deny", "sameorigin"):
                return "PASS", weight, f"Protected against Clickjacking ({val.upper()})."
            if "allow-from" in val_lower:
                return "WARN", int(weight * 0.5), "ALLOW-FROM is deprecated in modern browsers; use CSP frame-ancestors."
            return "FAIL", 0, f"Unrecognized value: {val}"

        if name == "X-Content-Type-Options":
            if val_lower == "nosniff":
                return "PASS", weight, "MIME type sniffing disabled (nosniff)."
            return "FAIL", 0, f"Expected 'nosniff', got '{val}'."

        if name == "Referrer-Policy":
            if any(good in val_lower for good in ("strict-origin-when-cross-origin", "no-referrer", "same-origin", "strict-origin")):
                return "PASS", weight, f"Secure Referrer-Policy ({val})."
            if "unsafe-url" in val_lower:
                return "FAIL", 0, "Dangerous 'unsafe-url' leaks full URLs including query params to external origins."
            return "WARN", int(weight * 0.7), f"Acceptable policy ({val})."

        if name == "Permissions-Policy":
            return "PASS", weight, "Permissions-Policy restricts sensitive hardware APIs."

        if name == "Cross-Origin-Opener-Policy":
            if "same-origin" in val_lower:
                return "PASS", weight, f"Process isolation enabled ({val})."
            return "PASS", int(weight * 0.8), f"Configured ({val})."

        if name == "Cross-Origin-Resource-Policy":
            return "PASS", weight, f"CORP configured ({val})."

        return "PASS", weight, "Configured."

    @classmethod
    def _generate_nginx_config(cls, items: list[dict[str, Any]], leaks: list[dict[str, Any]]) -> str:
        lines = ["# Nginx Hardening Configuration (Place inside 'server' block)", ""]
        if leaks:
            lines.append("server_tokens off;  # Hide Nginx version banner")
            lines.append("more_clear_headers 'Server' 'X-Powered-By';  # If headers-more module is active")
            lines.append("")

        for item in items:
            if item["status"] != "PASS":
                h = item["header"]
                rec = item["recommended"]
                if h == "X-Frame-Options":
                    rec = "SAMEORIGIN"
                lines.append(f"add_header {h} \"{rec}\" always;")

        return "\n".join(lines)

    @classmethod
    def _generate_apache_config(cls, items: list[dict[str, Any]], leaks: list[dict[str, Any]]) -> str:
        lines = ["# Apache Hardening Configuration (Place inside VirtualHost or .htaccess)", ""]
        if leaks:
            lines.append("ServerSignature Off")
            lines.append("ServerTokens Prod")
            lines.append("Header unset X-Powered-By")
            lines.append("")

        for item in items:
            if item["status"] != "PASS":
                h = item["header"]
                rec = item["recommended"]
                if h == "X-Frame-Options":
                    rec = "SAMEORIGIN"
                lines.append(f"Header always set {h} \"{rec}\"")

        return "\n".join(lines)
