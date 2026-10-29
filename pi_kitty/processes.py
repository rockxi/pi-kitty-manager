"""Process identity uses the OS start time, never just a reusable PID."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    started: str
    command: str


def parse_processes(text: str) -> dict[int, Process]:
    result = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.{24})\s+(.+)$", line)
        if match:
            pid, ppid, started, command = match.groups()
            result[int(pid)] = Process(int(pid), int(ppid), started, command.strip())
    return result


def is_descendant(pid: int, ancestor: int, processes: dict[int, Process]) -> bool:
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        proc = processes.get(pid)
        if not proc:
            return False
        pid = proc.ppid
    return False


def is_pi(argv: object) -> bool:
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) for a in argv)
    ):
        return False
    executable = os.path.basename(argv[0])
    if executable == "pi":
        return True
    if executable not in {"node", "nodejs", "bun"}:
        return False
    # Only the script argument, not arbitrary arguments of unrelated commands.
    script = next((x for x in argv[1:] if x and not x.startswith("-")), "")
    normalized = script.replace("\\", "/")
    return normalized.endswith(
        ("/pi-coding-agent/dist/cli.js", "/pi-coding-agent/dist/index.js")
    )
