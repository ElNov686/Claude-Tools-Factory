"""run_login_pattern — universal DOM login routed by env block.

Pick the <PREFIX>_{URL,USERNAME,PASSWORD} block whose lowercase prefix
appears in the target, fill visible inputs, click submit. No site names
or credentials live in code — everything is in env/factory.env.
"""

import os
from typing import Any

from ... import live_sessions
from ..registry import register
from ..value_resolver import resolve


FIELD_SELECTORS = ", ".join([
    "input[type=text]:visible",
    "input[type=email]:visible",
    "input[type=tel]:visible",
    "input[type=password]:visible",
    "input:not([type]):visible",
])

SUBMIT_SELECTORS = ", ".join([
    "button[type=submit]",
    "input[type=submit]",
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Continue")',
    'button:has-text("Войти")',
])

TRUTHY = ("1", "true", "yes", "on")


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def _pick_env_block(target: str, env: dict) -> tuple[str, str, str] | None:
    """Find (url, username, password) for the env prefix that matches target.

    The prefix is the part before `_USERNAME`. Longest prefix wins.
    Falls back to the target itself as the URL if it already looks like one.
    """
    needle = target.lower()
    prefixes = sorted(
        (k.removesuffix("_USERNAME") for k in env
         if k.endswith("_USERNAME") and not k.startswith("SIGNUP")),
        key=len,
        reverse=True,
    )
    for prefix in prefixes:
        if prefix.lower() not in needle:
            continue
        password = env.get(f"{prefix}_PASSWORD")
        if not password:
            continue
        url = env.get(f"{prefix}_URL") or (
            target if target.startswith("http") else ""
        )
        if url:
            return url, env[f"{prefix}_USERNAME"], password
    return None


async def _fill_and_submit(page: Any, username: str, password: str) -> None:
    try:
        await page.locator("input[type=password]").first.wait_for(
            state="visible", timeout=15000,
        )
    except Exception:
        pass
    fields = page.locator(FIELD_SELECTORS)
    for i in range(await fields.count()):
        field = fields.nth(i)
        is_password = (await field.get_attribute("type") or "").lower() == "password"
        await field.fill(password if is_password else username)

    await page.locator(SUBMIT_SELECTORS).first.click()

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


async def _close_quietly(web: Any, session: Any) -> None:
    try:
        await web.close_session(session)
    except Exception:
        pass


@register("run_login_pattern")
async def run_login_pattern(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    from ...backends import web

    target = resolve(step["target"], ctx)
    keep_open = _bool(resolve(step.get("keep_open"), ctx))
    visible = _bool(resolve(step.get("visible"), ctx))

    found = _pick_env_block(target, dict(os.environ))
    if not found:
        ctx["output"] = {
            "status": "failed",
            "target": target,
            "error": (
                f"no env block matches {target!r} — add "
                "<PREFIX>_URL / <PREFIX>_USERNAME / <PREFIX>_PASSWORD "
                "to env/factory.env"
            ),
        }
        return

    url, username, password = found
    session = await web.open_session(headless=not visible)

    try:
        await session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await _fill_and_submit(session.page, username, password)
    except Exception as exc:
        await _close_quietly(web, session)
        ctx["output"] = {
            "status": "failed", "target": target, "url": url, "error": str(exc),
        }
        return

    if keep_open:
        live_sessions.put(url, session)
        if target and target != url:
            live_sessions.put(target, session)
    else:
        await _close_quietly(web, session)

    ctx["output"] = {
        "status": "ok",
        "target": target,
        "url": url,
        "final_url": session.page.url,
    }
