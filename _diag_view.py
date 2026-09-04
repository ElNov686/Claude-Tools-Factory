"""Diagnostic: run the `view` tool steps directly so the real exception surfaces.
The MCP server swallows errors into `{}` — this bypasses that path.
"""
import asyncio
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "mcp_server" / "src"))

import yaml  # noqa: E402

# Load env files like server.py does
ENV_DIR = HERE / "env"
for env_file in sorted(ENV_DIR.glob("*.env")):
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from mcp_server.engine.executor import run_pattern  # noqa: E402
from mcp_server.session_store import SessionStore  # noqa: E402
# trigger @register side-effects
from mcp_server.engine.actions import (  # noqa: E402,F401
    control, hidden, login, login_app, map, qa, routes, sessions, signup, tc,
    touch, ui_web, view,
)

TOOLS_DIR = HERE / "mcp_server" / "tools"
SESSIONS_DIR = Path(os.environ.get("TEMP", "/tmp")) / "mcp_sessions"


async def run_tool(name: str, **inp):
    spec = yaml.safe_load((TOOLS_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    # Mirror MCP: every schema key is present in ctx["input"], missing optionals = None
    schema = spec.get("mcp_input_schema") or {}
    full_input = {k: inp.get(k) for k in schema}
    for k, v in inp.items():
        full_input[k] = v
    ctx = {"input": full_input, "creds": {}, "env": dict(os.environ),
           "vars": {}, "output": {}}
    store = SessionStore(SESSIONS_DIR, ttl_seconds=600)
    try:
        outcome = await run_pattern(
            spec["steps"], ctx, store, target=inp.get("target", ""),
            pattern_path=str(TOOLS_DIR / f"{name}.yaml"),
        )
        print(f"\n=== {name} outcome ===")
        print(f"status: {outcome.status}")
        print(f"error:  {outcome.error}")
        print(f"output keys: {list(ctx['output'].keys())}")
        print(f"output: {ctx['output']}")
    except Exception:
        print(f"\n=== {name} CRASHED ===")
        traceback.print_exc()


async def main():
    await run_tool("login", target="simple", keep_open="true")
    await run_tool("view", target="simple")


asyncio.run(main())
