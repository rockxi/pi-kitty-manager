import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_kitty.common import Error
from pi_kitty.history import hidden_history, list_history, read_session
from pi_kitty.resume import resume
from pi_kitty.view import grouped, status


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.folder = self.root / "sessions" / "--work--"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "session.jsonl"
        self.entries = [
            {"type": "session", "id": "test-1", "cwd": str(self.cwd)},
            {"type": "message", "message": {"role": "user", "content": "First task"}},
            {"type": "session_info", "name": "Old name"},
            {"type": "session_info", "name": "New name"},
        ]
        self.path.write_text("\n".join(json.dumps(x) for x in self.entries) + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_list_metadata_groups_and_filter(self):
        sessions = list_history([self.root / "sessions"])
        self.assertEqual(len(sessions), 1)
        item = sessions[0]
        self.assertEqual(item["session_name"], "New name")
        self.assertEqual(item["preview"], "First task")
        self.assertEqual(item["cwd"], str(self.cwd))
        self.assertEqual(status(item)[0], "История")
        self.assertEqual(
            [kind for kind, _ in grouped(sessions, "new")], ["folder", "agent"]
        )
        self.assertEqual(
            hidden_history(sessions, [{"session_file": str(self.path)}]), []
        )
        self.assertEqual(
            hidden_history(sessions, [{"session_id": "test-1", "cwd": str(self.cwd)}]),
            [],
        )
        self.assertEqual(
            len(hidden_history(sessions, [{"session_id": "test-1", "cwd": "/other"}])),
            1,
        )

    def test_no_name_and_malformed_entries(self):
        self.path.write_text(
            json.dumps(self.entries[0])
            + "\n{broken\n"
            + json.dumps(self.entries[1])
            + "\n"
        )
        item = read_session(self.path)
        self.assertIsNone(item["session_name"])
        self.assertEqual(item["preview"], "First task")
        self.path.write_text("not json\n")
        self.assertIsNone(read_session(self.path))

    def test_symlink_is_not_read(self):
        link = self.folder / "link.jsonl"
        link.symlink_to(self.path)
        self.assertIsNone(read_session(link))

    def test_resume_waits_overlay_and_launches_argv(self):
        calls = []

        class Kitty:
            def endpoint(self, value):
                return value

            async def topology(self, endpoint, urgent=False):
                calls.append(("topology", endpoint))
                return [{"window_id": 55}] if len(calls) == 1 else []

            async def call(self, endpoint, *args, urgent=False):
                calls.append((endpoint, args))
                return "42\n"

        class Client:
            def agents(self):
                return []

        with (
            patch.dict(
                os.environ, {"PI_KITTY_SESSION_DIRS": str(self.root / "sessions")}
            ),
            patch("pi_kitty.resume.shutil.which", return_value="/usr/bin/pi"),
        ):
            result = asyncio.run(
                resume(
                    str(self.path),
                    {"endpoint": "unix:/test", "window_id": 55},
                    Client(),
                    Kitty(),
                )
            )
        self.assertEqual(result["opened"], "42")
        self.assertEqual(
            calls[-1][1],
            (
                "launch",
                "--type=tab",
                "--cwd",
                str(self.cwd),
                "/usr/bin/pi",
                "--session",
                str(self.path.resolve()),
            ),
        )

    def test_existing_session_focuses_instead_of_launch(self):
        class Kitty:
            def endpoint(self, value):
                return value

            async def topology(self, endpoint, urgent=False):
                return []

            async def call(self, *args, **kwargs):
                raise AssertionError("must not launch duplicate")

        class Client:
            def agents(self):
                return [
                    {
                        "agent_id": "a",
                        "session_file": str(self.path),
                        "presence": "online",
                        "location_status": "verified",
                    }
                ]

            def request(self, method, params):
                return {"focused": params["agent_id"]}

            path = self.path

        with patch.dict(
            os.environ, {"PI_KITTY_SESSION_DIRS": str(self.root / "sessions")}
        ):
            result = asyncio.run(
                resume(
                    str(self.path),
                    {"endpoint": "unix:/test", "window_id": 55},
                    Client(),
                    Kitty(),
                )
            )
        self.assertEqual(result, {"focused": "a"})

    def test_reject_outside_roots(self):
        with patch("pi_kitty.resume.session_roots", return_value=[]):
            with self.assertRaises(Error):
                asyncio.run(
                    resume(
                        str(self.path),
                        {"endpoint": "unix:/test", "window_id": 55},
                        object(),
                    )
                )


if __name__ == "__main__":
    unittest.main()
