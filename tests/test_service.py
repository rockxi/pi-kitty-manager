from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pi_kitty.cli import LABEL, service
from pi_kitty.common import Error


class ServiceTests(unittest.TestCase):
    def test_install_and_uninstall_only_own_launchagent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sock = home / "run/daemon.sock"
            with (
                patch("pi_kitty.cli.sys.platform", "darwin"),
                patch("pi_kitty.cli.Path.home", return_value=home),
                patch.dict("os.environ", {"PI_KITTY_SOCKET": str(sock)}),
                patch(
                    "pi_kitty.cli.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as run,
            ):
                service("install")
                plist = home / "Library/LaunchAgents" / f"{LABEL}.plist"
                with plist.open("rb") as stream:
                    data = plistlib.load(stream)
                self.assertEqual(data["Label"], LABEL)
                self.assertTrue(Path(data["ProgramArguments"][0]).is_absolute())
                self.assertIn("--log-file", data["ProgramArguments"])
                self.assertEqual(data["StandardOutPath"], "/dev/null")
                self.assertIn("bootstrap", run.call_args.args[0])
                with self.assertRaises(Error):
                    service("install")
                service("uninstall")
                self.assertFalse(plist.exists())
                self.assertIn("bootout", run.call_args.args[0])

    def test_failed_bootstrap_removes_only_generated_plist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with (
                patch("pi_kitty.cli.sys.platform", "darwin"),
                patch("pi_kitty.cli.Path.home", return_value=home),
                patch.dict(
                    "os.environ", {"PI_KITTY_SOCKET": str(home / "run/daemon.sock")}
                ),
                patch(
                    "pi_kitty.cli.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 1, "", "failed"),
                ),
            ):
                with self.assertRaises(Error):
                    service("install")
                self.assertFalse(
                    (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()
                )


if __name__ == "__main__":
    unittest.main()
