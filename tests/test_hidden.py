import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from pi_kitty.common import Error
from pi_kitty.hidden import read_hidden, update_hidden, update_hidden_with_changes
from pi_kitty.view import visual_selection


class HiddenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "hidden-sessions.json"
        self.a = str((Path(self.temp.name) / "a.jsonl").resolve())
        self.b = str((Path(self.temp.name) / "b.jsonl").resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_hide_restore_and_persistence(self):
        self.assertEqual(read_hidden(self.path), set())
        self.assertEqual(
            update_hidden([self.a, self.b], True, self.path), {self.a, self.b}
        )
        self.assertEqual(read_hidden(self.path), {self.a, self.b})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(update_hidden([self.a], False, self.path), {self.b})
        self.assertEqual(json.loads(self.path.read_text())["sessions"], [self.b])

    def test_only_real_changes_are_reported_for_undo(self):
        update_hidden([self.a], True, self.path)
        entries, changed = update_hidden_with_changes([self.a, self.b], True, self.path)
        self.assertEqual(entries, {self.a, self.b})
        self.assertEqual(changed, {self.b})
        entries, changed = update_hidden_with_changes(
            [self.a, self.b], False, self.path
        )
        self.assertEqual(entries, set())
        self.assertEqual(changed, {self.a, self.b})
        entries, changed = update_hidden_with_changes([self.a], False, self.path)
        self.assertEqual(changed, set())

    def test_concurrent_updates_do_not_lose_entries(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda p: update_hidden([p], True, self.path), [self.a, self.b]
                )
            )
        self.assertEqual(read_hidden(self.path), {self.a, self.b})

    def test_corrupt_file_is_never_overwritten(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text("bad json")
        with self.assertRaises(Error):
            update_hidden([self.a], True, self.path)
        self.assertEqual(self.path.read_text(), "bad json")

    def test_relative_paths_rejected(self):
        with self.assertRaises(Error):
            update_hidden(["relative"], True, self.path)

    def test_visual_range_only_closed_sessions(self):
        rows = [
            {"agent_id": "a", "source": "history"},
            {"agent_id": "live", "source": "extension"},
            {"agent_id": "b", "source": "history"},
            {"agent_id": "c", "source": "history"},
        ]
        self.assertEqual(visual_selection(rows, "a", "b"), {"a", "b"})
        self.assertEqual(visual_selection(rows, "c", "a"), {"a", "b", "c"})
        self.assertEqual(visual_selection(rows, "missing", "a"), set())


if __name__ == "__main__":
    unittest.main()
