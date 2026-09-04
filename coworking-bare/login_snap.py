"""Bare snap login — Playwright, no factory MCP tools."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main():
    env = load_env(Path(r"e:\tools\claude-tools-factory\env\factory.env"))
    url = env["SNAP_URL"]
    user = env["SNAP_USERNAME"]
    pwd = env["SNAP_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=45000)
        # SPA — wait for the email input to render
        page.wait_for_selector('#email', timeout=20000)
        page.locator('#email').fill(user)
        page.locator('#password').fill(pwd)
        page.locator('button[type="submit"]').first.click()
        # wait for navigation away from /login
        try:
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        final = page.url
        ok = "/login" not in final
        print(f"SNAP_FINAL_URL={final}")
        print(f"SNAP_OK={ok}")
        browser.close()


if __name__ == "__main__":
    main()
