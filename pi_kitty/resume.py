"""Resume a saved session after the kitty overlay has closed."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import time

from .common import Client, Error
from .history import read_session, session_roots
from .kitty import Kitty


async def resume(
    session_file: str, overlay: dict, client: Client, kitty: Kitty | None = None
):
    path = Path(session_file)
    if not path.is_absolute() or path.is_symlink():
        raise Error("INVALID_REQUEST", "Некорректный путь сессии")
    path = path.resolve()
    if not any(path.is_relative_to(root.resolve()) for root in session_roots()):
        raise Error("PERMISSION_DENIED", "Сессия вне настроенных каталогов истории")
    session = await asyncio.to_thread(read_session, path)
    if not session:
        raise Error("SESSION_NOT_FOUND", "Сессия удалена или повреждена")
    if not Path(session["cwd"]).is_dir():
        raise Error("CWD_NOT_FOUND", "Рабочая папка сессии больше не существует")
    if not isinstance(overlay, dict) or type(overlay.get("window_id")) is not int:
        raise Error("INVALID_REQUEST", "Нет исходного окна kitty")
    kitty = kitty or Kitty()
    endpoint = kitty.endpoint(overlay.get("endpoint"))
    deadline = time.monotonic() + 3
    while any(
        w["window_id"] == overlay["window_id"]
        for w in await kitty.topology(endpoint, urgent=True)
    ):
        if time.monotonic() >= deadline:
            raise Error("FOCUS_FAILED", "Панель ещё открыта")
        await asyncio.sleep(0.05)
    agents = await asyncio.to_thread(client.agents)
    matching = [
        a
        for a in agents
        if a.get("session_file") and Path(a["session_file"]).resolve() == path
    ]
    matching += [
        a
        for a in agents
        if not a.get("session_file")
        and a.get("session_id") == session["session_id"]
        and a.get("cwd") == session["cwd"]
    ]
    if matching:
        agent = matching[0]
        if (
            agent.get("location_status") != "verified"
            or agent.get("presence") != "online"
        ):
            raise Error(
                "LOCATION_UNVERIFIED", "Сессия уже открыта, но её панель недоступна"
            )
        return await asyncio.to_thread(
            client.request, "agents.focus", {"agent_id": agent["agent_id"]}
        )
    executable = shutil.which("pi")
    if not executable:
        raise Error("PI_NOT_FOUND", "Команда pi не найдена в PATH")
    window_id = await kitty.call(
        endpoint,
        "launch",
        "--type=tab",
        "--cwd",
        session["cwd"],
        executable,
        "--session",
        str(path),
        urgent=True,
    )
    return {"opened": window_id.strip(), "session_id": session["session_id"]}
