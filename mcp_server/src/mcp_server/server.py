import os
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP

from .engine.executor import run_pattern
from .engine.validate import validate_tool
from .pattern_store import PatternStore
from .session_store import SessionStore

_ROOT = Path(__file__).resolve().parents[2]      # the mcp_server/ project dir
_TOOLS_DIR = _ROOT / "tools"
_PATTERNS_DIR = _ROOT / "patterns"
_SESSIONS_DIR = Path(os.environ.get("TEMP", "/tmp")) / "mcp_sessions"

# Auto-load env files from <repo>/env/*.env so credentials and signup data
# live in one gitignored place. Values already in os.environ are NOT
# overwritten — user's explicit env still wins.
_ENV_DIR = _ROOT.parent / "env"
if _ENV_DIR.is_dir():
    for _env_file in sorted(_ENV_DIR.glob("*.env")):
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            os.environ.setdefault(_k, _v)

pattern_store = PatternStore(_PATTERNS_DIR)
session_store = SessionStore(_SESSIONS_DIR, ttl_seconds=600)

# import action modules so their @register side-effects run
from .engine.actions import (  # noqa: E402,F401
    control, hidden, login, login_app, map, qa, routes, sessions, signup, tc,
    touch, ui_web, view,
)


def build_tool_specs() -> list[dict]:
    specs = []
    for f in sorted(_TOOLS_DIR.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        validate_tool(spec)
        spec["__path__"] = str(f)
        specs.append(spec)
    return specs


def _empty_ctx() -> dict:
    return {"input": {}, "creds": {}, "env": dict(os.environ), "vars": {}, "output": {}}


async def _login(target: str, credentials_key: str | None = None,  # noqa: ARG001
                 keep_open: bool = False, visible: bool = False) -> Any:
    """Thin programmatic entrypoint for runner.py / tests.

    Runs the `login` tool's steps in a fresh ctx and returns a small object
    with `.status`, `.target`, `.error` so existing callers keep working.
    `credentials_key` is accepted for backward compat but ignored — cred
    resolution is env-only now.
    """
    spec_path = _TOOLS_DIR / "login.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    ctx = _empty_ctx()
    ctx["input"] = {"target": target, "keep_open": keep_open, "visible": visible}
    await run_pattern(spec["steps"], ctx, session_store,
                      target=target, pattern_path=str(spec_path))
    out = ctx["output"]

    class _R:
        status = out.get("status", "failed")
        target = out.get("target", "")
        error = out.get("error")
        retriable = status != "ok"

    return _R()


def _make_generic_fn(name: str, steps: list, path: str,
                     input_schema: dict[str, Any] | None) -> Any:
    """Build a typed async function from YAML mcp_input_schema so FastMCP can
    introspect the parameters without hitting the **kwargs restriction."""
    schema = input_schema or {}
    required_params: list[tuple[str, bool]] = []
    optional_params: list[tuple[str, bool]] = []
    for pname, pdef in schema.items():
        if isinstance(pdef, dict) and pdef.get("required", False):
            required_params.append((pname, True))
        else:
            optional_params.append((pname, False))

    args_parts = []
    for pname, _ in required_params:
        args_parts.append(f"{pname}: str")
    for pname, _ in optional_params:
        args_parts.append(f"{pname}: str = None")

    args_str = ", ".join(args_parts)
    all_pnames = [p for p, _ in required_params + optional_params]
    kwargs_build = "{" + ", ".join(f'"{p}": {p}' for p in all_pnames) + "}"

    src = f"""
async def _tool_fn({args_str}):
    ctx = _empty_ctx()
    ctx["input"] = {kwargs_build}
    outcome = await run_pattern(_steps, ctx, session_store, target=_name, pattern_path=_path)
    out = ctx["output"]
    if outcome.status != "ok" and not out.get("error"):
        out["status"] = outcome.status
        out["error"] = outcome.error
    return out
"""
    globs = {
        "_empty_ctx": _empty_ctx,
        "run_pattern": run_pattern,
        "session_store": session_store,
        "_steps": steps,
        "_path": path,
        "_name": name,
    }
    exec(src, globs)  # noqa: S102
    fn = globs["_tool_fn"]
    fn.__name__ = name
    return fn


def make_server() -> FastMCP:
    mcp = FastMCP("claude-tools-factory")
    for spec in build_tool_specs():
        name = spec["name"]
        desc = spec["description"]
        fn = _make_generic_fn(
            name, spec["steps"], spec["__path__"],
            spec.get("mcp_input_schema"),
        )
        mcp.tool(name=name, description=desc)(fn)
    return mcp


mcp = make_server()
