import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { AfterEffectsBridgeClient, AfterEffectsBridgeError } from "../src/bridge-client.js";

async function makeRoot() {
  return fs.mkdtemp(path.join(os.tmpdir(), "codex-ae-bridge-test-"));
}

async function waitForRequest(inbox, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const names = (await fs.readdir(inbox).catch(() => [])).filter((name) => name.endsWith(".json"));
    if (names.length) {
      const requestPath = path.join(inbox, names[0]);
      return { requestPath, request: JSON.parse(await fs.readFile(requestPath, "utf8")) };
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("Timed out waiting for test request");
}

test("bridge client exchanges one atomic request and response", async (t) => {
  const root = await makeRoot();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const client = new AfterEffectsBridgeClient({ root, timeoutMs: 2_000 });
  await client.initialize();

  const responder = (async () => {
    const { requestPath, request } = await waitForRequest(client.inbox);
    const responsePath = path.join(client.outbox, `${request.id}.json`);
    await fs.writeFile(responsePath, JSON.stringify({ id: request.id, ok: true, result: { pong: true } }));
    await fs.unlink(requestPath).catch(() => {});
  })();

  const result = await client.call("ping");
  await responder;
  assert.deepEqual(result, { pong: true });
});

test("bridge client reports an offline bridge on timeout", async (t) => {
  const root = await makeRoot();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const client = new AfterEffectsBridgeClient({ root, timeoutMs: 260 });
  await assert.rejects(
    () => client.call("ping"),
    (error) => error instanceof AfterEffectsBridgeError && /offline/i.test(error.message),
  );
  assert.equal((await fs.readdir(client.inbox)).length, 0);
});

test("MCP server starts and publishes the After Effects tools", async (t) => {
  const root = await makeRoot();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.resolve("src/server.js")],
    env: { ...process.env, CODEX_AE_BRIDGE_DIR: root },
    stderr: "pipe",
  });
  const mcp = new Client({ name: "ae-bridge-test", version: "1.0.0" });
  t.after(() => mcp.close());
  await mcp.connect(transport);
  const listed = await mcp.listTools();
  const names = new Set(listed.tools.map((tool) => tool.name));
  assert.ok(names.has("ae_ping"));
  assert.ok(names.has("ae_render_frames"));
  assert.ok(names.has("ae_duplicate_comp"));

  const status = await mcp.callTool({ name: "ae_bridge_status", arguments: {} });
  assert.equal(status.isError, undefined);
  assert.match(status.content[0].text, /\"online\": false/);
});
