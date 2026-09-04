"""map — build a deduped page graph for an SPA.

Strategy: each unique route template (from extract_routes) is ONE node.
For each template with a navigable concrete URL we router.push there and
scan all `<a href>` / `[router-link]` / `[data-href]` on the page; each
href is resolved back to a template via template_to_regex and an edge is
recorded (from-template -> to-template) with the triggering locator.

The result is a deduped graph: a page reachable via 5 different links from
5 different sources appears as ONE node with 5 entries in paths_in.

Universal — no DFS click-walking, no go_back fragility, no max_pages cap.
Works on any SPA that uses real anchors for navigation (Vue/React/Angular
all render router-link as <a href> by default).
"""

import json
from typing import Any
from urllib.parse import urlparse

from .. import qa_paths
from ..registry import register
from .routes import template_to_regex

_SCAN_LINKS_JS = """
() => {
  const landmarkKind = (el) => {
    const a = el.closest('nav, [role="navigation"]');
    if (a) return 'nav';
    if (el.closest('header, [role="banner"]')) return 'header';
    if (el.closest('footer, [role="contentinfo"]')) return 'footer';
    if (el.closest('aside, [role="complementary"]')) return 'aside';
    return null;
  };

  const accName = (el) => {
    let t = el.getAttribute('aria-label');
    if (t && t.trim()) return t.trim();
    const labId = el.getAttribute('aria-labelledby');
    if (labId) {
      const lab = document.getElementById(labId);
      if (lab) {
        const s = (lab.innerText || '').trim();
        if (s) return s;
      }
    }
    t = (el.innerText || '').trim();
    if (t) return t;
    t = el.getAttribute('title');
    if (t && t.trim()) return t.trim();
    const img = el.querySelector('img[alt]');
    if (img) {
      const a = (img.getAttribute('alt') || '').trim();
      if (a) return a;
    }
    const svgT = el.querySelector('svg title');
    if (svgT) {
      const s = (svgT.textContent || '').trim();
      if (s) return s;
    }
    const iconCls = el.querySelector('[class*="icon-"]');
    if (iconCls) {
      const cls = iconCls.getAttribute('class') || '';
      const m = cls.match(/icon-([a-z0-9-]+)/i);
      if (m) return m[1].replace(/-/g, ' ');
    }
    return '';
  };

  const stableLocator = (el, href) => {
    const dt = el.getAttribute('data-testid') || el.getAttribute('data-test')
            || el.getAttribute('data-qa');
    if (dt) return `[data-testid="${dt}"]`;
    if (el.id) return `#${el.id}`;
    // parent-anchored: closest nav/aside/header/footer/main + href
    const anchor = el.closest('nav, aside, header, footer, main, [role="navigation"]');
    if (anchor) {
      const tag = anchor.tagName.toLowerCase();
      let parentSel = tag;
      if (anchor.id) parentSel = `#${anchor.id}`;
      else if (anchor.getAttribute('role')) parentSel = `[role="${anchor.getAttribute('role')}"]`;
      return `${parentSel} a[href="${href}"]`;
    }
    return `a[href="${href}"]`;
  };

  const out = [];
  const seen = new Set();
  const push = (href, text, locator, kind, landmark) => {
    const k = `${href}::${text}::${locator || ''}::${landmark || ''}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ href, text: (text || '').trim().slice(0, 80), locator, kind, landmark });
  };

  const pushEl = (href, el, kind) => {
    push(href, accName(el), stableLocator(el, href), kind, landmarkKind(el));
  };
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('mailto:')
        || href.startsWith('tel:') || href.startsWith('javascript:')) continue;
    pushEl(href, a, 'anchor');
  }
  for (const el of document.querySelectorAll(
        'router-link[to], [data-href], [data-to], [data-route]')) {
    const href = el.getAttribute('to') || el.getAttribute('data-href')
              || el.getAttribute('data-to') || el.getAttribute('data-route');
    if (!href) continue;
    pushEl(href, el, 'router-link');
  }
  return out;
}
"""


def _resolve_href_to_template(href: str, routes: list[dict],
                              origin: str) -> str | None:
    """Match a concrete href to one of the known route templates."""
    if href.startswith(origin):
        href = href[len(origin):]
    if href.startswith("http://") or href.startswith("https://"):
        return None  # external link
    if not href.startswith("/"):
        return None
    path = href.split("?")[0].split("#")[0]
    for r in routes:
        tpl = r.get("path", "")
        if not tpl.startswith("/"):
            continue
        try:
            rx = template_to_regex(tpl)
            if rx.match(path):
                return tpl
        except Exception:
            continue
    return None


def _path_to_template(path: str, routes: list[dict]) -> str | None:
    """Best-effort: pick the most-specific template whose regex matches path."""
    candidates: list[str] = []
    for r in routes:
        tpl = r.get("path", "")
        if not tpl.startswith("/"):
            continue
        try:
            rx = template_to_regex(tpl)
            if rx.match(path):
                candidates.append(tpl)
        except Exception:
            continue
    if not candidates:
        return None
    # Prefer the longest (most-specific) template
    return max(candidates, key=len)


