"""Debug snap form."""
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


env = load_env(r"e:\tools\claude-tools-factory\env\factory.env")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(env["SNAP_URL"], wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(3000)
    # print all inputs and buttons
    inputs = page.evaluate("""() => [...document.querySelectorAll('input')].map(i => ({
        type: i.type, name: i.name, id: i.id, placeholder: i.placeholder, ariaLabel: i.getAttribute('aria-label')
    }))""")
    buttons = page.evaluate("""() => [...document.querySelectorAll('button, input[type=submit]')].map(b => ({
        type: b.type, text: (b.innerText||b.value||'').trim().slice(0,60), id: b.id, name: b.name
    }))""")
    print("INPUTS:", inputs)
    print("BUTTONS:", buttons)
    print("URL:", page.url)
    print("TITLE:", page.title())
    browser.close()
