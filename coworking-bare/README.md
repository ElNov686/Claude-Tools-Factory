# Coworking — Bare (no factory MCP)

Same agent set, **no factory MCP tools**. Agents work via `Read, Write, Bash`
only — they script Playwright / pywinauto / requests by hand for every task.

## Agents

Same 7 agents as [`../coworking-tools/`](../coworking-tools/), but their
`tools:` frontmatter is stripped of every `mcp__factory__*` entry, and each
contains a banner reminding them factory tools are unavailable.

## What this proves

Same input → very different cost / reliability:

| | Tools coworking | Bare coworking |
|---|---|---|
| "log into snap" | one `mcp__factory__login` call | write Playwright script, install deps, debug selectors, retry |
| tokens | ~200 | ~10k+ |
| time | seconds | minutes |
| brittleness | low (selectors maintained centrally) | high (every run reinvents selectors) |

## Credentials

Both coworkings read the same `env/factory.env` — bare agents must `Read` the
file and parse the matching `<PREFIX>_*` block themselves.
