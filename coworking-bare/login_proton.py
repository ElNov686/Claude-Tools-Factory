"""Bare Proton desktop login — pywinauto, UIA backend."""
import re
import time
from pathlib import Path
from pywinauto import Desktop, Application


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
    user = env["PROTON_USERNAME"]
    pwd = env["PROTON_PASSWORD"]

    desktop = Desktop(backend="uia")
    # Find any top-level window whose title matches /Proton/
    target = None
    for w in desktop.windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if title and re.search(r"Proton", title, re.IGNORECASE):
            target = w
            print(f"FOUND_WINDOW='{title}'")
            break
    if target is None:
        print("PROTON_OK=False")
        print("PROTON_ERROR=no Proton window found")
        return

    try:
        target.set_focus()
    except Exception:
        pass
    time.sleep(0.5)

    # Walk every Edit / TextBox descendant; fill first two we find
    edits = target.descendants(control_type="Edit")
    print(f"EDIT_COUNT={len(edits)}")
    for i, e in enumerate(edits[:8]):
        try:
            print(f"  edit[{i}] name='{e.window_text()}' auto_id='{e.automation_id()}' rect={e.rectangle()}")
        except Exception as ex:
            print(f"  edit[{i}] err={ex}")

    if len(edits) < 2:
        # Sometimes username field is on a separate first screen — fill what we have and click Next
        if not edits:
            print("PROTON_OK=False")
            print("PROTON_ERROR=no edit controls found")
            return

    # Heuristic: pywinauto returns descendants in tree order; on Proton's login the
    # first edit is username/email, second is password.
    try:
        edits[0].set_focus()
        edits[0].type_keys("^a{BACKSPACE}", with_spaces=True)
        edits[0].type_keys(user, with_spaces=True)
    except Exception as ex:
        print(f"USER_FILL_ERR={ex}")

    if len(edits) >= 2:
        try:
            edits[1].set_focus()
            edits[1].type_keys("^a{BACKSPACE}", with_spaces=True)
            edits[1].type_keys(pwd, with_spaces=True)
        except Exception as ex:
            print(f"PWD_FILL_ERR={ex}")

    # Find the Sign In button (or Continue / Next)
    buttons = target.descendants(control_type="Button")
    print(f"BUTTON_COUNT={len(buttons)}")
    target_btn = None
    for b in buttons:
        try:
            t = (b.window_text() or "").strip()
        except Exception:
            continue
        if not t:
            continue
        if re.search(r"^(sign\s*in|log\s*in|continue|next)$", t, re.IGNORECASE):
            target_btn = b
            print(f"CLICK_BUTTON='{t}'")
            break
    if target_btn is None and buttons:
        # last-resort fallback: print first 10 button titles
        for i, b in enumerate(buttons[:10]):
            try:
                print(f"  btn[{i}] '{b.window_text()}'")
            except Exception:
                pass
    if target_btn is not None:
        try:
            target_btn.click_input()
        except Exception as ex:
            print(f"CLICK_ERR={ex}")
    else:
        print("NO_SIGNIN_BUTTON")

    time.sleep(3)
    # Re-read title
    try:
        title2 = target.window_text()
    except Exception:
        title2 = "<closed>"
    print(f"PROTON_FINAL_TITLE='{title2}'")
    # success heuristic: if there's no longer a visible password field, we likely logged in
    try:
        after_edits = target.descendants(control_type="Edit")
        still_has_pwd = False
        for e in after_edits:
            try:
                # password fields in UIA usually have IsPassword / value masked — we can't easily check
                # so just check: if there are still >=2 edits we probably are still on the login form
                pass
            except Exception:
                pass
        still_on_login = len(after_edits) >= 2
    except Exception:
        still_on_login = True
    ok = not still_on_login
    print(f"PROTON_OK={ok}")


if __name__ == "__main__":
    main()
