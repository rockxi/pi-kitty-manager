import base64
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
        server.RENAMES.clear()
        server.LAUNCHES.clear()
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
        server.RENAMES.clear()
        server.LAUNCHES.clear()

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

    def test_slash_commands_are_session_bound_and_explicit(self):
        machine, agent, generation = (str(uuid.uuid4()) for _ in range(3))
        auth = {"Authorization": "Bearer " + rotating_token("x" * 40)}
        browser = {
            **self.login(),
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        self.request(
            "POST",
            "/api/snapshot",
            json.dumps(
                {
                    "machine_id": machine,
                    "name": "Mac",
                    "daemon_connected": True,
                    "agents": [{"agent_id": agent, "source": "extension"}],
                }
            ),
            auth,
        )
        poll = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "busy": False,
            "messages": [],
            "session": "session-a",
            "commands_supported": True,
            "commands": [
                {"name": "skill:test", "description": "test skill", "source": "skill"}
            ],
        }
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        send = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "session": "session-a",
            "message": "/skill:test args",
            "slash": True,
        }
        self.assertEqual(
            self.request(
                "POST", "/api/send", json.dumps(dict(send, message="/quit")), browser
            )[0],
            409,
        )
        self.assertEqual(
            self.request(
                "POST", "/api/send", json.dumps(dict(send, session="other")), browser
            )[0],
            409,
        )
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(send), browser)[0], 202
        )
        command = json.loads(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
        )["message"]
        self.assertEqual(command["slash"], True)
        self.assertEqual(command["session"], "session-a")
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["message"]
        )
        poll["receipt"] = {"id": command["id"], "state": "accepted"}
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(send), browser)[0], 202
        )
        poll["session"] = "session-b"
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["message"]
        )
        self.assertEqual(server.CHATS[(machine, agent)]["delivery"]["state"], "expired")

    def test_images_require_capability_and_delivery_is_once(self):
        machine, agent, generation = (str(uuid.uuid4()) for _ in range(3))
        auth = {"Authorization": "Bearer " + rotating_token("x" * 40)}
        browser = {
            **self.login(),
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        self.request(
            "POST",
            "/api/snapshot",
            json.dumps(
                {
                    "machine_id": machine,
                    "name": "Mac",
                    "daemon_connected": True,
                    "agents": [{"agent_id": agent, "source": "extension"}],
                }
            ),
            auth,
        )
        poll = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "busy": False,
            "messages": [],
            "session": "session-a",
        }
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        image = {
            "type": "image",
            "mimeType": "image/png",
            "data": base64.b64encode(b"\x89PNG\r\n\x1a\nhello").decode(),
        }
        send = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "session": "session-a",
            "message": "",
            "images": [image],
        }
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(send), browser)[0], 409
        )
        poll["image_supported"] = True
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        invalid = dict(send, images=[dict(image, data="%%%")])
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(invalid), browser)[0], 400
        )
        self.assertEqual(
            self.request(
                "POST", "/api/send", json.dumps(dict(send, session="other")), browser
            )[0],
            409,
        )
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(send), browser)[0], 202
        )
        command = json.loads(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
        )["message"]
        self.assertEqual(command["images"], [image])
        self.assertEqual(command["session"], "session-a")
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["message"]
        )
        poll["receipt"] = {"id": command["id"], "state": "accepted"}
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        self.assertEqual(
            self.request("POST", "/api/send", json.dumps(send), browser)[0], 202
        )
        poll["session"] = "session-b"
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["message"]
        )
        self.assertEqual(server.CHATS[(machine, agent)]["delivery"]["state"], "expired")
        self.assertIsNone(server.CHATS[(machine, agent)]["pending"])

    def test_rename_requires_capability_and_matching_session(self):
        machine, agent, generation = (str(uuid.uuid4()) for _ in range(3))
        auth = {"Authorization": "Bearer " + rotating_token("x" * 40)}
        browser = {
            **self.login(),
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        self.request(
            "POST",
            "/api/snapshot",
            json.dumps(
                {
                    "machine_id": machine,
                    "name": "Mac",
                    "daemon_connected": True,
                    "agents": [{"agent_id": agent, "source": "extension"}],
                }
            ),
            auth,
        )
        poll = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "session": "session-a",
            "busy": True,
            "messages": [],
        }
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        rename = {
            "machine": machine,
            "agent": agent,
            "generation": generation,
            "session": "session-a",
            "name": "Новое имя",
        }
        self.assertEqual(
            self.request("POST", "/api/rename", json.dumps(rename), browser)[0], 409
        )
        poll["rename_supported"] = True
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        wrong = dict(rename, session="another")
        self.assertEqual(
            self.request("POST", "/api/rename", json.dumps(wrong), browser)[0], 409
        )
        self.assertEqual(
            self.request(
                "POST", "/api/rename", json.dumps(dict(rename, name="a\nb")), browser
            )[0],
            400,
        )
        self.assertEqual(
            self.request("POST", "/api/rename", json.dumps(rename))[0], 403
        )
        status, data = self.request("POST", "/api/rename", json.dumps(rename), browser)
        self.assertEqual(status, 202)
        ident = json.loads(data)["id"]
        command = json.loads(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
        )["rename"]
        self.assertEqual(
            command, {"id": ident, "name": "Новое имя", "session": "session-a"}
        )
        self.assertIsNone(
            json.loads(
                self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
            )["rename"]
        )
        self.assertEqual(
            self.request("POST", "/api/rename", json.dumps(rename), browser)[0], 409
        )
        poll["rename_receipt"] = {"id": ident, "state": "accepted"}
        self.request("POST", "/api/chat-poll", json.dumps(poll), auth)
        data = json.loads(
            self.request(
                "GET", f"/api/chat?machine={machine}&agent={agent}", headers=browser
            )[1]
        )
        self.assertEqual(data["rename"]["state"], "accepted")
        self.assertEqual(
            self.request("POST", "/api/rename", json.dumps(rename), browser)[0], 202
        )
        poll["session"] = "session-b"
        data = json.loads(
            self.request("POST", "/api/chat-poll", json.dumps(poll), auth)[1]
        )
        self.assertIsNone(data["rename"])
        self.assertEqual(server.RENAMES[(machine, agent)]["state"], "expired")

    def test_launch_is_opt_in_and_delivered_at_most_once(self):
        machine = str(uuid.uuid4())
        auth = {"Authorization": "Bearer " + rotating_token("x" * 40)}
        browser = {
            **self.login(),
            "Origin": "http://localhost:8500",
            "Content-Type": "application/json",
        }
        snapshot = {
            "machine_id": machine,
            "name": "Mac",
            "daemon_connected": True,
            "agents": [],
        }
        self.request("POST", "/api/snapshot", json.dumps(snapshot), auth)
        request = json.dumps({"machine": machine, "cwd": "/tmp"})
        self.assertEqual(self.request("POST", "/api/launch", request, browser)[0], 409)
        snapshot["launch_enabled"] = True
        self.request("POST", "/api/snapshot", json.dumps(snapshot), auth)
        self.assertEqual(self.request("POST", "/api/launch", request, auth)[0], 403)
        self.assertEqual(
            self.request(
                "POST",
                "/api/launch",
                json.dumps({"machine": machine, "cwd": "relative"}),
                browser,
            )[0],
            400,
        )
        status, data = self.request("POST", "/api/launch", request, browser)
        self.assertEqual(status, 202)
        ident = json.loads(data)["id"]
        self.assertEqual(self.request("POST", "/api/launch", request, browser)[0], 409)
        poll = json.dumps({"machine": machine})
        command = json.loads(self.request("POST", "/api/launch-poll", poll, auth)[1])[
            "command"
        ]
        self.assertEqual(command, {"id": ident, "cwd": "/tmp"})
        self.assertIsNone(
            json.loads(self.request("POST", "/api/launch-poll", poll, auth)[1])[
                "command"
            ]
        )
        self.assertEqual(self.request("POST", "/api/launch", request, browser)[0], 409)
        receipt = json.dumps(
            {"machine": machine, "receipt": {"id": ident, "state": "started"}}
        )
        self.request("POST", "/api/launch-poll", receipt, auth)
        state = json.loads(
            self.request(
                "GET", "/api/launch-status?machine=" + machine, headers=browser
            )[1]
        )
        self.assertEqual(state["launch"]["state"], "started")
        self.assertEqual(self.request("POST", "/api/launch", request, browser)[0], 202)

    def test_launch_arguments_are_not_interpreted_by_shell(self):
        from pi_kitty.web_client import launch_agent
        from unittest.mock import AsyncMock

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "a;touch danger"
            target.mkdir()
            with (
                patch("pi_kitty.web_client.shutil.which", return_value="/opt/pi"),
                patch("pi_kitty.web_client.Kitty.endpoint"),
                patch(
                    "pi_kitty.web_client.Client.request",
                    return_value={
                        "kitty": [{"endpoint": "unix:/tmp/test", "error": None}]
                    },
                ),
                patch(
                    "pi_kitty.web_client.Kitty.call",
                    new_callable=AsyncMock,
                    return_value="42\n",
                ) as call,
            ):
                self.assertEqual(
                    launch_agent(
                        str(target),
                        "unix:/tmp/test",
                        "https://example.com",
                        "/tmp/token",
                        True,
                    ),
                    "42",
                )
                args = call.await_args.args
                self.assertIn("--cwd=" + str(target), args)
                self.assertEqual(args[-1], "/opt/pi")
            with self.assertRaises(ValueError):
                launch_agent(
                    "/does-not-exist",
                    "unix:/tmp/test",
                    "https://example.com",
                    "/tmp/token",
                    True,
                )

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
        self.assertEqual(
            local_server("https://agents.rockxi.ru/"), "https://agents.rockxi.ru"
        )
        self.assertEqual(
            local_server("https://example.com:8443"), "https://example.com:8443"
        )
        for url in (
            "http://example.com:8500",
            "http://localhost",
            "http://localhost:9000",
            "http://localhost:8500/x",
            "http://user@localhost:8500",
            "https://user@example.com",
            "https://@example.com",
            "https://example.com/path",
            "https://example.com?key=1",
            "https://example.com/#fragment",
            "https://example.com\n",
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

    def test_tls_context_verifies_certificates(self):
        import ssl
        from pi_kitty.web_client import tls_context

        context = tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

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
