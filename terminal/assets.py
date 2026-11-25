"""
xterm.js asset management.

Optimizations compared with the original implementation:

- uses one shared terminal.html file
- avoids writing one HTML file per terminal tab
- keeps pinned asset URLs
- writes assets into the Admin Suite cache directory
"""

from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from admin_suite.core.paths import XTERM_DIR

Logger = Optional[Callable[[str, str], None]]

XTERM_ASSETS = {
    "xterm.min.js": "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js",
    "xterm.css": "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css",
    "xterm-addon-fit.min.js": "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js",
    "xterm-addon-search.min.js": "https://cdn.jsdelivr.net/npm/xterm-addon-search@0.13.0/lib/xterm-addon-search.min.js",
    "xterm-addon-web-links.min.js": "https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.min.js",
}

SHARED_TERMINAL_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="xterm.css"/>
<script src="xterm.min.js"></script>
<script src="xterm-addon-fit.min.js"></script>
<script src="xterm-addon-search.min.js"></script>
<script src="xterm-addon-web-links.min.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
}

#terminal {
    box-sizing: border-box;
    width: 100%;
    height: 100%;
    padding: 4px;
}

#search-bar {
    position: fixed;
    top: 4px;
    right: 4px;
    background: #2d2d2d;
    padding: 4px;
    border-radius: 3px;
    display: none;
    z-index: 100;
    border: 1px solid #555;
}

#search-bar input {
    background: #1e1e1e;
    color: #ddd;
    border: 1px solid #555;
    padding: 2px 6px;
    font-size: 12px;
}

#search-bar button {
    background: #3daee9;
    color: white;
    border: none;
    padding: 2px 6px;
    cursor: pointer;
    font-size: 12px;
    margin-left: 2px;
}
</style>
</head>
<body>
<div id="terminal"></div>

<div id="search-bar">
    <input type="text" id="search-input" placeholder="Search..."/>
    <button id="search-prev">&#8593;</button>
    <button id="search-next">&#8595;</button>
    <button id="search-close">&#10005;</button>
</div>

<script>
var term = new Terminal({
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Noto Sans Mono', 'DejaVu Sans Mono', Consolas, monospace",
    fontSize: 13,
    theme: {},
    cursorBlink: true,
    cursorStyle: 'block',
    allowProposedApi: true,
    scrollback: 10000,
    convertEol: false
});

var fitAddon = new FitAddon.FitAddon();
var searchAddon = new SearchAddon.SearchAddon();
var webLinksAddon = new WebLinksAddon.WebLinksAddon();

term.loadAddon(fitAddon);
term.loadAddon(searchAddon);
term.loadAddon(webLinksAddon);

var termContainer = document.getElementById('terminal');
term.open(termContainer);

var bridge = null;
var _fitScheduled = false;

function fitTerminal() {
    if (_fitScheduled) {
        return;
    }
    _fitScheduled = true;
    requestAnimationFrame(function () {
        _fitScheduled = false;
        try {
            if (!termContainer || termContainer.clientWidth <= 0 || termContainer.clientHeight <= 0) {
                return;
            }
            fitAddon.fit();
            if (bridge && bridge.resize_request) {
                bridge.resize_request(term.cols, term.rows);
            }
        } catch (e) {
            // Ignore transient layout errors during tab creation/teardown.
        }
    });
}

try {
    fitAddon.fit();
} catch (e) {
    // Ignore initial fit errors before layout completes.
}

termContainer.addEventListener('click', function () {
    term.focus();
});

term.focus();

function _handleTabSwitchKeyEvent(e) {
    if (!e || !e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) {
        return false;
    }
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') {
        return false;
    }
    var target = e.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') && !target.classList.contains('xterm-helper-textarea')) {
        return false;
    }
    if (e.type === 'keydown' && !e._adminSuiteTabHandled) {
        e._adminSuiteTabHandled = true;
        e.preventDefault();
        e.stopPropagation();
        if (bridge && bridge.request_tab_switch) {
            bridge.request_tab_switch(e.key === 'ArrowLeft' ? -1 : 1);
        }
    }
    return true;
}

term.attachCustomKeyEventHandler(function (e) {
    if (_handleTabSwitchKeyEvent(e)) {
        return false;
    }
    return true;
});

