"""
Web server and hosting administration commands.
Fully compatible with both Debian/Ubuntu (apache2, /etc/apache2, sites-available)
and RedHat/CentOS/Alma/Rocky/Fedora (httpd, /etc/httpd, conf.d, php-fpm, SELinux).
"""

from __future__ import annotations

import shlex
import re


# Command to discover installed and active web technologies
WEB_DISCOVERY_CMD = (
    "echo '=== OS_FAMILY ==='; "
    "if [ -f /etc/os-release ]; then "
    "  grep -E '^(ID|ID_LIKE|VERSION_ID)=' /etc/os-release; "
    "elif [ -f /etc/redhat-release ]; then "
    "  echo 'ID=rhel'; "
    "elif [ -f /etc/debian_version ]; then "
    "  echo 'ID=debian'; "
    "else "
    "  echo 'ID=unknown'; "
    "fi; "
    "echo '=== WEB_SERVICES ==='; "
    "for s in nginx apache2 httpd caddy; do "
    "  st=$(systemctl is-active $s 2>/dev/null || echo 'inactive'); "
    "  en=$(systemctl is-enabled $s 2>/dev/null || echo 'disabled'); "
    "  which $s >/dev/null 2>&1 && inst='installed' || inst='not_installed'; "
    "  echo \"$s|$inst|$st|$en\"; "
    "done; "
    "echo '=== PHP_FPM ==='; "
    "for s in $(systemctl list-unit-files 'php*-fpm.service' 'php-fpm.service' --no-legend 2>/dev/null | awk '{print $1}' | sort -u); do "
    "  st=$(systemctl is-active $s 2>/dev/null || echo 'inactive'); "
    "  echo \"$s|$st\"; "
    "done; "
    "echo '=== PM2 ==='; "
    "which pm2 >/dev/null 2>&1 && echo 'pm2_installed' || echo 'pm2_missing'; "
    "echo '=== CERTBOT ==='; "
    "which certbot >/dev/null 2>&1 && echo 'certbot_installed' || echo 'certbot_missing'"
)

# Command to list Virtual Hosts across Debian & RHEL for Nginx, Apache (httpd/apache2), and Caddy
VHOST_DISCOVERY_CMD = (
    "echo '=== NGINX_AVAILABLE ==='; "
    "for f in /etc/nginx/sites-available/*; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== NGINX_ENABLED ==='; "
    "for f in /etc/nginx/sites-enabled/*; do [ -e \"$f\" ] && basename \"$f\"; done; "
    "echo '=== NGINX_CONFD ==='; "
    "for f in /etc/nginx/conf.d/*.conf; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== NGINX_CONFD_DISABLED ==='; "
    "for f in /etc/nginx/conf.d/*.conf.disabled /etc/nginx/conf.d/*.conf.bak; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_DEBIAN_AVAILABLE ==='; "
    "for f in /etc/apache2/sites-available/*; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_DEBIAN_ENABLED ==='; "
    "for f in /etc/apache2/sites-enabled/*; do [ -e \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_RHEL_CONFD ==='; "
    "for f in /etc/httpd/conf.d/*.conf; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_RHEL_CONFD_DISABLED ==='; "
    "for f in /etc/httpd/conf.d/*.conf.disabled /etc/httpd/conf.d/*.conf.bak; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== CADDY_FILE ==='; "
    "[ -f /etc/caddy/Caddyfile ] && echo 'exists' || echo 'none'"
)

# Command to inspect Let's Encrypt and system SSL certificates
SSL_INSPECTION_CMD = (
    "echo '=== CERTBOT_CERTS ==='; "
    "certbot certificates 2>/dev/null || echo 'none'; "
    "echo '=== RAW_CERT_DATES ==='; "
    "for d in /etc/letsencrypt/live/*; do "
    "  if [ -d \"$d\" ] && [ -f \"$d/cert.pem\" ]; then "
    "    domain=$(basename \"$d\"); "
    "    enddate=$(openssl x509 -in \"$d/cert.pem\" -noout -enddate 2>/dev/null | cut -d= -f2); "
    "    issuer=$(openssl x509 -in \"$d/cert.pem\" -noout -issuer 2>/dev/null | sed 's/issuer= //'); "
    "    echo \"$domain|$enddate|$issuer\"; "
    "  fi; "
    "done; "
    "echo '=== RHEL_CUSTOM_CERTS ==='; "
    "for c in /etc/pki/tls/certs/*.crt; do "
    "  if [ -f \"$c\" ]; then "
    "    cname=$(basename \"$c\"); "
    "    enddate=$(openssl x509 -in \"$c\" -noout -enddate 2>/dev/null | cut -d= -f2); "
    "    issuer=$(openssl x509 -in \"$c\" -noout -issuer 2>/dev/null | sed 's/issuer= //'); "
    "    echo \"$cname|$enddate|$issuer\"; "
    "  fi; "
    "done"
)

