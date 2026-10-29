"""In-memory leases; process identity and presentation are independent of session ID."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid

from .common import Error, clean
from .kitty import contains_process
from .processes import Process


def positive(value: object, name: str) -> int:
    if type(value) is not int or value <= 1:
        raise Error("INVALID_REQUEST", f"Некорректный {name}")
    return value


def identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError):
        raise Error("INVALID_REQUEST", "Нужен UUID агента/поколения") from None


@dataclass
class Entry:
    data: dict
    generation: str
    seq: int
    owner: object
    started: str
    seen: float


class Registry:
    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.processes: dict[int, Process] = {}
        self.instances: dict[str, dict] = {}
        self.revision = 0
        self.mru: dict[str, float] = {}

    def update(
        self,
        params: dict,
        owner: object,
        register: bool = False,
        now: float | None = None,
    ):
        agent_id = identifier(params.get("agent_id"))
        generation = identifier(params.get("generation"))
        seq = params.get("seq")
        if type(seq) is not int or seq < 0:
            raise Error("INVALID_REQUEST", "Некорректный seq")
        pid = positive(params.get("pid"), "pid")
        proc = self.processes.get(pid)
        if not proc:
            raise Error("PROCESS_NOT_FOUND", "Процесс pi не найден на этой машине")
        previous = self.entries.get(agent_id)
        if previous:
            if previous.data["pid"] != pid or previous.started != proc.started:
                raise Error(
                    "INVALID_REQUEST", "agent_id уже принадлежит другому процессу"
                )
            if not register and (
                previous.owner is not owner or previous.generation != generation
            ):
                raise Error("LEASE_REVOKED", "Поколение регистрации уже заменено")
            if previous.generation == generation and seq <= previous.seq:
                return {"accepted": False}
        elif not register:
            raise Error("REGISTRATION_REQUIRED", "Сначала зарегистрируйте агента")
        if len(self.entries) >= 1000 and not previous:
            raise Error("REGISTRY_FULL", "Лимит регистраций достигнут")
        activity = params.get("activity", "unknown")
        phase = params.get("phase", "unknown")
        mode = params.get("mode", "tui")
        if activity not in {"idle", "busy", "unknown"} or phase not in {
            "starting",
            "model",
            "tools",
            "unknown",
        }:
            raise Error("INVALID_REQUEST", "Некорректное состояние агента")
        if mode not in {"tui", "rpc", "json", "print"}:
            raise Error("INVALID_REQUEST", "Некорректный режим pi")
        kitty = params.get("kitty") if params.get("kitty") is not None else {}
        if not isinstance(kitty, dict):
            raise Error("INVALID_REQUEST", "Некорректная привязка kitty")
        window_id = kitty.get("window_id")
        if window_id is not None and (type(window_id) is not int or window_id < 1):
            raise Error("INVALID_REQUEST", "Некорректный window_id")
        model = params.get("model") if params.get("model") is not None else {}
        if not isinstance(model, dict):
            raise Error("INVALID_REQUEST", "Некорректная модель")
        endpoint = kitty.get("endpoint")
        if endpoint is not None and (
            not isinstance(endpoint, str) or len(endpoint) > 1000
        ):
            raise Error("INVALID_REQUEST", "Некорректный endpoint")
        data = {
            "agent_id": agent_id,
            "source": "extension",
            "pid": pid,
            "process_started_at": proc.started,
            "cwd": clean(params.get("cwd")),
            "session_id": clean(params.get("session_id"), 128) or None,
            "session_name": clean(params.get("session_name"), 256) or None,
            "mode": mode,
            "model": {
                "provider": clean(model.get("provider"), 80),
                "id": clean(model.get("id"), 128),
            },
            "activity": activity,
            "phase": phase,
            "endpoint": endpoint,
            "window_id": window_id,
        }
        self.entries[agent_id] = Entry(
            data,
            generation,
            seq,
            owner,
            proc.started,
            now if now is not None else time.monotonic(),
        )
        self.revision += 1
        return {"accepted": True}

    def unregister(self, params: dict, owner: object):
        agent_id = identifier(params.get("agent_id"))
        entry = self.entries.get(agent_id)
        if (
            entry
            and entry.owner is owner
            and entry.generation == params.get("generation")
        ):
            del self.entries[agent_id]
            self.revision += 1
        return {"ok": True}

    def prune(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        for agent_id, entry in list(self.entries.items()):
            process = self.processes.get(entry.data["pid"])
            if (
                not process
                or process.started != entry.started
                or now - entry.seen > 300
            ):
                del self.entries[agent_id]
                self.revision += 1
        for agent_id in list(self.mru):
            if agent_id not in self.entries and len(self.mru) > 1000:
                del self.mru[agent_id]

    def snapshot(
        self, include_stale: bool = False, now: float | None = None
    ) -> list[dict]:
        now = time.monotonic() if now is None else now
        result, claimed = [], set()
        for entry in self.entries.values():
            data = entry.data
            process = self.processes.get(data["pid"])
            if not process or process.started != entry.started:
                continue
            age = now - entry.seen
            if age > 60 and not include_stale:
                continue
            instance = self.instances.get(data["endpoint"])
            location = None
            status = "outside_kitty" if not data["endpoint"] else "unverified"
            if instance:
                status = "unreachable" if instance.get("error") else "unverified"
                for window in instance.get("windows", []):
                    if window["window_id"] == data["window_id"] and contains_process(
                        window, data["pid"], self.processes
                    ):
                        location = self.location(instance, window)
                        if not instance.get("error"):
                            status = "verified"
                        break
            item = {k: v for k, v in data.items() if k not in {"endpoint", "window_id"}}
            item.update(
                presence="stale" if age > 15 else "online",
                location=location,
                location_status=status,
                last_seen_seconds=round(age),
                mru=self.mru.get(data["agent_id"], 0),
            )
            result.append(item)
            # Only suppress a process fallback while the extension observation is fresh.
            if age <= 60:
                claimed.add((data["pid"], entry.started))
        for endpoint, instance in self.instances.items():
            if instance.get("error"):
                continue
            for window in instance.get("windows", []):
                for foreground in window["foreground"]:
                    process = self.processes.get(foreground["pid"])
                    if (
                        not process
                        or not foreground["is_pi"]
                        or (process.pid, process.started) in claimed
                    ):
                        continue
                    agent_id = "process-" + str(
                        uuid.uuid5(
                            uuid.NAMESPACE_OID, f"{process.pid}:{process.started}"
                        )
                    )
                    claimed.add((process.pid, process.started))
                    result.append(
                        {
                            "agent_id": agent_id,
                            "source": "process",
                            "pid": process.pid,
                            "process_started_at": process.started,
                            "cwd": foreground["cwd"] or window["cwd"],
                            "session_id": None,
                            "session_name": None,
                            "mode": "tui",
                            "model": {},
                            "activity": "unknown",
                            "phase": "unknown",
                            "presence": "online",
                            "location_status": "verified",
                            "location": self.location(instance, window),
                            "last_seen_seconds": 0,
                            "mru": self.mru.get(agent_id, 0),
                        }
                    )
        return sorted(
            result, key=lambda a: (a["cwd"].casefold(), -a["mru"], a["agent_id"])
        )

    @staticmethod
    def location(instance: dict, window: dict) -> dict:
        return {
            "kitty_instance_id": instance["id"],
            **{
                key: window[key]
                for key in (
                    "os_window_id",
                    "tab_id",
                    "tab_index",
                    "tab_title",
                    "window_id",
                )
            },
        }

    def find(self, agent_id: str) -> dict:
        for agent in self.snapshot():
            if agent["agent_id"] == agent_id:
                return agent
        raise Error("AGENT_NOT_FOUND", "Агент уже завершён или потеряна связь")
