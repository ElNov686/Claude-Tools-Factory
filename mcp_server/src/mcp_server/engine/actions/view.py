"""VIEW — per-page visual anchor: screenshot + visible interactives with rect.

Runs AFTER map, BEFORE touch + hidden. View's job: open every page from the
map, take a screenshot, and produce a unique-per-instance list of visible
interactive elements. Each element gets a stable+unique locator (nth-of-type
appended when the accessible name isn't unique) and its bounding rect.

Touch and hidden consume view's per-page element list — that way they never
hit ambiguous locators ("4 buttons named Last Login" → 4 distinct entries
with `>> nth=0..3`), and TC can render visually-precise POM tests.

No clicking. Pure DOM + screenshot.
"""

import json
from typing import Any
from urllib.parse import urlparse

from .. import qa_paths
from ..registry import register

_ROUTER_PUSH_JS = """
async (path) => {
  for (const el of document.querySelectorAll('*')) {
    if (el.__vue_app__) {
      const r = el.__vue_app__.config?.globalProperties?.$router;
      if (r && r.push) { try { await r.push(path); return true; } catch(e) { return false; } }
    }
  }
  return false;
}
"""


async def _navigate(page, path):
    """In-app router push first; fall back to goto(absolute)."""
    try:
        ok = await page.evaluate(_ROUTER_PUSH_JS, path)
    except Exception:
        ok = False
    if not ok:
        origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
        try:
            await page.goto(origin.rstrip("/") + path)
        except Exception:
            pass


