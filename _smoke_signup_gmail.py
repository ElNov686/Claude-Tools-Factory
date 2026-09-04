"""Standalone smoke test for the refactored signup handler chain.
Runs the universal DOM-driven sign-up against Gmail with a visible browser
and prints the result dict. Bypasses MCP so we don't need a server reload."""
import asyncio
import io
import os
import sys
from pathlib import Path

# Force UTF-8 stdout so Russian/× characters don't crash on Windows cp1251.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                               errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                               errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent

# Load env/factory.env into os.environ (same logic as server.py)
ENV_DIR = ROOT / "env"
for f in sorted(ENV_DIR.glob("*.env")):
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Make src/ importable
sys.path.insert(0, str(ROOT / "mcp_server" / "src"))

from mcp_server.backends import web  # noqa: E402
from mcp_server.engine.actions.signup import (  # noqa: E402
    _PROBE_FORM_JS, _universal_signup_dom,
)
import json  # noqa: E402

TARGET = "https://accounts.google.com/signup"
DUMP_PATH = Path(__file__).resolve().parent / "_smoke_signup_dump.json"


async def main() -> int:
    sess = await web.open_session(headless=False)
    try:
        await sess.page.goto(TARGET)
        try:
            await sess.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        result = await _universal_signup_dom(
            sess.page, dict(os.environ), max_steps=20,
        )
        print("RESULT:", result)
        # Dump final-page probe + screenshot + a "everything clickable" probe
        try:
            final_probe = await sess.page.evaluate(_PROBE_FORM_JS)
            extra = await sess.page.evaluate(r"""
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const isVisible = (el) => {
                const cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2;
              };
              const out = [];
              // anything that looks tappable
              const sel = 'a, [tabindex], [onclick], [role="option"], '
                        + '[role="radio"], [role="link"], [role="menuitem"], '
                        + 'li[class*="suggest"], div[class*="suggest"], '
                        + 'span[class*="suggest"]';
              for (const el of document.querySelectorAll(sel)) {
                if (!isVisible(el)) continue;
                out.push({
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || '',
                  text: norm(el.innerText).slice(0, 100),
                  aria_label: el.getAttribute('aria-label') || '',
                  class: (el.getAttribute('class') || '').slice(0, 80),
                });
              }
              // ALL inputs regardless of visibility, for inspection
              const allInputs = [];
              for (const el of document.querySelectorAll('input, textarea')) {
                const cs = getComputedStyle(el);
                allInputs.push({
                  tag: el.tagName.toLowerCase(),
                  type: el.getAttribute('type') || '',
                  name: el.getAttribute('name') || '',
                  id: el.id || '',
                  display: cs.display,
                  visibility: cs.visibility,
                  disabled: el.disabled,
                  aria_disabled: el.getAttribute('aria-disabled') || '',
                  rect: el.getBoundingClientRect().toJSON(),
                });
              }
              return { clickables: out.slice(0, 80), all_inputs: allInputs };
            }
            """)
            await sess.page.screenshot(path=str(DUMP_PATH.with_suffix(".png")),
                                       full_page=True)
            html = await sess.page.content()
            DUMP_PATH.write_text(json.dumps({
                "url": sess.page.url,
                "probe": final_probe,
                "extra": extra,
                "html_len": len(html),
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"DUMP written: {DUMP_PATH}")
            print(f"PNG written: {DUMP_PATH.with_suffix('.png')}")
        except Exception as exc:
            print(f"DUMP failed: {exc}")
        await asyncio.sleep(20)
        return 0 if result.get("status") == "ok" else 1
    finally:
        await web.close_session(sess)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
