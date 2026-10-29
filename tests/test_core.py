from pathlib import Path
import tempfile
import unittest
import uuid

from pi_kitty.common import Error, MAX_LINE, clean, encode, secure_dir
from pi_kitty.kitty import contains_process
from pi_kitty.processes import Process, is_descendant, is_pi, parse_processes
from pi_kitty.registry import Registry
from pi_kitty.view import clip, grouped, half_page_target, status


def params(**overrides):
    return {
        "agent_id": str(uuid.uuid4()),
        "generation": str(uuid.uuid4()),
        "seq": 1,
        "pid": 42,
        "cwd": "/project",
        "mode": "tui",
        "activity": "idle",
        "session_id": "dialog",
        "session_name": "Настоящее название",
        "kitty": {"endpoint": "unix:/test", "window_id": 5},
        **overrides,
    }


def window(pid=40):
    return {
        "window_id": 5,
        "pid": pid,
        "os_window_id": 1,
        "tab_id": 8,
        "tab_index": 2,
        "tab_title": "Не название диалога",
        "cwd": "/project",
        "active": True,
        "foreground": [{"pid": 42, "is_pi": True, "cwd": "/project"}],
    }


class ProcessTests(unittest.TestCase):
    def test_parse_os_identity(self):
        result = parse_processes(
            " 42 40 Thu Oct 29 11:13:11 2026     /path with spaces/pi\ninvalid"
        )
        self.assertEqual(result[42].started, "Thu Oct 29 11:13:11 2026")
        self.assertEqual(result[42].command, "/path with spaces/pi")

    def test_only_pi_entry_points(self):
        for argv in (
            ["pi", ""],
            ["/opt/bin/pi"],
            ["node", "/x/@earendil-works/pi-coding-agent/dist/cli.js"],
        ):
            self.assertTrue(is_pi(argv))
        for argv in (
            ["echo", "pi"],
            ["python", "pi.py"],
            ["vim", "pi"],
            ["node", "/project/pi.js"],
            [],
            None,
        ):
            self.assertFalse(is_pi(argv))

    def test_ancestry_and_cycles(self):
        table = {42: Process(42, 41, "a", "pi"), 41: Process(41, 40, "b", "sh")}
        self.assertTrue(is_descendant(42, 40, table))
        self.assertFalse(is_descendant(42, 90, table))
        self.assertFalse(contains_process(window(90), 42, table))
        table[41] = Process(41, 42, "b", "sh")
        self.assertFalse(is_descendant(42, 90, table))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.r = Registry()
        self.r.processes = {
            42: Process(42, 40, "start", "pi"),
            40: Process(40, 1, "shell", "sh"),
        }
        self.r.instances["unix:/test"] = {
            "id": "kitty-a",
            "windows": [window()],
            "error": None,
        }
        self.p = params()
        self.owner = object()

    def register(self):
        return self.r.update(self.p, self.owner, register=True, now=100)

    def test_real_session_name_and_location(self):
        self.register()
        a = self.r.snapshot(now=101)[0]
        self.assertEqual(a["session_name"], "Настоящее название")
        self.assertEqual(a["location"]["tab_id"], 8)
        self.assertEqual(a["location_status"], "verified")
        self.assertEqual(a["presence"], "online")
        self.assertNotIn("endpoint", a)
        self.assertNotIn("generation", a)

    def test_old_generation_cannot_unregister_or_update(self):
        self.register()
        new_owner = object()
        new = {**self.p, "generation": str(uuid.uuid4()), "seq": 0}
        self.r.update(new, new_owner, register=True, now=101)
        self.r.unregister(self.p, self.owner)
        self.assertEqual(len(self.r.entries), 1)
        with self.assertRaises(Error):
            self.r.update({**self.p, "seq": 2}, self.owner, now=102)
        self.assertEqual(
            self.r.entries[self.p["agent_id"]].generation, new["generation"]
        )

    def test_duplicate_sequence_ignored(self):
        self.register()
        self.assertFalse(self.r.update(self.p, self.owner, now=102)["accepted"])
        self.assertEqual(self.r.entries[self.p["agent_id"]].seen, 100)

    def test_pid_reuse(self):
        self.register()
        self.r.processes[42] = Process(42, 40, "new-start", "pi")
        self.r.prune(now=101)
        self.assertFalse(self.r.entries)

    def test_stale_is_not_dead(self):
        self.register()
        self.assertEqual(self.r.snapshot(now=116)[0]["presence"], "stale")
        self.assertEqual(self.r.snapshot(now=161)[0]["source"], "process")
        self.assertEqual(len(self.r.snapshot(include_stale=True, now=161)), 2)
        self.r.prune(now=401)
        self.assertFalse(self.r.entries)

    def test_discovery_dedup_and_unknown(self):
        a = self.r.snapshot(now=100)[0]
        self.assertEqual(a["activity"], "unknown")
        self.assertIsNone(a["session_name"])
        self.register()
        self.assertEqual(len(self.r.snapshot(now=101)), 1)

    def test_same_session_two_processes(self):
        self.register()
        self.r.processes[43] = Process(43, 40, "start2", "pi")
        self.r.update(params(pid=43), object(), register=True, now=100)
        self.assertEqual(len(self.r.snapshot(now=101)), 2)

    def test_window_move(self):
        self.register()
        self.r.instances["unix:/test"]["windows"][0]["tab_id"] = 99
        self.assertEqual(self.r.snapshot(now=101)[0]["location"]["tab_id"], 99)

    def test_window_id_is_not_enough(self):
        self.register()
        self.r.instances["unix:/test"]["windows"][0]["pid"] = 999
        self.assertEqual(self.r.snapshot(now=101)[0]["location_status"], "unverified")

    def test_instances_with_same_window_id(self):
        self.register()
        self.r.instances["unix:/other"] = {
            "id": "kitty-b",
            "windows": [window(999)],
            "error": None,
        }
        a = next(a for a in self.r.snapshot(now=101) if a["source"] == "extension")
        self.assertEqual(a["location"]["kitty_instance_id"], "kitty-a")

    def test_unreachable_is_not_idle_or_dead(self):
        self.register()
        self.r.instances["unix:/test"]["error"] = "timeout"
        a = self.r.snapshot(now=101)[0]
        self.assertEqual(a["presence"], "online")
        self.assertEqual(a["location_status"], "unreachable")

    def test_registration_validation(self):
        for replacement in (
            {"pid": True},
            {"pid": 999},
            {"seq": "1"},
            {"mode": "shell"},
            {"activity": "waiting"},
            {"kitty": []},
            {"model": "bad"},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(Error):
                self.r.update({**self.p, **replacement}, self.owner, register=True)


class ViewTests(unittest.TestCase):
    def agent(self, cwd, name, **kw):
        return {
            "agent_id": name,
            "cwd": cwd,
            "session_name": name,
            "mode": "tui",
            "location_status": "verified",
            "presence": "online",
            "activity": "busy",
            **kw,
        }

    def test_exact_folder_grouping(self):
        agents = [
            self.agent("/b/api", "B"),
            self.agent("/a/api", "A"),
            self.agent("/a/api", "C"),
        ]
        rows = grouped(agents)
        self.assertEqual([v for k, v in rows if k == "folder"], ["/a/api", "/b/api"])
        self.assertEqual(len([v for k, v in rows if k == "agent"]), 3)

    def test_search_and_only_interactive_agents(self):
        rows = grouped(
            [
                self.agent("/backend", "Авторизация"),
                self.agent("/frontend", "Форма"),
                self.agent("/backend", "headless", mode="rpc"),
            ],
            "backend автор",
        )
        self.assertEqual(
            [a["agent_id"] for k, a in rows if k == "agent"], ["Авторизация"]
        )

    def test_selection_order_not_status_order(self):
        agents = [self.agent("/a", "a", mru=10), self.agent("/a", "b", mru=20)]
        rows = grouped(agents, order={"a": 0, "b": 1})
        self.assertEqual([a["agent_id"] for k, a in rows if k == "agent"], ["a", "b"])

    def test_sanitize_and_column_width(self):
        self.assertEqual(clean("\x1b[31mDanger\x1b[0m\n\x00"), "Danger")
        self.assertEqual(clean("\x1b]2;changed\x07name"), "name")
        self.assertEqual(clip("中文a", 3), "中")
        self.assertEqual(clip("e\u0301x", 1), "e\u0301")
        self.assertEqual(clip("abc", 0), "")

    def test_status_is_textual(self):
        self.assertIn("Работает", status(self.agent("/a", "a"))[0])
        self.assertEqual(
            status(self.agent("/a", "a", presence="stale"))[0], "Нет связи"
        )


class HalfPageTests(unittest.TestCase):
    def test_skips_folder_headers_and_clamps(self):
        rows = [
            ("folder", "/a"),
            ("agent", {"agent_id": "a"}),
            ("agent", {"agent_id": "b"}),
            ("folder", "/b"),
            ("agent", {"agent_id": "c"}),
            ("agent", {"agent_id": "d"}),
            ("folder", "/c"),
            ("agent", {"agent_id": "e"}),
        ]
        self.assertEqual(half_page_target(rows, "a", 6, 1), "c")
        self.assertEqual(half_page_target(rows, "c", 6, -1), "a")
        self.assertEqual(half_page_target(rows, "d", 20, 1), "e")
        self.assertEqual(half_page_target(rows, "b", 20, -1), "a")
        self.assertEqual(half_page_target(rows, "a", 1, 1), "b")
        self.assertIsNone(half_page_target([], None, 10, 1))


class SecurityTests(unittest.TestCase):
    def test_private_runtime_and_message_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run"
            secure_dir(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            path.chmod(0o755)
            with self.assertRaises(Error):
                secure_dir(path)
        with self.assertRaises(Error):
            encode({"data": "x" * MAX_LINE})


if __name__ == "__main__":
    unittest.main()
