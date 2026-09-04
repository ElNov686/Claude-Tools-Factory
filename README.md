# claude-tools-factory

> Declarative MCP tool factory for QA automation. Login + a
> `map → view → touch → hidden → tc → qa` pipeline that scrapes a live SPA
> and emits Playwright POM + spec files in BoardQA style.

Tools are YAML, not Python. The engine is stable; new tools = new `.yaml`.

---

## Install

### As a Claude Code plugin

This repo is a self-contained plugin. To use it from Claude Code:

```
# 1. clone next to your other plugins
git clone <repo-url> claude-tools-factory

# 2. install MCP server deps (the plugin runs the server as a subprocess)
cd claude-tools-factory/mcp_server
python -m venv .venv
.venv/Scripts/pip install -e .[dev]          # Windows
# .venv/bin/pip install -e '.[dev]'           # macOS/Linux
.venv/Scripts/python -m playwright install chromium

# 3. point Claude Code at the plugin
# In Claude Code Settings → Plugins → "Add local plugin", select the cloned dir.
```

`.claude-plugin/plugin.json` declares the MCP server with `command: python`
and `cwd: ${CLAUDE_PLUGIN_ROOT}/mcp_server`. The `python` on PATH must be the
venv's interpreter (or set `PATH` to prepend `mcp_server/.venv/Scripts`).

### As a standalone MCP server (Claude Desktop)

```
git clone <repo-url> e:/tools/claude-tools-factory
cd e:/tools/claude-tools-factory
# Windows
powershell -ExecutionPolicy Bypass -File ./setup.ps1
# macOS / Linux
./setup.sh
```

Then merge `claude_desktop_config.snippet.json` into Claude Desktop's
`claude_desktop_config.json` (Settings → Developer → Edit Config), adjusting
the absolute paths, and restart Claude Desktop.

### Credentials (keyring)

Login patterns read credentials from your OS keyring, never from disk.
Example — staging.projectsimple.ai:

```
keyring set staging.projectsimple.ai email
keyring set staging.projectsimple.ai password
```

The key name matches the `credentials_keyring_name` in the relevant
`mcp_server/patterns/web/<host>.yaml` (defaults to the target's hostname).

---

## Tools

User-facing tools — the ones Claude calls directly:

| Tool    | What it does                                                       |
|---------|--------------------------------------------------------------------|
| `login` | Log into a web service (URL) or desktop app (key) via a learned pattern. `keep_open: true` keeps the browser alive for follow-up tools. |
| `tc`    | Synthesize TC_NN_MM test-case proposals from a live SPA. Reuses the live login session, runs the full `map → view → touch → hidden → propose_tc` chain, returns a structured menu. |

Internal tools (hidden from the MCP catalog — composed by `tc` and the
runner, or invoked directly only during development):
`map`, `view`, `touch`, `hidden`, `routes`, `screenshot`, `read_ui_tree`,
`json_query`, `bug_report`, `notify`, `creds`, `sign_up`, `spin_up`,
`site_map`, `close_session`, `login_resume`.

---

## A→Z runner

One CLI command drives the full pipeline (login → goto → view → touch →
hidden → tc → qa) and writes Playwright POM + spec files:

```
python -m mcp_server.runner \
  --target https://staging.projectsimple.ai/ \
  --url /isteu/team/developers
```

Prints `TC count`, `Output dir`, and the top-5 generated files at the end.
Headless by default; `--headed` shows the browser (the login pattern wins
if it pins `headless`).

---

## Layout

- `mcp_server/tools/*.yaml` — one MCP tool per file
- `mcp_server/patterns/{web,desktop}/*.yaml` — login flows per target
- `mcp_server/src/mcp_server/engine/` — executor + action vocabulary
  (Python, stable)
- `mcp_server/src/mcp_server/runner.py` — A→Z CLI entry point
- `agents/`, `commands/`, `skills/` — plugin-bundled Claude Code assets

---

## Security

- Never commit credentials. Patterns and tool YAML are safe to commit — they
  contain no secrets.
- All credentials come from the OS keyring at runtime.
