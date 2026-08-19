#!/usr/bin/env node
import { promises as fs } from "node:fs";
import path from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import * as z from "zod/v4";
import { AfterEffectsBridgeClient } from "./bridge-client.js";

const client = new AfterEffectsBridgeClient();
const server = new McpServer({
  name: "cnc-pinball-after-effects",
  version: "0.1.0",
});

const itemRef = z.union([
  z.string().min(1).describe("Exact project item/layer name"),
  z.number().int().positive().describe("After Effects item ID or layer index"),
]);
const timeout = z.number().int().min(250).max(600_000).optional()
  .describe("Command timeout in milliseconds");
const vector2or3 = z.array(z.number()).min(2).max(3);

function jsonContent(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
  };
}

function errorContent(error) {
  const payload = {
    error: error.message || String(error),
    details: error.details,
  };
  return {
    isError: true,
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
  };
}

function register(name, config, handler) {
  server.registerTool(name, config, async (args) => {
    try {
      return await handler(args);
    } catch (error) {
      return errorContent(error);
    }
  });
}

function call(action, args, timeoutMs) {
  return client.call(action, args, { timeoutMs });
}

const readOnly = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
};
const mutating = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: false,
  openWorldHint: false,
};

register("ae_bridge_status", {
  title: "After Effects bridge status",
  description: "Check whether the local After Effects bridge is alive without modifying the project.",
  inputSchema: {},
  annotations: readOnly,
}, async () => jsonContent(await client.status()));

register("ae_ping", {
  title: "Ping After Effects",
  description: "Query the running After Effects instance, active project, and active composition.",
  inputSchema: { timeout_ms: timeout },
  annotations: readOnly,
}, async ({ timeout_ms }) => jsonContent(await call("ping", {}, timeout_ms || 5_000)));

register("ae_get_project", {
  title: "Inspect After Effects project",
  description: "List project items, compositions, footage, folders, and current selection.",
  inputSchema: {
    max_items: z.number().int().min(1).max(2_000).optional().default(500),
    timeout_ms: timeout,
  },
  annotations: readOnly,
}, async ({ max_items, timeout_ms }) => (
  jsonContent(await call("get_project", { max_items }, timeout_ms))
));

register("ae_get_comp", {
  title: "Inspect composition",
  description: "Return composition settings plus layer and transform details.",
  inputSchema: {
    comp: itemRef,
    max_layers: z.number().int().min(1).max(1_000).optional().default(250),
    timeout_ms: timeout,
  },
  annotations: readOnly,
}, async ({ comp, max_layers, timeout_ms }) => (
  jsonContent(await call("get_comp", { comp, max_layers }, timeout_ms))
));

