import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_kitty.i18n import language, tr, translate_error
from pi_kitty.view import grouped, status


class LanguageTests(unittest.TestCase):
    def test_default_config_and_environment_precedence(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}, clear=True),
        ):
            self.assertEqual(language(), "ru")
            config = Path(folder) / "pi-kitty/config.toml"
            config.parent.mkdir()
            config.write_text('language = "en"\n')
            self.assertEqual(language(), "en")
            with patch.dict(os.environ, {"PI_KITTY_LANG": "ru"}):
                self.assertEqual(language(), "ru")
            config.write_text("language = [invalid")
            self.assertEqual(language(), "ru")

    def test_labels_and_error_translation(self):
        agent = {"agent_id": "1", "mode": "tui", "source": "history"}
        self.assertEqual(status(agent, "en")[0], "History")
        self.assertEqual(status(agent, "ru")[0], "История")
        self.assertEqual(grouped([agent], lang="en")[0][1], "Unknown folder")
        self.assertEqual(
            tr("Отменено, чатов: {count}", "en", count=2), "Undone, chats: 2"
        )
        self.assertEqual(
            translate_error("Неверный путь сессии", "en"), "Invalid session path"
        )
        self.assertEqual(translate_error("/tmp/История", "en"), "/tmp/История")


if __name__ == "__main__":
    unittest.main()
