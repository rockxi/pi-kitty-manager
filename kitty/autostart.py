"""Kitty 0.45+ global watcher: start the registry once on kitty startup.

Loaded by: watcher /absolute/path/to/kitty/autostart.py
All socket I/O and daemon startup happen in an external worker, never the GUI.
"""

from pathlib import Path
import subprocess


def on_load(boss, data):
    from kitty.constants import kitty_exe

    root = str(Path(__file__).resolve().parent.parent)
    endpoint = getattr(boss, "listening_on", "") or None
    executable = kitty_exe()
    code = (
        f"import sys; sys.path.insert(0, {root!r}); "
        f"from pi_kitty.startup import ensure; ensure(endpoint={endpoint!r}, command={executable!r})"
    )
    subprocess.Popen(
        [executable, "+runpy", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