_SCAN_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const accName = (el) => {
    let t = el.getAttribute && el.getAttribute('aria-label');
    if (t && t.trim()) return norm(t);
    const labId = el.getAttribute && el.getAttribute('aria-labelledby');
    if (labId) {
      const lab = document.getElementById(labId);
      if (lab) { const s = norm(lab.innerText); if (s) return s.slice(0, 60); }
    }
    t = norm(el.innerText);
    if (t) return t.slice(0, 60);
    t = el.getAttribute && el.getAttribute('title');
    if (t && t.trim()) return norm(t);
    const img = el.querySelector && el.querySelector('img[alt]');
    if (img) {
      const a = norm(img.getAttribute('alt'));
      if (a) return a;
    }
    return '';
  };
  const isVisible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const isInsideChrome = (el) =>
    !!el.closest('header, footer, aside, nav, [role="navigation"], '
              + '[role="banner"], [role="contentinfo"], [role="complementary"]');
  const stableLocator = (el) => {
    const dt = el.getAttribute('data-testid') || el.getAttribute('data-test')
            || el.getAttribute('data-qa');
    if (dt) return { by: 'data-testid', sel: `[data-testid="${dt}"]` };
    if (el.id) return { by: 'id', sel: `#${el.id}` };
    if (el.name) {
      return { by: 'name',
               sel: `${el.tagName.toLowerCase()}[name="${el.name}"]` };
    }
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const nm = accName(el);
    if (nm) return { by: 'role-name',
      sel: `role=${role}[name="${nm.slice(0,60)}"]` };
    return { by: 'css', sel: el.tagName.toLowerCase() };
  };
  const elementKind = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button' || role === 'button') return 'button';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      return 'input';
    }
    if (tag === 'textarea') return 'input';
    if (tag === 'select') return 'select';
    if (role === 'link') return 'link';
    if (role === 'menuitem' || role === 'tab' || role === 'option') return 'choice';
    return null;
  };

  // Universal container detection: semantic landmarks PLUS divs that wear
  // a role / aria-label / common content-class hint. Modern SPAs frequently
  // use div-based layouts instead of <main>/<section> — we still find them.
  const containerSel = 'main, section, article, form, dialog, '
    + '[role="main"], [role="region"], [role="dialog"], [role="form"], '
    + 'div[role], div[aria-label], '
    + 'div[class*="card" i], div[class*="page" i], '
    + 'div[class*="panel" i], div[class*="content" i], '
    + 'div[class*="widget" i]';
  const containerOf = (el) => {
    const c = el.closest(containerSel);
    if (!c) return null;
    const heading = c.querySelector('h1, h2, h3, [role="heading"]');
    const name = (c.getAttribute('aria-label') || '').trim()
      || (heading && heading.innerText || '').trim().slice(0, 80)
      || c.id || c.tagName.toLowerCase();
    return { tag: c.tagName.toLowerCase(), name, role: c.getAttribute('role') };
  };

  const sel = 'a[href], button, input, textarea, select, '
    + '[role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="option"]';
  const out = [];
  let idx = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!isVisible(el)) continue;
    if (isInsideChrome(el)) continue;
    const kind = elementKind(el);
    if (!kind) continue;
    const loc = stableLocator(el);
    const r = el.getBoundingClientRect();
    const rec = {
      id: idx++,
      kind,
      text: accName(el),
      locator: loc.sel,
      locator_by: loc.by,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      container: containerOf(el),
    };
    if (kind === 'input') {
      rec.type = (el.getAttribute('type') || 'text').toLowerCase();
      if (el.hasAttribute('required')) rec.required = true;
      const ph = el.getAttribute('placeholder');
      if (ph) rec.placeholder = ph;
    }
    if (kind === 'link' && el.hasAttribute('href')) {
      rec.href = el.getAttribute('href');
    }
    if (el.disabled || el.getAttribute('aria-disabled') === 'true'
        || el.hasAttribute('disabled')) {
      rec.disabled = true;
    }
    out.push(rec);
  }
  return out;
}
"""


def _disambiguate(elements: list[dict]) -> None:
    """When N elements share the same locator string, append `>> nth=K` so
    every record addresses exactly one DOM element."""
    counts: dict[str, int] = {}
    for el in elements:
        counts[el["locator"]] = counts.get(el["locator"], 0) + 1
    seen: dict[str, int] = {}
    for el in elements:
        loc = el["locator"]
        if counts[loc] <= 1:
            continue
        i = seen.get(loc, 0)
        seen[loc] = i + 1
        el["locator"] = f"{loc} >> nth={i}"
        el["locator_by"] = (el.get("locator_by") or "") + "+nth"


def _dump_page(record: dict, host: str) -> str:
    """Write one page's view dump to <QA>/<host>/dom/view/<slug>.json.

    Stable filename — re-running the tool for the same page overwrites the
    same file. Sibling per-page files are left untouched.
    """
    slug = qa_paths.template_slug(record["template"])
    p = qa_paths.dom_dir(host, "view") / f"{slug}.json"
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


def _dump_summary(payload: dict, host: str) -> str:
    """Optional roll-up across all pages, written next to the per-page files."""
    p = qa_paths.dom_dir(host, "view") / "_summary.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


@register("view_pages")
async def view_pages(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Visit every map node; capture screenshot + visible interactive list
    with unique locators and bounding rects. Enriches __map__ for touch/hidden.

    Honors optional input.page (route template or concrete URL) — when set,
    only that page is visited and only its per-page dump file is rewritten.
    """
    page = ctx["backend"].page
    map_nodes: list[dict] = ctx["vars"].get("__map__") or []
    nav_map: list[dict] = ctx["vars"].get("__navigable_map__") or []
    nav_by_template = {n.get("template"): n.get("navigable_url")
                       for n in nav_map if n.get("template")}
    routes: list[dict] = ctx["vars"].get("__routes__") or []
    host = qa_paths.host_from_ctx(ctx)

    page_filter_arg = (ctx.get("input") or {}).get("page")
    filter_tpls = qa_paths.resolve_page_filter(page_filter_arg, routes)

    per_page: list[dict] = []
    skipped: list[dict] = []
    written_paths: list[str] = []
    visited = 0
    elements_total = 0

    for node in map_nodes:
        tpl = node["template"]
        if filter_tpls is not None and tpl not in filter_tpls:
            continue
        concrete = nav_by_template.get(tpl)
        if not concrete:
            skipped.append({"template": tpl, "reason": "no concrete URL"})
            continue
        try:
            await _navigate(page, concrete)
            try:
                await page.wait_for_load_state("networkidle", timeout=2500)
            except Exception:
                pass
        except Exception as exc:
            skipped.append({"template": tpl, "reason": f"nav fail: {exc!s}"})
            continue
        try:
            elements = await page.evaluate(_SCAN_JS)
        except Exception as exc:
            skipped.append({"template": tpl, "reason": f"scan fail: {exc!s}"})
            continue
        _disambiguate(elements)

        slug = qa_paths.template_slug(tpl)
        shot_path = str(qa_paths.screenshots_dir(host) / f"{slug}.png")
        try:
            await page.screenshot(path=shot_path, full_page=False)
        except Exception:
            shot_path = None

        record = {
            "template": tpl,
            "page_url": page.url,
            "screenshot_path": shot_path,
            "element_count": len(elements),
            "elements": elements,
        }
        per_page.append(record)
        node["view"] = {
            "screenshot_path": shot_path,
            "elements": elements,
        }
        written_paths.append(_dump_page(record, host))
        visited += 1
        elements_total += len(elements)

    # Roll-up only when we did a full scan; per-page runs leave the summary
    # alone so previous full-run summary stays meaningful.
    if filter_tpls is None:
        summary_path = _dump_summary({
            "page_count": visited,
            "elements_total": elements_total,
            "skipped": skipped,
            "pages": [r["template"] for r in per_page],
        }, host)
    else:
        summary_path = ""

    ctx["output"]["view_pages"] = visited
    ctx["output"]["view_elements_total"] = elements_total
    ctx["output"]["view_skipped"] = len(skipped)
    ctx["output"]["view_dump_paths"] = written_paths
    ctx["output"]["view_summary_path"] = summary_path
    if page_filter_arg:
        ctx["output"]["view_filter"] = page_filter_arg
        ctx["output"]["view_filter_matched"] = len(filter_tpls or set())
