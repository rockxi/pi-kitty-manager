"""Pure terminal view model; grouping is by exact working directory, not basename."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import unicodedata

from .common import clean
from .i18n import tr


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
    agents: list[dict],
    query: str = "",
    order: dict[str, int] | None = None,
    lang: str | None = None,
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
                agent.get("preview"),
                model.get("id"),
                location.get("tab_title"),
            )
        ).casefold()
        if all(token in haystack for token in tokens):
            groups[agent.get("cwd") or tr("Папка неизвестна", lang)].append(agent)
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


def visual_selection(
    choices: list[dict], anchor: str | None, selected: str | None
) -> set[str]:
    """Select only saved sessions in the inclusive, currently visible range."""
    ids = [item["agent_id"] for item in choices]
    if anchor not in ids or selected not in ids:
        return set()
    start, end = sorted((ids.index(anchor), ids.index(selected)))
    return {
        item["agent_id"]
        for item in choices[start : end + 1]
        if item.get("source") == "history"
    }


def half_page_target(
    rows: list[tuple[str, object]], selected: str | None, height: int, direction: int
) -> str | None:
    """Move by half a viewport in display rows, skipping folder headers and clamping at ends."""
    selectable = [
        (index, value["agent_id"])
        for index, (kind, value) in enumerate(rows)
        if kind == "agent"
    ]
    if not selectable:
        return None
    current = next(
        (i for i, (_, agent_id) in enumerate(selectable) if agent_id == selected), 0
    )
    target = selectable[current][0] + (1 if direction > 0 else -1) * max(1, height // 2)
    if direction > 0:
        return next(
            (agent_id for row, agent_id in selectable if row >= target),
            selectable[-1][1],
        )
    return next(
        (agent_id for row, agent_id in reversed(selectable) if row <= target),
        selectable[0][1],
    )


def status(agent: dict, lang: str | None = None) -> tuple[str, int]:
    if agent.get("source") == "history":
        return tr("История", lang), 4
    if agent.get("presence") != "online":
        return tr("Нет связи", lang), 3
    if agent.get("location_status") != "verified":
        return tr("Нет панели", lang), 3
    if agent.get("activity") == "busy":
        return tr("Работает", lang) + (
            " / tools" if agent.get("phase") == "tools" else ""
        ), 2
    if agent.get("activity") == "idle":
        return tr("Свободен", lang), 1
    return tr("Неизвестно", lang), 3
