"""Pure terminal view model; grouping is by exact working directory, not basename."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import unicodedata

from .common import clean


def clip(text: str, width: int) -> str:
    result, used = [], 0
    for char in clean(text, 8192):
        size = (
            0
            if unicodedata.combining(char)
            else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        )
        if used + size > max(0, width):
            break
        result.append(char)
        used += size
    return "".join(result)


def short_path(path: str) -> str:
    home = str(Path.home())
    return (
        "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path
    )


def grouped(
    agents: list[dict], query: str = "", order: dict[str, int] | None = None
) -> list[tuple[str, object]]:
    groups = defaultdict(list)
    tokens = query.casefold().split()
    for agent in agents:
        if (
            agent.get("mode") != "tui"
            or agent.get("location_status") == "outside_kitty"
        ):
            continue
        location = agent.get("location") or {}
        model = agent.get("model") or {}
        haystack = " ".join(
            str(x or "")
            for x in (
                agent.get("cwd"),
                agent.get("session_name"),
                model.get("id"),
                location.get("tab_title"),
            )
        ).casefold()
        if all(token in haystack for token in tokens):
            groups[agent.get("cwd") or "Папка неизвестна"].append(agent)
    rows = []
    for folder in sorted(groups, key=str.casefold):
        rows.append(("folder", folder))
        items = groups[folder]
        if order is None:
            items.sort(key=lambda a: (-a.get("mru", 0), a["agent_id"]))
        else:
            items.sort(key=lambda a: (order.get(a["agent_id"], 10**9), a["agent_id"]))
        rows.extend(("agent", agent) for agent in items)
    return rows


def status(agent: dict) -> tuple[str, int]:
    if agent.get("presence") != "online":
        return "Нет связи", 3
    if agent.get("location_status") != "verified":
        return "Нет панели", 3
    if agent.get("activity") == "busy":
        return "Работает" + (" / tools" if agent.get("phase") == "tools" else ""), 2
    if agent.get("activity") == "idle":
        return "Свободен", 1
    return "Неизвестно", 3
