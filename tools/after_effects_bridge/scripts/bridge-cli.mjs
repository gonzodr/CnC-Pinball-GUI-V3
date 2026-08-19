#!/usr/bin/env node
import { AfterEffectsBridgeClient } from "../src/bridge-client.js";

function usage() {
  process.stderr.write(
    "Usage: node scripts/bridge-cli.mjs <status|ping|call> [action] [json-args]\n",
  );
}

async function main() {
  const [command = "status", action, rawArgs = "{}"] = process.argv.slice(2);
  const client = new AfterEffectsBridgeClient();
  let result;

  if (command === "status") {
    result = await client.status();
  } else if (command === "ping") {
    result = await client.call("ping", {}, { timeoutMs: 5_000 });
  } else if (command === "call" && action) {
    result = await client.call(action, JSON.parse(rawArgs), { timeoutMs: 600_000 });
  } else {
    usage();
    process.exitCode = 2;
    return;
  }

  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  if (error.details) {
    process.stderr.write(`${JSON.stringify(error.details, null, 2)}\n`);
  }
  process.exitCode = 1;
});
