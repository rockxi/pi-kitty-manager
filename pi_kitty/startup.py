"""Idempotent background startup, called outside kitty's GUI process."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .common import Client, Error, secure_dir, socket_path


def ensure(endpoint: str | None = None, command: str | None = None) -> dict:
    path = socket_path()
    client = Client(path, timeout=1)

    def health():
        with contextlib.suppress(OSError, Error, ValueError):
            return client.request("health")
        return None

    if not health():
        secure_dir(path.parent)
        logs = path.parent / "logs"
        if (
            sys.platform == "darwin"
            and path == Path.home() / "Library/Caches/pi-kitty/run/daemon.sock"
        ):
            logs = Path.home() / "Library/Logs/pi-kitty"
        secure_dir(logs)
        argv = [
            "pi-kitty",
            "--socket",
            str(path),
            "daemon",
            "run",
            "--log-file",
            str(logs / "daemon.log"),
        ]
        if endpoint:
            argv.extend(["--endpoint", endpoint])
        if command:
            argv.extend(["--kitty-command", command])
        root = str(Path(__file__).resolve().parent.parent)
        # kitty's embedded Python is not a python executable; re-enter via +runpy.
        if Path(sys.executable).name.lower() in {"kitty", "kitty.exe"}:
            code = (
                f"import sys; sys.path.insert(0, {root!r}); sys.argv = {argv!r}; "
                "from pi_kitty.cli import main; main()"
            )
            launch = [sys.executable, "+runpy", code]
        else:
            launch = [sys.executable, "-m", "pi_kitty", *argv[1:]]
        env = {
            **os.environ,
            "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }
        process = subprocess.Popen(
            launch,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        threading.Thread(
            target=process.wait, name="pi-kitty-reaper", daemon=True
        ).start()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if health():
                break
            time.sleep(0.05)
        else:
            raise Error(
                "STARTUP_FAILED",
                f"Daemon не запустился; проверьте {logs / 'daemon.log'}",
            )
    if endpoint:
        Client(path, timeout=6).request("kitty.announce", {"endpoint": endpoint})
    return client.request("health")