# Command to inspect PHP-FPM pools on Debian (/etc/php/*/fpm/pool.d/) and RHEL (/etc/php-fpm.d/ & /etc/php-fpm.conf)
PHP_POOLS_CMD = (
    "for f in /etc/php/*/fpm/pool.d/*.conf /etc/php-fpm.d/*.conf /etc/php-fpm.conf; do "
    "  if [ -f \"$f\" ]; then "
    "    echo \"=== POOL:$f ===\"; "
    "    grep -E '^\\[|^(user|group|listen|pm|pm\\.max_children|pm\\.start_servers|pm\\.min_spare_servers|pm\\.max_spare_servers) *=' \"$f\" 2>/dev/null | grep -v '^;'; "
    "  fi; "
    "done"
)

# Command to inspect Node.js PM2 process list in JSON format
PM2_LIST_CMD = "pm2 jlist 2>/dev/null || echo '[]'"

# Command to inspect SELinux status and Web Hosting booleans (RHEL specific)
SELINUX_WEB_CMD = (
    "echo '=== SELINUX_STATUS ==='; "
    "if which sestatus >/dev/null 2>&1; then "
    "  sestatus; "
    "else "
    "  getenforce 2>/dev/null || echo 'SELinux disabled / not installed'; "
    "fi; "
    "echo '=== SELINUX_BOOLEANS ==='; "
    "getsebool httpd_can_network_connect httpd_can_network_connect_db httpd_unified httpd_read_user_content httpd_enable_homedirs 2>/dev/null || echo 'none'"
)

# Command to fetch recent web server access and error logs across Debian, RHEL, and custom paths
def web_logs_cmd(server: str = "nginx", lines: int = 150, custom_path: str = "") -> str:
    if custom_path:
        clean_path = shlex.quote(custom_path.strip())
        return f"tail -n {lines} {clean_path} 2>/dev/null || echo 'Cannot access custom log file: {clean_path}'"

    if server == "nginx":
        return (
            f"echo '=== ACCESS_LOG ==='; tail -n {lines} /var/log/nginx/access.log 2>/dev/null || echo 'no access.log'; "
            f"echo '=== ERROR_LOG ==='; tail -n {lines} /var/log/nginx/error.log 2>/dev/null || echo 'no error.log'"
        )
    elif server in ("apache2", "httpd"):
        return (
            f"echo '=== ACCESS_LOG ==='; "
            f"tail -n {lines} /var/log/apache2/access.log 2>/dev/null "
            f"|| tail -n {lines} /var/log/httpd/access_log 2>/dev/null "
            f"|| tail -n {lines} /var/log/httpd/access.log 2>/dev/null "
            f"|| echo 'no access log found'; "
            f"echo '=== ERROR_LOG ==='; "
            f"tail -n {lines} /var/log/apache2/error.log 2>/dev/null "
            f"|| tail -n {lines} /var/log/httpd/error_log 2>/dev/null "
            f"|| tail -n {lines} /var/log/httpd/error.log 2>/dev/null "
            f"|| echo 'no error log found'"
        )
    elif server == "caddy":
        return f"echo '=== CADDY_LOG ==='; journalctl -u caddy -n {lines} --no-pager 2>/dev/null || tail -n {lines} /var/log/caddy/access.log 2>/dev/null || echo 'no caddy log'"
    return f"tail -n {lines} /var/log/syslog 2>/dev/null || tail -n {lines} /var/log/messages 2>/dev/null || echo 'no log'"


