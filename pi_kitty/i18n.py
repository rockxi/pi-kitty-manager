"""Language selection and translations for user-facing text."""

from __future__ import annotations

import os
from pathlib import Path
import tomllib


EN = {
    "Подключение…": "Connecting…",
    "Нет доступных kitty endpoint: проверьте listen_on и Remote Control": "No kitty endpoints available: check listen_on and Remote Control",
    "Daemon недоступен: {error}": "Daemon unavailable: {error}",
    " Pi agents  ·  {count} чатов  ·  {busy} работают  ·  {mode}": " Pi agents  ·  {count} chats  ·  {busy} busy  ·  {mode}",
    "активные": "active",
    "история": "history",
    "скрытые": "hidden",
    " Поиск: {query}{cursor}": " Search: {query}{cursor}",
    " Ничего не найдено": " No matches found",
    " Нет запущенных pi-агентов в доступных kitty": " No running pi agents in available kitty instances",
    "Без расширения pi": "Without pi extension",
    "Без названия": "Untitled",
    "  [вкладка {tab} · панель {window}]": "  [tab {tab} · window {window}]",
    "панель не подтверждена": "window unverified",
    " j/k выбор  V выделить  dd скрыть  u отменить  / поиск  Ctrl+T режим  Enter открыть  Esc": " j/k select  V visual  dd hide  u undo  / search  Ctrl+T view  Enter open  Esc",
    "Нечего отменять": "Nothing to undo",
    "Отменено, чатов: {count}": "Undone, chats: {count}",
    "Выберите закрытую сессию из истории": "Select a closed session from history",
    "Скрывать можно только закрытые сессии": "Only closed sessions can be hidden",
    "Скрыто чатов: {count}": "Hidden chats: {count}",
    "Восстановлено чатов: {count}": "Restored chats: {count}",
    "d … нажмите d ещё раз": "d … press d again",
    "Нет связи с daemon; переход запрещён": "Daemon disconnected; switching is unavailable",
    "Сессия больше не существует; обновите список": "Session no longer exists; refresh the list",
    "Рабочая папка сессии больше не существует": "Session working directory no longer exists",
    "Панель или статус агента не подтверждены": "Agent window or status is unverified",
    "Папка неизвестна": "Unknown folder",
    "История": "History",
    "Нет связи": "Offline",
    "Нет панели": "No window",
    "Работает": "Busy",
    "Свободен": "Idle",
    "Неизвестно": "Unknown",
    "Файл скрытых чатов не должен быть ссылкой": "The hidden chats file must not be a symlink",
    "Повреждён файл скрытых чатов; данные не изменены": "The hidden chats file is corrupt; data was not changed",
    "Неверный формат файла скрытых чатов; данные не изменены": "Invalid hidden chats file format; data was not changed",
    "Неверный путь сессии": "Invalid session path",
    "Список меняется; повторите обновление": "The list is changing; refresh again",
    "Список агентов ещё открыт; переход отменён": "Agent list is still open; switching cancelled",
    "У агента нет подтверждённого экземпляра kitty": "Agent has no verified kitty instance",
    "Нет свежего статуса агента; дождитесь переподключения": "Agent status is stale; wait for reconnection",
    "Панель агента не подтверждена или kitty недоступен": "Agent window is unverified or kitty is unavailable",
    "Процесс больше не принадлежит выбранной панели": "Process no longer belongs to the selected window",
    "Kitty не подтвердил активацию панели": "Kitty did not confirm window activation",
    "Истекло время переключения на агента": "Timed out switching to agent",
    "Агент уже завершён или потеряна связь": "Agent has exited or disconnected",
    "Некорректный путь сессии": "Invalid session path",
    "Сессия вне настроенных каталогов истории": "Session is outside configured history directories",
    "Сессия удалена или повреждена": "Session was deleted or is corrupt",
    "Нет исходного окна kitty": "No source kitty window",
    "Панель ещё открыта": "Window is still open",
    "Сессия уже открыта, но её панель недоступна": "Session is already open but its window is unavailable",
    "Команда pi не найдена в PATH": "pi command not found in PATH",
    "Не собственный Unix socket": "Unix socket is not owned by the current user",
    "Сообщение превышает 64 KiB": "Message exceeds 64 KiB",
    "Неполный или слишком большой ответ daemon": "Daemon response is incomplete or too large",
    "Некорректный overlay": "Invalid overlay",
    "Некорректный offset": "Invalid offset",
    "Неизвестный метод": "Unknown method",
    "Слишком большое сообщение": "Message is too large",
    "Ожидается JSON object": "Expected a JSON object",
    "Некорректный request id": "Invalid request id",
    "Нужна версия протокола 1": "Protocol version 1 is required",
    "params должен быть object": "params must be an object",
    "Слишком длинный или относительный путь PI_KITTY_SOCKET": "PI_KITTY_SOCKET must be an absolute path of an appropriate length",
    "Чужой lock file": "Lock file is owned by another user",
    "Daemon уже запущен": "Daemon is already running",
    "Unix socket занят работающим сервером": "Unix socket is in use by a running server",
    "Слишком много экземпляров kitty": "Too many kitty instances",
    "Вывод дочернего процесса превысил лимит": "Child process output exceeded the limit",
    "Нужен абсолютный локальный endpoint unix:/path": "An absolute local unix:/path endpoint is required",
    "Unix socket должен иметь абсолютный путь": "Unix socket must have an absolute path",
    "Некорректное дерево kitty": "Invalid kitty window tree",
    "LaunchAgent поддерживается только на macOS; используйте daemon run": "LaunchAgent is only supported on macOS; use daemon run",
    "LaunchAgent удалён. Настройки pi и kitty не изменены.": "LaunchAgent removed. pi and kitty settings were not changed.",
    "LaunchAgent уже существует: {path}": "LaunchAgent already exists: {path}",
    "LaunchAgent установлен: {path}": "LaunchAgent installed: {path}",
    "Переключатель только pi-агентов в kitty, сгруппированных по папкам": "Switch between pi agents in kitty, grouped by folder",
    "Переопределить Unix socket daemon": "Override the daemon Unix socket",
    "Ротируемый лог: 3 файла по 1 MiB": "Rotating log: 3 files of 1 MiB",
    "Запустить в фоне, только если daemon ещё не работает": "Start in the background only if the daemon is not already running",
    "Открыть сохранённую сессию в kitty": "Open a saved session in kitty",
    "Daemon работает · PID {pid}": "Daemon running · PID {pid}",
    "  {label:<17} {name}  [вкладка {tab}, панель {window}]\n    {agent}": "  {label:<17} {name}  [tab {tab}, window {window}]\n    {agent}",
    "Нет pi-агентов в доступных kitty. Запустите pi или проверьте doctor.": "No pi agents in available kitty instances. Start pi or run doctor.",
    "не найден в PATH": "not found in PATH",
    "Предупреждение kitty: {error}": "kitty warning: {error}",
    "Daemon: OK · агентов {agents} · с расширением {extensions}": "Daemon: OK · agents {agents} · with extension {extensions}",
    "Нет известных kitty. Нужны listen_on + socket-only и перезапуск kitty.": "No known kitty instances. Configure listen_on + socket-only and restart kitty.",
    "  {endpoint}: {error} · панелей {windows}": "  {endpoint}: {error} · windows {windows}",
    "Для статусов и названий установите pi-extension и выполните /reload в каждом pi.": "For statuses and names, install pi-extension and run /reload in each pi.",
    "Последняя ошибка перехода: {error}": "Last switching error: {error}",
    "Запустите: pi-kitty daemon run (или pi-kitty service install)": "Run: pi-kitty daemon run (or pi-kitty service install)",
}


