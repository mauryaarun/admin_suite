"""
Web server and hosting administration commands.
Supports Nginx, Apache, Caddy, Certbot/SSL, PHP-FPM, and Node.js/PM2.
"""

from __future__ import annotations

# Command to discover installed and active web technologies
WEB_DISCOVERY_CMD = (
    "echo '=== WEB_SERVICES ==='; "
    "for s in nginx apache2 httpd caddy; do "
    "  st=$(systemctl is-active $s 2>/dev/null || echo 'inactive'); "
    "  en=$(systemctl is-enabled $s 2>/dev/null || echo 'disabled'); "
    "  which $s >/dev/null 2>&1 && inst='installed' || inst='not_installed'; "
    "  echo \"$s|$inst|$st|$en\"; "
    "done; "
    "echo '=== PHP_FPM ==='; "
    "for s in $(systemctl list-unit-files 'php*-fpm.service' --no-legend 2>/dev/null | awk '{print $1}'); do "
    "  st=$(systemctl is-active $s 2>/dev/null || echo 'inactive'); "
    "  echo \"$s|$st\"; "
    "done; "
    "echo '=== PM2 ==='; "
    "which pm2 >/dev/null 2>&1 && echo 'pm2_installed' || echo 'pm2_missing'; "
    "echo '=== CERTBOT ==='; "
    "which certbot >/dev/null 2>&1 && echo 'certbot_installed' || echo 'certbot_missing'"
)

# Command to list Virtual Hosts across Nginx, Apache, and Caddy
VHOST_DISCOVERY_CMD = (
    "echo '=== NGINX_AVAILABLE ==='; "
    "for f in /etc/nginx/sites-available/*; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== NGINX_ENABLED ==='; "
    "for f in /etc/nginx/sites-enabled/*; do [ -e \"$f\" ] && basename \"$f\"; done; "
    "echo '=== NGINX_CONFD ==='; "
    "for f in /etc/nginx/conf.d/*.conf; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_AVAILABLE ==='; "
    "for f in /etc/apache2/sites-available/*; do [ -f \"$f\" ] && basename \"$f\"; done; "
    "echo '=== APACHE_ENABLED ==='; "
    "for f in /etc/apache2/sites-enabled/*; do [ -e \"$f\" ] && basename \"$f\"; done; "
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
    "done"
)

# Command to inspect PHP-FPM pools
PHP_POOLS_CMD = (
    "for f in /etc/php/*/fpm/pool.d/*.conf; do "
    "  if [ -f \"$f\" ]; then "
    "    echo \"=== POOL:$f ===\"; "
    "    grep -E '^\\[|^(user|group|listen|pm|pm\\.max_children|pm\\.start_servers|pm\\.min_spare_servers|pm\\.max_spare_servers) *=' \"$f\" 2>/dev/null | grep -v '^;'; "
    "  fi; "
    "done"
)

# Command to inspect Node.js PM2 process list in JSON format
PM2_LIST_CMD = "pm2 jlist 2>/dev/null || echo '[]'"

# Command to fetch recent web server access and error logs
def web_logs_cmd(server: str = "nginx", lines: int = 150) -> str:
    if server == "nginx":
        return (
            f"echo '=== ACCESS_LOG ==='; tail -n {lines} /var/log/nginx/access.log 2>/dev/null || echo 'no access.log'; "
            f"echo '=== ERROR_LOG ==='; tail -n {lines} /var/log/nginx/error.log 2>/dev/null || echo 'no error.log'"
        )
    elif server in ("apache2", "httpd"):
        return (
            f"echo '=== ACCESS_LOG ==='; tail -n {lines} /var/log/apache2/access.log 2>/dev/null || tail -n {lines} /var/log/httpd/access_log 2>/dev/null || echo 'no access.log'; "
            f"echo '=== ERROR_LOG ==='; tail -n {lines} /var/log/apache2/error.log 2>/dev/null || tail -n {lines} /var/log/httpd/error_log 2>/dev/null || echo 'no error.log'"
        )
    elif server == "caddy":
        return f"echo '=== CADDY_LOG ==='; journalctl -u caddy -n {lines} --no-pager 2>/dev/null || tail -n {lines} /var/log/caddy/access.log 2>/dev/null || echo 'no caddy log'"
    return f"tail -n {lines} /var/log/syslog 2>/dev/null || echo 'no log'"
