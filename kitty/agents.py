"""Custom kitty kitten. Map an absolute path to this file in kitty.conf.

Curses is provided by kitty's embedded Python; the UI has no pip dependencies.
Imports are deliberately inside entry points: custom kittens have no __file__.
"""


def main(args):
    import locale
    import curses
    import os
    from pathlib import Path
    import sys
    import threading

    # kitty's embedded Python starts with LC_CTYPE=C even when LANG is UTF-8.
    # ncurses uses the C locale, not Python's stdout encoding, for Unicode.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        locale.setlocale(locale.LC_CTYPE, "C.UTF-8")

    root = Path(args[0]).resolve().parent.parent
    sys.path.insert(0, str(root))
    from pi_kitty.common import Client, clean, socket_path
    from pi_kitty.view import clip, grouped, short_path, status

    endpoint = os.environ.get("KITTY_LISTEN_ON")
    overlay_id = int(os.environ.get("KITTY_WINDOW_ID", "0"))
    path = socket_path()
    state = {"agents": [], "error": "Подключение…", "warning": "", "connected": False}
    lock = threading.Lock()
    stop = threading.Event()
    refresh = threading.Event()

    def poll():
        announced = False
        while not stop.is_set():
            try:
                client = Client(path)
                warning = ""
                if endpoint and not announced:
                    try:
                        Client(path, timeout=5).request(
                            "kitty.announce", {"endpoint": endpoint}
                        )
                        announced = True
                    except Exception as exc:
                        warning = clean(str(exc), 250)
                health = client.request("health")
                agents = client.agents()
                if not health["kitty"]:
                    warning = "Нет доступных kitty endpoint: проверьте listen_on и Remote Control"
                errors = [i["error"] for i in health["kitty"] if i["error"]]
                warning = (
                    warning
                    or (errors[0] if errors else "")
                    or health.get("last_error")
                    or ""
                )
                with lock:
                    state.update(
                        agents=agents, connected=True, error="", warning=warning
                    )
            except Exception as exc:
                announced = False
                with lock:
                    state.update(
                        connected=False,
                        error=f"Daemon недоступен: {clean(str(exc), 160)}",
                    )
            refresh.wait(1)
            refresh.clear()

    worker = threading.Thread(target=poll, name="pi-kitty-list", daemon=True)
    worker.start()

    def interact(screen):
        curses.curs_set(0)
        curses.set_escdelay(25)
        screen.timeout(100)
        screen.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for number, color in (
                (1, curses.COLOR_GREEN),
                (2, curses.COLOR_YELLOW),
                (3, curses.COLOR_RED),
                (4, curses.COLOR_CYAN),
            ):
                curses.init_pair(number, color, -1)
        query, selected, offset = "", None, 0
        order = {}
        local_error = ""

        def put(y, text, attr=0):
            height, width = screen.getmaxyx()
            if 0 <= y < height and width > 1:
                try:
                    screen.addstr(y, 0, clip(text, width - 1), attr)
                except curses.error:
                    pass  # A concurrent resize can invalidate the last coordinates.

        while True:
            with lock:
                snapshot = dict(state)
            for agent in snapshot["agents"]:
                order.setdefault(agent["agent_id"], len(order))
            rows = grouped(snapshot["agents"], query, order)
            choices = [value for kind, value in rows if kind == "agent"]
            ids = [a["agent_id"] for a in choices]
            if selected not in ids:
                selected = ids[0] if ids else None
            selected_row = next(
                (
                    i
                    for i, (kind, value) in enumerate(rows)
                    if kind == "agent" and value["agent_id"] == selected
                ),
                0,
            )
            height, width = screen.getmaxyx()
            available = max(1, height - 8)
            if selected_row < offset:
                offset = selected_row
            elif selected_row >= offset + available:
                offset = selected_row - available + 1
            offset = min(offset, max(0, len(rows) - available))
            screen.erase()
            busy = sum(a.get("activity") == "busy" for a in choices)
            put(
                0,
                f" Pi agents  ·  {len(choices)} агентов  ·  {busy} работают",
                curses.A_BOLD,
            )
            put(1, f" Поиск: {query}_")
            message = local_error or snapshot["error"] or snapshot["warning"]
            put(2, " " + message, curses.color_pair(3))
            if not rows:
                put(
                    4,
                    " Ничего не найдено"
                    if query
                    else " Нет запущенных pi-агентов в доступных kitty",
                )
            for y, (kind, value) in enumerate(rows[offset : offset + available], 4):
                if kind == "folder":
                    put(
                        y, " " + short_path(value), curses.color_pair(4) | curses.A_BOLD
                    )
                    continue
                agent = value
                label, color = status(agent)
                name = agent.get("session_name") or (
                    "Без названия"
                    if agent["source"] == "extension"
                    else "Без расширения pi"
                )
                location = agent.get("location") or {}
                suffix = (
                    f"  [вкладка {location.get('tab_index', '?')} · панель {location.get('window_id', '?')}]"
                    if width >= 90
                    else ""
                )
                selected_attr = curses.A_REVERSE if agent["agent_id"] == selected else 0
                marker = " › " if selected_attr else "   "
                put(
                    y,
                    f"{marker}{label:<17} {name}{suffix}",
                    curses.color_pair(color) | selected_attr,
                )
            chosen = next((a for a in choices if a["agent_id"] == selected), None)
            if chosen and height >= 8:
                put(height - 3, " " + short_path(chosen.get("cwd", "")), curses.A_DIM)
                loc = chosen.get("location") or {}
                put(
                    height - 2,
                    f" PID {chosen['pid']} · {loc.get('tab_title', 'панель не подтверждена')}",
                    curses.A_DIM,
                )
            put(
                height - 1,
                " ↑↓ выбрать  Enter перейти  Esc закрыть  Ctrl+U очистить  Ctrl+R обновить",
                curses.A_DIM,
            )
            screen.refresh()
            try:
                key = screen.get_wch()
            except curses.error:
                continue
            if key in ("\x1b", "\x03"):
                return None
            if key in ("\n", "\r", curses.KEY_ENTER) and chosen:
                if not snapshot["connected"]:
                    local_error = "Нет связи с daemon; переход запрещён"
                elif (
                    chosen.get("location_status") != "verified"
                    or chosen.get("presence") != "online"
                ):
                    local_error = "Панель или статус агента не подтверждены"
                else:
                    return {
                        "agent_id": selected,
                        "socket": str(path),
                        "overlay": {"endpoint": endpoint, "window_id": overlay_id},
                    }
            elif key in (curses.KEY_UP, curses.KEY_DOWN, "\t", curses.KEY_BTAB):
                if ids:
                    direction = -1 if key in (curses.KEY_UP, curses.KEY_BTAB) else 1
                    selected = ids[(ids.index(selected) + direction) % len(ids)]
                local_error = ""
            elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
                query = query[:-1]
                local_error = ""
            elif key == "\x15":
                query = ""
                local_error = ""
            elif key == "\x12":
                refresh.set()
                local_error = ""
            elif isinstance(key, str) and key.isprintable():
                query = (query + key)[:256]
                local_error = ""

    try:
        return curses.wrapper(interact)
    finally:
        stop.set()
        refresh.set()


def handle_result(args, result, target_window_id, boss):
    if not result:
        return
    import json
    from pathlib import Path
    import subprocess
    from kitty.constants import kitty_exe

    root = str(Path(args[0]).resolve().parent.parent)
    # Do not perform socket IO or wait for a subprocess in kitty's GUI thread.
    # The external client waits until the source overlay disappears from `ls`.
    argv = [
        "pi-kitty",
        "--socket",
        result["socket"],
        "focus",
        result["agent_id"],
        "--overlay",
        json.dumps(result["overlay"]),
    ]
    code = (
        f"import sys; sys.path.insert(0, {root!r}); sys.argv = {argv!r}; "
        "from pi_kitty.cli import main; main()"
    )
    try:
        subprocess.Popen(
            [kitty_exe(), "+runpy", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        boss.show_error("Pi agents", str(exc))
