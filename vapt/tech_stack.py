"""
Technology Stack Fingerprinting & Application Detection Module ("application used").
Analyzes HTTP headers, cookies, scripts, meta tags, and HTML structures to detect
web servers, CMS, backend frameworks, programming languages, and CDNs/WAFs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class TechStackDetector:
    """Detects technologies and frameworks running on the target application."""

    # Category definitions
    CATEGORY_SERVER = "Web Server"
    CATEGORY_CMS = "CMS / Platform"
    CATEGORY_FRAMEWORK = "Web Framework"
    CATEGORY_FRONTEND = "Frontend Library / UI"
    CATEGORY_LANGUAGE = "Programming Language"
    CATEGORY_CDN = "CDN / WAF / Proxy"
    CATEGORY_ANALYTICS = "Analytics & Utilities"

    @classmethod
    def detect(
        cls,
        headers: dict[str, str],
        html_body: str,
        cookies: list[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Detect software components and versions.
        Returns a sorted list of technology dictionaries:
        [{name, category, version, confidence, icon}]
        """
        detected: dict[str, dict[str, Any]] = {}
        cookies = cookies or []

        # Lowercase headers dict
        norm_headers = {k.lower(): v for k, v in headers.items()}
        server_hdr = norm_headers.get("server", "")
        powered_by = norm_headers.get("x-powered-by", "")
        via_hdr = norm_headers.get("via", "")

        def add_tech(name: str, category: str, version: str = "", confidence: str = "High", icon: str = "📦"):
            key = name.lower()
            if key not in detected or (version and not detected[key]["version"]):
                detected[key] = {
                    "name": name,
                    "category": category,
                    "version": version,
                    "confidence": confidence,
                    "icon": icon,
                }

        # 1. CDN & WAF Detection
        if "cloudflare" in server_hdr.lower() or "cf-ray" in norm_headers or "cf-cache-status" in norm_headers:
            add_tech("Cloudflare", cls.CATEGORY_CDN, icon="☁️")
        if "cloudfront" in via_hdr.lower() or "x-amz-cf-id" in norm_headers or "x-amz-cf-pop" in norm_headers:
            add_tech("AWS CloudFront", cls.CATEGORY_CDN, icon="☁️")
        if "fastly" in via_hdr.lower() or "fastly-debug-digest" in norm_headers or "x-fastly-request-id" in norm_headers:
            add_tech("Fastly", cls.CATEGORY_CDN, icon="⚡")
        if "akamai" in server_hdr.lower() or "x-akamai-transformed" in norm_headers:
            add_tech("Akamai", cls.CATEGORY_CDN, icon="🌐")
        if "varnish" in via_hdr.lower() or "x-varnish" in norm_headers:
            add_tech("Varnish Cache", cls.CATEGORY_CDN, icon="⚡")
        if "sucuri" in server_hdr.lower() or "x-sucuri-id" in norm_headers:
            add_tech("Sucuri WAF", cls.CATEGORY_CDN, icon="🛡️")
        if "incapsula" in norm_headers.get("x-iinfo", "") or "x-cdn" in norm_headers:
            add_tech("Imperva Incapsula", cls.CATEGORY_CDN, icon="🛡️")

        # 2. Web Server Detection
        if "nginx" in server_hdr.lower():
            v = cls._extract_version(r"nginx/([0-9.]+)", server_hdr)
            add_tech("Nginx", cls.CATEGORY_SERVER, v, icon="🌐")
        if "apache" in server_hdr.lower():
            v = cls._extract_version(r"apache/([0-9.]+)", server_hdr)
            add_tech("Apache HTTP Server", cls.CATEGORY_SERVER, v, icon="🪶")
        if "litespeed" in server_hdr.lower():
            add_tech("LiteSpeed", cls.CATEGORY_SERVER, icon="⚡")
        if "caddy" in server_hdr.lower():
            add_tech("Caddy", cls.CATEGORY_SERVER, icon="🔒")
        if "microsoft-iis" in server_hdr.lower():
            v = cls._extract_version(r"microsoft-iis/([0-9.]+)", server_hdr)
            add_tech("Microsoft IIS", cls.CATEGORY_SERVER, v, icon="🪟")
        if "openresty" in server_hdr.lower():
            v = cls._extract_version(r"openresty/([0-9.]+)", server_hdr)
            add_tech("OpenResty", cls.CATEGORY_SERVER, v, icon="🌐")
        if "gunicorn" in server_hdr.lower():
            v = cls._extract_version(r"gunicorn/([0-9.]+)", server_hdr)
            add_tech("Gunicorn", cls.CATEGORY_SERVER, v, icon="🦄")
        if "uvicorn" in server_hdr.lower():
            add_tech("Uvicorn", cls.CATEGORY_SERVER, icon="⚡")
        if "envoy" in server_hdr.lower():
            add_tech("Envoy Proxy", cls.CATEGORY_SERVER, icon="🔄")

        # 3. Programming Languages
        if "php" in powered_by.lower() or any("phpsessid" in c.lower() for c in cookies) or ".php" in html_body:
            v = cls._extract_version(r"php/([0-9.]+)", powered_by)
            add_tech("PHP", cls.CATEGORY_LANGUAGE, v, icon="🐘")
        if "python" in powered_by.lower() or "python" in server_hdr.lower() or any("csrftoken" in c.lower() for c in cookies):
            add_tech("Python", cls.CATEGORY_LANGUAGE, icon="🐍")
        if "asp.net" in powered_by.lower() or any("asp.net_sessionid" in c.lower() for c in cookies) or "x-aspnet-version" in norm_headers:
            v = norm_headers.get("x-aspnet-version", "")
            add_tech("ASP.NET / C#", cls.CATEGORY_LANGUAGE, v, icon="🪟")
        if "express" in powered_by.lower() or "next.js" in powered_by.lower() or "node" in powered_by.lower():
            add_tech("Node.js", cls.CATEGORY_LANGUAGE, icon="🟢")
        if "ruby" in powered_by.lower() or "phusion passenger" in server_hdr.lower():
            add_tech("Ruby", cls.CATEGORY_LANGUAGE, icon="💎")
        if "jsessionid" in "".join(cookies).lower():
            add_tech("Java / JVM", cls.CATEGORY_LANGUAGE, icon="☕")

        # 4. Backend Frameworks
        if "express" in powered_by.lower():
            add_tech("Express.js", cls.CATEGORY_FRAMEWORK, icon="🚀")
        if "next.js" in powered_by.lower() or "__NEXT_DATA__" in html_body or "/_next/static/" in html_body:
            add_tech("Next.js", cls.CATEGORY_FRAMEWORK, icon="▲")
        if "__NUXT__" in html_body or "/_nuxt/" in html_body:
            add_tech("Nuxt.js", cls.CATEGORY_FRAMEWORK, icon="💚")
        if any("laravel_session" in c.lower() for c in cookies) or any("XSRF-TOKEN" in c for c in cookies):
            add_tech("Laravel", cls.CATEGORY_FRAMEWORK, icon="🔴")
        if any("csrftoken" in c.lower() for c in cookies) and "csrfmiddlewaretoken" in html_body:
            add_tech("Django", cls.CATEGORY_FRAMEWORK, icon="🟩")
        if any("_session_id" in c.lower() for c in cookies) and "authenticity_token" in html_body:
            add_tech("Ruby on Rails", cls.CATEGORY_FRAMEWORK, icon="🛤️")
        if "x-generator" in norm_headers:
            gen_val = norm_headers["x-generator"]
            add_tech(gen_val, cls.CATEGORY_FRAMEWORK, icon="⚙️")

        # 5. CMS & Platforms (DOM and Meta analysis)
        gen_meta = cls._extract_meta_generator(html_body)
        if gen_meta:
            if "wordpress" in gen_meta.lower():
                v = cls._extract_version(r"wordpress\s*([0-9.]+)", gen_meta)
                add_tech("WordPress", cls.CATEGORY_CMS, v, icon="📝")
            elif "drupal" in gen_meta.lower():
                v = cls._extract_version(r"drupal\s*([0-9.]+)", gen_meta)
                add_tech("Drupal", cls.CATEGORY_CMS, v, icon="💧")
            elif "joomla" in gen_meta.lower():
                v = cls._extract_version(r"joomla!\s*([0-9.]+)", gen_meta)
                add_tech("Joomla", cls.CATEGORY_CMS, v, icon="🇯")
            elif "ghost" in gen_meta.lower():
                add_tech("Ghost", cls.CATEGORY_CMS, icon="👻")
            elif "shopify" in gen_meta.lower():
                add_tech("Shopify", cls.CATEGORY_CMS, icon="🛍️")
            else:
                add_tech(gen_meta, cls.CATEGORY_CMS, icon="📝")

        # HTML heuristic checks for CMS
        if "/wp-content/" in html_body or "/wp-includes/" in html_body or "wp-json" in html_body:
            add_tech("WordPress", cls.CATEGORY_CMS, icon="📝")
        if "drupal.js" in html_body or "Drupal.settings" in html_body:
            add_tech("Drupal", cls.CATEGORY_CMS, icon="💧")
        if "shopify.com" in html_body or "cdn.shopify.com" in html_body:
            add_tech("Shopify", cls.CATEGORY_CMS, icon="🛍️")
        if "wix.com" in html_body or "wixsite" in html_body:
            add_tech("Wix", cls.CATEGORY_CMS, icon="🎨")
        if "squarespace" in html_body:
            add_tech("Squarespace", cls.CATEGORY_CMS, icon="⬛")

        # 6. Frontend Libraries & Frameworks
        if "react" in html_body.lower() or "data-reactroot" in html_body or "_reactRootContainer" in html_body or "__NEXT_DATA__" in html_body:
            add_tech("React", cls.CATEGORY_FRONTEND, icon="⚛️")
        if "data-v-" in html_body or "vue" in html_body.lower() or "__NUXT__" in html_body:
            add_tech("Vue.js", cls.CATEGORY_FRONTEND, icon="💚")
        if "ng-version" in html_body or "angular" in html_body.lower():
            v = cls._extract_version(r"ng-version=\"([0-9.]+)\"", html_body)
            add_tech("Angular", cls.CATEGORY_FRONTEND, v, icon="🅰️")
        if "svelte" in html_body.lower():
            add_tech("Svelte", cls.CATEGORY_FRONTEND, icon="🧡")
        if "jquery" in html_body.lower():
            v = cls._extract_version(r"jquery[.-]([0-9.]+)(?:\.min)?\.js", html_body)
            add_tech("jQuery", cls.CATEGORY_FRONTEND, v, icon="💲")
        if "bootstrap" in html_body.lower():
            v = cls._extract_version(r"bootstrap[.-]([0-9.]+)(?:\.min)?\.(?:css|js)", html_body)
            add_tech("Bootstrap", cls.CATEGORY_FRONTEND, v, icon="🅱️")
        if "tailwind" in html_body.lower() or "tailwindcss" in html_body:
            add_tech("Tailwind CSS", cls.CATEGORY_FRONTEND, icon="🎨")
        if "alpine" in html_body.lower() or "x-data=" in html_body:
            add_tech("Alpine.js", cls.CATEGORY_FRONTEND, icon="🏔️")
        if "htmx" in html_body.lower() or "hx-get=" in html_body or "hx-post=" in html_body:
            add_tech("htmx", cls.CATEGORY_FRONTEND, icon="⚡")

        # 7. Analytics & Utilities
        if "google-analytics.com" in html_body or "gtag" in html_body or "G-" in html_body:
            add_tech("Google Analytics", cls.CATEGORY_ANALYTICS, icon="📊")
        if "googletagmanager.com" in html_body:
            add_tech("Google Tag Manager", cls.CATEGORY_ANALYTICS, icon="🏷️")
        if "sentry" in html_body.lower():
            add_tech("Sentry", cls.CATEGORY_ANALYTICS, icon="🚨")
        if "recaptcha" in html_body.lower():
            add_tech("Google reCAPTCHA", cls.CATEGORY_ANALYTICS, icon="🤖")
        if "turnstile" in html_body.lower():
            add_tech("Cloudflare Turnstile", cls.CATEGORY_ANALYTICS, icon="🛡️")

        return sorted(list(detected.values()), key=lambda x: (x["category"], x["name"]))

    @classmethod
    def _extract_version(cls, pattern: str, text: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @classmethod
    def _extract_meta_generator(cls, html: str) -> str:
        m = re.search(r"<meta\s+name=[\"']generator[\"']\s+content=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
        if not m:
            m = re.search(r"<meta\s+content=[\"']([^\"']+)[\"']\s+name=[\"']generator[\"']", html, re.IGNORECASE)
        return m.group(1).strip() if m else ""
