"""Async local registry service. One shared topology poll per kitty instance."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import socket
import struct
import time
import uuid

from .common import (
    Error,
    MAX_LINE,
    PAGE_SIZE,
    clean,
    encode,
    secure_dir,
    socket_path,
    validate_socket,
)
from .kitty import Kitty, contains_process, process_table
from .registry import Registry, identifier

log = logging.getLogger("pi-kitty")


def same_user(sock) -> bool:
    if hasattr(socket, "SO_PEERCRED"):
        _, uid, _ = struct.unpack(
            "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        return uid == os.getuid()
    # macOS/BSD expose getpeereid in libc, not in all Python socket builds.
    libc = ctypes.CDLL(None, use_errno=True)
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    return (
        libc.getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0
        and uid.value == os.getuid()
    )


class Daemon:
    def __init__(
        self,
        path: Path | None = None,
        command: str | None = None,
        endpoints: list[str] | None = None,
    ):
        self.path = path or socket_path()
        self.kitty = Kitty(command)
        self.registry = Registry()
        self.boot_id = str(uuid.uuid4())
        self.poll_lock = asyncio.Lock()
        self.focus_lock = asyncio.Lock()
        self.last_error: str | None = None
        self.seed_endpoints = endpoints or []
        self.connections: set[asyncio.StreamWriter] = set()
        self.stop = asyncio.Event()

    def announce(self, endpoint: str):
        endpoint = self.kitty.endpoint(endpoint)
        if endpoint not in self.registry.instances:
            if len(self.registry.instances) >= 64:
                raise Error("REGISTRY_FULL", "Слишком много экземпляров kitty")
            self.registry.instances[endpoint] = {
                "id": str(uuid.uuid4()),
                "windows": [],
                "error": None,
                "last_poll": 0,
                "failures": 0,
                "retry_at": 0,
            }
        return endpoint

    async def poll(self, force: bool = False, only_endpoint: str | None = None):
        async with self.poll_lock:
            self.registry.processes = await process_table()
            now = time.monotonic()
            # Only our private runtime directory; never glob /tmp.
            for path in list(self.path.parent.glob("kitty-*.sock"))[:64]:
                with contextlib.suppress(OSError, Error):
                    self.announce("unix:" + str(path))

            async def refresh(endpoint, instance):
                if not force and now < instance["retry_at"]:
                    return
                previous = (instance["windows"], instance["error"])
                try:
                    instance["windows"] = await self.kitty.topology(endpoint)
                    instance["error"] = None
                    instance["failures"] = 0
                    instance["retry_at"] = 0
                except FileNotFoundError:
                    # Closed kitty instances remove their Unix socket. Forget them
                    # instead of showing a permanent error in every overlay.
                    if self.registry.instances.get(endpoint) is instance:
                        del self.registry.instances[endpoint]
                        self.registry.revision += 1
                    return
                except (
                    OSError,
                    Error,
                    ValueError,
                    KeyError,
                    TypeError,
                    TimeoutError,
                ) as exc:
                    instance["error"] = clean(str(exc), 300)
                    instance["failures"] += 1
                    instance["retry_at"] = now + min(
                        30, 2 ** min(instance["failures"], 5)
                    )
                instance["last_poll"] = now
                if previous != (instance["windows"], instance["error"]):
                    self.registry.revision += 1

            await asyncio.gather(
                *(
                    refresh(e, i)
                    for e, i in list(self.registry.instances.items())
                    if only_endpoint is None or e == only_endpoint
                )
            )
            self.registry.prune()

    async def focus(self, params: dict):
        async with self.focus_lock:
            try:
                async with asyncio.timeout(5):
                    # Wait for source overlay removal, not a guessed fixed delay.
                    overlay = params.get("overlay")
                    if overlay is not None:
                        if (
                            not isinstance(overlay, dict)
                            or type(overlay.get("window_id")) is not int
                        ):
                            raise Error("INVALID_REQUEST", "Некорректный overlay")
                        source = self.announce(overlay.get("endpoint"))
                        deadline = time.monotonic() + 2
                        while True:
                            windows = await self.kitty.topology(source, urgent=True)
                            if not any(
                                w["window_id"] == overlay["window_id"] for w in windows
                            ):
                                break
                            if time.monotonic() >= deadline:
                                raise Error(
                                    "FOCUS_FAILED",
                                    "Список агентов ещё открыт; переход отменён",
                                )
                            await asyncio.sleep(0.05)
                    agent_id = str(params.get("agent_id", ""))
                    before = self.registry.find(agent_id)
                    entry = self.registry.entries.get(agent_id)
                    endpoint_hint = (
                        entry.data["endpoint"]
                        if entry
                        else next(
                            (
                                e
                                for e, i in self.registry.instances.items()
                                if i["id"]
                                == (before.get("location") or {}).get(
                                    "kitty_instance_id"
                                )
                            ),
                            None,
                        )
                    )
                    if not endpoint_hint:
                        raise Error(
                            "LOCATION_UNVERIFIED",
                            "У агента нет подтверждённого экземпляра kitty",
                        )
                    self.registry.processes = await process_table()
                    windows = await self.kitty.topology(endpoint_hint, urgent=True)
                    self.registry.instances[endpoint_hint].update(
                        windows=windows, error=None
                    )
                    self.registry.revision += 1
                    agent = self.registry.find(agent_id)
                    if agent["presence"] != "online":
                        raise Error(
                            "AGENT_STALE",
                            "Нет свежего статуса агента; дождитесь переподключения",
                        )
                    if agent["location_status"] != "verified":
                        raise Error(
                            "LOCATION_UNVERIFIED",
                            "Панель агента не подтверждена или kitty недоступен",
                        )
                    location = agent["location"]
                    endpoint, instance = next(
                        (e, i)
                        for e, i in self.registry.instances.items()
                        if i["id"] == location["kitty_instance_id"]
                    )
                    window_id = location["window_id"]
                    target = next(
                        w for w in instance["windows"] if w["window_id"] == window_id
                    )
                    if not contains_process(
                        target, agent["pid"], self.registry.processes
                    ):
                        raise Error(
                            "LOCATION_UNVERIFIED",
                            "Процесс больше не принадлежит выбранной панели",
                        )
                    await self.kitty.call(
                        endpoint,
                        "focus-window",
                        "--match",
                        f"id:{window_id}",
                        urgent=True,
                    )
                    windows = await self.kitty.topology(endpoint, urgent=True)
                    if not any(
                        w["window_id"] == window_id and w["active"] for w in windows
                    ):
                        raise Error(
                            "FOCUS_FAILED", "Kitty не подтвердил активацию панели"
                        )
                    self.registry.mru[agent["agent_id"]] = time.time()
                    self.registry.revision += 1
                    self.last_error = None
                    return {"focused": agent["agent_id"]}
            except TimeoutError:
                self.last_error = "Истекло время переключения на агента"
                raise Error("FOCUS_FAILED", self.last_error) from None
            except (Error, OSError) as exc:
                self.last_error = clean(str(exc), 300)
                raise

    async def dispatch(self, method: str, params: dict, owner: object):
        if method == "hello":
            return {
                "v": 1,
                "boot_id": self.boot_id,
                "capabilities": ["pagination", "focus", "discovery"],
            }
        if method == "health":
            return {
                "boot_id": self.boot_id,
                "pid": os.getpid(),
                "socket": str(self.path),
                "last_error": self.last_error,
                "agents": len(self.registry.snapshot()),
                "extensions": len(self.registry.entries),
                "kitty": [
                    {"endpoint": e, "error": i["error"], "windows": len(i["windows"])}
                    for e, i in self.registry.instances.items()
                ],
            }
        if method == "kitty.announce":
            self.announce(params.get("endpoint"))
            await self.poll(force=True)
            return {"ok": True}
        if method in {"agent.register", "agent.update", "agent.heartbeat"}:
            if method == "agent.register":
                self.registry.processes = await process_table()
            result = self.registry.update(
                params, owner, register=method == "agent.register"
            )
            entry = self.registry.entries[identifier(params["agent_id"])]
            if entry.data["endpoint"]:
                with contextlib.suppress(OSError, Error):
                    self.announce(entry.data["endpoint"])
            return result
        if method == "agent.unregister":
            return self.registry.unregister(params, owner)
        if method == "agents.list":
            offset = params.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise Error("INVALID_REQUEST", "Некорректный offset")
            # Non-interactive pi processes aren't targets for the terminal switcher.
            agents = [
                a
                for a in self.registry.snapshot(bool(params.get("include_stale")))
                if a["mode"] == "tui"
            ]
            end = offset + PAGE_SIZE
            return {
                "revision": self.registry.revision,
                "agents": agents[offset:end],
                "next_offset": end if end < len(agents) else None,
            }
        if method == "agents.focus":
            return await self.focus(params)
        raise Error("METHOD_NOT_FOUND", "Неизвестный метод")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        owner = object()
        if (
            not same_user(writer.get_extra_info("socket"))
            or len(self.connections) >= 256
        ):
            writer.close()
            return
        self.connections.add(writer)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), 30)
                except (ValueError, TimeoutError):
                    break
                if not line:
                    break
                request_id = None
                try:
                    if len(line) > MAX_LINE or not line.endswith(b"\n"):
                        raise Error("INVALID_REQUEST", "Слишком большое сообщение")
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise Error("INVALID_REQUEST", "Ожидается JSON object")
                    request_id = message.get("id")
                    if type(request_id) not in (str, int) or len(str(request_id)) > 128:
                        request_id = None
                        raise Error("INVALID_REQUEST", "Некорректный request id")
                    if message.get("v") != 1:
                        raise Error("INCOMPATIBLE_PROTOCOL", "Нужна версия протокола 1")
                    params = message.get("params", {})
                    if not isinstance(params, dict):
                        raise Error("INVALID_REQUEST", "params должен быть object")
                    result = await self.dispatch(message.get("method"), params, owner)
                    response = {"v": 1, "id": request_id, "result": result}
                except (Error, OSError, ValueError, TypeError, KeyError) as exc:
                    response = {
                        "v": 1,
                        "id": request_id,
                        "error": {
                            "code": exc.code
                            if isinstance(exc, Error)
                            else "INVALID_REQUEST",
                            "message": clean(str(exc), 300),
                        },
                    }
                writer.write(encode(response))
                await asyncio.wait_for(writer.drain(), 2)
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            self.connections.discard(writer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def serve(self):
        secure_dir(self.path.parent)
        if not self.path.is_absolute() or len(os.fsencode(self.path)) > 100:
            raise Error(
                "INVALID_REQUEST",
                "Слишком длинный или относительный путь PI_KITTY_SOCKET",
            )
        lock_path = self.path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise Error("PERMISSION_DENIED", "Чужой lock file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error("ALREADY_RUNNING", "Daemon уже запущен") from None
            if self.path.exists() or self.path.is_symlink():
                validate_socket(self.path)
                # Never unlink a live socket, even if another implementation holds it.
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.2)
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise Error(
                        "ALREADY_RUNNING", "Unix socket занят работающим сервером"
                    )
                finally:
                    probe.close()
            self.registry.processes = await process_table()
            for endpoint in self.seed_endpoints:
                try:
                    self.announce(endpoint)
                except (OSError, Error) as exc:
                    log.warning("Endpoint недоступен: %s", clean(str(exc), 300))
            old_umask = os.umask(0o177)
            try:
                server = await asyncio.start_unix_server(
                    self.handle, path=str(self.path), limit=MAX_LINE
                )
            finally:
                os.umask(old_umask)
            inode = self.path.stat().st_ino
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.stop.set)
            log.info("Daemon готов: %s", self.path)

            async def background():
                while not self.stop.is_set():
                    try:
                        await self.poll()
                    except (OSError, Error, ValueError) as exc:
                        log.warning("Ошибка опроса: %s", clean(str(exc), 300))
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.stop.wait(), 2)

            task = asyncio.create_task(background())
            try:
                async with server:
                    await self.stop.wait()
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                for writer in list(self.connections):
                    writer.close()
                if self.path.exists() and self.path.stat().st_ino == inode:
                    self.path.unlink()
        finally:
            os.close(fd)
