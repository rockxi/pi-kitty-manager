"""Local-only dashboard with opt-in authenticated chat."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import unicodedata
from http.cookies import SimpleCookie

ROOT = Path(__file__).resolve().parent
TOKEN = os.environ.get("PI_KITTY_WEB_TOKEN", "")
PREVIOUS_TOKEN = os.environ.get("PI_KITTY_WEB_PREVIOUS_TOKEN", "")
try:
    PREVIOUS_UNTIL = int(os.environ.get("PI_KITTY_WEB_PREVIOUS_UNTIL", "0"))
except ValueError:
    PREVIOUS_UNTIL = 0
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "http://localhost:8500").rstrip("/")
SESSION_TTL = 8 * 60 * 60
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()
MACHINES = {}
CHATS = {}
RENAMES = {}
LAUNCHES = {}
MAX_BYTES = 262144


def snapshot():
    now = time.time()
    with LOCK:
        return {
            "machines": [
                dict(value, online=now - value["last_seen"] < 20)
                for value in MACHINES.values()
            ]
        }


def parse_update(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected an object")
    machine_id = str(uuid.UUID(payload.get("machine_id", "")))
    name = payload.get("name")
    agents = payload.get("agents")
    connected = payload.get("daemon_connected")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 150
        or type(connected) is not bool
        or not isinstance(agents, list)
        or len(agents) > 1000
    ):
        raise ValueError("Invalid snapshot")
    safe_agents = []
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError("Invalid agent")
        item = {}
        for key, limit in (
            ("agent_id", 100),
            ("cwd", 1024),
            ("session_name", 500),
            ("activity", 30),
            ("presence", 30),
            ("model", 150),
            ("source", 30),
        ):
            value = agent.get(key, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("Invalid agent field")
            item[key] = value
        safe_agents.append(item)
    launch_enabled = payload.get("launch_enabled", False)
    if type(launch_enabled) is not bool:
        raise ValueError("Invalid launch capability")
    return machine_id, {
        "id": machine_id,
        "name": name,
        "daemon_connected": connected,
        "launch_enabled": launch_enabled,
        "agents": safe_agents,
        "last_seen": time.time(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "pi-kitty-web"

    def response_headers(self, status, content_type, size, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def send_data(self, status, content_type, data):
        self.response_headers(status, content_type, len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def json_response(self, status, obj, cookie=None):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.response_headers(
            status, "application/json; charset=utf-8", len(data), cookie
        )
        if self.command != "HEAD":
            self.wfile.write(data)

    def valid_host(self):
        host = self.headers.get("Host", "").lower()
        return host in {
            "localhost:8500",
            "127.0.0.1:8500",
            urlsplit(PUBLIC_ORIGIN).netloc.lower(),
        }

    def do_GET(self):
        if not self.valid_host():
            return self.json_response(403, {"error": "Invalid host"})
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.json_response(200, {"ok": True})
        if path == "/api/session":
            return self.json_response(200, {"authenticated": self.browser_authorized()})
        if path == "/api/machines":
            if not self.browser_authorized():
                return self.json_response(401, {"error": "Unauthorized"})
            return self.json_response(200, snapshot())
        if path == "/api/launch-status":
            if not self.browser_authorized():
                return self.json_response(401, {"error": "Unauthorized"})
            machine = parse_qs(urlsplit(self.path).query).get("machine", [""])[0]
            with LOCK:
                item = LAUNCHES.get(machine)
                result = {"state": item["state"], "id": item["id"]} if item else None
                if (
                    item
                    and item["state"] == "unconfirmed"
                    and time.time() - item.get("dispatched_at", 0) < 15
                ):
                    result["state"] = "dispatching"
                if (
                    item
                    and item["state"] == "queued"
                    and item["expires"] <= time.time()
                ):
                    item["state"] = "expired"
                    result["state"] = "expired"
            return self.json_response(200, {"launch": result})
        if path == "/api/chat":
            if not self.browser_authorized():
                return self.json_response(401, {"error": "Unauthorized"})
            query = parse_qs(urlsplit(self.path).query)
            key = (query.get("machine", [""])[0], query.get("agent", [""])[0])
            with LOCK:
                chat = CHATS.get(key)
                rename = RENAMES.get(key)
                if (
                    rename
                    and rename["state"] == "queued"
                    and rename["expires"] <= time.time()
                ):
                    rename["state"] = "expired"
                rename_status = (
                    {"id": rename["id"], "state": rename["state"]} if rename else None
                )
                if (
                    rename
                    and rename["state"] == "unconfirmed"
                    and time.time() - rename.get("dispatched_at", 0) < 10
                ):
                    rename_status["state"] = "dispatching"
                result = {
                    "available": bool(chat and time.time() - chat["seen"] < 15),
                    "generation": chat["generation"] if chat else None,
                    "session": chat.get("session") if chat else None,
                    "rename_supported": bool(chat and chat.get("rename_supported")),
                    "rename": rename_status,
                    "messages": chat["messages"] if chat else [],
                    "busy": chat["busy"] if chat else False,
                    "pending": bool(chat and chat["pending"]),
                    "delivery": chat.get("delivery") if chat else None,
                }
            return self.json_response(200, result)
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/chat.css": ("chat.css", "text/css; charset=utf-8"),
            "/launch.css": ("launch.css", "text/css; charset=utf-8"),
        }
        if path not in files:
            return self.json_response(404, {"error": "Not found"})
        name, kind = files[path]
        self.send_data(200, kind, (ROOT / name).read_bytes())

    def do_HEAD(self):
        return self.do_GET()

    def browser_authorized(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie.get("pi_session")
            with LOCK:
                expiry = SESSIONS.get(value.value, 0) if value else 0
            return expiry > time.time()
        except Exception:
            return False

    def machine_authorized(self):
        value = self.headers.get("Authorization", "")
        now = time.time()
        keys = [TOKEN]
        if PREVIOUS_TOKEN and now < PREVIOUS_UNTIL:
            keys.append(PREVIOUS_TOKEN)
        for key in keys:
            for epoch in (int(now // 300), int(now // 300) - 1):
                signature = hmac.new(
                    key.encode(), str(epoch).encode(), hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(value, f"Bearer v1.{epoch}.{signature}"):
                    return True
        return False

    def cookie(self, value, max_age):
        secure = "; Secure" if PUBLIC_ORIGIN.startswith("https://") else ""
        return f"pi_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def same_origin(self):
        return (
            self.headers.get("Origin") == PUBLIC_ORIGIN
            and self.headers.get("Content-Type", "").split(";")[0] == "application/json"
        )

    def login(self, data):
        address = (
            self.headers.get("X-Real-IP", self.client_address[0])
            if os.environ.get("TRUST_PROXY") == "1"
            else self.client_address[0]
        )
        now = time.time()
        with LOCK:
            if len(ATTEMPTS) >= 10000 and address not in ATTEMPTS:
                for key, attempts in list(ATTEMPTS.items()):
                    if not attempts or now - attempts[-1] >= 900:
                        del ATTEMPTS[key]
                if len(ATTEMPTS) >= 10000:
                    return self.json_response(429, {"error": "Too many attempts"})
            ATTEMPTS[address] = [t for t in ATTEMPTS.get(address, []) if now - t < 900]
            if len(ATTEMPTS[address]) >= 5:
                return self.json_response(
                    429, {"error": "Too many attempts; try again later"}
                )
            ATTEMPTS[address].append(now)
        login, password = data.get("login"), data.get("password")
        if (
            not isinstance(login, str)
            or not isinstance(password, str)
            or not hmac.compare_digest(login.encode(), ADMIN_LOGIN.encode())
            or not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
        ):
            return self.json_response(401, {"error": "Invalid credentials"})
        token = secrets.token_urlsafe(32)
        with LOCK:
            ATTEMPTS.pop(address, None)
            for key, expiry in list(SESSIONS.items()):
                if expiry <= now:
                    del SESSIONS[key]
            if len(SESSIONS) >= 100:
                return self.json_response(429, {"error": "Too many sessions"})
            SESSIONS[token] = now + SESSION_TTL
        return self.json_response(200, {"ok": True}, self.cookie(token, SESSION_TTL))

    def do_POST(self):
        if not self.valid_host():
            return self.json_response(403, {"error": "Invalid host"})
        path = urlsplit(self.path).path
        if path not in {
            "/api/snapshot",
            "/api/chat-poll",
            "/api/launch-poll",
            "/api/launch",
            "/api/launch-ack",
            "/api/send",
            "/api/rename",
            "/api/rename-ack",
            "/api/login",
            "/api/logout",
        }:
            return self.json_response(404, {"error": "Not found"})
        browser = path in {
            "/api/send",
            "/api/launch",
            "/api/launch-ack",
            "/api/rename",
            "/api/rename-ack",
            "/api/login",
            "/api/logout",
        }
        if browser and not self.same_origin():
            return self.json_response(403, {"error": "Invalid origin"})
        if path not in {"/api/login", "/api/logout"} and not (
            self.browser_authorized() if browser else self.machine_authorized()
        ):
            return self.json_response(401, {"error": "Unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BYTES:
                raise ValueError("Invalid length")
            payload = json.loads(self.rfile.read(length))
            if path == "/api/login":
                return self.login(payload)
            if path == "/api/logout":
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                session = cookie.get("pi_session")
                with LOCK:
                    if session:
                        SESSIONS.pop(session.value, None)
                return self.json_response(200, {"ok": True}, self.cookie("", 0))
            if path == "/api/launch":
                return self.browser_launch(payload)
            if path == "/api/launch-ack":
                return self.ack_launch(payload)
            if path == "/api/launch-poll":
                return self.launch_poll(payload)
            if path == "/api/chat-poll":
                return self.chat_poll(payload)
            if path == "/api/send":
                return self.browser_send(payload)
            if path == "/api/rename":
                return self.browser_rename(payload)
            if path == "/api/rename-ack":
                return self.ack_rename(payload)
            machine_id, machine = parse_update(payload)
            with LOCK:
                if machine_id not in MACHINES and len(MACHINES) >= 100:
                    return self.json_response(429, {"error": "Machine limit reached"})
                MACHINES[machine_id] = machine
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return self.json_response(400, {"error": "Invalid snapshot"})
        self.json_response(200, {"ok": True})

    def browser_launch(self, data):
        machine = str(uuid.UUID(data.get("machine", "")))
        cwd = data.get("cwd")
        if (
            not isinstance(cwd, str)
            or not cwd.startswith("/")
            or len(cwd) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in cwd)
        ):
            raise ValueError("Invalid folder")
        with LOCK:
            source = MACHINES.get(machine)
            current = LAUNCHES.get(machine)
            if (
                current
                and current["state"] == "queued"
                and current["expires"] <= time.time()
            ):
                current["state"] = "expired"
            if (
                not source
                or not source["launch_enabled"]
                or not source["daemon_connected"]
                or time.time() - source["last_seen"] >= 20
            ):
                return self.json_response(409, {"error": "Launch unavailable"})
            if current and current["state"] in {"queued", "unconfirmed"}:
                return self.json_response(
                    409, {"error": "Previous launch is pending or unconfirmed"}
                )
            item = {
                "id": str(uuid.uuid4()),
                "cwd": cwd,
                "state": "queued",
                "expires": time.time() + 30,
            }
            LAUNCHES[machine] = item
        return self.json_response(202, {"id": item["id"]})

    def ack_launch(self, data):
        machine = str(uuid.UUID(data.get("machine", "")))
        with LOCK:
            item = LAUNCHES.get(machine)
            if (
                not item
                or item["id"] != data.get("id")
                or item["state"] != "unconfirmed"
                or time.time() - item.get("dispatched_at", 0) < 15
            ):
                return self.json_response(409, {"error": "Launch state changed"})
            item["state"] = "acknowledged"
        return self.json_response(200, {"ok": True})

    def launch_poll(self, data):
        machine = str(uuid.UUID(data.get("machine", "")))
        with LOCK:
            source = MACHINES.get(machine)
            if (
                not source
                or not source["launch_enabled"]
                or time.time() - source["last_seen"] >= 20
            ):
                return self.json_response(409, {"error": "Launch unavailable"})
            item = LAUNCHES.get(machine)
            receipt = data.get("receipt")
            if (
                item
                and isinstance(receipt, dict)
                and receipt.get("id") == item["id"]
                and receipt.get("state") in {"started", "failed"}
                and item["state"] == "unconfirmed"
            ):
                item["state"] = receipt["state"]
            command = None
            if item and item["state"] == "queued":
                if item["expires"] <= time.time():
                    item["state"] = "expired"
                else:
                    command = {"id": item["id"], "cwd": item["cwd"]}
                    item["state"] = "unconfirmed"
                    item["dispatched_at"] = time.time()
        return self.json_response(200, {"command": command})

    def browser_rename(self, data):
        machine, agent, generation, session, name = (
            data.get(k) for k in ("machine", "agent", "generation", "session", "name")
        )
        if not all(
            isinstance(v, str) for v in (machine, agent, generation, session, name)
        ):
            raise ValueError("Invalid rename")
        name = name.strip()
        if (
            not name
            or len(name) > 200
            or any(unicodedata.category(c).startswith("C") for c in name)
        ):
            raise ValueError("Invalid name")
        key = (machine, agent)
        with LOCK:
            chat, current = CHATS.get(key), RENAMES.get(key)
            if (
                not chat
                or not chat.get("rename_supported")
                or chat["generation"] != generation
                or chat.get("session") != session
                or time.time() - chat["seen"] >= 15
            ):
                return self.json_response(
                    409, {"error": "Agent unavailable or session changed"}
                )
            if (
                current
                and current["state"] == "queued"
                and current["expires"] <= time.time()
            ):
                current["state"] = "expired"
            if current and current["state"] in {"queued", "unconfirmed"}:
                return self.json_response(
                    409, {"error": "Rename pending or unconfirmed"}
                )
            item = {
                "id": str(uuid.uuid4()),
                "name": name,
                "generation": generation,
                "session": session,
                "state": "queued",
                "expires": time.time() + 20,
            }
            RENAMES[key] = item
        return self.json_response(202, {"id": item["id"]})

    def ack_rename(self, data):
        key = (data.get("machine"), data.get("agent"))
        with LOCK:
            item = RENAMES.get(key)
            if (
                not item
                or item["id"] != data.get("id")
                or item["state"] != "unconfirmed"
                or time.time() - item.get("dispatched_at", 0) < 10
            ):
                return self.json_response(409, {"error": "Rename state changed"})
            item["state"] = "acknowledged"
        return self.json_response(200, {"ok": True})

    def browser_send(self, data):
        machine, agent, generation, message = (
            data.get(k) for k in ("machine", "agent", "generation", "message")
        )
        if (
            not all(isinstance(v, str) for v in (machine, agent, generation, message))
            or not message.strip()
            or len(message) > 4000
        ):
            raise ValueError("Invalid message")
        with LOCK:
            chat = CHATS.get((machine, agent))
            if (
                not chat
                or chat["generation"] != generation
                or time.time() - chat["seen"] >= 15
            ):
                return self.json_response(
                    409, {"error": "Agent unavailable or session changed"}
                )
            if (
                chat["busy"]
                or chat["pending"]
                or (chat.get("delivery") or {}).get("state") == "unconfirmed"
            ):
                return self.json_response(
                    409, {"error": "Agent is busy or delivery is unconfirmed"}
                )
            chat["pending"] = {
                "id": str(uuid.uuid4()),
                "text": message,
                "expires": time.time() + 20,
            }
            chat["delivery"] = {"id": chat["pending"]["id"], "state": "queued"}
        return self.json_response(202, {"queued": True})

    def chat_poll(self, data):
        machine = str(uuid.UUID(data.get("machine", "")))
        agent = str(uuid.UUID(data.get("agent", "")))
        generation = str(uuid.UUID(data.get("generation", "")))
        messages = data.get("messages")
        busy = data.get("busy")
        session = data.get("session")
        rename_supported = data.get("rename_supported", False)
        if type(rename_supported) is not bool or (
            rename_supported
            and (not isinstance(session, str) or not session or len(session) > 100)
        ):
            raise ValueError("Invalid rename capability")
        if (
            type(busy) is not bool
            or not isinstance(messages, list)
            or len(messages) > 30
        ):
            raise ValueError("Invalid chat")
        safe = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("text"), str)
                or len(message["text"]) > 4000
            ):
                raise ValueError("Invalid message")
            safe.append({"role": message["role"], "text": message["text"]})
        with LOCK:
            key = (machine, agent)
            source = MACHINES.get(machine)
            if (
                not source
                or not source["daemon_connected"]
                or time.time() - source["last_seen"] >= 20
                or not any(
                    a["agent_id"] == agent and a["source"] == "extension"
                    for a in source["agents"]
                )
            ):
                return self.json_response(409, {"error": "Agent not registered"})
            if key not in CHATS and len(CHATS) >= 1000:
                return self.json_response(429, {"error": "Chat limit reached"})
            previous = CHATS.get(key)
            pending = (
                previous["pending"]
                if previous and previous["generation"] == generation
                else None
            )
            command = (
                {"id": pending["id"], "text": pending["text"]}
                if pending and pending["expires"] > time.time() and not busy
                else None
            )
            delivery = (
                previous.get("delivery")
                if previous and previous["generation"] == generation
                else None
            )
            receipt = data.get("receipt")
            if (
                isinstance(receipt, dict)
                and delivery
                and receipt.get("id") == delivery["id"]
                and receipt.get("state") in {"accepted", "rejected"}
            ):
                delivery = {"id": delivery["id"], "state": receipt["state"]}
            if command:
                delivery = {"id": command["id"], "state": "unconfirmed"}
            elif pending and pending["expires"] <= time.time():
                delivery = {"id": pending["id"], "state": "expired"}
            rename = RENAMES.get(key)
            rename_receipt = data.get("rename_receipt")
            if (
                rename
                and isinstance(rename_receipt, dict)
                and rename_receipt.get("id") == rename["id"]
                and rename_receipt.get("state") in {"accepted", "rejected"}
                and rename["state"] == "unconfirmed"
                and rename["generation"] == generation
            ):
                rename["state"] = rename_receipt["state"]
            rename_command = None
            if rename and rename["state"] == "queued":
                if (
                    rename["expires"] <= time.time()
                    or rename["generation"] != generation
                    or rename["session"] != session
                ):
                    rename["state"] = "expired"
                elif rename_supported:
                    rename_command = {
                        "id": rename["id"],
                        "name": rename["name"],
                        "session": rename["session"],
                    }
                    rename["state"] = "unconfirmed"
                    rename["dispatched_at"] = time.time()
            CHATS[key] = {
                "generation": generation,
                "session": session,
                "rename_supported": rename_supported,
                "messages": safe,
                "seen": time.time(),
                "busy": busy,
                "delivery": delivery,
                "pending": None
                if command or (pending and pending["expires"] <= time.time())
                else pending,
            }
        return self.json_response(200, {"message": command, "rename": rename_command})


def main():
    if len(TOKEN) < 32 or not ADMIN_LOGIN or len(ADMIN_PASSWORD) < 8:
        raise SystemExit(
            "Set PI_KITTY_WEB_TOKEN (32+ characters), ADMIN_LOGIN and ADMIN_PASSWORD (8+ characters)"
        )
    ThreadingHTTPServer(("0.0.0.0", 8500), Handler).serve_forever()


if __name__ == "__main__":
    main()
