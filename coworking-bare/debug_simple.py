"""Debug simple/keycloak form."""
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
    page.goto(env["SIMPLE_URL"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector('input[type="password"]', timeout=30000)
    inputs = page.evaluate("""() => [...document.querySelectorAll('input')].map(i => ({
        type: i.type, name: i.name, id: i.id, placeholder: i.placeholder, ariaLabel: i.getAttribute('aria-label'), autocomplete: i.autocomplete
    }))""")
    buttons = page.evaluate("""() => [...document.querySelectorAll('button, input[type=submit]')].map(b => ({
        type: b.type, text: (b.innerText||b.value||'').trim().slice(0,80), id: b.id, name: b.name
    }))""")
    print("URL:", page.url)
    print("TITLE:", page.title())
    print("INPUTS:", inputs)
    print("BUTTONS:", buttons)

    # try fill + submit and check error message — use exact #username/#password
    page.locator('#username').fill(env["SIMPLE_USERNAME"])
    page.locator('#password').fill(env["SIMPLE_PASSWORD"])
    # check value made it in
    uval = page.locator('#username').input_value()
    pval = page.locator('#password').input_value()
    print(f"USERNAME_FILLED='{uval}' PASSWORD_LEN={len(pval)}")
    page.locator('#kc-login').click()
    page.wait_for_timeout(10000)
    print("POST URL:", page.url)
    print("POST TITLE:", page.title())
    # look for any error text
    err = page.evaluate("""() => {
        const txts = [...document.querySelectorAll('.kc-feedback-text, .alert, .error, [role=alert]')].map(e=>e.innerText.trim()).filter(Boolean);
        return txts;
    }""")
    print("ERRORS:", err)
    browser.close()