document.addEventListener('keydown', function (e) {
    _handleTabSwitchKeyEvent(e);
}, true);

new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;

    term.onData(function (data) {
        bridge.send_input(data);
    });

    term.onResize(function (size) {
        bridge.resize_request(size.cols, size.rows);
    });

    bridge.output_ready.connect(function (text) {
        term.write(text);
    });

    bridge.clear_terminal.connect(function () {
        term.clear();
    });

    bridge.set_font_size.connect(function (size) {
        term.options.fontSize = size;
        fitTerminal();
    });

    bridge.set_theme.connect(function (themeJson) {
        try {
            var parsed = JSON.parse(themeJson);
            term.options.theme = parsed;
            if (parsed && parsed.background) {
                document.documentElement.style.backgroundColor = parsed.background;
                document.body.style.backgroundColor = parsed.background;
            }
        } catch (e) {
            // Ignore invalid theme payloads.
        }
    });

    bridge.search_show.connect(function () {
        document.getElementById('search-bar').style.display = 'block';
        document.getElementById('search-input').focus();
    });

    bridge.search_hide.connect(function () {
        document.getElementById('search-bar').style.display = 'none';
    });

    bridge.find_next.connect(function (text) {
        searchAddon.findNext(text);
    });

    bridge.find_prev.connect(function (text) {
        searchAddon.findPrevious(text);
    });

    fitTerminal();
    setTimeout(fitTerminal, 120);
});

document.getElementById('search-input').addEventListener('input', function (e) {
    if (bridge) {
        bridge.find_next(e.target.value);
    }
});

document.getElementById('search-prev').addEventListener('click', function () {
    if (bridge) {
        bridge.find_prev(document.getElementById('search-input').value);
    }
});

document.getElementById('search-next').addEventListener('click', function () {
    if (bridge) {
        bridge.find_next(document.getElementById('search-input').value);
    }
});

document.getElementById('search-close').addEventListener('click', function () {
    document.getElementById('search-bar').style.display = 'none';
});

window.addEventListener('resize', fitTerminal);
if (typeof ResizeObserver !== 'undefined' && termContainer) {
    new ResizeObserver(fitTerminal).observe(termContainer);
}
if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(fitTerminal);
}
</script>
</body>
</html>
"""


def shared_terminal_html_path() -> Path:
    """
    Return path to the shared terminal HTML file.
    """
    return XTERM_DIR / "terminal_shared.html"


def _download_asset(
    url: str,
    destination: Path,
    logger: Logger = None,
) -> None:
    """
    Download one asset.
    """
    if logger:
        logger("terminal", f"Downloading {destination.name}...")

    with urllib.request.urlopen(url, timeout=20) as response:
        with open(destination, "wb") as out:
            shutil.copyfileobj(response, out)

    try:
        os.chmod(destination, 0o600)
    except Exception:
        pass


def ensure_xterm_assets(logger: Logger = None) -> bool:
    """
    Ensure xterm.js assets and shared terminal HTML exist.

    Returns True when assets are available.
    """
    try:
        XTERM_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        if logger:
            logger("terminal", f"Cannot create xterm asset directory: {e}")
        return False

    force_refresh = os.environ.get("ADMIN_SUITE_REFRESH_XTERM", "") == "1"

    for file_name, url in XTERM_ASSETS.items():
        path = XTERM_DIR / file_name

        if path.exists() and not force_refresh:
            continue

        try:
            _download_asset(url, path, logger)
        except Exception as e:
            if logger:
                logger("terminal", f"Download failed for {file_name}: {e}")
            return False

    html_path = shared_terminal_html_path()

    try:
        needs_write = force_refresh or not html_path.exists()
        if not needs_write:
            try:
                needs_write = html_path.read_text(encoding="utf-8") != SHARED_TERMINAL_HTML
            except Exception:
                needs_write = True

        if needs_write:
            html_path.write_text(SHARED_TERMINAL_HTML, encoding="utf-8")

            try:
                os.chmod(html_path, 0o600)
            except Exception:
                pass

    except Exception as e:
        if logger:
            logger("terminal", f"Cannot write shared terminal HTML: {e}")
        return False

    return True
