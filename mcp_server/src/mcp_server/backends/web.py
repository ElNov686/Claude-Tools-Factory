from dataclasses import dataclass
from typing import Any


@dataclass
class WebSession:
    """A live browser session. Fields are kept untyped so importing this module
    does not pull in playwright (heavy ~seconds on cold start). The actual
    playwright objects live inside the fields once `open_session` runs."""
    _pw: Any
    browser: Any
    context: Any
    page: Any


async def open_session(headless: bool = True, storage_state: dict | None = None) -> WebSession:
    from playwright.async_api import async_playwright  # lazy: import only when actually opening
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless)
    context = await browser.new_context(storage_state=storage_state)
    page = await context.new_page()
    return WebSession(_pw=pw, browser=browser, context=context, page=page)


async def export_state(sess: WebSession) -> dict:
    return await sess.context.storage_state()


async def close_session(sess: WebSession) -> None:
    await sess.context.close()
    await sess.browser.close()
    await sess._pw.stop()