# Apache VirtualHost Generator
def generate_apache_vhost(
    domain: str,
    aliases: str = "",
    site_type: str = "Static",
    doc_root: str = "/var/www/html",
    proxy_pass: str = "http://127.0.0.1:3000",
    php_socket: str = "unix:/run/php/php8.2-fpm.sock",
    access_log: str = "",
    error_log: str = "",
    sec_headers: bool = True,
) -> str:
    """Generate production Apache <VirtualHost> configuration for Debian or RHEL."""
    alias_line = f"    ServerAlias {aliases}" if aliases else ""
    acc_log = access_log or f"/var/log/apache2/{domain}.access.log combined"
    err_log = error_log or f"/var/log/apache2/{domain}.error.log"

    lines = [
        "<VirtualHost *:80>",
        f"    ServerName {domain}",
    ]
    if alias_line:
        lines.append(alias_line)

    lines.extend([
        f"    DocumentRoot {doc_root}",
        "",
        f"    <Directory {doc_root}>",
        "        Options -Indexes +FollowSymLinks",
        "        AllowOverride All",
        "        Require all granted",
        "    </Directory>",
        "",
    ])

    if sec_headers:
        lines.extend([
            "    # Security headers",
            '    Header always set X-Frame-Options "SAMEORIGIN"',
            '    Header always set X-Content-Type-Options "nosniff"',
            '    Header always set X-XSS-Protection "1; mode=block"',
            '    Header always set Referrer-Policy "strict-origin-when-cross-origin"',
            "",
        ])

    if site_type == "PHP-FPM":
        lines.extend([
            "    # PHP-FPM FastCGI proxy",
            f"    <FilesMatch \\.php$>",
            f"        SetHandler \"proxy:{php_socket}|fcgi://localhost\"",
            "    </FilesMatch>",
            "",
        ])
    elif site_type in ("Reverse Proxy", "Node.js"):
        lines.extend([
            "    # Reverse Proxy settings",
            "    ProxyPreserveHost On",
            f"    ProxyPass / {proxy_pass}/",
            f"    ProxyPassReverse / {proxy_pass}/",
            "",
        ])

    lines.extend([
        f"    ErrorLog {err_log}",
        f"    CustomLog {acc_log}",
        "</VirtualHost>",
    ])
    return "\n".join(lines)


# Caddyfile Generator
def generate_caddy_vhost(
    domain: str,
    aliases: str = "",
    site_type: str = "Static",
    doc_root: str = "/var/www/html",
    proxy_pass: str = "http://127.0.0.1:3000",
) -> str:
    """Generate modern Caddy vhost block."""
    domains = f"{domain} {aliases}".strip()
    lines = [f"{domains} {{"]
    if site_type == "Static":
        lines.extend([
            f"    root * {doc_root}",
            "    file_server",
            "    encode gzip zstd",
        ])
    elif site_type == "PHP-FPM":
        lines.extend([
            f"    root * {doc_root}",
            "    php_fastcgi unix//run/php/php-fpm.sock",
            "    file_server",
            "    encode gzip zstd",
        ])
    elif site_type in ("Reverse Proxy", "Node.js"):
        lines.extend([
            f"    reverse_proxy {proxy_pass}",
            "    encode gzip zstd",
        ])
    lines.append("}")
    return "\n".join(lines)


# Test configuration syntax across Debian and RHEL
def test_all_syntax_cmd() -> str:
    return (
        "echo '=== NGINX SYNTAX ==='; "
        "if which nginx >/dev/null 2>&1; then nginx -t 2>&1 || echo 'nginx test failed'; else echo 'nginx not installed'; fi; "
        "echo; echo '=== APACHE / HTTPD SYNTAX ==='; "
        "if which apachectl >/dev/null 2>&1; then "
        "  apachectl configtest 2>&1 || echo 'apachectl test failed'; "
        "elif which apache2ctl >/dev/null 2>&1; then "
        "  apache2ctl configtest 2>&1 || echo 'apache2ctl test failed'; "
        "elif which httpd >/dev/null 2>&1; then "
        "  httpd -t 2>&1 || echo 'httpd test failed'; "
        "else "
        "  echo 'apache/httpd not installed'; "
        "fi; "
        "echo; echo '=== CADDY SYNTAX ==='; "
        "if which caddy >/dev/null 2>&1 && [ -f /etc/caddy/Caddyfile ]; then "
        "  caddy validate --config /etc/caddy/Caddyfile 2>&1 || echo 'caddy validate failed'; "
        "else "
        "  echo 'caddy not installed / no Caddyfile'; "
        "fi"
    )


# PHP Configuration & OPcache Inspector
def php_config_inspector_cmd() -> str:
    return (
        "echo '=== PHP_VERSION ==='; php -v 2>/dev/null | head -1 || echo 'PHP CLI not installed'; "
        "echo; echo '=== KEY_INI_DIRECTIVES ==='; "
        "php -r 'foreach([\"memory_limit\", \"upload_max_filesize\", \"post_max_size\", \"max_execution_time\", \"display_errors\", \"opcache.enable\", \"date.timezone\"] as $k) { echo sprintf(\"%-24s = %s\\n\", $k, ini_get($k)); }' 2>/dev/null || echo 'Cannot read ini'; "
        "echo; echo '=== LOADED_EXTENSIONS ==='; php -m 2>/dev/null | grep -v '^\\[Module\\]' | tr '\\n' ' ' || echo 'none'; "
        "echo; echo; echo '=== OPCACHE_STATUS ==='; "
        "php -r 'if(function_exists(\"opcache_get_status\")) { $s = opcache_get_status(false); echo \"OPcache Enabled: \" . ($s ? \"Yes\" : \"No\"); if($s) { echo \" | Memory Used: \" . round($s[\"memory_usage\"][\"used_memory\"]/1024/1024, 1) . \"MB / \" . round($s[\"memory_usage\"][\"free_memory\"]/1024/1024, 1) . \"MB\"; } } else { echo \"OPcache CLI unavailable\"; }' 2>/dev/null || echo 'OPcache inactive'"
    )


