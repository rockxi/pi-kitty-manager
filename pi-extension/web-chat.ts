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
        return {role: message.role, text: text.slice(0, 2000)};
      }).filter((entry): entry is {role: "user" | "assistant", text: string} => !!entry && !!entry.text).slice(-30);
      this.abort = new AbortController();
      const timeout = setTimeout(() => this.abort?.abort(), 3000);
      timeout.unref();
      try {
        const receipt = this.receipt;
        const renameReceipt = this.renameReceipt;
        const session = this.ctx.sessionManager.getSessionId();
        const epoch = Math.floor(Date.now() / 300000);
        const rotated = `v1.${epoch}.${createHmac("sha256", token).update(String(epoch)).digest("hex")}`;
        const response = await fetch(base + "/api/chat-poll", {method: "POST", redirect: "error", signal: this.abort.signal, headers: {Authorization: `Bearer ${rotated}`, "Content-Type": "application/json"}, body: JSON.stringify({machine, agent: this.agent, generation: this.generation, session, rename_supported: true, busy: !this.ctx.isIdle(), messages, receipt, rename_receipt: renameReceipt})});
        if (!response.ok) return;
        const result = await response.json() as {message?: {id?: unknown; text?: unknown} | null; rename?: {id?: unknown; name?: unknown; session?: unknown} | null};
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
            if (this.active && this.ctx.isIdle() && command.text.trim() && command.text.length <= 4000) {
              this.pi.sendUserMessage(command.text, {expandPromptTemplates: false});
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
