import concurrent.futures
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from pi_kitty.common import Client
from pi_kitty.startup import ensure


class StartupTests(unittest.TestCase):
    def test_concurrent_startup_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="pk-start-", dir="/tmp") as directory:
            path = Path(directory) / "run/daemon.sock"
            pid = None
            with patch.dict(
                os.environ, {"PI_KITTY_SOCKET": str(path), "KITTY_LISTEN_ON": ""}
            ):
                try:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=3
                    ) as executor:
                        results = list(executor.map(lambda _: ensure(), range(3)))
                    pid = results[0]["pid"]
                    self.assertTrue(all(result["pid"] == pid for result in results))
                    self.assertEqual(ensure()["pid"], pid)
                    self.assertTrue((path.parent / "logs/daemon.log").exists())
                    self.assertEqual(Client(path).request("health")["pid"], pid)
                finally:
                    if pid:
                        os.kill(pid, signal.SIGTERM)
                        deadline = time.monotonic() + 5
                        while path.exists() and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