def language() -> str:
    """Explicit environment overrides config; Russian preserves existing installs."""
    value = os.environ.get("PI_KITTY_LANG")
    if value is None:
        path = (
            Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "pi-kitty/config.toml"
        )
        try:
            with path.open("rb") as stream:
                value = tomllib.load(stream).get("language", "ru")
        except (OSError, ValueError):
            value = "ru"
    return (
        "en"
        if isinstance(value, str) and value.lower().split("_")[0].split("-")[0] == "en"
        else "ru"
    )


def tr(text: str, lang: str | None = None, **values: object) -> str:
    translated = EN.get(text, text) if (lang or language()) == "en" else text
    return translated.format(**values) if values else translated


def translate_error(text: str, lang: str | None = None) -> str:
    """Translate known errors without touching paths, user data or OS messages."""
    if (lang or language()) != "en":
        return text
    if text in EN:
        return EN[text]
    for source, target in (
        (
            "Нужен собственный каталог с правами 0700: ",
            "An owned directory with 0700 permissions is required: ",
        ),
        (
            "Не собственный Unix socket: ",
            "Unix socket is not owned by the current user: ",
        ),
        ("Daemon не запустился; проверьте ", "Daemon failed to start; check "),
    ):
        if text.startswith(source):
            return target + text[len(source) :]
    return text
