import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { randomUUID } from "node:crypto";
import { lstatSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";
import { createConnection, type Socket } from "node:net";

function socketPath(): string {
  if (process.env.PI_KITTY_SOCKET) return process.env.PI_KITTY_SOCKET;
  const xdg = process.env.XDG_RUNTIME_DIR;
  if (xdg && isAbsolute(xdg)) {
    try {
      const s = statSync(xdg);
      if (s.uid === process.getuid?.() && (s.mode & 0o077) === 0)
        return join(xdg, "pi-kitty", "daemon.sock");
    } catch { /* Use the same fallback as Python. */ }
  }
  const base = process.platform === "darwin"
    ? join(homedir(), "Library", "Caches")
    : (process.env.XDG_CACHE_HOME || join(homedir(), ".cache"));
  return join(base, "pi-kitty", "run", "daemon.sock");
}

function processId(): string {
  const saved = process.env.PI_KITTY_PROCESS_ID?.split(":");
  if (saved?.[0] === String(process.pid) && saved[1]) return saved[1];
  const id = randomUUID();
  process.env.PI_KITTY_PROCESS_ID = `${process.pid}:${id}`;
  return id;
}

/** One object per session runtime. Never writes a transcript or prompt to the wire. */
class Reporter {
  readonly agentId = processId();
  readonly generation = randomUUID();
  context: ExtensionContext;
  socket?: Socket;
  heartbeat?: ReturnType<typeof setInterval>;
  retry?: ReturnType<typeof setTimeout>;
  flushTimer?: ReturnType<typeof setTimeout>;
  active = true;
  connected = false;
  registered = false;
  seq = 0;
  failures = 0;
  buffer = "";
  phase = "unknown";
  tools = new Set<string>();
  lastError = "";

  constructor(ctx: ExtensionContext) {
    this.context = ctx;
  }

  start() {
    this.connect();
    this.heartbeat = setInterval(() => this.publish(), 5000);
    this.heartbeat.unref();
  }

  snapshot() {
    const ctx = this.context;
    const busy = !ctx.isIdle();
    return {
      agent_id: this.agentId, generation: this.generation, seq: ++this.seq,
      pid: process.pid, cwd: ctx.cwd, mode: ctx.mode,
      session_id: ctx.sessionManager.getSessionId(),
      session_file: ctx.sessionManager.getSessionFile() || null,
      session_name: ctx.sessionManager.getSessionName() || null,
      model: ctx.model ? {provider: ctx.model.provider, id: ctx.model.id} : null,
      activity: busy ? "busy" : "idle",
      phase: busy ? (this.tools.size ? "tools" : this.phase) : "unknown",
      kitty: process.env.KITTY_LISTEN_ON ? {
        endpoint: process.env.KITTY_LISTEN_ON,
        window_id: Number(process.env.KITTY_WINDOW_ID) || null,
      } : null,
    };
  }

  connect() {
    if (!this.active || this.socket) return;
    try {
      const path = socketPath();
      const s = lstatSync(path);
      if (!s.isSocket() || s.uid !== process.getuid?.()) throw new Error("Небезопасный socket daemon");
      const sock = createConnection(path);
      this.socket = sock;
      sock.unref();
      sock.setTimeout(12000, () => sock.destroy());
      sock.on("connect", () => {
        if (!this.active || this.socket !== sock) return sock.destroy();
        this.connected = true;
        this.registered = false;
        this.send("agent.register");
      });
      sock.on("data", data => {
        if (this.socket !== sock) return;
        this.buffer += data.toString("utf8");
        if (this.buffer.length > 65536) return sock.destroy();
        let index: number;
        while ((index = this.buffer.indexOf("\n")) >= 0) {
          const line = this.buffer.slice(0, index);
          this.buffer = this.buffer.slice(index + 1);
          try {
            const response = JSON.parse(line);
            if (response.error) {
              this.lastError = String(response.error.message || response.error.code);
              if (response.error.code === "LEASE_REVOKED") this.stop();
              else sock.destroy();
            } else {
              this.registered = true;
              this.failures = 0;
              this.lastError = "";
            }
          } catch { sock.destroy(); }
        }
      });
      sock.on("error", error => { this.lastError = error.message; });
      sock.on("close", () => {
        if (this.socket !== sock) return;
        this.socket = undefined;
        this.connected = this.registered = false;
        this.buffer = "";
        this.scheduleReconnect();
      });
    } catch (error) {
      this.lastError = String(error);
      this.scheduleReconnect();
    }
  }

  scheduleReconnect() {
    if (!this.active || this.retry) return;
    const delay = Math.min(10000, 500 * 2 ** Math.min(this.failures++, 5)) * (0.8 + Math.random() * 0.4);
    this.retry = setTimeout(() => { this.retry = undefined; this.connect(); }, delay);
    this.retry.unref();
  }

  send(method: string) {
    const sock = this.socket;
    if (!this.active || !sock || !this.connected) return;
    // No event backlog: a blocked connection is replaced with a fresh full snapshot.
    if (sock.writableLength > 65536) { sock.destroy(); return; }
    try {
      const params = this.snapshot();
      const message = JSON.stringify({v: 1, id: String(params.seq), method, params}) + "\n";
      if (Buffer.byteLength(message) > 65536) { this.lastError = "Метаданные слишком велики"; return; }
      sock.write(message);
    } catch (error) {
      this.lastError = String(error);
      sock.destroy();
    }
  }

  publish(ctx?: ExtensionContext) {
    if (ctx) this.context = ctx;
    if (this.registered) this.send("agent.update");
  }

  changed(ctx: ExtensionContext) {
    this.context = ctx;
    if (this.flushTimer || !this.active) return;
    // Coalesce tool bursts without awaiting IPC on the pi streaming path.
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      this.publish();
    }, 30);
    this.flushTimer.unref();
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    if (this.heartbeat) clearInterval(this.heartbeat);
    if (this.retry) clearTimeout(this.retry);
    if (this.flushTimer) clearTimeout(this.flushTimer);
    const sock = this.socket;
    if (sock && this.connected && sock.writableLength < 65536) {
      sock.end(JSON.stringify({v: 1, id: "bye", method: "agent.unregister", params: {
        agent_id: this.agentId, generation: this.generation,
      }}) + "\n");
      const timeout = setTimeout(() => sock.destroy(), 100);
      timeout.unref();
    } else sock?.destroy();
  }
}

