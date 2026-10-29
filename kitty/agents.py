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
    import time

    # kitty's embedded Python starts with LC_CTYPE=C even when LANG is UTF-8.
    # ncurses uses the C locale, not Python's stdout encoding, for Unicode.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        locale.setlocale(locale.LC_CTYPE, "C.UTF-8")

    root = Path(args[0]).resolve().parent.parent
    sys.path.insert(0, str(root))
    from pi_kitty.common import Client, clean, socket_path
    from pi_kitty.history import hidden_history, list_history
    from pi_kitty.hidden import read_hidden, update_hidden_with_changes
    from pi_kitty.i18n import language, tr, translate_error

    lang = language()

    def _(text, **values):
        return tr(text, lang, **values)

    def error_text(exc):
        return clean(translate_error(str(exc), lang), 250)

    from pi_kitty.view import (
        clip,
        grouped,
        half_page_target,
        short_path,
        status,
        visual_selection,
    )

    endpoint = os.environ.get("KITTY_LISTEN_ON")
    overlay_id = int(os.environ.get("KITTY_WINDOW_ID", "0"))
    path = socket_path()
    state = {
        "agents": [],
        "history": [],
        "history_mode": 0,
        "error": _("Подключение…"),
        "warning": "",
        "connected": False,
    }
    lock = threading.Lock()
    stop = threading.Event()
    refresh = threading.Event()

    def poll():
        announced = False
        history_checked = 0.0
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
                        warning = error_text(exc)
                health = client.request("health")
                agents = client.agents()
                with lock:
                    history_enabled = state["history_mode"] != 0

                if history_enabled and time.monotonic() - history_checked >= 10:
                    history = list_history()
                    history_checked = time.monotonic()
                    with lock:
                        state["history"] = history
                if not health["kitty"]:
                    warning = _(
                        "Нет доступных kitty endpoint: проверьте listen_on и Remote Control"
                    )
                errors = [i["error"] for i in health["kitty"] if i["error"]]
                warning = (
                    warning
                    or (translate_error(errors[0], lang) if errors else "")
                    or translate_error(health.get("last_error") or "", lang)
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
                        error=_("Daemon недоступен: {error}", error=error_text(exc)),
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
        searching = False
        order = {}
        local_error = ""
        visual_anchor = None
        pending_d = 0.0
        undo_stack = []
        hidden = set()
        try:
            hidden = read_hidden()
        except Exception as exc:
            local_error = error_text(exc)

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
            closed = hidden_history(snapshot["history"], snapshot["agents"])
            mode = snapshot["history_mode"]
            if mode == 2:
                visible = [item for item in closed if item["session_file"] in hidden]
            else:
                visible = snapshot["agents"] + (
                    [item for item in closed if item["session_file"] not in hidden]
                    if mode == 1
                    else []
                )
            for agent in visible:
                order.setdefault(agent["agent_id"], len(order))
            rows = grouped(visible, query, order, lang)
            choices = [value for kind, value in rows if kind == "agent"]
            ids = [a["agent_id"] for a in choices]
            if selected not in ids:
                selected = ids[0] if ids else None
            if visual_anchor not in ids:
                visual_anchor = None
            marked = (
                visual_selection(choices, visual_anchor, selected)
                if visual_anchor
                else set()
            )
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
                _(
                    " Pi agents  ·  {count} чатов  ·  {busy} работают  ·  {mode}",
                    count=len(choices),
                    busy=busy,
                    mode=_(("активные", "история", "скрытые")[mode]),
                )
                + (f"  ·  VISUAL {len(marked)}" if visual_anchor else ""),
                curses.A_BOLD,
            )
            put(
                1,
                _(
                    " Поиск: {query}{cursor}",
                    query=query,
                    cursor="_" if searching else "",
                ),
            )
            message = local_error or snapshot["error"] or snapshot["warning"]
            put(2, " " + message, curses.color_pair(3))
            if not rows:
                put(
                    4,
                    _(" Ничего не найдено")
                    if query
                    else _(" Нет запущенных pi-агентов в доступных kitty"),
                )
            for y, (kind, value) in enumerate(rows[offset : offset + available], 4):
                if kind == "folder":
                    put(
                        y, " " + short_path(value), curses.color_pair(4) | curses.A_BOLD
                    )
                    continue
                agent = value
                label, color = status(agent, lang)
                name = (
                    agent.get("session_name")
                    or agent.get("preview")
                    or (
                        _("Без расширения pi")
                        if agent["source"] == "process"
                        else _("Без названия")
                    )
                )
                location = agent.get("location") or {}
                if agent["source"] == "history":
                    from datetime import datetime

                    suffix = (
                        "  ["
                        + datetime.fromtimestamp(agent["modified"]).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        + "]"
                    )
                else:
                    suffix = (
                        _(
                            "  [вкладка {tab} · панель {window}]",
                            tab=location.get("tab_index", "?"),
                            window=location.get("window_id", "?"),
                        )
                        if width >= 90
                        else ""
                    )
                selected_attr = curses.A_REVERSE if agent["agent_id"] == selected else 0
                marker = (" ›" if selected_attr else "  ") + (
                    "* " if agent["agent_id"] in marked else " "
                )
                if agent["agent_id"] in marked:
                    selected_attr |= curses.A_BOLD
                put(
                    y,
                    f"{marker}{label:<17} {name}{suffix}",
                    curses.color_pair(color) | selected_attr,
                )
            chosen = next((a for a in choices if a["agent_id"] == selected), None)
            if chosen and height >= 8 and chosen.get("source") != "history":
                put(height - 3, " " + short_path(chosen.get("cwd", "")), curses.A_DIM)
                loc = chosen.get("location") or {}
                put(
                    height - 2,
                    f" PID {chosen['pid']} · {loc.get('tab_title') or _('панель не подтверждена')}",
                    curses.A_DIM,
                )
            put(
                height - 1,
                _(
                    " j/k выбор  V выделить  dd скрыть  u отменить  / поиск  Ctrl+T режим  Enter открыть  Esc"
                ),
                curses.A_DIM,
            )
            screen.refresh()
            try:
                key = screen.get_wch()
            except curses.error:
                continue
            if key == "\x1b" and searching:
                searching = False
                continue
            if key == "\x1b" and visual_anchor:
                visual_anchor = None
                pending_d = 0.0
                continue
            if key in ("\x1b", "\x03"):
                return None
            if key in ("\x04", "\x15"):
                selected = half_page_target(
                    rows, selected, available, 1 if key == "\x04" else -1
                )
                local_error = ""
                continue
            if key == "\x14":
                with lock:
                    state["history_mode"] = (state["history_mode"] + 1) % 3
                try:
                    hidden = read_hidden()
                    local_error = ""
                except Exception as exc:
                    local_error = error_text(exc)
                visual_anchor = None
                pending_d = 0.0
                refresh.set()
                continue
            if not searching and key == "u":
                pending_d = 0.0
                if not undo_stack:
                    local_error = _("Нечего отменять")
                else:
                    paths, was_hidden = undo_stack[-1]
                    try:
                        hidden, changed = update_hidden_with_changes(
                            paths, hide=not was_hidden
                        )
                        undo_stack.pop()
                        visual_anchor = None
                        local_error = _("Отменено, чатов: {count}", count=len(changed))
                    except Exception as exc:
                        local_error = error_text(exc)
                continue
            if not searching and key == "V":
                if chosen and chosen.get("source") == "history":
                    visual_anchor = None if visual_anchor else selected
                    local_error = ""
                else:
                    local_error = _("Выберите закрытую сессию из истории")
                pending_d = 0.0
                continue
            if not searching and key == "d":
                now = time.monotonic()
                if visual_anchor or (pending_d and now - pending_d <= 1.0):
                    targets = (
                        marked
                        if visual_anchor
                        else (
                            {selected}
                            if chosen and chosen.get("source") == "history"
                            else set()
                        )
                    )
                    paths = [
                        item["session_file"]
                        for item in choices
                        if item["agent_id"] in targets
                        and item.get("source") == "history"
                    ]
                    if not paths:
                        local_error = _("Скрывать можно только закрытые сессии")
                    else:
                        try:
                            was_hidden = mode != 2
                            hidden, changed = update_hidden_with_changes(
                                paths, hide=was_hidden
                            )
                            if changed:
                                undo_stack.append((sorted(changed), was_hidden))
                                del undo_stack[:-100]
                            local_error = _(
                                "Скрыто чатов: {count}"
                                if was_hidden
                                else "Восстановлено чатов: {count}",
                                count=len(changed),
                            )
                            visual_anchor = None
                        except Exception as exc:
                            local_error = error_text(exc)
                    pending_d = 0.0
                else:
                    pending_d = now
                    local_error = _("d … нажмите d ещё раз")
                continue
            pending_d = 0.0
            if key == "/" and not searching:
                searching = True
                continue
            if key in ("j", "k") and not searching:
                if ids:
                    direction = 1 if key == "j" else -1
                    selected = ids[(ids.index(selected) + direction) % len(ids)]
                local_error = ""
                continue
            if key in ("\n", "\r", curses.KEY_ENTER) and chosen:
                if not snapshot["connected"]:
                    local_error = _("Нет связи с daemon; переход запрещён")
                elif (
                    chosen.get("source") == "history"
                    and not Path(chosen["session_file"]).is_file()
                ):
                    local_error = _("Сессия больше не существует; обновите список")
                elif (
                    chosen.get("source") == "history"
                    and not Path(chosen["cwd"]).is_dir()
                ):
                    local_error = _("Рабочая папка сессии больше не существует")
                elif chosen.get("source") == "history":
                    return {
                        "session_file": chosen["session_file"],
                        "socket": str(path),
                        "overlay": {"endpoint": endpoint, "window_id": overlay_id},
                    }
                elif (
                    chosen.get("location_status") != "verified"
                    or chosen.get("presence") != "online"
                ):
                    local_error = _("Панель или статус агента не подтверждены")
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
            elif key == "\x0c":
                query = ""
                local_error = ""
            elif key == "\x12":
                refresh.set()
                local_error = ""
            elif searching and isinstance(key, str) and key.isprintable():
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
    argv = ["pi-kitty", "--socket", result["socket"]]
    if result.get("session_file"):
        argv += ["resume", result["session_file"]]
    else:
        argv += ["focus", result["agent_id"]]
    argv += ["--overlay", json.dumps(result["overlay"])]
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
