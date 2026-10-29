"""Read pi session metadata locally without modifying the conversation files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .common import clean

MAX_LINE = 2 * 1024 * 1024


def session_roots() -> list[Path]:
    agent = Path(
        os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent"))
    ).expanduser()
    roots = [agent / "sessions"]
    custom = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if custom:
        roots.append(Path(custom).expanduser())
    roots.extend(
        Path(p).expanduser()
        for p in os.environ.get("PI_KITTY_SESSION_DIRS", "").split(os.pathsep)
        if p
    )
    return list(dict.fromkeys(p.resolve() for p in roots))


def read_session(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            first = stream.readline(MAX_LINE + 1)
            if len(first) > MAX_LINE:
                return None
            header = json.loads(first)
            if not isinstance(header, dict) or header.get("type") != "session":
                return None
            cwd, session_id = header.get("cwd"), header.get("id")
            if (
                not isinstance(cwd, str)
                or not Path(cwd).is_absolute()
                or not isinstance(session_id, str)
                or not session_id
            ):
                return None
            name = None
            preview = ""
            while True:
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_LINE + 1)
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "session_info":
                    name = clean(entry.get("name"), 256) or None
                if not preview and entry.get("type") == "message":
                    message = entry.get("message")
                    if not isinstance(message, dict) or message.get("role") != "user":
                        continue
                    content = message.get("content", "")
                    if isinstance(content, str):
                        preview = content
                    elif isinstance(content, list):
                        preview = " ".join(
                            b.get("text", "")
                            for b in content
                            if isinstance(b, dict)
                            and b.get("type") == "text"
                            and isinstance(b.get("text"), str)
                        )
            absolute = str(path.resolve())
            return {
                "agent_id": "history-"
                + hashlib.sha256(os.fsencode(absolute)).hexdigest(),
                "source": "history",
                "session_file": absolute,
                "session_id": clean(session_id, 128),
                "session_name": name,
                "preview": clean(preview, 160),
                "cwd": clean(cwd),
                "modified": path.stat().st_mtime,
                "mode": "tui",
                "location_status": "history",
                "presence": "offline",
                "activity": "unknown",
                "model": {},
                "mru": 0,
            }
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def list_history(roots: list[Path] | None = None) -> list[dict]:
    sessions = []
    seen = set()
    for root in roots if roots is not None else session_roots():
        try:
            # Defaults are grouped one level down; custom session directories are flat.
            paths = list(root.glob("*.jsonl")) + list(root.glob("*/*.jsonl"))
            for path in paths:
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                item = read_session(path)
                if item:
                    sessions.append(item)
        except OSError:
            continue
    return sorted(sessions, key=lambda s: s["modified"], reverse=True)


def hidden_history(history: list[dict], agents: list[dict]) -> list[dict]:
    # Match by actual file identity, not session ID: forks/copies can share IDs.
    active = {
        str(Path(a["session_file"]).resolve()) for a in agents if a.get("session_file")
    }
    legacy = {
        (a.get("session_id"), a.get("cwd"))
        for a in agents
        if not a.get("session_file") and a.get("session_id")
    }
    return [
        item
        for item in history
        if item["session_file"] not in active
        and (item["session_id"], item["cwd"]) not in legacy
    ]
