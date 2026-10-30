"""Kitty 0.45+ global watcher: start the registry once on kitty startup.

Loaded by: watcher /absolute/path/to/kitty/autostart.py
All socket I/O and daemon startup happen in an external worker, never the GUI.
"""

from pathlib import Path
import subprocess


def on_load(boss, data):
    from kitty.constants import kitty_exe
    from kitty.fast_data_types import get_options

    root = str(Path(__file__).resolve().parent.parent)
    endpoint = getattr(boss, "listening_on", "") or None
    executable = kitty_exe()
    configured = getattr(get_options(), "env", {}) or {}
    web_config = {
        key: configured[key]
        for key in (
            "PI_KITTY_WEB_TOKEN_FILE",
            "PI_KITTY_WEB_SERVER",
            "PI_KITTY_WEB_NAME",
        )
        if isinstance(configured.get(key), str)
    }
    code = (
        f"import sys; sys.path.insert(0, {root!r}); "
        "from pi_kitty.startup import ensure, ensure_web_client; "
        f"ensure(endpoint={endpoint!r}, command={executable!r}); "
        f"ensure_web_client({web_config!r})"
    )
    subprocess.Popen(
        [executable, "+runpy", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
