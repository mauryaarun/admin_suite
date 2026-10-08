"""
VAPT Audit Report Generator Module.
Produces professional HTML, Markdown, JSON, and Text reports of the security audit.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List


class VaptReporter:
    """Generates executive and technical security audit reports in multiple formats."""

    @classmethod
    def generate_html(cls, data: dict[str, Any]) -> str:
        """Generate a self-contained HTML report with responsive CSS dashboard."""
        target_url = html.escape(data.get("target_url", ""))
        domain = html.escape(data.get("domain", ""))
        score = data.get("score", 0)
        grade = data.get("grade", "F")
        duration = data.get("duration", 0)
        timestamp = data.get("timestamp", "")
        counts = data.get("finding_counts", {})
        crit = counts.get("CRITICAL", 0)
        high = counts.get("HIGH", 0)
        med = counts.get("MEDIUM", 0)
        low = counts.get("LOW", 0)

        # Grade color
        grade_colors = {
            "A+": "#00e676", "A": "#00e676",
            "B": "#29b6f6",
            "C": "#ffca28",
            "D": "#ff9800",
            "F": "#f44336",
        }
        g_color = grade_colors.get(grade, "#f44336")

        # Build Tech Stack rows
        tech_rows = ""
        for t in data.get("tech_stack", []):
            name = html.escape(t.get("name", ""))
            cat = html.escape(t.get("category", ""))
            ver = html.escape(t.get("version", "") or "-")
            icon = t.get("icon", "📦")
            tech_rows += f"<tr><td>{icon} <b>{name}</b></td><td>{cat}</td><td><code>{ver}</code></td></tr>\n"
        if not tech_rows:
            tech_rows = "<tr><td colspan='3'>No technologies identified.</td></tr>"

        # Build Findings rows
        findings_rows = ""
        for f in data.get("all_findings", []):
            sev = f.get("severity", "LOW")
            issue = html.escape(f.get("issue", ""))
            detail = html.escape(f.get("detail", ""))
            remed = html.escape(f.get("remediation", ""))

            badge_style = "background:#f44336;color:white;" if sev == "CRITICAL" else (
                "background:#ff5722;color:white;" if sev == "HIGH" else (
                    "background:#ffa000;color:black;" if sev == "MEDIUM" else "background:#0288d1;color:white;"
                )
            )
            findings_rows += f"""
            <tr>
                <td><span class="badge" style="{badge_style}">{sev}</span></td>
                <td><b>{issue}</b><br><small style="color:#aaa;">{detail}</small></td>
                <td><small style="color:#81c784;">{remed}</small></td>
            </tr>
            """
        if not findings_rows:
            findings_rows = "<tr><td colspan='3' style='color:#00e676;'>✓ No security vulnerabilities or misconfigurations flagged!</td></tr>"

        # Build Security Headers rows
        headers_rows = ""
        sec_h = data.get("security_headers", {})
        for item in sec_h.get("items", []):
            h_name = html.escape(item.get("header", ""))
            status = item.get("status", "FAIL")
            val = html.escape(item.get("value", ""))
            rec = html.escape(item.get("recommended", ""))

            s_badge = "background:#00e676;color:black;" if status == "PASS" else ("background:#ffca28;color:black;" if status == "WARN" else "background:#f44336;color:white;")
            headers_rows += f"""
            <tr>
                <td><b>{h_name}</b></td>
                <td><span class="badge" style="{s_badge}">{status}</span></td>
                <td><code>{val[:80]}</code></td>
                <td><small>{rec}</small></td>
            </tr>
            """

        # Subdomains rows
        sub_rows = ""
        subdomains = data.get("subdomains", [])
        for s in subdomains[:50]:  # limit to top 50 in HTML
            sub_name = html.escape(s.get("subdomain", ""))
            ip = html.escape(s.get("ip", ""))
            code = html.escape(s.get("status_code", ""))
            src = html.escape(", ".join(s.get("sources", [])))
            alive_str = "🟢 Alive" if s.get("is_alive") else "⚪ Inactive"
            sub_rows += f"<tr><td><b>{sub_name}</b></td><td>{ip}</td><td>{code}</td><td>{alive_str}</td><td><small>{src}</small></td></tr>\n"
        if not sub_rows:
            sub_rows = "<tr><td colspan='5'>No subdomains uncovered.</td></tr>"

        # WHOIS details
        whois = data.get("whois", {})
        registrar = html.escape(str(whois.get("registrar", "Unknown")))
        created = html.escape(str(whois.get("creation_date", "Unknown")))
        expires = html.escape(str(whois.get("expiration_date", "Unknown")))
        days_left = whois.get("days_until_expiry")
        days_str = f" ({days_left} days left)" if days_left is not None else ""
        ns_list = "<br>".join(html.escape(n) for n in whois.get("name_servers", [])) or "None"

        # SSL details
        ssl_info = data.get("ssl", {})
        ssl_issuer = html.escape(ssl_info.get("issuer", {}).get("commonName", "N/A"))
        ssl_expiry = html.escape(str(ssl_info.get("valid_to", "N/A")))
        ssl_proto = html.escape(str(ssl_info.get("protocol", "N/A")))
        ssl_cipher = html.escape(str(ssl_info.get("cipher", "N/A")))

        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VAPT Security & Domain Audit Report — {domain}</title>
<style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #181b1f; color: #e0e0e0; margin: 0; padding: 24px; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .header {{ background: #22262c; border-radius: 8px; padding: 24px; margin-bottom: 24px; border: 1px solid #333942; display: flex; justify-content: space-between; align-items: center; }}
    .title h1 {{ margin: 0 0 6px 0; font-size: 24px; color: #3daee9; }}
    .title p {{ margin: 0; color: #8892b0; font-size: 14px; }}
    .score-card {{ text-align: center; background: #1b1e23; border: 2px solid {g_color}; border-radius: 8px; padding: 12px 24px; min-width: 140px; }}
    .score-grade {{ font-size: 42px; font-weight: bold; color: {g_color}; line-height: 1; }}
    .score-val {{ font-size: 14px; color: #aaa; margin-top: 4px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
    .kpi-box {{ background: #22262c; border: 1px solid #333942; border-radius: 8px; padding: 16px; text-align: center; }}
    .kpi-box .num {{ font-size: 28px; font-weight: bold; margin-bottom: 4px; }}
    .kpi-box .lbl {{ font-size: 12px; color: #8892b0; text-transform: uppercase; letter-spacing: 0.5px; }}
    .section {{ background: #22262c; border: 1px solid #333942; border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
    .section h2 {{ margin: 0 0 16px 0; font-size: 18px; color: #eff0f1; border-bottom: 1px solid #333942; padding-bottom: 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2d333b; }}
    th {{ background: #1b1e23; color: #3daee9; font-weight: 600; }}
    code {{ background: #1b1e23; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; color: #e6db74; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
    @media (max-width: 768px) {{ .kpi-grid, .grid-2 {{ grid-template-columns: 1fr; }} .header {{ flex-direction: column; text-align: center; }} }}
    @media print {{ body {{ background: #fff; color: #000; }} .header, .section, .kpi-box {{ background: #fff; border-color: #ccc; }} }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="title">
            <h1>🛡️ VAPT & Web Security Audit Report</h1>
            <p>Target: <b>{target_url}</b> · Domain: <b>{domain}</b> · Scanned: {timestamp} ({duration}s)</p>
        </div>
        <div class="score-card">
            <div class="score-grade">{grade}</div>
            <div class="score-val">Score: {score} / 100</div>
        </div>
    </div>

    <div class="kpi-grid">
        <div class="kpi-box"><div class="num" style="color:#f44336;">{crit}</div><div class="lbl">Critical Issues</div></div>
        <div class="kpi-box"><div class="num" style="color:#ff5722;">{high}</div><div class="lbl">High Issues</div></div>
        <div class="kpi-box"><div class="num" style="color:#ffa000;">{med}</div><div class="lbl">Medium / Warnings</div></div>
        <div class="kpi-box"><div class="num" style="color:#00e676;">{len(subdomains)}</div><div class="lbl">Subdomains Found</div></div>
    </div>

    <div class="section">
        <h2>⚠️ Identified Vulnerabilities & Security Risks</h2>
        <table>
            <thead><tr><th style="width:100px;">Severity</th><th>Issue & Details</th><th>Remediation Advice</th></tr></thead>
            <tbody>{findings_rows}</tbody>
        </table>
    </div>

    <div class="grid-2">
        <div class="section">
            <h2>💻 Detected Tech Stack & Applications</h2>
            <table>
                <thead><tr><th>Technology</th><th>Category</th><th>Version</th></tr></thead>
                <tbody>{tech_rows}</tbody>
            </table>
        </div>

        <div class="section">
            <h2>📋 WHOIS & Domain Intelligence</h2>
            <table>
                <tbody>
                    <tr><td><b>Registrar:</b></td><td>{registrar}</td></tr>
                    <tr><td><b>Registered On:</b></td><td>{created}</td></tr>
                    <tr><td><b>Expires On:</b></td><td>{expires}{days_str}</td></tr>
                    <tr><td><b>Name Servers:</b></td><td>{ns_list}</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="grid-2">
        <div class="section">
            <h2>🔒 SSL / TLS Certificate Posture</h2>
            <table>
                <tbody>
                    <tr><td><b>Issuer CA:</b></td><td>{ssl_issuer}</td></tr>
                    <tr><td><b>Valid Until:</b></td><td>{ssl_expiry}</td></tr>
                    <tr><td><b>TLS Protocol:</b></td><td><code>{ssl_proto}</code></td></tr>
                    <tr><td><b>Cipher Suite:</b></td><td><code>{ssl_cipher}</code></td></tr>
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>📧 Email Security (SPF & DMARC)</h2>
            <table>
                <tbody>
                    <tr><td><b>SPF Status:</b></td><td><code>{html.escape(data.get("dns", {}).get("spf", {}).get("status", "Unknown"))}</code></td></tr>
                    <tr><td><b>SPF Policy:</b></td><td><code>{html.escape(str(data.get("dns", {}).get("spf", {}).get("policy", "None")))}</code></td></tr>
                    <tr><td><b>DMARC Status:</b></td><td><code>{html.escape(data.get("dns", {}).get("dmarc", {}).get("status", "Unknown"))}</code></td></tr>
                    <tr><td><b>DMARC Policy:</b></td><td><code>{html.escape(str(data.get("dns", {}).get("dmarc", {}).get("policy", "None")))}</code></td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <h2>🛡️ HTTP Security Headers Audit</h2>
        <table>
            <thead><tr><th>Header Name</th><th>Status</th><th>Detected Value</th><th>Recommended Best Practice</th></tr></thead>
            <tbody>{headers_rows}</tbody>
        </table>
    </div>

    <div class="section">
        <h2>🔎 Discovered Subdomains Inventory ({len(subdomains)})</h2>
        <table>
            <thead><tr><th>Subdomain</th><th>Resolved IP</th><th>HTTP Status</th><th>Liveness</th><th>Discovery Source</th></tr></thead>
            <tbody>{sub_rows}</tbody>
        </table>
    </div>

    <div style="text-align:center; color:#666; font-size:12px; margin-top:20px;">
        Generated by Admin Suite v5 — VAPT & Web Security Auditor Subsystem
    </div>
</div>
</body>
</html>
"""
        return html_out

    @classmethod
    def generate_markdown(cls, data: dict[str, Any]) -> str:
        """Generate a GitHub Flavored Markdown report."""
        target_url = data.get("target_url", "")
        domain = data.get("domain", "")
        score = data.get("score", 0)
        grade = data.get("grade", "F")
        duration = data.get("duration", 0)
        timestamp = data.get("timestamp", "")
        counts = data.get("finding_counts", {})

        lines = [
            f"# 🛡️ VAPT & Web Security Audit Report: {domain}",
            f"- **Target URL:** `{target_url}`",
            f"- **Audit Timestamp:** `{timestamp}`",
            f"- **Execution Duration:** `{duration}s`",
            f"- **Security Score:** **{score} / 100 (Grade {grade})**",
            "",
            "## 📊 Executive Summary",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Overall Grade | **{grade}** |",
            f"| Security Score | **{score}/100** |",
            f"| 🔴 Critical Vulnerabilities | **{counts.get('CRITICAL', 0)}** |",
            f"| 🟠 High Severity Issues | **{counts.get('HIGH', 0)}** |",
            f"| 🟡 Medium Warnings | **{counts.get('MEDIUM', 0)}** |",
            f"| 🟢 Subdomains Discovered | **{len(data.get('subdomains', []))}** |",
            "",
            "## ⚠️ Security Findings & Vulnerabilities",
        ]

        findings = data.get("all_findings", [])
        if not findings:
            lines.append("✓ *No security vulnerabilities or misconfigurations flagged.*")
        else:
            lines.append("| Severity | Issue | Remediation |")
            lines.append("|---|---|---|")
            for f in findings:
                sev = f.get("severity", "LOW")
                issue = f.get("issue", "").replace("|", "-")
                remed = f.get("remediation", "").replace("|", "-")
                lines.append(f"| **{sev}** | {issue} | {remed} |")

        lines.extend([
            "",
            "## 💻 Applications & Tech Stack",
            "| Technology | Category | Version |",
            "|---|---|---|",
        ])
        for t in data.get("tech_stack", []):
            name = t.get("name", "")
            cat = t.get("category", "")
            ver = t.get("version", "-") or "-"
            lines.append(f"| {name} | {cat} | `{ver}` |")

        lines.extend([
            "",
            "## 🛡️ HTTP Security Headers",
            "| Header | Status | Current Value |",
            "|---|---|---|",
        ])
        for item in data.get("security_headers", {}).get("items", []):
            lines.append(f"| `{item.get('header')}` | **{item.get('status')}** | `{item.get('value')[:60]}` |")

        lines.extend([
            "",
            "## 🔎 Subdomains Reconnaissance",
            "| Subdomain | IP Address | Status | Alive |",
            "|---|---|---|---|",
        ])
        for s in data.get("subdomains", [])[:40]:
            alive = "Yes" if s.get("is_alive") else "No"
            lines.append(f"| `{s.get('subdomain')}` | {s.get('ip')} | {s.get('status_code')} | {alive} |")

        return "\n".join(lines)

    @classmethod
    def generate_json(cls, data: dict[str, Any]) -> str:
        """Export raw audit structure as JSON."""
        return json.dumps(data, indent=2, default=str)
