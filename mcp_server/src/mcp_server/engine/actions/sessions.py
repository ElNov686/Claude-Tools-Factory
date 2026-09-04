"""Actions for reusing / closing live browser sessions (see live_sessions)."""

from typing import Any
from urllib.parse import urlparse

from ... import live_sessions
from ..registry import register
from ..value_resolver import resolve


@register("use_session")
async def use_session(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Attach the live browser session for `target` to ctx['backend'].

    Lets a tool run on the browser a prior login(keep_open=True) left open.
    Also stashes the resolved host on ctx['target_host'] so downstream
    actions can compute QA/<host>/... dump paths without re-deriving it.
    """
    target = resolve(step["target"], ctx)
    sess = live_sessions.get(target)
    if sess is None:
        raise RuntimeError(
            f"no live session for {target!r}; call login with keep_open=true first"
        )
    ctx["backend"] = sess
    ctx["target_host"] = urlparse(target).netloc or target


@register("close_session")
async def close_session(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Close a live browser session kept open by login(keep_open=true)."""
    target = resolve(step["target"], ctx)
    sess = live_sessions.pop(target)
    if sess is not None and hasattr(sess, "page"):
        from ...backends import web as _web
        try:
            await _web.close_session(sess)
        except Exception:
            pass
    ctx["output"]["closed"] = sess is not None
