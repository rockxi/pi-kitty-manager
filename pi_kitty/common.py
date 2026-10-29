"""Shared paths, framing and safe display helpers (also imported by kitty Python)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import unicodedata

MAX_LINE = 65536
PAGE_SIZE = 4
ANSI = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])")


class Error(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clean(value: object, limit: int = 2048) -> str:
    text = ANSI.sub("", str(value or ""))
    return "".join(c for c in text if not unicodedata.category(c).startswith("C"))[
        :limit
    ]


def runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        base = Path(xdg)
        try:
            s = base.stat()
            if base.is_absolute() and s.st_uid == os.getuid() and not s.st_mode & 0o077:
                return base / "pi-kitty"
        except OSError:
            pass
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/pi-kitty/run"
    return (
        Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        / "pi-kitty/run"
    )


def socket_path() -> Path:
    return Path(
        os.environ.get("PI_KITTY_SOCKET", str(runtime_dir() / "daemon.sock"))
    ).expanduser()


def secure_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise Error(
            "PERMISSION_DENIED", f"Нужен собственный каталог с правами 0700: {path}"
        )


def validate_socket(path: Path) -> None:
    s = path.lstat()
    if not stat.S_ISSOCK(s.st_mode) or s.st_uid != os.getuid():
        raise Error("PERMISSION_DENIED", f"Не собственный Unix socket: {path}")


def encode(message: dict) -> bytes:
    data = (
        json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    )
    if len(data) > MAX_LINE:
        raise Error("INVALID_REQUEST", "Сообщение превышает 64 KiB")
    return data


class Client:
    def __init__(self, path: Path | None = None, timeout: float = 2):
        self.path = path or socket_path()
        self.timeout = timeout

    def request(self, method: str, params: dict | None = None):
        validate_socket(self.path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.path))
            sock.sendall(
                encode({"v": 1, "id": "cli", "method": method, "params": params or {}})
            )
            with sock.makefile("rb") as stream:
                line = stream.readline(MAX_LINE + 1)
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            raise Error("INVALID_RESPONSE", "Неполный или слишком большой ответ daemon")
        response = json.loads(line)
        if "error" in response:
            raise Error(response["error"]["code"], response["error"]["message"])
        return response["result"]

    def agents(self, include_stale: bool = False) -> list[dict]:
        # Each page is bounded; revisions prevent mixing two different snapshots.
        for _ in range(3):
            result, offset, revision = [], 0, None
            while True:
                page = self.request(
                    "agents.list", {"offset": offset, "include_stale": include_stale}
                )
                if revision is not None and page["revision"] != revision:
                    break
                revision = page["revision"]
                result.extend(page["agents"])
                offset = page["next_offset"]
                if offset is None:
                    return result
        raise Error("REGISTRY_CHANGED", "Список меняется; повторите обновление")
