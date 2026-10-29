import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from pi_kitty.common import Client, Error, encode

ROOT = Path(__file__).resolve().parent.parent


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pk-", dir="/tmp")
        self.folder = Path(self.temp.name)
        self.path = self.folder / "daemon.sock"
        self.endpoint_path = self.folder / "kitty.sock"
        self.endpoint_socket = socket.socket(socket.AF_UNIX)
        self.endpoint_socket.bind(str(self.endpoint_path))
        self.endpoint = "unix:" + str(self.endpoint_path)
        self.fixture = self.folder / "state.json"
        self.tree = [
            {
                "id": 1,
                "tabs": [
                    {
                        "id": 10,
                        "title": "shell",
                        "is_active": True,
                        "windows": [
                            {
                                "id": 5,
                                "pid": os.getpid(),
                                "is_active": True,
                                "foreground_processes": [
                                    {
                                        "pid": os.getpid(),
                                        "cmdline": ["pi"],
                                        "cwd": "/work/api",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "id": 11,
                        "title": "other",
                        "is_active": False,
                        "windows": [
                            {
                                "id": 6,
                                "pid": os.getpid(),
                                "is_active": True,
                                "foreground_processes": [],
                            }
                        ],
                    },
                ],
            }
        ]
        self.fixture.write_text(json.dumps(self.tree))
        env = {
            **os.environ,
            "KITTY_LISTEN_ON": "",
            "PI_KITTY_SOCKET": str(self.path),
            "FAKE_KITTY_STATE": str(self.fixture),
            "PYTHONPATH": str(ROOT),
        }
        self.env = env
        self.log = (self.folder / "daemon.log").open("w+")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pi_kitty",
                "daemon",
                "run",
                "--kitty-command",
                str(ROOT / "tests/fake_kitty.py"),
                "--endpoint",
                self.endpoint,
            ],
            cwd=ROOT,
            env=env,
            stdout=self.log,
            stderr=self.log,
        )
        self.client = Client(self.path, timeout=6)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.log.seek(0)
                self.fail(self.log.read())
            try:
                if self.client.agents():
                    return
            except (OSError, Error):
                pass
            time.sleep(0.05)
        self.fail("daemon did not start")

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=7)
        self.endpoint_socket.close()
        self.log.close()
        self.temp.cleanup()

    def session(self):
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(5)
        sock.connect(str(self.path))
        stream = sock.makefile("rb")
        return sock, stream

    def send(self, sock, stream, method, params):
        sock.sendall(encode({"v": 1, "id": "test", "method": method, "params": params}))
        return json.loads(stream.readline())

    def registration(self, **changes):
        return {
            "agent_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "seq": 1,
            "pid": os.getpid(),
            "cwd": "/work/api",
            "mode": "tui",
            "activity": "busy",
            "phase": "tools",
            "session_name": "Исправление API",
            "kitty": {"endpoint": self.endpoint, "window_id": 5},
            **changes,
        }

    def test_discovery_only_pi(self):
        agents = self.client.agents()
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["source"], "process")
        self.assertEqual(agents[0]["activity"], "unknown")
        self.assertEqual(agents[0]["location"]["window_id"], 5)
        self.assertIsNone(agents[0]["session_name"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_lifecycle_over_socket(self):
        sock, stream = self.session()
        try:
            p = self.registration()
            self.assertIn("result", self.send(sock, stream, "agent.register", p))
            agents = self.client.agents()
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["session_name"], "Исправление API")
            p.update(seq=2, activity="idle", session_name="Другое название")
            self.send(sock, stream, "agent.update", p)
            self.assertEqual(self.client.agents()[0]["activity"], "idle")
            self.assertEqual(self.client.agents()[0]["session_name"], "Другое название")
            self.send(sock, stream, "agent.unregister", p)
            self.assertEqual(self.client.agents()[0]["source"], "process")
        finally:
            stream.close()
            sock.close()

    def test_focus_validates_and_activates(self):
        sock, stream = self.session()
        try:
            p = self.registration(kitty={"endpoint": self.endpoint, "window_id": 6})
            self.send(sock, stream, "agent.register", p)
            self.client.request("agents.focus", {"agent_id": p["agent_id"]})
            tree = json.loads(self.fixture.read_text())
            self.assertTrue(tree[0]["tabs"][1]["is_active"])
            self.assertFalse(tree[0]["tabs"][0]["is_active"])
            with self.assertRaises(Error):
                self.client.request("agents.focus", {"agent_id": "missing"})
        finally:
            stream.close()
            sock.close()

    def test_focus_waits_for_overlay_to_disappear(self):
        agent = self.client.agents()[0]
        overlay = {
            "id": 99,
            "pid": os.getpid(),
            "is_active": True,
            "foreground_processes": [],
        }
        self.tree[0]["tabs"][0]["windows"].append(overlay)
        self.fixture.write_text(json.dumps(self.tree))
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                self.client.request,
                "agents.focus",
                {
                    "agent_id": agent["agent_id"],
                    "overlay": {"endpoint": self.endpoint, "window_id": 99},
                },
            )
            time.sleep(0.2)
            self.assertFalse(future.done())
            self.tree[0]["tabs"][0]["windows"].pop()
            self.fixture.write_text(json.dumps(self.tree))
            self.assertEqual(future.result(timeout=5)["focused"], agent["agent_id"])

    def test_panel_closed_is_not_focused(self):
        agent = self.client.agents()[0]
        self.tree[0]["tabs"][0]["windows"].clear()
        self.fixture.write_text(json.dumps(self.tree))
        with self.assertRaises(Error):
            self.client.request("agents.focus", {"agent_id": agent["agent_id"]})

    def test_fragmented_and_invalid_messages(self):
        sock, stream = self.session()
        try:
            data = encode({"v": 1, "id": "fragment", "method": "hello"})
            sock.sendall(data[:8])
            sock.sendall(data[8:])
            self.assertEqual(json.loads(stream.readline())["id"], "fragment")
            sock.sendall(b"not json\n[]\n")
            self.assertIn("error", json.loads(stream.readline()))
            self.assertIn("error", json.loads(stream.readline()))
            sock.sendall(encode({"v": 9, "id": 4, "method": "hello"}))
            self.assertEqual(
                json.loads(stream.readline())["error"]["code"], "INCOMPATIBLE_PROTOCOL"
            )
        finally:
            stream.close()
            sock.close()

    def test_singleton_and_shutdown_cleanup(self):
        result = subprocess.run(
            [sys.executable, "-m", "pi_kitty", "daemon", "run"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("уже запущен", result.stderr)
        self.assertTrue(self.path.exists())
        self.assertIn("boot_id", self.client.request("health"))
        self.proc.terminate()
        self.proc.wait(timeout=7)
        self.assertFalse(self.path.exists())

    def test_large_utf8_snapshot_is_paginated(self):
        sock, stream = self.session()
        try:
            for _ in range(12):
                p = self.registration(cwd="/" + "界" * 2000, session_name="名" * 256)
                self.assertIn("result", self.send(sock, stream, "agent.register", p))
            self.assertEqual(len(self.client.agents()), 12)
        finally:
            stream.close()
            sock.close()

    def test_reject_arbitrary_endpoint(self):
        for endpoint in (
            "tcp:127.0.0.1:1234",
            "unix:relative",
            "unix:" + str(self.fixture),
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(Error):
                self.client.request("kitty.announce", {"endpoint": endpoint})


if __name__ == "__main__":
    unittest.main()
