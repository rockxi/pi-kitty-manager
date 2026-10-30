"""Opt-in, read-only bridge from the local daemon to a dashboard."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import shutil
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
import uuid

from .common import Client, clean, secure_dir
from .kitty import Kitty


def client_dir():
    base = (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "pi-kitty"
        / "web-client"
    )
    secure_dir(base)
    return base


def machine_id():
    path = client_dir() / "machine-id"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(uuid.uuid4()))
    return str(uuid.UUID(path.read_text().strip()))


def read_token(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith("PI_KITTY_WEB_TOKEN="):
            token = line.partition("=")[2].strip()
            if len(token) >= 32:
                return token
    raise ValueError(
        "Token file must contain PI_KITTY_WEB_TOKEN with at least 32 characters"
    )


def rotating_token(secret, now=None):
    epoch = int((time.time() if now is None else now) // 300)
    signature = hmac.new(
        secret.encode(), str(epoch).encode(), hashlib.sha256
    ).hexdigest()
    return f"v1.{epoch}.{signature}"


def agent_view(agent):
    model = agent.get("model") or {}
    if not isinstance(model, dict):
        model = {}
    return {
        "agent_id": clean(agent.get("agent_id"), 100),
        "cwd": clean(agent.get("cwd"), 1024),
        "session_name": clean(agent.get("session_name"), 500),
        "activity": clean(agent.get("activity"), 30),
        "presence": clean(agent.get("presence"), 30),
        "model": clean(model.get("id"), 150),
        "source": clean(agent.get("source"), 30),
    }


def local_server(url):
    parts = urlsplit(url)
    local_http = (
        parts.scheme == "http"
        and parts.hostname in {"localhost", "127.0.0.1"}
        and parts.port == 8500
    )
    if (
        not parts.hostname
        or (parts.scheme != "https" and not local_http)
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)
    ):
        raise ValueError("Use an HTTPS origin or http://localhost:8500")
    return url.rstrip("/")


def tls_context():
    context = ssl.create_default_context()
    # python.org macOS builds sometimes have no configured CA bundle. Use the
    # macOS system bundle only if the user did not specify a trust store.
    if (
        sys.platform == "darwin"
        and context.cert_store_stats()["x509_ca"] == 0
        and not os.environ.get("SSL_CERT_FILE")
        and not os.environ.get("SSL_CERT_DIR")
    ):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LaunchUnconfirmed(Exception):
    """The kitty request was attempted, but the result may have been lost."""


def launch_agent(cwd, endpoint, server, token_path, chat):
    if (
        not isinstance(cwd, str)
        or not cwd.startswith("/")
        or len(cwd) > 1024
        or any(ord(c) < 32 or ord(c) == 127 for c in cwd)
        or not Path(cwd).is_dir()
    ):
        raise ValueError("Invalid or missing working directory")
    pi = shutil.which("pi")
    if not pi:
        raise ValueError("pi executable not found")
    kitty = Kitty()
    instances = Client(timeout=2).request("health").get("kitty", [])
    healthy = [
        item.get("endpoint")
        for item in instances
        if not item.get("error") and item.get("endpoint")
    ]
    if endpoint not in healthy:
        if len(healthy) != 1:
            raise ValueError("No unambiguous running kitty instance")
        endpoint = healthy[0]
    kitty.endpoint(endpoint)
    args = [
        "launch",
        "--type=tab",
        "--cwd=" + cwd,
        "--env=PI_KITTY_WEB_SERVER=" + server,
        "--env=PI_KITTY_WEB_TOKEN_FILE=" + str(Path(token_path).resolve()),
    ]
    if chat:
        args.append("--env=PI_KITTY_WEB_CHAT=1")
    args.append(pi)
    try:
        return asyncio.run(kitty.call(endpoint, *args, urgent=True)).strip()
    except Exception as exc:
        raise LaunchUnconfirmed("The kitty launch result is unknown") from exc


def run(server, token_path, name, interval, launch_endpoint=None, chat=False):
    server = local_server(server)
    read_token(
        token_path
    )  # Validate before starting; reread to support key replacement.
    ident = machine_id()
    opener = build_opener(
        ProxyHandler({}), HTTPSHandler(context=tls_context()), NoRedirect()
    )
    receipt = None
    while True:
        try:
            agents = [
                agent_view(a)
                for a in Client(timeout=2).agents()
                if a.get("mode") == "tui"
            ]
            connected = True
        except (OSError, ValueError, RuntimeError):
            agents, connected = [], False
        # common.Error is not an OSError; an unavailable daemon must not stop reporting.
        except Exception:
            agents, connected = [], False
        payload = json.dumps(
            {
                "machine_id": ident,
                "name": name,
                "daemon_connected": connected,
                "launch_enabled": bool(launch_endpoint),
                "agents": agents,
            },
            ensure_ascii=False,
        ).encode()
        try:
            token = rotating_token(read_token(token_path))
        except (OSError, ValueError) as exc:
            print(f"Token unavailable: {exc}", flush=True)
            time.sleep(interval)
            continue
        req = Request(
            server + "/api/snapshot",
            data=payload,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        try:
            with opener.open(req, timeout=5) as response:
                response.read()
            if launch_endpoint and connected:
                poll = Request(
                    server + "/api/launch-poll",
                    data=json.dumps({"machine": ident, "receipt": receipt}).encode(),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                with opener.open(poll, timeout=5) as response:
                    result = json.load(response)
                receipt = None
                command = result.get("command")
                if isinstance(command, dict) and isinstance(command.get("id"), str):
                    state = "failed"
                    try:
                        launch_agent(
                            command.get("cwd"),
                            launch_endpoint,
                            server,
                            token_path,
                            chat,
                        )
                        state = "started"
                    except LaunchUnconfirmed:
                        state = None
                        print("Kitty launch result unconfirmed", flush=True)
                    except Exception as exc:
                        print(
                            f"Unable to launch agent: {type(exc).__name__}", flush=True
                        )
                    receipt = {"id": command["id"], "state": state} if state else None
        except HTTPError as exc:
            print(f"Server rejected snapshot: HTTP {exc.code}", flush=True)
        except (OSError, URLError) as exc:
            print(f"Server unavailable: {exc}", flush=True)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="Share pi agent metadata with the local web dashboard (read-only)"
    )
    parser.add_argument("--server", default="http://localhost:8500")
    parser.add_argument(
        "--token-file", required=True, help="Path to .env containing PI_KITTY_WEB_TOKEN"
    )
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument(
        "--launch-endpoint",
        help="Opt in to launching pi tabs at this local kitty Unix endpoint",
    )
    parser.add_argument(
        "--chat", action="store_true", help="Enable web chat in newly launched agents"
    )
    args = parser.parse_args()
    if not args.name.strip() or len(args.name) > 150 or not 2 <= args.interval <= 60:
        parser.error("Name must be 1–150 characters and interval must be 2–60 seconds")
    try:
        lock_path = client_dir() / "client.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return
        try:
            run(
                args.server,
                os.path.expanduser(args.token_file),
                args.name,
                args.interval,
                args.launch_endpoint,
                args.chat,
            )
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