# Web Server Modules Inspector
def web_modules_inspector_cmd() -> str:
    return (
        "echo '=== APACHE / HTTPD LOADED MODULES ==='; "
        "apachectl -M 2>/dev/null || apache2ctl -M 2>/dev/null || httpd -M 2>/dev/null || echo 'Apache not available'; "
        "echo; echo '=== NGINX COMPILED MODULES & VERSION ==='; "
        "nginx -V 2>&1 || echo 'Nginx not available'"
    )


# HTTP Latency & Network Breakdown Probe
def http_latency_benchmark_cmd(url: str) -> str:
    clean_url = shlex.quote(url.strip())
    fmt = (
        "\\n=== HTTP LATENCY BREAKDOWN (ms) ===\\n"
        "DNS Resolution:       %{time_namelookup} s\\n"
        "TCP Connect:          %{time_connect} s\\n"
        "TLS Handshake:        %{time_appconnect} s\\n"
        "Time to First Byte:   %{time_starttransfer} s\\n"
        "Total Request Time:   %{time_total} s\\n"
        "HTTP Status Code:     %{http_code}\\n"
        "Download Size:        %{size_download} bytes\\n"
        "Download Speed:       %{speed_download} bytes/s\\n"
    )
    return f"curl -k -s -o /dev/null -w {shlex.quote(fmt)} {clean_url} 2>&1 || echo 'curl benchmark failed'"


# Fix permissions for web document root
def web_permissions_fix_cmd(doc_root: str, os_family: str = "auto") -> str:
    clean_root = shlex.quote(doc_root.strip())
    return (
        f"if [ '{os_family}' = 'rhel' ] || [ -d /etc/httpd ]; then "
        f"  chown -R apache:apache {clean_root} 2>/dev/null || chown -R nginx:nginx {clean_root} 2>/dev/null; "
        f"else "
        f"  chown -R www-data:www-data {clean_root} 2>/dev/null; "
        f"fi; "
        f"find {clean_root} -type d -exec chmod 755 {{}} + 2>/dev/null; "
        f"find {clean_root} -type f -exec chmod 644 {{}} + 2>/dev/null; "
        f"echo 'Permissions updated for {clean_root}'"
    )


# Service control commands (start, stop, restart, reload)
def service_control_cmd(service: str, action: str) -> str:
    """Safely builds systemctl command for service management."""
    clean_srv = re.sub(r'[^a-zA-Z0-9_\-\.]', '', service)
    clean_act = re.sub(r'[^a-zA-Z0-9_\-]', '', action)
    return f"systemctl {clean_act} {clean_srv}"


# HTTP endpoint tester
def http_probe_cmd(url: str) -> str:
    clean_url = shlex.quote(url.strip())
    return f"curl -I -sS -k --connect-timeout 5 -m 10 {clean_url} 2>&1 || echo 'curl failed'"


# DNS lookup probe
def dns_probe_cmd(domain: str) -> str:
    clean_dom = re.sub(r'[^a-zA-Z0-9_\-\.]', '', domain.strip())
    return (
        f"echo '== A & AAAA RECORDS =='; getent ahostsv4 {clean_dom} 2>/dev/null | head -5; "
        f"echo; echo '== HOST RESOLUTION =='; host {clean_dom} 2>/dev/null || nslookup {clean_dom} 2>/dev/null || echo 'lookup tool not available'; "
        f"echo; echo '== MX RECORDS =='; host -t MX {clean_dom} 2>/dev/null || echo 'none'; "
        f"echo; echo '== TXT RECORDS =='; host -t TXT {clean_dom} 2>/dev/null || echo 'none'"
    )


# Set SELinux Boolean
def set_selinux_boolean_cmd(boolean_name: str, enable: bool) -> str:
    clean_bool = re.sub(r'[^a-zA-Z0-9_]', '', boolean_name)
    val = "1" if enable else "0"
    return f"setsebool -P {clean_bool} {val}"