register("ae_create_comp", {
  title: "Create composition",
  description: "Create a new composition inside one After Effects undo group.",
  inputSchema: {
    name: z.string().min(1),
    width: z.number().int().min(4).max(30_000).default(640),
    height: z.number().int().min(4).max(30_000).default(480),
    pixel_aspect: z.number().positive().default(1),
    duration: z.number().positive().max(86_400).default(5),
    fps: z.number().positive().max(240).default(30),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("create_comp", args, timeout_ms)));

register("ae_duplicate_comp", {
  title: "Duplicate composition safely",
  description: "Duplicate a composition. If no name is supplied, a non-conflicting _codex_vNN name is used.",
  inputSchema: {
    comp: itemRef,
    new_name: z.string().min(1).optional(),
    open: z.boolean().optional().default(true),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("duplicate_comp", args, timeout_ms)));

register("ae_build_reward_variant", {
  title: "Build polished score reward variant",
  description: "Duplicate a score composition and rebuild it with a generated background, layered smoke, animated typography, particles, and an outro fade.",
  inputSchema: {
    source_comp: itemRef,
    new_name: z.string().min(1),
    background_path: z.string().min(1),
    smoke_path: z.string().min(1),
    text: z.string().min(1),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => (
  jsonContent(await call("build_reward_variant", args, timeout_ms || 120_000))
));

register("ae_enhance_reward_variant", {
  title: "Enrich a psychedelic reward variant",
  description: "Duplicate an existing reward composition, brighten its flat cel palette, start smoke at zero scale, and add layered psychedelic particles and pulse rings.",
  inputSchema: {
    source_comp: itemRef,
    new_name: z.string().min(1),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => (
  jsonContent(await call("enhance_reward_variant", args, timeout_ms || 120_000))
));

register("ae_build_modular_reward_variant", {
  title: "Build modular AE reward variant",
  description: "Build a layered reward animation from separate radial background, frame, leaves, smoke and editable score text assets.",
  inputSchema: {
    source_comp: itemRef,
    new_name: z.string().min(1),
    text: z.string().min(1).optional().default("2500"),
    background_path: z.string().min(1),
    frame_path: z.string().min(1),
    leaves_path: z.string().min(1),
    smoke_path: z.string().min(1),
    duration: z.number().positive().max(30).optional().default(5),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => (
  jsonContent(await call("build_modular_reward_variant", args, timeout_ms || 120_000))
));

register("ae_import_asset", {
  title: "Import asset into After Effects",
  description: "Import a still, media file, or numbered image sequence into the current project.",
  inputSchema: {
    path: z.string().min(1).describe("Absolute path to the file or first numbered frame"),
    sequence: z.boolean().optional().default(false),
    fps: z.number().positive().max(240).optional(),
    folder: itemRef.optional().describe("Optional target project folder"),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("import_asset", args, timeout_ms)));

register("ae_add_layer", {
  title: "Add project item as layer",
  description: "Add footage or a composition to another composition and optionally set its transform.",
  inputSchema: {
    comp: itemRef,
    item: itemRef,
    name: z.string().min(1).optional(),
    position: vector2or3.optional(),
    anchor: vector2or3.optional(),
    scale: vector2or3.optional(),
    opacity: z.number().min(0).max(100).optional(),
    rotation: z.number().optional(),
    start_time: z.number().optional(),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("add_layer", args, timeout_ms)));

register("ae_add_text", {
  title: "Add text layer",
  description: "Create a text layer with common styling and positioning controls.",
  inputSchema: {
    comp: itemRef,
    text: z.string(),
    name: z.string().min(1).optional(),
    position: vector2or3.optional(),
    font: z.string().min(1).optional(),
    font_size: z.number().positive().max(2_000).optional(),
    fill_color: z.array(z.number().min(0).max(1)).length(3).optional(),
    opacity: z.number().min(0).max(100).optional(),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("add_text", args, timeout_ms)));

register("ae_set_transform", {
  title: "Set or keyframe layer transform",
  description: "Set layer transform values, or insert transform keyframes when time is supplied.",
  inputSchema: {
    comp: itemRef,
    layer: itemRef,
    time: z.number().min(0).optional(),
    position: vector2or3.optional(),
    anchor: vector2or3.optional(),
    scale: vector2or3.optional(),
    opacity: z.number().min(0).max(100).optional(),
    rotation: z.number().optional(),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("set_transform", args, timeout_ms)));

register("ae_open_comp", {
  title: "Open composition",
  description: "Open a composition in the viewer and optionally move the playhead.",
  inputSchema: {
    comp: itemRef,
    time: z.number().min(0).optional(),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("open_comp", args, timeout_ms)));

register("ae_play_preview", {
  title: "Play composition preview",
  description: "Open a composition and ask After Effects to play the current preview using its menu command.",
  inputSchema: {
    comp: itemRef,
    time: z.number().min(0).optional(),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => jsonContent(await call("play_preview", args, timeout_ms)));

register("ae_render_frames", {
  title: "Render composition frames for visual review",
  description: "Render one to eight PNG frames and return them as image content so Codex can inspect the animation.",
  inputSchema: {
    comp: itemRef,
    times: z.array(z.number().min(0)).min(1).max(8).optional(),
    timeout_ms: timeout,
  },
  annotations: readOnly,
}, async ({ comp, times, timeout_ms }) => {
  const result = await call("render_frames", { comp, times }, timeout_ms || 120_000);
  const content = [{ type: "text", text: JSON.stringify(result, null, 2) }];
  for (const frame of result.frames || []) {
    const data = await fs.readFile(path.resolve(frame.path));
    content.push({ type: "image", data: data.toString("base64"), mimeType: "image/png" });
  }
  return { content };
});

register("ae_enqueue_render", {
  title: "Queue or render composition",
  description: "Add a composition to the render queue, with optional templates and optional immediate rendering.",
  inputSchema: {
    comp: itemRef,
    output_path: z.string().min(1),
    render_settings_template: z.string().min(1).optional(),
    output_module_template: z.string().min(1).optional(),
    render_now: z.boolean().optional().default(false),
    timeout_ms: timeout,
  },
  annotations: mutating,
}, async ({ timeout_ms, ...args }) => (
  jsonContent(await call("enqueue_render", args, timeout_ms || (args.render_now ? 600_000 : 15_000)))
));

register("ae_save_project", {
  title: "Save After Effects project",
  description: "Save the current project. A new path changes the active project file; overwrite is refused unless explicitly allowed.",
  inputSchema: {
    path: z.string().min(1).optional(),
    allow_overwrite: z.boolean().optional().default(false),
    timeout_ms: timeout,
  },
  annotations: {
    readOnlyHint: false,
    destructiveHint: true,
    idempotentHint: false,
    openWorldHint: false,
  },
}, async ({ timeout_ms, ...args }) => jsonContent(await call("save_project", args, timeout_ms || 120_000)));

register("ae_undo", {
  title: "Undo last After Effects action",
  description: "Execute one After Effects Undo command.",
  inputSchema: { timeout_ms: timeout },
  annotations: mutating,
}, async ({ timeout_ms }) => jsonContent(await call("undo", {}, timeout_ms)));

async function main() {
  await client.initialize();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`Codex After Effects MCP server ready; queue: ${client.root}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
