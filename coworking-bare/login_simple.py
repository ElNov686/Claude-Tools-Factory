"""Bare simple login — Keycloak, Playwright (sync)."""
from pathlib import Path
from playwright.sync_api import sync_playwright


def load_env(path):
    env = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main():
    env = load_env(r"e:\tools\claude-tools-factory\env\factory.env")
    url = env["SIMPLE_URL"]
    user = env["SIMPLE_USERNAME"]
    pwd = env["SIMPLE_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # Keycloak renders late — wait for password input
        page.wait_for_selector('input[type="password"]', timeout=30000)
        # Keycloak page has honeypot/decoy unnamed inputs — target the named real ones explicitly
        username_loc = None
        for sel in ['#username', 'input[name="username"]', 'input[autocomplete="username"]',
                    'input[type="email"]', 'input[name="email"]']:
            if page.locator(sel).count():
                username_loc = page.locator(sel).first
                break
        if username_loc is None:
            username_loc = page.locator('input:not([type="password"]):not([type="hidden"]):visible').first
        username_loc.fill(user)
        # password — prefer named real one over decoy
        if page.locator('#password').count():
            page.locator('#password').fill(pwd)
        elif page.locator('input[name="password"]').count():
            page.locator('input[name="password"]').first.fill(pwd)
        else:
            page.locator('input[type="password"]:visible').first.fill(pwd)
        # submit
        clicked = False
        for sel in ['#kc-login', 'button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("Sign In")', 'button:has-text("Sign in")', 'button:has-text("Log in")']:
            loc = page.locator(sel)
            if loc.count():
                loc.first.click()
                clicked = True
                break
        if not clicked:
            page.keyboard.press("Enter")
        # wait for the redirect chain to leave the Keycloak /auth/ path
        try:
            page.wait_for_url(lambda u: "/auth/" not in u, timeout=20000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        final = page.url
        ok = ("/auth/" not in final) and ("openid-connect" not in final)
        print(f"SIMPLE_FINAL_URL={final}")
        print(f"SIMPLE_OK={ok}")
        browser.close()


if __name__ == "__main__":
    main()
