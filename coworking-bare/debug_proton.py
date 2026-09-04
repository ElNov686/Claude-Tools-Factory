"""List all visible top-level windows so we can find Proton."""
from pywinauto import Desktop

for backend in ("uia", "win32"):
    print(f"--- backend={backend} ---")
    d = Desktop(backend=backend)
    for w in d.windows():
        try:
            t = w.window_text()
            cls = w.class_name() if hasattr(w, "class_name") else ""
        except Exception as ex:
            t = f"<err {ex}>"
            cls = ""
        if t and t.strip():
            print(f"  '{t}'  cls={cls}")