export default function (pi: ExtensionAPI) {
  let reporter: Reporter | undefined;
  pi.on("session_start", (_event, ctx) => {
    reporter?.stop();
    reporter = new Reporter(ctx);
    reporter.start();
  });
  pi.on("session_shutdown", () => { reporter?.stop(); reporter = undefined; });
  pi.on("before_agent_start", (_event, ctx) => {
    if (reporter) { reporter.phase = "starting"; reporter.changed(ctx); }
  });
  pi.on("agent_start", (_event, ctx) => { reporter?.changed(ctx); });
  pi.on("turn_start", (_event, ctx) => {
    if (reporter) { reporter.phase = "model"; reporter.changed(ctx); }
  });
  pi.on("tool_execution_start", (event, ctx) => {
    reporter?.tools.add(event.toolCallId);
    reporter?.changed(ctx);
  });
  pi.on("tool_execution_end", (event, ctx) => {
    reporter?.tools.delete(event.toolCallId);
    reporter?.changed(ctx);
  });
  // agent_end is NOT idle: pi can still retry or continue automatically.
  pi.on("agent_settled", (_event, ctx) => {
    reporter?.tools.clear();
    if (reporter) { reporter.phase = "unknown"; reporter.changed(ctx); }
  });
  pi.on("model_select", (_event, ctx) => { reporter?.changed(ctx); });
  pi.on("session_info_changed", (_event, ctx) => { reporter?.changed(ctx); });
  pi.registerCommand("kitty-status", {
    description: "Состояние связи с панелью pi-агентов в kitty",
    handler: async (_args, ctx) => {
      const state = reporter?.registered ? "Подключено" : `Нет связи: ${reporter?.lastError || "подключение"}`;
      if (ctx.hasUI) ctx.ui.notify(`${state}\nSocket: ${socketPath()}`, reporter?.registered ? "info" : "warning");
    },
  });
}
