# Coworking — Tools (powered by factory MCP)

Full agent set, all factory MCP tools enabled.

## Agents

| Agent | Role |
|---|---|
| tool-orchestrator | routes build / login-discovery requests to other agents |
| tool-architect | scaffolds new tool YAML skeletons |
| flow-builder | fills `steps:` of a tool, or discovers a login pattern |
| tool-verifier | validates YAML against schema, dry-runs |
| qa-mapper | builds navigable route map of an SPA |
| qa-prober | exercises pages, captures trace evidence |
| qa-author | writes POM + Playwright stubs from collected dumps |

## Factory MCP tools available

| Tool | What it does |
|---|---|
| `mcp__factory__login` | universal web login by env block (`SIMPLE`, `SNAP`, ...) |
| `mcp__factory__login_app` | desktop app login via UIA (`PROTON`, ...) |
| `mcp__factory__sign_up` | universal DOM-driven sign-up from `SIGNUP_*` env |
| `mcp__factory__view` | screenshot + visible-interactives inventory of a page |
| `mcp__factory__touch` | drives interactions, records trace |
| `mcp__factory__hidden` | surfaces hidden affordances (menus, tooltips, off-screen) |
| `mcp__factory__map` | deduped page graph for an SPA |
| `mcp__factory__routes` | extract + resolve SPA route table |
| `mcp__factory__tc` | synthesize test-case proposals from dumps |
| `mcp__factory__close_session` | close a live browser session |

## Use it

User asks "log into snap" → agent calls `mcp__factory__login(target="snap", visible=true, keep_open=true)` → one shot, ~200 tokens.

Pair with [`../coworking-bare/`](../coworking-bare/) for the no-tools comparison.
