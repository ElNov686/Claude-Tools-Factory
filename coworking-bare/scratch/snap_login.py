"""Bare-coworking login demo: log into SNAP by hand, no factory MCP.

Reads env/factory.env, picks SNAP_* block, drives Chromium with Playwright sync.
Saves screenshots before/after submit so we can see what landed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "env" / "factory.env"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def load_env(prefix: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.startswith(prefix + "_"):
            found[k[len(prefix) + 1 :]] = v
    return found


def main(prefix: str = "SNAP") -> int:
    creds = load_env(prefix)
    missing = [k for k in ("URL", "USERNAME", "PASSWORD") if k not in creds]
    if missing:
        print(f"FAIL: missing {prefix}_{missing} in {ENV}")
        return 2

    url, user, pwd = creds["URL"], creds["USERNAME"], creds["PASSWORD"]
    print(f"target: {url}")
    print(f"user:   {user}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.screenshot(path=str(OUT / "01_before.png"), full_page=True)

        email_sel = "input[type=email], input[name*=email i], input[autocomplete=username], input[placeholder*=mail i]"
        pwd_sel = "input[type=password], input[name*=pass i], input[autocomplete=current-password]"
        submit_sel = "button[type=submit], input[type=submit], button:has-text('Sign In'), button:has-text('Log In'), button:has-text('Login')"

        page.locator(email_sel).first.fill(user)
        page.locator(pwd_sel).first.fill(pwd)
        page.screenshot(path=str(OUT / "02_filled.png"), full_page=True)

        with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            page.locator(submit_sel).first.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.screenshot(path=str(OUT / "03_after.png"), full_page=True)

        landed = page.url
        title = page.title()
        print(f"landed: {landed}")
        print(f"title:  {title}")
        logged_in = "login" not in landed.lower()
        print(f"logged_in_heuristic: {logged_in}")

        browser.close()
        return 0 if logged_in else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "SNAP"))
