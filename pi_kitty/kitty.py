"""Bounded subprocess adapter. No shell and no terminal content collection."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil

from .common import Error, clean, validate_socket
from .processes import Process, is_descendant, is_pi, parse_processes


async def run(argv: list[str], timeout: float = 3, limit: int = 4 * 1024 * 1024) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
        limit=65536,
    )

    async def bounded(stream, maximum):
        parts, size = [], 0
        while chunk := await stream.read(16384):
            size += len(chunk)
            if size > maximum:
                raise Error("OUTPUT_LIMIT", "Вывод дочернего процесса превысил лимит")
            parts.append(chunk)
        return b"".join(parts)

    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await asyncio.gather(
                bounded(proc.stdout, limit), bounded(proc.stderr, 16384)
            )
            await proc.wait()
        if proc.returncode:
            raise Error(
                "KITTY_UNREACHABLE", clean(stderr.decode(errors="replace"), 300)
            )
        return stdout.decode("utf-8")
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise


async def process_table() -> dict[int, Process]:
    return parse_processes(
        await run(
            ["/bin/ps", "-axo", "pid=,ppid=,lstart=,comm="], limit=8 * 1024 * 1024
        )
    )


class Kitty:
    def __init__(self, command: str | None = None):
        self.command = (
            command
            or shutil.which("kitty")
            or "/Applications/kitty.app/Contents/MacOS/kitty"
        )
        self.semaphore = asyncio.Semaphore(4)

    @staticmethod
    def endpoint(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value.startswith("unix:/")
            or len(value) > 1000
        ):
            raise Error(
                "INVALID_REQUEST", "Нужен абсолютный локальный endpoint unix:/path"
            )
        path = Path(value[5:])
        if not path.is_absolute():
            raise Error("INVALID_REQUEST", "Unix socket должен иметь абсолютный путь")
        validate_socket(path)
        return value

    async def call(self, endpoint: str, *args: str, urgent: bool = False) -> str:
        self.endpoint(endpoint)  # Recheck ownership/type on every use.
        argv = [self.command, "@", "--to", endpoint, *args]
        # One serialized focus request may bypass the four background slots, so a
        # dead unrelated instance cannot starve switching to a healthy terminal.
        if urgent:
            return await run(argv)
        async with self.semaphore:
            return await run(argv)

    async def topology(self, endpoint: str, urgent: bool = False) -> list[dict]:
        tree = json.loads(await self.call(endpoint, "ls", urgent=urgent))
        if not isinstance(tree, list):
            raise Error("INVALID_RESPONSE", "Некорректное дерево kitty")
        result = []
        for os_window in tree:
            for index, tab in enumerate(os_window.get("tabs", []), 1):
                for window in tab.get("windows", []):
                    # Deliberately discard env and other unnecessary fields immediately.
                    result.append(
                        {
                            "os_window_id": int(os_window["id"]),
                            "tab_id": int(tab["id"]),
                            "tab_index": index,
                            "tab_title": clean(tab.get("title"), 256),
                            "window_id": int(window["id"]),
                            "pid": int(window.get("pid", 0)),
                            "foreground": [
                                {
                                    "pid": int(p.get("pid", 0)),
                                    "cwd": clean(p.get("cwd")),
                                    "is_pi": is_pi(p.get("cmdline")),
                                }
                                for p in window.get("foreground_processes", [])
                            ],
                            "cwd": clean(window.get("cwd")),
                            "active": bool(window.get("is_active"))
                            and bool(tab.get("is_active")),
                        }
                    )
        return result


def contains_process(window: dict, pid: int, processes: dict[int, Process]) -> bool:
    # A claimed window ID alone is insufficient; PID must belong to that PTY tree.
    root = window["pid"]
    return root > 1 and is_descendant(pid, root, processes)
