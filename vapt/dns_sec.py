"""
DNS Records & Email Security (SPF, DMARC, DNSSEC) Auditor Module.
Queries authoritative DNS records and evaluates anti-spoofing protections.
"""

from __future__ import annotations

import re
import socket
import subprocess
from typing import Any, Dict, List, Optional


class DnsAuditor:
    """DNS record resolver and email authentication posture auditor."""

    RECORD_TYPES = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "CAA"]

    @classmethod
    def audit(cls, domain: str) -> dict[str, Any]:
        """
        Query DNS records and evaluate email authentication & DNS security.
        """
        domain = cls._clean_domain(domain)
        records: dict[str, list[str]] = {rt: [] for rt in cls.RECORD_TYPES}

        # Try dnspython first if available
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = 3.0
            resolver.lifetime = 4.0

            for rtype in cls.RECORD_TYPES:
                try:
                    answers = resolver.resolve(domain, rtype)
                    records[rtype] = [str(r.to_text()).strip('"') for r in answers]
                except Exception:
                    pass
        except Exception:
            # Fallback to system dig or socket
            records = cls._fallback_query(domain)

        # Basic socket resolution if A records empty
        if not records.get("A"):
            try:
                ip = socket.gethostbyname(domain)
                if ip:
                    records["A"] = [ip]
            except Exception:
                pass

        # Email Security Evaluation
        spf_analysis = cls._analyze_spf(records.get("TXT", []))
        dmarc_analysis = cls._analyze_dmarc(domain)

        # Security Risk Assessment
        risks = []
        if not spf_analysis.get("present"):
            risks.append({
                "severity": "HIGH",
                "issue": "Missing SPF Record",
                "detail": f"Domain {domain} does not publish an SPF record. Anyone can spoof emails claiming to be from this domain.",
                "remediation": "Publish a TXT record with 'v=spf1 include:... ~all' or '-all'.",
            })
        elif spf_analysis.get("policy") == "+all":
            risks.append({
                "severity": "CRITICAL",
                "issue": "Dangerous SPF '+all' Policy",
                "detail": "SPF policy permits all servers on the internet to send email on behalf of your domain.",
                "remediation": "Change '+all' to '~all' or '-all' immediately.",
            })
        elif spf_analysis.get("policy") == "?all":
            risks.append({
                "severity": "MEDIUM",
                "issue": "Weak SPF Neutral '?all' Policy",
                "detail": "SPF explicitly states neutral policy, offering zero protection against spoofing.",
                "remediation": "Change '?all' to '~all' or '-all'.",
            })

        if not dmarc_analysis.get("present"):
            risks.append({
                "severity": "HIGH",
                "issue": "Missing DMARC Policy",
                "detail": f"No DMARC record found at _dmarc.{domain}. Receiving mail servers cannot verify SPF/DKIM alignment.",
                "remediation": "Publish a TXT record at _dmarc." + domain + " with 'v=DMARC1; p=reject; rua=mailto:...'",
            })
        elif dmarc_analysis.get("policy") == "none":
            risks.append({
                "severity": "MEDIUM",
                "issue": "DMARC Policy Set to 'none' (Monitoring Only)",
                "detail": "DMARC policy 'p=none' does not reject or quarantine spoofed emails; spoofed emails will still reach recipient inboxes.",
                "remediation": "Progressively transition DMARC policy from 'p=none' to 'p=quarantine' and ultimately 'p=reject'.",
            })

        if not records.get("CAA"):
            risks.append({
                "severity": "LOW",
                "issue": "No CAA Records Configured",
                "detail": "Certification Authority Authorization (CAA) records restrict which CAs are allowed to issue SSL certificates for this domain.",
                "remediation": "Add CAA records specifying allowed CAs (e.g. '0 issue letsencrypt.org').",
            })

        return {
            "domain": domain,
            "records": records,
            "spf": spf_analysis,
            "dmarc": dmarc_analysis,
            "risks": risks,
            "has_mx": bool(records.get("MX")),
        }

    @classmethod
    def _clean_domain(cls, domain_or_url: str) -> str:
        s = domain_or_url.strip().lower()
        s = re.sub(r"^https?://", "", s)
        s = s.split("/")[0]
        s = s.split(":")[0]
        return s.rstrip(".")

    @classmethod
    def _analyze_spf(cls, txt_records: list[str]) -> dict[str, Any]:
        """Examine TXT records for SPF definition."""
        spf_raw = None
        for txt in txt_records:
            t = txt.strip()
            if t.startswith("v=spf1"):
                spf_raw = t
                break

        if not spf_raw:
            return {"present": False, "raw": "", "policy": None, "status": "Missing"}

        policy = "unknown"
        if "-all" in spf_raw:
            policy = "-all"  # Hard fail (Best)
        elif "~all" in spf_raw:
            policy = "~all"  # Soft fail (Good)
        elif "?all" in spf_raw:
            policy = "?all"  # Neutral (Weak)
        elif "+all" in spf_raw:
            policy = "+all"  # Pass all (Critical flaw)

        return {
            "present": True,
            "raw": spf_raw,
            "policy": policy,
            "status": "Configured",
        }

    @classmethod
    def _analyze_dmarc(cls, domain: str) -> dict[str, Any]:
        """Query _dmarc.<domain> for DMARC policy."""
        dmarc_target = f"_dmarc.{domain}"
        dmarc_raw = None

        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = 3.0
            answers = resolver.resolve(dmarc_target, "TXT")
            for r in answers:
                txt = str(r.to_text()).strip('"')
                if txt.startswith("v=DMARC1"):
                    dmarc_raw = txt
                    break
        except Exception:
            pass

        # CLI dig fallback
        if not dmarc_raw:
            try:
                res = subprocess.run(
                    ["dig", "+short", "TXT", dmarc_target],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for line in res.stdout.splitlines():
                    clean = line.strip().strip('"')
                    if clean.startswith("v=DMARC1"):
                        dmarc_raw = clean
                        break
            except Exception:
                pass

        if not dmarc_raw:
            return {"present": False, "raw": "", "policy": None, "status": "Missing"}

        # Parse policy tag p=
        p_match = re.search(r"\bp=([a-zA-Z]+)", dmarc_raw)
        policy = p_match.group(1).lower() if p_match else "unknown"

        # Parse reporting tag rua=
        rua_match = re.search(r"\brua=([^\s;]+)", dmarc_raw)
        rua = rua_match.group(1) if rua_match else None

        # Parse sp= (subdomain policy)
        sp_match = re.search(r"\bsp=([a-zA-Z]+)", dmarc_raw)
        subdomain_policy = sp_match.group(1).lower() if sp_match else policy

        # Parse pct=
        pct_match = re.search(r"\bpct=([0-9]+)", dmarc_raw)
        percentage = int(pct_match.group(1)) if pct_match else 100

        return {
            "present": True,
            "raw": dmarc_raw,
            "policy": policy,
            "subdomain_policy": subdomain_policy,
            "percentage": percentage,
            "rua": rua,
            "status": f"Policy: {policy.upper()} (pct={percentage}%)",
        }

    @classmethod
    def _fallback_query(cls, domain: str) -> dict[str, list[str]]:
        """Fallback to dig command."""
        records: dict[str, list[str]] = {rt: [] for rt in cls.RECORD_TYPES}
        for rtype in cls.RECORD_TYPES:
            try:
                res = subprocess.run(
                    ["dig", "+short", rtype, domain],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                lines = [l.strip().strip('"') for l in res.stdout.splitlines() if l.strip()]
                records[rtype] = lines
            except Exception:
                pass
        return records
