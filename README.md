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

### Credentials (`env/factory.env`)

`login` reads credentials from `env/factory.env`, not from the OS keyring.
Add one block per project, three variables sharing a prefix:

```
BOARD_URL=https://staging.projectsimple.ai
BOARD_USERNAME=board.simple.qa+08@gmail.com
BOARD_PASSWORD=QA_board00
```

Call `login` with `target` set to that prefix (case-insensitive substring
match, e.g. `target: "board"`), not the URL itself — `login` picks the
`<PREFIX>_*` block whose prefix appears in `target`, then walks the DOM
(username → password → submit) on `<PREFIX>_URL`.

`env/factory.env` is gitignored; never commit it.

---

## Tools

User-facing tools — the ones Claude calls directly:

| Tool         | What it does                                                       |
|--------------|--------------------------------------------------------------------|
| `login`      | Universal login on any web host. Reads `<PREFIX>_URL/_USERNAME/_PASSWORD` from `env/factory.env` and walks the DOM (username → password → submit) — no per-site pattern needed. `keep_open: true` keeps the browser alive for follow-up tools. |
| `login_app`  | Same idea for a desktop app already running on the OS: reads `<PREFIX>_APP` (window title regex) + `_USERNAME`/`_PASSWORD`, attaches via UIA. |
| `sign_up`    | Walks a registration form using `SIGNUP_*` values from `env/factory.env`. Uses a per-host pattern at `mcp_server/patterns/web/<host>.signup.yaml` when one exists. |
| `tc`         | Synthesize TC_NN_MM test-case proposals from a live SPA. Reuses the live login session, runs the full `map → view → touch → hidden → propose_tc → qa_render` chain itself, returns a structured menu and writes POM/spec files. |

Internal tools (`kind: internal` in their YAML — not shown in the MCP
catalog; composed by `tc` and the runner, or fetched directly by name for
debugging): `map`, `view`, `touch`, `hidden`, `routes`, `close_session`.
Each of `map`/`view`/`touch`/`hidden`/`tc` takes an optional `page` (a route
template or a concrete path) to scope the work to one page instead of the
whole SPA — always use it when you only care about one feature, since
without it these tools crawl every route. See
[`docs/SESSION_GUIDE.md`](docs/SESSION_GUIDE.md) for the practical call
patterns, including why these internal tools must never be called
concurrently against the same `target` (they share one live browser page).

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
Headless by default; `--headed` shows the browser.

---

## Session guide

Practical notes on starting a session against a specific project and the
token-saving call patterns for generating test cases — see
[`docs/SESSION_GUIDE.md`](docs/SESSION_GUIDE.md) (RU).

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
- All credentials come from `env/factory.env` at runtime (gitignored).
