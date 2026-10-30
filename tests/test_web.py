import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from pi_kitty.web_client import (
    agent_view,
    local_server,
    machine_id,
    read_token,
    rotating_token,
)
from web import server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.old_token = server.TOKEN
        self.old_login, self.old_password = server.ADMIN_LOGIN, server.ADMIN_PASSWORD
        server.TOKEN = "x" * 40
        server.ADMIN_LOGIN, server.ADMIN_PASSWORD = "admin", "password-123456"
        server.SESSIONS.clear()
        server.ATTEMPTS.clear()
        server.MACHINES.clear()
        server.CHATS.clear()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.TOKEN = self.old_token
        server.ADMIN_LOGIN, server.ADMIN_PASSWORD = self.old_login, self.old_password
        server.SESSIONS.clear()
        server.ATTEMPTS.clear()
        server.MACHINES.clear()
        server.CHATS.clear()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.http.server_port, timeout=3
        )
        connection.request(
            method, path, body, {"Host": "localhost:8500", **(headers or {})}
        )
        result = connection.getresponse()
        data = result.read()
        connection.close()
        return result.status, data

    def login(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.http.server_port, timeout=3
        )
        connection.request(
            "POST",
            "/api/login",
            json.dumps({"login": "admin", "password": "password-123456"}),
            {
                "Host": "localhost:8500",
                "Origin": "http://localhost:8500",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        connection.close()
        return {"Cookie": cookie}

    def test_auth_host_update_and_offline(self):
        self.assertEqual(self.request("HEAD", "/")[0], 200)
        self.assertEqual(self.request("HEAD", "/healthz")[0], 200)
        self.assertEqual(self.request("HEAD", "/api/machines")[0], 401)
        ident = str(uuid.uuid4())
        body = json.dumps(
            {
                "machine_id": ident,
                "name": "Mac",
                "daemon_connected": True,
                "agents": [
                    {
                        "agent_id": "1",
                        "cwd": "/work",
                        "session_name": "Session",
                        "activity": "idle",
                        "presence": "online",
                        "model": "m",
                        "source": "extension",
                        "secret": "never retained",
                    }
                ],
            }
        )
        self.assertEqual(self.request("POST", "/api/snapshot", body)[0], 401)
        self.assertEqual(
            self.request("GET", "/api/machines", headers={"Host": "evil.example"})[0],
            403,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/snapshot",
                body,
                {"Authorization": "Bearer " + rotating_token("x" * 40)},
            )[0],
            200,
        )
        self.assertEqual(self.request("GET", "/api/machines")[0], 401)
        status, data = self.request("GET", "/api/machines", headers=self.login())
        machine = json.loads(data)["machines"][0]
        self.assertEqual(status, 200)
        self.assertTrue(machine["online"])
        self.assertNotIn("secret", machine["agents"][0])
        server.MACHINES[ident]["last_seen"] = time.time() - 21
        self.assertFalse(server.snapshot()["machines"][0]["online"])

    def test_invalid_snapshot(self):
        body = json.dumps(
            {
                "machine_id": "bad",
                "name": "test",
                "daemon_connected": True,
                "agents": [],
            }
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/snapshot",
                body,
                {"Authorization": "Bearer " + rotating_token("x" * 40)},
            )[0],
            400,
        )
        self.assertEqual(server.snapshot()["machines"], [])

    def test_chat_requires_auth_and_origin_and_delivers_once(self):
        machine, agent, generation = (str(uuid.uuid4()) for _ in range(3))
        auth = {"Authorization": "Bearer " + rotating_token("x" * 40)}
        snap = {
            "machine_id": machine,
            "name": "Mac",
            "daemon_connected": True,
            "agents": [{"agent_id": agent, "source": "extension"}],
        }
        self.assertEqual(
            self.request("POST", "/api/snapshot", json.dumps(snap), auth)[0], 200
        )
        poll = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "busy": False,
            "messages": [{"role": "assistant", "text": "Hello"}],
        }
        self.assertEqual(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[0], 200
        )
        url = f"/api/chat?machine={machine}&agent={agent}"
        self.assertEqual(self.request("GET", url)[0], 401)
        browser_session = self.login()
        self.assertEqual(
            json.loads(self.request("GET", url, headers=browser_session)[1])[
                "messages"
            ][0]["text"],
            "Hello",
        )
        send = json.dumps(
            {
                "machine": machine,
                "agent": agent,
                "generation": generation,
                "message": "hi",
            }
        )
        self.assertEqual(self.request("POST", "/api/send", send, auth)[0], 403)
        browser = {
            **browser_session,
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        poll["busy"] = True
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        self.assertEqual(self.request("POST", "/api/send", send, browser)[0], 409)
        poll["busy"] = False
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        self.assertEqual(self.request("POST", "/api/send", send, browser)[0], 202)
        self.assertEqual(self.request("POST", "/api/send", send, browser)[0], 409)
        command = json.loads(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
        )["message"]
        self.assertEqual(command["text"], "hi")
        self.assertEqual(self.request("POST", "/api/send", send, browser)[0], 409)
        poll["receipt"] = {"id": command["id"], "state": "accepted"}
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["message"]
        )
        self.assertEqual(
            json.loads(self.request("GET", url, headers=browser_session)[1])[
                "delivery"
            ]["state"],
            "accepted",
        )
        poll["generation"] = str(uuid.uuid4())
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        self.assertEqual(self.request("POST", "/api/send", send, browser)[0], 409)

    def test_login_csrf_rate_limit_and_rotated_keys(self):
        payload = json.dumps({"login": "admin", "password": "password-123456"})
        headers = {"Origin": "http://evil.test", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/login", payload, headers)[0], 403)
        browser = {
            **self.login(),
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        self.assertEqual(self.request("POST", "/api/logout", "{}", browser)[0], 200)
        self.assertEqual(self.request("GET", "/api/machines", headers=browser)[0], 401)
        for _ in range(5):
            self.assertEqual(
                self.request(
                    "POST",
                    "/api/login",
                    json.dumps({"login": "admin", "password": "bad"}),
                    browser,
                )[0],
                401,
            )
        self.assertEqual(self.request("POST", "/api/login", payload, browser)[0], 429)
        ident = str(uuid.uuid4())
        snap = json.dumps(
            {"machine_id": ident, "name": "Mac", "daemon_connected": True, "agents": []}
        )
        self.assertEqual(
            self.request(
                "POST", "/api/snapshot", snap, {"Authorization": "Bearer " + "x" * 40}
            )[0],
            401,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/snapshot",
                snap,
                {
                    "Authorization": "Bearer "
                    + rotating_token("x" * 40, time.time() - 900)
                },
            )[0],
            401,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/snapshot",
                snap,
                {
                    "Authorization": "Bearer "
                    + rotating_token("x" * 40, time.time() - 300)
                },
            )[0],
            200,
        )

    def test_local_only_and_private_metadata(self):
        self.assertEqual(
            local_server("http://localhost:8500/"), "http://localhost:8500"
        )
        for url in (
            "http://example.com:8500",
            "http://localhost",
            "http://localhost:9000",
            "http://localhost:8500/x",
            "http://user@localhost:8500",
        ):
            with self.assertRaises(ValueError):
                local_server(url)
        view = agent_view(
            {
                "session_name": "x",
                "model": {"id": "test"},
                "session_file": "/secret",
                "preview": "private",
            }
        )
        self.assertNotIn("session_file", view)
        self.assertNotIn("preview", view)

    def test_stable_machine_identity_and_token(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}),
        ):
            self.assertEqual(machine_id(), machine_id())
            token = Path(tmp) / ".env"
            token.write_text("PI_KITTY_WEB_TOKEN=" + "x" * 40 + "\n")
            self.assertEqual(read_token(token), "x" * 40)


if __name__ == "__main__":
    unittest.main()
