import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_TIMEOUT_MS = 15_000;
const POLL_INTERVAL_MS = 40;

function defaultBridgeRoot() {
  const roaming = process.env.APPDATA;
  return process.env.CODEX_AE_BRIDGE_DIR
    || (roaming ? path.join(roaming, "CodexAEBridge") : path.join(os.homedir(), ".codex-ae-bridge"));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

export class AfterEffectsBridgeError extends Error {
  constructor(message, details = undefined) {
    super(message);
    this.name = "AfterEffectsBridgeError";
    this.details = details;
  }
}

export class AfterEffectsBridgeClient {
  constructor(options = {}) {
    this.root = path.resolve(options.root || defaultBridgeRoot());
    this.inbox = path.join(this.root, "inbox");
    this.outbox = path.join(this.root, "outbox");
    this.previewDir = path.join(this.root, "previews");
    this.heartbeatPath = path.join(this.root, "heartbeat.json");
    this.defaultTimeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
  }

  async initialize() {
    await Promise.all([
      fs.mkdir(this.inbox, { recursive: true }),
      fs.mkdir(this.outbox, { recursive: true }),
      fs.mkdir(this.previewDir, { recursive: true }),
    ]);
  }

  async heartbeat() {
    try {
      const heartbeat = await readJson(this.heartbeatPath);
      const writtenAtMs = Date.parse(heartbeat.written_at || "");
      return {
        ...heartbeat,
        age_ms: Number.isFinite(writtenAtMs) ? Math.max(0, Date.now() - writtenAtMs) : null,
        online: Number.isFinite(writtenAtMs) && Date.now() - writtenAtMs < 3_000,
      };
    } catch (error) {
      if (error?.code === "ENOENT" || error instanceof SyntaxError) {
        return { online: false, age_ms: null };
      }
      throw error;
    }
  }

  async status() {
    await this.initialize();
    return {
      root: this.root,
      heartbeat: await this.heartbeat(),
    };
  }

  async call(action, args = {}, options = {}) {
    await this.initialize();
    const timeoutMs = Math.max(250, options.timeoutMs || this.defaultTimeoutMs);
    const id = randomUUID();
    const requestPath = path.join(this.inbox, `${id}.json`);
    const tempRequestPath = path.join(this.inbox, `${id}.tmp`);
    const responsePath = path.join(this.outbox, `${id}.json`);
    const request = {
      protocol: 1,
      id,
      action,
      args,
      created_at: new Date().toISOString(),
      expires_at_ms: Date.now() + timeoutMs,
    };

    await fs.writeFile(tempRequestPath, JSON.stringify(request), "utf8");
    await fs.rename(tempRequestPath, requestPath);

    const deadline = Date.now() + timeoutMs;
    try {
      while (Date.now() <= deadline) {
        try {
          const response = await readJson(responsePath);
          await fs.unlink(responsePath).catch(() => {});
          if (!response.ok) {
            throw new AfterEffectsBridgeError(
              response.error?.message || `After Effects command failed: ${action}`,
              response.error,
            );
          }
          return response.result;
        } catch (error) {
          if (error?.code !== "ENOENT") {
            throw error;
          }
        }
        await sleep(POLL_INTERVAL_MS);
      }
    } finally {
      await fs.unlink(requestPath).catch(() => {});
      await fs.unlink(tempRequestPath).catch(() => {});
    }

    const heartbeat = await this.heartbeat();
    throw new AfterEffectsBridgeError(
      heartbeat.online
        ? `After Effects did not finish '${action}' within ${timeoutMs} ms.`
        : "After Effects bridge is offline. Start After Effects and make sure CodexAEBridge.jsx is installed/running.",
      { action, timeout_ms: timeoutMs, heartbeat },
    );
  }
}

export function getDefaultBridgeRoot() {
  return defaultBridgeRoot();
}
