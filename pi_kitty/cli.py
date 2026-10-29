from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tomllib

from . import __version__
from .common import Client, Error, clean, secure_dir, socket_path
from .view import grouped, short_path, status

LABEL = "local.pi-kitty.agents"


def config() -> dict:
    path = (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "pi-kitty/config.toml"
    )
    if path.exists():
        with path.open("rb") as stream:
            return tomllib.load(stream)
    return {}


def service(action: str):
    if sys.platform != "darwin":
        raise Error(
            "UNSUPPORTED_PLATFORM",
            "LaunchAgent поддерживается только на macOS; используйте daemon run",
        )
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if action == "uninstall":
        subprocess.run(
            ["launchctl", "bootout", domain, str(plist)], capture_output=True
        )
        if plist.exists():
            plist.unlink()
        print("LaunchAgent удалён. Настройки pi и kitty не изменены.")
        return
    if plist.exists():
        raise Error("ALREADY_INSTALLED", f"LaunchAgent уже существует: {plist}")
    secure_dir(socket_path().parent)
    logs = Path.home() / "Library/Logs/pi-kitty"
    secure_dir(logs)
    plist.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": LABEL,
        "ProgramArguments": [
            os.path.realpath(sys.executable),
            "-m",
            "pi_kitty",
            "--socket",
            str(socket_path()),
            "daemon",
            "run",
            "--log-file",
            str(logs / "daemon.log"),
        ],
        "EnvironmentVariables": {
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    with plist.open("xb") as stream:
        plistlib.dump(data, stream)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True
    )
    if result.returncode:
        plist.unlink()
        raise Error("SERVICE_FAILED", clean(result.stderr, 500))
    print(f"LaunchAgent установлен: {plist}")


def announce_current(client: Client):
    endpoint = os.environ.get("KITTY_LISTEN_ON")
    if endpoint:
        Client(client.path, timeout=5).request("kitty.announce", {"endpoint": endpoint})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Переключатель только pi-агентов в kitty, сгруппированных по папкам"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--socket", type=Path, help="Переопределить Unix socket daemon")
    commands = p.add_subparsers(dest="command", required=True)
    daemon = commands.add_parser("daemon").add_subparsers(dest="action", required=True)
    run = daemon.add_parser("run")
    run.add_argument("--kitty-command")
    run.add_argument("--log-file", type=Path, help="Ротируемый лог: 3 файла по 1 MiB")
    run.add_argument("--endpoint", action="append", default=[])
    ensure = daemon.add_parser(
        "ensure", help="Запустить в фоне, только если daemon ещё не работает"
    )
    ensure.add_argument("--endpoint")
    ensure.add_argument("--kitty-command")
    ensure.set_defaults(log_file=None)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--include-stale", action="store_true")
    focus = commands.add_parser("focus")
    focus.add_argument("agent_id")
    focus.add_argument("--overlay", type=json.loads, help=argparse.SUPPRESS)
    commands.add_parser("doctor")
    svc = commands.add_parser("service")
    svc.add_argument("action", choices=["install", "uninstall"])
    return p


def main():
    args = parser().parse_args()
    if args.socket:
        os.environ["PI_KITTY_SOCKET"] = str(args.socket.expanduser().absolute())
    try:
        if args.command == "daemon" and args.action == "ensure":
            from .startup import ensure

            health = ensure(
                args.endpoint or os.environ.get("KITTY_LISTEN_ON"), args.kitty_command
            )
            print(f"Daemon работает · PID {health['pid']}")
            return
        if args.command == "daemon":
            from .daemon import Daemon

            handlers = [logging.StreamHandler()]
            if args.log_file:
                secure_dir(args.log_file.parent)
                handlers = [
                    RotatingFileHandler(
                        args.log_file,
                        maxBytes=1024 * 1024,
                        backupCount=2,
                        encoding="utf-8",
                    )
                ]
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                handlers=handlers,
            )
            cfg = config().get("kitty", {})
            endpoints = [*cfg.get("endpoints", []), *args.endpoint]
            if os.environ.get("KITTY_LISTEN_ON"):
                endpoints.append(os.environ["KITTY_LISTEN_ON"])
            asyncio.run(
                Daemon(
                    command=args.kitty_command or cfg.get("command"),
                    endpoints=endpoints,
                ).serve()
            )
        elif args.command == "service":
            service(args.action)
        elif args.command == "focus":
            params = {"agent_id": args.agent_id}
            if args.overlay:
                params["overlay"] = args.overlay
            Client(timeout=7).request("agents.focus", params)
        elif args.command == "list":
            client = Client()
            with contextlib.suppress(OSError, Error):
                announce_current(client)
            agents = client.agents(args.include_stale)
            if args.json:
                print(json.dumps(agents, ensure_ascii=False, indent=2))
            else:
                rows = grouped(agents)
                for kind, value in rows:
                    if kind == "folder":
                        print(short_path(value))
                    else:
                        label, _ = status(value)
                        name = value.get("session_name") or (
                            "Без названия"
                            if value["source"] == "extension"
                            else "Без расширения pi"
                        )
                        loc = value.get("location") or {}
                        print(
                            f"  {label:<17} {name}  [вкладка {loc.get('tab_index', '?')}, панель {loc.get('window_id', '?')}]\n    {value['agent_id']}"
                        )
                if not rows:
                    print(
                        "Нет pi-агентов в доступных kitty. Запустите pi или проверьте doctor."
                    )
        elif args.command == "doctor":
            print(f"pi-kitty {__version__} · Python {sys.version.split()[0]}")
            print(f"Socket: {socket_path()}")
            print(f"Kitty: {shutil.which('kitty') or 'не найден в PATH'}")
            print(f"Pi: {shutil.which('pi') or 'не найден в PATH'}")
            client = Client()
            try:
                announce_current(client)
            except (OSError, Error) as exc:
                print(f"Предупреждение kitty: {clean(str(exc), 300)}")
            health = client.request("health")
            print(
                f"Daemon: OK · агентов {health['agents']} · с расширением {health['extensions']}"
            )
            if not health["kitty"]:
                print(
                    "Нет известных kitty. Нужны listen_on + socket-only и перезапуск kitty."
                )
            for instance in health["kitty"]:
                print(
                    f"  {instance['endpoint']}: {instance['error'] or 'OK'} · панелей {instance['windows']}"
                )
            if not health["extensions"]:
                print(
                    "Для статусов и названий установите pi-extension и выполните /reload в каждом pi."
                )
            if health["last_error"]:
                print(f"Последняя ошибка перехода: {health['last_error']}")
    except (Error, OSError, ValueError) as exc:
        message = clean(str(exc), 500)
        if args.command == "daemon" and args.log_file:
            logging.getLogger("pi-kitty").error(message)
        else:
            print(f"pi-kitty: {message}", file=sys.stderr)
        if isinstance(exc, FileNotFoundError) and args.command != "daemon":
            print(
                "Запустите: pi-kitty daemon run (или pi-kitty service install)",
                file=sys.stderr,
            )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
