#!/usr/bin/env python3
"""Opt-in isolated kitty watcher test for web configuration; no model calls."""

import contextlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pi_kitty.common import Client  # noqa: E402


def main():
    kitty = shutil.which("kitty")
    if not kitty:
        raise SystemExit("kitty not installed")
    with tempfile.TemporaryDirectory(prefix="pk-web-auto-", dir="/tmp") as directory:
        folder = Path(directory)
        token = folder / ".env"
        token.write_text("PI_KITTY_WEB_TOKEN=" + "x" * 64 + "\n")
        token.chmod(0o600)
        sock = folder / "daemon.sock"
        env = {
            **os.environ,
            "PI_KITTY_SOCKET": str(sock),
            "XDG_CONFIG_HOME": str(folder / "config"),
            "KITTY_LISTEN_ON": "",
        }
        client = Client(sock)
        pid = None
        try:
            subprocess.run(
                [
                    kitty,
                    "--detach",
                    "--config",
                    "NONE",
                    "--listen-on",
                    f"unix:{folder}/kitty-{{kitty_pid}}.sock",
                    "-o",
                    "allow_remote_control=socket-only",
                    "-o",
                    "confirm_os_window_close=0",
                    "-o",
                    f"watcher={ROOT / 'kitty/autostart.py'}",
                    "-o",
                    f"env=PI_KITTY_WEB_TOKEN_FILE={token}",
                    "-o",
                    "env=PI_KITTY_WEB_NAME=Kitty Test",
                    "/bin/sh",
                    "-c",
                    "sleep 60",
                ],
                env=env,
                check=True,
                timeout=10,
            )
            deadline = time.monotonic() + 12
            identity = folder / "config/pi-kitty/web-client/machine-id"
            while time.monotonic() < deadline:
                with contextlib.suppress(Exception):
                    pid = client.request("health")["pid"]
                if pid and identity.exists():
                    break
                time.sleep(0.1)
            assert pid and identity.exists(), (
                "kitty env settings did not start web client"
            )
            print("PASS kitty.conf env starts the isolated web client automatically")
        finally:
            for socket in folder.glob("kitty-*.sock"):
                subprocess.run(
                    [
                        kitty,
                        "@",
                        "--to",
                        "unix:" + str(socket),
                        "close-tab",
                        "--match",
                        "all",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            # This path is unique to the test; never stop another web client.
            processes = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            for line in processes.splitlines():
                if str(token) in line and "pi_kitty.web_client" in line:
                    with contextlib.suppress(ProcessLookupError, ValueError):
                        os.kill(int(line.strip().split(None, 1)[0]), signal.SIGTERM)
            if pid:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
