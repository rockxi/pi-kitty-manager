"""Opt-in, read-only bridge from the local daemon to a local web server."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

from .common import Client, clean, secure_dir


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
    if (
        parts.scheme != "http"
        or parts.hostname not in {"localhost", "127.0.0.1"}
        or parts.port != 8500
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "Only http://localhost:8500 or http://127.0.0.1:8500 is supported; use an SSH tunnel for another machine"
        )
    return url.rstrip("/")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(server, token_path, name, interval):
    server = local_server(server)
    read_token(
        token_path
    )  # Validate before starting; reread to support key replacement.
    ident = machine_id()
    opener = build_opener(ProxyHandler({}), NoRedirect())
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
            )
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
