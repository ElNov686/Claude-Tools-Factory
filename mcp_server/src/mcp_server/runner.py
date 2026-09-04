"""A→Z pipeline runner: login → goto → view → touch → hidden → tc → qa.

One command drives the full chain end-to-end and prints a summary
(TC count, output dir, top-5 generated files).

CLI:
    python -m mcp_server.runner --target https://staging.projectsimple.ai/ \
                                --url /isteu/team/developers

`--url` is optional; without it the runner stays on the landing page that
login lands on. Headless by default; pass `--headed` to show the browser
(only honored when the login pattern doesn't pin `headless` itself).

The module is split into two seams so the pipeline can be unit-tested with
a mocked SPA, without keyring or real login:
  - run_pipeline(target, url)  — drives view→…→qa over a backend already
                                 put in live_sessions (login or a fixture).
  - run_e2e(target, url, …)    — full A→Z: login + run_pipeline + cleanup.
"""

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

import yaml

from . import live_sessions
from .engine.executor import run_pattern
from .session_store import SessionStore

# Register every action used by the YAML pipelines + qa_render.
from .engine.actions import (  # noqa: F401
    hidden,
    qa,
    routes,
    sessions,
    tc,
    touch,
    view,
)
from .engine.actions import map as _map_actions  # noqa: F401

_MCP_ROOT = Path(__file__).resolve().parent.parent.parent  # mcp_server/ (project dir)
_TOOLS_DIR = _MCP_ROOT / "tools"


def _pipeline_steps() -> list[dict]:
    """tc.yaml's steps + qa_render. One declarative source of truth."""
    tc_steps = yaml.safe_load(
        (_TOOLS_DIR / "tc.yaml").read_text(encoding="utf-8")
    )["steps"]
    return list(tc_steps) + [{"action": "qa_render"}]


async def run_pipeline(target: str, url: str | None) -> dict[str, Any]:
    """Drive view → touch → hidden → tc → qa against the live backend stored
    in live_sessions under `target`. Caller is responsible for putting the
    backend there (login(keep_open=True) or a test fixture)."""
    backend = live_sessions.get(target)
    if backend is None:
        raise RuntimeError(
            f"no live session for {target!r}; login(keep_open=true) first"
        )
    if url:
        full = target.rstrip("/") + url
        await backend.page.goto(full)

    ctx: dict[str, Any] = {
        "input": {"target": target},
        "creds": {},
        "env": dict(os.environ),
        "vars": {},
        "output": {},
        "backend": backend,
    }
    store = SessionStore(
        Path(os.environ.get("TEMP", "/tmp")) / "mcp_sessions",
        ttl_seconds=600,
    )
    outcome = await run_pattern(
        _pipeline_steps(), ctx, store, target=target, pattern_path=""
    )
    if outcome.status != "ok":
        raise RuntimeError(
            f"pipeline failed ({outcome.status}): {outcome.error}"
        )
    return ctx["output"]


async def run_e2e(target: str, url: str | None, *, headless: bool = True) -> dict[str, Any]:
    """Full A→Z: login(keep_open=true) → run_pipeline → close session."""
    from .server import _login  # local import: avoids loading FastMCP at import-time

    result = await _login(target, keep_open=True, visible=not headless)
    if result.status != "ok":
        raise RuntimeError(
            f"login failed: {result.status} {result.error or ''}"
        )
    try:
        return await run_pipeline(target, url)
    finally:
        sess = live_sessions.pop(target)
        if sess is not None and hasattr(sess, "page"):
            from .backends import web as _web

            try:
                await _web.close_session(sess)
            except Exception:
                pass


def _print_summary(out: dict[str, Any]) -> None:
    tc_count = out.get("tc_proposal_count", 0)
    qa_dir = out.get("qa_output_dir") or "(none — no proposals rendered)"
    files = out.get("qa_files") or []
    print(f"TC count    : {tc_count}")
    print(f"Output dir  : {qa_dir}")
    print(f"Files written: {len(files)} (top-5 below)")
    for f in files[:5]:
        print(f"  - {f}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="mcp_server.runner")
    p.add_argument("--target", required=True,
                   help="base URL, e.g. https://staging.projectsimple.ai/")
    p.add_argument("--url", default=None,
                   help="path appended after login (default: landing page)")
    p.add_argument("--headed", action="store_true",
                   help="show the browser (default: headless; pattern wins if pinned)")
    args = p.parse_args(argv)
    out = asyncio.run(run_e2e(args.target, args.url, headless=not args.headed))
    _print_summary(out)


if __name__ == "__main__":
    main()
