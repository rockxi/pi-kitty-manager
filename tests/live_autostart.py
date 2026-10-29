#!/usr/bin/env python3
"""Opt-in test: a NEW kitty instance starts its own isolated daemon via watcher."""

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
    with tempfile.TemporaryDirectory(prefix="pk-auto-", dir="/tmp") as directory:
        folder = Path(directory)
        path = folder / "daemon.sock"
        env = {**os.environ, "PI_KITTY_SOCKET": str(path), "KITTY_LISTEN_ON": ""}
        client = Client(path)
        endpoint, pid = None, None
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
                    "/bin/sh",
                    "-c",
                    "sleep 60",
                ],
                env=env,
                check=True,
                timeout=10,
            )
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                with contextlib.suppress(Exception):
                    health = client.request("health")
                    pid = health["pid"]
                    if health["kitty"] and health["kitty"][0]["windows"]:
                        endpoint = health["kitty"][0]["endpoint"]
                        break
                time.sleep(0.1)
            assert endpoint and pid, "watcher failed to start/announce daemon"
            subprocess.check_output(
                [
                    kitty,
                    "@",
                    "--to",
                    endpoint,
                    "launch",
                    "--type=tab",
                    "/bin/sh",
                    "-c",
                    "sleep 60",
                ],
                env=env,
                text=True,
                timeout=5,
            )
            time.sleep(0.3)
            assert client.request("health")["pid"] == pid
            print(
                "PASS real kitty startup watcher starts daemon without a terminal or manual command"
            )
            print(
                "PASS second tab reuses the same daemon; kitty endpoint automatically registered"
            )
        finally:
            for sock in folder.glob("kitty-*.sock"):
                subprocess.run(
                    [
                        kitty,
                        "@",
                        "--to",
                        "unix:" + str(sock),
                        "close-tab",
                        "--match",
                        "all",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            if pid:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while path.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)


if __name__ == "__main__":
    main()
