"""Persistent, non-destructive visibility preferences for session files."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile

from .common import Error, secure_dir


def hidden_path() -> Path:
    config = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    return config / "pi-kitty" / "hidden-sessions.json"


def read_hidden(path: Path | None = None) -> set[str]:
    path = path or hidden_path()
    try:
        if path.is_symlink():
            raise Error(
                "PERMISSION_DENIED", "Файл скрытых чатов не должен быть ссылкой"
            )
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return set()
    except (ValueError, UnicodeError) as exc:
        raise Error(
            "INVALID_HIDDEN_FILE", "Повреждён файл скрытых чатов; данные не изменены"
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("sessions"), list)
        or not all(
            isinstance(p, str) and Path(p).is_absolute() for p in data["sessions"]
        )
    ):
        raise Error(
            "INVALID_HIDDEN_FILE",
            "Неверный формат файла скрытых чатов; данные не изменены",
        )
    return set(data["sessions"])


def update_hidden(paths: list[str], hide: bool, path: Path | None = None) -> set[str]:
    entries, _ = update_hidden_with_changes(paths, hide, path)
    return entries


def update_hidden_with_changes(
    paths: list[str], hide: bool, path: Path | None = None
) -> tuple[set[str], set[str]]:
    """Atomically update visibility and report only paths actually changed."""
    path = path or hidden_path()
    secure_dir(path.parent)
    normalized = {str(Path(p).resolve()) for p in paths if Path(p).is_absolute()}
    if len(normalized) != len(set(paths)):
        raise Error("INVALID_REQUEST", "Неверный путь сессии")
    fd = os.open(
        path.parent / "hidden-sessions.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    tmp = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        entries = read_hidden(path)
        changed = normalized - entries if hide else normalized & entries
        entries = entries | normalized if hide else entries - normalized
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".hidden-", delete=False
        ) as stream:
            tmp = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(
                {"version": 1, "sessions": sorted(entries)},
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        tmp = None
        return entries, changed
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        os.close(fd)
