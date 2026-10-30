import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { createHmac } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

/** Accept only verified HTTPS or the existing local-only HTTP endpoint. */
export function serverOrigin(value: string): string {
  if (/[\x00-\x20\x7f]/.test(value) || /^[a-z]+:\/\/[^/]*@/i.test(value)) throw new Error("Invalid server URL");
  const url = new URL(value);
  const local = url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname) && url.port === "8500";
  if ((!local && url.protocol !== "https:") || !url.hostname || url.username || url.password || (url.pathname !== "/" && url.pathname !== "") || url.search || url.hash) throw new Error("Use an HTTPS origin or http://localhost:8500");
  if (url.protocol === "https:" && process.env.NODE_TLS_REJECT_UNAUTHORIZED === "0") throw new Error("TLS certificate verification is disabled");
  return local && url.hostname === "localhost" ? "http://127.0.0.1:8500" : url.origin;
}

/** Opt-in chat bridge. No shell input, terminal emulation or slash commands. */
export class WebChat {
  private active = true;
  private timer?: ReturnType<typeof setTimeout>;
  private abort?: AbortController;
  private receipt?: {id: string; state: "accepted" | "rejected"};
  private renameReceipt?: {id: string; state: "accepted" | "rejected"};
  constructor(private pi: ExtensionAPI, private ctx: ExtensionContext, private agent: string, private generation: string) {}

  start() {
    if (process.env.PI_KITTY_WEB_CHAT !== "1" || !process.env.PI_KITTY_WEB_TOKEN_FILE) return;
    this.schedule(0);
  }
  stop() {
    this.active = false;
    if (this.timer) clearTimeout(this.timer);
    this.abort?.abort();
  }
  private schedule(delay: number) {
    if (!this.active) return;
    this.timer = setTimeout(() => void this.poll(), delay);
    this.timer.unref();
  }
  private async poll() {
    try {
      const base = serverOrigin(process.env.PI_KITTY_WEB_SERVER || "http://localhost:8500");
      const tokenPath = process.env.PI_KITTY_WEB_TOKEN_FILE!;
      const token = readFileSync(tokenPath.replace(/^~(?=\/)/, homedir()), "utf8").split(/\r?\n/).find(s => s.startsWith("PI_KITTY_WEB_TOKEN="))?.slice("PI_KITTY_WEB_TOKEN=".length).trim();
      if (!token || token.length < 32) throw new Error("Invalid token file");
      const config = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
      const machine = readFileSync(join(config, "pi-kitty", "web-client", "machine-id"), "utf8").trim();
      const messages = this.ctx.sessionManager.getBranch().filter(entry => entry.type === "message" && (entry.message.role === "user" || entry.message.role === "assistant")).map(entry => {
        if (entry.type !== "message") return null;
        const message = entry.message;
        if (message.role !== "user" && message.role !== "assistant") return null;
        const text = typeof message.content === "string" ? message.content : message.content.filter(block => block.type === "text").map(block => block.text).join("\n");
        const image_count = typeof message.content === "string" ? 0 : Math.min(100, message.content.filter(block => block.type === "image").length);
        return {role: message.role, text: text.slice(0, 8000), image_count};
      }).filter((entry): entry is {role: "user" | "assistant", text: string, image_count: number} => !!entry && (!!entry.text || !!entry.image_count)).slice(-30);
      const commands = (typeof this.pi.getCommands === "function" ? this.pi.getCommands() : []).filter(command => /^[a-zA-Z0-9_:.\-]{1,120}$/.test(command.name)).slice(0, 250).map(command => ({name: command.name, description: (command.description || "").slice(0, 200), source: command.source}));
      this.abort = new AbortController();
      const timeout = setTimeout(() => this.abort?.abort(), 30000);
      timeout.unref();
      try {
        const receipt = this.receipt;
        const renameReceipt = this.renameReceipt;
        const session = this.ctx.sessionManager.getSessionId();
        const epoch = Math.floor(Date.now() / 300000);
        const rotated = `v1.${epoch}.${createHmac("sha256", token).update(String(epoch)).digest("hex")}`;
        const response = await fetch(base + "/api/chat-poll", {method: "POST", redirect: "error", signal: this.abort.signal, headers: {Authorization: `Bearer ${rotated}`, "Content-Type": "application/json"}, body: JSON.stringify({machine, agent: this.agent, generation: this.generation, session, rename_supported: true, image_supported: true, commands_supported: true, commands, busy: !this.ctx.isIdle(), messages, receipt, rename_receipt: renameReceipt})});
        if (!response.ok) return;
        const result = await response.json() as {message?: {id?: unknown; text?: unknown; images?: unknown; session?: unknown; slash?: unknown} | null; rename?: {id?: unknown; name?: unknown; session?: unknown} | null};
        if (this.receipt === receipt) this.receipt = undefined;
        if (this.renameReceipt === renameReceipt) this.renameReceipt = undefined;
        const rename = result.rename;
        if (rename && typeof rename.id === "string" && typeof rename.name === "string") {
          let state: "accepted" | "rejected" = "rejected";
          try {
            if (this.active && rename.session === session && this.ctx.sessionManager.getSessionId() === session && rename.name.trim() && [...rename.name].length <= 200 && !/\p{C}/u.test(rename.name)) {
              this.pi.setSessionName(rename.name);
              state = "accepted";
            }
          } catch { /* Report failure rather than retrying. */ }
          this.renameReceipt = {id: rename.id, state};
        }
        const command = result.message;
        if (command && typeof command.id === "string" && typeof command.text === "string") {
          let state: "accepted" | "rejected" = "rejected";
          try {
            const images = command.images === undefined ? [] : command.images;
            if (!Array.isArray(images) || images.length > 3) throw new Error("Invalid images");
            let bytes = 0;
            for (const image of images) {
              if (!image || image.type !== "image" || !["image/png", "image/jpeg", "image/webp"].includes(image.mimeType) || typeof image.data !== "string" || image.data.length > 6*1024*1024 || !/^[A-Za-z0-9+/]*={0,2}$/.test(image.data)) throw new Error("Invalid image");
              bytes += Buffer.byteLength(image.data, "base64");
            }
            const slash = command.slash === true;
            const commandName = slash ? command.text.trim().split(/\s+/, 1)[0].slice(1) : "";
            if (slash && (images.length || command.session !== session || !commands.some(item => item.name === commandName))) throw new Error("Invalid slash command");
            if (this.active && this.ctx.isIdle() && this.ctx.sessionManager.getSessionId() === session && (!(images.length || slash) || command.session === session) && bytes <= 4*1024*1024 && (command.text.trim() || images.length) && command.text.length <= 4000) {
              const content = images.length ? [...(command.text.trim() ? [{type: "text" as const, text: command.text}] : []), ...images.map(image => ({type: "image" as const, mimeType: image.mimeType as string, data: image.data as string}))] : command.text;
              this.pi.sendUserMessage(content, {expandPromptTemplates: slash});
              state = "accepted";
            }
          } catch { /* Report failure rather than retrying the command. */ }
          this.receipt = {id: command.id, state};
        }
      } finally { clearTimeout(timeout); this.abort = undefined; }
    } catch { /* Retry without logging private content or credentials. */ }
    finally { this.schedule(2000); }
  }
}
