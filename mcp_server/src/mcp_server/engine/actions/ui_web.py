from typing import Any

from ...backends import web as _web
from ..registry import register
from ..value_resolver import resolve


def _page(ctx):
    return ctx["backend"].page


@register("open_browser")
async def open_browser(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Open a playwright session and place it in ctx['backend']. Default headed."""
    headless = step.get("headless", False)
    ctx["backend"] = await _web.open_session(headless=headless)


@register("close_browser")
async def close_browser(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    sess = ctx.get("backend")
    if sess is not None and hasattr(sess, "page"):
        await _web.close_session(sess)
        ctx["backend"] = None


@register("goto")
async def goto(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).goto(resolve(step["url"], ctx))


@register("fill")
async def fill(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).fill(step["selector"], str(resolve(step["value_from"], ctx)))


@register("click")
async def click(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).click(step["selector"])


@register("wait_for_selector")
async def wait_for_selector(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).wait_for_selector(step["selector"], timeout=step.get("timeout", 10000))


@register("assert_url_contains")
async def assert_url_contains(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    page = _page(ctx)
    await page.wait_for_url(f"**{step['value']}**", timeout=step.get("timeout", 10000))


@register("is_visible")
async def is_visible(step: dict[str, Any], ctx: dict[str, Any]) -> bool:
    return await _page(ctx).is_visible(step["selector"])


@register("wait_for_url")
async def wait_for_url(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).wait_for_url(step["url_glob"], timeout=step.get("timeout", 10000))


@register("assert_selector_visible")
async def assert_selector_visible(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    await _page(ctx).wait_for_selector(
        step["selector"], state="visible", timeout=step.get("timeout", 10000))


def _cast(value: str, cast: str | None):
    if cast == "int":
        return int("".join(c for c in value if c.isdigit() or c == "-") or "0")
    if cast == "float":
        return float(value)
    return value


@register("extract_text")
async def extract_text(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    el = await _page(ctx).query_selector(step["selector"])
    text = (await el.inner_text()).strip() if el else ""
    ctx["vars"][step["save_as"]] = _cast(text, step.get("cast"))


@register("extract_table")
async def extract_table(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    rows = await _page(ctx).query_selector_all(step["selector"])
    out = []
    for row in rows:
        record = {}
        for col in step["columns"]:
            cell = await row.query_selector(col["selector"])
            raw = (await cell.inner_text()).strip() if cell else ""
            record[col["field"]] = _cast(raw, col.get("cast"))
        out.append(record)
    ctx["vars"][step["save_as"]] = out
