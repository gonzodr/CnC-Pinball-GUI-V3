# Codex ↔ After Effects bridge

This local integration connects Codex to Adobe After Effects through two small components:

1. a stdio MCP server running under Node.js;
2. an on-demand After Effects ExtendScript worker that exchanges atomic JSON messages through `%APPDATA%\CodexAEBridge`.

No project content is uploaded by the bridge. Preview PNGs are rendered locally and returned to Codex only when the `ae_render_frames` tool is called.

## Install

From PowerShell:

```powershell
cd "F:\Projects\CnC Pinball GUI V3 Python\tools\after_effects_bridge"
npm install
.\scripts\install.ps1 -AfterEffectsVersion 23.2 -StartAfterEffects
```

After Effects must have **Preferences → Scripting & Expressions → Allow Scripts to Write Files and Access Network** enabled. On this workstation the setting is already enabled.

The worker is deliberately not placed in `Scripts/Startup`: AE 2023 can expose an unstable "no current context" during early launch. Start it with `-StartAfterEffects`, `scripts/start-bridge.ps1`, or from **Window → Codex AE Bridge**. The panel can start, stop, and inspect the worker.

## Codex MCP configuration

Add this to `%USERPROFILE%\.codex\config.toml`, then start a new Codex task/app session:

```toml
[mcp_servers.after_effects]
command = 'C:\Program Files\nodejs\node.exe'
args = ['F:\Projects\CnC Pinball GUI V3 Python\tools\after_effects_bridge\src\server.js']
startup_timeout_sec = 20
tool_timeout_sec = 600
```

An MCP configuration change cannot inject tools into an already running Codex task; a new task/session is required.

## Smoke checks

```powershell
npm test
npm run cli -- status
npm run cli -- ping
```

Generic command calls can also be tested without MCP:

```powershell
npm run cli -- call get_project '{"max_items":100}'
npm run cli -- call get_comp '{"comp":"2500"}'
npm run cli -- call render_frames '{"comp":"2500","times":[0,1,2]}'
```

## MCP tools

- `ae_bridge_status`, `ae_ping`
- `ae_get_project`, `ae_get_comp`
- `ae_create_comp`, `ae_duplicate_comp`
- `ae_build_reward_variant`
- `ae_enhance_reward_variant`
- `ae_import_asset`, `ae_add_layer`, `ae_add_text`
- `ae_set_transform`, `ae_open_comp`, `ae_play_preview`
- `ae_render_frames`, `ae_enqueue_render`
- `ae_save_project`, `ae_undo`

Composition names must be unique. If duplicate names exist, use the numeric item ID returned by `ae_get_project`. Layer names follow the same rule; use a numeric layer index when necessary.

## Safety model

- Creative revisions should begin with `ae_duplicate_comp`; without a supplied name it creates `name_codex_v01`, `v02`, and so on.
- Project overwrites are refused unless `allow_overwrite=true` is explicit.
- Editing operations use After Effects undo groups.
- The bridge does not expose arbitrary ExtendScript execution.
- Long final renders are queued by default; `render_now=true` must be explicit.

## Typical creative review loop

1. `ae_get_project` finds the target composition.
2. `ae_render_frames` returns representative frames for visual inspection.
3. `ae_duplicate_comp` creates a safe working version.
4. New generated assets are imported with `ae_import_asset`.
5. Layers and animation are assembled with `ae_add_layer` and `ae_set_transform`.
6. Another `ae_render_frames` call verifies the result.
7. `ae_play_preview` opens and starts the AE preview where the installed language exposes a matching Preview menu command.