def _dump(payload: dict, host: str) -> str:
    """Stable per-host file: <QA>/<host>/dom/map/graph.json. Overwritten in place."""
    p = qa_paths.dom_dir(host, "map") / "graph.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


@register("build_map")
async def build_map(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Build the deduped page graph. Reads __routes__ + __navigable_map__,
    writes __map__ for downstream tools (touch / hidden / view / tc)."""
    page = ctx["backend"].page
    routes: list[dict] = ctx["vars"].get("__routes__") or []
    nav_map: list[dict] = ctx["vars"].get("__navigable_map__") or []
    nav_by_template = {n.get("template"): n.get("navigable_url")
                       for n in nav_map if n.get("template")}

    # 1) seed: every unique template gets a node, deduped by template string
    nodes: dict[str, dict] = {}
    next_id = [1]

    def _node(tpl: str, name: str | None = None) -> dict:
        if tpl not in nodes:
            nodes[tpl] = {
                "id": next_id[0],
                "template": tpl,
                "name": name,
                "page_url": nav_by_template.get(tpl),
                "depth": None,
                "paths_in": [],
                "paths_out": [],
            }
            next_id[0] += 1
        elif name and not nodes[tpl].get("name"):
            nodes[tpl]["name"] = name
        return nodes[tpl]

    for r in routes:
        tpl = r.get("path")
        if tpl:
            _node(tpl, r.get("name"))

    # 2) single DOM scan from current page (no per-template router.push):
    # links wearing a header/footer/aside/nav landmark are universal chrome
    # — one shared "(global-nav)" entry per target, attached once.
    origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
    current_tpl = _path_to_template(urlparse(page.url).path, routes)

    try:
        links = await page.evaluate(_SCAN_LINKS_JS)
    except Exception:
        links = []

    chrome_emitted: set[tuple] = set()
    landing_node = _node(current_tpl) if current_tpl else None

    for lnk in links:
        target_tpl = _resolve_href_to_template(lnk["href"], routes, origin)
        if not target_tpl:
            continue
        landmark = lnk.get("landmark")
        trigger = {
            "text": lnk["text"], "locator": lnk.get("locator"),
            "kind": lnk.get("kind"), "href": lnk["href"],
            "landmark": landmark,
        }
        to_node = _node(target_tpl)

        if landmark:
            sig = (target_tpl, lnk.get("href"))
            if sig in chrome_emitted:
                continue
            chrome_emitted.add(sig)
            chrome_trigger = dict(trigger)
            chrome_trigger["kind"] = "global-nav"
            to_node["paths_in"].append({
                "from_template": f"(global-nav:{landmark})",
                "from_id": None, "trigger": chrome_trigger,
            })
            if landing_node and landing_node["template"] != target_tpl:
                landing_node["paths_out"].append({
                    "to_id": to_node["id"], "to_template": target_tpl,
                    "trigger": chrome_trigger,
                })
        else:
            if not landing_node or target_tpl == current_tpl:
                continue
            landing_node["paths_out"].append({
                "to_id": to_node["id"], "to_template": target_tpl,
                "trigger": trigger,
            })
            to_node["paths_in"].append({
                "from_id": landing_node["id"],
                "from_template": current_tpl, "trigger": trigger,
            })

    visited_templates = [current_tpl] if current_tpl else []
    skipped_templates: list[dict] = []

    # 3) BFS depths from the page we scanned (true landing for this run)
    landing = current_tpl or ("/" if "/" in nodes else None)
    if landing:
        seen = {landing: 0}
        queue = [landing]
        while queue:
            cur = queue.pop(0)
            d = seen[cur]
            for out in nodes[cur]["paths_out"]:
                tpl = out["to_template"]
                if tpl not in seen and tpl in nodes:
                    seen[tpl] = d + 1
                    queue.append(tpl)
        for tpl, n in nodes.items():
            n["depth"] = seen.get(tpl)

    # 4) write a laconic, ordered dump
    out_nodes = sorted(
        nodes.values(),
        key=lambda n: (n["depth"] is None, n["depth"] or 9999, n["id"]))
    edge_count = sum(len(n["paths_out"]) for n in out_nodes)
    multi_path_pages = [n for n in out_nodes if len(n["paths_in"]) > 1]

    payload = {
        "landing": landing,
        "page_count": len(out_nodes),
        "edge_count": edge_count,
        "multi_path_page_count": len(multi_path_pages),
        "visited_count": len(visited_templates),
        "skipped": skipped_templates,
        "nodes": out_nodes,
    }
    dump_path = _dump(payload, qa_paths.host_from_ctx(ctx))

    ctx["vars"]["__map__"] = out_nodes
    ctx["output"]["map_page_count"] = len(out_nodes)
    ctx["output"]["map_edge_count"] = edge_count
    ctx["output"]["map_multi_path_pages"] = len(multi_path_pages)
    ctx["output"]["map_dump_path"] = dump_path
