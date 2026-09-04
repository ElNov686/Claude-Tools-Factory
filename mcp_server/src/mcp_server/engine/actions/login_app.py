"""run_app_login_pattern — log into a desktop app already running on the OS.

Looks up the env block <PREFIX>_{APP,USERNAME,PASSWORD} whose prefix appears
in target. APP is a regex matching the window title. Attaches via UIA,
fills the first user-like Edit and the first password-like Edit, clicks
Sign In. Reuses credentials with the web login block by sharing keys.
"""

import asyncio
import os
import re
from typing import Any

from ..registry import register
from ..value_resolver import resolve


SUBMIT_NAME_RE = re.compile(
    r"sign\s*in|log\s*in|login|continue|войти|next", re.IGNORECASE,
)
PASSWORD_NAME_RE = re.compile(r"password|пароль", re.IGNORECASE)
USER_NAME_RE = re.compile(r"email|user|login|почт|address|name", re.IGNORECASE)


def _pick_env_block(target: str, env: dict) -> tuple[str, str, str] | None:
    """Find (app_title_re, username, password) for env prefix matching target."""
    needle = target.lower()
    prefixes = sorted(
        (k.removesuffix("_APP") for k in env
         if k.endswith("_APP") and not k.startswith("SIGNUP")),
        key=len,
        reverse=True,
    )
    for prefix in prefixes:
        if prefix.lower() not in needle:
            continue
        app = env.get(f"{prefix}_APP")
        user = env.get(f"{prefix}_USERNAME")
        password = env.get(f"{prefix}_PASSWORD")
        if app and user and password:
            return app, user, password
    return None


def _desktop_login(window_title_re: str, username: str, password: str) -> dict:
    """Synchronous pywinauto driver. Runs in a worker thread."""
    from pywinauto import Application

    app = Application(backend="uia").connect(title_re=window_title_re, timeout=10)
    win = app.top_window()
    win.set_focus()

    edits = win.descendants(control_type="Edit")
    user_filled = pw_filled = False

    for edit in edits:
        try:
            name = edit.element_info.name or ""
        except Exception:
            continue
        if not pw_filled and PASSWORD_NAME_RE.search(name):
            edit.set_edit_text(password)
            pw_filled = True
        elif not user_filled and USER_NAME_RE.search(name):
            edit.set_edit_text(username)
            user_filled = True

    if not pw_filled and len(edits) >= 2:
        edits[0].set_edit_text(username)
        edits[1].set_edit_text(password)
        user_filled = pw_filled = True

    if not (user_filled and pw_filled):
        return {"status": "failed", "window": win.window_text(),
                "error": "could not locate both user/password fields"}

    for button in win.descendants(control_type="Button"):
        try:
            name = button.element_info.name or ""
        except Exception:
            continue
        if SUBMIT_NAME_RE.search(name):
            button.click()
            return {"status": "ok", "window": win.window_text(), "clicked": name}

    return {"status": "failed", "window": win.window_text(),
            "error": "no submit button matched"}


@register("run_app_login_pattern")
async def run_app_login_pattern(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    target = resolve(step["target"], ctx)

    found = _pick_env_block(target, dict(os.environ))
    if not found:
        ctx["output"] = {
            "status": "failed",
            "target": target,
            "error": (
                f"no env block matches {target!r} — add "
                "<PREFIX>_APP / <PREFIX>_USERNAME / <PREFIX>_PASSWORD to env"
            ),
        }
        return

    app_re, username, password = found

    try:
        result = await asyncio.to_thread(_desktop_login, app_re, username, password)
    except Exception as exc:
        ctx["output"] = {
            "status": "failed", "target": target, "app": app_re,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return

    result["target"] = target
    result["app"] = app_re
    ctx["output"] = result
