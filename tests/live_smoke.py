#!/usr/bin/env python3
"""Opt-in real macOS/kitty smoke test; creates and closes ONLY a new test instance.

No prompts/model calls. Does not edit settings or install a service.
Run: python3 tests/live_smoke.py
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pi_kitty.common import Client  # noqa: E402 - standalone opt-in script


def eventually(fn, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as exc:
            last = exc
        time.sleep(0.1)
    raise AssertionError(f"Condition not met; last error: {last}")


def main():
    kitty = shutil.which("kitty")
    pi = shutil.which("pi")
    if not kitty or not pi:
        raise SystemExit("kitty and pi must be installed")
    with tempfile.TemporaryDirectory(prefix="pk-live-", dir="/tmp") as directory:
        folder = Path(directory)
        folder.chmod(0o700)
        backend, frontend = folder / "backend", folder / "frontend"
        backend.mkdir()
        frontend.mkdir()
        sock = folder / "daemon.sock"
        # kitty appends PID when the listen path has no {kitty_pid} marker.
        endpoint_pattern = f"unix:{folder}/kitty-{{kitty_pid}}.sock"
        env = {
            **os.environ,
            "PI_KITTY_SOCKET": str(sock),
            "KITTY_LISTEN_ON": "",
            "PI_OFFLINE": "1",
        }
        logfile = (folder / "daemon.log").open("w+")
        daemon = subprocess.Popen(
            [sys.executable, "-m", "pi_kitty", "daemon", "run"],
            cwd=ROOT,
            env=env,
            stdout=logfile,
            stderr=logfile,
        )
        endpoint = None
        client = Client(sock, timeout=7)
        pi_args = [
            pi,
            "--offline",
            "--no-session",
            "--no-extensions",
            "-e",
            str(ROOT / "pi-extension/index.ts"),
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-approve",
        ]
        try:
            eventually(lambda: client.request("health"))
            subprocess.run(
                [
                    kitty,
                    "--detach",
                    "--config",
                    "NONE",
                    "--listen-on",
                    endpoint_pattern,
                    "-o",
                    "allow_remote_control=socket-only",
                    "-o",
                    "confirm_os_window_close=0",
                    "--directory",
                    str(backend),
                    *pi_args,
                    "--name",
                    "Smoke API",
                ],
                env=env,
                check=True,
                timeout=10,
            )
            endpoint_file = eventually(
                lambda: next(iter(folder.glob("kitty-*.sock")), None)
            )
            endpoint = "unix:" + str(endpoint_file)

            def rc(*args):
                return subprocess.check_output(
                    [kitty, "@", "--to", endpoint, *args], env=env, text=True, timeout=6
                )

            def windows():
                return [
                    (o, t, w)
                    for o in json.loads(rc("ls"))
                    for t in o["tabs"]
                    for w in t["windows"]
                ]

            first = eventually(
                lambda: next(
                    (
                        a
                        for a in client.agents()
                        if a.get("session_name") == "Smoke API"
                        and a["location_status"] == "verified"
                    ),
                    None,
                )
            )
            second_id = int(
                rc(
                    "launch",
                    "--type=tab",
                    "--tab-title=Smoke frontend",
                    "--cwd",
                    str(frontend),
                    "--env",
                    f"PI_KITTY_SOCKET={sock}",
                    *pi_args,
                    "--name",
                    "Smoke UI",
                ).strip()
            )
            shell_id = int(
                rc(
                    "launch",
                    "--type=tab",
                    "--tab-title=Not an agent",
                    "/bin/sh",
                    "-c",
                    "sleep 90",
                ).strip()
            )
            agents = eventually(
                lambda: (
                    a
                    if len(a := client.agents()) == 2
                    and all(
                        x["source"] == "extension"
                        and x["location_status"] == "verified"
                        for x in a
                    )
                    else None
                )
            )
            assert {a["cwd"] for a in agents} == {
                str(backend.resolve()),
                str(frontend.resolve()),
            }
            assert all(a["activity"] == "idle" for a in agents)
            assert shell_id not in {a["location"]["window_id"] for a in agents}
            print(
                "PASS real pi registration, session names, folder groups, idle statuses, shell excluded"
            )

            rc("kitten", "--match", f"id:{shell_id}", str(ROOT / "kitty/agents.py"))
            overlay_id = eventually(
                lambda: next(
                    (
                        w["id"]
                        for _, _, w in windows()
                        if w["id"]
                        not in {first["location"]["window_id"], second_id, shell_id}
                    ),
                    None,
                )
            )
            screen = eventually(
                lambda: (
                    s
                    if "Свободен"
                    in (s := rc("get-text", "--match", f"id:{overlay_id}"))
                    else None
                )
            )
            assert (
                "Поиск" in screen and "Smoke API" in screen and "вкладка" in screen
            ), screen
            print(
                "PASS Unicode labels and Russian statuses render in the real kitty overlay"
            )
            rc("send-key", "--match", f"id:{overlay_id}", "enter")
            eventually(lambda: not any(w["id"] == overlay_id for _, _, w in windows()))
            selected = eventually(
                lambda: next((a for a in client.agents() if a["mru"] > 0), None)
            )
            assert selected["session_name"] == "Smoke API"
            assert any(
                t["is_active"]
                and w["is_active"]
                and w["id"] == selected["location"]["window_id"]
                for _, t, w in windows()
            )
            print(
                "PASS real kitten rendering, Enter, overlay removal, asynchronous callback and focus"
            )

            rc("focus-window", "--match", f"id:{shell_id}")
            rc("kitten", "--match", f"id:{shell_id}", str(ROOT / "kitty/agents.py"))
            overlay_id = eventually(
                lambda: next(
                    (
                        w["id"]
                        for _, _, w in windows()
                        if w["id"]
                        not in {first["location"]["window_id"], second_id, shell_id}
                    ),
                    None,
                )
            )
            rc("send-key", "--match", f"id:{overlay_id}", "esc")
            eventually(lambda: not any(w["id"] == overlay_id for _, _, w in windows()))
            assert any(
                t["is_active"] and w["is_active"] and w["id"] == shell_id
                for _, t, w in windows()
            )
            print("PASS Escape preserves original terminal focus")

            # Only write a slash command to the isolated smoke-test pi, never user agents.
            rc(
                "send-text",
                "--match",
                f"id:{first['location']['window_id']}",
                "/reload\r",
            )
            time.sleep(2)
            after = eventually(
                lambda: next(
                    (
                        a
                        for a in client.agents()
                        if a["agent_id"] == first["agent_id"]
                        and a["source"] == "extension"
                    ),
                    None,
                )
            )
            assert after["session_name"] == "Smoke API"
            assert len(client.agents()) == 2
            print("PASS real /reload keeps agent identity without duplicates")

            previous_sockets = set(folder.glob("kitty-*.sock"))
            subprocess.run(
                [
                    kitty,
                    "--detach",
                    "--config",
                    "NONE",
                    "--listen-on",
                    endpoint_pattern,
                    "-o",
                    "allow_remote_control=socket-only",
                    "-o",
                    "confirm_os_window_close=0",
                    "--directory",
                    str(frontend),
                    *pi_args,
                    "--name",
                    "Smoke second instance",
                ],
                env=env,
                check=True,
                timeout=10,
            )
            other_file = eventually(
                lambda: next(
                    iter(set(folder.glob("kitty-*.sock")) - previous_sockets), None
                )
            )
            other_endpoint = "unix:" + str(other_file)
            other_agent = eventually(
                lambda: next(
                    (
                        a
                        for a in client.agents()
                        if a.get("session_name") == "Smoke second instance"
                        and a["location_status"] == "verified"
                    ),
                    None,
                )
            )
            rc("focus-window", "--match", f"id:{shell_id}")
            rc("kitten", "--match", f"id:{shell_id}", str(ROOT / "kitty/agents.py"))
            overlay_id = eventually(
                lambda: next(
                    (
                        w["id"]
                        for _, _, w in windows()
                        if w["id"]
                        not in {first["location"]["window_id"], second_id, shell_id}
                    ),
                    None,
                )
            )
            time.sleep(1)
            rc("send-text", "--match", f"id:{overlay_id}", "Smoke second instance")
            time.sleep(0.2)
            rc("send-key", "--match", f"id:{overlay_id}", "enter")
            eventually(
                lambda: next(
                    (
                        a
                        for a in client.agents()
                        if a["agent_id"] == other_agent["agent_id"] and a["mru"] > 0
                    ),
                    None,
                )
            )
            other_tree = json.loads(
                subprocess.check_output(
                    [kitty, "@", "--to", other_endpoint, "ls"],
                    env=env,
                    text=True,
                    timeout=5,
                )
            )
            assert any(
                w["id"] == other_agent["location"]["window_id"] and w["is_focused"]
                for o in other_tree
                for t in o["tabs"]
                for w in t["windows"]
            )
            print("PASS search and cross-instance switching, including OS window focus")
        except Exception:
            logfile.flush()
            logfile.seek(0)
            print(logfile.read(), file=sys.stderr)
            if endpoint:
                # Read only this test instance, never the user's terminal contents.
                with __import__("contextlib").suppress(Exception):
                    print(
                        subprocess.check_output(
                            [kitty, "@", "--to", endpoint, "get-text"],
                            text=True,
                            timeout=5,
                        ),
                        file=sys.stderr,
                    )
            raise
        finally:
            for owned_socket in folder.glob("kitty-*.sock"):
                subprocess.run(
                    [
                        kitty,
                        "@",
                        "--to",
                        "unix:" + str(owned_socket),
                        "close-tab",
                        "--match",
                        "all",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=6,
                )
            daemon.terminate()
            daemon.wait(timeout=7)
            logfile.close()


if __name__ == "__main__":
    main()
